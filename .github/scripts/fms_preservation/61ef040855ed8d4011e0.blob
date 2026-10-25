"""Offline longitudinal policy mappings, never a vehicle or MPC simulation.

Each candidate owns its replay state. References must come from the caller's
independent objective; recorded ego responses are inputs, never desired policy.
"""
from __future__ import annotations

import math

from openpilot.selfdrive.carrot.t_follow import (
  get_speed_t_follow_factor, get_t_follow_mode_max, get_lead_response_for_gap,
  ramp_mode_t_follow_factor, ramp_t_follow,
)
from openpilot.selfdrive.controls.lib.longitudinal_preview import LeadAccelResponseState, get_lead_accel_mpc_request


CRUISE_KEYS = tuple(f"CruiseMaxVals{i}" for i in range(7))
GAP_KEYS = tuple(f"TFollowGap{i}" for i in range(1, 5))
RESPONSE_KEYS = ("LeadAccelResponse",) + tuple(f"LeadAccelResponseTF{i}" for i in range(1, 5))
TF_KEYS = GAP_KEYS + ("SpeedTFFactor", "TFollowDecelBoost")
UNAVAILABLE_KEYS = ("LongTuningKpV", "LongTuningKiV", "LongTuningKf", "LongActuatorDelay", "VEgoStopping")
METHOD_PREFIX = "fms_longitudinal_policy_v1"

_TF_SIGNALS = {"v_ego": "m/s", "a_ego": "m/s^2", "personality": "enum", "tf_mode_factor_target": "1",
               "tf_initial_mode_factor": "1", "tf_initial_decel_base": "s", "tf_initial_decel_extra": "s",
               "tf_initial_applied": "s", "planner_dt": "s"}
_RESPONSE_SIGNALS = {"personality": "enum", "driving_mode": "enum", "response_enabled": "bool", "source": "enum",
                     "lead_status": "bool", "a_lead": "m/s^2", "a_ego": "m/s^2", "v_rel": "m/s",
                     "gap_margin": "m", "speed_error": "m/s", "radar_track_id": "id", "planner_dt": "s",
                     "response_initial_strength": "1", "response_initial_level": "enum", "response_initial_track_id": "id"}
_LC_SIGNALS = {"v_ego": "m/s", "max_accel": "m/s^2", "t_follow": "s", "stop_distance": "m", "horizon": "s",
               "lc_active": "bool", "lc_confidence": "1", "lc_clearance": "s", "lc_target_count": "enum"}
for _prefix in ("primary", "target"):
  _LC_SIGNALS.update({f"{_prefix}_track_id": "id", f"{_prefix}_distance": "m", f"{_prefix}_y_rel": "m",
                      f"{_prefix}_v_rel": "m/s", f"{_prefix}_speed": "m/s", f"{_prefix}_accel": "m/s^2"})


def capabilities():
  rows = {}
  def add(keys, method, unit, signals, prediction="static_formula", reasons=()):
    for key in keys:
      rows[key] = {"method_id": f"{METHOD_PREFIX}.{method}", "backend": "source_policy_mapping",
                   "required_signals": dict(signals), "output_unit": unit, "prediction_kind": prediction,
                   "reason_codes": list(reasons)}
  add(CRUISE_KEYS, "accel_curve", "m/s^2", {"v_ego": "m/s", "accel_mode_factor": "1"})
  add(TF_KEYS, "following_time", "s", _TF_SIGNALS, "frozen_input_replay")
  add(RESPONSE_KEYS, "response_a_change_factor", "1", _RESPONSE_SIGNALS, "frozen_input_replay")
  add(("DynamicTFollowLC",), "lane_change_credit", "m", _LC_SIGNALS, "frozen_input_replay")
  add(("StopDistanceCarrot",), "base_desired_follow_distance", "m",
      {"v_ego": "m/s", "lead_speed": "m/s", "t_follow": "s", "comfort_brake": "m/s^2"})
  add(("TrafficStopDistanceAdjust",), "effective_traffic_offset", "m", {"v_ego": "m/s", "model_lead_offset": "m"})
  add(("StoppingAccel",), "ordinary_stopping_command", "m/s^2",
      {"active": "bool", "soft_hold": "bool", "long_control_state": "enum", "should_stop": "bool",
       "previous_output_accel": "m/s^2", "stopping_decel_rate": "m/s^3", "accel_min": "m/s^2", "accel_max": "m/s^2"},
      "frozen_input_replay")
  for key in UNAVAILABLE_KEYS:
    rows[key] = {"method_id": f"{METHOD_PREFIX}.unavailable", "backend": "unavailable", "required_signals": {},
                 "output_unit": "m/s^2" if key != "VEgoStopping" else "bool", "prediction_kind": "frozen_input_replay",
                 "reason_codes": ["backend_unavailable", "independent_plant_required" if key.startswith("LongTuning") else "trajectory_replay_required"]}
  return rows


class _Blocked(ValueError):
  pass


def _number(value):
  if type(value) not in (float, int) or not math.isfinite(value):
    raise _Blocked("invalid_numeric_input")
  return float(value)


def _signals(row, expected):
  result = {}
  for name, unit in expected.items():
    signal = row.get("signals", {}).get(name)
    if not isinstance(signal, dict) or signal.get("unit") != unit:
      raise _Blocked("required_signal_missing")
    value = signal.get("value")
    if unit == "bool":
      if type(value) not in (int, float) or value not in (0, 1):
        raise _Blocked("invalid_boolean_input")
      value = bool(value)
    elif unit in ("enum", "id"):
      number = _number(value)
      if not number.is_integer():
        raise _Blocked("invalid_selector_input")
      value = int(number)
    else:
      value = _number(value)
    result[name] = value
  return result


def _setting(settings, key):
  if key not in settings:
    raise _Blocked("dependency_missing")
  return _number(settings[key])


def _objective(context, capability):
  objective = context.get("objective", {})
  if objective.get("kind") not in ("policy_reference", "geometry_reference") or objective.get("source") not in ("user_specified", "synthetic_truth"):
    raise _Blocked("independent_policy_objective_required")
  if objective.get("unit") != capability["output_unit"]:
    raise _Blocked("objective_unit_mismatch")


def _interp(x, xs, ys):
  # Same endpoint-clamped piecewise linear interpolation used by np.interp.
  if x <= xs[0]:
    return float(ys[0])
  for index in range(1, len(xs)):
    if x <= xs[index]:
      weight = (x - xs[index - 1]) / (xs[index] - xs[index - 1])
      return float(ys[index - 1] + weight * (ys[index] - ys[index - 1]))
  return float(ys[-1])


def _personality(value):
  if type(value) is not int or not 0 <= value <= 3:
    raise _Blocked("personality_unknown")
  return value


def _tf_step(settings, signals, state):
  if signals["planner_dt"] != 0.05:
    raise _Blocked("planner_cadence_unsupported")
  gap = _personality(signals["personality"])
  base = _setting(settings, GAP_KEYS[gap]) * 0.01
  target = base * get_speed_t_follow_factor(int(_setting(settings, "SpeedTFFactor")), signals["v_ego"] * 3.6)
  mode = ramp_mode_t_follow_factor(signals["tf_mode_factor_target"], state[0], 0.05)
  target *= mode
  decel_base = max(target, state[1]) if signals["a_ego"] <= -0.2 else target
  requested = _interp(signals["a_ego"], (-2.5, -1.0, -0.3, 0.), (.50, .25, .06, 0.)) * _setting(settings, "TFollowDecelBoost") * .01
  extra = max(requested, state[2] - .10 * .05)
  maximum = get_t_follow_mode_max(max(target, decel_base), 1., extra)
  final = max(.3, min(max(.3, maximum), decel_base + extra))
  applied = ramp_t_follow(final, state[3], extra, .05)
  return applied, [mode, decel_base, extra, applied]


def _response_step(settings, signals, state):
  gap = _personality(signals["personality"])
  mode = signals["driving_mode"]
  if type(mode) is not int or mode not in (1, 2, 3, 4):
    raise _Blocked("driving_mode_unknown")
  common = int(_setting(settings, "LeadAccelResponse"))
  overrides = [int(_setting(settings, key)) for key in RESPONSE_KEYS[1:]]
  resolved = min(get_lead_response_for_gap(common, overrides, gap), 2 if mode == 1 else 3 if mode == 2 else 5)
  if signals["source"] not in (0, 1, 2, 3):
    raise _Blocked("planner_source_unknown")
  if type(signals["radar_track_id"]) is not int or signals["radar_track_id"] < -1:
    raise _Blocked("lead_identity_unknown")
  dt = signals["planner_dt"]
  if not 0. < dt <= .2:
    raise _Blocked("planner_cadence_unsupported")
  request = get_lead_accel_mpc_request(resolved, enabled=signals["response_enabled"], source=("lead0", "lead1", "cruise", "e2e")[signals["source"]],
    lead_status=signals["lead_status"], a_lead=signals["a_lead"], a_ego=signals["a_ego"], v_rel=signals["v_rel"],
    gap_margin=signals["gap_margin"], speed_error=signals["speed_error"])
  result = state.update(request, dt, signals["radar_track_id"])
  return result.a_change_cost_factor


def _lane_credit(raw, signals):
  # The source function checks every transition time, even for one output horizon.
  import numpy as np
  from openpilot.selfdrive.carrot.radar_motion.lane_change_gap import GapLead, LaneChangeGapPlan
  if signals["lc_target_count"] != 1:
    raise _Blocked("multi_target_replay_state_unavailable")
  def lead(prefix):
    identity = signals[f"{prefix}_track_id"]
    if type(identity) is not int or identity < 0:
      raise _Blocked("lead_identity_unknown")
    return GapLead(identity, signals[f"{prefix}_distance"], signals[f"{prefix}_y_rel"], signals[f"{prefix}_v_rel"],
                   signals[f"{prefix}_speed"], signals[f"{prefix}_accel"])
  primary, target = lead("primary"), lead("target")
  if primary.radarTrackId == target.radarTrackId:
    raise _Blocked("lane_change_target_not_independent")
  if not 0. <= signals["horizon"] <= 10. or not 0. <= signals["lc_confidence"] <= 1.:
    raise _Blocked("lane_change_state_invalid")
  plan = LaneChangeGapPlan(active=signals["lc_active"], primary_id=primary.radarTrackId, targets=(target,),
                           clearance_s=signals["lc_clearance"], confidence=signals["lc_confidence"])
  return float(plan.credit(primary, np.array([signals["horizon"]]), signals["v_ego"], signals["max_accel"],
                           signals["t_follow"], signals["stop_distance"], raw * .01)[0])


def _predict(key, raw, context, split):
  capability = capabilities()[key]
  settings = dict(context.get("dependencies", {}))
  settings.update(context.get("settings_raw", {}))
  settings[key] = raw
  rows = context.get("records", {}).get(split, [])
  if not rows:
    raise _Blocked("evaluation_records_missing")
  predictions, seen_blocks = [], set()
  previous, state = None, None
  for row in rows:
    signals = _signals(row, capability["required_signals"])
    block = (row.get("route_id"), row.get("block_id"))
    timestamp = row.get("mono_ns")
    if not all(isinstance(value, str) and value for value in block) or type(timestamp) is not int or timestamp < 0:
      raise _Blocked("block_identity_missing")
    new_block = previous is None or previous[0] != block
    if new_block:
      if block in seen_blocks:
        raise _Blocked("block_not_contiguous")
      seen_blocks.add(block)
      if key in TF_KEYS:
        state = [signals[name] for name in ("tf_initial_mode_factor", "tf_initial_decel_base", "tf_initial_decel_extra", "tf_initial_applied")]
        if not 0. < state[0] <= 3. or min(state[1:]) < 0.:
          raise _Blocked("initial_replay_state_invalid")
      elif key in RESPONSE_KEYS:
        strength, level, identity = signals["response_initial_strength"], signals["response_initial_level"], signals["response_initial_track_id"]
        if not 0. <= strength <= 1. or type(level) is not int or not 0 <= level <= 5 or type(identity) is not int or identity < -1:
          raise _Blocked("initial_replay_state_invalid")
        state = LeadAccelResponseState()
        state.strength, state.key = strength, (level, identity) if strength > 0. else None
    elif timestamp <= previous[1]:
      raise _Blocked("sample_time_not_monotonic")
    if not new_block and key in TF_KEYS + RESPONSE_KEYS:
      if abs((timestamp - previous[1]) * 1e-9 - signals["planner_dt"]) > 1e-6:
        raise _Blocked("replay_signal_gap")
    if key in CRUISE_KEYS:
      factor = signals["accel_mode_factor"]
      if not 0. < factor <= 1.2:
        raise _Blocked("driving_mode_factor_invalid")
      result = _interp(signals["v_ego"] * 3.6, (0., 10., 40., 60., 80., 110., 140.),
                       [_setting(settings, name) * .01 for name in CRUISE_KEYS]) * factor
    elif key in TF_KEYS:
      result, state = _tf_step(settings, signals, state)
    elif key in RESPONSE_KEYS:
      result = _response_step(settings, signals, state)
    elif key == "DynamicTFollowLC":
      result = _lane_credit(raw, signals)
    elif key == "StopDistanceCarrot":
      v, lead, comfort = signals["v_ego"], signals["lead_speed"], signals["comfort_brake"]
      if min(v, lead, signals["t_follow"]) < 0. or comfort <= 0.:
        raise _Blocked("following_state_invalid")
      result = v * v / (2. * comfort) + signals["t_follow"] * v + raw * .01 - lead * lead / (2. * 2.5)
    elif key == "TrafficStopDistanceAdjust":
      from openpilot.selfdrive.carrot.traffic_stop import get_traffic_stop_distance_adjust
      result = get_traffic_stop_distance_adjust(raw * .01, signals["v_ego"], signals["model_lead_offset"])
    elif key == "StoppingAccel":
      if not signals["active"] or signals["soft_hold"] or signals["long_control_state"] != 2 or not signals["should_stop"]:
        raise _Blocked("ordinary_stopping_path_required")
      output = signals["previous_output_accel"]
      if signals["stopping_decel_rate"] < 0. or signals["accel_min"] > signals["accel_max"]:
        raise _Blocked("stopping_limits_invalid")
      if output > max(-1., min(-.5, raw * .01)):
        output = min(output, 0.) - signals["stopping_decel_rate"] * .01
      result = max(signals["accel_min"], min(signals["accel_max"], output))
    else:
      raise _Blocked("backend_unavailable")
    predictions.append(_number(result))
    previous = (block, timestamp)
  return predictions


def evaluate_family(key, candidate_raw, context, split, budget):
  capability = capabilities().get(key)
  if capability is None:
    return {"status": "unsupported", "prediction_kind": "static_formula", "predictions": [], "reason_codes": ["key_unsupported"]}
  result = {"status": "blocked", "prediction_kind": capability["prediction_kind"], "predictions": [],
            "reason_codes": list(capability["reason_codes"])}
  if capability["backend"] == "unavailable":
    return result
  try:
    _objective(context, capability)
    if type(candidate_raw) is not int or split not in ("fit", "select", "holdout"):
      raise _Blocked("candidate_or_split_invalid")
    rows = context.get("records", {}).get(split, [])
    if type(budget) is not dict or type(budget.get("max_rows")) is not int or len(rows) > budget["max_rows"] or budget.get("remaining_evaluations", -1) < 0:
      raise _Blocked("evaluation_budget_exhausted")
    result["predictions"] = _predict(key, candidate_raw, context, split)
    result.update(status="evaluated", reason_codes=[])
  except _Blocked as exc:
    result["reason_codes"] = [str(exc)]
  except ImportError:
    result["reason_codes"] = ["numeric_backend_unavailable"]
  return result


def propose_family(key, context, profile):
  capability = capabilities().get(key)
  result = {"raw_estimate": None, "candidate_raw_values": [],
            "method_id": capability["method_id"] if capability else f"{METHOD_PREFIX}.unsupported",
            "reason_codes": list(capability["reason_codes"]) if capability else ["key_unsupported"]}
  if capability is None or capability["backend"] == "unavailable":
    return result
  try:
    _objective(context, capability)
    parameter = profile.get("parameter", {})
    current = context.get("settings_raw", {}).get(key)
    if type(current) is not int or parameter.get("current_raw") != current:
      raise _Blocked("current_raw_unknown")
    step = _number(parameter.get("step"))
    lower, upper = _number(parameter.get("min")), _number(parameter.get("max"))
    if not step.is_integer() or step <= 0 or not lower <= current <= upper:
      raise _Blocked("parameter_grid_invalid")
    step = int(step)
    if key in RESPONSE_KEYS[1:] and current == -1:
      raise _Blocked("inherited_profile_requires_explicit_selection")
    if key in UNAVAILABLE_KEYS and current == 0:
      raise _Blocked("special_mode_requires_explicit_selection")
    fit = context.get("records", {}).get("fit", [])
    if not fit or len(fit) > profile.get("max_rows", 0):
      raise _Blocked("proposal_fit_budget_exhausted")
    if any(context.get("records", {}).get(split) for split in ("select", "holdout")):
      raise _Blocked("proposal_received_nonfit_data")
    if key in RESPONSE_KEYS:
      candidates = [value for value in range(6) if lower <= value <= upper]
      scores = []
      for value in candidates:
        predictions = _predict(key, value, context, "fit")
        scores.append((math.fsum((_number(row["reference"]) - prediction) ** 2 for row, prediction in zip(fit, predictions)), value))
      if max(score for score, _ in scores) == min(score for score, _ in scores):
        raise _Blocked("candidate_has_no_exposed_effect")
      estimate = float(min(scores)[1])
    else:
      base = _predict(key, current, context, "fit")
      neighbor = current + step if current + step <= upper else current - step
      if neighbor < lower:
        raise _Blocked("parameter_grid_empty")
      trial = _predict(key, neighbor, context, "fit")
      slopes = [(b - a) / (neighbor - current) for a, b in zip(base, trial)]
      scale = max(abs(slope) for slope in slopes)
      if scale == 0.:
        raise _Blocked("candidate_has_no_exposed_effect")
      normalized = [slope / scale for slope in slopes]
      delta = math.fsum((_number(row["reference"]) - value) * slope for row, value, slope in zip(fit, base, normalized)) / (scale * math.fsum(slope ** 2 for slope in normalized))
      estimate = current + delta
      if not math.isfinite(estimate):
        raise _Blocked("raw_estimate_nonfinite")
      snapped = current + round((estimate - current) / step) * step
      candidates = sorted({current - step, current, current + step, snapped - step, snapped, snapped + step})
    limit = parameter.get("max_delta_raw")
    candidates = [value for value in candidates if lower <= value <= upper and (limit is None or abs(value - current) <= limit)]
    candidates.sort(key=lambda value: (abs(value - estimate), abs(value - current), value))
    candidates = candidates[:profile.get("max_candidates_per_key", 32)]
    result.update(raw_estimate=estimate, candidate_raw_values=candidates, reason_codes=[])
  except _Blocked as exc:
    result["reason_codes"] = [str(exc)]
  except ImportError:
    result["reason_codes"] = ["numeric_backend_unavailable"]
  return result
