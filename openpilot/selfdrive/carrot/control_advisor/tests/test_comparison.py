"""Pure comparison gates; native artifact/PC-store integration belongs to the adapter."""
import copy
from dataclasses import asdict
import hashlib
import json
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import comparison as comparison_module
from openpilot.selfdrive.carrot.control_advisor.analysis import Observation
from openpilot.selfdrive.carrot.control_advisor.comparison import evaluate_comparison, validate_public_comparison
from openpilot.selfdrive.carrot.control_advisor.comparison_intervals import encode_exclusions, decode_exclusions
from openpilot.selfdrive.carrot.control_advisor.consumer_observer import CONSUMER_PATHS, OBSERVER_PATH
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry


NS = 1_000_000_000


def digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def freeze_profile(application, profile):
  application["baseline"].update(comparison_profile=copy.deepcopy(profile), comparison_profile_sha256=digest(profile))


def comparison_fixture(*, lateral=False, duration_seconds=None):
  key = "LateralTorqueKpV" if lateral else "CruiseMaxVals0"
  item = next(item for item in load_registry()["items"] if item["key"] == key)
  old, new = (30, 40) if lateral else (100, 130)
  roles = ("controls.torque",) if lateral else ("planner.carrot", "planner.longitudinal")
  code = {path: "a" * 64 for role in roles for path in (OBSERVER_PATH, CONSUMER_PATHS[role])}
  before_ref = {"path": "settings.jsonl", "sequence": 10, "sha256": "b" * 64, "bytes": 1000, "durable": True}
  after_ref = {**before_ref, "sequence": 30, "sha256": "c" * 64, "bytes": 3000}
  target = {"target_id": "pc-only", "session_id": "generated-session", "session_generation": 1,
            "route": "00000000--0123456789", "boot_id": "00000000-0000-0000-0000-000000000001"}
  application = {"schema_version": 1, "request_id": "generated-request", "request_sha256": "d" * 64,
    "source_kind": "pc_fake", "execution_state": "stored", "target": target, "key": key, "old_raw": old,
    "candidate_raw": new, "settings_revision": 1,
    "source_candidate": {"bundle_sha256": "e" * 64, "evidence_sha256": "f" * 64, "profile_sha256": "1" * 64,
                         "code_hashes": code, "source_epoch_id": "old-epoch"},
    "baseline": {"settings_history_reference": before_ref, "dependencies": {}, "dependency_digest": "9" * 64,
                 "dependencies_sha256": digest({})},
    "stored_mono_ns": (30 if duration_seconds is None else duration_seconds + 10) * NS, "receipt_sha256": "2" * 64}
  values = {item["key"]: 0 for item in load_registry()["items"]}
  values[key] = old
  if lateral:
    values.update(LateralTorqueAccelFactor=1500, LateralTorqueFriction=100, LateralTorqueKiV=10,
                  LateralTorqueKf=100, LateralTorqueCustom=1)
  if lateral:
    context_values = {"controls.torque.torque_custom_mode": 1, "controls.torque.nnff": False,
      "controls.torque.nnff_lite": False, "controls.torque.nnff_model_selected": False,
      "controls.vehicle_model.live_steer_ratio": 15., "controls.vehicle_model.stiffness_factor": 1., "controls.lateral_target.path": "model"}
  else:
    context_values = {"planner.longitudinal.mode": "acc", "planner.longitudinal.driving_mode": "Normal",
      "planner.longitudinal.source": "cruise", "planner.carrot.driving_mode": "Normal", "log.accel_mode_factor": 1.}
  contexts = {}
  for name, value in context_values.items():
    consumer, _, field = name.rpartition(".")
    contexts[name] = {"value": value, "source": "longitudinalPlan.myDrivingMode" if name == "log.accel_mode_factor" else f"consumer:{consumer}.context.{field}"}
  axis = "lateral" if lateral else "longitudinal"
  def rows(begin, end, actual, epoch):
    return [asdict(Observation(stamp * 100_000_000, axis, 1., actual, True, True, False, True, epoch,
      {"v_ego": 10., "saturated": False, "output_limited": False,
       "target_mono_ns": stamp * 100_000_000, "actual_mono_ns": stamp * 100_000_000, "car_control_mono_ns": stamp * 100_000_000,
       "response_kind": "steering_model_derived" if lateral else "car_state_acceleration"})) for stamp in range(begin * 10, end * 10 + 1)]
  def side(begin, end, actual, epoch, reference, raw):
    return {"settings_raw": {**values, key: raw}, "dependencies": {}, "history_reference": reference,
      "code_hashes": code, "controller_path": roles[-1], "mode": "acc", "target_unit": "m/s^2", "context": copy.deepcopy(contexts),
      "window": {"start_ns": (begin + 2) * NS, "end_ns": (end - 3) * NS, "finalized_through_ns": (end - 3) * NS},
      "eligible_intervals": [{"start_ns": begin * NS, "end_ns": end * NS}],
      "observations": rows(begin, end, actual, epoch)}
  stored_seconds = application["stored_mono_ns"] // NS
  after_begin = stored_seconds + 6
  after_end = 55 if duration_seconds is None else after_begin + duration_seconds
  before = side(0, 15 if duration_seconds is None else duration_seconds, .7, "old-epoch", before_ref, old)
  after = side(after_begin, after_end, .9, "new-epoch", after_ref, new)
  events = []
  for stamp in range(after_end + 6):
    for index, role in enumerate(roles):
      physical = (new if stamp >= stored_seconds + 1 + index else old) * item["raw_scale"]
      events.append({"schema_version": 1, "consumer_id": role, "boot_id": target["boot_id"], "pid": 42,
        "process_instance_id": "3" * 32, "consumer_instance_id": str(index + 4) * 32, "sequence": stamp + 1,
        "produced_mono_ns": stamp * NS, "cycle_id": stamp, "phase": "sampled_use", "dropped_events": 0,
        "source_hashes": {path: code[path] for path in (OBSERVER_PATH, CONSUMER_PATHS[role])}, "context": {},
        "fields": {key: {"read_value": physical, "effective_value": physical, "state": "loaded" if role == "planner.carrot" else "used",
                         "source": "stored", "read_mono_ns": stamp * NS}}})
      if lateral:
        for sibling, physical in (("LateralTorqueAccelFactor", 1.5), ("LateralTorqueFriction", .1),
                                  ("LateralTorqueKiV", .1), ("LateralTorqueKf", 1.)):
          events[-1]["fields"][sibling] = {"read_value": physical, "effective_value": physical, "state": "used",
                                           "source": "stored", "read_mono_ns": stamp * NS}
  cohort = {"schema_version": 1, "request_id": application["request_id"], "request_sha256": application["request_sha256"],
    "target": target, "source_kind": "generated_native", **{key: target[key] for key in ("session_id", "route", "boot_id")},
    "history_reference": after_ref, "source_hashes": {"native-before": "5" * 64, "native-after": "6" * 64},
    "consumer_events": events, "setting_change_interval": {"after_ns": (stored_seconds - 1) * NS, "before_ns": (stored_seconds + 1) * NS},
    "before": before, "after": after}
  profile = {"schema_version": 1, "profile_id": "explicit-generated-comparison", "version": 1,
    "analysis_profile": {"profile_id": "explicit-generated-driver-mask", "pre_margin_ns": NS // 2, "recovery_ns": NS,
                         "max_gap_ns": NS // 4, "dependency_ns": NS // 2, "max_samples": 10000},
    "settling_ns": 3 * NS, "min_samples": 20, "min_duration_ns": 5 * NS, "max_consumer_age_ns": 2 * NS,
    "speed_bins_mps": [0., 5., 15., 50.], "target_bins": [-2., 0., 2.], "required_context_keys": list(contexts), "model_identity_key": None}
  freeze_profile(application, profile)
  return application, cohort, profile


class ComparisonTests(unittest.TestCase):
  def test_native_receipt_uses_recorded_cohort_and_keeps_family_checks(self):
    application, cohort, profile = comparison_fixture()
    application["source_kind"] = "vehicle_stored"
    # Pure schema fixture; this is not an actual native device measurement.
    cohort["source_kind"] = "recorded"
    profile["required_context_keys"] = []
    freeze_profile(application, profile)
    self.assertEqual("comparison_ready", evaluate_comparison(application, cohort, profile)["state"])
    cohort["before"]["context"].pop("planner.longitudinal.mode")
    rejected = evaluate_comparison(application, cohort, profile)
    self.assertNotEqual("comparison_ready", rejected["state"])
    self.assertIn("comparison_family_context_missing", rejected["reason_codes"])
    self.assertIsNone(rejected["comparison"]["matched"])

  def test_pc_receipt_cannot_relabel_real_cohort_or_native_receipt_generated_cohort(self):
    for source, cohort_source in (("pc_fake", "recorded"), ("vehicle_stored", "generated_native")):
      application, cohort, profile = comparison_fixture()
      application["source_kind"], cohort["source_kind"] = source, cohort_source
      result = evaluate_comparison(application, cohort, profile)
      self.assertEqual("held", result["state"])
      self.assertIn("comparison_request_target_or_source_unbound", result["reason_codes"])

  def test_generated_numeric_delta_is_observational_and_inputs_are_preserved(self):
    application, cohort, profile = comparison_fixture()
    original = copy.deepcopy((application, cohort, profile))
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("comparison_ready", result["state"], result["reason_codes"])
    public = validate_public_comparison(result["comparison"])
    self.assertEqual("observational_vehicle_acceleration", public["scope"])
    self.assertAlmostEqual(.3, public["matched"]["before"]["rmse"])
    self.assertAlmostEqual(.1, public["matched"]["after"]["rmse"])
    self.assertAlmostEqual(-.2, public["matched"]["after_minus_before"]["rmse"])
    self.assertEqual(32 * NS, public["boundary"]["consumer_observed_ns"])
    self.assertEqual(35 * NS, public["boundary"]["after_eligible_from_ns"])
    for flag in ("consumer_application_ack", "causal_improvement_proven", "apply_enabled", "device_qualified"):
      self.assertFalse(public[flag])
    self.assertEqual(application["receipt_sha256"], result["receipt"]["application_receipt_sha256"])
    self.assertEqual(original, (application, cohort, profile))

  def test_held_vehicle_request_never_becomes_executed_from_matching_values(self):
    application, cohort, profile = comparison_fixture()
    application.update(source_kind="vehicle_held", execution_state="held", stored_mono_ns=None, receipt_sha256=None)
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("held", result["state"])
    self.assertIsNone(result["receipt"])
    self.assertIsNone(result["comparison"]["matched"])

  def test_all_registered_consumers_and_effective_values_are_required(self):
    for mutation in ("missing", "read_only"):
      application, cohort, profile = comparison_fixture()
      if mutation == "missing":
        cohort["consumer_events"] = [event for event in cohort["consumer_events"] if event["consumer_id"] != "planner.carrot"]
      else:
        for event in cohort["consumer_events"]:
          if event["produced_mono_ns"] >= 31 * NS:
            event["fields"][application["key"]]["effective_value"] = 1.
      self.assertEqual("held", evaluate_comparison(application, cohort, profile)["state"])

  def test_parameter_dependency_code_and_request_changes_block_comparison(self):
    for change in ("other_key", "code", "request", "dependency", "context"):
      application, cohort, profile = comparison_fixture()
      if change == "other_key":
        cohort["after"]["settings_raw"]["CruiseMaxVals1"] += 1
      elif change == "code":
        cohort["after"]["code_hashes"] = {key: "7" * 64 for key in cohort["after"]["code_hashes"]}
      elif change == "request":
        cohort["request_sha256"] = "7" * 64
      elif change == "dependency":
        cohort["after"]["dependencies"] = {"changed": True}
      else:
        cohort["after"]["context"]["planner.longitudinal.source"]["value"] = "lead0"
      result = evaluate_comparison(application, cohort, profile)
      self.assertNotEqual("comparison_ready", result["state"], change)

  def test_missing_family_context_is_not_filled_from_profile_or_defaults(self):
    application, cohort, profile = comparison_fixture()
    del cohort["after"]["context"]["planner.longitudinal.driving_mode"]
    result = evaluate_comparison(application, cohort, profile)
    self.assertIn("comparison_family_context_missing", result["reason_codes"])
    self.assertNotEqual("comparison_ready", result["state"])

  def test_transition_and_host_settling_are_excluded_from_after_window(self):
    application, cohort, profile = comparison_fixture()
    cohort["after"]["window"]["start_ns"] = 34 * NS
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("consumer_observed", result["state"])
    self.assertIn("comparison_windows_overlap_transition_or_settling", result["reason_codes"])

  def test_context_matching_refuses_disjoint_speed_regimes(self):
    application, cohort, profile = comparison_fixture()
    for row in cohort["after"]["observations"]:
      row["context"]["v_ego"] = 30.
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("consumer_observed", result["state"])
    self.assertIn("comparison_matched_context_unavailable", result["reason_codes"])
    self.assertIsNone(result["comparison"]["matched"])

  def test_matching_reweights_different_speed_mixtures_without_inventing_improvement(self):
    application, cohort, profile = comparison_fixture()
    for name, fraction in (("before", .8), ("after", .2)):
      side = cohort[name]
      rows = [row for row in side["observations"] if side["window"]["start_ns"] <= row["mono_ns"] <= side["window"]["end_ns"]]
      for index, row in enumerate(rows):
        low = index < int(len(rows) * fraction)
        row["context"]["v_ego"] = 10. if low else 30.
        row["actual"] = .9 if low else 0.
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("comparison_ready", result["state"], result["reason_codes"])
    public = result["comparison"]
    self.assertGreater(public["after"]["raw_metrics"]["rmse"], public["before"]["raw_metrics"]["rmse"])
    self.assertEqual(2, public["matched"]["common_bin_count"])
    self.assertAlmostEqual(0., public["matched"]["after_minus_before"]["rmse"])

  def test_driver_pre_recovery_and_dependency_masks_preserve_raw_error(self):
    application, cohort, profile = comparison_fixture()
    for row in cohort["before"]["observations"]:
      if 6 * NS <= row["mono_ns"] <= 7 * NS:
        row.update(driver_override=True, actual=5.)
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("comparison_ready", result["state"], result["reason_codes"])
    before = result["comparison"]["before"]
    self.assertGreater(before["raw_metrics"]["rmse"], before["eligible_metrics"]["rmse"])
    self.assertGreater(before["exclusion_counts"]["driver_intervention"], 0)
    self.assertGreater(before["exclusion_counts"]["intervention_pre_margin"], 0)
    self.assertGreater(before["exclusion_counts"]["recovery"], 0)
    self.assertGreater(before["exclusion_counts"]["dependency_window"], 0)

  def test_exposure_profile_is_explicit_and_minimum_duration_is_enforced(self):
    application, cohort, profile = comparison_fixture()
    profile["min_duration_ns"] = 20 * NS
    freeze_profile(application, profile)
    result = evaluate_comparison(application, cohort, profile)
    self.assertIn("comparison_eligible_exposure_insufficient", result["reason_codes"])
    del profile["speed_bins_mps"]
    self.assertEqual("held", evaluate_comparison(application, cohort, profile)["state"])

  def test_lateral_model_derived_response_is_never_called_independent_vehicle_improvement(self):
    application, cohort, profile = comparison_fixture(lateral=True)
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("comparison_ready", result["state"], result["reason_codes"])
    self.assertEqual("observational_model_derived", result["comparison"]["scope"])
    self.assertFalse(result["comparison"]["causal_improvement_proven"])

  def test_nonfinite_input_and_forged_public_claims_are_rejected(self):
    application, cohort, profile = comparison_fixture()
    ready = evaluate_comparison(application, cohort, profile)["comparison"]
    invalid = copy.deepcopy(ready)
    invalid["consumer_application_ack"] = True
    with self.assertRaises(ValueError):
      validate_public_comparison(invalid)
    invalid = copy.deepcopy(ready)
    invalid["before"]["private_path"] = "must-not-be-public"
    with self.assertRaises(ValueError):
      validate_public_comparison(invalid)
    cohort["after"]["observations"][0]["actual"] = float("nan")
    self.assertEqual("held", evaluate_comparison(application, cohort, profile)["state"])

  def test_candidate_binding_digest_is_distinct_from_raw_dependency_digest(self):
    application, cohort, profile = comparison_fixture()
    application["baseline"]["dependency_digest"] = digest({"settings_raw": cohort["before"]["settings_raw"],
      "dependencies": {}, "vehicle_context": {"controller_path": "planner.longitudinal"},
      "code_hashes": cohort["before"]["code_hashes"], "profile_sha256": application["source_candidate"]["profile_sha256"]})
    self.assertNotEqual(application["baseline"]["dependency_digest"], application["baseline"]["dependencies_sha256"])
    self.assertEqual("comparison_ready", evaluate_comparison(application, cohort, profile)["state"])

  def test_held_target_before_after_boundary_is_excluded_even_without_settling(self):
    application, cohort, profile = comparison_fixture()
    profile["settling_ns"] = 0
    profile["analysis_profile"].update(recovery_ns=0, dependency_ns=0)
    freeze_profile(application, profile)
    for row in cohort["after"]["observations"]:
      row["context"]["target_mono_ns"] = 29 * NS
    result = evaluate_comparison(application, cohort, profile)
    self.assertNotEqual("comparison_ready", result["state"])
    self.assertGreater(result["comparison"]["after"]["exclusion_counts"]["comparison_source_precedes_boundary"], 0)
    self.assertEqual(0, result["comparison"]["after"]["eligible_count"])

  def test_held_source_may_not_cross_a_driver_mask_or_settings_observation_hole(self):
    for kind in ("driver", "settings"):
      application, cohort, profile = comparison_fixture()
      profile["analysis_profile"].update(pre_margin_ns=0, recovery_ns=0, dependency_ns=0, max_gap_ns=10 * NS)
      freeze_profile(application, profile)
      if kind == "driver":
        for row in cohort["after"]["observations"]:
          if row["mono_ns"] == 40 * NS:
            row["driver_override"] = True
          if row["mono_ns"] > 40 * NS:
            row["context"]["target_mono_ns"] = 39 * NS
      else:
        cohort["after"]["eligible_intervals"] = [{"start_ns": 36 * NS, "end_ns": 40 * NS}, {"start_ns": 41 * NS, "end_ns": 55 * NS}]
        for row in cohort["after"]["observations"]:
          if row["mono_ns"] >= 41 * NS:
            row["context"]["target_mono_ns"] = 39 * NS
      result = evaluate_comparison(application, cohort, profile)
      self.assertNotEqual("comparison_ready", result["state"])
      reason = "comparison_source_dependency_masked" if kind == "driver" else "comparison_settings_source_uncertain"
      self.assertGreater(result["comparison"]["after"]["exclusion_counts"][reason], 0)

  def test_a_different_startup_comparison_profile_cannot_reinterpret_a_stored_request(self):
    application, cohort, profile = comparison_fixture()
    profile["settling_ns"] += NS
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("held", result["state"])
    self.assertIn("comparison_profile_unbound", result["reason_codes"])

  def test_settings_scan_holes_do_not_restart_sensor_recovery(self):
    for invalid_sensor in (False, True):
      with self.subTest(invalid_sensor=invalid_sensor):
        application, cohort, profile = comparison_fixture()
        profile["analysis_profile"].update(recovery_ns=2 * NS, dependency_ns=NS // 10)
        freeze_profile(application, profile)
        for side, begin, end in ((cohort["before"], 0, 15), (cohort["after"], 36, 55)):
          side["eligible_intervals"] = [{"start_ns": second * NS + 1, "end_ns": (second + 1) * NS}
                                        for second in range(begin, end)]
        if invalid_sensor:
          next(row for row in cohort["before"]["observations"] if row["mono_ns"] == 6 * NS + 3 * NS // 10)["valid"] = False
        result = evaluate_comparison(application, cohort, profile)
        self.assertEqual("comparison_ready", result["state"], result["reason_codes"])
        before = result["comparison"]["before"]
        self.assertGreater(before["exclusion_counts"]["comparison_settings_source_uncertain"], 0)
        scan = 5 * NS + NS // 10
        fresh = 7 * NS + 2 * NS // 10
        self.assertTrue(any(interval["reason"] == "comparison_settings_source_uncertain"
                            and interval["start_ns"] <= scan <= interval["end_ns"] for interval in before["exclusions"]))
        self.assertEqual(invalid_sensor, any(interval["reason"] == "recovery"
                         and interval["start_ns"] <= fresh <= interval["end_ns"] for interval in before["exclusions"]))
        if invalid_sensor:
          self.assertEqual(1, before["exclusion_counts"]["invalid_signal"])

  def test_three_and_five_minute_public_comparisons_keep_every_exclusion(self):
    for duration in (180, 300):
      with self.subTest(seconds=duration):
        application, cohort, profile = comparison_fixture(duration_seconds=duration)
        profile["analysis_profile"].update(pre_margin_ns=NS // 10, recovery_ns=2 * NS, dependency_ns=NS // 10)
        freeze_profile(application, profile)
        for side in (cohort["before"], cohort["after"]):
          begin, end = (side["observations"][index]["mono_ns"] // NS for index in (0, -1))
          side["eligible_intervals"] = [{"start_ns": second * NS + 1, "end_ns": (second + 1) * NS}
                                        for second in range(begin, end)]
        original_side, intervals = comparison_module._side, []
        def capture(*args, **kwargs):
          value = original_side(*args, **kwargs)
          intervals.append(copy.deepcopy(value[0]["exclusions"]))
          return value
        with patch.object(comparison_module, "_side", side_effect=capture):
          result = evaluate_comparison(application, cohort, profile)
        self.assertEqual("comparison_ready", result["state"], result["reason_codes"])
        public = result["comparison"]
        self.assertEqual(2, public["schema_version"])
        self.assertEqual(public, validate_public_comparison(public))
        self.assertLessEqual(len(json.dumps(public, sort_keys=True, separators=(",", ":")).encode()), 32 * 1024)
        for index, side in enumerate(("before", "after")):
          self.assertEqual(intervals[index], decode_exclusions(public[side]["exclusions"], schema_version=2))

  def test_versioned_public_exclusions_are_strict_and_small_legacy_stays_unchanged(self):
    application, cohort, profile = comparison_fixture()
    legacy = evaluate_comparison(application, cohort, profile)["comparison"]
    self.assertEqual(1, legacy["schema_version"])
    compact = copy.deepcopy(legacy)
    compact["schema_version"] = 2
    for side in ("before", "after"):
      compact[side]["exclusions"] = encode_exclusions(compact[side]["exclusions"])
    self.assertEqual(compact, validate_public_comparison(compact))
    invalid = copy.deepcopy(compact)
    invalid["before"]["exclusions"]["rows"].append([100_000, "0", "1", 1])
    with self.assertRaisesRegex(ValueError, "invalid_public_comparison_exclusions"):
      validate_public_comparison(invalid)
    invalid = copy.deepcopy(compact)
    invalid["schema_version"] = 1
    with self.assertRaisesRegex(ValueError, "invalid_public_comparison_exclusions"):
      validate_public_comparison(invalid)
    application["baseline"]["comparison_profile"] = None
    self.assertEqual("held", evaluate_comparison(application, cohort, profile)["state"])

  def test_learned_factor_friction_and_cached_gains_cannot_confounded_a_single_key_comparison(self):
    for key, change in (("LateralTorqueAccelFactor", "value"), ("LateralTorqueFriction", "value"),
                        ("LateralTorqueFriction", "source"), ("LateralTorqueKiV", "value"), ("LateralTorqueKf", "missing")):
      with self.subTest(key=key, change=change):
        application, cohort, profile = comparison_fixture(lateral=True)
        for event in cohort["consumer_events"]:
          if event["produced_mono_ns"] < 36 * NS:
            continue
          if change == "missing":
            del event["fields"][key]
          else:
            event["fields"][key]["source"] = "learned" if key != "LateralTorqueKiV" else "stored"
            if change == "value":
              event["fields"][key]["effective_value"] += .01
        result = evaluate_comparison(application, cohort, profile)
        self.assertNotEqual("comparison_ready", result["state"])
        expected = "comparison_torque_effective_context_missing" if change == "missing" else "comparison_torque_effective_context_changed"
        self.assertIn(expected, result["reason_codes"])


if __name__ == "__main__":
  unittest.main()
