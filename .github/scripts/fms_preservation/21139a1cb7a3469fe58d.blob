"""Generated continuous native logs through actual masks, decoder and publication."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.analysis import AnalysisProfile
from openpilot.selfdrive.carrot.control_advisor.analysis_control import AnalysisControl
from openpilot.selfdrive.carrot.control_advisor import log_signals, periodic
from openpilot.selfdrive.carrot.control_advisor.candidate_report import MAX_INPUT_BYTES, MAX_BUNDLE_BYTES, _link_receipt
from openpilot.selfdrive.carrot.control_advisor.evidence import verify_bundle
from openpilot.selfdrive.carrot.control_advisor.candidates import consumer_events
from openpilot.selfdrive.carrot.control_advisor.rlog import validate_closed_log
from openpilot.selfdrive.carrot.control_advisor.vehicle_binding import VehicleBindings
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import describe_car_params
from openpilot.selfdrive.carrot.control_advisor.log_candidates import _sample_response_indices, _sampling_policy, _sampling_clock_basis
from openpilot.selfdrive.carrot.control_advisor.goals import preset_reference
from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
from . import test_lateral_log_signals as lateral_fixtures
from .test_log_signals import BUDGET
from .test_native_calibration_flow import NativeCalibrationFixture
from .test_log_candidates import BOOT, ROUTE, NativeLogCandidateFixture
from .test_periodic import Clock, wait_controller


POLICY = {"version": "native_static_response_5hz_v1", "period_ns": 200_000_000}


class ContinuousLogAnalysisTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)

  def streams(self, *, segment=1, start=50, intervention=False, changed=False):
    factory = lateral_fixtures.LateralLogSignalTests()
    factory.root = self.root
    from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
    from .test_lateral_log_signals import cp_bytes
    factory.log = _schema()[1]
    first = factory.stream(start=20, end=50)
    second = factory.stream(start=start, end=start + 30, raw=cp_bytes(mass=1700.) if changed else None)
    if intervention:
      next(event for event in second if event.which() == "carState").carState.steeringPressed = True
    return factory.write(first, segment=0), factory.write(second, segment=segment)

  def extract(self, paths, **budget):
    profile = AnalysisProfile("continuous-native-test", 150_000_000, 300_000_000, 250_000_000, 0, 10000)
    return log_signals.extract_log_signals(list(paths), analysis_profile=profile,
      budget={**BUDGET, "max_age_ns": 250_000_000, **budget}, continuous=True)

  def test_actual_decoder_carries_pre_margin_recovery_cache_and_finalization(self):
    paths = self.streams(intervention=True)
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    value = self.extract(paths)
    rows = {row["mono_ns"]: row for row in value["rows"]}
    self.assertIn("intervention_pre_margin", rows[4_900_000_000]["excluded_reasons"]["lateral"])
    self.assertIn("driver_intervention", rows[5_000_000_000]["excluded_reasons"]["lateral"])
    self.assertIn("recovery", rows[5_200_000_000]["excluded_reasons"]["lateral"])
    self.assertEqual([], rows[5_500_000_000]["excluded_reasons"]["lateral"])
    self.assertNotIn("unfinalized_tail", rows[4_900_000_000]["excluded_reasons"]["longitudinal"])
    self.assertEqual(value["source_receipts"][0]["finalized_through_ns"], 4_900_000_000)
    self.assertEqual(value["budget_usage"]["decoded_bytes_charged"],
      sum(source["decompressed_bytes"] * 2 for source in value["source_receipts"]))
    spans = {row["lateral_observation"]["provenance"]["epoch_id"] for row in value["rows"]}
    self.assertEqual(1, len(spans))
    self.assertEqual(hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

  def test_each_file_keeps_event_and_decode_caps_while_batch_counts_all_sources(self):
    paths = self.streams()
    value = self.extract(paths)
    events = max(source["event_count"] for source in value["source_receipts"])
    charged = max(source["decoded_bytes_charged"] for source in value["source_receipts"])
    result = self.extract(paths, max_events=events, max_decompressed_bytes=charged)
    self.assertGreater(result["budget_usage"]["events"], events)
    self.assertGreater(result["budget_usage"]["decoded_bytes_charged"], charged)
    with self.assertRaises(ValueError):
      self.extract(paths, max_events=events - 1)
    with self.assertRaises(ValueError):
      self.extract(paths, max_decompressed_bytes=charged - 1)

  def test_missing_overlap_and_gap_sources_are_rejected_without_smaller_fallback(self):
    for segment, start in ((2, 50), (1, 49), (1, 55)):
      with self.subTest(segment=segment, start=start):
        paths = self.streams(segment=segment, start=start)
        with self.assertRaises(ValueError):
          self.extract(paths)

  def test_cp_change_splits_normalization_span_and_never_reuses_old_sensor_context(self):
    value = self.extract(self.streams(changed=True))
    rows = {row["mono_ns"]: row for row in value["rows"]}
    self.assertNotEqual(rows[4_900_000_000]["lateral_observation"]["provenance"]["epoch_id"],
      rows[5_000_000_000]["lateral_observation"]["provenance"]["epoch_id"])
    self.assertIn("recovery", rows[5_000_000_000]["excluded_reasons"]["lateral"])

  def test_preset_sampling_keeps_longitudinal_without_pose_and_reports_anchor_origin(self):
    config = {"sampling": POLICY, "objectives": {"CruiseMaxVals0": {
      "kind": "policy_reference", "reference": preset_reference()}}}
    policy = _sampling_policy(config)
    self.assertEqual("anchor_time", _sampling_clock_basis(config))
    rows = [{"mono_ns": stamp} for stamp in (0, 50_000_000, 200_000_000)]
    selected, audit = _sample_response_indices(rows, policy, clock_basis="anchor_time")
    self.assertEqual({0, 2}, selected)
    self.assertEqual(0, audit["unique_response_count"])
    self.assertEqual(3, audit["anchor_fallback_count"])
    broken = copy.deepcopy(config)
    broken["objectives"]["CruiseMaxVals0"]["reference"]["version"] = True
    with self.assertRaises(ValueError):
      _sampling_policy(broken)

  def test_actual_log_preset_reference_uses_fms_curve_and_real_mode_without_pose(self):
    from openpilot.selfdrive.carrot.control_advisor.log_candidates import calculate_log_candidates
    from openpilot.selfdrive.carrot.control_advisor.candidate_report import load_candidate_bundle
    fixture = NativeLogCandidateFixture(self.root, mode=4)
    defaults = Path(BUILTIN_OBJECTIVES_PATH).parent
    output = self.root / "preset-result"
    calculate_log_candidates([fixture.archive_copy], defaults / "native_acceleration_profile.json",
      defaults / "native_acceleration_objectives.json", output, session_directory=fixture.session)
    loaded = load_candidate_bundle(output)
    latest = json.loads((output / "index.json").read_text())["latest"]
    evidence = json.loads((output / f"generated-{latest}.json").read_text())
    references = {evidence["objectives"][f"CruiseMaxVals{i}"]["reference_signal"] for i in range(7)}
    self.assertEqual(1, len(references))
    reference = references.pop()
    rows = [sample for block in evidence["blocks"] for sample in block["samples"] if reference in sample["signals"]]
    self.assertTrue(rows)
    for row in rows:
      self.assertAlmostEqual(1.6 * 1.2, row["signals"][reference]["value"])
      self.assertEqual("m/s^2", row["signals"][reference]["unit"])
      self.assertNotIn("CruiseMaxVals0", row.get("unavailable_for", {}))
    self.assertEqual(160, evidence["objectives"]["CruiseMaxVals0"]["target_raw"])
    audit = loaded["log_link"]["sampling_audit"]
    self.assertEqual("response_or_anchor", audit["clock_basis"])
    self.assertEqual(0, audit["unique_response_count"])
    self.assertGreater(audit["anchor_fallback_count"], audit["selected_count"])
    self.assertGreater(audit["full_rate_unavailable_for"]["LateralTorqueAccelFactor"]["recorded_reference_unlinked"], 0)

  def test_middle_stored_setting_change_splits_epochs_and_latest_range_is_not_whole_batch(self):
    from openpilot.selfdrive.carrot.control_advisor.log_candidates import calculate_log_candidates
    from openpilot.selfdrive.carrot.control_advisor.candidate_report import load_candidate_bundle
    fixture = NativeCalibrationFixture(self.root, keep_open=True, segment_stride_seconds=60, consumer_samples=60)
    self.addCleanup(fixture.close)
    first = fixture.archive_copy
    fixture.values["LateralTorqueAccelFactor"] = 2900
    (fixture.settings / "LateralTorqueAccelFactor").write_text("2900", encoding="ascii")
    fixture.append_phase()
    second = fixture.archive_copy
    fixture.close()
    before = fixture.input_hashes()
    output = self.root / "changed-result"
    calculate_log_candidates([first, second], BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH,
      output, session_directory=fixture.session)
    index = json.loads((output / "index.json").read_text())
    evidence = [json.loads((output / f"generated-{item['bundle']}.json").read_text()) for item in index["epochs"]]
    observed = [item for item in evidence if item["epoch"]["completeness"] == "observed"]
    self.assertEqual([2800, 2900], [item["epoch"]["settings_raw"]["LateralTorqueAccelFactor"] for item in observed])
    latest = load_candidate_bundle(output)
    latest_input = evidence[-1]
    self.assertEqual(2900, latest_input["epoch"]["settings_raw"]["LateralTorqueAccelFactor"])
    self.assertGreaterEqual(latest["latest_epoch"]["start_ns"], 62_000_000_000)
    self.assertLess(latest["latest_epoch"]["end_ns"] - latest["latest_epoch"]["start_ns"], 60_000_000_000)
    self.assertEqual(2, latest["log_link"]["source_count"])
    self.assertEqual(before, fixture.input_hashes())

  def test_missing_expected_source_blocks_whole_job_without_calculation(self):
    source = {"archive_id": "first", "route": "route", "segment": 0, "start_ns": 100, "end_ns": 200,
              "state": "available", "reason": None, "range_kind": "verified_car_state", "coverage_verified": True,
              "sha256": "a" * 64, "path": "unused"}
    missing = {**source, "archive_id": "second", "segment": 1, "start_ns": None, "end_ns": None,
               "state": "unavailable", "reason": "periodic_segment_missing", "coverage_verified": False, "range_kind": "unknown"}
    snapshot = {"route_observation": {"route": "route", "state": "observed"}, "archive_sources": [source],
                "expected_archive_sources": [source, missing], "snapshot_directory": "unused"}
    identity = {"checkpoint": {}, "code_hashes": {}, "profile_sha256": "b" * 64, "objectives_sha256": "b" * 64}
    request = {"identity": identity, "job_id": periodic._hash(identity), "profile_path": "profile", "objectives_path": "objectives",
               "session_directory": "session", "interval_seconds": 180, "completed_source_keys": []}
    config = json.loads(Path(BUILTIN_OBJECTIVES_PATH).read_text())
    emitted = []
    with patch.object(periodic.threading, "Thread"), patch.object(periodic, "_code_hashes", return_value={}), \
         patch.object(periodic, "hash_file", return_value=("b" * 64, 1)), \
         patch.object(periodic, "_read", side_effect=lambda path, limit: (config if str(path) == "objectives" else request, "b" * 64)), \
         patch.object(periodic, "_atomic", side_effect=lambda path, value, **kwargs: emitted.append(value)), \
         patch("openpilot.selfdrive.carrot.control_advisor.journal_snapshot.create_journal_snapshot", return_value=snapshot), \
         patch("openpilot.selfdrive.carrot.control_advisor.log_candidates.calculate_log_candidates") as calculate:
      self.assertEqual(0, periodic._child(self.root / "request.json"))
    calculate.assert_not_called()
    self.assertEqual(1, len(emitted))
    self.assertEqual(("partial", 2, "continuous_batch"), (emitted[0]["state"], emitted[0]["source_count"], emitted[0]["analysis_mode"]))
    self.assertEqual(1, len(emitted[0]["windows"]))
    self.assertEqual("periodic_segment_missing", emitted[0]["windows"][0]["reason"])
    self.assertIsNone(emitted[0]["source_start_ns"])
    self.assertIsNone(emitted[0]["source_end_ns"])

  def test_actual_three_and_five_minute_child_publish_one_accumulated_window(self):
    pythonpath = os.pathsep.join(str(path) for path in sys.path if path)
    for interval, count in ((180, 3), (300, 5)):
      with self.subTest(interval=interval), patch.dict(os.environ, {"PYTHONPATH": pythonpath}):
        bindings = VehicleBindings(self.root / f"binding-{interval}", BOOT)
        self.addCleanup(bindings.close)
        raw_cp = lateral_fixtures.cp_bytes(steerControlType="torque",
          lateralTuning={"torque": {"latAccelFactor": 2.8, "friction": .15}})
        identity = bindings.confirm(describe_car_params(raw_cp))
        fixture = NativeCalibrationFixture(self.root / str(interval), keep_open=True, segment_stride_seconds=60, consumer_samples=60,
          identity_provider=lambda: identity)
        self.addCleanup(fixture.close)
        for _ in range(count - 1):
          fixture.append_phase()
        before = fixture.input_hashes()
        coverage = [validate_closed_log(path)["services"]["carState"] for path in fixture.session.glob("archive/*/rlog.bz2")]
        control = AnalysisControl(fixture.session_id, BOOT,
          profile_sha256=hashlib.sha256(Path(BUILTIN_PROFILE_PATH).read_bytes()).hexdigest(),
          objectives_sha256=hashlib.sha256(Path(BUILTIN_OBJECTIVES_PATH).read_bytes()).hexdigest())
        self.assertEqual(raw_cp, fixture.raw_cp)
        for stamp in (min(row["mono_min_ns"] for row in coverage), max(row["mono_max_ns"] for row in coverage)):
          control.observe(stamp, {"state": "observed", "route": ROUTE}, identity, {})
        clock = Clock()
        controller = periodic.PeriodicAnalyzer(fixture.session, BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH,
          self.root / f"automatic-{interval}", interval_seconds=interval, clock=clock)
        self.addCleanup(controller.close)
        controller.offer(fixture.recorder.checkpoint())
        controller.tick()
        clock.now = interval
        controller.tick()
        self.assertIsNone(controller._process)
        self.assertTrue(control.request("manual-whole-trip"))
        intent = control.take()
        self.assertEqual(interval * 1_000_000_000 - 100_000_000, intent["requested_end_ns"] - intent["range_start_ns"])
        controller.request_analysis(intent)
        controller.update_current_context({"session_id": fixture.session_id,
          "analysis_binding": {"trip_binding": intent["trip_binding"], "cut_revision": intent["cut_revision"]},
          "settings_raw": fixture.values, "vehicle_identity": identity})
        state = wait_controller(controller, seconds=90)
        self.assertEqual("ready", state["state"], state)
        result = periodic.read_periodic_result(controller.root)
        self.assertEqual(count, result["attempt"]["source_count"])
        self.assertEqual(1, result["attempt"]["processed_windows"])
        self.assertEqual(1, len(result["attempt"]["windows"]))
        self.assertEqual(count, result["log_link"]["source_count"])
        audit = result["log_link"]["sampling_audit"]
        whole_trip = result["log_link"]["whole_trip"]
        epoch = result["latest_epoch"]
        self.assertEqual(interval * 10, whole_trip["raw_anchor_count"])
        self.assertEqual({"start_ns": intent["range_start_ns"], "end_ns": intent["requested_end_ns"]}, whole_trip["source_range"])
        self.assertLessEqual(audit["selected_count"], count * 300)
        self.assertEqual("response_time", audit["clock_basis"])
        self.assertEqual(0, audit["anchor_fallback_count"])
        self.assertEqual(interval * 1_000_000_000 - 100_000_000,
          result["attempt"]["source_end_ns"] - result["attempt"]["source_start_ns"])
        # The first anchor can predate the first completed stored-value scan.
        self.assertLessEqual(0, epoch["start_ns"] - result["attempt"]["source_start_ns"])
        self.assertLessEqual(epoch["start_ns"] - result["attempt"]["source_start_ns"], 100_000_000)
        self.assertEqual(result["attempt"]["source_end_ns"], epoch["end_ns"])
        rows = {item["key"]: item for item in result["report"]["items"]}
        self.assertEqual(("search_candidate", 3000), (rows["LateralTorqueAccelFactor"]["status"], rows["LateralTorqueAccelFactor"]["candidate_raw"]))
        self.assertEqual(("search_candidate", 80), (rows["SteerRatioRate"]["status"], rows["SteerRatioRate"]["candidate_raw"]))
        pointer = json.loads((controller.root / "latest.json").read_text())
        job = controller.root / "jobs" / pointer["directory"]
        self.assertEqual("whole_trip", json.loads((job / "job-result.json").read_text())["analysis_mode"])
        self.assertTrue((job / "completion-seal.json").is_file())
        collection = job / "window-0000"
        self.assertFalse((job / "window-0001").exists())
        latest = json.loads((collection / "index.json").read_text())["latest"]
        generated = collection / f"generated-{latest}.json"
        self.assertLessEqual(generated.stat().st_size, MAX_INPUT_BYTES)
        evidence = json.loads(generated.read_text())
        self.assertEqual(count, len(evidence["source_collection"]["sources"]))
        self.assertEqual(audit["selected_count"], sum(len(block["samples"]) for block in evidence["blocks"]))
        self.assertEqual({"controls.torque", "controls.vehicle_model"}, {event["consumer_id"] for event in consumer_events(evidence)})
        bundle = collection / latest
        manifest = verify_bundle(bundle, byte_limit=MAX_BUNDLE_BYTES)
        self.assertLessEqual(manifest["result"]["bytes"] + sum(item["bytes"] for item in manifest["files"]), MAX_BUNDLE_BYTES)
        forged = copy.deepcopy(result["log_link"]["sampling_audit"])
        forged["full_rate_unavailable_for"] = {"private/path": {"recorded_dependency_unavailable": 1}}
        receipt = json.loads((bundle / manifest["files"][2]["path"]).read_text())
        receipt["sampling_audit"] = forged
        with self.assertRaises(ValueError):
          _link_receipt(receipt)
        controller.close()
        # Publishing uses a pinned snapshot and never rewrites the native inputs.
        self.assertEqual(before, fixture.input_hashes())


if __name__ == "__main__":
  unittest.main()
