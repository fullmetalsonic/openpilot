"""Partial recorded support excludes only the named calibration, never errors."""
import copy
import unittest

from openpilot.selfdrive.carrot.control_advisor import candidates
from openpilot.selfdrive.carrot.control_advisor.tests.test_candidates import factor_fixture


class CandidateAvailabilityTests(unittest.TestCase):
  key = "LateralTorqueAccelFactor"

  def row(self, evidence, profile):
    return next(row for row in candidates.evaluate_candidates(evidence, profile)["items"] if row["key"] == self.key)

  def test_explicit_support_gaps_keep_sufficient_independent_samples(self):
    evidence, profile = factor_fixture()
    for block in evidence["blocks"]:
      row = block["samples"][0]
      row["signals"].pop("lateral_accel_offset")
      row["unavailable_for"] = {self.key: ["recorded_dependency_unavailable"]}
    original = copy.deepcopy(evidence)
    result = self.row(evidence, profile)
    self.assertEqual(("search_candidate", 3000), (result["status"], result["candidate_raw"]))
    self.assertEqual(3, result["provenance"]["excluded_rows"])
    self.assertEqual(7, result["metrics"]["holdout"]["rows"])
    self.assertEqual(original, evidence)

  def test_missing_unmarked_or_other_parameter_support_still_blocks(self):
    for marked in (False, True):
      with self.subTest(marked=marked):
        evidence, profile = factor_fixture()
        row = evidence["blocks"][0]["samples"][0]
        row["signals"].pop("lateral_accel_offset")
        if marked:
          row["unavailable_for"] = {"CustomSR": ["recorded_dependency_unavailable"]}
        self.assertIn("missing_stale_or_mismatched_signal_units", self.row(evidence, profile)["reason_codes"])

  def test_support_exclusions_cannot_replace_required_exposure(self):
    evidence, profile = factor_fixture()
    for row in evidence["blocks"][0]["samples"]:
      row["unavailable_for"] = {self.key: ["factor_mapping_unverified"]}
    result = self.row(evidence, profile)
    self.assertIsNone(result["candidate_raw"])
    self.assertIn("insufficient_independent_split_exposure", result["reason_codes"])

  def test_only_known_support_reasons_and_parameter_keys_are_accepted(self):
    for unavailable in ({self.key: ["large_error"]}, {"unknown": ["factor_mapping_unverified"]},
                        {self.key: []}, {self.key: ["factor_mapping_unverified"] * 2}):
      with self.subTest(unavailable=unavailable):
        evidence, profile = factor_fixture()
        evidence["blocks"][0]["samples"][0]["unavailable_for"] = unavailable
        with self.assertRaisesRegex(ValueError, "invalid_key_availability"):
          candidates.evaluate_candidates(evidence, profile)


if __name__ == "__main__":
  unittest.main()
