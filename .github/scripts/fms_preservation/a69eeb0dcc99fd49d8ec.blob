"""Static row retention keeps full-rate validation, masks and published evidence."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import lateral_observations, log_candidates, log_signals
from openpilot.selfdrive.carrot.control_advisor.analysis import AXES, Observation, _mask_axis
from openpilot.selfdrive.carrot.control_advisor.candidates import candidate_capabilities
from openpilot.selfdrive.carrot.control_advisor.normalizer import EventNormalizer
from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_OBJECTIVES_PATH, BUILTIN_PROFILE_PATH
from . import test_lateral_log_signals as lateral_fixture


class StaticLogProjectionTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    lateral_fixture.LateralLogSignalTests.setUpClass()

  def setUp(self):
    self.fixture = lateral_fixture.LateralLogSignalTests()
    self.fixture.setUp()
    self.addCleanup(self.fixture.doCleanups)
    self.config = json.loads(Path(BUILTIN_OBJECTIVES_PATH).read_text())
    self.capabilities = candidate_capabilities()
    self.projection = log_candidates._static_projection(self.config, self.capabilities)

  def extract(self, path, projection=None):
    return log_signals.extract_log_signals([path], analysis_profile=lateral_fixture.PROFILE,
      budget=lateral_fixture.BUDGET, static_projection=projection)

  def expected_projection(self, full):
    expected = copy.deepcopy(full)
    wanted = set(self.projection["signals"])
    for row in expected["rows"]:
      row["signals"] = {key: value for key, value in row["signals"].items() if key in wanted}
      row["lateral_observation"]["provenance"].pop("supports")
    return expected

  def test_default_retains_supports_and_explicit_projection_only_prunes_unused_fields(self):
    path = self.fixture.write(self.fixture.stream())
    full = self.extract(path)
    self.assertTrue(any(row["lateral_observation"]["provenance"]["supports"] for row in full["rows"]))
    projected = self.extract(path, self.projection)
    self.assertEqual(self.expected_projection(full), projected)
    self.assertEqual(len(full["rows"]), len(projected["rows"]))
    self.assertFalse(any("supports" in row["lateral_observation"]["provenance"] for row in projected["rows"]))
    for source, retained in zip(full["rows"], projected["rows"]):
      self.assertEqual(source["lateral_observation"]["availability"], retained["lateral_observation"]["availability"])
      for name in ("source_id", "epoch_id", "config_sha256", "response_mono_ns", "pose_event_mono_ns", "alignment",
                   "consumer_mapping_verified", "parameter_application_ack", "closed_loop_improvement_proven"):
        self.assertEqual(source["lateral_observation"]["provenance"][name],
                         retained["lateral_observation"]["provenance"][name])

  def test_projection_preserves_invalid_override_cp_and_stale_masks(self):
    cases = ({"override": True}, {"future": True}, {"runtime_changed": True}, {"omitted": "liveDelay"},
             {"omitted": "liveCalibration"}, {"changed": True})
    for index, options in enumerate(cases):
      with self.subTest(options=options):
        path = self.fixture.write(self.fixture.stream(**options), segment=index)
        full = self.extract(path)
        self.assertEqual(self.expected_projection(full), self.extract(path, self.projection))

  def test_support_budget_still_runs_before_arrays_are_discarded(self):
    path = self.fixture.write(self.fixture.stream())
    for limit in (25, 32):
      with self.subTest(limit=limit), patch.object(lateral_observations, "MAX_SUPPORTS", limit):
        full = self.extract(path)
        projected = self.extract(path, self.projection)
        self.assertTrue(any("provenance_support_budget" in row["lateral_observation"]["reason_codes"]
                            for row in full["rows"]))
        self.assertEqual(self.expected_projection(full), projected)

  def test_mask_context_projection_matches_original_full_context_masks(self):
    path = self.fixture.write(self.fixture.stream(override=True))
    original_feed = EventNormalizer.feed
    original = {axis: [] for axis in AXES}

    def collect(normalizer, *args, **kwargs):
      observations = original_feed(normalizer, *args, **kwargs)
      for observation in observations:
        original[observation["axis"]].append(Observation(**copy.deepcopy(observation)))
      return observations

    with patch.object(EventNormalizer, "feed", collect):
      extracted = self.extract(path)
    finalized = extracted["source_receipts"][0]["finalized_through_ns"]
    for axis, observations in original.items():
      self.assertTrue(any(len(item.context) > 2 for item in observations))
      expected = _mask_axis(observations, lateral_fixture.PROFILE)
      for row, reasons in zip(extracted["rows"], expected):
        self.assertEqual(sorted(reasons | ({"unfinalized_tail"} if row["mono_ns"] > finalized else set())),
                         row["excluded_reasons"][axis])

  def test_unsupported_projection_is_rejected_and_dense_config_is_not_projected(self):
    path = self.fixture.write(self.fixture.stream())
    for invalid in (True, {}, {"version": "other", "signals": ["v_ego"]},
                    {"version": "native_static_rows_v1", "signals": ["v_ego", "v_ego"]},
                    {"version": "native_static_rows_v1", "signals": ["unknown"]},
                    {"version": "native_static_rows_v1", "signals": ["v_ego"] * 65}):
      with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "invalid_static_projection"):
        self.extract(path, invalid)
    dense = copy.deepcopy(self.config)
    dense.pop("sampling")
    self.assertIsNone(log_candidates._static_projection(dense, self.capabilities))

  def test_projected_and_full_rows_make_identical_pack_and_isolated_selected_samples(self):
    path = self.fixture.write(self.fixture.stream())
    full, projected = self.extract(path), self.extract(path, self.projection)
    history = {"state": "unlinked", "epochs": []}
    profile = json.loads(Path(BUILTIN_PROFILE_PATH).read_text())

    def packed(extracted):
      epoch = log_candidates._epoch_rows(extracted["rows"], history, 4)[0]
      return log_candidates._pack(epoch, extracted, history, self.config, profile, self.capabilities)

    before = copy.deepcopy(projected)
    self.assertEqual(packed(full), packed(projected))
    evidence, _ = packed(projected)
    sample = next(sample for block in evidence["blocks"] for sample in block["samples"] if "v_ego" in sample["signals"])
    sample["signals"]["v_ego"]["value"] = -12345.
    self.assertEqual(before, projected)


class TorqueTimelineTests(unittest.TestCase):
  def setUp(self):
    self.epoch = {"boot_id": "boot", "vehicle_identity": {"config_sha256": "a" * 64},
                  "code_hashes": {"owned.py": "b" * 64}}
    self.row = {"mono_ns": 2500, "signals": {"sent_torque": {"source_mono_ns": 2200},
                "lateral_accel_independent": {"source_mono_ns": 2400}}}
    self.profile = {"max_lag_ns": 2000}

  def event(self, stamp, *, valid=True):
    return {"consumer_id": "controls.torque", "produced_mono_ns": stamp, "phase": "sampled_use", "boot_id": "boot",
      "context": {"active": True, "torque_ff_path": "linear_standard" if valid else "unverified", "nnff": False,
                  "nnff_lite": False, "nnff_model_selected": False}, "identity_version": 1,
      "vehicle_identity": {"state": "observed", "config_sha256": "a" * 64},
      "source_hashes": {"owned.py": "b" * 64}}

  def selected(self, events):
    ordered = sorted(events, key=lambda item: item["produced_mono_ns"])
    timeline = ([item["produced_mono_ns"] for item in ordered], ordered)
    return log_candidates._standard_torque_event(self.epoch, self.row, self.profile, timeline)

  def test_latest_invalid_event_does_not_fall_back_to_older_valid(self):
    self.assertIsNone(self.selected([self.event(1000), self.event(2000, valid=False)]))

  def test_same_timestamp_keeps_first_arrival_tie_and_ignores_future(self):
    first, second, future = self.event(2000), self.event(2000, valid=False), self.event(2300)
    self.assertIs(self.selected([future, first, second]), first)
    self.assertIsNone(self.selected([second, first, future]))

  def test_missing_and_stale_event_boundaries_remain_explicit(self):
    self.assertIsNone(self.selected([]))
    self.assertIsNone(self.selected([self.event(2300)]))
    self.assertIsNotNone(self.selected([self.event(500)]))
    self.assertIsNone(self.selected([self.event(499)]))


if __name__ == "__main__":
  unittest.main()
