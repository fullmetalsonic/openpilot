"""Generated native CP bytes and sensor events through the real log extractor."""
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
from opendbc.car.vehicle_model import VehicleModel
from types import SimpleNamespace
from openpilot.common.transformations.orientation import rot_from_euler
from openpilot.selfdrive.carrot.control_advisor.log_signals import extract_log_signals
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema, decode_recorded_car_params, iter_events
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import _schema as car_schema, describe_car_params, MAX_CP_BYTES
from openpilot.selfdrive.carrot.control_advisor.tests.test_log_signals import PROFILE, BUDGET
from openpilot.selfdrive.carrot.control_advisor.tests.test_lateral_observations import CP, RPY, ANGULAR


NS = 1_000_000_000


def cp_bytes(**changes):
  _, schema, _ = car_schema()
  cp = schema.CarParams.new_message()
  cp.carFingerprint, cp.brand, cp.carVin = "SYNTHETIC_C1_CP", "hyundai", "1HGCM82633A004352"
  for key, value in {**CP, **changes}.items():
    setattr(cp, key, value)
  return cp.to_bytes()


class LateralLogSignalTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    _, cls.log = _schema()

  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)

  def event(self, kind, stamp, data=None, valid=True):
    message = self.log.Event.new_message(logMonoTime=stamp, valid=valid)
    child = message.init(kind)
    for key, value in (data or {}).items():
      setattr(child, key, value)
    return message

  def header(self, raw, *, duplicate=False):
    message = self.event("initData", 10, {"gitCommit": "a" * 40})
    entries = [{"key": "CruiseMaxVals2", "value": b"100"}, {"key": "ApiToken", "value": b"SYNTHETIC_PRIVATE_TOKEN"}]
    if raw is not None:
      entries.append({"key": "CarParams", "value": raw})
      if duplicate:
        entries.append({"key": "CarParams", "value": raw})
    message.initData.params = {"entries": entries}
    return message

  def stream(self, *, start=20, end=70, raw=None, omitted=None, repeated=False, changed=False,
             future=False, override=False, runtime_changed=False):
    raw = cp_bytes() if raw is None else raw
    result = [self.header(raw), self.event("sentinel", start * 100_000_000 - 1, {"type": "startOfSegment"})]
    for index in range(start, end):
      stamp, seconds = index * 100_000_000, index / 10
      if index == 50 and (repeated or changed):
        result.append(self.header(cp_bytes(mass=1700.) if changed else raw))
      if index == 50 and runtime_changed:
        result.append(self.event("carParams", stamp, {**CP, "mass": 1700.}))
      result.append(self.event("carControl", stamp, {"enabled": True, "latActive": True, "longActive": True}))
      controls = self.event("controlsState", stamp)
      torque = controls.controlsState.lateralControlState.init("torqueState")
      torque.active, torque.desiredLateralAccel, torque.actualLateralAccel = True, .4, .3
      result.extend((controls, self.event("longitudinalPlan", stamp, {"aTarget": .7, "myDrivingMode": 3})))
      output = self.event("carOutput", stamp)
      output.carOutput.actuatorsOutput.torque = .01 * seconds
      result.append(output)
      result.append(self.event("liveCalibration", stamp, {"calStatus": "calibrated", "rpyCalib": RPY}))
      result.append(self.event("liveDelay", stamp, {"status": "estimated", "validBlocks": 10, "lateralDelay": .2}))
      result.append(self.event("liveParameters", stamp, {"valid": True, "sensorValid": True, "posenetValid": True,
                    "steerRatio": 16., "stiffnessFactor": 1.15, "angleOffsetDeg": .3, "roll": .06,
                    "angleOffsetValid": True, "steerRatioValid": True, "stiffnessFactorValid": True}))
      if index % 2 == 0:
        result.append(self.event("livePose", stamp, {"timestamp": stamp + 1 if future else stamp - 20_000_000,
                      "inputsOK": True, "sensorsOK": True, "posenetOK": True,
                      "angularVelocityDevice": {"x": ANGULAR[0], "y": ANGULAR[1], "z": ANGULAR[2], "valid": True},
                      "orientationNED": {"x": .04, "y": -.02, "z": .01, "valid": True}}))
      result.append(self.event("carState", stamp, {"canValid": True, "gearShifter": "drive", "vEgo": 10 + seconds,
                    "aEgo": .6, "steeringAngleDeg": 5 + .4 * seconds, "steeringPressed": override and index == 49}))
    result.append(self.event("sentinel", end * 100_000_000, {"type": "endOfSegment"}))
    return [item for item in result if item.which() != omitted]

  def write(self, messages, *, route="00000001--abcdefghij", segment=0):
    import zstandard
    blobs = []
    for message in messages:
      blobs.append(message.to_bytes())
      message.clear_write_flag()
    path = self.root / f"{route}--{segment}.rlog.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress(b"".join(blobs)))
    return path

  def extract(self, paths, **options):
    return extract_log_signals(paths, analysis_profile=PROFILE, budget={**BUDGET, **options})

  def test_native_cp_raw_to_projected_sensor_numbers_and_privacy(self):
    raw = cp_bytes()
    path = self.write(self.stream(raw=raw))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = self.extract([path])
    decoded = result["initial_snapshots"][0]["recorded_car_params"]
    identity = describe_car_params(raw)
    self.assertEqual(decoded["state"], "observed")
    self.assertEqual(set(decoded["values"]), set(CP))
    for key in CP:
      self.assertAlmostEqual(decoded["values"][key], CP[key], places=5)
    for key in ("raw_sha256", "config_sha256", "schema_sha256"):
      self.assertEqual(decoded[key], identity[key])
    self.assertFalse(decoded["recording_schema_verified"])
    row = next(item for item in result["rows"] if item["mono_ns"] == 5 * NS)
    metadata, signals = row["lateral_observation"], row["signals"]
    self.assertTrue(metadata["availability"]["torque_response"]["available"], metadata)
    self.assertTrue(metadata["availability"]["geometry"]["available"], metadata)
    self.assertEqual(row["excluded_reasons"]["lateral"], [])
    self.assertEqual(row["excluded_reasons"]["longitudinal"], [])
    yaw = float((rot_from_euler(RPY).T @ np.array(ANGULAR))[2])
    self.assertAlmostEqual(signals["sent_torque"]["value"], -.0478, places=6)
    self.assertAlmostEqual(signals["lateral_accel_independent"]["value"], 14.78 * yaw - math.sin(.04) * 9.81, places=6)
    self.assertAlmostEqual(signals["curvature_independent"]["value"], yaw / 14.98, places=7)
    model = VehicleModel(SimpleNamespace(**CP))
    model.update_params(1.15, 16.)
    self.assertAlmostEqual(signals["vm_curvature_factor"]["value"], model.curvature_factor(14.98), places=7)
    self.assertAlmostEqual(signals["roll_curvature"]["value"], model.roll_compensation(.06, 14.98), places=7)
    self.assertEqual(metadata["provenance"]["response_mono_ns"], 4_980_000_000)
    self.assertEqual(metadata["provenance"]["pose_event_mono_ns"], 5 * NS)
    self.assertEqual(metadata["epoch_kind"], "recorded_cp_span")
    self.assertFalse(metadata["provenance"]["consumer_mapping_verified"])
    self.assertEqual(len(result["rows"]), 50)
    following = next(item for item in result["rows"] if item["mono_ns"] == 5_100_000_000)
    self.assertEqual(following["lateral_observation"]["provenance"]["response_mono_ns"], 4_980_000_000)
    self.assertAlmostEqual(following["signals"]["measured_a_ego"]["value"], .6, places=6)
    serialized = json.dumps(result)
    for private in ("SYNTHETIC_PRIVATE_TOKEN", "ApiToken", "1HGCM82633A004352", "vin_sha256", "SYNTHETIC_C1_CP", raw.hex()):
      self.assertNotIn(private, serialized)
    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

  def test_missing_duplicate_corrupt_cp_hold_lateral_only(self):
    for raw, duplicate, reason in ((None, False, "recorded_cp_missing"), (b"", False, "recorded_cp_empty"),
                                   (b"not capnp", False, "recorded_car_params_decode_unavailable"),
                                   (cp_bytes(), True, "recorded_cp_duplicate"),
                                   (cp_bytes(mass=0.), False, "recorded_cp_vm_values_invalid")):
      messages = self.stream()
      messages[0] = self.header(raw, duplicate=duplicate)
      with self.subTest(reason=reason):
        result = self.extract([self.write(messages)])
        self.assertEqual(len(result["rows"]), 50)
        row = result["rows"][30]
        self.assertIn(reason, row["lateral_observation"]["reason_codes"])
        self.assertNotIn("lateral_accel_independent", row["signals"])
        self.assertAlmostEqual(row["signals"]["accel_mode_factor"]["value"], 1.)
        self.assertEqual(row["excluded_reasons"]["longitudinal"], [])
    self.assertEqual(decode_recorded_car_params(b"x" * (MAX_CP_BYTES + 1))["reason"], "recorded_cp_size_or_type_invalid")
    messages = self.stream()
    messages[0] = self.header(b"x" * (MAX_CP_BYTES + 1))
    oversized = self.extract([self.write(messages)], max_event_bytes=2 * MAX_CP_BYTES)
    self.assertEqual(len(oversized["rows"]), 50)
    self.assertIn("recorded_cp_size_or_type_invalid", oversized["rows"][30]["lateral_observation"]["reason_codes"])

  def test_repeat_old_header_keeps_span_changed_cp_resets_support(self):
    repeated = self.extract([self.write(self.stream(repeated=True))])["rows"]
    previous, current = repeated[29], repeated[30]
    self.assertEqual(previous["lateral_observation"]["provenance"]["epoch_id"], current["lateral_observation"]["provenance"]["epoch_id"])
    self.assertTrue(current["lateral_observation"]["availability"]["torque_response"]["available"])
    changed = self.extract([self.write(self.stream(changed=True))])["rows"]
    self.assertNotEqual(changed[29]["lateral_observation"]["provenance"]["epoch_id"], changed[30]["lateral_observation"]["provenance"]["epoch_id"])
    self.assertFalse(changed[30]["lateral_observation"]["availability"]["torque_response"]["available"])
    runtime = self.extract([self.write(self.stream(runtime_changed=True))])["rows"]
    self.assertIn("recorded_cp_changed", runtime[30]["lateral_observation"]["reason_codes"])

  def test_contiguous_route_uses_original_cp_span_other_routes_do_not(self):
    first = self.write(self.stream(start=20, end=50), segment=0)
    second = self.write(self.stream(start=50, end=80), segment=1)
    result = self.extract([first, second])
    row = result["rows"][30]
    self.assertTrue(row["lateral_observation"]["availability"]["torque_response"]["available"], row)
    self.assertEqual(row["lateral_observation"]["provenance"]["source_id"], result["rows"][0]["source_id"])
    self.assertNotEqual(row["source_id"], row["lateral_observation"]["provenance"]["source_id"])
    other = self.write(self.stream(start=50, end=80), route="00000002--abcdefghij", segment=1)
    result = self.extract([first, other])
    self.assertFalse(result["rows"][30]["lateral_observation"]["availability"]["torque_response"]["available"])

  def test_sensor_availability_and_driver_masks_remain_separate(self):
    for omitted, held, available in (("liveDelay", "torque_response", "geometry"),
                                    ("liveParameters", "geometry", "torque_response"),
                                    ("liveCalibration", "pose", None)):
      result = self.extract([self.write(self.stream(omitted=omitted))])
      row = result["rows"][30]
      self.assertFalse(row["lateral_observation"]["availability"][held]["available"])
      if available:
        self.assertTrue(row["lateral_observation"]["availability"][available]["available"])
      self.assertEqual(len(result["rows"]), 50)
      self.assertEqual(row["excluded_reasons"]["longitudinal"], [])
    future = self.extract([self.write(self.stream(future=True))])["rows"][30]
    self.assertIn("pose_timestamp_invalid", future["lateral_observation"]["reason_codes"])
    intervention = self.extract([self.write(self.stream(override=True))])["rows"]
    self.assertIn("driver_intervention", intervention[29]["excluded_reasons"]["lateral"])
    self.assertFalse(intervention[30]["lateral_observation"]["availability"]["torque_response"]["available"])

  def test_invalid_lateral_options_and_small_buffer_keep_explicit_outcomes(self):
    path = self.write(self.stream())
    for options in ({"lateral_max_alignment_ns": 0}, {"lateral_max_buffer_events": True}, {"lateral_max_buffer_events": 100001}):
      with self.subTest(options=options), self.assertRaisesRegex(ValueError, "lateral observation bounds"):
        self.extract([path], **options)
    rows = self.extract([path], lateral_max_buffer_events=50)["rows"]
    self.assertEqual(len(rows), 50)
    self.assertFalse(rows[30]["lateral_observation"]["availability"]["torque_response"]["available"])
    self.assertTrue(rows[30]["lateral_observation"]["availability"]["geometry"]["available"])

  def test_native_sensor_faults_and_segment_gap_are_not_hidden(self):
    for mode in ("invalid_pose", "regression", "stale_calibration"):
      messages = self.stream()
      if mode == "invalid_pose":
        for message in messages:
          if message.which() == "livePose":
            message.livePose.sensorsOK = False
      elif mode == "stale_calibration":
        messages = [message for message in messages if message.which() != "liveCalibration" or message.logMonoTime == 2 * NS]
      else:
        position = next(i for i, message in enumerate(messages) if message.which() == "carState" and message.logMonoTime == 5 * NS)
        invalid = self.event("livePose", 4_800_000_000)
        messages.insert(position, invalid)
      with self.subTest(mode=mode):
        result = self.extract([self.write(messages)])
        row = result["rows"][30]
        self.assertFalse(row["lateral_observation"]["availability"]["pose"]["available"])
        self.assertNotIn("lateral_accel_independent", row["signals"])
        self.assertEqual(len(result["rows"]), 50)
        self.assertEqual(row["excluded_reasons"]["longitudinal"], [])
    first = self.write(self.stream(start=20, end=50), segment=0)
    gap = self.write(self.stream(start=60, end=90), segment=1)
    rows = self.extract([first, gap])["rows"]
    self.assertFalse(rows[30]["lateral_observation"]["availability"]["torque_response"]["available"])
    self.assertNotEqual(rows[29]["lateral_observation"]["provenance"]["epoch_id"], rows[30]["lateral_observation"]["provenance"]["epoch_id"])
    from openpilot.selfdrive.carrot.control_advisor.candidate_report import ENGINE_FILES
    self.assertIn("lateral_observations.py", ENGINE_FILES)


if __name__ == "__main__":
  unittest.main()
