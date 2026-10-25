"""Bounded, side-effect-free diagnostics for explicitly normalized observations.

The caller owns the original logs, normalization, settings-epoch evidence and
profile qualification. These statistics are not parameter recommendations.
"""

from bisect import bisect_right
from dataclasses import dataclass, field
import math
from numbers import Real
from typing import Iterable


AXES = ("lateral", "longitudinal")


@dataclass(frozen=True)
class Observation:
  mono_ns: int
  axis: str
  target: float | None
  actual: float | None
  active: bool
  valid: bool
  driver_override: bool | None
  recovery_ready: bool
  epoch: str
  context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisProfile:
  profile_id: str
  pre_margin_ns: int
  recovery_ns: int
  max_gap_ns: int
  dependency_ns: int
  max_samples: int = 100_000

  def __post_init__(self):
    if not isinstance(self.profile_id, str) or not self.profile_id.strip():
      raise ValueError("profile_id must be a nonempty string")
    for name in ("pre_margin_ns", "recovery_ns", "max_gap_ns", "dependency_ns", "max_samples"):
      value = getattr(self, name)
      if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    if self.max_gap_ns == 0 or self.max_samples == 0:
      raise ValueError("max_gap_ns and max_samples must be positive")


def _validate_observation(item):
  if not isinstance(item, Observation):
    raise ValueError("input must contain Observation instances")
  if type(item.mono_ns) is not int or item.mono_ns < 0:
    raise ValueError("mono_ns must be a nonnegative integer")
  if item.axis not in AXES:
    raise ValueError("axis must be lateral or longitudinal")
  if not isinstance(item.epoch, str) or not item.epoch.strip():
    raise ValueError("epoch must be a nonempty string")
  if not isinstance(item.context, dict):
    raise ValueError("context must be a dictionary")
  for name in ("saturated", "output_limited"):
    value = item.context.get(name)
    if value is not None and type(value) is not bool:
      raise ValueError(f"context {name} must be boolean or None")
  for name in ("active", "valid", "recovery_ready"):
    if type(getattr(item, name)) is not bool:
      raise ValueError(f"{name} must be boolean")
  if item.driver_override is not None and type(item.driver_override) is not bool:
    raise ValueError("driver_override must be boolean or None")
  for name in ("target", "actual"):
    value = getattr(item, name)
    if value is not None and (isinstance(value, bool) or not isinstance(value, Real)):
      raise ValueError(f"{name} must be numeric or None")


def _error(item):
  if item.target is None or item.actual is None:
    return None, "measurement_missing"
  try:
    target, actual = float(item.target), float(item.actual)
  except (OverflowError, ValueError) as exc:
    raise ValueError("measurement cannot be represented as a finite float") from exc
  if not math.isfinite(target) or not math.isfinite(actual):
    return None, "measurement_nonfinite"
  error = actual - target
  if not math.isfinite(error):
    raise ValueError("finite measurement difference overflowed")
  return error, None


def _metrics(errors):
  count = len(errors)
  if not count:
    return {"count": 0, "bias": None, "rmse": None, "p95_absolute_error": None}
  scale = max(abs(error) for error in errors)
  if scale:
    bias = scale * (math.fsum(error / scale for error in errors) / count)
    rmse = scale * math.sqrt(math.fsum((error / scale) ** 2 for error in errors) / count)
  else:
    bias = rmse = 0.0
  p95 = sorted(abs(error) for error in errors)[math.ceil(count * 0.95) - 1]
  return {"count": count, "bias": bias, "rmse": rmse, "p95_absolute_error": p95}


def _merge_intervals(intervals):
  """Merge half-open intervals; integer timestamps let points use [t, t+1)."""
  merged = []
  for start, end in sorted(intervals):
    if merged and start <= merged[-1][1]:
      merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
    else:
      merged.append((start, end))
  return merged


def _mask_axis(rows, profile):
  reasons = [set() for _ in rows]
  stable_since = None
  previous = None
  pre_intervals = []
  gaps = []
  epoch_starts = []
  epoch_start = rows[0].mono_ns
  for index, item in enumerate(rows):
    current = reasons[index]
    if previous is None or item.epoch != previous.epoch:
      stable_since = None
      epoch_start = item.mono_ns
    epoch_starts.append(epoch_start)
    if previous is not None and item.mono_ns - previous.mono_ns > profile.max_gap_ns:
      current.add("signal_gap")
      gaps.append((previous.mono_ns + 1, item.mono_ns + 1))
    if not item.active:
      current.add("control_inactive")
    if not item.valid:
      current.add("invalid_signal")
    if not item.recovery_ready:
      current.add("recovery_not_ready")
    if item.context.get("saturated") is True or item.context.get("output_limited") is True:
      current.add("output_limited")
    if item.driver_override is None:
      current.add("intervention_unknown")
    elif item.driver_override:
      current.add("driver_intervention")
      pre_intervals.append((max(0, item.mono_ns - profile.pre_margin_ns), item.mono_ns + 1))
    _, measurement_reason = _error(item)
    if measurement_reason:
      current.add(measurement_reason)
    if current:
      stable_since = None
    else:
      if stable_since is None:
        stable_since = item.mono_ns
      if item.mono_ns - stable_since < profile.recovery_ns:
        current.add("recovery")
    previous = item

  merged_pre = _merge_intervals(pre_intervals)
  pre_starts = [interval[0] for interval in merged_pre]
  for index, item in enumerate(rows):
    position = bisect_right(pre_starts, item.mono_ns) - 1
    if position >= 0 and item.mono_ns < merged_pre[position][1] and item.driver_override is not True:
      reasons[index].add("intervention_pre_margin")

  # A bad observed state remains uncertain until the next observation. Derived
  # dependency exclusions must not recursively extend this base uncertainty.
  unsafe = list(gaps)
  for index, item in enumerate(rows):
    if reasons[index]:
      end = rows[index + 1].mono_ns if index + 1 < len(rows) else item.mono_ns + 1
      unsafe.append((item.mono_ns, end))
  merged_unsafe = _merge_intervals(unsafe)
  unsafe_starts = [interval[0] for interval in merged_unsafe]
  if profile.dependency_ns:
    for index, item in enumerate(rows):
      if reasons[index]:
        continue
      window_start = item.mono_ns - profile.dependency_ns
      if window_start < epoch_starts[index]:
        reasons[index].add("dependency_unobserved")
      position = bisect_right(unsafe_starts, item.mono_ns) - 1
      if position >= 0 and merged_unsafe[position][1] > window_start:
        reasons[index].add("dependency_window")
  return reasons


def _summarize_epoch(records, boundary, max_gap_ns):
  raw_errors, eligible_errors = [], []
  provisional = excluded = 0
  observed_duration = eligible_duration = 0
  limit_observation = {"saturated_known_count": 0, "output_limited_known_count": 0,
                       "fully_known_count": 0, "unknown_count": 0, "limited_count": 0}
  intervals = []
  active_intervals = {}
  previous, previous_eligible = None, False
  for item, reasons in records:
    saturated_known = type(item.context.get("saturated")) is bool
    limited_known = type(item.context.get("output_limited")) is bool
    limit_observation["saturated_known_count"] += saturated_known
    limit_observation["output_limited_known_count"] += limited_known
    limit_observation["fully_known_count"] += saturated_known and limited_known
    limit_observation["unknown_count"] += not (saturated_known and limited_known)
    limit_observation["limited_count"] += item.context.get("saturated") is True or item.context.get("output_limited") is True
    eligible = item.mono_ns <= boundary and not reasons
    if previous is not None:
      elapsed = item.mono_ns - previous.mono_ns
      if elapsed <= max_gap_ns:
        observed_duration += elapsed
        if eligible and previous_eligible:
          eligible_duration += elapsed
    error, _ = _error(item)
    if error is not None:
      raw_errors.append(error)
    if item.mono_ns > boundary:
      provisional += 1
      current_reasons = set()
    elif reasons:
      excluded += 1
      current_reasons = reasons
    else:
      eligible_errors.append(error)
      current_reasons = set()
    for reason in list(active_intervals):
      if reason not in current_reasons:
        intervals.append(active_intervals.pop(reason))
    for reason in sorted(current_reasons):
      if reason not in active_intervals:
        active_intervals[reason] = {"reason": reason, "start_ns": item.mono_ns,
                                    "end_ns": item.mono_ns, "sample_count": 0}
      active_intervals[reason]["end_ns"] = item.mono_ns
      active_intervals[reason]["sample_count"] += 1
    previous, previous_eligible = item, eligible
  intervals.extend(active_intervals.values())
  intervals.sort(key=lambda entry: (entry["start_ns"], entry["reason"]))
  return {
    "raw_metrics": _metrics(raw_errors),
    "eligible_metrics": _metrics(eligible_errors),
    "sample_count": len(records),
    "eligible_count": len(eligible_errors),
    "excluded_count": excluded,
    "provisional_count": provisional,
    "observed_duration_ns": observed_duration,
    "eligible_duration_ns": eligible_duration,
    "output_limit_observation": limit_observation,
    "scope_limitations": ["output_limit_state_unknown"] if limit_observation["unknown_count"] else [],
    "exclusions": intervals,
  }


def analyze(observations: Iterable[Observation], profile: AnalysisProfile,
            finalized_through_ns: int | None = None) -> dict:
  """Analyze bounded monotonic inputs without assuming the tail is finalized.

  Exclusion interval endpoints are inclusive timestamps of consecutive observed
  samples with a reason, not interpolated claims about unobserved measurements.
  Raw metrics cover finite measurements including excluded/provisional samples.
  The caller must provide a new epoch ID for each settings/code/model event.
  """
  if not isinstance(profile, AnalysisProfile):
    raise ValueError("profile must be an AnalysisProfile")
  if finalized_through_ns is not None and (type(finalized_through_ns) is not int or finalized_through_ns < 0):
    raise ValueError("finalized_through_ns must be a nonnegative integer or None")
  axis_rows = {axis: [] for axis in AXES}
  closed_epochs = {axis: set() for axis in AXES}
  count = 0
  for item in observations:
    if count >= profile.max_samples:
      raise ValueError("max_samples exceeded; input was not silently truncated")
    _validate_observation(item)
    rows = axis_rows[item.axis]
    if rows:
      previous = rows[-1]
      if item.mono_ns <= previous.mono_ns:
        raise ValueError("mono_ns must strictly increase per axis")
      if item.epoch != previous.epoch:
        closed_epochs[item.axis].add(previous.epoch)
        if item.epoch in closed_epochs[item.axis]:
          raise ValueError("a closed epoch ID cannot reappear on the same axis")
    rows.append(item)
    count += 1
  safe_ends = [rows[-1].mono_ns - profile.pre_margin_ns - profile.dependency_ns
               for rows in axis_rows.values() if rows]
  safe_end = max(-1, min(safe_ends)) if safe_ends else None
  if finalized_through_ns is not None and (safe_end is None or finalized_through_ns > safe_end):
    raise ValueError("finalized_through_ns exceeds the observed safe boundary")
  boundary = finalized_through_ns if finalized_through_ns is not None else safe_end
  result = {"schema_version": 1, "profile_id": profile.profile_id,
            "finalized_through_ns": boundary,
            "raw_metrics_scope": "all_finite_measurements_including_excluded_and_provisional",
            "eligible_metrics_scope": "finalized_state_and_dependency_eligible",
            "exclusion_interval_semantics": "consecutive_observed_samples_inclusive_endpoints",
            "axes": {}}
  for axis, rows in axis_rows.items():
    if not rows:
      continue
    reasons = _mask_axis(rows, profile)
    epochs = {}
    for item, item_reasons in zip(rows, reasons):
      epochs.setdefault(item.epoch, []).append((item, item_reasons))
    result["axes"][axis] = {"epochs": {epoch: _summarize_epoch(records, boundary, profile.max_gap_ns)
                                        for epoch, records in epochs.items()}}
  return result
