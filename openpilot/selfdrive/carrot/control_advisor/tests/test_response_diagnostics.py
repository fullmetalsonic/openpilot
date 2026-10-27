from copy import deepcopy
import unittest
import json

from openpilot.selfdrive.carrot.control_advisor import calibration_review as review
from openpilot.selfdrive.carrot.control_advisor import response_diagnostics as response
from .response_diagnostic_fixtures import response_rows


class ResponseDiagnosticsTests(unittest.TestCase):
  def test_known_lag_tau_from_full_rate_independent_forward_generator(self):
    rows = response_rows()
    summary = review.summarize_diagnostics(rows, rows[0]["mono_ns"], rows[-1]["mono_ns"], 250_000_000)
    diagnostics = summary["diagnostics"]
    stage = next(item for item in diagnostics["stages"] if item["id"] == "longitudinal_scc_to_acceleration")
    self.assertEqual("identified_response", stage["state"], stage)
    self.assertGreaterEqual(stage["event_count"], 6)
    self.assertAlmostEqual(.2, stage["lag_s"]["estimate"], delta=.05)
    self.assertAlmostEqual(.3, stage["time_constant_s"]["estimate"], delta=.025)
    self.assertLessEqual(stage["time_constant_s"]["lower"], .3)
    self.assertGreaterEqual(stage["time_constant_s"]["upper"], .3)
    self.assertLessEqual(stage["lag_s"]["lower"], .2)
    self.assertGreaterEqual(stage["lag_s"]["upper"], .2)
    self.assertEqual("experiment_required", diagnostics["timing"]["LongActuatorDelay"]["state"])
    self.assertEqual("experiment_required", diagnostics["timing"]["SteerActuatorDelay"]["state"])
    self.assertFalse(diagnostics["software_optimum_identified"])
    self.assertLessEqual(len(summary["series"]["longitudinal"]["points"]), 600)
    self.assertGreater(summary["metrics"]["longitudinal"]["raw_count"], 600)
    response.validate_diagnostics(diagnostics, rows[0]["mono_ns"], rows[-1]["mono_ns"])
    self.assertEqual(diagnostics, response.build_diagnostics(rows))

  def test_second_order_generator_rejects_first_order_delay_claim(self):
    rows = response_rows(second_order=True)
    value = response.build_diagnostics(rows)
    stage = next(item for item in value["stages"] if item["id"] == "longitudinal_scc_to_acceleration")
    self.assertEqual("held", stage["state"], stage)
    self.assertIn("response_residual_serial_correlation", stage["rejected_reasons"])
    self.assertEqual("held", value["timing"]["LongActuatorDelay"]["state"])

  def test_noise_saturation_missing_clock_and_insufficient_events_hold(self):
    for kwargs in ({"noise": .4}, {"saturated": True}, {"missing_time": True}, {"event_count": 2}):
      with self.subTest(kwargs=kwargs):
        value = response.build_diagnostics(response_rows(**kwargs))
        self.assertEqual("held", value["timing"]["LongActuatorDelay"]["state"])

  def test_future_source_clock_cannot_be_used_as_an_observed_input(self):
    rows = response_rows(event_count=2)
    for row in rows:
      row["sent_mono_ns"] += 100_000_000
    value = response.build_diagnostics(rows)
    self.assertEqual("held", value["timing"]["LongActuatorDelay"]["state"])
    stage = next(item for item in value["stages"] if item["id"] == "longitudinal_scc_to_acceleration")
    self.assertEqual(0, stage["event_count"])
    self.assertIn("stage_signal_or_original_clock_missing", stage["reasons"])

  def test_large_original_event_timestamps_remain_exact_integers(self):
    rows = response_rows(event_count=2)
    clocks = ("mono_ns", "target_mono_ns", "actual_mono_ns", "independent_mono_ns", "sent_mono_ns", "steering_mono_ns", "yaw_mono_ns")
    for row in rows:
      for key in clocks:
        row[key] += 900_000_000_000_000_007
    original = {row["mono_ns"] for row in rows}
    value = response.build_diagnostics(rows)
    count = 0
    for stage in value["stages"]:
      for event in stage["events"]:
        self.assertIn(event["start_ns"], original)
        self.assertIn(event["end_ns"], original)
        count += 1
    self.assertGreater(count, 0)

  def test_stop_preparation_and_standstill_do_not_become_response_events(self):
    for flag in ("should_stop", "standstill"):
      rows = response_rows(event_count=2)
      for row in rows:
        row[flag] = True
        row["stop_req"] = 0
      value = response.build_diagnostics(rows)
      self.assertTrue(all(stage["event_count"] == 0 for stage in value["stages"]))
      self.assertEqual("held", value["timing"]["LongActuatorDelay"]["state"])

  def test_different_target_amplitudes_do_not_become_repeat_noise(self):
    value = response.build_diagnostics(response_rows(amplitudes=[1., 1.2, 1.5, 1.8, 2.2, 2.6, 3., 3.5]))
    stage = next(item for item in value["stages"] if item["id"] == "longitudinal_scc_to_acceleration")
    self.assertEqual("identified_response", stage["state"], stage["reasons"])
    timing = value["timing"]["LongActuatorDelay"]
    self.assertEqual("held", timing["state"])
    self.assertIn("matched_repeat_tracking_events_insufficient", timing["reasons"])

  def test_validator_rejects_causality_seed_intervals_and_unsupported_trials(self):
    rows = response_rows(event_count=2)
    value = response.build_diagnostics(rows)
    for path, replacement in ((["causal_effect_proven"], True), (["execution", "seed"], 5),
      (["stages", 0, "lag_s"], {"estimate": 1., "lower": 2., "upper": 0.}),
      (["timing", "LongActuatorDelay", "state"], "experiment_required")):
      bad, cursor = deepcopy(value), None
      cursor = bad
      for key in path[:-1]:
        cursor = cursor[key]
      cursor[path[-1]] = replacement
      with self.subTest(path=path), self.assertRaises(ValueError):
        response.validate_diagnostics(bad, rows[0]["mono_ns"], rows[-1]["mono_ns"])

  def test_reservoir_reads_entire_long_trip_and_keeps_native_windows_bounded(self):
    collector = response.DiagnosticWindows(1_000_000_000)
    for index in range(205000):
      collector.add({"axis": "longitudinal", "mono_ns": 1_000_000_000 + index*10_000_000})
    rows, selection = collector.finish()
    self.assertEqual(205000, selection["input_rows"])
    self.assertEqual(response.OPTIONS["reservoir_windows"], len(selection["selected_windows"]))
    self.assertLessEqual(len(rows), response.OPTIONS["max_rows"])
    self.assertFalse(selection["overflow"])
    self.assertGreater(max(selection["selected_windows"]), 32)
    self.assertEqual(10_000_000, rows[1]["mono_ns"] - rows[0]["mono_ns"])

  def test_excluded_plot_segment_is_null_and_raw_counts_remain(self):
    rows = response_rows(event_count=1)
    for row in rows:
      row["excluded_reasons"] = ["driver_intervention"]
    summary = review.summarize_diagnostics(rows, rows[0]["mono_ns"], rows[-1]["mono_ns"], 250_000_000)
    for axis in ("lateral", "longitudinal"):
      self.assertTrue(all(point[9] is None for point in summary["series"][axis]["points"]))
      self.assertEqual(900, summary["metrics"][axis]["raw_count"])
      self.assertEqual(0, summary["metrics"][axis]["usable_count"])

  def test_many_event_review_compacts_examples_not_full_rate_intervals(self):
    from .test_six_parameter_backend import manual_fixture
    evidence, host, config, _ = manual_fixture("LongActuatorDelay")
    rows = response_rows(event_count=32)
    evidence["epoch"]["end_ns"] = rows[-1]["mono_ns"]
    summary = review.summarize_diagnostics(rows, rows[0]["mono_ns"], rows[-1]["mono_ns"], 250_000_000)
    result = review.build_calibration_review(evidence, config, host, summary, input_sha256="f"*64, profile_sha256="1"*64)
    self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()), review.MAX_REVIEW_BYTES)
    compacted = result["diagnostics"]["stages"][0]
    full = summary["diagnostics"]["stages"][0]
    self.assertGreater(full["event_count"], response.OPTIONS["report_event_details"])
    self.assertTrue(compacted["events_truncated"])
    self.assertEqual(full["event_count"], compacted["event_count"])
    self.assertEqual(full["event_ids"], compacted["event_ids"])
    self.assertEqual(full["lag_s"], compacted["lag_s"])

  def test_sampled_mapping_requires_parity_mask_and_original_frame_fields(self):
    from .test_six_parameter_backend import manual_fixture
    evidence, host, config, summary = manual_fixture("LatSmoothSec")
    original = review.build_calibration_review(evidence, config, host, summary, input_sha256="f"*64, profile_sha256="1"*64)
    self.assertEqual("ready", original["state"], original["reasons"])
    self.assertFalse(original["diagnostics"]["sampled_smoothing"]["continuous_response_identified"])
    for kind in ("missing", "parity", "mixed_current", "masked"):
      changed, stats = deepcopy(evidence), deepcopy(summary)
      events = [event for event in changed["consumer_evidence"] if event["consumer_id"] == "modeld.action"]
      if kind == "missing":
        for event in events:
          event["context"].pop("lat_pre_smooth_curvature")
      elif kind == "parity":
        for event in events:
          event["context"]["lat_post_smooth_curvature"] += .001
      elif kind == "mixed_current":
        events[len(events)//2]["fields"]["LatSmoothSec"]["effective_value"] += .1
      else:
        stats["_consumer_times"]["lateral"] = []
      result = review.build_calibration_review(changed, config, host, stats, input_sha256="f"*64, profile_sha256="1"*64)
      self.assertEqual(("held", []), (result["state"], result["trial_options"]), (kind, result["reasons"]))

  def test_diagnostic_row_preserves_existing_source_clocks(self):
    value = review.diagnostic_row({"axis": "lateral", "mono_ns": 10, "target": 1., "actual": .8,
      "context": {"target_mono_ns": 9, "actual_mono_ns": 9, "saturated": True}},
      {"steering_angle_deg": {"value": 2., "source_mono_ns": 8}, "yaw_rate_calibrated": {"value": .1, "source_mono_ns": 7},
       "v_ego": {"value": 15., "source_mono_ns": 10}}, [])
    self.assertEqual((2., 8, .1, 7, 15., True), tuple(value[key] for key in
      ("steering_angle_deg", "steering_mono_ns", "yaw_rate", "yaw_mono_ns", "v_ego", "saturated")))


if __name__ == "__main__":
  unittest.main()
