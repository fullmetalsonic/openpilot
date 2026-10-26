"""Tests for lossless comparison exclusion interval codecs."""
import copy
import json
import unittest

from openpilot.selfdrive.carrot.control_advisor.comparison_intervals import (
  ENCODING,
  decode_exclusions,
  encode_exclusions,
)


class ComparisonIntervalsTests(unittest.TestCase):
  def test_empty_intervals_roundtrip(self):
    encoded = encode_exclusions([])
    self.assertEqual({"encoding": ENCODING, "reasons": [], "rows": []}, encoded)
    self.assertEqual([], decode_exclusions([], schema_version=1))
    self.assertEqual([], decode_exclusions(encoded, schema_version=2))

  def test_schema1_exact_handling_and_deep_copy_isolation(self):
    legacy = [
      {"reason": "driver_intervention", "start_ns": 10, "end_ns": 20, "sample_count": 5},
      {"reason": "recovery", "start_ns": 25, "end_ns": 30, "sample_count": 2},
    ]
    decoded = decode_exclusions(legacy, schema_version=1)
    self.assertEqual(legacy, decoded)
    self.assertIsNot(legacy, decoded)
    decoded[0]["start_ns"] = 999
    self.assertEqual(10, legacy[0]["start_ns"])

  def test_schema2_exact_roundtrip_order_gaps_overlap_zeroduration(self):
    intervals = [
      {"reason": "settings_hole", "start_ns": 1000, "end_ns": 2000, "sample_count": 10},
      {"reason": "driver_mask", "start_ns": 1500, "end_ns": 2500, "sample_count": 8},
      {"reason": "point_event", "start_ns": 3000, "end_ns": 3000, "sample_count": 1},
      {"reason": "settings_hole", "start_ns": 5000, "end_ns": 6000, "sample_count": 12},
      {"reason": "driver_mask", "start_ns": 7000, "end_ns": 7500, "sample_count": 3},
    ]
    encoded = encode_exclusions(intervals)
    self.assertEqual(ENCODING, encoded["encoding"])
    self.assertEqual(["settings_hole", "driver_mask", "point_event"], encoded["reasons"])
    self.assertEqual([
      [0, "1000", "2000", 10],
      [1, "1500", "2500", 8],
      [2, "3000", "3000", 1],
      [0, "5000", "6000", 12],
      [1, "7000", "7500", 3],
    ], encoded["rows"])

    decoded = decode_exclusions(encoded, schema_version=2)
    self.assertEqual(intervals, decoded)
    self.assertIsNot(intervals, decoded)
    for a, b in zip(intervals, decoded):
      self.assertIsNot(a, b)

    reencoded = encode_exclusions(decoded)
    self.assertEqual(encoded, reencoded)

  def test_deep_copy_isolation_on_encode(self):
    original = [{"reason": "recovery", "start_ns": 100, "end_ns": 200, "sample_count": 1}]
    clone = copy.deepcopy(original)
    encoded = encode_exclusions(original)
    original[0]["start_ns"] = 999
    self.assertEqual("100", encoded["rows"][0][1])
    decoded = decode_exclusions(encoded, schema_version=2)
    self.assertEqual(clone, decoded)

  def test_int64_edges_and_safe_integer_counts(self):
    max_int64 = (1 << 63) - 1
    max_safe_int = (1 << 53) - 1
    intervals = [
      {"reason": "zero_point", "start_ns": 0, "end_ns": 0, "sample_count": 0},
      {"reason": "max_boundary", "start_ns": max_int64, "end_ns": max_int64, "sample_count": max_safe_int},
    ]
    encoded = encode_exclusions(intervals)
    self.assertEqual("0", encoded["rows"][0][1])
    self.assertEqual("0", encoded["rows"][0][2])
    self.assertEqual(0, encoded["rows"][0][3])
    self.assertEqual(str(max_int64), encoded["rows"][1][1])
    self.assertEqual(str(max_int64), encoded["rows"][1][2])
    self.assertEqual(max_safe_int, encoded["rows"][1][3])

    decoded = decode_exclusions(encoded, schema_version=2)
    self.assertEqual(intervals, decoded)
    self.assertIsInstance(decoded[1]["start_ns"], int)
    self.assertNotIsInstance(decoded[1]["start_ns"], bool)

  def test_dictionary_canonicality_rules(self):
    unreferenced = {
      "encoding": ENCODING,
      "reasons": ["used_reason", "unreferenced_reason"],
      "rows": [[0, "10", "20", 1]],
    }
    with self.assertRaises(ValueError) as ctx:
      decode_exclusions(unreferenced, schema_version=2)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))

    duplicate_reasons = {
      "encoding": ENCODING,
      "reasons": ["reason_a", "reason_a"],
      "rows": [[0, "10", "20", 1]],
    }
    with self.assertRaises(ValueError) as ctx:
      decode_exclusions(duplicate_reasons, schema_version=2)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))

    non_first_use = {
      "encoding": ENCODING,
      "reasons": ["reason_zero", "reason_one"],
      "rows": [[1, "10", "20", 1], [0, "30", "40", 1]],
    }
    with self.assertRaises(ValueError) as ctx:
      decode_exclusions(non_first_use, schema_version=2)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))

    bad_index = {
      "encoding": ENCODING,
      "reasons": ["reason_zero"],
      "rows": [[2, "10", "20", 1]],
    }
    with self.assertRaises(ValueError) as ctx:
      decode_exclusions(bad_index, schema_version=2)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))

  def test_strict_validation_rejections(self):
    cases = [
      ({"reason": "r", "start_ns": 0, "end_ns": 10, "sample_count": 1, "extra": True}, "legacy extra key"),
      ({"reason": "r", "start_ns": 0, "end_ns": 10}, "legacy missing key"),
      ({"reason": "r", "start_ns": True, "end_ns": 10, "sample_count": 1}, "bool start_ns"),
      ({"reason": "r", "start_ns": 0, "end_ns": False, "sample_count": 1}, "bool end_ns"),
      ({"reason": "r", "start_ns": 0, "end_ns": 10, "sample_count": True}, "bool count"),
      ({"reason": "r", "start_ns": 1.5, "end_ns": 10, "sample_count": 1}, "float start_ns"),
      ({"reason": "r", "start_ns": 0, "end_ns": 10.5, "sample_count": 1}, "float end_ns"),
      ({"reason": "r", "start_ns": 0, "end_ns": 10, "sample_count": 1.5}, "float count"),
      ({"reason": "r", "start_ns": -1, "end_ns": 10, "sample_count": 1}, "negative start_ns"),
      ({"reason": "r", "start_ns": 10, "end_ns": 5, "sample_count": 1}, "reversed start/end"),
      ({"reason": "r", "start_ns": 0, "end_ns": (1 << 63), "sample_count": 1}, "overflow int64"),
      ({"reason": "r", "start_ns": 0, "end_ns": 10, "sample_count": (1 << 53)}, "overflow safe count"),
      ({"reason": "Invalid_Reason", "start_ns": 0, "end_ns": 10, "sample_count": 1}, "uppercase reason"),
      ({"reason": "reason-1", "start_ns": 0, "end_ns": 10, "sample_count": 1}, "hyphen reason"),
      ({"reason": "", "start_ns": 0, "end_ns": 10, "sample_count": 1}, "empty reason"),
      ({"reason": "a" * 129, "start_ns": 0, "end_ns": 10, "sample_count": 1}, "reason too long"),
    ]
    for bad_item, desc in cases:
      with self.subTest(desc=desc):
        with self.assertRaises(ValueError) as ctx:
          encode_exclusions([bad_item])
        self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
          decode_exclusions([bad_item], schema_version=1)
        self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))

    v2_cases = [
      ({"encoding": "reason-indexed-v2", "reasons": [], "rows": []}, "bad encoding"),
      ({"encoding": ENCODING, "reasons": [], "rows": [], "extra": 1}, "v2 extra key"),
      ({"encoding": ENCODING, "reasons": []}, "v2 missing rows"),
      ({"encoding": ENCODING, "reasons": ["ok"], "rows": [[0, "01", "10", 1]]}, "leading zero decimal"),
      ({"encoding": ENCODING, "reasons": ["ok"], "rows": [[0, "+5", "10", 1]]}, "plus sign decimal"),
      ({"encoding": ENCODING, "reasons": ["ok"], "rows": [[0, "-5", "10", 1]]}, "minus sign decimal"),
      ({"encoding": ENCODING, "reasons": ["ok"], "rows": [[0, "5", "10.0", 1]]}, "float decimal string"),
      ({"encoding": ENCODING, "reasons": ["ok"], "rows": [[0, "5", "10", 1, "extra"]]}, "row length > 4"),
      ({"encoding": ENCODING, "reasons": ["ok"], "rows": [[0, "5", "10"]]}, "row length < 4"),
      ({"encoding": ENCODING, "reasons": ["ok"], "rows": "not_a_list"}, "rows not list"),
      ({"encoding": ENCODING, "reasons": "not_a_list", "rows": []}, "reasons not list"),
    ]
    for bad_v2, desc in v2_cases:
      with self.subTest(v2_desc=desc):
        with self.assertRaises(ValueError) as ctx:
          decode_exclusions(bad_v2, schema_version=2)
        self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))

    with self.assertRaises(ValueError) as ctx:
      decode_exclusions([], schema_version=0)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))
    with self.assertRaises(ValueError) as ctx:
      decode_exclusions([], schema_version=3)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))
    with self.assertRaises(ValueError) as ctx:
      decode_exclusions([], schema_version=2)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))
    with self.assertRaises(ValueError) as ctx:
      decode_exclusions({"encoding": ENCODING, "reasons": [], "rows": []}, schema_version=1)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))

  def test_bound_limit_100k(self):
    item = {"reason": "gap", "start_ns": 0, "end_ns": 1, "sample_count": 1}
    with self.assertRaises(ValueError) as ctx:
      encode_exclusions([item] * 100_001)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))
    with self.assertRaises(ValueError) as ctx:
      decode_exclusions([item] * 100_001, schema_version=1)
    self.assertEqual("invalid_public_comparison_exclusions", str(ctx.exception))

  def test_realistic_180s_and_300s_payload_budget(self):
    overhead = {
      "schema_version": 1,
      "state": "comparison_ready",
      "axis": "longitudinal",
      "scope": "observational_vehicle_acceleration",
      "target_unit": "m/s^2",
      "consumer_application_ack": False,
      "causal_improvement_proven": False,
      "apply_enabled": False,
      "device_qualified": False,
      "reason_codes": [],
      "boundary": {
        "setting_uncertainty_start_ns": 29_000_000_000,
        "setting_uncertainty_end_ns": 31_000_000_000,
        "consumer_observed_ns": 32_000_000_000,
        "after_eligible_from_ns": 35_000_000_000,
        "exact_consumer_apply_time_known": False,
      },
      "matched": {
        "weighting": "common_stratum_minimum_count",
        "common_bin_count": 1,
        "bins": [{"speed_bin": 0, "target_bin": 0, "before_count": 100, "after_count": 100, "weight": 1.0}],
        "before": {"count": 100, "bias": 0.0, "rmse": 0.1, "p95_absolute_error": 0.15},
        "after": {"count": 100, "bias": 0.0, "rmse": 0.08, "p95_absolute_error": 0.12},
        "before_duration_ns": 10_000_000_000,
        "after_duration_ns": 10_000_000_000,
        "after_minus_before": {"bias": 0.0, "rmse": -0.02, "p95_absolute_error": -0.03},
      },
    }

    def side_summary(count, exclusions):
      return {
        "window_start_ns": 0,
        "window_end_ns": count * 1_000_000_000,
        "raw_metrics": {"count": count * 10, "bias": 0.0, "rmse": 0.2, "p95_absolute_error": 0.3},
        "eligible_metrics": {"count": count * 9, "bias": 0.0, "rmse": 0.1, "p95_absolute_error": 0.15},
        "sample_count": count * 10,
        "eligible_count": count * 9,
        "excluded_count": count,
        "provisional_count": 0,
        "observed_duration_ns": count * 1_000_000_000,
        "eligible_duration_ns": count * 900_000_000,
        "output_limit_observation": {
          "fully_known_count": count * 10,
          "limited_count": 0,
          "output_limited_known_count": 0,
          "saturated_known_count": 0,
          "unknown_count": 0,
        },
        "scope_limitations": [],
        "exclusion_counts": {"comparison_settings_source_uncertain": count},
        "exclusions": exclusions,
      }

    def make_scan_holes(count, base_stamp):
      return [{
        "reason": "comparison_settings_source_uncertain",
        "start_ns": base_stamp + sec * 1_000_000_000,
        "end_ns": base_stamp + sec * 1_000_000_000 + 100_000_000,
        "sample_count": 1,
      } for sec in range(count)]

    for duration in (180, 300):
      with self.subTest(duration=duration):
        v1_before_ex = make_scan_holes(duration, 1_700_000_000_000_000_000)
        v1_after_ex = make_scan_holes(duration, 1_700_000_300_000_000_000)
        v1_comparison = {
          **overhead,
          "schema_version": 1,
          "before": side_summary(duration, v1_before_ex),
          "after": side_summary(duration, v1_after_ex),
        }
        v1_bytes = len(json.dumps(v1_comparison, separators=(",", ":")).encode())
        self.assertGreater(v1_bytes, 32 * 1024)
        if duration == 180:
          self.assertGreaterEqual(v1_bytes, 42_000)
        else:
          self.assertGreaterEqual(v1_bytes, 69_000)

        v2_before_ex = encode_exclusions(v1_before_ex)
        v2_after_ex = encode_exclusions(v1_after_ex)
        self.assertEqual(duration, len(v2_before_ex["rows"]))
        self.assertEqual(duration, len(v2_after_ex["rows"]))

        v2_comparison = {
          **overhead,
          "schema_version": 2,
          "before": side_summary(duration, v2_before_ex),
          "after": side_summary(duration, v2_after_ex),
        }
        v2_bytes = len(json.dumps(v2_comparison, separators=(",", ":")).encode())
        self.assertLessEqual(v2_bytes, 32 * 1024)

        decoded_before = decode_exclusions(v2_before_ex, schema_version=2)
        decoded_after = decode_exclusions(v2_after_ex, schema_version=2)
        self.assertEqual(v1_before_ex, decoded_before)
        self.assertEqual(v1_after_ex, decoded_after)


if __name__ == "__main__":
  unittest.main()
