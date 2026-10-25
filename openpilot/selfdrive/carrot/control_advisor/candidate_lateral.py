"""Pure lateral calibration proposals; none assert closed-loop improvement or qualification.

sent_torque uses torqued's negative carOutput.actuatorsOutput.torque convention.
Independent acceleration is already aligned and roll-compensated by the input
producer. A declared per-row nuisance offset is shared by every Factor candidate.
"""
import math
from numbers import Real

from .torque_fit import estimate_torque


MAX_ROWS = 100_000
MAX_LAG_PAIRS = 2_000_000
INDEPENDENT = {"independent_measurement", "synthetic_truth"}
TORQUE_SIGNALS = {"sent_torque": "1", "lateral_accel_independent": "m/s^2", "lateral_accel_offset": "m/s^2"}
RATIO_SIGNALS = {"steering_angle_corrected": "rad", "vm_curvature_factor": "1/m", "live_steer_ratio": "1",
                 "roll_curvature": "1/m", "curvature_independent": "1/m"}
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
    "CustomSR": item("inverse_vehicle_model_manual_ratio_v1", "independent_geometry_calibration", RATIO_SIGNALS, "1/m"),
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
  for key in ("LateralTorqueKpV", "LateralTorqueKiV", "LateralTorqueKf"):
    result[key] = item("identified_response_model_required", "unavailable", {}, "m/s^2", kind="closed_loop_model",
                       reasons=("backend_unavailable",))
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
    live = max(_signal(row, "live_steer_ratio", "1"), .1)
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
    elif key in ("SteerRatioRate", "CustomSR"):
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
      predictions = [raw * .001 * _signal(row, "sent_torque", "1") + _signal(row, "lateral_accel_offset", "m/s^2") for row in rows]
    elif key in ("SteerRatioRate", "CustomSR"):
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
    return {**result, "status": "evaluated", "predictions": predictions}
  except _Blocked as exc:
    return {**result, "reason_codes": [str(exc)]}
  except (ValueError, TypeError, OverflowError, ZeroDivisionError):
    return {**result, "reason_codes": ["numerical_failure"]}
