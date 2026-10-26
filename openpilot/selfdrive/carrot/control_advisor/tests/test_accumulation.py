"""Targeted tests for continuous extraction and native static response sampling."""
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.analysis import AnalysisProfile, Observation, _mask_axis

from openpilot.selfdrive.carrot.control_advisor.log_candidates import _sampling_policy, _sample_response_indices
from openpilot.selfdrive.carrot.control_advisor.periodic import _verify_job
from openpilot.selfdrive.carrot.control_advisor.candidate_report import _link_receipt


class TestSamplingPolicy(unittest.TestCase):
  def test_absent_policy_backward_compatibility(self):
    self.assertIsNone(_sampling_policy({}))
    with self.assertRaises(ValueError):
      _sampling_policy({"sampling": None})
    rows = [{"mono_ns": 100}, {"mono_ns": 200}]
    indices, audit = _sample_response_indices(rows, None)
    self.assertEqual(indices, {0, 1})
    self.assertIsNone(audit)

  def test_valid_sampling_policy(self):
    config = {
      "sampling": {"version": "native_static_response_5hz_v1", "period_ns": 200000000},
      "objectives": {
        "LateralTorqueAccelFactor": {"kind": "tracking", "reference": {"type": "recorded_signal", "signal": "lateral_accel_independent", "unit": "m/s^2"}},
        "SteerRatioRate": {"kind": "geometry_reference", "reference": {"type": "recorded_signal", "signal": "curvature_independent", "unit": "1/m"}},
      }
    }
    policy = _sampling_policy(config)
    self.assertEqual(policy, {"version": "native_static_response_5hz_v1", "period_ns": 200000000})

  def test_bad_policy_and_mixed_mapping_rejects(self):
    base_obj = {"LateralTorqueAccelFactor": {"kind": "tracking", "reference": {"type": "recorded_signal", "signal": "lateral_accel_independent", "unit": "m/s^2"}}}
    # Bad version
    with self.assertRaises(ValueError):
      _sampling_policy({"sampling": {"version": "invalid_version", "period_ns": 200000000}, "objectives": base_obj})
    # Boolean period
    with self.assertRaises(ValueError):
      _sampling_policy({"sampling": {"version": "native_static_response_5hz_v1", "period_ns": True}, "objectives": base_obj})
    # Wrong period
    with self.assertRaises(ValueError):
      _sampling_policy({"sampling": {"version": "native_static_response_5hz_v1", "period_ns": 100000000}, "objectives": base_obj})
    # Mixed policy_curve
    mixed_obj = {
      **base_obj,
      "SteerRatioRate": {"kind": "policy_reference", "reference": {"type": "policy_curve", "signal": "curvature_independent", "unit": "1/m"}}
    }
    with self.assertRaises(ValueError):
      _sampling_policy({"sampling": {"version": "native_static_response_5hz_v1", "period_ns": 200000000}, "objectives": mixed_obj})
    # Non-supported objective key
    unsupported_obj = {"UnknownObjective": {"kind": "tracking", "reference": {"type": "recorded_signal", "signal": "lateral_accel_independent", "unit": "m/s^2"}}}
    with self.assertRaises(ValueError):
      _sampling_policy({"sampling": {"version": "native_static_response_5hz_v1", "period_ns": 200000000}, "objectives": unsupported_obj})

  def test_held_pose_dedup_and_first_invalid_chosen(self):
    policy = {"version": "native_static_response_5hz_v1", "period_ns": 200000000}
    rows = [
      {"lateral_observation": {"provenance": {"epoch_id": "cp-1", "response_mono_ns": 50_000_000}}, "error": True},
      {"lateral_observation": {"provenance": {"epoch_id": "cp-1", "response_mono_ns": 50_000_000}}, "error": False},
      {"lateral_observation": {"provenance": {"epoch_id": "cp-1", "response_mono_ns": 120_000_000}}, "error": False},
      {"lateral_observation": {"provenance": {"epoch_id": "cp-1", "response_mono_ns": 210_000_000}}, "error": False},
    ]
    indices, audit = _sample_response_indices(rows, policy)
    self.assertEqual(indices, {0, 3})
    self.assertEqual(audit["raw_anchor_count"], 4)
    self.assertEqual(audit["unique_response_count"], 3)
    self.assertEqual(audit["selected_count"], 2)
    self.assertTrue(audit["selected_count"] <= audit["unique_response_count"] <= audit["raw_anchor_count"])

  def test_per_cp_epoch_buckets(self):
    policy = {"version": "native_static_response_5hz_v1", "period_ns": 200000000}
    rows = [
      {"lateral_observation": {"provenance": {"epoch_id": "cp-1", "response_mono_ns": 50_000_000}}},
      {"lateral_observation": {"provenance": {"epoch_id": "cp-2", "response_mono_ns": 50_000_000}}},
    ]
    indices, audit = _sample_response_indices(rows, policy)
    self.assertEqual(indices, {0, 1})
    self.assertEqual(audit["selected_count"], 2)

  def test_anchor_fallback_is_not_replaced_when_response_arrives_in_same_bucket(self):
    policy = {"version": "native_static_response_5hz_v1", "period_ns": 200000000}
    rows = [
      {"mono_ns": 20_000_000, "lateral_observation": {"provenance": {"epoch_id": "cp-1", "response_mono_ns": None}}},
      {"mono_ns": 100_000_000, "lateral_observation": {"provenance": {"epoch_id": "cp-1", "response_mono_ns": 100_000_000}}},
      {"mono_ns": 250_000_000, "lateral_observation": {"provenance": {"epoch_id": "cp-1", "response_mono_ns": 250_000_000}}},
    ]
    selected, audit = _sample_response_indices(rows, policy, clock_basis="response_or_anchor")
    self.assertEqual({0, 2}, selected)
    self.assertEqual(1, audit["anchor_fallback_count"])
    self.assertEqual(2, audit["unique_response_count"])




class TestPeriodicJobResult(unittest.TestCase):
  @patch("openpilot.selfdrive.carrot.control_advisor.periodic._read")
  @patch("openpilot.selfdrive.carrot.control_advisor.periodic.load_candidate_bundle")
  def test_aggregate_verify_job_continuous_batch(self, mock_load, mock_read):
    job_id = "a" * 64
    directory = unittest.mock.MagicMock()
    result = {
      "schema_version": 1,
      "job_id": job_id,
      "source_key": "b" * 64,
      "settling_retry_needed": False,
      "state": "ready",
      "source_start_ns": 100,
      "source_end_ns": 500,
      "source_count": 3,
      "windows": [{
        "source_start_ns": 100,
        "source_end_ns": 500,
        "state": "ready",
        "reason": None,
        "directory": "window-0000",
        "range_kind": "verified_car_state",
        "coverage_verified": True,
      }],
      "analysis_mode": "continuous_batch"
    }
    mock_read.return_value = (result, "digest123")
    mock_load.return_value = {"report": {}}
    res, digest, latest = _verify_job(directory, job_id)
    self.assertEqual(res["analysis_mode"], "continuous_batch")
    self.assertEqual(res["source_count"], 3)
    self.assertEqual(len(res["windows"]), 1)

  @patch("openpilot.selfdrive.carrot.control_advisor.periodic._read")
  def test_verify_job_continuous_batch_rejects_multi_window(self, mock_read):
    job_id = "a" * 64
    directory = unittest.mock.MagicMock()
    result = {
      "schema_version": 1,
      "job_id": job_id,
      "source_key": "b" * 64,
      "settling_retry_needed": False,
      "state": "ready",
      "source_start_ns": 100,
      "source_end_ns": 500,
      "source_count": 2,
      "windows": [
        {"source_start_ns": 100, "source_end_ns": 300, "state": "ready", "reason": None, "directory": "window-0000", "range_kind": "verified_car_state", "coverage_verified": True},
        {"source_start_ns": 301, "source_end_ns": 500, "state": "ready", "reason": None, "directory": "window-0001", "range_kind": "verified_car_state", "coverage_verified": True},
      ],
      "analysis_mode": "continuous_batch"
    }
    mock_read.return_value = (result, "digest123")
    with self.assertRaises(ValueError):
      _verify_job(directory, job_id)


class TestReportLinkReceipt(unittest.TestCase):
  def test_link_receipt_sampling_audit_validation(self):
    receipt = {
      "schema_version": 1,
      "epoch_id": "epoch-test",
      "source_bytes_verified_at_generation": True,
      "schema_decoded": True,
      "session_link": "unlinked",
      "semantic_mapping": "partial",
      "originals_preservation": "reference_only",
      "originals_current_availability": "not_checked",
      "sources": [{"id": "log-src1", "sha256": "c" * 64, "bytes": 1024}],
      "source_fields": {"v_ego": "carState.vEgo"},
      "objectives": {
        "sampling": {"version": "native_static_response_5hz_v1", "period_ns": 200000000},
        "objectives": {"LateralTorqueAccelFactor": {"kind": "tracking", "reference": {"type": "recorded_signal", "signal": "lateral_accel_independent", "unit": "m/s^2"}}}
      },
      "objectives_sha256": "d" * 64,
      "mask_profile_sha256": "e" * 64,
      "excluded_counts": {"test": 1},
      "reasons": ["test_reason"],
      "input_sha256": "f" * 64,
      "profile_sha256": "0" * 64,
      "sampling_audit": {
        "version": "native_static_response_5hz_v1",
        "raw_anchor_count": 100,
        "unique_response_count": 80,
        "selected_count": 50,
        "full_rate_excluded_counts": {"signal_gap": 5}
      }
    }
    validated = _link_receipt(receipt)
    self.assertIn("sampling_audit", validated)

    bad_receipt = dict(receipt)
    bad_receipt["objectives"] = {"objectives": bad_receipt["objectives"]["objectives"]}
    with self.assertRaises(ValueError):
      _link_receipt(bad_receipt)

    from copy import deepcopy
    for mutation in (
      {"raw_anchor_count": True}, {"unique_response_count": 101}, {"selected_count": 81},
      {"full_rate_excluded_counts": {"signal_gap": 101}},
      {"clock_basis": "anchor_time", "anchor_fallback_count": 1},
      {"full_rate_unavailable_for": {"private/path": {"recorded_dependency_unavailable": 1}}},
      {"full_rate_unavailable_for": {"LateralTorqueAccelFactor": {"unknown_private_reason": 1}}},
    ):
      with self.subTest(mutation=mutation):
        forged = deepcopy(receipt)
        forged["sampling_audit"].update(mutation)
        with self.assertRaises(ValueError):
          _link_receipt(forged)


if __name__ == "__main__":
  unittest.main()
