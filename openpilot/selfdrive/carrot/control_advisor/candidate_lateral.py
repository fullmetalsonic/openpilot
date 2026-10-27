"""Pure lateral calibration proposals; none assert closed-loop improvement or qualification.

sent_torque uses torqued's negative carOutput.actuatorsOutput.torque convention.
Independent acceleration is already aligned and roll-compensated by the input
producer. Factor uses declared callback offsets, or an explicitly fit-only frozen intercept.
"""
import math
from numbers import Real
from statistics import median
import hashlib
import json

from .torque_fit import estimate_torque
from . import candidate_pid


MAX_ROWS = 100_000
MAX_LAG_PAIRS = 2_000_000
INDEPENDENT = {"independent_measurement", "synthetic_truth"}
TORQUE_SIGNALS = {"sent_torque": "1", "lateral_accel_independent": "m/s^2", "lateral_accel_offset": "m/s^2"}
RATIO_SIGNALS = {"steering_angle_corrected": "rad", "vm_curvature_factor": "1/m", "live_steer_ratio": "1",
                 "roll_curvature": "1/m", "curvature_independent": "1/m"}
MANUAL_RATIO_SIGNALS = {**{key: unit for key, unit in RATIO_SIGNALS.items() if key != "live_steer_ratio"}, "v_ego": "m/s"}
PATH_SIGNALS = {"unshifted_path_y": "m", "reference_path_y": "m"}
LAG_SIGNALS = {"desired_lateral_accel": "m/s^2", "lateral_accel_independent": "m/s^2"}


def capabilities():
  def item(method, backend, signals, unit, *, kind="static_formula", reasons=()):
    return {"method_id": method, "backend": backend, "required_signals": dict(signals), "output_unit": unit,
            "prediction_kind": kind, "reason_codes": list(reasons)}
  result = {
    "LateralTorqueAccelFactor": item("torqued_tls_factor_calibration_v1", "independent_torque_calibration", TORQUE_SIGNALS, "m/s^2"),
    "LateralTorqueFriction": item("torqued_tls_spread_proxy_v1", "raw_proxy_only", TORQUE_SIGNALS, "1",
                                   reasons=("physical_friction_not_identified",)),
    "SteerRatioRate": item("inverse_vehicle_model_rate_v1", "independent_geometry_calibration", RATIO_SIGNALS, "1/m"),
    "CustomSR": item("inverse_vehicle_model_manual_ratio_v1", "independent_geometry_calibration", MANUAL_RATIO_SIGNALS, "1/m"),
    "PathOffset": item("external_path_reference_offset_v1", "external_geometry_calibration", PATH_SIGNALS, "m"),
    "SteerActuatorDelay": item("masked_discrete_total_path_lag_v1", "raw_delay_only", LAG_SIGNALS, "m/s^2",
                                kind="frozen_input_replay", reasons=("effective_delay_mapping_unavailable",)),
    "LatMpcInputOffset": item("lane_trajectory_replay_required", "unavailable", {}, "m", kind="frozen_input_replay",
                               reasons=("trajectory_input_required",)),
    "LatSmoothSec": item("identified_response_model_required", "unavailable", {}, "m/s^2", kind="closed_loop_model",
                           reasons=("backend_unavailable",)),
    "LateralTorqueKd": item("inactive_derivative_path", "unavailable", {}, "1", kind="closed_loop_model",
                              reasons=("derivative_path_inactive",)),
  }
  result.update(candidate_pid.capabilities())
  for key in ("LateralTorqueFriction", "SteerActuatorDelay"):
    result[key]["estimate_kind"] = "diagnostic_proxy"
  return result


class _Blocked(ValueError):
  pass


def _number(value):
  if isinstance(value, bool) or not isinstance(value, Real):
    raise _Blocked("invalid_numeric_input")
  result = float(value)
  if not math.isfinite(result):
    raise _Blocked("nonfinite_numeric_input")
  return result


def _rows(context, split):
  rows = context.get("records", {}).get(split, [])
  if not isinstance(rows, list) or not rows:
    raise _Blocked("samples_unavailable")
  if len(rows) > MAX_ROWS:
    raise _Blocked("numerical_budget_exceeded")
  if any(not isinstance(row, dict) or not isinstance(row.get("signals"), dict) for row in rows):
    raise _Blocked("invalid_sample_structure")
  return rows


def _signal(row, name, unit, *, independent=False, reference=False, user_reference=False):
  signal = row["signals"].get(name)
  if not isinstance(signal, dict) or signal.get("unit") != unit:
    raise _Blocked("required_signal_missing_or_wrong_unit")
  stamp, anchor = signal.get("source_mono_ns"), row.get("mono_ns")
  if type(stamp) is not int or type(anchor) is not int or stamp < 0 or stamp > anchor:
    raise _Blocked("signal_time_missing_or_future")
  if independent and signal.get("source") not in (INDEPENDENT | ({"user_specified"} if user_reference else set())):
    raise _Blocked("independent_response_required")
  value = _number(signal.get("value"))
  if reference and _number(row.get("reference")) != value:
    raise _Blocked("objective_reference_mismatch")
  return value


def _setting(context, key):
  value = context.get("settings_raw", {}).get(key)
  if value is None:
    raise _Blocked("consumer_mode_unobserved")
  return _number(value)


def _torque_points(context, rows, *, reference=False):
  if context.get("dependencies", {}).get("torque_ff_path") != "linear_standard":
    raise _Blocked("torque_mapping_adapter_unavailable")
  if context.get("dependencies", {}).get("torque_response_alignment") != "declared_aligned":
    raise _Blocked("response_time_alignment_required")
  if _setting(context, "LateralTorqueCustom") <= 0:
    raise _Blocked("consumer_bypassed")
  return [(_signal(row, "sent_torque", "1"),
           _signal(row, "lateral_accel_independent", "m/s^2", independent=True, reference=reference)
           - _signal(row, "lateral_accel_offset", "m/s^2")) for row in rows]


def _ratio_points(context, rows, key, *, reference=False):
  manual = _setting(context, "CustomSR") * .1
  if (key == "SteerRatioRate" and manual > 1.) or (key == "CustomSR" and manual <= 1.):
    raise _Blocked("consumer_bypassed" if key == "SteerRatioRate" else "mode_switch_required")
  points = []
  for row in rows:
    angle = _signal(row, "steering_angle_corrected", "rad")
    factor = _signal(row, "vm_curvature_factor", "1/m")
    live = max(_signal(row, "live_steer_ratio", "1"), .1) if key == "SteerRatioRate" else None
    roll = _signal(row, "roll_curvature", "1/m")
    independent = _signal(row, "curvature_independent", "1/m", independent=True, reference=reference)
    points.append((factor * angle / live if key == "SteerRatioRate" else factor * angle, independent - roll))
  return points


def _positive_inverse_slope(points):
  if len(points) < 3:
    raise _Blocked("insufficient_samples")
  xs = [x for x, _ in points]
  resolution = 64 * math.ulp(max(1., max(abs(x) for x in xs)))
  if max(xs) - min(xs) <= resolution:
    raise _Blocked("insufficient_excitation")
  denominator = math.fsum(x * x for x, _ in points)
  slope = math.fsum(x * y for x, y in points) / denominator if denominator > 0 else 0.
  if not math.isfinite(slope) or slope <= 0:
    raise _Blocked("geometry_sign_or_rank_invalid")
  return slope


def _manual_ratio_quality(context, rows):
  if context.get("dependencies", {}).get("manual_ratio_quality_v1") is not True:
    return
  # Identification gates for the manual-ratio goal, not vehicle safety limits.
  points = _ratio_points(context, rows, "CustomSR")
  steady = []
  previous = None
  for row, point in zip(rows, points):
    angle = _signal(row, "steering_angle_corrected", "rad")
    speed = _signal(row, "v_ego", "m/s")
    stamp = row["mono_ns"]
    if previous is not None and 0 < stamp - previous[0] <= 250_000_000:
      rate = abs(angle - previous[1]) / ((stamp - previous[0]) / 1e9)
      if speed >= 5 and abs(angle) >= .015 and rate <= .05:
        steady.append((point[0], point[1], speed))
    previous = (stamp, angle)
  if len(steady) < 12:
    raise _Blocked("manual_ratio_steady_curve_insufficient")
  speed_mid = (min(value[2] for value in steady) + max(value[2] for value in steady)) / 2
  groups = [[(x, y) for x, y, speed in steady if predicate(x, speed)] for predicate in (
    lambda x, speed: x < 0, lambda x, speed: x > 0,
    lambda x, speed: speed <= speed_mid, lambda x, speed: speed > speed_mid)]
  if any(len(group) < 3 for group in groups):
    raise _Blocked("manual_ratio_direction_or_speed_coverage_insufficient")
  slopes = [_positive_inverse_slope(group) for group in groups]
  if max(slopes) / min(slopes) > 1.2:
    raise _Blocked("manual_ratio_direction_or_speed_disagreement")
  xy = [(x, y) for x, y, _ in steady]
  slope = _positive_inverse_slope(xy)
  if abs(math.fsum(y - slope * x for x, y in xy) / len(xy)) > .0005:
    raise _Blocked("manual_ratio_zero_offset_disagreement")


def _ratio_binding(context):
  value = {name: context.get(name) for name in ("settings_raw", "dependencies", "vehicle_context", "evidence", "objective", "inference_config")}
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _robust_origin(points):
  usable = [(x, y) for x, y in points if abs(x) > 64 * math.ulp(1.)]
  if len(usable) < 3:
    raise _Blocked("ratio_uncertainty_unavailable")
  slope = median(y / x for x, y in usable)
  for _ in range(12):
    residual = [y - slope * x for x, y in usable]
    centre = median(residual)
    scale = max(1.4826 * median(abs(value - centre) for value in residual), 128 * math.ulp(max(1., *(abs(y) for _, y in usable))))
    weights = [min(1., 1.345 * scale / abs(value - centre)) if value != centre else 1. for value in residual]
    denominator = math.fsum(weight * x * x for weight, (x, y) in zip(weights, usable))
    updated = math.fsum(weight * x * y for weight, (x, y) in zip(weights, usable)) / denominator
    if abs(updated - slope) < 1e-12:
      slope = updated
      break
    slope = updated
  if not math.isfinite(slope) or slope <= 0:
    raise _Blocked("geometry_sign_or_rank_invalid")
  return slope


def _fit_manual_ratio(context, rows):
  import numpy as np
  config = candidate_pid.EVENT_INFERENCE_CONFIG
  groups = {}
  for row, point in zip(rows, _ratio_points(context, rows, "CustomSR")):
    groups.setdefault(row["block_id"], []).append((row, point))
  if not config["min_events_per_split"] <= len(groups) <= config["max_events_per_split"]:
    raise _Blocked("independent_events_insufficient")
  summaries = []
  for event_id, group in groups.items():
    slope = _robust_origin([point for row, point in group])
    summaries.append({"event_id": event_id, "slope": slope,
      "direction": 1 if median(point[0] for row, point in group) > 0 else -1,
      "speed": median(_signal(row, "v_ego", "m/s") for row, point in group)})
  rng = np.random.default_rng(config["seed"])
  def agree(first, second):
    if min(len(first), len(second)) < 2:
      raise _Blocked("ratio_uncertainty_unavailable")
    delta = (np.asarray(first)[rng.integers(0, len(first), size=(config["bootstrap_repetitions"], len(first)))].mean(axis=1)
             - np.asarray(second)[rng.integers(0, len(second), size=(config["bootstrap_repetitions"], len(second)))].mean(axis=1))
    low, high = np.quantile(delta, [.025, .975])
    resolution = 128 * math.ulp(max(1., *first, *second))
    if low > resolution or high < -resolution:
      raise _Blocked("ratio_direction_or_speed_disagreement")
  agree([item["slope"] for item in summaries if item["direction"] < 0], [item["slope"] for item in summaries if item["direction"] > 0])
  speeds = [item["speed"] for item in summaries]
  if max(speeds) - min(speeds) > .5:
    mid = (max(speeds) + min(speeds)) / 2
    agree([item["slope"] for item in summaries if item["speed"] <= mid], [item["slope"] for item in summaries if item["speed"] > mid])
  interval = candidate_pid.event_bootstrap([item["slope"] for item in summaries])
  slope = median(item["slope"] for item in summaries)
  event_offsets = [median(point[1] - slope * point[0] for row, point in group) for group in groups.values()]
  offset = candidate_pid.event_bootstrap(event_offsets)
  resolution = 128 * math.ulp(max(1., *(abs(point[1]) for group in groups.values() for row, point in group)))
  if offset["lower"] > resolution or offset["upper"] < -resolution:
    raise _Blocked("ratio_zero_offset_uncertain")
  if interval["lower"] <= 0:
    raise _Blocked("ratio_uncertainty_unavailable")
  angles = [_signal(row, "steering_angle_corrected", "rad") for row in rows]
  speeds = [_signal(row, "v_ego", "m/s") for row in rows]
  return {"version": 2, "kind": "manual_ratio_robust_v2", "binding_sha256": _ratio_binding(context),
    "configuration": dict(config), "slope": slope, "raw_interval_95": [10 / interval["upper"], 10 / interval["lower"]],
    "bootstrap": interval, "zero_offset_interval": offset, "fit_event_ids": list(groups),
    "angle_support": [min(angles), max(angles)], "speed_support": [min(speeds), max(speeds)],
    "interpretation": "effective_ratio_conditional_on_recorded_stiffness"}


def validate_manual_ratio_context(value):
  names = {"version", "kind", "binding_sha256", "configuration", "slope", "raw_interval_95", "bootstrap", "zero_offset_interval",
           "fit_event_ids", "angle_support", "speed_support", "interpretation"}
  if (type(value) is not dict or set(value) != names or value["version"] != 2 or value["kind"] != "manual_ratio_robust_v2"
      or value["configuration"] != candidate_pid.EVENT_INFERENCE_CONFIG or type(value["binding_sha256"]) is not str
      or len(value["binding_sha256"]) != 64 or any(char not in "abcdef0123456789" for char in value["binding_sha256"])
      or value["interpretation"] != "effective_ratio_conditional_on_recorded_stiffness"):
    raise ValueError("invalid_manual_ratio_fit_context")
  if (_number(value["slope"]) <= 0 or type(value["fit_event_ids"]) is not list or not 4 <= len(value["fit_event_ids"]) <= 64
      or any(type(identifier) is not str or not 1 <= len(identifier) <= 96 for identifier in value["fit_event_ids"])
      or len(set(value["fit_event_ids"])) != len(value["fit_event_ids"])):
    raise ValueError("invalid_manual_ratio_fit_context")
  for name in ("raw_interval_95", "angle_support", "speed_support"):
    if type(value[name]) is not list or len(value[name]) != 2 or _number(value[name][0]) > _number(value[name][1]):
      raise ValueError("invalid_manual_ratio_fit_context")
  for name in ("bootstrap", "zero_offset_interval"):
    if type(value[name]) is not dict or set(value[name]) != {"mean", "lower", "upper", "convergence_error", "event_count"}:
      raise ValueError("invalid_manual_ratio_fit_context")
    if _number(value[name]["lower"]) > _number(value[name]["upper"]) or _number(value[name]["convergence_error"]) < 0 or value[name]["event_count"] != len(value["fit_event_ids"]):
      raise ValueError("invalid_manual_ratio_fit_context")
    _number(value[name]["mean"])
  return json.loads(json.dumps(value, allow_nan=False))


def _path_pairs(context, rows, *, reference=False):
  if context.get("dependencies", {}).get("path_offset_selected") is not True:
    raise _Blocked("consumer_bypassed_or_unobserved")
  return [(_signal(row, "unshifted_path_y", "m"),
           _signal(row, "reference_path_y", "m", independent=True, user_reference=True, reference=reference)) for row in rows]


def _nearby_candidates(raw, key, profile):
  parameter = profile.get("parameter", {})
  required = ("current_raw", "min", "max", "step")
  if any(name not in parameter for name in required):
    return [], ["search_profile_missing"]
  current, minimum, maximum, step = (_number(parameter[name]) for name in required)
  if step <= 0 or minimum > maximum:
    raise _Blocked("invalid_search_profile")
  lower = math.floor((raw - current) / step)
  candidates = sorted({current + index * step for index in (lower, lower + 1)})
  result = [int(value) for value in candidates if minimum <= value <= maximum and value.is_integer()
            and (key != "CustomSR" or value * .1 > 1.) and (key != "SteerRatioRate" or 30 <= value <= 200)]
  return result, [] if result else ["estimate_outside_candidate_grid"]


def _raw_lag(context, rows, profile):
  options = profile.get("method_options", {}).get("SteerActuatorDelay", {})
  grid = options.get("lag_grid_seconds")
  if not isinstance(grid, list) or not 1 <= len(grid) <= 256:
    raise _Blocked("lag_grid_required")
  if "sample_period_seconds" not in options:
    raise _Blocked("sample_period_required")
  period = _number(options["sample_period_seconds"])
  if period <= 0:
    raise _Blocked("sample_period_required")
  period_ns = round(period * 1e9)
  lags = sorted({round(_number(value) * 1e9) for value in grid})
  if period_ns <= 0 or any(lag < 0 or lag % period_ns for lag in lags):
    raise _Blocked("lag_grid_not_on_declared_timebase")
  if len(rows) * len(lags) > MAX_LAG_PAIRS:
    raise _Blocked("numerical_budget_exceeded")
  groups, order = {}, []
  for row in rows:
    block, route, stamp = row.get("block_id"), row.get("route_id"), row.get("mono_ns")
    if not isinstance(block, str) or not isinstance(route, str) or type(stamp) is not int or stamp < 0:
      raise _Blocked("sample_time_identity_required")
    previous = groups.get((block, route))
    if previous is not None and stamp <= previous[0]:
      raise _Blocked("sample_times_not_strictly_ordered")
    run = (previous[1] + int(stamp - previous[0] != period_ns)) if previous is not None else 0
    groups[(block, route)] = (stamp, run)
    key = (block, route, run, stamp)
    order.append((key, _signal(row, "desired_lateral_accel", "m/s^2"),
                  _signal(row, "lateral_accel_independent", "m/s^2", independent=True)))
  signals = {key: (desired, actual) for key, desired, actual in order}
  common = [(key, actual) for key, _, actual in order if all((*key[:3], key[3] - lag) in signals for lag in lags)]
  if len(common) < 3:
    raise _Blocked("insufficient_lag_overlap")
  scores = []
  ys = [actual for _, actual in common]
  ymean = math.fsum(ys) / len(ys)
  yenergy = math.fsum((value - ymean) ** 2 for value in ys)
  for lag in lags:
    xs = [signals[(*key[:3], key[3] - lag)][0] for key, _ in common]
    xmean = math.fsum(xs) / len(xs)
    energy = math.fsum((value - xmean) ** 2 for value in xs) * yenergy
    if energy <= 0 or not math.isfinite(energy):
      raise _Blocked("insufficient_lag_excitation")
    score = math.fsum((x - xmean) * (y - ymean) for x, y in zip(xs, ys)) / math.sqrt(energy)
    scores.append((score, lag))
  ranked = sorted(scores, reverse=True)
  if ranked[0][0] <= 0 or (len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) <= 64 * math.ulp(1.)):
    raise _Blocked("ambiguous_lag")
  return ranked[0][1] / 1e9 / .01


def propose_family(key, context, profile):
  if key in candidate_pid.KEYS:
    return candidate_pid.propose_family(key, context, profile)
  capability = capabilities().get(key)
  result = {"raw_estimate": None, "candidate_raw_values": [],
            "method_id": capability["method_id"] if capability else "unsupported", "reason_codes": []}
  if capability is None:
    return {**result, "reason_codes": ["unsupported_parameter"]}
  try:
    if capability["backend"] == "unavailable":
      raise _Blocked(capability["reason_codes"][0])
    rows = _rows(context, "fit")
    if key in ("LateralTorqueAccelFactor", "LateralTorqueFriction"):
      points = _torque_points(context, rows)
      options = profile.get("method_options", {}).get(key, {})
      if type(options) is not dict or "offset_policy" in options and options["offset_policy"] != "fit_intercept":
        raise _Blocked("torque_offset_policy_unsupported")
      fit_intercept = key == "LateralTorqueAccelFactor" and options.get("offset_policy") == "fit_intercept"
      if fit_intercept and any(_signal(row, "lateral_accel_offset", "m/s^2") != 0 for row in rows):
        raise _Blocked("torque_callback_zero_offset_required")
      fit = estimate_torque(points, max_input_points=MAX_ROWS, max_fit_points=MAX_ROWS, seed=0)
      if fit["status"] != "available":
        raise _Blocked(fit["reason"] or "raw_fit_unavailable")
      raw = fit["raw_fit"]["latAccelFactor" if key == "LateralTorqueAccelFactor" else "frictionCoefficient"] / .001
      if not math.isfinite(raw):
        raise _Blocked("numerical_failure")
      result["raw_estimate"] = raw
      if key == "LateralTorqueFriction":
        return {**result, "reason_codes": ["physical_friction_not_identified"]}
      if raw <= 0:
        raise _Blocked("torque_sign_or_rank_invalid")
      if not min(x for x, _ in points) < 0 < max(x for x, _ in points):
        raise _Blocked("insufficient_direction_coverage")
      if fit_intercept:
        result["fit_context"] = {"lateral_accel_offset": _number(fit["raw_fit"]["latAccelOffset"])}
    elif key in ("SteerRatioRate", "CustomSR"):
      if key == "CustomSR" and context.get("inference_config") is not None:
        fitted = validate_manual_ratio_context(_fit_manual_ratio(context, rows))
        result["fit_context"] = fitted
        slope = fitted["slope"]
      else:
        if key == "CustomSR":
          _manual_ratio_quality(context, rows)
        slope = _positive_inverse_slope(_ratio_points(context, rows, key))
      raw = (100. if key == "SteerRatioRate" else 10.) / slope
    elif key == "PathOffset":
      pairs = _path_pairs(context, rows)
      raw = math.fsum(target - base for base, target in pairs) / len(pairs) / .01
    elif key == "SteerActuatorDelay":
      result["raw_estimate"] = _raw_lag(context, rows, profile)
      return {**result, "reason_codes": ["effective_delay_mapping_unavailable"]}
    else:
      raise _Blocked("backend_unavailable")
    if not math.isfinite(raw):
      raise _Blocked("numerical_failure")
    result["raw_estimate"] = raw
    result["candidate_raw_values"], result["reason_codes"] = _nearby_candidates(raw, key, profile)
  except _Blocked as exc:
    result["reason_codes"] = [str(exc)]
  except (ValueError, TypeError, OverflowError, ZeroDivisionError):
    result["reason_codes"] = ["numerical_failure"]
  return result


def evaluate_family(key, candidate_raw, context, split, budget):
  if key in candidate_pid.KEYS:
    return candidate_pid.evaluate_family(key, candidate_raw, context, split, budget)
  capability = capabilities().get(key)
  result = {"status": "blocked", "prediction_kind": capability["prediction_kind"] if capability else "static_formula",
            "predictions": [], "reason_codes": []}
  if capability is None:
    return {**result, "status": "unsupported", "reason_codes": ["unsupported_parameter"]}
  try:
    if capability["backend"] in ("unavailable", "raw_delay_only", "raw_proxy_only"):
      raise _Blocked(capability["reason_codes"][0])
    raw, rows = _number(candidate_raw), _rows(context, split)
    if key == "LateralTorqueAccelFactor":
      _torque_points(context, rows, reference=True)
      if raw <= 0:
        raise _Blocked("torque_sign_or_rank_invalid")
      fixed = context.get("fit_context")
      if fixed is not None:
        if type(fixed) is not dict or set(fixed) != {"lateral_accel_offset"}:
          raise _Blocked("invalid_fit_context")
        offset = _number(fixed["lateral_accel_offset"])
        if any(_signal(row, "lateral_accel_offset", "m/s^2") != 0 for row in rows):
          raise _Blocked("torque_callback_zero_offset_required")
        predictions = [raw * .001 * _signal(row, "sent_torque", "1") + offset for row in rows]
      else:
        predictions = [raw * .001 * _signal(row, "sent_torque", "1") + _signal(row, "lateral_accel_offset", "m/s^2") for row in rows]
    elif key in ("SteerRatioRate", "CustomSR"):
      if key == "CustomSR" and context.get("inference_config") is not None:
        fitted = validate_manual_ratio_context(context.get("fit_context"))
        if fitted["binding_sha256"] != _ratio_binding(context):
          raise _Blocked("manual_ratio_mapping_or_epoch_changed")
        if split != "fit" and set(row["block_id"] for row in rows) & set(fitted["fit_event_ids"]):
          raise _Blocked("event_split_dependency_overlap")
        for row in rows:
          for signal_name, unit, support in (("v_ego", "m/s", "speed_support"), ("steering_angle_corrected", "rad", "angle_support")):
            value = _signal(row, signal_name, unit)
            if value < fitted[support][0] - 64 * math.ulp(max(1., abs(value))) or value > fitted[support][1] + 64 * math.ulp(max(1., abs(value))):
              raise _Blocked("ratio_outside_fit_support")
      elif key == "CustomSR":
        _manual_ratio_quality(context, rows)
      _ratio_points(context, rows, key, reference=True)
      if key == "CustomSR" and raw * .1 <= 1.:
        raise _Blocked("mode_switch_required")
      ratio_scale = raw * .01 if 30 <= raw <= 200 else 1.
      predictions = []
      for row in rows:
        denominator = max(max(_signal(row, "live_steer_ratio", "1"), .1) * ratio_scale, .1) if key == "SteerRatioRate" else raw * .1
        predictions.append(_signal(row, "vm_curvature_factor", "1/m") * _signal(row, "steering_angle_corrected", "rad") / denominator
                           + _signal(row, "roll_curvature", "1/m"))
    elif key == "PathOffset":
      predictions = [base + raw * .01 for base, _ in _path_pairs(context, rows, reference=True)]
    else:
      raise _Blocked("backend_unavailable")
    if any(not math.isfinite(value) for value in predictions):
      raise _Blocked("numerical_failure")
    details = {}
    if context.get("inference_config") is not None:
      for row, prediction in zip(rows, predictions):
        details.setdefault(row["block_id"], []).append((row, prediction))
      details = [candidate_pid.event_metrics(event_id,
        [predicted - row["reference"] for row, predicted in event], [row["reference"] for row, predicted in event],
        [_signal(row, "v_ego", "m/s") for row, predicted in event]) for event_id, event in details.items()]
    return {**result, "status": "evaluated", "predictions": predictions,
            **({"event_metrics": details} if context.get("inference_config") is not None else {})}
  except _Blocked as exc:
    return {**result, "reason_codes": [str(exc)]}
  except (ValueError, TypeError, OverflowError, ZeroDivisionError):
    return {**result, "reason_codes": ["numerical_failure"]}
