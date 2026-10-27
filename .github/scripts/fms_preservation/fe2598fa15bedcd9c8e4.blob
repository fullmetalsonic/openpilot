"""Six single-setting goals and evidence-bound manual trials on host fixtures."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import calibration_review as review
from openpilot.selfdrive.carrot.control_advisor.candidate_report import load_candidate_bundle
from openpilot.selfdrive.carrot.control_advisor.consumer_observer import CONSUMER_PATHS, OBSERVER_PATH, consumer_ids_for_key, validate_event
from openpilot.selfdrive.carrot.control_advisor.goals import goal_options, goal_configuration, PROFILE_ENV, OBJECTIVES_ENV
from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.log_candidates import _objectives, calculate_log_candidates
from openpilot.selfdrive.carrot.control_advisor.candidates import _profile, candidate_capabilities
from openpilot.selfdrive.carrot.control_advisor.log_signals import _scc_signals
from openpilot.selfdrive.carrot.control_advisor.tests.test_candidate_lateral import ratio_rows, context, profile, record, signal
from openpilot.selfdrive.carrot.control_advisor.candidate_lateral import propose_family, evaluate_family, capabilities
from openpilot.selfdrive.carrot.control_advisor.tests.test_lateral_observations import builder, fixture_events, ANCHOR
from openpilot.selfdrive.carrot.control_advisor.tests.test_log_candidates import NativeLogCandidateFixture

ENVIRONMENT = {PROFILE_ENV: BUILTIN_PROFILE_PATH, OBJECTIVES_ENV: BUILTIN_OBJECTIVES_PATH}


def config_for(goal):
  paths = goal_configuration(goal, ENVIRONMENT)
  return json.loads(Path(paths["profile_path"]).read_text()), json.loads(Path(paths["objectives_path"]).read_text()), paths


def diagnostics(count=40):
  for index in range(count):
    for axis in ("lateral", "longitudinal"):
      stamp = 1_000_000_000 + index * 10_000_000
      yield {"axis": axis, "mono_ns": stamp, "target": .5 if index % 2 else -.5, "actual": .4,
        "target_mono_ns": stamp - 1, "actual_mono_ns": stamp, "independent_actual": .41,
        "independent_mono_ns": stamp - 2, "sent_request": .6, "sent_mono_ns": stamp - 3,
        "excluded_reasons": []}


@lru_cache(maxsize=1)
def identified_diagnostic_fixture():
  from .response_diagnostic_fixtures import response_rows
  rows = response_rows()
  return rows[-1]["mono_ns"], review.summarize_diagnostics(rows, rows[0]["mono_ns"], rows[-1]["mono_ns"], 250_000_000)


def manual_fixture(key):
  registry = {row["key"]: row for row in load_registry()["items"]}
  settings = {name: (registry[name]["ui"] or {}).get("min", 0) for name in review.FIXED_KEYS}
  settings.update(CustomSR=140, SteerActuatorDelay=20, LatSmoothSec=10, LongActuatorDelay=30, LateralTorqueKiV=0)
  identity = {"identity_version": 1, "state": "observed", "reason": None, "config_sha256": "a" * 64,
    "schema_sha256": "b" * 64, "raw_sha256": "c" * 64, "vin_sha256": None, "car_fingerprint": "fixture",
    "physical_group_id": None, "physical_group_basis": None}
  events, hashes = [], {}
  for index, consumer in enumerate(consumer_ids_for_key(key)):
    source = {path: "d" * 64 for path in (OBSERVER_PATH, CONSUMER_PATHS[consumer])}
    hashes.update(source)
    value = settings[key] * registry[key]["raw_scale"]
    events.append(validate_event({"schema_version": 1, "consumer_id": consumer, "boot_id": "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12,
      "pid": index + 1, "process_instance_id": "a" * 32, "consumer_instance_id": "b" * 32, "sequence": 1,
      "produced_mono_ns": 900_000_000, "cycle_id": 1, "phase": "sampled_use", "fields": {
        key: {"read_value": value, "effective_value": value, "state": "used", "source": "stored", "read_mono_ns": 900_000_000}},
      "context": {}, "dropped_events": 0, "source_hashes": source, "identity_version": 1, "vehicle_identity": identity}))
  end, summary = identified_diagnostic_fixture()
  from .response_diagnostic_fixtures import smoothing_context
  templates, events = events, []
  for template in templates:
    for second in range(73):
      event = deepcopy(template)
      stamp = 900_000_000 if second == 0 else 1_000_000_000 + (second - 1) * 1_000_000_000
      event.update(sequence=second+1, produced_mono_ns=stamp, cycle_id=second*20)
      event["fields"][key]["read_mono_ns"] = stamp
      if template["consumer_id"] == "modeld.action":
        event["context"] = smoothing_context(max(0, second-1), y_std=.2)
        if key == "LatSmoothSec":
          event["fields"][key].update(source="derived", effective_value=event["context"]["lat_smooth_dynamic_s"])
      elif template["consumer_id"] == "controls.lateral_target":
        event["context"] = {"active": True, "path": "lane", "lane_selected": True, "model_target_selected": False}
      elif template["consumer_id"] == "controls.torque" and key == "SteerActuatorDelay":
        event["context"] = {"active": True, "nnff": False, "nnff_lite": False}
        event["fields"][key].update(state="bypassed", read_value=None, effective_value=None)
      events.append(validate_event(event))
  evidence = {"source_kind": "recorded", "epoch": {"id": "fixture", "start_ns": 1_000_000_000, "end_ns": end,
    "settings_raw": settings, "completeness": "observed", "boot_id": events[0]["boot_id"]},
    "vehicle_context": {"code_hashes": hashes, "context": {"vehicle_identity": identity}},
    "artifacts": [{"id": "log", "sha256": "e" * 64}], "consumer_evidence": events}
  goal = next(goal for goal, row in review.SIX_PARAMETER_GOALS.items() if row[0] == key)
  host, config, _ = config_for(goal)
  return evidence, host, config, deepcopy(summary)


class SixParameterBackendTests(unittest.TestCase):
  def test_exact_six_single_goals_preserve_old_reading_and_custom_pair(self):
    goals = goal_options(ENVIRONMENT)
    self.assertEqual({row[0] for row in review.SIX_PARAMETER_GOALS.values()}, {goal["parameter_key"] for goal in goals})
    self.assertEqual(6, len(goals))
    for goal in goals:
      host, config, _ = config_for(goal["id"])
      _profile(host)
      _objectives(config, candidate_capabilities())
      self.assertEqual([goal["parameter_key"]], list(config["objectives"]))
      self.assertNotIn("LateralTorqueKiV", config["objectives"])
    for old in ("tracking", "fms_accel_defaults_v1", "fms_policy_defaults_v1", "torque_pid_experiment_v1"):
      self.assertTrue(Path(goal_configuration(old, ENVIRONMENT)["profile_path"]).is_file())
    self.assertEqual([], goal_options({**ENVIRONMENT, PROFILE_ENV: "custom"}))

  def test_manual_ratio_is_independent_of_live_ratio_number_or_ratio_validity(self):
    for live in (12, 16, 20, None):
      rows = ratio_rows(manual=14., live=16)
      for row in rows:
        if live is None:
          row["signals"].pop("live_steer_ratio")
        else:
          row["signals"]["live_steer_ratio"]["value"] = live
      result = propose_family("CustomSR", context(rows, settings_raw={"CustomSR": 140}), profile(150, 11, 300, 1))
      self.assertAlmostEqual(140, result["raw_estimate"])
    events = fixture_events()
    live = next(row["data"] for row in events if row["service"] == "liveParameters")
    live.update(steerRatioValid=False, steerRatio=float("nan"))
    result = builder(events).observe(ANCHOR)
    self.assertTrue(result["availability"]["manual_geometry"]["available"])
    self.assertFalse(result["availability"]["geometry"]["available"])
    for field in ("valid", "angleOffsetValid", "stiffnessFactorValid"):
      bad = deepcopy(events)
      next(row["data"] for row in bad if row["service"] == "liveParameters")[field] = False
      self.assertFalse(builder(bad).observe(ANCHOR)["availability"]["manual_geometry"]["available"])

  def test_three_timing_goals_require_response_evidence_before_bounded_experiments(self):
    for key in review.TIMING_KEYS:
      evidence, host, config, summary = manual_fixture(key)
      original = deepcopy(evidence)
      value = review.build_calibration_review(evidence, config, host, summary, input_sha256="f" * 64, profile_sha256="1" * 64)
      self.assertEqual("ready", value["state"], value["reasons"])
      self.assertEqual(2, len(value["trial_options"]))
      self.assertEqual(2, value["schema_version"])
      self.assertEqual("experiment_required", value["diagnostics"]["timing"][key]["state"])
      self.assertEqual("unconfirmed", value["diagnostics"]["timing"][key]["trial_plan"]["direction_effect"])
      self.assertEqual(original, evidence)
      for option in value["trial_options"]:
        self.assertEqual(option["current_raw"], option["restore_raw"])
        self.assertEqual(review.trial_option_id(value["baseline"], key, option["current_raw"], option["candidate_raw"]), option["id"])
      changed = deepcopy(value)
      changed["baseline"]["settings_raw"]["CustomSR"] += 1
      with self.assertRaises(ValueError):
        review.validate_calibration_review(changed, evidence=evidence)
      changed = deepcopy(value)
      changed["diagnostics"]["timing"][key]["trial_plan"]["minimum_effect"]["value"] *= 2
      with self.assertRaisesRegex(ValueError, "minimum_effect_unreconciled"):
        review.validate_calibration_review(changed)
      evidence["consumer_evidence"] = []
      held = review.build_calibration_review(evidence, config, host, summary, input_sha256="f" * 64, profile_sha256="1" * 64)
      self.assertEqual(("held", []), (held["state"], held["trial_options"]))

  def test_old_unexcited_summary_no_longer_creates_unconditional_timing_trials(self):
    evidence, host, config, _ = manual_fixture("LongActuatorDelay")
    summary = review.summarize_diagnostics(diagnostics(), evidence["epoch"]["start_ns"], evidence["epoch"]["end_ns"], 250_000_000)
    value = review.build_calibration_review(evidence, config, host, summary, input_sha256="f"*64, profile_sha256="1"*64)
    self.assertEqual(("held", []), (value["state"], value["trial_options"]))

  def test_previous_schema_one_review_remains_readable(self):
    evidence, host, config, summary = manual_fixture("LongActuatorDelay")
    value = review.build_calibration_review(evidence, config, host, summary, input_sha256="f"*64, profile_sha256="1"*64)
    value["schema_version"] = 1
    value.pop("diagnostics")
    self.assertEqual(value, review.validate_calibration_review(value, evidence=evidence))

  def test_manual_geometry_quality_reaches_each_split_with_speed_and_rejects_conflict(self):
    self.assertIn("v_ego", capabilities()["CustomSR"]["required_signals"])
    rows = []
    for index in range(100):
      angle = (-1 if index < 50 else 1) * (.04 + .001 * (index % 50))
      curvature = .35 * angle / 14 + .0002
      rows.append(record(index, {"steering_angle_corrected": signal(angle, "rad"), "vm_curvature_factor": signal(.35, "1/m"),
        "roll_curvature": signal(.0002, "1/m"), "curvature_independent": signal(curvature, "1/m", "synthetic_truth"),
        "v_ego": signal(10 if index % 50 < 25 else 20, "m/s")}, curvature))
    for split in ("fit", "select", "holdout"):
      ctx = context(rows, split, settings_raw={"CustomSR": 140}, dependencies={"manual_ratio_quality_v1": True})
      if split == "fit":
        self.assertAlmostEqual(140, propose_family("CustomSR", ctx, profile(150, 11, 300, 1))["raw_estimate"])
      self.assertEqual("evaluated", evaluate_family("CustomSR", 140, ctx, split, {})["status"])
      ctx["records"][split] = deepcopy(rows)
      for row in ctx["records"][split][50:]:
        row["signals"]["curvature_independent"]["value"] *= 2
      self.assertEqual("blocked", evaluate_family("CustomSR", 140, ctx, split, {})["status"])

  def test_special_or_off_mode_is_not_changed_by_trials(self):
    for key in ("SteerActuatorDelay", "LatSmoothSec"):
      evidence, host, config, summary = manual_fixture(key)
      evidence["epoch"]["settings_raw"][key] = 0
      value = review.build_calibration_review(evidence, config, host, summary, input_sha256="f" * 64, profile_sha256="1" * 64)
      self.assertEqual(("held", []), (value["state"], value["trial_options"]))

  def test_nonzero_fixed_ki_holds_numeric_and_manual_review(self):
    for key in ("CustomSR", "LateralTorqueKpV", "LateralTorqueKf", *review.TIMING_KEYS):
      evidence, host, config, summary = manual_fixture(key)
      evidence["epoch"]["settings_raw"]["LateralTorqueKiV"] = 1
      value = review.build_calibration_review(evidence, config, host, summary, input_sha256="f" * 64, profile_sha256="1" * 64)
      self.assertEqual("held", value["state"])
      self.assertIn("fixed_ki_zero_required", value["reasons"])
      value["state"], value["reasons"] = "ready", []
      with self.assertRaises(ValueError):
        review.validate_calibration_review(value)

  def test_full_raw_metrics_signs_gap_and_graph_budget_are_separate(self):
    evidence, host, config, _ = manual_fixture("SteerActuatorDelay")
    rows = list(diagnostics(8000))
    for row in rows:
      if row["mono_ns"] // 10_000_000 % 5 == 0:
        row["excluded_reasons"] = ["driver_intervention"]
    evidence["epoch"]["end_ns"] = rows[-1]["mono_ns"]
    summary = review.summarize_diagnostics(rows, 1_000_000_000, rows[-1]["mono_ns"], 250_000_000)
    value = review.build_calibration_review(evidence, config, host, summary, input_sha256="f" * 64, profile_sha256="1" * 64)
    self.assertLessEqual(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()), review.MAX_REVIEW_BYTES)
    for axis in ("lateral", "longitudinal"):
      self.assertEqual(8000, value["metrics"][axis]["raw_count"])
      self.assertEqual(6400, value["metrics"][axis]["usable_count"])
      self.assertLessEqual(len(value["series"][axis]["points"]), 600)
      self.assertEqual(3200, value["metrics"][axis]["positive_target"]["count"])
      self.assertTrue(all(type(point[0]) is str for point in value["series"][axis]["points"]))
      self.assertTrue(value["exclusions"][axis]["intervals_truncated"])

  def test_actual_closed_log_without_candidate_still_has_both_series_and_preserves_source(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      fixture = NativeLogCandidateFixture(root)
      before = fixture.source.read_bytes()
      _, _, paths = config_for("long_actuator_delay_v1")
      calculate_log_candidates([fixture.source], paths["profile_path"], paths["objectives_path"], root / "result", session_directory=fixture.session)
      loaded = load_candidate_bundle(root / "result")
      value = loaded["calibration_review"]
      self.assertEqual(("manual_trial", "held", []), (value["purpose"], value["state"], value["trial_options"]))
      self.assertTrue(all(value["series"][axis]["points"] for axis in ("lateral", "longitudinal")))
      self.assertEqual(before, fixture.source.read_bytes())

  def test_scc_control_uses_recorded_cp_bus_and_dbc_and_retains_both_requests(self):
    # Windows lacks native Params; this import-only adapter refuses every read.
    def no_params(*args, **kwargs):
      raise AssertionError("DBC decoding must not read live Params")
    with patch.dict(sys.modules, {"openpilot.common.params": SimpleNamespace(Params=no_params)}):
      self._check_scc_control()

  def _check_scc_control(self):
    from opendbc.car import Bus
    from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags
    from opendbc.can.packer import CANPacker
    car = CAR.KIA_SORENTO_HEV_4TH_GEN
    packed = CANPacker(DBC[car][Bus.pt]).make_can_msg("SCC_CONTROL", 0,
      {"aReqRaw": .5, "aReqValue": .3, "StopReq": 1, "JerkUpperLimit": 2., "JerkLowerLimit": 1.})
    cp = {"state": "observed", "controller_context": {"brand": "hyundai", "openpilot_longitudinal": True,
      "flags": int(HyundaiFlags.CANFD), "hyundai_camera_scc": 1, "safety_config_count": 1, "car_fingerprint": str(car)}}
    event = {"valid": True, "mono_ns": 100, "data": {"frames": [{"address": packed[0], "src": packed[2], "dat": packed[1].hex()}]}}
    signals = _scc_signals(event, cp)
    self.assertAlmostEqual(.5, signals["scc_a_req_raw"]["value"])
    self.assertAlmostEqual(.3, signals["scc_a_req_value"]["value"])
    self.assertEqual(1, signals["scc_stop_req"]["value"])
    event["data"]["frames"][0]["src"] = 1
    self.assertEqual({}, _scc_signals(event, cp))


if __name__ == "__main__":
  unittest.main()
