"""Actual archived native CP/CAN/pose bytes through the trusted cohort adapter."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver, _bundle
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import describe_car_params
from .test_request_evidence import GeneratedRequestFixture, generated_comparison_profile
from .test_lateral_log_signals import cp_bytes


NS = 1_000_000_000


class NativeAdapterFixture(GeneratedRequestFixture):
  def __init__(self, root, raw_cp, packer, *, future_pose=False, wrong_cp=False, stale_outputs=False):
    self.raw_cp, self.packer = raw_cp, packer
    self.future_pose, self.wrong_cp, self.stale_outputs = future_pose, wrong_cp, stale_outputs
    self.expected = {}
    super().__init__(root, identity_provider=lambda: describe_car_params(self.raw_cp))

  def publish_phase(self):
    (self.settings / "CarParams").write_bytes(self.raw_cp)
    return super().publish_phase()

  def native_chunks(self, chunks):
    _, log = _schema()
    packed = self.packer.make_can_msg("SCC_CONTROL", 0,
      {"aReqRaw": .5, "aReqValue": .3, "StopReq": 0, "JerkUpperLimit": 2., "JerkLowerLimit": 1.})
    def event(service, stamp, values):
      message = log.Event.new_message(logMonoTime=stamp, valid=True)
      message.init(service).from_dict(values)
      return message.to_bytes()
    def can(stamp, *, unrelated=False):
      message = log.Event.new_message(logMonoTime=stamp, valid=True)
      frame = message.init("sendcan", 1)[0]
      frame.address, frame.src, frame.dat = (0x123 if unrelated else packed[0]), packed[2], packed[1]
      return message.to_bytes()
    result, first = [], None
    for raw in chunks:
      with log.Event.from_bytes(raw) as message:
        service, stamp = message.which(), int(message.logMonoTime)
        if service == "initData":
          header_cp = self.raw_cp
          if self.wrong_cp:
            from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import _schema as car_schema
            with car_schema()[1].CarParams.from_bytes(self.raw_cp) as cp:
              changed = cp.as_builder()
              changed.mass += 1.
              header_cp = changed.to_bytes()
          result.append(event("initData", stamp, {"gitCommit": "b" * 40, "params": {"entries": [
            {"key": "CarParams", "value": header_cp}, {"key": "HyundaiCameraSCC", "value": b"1"}]}}))
          continue
        if service == "carControl":
          first = stamp if first is None else first
          output_stamp, send_stamp, pose_event = stamp - 40_000_000, stamp - 30_000_000, stamp - 20_000_000
          pose_source = pose_event + 1 if self.future_pose else stamp - 35_000_000
          if not self.stale_outputs or stamp - first < NS:
            result.append(event("carOutput", output_stamp, {"actuatorsOutput": {"torque": .25}}))
            result.append(can(send_stamp))
          result.append(can(stamp - 10_000_000, unrelated=True))
          result.append(event("liveCalibration", stamp - 45_000_000, {"calStatus": "calibrated", "rpyCalib": [0., 0., 0.]}))
          result.append(event("liveParameters", stamp - 45_000_000, {"valid": True, "sensorValid": True, "posenetValid": True,
            "steerRatio": 16., "stiffnessFactor": 1.15, "angleOffsetDeg": .3, "roll": 0.,
            "angleOffsetValid": True, "steerRatioValid": True, "stiffnessFactorValid": True}))
          result.append(event("liveDelay", stamp - 45_000_000, {"status": "estimated", "validBlocks": 10, "lateralDelay": .2}))
          result.append(event("livePose", pose_event, {"timestamp": pose_source, "inputsOK": True, "sensorsOK": True, "posenetOK": True,
            "angularVelocityDevice": {"x": 0., "y": 0., "z": .2, "valid": True},
            "orientationNED": {"x": 0., "y": 0., "z": 0., "valid": True}}))
          self.expected[stamp] = {"output_stamp": output_stamp, "send_stamp": send_stamp, "pose_stamp": pose_source}
        if service == "carState":
          changed = message.as_builder()
          changed.carState.vEgo, changed.carState.steeringAngleDeg = 15., 5.
          raw = changed.to_bytes()
        result.append(raw)
    # Supplemental events have their original source times, not arrival order.
    def stamp(raw):
      with log.Event.from_bytes(raw) as message:
        return int(message.logMonoTime)
    return sorted(result, key=stamp)


class NativeComparisonAdapterTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.params_preloaded = "openpilot.common.params" in sys.modules
    from opendbc.car import Bus
    from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags
    from opendbc.can.packer import CANPacker
    cls.packer = CANPacker(DBC[CAR.KIA_SORENTO_HEV_4TH_GEN][Bus.pt])
    cls.raw_cp = cp_bytes(carFingerprint=str(CAR.KIA_SORENTO_HEV_4TH_GEN), flags=int(HyundaiFlags.CANFD),
      openpilotLongitudinalControl=True, safetyConfigs=[{"safetyModel": "hyundaiCanfd", "safetyParam": 0}])

  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)

  def tearDown(self):
    if not self.params_preloaded:
      self.assertNotIn("openpilot.common.params", sys.modules)

  def fixture(self, name, **options):
    fixture = NativeAdapterFixture(self.root / name, self.raw_cp, self.packer, **options)
    self.addCleanup(fixture.close)
    pointer = json.loads((fixture.periodic / "latest.json").read_text())
    job = fixture.periodic / "jobs" / pointer["directory"]
    capture = _bundle(job / "window-0000")
    request = json.loads((job / "request.json").read_text())
    capture.update(session_directory=fixture.session, snapshot_directory=job / "snapshot", checkpoint=request["identity"]["checkpoint"])
    resolver = CandidateEvidenceResolver(fixture.targets(), lambda _: {})
    return fixture, capture, resolver

  def side(self, capture, resolver, key):
    result, history, sealed, hashes = resolver._cohort_side(capture, key, generated_comparison_profile())
    self.assertEqual("linked", history["state"])
    self.assertEqual(capture["checkpoint"], sealed["marker"]["checkpoint"])
    self.assertEqual(1, len(hashes))
    return result

  def test_preserved_native_cp_pose_and_actual_scc_keep_values_and_original_clocks(self):
    fixture, capture, resolver = self.fixture("native")
    originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.session.rglob("*") if p.is_file()}
    lateral = self.side(capture, resolver, "LateralTorqueKpV")
    longitudinal = self.side(capture, resolver, "CruiseMaxVals0")
    stamp = min(fixture.expected) + 2 * NS
    lat = next(row for row in lateral["observations"] if row["mono_ns"] == stamp)
    lon = next(row for row in longitudinal["observations"] if row["mono_ns"] == stamp)
    self.assertTrue(lat["valid"] and lon["valid"])
    self.assertAlmostEqual(.3, lat["actual"])
    self.assertAlmostEqual(3., lat["context"]["independent_actual"])
    self.assertEqual("yaw_speed_kinematic", lat["context"]["independent_response_kind"])
    self.assertEqual(fixture.expected[stamp]["pose_stamp"], lat["context"]["independent_mono_ns"])
    self.assertAlmostEqual(-.25, lat["context"]["sent_request"])
    self.assertEqual(fixture.expected[stamp]["output_stamp"], lat["context"]["sent_mono_ns"])
    self.assertAlmostEqual(.3, lon["context"]["sent_request"])
    # The intervening unrelated CAN batch must not retime the SCC sample.
    self.assertEqual(fixture.expected[stamp]["send_stamp"], lon["context"]["sent_mono_ns"])
    self.assertEqual(originals, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals})

  def test_future_pose_and_mismatched_recorded_cp_do_not_create_independent_measurement(self):
    for name, options in (("future", {"future_pose": True}), ("different-cp", {"wrong_cp": True})):
      with self.subTest(source=name):
        fixture, capture, resolver = self.fixture(name, **options)
        lateral = self.side(capture, resolver, "LateralTorqueKpV")
        self.assertTrue(lateral["observations"])
        self.assertTrue(all("independent_actual" not in row["context"] for row in lateral["observations"]))
        if name == "different-cp":
          longitudinal = self.side(capture, resolver, "CruiseMaxVals0")
          self.assertTrue(all("sent_request" not in row["context"] for row in longitudinal["observations"]))
        else:
          longitudinal = self.side(capture, resolver, "CruiseMaxVals0")
          self.assertTrue(any(abs(row["context"].get("sent_request", -10.) - .3) < 1e-8 for row in longitudinal["observations"]))

  def test_expired_outputs_are_not_carried_forward_with_fresh_anchor_timestamps(self):
    fixture, capture, resolver = self.fixture("expired", stale_outputs=True)
    stamp = min(fixture.expected) + 3 * NS
    for key in ("LateralTorqueKpV", "CruiseMaxVals0"):
      with self.subTest(key=key):
        side = self.side(capture, resolver, key)
        early = next(row for row in side["observations"] if row["mono_ns"] == min(fixture.expected) + 500_000_000)
        self.assertAlmostEqual(-.25 if key == "LateralTorqueKpV" else .3, early["context"]["sent_request"])
        row = next(row for row in side["observations"] if row["mono_ns"] == stamp)
        self.assertNotIn("sent_request", row["context"])
        self.assertNotIn("sent_mono_ns", row["context"])
        if key == "LateralTorqueKpV":
          self.assertAlmostEqual(3., row["context"]["independent_actual"])


if __name__ == "__main__":
  unittest.main()
