"""Pure, explicitly scoped observations after a PC application receipt.

Only trusted archived-evidence adapters may supply cohorts. Matching stored
values is never an execution receipt, consumer ACK, or causal improvement proof.
"""
from bisect import bisect_right
from collections import Counter
from dataclasses import replace
import hashlib
import json
import math
import re
import sqlite3

from .analysis import AnalysisProfile, Observation, _error, _mask_axis, _merge_intervals, _metrics, _summarize_epoch, _validate_observation
from .consumer_observer import CONTEXT_KEYS, consumer_ids_for_key, validate_event
from .comparison_intervals import encode_exclusions, decode_exclusions
from .registry import load_registry


_SHA = re.compile(r"^[a-f0-9]{64}$")
MAX_PUBLIC_BYTES = 32 * 1024
_UNKNOWN = {"unknown", "unavailable", "not_recorded", "unsupported", "none"}
_ACCEL = {"planner.longitudinal.mode", "planner.longitudinal.driving_mode", "planner.longitudinal.source",
          "planner.carrot.driving_mode", "log.accel_mode_factor"}
_FOLLOW = {"planner.long_mpc.mode", "planner.long_mpc.driving_mode", "planner.long_mpc.personality",
           "planner.long_mpc.source", "planner.long_mpc.lane_change_active", "planner.carrot.personality"}
_TORQUE = {"controls.torque.torque_custom_mode", "controls.torque.nnff", "controls.torque.nnff_lite",
           "controls.torque.nnff_model_selected", "controls.vehicle_model.live_steer_ratio",
           "controls.vehicle_model.stiffness_factor", "controls.lateral_target.path"}
_LANE = {"planner.lateral.lane_mode", "planner.lateral.lanes_active", "planner.lateral.model_valid",
         "controls.lateral_target.path", "controls.lateral_target.lane_selected"}
_LONG = {"controls.longitudinal.brand", "controls.longitudinal.fixed_tuning", "controls.longitudinal.long_control_state",
         "planner.longitudinal.mode", "planner.longitudinal.source"}


class _Hold(ValueError):
  pass


def _hash(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _integer(value, minimum=0):
  if type(value) is not int or not minimum <= value <= 2**63 - 1:
    raise _Hold("comparison_integer_invalid")
  return value


def _finite(value):
  if type(value) not in (int, float) or not math.isfinite(value):
    raise _Hold("comparison_measurement_invalid")
  return float(value)


def _sha(value):
  return type(value) is str and _SHA.fullmatch(value) is not None


def _hashes(value, maximum=128):
  if not isinstance(value, dict) or not 1 <= len(value) <= maximum or any(
      not isinstance(key, str) or not 1 <= len(key) <= 180 or not _sha(digest) for key, digest in value.items()):
    raise _Hold("comparison_source_hashes_missing")


def _reference(value):
  if isinstance(value, dict) and value.get("schema_version") == 2:
    from .journal_segments import validate_reference
    try:
      validate_reference(value)
    except (ValueError, TypeError):
      raise _Hold("comparison_history_reference_invalid") from None
    return
  if (not isinstance(value, dict) or set(value) != {"path", "sequence", "sha256", "bytes", "durable"}
      or value["path"] != "settings.jsonl" or value["durable"] is not True or not _sha(value["sha256"])):
    raise _Hold("comparison_history_reference_invalid")
  _integer(value["sequence"], 1)
  _integer(value["bytes"], 1)


def _known(value, depth=0):
  if depth > 8:
    return False
  if isinstance(value, dict):
    return len(value) <= 128 and all(isinstance(k, str) and len(k) <= 180 and _known(v, depth + 1) for k, v in value.items())
  if isinstance(value, list):
    return len(value) <= 32 and all(_known(v, depth + 1) for v in value)
  if type(value) is str:
    return 0 < len(value) <= 180 and value.lower() not in _UNKNOWN
  return type(value) is bool or type(value) in (int, float) and math.isfinite(value)


def _profile(value):
  required = {"schema_version", "profile_id", "version", "analysis_profile", "settling_ns", "min_samples", "min_duration_ns",
              "max_consumer_age_ns", "speed_bins_mps", "target_bins", "required_context_keys", "model_identity_key"}
  if not isinstance(value, dict) or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] != 1:
    raise _Hold("comparison_profile_required")
  if not isinstance(value["profile_id"], str) or not 1 <= len(value["profile_id"]) <= 128:
    raise _Hold("comparison_profile_required")
  for key in ("version", "min_samples", "min_duration_ns", "max_consumer_age_ns"):
    _integer(value[key], 1)
  _integer(value["settling_ns"])
  try:
    mask = AnalysisProfile(**value["analysis_profile"])
  except (TypeError, ValueError):
    raise _Hold("comparison_analysis_profile_invalid") from None
  if mask.max_samples > 100_000:
    raise _Hold("comparison_sample_budget")
  for key in ("speed_bins_mps", "target_bins"):
    edges = value[key]
    if type(edges) is not list or not 2 <= len(edges) <= 17:
      raise _Hold("comparison_context_bins_required")
    if any(_finite(a) >= _finite(b) for a, b in zip(edges, edges[1:])):
      raise _Hold("comparison_context_bins_invalid")
  keys = value["required_context_keys"]
  if type(keys) is not list or not 0 <= len(keys) <= 64 or len(set(keys)) != len(keys) or any(
      not isinstance(key, str) or not 1 <= len(key) <= 128 for key in keys):
    raise _Hold("comparison_context_keys_required")
  model = value["model_identity_key"]
  if model is not None and model not in keys:
    raise _Hold("comparison_model_identity_key_invalid")
  return mask


def _minimum_context(item):
  family = item["family"]
  if family == "acceleration_limit":
    return _ACCEL, False
  if family in {"following_time", "following_speed_scale", "following_deceleration", "lead_response_profile", "lane_change_gap"}:
    return _FOLLOW, False
  if family in {"torque", "control_mode"}:
    return _TORQUE, False
  if family in {"vehicle_model", "vehicle_model_context"}:
    return {"controls.vehicle_model.live_steer_ratio", "controls.vehicle_model.stiffness_factor", "controls.vehicle_model.manual_override"}, False
  if family in {"lane_mpc_cost", "path_geometry"}:
    return _LANE, True
  if family == "lane_geometry_context":
    return {"planner.lane_path.lanefull_mode", "planner.lane_path.lanes_active", "controls.lateral_target.path"}, True
  if family in {"delay", "smoothing"}:
    return {"modeld.action.model_action_head"}, True
  return _LONG, False


def _contexts(before, after, item, profile, cohort):
  minimum, model_needed = _minimum_context(item)
  required = set(profile["required_context_keys"]) | set(minimum)
  if isinstance(before, dict) and item["family"] == "torque":
    model_entry = before.get("controls.torque.nnff_model_selected")
    model_needed |= isinstance(model_entry, dict) and model_entry.get("value") is True
  if model_needed and profile["model_identity_key"] is None:
    raise _Hold("model_identity_unobserved")
  if not isinstance(before, dict) or not isinstance(after, dict) or not required <= set(before) or set(before) != set(after):
    raise _Hold("comparison_family_context_missing")
  for side in (before, after):
    for key, entry in side.items():
      if not isinstance(key, str) or not isinstance(entry, dict) or set(entry) != {"value", "source"} or not _known(entry["value"]):
        raise _Hold("comparison_context_unknown")
      if key == "log.accel_mode_factor":
        expected = "longitudinalPlan.myDrivingMode"
      elif key == profile["model_identity_key"]:
        model = cohort.get("model_artifact")
        if (not isinstance(model, dict) or not _sha(model.get("sha256")) or not isinstance(model.get("id"), str)
            or cohort["source_hashes"].get(model["id"]) != model["sha256"] or entry["value"] != model["sha256"]):
          raise _Hold("model_identity_unobserved")
        expected = "artifact:" + model["id"]
      else:
        consumer, _, name = key.rpartition(".")
        if consumer not in CONTEXT_KEYS or name not in CONTEXT_KEYS[consumer]:
          raise _Hold("comparison_context_source_unrecognized")
        expected = f"consumer:{consumer}.context.{name}"
      if entry["source"] != expected:
        raise _Hold("comparison_context_source_unrecognized")
  if _hash(before) != _hash(after):
    raise _Hold("comparison_dependency_context_changed")


def _field_matches(event, key, expected):
  field = event["fields"].get(key)
  states = {"loaded"} if event["consumer_id"] == "planner.carrot" else {"used", "latched"}
  if (field is None or field["state"] not in states or field["source"] not in {"stored", "retained_previous"}
      or type(field["effective_value"]) not in (int, float)
      or not math.isclose(field["effective_value"], expected, rel_tol=1e-9, abs_tol=1e-12)):
    return False
  read = field["read_value"]
  return (read is None and event["consumer_id"] != "planner.carrot") or (
    type(read) in (int, float) and math.isclose(read, expected, rel_tol=1e-9, abs_tol=1e-12))


def _torque_effective_context(item, timelines, before, after):
  if item["family"] != "torque":
    return
  keys = {"LateralTorqueAccelFactor", "LateralTorqueFriction", "LateralTorqueKpV", "LateralTorqueKiV", "LateralTorqueKf"} - {item["key"]}
  timeline = timelines.get("controls.torque", [])
  stamps = timeline.stamps if hasattr(timeline, "stamps") else [event["produced_mono_ns"] for event in timeline]
  observed = []
  for rows in (before, after):
    values = {key: set() for key in keys}
    for row in rows:
      index = bisect_right(stamps, row.mono_ns) - 1
      if index < 0:
        raise _Hold("comparison_torque_effective_context_missing")
      for key in keys:
        field = timeline[index]["fields"].get(key)
        if (field is None or field["source"] == "unknown" or field["state"] not in {"used", "latched"}
            or type(field["effective_value"]) not in (int, float) or not math.isfinite(field["effective_value"])):
          raise _Hold("comparison_torque_effective_context_missing")
        values[key].add((field["effective_value"], field["source"], field["state"]))
    if any(len(samples) != 1 for samples in values.values()):
      raise _Hold("comparison_torque_effective_context_changed")
    observed.append(values)
  if observed[0] != observed[1]:
    raise _Hold("comparison_torque_effective_context_changed")


def _consumers(application, cohort, item):
  required = consumer_ids_for_key(item["key"])
  if not required:
    raise _Hold("required_consumer_observation_missing")
  scale = 1 if item["key"] == "SpeedTFFactor" else item["raw_scale"]
  old, new = application["old_raw"] * scale, application["candidate_raw"] * scale
  events = cohort["consumer_events"]
  if type(events) is dict:
    from .comparison_stream import streaming_consumers
    return streaming_consumers(application, cohort, item, required, validate_event, _field_matches)
  if type(events) is not list or not 1 <= len(events) <= 5000:
    raise _Hold("required_consumer_observation_missing")
  timelines, boundary = {}, []
  for event in events:
    validate_event(event)
    if event["consumer_id"] not in required:
      continue
    if event["boot_id"] != cohort["boot_id"] or any(
        digest is None or cohort["before"]["code_hashes"].get(path) != digest for path, digest in event["source_hashes"].items()):
      raise _Hold("comparison_consumer_identity_or_code_changed")
    timeline = timelines.setdefault(event["consumer_id"], [])
    if timeline:
      previous = timeline[-1]
      if (event["produced_mono_ns"] <= previous["produced_mono_ns"] or event["sequence"] != previous["sequence"] + 1
          or event["dropped_events"] != previous["dropped_events"] or any(event[key] != previous[key] for key in (
            "pid", "process_instance_id", "consumer_instance_id"))):
        raise _Hold("comparison_consumer_gap_or_restart")
    elif event["dropped_events"]:
      raise _Hold("comparison_consumer_gap_or_restart")
    timeline.append(event)
  for consumer in required:
    timeline = timelines.get(consumer, [])
    new_event = next((event for event in timeline if event["produced_mono_ns"] >= application["stored_mono_ns"]
                      and _field_matches(event, item["key"], new)), None)
    if new_event is None:
      raise _Hold("required_consumer_observation_missing")
    old_events = [event for event in timeline if event["produced_mono_ns"] < new_event["produced_mono_ns"]
                  and _field_matches(event, item["key"], old)]
    if not old_events or any(not _field_matches(event, item["key"], new) for event in timeline
                             if event["produced_mono_ns"] >= new_event["produced_mono_ns"]):
      raise _Hold("comparison_consumer_baseline_or_reversion")
    boundary.append(new_event["produced_mono_ns"])
  return timelines, max(boundary), old, new


def _side(side, axis, profile, mask, timelines, expected, key, source_floor):
  if type(side.get("observations")) is dict:
    from .comparison_stream import streaming_side
    return streaming_side(side, axis, profile, mask, timelines, expected, key, source_floor, _side)
  window = side["window"]
  for name in ("start_ns", "end_ns", "finalized_through_ns"):
    _integer(window[name])
  if not window["start_ns"] < window["end_ns"] or not window["start_ns"] <= window["finalized_through_ns"] <= window["end_ns"]:
    raise _Hold("comparison_window_invalid")
  rows = side["observations"]
  if type(rows) is not list or not 1 <= len(rows) <= mask.max_samples:
    raise _Hold("comparison_sample_budget")
  intervals = side["eligible_intervals"]
  if type(intervals) is not list or not 1 <= len(intervals) <= 10000:
    raise _Hold("comparison_settings_intervals_missing")
  previous_end = -1
  for interval in intervals:
    lo, hi = _integer(interval["start_ns"]), _integer(interval["end_ns"])
    if not previous_end < lo <= hi:
      raise _Hold("comparison_settings_intervals_invalid")
    previous_end = hi
  interval_starts = [interval["start_ns"] for interval in intervals]
  stamps = {role: timeline.stamps if hasattr(timeline, "stamps") else [event["produced_mono_ns"] for event in timeline] for role, timeline in timelines.items()}
  observations, extras = [], []
  for raw in rows:
    observation = Observation(**raw)
    _validate_observation(observation)
    expected_response = "steering_model_derived" if axis == "lateral" else "car_state_acceleration"
    if observation.context.get("response_kind") != expected_response:
      raise _Hold("comparison_response_source_unknown")
    if observation.axis != axis or observations and observation.mono_ns <= observations[-1].mono_ns:
      raise _Hold("comparison_observation_order_or_axis_invalid")
    reasons = set()
    for role, timeline in timelines.items():
      index = bisect_right(stamps[role], observation.mono_ns) - 1
      if index < 0 or observation.mono_ns - timeline[index]["produced_mono_ns"] > profile["max_consumer_age_ns"] or not _field_matches(timeline[index], key, expected):
        reasons.add("consumer_sample_not_time_aligned")
    observations.append(observation)
    extras.append(reasons)
  raw_masks = _mask_axis(observations, mask)
  unsafe = _merge_intervals((item.mono_ns, observations[index + 1].mono_ns if index + 1 < len(observations) else item.mono_ns + 1)
                            for index, item in enumerate(observations) if raw_masks[index])
  unsafe_starts = [interval[0] for interval in unsafe]
  for observation, reasons in zip(observations, extras):
    source_stamps = [observation.context.get(name) for name in ("target_mono_ns", "actual_mono_ns", "car_control_mono_ns")]
    if any(type(stamp) is not int or not 0 <= stamp <= observation.mono_ns for stamp in source_stamps):
      reasons.add("comparison_source_timestamp_unknown_or_future")
      continue
    earliest = min(source_stamps)
    support = earliest - mask.dependency_ns
    if earliest < source_floor:
      reasons.add("comparison_source_precedes_boundary")
    if any(observation.mono_ns - stamp > mask.max_gap_ns for stamp in source_stamps):
      reasons.add("comparison_source_stale")
    index = bisect_right(interval_starts, support) - 1
    if index < 0 or observation.mono_ns > intervals[index]["end_ns"]:
      reasons.add("comparison_settings_source_uncertain")
    index = bisect_right(unsafe_starts, observation.mono_ns) - 1
    if index >= 0 and unsafe[index][1] > earliest:
      reasons.add("comparison_source_dependency_masked")
  safe = observations[-1].mono_ns - mask.pre_margin_ns - mask.dependency_ns
  if window["finalized_through_ns"] > safe:
    raise _Hold("comparison_unfinalized_window")
  # Raw recovery/dependency masks must not recursively extend themselves merely
  # because a fresh source sits on an already excluded row. Settings scan holes
  # exclude their rows without restarting physical sensor recovery.
  gated = [replace(observation, valid=observation.valid and not (
    reasons - {"comparison_source_dependency_masked", "comparison_settings_source_uncertain"}
    or "comparison_source_dependency_masked" in reasons and not raw_mask))
    for observation, reasons, raw_mask in zip(observations, extras, raw_masks)]
  reasons = _mask_axis(gated, mask)
  records = [(observation, reason | extra) for observation, reason, extra in zip(observations, reasons, extras)
             if window["start_ns"] <= observation.mono_ns <= window["end_ns"]]
  if not records:
    raise _Hold("comparison_window_empty")
  summary = _summarize_epoch(records, window["finalized_through_ns"], mask.max_gap_ns)
  eligible = [observation for observation, reason in records if not reason and observation.mono_ns <= window["finalized_through_ns"]]
  counts = Counter(reason for _, reasons in records for reason in reasons)
  return {"window_start_ns": window["start_ns"], "window_end_ns": window["end_ns"], **summary,
          "exclusion_counts": dict(sorted(counts.items()))}, eligible, records


def _bin(value, edges):
  if value < edges[0] or value > edges[-1]:
    return None
  return min(bisect_right(edges, value) - 1, len(edges) - 2)


def _duration(records, selected, maximum_gap):
  times, duration, previous = {item.mono_ns for item in selected}, 0, None
  for observation, _ in records:
    if previous is not None and observation.mono_ns - previous <= maximum_gap and previous in times and observation.mono_ns in times:
      duration += observation.mono_ns - previous
    previous = observation.mono_ns
  return duration


def _matched(before, after, records, profile, mask):
  from .comparison_stream import DiskRows, streaming_matched
  if isinstance(before, DiskRows) or isinstance(after, DiskRows):
    if not isinstance(before, DiskRows) or not isinstance(after, DiskRows):
      raise _Hold("comparison_disk_pair_invalid")
    return streaming_matched(before, after, profile, mask)
  bins = [{}, {}]
  for side, observations in zip(bins, (before, after)):
    for observation in observations:
      speed = observation.context.get("v_ego")
      if type(speed) not in (int, float) or not math.isfinite(speed):
        continue
      speed_bin, target_bin = _bin(speed, profile["speed_bins_mps"]), _bin(observation.target, profile["target_bins"])
      if speed_bin is not None and target_bin is not None:
        side.setdefault((speed_bin, target_bin), []).append(observation)
  common = sorted(set(bins[0]) & set(bins[1]))
  if not common:
    raise _Hold("comparison_matched_context_unavailable")
  masses = {key: min(len(bins[0][key]), len(bins[1][key])) for key in common}
  total = sum(masses.values())
  summaries, selected = [], []
  for side in bins:
    biases, errors_with_weights, rmses, selected_rows = [], [], [], []
    for key in common:
      rows, mass = side[key], masses[key] / total
      errors = [_error(item)[0] for item in rows]
      metrics = _metrics(errors)
      biases.append(mass * metrics["bias"])
      rmses.append((mass, metrics["rmse"]))
      errors_with_weights.extend((abs(error), mass / len(rows)) for error in errors)
      selected_rows.extend(rows)
    scale = max(value for _, value in rmses)
    rmse = scale * math.sqrt(math.fsum(mass * (value / scale) ** 2 for mass, value in rmses)) if scale else 0.
    cumulative, p95 = 0., max(value for value, _ in errors_with_weights)
    for value, weight in sorted(errors_with_weights):
      cumulative += weight
      if cumulative >= .95:
        p95 = value
        break
    summaries.append({"count": len(selected_rows), "bias": math.fsum(biases), "rmse": rmse, "p95_absolute_error": p95})
    selected.append(selected_rows)
  durations = [_duration(rows, values, mask.max_gap_ns) for rows, values in zip(records, selected)]
  return {"weighting": "common_stratum_minimum_count", "common_bin_count": len(common),
          "bins": [{"speed_bin": key[0], "target_bin": key[1], "before_count": len(bins[0][key]),
                    "after_count": len(bins[1][key]), "weight": masses[key] / total} for key in common],
          "before": summaries[0], "after": summaries[1], "before_duration_ns": durations[0], "after_duration_ns": durations[1],
          "after_minus_before": {key: summaries[1][key] - summaries[0][key] for key in ("bias", "rmse", "p95_absolute_error")}}


def evaluate_comparison(application, cohort, profile):
  """Return a held result rather than manufacturing evidence at a missing gate."""
  public = {"schema_version": 1, "state": "held", "axis": None, "scope": "observational_generated", "target_unit": "m/s^2",
            "before": None, "after": None, "matched": None, "boundary": None, "reason_codes": [],
            "consumer_application_ack": False, "causal_improvement_proven": False, "apply_enabled": False, "device_qualified": False}
  receipt, observed = None, None
  resources = []
  try:
    mask = _profile(profile)
    if not isinstance(application, dict) or not isinstance(cohort, dict):
      raise _Hold("comparison_evidence_invalid")
    if not isinstance(application.get("baseline"), dict):
      raise _Hold("comparison_baseline_unbound")
    application_hash, cohort_hash = _hash(application), _hash(cohort)
    if (application.get("source_kind") not in {"pc_fake", "vehicle_stored"} or application.get("execution_state") not in {"stored", "consumer_observed", "comparison_ready"}
        or not _sha(application.get("receipt_sha256")) or application.get("stored_mono_ns") is None):
      raise _Hold("application_storage_receipt_unavailable")
    baseline_profile = application["baseline"].get("comparison_profile")
    baseline_profile_hash = application["baseline"].get("comparison_profile_sha256")
    if (baseline_profile is None or not _sha(baseline_profile_hash) or _hash(profile) != baseline_profile_hash
        or _hash(baseline_profile) != baseline_profile_hash):
      raise _Hold("comparison_profile_unbound")
    _integer(application["stored_mono_ns"])
    if not _sha(application["request_sha256"]):
      raise _Hold("comparison_request_identity_invalid")
    expected_source = "recorded" if application["source_kind"] == "vehicle_stored" else "generated_native"
    if (type(cohort.get("schema_version")) is not int or cohort.get("schema_version") != 1 or cohort.get("source_kind") != expected_source
        or cohort.get("request_id") != application["request_id"] or cohort.get("request_sha256") != application["request_sha256"]
        or cohort.get("target") != application["target"] or any(cohort.get(key) != application["target"][key] for key in ("session_id", "route", "boot_id"))):
      raise _Hold("comparison_request_target_or_source_unbound")
    _hashes(cohort["source_hashes"], 128)
    _reference(cohort["history_reference"])
    items = {item["key"]: item for item in load_registry()["items"]}
    item = items.get(application["key"])
    if item is None:
      raise _Hold("comparison_parameter_unknown")
    public["axis"] = item["axis"]
    public["scope"] = "observational_model_derived" if item["axis"] == "lateral" else "observational_vehicle_acceleration"
    for name in ("old_raw", "candidate_raw"):
      if type(application[name]) is not int:
        raise _Hold("comparison_parameter_raw_invalid")
    if application["old_raw"] == application["candidate_raw"]:
      raise _Hold("comparison_parameter_unchanged")
    before, after = cohort["before"], cohort["after"]
    _hashes(application["source_candidate"]["code_hashes"])
    for side in (before, after):
      _reference(side["history_reference"])
      _hashes(side["code_hashes"])
      if (side["target_unit"] != "m/s^2" or not _known(side["controller_path"]) or not _known(side["mode"])
          or not isinstance(side["settings_raw"], dict) or set(side["settings_raw"]) != set(items)
          or any(type(value) is not int for value in side["settings_raw"].values())):
        raise _Hold("comparison_baseline_or_controller_unknown")
    if before["history_reference"] != application["baseline"]["settings_history_reference"] or after["history_reference"] != cohort["history_reference"]:
      raise _Hold("comparison_baseline_history_unbound")
    if (before["settings_raw"][item["key"]] != application["old_raw"] or after["settings_raw"][item["key"]] != application["candidate_raw"]
        or [key for key in items if before["settings_raw"][key] != after["settings_raw"][key]] != [item["key"]]):
      raise _Hold("comparison_not_a_single_parameter_change")
    if before["code_hashes"] != after["code_hashes"] or any(before["code_hashes"].get(path) != digest
        for path, digest in application["source_candidate"]["code_hashes"].items()):
      raise _Hold("comparison_code_changed_or_unbound")
    if before["mode"] != after["mode"] or before["controller_path"] != after["controller_path"]:
      raise _Hold("comparison_controller_or_mode_changed")
    dependencies = application["baseline"]["dependencies"]
    if (not _known(dependencies) or not _sha(application["baseline"]["dependency_digest"])
        or _hash(dependencies) != application["baseline"]["dependencies_sha256"]
        or before["dependencies"] != dependencies or after["dependencies"] != dependencies):
      raise _Hold("comparison_dependencies_changed_or_unknown")
    timelines, observed, old, new = _consumers(application, cohort, item)
    for timeline in timelines.values():
      reader = getattr(timeline, "reader", None)
      if reader is not None and reader not in resources:
        resources.append(reader)
    public["state"] = "consumer_observed"
    interval = cohort["setting_change_interval"]
    lower, upper = _integer(interval["after_ns"]), _integer(interval["before_ns"])
    if lower > upper:
      raise _Hold("comparison_setting_interval_invalid")
    start = max(observed, upper, application["stored_mono_ns"]) + profile["settling_ns"]
    public["boundary"] = {"setting_uncertainty_start_ns": lower, "setting_uncertainty_end_ns": upper,
                          "consumer_observed_ns": observed, "after_eligible_from_ns": start, "exact_consumer_apply_time_known": False}
    if before["window"]["end_ns"] >= min(lower, application["stored_mono_ns"]) or after["window"]["start_ns"] < start or before["window"]["end_ns"] >= after["window"]["start_ns"]:
      raise _Hold("comparison_windows_overlap_transition_or_settling")
    _contexts(before["context"], after["context"], item, profile, cohort)
    first, eligible_before, records_before = _side(before, item["axis"], profile, mask, timelines, old, item["key"], before["eligible_intervals"][0]["start_ns"])
    if hasattr(eligible_before, "owner"):
      resources.append(eligible_before.owner)
    second, eligible_after, records_after = _side(after, item["axis"], profile, mask, timelines, new, item["key"], start)
    if hasattr(eligible_after, "owner"):
      resources.append(eligible_after.owner)
    public.update(before=first, after=second)
    if any(summary["eligible_count"] < profile["min_samples"] or summary["eligible_duration_ns"] < profile["min_duration_ns"] for summary in (first, second)):
      raise _Hold("comparison_eligible_exposure_insufficient")
    _torque_effective_context(item, timelines, eligible_before, eligible_after)
    matched = _matched(eligible_before, eligible_after, (records_before, records_after), profile, mask)
    public["matched"] = matched
    if min(matched["before"]["count"], matched["after"]["count"]) < profile["min_samples"] or min(matched["before_duration_ns"], matched["after_duration_ns"]) < profile["min_duration_ns"]:
      raise _Hold("comparison_matched_exposure_insufficient")
    public["state"] = "comparison_ready"
  except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, sqlite3.Error) as error:
    reason = str(error)
    public["reason_codes"] = [reason if re.fullmatch(r"[a-z_]{1,128}", reason) else "comparison_evidence_invalid"]
  for resource in resources:
    try:
      resource.close()
    except (OSError, ValueError, sqlite3.Error):
      public.update(state="held", before=None, after=None, matched=None, boundary=None, reason_codes=["comparison_disk_source_changed"])
  try:
    if any(isinstance(public[name], dict) and isinstance(public[name].get("exclusions"), dict) and public[name]["exclusions"].get("encoding") == "reason-indexed-pages-v1" for name in ("before", "after")):
      public = {**public, "schema_version": 3}
    elif len(json.dumps(public, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) > MAX_PUBLIC_BYTES:
      public = {**public, "schema_version": 2}
      for name in ("before", "after"):
        if public[name] is not None:
          public[name] = {**public[name], "exclusions": encode_exclusions(public[name]["exclusions"])}
    validate_public_comparison(public)
  except (ValueError, TypeError, OverflowError, RecursionError):
    public.update(state="held", before=None, after=None, matched=None, boundary=None, reason_codes=["comparison_public_budget_or_schema"])
  if public["state"] in {"consumer_observed", "comparison_ready"}:
    receipt = {"request_id": application["request_id"], "request_sha256": application["request_sha256"],
               "application_receipt_sha256": application["receipt_sha256"], "state": public["state"],
               "profile_sha256": _hash(profile), "cohort_sha256": cohort_hash, "application_sha256": application_hash,
               "comparison_sha256": _hash(public),
               "history_reference": cohort["history_reference"], "source_hashes": cohort["source_hashes"],
               "consumer_observed_ns": observed}
  return {"state": public["state"], "reason_codes": public["reason_codes"], "receipt": receipt, "comparison": public}


def validate_public_comparison(value):
  required = {"schema_version", "state", "axis", "scope", "target_unit", "before", "after", "matched", "boundary", "reason_codes",
              "consumer_application_ack", "causal_improvement_proven", "apply_enabled", "device_qualified"}
  if (not isinstance(value, dict) or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] not in (1, 2, 3)
      or type(value["state"]) is not str or value["state"] not in {"held", "consumer_observed", "comparison_ready"}
      or (value["axis"] is not None and type(value["axis"]) is not str) or value["axis"] not in {None, "lateral", "longitudinal"}
      or type(value["scope"]) is not str or value["scope"] not in {"observational_generated", "observational_model_derived", "observational_vehicle_acceleration"}
      or value["target_unit"] != "m/s^2" or any(value[key] is not False for key in (
        "consumer_application_ack", "causal_improvement_proven", "apply_enabled", "device_qualified"))):
    raise ValueError("invalid_public_comparison")
  if type(value["reason_codes"]) is not list or len(value["reason_codes"]) > 32 or any(
      not isinstance(code, str) or re.fullmatch(r"[a-z_]{1,128}", code) is None for code in value["reason_codes"]):
    raise ValueError("invalid_public_comparison_reasons")
  def metric(data):
    if not isinstance(data, dict) or set(data) != {"count", "bias", "rmse", "p95_absolute_error"}:
      raise ValueError("invalid_public_comparison_metrics")
    _integer(data["count"])
    for key in ("bias", "rmse", "p95_absolute_error"):
      if data["count"] == 0:
        if data[key] is not None:
          raise ValueError("invalid_public_comparison_metrics")
      elif _finite(data[key]) < 0 and key != "bias":
        raise ValueError("invalid_public_comparison_metrics")
  def reason(code):
    if not isinstance(code, str) or re.fullmatch(r"[a-z_]{1,128}", code) is None:
      raise ValueError("invalid_public_comparison_reasons")
  summary_keys = {"window_start_ns", "window_end_ns", "raw_metrics", "eligible_metrics", "sample_count", "eligible_count",
                  "excluded_count", "provisional_count", "observed_duration_ns", "eligible_duration_ns", "output_limit_observation",
                  "scope_limitations", "exclusions", "exclusion_counts"}
  for name in ("before", "after"):
    summary = value[name]
    if summary is None:
      continue
    if not isinstance(summary, dict) or set(summary) != summary_keys:
      raise ValueError("invalid_public_comparison_summary")
    metric(summary["raw_metrics"])
    metric(summary["eligible_metrics"])
    for key in ("window_start_ns", "window_end_ns", "sample_count", "eligible_count", "excluded_count", "provisional_count",
                "observed_duration_ns", "eligible_duration_ns"):
      _integer(summary[key])
    if summary["eligible_count"] + summary["excluded_count"] + summary["provisional_count"] != summary["sample_count"]:
      raise ValueError("invalid_public_comparison_counts")
    limits = summary["output_limit_observation"]
    if not isinstance(limits, dict) or set(limits) != {"saturated_known_count", "output_limited_known_count", "fully_known_count", "unknown_count", "limited_count"}:
      raise ValueError("invalid_public_comparison_limits")
    for count in limits.values():
      _integer(count)
    if not isinstance(summary["exclusion_counts"], dict) or not isinstance(summary["scope_limitations"], list):
      raise ValueError("invalid_public_comparison_exclusions")
    for code, count in summary["exclusion_counts"].items():
      reason(code)
      _integer(count)
    for code in summary["scope_limitations"]:
      reason(code)
    if value["schema_version"] == 3:
      from .comparison_stream import validate_interval_reference
      validate_interval_reference(summary["exclusions"])
      intervals = []
    else:
      intervals = decode_exclusions(summary["exclusions"], schema_version=value["schema_version"])
    for interval in intervals:
      if not isinstance(interval, dict) or set(interval) != {"reason", "start_ns", "end_ns", "sample_count"}:
        raise ValueError("invalid_public_comparison_exclusions")
      reason(interval["reason"])
      for key in ("start_ns", "end_ns", "sample_count"):
        _integer(interval[key])
  boundary = value["boundary"]
  if boundary is not None:
    keys = {"setting_uncertainty_start_ns", "setting_uncertainty_end_ns", "consumer_observed_ns", "after_eligible_from_ns", "exact_consumer_apply_time_known"}
    if not isinstance(boundary, dict) or set(boundary) != keys or boundary["exact_consumer_apply_time_known"] is not False:
      raise ValueError("invalid_public_comparison_boundary")
    for key in keys - {"exact_consumer_apply_time_known"}:
      _integer(boundary[key])
  matched = value["matched"]
  if matched is not None:
    keys = {"weighting", "common_bin_count", "bins", "before", "after", "before_duration_ns", "after_duration_ns", "after_minus_before"}
    if not isinstance(matched, dict) or set(matched) != keys or matched["weighting"] != "common_stratum_minimum_count":
      raise ValueError("invalid_public_comparison_matched")
    metric(matched["before"])
    metric(matched["after"])
    for key in ("common_bin_count", "before_duration_ns", "after_duration_ns"):
      _integer(matched[key])
    if not isinstance(matched["bins"], list) or not 1 <= len(matched["bins"]) <= 256 or matched["common_bin_count"] != len(matched["bins"]):
      raise ValueError("invalid_public_comparison_bins")
    for cell in matched["bins"]:
      if not isinstance(cell, dict) or set(cell) != {"speed_bin", "target_bin", "before_count", "after_count", "weight"}:
        raise ValueError("invalid_public_comparison_bins")
      for key in ("speed_bin", "target_bin", "before_count", "after_count"):
        _integer(cell[key])
      if not 0 < _finite(cell["weight"]) <= 1:
        raise ValueError("invalid_public_comparison_bins")
    if not isinstance(matched["after_minus_before"], dict) or set(matched["after_minus_before"]) != {"bias", "rmse", "p95_absolute_error"}:
      raise ValueError("invalid_public_comparison_delta")
    for number in matched["after_minus_before"].values():
      _finite(number)
  if value["state"] == "comparison_ready" and (any(value[key] is None for key in ("before", "after", "matched", "boundary")) or value["reason_codes"]):
    raise ValueError("invalid_public_comparison_ready")
  # JSON roundtrip also rejects nonfinite nested metrics and mutable aliasing.
  encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
  if len(encoded.encode()) > MAX_PUBLIC_BYTES:
    raise ValueError("public_comparison_budget")
  return json.loads(encoded)
