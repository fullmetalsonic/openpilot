"""Independent first-order truth and production-PID parity, never device qualification."""
from copy import deepcopy
import math
import unittest

from openpilot.common.pid import PIDController
from openpilot.selfdrive.carrot.control_advisor import candidate_pid as backend


def _clip(value, low, high):
  return min(high, max(low, value))


def fixture(*, key="fit-A", start_ns=1_000_000_000, amplitude=1.8, gains=(0, 0, 80), count=800,
            friction=0., deadzone=0., low_speed=0., roll=0., initial_i=0., constant=False, sign=1., freeze=False,
            transport_lag=0, quantum=0., initial_response=0.):
  """Known plant advances independently of the fitted backend or its replay helpers."""
  factor, period, v_ego = 3., 10_000_000, 20.
  y, measured, integral = initial_response, initial_response, initial_i
  pose, pose_stamp = 0., start_ns
  rows = []
  commands = []
  production = PIDController(gains[0] * .01, gains[1] * .01, k_f=gains[2] * .01,
                             pos_limit=1., neg_limit=-1.)
  production.i = initial_i
  for index in range(count):
    stamp = start_ns + index * period
    if index % 5 == 0:
      pose, pose_stamp = y, stamp
    t = index * .01
    desired = amplitude if constant else amplitude * (.65 * math.sin(1.7 * t) + .35 * math.sin(4.1 * t))
    error = (desired - measured) * (1 + low_speed / v_ego**2) / factor
    accel_error = desired - measured
    friction_error = 0. if -deadzone < accel_error < deadzone else accel_error
    feedforward = (desired - roll) / factor + _clip(friction_error / .3, -1., 1.) * friction
    freeze_now = bool(freeze and 50 <= index < 100)
    before = integral
    p, f = error * gains[0] * .01, feedforward * gains[2] * .01
    if not freeze_now:
      integral += error * gains[1] * .01 * .01
      no_i = _clip(p + f, -1., 1.)
      integral = _clip(integral, -1. - no_i, 1. - no_i)
    command = _clip(p + integral + f, -1., 1.)
    commands.append(command)
    sent = commands[index - transport_lag] if index >= transport_lag else 0.
    if quantum:
      sent = round(sent / quantum) * quantum
    actual = production.update(error, speed=v_ego, feedforward=feedforward, freeze_integrator=freeze_now)
    if max(abs(actual - command), abs(production.i - integral)) > 1e-12:
      raise AssertionError("independent fixture PID differs from production")
    values = {
      "desired_lateral_accel": desired, "actual_lateral_accel": measured,
      "setpoint": desired * (1 + low_speed / v_ego**2), "measurement": measured * (1 + low_speed / v_ego**2),
      "low_speed_factor": low_speed, "v_ego": v_ego, "a_ego": 0., "roll_compensation": roll,
      "lateral_accel_deadzone": deadzone, "pid_error": error, "feedforward": feedforward,
      "integral_before": before, "p_after": p, "i_after": integral, "f_after": f, "output_torque": command,
      "effective_kp": gains[0] * .01, "effective_ki": gains[1] * .01, "effective_kf": gains[2] * .01,
      "factor": factor, "friction": friction, "freeze_integrator": int(freeze_now),
      "steer_limited_by_controls": 0, "curvature_limited": 0, "steering_pressed": 0,
      "pid_frame": index, "sent_torque_raw": sent, "lateral_accel_kinematic": pose,
    }
    signals = {}
    for name, value in values.items():
      source = "synthetic_truth" if name == "desired_lateral_accel" else "controller_trace"
      if name == "sent_torque_raw":
        source = "controller_output"
      elif name == "lateral_accel_kinematic":
        source = "synthetic_truth"
      signals[name] = {"value": value, "unit": backend.REQUIRED_SIGNALS[name], "source": source,
                       "source_mono_ns": pose_stamp if name == "lateral_accel_kinematic" else stamp}
    rows.append({"block_id": key, "route_id": "synthetic-route", "mono_ns": stamp,
                 "signals": signals, "reference": desired})
    # Exact, independent known stable first-order plant at the original controller clock.
    y = .94 * y + sign * .18 * sent
    measured = .94 * measured + sign * .18 * sent
  return rows


def context(rows, split="fit", *, gains=(0, 0, 80), friction=0., fit_context=None, quantum=0.):
  value = {
    "settings_raw": {**dict(zip(backend.KEYS, gains)), "LateralTorqueCustom": 1,
                     "LateralTorqueAccelFactor": 3000, "LateralTorqueFriction": int(friction * 1000)},
    "dependencies": {"pid_trace_schema": backend.SCHEMA, "pid_cycle_ns": 10_000_000,
                     "pid_pos_limit": 1., "pid_neg_limit": -1., "pid_output_sign": "positive_pid_domain",
                     "pid_source_clock": "original", "torque_ff_path": "linear_standard",
                     "pid_nnff_selected": False, "pid_nnff_lite_selected": False,
                     "pid_transport_sign": 1, "pid_transport_quantum": quantum,
                     "pid_transport_rule": "nearest" if quantum else "exact", "pid_transport_source": "synthetic_truth"},
    "evidence": {"source_kind": "synthetic", "epoch": {"id": "fixture-epoch"}},
    "vehicle_context": {"brand": "synthetic"}, "records": {"fit": [], "select": [], "holdout": []},
    "objective": {"kind": "tracking", "source": "synthetic_truth", "reference_signal": "desired_lateral_accel"},
  }
  value["records"][split] = rows
  if fit_context is not None:
    value["fit_context"] = deepcopy(fit_context)
  return value


def profile(current=80, **changes):
  options = deepcopy(backend.DEFAULT_OPTIONS)
  options.update(delay_grid_steps=[0], min_response_points=10, model_rmse_bound=.08,
                 model_max_error_bound=.15, minimum_improvement=.0001, noise_multiplier=1.)
  options.update(changes)
  return {"method_options": {key: deepcopy(options) for key in backend.KEYS},
          "parameter": {"current_raw": current, "min": 0, "max": 200, "step": 1}, "max_candidates_per_key": 8}


def wrapper_fixture(*, key="LateralTorqueKf", constant_validation=False, gains=(0, 0, 80), steady_validation=False):
  from .test_candidates import candidate_fixture
  evidence, outer_profile = candidate_fixture()
  base = context([], gains=gains)
  blocks = []
  for split, start, amplitude in (("fit", 1_000_000_000, 1.8), ("select", 20_000_000_000, .6),
                                  ("holdout", 40_000_000_000, .7)):
    rows = fixture(key=split + "-A", start_ns=start, amplitude=amplitude, gains=gains,
                   constant=constant_validation and split != "fit",
                   initial_response=amplitude if steady_validation and split != "fit" else 0.)
    blocks.append({"id": split + "-A", "route_id": "synthetic-route", "start_ns": rows[0]["mono_ns"],
                   "end_ns": rows[-1]["mono_ns"], "finalized_through_ns": rows[-1]["mono_ns"],
                   "mask_version": "explicit-synthetic-mask-v1",
                   "samples": [{"mono_ns": row["mono_ns"], "signals": deepcopy(row["signals"]), "excluded_reasons": []}
                               for row in rows]})
  evidence["blocks"] = blocks
  evidence["splits"] = {split: [split + "-A"] for split in ("fit", "select", "holdout")}
  evidence["epoch"].update(start_ns=blocks[0]["start_ns"], end_ns=blocks[-1]["end_ns"],
                            settings_raw=base["settings_raw"], dependencies=base["dependencies"])
  evidence["objectives"] = {key: {"id": "synthetic-controller-tracking", "kind": "tracking", "source": "synthetic_truth",
                                    "unit": "m/s^2", "reference_signal": "desired_lateral_accel"}}
  outer_profile.update(max_rows=3000, max_lag_ns=100_000_000, dependency_margin_ns=0,
                       min_samples_per_split=20, minimum_improvement=.0001,
                       guard_tolerances={key: {"mode": "baseline_bounded"}},
                       change_limit_policy="manual_experiment", per_key_change_limits=None,
                       method_options={key: deepcopy(profile()["method_options"][key])})
  return evidence, outer_profile


class CandidatePIDTests(unittest.TestCase):
  def test_known_current_equilibrium_is_retained_instead_of_forcing_a_gain_change(self):
    from openpilot.selfdrive.carrot.control_advisor.candidates import evaluate_candidates
    evidence, outer_profile = wrapper_fixture(gains=(0, 0, 100), constant_validation=True, steady_validation=True)
    report = evaluate_candidates(evidence, outer_profile)
    row = next(item for item in report["items"] if item["key"] == "LateralTorqueKf")
    self.assertEqual("keep_current", row["status"], row)
    self.assertIsNone(row["candidate_raw"])
    self.assertIn("current_value_retained", row["reason_codes"])
    self.assertIsNone(row["raw_estimate"])

  def test_proportional_and_integral_keys_reach_real_wrapper_numeric_selection(self):
    from openpilot.selfdrive.carrot.control_advisor.candidates import evaluate_candidates
    reports = {}
    for key in ("LateralTorqueKpV", "LateralTorqueKiV"):
      with self.subTest(key=key):
        evidence, outer_profile = wrapper_fixture(key=key, constant_validation=True)
        report = evaluate_candidates(evidence, outer_profile)
        row = next(item for item in report["items"] if item["key"] == key)
        self.assertEqual("search_candidate", row["status"], row)
        self.assertGreater(row["candidate_raw"], 0)
        self.assertLess(row["metrics"]["selection"]["rmse"], row["metrics"]["baseline"]["select"]["rmse"])
        self.assertLess(row["metrics"]["holdout"]["rmse"], row["metrics"]["baseline"]["holdout"]["rmse"])
        self.assertFalse(row["provenance"]["causal_improvement_proven"])
        reports[key] = deepcopy(report)
    type(self).pid_gain_reports = reports

  def test_real_candidate_wrapper_selects_numeric_change_and_confirms_untouched_holdout(self):
    from openpilot.selfdrive.carrot.control_advisor.candidates import evaluate_candidates, validate_candidate_report
    evidence, outer_profile = wrapper_fixture()
    original = deepcopy(evidence)
    report = evaluate_candidates(evidence, outer_profile)
    row = next(item for item in report["items"] if item["key"] == "LateralTorqueKf")
    self.assertEqual("search_candidate", row["status"], row)
    self.assertGreater(row["candidate_raw"], 80)
    self.assertIsNone(row["raw_estimate"])
    self.assertIsNone(row["estimate_kind"])
    self.assertLess(row["metrics"]["selection"]["rmse"], row["metrics"]["baseline"]["select"]["rmse"])
    self.assertLess(row["metrics"]["holdout"]["rmse"], row["metrics"]["baseline"]["holdout"]["rmse"])
    self.assertFalse(row["provenance"]["causal_improvement_proven"])
    self.assertEqual("manual_experiment", row["provenance"]["candidate_semantics"])
    self.assertFalse(report["device_qualified"])
    self.assertEqual(report, validate_candidate_report(report))
    self.assertEqual(original, evidence)
    type(self).wrapper_report = deepcopy(report)

  def test_current_pid_parity_includes_nonzero_integral_freeze_roll_friction_and_low_speed(self):
    gains = (40, 20, 80)
    ctx = context(fixture(amplitude=.5, gains=gains, friction=.02, deadzone=.03, low_speed=100.,
                          roll=.04, initial_i=.07, freeze=True), gains=gains, friction=.02)
    opts = backend._options(profile()["method_options"][backend.KEYS[0]])
    binding = backend._binding(ctx)
    groups = backend._rows(ctx, "fit", opts, binding)
    backend._parity(groups, binding, opts)
    self.assertEqual(.02, backend._friction(.1, .1, .06))
    self.assertEqual(0., backend._friction(.099, .1, .06))
    self.assertAlmostEqual(-.02, backend._friction(-.1, .1, .06))
    changed = deepcopy(groups)
    changed[0][80]["i_after"] += .01
    with self.assertRaisesRegex(ValueError, "controller_replay_mismatch"):
      backend._parity(changed, binding, opts)

  def test_fit_counts_twenty_hz_pose_once_and_recovers_two_channel_periods(self):
    ctx = context(fixture())
    proposed = backend.propose_family("LateralTorqueKf", ctx, profile())
    self.assertEqual([], proposed["reason_codes"])
    model = proposed["fit_context"]["models"][0]
    self.assertEqual(50_000_000, model["independent"][3])
    self.assertEqual(10_000_000, model["feedback"][3])
    self.assertAlmostEqual(.94, model["feedback"][0], places=10)
    self.assertAlmostEqual(.18, model["feedback"][1], places=10)
    self.assertAlmostEqual(.94**5, model["independent"][0], delta=.01)
    self.assertFalse(proposed["fit_context"]["causal_improvement_proven"])
    self.assertEqual("manual_experiment", proposed["fit_context"]["candidate_semantics"])

  def test_fit_proposes_search_grid_and_select_holdout_validate_a_real_numeric_change(self):
    ctx = context(fixture())
    original = deepcopy(ctx)
    proposed = backend.propose_family("LateralTorqueKf", ctx, profile())
    self.assertEqual([], proposed["reason_codes"])
    self.assertIsNone(proposed["raw_estimate"])
    self.assertIn(90, proposed["candidate_raw_values"])
    fixed = proposed["fit_context"]
    frozen = deepcopy(fixed)
    for split, start, amplitude in (("select", 20_000_000_000, .6), ("holdout", 40_000_000_000, .7)):
      rows = fixture(key=split + "-A", start_ns=start, amplitude=amplitude)
      current = context(rows, split, fit_context=fixed)
      baseline = backend.evaluate_family("LateralTorqueKf", 80, current, split, {})
      candidate = backend.evaluate_family("LateralTorqueKf", 90, current, split, {})
      self.assertEqual("evaluated", baseline["status"], baseline)
      self.assertEqual("evaluated", candidate["status"], candidate)
      errors = lambda report: sum((prediction - row["reference"])**2 for prediction, row in zip(report["predictions"], rows))
      self.assertLess(errors(candidate), errors(baseline))
    self.assertEqual(frozen, fixed)
    self.assertEqual(original, ctx)

  def test_transport_has_pointwise_unique_lag_and_stays_frozen_on_holdout(self):
    proposed = backend.propose_family("LateralTorqueKf", context(fixture(transport_lag=3)), profile())
    self.assertEqual([], proposed["reason_codes"])
    self.assertEqual(3, proposed["fit_context"]["transport"]["lag_steps"])
    fixed = proposed["fit_context"]
    rows = fixture(key="holdout-lag", start_ns=20_000_000_000, amplitude=.6, transport_lag=2)
    evaluated = backend.evaluate_family("LateralTorqueKf", 80, context(rows, "holdout", fit_context=fixed), "holdout", {})
    self.assertEqual(["pid_command_transport_changed"], evaluated["reason_codes"])
    unknown = context(fixture())
    del unknown["dependencies"]["pid_transport_quantum"]
    self.assertEqual(["pid_command_transport_unverified"], backend.propose_family("LateralTorqueKf", unknown, profile())["reason_codes"])

  def test_known_quantization_and_fixed_transport_lag_are_replayed_without_scale_fitting(self):
    quantum = .001
    proposed = backend.propose_family("LateralTorqueKf", context(fixture(transport_lag=2, quantum=quantum), quantum=quantum), profile())
    self.assertEqual([], proposed["reason_codes"])
    fixed = proposed["fit_context"]
    self.assertEqual({"lag_steps": 2, "sign": 1, "quantum": quantum, "rule": "nearest", "source": "synthetic_truth"}, fixed["transport"])
    rows = fixture(key="holdout-q", start_ns=20_000_000_000, amplitude=.6, transport_lag=2, quantum=quantum)
    evaluated = backend.evaluate_family("LateralTorqueKf", 90, context(rows, "holdout", fit_context=fixed, quantum=quantum), "holdout", {})
    self.assertEqual("evaluated", evaluated["status"], evaluated)
    changed = context(rows, "holdout", fit_context=fixed, quantum=.002)
    self.assertEqual(["pid_mapping_or_epoch_changed"], backend.evaluate_family("LateralTorqueKf", 90, changed, "holdout", {})["reason_codes"])

  def test_ambiguous_transport_and_clipped_actual_pid_are_not_identification_evidence(self):
    ctx = context(fixture(constant=True))
    options = backend._options(profile()["method_options"]["LateralTorqueKf"])
    binding = backend._binding(ctx)
    with self.assertRaisesRegex(ValueError, "pid_command_transport_ambiguous"):
      backend._transport(backend._rows(ctx, "fit", options, binding), binding, options)
    gains = (100, 100, 100)
    clipped = context(fixture(amplitude=4., gains=gains), gains=gains)
    self.assertEqual(["pid_output_clipping"], backend.propose_family("LateralTorqueKf", clipped, profile(current=100))["reason_codes"])

  def test_fit_cannot_read_or_refit_other_splits_and_rejects_changed_epoch(self):
    ctx = context(fixture())
    ctx["records"]["holdout"] = [{"not": "valid rows"}]
    proposed = backend.propose_family("LateralTorqueKf", ctx, profile())
    self.assertEqual([], proposed["reason_codes"])
    fixed = deepcopy(proposed["fit_context"])
    changed = context(fixture(key="holdout", start_ns=20_000_000_000, amplitude=.5), "holdout", fit_context=fixed)
    changed["evidence"]["epoch"]["id"] = "another-epoch"
    evaluated = backend.evaluate_family("LateralTorqueKf", 80, changed, "holdout", {})
    self.assertEqual(["pid_mapping_or_epoch_changed"], evaluated["reason_codes"])
    self.assertEqual(fixed, changed["fit_context"])

  def test_missing_profile_clock_alignment_nn_mode_and_mapping_are_rejected(self):
    self.assertEqual(["pid_profile_required"], backend.propose_family("LateralTorqueKf", context(fixture()), {})["reason_codes"])
    for key, value in (("pid_source_clock", "response_aligned"), ("pid_nnff_selected", True),
                       ("torque_ff_path", "unverified")):
      ctx = context(fixture())
      ctx["dependencies"][key] = value
      self.assertEqual(["pid_trace_contract_required"], backend.propose_family("LateralTorqueKf", ctx, profile())["reason_codes"])
    ctx = context(fixture())
    ctx["records"]["fit"][50]["signals"]["factor"]["value"] = 3.1
    self.assertEqual(["pid_mapping_or_gain_changed"], backend.propose_family("LateralTorqueKf", ctx, profile())["reason_codes"])

  def test_no_excitation_missing_control_cycle_and_source_conflicts_are_rejected(self):
    result = backend.propose_family("LateralTorqueKf", context(fixture(constant=True)), profile())
    self.assertEqual(["insufficient_pid_excitation"], result["reason_codes"])
    ctx = context(fixture())
    del ctx["records"]["fit"][100]
    self.assertEqual(["pid_cycle_gap"], backend.propose_family("LateralTorqueKf", ctx, profile())["reason_codes"])
    ctx = context(fixture())
    ctx["records"]["fit"][51]["signals"]["lateral_accel_kinematic"]["value"] += .01
    self.assertEqual(["pid_response_source_conflict"], backend.propose_family("LateralTorqueKf", ctx, profile())["reason_codes"])

  def test_driver_or_output_limitation_and_wrong_sign_are_not_tuning_evidence(self):
    for field in ("steer_limited_by_controls", "curvature_limited", "steering_pressed"):
      ctx = context(fixture())
      ctx["records"]["fit"][100]["signals"][field]["value"] = 1
      self.assertEqual(["pid_intervention_or_limitation"], backend.propose_family("LateralTorqueKf", ctx, profile())["reason_codes"])
    proposed = backend.propose_family("LateralTorqueKf", context(fixture(sign=-1.)), profile())
    self.assertEqual(["pid_model_pole_or_sign"], proposed["reason_codes"])

  def test_strict_fit_bundle_rejects_false_qualification_and_extra_data(self):
    proposed = backend.propose_family("LateralTorqueKf", context(fixture()), profile())
    fixed = proposed["fit_context"]
    self.assertEqual(fixed, backend.validate_fit_context(fixed))
    for mutate in (lambda value: value.update(causal_improvement_proven=True),
                   lambda value: value.update(extra="unexpected"),
                   lambda value: value.update(version=True),
                   lambda value: value["models"][0]["feedback"].__setitem__(1, -1.),
                   lambda value: value["models"][0].update(delay_steps=0.),
                   lambda value: value.update(model_error_floor=0.),
                   lambda value: value.update(command_support=[0., 0., 0.]),
                   lambda value: value["options"].update(version=True)):
      bad = deepcopy(fixed)
      mutate(bad)
      with self.assertRaises(ValueError):
        backend.validate_fit_context(bad)


if __name__ == "__main__":
  unittest.main()
