"""Fixed policy targets never compensate a different speed node by overshoot."""
import unittest

from openpilot.selfdrive.carrot.control_advisor import candidates
from openpilot.selfdrive.carrot.control_advisor.tests.test_candidates import candidate_fixture


class CandidateGoalTargetsTests(unittest.TestCase):
  def fixture(self):
    evidence, profile = candidate_fixture()
    key = "CruiseMaxVals0"
    evidence["epoch"]["settings_raw"] = {f"CruiseMaxVals{i}": 100 for i in range(7)}
    evidence["objectives"] = {key: {"id": "fixed-policy-node", "kind": "policy_reference", "source": "user_specified",
      "unit": "m/s^2", "reference_signal": "accel_reference", "target_raw": 160}}
    for block in evidence["blocks"]:
      for row in block["samples"]:
        stamp = row["mono_ns"]
        row["signals"] = {name: {"value": value, "unit": unit, "source": source, "source_mono_ns": stamp}
          for name, value, unit, source in (("v_ego", 5 / 3.6, "m/s", "independent_measurement"),
            ("accel_mode_factor", 1., "1", "derived_input"), ("accel_reference", 1.8, "m/s^2", "user_specified"))}
    profile.update(change_limit_policy="manual_experiment", per_key_change_limits=None,
                   guard_tolerances={key: {"mode": "baseline_bounded"}})
    return key, evidence, profile

  def test_neighbor_error_does_not_push_candidate_beyond_selected_target(self):
    key, evidence, profile = self.fixture()
    row = next(row for row in candidates.evaluate_candidates(evidence, profile)["items"] if row["key"] == key)
    self.assertGreater(row["raw_estimate"], 160)
    self.assertEqual(("search_candidate", 160), (row["status"], row["candidate_raw"]))
    self.assertTrue(row["validation"]["holdout_objective_pass"])

  def test_explicit_change_limit_still_wins_and_same_target_keeps_current(self):
    key, evidence, profile = self.fixture()
    profile["per_key_change_limits"] = {key: 10}
    row = next(row for row in candidates.evaluate_candidates(evidence, profile)["items"] if row["key"] == key)
    self.assertEqual(110, row["candidate_raw"])
    evidence["objectives"][key]["target_raw"] = 100
    row = next(row for row in candidates.evaluate_candidates(evidence, profile)["items"] if row["key"] == key)
    self.assertEqual("keep_current", row["status"])
    self.assertIsNone(row["candidate_raw"])

  def test_malformed_target_is_rejected(self):
    key, evidence, profile = self.fixture()
    for value in (True, 1.2, -1, 1_000_001):
      with self.subTest(value=value):
        evidence["objectives"][key]["target_raw"] = value
        with self.assertRaises(ValueError):
          candidates.evaluate_candidates(evidence, profile)


if __name__ == "__main__":
  unittest.main()
