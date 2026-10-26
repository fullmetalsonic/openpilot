"""Bounded observational torque-PID models for manual experiments, not qualification."""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
import hashlib
import json
import math
from numbers import Real

import numpy as np

from openpilot.common.pid import PIDController


KEYS = ("LateralTorqueKpV", "LateralTorqueKiV", "LateralTorqueKf")
KIND = "observational_pid_ensemble_v1"
SCHEMA = "torque_pid_cycle_v1"
METHOD = "linear_torque_pid_observational_ensemble_v1"
REQUIRED_SIGNALS = {
  "desired_lateral_accel": "m/s^2", "actual_lateral_accel": "m/s^2", "setpoint": "m/s^2",
  "measurement": "m/s^2", "low_speed_factor": "m^2/s^2", "v_ego": "m/s", "a_ego": "m/s^2",
  "roll_compensation": "m/s^2", "lateral_accel_deadzone": "m/s^2", "pid_error": "1",
  "feedforward": "1", "integral_before": "1", "p_after": "1", "i_after": "1", "f_after": "1",
  "output_torque": "1", "effective_kp": "1", "effective_ki": "1", "effective_kf": "1",
  "factor": "m/s^2", "friction": "1", "freeze_integrator": "bool", "steer_limited_by_controls": "bool",
  "curvature_limited": "bool", "steering_pressed": "bool", "pid_frame": "count",
  "sent_torque_raw": "1", "lateral_accel_kinematic": "m/s^2",
}
# Development identification thresholds, never physical safety or device qualification limits.
DEFAULT_OPTIONS = {
  "version": 1, "delay_grid_steps": [0, 2, 5, 10, 15, 20], "max_models": 8,
  "transport_lag_grid_steps": [0, 1, 2, 3, 4, 5],
  "min_response_points": 20, "min_torque_std": .01, "max_condition": 1e6,
  "max_speed_span": 2., "max_clock_jitter_ns": 3_000_000, "max_signal_age_ns": 100_000_000,
  "parity_tolerance": 1e-6, "mapping_tolerance": 1e-6, "model_rmse_bound": .1,
  "model_max_error_bound": .35, "plausibility_rmse_margin": .01,
  "pole_min": .00001, "pole_max": .99999, "max_prediction_disagreement": .2,
  "minimum_improvement": .002, "noise_multiplier": 2., "command_margin": 0.,
  "command_rate_margin": 0., "max_integral_growth": .05, "max_error_growth": 0.,
  "candidate_offsets_steps": [-10, -5, -1, 0, 1, 5, 10], "max_rows": 20_000,
}
_BOOLS = {name for name, unit in REQUIRED_SIGNALS.items() if unit == "bool"}
_MODEL_KEYS = {"delay_steps", "independent", "feedback", "fit_rmse", "fit_max_error"}
_BUNDLE_KEYS = {"version", "kind", "candidate_semantics", "causal_improvement_proven", "trace_schema", "period_ns",
                "binding_sha256", "gains_raw", "factor", "friction", "pos_limit", "neg_limit", "fit_blocks", "options",
                "models", "command_support", "speed_support", "model_error_floor", "transport_contract", "transport"}


class _Blocked(ValueError):
  pass


def _number(value, reason="invalid_pid_numeric"):
  if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
    raise _Blocked(reason)
  return float(value)


def _integer(value, minimum=0, maximum=2**63 - 1):
  if type(value) is not int or not minimum <= value <= maximum:
    raise _Blocked("invalid_pid_integer")
  return value


def _options(value):
  if type(value) is not dict or set(value) != set(DEFAULT_OPTIONS):
    raise _Blocked("pid_profile_required")
  result = deepcopy(value)
  if type(result["version"]) is not int or result["version"] != 1:
    raise _Blocked("pid_profile_version")
  for key, minimum, maximum in (("max_models", 1, 16), ("min_response_points", 4, 100_000),
                                ("max_rows", 8, 100_000), ("max_clock_jitter_ns", 0, 10_000_000),
                                ("max_signal_age_ns", 0, 1_000_000_000)):
    _integer(result[key], minimum, maximum)
  for key in set(result) - {"version", "delay_grid_steps", "transport_lag_grid_steps", "candidate_offsets_steps", "max_models", "min_response_points",
                           "max_rows", "max_clock_jitter_ns", "max_signal_age_ns"}:
    result[key] = _number(result[key], "invalid_pid_profile")
    if result[key] < 0:
      raise _Blocked("invalid_pid_profile")
  if not 0 < result["pole_min"] < result["pole_max"] < 1 or result["max_condition"] <= 1:
    raise _Blocked("invalid_pid_profile")
  if min(result[key] for key in ("parity_tolerance", "mapping_tolerance", "min_torque_std",
                                 "model_rmse_bound", "model_max_error_bound")) <= 0:
    raise _Blocked("invalid_pid_profile")
  for key, limit, low, high in (("delay_grid_steps", 32, 0, 100), ("transport_lag_grid_steps", 16, 0, 20),
                               ("candidate_offsets_steps", 32, -200, 200)):
    values = result[key]
    if type(values) is not list or not 1 <= len(values) <= limit or len(set(values)) != len(values):
      raise _Blocked("invalid_pid_profile_grid")
    for number in values:
      _integer(number, low, high)
    result[key] = sorted(values)
  if 0 not in result["candidate_offsets_steps"]:
    raise _Blocked("invalid_pid_profile_grid")
  return result


def capabilities():
  return {key: {"method_id": METHOD, "backend": "observational_pid_model", "required_signals": dict(REQUIRED_SIGNALS),
                "output_unit": "m/s^2", "prediction_kind": "closed_loop_model", "reason_codes": [],
                "candidate_semantics": "manual_experiment", "causal_improvement_proven": False} for key in KEYS}


def _binding(context):
  dependencies = context.get("dependencies", {})
  expected = {"pid_trace_schema": SCHEMA, "pid_cycle_ns": 10_000_000, "pid_output_sign": "positive_pid_domain",
              "pid_source_clock": "original", "torque_ff_path": "linear_standard", "pid_nnff_selected": False,
              "pid_nnff_lite_selected": False}
  if any(dependencies.get(key) != value or type(dependencies.get(key)) is not type(value) for key, value in expected.items()):
    raise _Blocked("pid_trace_contract_required")
  pos = _number(dependencies.get("pid_pos_limit"))
  neg = _number(dependencies.get("pid_neg_limit"))
  if not neg < 0 < pos:
    raise _Blocked("invalid_pid_output_limits")
  settings = context.get("settings_raw", {})
  gains = [_number(settings.get(key)) for key in KEYS]
  if any(not value.is_integer() or not 0 <= value <= 200 for value in gains):
    raise _Blocked("pid_stored_gain_required")
  if _number(settings.get("LateralTorqueCustom")) <= 0:
    raise _Blocked("consumer_bypassed")
  factor = _number(settings.get("LateralTorqueAccelFactor")) * .001
  friction = _number(settings.get("LateralTorqueFriction")) * .001
  if factor <= 0 or friction < 0:
    raise _Blocked("pid_mapping_invalid")
  transport = {"sign": dependencies.get("pid_transport_sign"), "quantum": dependencies.get("pid_transport_quantum"),
               "rule": dependencies.get("pid_transport_rule"), "source": dependencies.get("pid_transport_source")}
  _transport_contract(transport)
  if transport["source"] == "synthetic_truth" and context.get("evidence", {}).get("source_kind") != "synthetic":
    raise _Blocked("pid_command_transport_unverified")
  identity = {"settings": {key: settings[key] for key in (*KEYS, "LateralTorqueCustom", "LateralTorqueAccelFactor", "LateralTorqueFriction")},
              "dependencies": {**expected, "pid_pos_limit": pos, "pid_neg_limit": neg},
              "transport_contract": transport,
              "epoch": context.get("evidence", {}).get("epoch"), "vehicle": context.get("vehicle_context"),
              "source_kind": context.get("evidence", {}).get("source_kind")}
  try:
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
  except (TypeError, ValueError, OverflowError):
    raise _Blocked("pid_identity_invalid") from None
  return {"gains_raw": gains, "factor": factor, "friction": friction, "pos_limit": pos, "neg_limit": neg,
          "period_ns": expected["pid_cycle_ns"], "binding_sha256": digest, "transport_contract": transport}


def _transport_contract(value):
  if type(value) is not dict or set(value) != {"sign", "quantum", "rule", "source"}:
    raise _Blocked("pid_command_transport_unverified")
  if type(value["sign"]) is not int or value["sign"] != 1 or value["source"] not in {"synthetic_truth", "producer_observation"}:
    raise _Blocked("pid_command_transport_unverified")
  try:
    quantum = _number(value["quantum"])
  except _Blocked:
    raise _Blocked("pid_command_transport_unverified") from None
  if quantum < 0 or value["rule"] != ("exact" if quantum == 0 else "nearest"):
    raise _Blocked("pid_command_transport_unverified")


def _quantized(value, contract):
  quantum = contract["quantum"]
  return round(value / quantum) * quantum if quantum else value


def _transport(groups, binding, options, fixed_lag=None):
  contract = binding["transport_contract"]
  def matches(lag):
    for group in groups:
      clocks = [row["mono_ns"] for row in group]
      compared = 0
      for stamp, actual in zip(*_input(group)[:2]):
        index = bisect_right(clocks, stamp) - 1 - lag
        if index < 0:
          continue
        expected = _quantized(group[index]["output_torque"], contract)
        if abs(actual - expected) > options["mapping_tolerance"] + _roundoff(actual, expected):
          return False
        compared += 1
      if compared < options["min_response_points"]:
        return False
    return True
  if fixed_lag is not None:
    if not matches(fixed_lag):
      raise _Blocked("pid_command_transport_changed")
    return {"lag_steps": fixed_lag, **contract}
  possible = [lag for lag in options["transport_lag_grid_steps"] if matches(lag)]
  if not possible:
    raise _Blocked("pid_command_transport_unverified")
  if len(possible) != 1:
    raise _Blocked("pid_command_transport_ambiguous")
  return {"lag_steps": possible[0], **contract}


def _rows(context, split, options, binding):
  rows = context.get("records", {}).get(split)
  if type(rows) is not list or not rows:
    raise _Blocked("pid_trace_required")
  if len(rows) > options["max_rows"]:
    raise _Blocked("numerical_budget_exceeded")
  groups, seen, active = [], set(), None
  for row in rows:
    if type(row) is not dict or type(row.get("signals")) is not dict:
      raise _Blocked("invalid_pid_trace")
    stamp = _integer(row.get("mono_ns"))
    block = (row.get("route_id"), row.get("block_id"))
    if any(type(value) is not str or not value or len(value) > 192 for value in block):
      raise _Blocked("invalid_pid_block")
    if block != active:
      if block in seen:
        raise _Blocked("pid_block_order_invalid")
      seen.add(block)
      groups.append([])
      active = block
    values = {}
    stamps = {}
    for key, unit in REQUIRED_SIGNALS.items():
      signal = row["signals"].get(key)
      if type(signal) is not dict or signal.get("unit") != unit:
        raise _Blocked("pid_signal_missing_or_wrong_unit")
      source_stamp = _integer(signal.get("source_mono_ns"), 0, stamp)
      if stamp - source_stamp > options["max_signal_age_ns"]:
        raise _Blocked("pid_signal_stale")
      source = signal.get("source")
      allowed = {"controller_trace"}
      if key == "desired_lateral_accel":
        allowed = {"controller_target"}
        if context.get("evidence", {}).get("source_kind") == "synthetic":
          allowed.add("synthetic_truth")
      elif key == "sent_torque_raw":
        allowed = {"controller_output"}
      elif key == "lateral_accel_kinematic":
        allowed = {"independent_measurement", "synthetic_truth"}
        if source == "synthetic_truth" and context.get("evidence", {}).get("source_kind") != "synthetic":
          raise _Blocked("recorded_response_is_synthetic")
      if source not in allowed:
        raise _Blocked("pid_signal_source_mismatch")
      values[key] = _number(signal.get("value"))
      stamps[key] = source_stamp
      if key in _BOOLS and values[key] not in (0, 1):
        raise _Blocked("pid_boolean_invalid")
    if any(values[key] for key in ("steer_limited_by_controls", "curvature_limited", "steering_pressed")):
      raise _Blocked("pid_intervention_or_limitation")
    if values["v_ego"] < 5 or values["low_speed_factor"] < 0 or values["lateral_accel_deadzone"] < 0:
      raise _Blocked("pid_mapping_domain_unavailable")
    for key, expected in (("factor", binding["factor"]), ("friction", binding["friction"]),
                          *(zip(("effective_kp", "effective_ki", "effective_kf"), (gain * .01 for gain in binding["gains_raw"])))):
      if abs(values[key] - expected) > options["mapping_tolerance"]:
        raise _Blocked("pid_mapping_or_gain_changed")
    frame = values["pid_frame"]
    if not frame.is_integer() or not 0 <= frame <= 2**53 - 1:
      raise _Blocked("pid_frame_invalid")
    values.update(mono_ns=stamp, source_stamps=stamps, block_id=block[1])
    group = groups[-1]
    if group:
      previous = group[-1]
      if frame != previous["pid_frame"] + 1 or abs(stamp - previous["mono_ns"] - binding["period_ns"]) > options["max_clock_jitter_ns"]:
        raise _Blocked("pid_cycle_gap")
    group.append(values)
  if len(groups) > 64:
    raise _Blocked("numerical_budget_exceeded")
  speeds = [row["v_ego"] for group in groups for row in group]
  if max(speeds) - min(speeds) > options["max_speed_span"]:
    raise _Blocked("pid_speed_domain_changed")
  return groups


def _friction(error, deadzone, friction):
  # This is the native-free form of the verified standard callback's 0.3 deadzone interpolation.
  shifted = 0. if -deadzone < error < deadzone else error
  return min(1., max(-1., shifted / .3)) * friction


def _pid_inputs(row, feedback, binding):
  error = (row["desired_lateral_accel"] - feedback) * (1. + row["low_speed_factor"] / row["v_ego"]**2) / binding["factor"]
  ff = ((row["desired_lateral_accel"] - row["roll_compensation"]) / binding["factor"]
        + _friction(row["desired_lateral_accel"] - feedback, row["lateral_accel_deadzone"], binding["friction"]))
  return error, ff


def _parity(groups, binding, options):
  tolerance = options["parity_tolerance"]
  gains = [raw * .01 for raw in binding["gains_raw"]]
  for group in groups:
    pid = PIDController(*gains[:2], k_f=gains[2], pos_limit=binding["pos_limit"], neg_limit=binding["neg_limit"],
                        rate=1e9 / binding["period_ns"])
    pid.i = group[0]["integral_before"]
    for row in group:
      error, ff = _pid_inputs(row, row["actual_lateral_accel"], binding)
      if max(abs(error - row["pid_error"]), abs(ff - row["feedforward"]),
             abs((row["setpoint"] - row["measurement"]) / binding["factor"] - row["pid_error"]),
             abs(pid.i - row["integral_before"])) > tolerance:
        raise _Blocked("controller_replay_mismatch")
      output = pid.update(row["pid_error"], speed=row["v_ego"], feedforward=row["feedforward"],
                          freeze_integrator=bool(row["freeze_integrator"]))
      if max(abs(pid.p - row["p_after"]), abs(pid.i - row["i_after"]), abs(pid.f - row["f_after"]),
             abs(output - row["output_torque"])) > tolerance:
        raise _Blocked("controller_replay_mismatch")
      if abs(row["p_after"] + row["i_after"] + row["f_after"] - row["output_torque"]) > tolerance:
        raise _Blocked("pid_output_clipping")


def _input(group):
  stamps, values = [], []
  for row in group:
    stamp, value = row["source_stamps"]["sent_torque_raw"], row["sent_torque_raw"]
    if stamps and stamp == stamps[-1]:
      if value != values[-1]:
        raise _Blocked("pid_input_source_conflict")
      continue
    if stamps and stamp < stamps[-1]:
      raise _Blocked("pid_input_clock_regression")
    stamps.append(stamp)
    values.append(value)
  prefix = [0.]
  for index in range(1, len(stamps)):
    prefix.append(prefix[-1] + values[index - 1] * (stamps[index] - stamps[index - 1]))
  return stamps, values, prefix


def _average(source, start, end):
  stamps, values, prefix = source
  if start < stamps[0] or end <= start:
    raise _Blocked("pid_input_support_missing")
  def integral(stamp):
    index = bisect_right(stamps, stamp) - 1
    return prefix[index] + values[index] * (stamp - stamps[index])
  return (integral(end) - integral(start)) / (end - start)


def _observations(group, key):
  observations = []
  for row in group:
    stamp, value = row["source_stamps"][key], row[key]
    if observations and stamp == observations[-1][0]:
      if value != observations[-1][1]:
        raise _Blocked("pid_response_source_conflict")
      continue
    if observations and stamp < observations[-1][0]:
      raise _Blocked("pid_response_clock_regression")
    observations.append((stamp, value))
  return observations


def _series(group, key, delay_ns, options):
  source = _input(group)
  values = _observations(group, key)
  points = []
  for previous, current in zip(values, values[1:]):
    start, end = previous[0] - delay_ns, current[0] - delay_ns
    if start < source[0][0]:
      continue
    points.append((previous[1], _average(source, start, end), current[1], current[0] - previous[0]))
  if len(points) < options["min_response_points"]:
    raise _Blocked("insufficient_unique_response_samples")
  period = int(np.median([point[3] for point in points]))
  if period <= 0 or any(abs(point[3] - period) > options["max_clock_jitter_ns"] for point in points):
    raise _Blocked("pid_response_cadence_gap")
  return points, period


def _fit_channel(group, key, delay_ns, options):
  points, period = _series(group, key, delay_ns, options)
  matrix = np.array([[point[0], point[1]] for point in points], dtype=float)
  mean, scale = matrix.mean(axis=0), matrix.std(axis=0)
  if scale[1] < options["min_torque_std"] or min(scale) <= np.finfo(float).eps:
    raise _Blocked("insufficient_pid_excitation")
  normalized = np.column_stack(((matrix - mean) / scale, np.ones(len(points))))
  coefficient, _, rank, singular = np.linalg.lstsq(normalized, [point[2] for point in points], rcond=None)
  if rank != 3 or singular[-1] <= 0 or singular[0] / singular[-1] > options["max_condition"]:
    raise _Blocked("pid_model_rank_or_condition")
  a, b = coefficient[:2] / scale
  c = coefficient[2] - np.dot(coefficient[:2] / scale, mean)
  if not options["pole_min"] < a < options["pole_max"] or b <= 0 or not all(math.isfinite(value) for value in (a, b, c)):
    raise _Blocked("pid_model_pole_or_sign")
  prediction = points[0][0]
  errors = []
  for _, control, observed, _ in points:
    prediction = a * prediction + b * control + c
    errors.append(prediction - observed)
  rmse = math.sqrt(math.fsum(error * error for error in errors) / len(errors))
  maximum = max(abs(error) for error in errors)
  if rmse > options["model_rmse_bound"] or maximum > options["model_max_error_bound"]:
    raise _Blocked("response_model_mismatch")
  return [float(a), float(b), float(c), period], rmse, maximum


def _transition(coefficients, elapsed_ns, initial, control):
  a, b, c, period = coefficients
  pole = a ** (elapsed_ns / period)
  ratio = (1. - pole) / (1. - a)
  return pole * initial + ratio * (b * control + c)


def _initialize(group, index, key, coefficients, delay_ns):
  row = group[index]
  stamp = row["source_stamps"][key]
  elapsed = row["mono_ns"] - stamp
  if not elapsed:
    return row[key]
  control = _average(_input(group[:index + 1]), stamp - delay_ns, row["mono_ns"] - delay_ns)
  return _transition(coefficients, elapsed, row[key], control)


def _replay(group, gains_raw, model, bundle):
  options, period = bundle["options"], bundle["period_ns"]
  transport = bundle["transport"]
  warmup = max(item["delay_steps"] for item in bundle["models"]) + transport["lag_steps"] + math.ceil(options["max_signal_age_ns"] / period) + 1
  if len(group) < warmup + options["min_response_points"]:
    raise _Blocked("insufficient_pid_replay_support")
  delay = model["delay_steps"] * period
  y_values = [row["lateral_accel_kinematic"] for row in group[:warmup]]
  m_values = [row["actual_lateral_accel"] for row in group[:warmup]]
  commands = [row["output_torque"] for row in group[:warmup]]
  applied = [row["sent_torque_raw"] for row in group[:warmup]]
  integrals = [row["i_after"] for row in group[:warmup]]
  pid = PIDController(gains_raw[0] * .01, gains_raw[1] * .01, k_f=gains_raw[2] * .01,
                      pos_limit=bundle["pos_limit"], neg_limit=bundle["neg_limit"], rate=1e9 / period)
  pid.i = group[warmup]["integral_before"]
  y = _initialize(group, warmup, "lateral_accel_kinematic", model["independent"], delay)
  m = _initialize(group, warmup, "actual_lateral_accel", model["feedback"], delay)
  for index in range(warmup, len(group)):
    row = group[index]
    y_values.append(y)
    m_values.append(m)
    error, ff = _pid_inputs(row, m, bundle)
    command = float(pid.update(error, speed=row["v_ego"], feedforward=ff,
                               freeze_integrator=bool(row["freeze_integrator"])))
    commands.append(command)
    applied.append(_quantized(commands[index - transport["lag_steps"]], transport))
    integrals.append(float(pid.i))
    if index + 1 < len(group):
      elapsed = group[index + 1]["mono_ns"] - row["mono_ns"]
      delayed_index = index - model["delay_steps"]
      control = applied[delayed_index]
      y = _transition(model["independent"], elapsed, y, control)
      m = _transition(model["feedback"], elapsed, m, control)
  return {"predictions": y_values, "feedback": m_values, "commands": commands, "applied_commands": applied,
          "integrals": integrals, "warmup": warmup}


def _response_error(group, trace, key, predicted):
  first = group[trace["warmup"]]["mono_ns"]
  clocks = [row["mono_ns"] for row in group]
  errors = [float(np.interp(stamp, clocks, trace[predicted])) - value
            for stamp, value in _observations(group, key) if stamp >= first]
  if not errors:
    raise _Blocked("insufficient_unique_response_samples")
  return math.sqrt(math.fsum(error * error for error in errors) / len(errors)), max(map(abs, errors))


def _objective_error(group, trace):
  return [prediction - row["desired_lateral_accel"]
          for row, prediction in zip(group[trace["warmup"]:], trace["predictions"][trace["warmup"]:])]


def _roundoff(*values):
  return 32 * math.ulp(max(1., *(abs(value) for value in values)))


def _candidate_predictions(groups, candidate, bundle):
  options = bundle["options"]
  all_members = []
  current = bundle["gains_raw"]
  for model in bundle["models"]:
    member = []
    for group in groups:
      baseline = _replay(group, current, model, bundle)
      for key, predicted in (("lateral_accel_kinematic", "predictions"), ("actual_lateral_accel", "feedback")):
        rmse, maximum = _response_error(group, baseline, key, predicted)
        if rmse > options["model_rmse_bound"] or maximum > options["model_max_error_bound"]:
          raise _Blocked("response_model_mismatch")
      trace = baseline if candidate == current else _replay(group, candidate, model, bundle)
      start = trace["warmup"]
      controls = trace["applied_commands"][start:]
      support = bundle["command_support"]
      rounding = _roundoff(*support[:2], min(controls), max(controls))
      if min(controls) < support[0] - options["command_margin"] - rounding or max(controls) > support[1] + options["command_margin"] + rounding:
        raise _Blocked("candidate_outside_observed_actuation")
      rates = [abs(trace["applied_commands"][index] - trace["applied_commands"][index - 1]) * 1e9 /
               (group[index]["mono_ns"] - group[index - 1]["mono_ns"]) for index in range(start + 1, len(group))]
      if rates and max(rates) > support[2] + options["command_rate_margin"] + _roundoff(max(rates), support[2]):
        raise _Blocked("candidate_outside_observed_rate")
      if max(map(abs, trace["integrals"][start:])) > max(map(abs, baseline["integrals"][start:])) + options["max_integral_growth"]:
        raise _Blocked("candidate_integral_guard")
      if candidate != current:
        old_errors, new_errors = _objective_error(group, baseline), _objective_error(group, trace)
        old = math.sqrt(math.fsum(error * error for error in old_errors) / len(old_errors))
        new = math.sqrt(math.fsum(error * error for error in new_errors) / len(new_errors))
        noise = max(options["minimum_improvement"], bundle["model_error_floor"] * options["noise_multiplier"])
        if old - new <= noise or max(map(abs, new_errors)) > max(map(abs, old_errors)) + options["max_error_growth"]:
          raise _Blocked("model_uncertainty_no_candidate")
      member.extend(trace["predictions"])
    all_members.append(member)
  if any(max(column) - min(column) > options["max_prediction_disagreement"] for column in zip(*all_members)):
    raise _Blocked("model_uncertainty_no_candidate")
  return [math.fsum(column) / len(column) for column in zip(*all_members)]


def _fit(groups, context, binding, options):
  sources = [_input(group) for group in groups]
  controls = [value for _, values, _ in sources for value in values]
  rates = [abs(values[index] - values[index - 1]) * 1e9 / (stamps[index] - stamps[index - 1])
           for stamps, values, _ in sources for index in range(1, len(stamps))]
  if float(np.std(controls)) < options["min_torque_std"] or not rates:
    raise _Blocked("insufficient_pid_excitation")
  transport = _transport(groups, binding, options)
  models = []
  rejected = []
  for group in groups:
    choices = []
    for delay in options["delay_grid_steps"]:
      try:
        independent, y_rmse, y_max = _fit_channel(group, "lateral_accel_kinematic", delay * binding["period_ns"], options)
        feedback, m_rmse, m_max = _fit_channel(group, "actual_lateral_accel", delay * binding["period_ns"], options)
        choices.append({"delay_steps": delay, "independent": independent, "feedback": feedback,
                        "fit_rmse": [y_rmse, m_rmse], "fit_max_error": [y_max, m_max]})
      except _Blocked as error:
        rejected.append(str(error))
    if not choices:
      raise _Blocked(rejected[-1] if rejected else "response_model_mismatch")
    best = min(max(model["fit_rmse"]) for model in choices)
    models.extend(model for model in choices if max(model["fit_rmse"]) <= best + options["plausibility_rmse_margin"])
  if len(models) > options["max_models"]:
    raise _Blocked("pid_model_budget_exceeded")
  speeds = [row["v_ego"] for group in groups for row in group]
  return {"version": 1, "kind": KIND, "candidate_semantics": "manual_experiment", "causal_improvement_proven": False,
          "trace_schema": SCHEMA, **binding, "fit_blocks": [group[0]["block_id"] for group in groups], "options": options,
          "models": models, "transport": transport, "command_support": [min(controls), max(controls), max(rates)],
          "speed_support": [min(speeds), max(speeds)], "model_error_floor": max(max(model["fit_rmse"]) for model in models)}


def validate_fit_context(value):
  if type(value) is not dict or set(value) != _BUNDLE_KEYS or type(value["version"]) is not int or value["version"] != 1 or value["kind"] != KIND:
    raise ValueError("invalid_pid_fit_context")
  try:
    options = _options(value["options"])
    _transport_contract(value["transport_contract"])
    if type(value["transport"]) is not dict or set(value["transport"]) != {"lag_steps", "sign", "quantum", "rule", "source"}:
      raise _Blocked("invalid_pid_transport")
    _integer(value["transport"]["lag_steps"], 0, 20)
    if value["transport"]["lag_steps"] not in options["transport_lag_grid_steps"] or any(
        value["transport"][key] != expected for key, expected in value["transport_contract"].items()):
      raise _Blocked("invalid_pid_transport")
    if value["candidate_semantics"] != "manual_experiment" or value["causal_improvement_proven"] is not False or value["trace_schema"] != SCHEMA:
      raise _Blocked("invalid_pid_model_semantics")
    if value["period_ns"] != 10_000_000 or type(value["period_ns"]) is not int:
      raise _Blocked("invalid_pid_model_period")
    if type(value["binding_sha256"]) is not str or len(value["binding_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in value["binding_sha256"]):
      raise _Blocked("invalid_pid_model_binding")
    if type(value["gains_raw"]) is not list or len(value["gains_raw"]) != 3:
      raise _Blocked("invalid_pid_model_gains")
    for raw in value["gains_raw"]:
      number = _number(raw)
      if not number.is_integer() or not 0 <= number <= 200:
        raise _Blocked("invalid_pid_model_gains")
    for key in ("factor", "friction", "pos_limit", "neg_limit", "model_error_floor"):
      _number(value[key])
    if value["factor"] <= 0 or value["friction"] < 0 or not value["neg_limit"] < 0 < value["pos_limit"] or value["model_error_floor"] < 0:
      raise _Blocked("invalid_pid_model_mapping")
    if type(value["fit_blocks"]) is not list or not 1 <= len(value["fit_blocks"]) <= 64 or len(set(value["fit_blocks"])) != len(value["fit_blocks"]):
      raise _Blocked("invalid_pid_model_blocks")
    if any(type(block) is not str or not block or len(block) > 192 for block in value["fit_blocks"]):
      raise _Blocked("invalid_pid_model_blocks")
    for name, size in (("command_support", 3), ("speed_support", 2)):
      vector = value[name]
      if type(vector) is not list or len(vector) != size or any(not math.isfinite(_number(item)) for item in vector):
        raise _Blocked("invalid_pid_model_support")
      if vector[0] > vector[1]:
        raise _Blocked("invalid_pid_model_support")
    if (value["command_support"][0] >= value["command_support"][1] or value["command_support"][2] <= 0
        or value["speed_support"][0] < 5 or value["speed_support"][1] - value["speed_support"][0] > options["max_speed_span"]):
      raise _Blocked("invalid_pid_model_support")
    models = value["models"]
    if type(models) is not list or not 1 <= len(models) <= options["max_models"]:
      raise _Blocked("invalid_pid_model_count")
    for model in models:
      if type(model) is not dict or set(model) != _MODEL_KEYS or model["delay_steps"] not in options["delay_grid_steps"]:
        raise _Blocked("invalid_pid_model")
      _integer(model["delay_steps"], 0, 100)
      for key in ("independent", "feedback"):
        coefficients = model[key]
        if type(coefficients) is not list or len(coefficients) != 4:
          raise _Blocked("invalid_pid_model")
        a, b, c = [_number(item) for item in coefficients[:3]]
        _integer(coefficients[3], 1, 1_000_000_000)
        if not options["pole_min"] < a < options["pole_max"] or b <= 0:
          raise _Blocked("invalid_pid_model")
      for key, maximum in (("fit_rmse", options["model_rmse_bound"]), ("fit_max_error", options["model_max_error_bound"])):
        if type(model[key]) is not list or len(model[key]) != 2 or any(not 0 <= _number(number) <= maximum for number in model[key]):
          raise _Blocked("invalid_pid_model_error")
      if any(rmse > maximum for rmse, maximum in zip(model["fit_rmse"], model["fit_max_error"])):
        raise _Blocked("invalid_pid_model_error")
    error_floor = max(max(model["fit_rmse"]) for model in models)
    if abs(value["model_error_floor"] - error_floor) > _roundoff(value["model_error_floor"], error_floor):
      raise _Blocked("invalid_pid_model_error_floor")
    json.dumps(value, allow_nan=False)
  except (_Blocked, TypeError, OverflowError) as error:
    raise ValueError(str(error)) from None
  return deepcopy(value)


def _prepare(context, split, bundle):
  binding = _binding(context)
  if any(binding[key] != bundle[key] for key in binding):
    raise _Blocked("pid_mapping_or_epoch_changed")
  groups = _rows(context, split, bundle["options"], binding)
  if split != "fit" and any(group[0]["block_id"] in bundle["fit_blocks"] for group in groups):
    raise _Blocked("pid_split_dependency_overlap")
  speeds = [row["v_ego"] for group in groups for row in group]
  if min(speeds) < bundle["speed_support"][0] or max(speeds) > bundle["speed_support"][1]:
    raise _Blocked("pid_speed_outside_fit_support")
  _parity(groups, binding, bundle["options"])
  _transport(groups, binding, bundle["options"], bundle["transport"]["lag_steps"])
  return groups


def propose_family(key, context, profile):
  result = {"raw_estimate": None, "candidate_raw_values": [], "method_id": METHOD, "reason_codes": []}
  try:
    if key not in KEYS:
      raise _Blocked("unsupported_parameter")
    options = _options(profile.get("method_options", {}).get(key))
    binding = _binding(context)
    groups = _rows(context, "fit", options, binding)
    _parity(groups, binding, options)
    bundle = validate_fit_context(_fit(groups, context, binding, options))
    parameter = profile.get("parameter", {})
    current = _number(parameter.get("current_raw"))
    step = _integer(parameter.get("step"), 1, 200)
    minimum, maximum = _integer(parameter.get("min"), 0, 200), _integer(parameter.get("max"), 0, 200)
    if current != binding["gains_raw"][KEYS.index(key)] or minimum > maximum:
      raise _Blocked("pid_current_gain_mismatch")
    candidates = sorted({int(current + offset * step) for offset in options["candidate_offsets_steps"]
                         if minimum <= current + offset * step <= maximum})
    if len(candidates) > profile.get("max_candidates_per_key", 8):
      raise _Blocked("candidate_budget_exceeded")
    _candidate_predictions(groups, binding["gains_raw"], bundle)
    # These are search points, not fitted optima. Only outer select/holdout may accept a changed gain.
    return {**result, "candidate_raw_values": candidates, "fit_context": bundle}
  except (_Blocked, ValueError, TypeError, OverflowError, ZeroDivisionError, np.linalg.LinAlgError) as error:
    return {**result, "reason_codes": [str(error) if isinstance(error, _Blocked) else "pid_numerical_failure"]}


def evaluate_family(key, candidate_raw, context, split, budget):
  result = {"status": "blocked", "prediction_kind": "closed_loop_model", "predictions": [], "reason_codes": []}
  try:
    if key not in KEYS:
      raise _Blocked("unsupported_parameter")
    bundle = validate_fit_context(context.get("fit_context"))
    raw = _number(candidate_raw)
    if not raw.is_integer() or not 0 <= raw <= 200:
      raise _Blocked("invalid_pid_gain")
    groups = _prepare(context, split, bundle)
    gains = list(bundle["gains_raw"])
    gains[KEYS.index(key)] = raw
    predictions = _candidate_predictions(groups, gains, bundle)
    if any(not math.isfinite(value) for value in predictions):
      raise _Blocked("pid_numerical_failure")
    return {**result, "status": "evaluated", "predictions": predictions}
  except (_Blocked, ValueError, TypeError, OverflowError, ZeroDivisionError) as error:
    return {**result, "reason_codes": [str(error) if isinstance(error, _Blocked) else "pid_fit_context_invalid"]}
