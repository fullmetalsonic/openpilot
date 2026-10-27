"""Bounded event response diagnostics, never software optima or causal effects."""
from copy import deepcopy
from bisect import bisect_left
import hashlib
import json
import math
import re

import numpy as np


METHOD = "signal_stage_event_response_v1"
OPTIONS = {"seed": 1729, "bootstrap_repetitions": 256, "confidence": .95, "max_events": 64,
  "min_events": 6, "max_model_evaluations": 6720, "max_rows": 200000, "event_duration_s": 6.,
  "event_guard_s": 2., "pre_event_s": .5, "max_period_s": .05, "max_delay_s": 1., "delay_step_s": .05,
  "max_relative_rmse": .2, "max_residual_correlation": .6, "max_lag_interval_s": .25,
  "max_tau_interval_s": .75, "bootstrap_convergence_s": .1, "bootstrap_convergence_curvature": 1e-6,
  "reservoir_windows": 16, "reservoir_window_s": 32, "report_event_details": 16,
  "trial_baseline_events": 8, "repeat_speed_tolerance_m_s": 2., "repeat_amplitude_fraction": .1,
  "tau_resolution_period_fraction": .5}
STAGES = {
  "lateral_target_to_output": ("lateral", "target", "target_mono_ns", "sent_request", "sent_mono_ns", .01),
  "lateral_output_to_steering": ("lateral", "sent_request", "sent_mono_ns", "steering_angle_deg", "steering_mono_ns", .01),
  "lateral_steering_to_yaw": ("lateral", "steering_angle_deg", "steering_mono_ns", "yaw_rate", "yaw_mono_ns", .02),
  "longitudinal_target_to_scc": ("longitudinal", "target", "target_mono_ns", "sent_request", "sent_mono_ns", .02),
  "longitudinal_scc_to_acceleration": ("longitudinal", "sent_request", "sent_mono_ns", "actual", "actual_mono_ns", .02),
}
STAGE_UNITS = {"lateral_target_to_output": ("m/s^2", "1"), "lateral_output_to_steering": ("1", "deg"),
  "lateral_steering_to_yaw": ("deg", "rad/s"), "longitudinal_target_to_scc": ("m/s^2", "m/s^2"),
  "longitudinal_scc_to_acceleration": ("m/s^2", "m/s^2")}
LIMITATIONS = ["observational_closed_loop_response", "event_clock_not_sensor_capture_clock",
  "sensor_filter_and_ecu_processing_not_separated", "lag_is_not_software_setting", "development_identification_budget",
  "separated_events_do_not_prove_statistical_independence", "native_windows_selected_across_full_input",
  "event_bootstrap_plus_clock_resolution_not_total_model_uncertainty"]


def _finite(value):
  return type(value) in (int, float) and math.isfinite(value)


def _hash(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class DiagnosticWindows:
  """Frozen hash reservoir over a complete pass, retaining native samples."""
  def __init__(self, start):
    self.start, self.windows, self.count, self.retained = start, {}, 0, 0
    self.source_hash, self.overflow = hashlib.sha256(), False

  def add(self, row):
    self.count += 1
    self.source_hash.update(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n")
    key = (row["mono_ns"] - self.start) // (OPTIONS["reservoir_window_s"] * 1_000_000_000)
    if key not in self.windows:
      rank = _hash([OPTIONS["seed"], key])
      if len(self.windows) == OPTIONS["reservoir_windows"]:
        worst = max(self.windows, key=lambda name: self.windows[name][0])
        if rank >= self.windows[worst][0]:
          return
        self.retained -= len(self.windows.pop(worst)[1])
      self.windows[key] = (rank, [])
    if self.retained >= OPTIONS["max_rows"]:
      self.overflow = True
      return
    self.windows[key][1].append(row)
    self.retained += 1

  def finish(self):
    rows = [row for _, points in self.windows.values() for row in points]
    rows.sort(key=lambda row: (row["mono_ns"], row["axis"]))
    return rows, {"policy": "full_pass_seeded_native_window_reservoir_v1", "input_rows": self.count,
      "selected_rows": len(rows), "selected_windows": sorted(self.windows), "overflow": self.overflow,
      "input_sha256": self.source_hash.hexdigest()}


def _interval(values, rng):
  array = np.asarray(values, dtype=float)
  draws = rng.integers(0, len(array), (OPTIONS["bootstrap_repetitions"], len(array)))
  samples = np.median(array[draws], axis=1)
  lo, hi = np.quantile(samples, [.025, .975])
  half = np.quantile(samples[:len(samples)//2], [.025, .975])
  return {"estimate": float(np.median(array)), "lower": float(lo), "upper": float(hi)}, float(max(abs(half - [lo, hi])))


def _minimum_effect(events):
  """Estimate repeat variation within conditions, not between turns or speeds."""
  groups = []
  for event in events[:OPTIONS["trial_baseline_events"]]:
    if event["tracking_rmse_m_s2"] is None or event["speed_m_s"] is None:
      continue
    group = next((group for group in groups if event["direction"] == group[0]["direction"]
      and abs(event["speed_m_s"] - group[0]["speed_m_s"]) <= OPTIONS["repeat_speed_tolerance_m_s"]
      and abs(event["target_amplitude_m_s2"] - group[0]["target_amplitude_m_s2"])
        <= OPTIONS["repeat_amplitude_fraction"] * max(event["target_amplitude_m_s2"], group[0]["target_amplitude_m_s2"]) + 1e-9), None)
    if group is None:
      groups.append([event])
    else:
      group.append(event)
  deviations = []
  for group in groups:
    if len(group) >= 2:
      values = np.array([event["tracking_rmse_m_s2"] for event in group])
      deviations.extend(abs(values - np.median(values)))
  return max(1e-9, 1.4826 * float(np.median(deviations))) if len(deviations) >= OPTIONS["min_events"] else None


def _fit(t, u, y, budget):
  dt = float(np.median(np.diff(t)))
  if dt <= 0 or dt > OPTIONS["max_period_s"] * 1.001 or max(abs(np.diff(t) - dt)) > dt * .2:
    return None, "response_clock_resolution_or_jitter"
  delay_grid = np.arange(0., OPTIONS["max_delay_s"] + OPTIONS["delay_step_s"] / 2, OPTIONS["delay_step_s"])
  first = int(math.ceil(OPTIONS["max_delay_s"] / dt)) + 1
  if len(t) - first < 40 or np.std(u) < 1e-8 or np.std(y) < 1e-8:
    return None, "response_excitation_insufficient"
  target, previous = y[first:], y[first-1:-1]
  choices = []
  for delay in delay_grid:
    if budget[0] >= OPTIONS["max_model_evaluations"] // len(STAGES):
      return None, "diagnostic_model_budget_exhausted"
    budget[0] += 1
    indices = np.searchsorted(t, t[first:] - delay + dt * 1e-6, side="right") - 1
    matrix = np.column_stack((previous, u[np.maximum(indices, 0)], np.ones(len(target))))
    scale = np.std(matrix[:, :2], axis=0)
    if min(scale) < 1e-10:
      continue
    scaled = matrix.copy()
    scaled[:, :2] /= scale
    coefficient, _, rank, singular = np.linalg.lstsq(scaled, target, rcond=None)
    if rank != 3 or singular[0] / singular[-1] > 1e6:
      continue
    coefficient[:2] /= scale
    pole = float(coefficient[0])
    if not 0 <= pole < .99999:
      continue
    residual = target - matrix @ coefficient
    rmse = float(np.sqrt(np.mean(residual**2)))
    tau = -dt / math.log(pole) if pole > 1e-8 else 0.
    if tau > OPTIONS["event_duration_s"] / 2:
      continue
    corr = (float(np.corrcoef(residual[:-1], residual[1:])[0, 1])
            if np.std(residual) > max(1e-8, np.std(y)*1e-6) else 0.)
    choices.append((rmse, float(delay), tau, float(coefficient[1] / (1 - pole)), corr))
  if not choices:
    return None, "response_model_unidentifiable"
  rmse, delay, tau, gain, corr = min(choices)
  if rmse / np.std(y) > OPTIONS["max_relative_rmse"]:
    return None, "response_model_residual_large"
  if not math.isfinite(corr) or abs(corr) > OPTIONS["max_residual_correlation"]:
    return None, "response_residual_serial_correlation"
  # Equally plausible delays indicate a confounded pole/delay, even at high fit.
  floor = max(1e-12, rmse**2 * .05)
  plausible = [item[1] for item in choices if item[0]**2 <= rmse**2 + floor]
  if max(plausible) - min(plausible) > OPTIONS["max_lag_interval_s"]:
    return None, "response_delay_profile_unidentified"
  return {"lag_s": delay, "time_constant_s": tau, "gain": gain, "rmse": rmse,
          "relative_rmse": rmse / float(np.std(y)), "residual_correlation": corr, "period_s": dt}, None


def _events(rows, spec, budget):
  if len(rows) > OPTIONS["max_rows"]:
    return [], {"diagnostic_row_budget_exhausted"}
  axis, input_key, input_time, output_key, output_time, minimum = spec
  chunks, current, failures, previous = [], [], set(), None
  for row in rows:
    if row["axis"] != axis:
      continue
    blocked = bool(row["excluded_reasons"] or row.get("saturated", False) or row.get("stop_req") == 1
                   or row.get("should_stop") is True or row.get("standstill") is True)
    valid = (not blocked
             and all(_finite(row.get(key)) for key in (input_key, output_key))
             and all(type(row.get(key)) is int and 0 <= row[key] <= row["mono_ns"] for key in (input_time, output_time)))
    if not valid:
      failures.add("masked_saturated_or_stopping_response" if blocked else "stage_signal_or_original_clock_missing")
      if current:
        chunks.append(current)
      current, previous = [], None
      continue
    stamp = row[output_time]
    if previous is not None and stamp == previous:
      continue  # Held source values are one observation, never extra samples.
    if previous is not None and (stamp < previous or stamp - previous > 100_000_000):
      if current:
        chunks.append(current)
      current = []
    track_actual = row.get("independent_actual") if axis == "lateral" else row.get("actual")
    tracking_error = track_actual - row["target"] if _finite(track_actual) and _finite(row.get("target")) else None
    current.append((stamp, row[input_time], row[input_key], row[output_key], row.get("v_ego"), tracking_error, row.get("target")))
    previous = stamp
  if current:
    chunks.append(current)
  events = []
  last_event_end = -10**30
  for chunk in chunks:
    if len(chunk) < 120:
      continue
    t = np.array([item[0] for item in chunk], dtype=np.float64) * 1e-9
    # Respect the input's source time rather than the row/arrival clock.
    inputs = {}
    for item in chunk:
      if item[1] in inputs and inputs[item[1]] != item[2]:
        failures.add("stage_source_clock_conflict")
        break
      inputs[item[1]] = item[2]
    else:
      source_t = np.array(sorted(inputs), dtype=np.float64) * 1e-9
      source_u = np.array([inputs[key] for key in sorted(inputs)])
      if len(source_t) < 40 or np.median(np.diff(source_t)) > OPTIONS["max_period_s"] * 1.001:
        failures.add("input_clock_resolution_insufficient")
        continue
      # An older held response cannot borrow the first later input.
      supported = t >= source_t[0]
      chunk = [item for item, use in zip(chunk, supported) if use]
      t = t[supported]
      if len(t) < 120:
        failures.add("stage_source_support_insufficient")
        continue
      u = source_u[np.searchsorted(source_t, t, side="right") - 1]
      y = np.array([item[3] for item in chunk])
      span = float(np.quantile(u, .95) - np.quantile(u, .05))
      if span < minimum * 4:
        failures.add("response_excitation_insufficient")
        continue
      delta = np.diff(u)
      noise = float(np.median(abs(delta - np.median(delta))))
      lookback = max(1, int(round(.3 / np.median(np.diff(t)))))
      threshold = max(span * .18, noise * 6 * math.sqrt(lookback), minimum)
      crossings = np.flatnonzero(abs(u[lookback:] - u[:-lookback]) > threshold) + lookback
      next_start = t[0] + OPTIONS["pre_event_s"]
      for index in crossings:
        if t[index] < next_start:
          continue
        start, end = t[index] - OPTIONS["pre_event_s"], t[index] - OPTIONS["pre_event_s"] + OPTIONS["event_duration_s"]
        next_start = end + OPTIONS["event_guard_s"]
        if end > t[-1]:
          failures.add("event_response_right_censored")
          continue
        if start < last_event_end + OPTIONS["event_guard_s"]:
          continue
        selected = (t >= start) & (t <= end)
        et, eu, ey = t[selected], u[selected], y[selected]
        selected_indices = np.flatnonzero(selected)
        original_start, original_end = chunk[int(selected_indices[0])][0], chunk[int(selected_indices[-1])][0]
        fit, reason = _fit(et - et[0], eu, ey, budget)
        if reason:
          failures.add(reason)
          continue
        # A completed input return is required for the predeclared recovery metric.
        baseline = float(np.median(eu[:max(2, lookback//2)]))
        departed = np.flatnonzero(abs(eu - baseline) > threshold)
        returned = (np.flatnonzero((np.arange(len(eu)) > departed[-1]) & (abs(eu - baseline) <= threshold))
                    if len(departed) else np.array([], dtype=int))
        recovery = None
        if len(returned):
          response_base = float(np.median(ey[:max(2, lookback//2)]))
          response_band = max(float(np.ptp(ey))*.1, 3*fit["rmse"], 1e-8)
          for cursor in returned:
            duration = max(2, lookback)
            if cursor + duration <= len(ey) and np.all(abs(ey[cursor:cursor+duration] - response_base) <= response_band):
              recovery = float(et[cursor] - et[returned[0]])
              break
        quiet = max(2, lookback)
        if (recovery is None or np.any(abs(eu[:quiet] - baseline) > threshold)
            or np.any(abs(eu[-quiet:] - baseline) > threshold)):
          failures.add("event_boundary_or_recovery_censored")
          continue
        speed = [item[4] for item, use in zip(chunk, selected) if use and _finite(item[4])]
        tracking = [item[5] for item, use in zip(chunk, selected) if use and _finite(item[5])]
        targets = [item[6] for item, use in zip(chunk, selected) if use and _finite(item[6])]
        sign = 1 if float(np.mean(eu - baseline)) >= 0 else -1
        event = {"id": _hash([axis, input_key, output_key, original_start, original_end]),
          "start_ns": original_start, "end_ns": original_end, "direction": sign,
          "speed_m_s": float(np.median(speed)) if speed else None, "sample_count": len(et),
          **fit, "recovery_time_s": recovery,
          "target_amplitude_m_s2": float(np.ptp(targets)) if targets else 0.,
          "tracking_rmse_m_s2": float(np.sqrt(np.mean(np.square(tracking)))) if len(tracking) >= 40 else None}
        events.append(event)
        last_event_end = float(et[-1])
        if len(events) >= OPTIONS["max_events"]:
          return events, failures
  return events, failures


def build_diagnostics(rows, *, selection=None):
  if selection is None:
    reservoir = DiagnosticWindows(rows[0]["mono_ns"] if rows else 0)
    for row in rows:
      reservoir.add(row)
    rows, selection = reservoir.finish()
  total_evaluations, stages = 0, []
  for name, spec in STAGES.items():
    budget = [0]
    events, failures = _events(rows, spec, budget) if not selection["overflow"] else ([], {"diagnostic_row_budget_exhausted"})
    total_evaluations += budget[0]
    reasons, lag, tau, convergence = [], None, None, None
    groups = {}
    for event in events:
      group = (event["direction"], int(event["speed_m_s"] // 10) if event["speed_m_s"] is not None else -1)
      groups.setdefault(group, []).append(event)
    support = [{"direction": group[0], "speed_band": group[1], "event_count": len(items),
      "lag_s": float(np.median([event["lag_s"] for event in items])),
      "time_constant_s": float(np.median([event["time_constant_s"] for event in items]))}
      for group, items in sorted(groups.items())]
    if len(events) < OPTIONS["min_events"]:
      reasons.extend(sorted(failures) or ["independent_response_events_insufficient"])
      reasons.append("independent_response_events_insufficient")
      if selection["selected_rows"] < selection["input_rows"]:
        reasons.append("bounded_windows_support_insufficient_unselected_input_uncharacterized")
    else:
      rng = np.random.default_rng(OPTIONS["seed"])
      lag, drift_lag = _interval([event["lag_s"] for event in events], rng)
      tau, drift_tau = _interval([event["time_constant_s"] for event in events], rng)
      resolution = OPTIONS["delay_step_s"] / 2 + float(np.median([event["period_s"] for event in events]))
      lag["lower"], lag["upper"] = max(0., lag["lower"] - resolution), lag["upper"] + resolution
      tau_resolution = float(np.median([event["period_s"] for event in events])) * OPTIONS["tau_resolution_period_fraction"]
      tau["lower"], tau["upper"] = max(0., tau["lower"] - tau_resolution), tau["upper"] + tau_resolution
      convergence = max(drift_lag, drift_tau)
      if convergence > OPTIONS["bootstrap_convergence_s"]:
        reasons.append("event_bootstrap_unstable")
      if lag["upper"] - lag["lower"] > OPTIONS["max_lag_interval_s"] or tau["upper"] - tau["lower"] > OPTIONS["max_tau_interval_s"]:
        reasons.append("response_interval_too_wide")
      signs = {1 if event["gain"] > 0 else -1 for event in events}
      if len(signs) != 1:
        reasons.append("response_direction_inconsistent")
      if any(event["speed_m_s"] is None for event in events):
        reasons.append("speed_condition_unavailable")
      supported = [item for item in support if item["event_count"] >= 2]
      if supported and (np.ptp([item["lag_s"] for item in supported]) > 2 * OPTIONS["delay_step_s"] + 1e-9
          or np.ptp([item["time_constant_s"] for item in supported]) > .2):
        reasons.append("direction_or_speed_response_inconsistent")
    stages.append({"id": name, "axis": spec[0], "input_signal": spec[1], "output_signal": spec[3],
      "input_unit": STAGE_UNITS[name][0], "output_unit": STAGE_UNITS[name][1],
      "state": "held" if reasons else "identified_response", "reasons": list(dict.fromkeys(reasons)),
      "event_count": len(events), "events": events, "lag_s": lag, "time_constant_s": tau,
      "bootstrap_convergence_s": convergence, "rejected_reasons": sorted(failures), "condition_support": support,
      "event_ids": [event["id"] for event in events], "events_truncated": False})
  timing = {}
  for key, stage_id in (("SteerActuatorDelay", "lateral_output_to_steering"),
                        ("LongActuatorDelay", "longitudinal_scc_to_acceleration")):
    stage = next(item for item in stages if item["id"] == stage_id)
    complete = [event for event in stage["events"] if event["recovery_time_s"] is not None]
    reasons = list(stage["reasons"])
    if len(complete) < OPTIONS["min_events"]:
      reasons.append("completed_recovery_events_insufficient")
    if {event["direction"] for event in complete} != {-1, 1}:
      reasons.append("bidirectional_recovery_support_insufficient")
    # The same development primary metric is declared before any events are inspected.
    recovery = float(np.median([event["recovery_time_s"] for event in complete])) if complete else None
    if recovery is not None and recovery <= OPTIONS["max_period_s"] * 2:
      reasons.append("recovery_delay_below_resolution")
    tracking = [event["tracking_rmse_m_s2"] for event in complete if event["tracking_rmse_m_s2"] is not None]
    if len(tracking) < OPTIONS["min_events"]:
      reasons.append("independent_tracking_variability_unavailable")
    minimum = _minimum_effect(complete)
    if minimum is None:
      reasons.append("matched_repeat_tracking_events_insufficient")
    eligible = not reasons
    timing[key] = {"state": "experiment_required" if eligible else "held", "reasons": list(dict.fromkeys(reasons)),
      "evidence_stage_ids": [stage_id], "hypothesis": "timing_setting_may_change_target_tracking_not_yet_demonstrated",
      "trial_plan": {"primary_metric": "target_tracking_rmse", "direction_effect": "unconfirmed",
        "minimum_effect": {"unit": "m/s^2", "value": minimum,
                           "basis": "repeated_event_tracking_variability_numeric_floor"},
        "guards": ["overshoot", "output_rate", "saturation_fraction", "intervention_fraction"],
        "event_count_per_candidate": 8, "max_observation_s": 900, "max_candidates": 2,
        "matched_pairs_required": 8, "insufficient_match_outcome": "inconclusive",
        "stop_rule": "first_eight_after_complete_or_900s", "development_budget": True} if eligible else None}
  timing["LatSmoothSec"] = {"state": "held", "reasons": ["full_rate_pre_post_smoothing_and_frame_state_unavailable"],
    "evidence_stage_ids": [], "hypothesis": "smoothing_noise_recovery_tradeoff_requires_frame_inputs", "trial_plan": None}
  return {"version": 1, "method_id": METHOD, "semantics": "observational_response_diagnostic", "causal_effect_proven": False,
    "software_optimum_identified": False, "execution": {**deepcopy(OPTIONS), "model_evaluations": total_evaluations,
      **selection}, "limitations": list(LIMITATIONS), "stages": stages, "timing": timing}


def attach_smoothing(diagnostics, events, usable_times, current_raw, start, end):
  """Check each sampled frame's exact map; never infer its inter-frame response."""
  from .consumer_mapping import special_field_matches
  value = deepcopy(diagnostics)
  usable = sorted(set(usable_times))
  samples, rejected, last = [], set(), -10**30
  support = next(stage for stage in value["stages"] if stage["id"] == "lateral_target_to_output")
  used_events = set()
  required = ("lat_pre_smooth_curvature", "lat_post_smooth_curvature", "lat_previous_curvature", "v_ego",
              "lat_smooth_y_std", "lat_smooth_dynamic_s", "lat_action_t", "lat_delay_used_s")
  for event in events:
    if event.get("consumer_id") != "modeld.action":
      continue
    stamp = event.get("produced_mono_ns")
    if type(stamp) is not int or not start <= stamp <= end:
      continue
    cursor = bisect_left(usable, stamp)
    if cursor == len(usable) or usable[cursor] - stamp > 100_000_000:
      rejected.add("smoothing_sample_outside_usable_mask")
      continue
    event_support = next((item for item in support["events"] if item["start_ns"] <= stamp <= item["end_ns"]), None)
    if event_support is None:
      rejected.add("smoothing_independent_event_support_unavailable")
      continue
    if event_support["id"] in used_events:
      continue
    ctx = event.get("context", {})
    if any(not _finite(ctx.get(key)) for key in required) or ctx.get("lat_smoothing_applied") is not True:
      rejected.add("pre_smoothing_target_unavailable")
      continue
    base = current_raw * .01 if type(current_raw) is int else -1
    if special_field_matches(event, "LatSmoothSec", base) is not True:
      rejected.add("smoothing_current_mapping_unreconciled")
      continue
    dynamic = ctx["lat_smooth_dynamic_s"]
    raw, post, previous = (ctx[key] for key in required[:3])
    if abs(raw - previous) < 1e-6 or ctx["v_ego"] <= 1.:
      rejected.add("smoothing_excitation_insufficient")
      continue
    if stamp - last < 8_000_000_000:
      continue
    last = stamp
    used_events.add(event_support["id"])
    samples.append({"event_sha256": _hash(event), "support_event_id": event_support["id"], "mono_ns": stamp, "direction": 1 if raw > previous else -1,
      "dynamic_s": dynamic, "filter_gap_curvature": abs(raw - post), "one_cycle_change_curvature": abs(post - previous)})
    if len(samples) == OPTIONS["max_events"]:
      break
  reasons = []
  if support["state"] != "identified_response":
    reasons.append("smoothing_independent_response_support_unavailable")
  tracking = [event["tracking_rmse_m_s2"] for event in support["events"] if event["tracking_rmse_m_s2"] is not None]
  if len(tracking) < OPTIONS["min_events"]:
    reasons.append("independent_tracking_variability_unavailable")
  minimum = _minimum_effect(support["events"])
  if minimum is None:
    reasons.append("matched_repeat_tracking_events_insufficient")
  if len(samples) < OPTIONS["min_events"]:
    reasons.extend(sorted(rejected) or ["pre_smoothing_target_unavailable"])
    reasons.append("independent_smoothing_samples_insufficient")
  if {sample["direction"] for sample in samples} != {-1, 1}:
    reasons.append("bidirectional_smoothing_support_insufficient")
  # A parity failure is never hidden by selecting other convenient frames.
  if {"smoothing_output_parity_failed", "smoothing_current_mapping_unreconciled"} & rejected:
    reasons.append("smoothing_current_mapping_unreconciled")
  gap, change, convergence = None, None, None
  if len(samples) >= OPTIONS["min_events"]:
    rng = np.random.default_rng(OPTIONS["seed"])
    gap, drift_gap = _interval([sample["filter_gap_curvature"] for sample in samples], rng)
    change, drift_change = _interval([sample["one_cycle_change_curvature"] for sample in samples], rng)
    convergence = max(drift_gap, drift_change)
    if convergence > OPTIONS["bootstrap_convergence_curvature"]:
      reasons.append("smoothing_event_bootstrap_unstable")
    if gap["lower"] <= 1e-7:
      reasons.append("smoothing_effect_below_resolution")
  value["sampled_smoothing"] = {"method_id": "sampled_frame_filter_mapping_v1", "sample_clock": "consumer_produced_approximate",
    "continuous_response_identified": False, "sample_count": len(samples), "samples": samples,
    "filter_gap_curvature": gap, "one_cycle_change_curvature": change,
    "bootstrap_convergence_curvature": convergence, "rejected_reasons": sorted(rejected)}
  eligible = not reasons
  value["timing"]["LatSmoothSec"] = {"state": "experiment_required" if eligible else "held", "reasons": list(dict.fromkeys(reasons)),
    "evidence_stage_ids": ["lateral_target_to_output"], "hypothesis": "sampled_filter_attenuation_recovery_tradeoff_requires_independent_ab_test",
    "trial_plan": {"primary_metric": "target_tracking_rmse", "direction_effect": "unconfirmed",
      "minimum_effect": {"unit": "m/s^2", "value": minimum,
                         "basis": "repeated_event_tracking_variability_numeric_floor"},
      "guards": ["overshoot", "output_rate", "saturation_fraction", "intervention_fraction"],
      "event_count_per_candidate": 8, "max_observation_s": 900, "max_candidates": 2,
      "matched_pairs_required": 8, "insufficient_match_outcome": "inconclusive",
      "stop_rule": "first_eight_after_complete_or_900s", "development_budget": True} if eligible else None}
  return value


def compact_diagnostics(value):
  """Reduce report examples only, preserving all computed evidence/intervals."""
  result = deepcopy(value)
  for stage in result["stages"]:
    stage["events"] = stage["events"][:OPTIONS["report_event_details"]]
    stage["events_truncated"] = len(stage["events"]) < stage["event_count"]
  return result


def validate_diagnostics(value, start, end):
  """Strict report boundary; historical schema-one reviews use their old path."""
  required = {"version", "method_id", "semantics", "causal_effect_proven", "software_optimum_identified",
              "execution", "limitations", "stages", "timing"}
  if (type(value) is not dict or set(value) not in (required, required | {"sampled_smoothing"})
      or type(value["version"]) is not int or value["version"] != 1 or value["method_id"] != METHOD
      or value["semantics"] != "observational_response_diagnostic" or value["causal_effect_proven"] is not False
      or value["software_optimum_identified"] is not False or value["limitations"] != LIMITATIONS):
    raise ValueError("invalid_response_diagnostics")
  execution = value["execution"]
  selection_fields = {"model_evaluations", "input_rows", "input_sha256", "selected_rows", "selected_windows", "overflow", "policy"}
  if (type(execution) is not dict or set(execution) != set(OPTIONS) | selection_fields
      or any(type(execution[name]) is not type(expected) or execution[name] != expected for name, expected in OPTIONS.items())
      or type(execution["model_evaluations"]) is not int or not 0 <= execution["model_evaluations"] <= OPTIONS["max_model_evaluations"]
      or type(execution["input_rows"]) is not int or execution["input_rows"] < 0
      or type(execution["selected_rows"]) is not int or not 0 <= execution["selected_rows"] <= min(execution["input_rows"], OPTIONS["max_rows"])
      or type(execution["overflow"]) is not bool or execution["policy"] != "full_pass_seeded_native_window_reservoir_v1"
      or type(execution["selected_windows"]) is not list or len(execution["selected_windows"]) > OPTIONS["reservoir_windows"]
      or any(type(number) is not int or number < 0 for number in execution["selected_windows"])
      or sorted(set(execution["selected_windows"])) != execution["selected_windows"]
      or type(execution["input_sha256"]) is not str or re.fullmatch("[a-f0-9]{64}", execution["input_sha256"]) is None):
    raise ValueError("invalid_response_execution")
  def reasons(items):
    if (type(items) is not list or len(items) > 32
        or any(type(reason) is not str or re.fullmatch("[a-z0-9_]{1,128}", reason) is None for reason in items)
        or len(set(items)) != len(items)):
      raise ValueError("invalid_response_reasons")
  def interval(item):
    if item is not None and (type(item) is not dict or set(item) != {"estimate", "lower", "upper"}
      or any(not _finite(number) or number < 0 for number in item.values()) or not item["lower"] <= item["estimate"] <= item["upper"]):
      raise ValueError("invalid_response_interval")
  if type(value["stages"]) is not list or [stage.get("id") for stage in value["stages"] if type(stage) is dict] != list(STAGES):
    raise ValueError("invalid_response_stages")
  stage_fields = {"id", "axis", "input_signal", "output_signal", "state", "reasons", "event_count", "events", "lag_s",
                  "time_constant_s", "bootstrap_convergence_s", "rejected_reasons", "condition_support", "event_ids", "events_truncated", "input_unit", "output_unit"}
  event_fields = {"id", "start_ns", "end_ns", "direction", "speed_m_s", "sample_count", "lag_s", "time_constant_s",
                  "gain", "rmse", "relative_rmse", "residual_correlation", "period_s", "recovery_time_s", "tracking_rmse_m_s2", "target_amplitude_m_s2"}
  for stage in value["stages"]:
    spec = STAGES[stage["id"]]
    if (set(stage) != stage_fields or (stage["axis"], stage["input_signal"], stage["output_signal"]) != (spec[0], spec[1], spec[3])
        or (stage["input_unit"], stage["output_unit"]) != STAGE_UNITS[stage["id"]]
        or stage["state"] not in {"held", "identified_response"} or type(stage["events"]) is not list
        or type(stage["event_count"]) is not int or not 0 <= stage["event_count"] <= OPTIONS["max_events"]
        or type(stage["events_truncated"]) is not bool or stage["events_truncated"] != (len(stage["events"]) < stage["event_count"])
        or len(stage["events"]) != (min(OPTIONS["report_event_details"], stage["event_count"]) if stage["events_truncated"] else stage["event_count"])
        or type(stage["event_ids"]) is not list or len(stage["event_ids"]) != stage["event_count"]
        or any(type(sha) is not str or re.fullmatch("[a-f0-9]{64}", sha) is None for sha in stage["event_ids"])
        or len(set(stage["event_ids"])) != stage["event_count"]
        or [event.get("id") for event in stage["events"] if type(event) is dict] != stage["event_ids"][:len(stage["events"])]):
      raise ValueError("invalid_response_stage")
    reasons(stage["reasons"])
    reasons(stage["rejected_reasons"])
    if type(stage["condition_support"]) is not list or len(stage["condition_support"]) > OPTIONS["max_events"]:
      raise ValueError("invalid_response_conditions")
    for support in stage["condition_support"]:
      if (type(support) is not dict or set(support) != {"direction", "speed_band", "event_count", "lag_s", "time_constant_s"}
          or type(support["direction"]) is not int or support["direction"] not in (-1, 1)
          or type(support["speed_band"]) is not int or support["speed_band"] < -1
          or type(support["event_count"]) is not int or not 1 <= support["event_count"] <= stage["event_count"]
          or not _finite(support["lag_s"]) or not 0 <= support["lag_s"] <= OPTIONS["max_delay_s"]
          or not _finite(support["time_constant_s"]) or support["time_constant_s"] < 0):
        raise ValueError("invalid_response_conditions")
    if sum(support["event_count"] for support in stage["condition_support"]) != stage["event_count"]:
      raise ValueError("invalid_response_conditions")
    interval(stage["lag_s"])
    interval(stage["time_constant_s"])
    previous, ids = -10**30, set()
    for event in stage["events"]:
      if (type(event) is not dict or set(event) != event_fields or type(event["id"]) is not str
          or re.fullmatch("[a-f0-9]{64}", event["id"]) is None or event["id"] in ids
          or any(type(event[key]) is not int for key in ("start_ns", "end_ns", "sample_count", "direction"))
          or not start <= event["start_ns"] < event["end_ns"] <= end or event["sample_count"] < 40
          or event["start_ns"] < previous + int(OPTIONS["event_guard_s"] * 1e9) - 1
          or event["direction"] not in (-1, 1)
          or any(not _finite(event[key]) for key in ("lag_s", "time_constant_s", "gain", "rmse", "relative_rmse", "residual_correlation", "period_s"))
          or not 0 <= event["lag_s"] <= OPTIONS["max_delay_s"] or event["time_constant_s"] < 0
          or not 0 < event["period_s"] <= OPTIONS["max_period_s"] * 1.001 or event["rmse"] < 0
          or not 0 <= event["relative_rmse"] <= OPTIONS["max_relative_rmse"] or abs(event["residual_correlation"]) > OPTIONS["max_residual_correlation"]
          or event["speed_m_s"] is not None and (not _finite(event["speed_m_s"]) or event["speed_m_s"] < 0)
          or event["tracking_rmse_m_s2"] is not None and (not _finite(event["tracking_rmse_m_s2"]) or event["tracking_rmse_m_s2"] < 0)
          or not _finite(event["target_amplitude_m_s2"]) or event["target_amplitude_m_s2"] < 0
          or event["recovery_time_s"] is not None and (not _finite(event["recovery_time_s"]) or event["recovery_time_s"] < 0)):
        raise ValueError("invalid_response_event")
      ids.add(event["id"])
      previous = event["end_ns"]
    if stage["state"] == "identified_response" and (stage["reasons"] or stage["event_count"] < OPTIONS["min_events"]
        or stage["lag_s"] is None or stage["time_constant_s"] is None or not _finite(stage["bootstrap_convergence_s"])
        or stage["bootstrap_convergence_s"] > OPTIONS["bootstrap_convergence_s"] or execution["overflow"]
        or stage["lag_s"]["upper"] - stage["lag_s"]["lower"] > OPTIONS["max_lag_interval_s"]
        or stage["time_constant_s"]["upper"] - stage["time_constant_s"]["lower"] > OPTIONS["max_tau_interval_s"]):
      raise ValueError("invalid_identified_response")
    if stage["state"] == "held" and not stage["reasons"]:
      raise ValueError("invalid_held_response")
  if type(value["timing"]) is not dict or set(value["timing"]) != {"SteerActuatorDelay", "LongActuatorDelay", "LatSmoothSec"}:
    raise ValueError("invalid_timing_diagnostics")
  plan_keys = {"primary_metric", "direction_effect", "minimum_effect", "guards", "event_count_per_candidate", "max_observation_s", "max_candidates", "stop_rule", "development_budget", "matched_pairs_required", "insufficient_match_outcome"}
  for key, timing in value["timing"].items():
    if (type(timing) is not dict or set(timing) != {"state", "reasons", "evidence_stage_ids", "hypothesis", "trial_plan"}
        or timing["state"] not in {"held", "experiment_required"} or type(timing["evidence_stage_ids"]) is not list
        or any(type(name) is not str or name not in STAGES for name in timing["evidence_stage_ids"]) or type(timing["hypothesis"]) is not str
        or re.fullmatch("[a-z0-9_]{1,128}", timing["hypothesis"]) is None):
      raise ValueError("invalid_timing_diagnostic")
    reasons(timing["reasons"])
    plan = timing["trial_plan"]
    if timing["state"] == "held":
      if plan is not None or not timing["reasons"]:
        raise ValueError("invalid_held_timing")
      continue
    if (timing["reasons"] or type(plan) is not dict or set(plan) != plan_keys or not timing["evidence_stage_ids"]
        or timing["evidence_stage_ids"] != [{"SteerActuatorDelay": "lateral_output_to_steering",
          "LongActuatorDelay": "longitudinal_scc_to_acceleration", "LatSmoothSec": "lateral_target_to_output"}[key]]
        or plan["primary_metric"] != "target_tracking_rmse"
        or plan["direction_effect"] != "unconfirmed" or plan["guards"] != ["overshoot", "output_rate", "saturation_fraction", "intervention_fraction"]
        or type(plan["event_count_per_candidate"]) is not int or plan["event_count_per_candidate"] != 8
        or type(plan["max_observation_s"]) is not int or plan["max_observation_s"] != 900
        or type(plan["max_candidates"]) is not int or plan["max_candidates"] != 2
        or plan["development_budget"] is not True or plan["stop_rule"] != "first_eight_after_complete_or_900s"
        or type(plan["matched_pairs_required"]) is not int or plan["matched_pairs_required"] != 8
        or plan["insufficient_match_outcome"] != "inconclusive"
        or any(next(stage for stage in value["stages"] if stage["id"] == name)["state"] != "identified_response" for name in timing["evidence_stage_ids"])):
      raise ValueError("invalid_timing_experiment")
    minimum = plan["minimum_effect"]
    if (type(minimum) is not dict or set(minimum) != {"unit", "value", "basis"}
        or minimum["unit"] != "m/s^2" or not _finite(minimum["value"]) or minimum["value"] <= 0
        or minimum["basis"] != "repeated_event_tracking_variability_numeric_floor"):
      raise ValueError("invalid_timing_minimum_effect")
    support = next(stage for stage in value["stages"] if stage["id"] == timing["evidence_stage_ids"][0])
    expected_minimum = _minimum_effect(support["events"])
    if expected_minimum is None or not math.isclose(minimum["value"], expected_minimum, rel_tol=1e-9, abs_tol=1e-12):
      raise ValueError("timing_minimum_effect_unreconciled")
  if "sampled_smoothing" in value:
    sample = value["sampled_smoothing"]
    fields = {"method_id", "sample_clock", "continuous_response_identified", "sample_count", "samples", "filter_gap_curvature",
              "one_cycle_change_curvature", "bootstrap_convergence_curvature", "rejected_reasons"}
    if (type(sample) is not dict or set(sample) != fields or sample["method_id"] != "sampled_frame_filter_mapping_v1"
        or sample["sample_clock"] != "consumer_produced_approximate" or sample["continuous_response_identified"] is not False
        or type(sample["samples"]) is not list or type(sample["sample_count"]) is not int
        or sample["sample_count"] != len(sample["samples"]) or not 0 <= sample["sample_count"] <= OPTIONS["max_events"]):
      raise ValueError("invalid_sampled_smoothing")
    interval(sample["filter_gap_curvature"])
    interval(sample["one_cycle_change_curvature"])
    reasons(sample["rejected_reasons"])
    support = next(stage for stage in value["stages"] if stage["id"] == "lateral_target_to_output")
    support_ids, used, previous = set(support["event_ids"]), set(), -10**30
    for item in sample["samples"]:
      if (type(item) is not dict or set(item) != {"event_sha256", "support_event_id", "mono_ns", "direction", "dynamic_s", "filter_gap_curvature", "one_cycle_change_curvature"}
          or type(item["event_sha256"]) is not str or re.fullmatch("[a-f0-9]{64}", item["event_sha256"]) is None
          or item["support_event_id"] not in support_ids or item["support_event_id"] in used
          or type(item["mono_ns"]) is not int or not start <= item["mono_ns"] <= end or item["mono_ns"] < previous + 8_000_000_000
          or type(item["direction"]) is not int or item["direction"] not in (-1, 1)
          or any(not _finite(item[key]) or item[key] < 0 for key in ("dynamic_s", "filter_gap_curvature", "one_cycle_change_curvature"))):
        raise ValueError("invalid_sampled_smoothing_point")
      previous = item["mono_ns"]
      used.add(item["support_event_id"])
    if value["timing"]["LatSmoothSec"]["state"] == "experiment_required" and sample["sample_count"] < OPTIONS["min_events"]:
      raise ValueError("invalid_smoothing_experiment")
  elif value["timing"]["LatSmoothSec"]["state"] == "experiment_required":
    raise ValueError("invalid_smoothing_experiment")
  return deepcopy(value)
