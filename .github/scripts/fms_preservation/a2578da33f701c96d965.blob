"""Synthetic state/time tests; no device thresholds or vehicle claims."""

from dataclasses import replace
import importlib.util
import json
import math
from pathlib import Path
import sys
import unittest


SPEC = importlib.util.spec_from_file_location("advisor_analysis_under_test", Path(__file__).parents[1] / "analysis.py")
analysis = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analysis
SPEC.loader.exec_module(analysis)
Observation, AnalysisProfile, analyze = analysis.Observation, analysis.AnalysisProfile, analysis.analyze


def profile(**changes):
  values = {"profile_id": "synthetic-state-fixture-v1", "pre_margin_ns": 0, "recovery_ns": 0,
            "max_gap_ns": 10, "dependency_ns": 0, "max_samples": 100}
  values.update(changes)
  return AnalysisProfile(**values)


def observation(t, **changes):
  values = {"mono_ns": t, "axis": "lateral", "target": 0.0, "actual": float(t), "active": True,
            "valid": True, "driver_override": False, "recovery_ready": True, "epoch": "a"}
  values.update(changes)
  return Observation(**values)


def epoch(result, axis="lateral", name="a"):
  return result["axes"][axis]["epochs"][name]


def reason_count(summary, reason):
  return sum(interval["sample_count"] for interval in summary["exclusions"] if interval["reason"] == reason)


class AnalysisTest(unittest.TestCase):
  def test_known_raw_and_eligible_metrics(self):
    rows = [observation(t, actual=float(t - 2)) for t in range(5)]
    result = analyze(rows, profile())
    summary = epoch(result)
    self.assertEqual(summary["eligible_count"], 5)
    self.assertEqual(summary["raw_metrics"], summary["eligible_metrics"])
    self.assertEqual(summary["eligible_metrics"]["bias"], 0.0)
    self.assertAlmostEqual(summary["eligible_metrics"]["rmse"], math.sqrt(2))
    self.assertEqual(summary["eligible_metrics"]["p95_absolute_error"], 2.0)
    self.assertEqual(result["finalized_through_ns"], 4)

  def test_initial_recovery_and_large_error_remain_visible(self):
    rows = [observation(t, actual=1_000_000.0) for t in range(6)]
    summary = epoch(analyze(rows, profile(recovery_ns=2)))
    self.assertEqual(summary["eligible_count"], 4)
    self.assertEqual(reason_count(summary, "recovery"), 2)
    self.assertEqual(summary["eligible_metrics"]["rmse"], 1_000_000.0)
    self.assertEqual(summary["raw_metrics"]["count"], 6)

  def test_intervention_pre_margin_recovery_and_provisional_tail(self):
    rows = [observation(t, driver_override=t == 5) for t in range(14)]
    result = analyze(rows, profile(pre_margin_ns=2, recovery_ns=2))
    summary = epoch(result)
    # Eligible timestamps are 2, 8, 9, 10, 11; 12/13 remain provisional.
    self.assertEqual(result["finalized_through_ns"], 11)
    self.assertEqual((summary["eligible_count"], summary["excluded_count"], summary["provisional_count"]), (5, 7, 2))
    self.assertEqual(summary["eligible_metrics"]["bias"], 8.0)
    self.assertEqual(reason_count(summary, "intervention_pre_margin"), 2)
    self.assertEqual(reason_count(summary, "driver_intervention"), 1)
    self.assertEqual(summary["raw_metrics"]["count"], 14)

  def test_pre_mask_crosses_epoch_and_recovery_resets(self):
    rows = [observation(t, epoch="a" if t < 4 else "b", driver_override=t == 5) for t in range(13)]
    result = analyze(rows, profile(pre_margin_ns=2, recovery_ns=2))
    before, after = epoch(result), epoch(result, name="b")
    self.assertEqual(before["eligible_count"], 1)  # only t=2, t=3 is before intervention
    self.assertEqual(before["eligible_metrics"]["bias"], 2.0)
    self.assertEqual(reason_count(before, "intervention_pre_margin"), 1)
    self.assertEqual(after["eligible_count"], 3)  # t=8..10
    self.assertEqual(after["eligible_metrics"]["bias"], 9.0)
    self.assertEqual(after["provisional_count"], 2)

  def test_repeated_intervention_restarts_recovery(self):
    rows = [observation(t, driver_override=t in (5, 7)) for t in range(15)]
    summary = epoch(analyze(rows, profile(pre_margin_ns=1, recovery_ns=2)))
    self.assertEqual(summary["eligible_count"], 6)  # 2,3,10,11,12,13
    self.assertEqual(summary["eligible_metrics"]["bias"], 8.5)
    self.assertEqual(reason_count(summary, "driver_intervention"), 2)

  def test_unknown_override_and_gap_require_new_stability(self):
    times = [0, 1, 2, 3, 4, 5, 6, 11, 12, 13, 14, 15]
    rows = [observation(t, driver_override=None if t == 4 else False) for t in times]
    summary = epoch(analyze(rows, profile(recovery_ns=2, max_gap_ns=3)))
    self.assertEqual(summary["eligible_count"], 4)  # 2,3,14,15
    self.assertEqual(summary["eligible_metrics"]["bias"], 8.5)
    self.assertEqual(reason_count(summary, "intervention_unknown"), 1)
    self.assertEqual(reason_count(summary, "signal_gap"), 1)

  def test_control_validity_and_recovery_readiness_are_independent(self):
    rows = [observation(t, valid=t != 2, active=t != 4, recovery_ready=t != 6) for t in range(9)]
    summary = epoch(analyze(rows, profile(recovery_ns=1)))
    self.assertEqual(summary["eligible_count"], 2)  # 1 and 8
    self.assertEqual(summary["eligible_metrics"]["bias"], 4.5)
    for reason in ("invalid_signal", "control_inactive", "recovery_not_ready"):
      self.assertEqual(reason_count(summary, reason), 1)

  def test_dependency_window_excludes_bad_state_without_recursive_poisoning(self):
    rows = [observation(t, valid=t != 4) for t in range(11)]
    summary = epoch(analyze(rows, profile(dependency_ns=2)))
    self.assertEqual(summary["eligible_count"], 4)  # 2,3,7,8
    self.assertEqual(summary["eligible_metrics"]["bias"], 5.0)
    self.assertEqual(reason_count(summary, "dependency_unobserved"), 2)
    self.assertEqual(reason_count(summary, "dependency_window"), 2)
    self.assertEqual(summary["provisional_count"], 2)

  def test_dependency_window_cannot_cross_epoch_start(self):
    rows = [observation(t, epoch="a" if t < 5 else "b") for t in range(13)]
    result = analyze(rows, profile(dependency_ns=2))
    self.assertEqual(epoch(result)["eligible_count"], 3)
    self.assertEqual(epoch(result, name="b")["eligible_count"], 4)
    self.assertEqual(epoch(result, name="b")["eligible_metrics"]["bias"], 8.5)
    self.assertEqual(reason_count(epoch(result, name="b"), "dependency_unobserved"), 2)

  def test_future_pre_mask_invalidates_dependent_earlier_samples(self):
    rows = [observation(t, driver_override=t == 7) for t in range(16)]
    summary = epoch(analyze(rows, profile(pre_margin_ns=2, dependency_ns=2)))
    self.assertEqual(summary["eligible_count"], 5)  # 2,3,4,10,11
    self.assertEqual(summary["eligible_metrics"]["bias"], 6.0)
    self.assertEqual(reason_count(summary, "dependency_window"), 2)

  def test_axes_have_independent_masks_and_conservative_shared_watermark(self):
    rows = [observation(t, axis=axis, driver_override=axis == "lateral" and t == 3)
            for t in range(7) for axis in ("lateral", "longitudinal")]
    rows.extend(observation(t) for t in range(7, 11))
    result = analyze(rows, profile(pre_margin_ns=1))
    self.assertEqual(result["finalized_through_ns"], 5)
    self.assertEqual(epoch(result, axis="longitudinal")["eligible_count"], 6)
    self.assertEqual(epoch(result, axis="longitudinal")["excluded_count"], 0)
    self.assertEqual(epoch(result)["eligible_count"], 4)
    self.assertEqual(epoch(result)["provisional_count"], 5)

  def test_dynamic_context_does_not_create_epoch_or_reset_recovery(self):
    rows = [observation(t, context={"liveDelay": 0.1 + t / 100, "gap": t % 4}) for t in range(6)]
    original_context = dict(rows[2].context)
    result = analyze(rows, profile(recovery_ns=2))
    self.assertEqual(list(result["axes"]["lateral"]["epochs"]), ["a"])
    self.assertEqual(epoch(result)["eligible_count"], 4)
    self.assertEqual(rows[2].context, original_context)

  def test_nonfinite_or_missing_measurement_is_not_zero_filled(self):
    rows = [observation(0, target=None), observation(1, actual=float("nan")),
            observation(2, target=float("inf")), observation(3, actual=3.0)]
    result = analyze(rows, profile())
    summary = epoch(result)
    self.assertEqual(summary["raw_metrics"]["count"], 1)
    self.assertEqual(summary["eligible_count"], 1)
    self.assertEqual(summary["eligible_metrics"]["bias"], 3.0)
    self.assertEqual(reason_count(summary, "measurement_missing"), 1)
    self.assertEqual(reason_count(summary, "measurement_nonfinite"), 2)
    json.dumps(result, allow_nan=False)

  def test_empty_and_short_inputs_have_no_fabricated_finalization(self):
    empty = analyze([], profile())
    self.assertEqual(empty["axes"], {})
    self.assertIsNone(empty["finalized_through_ns"])
    result = analyze([observation(0)], profile(pre_margin_ns=2))
    self.assertEqual(result["finalized_through_ns"], -1)
    self.assertEqual(epoch(result)["provisional_count"], 1)
    self.assertIsNone(epoch(result)["eligible_metrics"]["rmse"])

  def test_explicit_finalization_can_shrink_but_not_extend_safe_boundary(self):
    rows = [observation(t) for t in range(8)]
    summary = epoch(analyze(rows, profile(pre_margin_ns=1, dependency_ns=1), finalized_through_ns=3))
    self.assertEqual(summary["provisional_count"], 4)
    for boundary in (6, -1, True, 3.0):
      with self.subTest(boundary=boundary), self.assertRaises(ValueError):
        analyze(rows, profile(pre_margin_ns=1, dependency_ns=1), boundary)
    with self.assertRaises(ValueError):
      analyze([], profile(), 0)

  def test_per_axis_time_must_increase_without_silent_sorting(self):
    for rows in ([observation(2), observation(1)], [observation(1), observation(1)]):
      with self.assertRaisesRegex(ValueError, "strictly increase"):
        analyze(rows, profile())
    first = [observation(0), observation(0, axis="longitudinal"),
             observation(1, axis="longitudinal"), observation(1)]
    second = [first[1], first[0], first[3], first[2]]
    self.assertEqual(analyze(first, profile()), analyze(second, profile()))

  def test_closed_epoch_cannot_be_reused(self):
    with self.assertRaisesRegex(ValueError, "closed epoch"):
      analyze([observation(0), observation(1, epoch="b"), observation(2)], profile())

  def test_input_limit_stops_a_generator_instead_of_truncating(self):
    consumed = []
    def records():
      for t in range(1000):
        consumed.append(t)
        yield observation(t)
    with self.assertRaisesRegex(ValueError, "max_samples"):
      analyze(records(), profile(max_samples=3))
    self.assertEqual(consumed, [0, 1, 2, 3])

  def test_profile_requires_explicit_valid_integer_thresholds(self):
    for changes in ({"profile_id": " "}, {"pre_margin_ns": -1}, {"recovery_ns": True},
                    {"dependency_ns": 1.5}, {"max_gap_ns": 0}, {"max_samples": 0}):
      with self.subTest(changes=changes), self.assertRaises(ValueError):
        profile(**changes)
    with self.assertRaises(ValueError):
      analyze([], {})

  def test_malformed_observation_fields_are_rejected(self):
    changes = ({"mono_ns": -1}, {"mono_ns": True}, {"axis": "other"}, {"epoch": ""},
               {"active": 1}, {"valid": 0}, {"recovery_ready": "yes"}, {"driver_override": 1},
               {"actual": "1"}, {"target": True}, {"context": []})
    for change in changes:
      with self.subTest(change=change), self.assertRaises(ValueError):
        analyze([replace(observation(0), **change)], profile())
    with self.assertRaises(ValueError):
      analyze([{}], profile())

  def test_large_finite_metrics_remain_json_finite(self):
    result = analyze([observation(0, actual=1e308), observation(1, actual=-1e308)], profile())
    self.assertEqual(epoch(result)["eligible_metrics"]["bias"], 0.0)
    self.assertEqual(epoch(result)["eligible_metrics"]["rmse"], 1e308)
    json.dumps(result, allow_nan=False)
    with self.assertRaisesRegex(ValueError, "overflowed"):
      analyze([observation(0, target=-1e308, actual=1e308)], profile())

  def test_output_is_reproducible_and_accounts_for_every_sample(self):
    rows = [observation(t, driver_override=t == 6) for t in range(15)]
    selected = profile(pre_margin_ns=2, recovery_ns=2, dependency_ns=1)
    result = analyze(iter(rows), selected)
    self.assertEqual(json.dumps(result, sort_keys=True, allow_nan=False),
                     json.dumps(analyze(iter(rows), selected), sort_keys=True, allow_nan=False))
    summary = epoch(result)
    self.assertEqual(summary["sample_count"], summary["eligible_count"] + summary["excluded_count"] + summary["provisional_count"])
    self.assertIn("including_excluded_and_provisional", result["raw_metrics_scope"])

  def test_output_limits_are_excluded_on_both_axes_with_raw_metrics_preserved(self):
    rows = [observation(t, axis=axis, context={"saturated": t == 2, "output_limited": t == 4})
            for t in range(7) for axis in ("lateral", "longitudinal")]
    result = analyze(rows, profile())
    for axis in ("lateral", "longitudinal"):
      summary = epoch(result, axis=axis)
      self.assertEqual(summary["raw_metrics"]["count"], 7)
      self.assertEqual(summary["eligible_count"], 5)
      self.assertEqual(reason_count(summary, "output_limited"), 2)
      self.assertEqual(summary["observed_duration_ns"], 6)
      self.assertEqual(summary["eligible_duration_ns"], 2)  # pairs 0..1 and 5..6
      self.assertEqual(summary["output_limit_observation"]["limited_count"], 2)
      self.assertEqual(summary["scope_limitations"], [])

  def test_output_limit_unknowns_are_visible_and_non_boolean_flags_are_rejected(self):
    contexts = [{}, {"saturated": False}, {"saturated": None}, {"output_limited": False},
                {"saturated": False, "output_limited": False}]
    summary = epoch(analyze([observation(t, context=context) for t, context in enumerate(contexts)], profile()))
    self.assertEqual(summary["output_limit_observation"], {"saturated_known_count": 2,
                     "output_limited_known_count": 2, "fully_known_count": 1, "unknown_count": 4, "limited_count": 0})
    self.assertEqual(summary["scope_limitations"], ["output_limit_state_unknown"])
    for context in ({"saturated": "false"}, {"output_limited": 0}):
      with self.subTest(context=context), self.assertRaisesRegex(ValueError, "context"):
        analyze([observation(0, context=context)], profile())

  def test_duration_does_not_bridge_gaps_epochs_or_provisional_samples(self):
    rows = [observation(t, epoch="a" if t < 3 else "b") for t in (0, 1, 2, 3, 4, 10, 11, 12)]
    result = analyze(rows, profile(max_gap_ns=2, pre_margin_ns=1))
    before, after = epoch(result), epoch(result, name="b")
    self.assertEqual((before["observed_duration_ns"], before["eligible_duration_ns"]), (2, 2))
    self.assertEqual((after["observed_duration_ns"], after["eligible_duration_ns"]), (3, 1))
    self.assertEqual(after["provisional_count"], 1)


if __name__ == "__main__":
  unittest.main()
