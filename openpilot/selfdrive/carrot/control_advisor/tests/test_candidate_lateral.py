"""Independent synthetic calibration fixtures, not device or tuning qualification."""
from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import candidate_lateral as lateral
from openpilot.selfdrive.carrot.control_advisor.torque_fit import estimate_torque
from openpilot.selfdrive.controls.lib.steer_ratio import resolve_vehicle_model_steer_ratio


def signal(value, unit, source="derived_input", stamp=0):
  return {"value": value, "unit": unit, "source": source, "source_mono_ns": stamp}


def record(index, signals, reference, block="fit-A"):
  return {"block_id": block, "route_id": "synthetic-route", "mono_ns": index * 100_000_000,
          "signals": signals, "reference": reference}


def context(rows, split="fit", **changes):
  value = {"settings_raw": {"LateralTorqueCustom": 1, "CustomSR": 0},
           "dependencies": {"torque_ff_path": "linear_standard", "torque_response_alignment": "declared_aligned",
                            "path_offset_selected": True},
           "vehicle_context": {}, "evidence": {}, "objective": {},
           "records": {"fit": [], "select": [], "holdout": []}}
  value["records"][split] = rows
  value.update(changes)
  return value


def profile(current, minimum, maximum, step):
  return {"parameter": {"current_raw": current, "min": minimum, "max": maximum, "step": step}}


def torque_rows(*, noisy=False, source="synthetic_truth"):
  rows = []
  for index, x in enumerate((-.4, -.3, -.2, -.1, .1, .2, .3, .4)):
    y = 3.0 * x + .12 + (.025 * (-1 if index % 2 else 1) if noisy else 0)
    rows.append(record(index, {"sent_torque": signal(x, "1", "consumer_state"),
                               "lateral_accel_independent": signal(y, "m/s^2", source),
                               "lateral_accel_offset": signal(.12, "m/s^2")}, y))
  return rows


def ratio_rows(*, rate=80, manual=None, live=15.):
  rows = []
  for index, angle in enumerate((-.16, -.08, -.04, .04, .08, .16)):
    factor, roll = .35, .0002
    ratio = live * rate * .01 if manual is None else manual
    curvature = factor * angle / ratio + roll
    rows.append(record(index, {
      "steering_angle_corrected": signal(angle, "rad", "consumer_state"),
      "vm_curvature_factor": signal(factor, "1/m"), "live_steer_ratio": signal(live, "1", "consumer_state"),
      "roll_curvature": signal(roll, "1/m"), "curvature_independent": signal(curvature, "1/m", "synthetic_truth"),
    }, curvature))
  return rows


class LateralCandidateTests(unittest.TestCase):
  def test_torque_tls_factor_calibrates_independent_response_and_matches_raw_adapter(self):
    rows = torque_rows()
    ctx = context(rows)
    proposal = lateral.propose_family("LateralTorqueAccelFactor", ctx, profile(2800, 1000, 6000, 100))
    self.assertAlmostEqual(3000., proposal["raw_estimate"])
    self.assertIn(3000, proposal["candidate_raw_values"])
    fit = estimate_torque([(row["signals"]["sent_torque"]["value"], row["reference"] - .12) for row in rows],
                          max_input_points=100_000, max_fit_points=100_000, seed=0)
    self.assertEqual(fit["raw_fit"]["latAccelFactor"] / .001, proposal["raw_estimate"])
    held = context(deepcopy(rows), "holdout")
    for row in held["records"]["holdout"]:
      row["block_id"] = "held-B"
    evaluated = lateral.evaluate_family("LateralTorqueAccelFactor", 3000, held, "holdout", {})
    self.assertEqual("evaluated", evaluated["status"])
    self.assertEqual("static_formula", evaluated["prediction_kind"])
    self.assertEqual([row["reference"] for row in rows], evaluated["predictions"])

  def test_model_derived_torque_response_and_unobserved_mapping_are_blocked(self):
    ctx = context(torque_rows(source="vehicle_model_response"))
    proposal = lateral.propose_family("LateralTorqueAccelFactor", ctx, profile(2800, 1000, 6000, 100))
    self.assertEqual(["independent_response_required"], proposal["reason_codes"])
    ctx = context(torque_rows(), dependencies={"torque_ff_path": "nnff", "torque_response_alignment": "declared_aligned"})
    self.assertEqual(["torque_mapping_adapter_unavailable"],
                     lateral.propose_family("LateralTorqueAccelFactor", ctx, {})["reason_codes"])

  def test_tls_residual_spread_is_a_proxy_without_a_friction_recommendation(self):
    ctx = context(torque_rows(noisy=True))
    proposed = lateral.propose_family("LateralTorqueFriction", ctx, profile(150, 0, 500, 10))
    self.assertGreater(proposed["raw_estimate"], 0.)
    self.assertEqual([], proposed["candidate_raw_values"])
    self.assertEqual(["physical_friction_not_identified"], proposed["reason_codes"])
    evaluated = lateral.evaluate_family("LateralTorqueFriction", 150, ctx, "fit", {})
    self.assertEqual("blocked", evaluated["status"])
    self.assertEqual([], evaluated["predictions"])

  def test_inverse_vehicle_model_rate_fit_and_consumer_resolution_are_source_faithful(self):
    rows = ratio_rows()
    proposed = lateral.propose_family("SteerRatioRate", context(rows), profile(100, 30, 200, 1))
    self.assertAlmostEqual(80., proposed["raw_estimate"])
    self.assertIn(80, proposed["candidate_raw_values"])
    for raw in (80, 100, 0, 201):
      evaluated = lateral.evaluate_family("SteerRatioRate", raw, context(rows, "holdout"), "holdout", {})
      self.assertEqual("evaluated", evaluated["status"])
      for row, predicted in zip(rows, evaluated["predictions"]):
        signals = row["signals"]
        resolved = resolve_vehicle_model_steer_ratio(signals["live_steer_ratio"]["value"], raw, 0)
        expected = signals["vm_curvature_factor"]["value"] * signals["steering_angle_corrected"]["value"] / resolved
        expected += signals["roll_curvature"]["value"]
        self.assertEqual(expected, predicted)
    floor_rows = ratio_rows(live=.05)
    result = lateral.evaluate_family("SteerRatioRate", 30, context(floor_rows, "holdout"), "holdout", {})
    self.assertAlmostEqual(floor_rows[0]["signals"]["vm_curvature_factor"]["value"]
                           * floor_rows[0]["signals"]["steering_angle_corrected"]["value"] / .1 + .0002,
                           result["predictions"][0])

  def test_ratio_manual_selection_is_observed_and_never_switched_by_a_candidate(self):
    rows = ratio_rows(manual=14.)
    ctx = context(rows, settings_raw={"CustomSR": 140})
    proposed = lateral.propose_family("CustomSR", ctx, profile(150, 11, 300, 1))
    self.assertAlmostEqual(140., proposed["raw_estimate"])
    self.assertIn(140, proposed["candidate_raw_values"])
    self.assertEqual(["consumer_bypassed"], lateral.propose_family("SteerRatioRate", ctx, {})["reason_codes"])
    self.assertEqual(["mode_switch_required"], lateral.propose_family("CustomSR", context(rows), {})["reason_codes"])
    self.assertEqual(["mode_switch_required"], lateral.evaluate_family("CustomSR", 0, ctx, "fit", {})["reason_codes"])
    bad = context(ratio_rows())
    bad["records"]["fit"][0]["signals"]["curvature_independent"]["source"] = "vehicle_model_response"
    self.assertEqual(["independent_response_required"], lateral.propose_family("SteerRatioRate", bad, {})["reason_codes"])

  def test_external_path_offset_fit_and_wrong_reference_or_path_counterexamples(self):
    rows = [record(index, {"unshifted_path_y": signal(base, "m"),
                           "reference_path_y": signal(base + .15, "m", "user_specified")}, base + .15)
            for index, base in enumerate((-.2, -.1, .0, .1, .3))]
    proposed = lateral.propose_family("PathOffset", context(rows), profile(0, -100, 100, 1))
    self.assertAlmostEqual(15., proposed["raw_estimate"])
    self.assertIn(15, proposed["candidate_raw_values"])
    evaluated = lateral.evaluate_family("PathOffset", 15, context(rows, "holdout"), "holdout", {})
    self.assertEqual([row["reference"] for row in rows], evaluated["predictions"])
    self.assertEqual(["consumer_bypassed_or_unobserved"],
                     lateral.propose_family("PathOffset", context(rows, dependencies={}), {})["reason_codes"])
    rows[0]["reference"] += .1
    self.assertEqual(["objective_reference_mismatch"],
                     lateral.evaluate_family("PathOffset", 15, context(rows, "holdout"), "holdout", {})["reason_codes"])

  def lag_rows(self, *, hole=False, constant=False):
    expected = [1. if constant else math.sin(index * 1.7) + .2 * math.cos(index * .37) for index in range(70)]
    result = []
    for index, value in enumerate(expected):
      if hole and index == 30:
        continue
      actual = expected[index - 2] if index >= 2 else 0.
      if hole and index in (31, 32):
        actual += 50.  # These response samples must not connect across the missing interval.
      result.append(record(index, {"desired_lateral_accel": signal(value, "m/s^2", "consumer_state"),
                                    "lateral_accel_independent": signal(actual, "m/s^2", "synthetic_truth")}, actual))
    return result

  def test_total_path_lag_is_numeric_but_not_an_automatic_actuator_delay_candidate(self):
    options = {"method_options": {"SteerActuatorDelay": {"lag_grid_seconds": [0., .1, .2, .3], "sample_period_seconds": .1}}}
    for hole in (False, True):
      proposed = lateral.propose_family("SteerActuatorDelay", context(self.lag_rows(hole=hole)), options)
      self.assertEqual(20., proposed["raw_estimate"])
      self.assertEqual([], proposed["candidate_raw_values"])
      self.assertEqual(["effective_delay_mapping_unavailable"], proposed["reason_codes"])
    self.assertEqual(["lag_grid_required"],
                     lateral.propose_family("SteerActuatorDelay", context(self.lag_rows()), {})["reason_codes"])
    self.assertEqual(["insufficient_lag_excitation"],
                     lateral.propose_family("SteerActuatorDelay", context(self.lag_rows(constant=True)), options)["reason_codes"])
    evaluated = lateral.evaluate_family("SteerActuatorDelay", 20, context(self.lag_rows(), "holdout"), "holdout", {})
    self.assertEqual("blocked", evaluated["status"])
    self.assertEqual([], evaluated["predictions"])

  def test_pid_and_smoothing_do_not_claim_tracking_benefit_without_a_closed_loop_backend(self):
    for key in ("LateralTorqueKpV", "LateralTorqueKiV", "LateralTorqueKf", "LatSmoothSec"):
      with self.subTest(key=key):
        self.assertEqual(["backend_unavailable"], lateral.propose_family(key, context([]), {})["reason_codes"])
        result = lateral.evaluate_family(key, 100, context([]), "holdout", {})
        self.assertEqual("closed_loop_model", result["prediction_kind"])
        self.assertEqual("blocked", result["status"])
    self.assertEqual(["derivative_path_inactive"], lateral.propose_family("LateralTorqueKd", context([]), {})["reason_codes"])

  def test_fit_only_inputs_no_mutation_and_no_files_or_vehicle_parameters(self):
    original = context(ratio_rows())
    original["records"]["holdout"] = ratio_rows(rate=150)
    unchanged = deepcopy(original)
    with patch("builtins.open", side_effect=AssertionError("math must not read files")):
      result = lateral.propose_family("SteerRatioRate", original, profile(100, 30, 200, 1))
      self.assertAlmostEqual(80., result["raw_estimate"])
      lateral.evaluate_family("SteerRatioRate", 80, original, "holdout", {})
    self.assertEqual(unchanged, original)

  def test_future_signals_and_lag_pairs_across_disjoint_blocks_are_rejected(self):
    future = context(ratio_rows())
    future["records"]["fit"][0]["signals"]["curvature_independent"]["source_mono_ns"] = 1
    self.assertEqual(["signal_time_missing_or_future"], lateral.propose_family("SteerRatioRate", future, {})["reason_codes"])
    rows = self.lag_rows()[:4]
    for index, row in enumerate(rows):
      row["block_id"] = f"separate-{index // 2}"
    options = {"method_options": {"SteerActuatorDelay": {"lag_grid_seconds": [.1, .2], "sample_period_seconds": .1}}}
    self.assertEqual(["insufficient_lag_overlap"], lateral.propose_family("SteerActuatorDelay", context(rows), options)["reason_codes"])

  def test_actual_dispatch_selects_lateral_calibration_and_retains_unqualified_flags(self):
    from openpilot.selfdrive.carrot.control_advisor.candidates import evaluate_candidates
    from .test_candidates import candidate_fixture
    pack, search = candidate_fixture()
    report = evaluate_candidates(pack, search)
    row = next(item for item in report["items"] if item["key"] == "PathOffset")
    self.assertEqual("search_candidate", row["status"], row)
    self.assertEqual(20, row["candidate_raw"])
    self.assertFalse(report["apply_enabled"])
    self.assertFalse(report["recommendations_enabled"])
    self.assertFalse(report["device_qualified"])
    self.assertEqual("static_formula", row["prediction_kind"])
    key = "LateralTorqueAccelFactor"
    pack["epoch"]["settings_raw"] = {key: 2800, "LateralTorqueCustom": 1}
    pack["epoch"]["dependencies"] = {"torque_ff_path": "linear_standard", "torque_response_alignment": "declared_aligned"}
    pack["epoch"]["end_ns"] = 6000
    pack["objectives"] = {key: {"id": "synthetic-response-calibration", "kind": "tracking", "source": "synthetic_truth",
                                "unit": "m/s^2", "reference_signal": "lateral_accel_independent"}}
    for block in pack["blocks"]:
      samples = []
      for index, value in enumerate(torque_rows()):
        stamp = block["start_ns"] + index * 10
        signals = deepcopy(value["signals"])
        for entry in signals.values():
          entry["source_mono_ns"] = stamp
        samples.append({"mono_ns": stamp, "excluded_reasons": [], "signals": signals})
      block.update(samples=samples, end_ns=block["start_ns"] + 70, finalized_through_ns=block["start_ns"] + 70)
    search["per_key_change_limits"] = {key: 300}
    search["guard_tolerances"] = {key: {"max_absolute_error": .2, "max_absolute_prediction": 2.}}
    result = next(item for item in evaluate_candidates(pack, search)["items"] if item["key"] == key)
    self.assertEqual("search_candidate", result["status"], result)
    self.assertEqual(3000, result["candidate_raw"])
    self.assertEqual(0., result["metrics"]["holdout"]["rmse"])


if __name__ == "__main__":
  unittest.main()
