"""Generated native logs through default calibration and isolated request registration.

Sensor numbers are constructed bench truth, never real driving qualification.
"""
import bz2
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.carrot.control_advisor.analysis_control import AnalysisControl
from openpilot.selfdrive.carrot.control_advisor.apply_requests import ApplyRequestController, IsolatedPCStore
from openpilot.selfdrive.carrot.control_advisor.archive import SegmentArchive
from openpilot.selfdrive.carrot.control_advisor.candidate_report import load_candidate_bundle
from openpilot.selfdrive.carrot.control_advisor.consumer_observer import CONSUMER_PATHS, OBSERVER_PATH
from openpilot.selfdrive.carrot.control_advisor.log_candidates import calculate_log_candidates
from openpilot.selfdrive.carrot.control_advisor.goals import goal_configuration
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema, validate_closed_log
from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver, make_comparison_provider
from openpilot.selfdrive.carrot.control_advisor.native_runtime import NativeRuntime, comparison_profile
from openpilot.selfdrive.carrot.control_advisor.periodic import PeriodicAnalyzer
from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import describe_car_params
from . import test_lateral_log_signals as lateral_fixture
from .test_lateral_observations import CP
from .test_log_candidates import BOOT, ROUTE, NativeLogCandidateFixture
from .test_request_evidence import request_body
from .test_vehicle_store import FakeNativeParams


NS = 1_000_000_000
REPO = Path(__file__).resolve().parents[5]


class NativeCalibrationFixture(NativeLogCandidateFixture):
  """Reuse the native journal clock and actual C1 event factory, with known numbers."""
  session_id = "native-calibration-fixture"

  def __init__(self, root, *, manual=False, missing_torque_consumer=False, keep_open=False, identity_provider=None,
               segment_stride_seconds=80, consumer_samples=63, longitudinal=False):
    self.root, self.now = Path(root).resolve(), 2 * NS
    self.root.mkdir(exist_ok=True)
    self.settings, self.logs = (self.root / name for name in ("settings", "logs"))
    self.session = self.root / "sessions" / self.session_id
    self.session.parent.mkdir()
    for directory in (self.settings, self.logs, self.session):
      directory.mkdir()
    self.values = {item["key"]: 0 for item in load_registry()["items"]}
    self.values.update(LateralTorqueAccelFactor=2800, LateralTorqueFriction=150,
                       LateralTorqueKpV=100, LateralTorqueKiV=10, LateralTorqueKf=100,
                       LateralTorqueCustom=1, SteerRatioRate=100, CustomSR=160 if manual else 0)
    self.longitudinal = longitudinal
    self.profile_path, self.objectives_path = BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
    if longitudinal:
      self.values.update({f"CruiseMaxVals{index}": 100 for index in range(7)})
      goal = goal_configuration("fms_accel_defaults_v1", {
        "CARROT_CONTROL_ADVISOR_AUTO_PROFILE": BUILTIN_PROFILE_PATH,
        "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES": BUILTIN_OBJECTIVES_PATH})
      self.profile_path, self.objectives_path = goal["profile_path"], goal["objectives_path"]
    for key, value in self.values.items():
      (self.settings / key).write_text(str(value), encoding="ascii")
    (self.settings / "CurrentRoute").write_text(ROUTE, encoding="ascii")
    raw_cp = lateral_fixture.cp_bytes(steerControlType="torque",
      lateralTuning={"torque": {"latAccelFactor": 2.8, "friction": .15}})
    (self.settings / "CarParams").write_bytes(raw_cp)
    self.raw_cp, self.phase = raw_cp, -1
    self.segment_stride_seconds, self.consumer_samples = segment_stride_seconds, consumer_samples
    self.missing_torque_consumer = missing_torque_consumer
    self.latest_events = {}
    self.recorder = SettingsRecorder(self.settings, self.session, self.session_id, clock_ns=self.clock,
      identity_provider=identity_provider or (lambda: describe_car_params((self.settings / "CarParams").read_bytes())))
    self.archive = SegmentArchive(self.logs, self.session / "archive", session_id=self.session_id,
      source_mode="live", byte_limit=32 * 1024 * 1024, max_files=64, free_reserve_bytes=0)
    (self.session / "recording.json").write_text(json.dumps({"session_id": self.session_id,
      "source_mode": "live", "clock": {"domain": "native_monotonic", "scale": 1}}), encoding="utf-8")
    try:
      self.append_phase()
    except BaseException:
      self.close()
      raise
    if not keep_open:
      self.close()

  def append_phase(self):
    self.phase += 1
    base = (2 + self.phase * self.segment_stride_seconds) * NS
    self.now = max(self.now, base)
    recorder, archive, raw_cp = self.recorder, self.archive, self.raw_cp
    manual = self.values["CustomSR"] > 10
    identity = describe_car_params(raw_cp)
    observed = recorder.sample()
    revision = observed["durable_revision"]
    for index in range(self.consumer_samples):
      # Observe consumers inside a settings bracket, not during its scan hole.
      stamp = base + 300_000_000 + index * NS
      self.now = stamp
      observations = {
        "controls.vehicle_model": ({
          "SteerRatioRate": self.field(1., state="bypassed" if manual else "used", effective=None if manual else 1.),
          "CustomSR": self.field(16. if manual else 0., state="used" if manual else "bypassed", effective=16. if manual else None),
        }, {"resolved_steer_ratio": 16., "live_steer_ratio": 16., "stiffness_factor": 1.15, "manual_override": manual}),
        "controls.lateral_target": ({
          "SteerActuatorDelay": self.field(None, state="bypassed", source="unknown"),
          "LatSmoothSec": self.field(None, state="bypassed", source="unknown"),
        }, {"active": True, "path": "model", "lane_selected": False, "live_delay_used": False}),
        "controls.torque": ({
          "LateralTorqueAccelFactor": self.field(self.values["LateralTorqueAccelFactor"] * .001, read_value=None),
          "LateralTorqueCustom": self.field(1., state="latched", read_value=None),
          "LateralTorqueFriction": self.field(.15, read_value=None),
          "LateralTorqueKpV": self.field(1., read_value=None), "LateralTorqueKiV": self.field(.1, read_value=None),
          "LateralTorqueKf": self.field(1., read_value=None),
          "LateralTorqueKd": self.field(0., state="unsupported", source="derived", read_value=None),
          "NNFF": self.field(0, state="latched", source="inherited", read_value=None),
          "NNFFLite": self.field(0, state="latched", source="inherited", read_value=None),
          "SteerActuatorDelay": self.field(0., state="bypassed", source="unknown", effective=None),
        }, {"active": True, "torque_custom_mode": 1, "nnff": False, "nnff_lite": False,
            "nnff_model_selected": False, "torque_ff_path": "linear_standard", "derivative_effective": False}),
      }
      if self.longitudinal:
        observations.update({
          "planner.carrot": ({f"CruiseMaxVals{k}": self.field(1., state="loaded") for k in range(7)},
            {"mode": "acc", "driving_mode": "Normal", "personality": 1, "v_ego": 0., "a_ego": .6,
             "group_read_complete": True, "group_read_basis": "group_read_complete"}),
          "planner.longitudinal": ({f"CruiseMaxVals{k}": self.field(1.) for k in range(7)},
            {"mode": "acc", "driving_mode": "Normal", "source": "cruise", "v_ego": 0., "a_ego": .6,
             "accel_limit_base": 1., "accel_limit_final": 1., "group_read_complete": True}),
        })
      for consumer, (fields, context) in observations.items():
        for field in fields.values():
          field["read_mono_ns"] = base if field["read_value"] is not None else None
        if consumer == "controls.torque" and self.missing_torque_consumer:
          continue
        paths = (OBSERVER_PATH, CONSUMER_PATHS[consumer])
        event = {"schema_version": 1, "consumer_id": consumer,
          "boot_id": BOOT, "pid": 42, "process_instance_id": "a" * 32,
          "consumer_instance_id": {"controls.torque": "b", "controls.vehicle_model": "c", "controls.lateral_target": "d",
                                   "planner.carrot": "e", "planner.longitudinal": "f"}[consumer] * 32,
          "sequence": self.phase * self.consumer_samples + index + 1, "produced_mono_ns": stamp,
          "cycle_id": self.phase * self.consumer_samples + index, "phase": "sampled_use",
          "fields": fields, "context": context, "dropped_events": 0,
          "source_hashes": {path: hashlib.sha256((REPO / path).read_bytes()).hexdigest() for path in paths},
          "identity_version": 1, "vehicle_identity": identity}
        recorder.append_consumer_observation(event)
        self.latest_events[consumer] = event
      self.now = base + (index + 1) * NS
      recorder.sample()
    factory = lateral_fixture.LateralLogSignalTests()
    factory.log = _schema()[1]
    # Enough independent 5 Hz pose responses after recovery/poll-hole masks.
    messages = factory.stream(start=base // 100_000_000, end=base // 100_000_000 + 600, raw=raw_cp)
    model = VehicleModel(SimpleNamespace(**CP))
    model.update_params(1.15, 16.)
    curvature_factor = model.curvature_factor(15.)
    for message in messages:
      kind, seconds = message.which(), message.logMonoTime / NS
      if kind == "carOutput":
        message.carOutput.actuatorsOutput.torque = -self.command(seconds)
      elif kind == "liveCalibration":
        message.liveCalibration.rpyCalib = [0., 0., 0.]
      elif kind == "liveParameters":
        message.liveParameters.roll = 0.
      elif kind == "livePose":
        response = 3. * self.command(seconds - .2) + .12
        message.livePose.timestamp = message.logMonoTime
        message.livePose.angularVelocityDevice = {"x": 0., "y": 0., "z": response / 15., "valid": True}
        message.livePose.orientationNED = {"x": 0., "y": 0., "z": 0., "valid": True}
      elif kind == "carState":
        curvature = (3. * self.command(seconds - .2) + .12) / 225.
        message.carState.vEgo = 0. if self.longitudinal else 15.
        message.carState.steeringAngleDeg = math.degrees(curvature * 12.8 / curvature_factor) + .3
      elif kind == "controlsState" and self.longitudinal:
        message.controlsState.longControlState = "pid"
    directory = self.logs / f"{ROUTE}--{self.phase}"
    directory.mkdir()
    self.source = directory / "rlog.bz2"
    self.source.write_bytes(bz2.compress(b"".join(message.to_bytes() for message in messages)))
    start, end = base, base + 59_900_000_000
    diagnostic = {"revision": self.phase + 1, "window_start_ns": start, "window_end_ns": end,
      "profile_id": "generated-native-calibration", "invalidated": False, "partial_window": False,
      "observed_settings_revision": revision, "route_observation": recorder.revision_metadata(revision)["route_observation"]}
    data = json.dumps(diagnostic, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(data).hexdigest()
    (self.session / "diagnostics").mkdir(exist_ok=True)
    (self.session / "diagnostics" / f"{self.phase + 1}-{digest[:16]}.json").write_bytes(data)
    record = {key: value for key, value in diagnostic.items() if key not in {"observed_settings_revision", "route_observation"}}
    reference = recorder.append_diagnostic({**record, "sha256": digest}, settings_revision=revision)["journal"]
    saved = archive.preserve(self.source, window_ns=(start, end), diagnostic_revision=self.phase + 1,
                             settings_revision=revision, journal_ref=reference)
    if saved["state"] != "complete":
      raise AssertionError(saved)
    self.archive_copy = self.session / "archive" / saved["archive_id"] / self.source.name
    recorder.append_archive({key: value for key, value in saved.items() if key not in {"source_relative", "decompressed_bytes"}})
    recorder.append_job_status({"diagnostic_revision": self.phase + 1, "settings_revision": revision, "state": "complete",
      "window_start_ns": start, "window_end_ns": end, "route_observation": diagnostic["route_observation"],
      "settings_history_reference": reference, "actual_boundary_gaps": [], "gap_tolerance_ns": 250_000_000,
      "missing_segments": [], "missing_segments_count": 0, "endpoint_start_covered": True,
      "endpoint_end_covered": True, "completion_scope": "archive_work_only_host_profile",
      "coverage_complete": False, "interpolation_applied": False})
    self.checkpoint = recorder.checkpoint()

  def observe_store(self, store, *, observed_ns=None):
    self.values = store.snapshot()["values"]
    for key, value in self.values.items():
      (self.settings / key).write_text(str(value), encoding="ascii")
    if observed_ns is not None:
      self.now = max(self.now, observed_ns)
    self.append_phase()

  def close(self):
    self.recorder.close()
    self.archive.close()

  @staticmethod
  def field(read, *, state="used", effective="same", source="stored", read_value="same"):
    return {"read_value": read if read_value == "same" else read_value,
            "effective_value": read if effective == "same" else effective,
            "state": state, "source": source, "read_mono_ns": 2 * NS}

  @staticmethod
  def command(seconds):
    return .28 * math.sin(seconds * 1.7) + .07 * math.sin(seconds * 3.1)

  def calculate(self, name="result"):
    destination = self.root / name
    summary = calculate_log_candidates([self.archive_copy], self.profile_path, self.objectives_path,
                                       destination, session_directory=self.session)
    return summary, load_candidate_bundle(destination), destination

  def input_hashes(self):
    hashes = {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for root in (self.settings, self.logs, self.session) for path in root.rglob("*") if path.is_file()}
    for name in (self.profile_path, self.objectives_path):
      hashes["builtin/" + Path(name).name] = hashlib.sha256(Path(name).read_bytes()).hexdigest()
    return hashes


class NativeCalibrationFlowTests(unittest.TestCase):
  def fixture(self, **options):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    return NativeCalibrationFixture(Path(temporary.name), **options)

  def assert_candidate(self, loaded, key, raw):
    row = next(item for item in loaded["report"]["items"] if item["key"] == key)
    self.assertEqual(("search_candidate", raw), (row["status"], row["candidate_raw"]), row)
    self.assertTrue(row["validation"]["split_independent"])
    self.assertTrue(row["validation"]["holdout_objective_pass"])
    self.assertFalse(row["validation"]["independent_route_reproduced"])
    self.assertAlmostEqual({"LateralTorqueAccelFactor": 3., "SteerRatioRate": .8, "CustomSR": 12.8}[key], row["candidate_physical"])
    return row

  def test_default_native_logs_produce_factor_ratio_and_real_request_registration(self):
    fixture = self.fixture()
    before = fixture.input_hashes()
    summary, loaded, destination = fixture.calculate()
    self.assertEqual("complete", summary["state"])
    factor = self.assert_candidate(loaded, "LateralTorqueAccelFactor", 3000)
    self.assert_candidate(loaded, "SteerRatioRate", 80)
    self.assertAlmostEqual(.12, factor["provenance"]["fit_context"]["lateral_accel_offset"], places=5)
    self.assertEqual(47, len(loaded["report"]["items"]))
    self.assertFalse(loaded["apply_enabled"])
    self.assertFalse(loaded["device_qualified"])
    clock = lambda: 70 * NS
    store = IsolatedPCStore(fixture.root / "pc-store", "native-calibration", fixture.values,
                            session_id=fixture.session_id, clock_ns=clock)
    self.addCleanup(store.close)
    resolver = CandidateEvidenceResolver({"native-calibration": {"scope": "pc_simulation", "kind": "static",
      "directory": destination}}, lambda target_id: store.request_binding(), clock_ns=clock)
    actor = ApplyRequestController(fixture.root / "requests", resolver.resolve, pc_store=store, enabled=True, clock_ns=clock)
    self.addCleanup(actor.close)
    context = resolver.candidate_context("native-calibration")
    for key, raw in (("LateralTorqueAccelFactor", 3000), ("SteerRatioRate", 80)):
      body = request_body(context, key=key, request_id="native-" + key)
      self.assertEqual(raw, resolver.resolve(body)["candidate_raw"])
      registered = actor.register(body)
      self.assertEqual("registered", registered["state"])
      self.assertFalse(registered["apply_enabled"])
    self.assertEqual(fixture.values, store.snapshot()["values"])
    self.assertEqual(before, fixture.input_hashes())

  def test_manual_ratio_uses_same_native_objective_without_mode_switch(self):
    fixture = self.fixture(manual=True)
    before = fixture.input_hashes()
    _, loaded, _ = fixture.calculate()
    self.assert_candidate(loaded, "LateralTorqueAccelFactor", 3000)
    self.assert_candidate(loaded, "CustomSR", 128)
    inactive = next(item for item in loaded["report"]["items"] if item["key"] == "SteerRatioRate")
    self.assertIsNone(inactive["candidate_raw"])
    self.assertEqual(before, fixture.input_hashes())

  def test_selected_fms_acceleration_goal_uses_active_speed_node_and_real_request(self):
    """Native speed and mode select a fixed user policy; aEgo is not its target."""
    fixture = self.fixture(longitudinal=True)
    before = fixture.input_hashes()
    _, loaded, destination = fixture.calculate()
    rows = {item["key"]: item for item in loaded["report"]["items"]}
    active = rows["CruiseMaxVals0"]
    self.assertEqual(("search_candidate", 160, 1.6),
                     (active["status"], active["candidate_raw"], active["candidate_physical"]), active)
    self.assertTrue(active["validation"]["holdout_objective_pass"])
    for index in range(1, 7):
      inactive = rows[f"CruiseMaxVals{index}"]
      self.assertIsNone(inactive["candidate_raw"], inactive)
      self.assertIn("candidate_has_no_exposed_effect", inactive["reason_codes"], inactive)
    clock = lambda: 70 * NS
    store = IsolatedPCStore(fixture.root / "pc-store", "native-acceleration", fixture.values,
                            session_id=fixture.session_id, clock_ns=clock)
    self.addCleanup(store.close)
    resolver = CandidateEvidenceResolver({"native-acceleration": {"scope": "pc_simulation", "kind": "static",
      "directory": destination}}, lambda target_id: store.request_binding(), clock_ns=clock)
    actor = ApplyRequestController(fixture.root / "requests", resolver.resolve, pc_store=store, enabled=True, clock_ns=clock)
    self.addCleanup(actor.close)
    context = resolver.candidate_context("native-acceleration")
    body = request_body(context, key="CruiseMaxVals0", request_id="native-acceleration-active-node")
    self.assertEqual(160, resolver.resolve(body)["candidate_raw"])
    registered = actor.register(body)
    self.assertEqual("registered", registered["state"])
    self.assertFalse(registered["apply_enabled"])
    self.assertEqual(fixture.values, store.snapshot()["values"])
    self.assertEqual(before, fixture.input_hashes())

  def publish(self, fixture, analyzer, wall, control, runtime, binding):
    self.assertTrue(analyzer.offer(fixture.checkpoint,
      archive_watermark=hashlib.sha256(fixture.archive_copy.read_bytes()).hexdigest()))
    wall[0] += 180.
    self.assertIsNone(analyzer._process)
    self.assertEqual("waiting", analyzer.tick()["state"])
    coverage = [validate_closed_log(path)["services"]["carState"] for path in fixture.session.glob("archive/*/rlog.bz2")]
    identity = runtime.current_context("current_vehicle")["vehicle_identity"]
    for stamp in (min(row["mono_min_ns"] for row in coverage), max(row["mono_max_ns"] for row in coverage)):
      control.observe(stamp, {"state": "observed", "route": ROUTE}, identity, {})
    self.assertTrue(control.request("manual-phase-" + str(fixture.phase)))
    intent = control.take()
    binding["analysis_binding"] = {"trip_binding": intent["trip_binding"], "cut_revision": intent["cut_revision"]}
    analyzer.request_analysis(intent)
    def tick():
      context = runtime.current_context("current_vehicle")
      analyzer.update_current_context({"session_id": fixture.session_id, "analysis_binding": binding["analysis_binding"],
        "settings_raw": context["settings_raw"], "vehicle_identity": context["vehicle_identity"]})
      status = analyzer.tick()
      control.update(status)
      return status
    status = tick()
    self.assertEqual("running", status["state"], status)
    deadline = time.monotonic() + 60
    while analyzer._process is not None and time.monotonic() < deadline:
      time.sleep(.03)
      status = tick()
    if analyzer._process is not None:
      details = "\n".join(path.read_text(errors="replace")[-4000:] for path in analyzer.root.glob("jobs/*/stderr.txt"))
      self.fail("native calibration child exceeded bounded test time: " + details)
    self.assertEqual("ready", status["state"], status)
    self.assertTrue((analyzer._job / "completion-seal.json").is_file())
    result = json.loads((analyzer._job / "job-result.json").read_text())
    self.assertEqual("whole_trip", result["analysis_mode"])
    self.assertEqual(len(coverage), result["source_count"])
    self.assertEqual((intent["range_start_ns"], intent["requested_end_ns"]), (result["source_start_ns"], result["source_end_ns"]))
    return status

  def test_native_fake_manual_apply_independent_consumer_comparison_and_restore(self):
    """Real composition/publisher/resolver; only physical Params and telemetry are fake."""
    if getattr(self, "retained_root", None) is not None:
      root = Path(self.retained_root).resolve()
      root.mkdir(parents=True, exist_ok=False)
    else:
      temporary = tempfile.TemporaryDirectory()
      self.addCleanup(temporary.cleanup)
      root = Path(temporary.name)
    clock_value = [62 * NS + 10_000]
    clock = lambda: clock_value[0]
    params = FakeNativeParams(root / "native-params")
    raw_cp = lateral_fixture.cp_bytes(steerControlType="torque",
      lateralTuning={"torque": {"latAccelFactor": 2.8, "friction": .15}})
    params.values["CarParams"] = raw_cp
    params.values.update({key: str(value).encode("ascii") for key, value in {
      "LateralTorqueAccelFactor": 2800, "LateralTorqueFriction": 150, "LateralTorqueCustom": 1,
      "LateralTorqueKpV": 100, "LateralTorqueKiV": 10, "LateralTorqueKf": 100, "SteerRatioRate": 100}.items()})
    params.values.update(IsOffroad=b"0", IsOnroad=b"1")
    binding = {"session_id": NativeCalibrationFixture.session_id, "generation": 0,
               "source_mode": "live", "state": "collecting", "settings_observation_fresh": True}
    def session_status():
      return {"source_mode": "live", "state": "collecting", "parking": {
        "source_kind": "native_event", "state": "park_observed", "observed_mono_ns": clock()}}
    runtime = NativeRuntime({"runtime_kind": "native_comma", "paths": {"requests": str(root / "native-requests")},
      "capabilities": {"analysis_configured": True}, "blockers": {"analysis": []}}, params,
      session_status=session_status, session_binding=lambda: binding, clock_ns=clock, boot_id=BOOT)
    self.addCleanup(runtime.close)
    self.assertTrue(runtime.confirm_vehicle()["vehicle_confirmed"])
    fixture = NativeCalibrationFixture(root / "input", keep_open=True, segment_stride_seconds=60, consumer_samples=60,
      identity_provider=lambda: runtime.current_context("current_vehicle")["vehicle_identity"])
    self.addCleanup(fixture.close)
    wall = [0.]
    analyzer = PeriodicAnalyzer(fixture.session, BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH,
      root / "automatic", interval_seconds=180, clock=lambda: wall[0])
    self.addCleanup(analyzer.close)
    control = AnalysisControl(fixture.session_id, BOOT,
      profile_sha256=hashlib.sha256(Path(BUILTIN_PROFILE_PATH).read_bytes()).hexdigest(),
      objectives_sha256=hashlib.sha256(Path(BUILTIN_OBJECTIVES_PATH).read_bytes()).hexdigest())
    # Pass only the already configured test runtime paths into its real child.
    with patch.dict(os.environ, {"PYTHONPATH": os.pathsep.join(str(Path(p).resolve()) for p in sys.path if p)}), \
         patch("openpilot.selfdrive.carrot.control_advisor.periodic.time.monotonic_ns", side_effect=clock):
      self.publish(fixture, analyzer, wall, control, runtime, binding)
      resolver = CandidateEvidenceResolver({"current_vehicle": {"scope": "vehicle", "kind": "periodic",
        "directory": analyzer.root, "session_root": fixture.session.parent}}, runtime.current_context, clock_ns=clock)
      provider = make_comparison_provider(resolver, comparison_profile())
      actor = ApplyRequestController(root / "ledger", resolver.resolve, vehicle_store=runtime,
                                     comparison_provider=provider, enabled=True, clock_ns=clock)
      self.addCleanup(actor.close)
      context = resolver.candidate_context("current_vehicle")
      body = request_body(context, key="LateralTorqueAccelFactor", request_id="native-calibration-apply")
      resolved = resolver.resolve(body)
      self.assertEqual(3000, resolved["candidate_raw"])
      self.assertTrue(actor.register(body)["apply_enabled"])
      stored = actor.execute(body["request_id"])
      self.assertEqual("stored", stored["state"])
      self.assertFalse(stored["consumer_observed"])
      self.assertEqual(b"3000", params.values["LateralTorqueAccelFactor"])
      self.assertEqual(2800, runtime.backup()["integer_settings"]["LateralTorqueAccelFactor"])
      cut = runtime.successful_analysis_cut(body["request_id"])
      self.assertTrue(control.apply_receipt(cut["receipt"],
        {"vehicle_id": control.trip["vehicle_id"], "boot_id": BOOT}, observed_now_ns=clock()))
      analyzer.invalidate_analysis()
      original_archives = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in fixture.session.glob("archive/*/rlog.bz2")}
      # Independent fake consumer reads the native store after its actual adapter CAS.
      fixture.observe_store(runtime, observed_ns=clock())
      used = fixture.latest_events["controls.torque"]["fields"]["LateralTorqueAccelFactor"]
      self.assertIsNone(used["read_value"])
      self.assertAlmostEqual(3., used["effective_value"])
      clock_value[0] = 122 * NS + 10_000
      self.publish(fixture, analyzer, wall, control, runtime, binding)
      self.assertEqual(cut["receipt"]["stored_mono_ns"], control.seen["manual-phase-1"]["range_start_ns"])
      compared = actor.review(body["request_id"])
      restored = runtime.restore_request(body["request_id"], "native-calibration-restore")
      self.assertEqual("stored", restored["state"])
      self.assertFalse(restored["consumer_observed"])
      self.assertEqual(b"2800", params.values["LateralTorqueAccelFactor"])
      self.assertEqual(2, len(params.calls))
      self.assertEqual(original_archives, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in original_archives})
      self.assertEqual("comparison_ready", compared["state"], compared)
      self.assertTrue(compared["consumer_observed"])
      self.assertFalse(compared["comparison"]["causal_improvement_proven"])


if __name__ == "__main__":
  unittest.main()
