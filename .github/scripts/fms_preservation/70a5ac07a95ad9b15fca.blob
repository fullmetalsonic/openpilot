"""Generate offline experiments from closed logs, never from inferred settings.

Original samples stay in the native files. Explicit split gaps remain in those
files and are counted; selected blocks retain their masked anchors. Published
planner values and sampled sidecars do not establish a vehicle improvement.
"""
from bisect import bisect_left, bisect_right
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re

from .analysis import AnalysisProfile
from .candidate_report import (MAX_INPUT_BYTES, MAX_PID_ANALYSIS_ROWS, MAX_PID_INPUT_BYTES,
                               _code_hashes, _read, calculate_bundle, load_candidate_bundle)
from .candidates import _profile, candidate_capabilities
from .evidence import hash_file


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_LIMIT = 32 * 1024 * 1024
_RECORDED = {"LateralTorqueAccelFactor": ("tracking", "lateral_accel_independent", "m/s^2", "torque_response"),
             "SteerRatioRate": ("geometry_reference", "curvature_independent", "1/m", "geometry"),
             "CustomSR": ("geometry_reference", "curvature_independent", "1/m", "manual_geometry")}
_PID_KEYS = {"LateralTorqueKpV", "LateralTorqueKiV", "LateralTorqueKf"}


def _typed_capabilities(config, capabilities):
  from .policy_observations import policy_capabilities, projection_capabilities
  caps = {row["key"]: row for row in capabilities}
  for key, spec in config["objectives"].items():
    kind = spec.get("reference", {}).get("type")
    if kind == "fms_policy_point_preset" and key in policy_capabilities():
      caps[key] = {**caps[key], **policy_capabilities()[key]}
    elif kind == "fms_frozen_plan_preset" and key in projection_capabilities():
      caps[key] = {**caps[key], **projection_capabilities()[key]}
  return caps


def _encoded(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
  return hashlib.sha256(_encoded(value)).hexdigest()


def _positive(value, name, maximum):
  if type(value) is not int or not 1 <= value <= maximum:
    raise ValueError(f"invalid_{name}")


def _fields(value, names):
  if type(value) is not dict or set(value) != set(names):
    raise ValueError("invalid_log_objective_fields")


def _finite(value):
  try:
    valid = type(value) in (int, float) and math.isfinite(value)
  except OverflowError:
    valid = False
  if not valid:
    raise ValueError("nonfinite_log_objective")
  return value



def _sampling_policy(config):
  if type(config) is not dict:
    raise ValueError("invalid_log_objective_config")
  if "sampling" not in config:
    return None
  policy = config["sampling"]
  if (type(policy) is not dict or set(policy) != {"version", "period_ns"}
      or policy["version"] not in {"native_static_response_5hz_v1", "native_static_5hz_policy_points_v1"}
      or type(policy["period_ns"]) is not int or policy["period_ns"] != 200_000_000):
    raise ValueError("invalid_sampling_policy")
  requested = config.get("objectives")
  if type(requested) is not dict or not requested:
    raise ValueError("invalid_sampling_objectives")
  for key, spec in requested.items():
    if type(spec) is not dict or type(spec.get("reference")) is not dict:
      raise ValueError("invalid_sampling_objective_mapping")
    ref = spec["reference"]
    if policy["version"] == "native_static_5hz_policy_points_v1" and ref.get("type") in {"fms_policy_point_preset", "fms_frozen_plan_preset"}:
      from .policy_observations import validate_preset_reference, validate_projection_preset_reference, policy_capabilities, projection_capabilities
      validator, caps = (validate_preset_reference, policy_capabilities()) if ref["type"] == "fms_policy_point_preset" else (validate_projection_preset_reference, projection_capabilities())
      validator(ref)
      if key not in caps or spec.get("kind") != "policy_reference":
        raise ValueError("invalid_sampling_objective_mapping")
      continue
    if key in _RECORDED:
      if (set(ref) != {"type", "signal", "unit"} or ref["type"] != "recorded_signal"
          or (spec.get("kind"), ref["signal"], ref["unit"]) != _RECORDED[key][:3]):
        raise ValueError("invalid_sampling_objective_mapping")
    elif key in {f"CruiseMaxVals{i}" for i in range(7)} and spec.get("kind") == "policy_reference":
      from .goals import validate_preset_reference
      validate_preset_reference(ref)
    else:
      raise ValueError("sampling_policy_unsupported_objectives")
  return dict(policy)


def _sampling_clock_basis(config):
  kinds = {spec["reference"]["type"] for spec in config["objectives"].values()}
  return "response_or_anchor" if len(kinds) > 1 else "anchor_time" if "fms_policy_preset" in kinds else "response_time"


def _static_projection(config, capabilities):
  if _sampling_policy(config) is None:
    return None
  caps = _typed_capabilities(config, capabilities)
  required = {name for key in config["objectives"] for name in caps[key]["required_signals"]} | {"v_ego"}
  return {"version": "native_static_rows_v1", "signals": sorted(required)}



def _sample_response_indices(rows, policy, *, clock_basis="response_time"):
  if policy is None:
    return set(range(len(rows))), None
  if clock_basis not in {"response_time", "anchor_time", "response_or_anchor"}:
    raise ValueError("invalid_sampling_clock_basis")
  seen_responses, seen_anchors, occupied, selected = set(), set(), set(), set()
  for index, row in enumerate(rows):
    provenance = row.get("lateral_observation", {}).get("provenance", {})
    epoch, response, anchor = provenance.get("epoch_id"), provenance.get("response_mono_ns"), row.get("mono_ns")
    valid_response = (type(epoch) is str and bool(epoch) and type(response) is int and response >= 0
                      and (anchor is None or type(anchor) is int and response <= anchor))
    if clock_basis != "anchor_time" and valid_response:
      token, stamp = (epoch, response), response
      if token in seen_responses:
        continue
      seen_responses.add(token)
    elif clock_basis != "response_time" and type(anchor) is int and anchor >= 0:
      token, stamp = (epoch, anchor), anchor
      if token in seen_anchors:
        continue
      seen_anchors.add(token)
    else:
      continue
    bucket = (epoch, stamp // policy["period_ns"])
    if bucket not in occupied:
      occupied.add(bucket)
      selected.add(index)
  return selected, {"version": policy["version"], "raw_anchor_count": len(rows),
    "unique_response_count": len(seen_responses), "anchor_fallback_count": len(seen_anchors),
    "selected_count": len(selected), "clock_basis": clock_basis}



def _objectives(config, capabilities):
  if type(config) is not dict or not {"schema_version", "analysis_profile", "objectives"} <= set(config) <= {"schema_version", "analysis_profile", "objectives", "sampling"}:
    raise ValueError("invalid_log_objective_fields")
  if type(config["schema_version"]) is not int or config["schema_version"] != 1:
    raise ValueError("invalid_log_objective_version")
  _fields(config["analysis_profile"], {"profile_id", "pre_margin_ns", "recovery_ns", "max_gap_ns", "dependency_ns", "max_samples"})
  mask = AnalysisProfile(**config["analysis_profile"])
  if not _IDENTIFIER.fullmatch(mask.profile_id) or len(mask.profile_id) > 64:
    raise ValueError("invalid_mask_profile_identifier")
  requested = config["objectives"]
  caps = _typed_capabilities(config, capabilities)
  if type(requested) is not dict or len(requested) > 47 or set(requested) - set(caps):
    raise ValueError("invalid_log_objective_keys")
  for key, spec in requested.items():
    _fields(spec, {"id", "kind", "reference"})
    if type(spec["id"]) is not str or _IDENTIFIER.fullmatch(spec["id"]) is None:
      raise ValueError("log_objective_kind_not_supported")
    reference = spec["reference"]
    if type(reference) is dict and reference.get("type") == "manual_trial":
      _fields(reference, {"type", "signal", "unit"})
      from .calibration_review import TIMING_KEYS
      if (len(requested) != 1 or key not in TIMING_KEYS or spec["kind"] != "manual_trial" or reference["unit"] != "m/s^2"
          or reference["signal"] != ("aTarget" if key == "LongActuatorDelay" else "desired_lateral_accel")):
        raise ValueError("invalid_manual_trial_objective")
      continue
    if type(reference) is dict and reference.get("type") == "recorded_pid_target":
      _fields(reference, {"type", "signal", "unit", "trace_schema"})
      if (key not in _PID_KEYS or spec["kind"] != "tracking" or reference["signal"] != "desired_lateral_accel"
          or reference["unit"] != "m/s^2" or reference["trace_schema"] != "torque_pid_cycle_v1"):
        raise ValueError("log_pid_reference_mapping_not_supported")
      continue
    if type(reference) is dict and reference.get("type") in {"fms_policy_point_preset", "fms_frozen_plan_preset"}:
      from .policy_observations import validate_preset_reference, validate_projection_preset_reference
      (validate_preset_reference if reference["type"] == "fms_policy_point_preset" else validate_projection_preset_reference)(reference)
      expected_method = "fms_policy_state_conditioned_one_step_v1" if reference["type"] == "fms_policy_point_preset" else "fms_frozen_longitudinal_plan_projection_v1"
      if spec["kind"] != "policy_reference" or caps[key].get("method_id") != expected_method:
        raise ValueError("log_point_reference_mapping_not_supported")
      continue
    if type(reference) is dict and reference.get("type") == "recorded_signal":
      _fields(reference, {"type", "signal", "unit"})
      expected = _RECORDED.get(key)
      if (expected is None or (spec["kind"], reference["signal"], reference["unit"]) != expected[:3]
          or reference["unit"] != caps[key]["output_unit"]):
        raise ValueError("log_recorded_reference_mapping_not_supported")
      continue
    if type(reference) is dict and reference.get("type") == "fms_policy_preset":
      from .goals import validate_preset_reference
      validate_preset_reference(reference)
      if key not in {f"CruiseMaxVals{i}" for i in range(7)} or spec["kind"] != "policy_reference" or caps[key]["output_unit"] != "m/s^2":
        raise ValueError("log_preset_reference_mapping_not_supported")
      continue
    if spec["kind"] != "policy_reference":
      raise ValueError("log_objective_kind_not_supported")
    _fields(reference, {"type", "input_signal", "input_unit", "unit", "knots"})
    if (reference["type"] != "policy_curve" or reference["input_signal"] != "v_ego" or reference["input_unit"] != "m/s"
        or reference["unit"] != caps[key]["output_unit"] or reference["unit"] is None):
      raise ValueError("log_policy_reference_mapping_not_supported")
    knots = reference["knots"]
    if type(knots) is not list or not 2 <= len(knots) <= 64:
      raise ValueError("log_policy_curve_budget")
    previous = None
    for pair in knots:
      if type(pair) is not list or len(pair) != 2:
        raise ValueError("invalid_log_policy_curve")
      x, _ = (_finite(value) for value in pair)
      if previous is not None and x <= previous:
        raise ValueError("nonincreasing_log_policy_curve")
      previous = x
  _sampling_policy(config)
  return mask


def _curve(knots, x):
  if x < knots[0][0] or x > knots[-1][0]:
    return None
  index = min(bisect_right([pair[0] for pair in knots], x), len(knots) - 1)
  if index == 0:
    return knots[0][1]
  left, right = knots[index - 1], knots[index]
  return left[1] + (right[1] - left[1]) * (x - left[0]) / (right[0] - left[0])


def _epoch_rows(rows, history, maximum):
  """Assign each actual anchor once; settings outside brackets remain unknown."""
  groups, known = {}, history.get("epochs", []) if history.get("state") == "linked" else []
  for row in rows:
    groups.setdefault(row["route_id"], []).append(row)
  result = []
  for route, anchors in groups.items():
    anchors.sort(key=lambda row: row["mono_ns"])
    if any(a["mono_ns"] >= b["mono_ns"] for a, b in zip(anchors, anchors[1:])):
      raise ValueError("duplicate_or_overlapping_native_anchors")
    intervals = sorted((item for item in known if item["route"] == route), key=lambda item: item["start_ns"])
    if any(a["end_ns"] >= b["start_ns"] for a, b in zip(intervals, intervals[1:])):
      raise ValueError("overlapping_observed_setting_epochs")
    selected = {}
    unknown, previous = [], None
    def append(item, items):
      if not items:
        return
      if len(result) >= maximum:
        raise ValueError("log_epoch_budget_exceeded")
      item = copy.deepcopy(item) if item else {"id": f"unknown-{len(result):04d}", "settings_raw": {}, "dependencies": {},
                                             "completeness": "unknown", "consumer_evidence": [], "route": route, "boot_id": None}
      item["start_ns"], item["end_ns"], item["rows"] = items[0]["mono_ns"], items[-1]["mono_ns"], items
      result.append(item)
    starts = [item["start_ns"] for item in intervals]
    for row in anchors:
      index = bisect_right(starts, row["mono_ns"]) - 1
      item = intervals[index] if index >= 0 and row["mono_ns"] <= intervals[index]["end_ns"] else None
      if item is None:
        if previous is not None:
          append(previous, selected.pop(previous["id"]))
          previous = None
        unknown.append(row)
      else:
        if unknown:
          append(None, unknown)
          unknown = []
        if previous is not None and previous["id"] != item["id"]:
          append(previous, selected.pop(previous["id"]))
        selected.setdefault(item["id"], []).append(row)
        previous = item
    if previous is not None:
      append(previous, selected.pop(previous["id"]))
    append(None, unknown)
  return sorted(result, key=lambda item: (item["end_ns"], item["start_ns"], item["id"]))


def _recorded_support(row, epoch, extracted, branch):
  metadata = row.get("lateral_observation", {})
  provenance = metadata.get("provenance", {})
  identity = epoch.get("vehicle_identity", {})
  response = provenance.get("response_mono_ns")
  if (metadata.get("epoch_kind") != "recorded_cp_span" or metadata.get("recording_schema_verified") is not False
      or metadata.get("availability", {}).get(branch, {}).get("available") is not True
      or type(response) is not int or not 0 <= response <= row["mono_ns"]
      or identity.get("state") != "observed" or provenance.get("config_sha256") != identity.get("config_sha256")
      or provenance.get("consumer_mapping_verified") is not False or provenance.get("parameter_application_ack") is not False):
    return False
  source = provenance.get("source_id")
  headers = [item for item in extracted.get("initial_snapshots", []) if item["source_id"] == source
             and item.get("event_valid") is True and item["mono_ns"] <= response
             and item.get("recorded_car_params", {}).get("state") == "observed"
             and item["recorded_car_params"].get("config_sha256") == identity["config_sha256"]]
  if not headers:
    return False
  receipts = {item["id"]: item for item in extracted["source_receipts"]}
  if source not in receipts or row["source_id"] not in receipts:
    return False
  if source != row["source_id"]:
    first, last = receipts[source], receipts[row["source_id"]]
    if (first.get("route") != epoch["route"] or last.get("route") != epoch["route"]
        or type(first.get("segment")) is not int or type(last.get("segment")) is not int or first["segment"] > last["segment"]):
      return False
    chain = sorted((item for item in receipts.values() if item.get("route") == epoch["route"]
                    and type(item.get("segment")) is int and first["segment"] <= item["segment"] <= last["segment"]),
                   key=lambda item: item["segment"])
    if [item["segment"] for item in chain] != list(range(first["segment"], last["segment"] + 1)):
      return False
    if any(type(a.get("mono_max_ns")) is not int or type(b.get("mono_min_ns")) is not int
           or not 0 <= b["mono_min_ns"] - a["mono_max_ns"] <= extracted["join_max_age_ns"] for a, b in zip(chain, chain[1:])):
      return False
  return True


def _standard_torque_event(epoch, row, profile, timeline):
  stamps = [signal["source_mono_ns"] for key, signal in row["signals"].items()
            if key in {"sent_torque", "lateral_accel_independent"}]
  support = min(stamps, default=row["mono_ns"])
  times, events = timeline
  position = bisect_right(times, support) - 1
  if position < 0:
    return None
  # max() selected the first arrival when several events had the same timestamp.
  event = events[bisect_left(times, times[position])]
  context = event.get("context", {})
  if (row["mono_ns"] - event["produced_mono_ns"] > profile["max_lag_ns"]
      or event.get("phase") != "sampled_use" or event.get("boot_id") != epoch.get("boot_id")
      or context.get("active") is not True or context.get("torque_ff_path") != "linear_standard"
      or any(context.get(key) is not False for key in ("nnff", "nnff_lite", "nnff_model_selected"))
      or event.get("identity_version") != 1 or event.get("vehicle_identity", {}).get("state") != "observed"
      or event.get("vehicle_identity", {}).get("config_sha256") != epoch.get("vehicle_identity", {}).get("config_sha256")
      or any(value is None or epoch.get("code_hashes", {}).get(path) != value for path, value in event.get("source_hashes", {}).items())
      or not event.get("source_hashes")):
    return None
  return event


def _pack(epoch, extracted, history, config, profile, capabilities):
  requested = config["objectives"]
  sampling = _sampling_policy(config)
  selected_indices, audit = _sample_response_indices(epoch["rows"], sampling, clock_basis=_sampling_clock_basis(config))
  selected_times = {epoch["rows"][index]["mono_ns"] for index in selected_indices}
  full_unavailable = {key: Counter() for key in requested}
  caps = _typed_capabilities(config, capabilities)
  axes = {caps[key]["axis"] for key in requested}
  raw_reasons = {row["mono_ns"]: {reason for axis in axes for reason in row["excluded_reasons"][axis]} for row in epoch["rows"]}
  required = {name for key in requested for name in caps[key]["required_signals"]} | ({"v_ego"} if requested else set())
  # Objective IDs remain separate. Identical independently specified policy
  # functions need one sampled reference, rather than seven copies per anchor.
  recorded = {key for key, spec in requested.items() if spec["reference"]["type"] == "recorded_signal"}
  presets = {key for key, spec in requested.items() if spec["reference"]["type"] == "fms_policy_preset"}
  point_keys = {key for key, spec in requested.items() if spec["reference"]["type"] in {"fms_policy_point_preset", "fms_frozen_plan_preset"}}
  scoped_keys = recorded | presets | point_keys
  reference_names = {key: spec["reference"]["signal"] if key in recorded else f"policy_reference_{_digest([key, spec['reference']] if key in point_keys else spec['reference'])}"
                     for key, spec in requested.items()}
  references = {reference_names[key]: spec["reference"] for key, spec in requested.items()}
  if len(required | set(reference_names.values())) > 64:
    raise ValueError("generated_log_signal_budget_exceeded")
  objectives = {key: {"id": spec["id"], "kind": spec["kind"], "source": "independent_measurement" if key in recorded else "user_specified",
                      "unit": caps[key]["output_unit"] if key in presets | point_keys else spec["reference"]["unit"], "reference_signal": reference_names[key]} for key, spec in requested.items()}
  for key in presets | point_keys:
    objectives[key]["target_raw"] = requested[key]["reference"]["settings_raw"][key]
    if key in point_keys:
      objectives[key]["method_id"] = caps[key]["method_id"]
  begin, end = epoch["start_ns"], epoch["end_ns"]
  cuts = [begin + (end - begin) // 3, begin + 2 * (end - begin) // 3]
  partitions = {name: [] for name in ("fit", "select", "holdout")}
  margin = max(profile["dependency_margin_ns"], config["analysis_profile"]["dependency_ns"])
  exclusions = Counter()
  seen_responses = set()
  factor_verified = False
  torque_events = sorted((event for event in epoch["consumer_evidence"] if event["consumer_id"] == "controls.torque"),
                         key=lambda item: item["produced_mono_ns"]) if "LateralTorqueAccelFactor" in recorded else []
  torque_timeline = ([event["produced_mono_ns"] for event in torque_events], torque_events)
  from .policy_observations import extract_policy_points, extract_projection_point, policy_family_for_key, policy_reference, projection_reference
  point_events = []
  if point_keys:
    identity = epoch.get("vehicle_identity", {})
    for event in epoch["consumer_evidence"]:
      if (event["boot_id"] == epoch.get("boot_id") and identity.get("state") == "observed"
          and event.get("vehicle_identity", {}).get("config_sha256") == identity.get("config_sha256")):
        points, projection = extract_policy_points(event), extract_projection_point(event)
        if points or projection is not None:
          point_events.append((event, _digest(event), points, projection))
  point_seen = set()
  for row in epoch["rows"]:
    if recorded and sampling is None and not point_keys:
      provenance = row.get("lateral_observation", {}).get("provenance", {})
      response = provenance.get("response_mono_ns")
      if type(response) is int:
        token = (provenance.get("epoch_id"), response)
        if token in seen_responses:
          exclusions["recorded_response_duplicate"] += 1
          continue
        seen_responses.add(token)
      elif len(recorded) == len(requested):
        exclusions["recorded_response_unavailable"] += 1
        continue
    if any(abs(row["mono_ns"] - cut) <= margin for cut in cuts):
      exclusions.update({"split_dependency_support", *(reason for axis in axes for reason in row["excluded_reasons"][axis])})
      raw_reasons[row["mono_ns"]].add("split_dependency_support")
      continue
    split = "fit" if row["mono_ns"] < cuts[0] else "select" if row["mono_ns"] < cuts[1] else "holdout"
    partitions[split].append(row)
  receipts = {item["id"]: item for item in extracted["source_receipts"]}
  intervals = epoch.get("eligible_intervals", [{"start_ns": begin, "end_ns": end}])
  interval_starts = [interval["start_ns"] for interval in intervals]
  blocks, splits = [], {name: [] for name in partitions}
  for split, rows in partitions.items():
    if not rows:
      continue
    block_start, block_end = rows[0]["mono_ns"], rows[-1]["mono_ns"]
    samples = []
    for row in rows:
      stamp = row["mono_ns"]
      unavailable = {key: set() for key in scoped_keys}
      excluded = {reason for axis in axes for reason in row["excluded_reasons"][axis]}
      interval_index = bisect_right(interval_starts, stamp) - 1
      if epoch["completeness"] == "observed" and (interval_index < 0 or stamp > intervals[interval_index]["end_ns"]):
        excluded.add("settings_observation_uncertain")
      signals = {name: row["signals"][name] for name in required if name in row["signals"]}
      for key in recorded:
        name = reference_names[key]
        valid = _recorded_support(row, epoch, extracted, _RECORDED[key][3])
        reference = row["signals"].get(name)
        if (not valid or type(reference) is not dict or reference.get("source") != "independent_measurement"
            or reference.get("unit") != _RECORDED[key][2]
            or reference.get("source_mono_ns") != row.get("lateral_observation", {}).get("provenance", {}).get("response_mono_ns")):
          signals.pop(name, None)
          unavailable[key].add("recorded_reference_unlinked")
          exclusions["recorded_reference_unlinked"] += 1
        if key == "LateralTorqueAccelFactor":
          event = _standard_torque_event(epoch, row, profile, torque_timeline) if valid and name in signals else None
          if event is None:
            signals.pop("lateral_accel_offset", None)
            unavailable[key].add("factor_mapping_unverified")
            exclusions["factor_mapping_unverified"] += 1
          else:
            factor_verified = True
            signals["lateral_accel_offset"] = {"value": 0., "unit": "m/s^2", "source": "controller_derived",
                                               "source_mono_ns": event["produced_mono_ns"]}
      if epoch["completeness"] == "observed" and interval_index >= 0:
        if recorded:
          interval = intervals[interval_index]
          removed = [name for name, signal in signals.items()
                     if signal["source_mono_ns"] - margin < interval["start_ns"] or stamp + margin > interval["end_ns"]]
          for name in removed:
            signals.pop(name)
          if removed:
            exclusions["recorded_dependency_unavailable"] += 1
        support_start = min((signal["source_mono_ns"] for signal in signals.values()), default=stamp) - margin
        interval = intervals[interval_index]
        if support_start < interval["start_ns"] or stamp + margin > interval["end_ns"]:
          excluded.add("settings_dependency_uncertain")
      if recorded:
        for name in [name for name, signal in signals.items() if signal["source_mono_ns"] < block_start + margin]:
          signals.pop(name)
      if any(signal["source_mono_ns"] < block_start + margin for signal in signals.values()) or stamp < block_start + margin or stamp > block_end - margin:
        excluded.add("split_dependency_support")
      speed = row["signals"].get("v_ego")
      wrappers, projection_wrapper = {}, None
      if point_keys and not excluded:
        for event, digest, points, projection in point_events:
          produced = event["produced_mono_ns"]
          if not block_start + margin <= produced <= stamp or stamp - produced > profile["max_lag_ns"]:
            continue
          for family, point in points.items():
            if (digest, family) not in point_seen and family not in wrappers:
              wrappers[family] = {"consumer_event_sha256": digest, "point": point}
          if projection is not None and projection_wrapper is None and (digest, "frozen_plan_projection") not in point_seen:
            projection_wrapper = {"consumer_event_sha256": digest, "point": projection}
      for key in point_keys:
        ref = requested[key]["reference"]
        projection = ref["type"] == "fms_frozen_plan_preset"
        wrapper = projection_wrapper if projection else wrappers.get(policy_family_for_key(key))
        if wrapper is not None:
          value = (projection_reference if projection else policy_reference)(key, wrapper["point"], ref)
          produced = next(event["produced_mono_ns"] for event, digest, _, _ in point_events if digest == wrapper["consumer_event_sha256"])
          signals[reference_names[key]] = {"value": value, "unit": caps[key]["output_unit"], "source": "user_specified", "source_mono_ns": produced}
      for reference_name, reference in references.items():
        if reference["type"] in {"recorded_signal", "fms_policy_point_preset", "fms_frozen_plan_preset"}:
          continue
        if reference["type"] == "fms_policy_preset":
          mode = signals.get("accel_mode_factor")
          knots = [[point, reference["settings_raw"][f"CruiseMaxVals{i}"] * .01] for i, point in enumerate((0, 10, 40, 60, 80, 110, 140))]
          value = (_curve(knots, min(140., max(0., speed["value"] * 3.6))) * mode["value"]
                   if speed and mode and mode["unit"] == "1" else None)
          unit = "m/s^2"
        else:
          value = _curve(reference["knots"], speed["value"]) if speed else None
          unit = reference["unit"]
        if value is not None:
          signals[reference_name] = {"value": value, "unit": unit, "source": "user_specified", "source_mono_ns": stamp}
      exclusions.update(excluded)
      for key in scoped_keys:
        needed = {**caps[key]["required_signals"], reference_names[key]: objectives[key]["unit"]}
        if any(name not in signals or signals[name]["unit"] != unit for name, unit in needed.items()):
          if not unavailable[key]:
            unavailable[key].add("recorded_dependency_unavailable")
      sample = {"mono_ns": stamp, "signals": signals, "excluded_reasons": sorted(excluded)}
      scoped = {key: sorted(reasons) for key, reasons in unavailable.items() if reasons}
      if scoped:
        sample["unavailable_for"] = scoped
      if wrappers:
        sample["policy_observations"] = copy.deepcopy(wrappers)
      if projection_wrapper is not None:
        sample["projection_observation"] = copy.deepcopy(projection_wrapper)
      raw_reasons[stamp].update(excluded)
      for key, reasons in unavailable.items():
        full_unavailable[key].update(reasons)
        raw_reasons[stamp].update(reasons)
      if sampling is None or stamp in selected_times or wrappers or projection_wrapper is not None:
        sample["signals"] = copy.deepcopy(signals)
        samples.append(sample)
        point_seen.update((wrapper["consumer_event_sha256"], family) for family, wrapper in wrappers.items())
        if projection_wrapper is not None:
          point_seen.add((projection_wrapper["consumer_event_sha256"], "frozen_plan_projection"))
    final = min(block_end, max(receipts[row["source_id"]]["finalized_through_ns"] for row in rows))
    # Finalized bounds cannot erase raw samples; a wholly provisional block is
    # retained with an empty usable prefix and its explicit finalization masks.
    final = max(block_start, final)
    block_id = f"{split}-{len(blocks):04d}"
    blocks.append({"id": block_id, "route_id": epoch["route"], "start_ns": block_start, "end_ns": block_end,
                   "finalized_through_ns": final, "mask_version": extracted["mask_version"], "samples": samples})
    splits[split].append(block_id)
  code_hashes = dict(epoch.get("code_hashes", {}))
  relative = "openpilot/selfdrive/carrot/control_advisor/log_candidates.py"
  code_hashes[relative] = hash_file(Path(__file__), MAX_INPUT_BYTES)[0]
  raw = {key: int(value) if type(value) in (int, float) and math.isfinite(value) and value == int(value) and -1000000 <= value <= 1000000 else None
         for key, value in epoch["settings_raw"].items()}
  source_ids = {row["source_id"] for row in epoch["rows"]}
  if recorded:
    source_ids.update(row.get("lateral_observation", {}).get("provenance", {}).get("source_id") for row in epoch["rows"])
  sources = [receipt for receipt in extracted["source_receipts"] if receipt["id"] in source_ids]
  dependencies = copy.deepcopy(epoch["dependencies"])
  if profile.get("profile_id") == "custom_sr_v1":
    dependencies["manual_ratio_quality_v1"] = True
  if "LateralTorqueAccelFactor" in recorded:
    dependencies.pop("torque_ff_path", None)
    dependencies.pop("torque_response_alignment", None)
    if factor_verified:
      dependencies.update(torque_ff_path="linear_standard", torque_response_alignment="declared_aligned")
  consumers = epoch["consumer_evidence"]
  if sampling is not None:
    from .consumer_observer import consumer_ids_for_key
    required_consumers = {consumer for key in requested for consumer in consumer_ids_for_key(key)}
    consumers = [event for event in consumers if event["consumer_id"] in required_consumers]
  evidence = {"schema_version": 1, "source_kind": "recorded", "evidence_id": f"log-epoch-{_digest([epoch['id'], begin, end])[:24]}",
              "artifacts": [{"id": item["id"], "sha256": item["sha256"]} for item in sources],
              "vehicle_context": {"brand": "unknown", "controller_path": "unknown", "mode": "unknown", "code_hashes": code_hashes},
              "epoch": {"id": epoch["id"], "start_ns": begin, "end_ns": end, "settings_raw": raw,
                        "dependencies": dependencies, "completeness": epoch["completeness"], "boot_id": epoch["boot_id"]},
              "splits": splits, "blocks": blocks, "consumer_evidence": copy.deepcopy(consumers),
              "objectives": objectives, "model_artifact": None}
  if consumers:
    from .candidates import encode_consumer_evidence
    evidence["consumer_evidence_block"] = encode_consumer_evidence(evidence["consumer_evidence"])
    evidence["consumer_evidence"] = []
  if "vehicle_identity" in epoch:
    evidence["vehicle_context"]["context"] = {"identity_version": 1, "vehicle_identity": copy.deepcopy(epoch["vehicle_identity"])}
  linked = history.get("state") == "linked" and epoch["completeness"] == "observed"
  reasons = list(dict.fromkeys([*extracted["limitations"], *history.get("reason_codes", []), "masks_scoped_to_requested_axes",
                               "helper_source_provenance_not_recorded", "recording_schema_match_unverified",
                               "clock_binding_witnessed_coorigin" if linked else "settings_epoch_unlinked"]))
  receipt = {"schema_version": 1, "epoch_id": epoch["id"], "source_bytes_verified_at_generation": True, "schema_decoded": True,
             "session_link": "verified_archive_receipt" if linked else "unlinked", "semantic_mapping": "partial",
             "originals_preservation": "verified_session_archive" if linked else "reference_only", "originals_current_availability": "not_checked",
             "sources": [{name: item[name] for name in ("id", "sha256", "bytes")} for item in extracted["source_receipts"]],
             "source_fields": extracted["source_fields"], "objectives": copy.deepcopy(config), "mask_profile_sha256": _digest(config["analysis_profile"]),
             "excluded_counts": dict(exclusions), "reasons": reasons}
  if audit is not None:
    audit["selected_count"] = sum(len(block["samples"]) for block in blocks)
    audit["full_rate_excluded_counts"] = dict(Counter(reason for reasons in raw_reasons.values() for reason in reasons))
    audit["full_rate_unavailable_for"] = {key: dict(reasons) for key, reasons in full_unavailable.items() if reasons}
    receipt["sampling_audit"] = audit
  return evidence, receipt


def _pack_pid(epoch, extracted, history, config, profile, capabilities):
  """Select full original bursts under the separate offline analysis budget."""
  from .pid_burst import COLUMNS, STEP_NS, SOURCE_PATHS, validate_header
  from .journal_snapshot import _time_selected
  from .candidates import _pid_use_receipt
  begin, end = epoch["start_ns"], epoch["end_ns"]
  margin = max(profile["dependency_margin_ns"], config["analysis_profile"]["dependency_ns"])
  cuts = [begin, begin + (end - begin) // 3, begin + 2 * (end - begin) // 3, end]
  raw = copy.deepcopy(epoch["settings_raw"])
  hashes = dict(epoch.get("code_hashes", {}))
  root = Path(__file__).resolve().parents[4]
  adapter_paths = ["openpilot/selfdrive/carrot/control_advisor/" + name + ".py"
                   for name in ("log_candidates", "log_signals", "lateral_observations", "rlog", "vehicle_identity", "analysis", "normalizer")]
  hashes.update({name: hash_file(root / name, MAX_INPUT_BYTES)[0] for name in (*SOURCE_PATHS, *adapter_paths)})
  artifacts = [{"id": item["id"], "sha256": item["sha256"]} for item in extracted["source_receipts"]]
  context = {"brand": "unknown", "controller_path": "torque", "mode": "custom", "code_hashes": hashes}
  if "vehicle_identity" in epoch:
    context["context"] = {"identity_version": 1, "vehicle_identity": copy.deepcopy(epoch["vehicle_identity"])}
  pack = {"schema_version": 1, "source_kind": "recorded", "evidence_id": f"pid-epoch-{_digest([epoch['id'], begin, end])[:24]}",
          "artifacts": artifacts, "vehicle_context": context,
          "epoch": {"id": epoch["id"], "start_ns": begin, "end_ns": end, "settings_raw": raw,
                    "dependencies": copy.deepcopy(epoch["dependencies"]), "completeness": epoch["completeness"], "boot_id": epoch["boot_id"]},
          "splits": {split: [] for split in ("fit", "select", "holdout")}, "blocks": [], "pid_trace_blocks": [],
          "consumer_evidence": copy.deepcopy(epoch["consumer_evidence"]), "model_artifact": None,
          "objectives": {key: {"id": spec["id"], "kind": "tracking", "source": "controller_target", "unit": "m/s^2",
                              "reference_signal": "desired_lateral_accel"} for key, spec in config["objectives"].items()}}
  rows, stamps = epoch["rows"], [row["mono_ns"] for row in epoch["rows"]]
  reasons = Counter()
  finalized = {item["id"]: item["finalized_through_ns"] for item in extracted["source_receipts"]}
  source_ids = [item["id"] for item in extracted["source_receipts"]]
  partitions = []
  for split, left, right in zip(pack["splits"], cuts, cuts[1:]):
    partition = [row for row in rows if left <= row["mono_ns"] <= right
                 and (left == begin or row["mono_ns"] > left + margin)
                 and (right == end or row["mono_ns"] < right - margin)]
    if not partition:
      continue
    block_id = f"pid-{split}"
    block = {"id": block_id, "route_id": epoch["route"], "start_ns": partition[0]["mono_ns"], "end_ns": partition[-1]["mono_ns"],
             "finalized_through_ns": max(partition[0]["mono_ns"], min(partition[-1]["mono_ns"], max(finalized[row["source_id"]] for row in partition))),
             "mask_version": extracted["mask_version"], "samples": [{"mono_ns": row["mono_ns"], "signals": {},
               "excluded_reasons": list(row["excluded_reasons"]["lateral"])} for row in partition]}
    pack["blocks"].append(block)
    pack["splits"][split].append(block_id)
    partitions.append((split, block, partition))
  row_budget = min(MAX_PID_ANALYSIS_ROWS, profile["max_rows"]) // 3
  byte_budget = max(0, (MAX_PID_INPUT_BYTES - len(_encoded(pack)) - 64 * 1024) // 3)
  selection = {"version": 3, "policy": "balanced_time_ordinals", "row_budget": min(MAX_PID_ANALYSIS_ROWS, profile["max_rows"]),
               "max_input_bytes": MAX_PID_INPUT_BYTES, "splits": {}}
  for split, block, partition in partitions:
    block_id = block["id"]
    eligible = sorted((burst for burst in history.get("pid_blocks", []) if burst["state"] == "complete" and burst["rows"]
      and burst["rows"][0]["mono_ns"] >= block["start_ns"] + margin
      and burst["rows"][-1]["mono_ns"] <= block["finalized_through_ns"] - margin),
      key=lambda burst: (burst["rows"][0]["mono_ns"], burst["burst_id"]))
    chosen = _time_selected(eligible, 4)
    audit = {"row_budget": row_budget, "byte_budget": byte_budget, "eligible_count": len(eligible), "selected_burst_ids": [],
             "selected_rows": 0, "selected_bytes": 0, "excluded_counts": {"time_budget": max(0, len(eligible) - len(chosen))}}
    selection["splits"][split] = audit
    if len(chosen) < 2:
      reasons["pid_split_burst_insufficient"] += 2 - len(chosen)
    for burst in chosen:
      native = burst["rows"]
      if native[0]["mono_ns"] < block["start_ns"] + margin or native[-1]["mono_ns"] > block["finalized_through_ns"] - margin:
        continue
      header = validate_header(burst["header"], require_verified=True)
      if header["boot_id"] != epoch["boot_id"] or header["vehicle_identity"].get("config_sha256") != epoch.get("vehicle_identity", {}).get("config_sha256"):
        reasons["pid_effective_use_unlinked"] += 1
        continue
      masked = [row for row in partition if native[0]["mono_ns"] - margin <= row["mono_ns"] <= native[-1]["mono_ns"] + margin
                and row["excluded_reasons"]["lateral"]]
      if masked:
        reasons["pid_physical_masked"] += 1
        continue
      dense, joins, valid = [], [], True
      for item in native:
        index = bisect_right(stamps, item["mono_ns"]) - 1
        row = rows[index] if index >= 0 else {}
        signals, provenance = row.get("signals", {}), row.get("lateral_observation", {}).get("provenance", {})
        output, response = signals.get("sent_torque_raw"), signals.get("lateral_accel_kinematic")
        kinematic = provenance.get("pid_kinematic", {})
        output_source, pose_source = provenance.get("pid_output_source_id"), kinematic.get("source_id")
        if (output is None or response is None or output_source not in source_ids or pose_source not in source_ids
            or kinematic.get("calibration_valid") is not True or kinematic.get("gravity_corrected") is not False
            or provenance.get("config_sha256") != header["vehicle_identity"]["config_sha256"]
            or any(not 0 <= item["mono_ns"] - signal["source_mono_ns"] <= profile["max_lag_ns"] for signal in (output, response))):
          valid = False
          break
        dense.append([item["pid_frame"], item["mono_ns"], *[item[name] for name in COLUMNS]])
        joins.append([output["value"], output["source_mono_ns"], response["value"], response["source_mono_ns"],
                      kinematic["pose_event_mono_ns"], source_ids.index(output_source), source_ids.index(pose_source)])
      if not valid:
        reasons["pid_native_join_unavailable"] += 1
        continue
      configuration = header["configuration"]
      pack["epoch"]["dependencies"].update(pid_trace_schema="torque_pid_cycle_v1", pid_cycle_ns=STEP_NS,
        pid_output_sign="positive_pid_domain", pid_source_clock="original", torque_ff_path="linear_standard",
        pid_nnff_selected=configuration["nnff"], pid_nnff_lite_selected=configuration["nnff_lite"],
        pid_pos_limit=configuration["pos_limit"], pid_neg_limit=configuration["neg_limit"])
      trace = {"id": burst["burst_id"], "block_id": block_id, "header": header, "rows": dense,
               "joins": joins, "join_sources": source_ids, "chunk_refs": copy.deepcopy(burst["chunk_refs"])}
      used_sources = sorted({index for join in joins for index in join[-2:]})
      trace["join_sources"] = [source_ids[index] for index in used_sources]
      remap = {old: new for new, old in enumerate(used_sources)}
      for join in trace["joins"]:
        join[-2:] = [remap[index] for index in join[-2:]]
      trace["use_receipt"] = _pid_use_receipt(pack, trace)
      trace_bytes = len(_encoded(trace)) + 1
      if audit["selected_rows"] + len(dense) > row_budget:
        reasons["pid_split_row_budget"] += 1
        audit["excluded_counts"]["row_budget"] = audit["excluded_counts"].get("row_budget", 0) + 1
        continue
      if audit["selected_bytes"] + trace_bytes > byte_budget:
        reasons["pid_split_byte_budget"] += 1
        audit["excluded_counts"]["byte_budget"] = audit["excluded_counts"].get("byte_budget", 0) + 1
        continue
      pack["pid_trace_blocks"].append(trace)
      audit["selected_burst_ids"].append(burst["burst_id"])
      audit["selected_rows"] += len(dense)
      audit["selected_bytes"] += trace_bytes
  pack["epoch"]["dependencies"]["pid_selection"] = selection
  # Transport quantization is supplied only by the verified native output-pair
  # adapter. An absent contract stays absent and the PID backend blocks it.
  transport = _native_pid_transport(epoch, extracted)
  if transport is not None:
    pack["epoch"]["dependencies"].update(transport)
  receipt = {"schema_version": 1, "epoch_id": epoch["id"], "source_bytes_verified_at_generation": True,
    "schema_decoded": True, "session_link": "verified_archive_receipt" if history.get("state") == "linked" else "unlinked",
    "semantic_mapping": "partial", "originals_preservation": "verified_session_archive", "originals_current_availability": "not_checked",
    "sources": [{name: item[name] for name in ("id", "sha256", "bytes")} for item in extracted["source_receipts"]],
    "source_fields": extracted["source_fields"], "objectives": copy.deepcopy(config), "mask_profile_sha256": _digest(config["analysis_profile"]),
    "excluded_counts": dict(reasons), "reasons": [*extracted["limitations"], *history.get("reason_codes", []), "pid_dense_native_bursts",
      "continuous_effective_pid_use_receipt", "observational_model_manual_experiment", "no_causal_improvement_claim"]}
  return pack, receipt


def _native_pid_transport(epoch, extracted):
  """Verify Hyundai's actual output/OutputCan equation; never fit a scale."""
  headers = [item.get("recorded_car_params", {}) for item in extracted.get("initial_snapshots", [])]
  matching = [cp for cp in headers if cp.get("state") == "observed" and cp.get("config_sha256") == epoch.get("vehicle_identity", {}).get("config_sha256")]
  if not matching or any(cp.get("controller_context", {}).get("brand") != "hyundai" or
      cp.get("controller_context", {}).get("steer_control_type") != "torque" for cp in matching):
    return None
  quantum_scale, count = None, 0
  for row in epoch["rows"]:
    signals = row.get("signals", {})
    torque, integer = signals.get("sent_torque_raw"), signals.get("sent_torque_can_raw")
    if not torque or not integer or torque["source_mono_ns"] != integer["source_mono_ns"]:
      continue
    value, raw = torque["value"], integer["value"]
    if value == 0 and raw == 0:
      continue
    if value == 0 or type(raw) is not int:
      return None
    ratio = raw / value
    if not math.isfinite(ratio) or not 1 <= ratio <= 100000:
      return None
    scale = round(ratio)
    expected = raw / scale
    if abs(value - expected) > 16 * max(math.ulp(value), math.ulp(expected)):
      return None
    if quantum_scale is not None and scale != quantum_scale:
      return None
    quantum_scale, count = scale, count + 1
  return {"pid_transport_sign": 1, "pid_transport_quantum": 1 / quantum_scale,
          "pid_transport_rule": "nearest", "pid_transport_source": "producer_observation"} if count >= 20 else None


def _write(path, value):
  payload = _encoded(value)
  with path.open("xb") as stream:
    stream.write(payload)
    stream.flush()
    os.fsync(stream.fileno())
  return len(payload)


def calculate_log_candidates(log_paths, profile_path, objectives_path, destination, *, session_directory=None,
                             session_snapshot=None, cancelled=None, continuous=False,
                             max_files=32, max_events=200000, max_samples=100000,
                             max_decompressed_bytes=256 * 1024 * 1024, max_input_bytes=256 * 1024 * 1024,
                             max_epochs=32, max_output_bytes=None, _prepared=None):
  """Build a new bounded collection; select latest source time, never best score."""
  def check_cancelled():
    if cancelled is not None and cancelled():
      raise ValueError("log_analysis_cancelled")
  check_cancelled()
  if type(continuous) is not bool:
    raise ValueError("invalid_continuous_mode")
  for value, name, cap in ((max_files, "max_files", 32), (max_events, "max_events", 2000000), (max_samples, "max_samples", 100000),
                           (max_decompressed_bytes, "max_decompressed_bytes", 2**31), (max_input_bytes, "max_input_bytes", 2**31),
                           (max_epochs, "max_epochs", 32)):
    _positive(value, name, cap)
  if isinstance(log_paths, (str, bytes, Path)):
    raise ValueError("log_paths_must_be_a_collection")
  paths = []
  for path in log_paths:
    if len(paths) >= (128 if _prepared is not None else max_files):
      raise ValueError("log_file_budget_exceeded")
    resolved = Path(path).absolute()
    if resolved in paths:
      raise ValueError("duplicate_log_input")
    paths.append(resolved)
  if not paths:
    raise ValueError("log_inputs_required")
  destination = Path(destination)
  if destination.exists() or destination.is_symlink():
    raise ValueError("log_candidate_destination_exists")
  profile, profile_hash = _read(profile_path, MAX_INPUT_BYTES)
  _profile(profile)
  if profile["status"] != "not_validated" or "recorded" not in profile["allowed_source_kinds"]:
    raise ValueError("recorded_host_profile_required")
  config, objective_hash = _read(objectives_path, 64 * 1024)
  pid_mode = bool(config.get("objectives")) and all(spec.get("reference", {}).get("type") == "recorded_pid_target" for spec in config["objectives"].values())
  output_limit = 48 * 1024 * 1024 if pid_mode else _LIMIT
  max_output_bytes = output_limit if max_output_bytes is None else max_output_bytes
  _positive(max_output_bytes, "max_output_bytes", output_limit)
  pack_limit = MAX_PID_INPUT_BYTES if pid_mode else MAX_INPUT_BYTES
  manual_mode = any(spec.get("reference", {}).get("type") == "manual_trial" for spec in config["objectives"].values())
  capabilities = candidate_capabilities()
  mask = _objectives(config, capabilities)
  continuous = continuous or _sampling_policy(config) is not None
  if continuous and len(paths) > 8 and _prepared is None:
    raise ValueError("continuous_log_input_count_limit")
  adapter_hashes = _code_hashes()
  from .log_history import load_log_history
  history, identities = None, None
  if continuous and _prepared is None:
    preliminary = []
    for path in paths:
      check_cancelled()
      digest, size = hash_file(path, max_input_bytes, cancelled)
      preliminary.append({"id": "log-" + digest[:16], "sha256": digest, "bytes": size, "route": None, "segment": None})
    if len({item["sha256"] for item in preliminary}) != len(preliminary):
      raise ValueError("duplicate_log_content")
    if session_directory is not None:
      history = load_log_history(session_directory, preliminary, session_snapshot=session_snapshot)
      if history["state"] == "linked":
        identities = {item["sha256"]: {"route": item["route"], "segment": item["segment"]} for item in history["source_receipts"]}
  from .log_signals import extract_log_signals
  extracted = _prepared["extracted"] if _prepared is not None else extract_log_signals(paths, analysis_profile=mask, budget={"max_events": max_events, "max_samples": max_samples,
    "max_decompressed_bytes": max_decompressed_bytes, "max_input_bytes": max_input_bytes, "max_event_bytes": 8 * 1024 * 1024,
    "max_age_ns": min(mask.max_gap_ns, profile["max_lag_ns"])}, cancelled=cancelled, continuous=continuous, source_identities=identities,
    static_projection=_static_projection(config, capabilities), include_pid_inputs=pid_mode)
  receipts = extracted["source_receipts"]
  if len({item["sha256"] for item in receipts}) != len(receipts):
    raise ValueError("duplicate_log_content")
  if not extracted["rows"]:
    raise ValueError("log_anchor_samples_unavailable")
  history = _prepared["history"] if _prepared is not None else history if history is not None else load_log_history(session_directory, receipts, session_snapshot=session_snapshot) if session_directory is not None else {
    "state": "unlinked", "reason_codes": ["session_directory_not_provided"], "epochs": [], "code_hashes": {}}
  if pid_mode and history.get("state") == "linked":
    from .pid_burst import load_chunk, assemble_blocks
    chunk_root = Path(session_snapshot if session_snapshot is not None else session_directory)
    history["pid_blocks"] = assemble_blocks([{"event": load_chunk(chunk_root, ref), "reference": ref}
                                             for ref in history.get("pid_chunk_refs", [])])
  resolved = {}
  if history["state"] == "linked":
    # Preserved copies have neutral filenames. A verified exact-byte receipt,
    # rather than that filename, may establish their recorded route identity.
    resolved = {item["sha256"]: item["route"] for item in history["source_receipts"]}
    for source in receipts:
      route = resolved.get(source["sha256"])
      if route is not None:
        if source["route"] is not None and source["route"] != route:
          raise ValueError("verified_log_route_conflict")
        for row in extracted["rows"]:
          if row["source_id"] == source["id"]:
            row["route_id"] = route
  route_keys = {resolved.get(source["sha256"]) or source["route"] or source["id"] for source in receipts}
  if len(route_keys) != 1:
    raise ValueError("multi_route_clock_unlinked")
  epochs = _epoch_rows(extracted["rows"], history, max_epochs)
  if _prepared is not None:
    observed = {item["id"]: item for item in history.get("epochs", [])}
    requested_range = _prepared["manifest"]["source_range"]
    for epoch in epochs:
      if epoch["id"] in observed:
        original = observed[epoch["id"]]
        epoch["start_ns"] = max(original["start_ns"], requested_range["start_ns"])
        epoch["end_ns"] = min(original["end_ns"], requested_range["end_ns"])
  destination.mkdir(parents=False)
  index = {"schema_version": 1, "scope": "log_candidate_collection", "epochs": [], "latest": None,
           "apply_enabled": False, "device_qualified": False}
  used = 0
  for number, epoch in enumerate(epochs):
    check_cancelled()
    pack_config = {**config, "objectives": {}} if manual_mode else config
    evidence, receipt = (_pack_pid if pid_mode else _pack)(epoch, extracted, history, pack_config, profile, capabilities)
    if _prepared is not None:
      # All input bytes and all native masks were checked before bounded time
      # selection. The manifest remains a separately typed collection artifact.
      from .candidates import source_collection, encode_consumer_evidence
      collection = {"version": 1, "sources": [{key: row[key] for key in ("id", "sha256", "bytes")} for row in receipts]}
      digest = source_collection(collection)
      evidence["source_collection"] = collection
      evidence["artifacts"] = [{"id": "collection-" + digest[:16], "sha256": digest}]
      receipt["source_collection"] = collection
      receipt["whole_trip"] = copy.deepcopy(_prepared["manifest"])
      receipt["sources"] = [{**evidence["artifacts"][0], "bytes": len(_encoded(collection))}]
      evidence["epoch"]["dependencies"]["trip_selection_manifest_sha256"] = _prepared["manifest_sha256"]
      receipt["reasons"] = list(dict.fromkeys([*receipt["reasons"], "whole_trip_time_stratified_selection"]))
      if evidence.get("consumer_evidence"):
        evidence["consumer_evidence_block"] = encode_consumer_evidence(evidence["consumer_evidence"])
        evidence["consumer_evidence"] = []
    receipt["objectives_sha256"] = objective_hash
    if manual_mode:
      receipt["objectives"] = copy.deepcopy(config)
    payload = _encoded(evidence)
    if len(payload) > pack_limit:
      raise ValueError("generated_log_pack_byte_limit")
    # Reserve a complete bounded result/manifest in addition to copied inputs.
    upper = 2 * len(payload) + Path(profile_path).stat().st_size + len(_encoded(receipt)) + 1024 * 1024 + 64 * 1024
    if used + upper + 64 * 1024 > max_output_bytes:
      raise ValueError("log_candidate_output_budget_exceeded")
    name = f"epoch-{number:04d}"
    generated = destination / f"generated-{name}.json"
    used += _write(generated, evidence)
    from .calibration_review import selected_parameter, summarize_diagnostics, build_calibration_review
    review = None
    if selected_parameter(config) is not None:
      if _prepared is not None and "diagnostics" in _prepared:
        diagnostics = _prepared["diagnostics"](epoch["start_ns"], epoch["end_ns"])
      else:
        diagnostics = (item for row in epoch["rows"] for item in row.get("diagnostics", []))
      summary = summarize_diagnostics(diagnostics, epoch["start_ns"], epoch["end_ns"], mask.max_gap_ns)
      review = build_calibration_review(evidence, config, profile, summary, input_sha256=hash_file(generated, pack_limit)[0], profile_sha256=profile_hash)
    calculate_bundle(generated, profile_path, destination / name, log_link=receipt, calibration_review=review)
    load_candidate_bundle(destination / name)
    used += sum(path.stat().st_size for path in (destination / name).rglob("*") if path.is_file())
    index["epochs"].append({"epoch_id": epoch["id"], "start_ns": epoch["start_ns"], "end_ns": epoch["end_ns"], "bundle": name, "status": "complete"})
    index["latest"] = name
  for path, receipt in zip(paths, receipts):
    check_cancelled()
    if hash_file(path, max_input_bytes) != (receipt["sha256"], receipt["bytes"]):
      raise ValueError("original_log_changed_after_analysis")
  if _read(profile_path, MAX_INPUT_BYTES)[1] != profile_hash or _read(objectives_path, 64 * 1024)[1] != objective_hash:
    raise ValueError("log_candidate_configuration_changed")
  if _code_hashes() != adapter_hashes:
    raise ValueError("log_candidate_adapter_changed")
  if used + len(_encoded(index)) > max_output_bytes:
    raise ValueError("log_candidate_output_budget_exceeded")
  check_cancelled()
  _write(destination / "index.json", index)
  return {"state": "complete", "scope": index["scope"], "output": str(destination), "epoch_count": len(epochs),
          "latest_bundle": str(destination / index["latest"]), "parameter_count": 47, "source_count": len(paths),
          "session_link": history["state"], "apply_enabled": False, "device_qualified": False}
