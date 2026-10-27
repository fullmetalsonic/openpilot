"""Completed manoeuvres, not resampled timestamps, determine observed effects."""
import copy
import math
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.comparison import evaluate_comparison, validate_public_comparison, _assessment, _profile
from openpilot.selfdrive.carrot.control_advisor.analysis import Observation
from openpilot.selfdrive.carrot.control_advisor.comparison_stream import InputWriter
from .test_comparison import comparison_fixture


NS = 1_000_000_000


def event_fixture(*, before_error=.3, after_error=.1, events=8, duration=900, output_scale=1.,
                  recovery_before=None, recovery_after=None):
  application, cohort, profile = comparison_fixture(lateral=True, duration_seconds=duration)
  for side_index, side in enumerate((cohort["before"], cohort["after"])):
    origin = side["observations"][0]["mono_ns"]
    for row in side["observations"]:
      elapsed = row["mono_ns"] - origin
      event_number = elapsed // (100 * NS)
      phase = elapsed % (100 * NS)
      direction = 1 if event_number % 2 == 0 else -1
      moving = event_number < events and 20 * NS <= phase < 24 * NS
      target = direction * 1. if moving else 0.
      error = before_error if side_index == 0 else after_error
      error = error[event_number % len(error)] if isinstance(error, list) else error
      actual = target + direction * error if moving else 0.
      delay = recovery_before if side_index == 0 else recovery_after
      if delay is not None:
        actual = target
        if event_number < events and 24 * NS <= phase < 24 * NS + int(round(delay * NS)):
          actual = direction * 1.
      row.update(target=target, actual=actual)
      row["context"].update(sent_request=target * .5 * (output_scale if side_index else 1.), sent_mono_ns=row["mono_ns"],
        independent_actual=actual, independent_mono_ns=row["mono_ns"], independent_response_kind="yaw_speed_kinematic")
  return application, cohort, profile


class AssessmentTests(unittest.TestCase):
  def assess(self, **options):
    application, cohort, profile = event_fixture(**options)
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("comparison_ready", result["state"], result)
    public = validate_public_comparison(result["comparison"])
    self.assertFalse(public["causal_improvement_proven"])
    self.assertFalse(public["apply_enabled"])
    return public["assessment"], (application, cohort, profile)

  def test_positive_negative_and_uncertain_effects_end_after_eight_events(self):
    for error, expected in ((.1, "improved"), (.7, "worsened"), (.3, "inconclusive")):
      with self.subTest(after_error=error):
        result, inputs = self.assess(after_error=error)
        self.assertEqual(expected, result["state"], result)
        self.assertTrue(result["ended"])
        self.assertEqual({"before_events": 8, "after_events": 8, "matched_pairs": 8}, result["counts"])
        self.assertEqual(8, len(result["event_pairs"]))
        self.assertEqual(16, len({pair[name] for pair in result["event_pairs"] for name in ("before_id", "after_id")}))
        self.assertEqual(result, evaluate_comparison(*inputs)["comparison"]["assessment"])
        self.assertFalse(result["causal_improvement_proven"])

  def test_guard_worsening_cannot_be_hidden_by_better_tracking(self):
    result, _ = self.assess(output_scale=2.)
    self.assertEqual("worsened", result["state"])
    self.assertGreater(result["effect"]["lower"], result["minimum_effect"])
    self.assertEqual("worsened", result["guards"]["output_rate"]["state"])

  def test_timing_trial_uses_target_tracking_error_not_physical_return_seconds(self):
    application, cohort, profile = event_fixture(recovery_before=1., recovery_after=.4)
    application["key"] = "SteerActuatorDelay"
    rows = [[Observation(**row) for row in side["observations"]] for side in (cohort["before"], cohort["after"])]
    matched = {"bins": [{"speed_bin": 1, "target_bin": value} for value in (0, 1)]}
    result = _assessment(application, cohort, profile, _profile(profile), rows[0], rows[1],
      ([(row, set()) for row in rows[0]], [(row, set()) for row in rows[1]]), matched)
    self.assertEqual("target_tracking_rmse", result["primary_metric"])
    self.assertEqual("m/s^2", result["unit"])
    self.assertEqual("improved", result["state"], result)
    self.assertGreater(result["effect"]["estimate"], 0)
    for pair in result["event_pairs"]:
      self.assertAlmostEqual(math.sqrt(.4), pair["after"] / pair["before"])

  def test_intervention_frequency_stays_a_guard_while_its_rows_are_masked(self):
    application, cohort, profile = event_fixture()
    origin = cohort["after"]["observations"][0]["mono_ns"]
    for row in cohort["after"]["observations"]:
      phase = ((row["mono_ns"] - origin) / NS) % 100
      if 21 <= phase <= 21.1:
        row["driver_override"] = True
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("comparison_ready", result["state"], result)
    assessment = result["comparison"]["assessment"]
    self.assertEqual("worsened", assessment["state"], assessment)
    self.assertEqual("worsened", assessment["guards"]["intervention_fraction"]["state"])
    self.assertGreater(result["comparison"]["after"]["exclusion_counts"]["driver_intervention"], 0)

  def test_eighth_event_stops_selection_and_later_results_cannot_extend_the_trial(self):
    result, (application, cohort, profile) = self.assess()
    self.assertEqual(732., result["observed_after_s"])
    origin = cohort["after"]["observations"][0]["mono_ns"]
    for row in cohort["after"]["observations"]:
      if row["mono_ns"] - origin > 850 * NS:
        row["actual"] += 10.
        row["context"]["independent_actual"] += 10.
        row["context"]["sent_request"] += 10.
    cohort["after"]["window"].update(end_ns=1700 * NS, finalized_through_ns=1700 * NS)
    later = evaluate_comparison(application, cohort, profile)["comparison"]["assessment"]
    self.assertEqual(result, later)

  def test_unmatched_event_keeps_its_first_eight_slot_when_later_matching_event_arrives(self):
    assessments = []
    for count in (8, 9):
      application, cohort, profile = event_fixture(events=count)
      origin = cohort["after"]["observations"][0]["mono_ns"]
      for row in cohort["after"]["observations"]:
        if 16 * NS <= row["mono_ns"] - origin <= 28 * NS:
          # A valid profile speed bin, absent from all baseline observations.
          row["context"]["v_ego"] = 30.
      result = evaluate_comparison(application, cohort, profile)
      self.assertEqual("comparison_ready", result["state"], result)
      assessment = result["comparison"]["assessment"]
      self.assertEqual("inconclusive", assessment["state"], assessment)
      self.assertTrue(assessment["ended"])
      self.assertEqual({"before_events": 8, "after_events": 8, "matched_pairs": 7}, assessment["counts"])
      self.assertEqual(732., assessment["observed_after_s"])
      self.assertIsNone(assessment["effect"])
      assessments.append(assessment)
    self.assertEqual(assessments[0], assessments[1])

  def test_baseline_repeated_event_variation_sets_effect_threshold(self):
    result, _ = self.assess(before_error=[.1, .1, .4, .4, .2, .2, .3, .3], after_error=.24)
    self.assertGreater(result["baseline_repeat_variation"], .1)
    self.assertEqual(result["baseline_repeat_variation"], result["minimum_effect"])
    self.assertEqual("inconclusive", result["state"])

  def test_only_independent_complete_events_count_and_deadline_ends_observation(self):
    result, _ = self.assess(events=1)
    self.assertEqual("inconclusive", result["state"])
    self.assertEqual(900., result["observed_after_s"])
    self.assertEqual(1, result["counts"]["matched_pairs"])
    self.assertIsNone(result["effect"])
    application, cohort, profile = comparison_fixture(lateral=True)
    for side in (cohort["before"], cohort["after"]):
      for row in side["observations"]:
        row["context"].update(sent_request=.2, sent_mono_ns=row["mono_ns"], independent_actual=row["actual"],
          independent_mono_ns=row["mono_ns"], independent_response_kind="yaw_speed_kinematic")
    pending = evaluate_comparison(application, cohort, profile)["comparison"]["assessment"]
    self.assertEqual("observing", pending["state"])
    self.assertFalse(pending["ended"])
    self.assertEqual(0, pending["counts"]["matched_pairs"])

  def test_missing_original_output_and_masked_partial_events_are_inconclusive(self):
    application, cohort, profile = event_fixture()
    for row in cohort["after"]["observations"]:
      row["context"].pop("sent_request")
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual("comparison_ready", result["state"])
    self.assertEqual("inconclusive", result["comparison"]["assessment"]["state"])
    self.assertIn("assessment_output_source_unavailable", result["comparison"]["assessment"]["reason_codes"])
    application, cohort, profile = event_fixture()
    origin = cohort["after"]["observations"][0]["mono_ns"]
    for row in cohort["after"]["observations"]:
      if 21 <= (row["mono_ns"] - origin) / NS < 22:
        row["valid"] = False
    assessed = evaluate_comparison(application, cohort, profile)["comparison"]["assessment"]
    self.assertEqual("inconclusive", assessed["state"])
    self.assertLess(assessed["counts"]["matched_pairs"], 8)

  def test_direction_reversal_and_event_condition_mismatch_do_not_claim_improvement(self):
    result, _ = self.assess(after_error=[.05, .6])
    self.assertEqual("inconclusive", result["state"])
    self.assertIn("assessment_condition_effect_reversal", result["reason_codes"])
    application, cohort, profile = event_fixture()
    for row in cohort["after"]["observations"]:
      row["target"] = abs(row["target"])
      row["actual"] = abs(row["actual"])
    result = evaluate_comparison(application, cohort, profile)["comparison"]["assessment"]
    self.assertEqual("inconclusive", result["state"])
    self.assertEqual(4, result["counts"]["matched_pairs"])
    application, cohort, profile = event_fixture()
    for row in cohort["after"]["observations"]:
      row["target"] *= .5
      row["actual"] *= .5
    result = evaluate_comparison(application, cohort, profile)["comparison"]["assessment"]
    self.assertEqual("inconclusive", result["state"])
    self.assertEqual(0, result["counts"]["matched_pairs"])

  def test_steering_model_response_cannot_validate_a_change_to_its_own_ratio(self):
    application, cohort, profile = event_fixture()
    application["key"] = "CustomSR"
    rows = []
    for side in (cohort["before"], cohort["after"]):
      row = copy.deepcopy(side["observations"][0])
      row["context"].pop("independent_actual")
      rows.append([Observation(**row)])
    result = _assessment(application, cohort, profile, _profile(profile), rows[0], rows[1], ([], []), {"bins": []})
    self.assertEqual("inconclusive", result["state"])
    self.assertIn("assessment_independent_response_required", result["reason_codes"])
    self.assertIsNone(result["effect"])

  def test_disk_stream_and_original_rows_produce_identical_event_assessment(self):
    application, cohort, profile = event_fixture()
    expected = evaluate_comparison(application, cohort, profile)["comparison"]["assessment"]
    original = copy.deepcopy(cohort)
    with tempfile.TemporaryDirectory() as directory:
      for side in (cohort["before"], cohort["after"]):
        writer = InputWriter(Path(directory) / "cache")
        try:
          for row in side["observations"]:
            writer.append(row)
          side["observations"] = writer.finish()
        finally:
          writer.close()
        side["comparison_output_root"] = directory
      result = evaluate_comparison(application, cohort, profile)
      self.assertEqual("comparison_ready", result["state"], result)
      self.assertEqual(3, result["comparison"]["schema_version"])
      self.assertEqual(expected, result["comparison"]["assessment"])
      validate_public_comparison(result["comparison"])
    self.assertNotEqual(original, cohort)

  def test_public_validator_rejects_forged_conclusion_duplicate_events_and_nonfinite_interval(self):
    _, inputs = self.assess()
    original = evaluate_comparison(*inputs)["comparison"]
    for mutation in ("guard", "duplicate", "nonfinite", "count"):
      public = copy.deepcopy(original)
      item = public["assessment"]
      if mutation == "guard":
        item["guards"]["output_rate"]["state"] = "uncertain"
      elif mutation == "duplicate":
        item["event_pairs"][1]["before_id"] = item["event_pairs"][0]["before_id"]
      elif mutation == "nonfinite":
        item["effect"]["lower"] = float("nan")
      else:
        item["counts"]["matched_pairs"] = 7
      with self.subTest(mutation=mutation), self.assertRaises(ValueError):
        validate_public_comparison(public)


if __name__ == "__main__":
  unittest.main()
