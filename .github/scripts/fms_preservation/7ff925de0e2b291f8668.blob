"""Bounded presentation of existing normalized observations and masked evidence.

Manual trials deliberately do not estimate a parameter or predict improvement.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import re

from .analysis import _metrics
from .goals import SIX_PARAMETER_GOALS

MAX_REVIEW_BYTES = 128 * 1024
COLUMNS = ["mono_ns", "target", "actual", "target_mono_ns", "actual_mono_ns",
           "independent_actual", "independent_mono_ns", "sent_request", "sent_mono_ns", "segment",
           "sent_raw", "stop_req", "jerk_upper", "jerk_lower"]
TIMING_KEYS = {"SteerActuatorDelay", "LatSmoothSec", "LongActuatorDelay"}
FIXED_KEYS = {"LateralTorqueAccelFactor", "LateralTorqueFriction", "LateralTorqueKiV", "LateralTorqueKd",
              "LateralTorqueCustom", "NNFF", "NNFFLite", *(row[0] for row in SIX_PARAMETER_GOALS.values())}


def digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def selected_parameter(config):
  keys = list(config.get("objectives", {}))
  return keys[0] if len(keys) == 1 and keys[0] in {row[0] for row in SIX_PARAMETER_GOALS.values()} else None


def diagnostic_row(observation, signals, reasons):
  context, axis = observation["context"], observation["axis"]
  independent = signals.get("lateral_accel_kinematic", {}) if axis == "lateral" else {}
  sent = signals.get("scc_a_req_value", {}) if axis == "longitudinal" else signals.get("sent_torque", {})
  return {"mono_ns": observation["mono_ns"], "axis": axis, "target": observation["target"], "actual": observation["actual"],
    "target_mono_ns": context.get("target_mono_ns"), "actual_mono_ns": context.get("actual_mono_ns"),
    "independent_actual": independent.get("value"), "independent_mono_ns": independent.get("source_mono_ns"),
    "sent_request": sent.get("value"), "sent_mono_ns": sent.get("source_mono_ns"), "excluded_reasons": list(reasons),
    **{name: signals.get(key, {}).get("value") for name, key in (("sent_raw", "scc_a_req_raw"), ("stop_req", "scc_stop_req"),
      ("jerk_upper", "scc_jerk_upper"), ("jerk_lower", "scc_jerk_lower"))}}


def summarize_diagnostics(rows, start, end, max_gap):
  """All raw masked observations determine statistics; only display is reduced."""
  series, metrics, exclusions = {}, {}, {}
  states = {axis: {"errors": [], "counts": Counter(), "points": [], "intervals": [], "raw": 0,
    "positive": 0, "negative": 0, "independent": 0, "sent": 0, "previous": None, "segment": 0,
    "bucket": None, "range": None, "truncated": False, "positive_errors": [], "negative_errors": [], "usable_times": []} for axis in ("lateral", "longitudinal")}
  for row in rows:
    stamp, axis = row["mono_ns"], row["axis"]
    if not start <= stamp <= end:
      continue
    state = states[axis]
    reasons = sorted(set(row["excluded_reasons"]))
    state["raw"] += 1
    state["range"] = {"start_ns": state["range"]["start_ns"] if state["range"] else stamp, "end_ns": stamp}
    state["counts"].update(reasons)
    previous = state["previous"]
    if previous is None or stamp - previous[0] > max_gap or reasons != previous[1] or reasons:
      state["segment"] += 1
    state["previous"] = (stamp, reasons)
    if reasons:
      intervals = state["intervals"]
      if intervals and previous is not None and previous[1] == reasons and intervals[-1]["end_ns"] == previous[0] and stamp - previous[0] <= max_gap:
        intervals[-1]["end_ns"] = stamp
      elif len(intervals) < 128:
        intervals.append({"start_ns": stamp, "end_ns": stamp, "reasons": reasons})
      else:
        state["truncated"] = True
    elif row["target"] is not None and row["actual"] is not None:
      state["errors"].append(row["actual"] - row["target"])
      state["usable_times"].append(stamp)
      if row["target"] > 0:
        state["positive_errors"].append(row["actual"] - row["target"])
      elif row["target"] < 0:
        state["negative_errors"].append(row["actual"] - row["target"])
      state["positive"] += int(row["target"] > 0)
      state["negative"] += int(row["target"] < 0)
      state["independent"] += int(row.get("independent_actual") is not None)
      state["sent"] += int(row.get("sent_request") is not None)
    bucket = min(599, (stamp - start) * 600 // (end - start + 1))
    if bucket != state["bucket"]:
      state["bucket"] = bucket
      point = [row.get(name) if name != "segment" else state["segment"] for name in COLUMNS]
      for index in (0, 3, 4, 6, 8):
        point[index] = str(point[index]) if type(point[index]) is int else None
      state["points"].append(point)
  for axis, state in states.items():
    points = state["points"]
    series[axis] = {"unit": "m/s^2", "actual_source": "steering_model_derived" if axis == "lateral" else "carState.aEgo",
      "independent_source": "calibrated_yaw_times_speed_uncompensated" if axis == "lateral" else None,
      "sent_source": "sendcan.SCC_CONTROL.aReqValue" if axis == "longitudinal" else "carOutput.actuatorsOutput.torque_negative_convention",
      "columns": list(COLUMNS), "points": points, "raw_count": state["raw"], "selected_count": len(points),
      "raw_range": state["range"], "graph_range": {"start_ns": int(points[0][0]), "end_ns": int(points[-1][0])} if points else None}
    metrics[axis] = {**_metrics(state["errors"]), "raw_count": state["raw"], "usable_count": len(state["errors"]),
      "positive_target_count": state["positive"], "negative_target_count": state["negative"],
      "independent_count": state["independent"], "sent_count": state["sent"],
      "positive_target": _metrics(state["positive_errors"]), "negative_target": _metrics(state["negative_errors"])}
    exclusions[axis] = {"counts": dict(state["counts"]), "intervals": state["intervals"], "intervals_truncated": state["truncated"]}
  return {"series": series, "metrics": metrics, "exclusions": exclusions,
          "_consumer_times": {axis: state["usable_times"] for axis, state in states.items()}}


def trial_option_id(baseline, key, current, candidate):
  return digest({"baseline": baseline, "parameter_key": key, "current_raw": current, "candidate_raw": candidate})


def build_calibration_review(evidence, config, profile, summary, *, input_sha256, profile_sha256):
  from .registry import load_registry
  from .candidates import _consumer_gate
  key = selected_parameter(config)
  if key is None:
    return None
  epoch = evidence["epoch"]
  from .candidates import consumer_events
  baseline = {"epoch_id": epoch["id"], "start_ns": epoch["start_ns"], "end_ns": epoch["end_ns"],
    "settings_raw": deepcopy(epoch["settings_raw"]),
    "vehicle_identity": deepcopy(evidence["vehicle_context"].get("context", {}).get("vehicle_identity")),
    "code_hashes": deepcopy(evidence["vehicle_context"]["code_hashes"]),
    "source_collection_sha256": digest(evidence.get("source_collection", evidence["artifacts"])),
    "consumer_evidence_sha256": digest(consumer_events(evidence)),
    "mask_profile_sha256": digest(config["analysis_profile"]), "input_sha256": input_sha256, "profile_sha256": profile_sha256}
  item = next(item for item in load_registry()["items"] if item["key"] == key)
  reasons, options = [], []
  purpose = "manual_trial" if key in TIMING_KEYS else "numeric_calibration"
  axis = item["axis"]
  stats = summary["metrics"][axis]
  if epoch["completeness"] != "observed" or not epoch.get("boot_id"):
    reasons.append("settings_epoch_unlinked")
  if not FIXED_KEYS <= set(baseline["settings_raw"]) or any(type(baseline["settings_raw"].get(name)) is not int for name in FIXED_KEYS):
    reasons.append("fixed_settings_unobserved")
  if baseline["settings_raw"].get("LateralTorqueKiV") != 0:
    reasons.append("fixed_ki_zero_required")
  identity = baseline["vehicle_identity"] or {}
  if identity.get("state") != "observed":
    reasons.append("recorded_cp_unavailable")
  if not baseline["code_hashes"] or not evidence["artifacts"]:
    reasons.append("source_binding_unavailable")
  current = baseline["settings_raw"].get(key)
  if type(current) is not int or str(current) in item.get("special_values", {}) or not item["ui"]["min"] <= current <= item["ui"]["max"]:
    reasons.append("current_raw_special_or_unobserved")
  if baseline["settings_raw"].get("CustomSR", 0) <= 10:
    reasons.append("manual_steer_ratio_mode_required")
  if stats["usable_count"] < profile["min_samples_per_split"]:
    reasons.append("usable_baseline_insufficient")
  if evidence["source_kind"] != "recorded":
    reasons.append("recorded_source_required")
  from .consumer_observer import consumer_ids_for_key
  events = [event for event in consumer_events(evidence) if event["consumer_id"] in consumer_ids_for_key(key)]
  if not events or any(event.get("vehicle_identity", {}).get("config_sha256") != identity.get("config_sha256") for event in events):
    reasons.append("consumer_cp_binding_unavailable")
  if purpose == "manual_trial":
    if axis == "lateral" and stats["independent_count"] < profile["min_samples_per_split"]:
      reasons.append("independent_lateral_response_unavailable")
    if axis == "longitudinal" and stats["sent_count"] < profile["min_samples_per_split"]:
      reasons.append("matching_scc_control_sendcan_unavailable")
    if not reasons:
      # Reuse the existing exact effective-value, epoch, hash and all-consumer gate.
      records = {"fit": [{"mono_ns": stamp} for stamp in summary["_consumer_times"][axis]], "select": [], "holdout": []}
      reasons.extend(_consumer_gate(evidence, profile, item, records)[0])
    if not reasons:
      ui = item["ui"]
      for candidate in (current - ui["step"], current + ui["step"]):
        if ui["min"] <= candidate <= ui["max"] and str(candidate) not in item.get("special_values", {}):
          options.append({"id": trial_option_id(baseline, key, current, candidate), "purpose": "manual_trial",
            "parameter_key": key, "current_raw": current, "candidate_raw": candidate, "restore_raw": current})
      if not options:
        reasons.append("no_single_step_trial_available")
  else:
    reasons.append("numeric_candidate_gate_remains_authoritative")
  value = {"schema_version": 1, "parameter_key": key, "purpose": purpose, "state": "ready" if options else "held",
    "baseline": baseline, **{name: deepcopy(summary[name]) for name in ("series", "metrics", "exclusions")},
    "reasons": list(dict.fromkeys(reasons)), "trial_options": options}
  # The full-rate statistics and intervals stay intact; reduce only plot points.
  while len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_REVIEW_BYTES:
    reducible = [series for series in value["series"].values() if len(series["points"]) > 2]
    if not reducible:
      break
    for series in reducible:
      points = series["points"]
      series["points"] = points[::2] + ([points[-1]] if points[-1] != points[::2][-1] else [])
      series["selected_count"] = len(series["points"])
  return validate_calibration_review(value)


def validate_calibration_review(value, *, evidence=None, input_sha256=None, profile_sha256=None):
  required = {"schema_version", "parameter_key", "purpose", "state", "baseline", "series", "metrics", "exclusions", "reasons", "trial_options"}
  if (type(value) is not dict or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] != 1
      or value["parameter_key"] not in {row[0] for row in SIX_PARAMETER_GOALS.values()}
      or value["purpose"] != ("manual_trial" if value["parameter_key"] in TIMING_KEYS else "numeric_calibration")
      or value["state"] not in {"ready", "held"}):
    raise ValueError("invalid_calibration_review")
  baseline = value["baseline"]
  names = {"epoch_id", "start_ns", "end_ns", "settings_raw", "vehicle_identity", "code_hashes", "source_collection_sha256",
           "consumer_evidence_sha256", "mask_profile_sha256", "input_sha256", "profile_sha256"}
  if type(baseline) is not dict or set(baseline) != names or not isinstance(baseline["epoch_id"], str):
    raise ValueError("invalid_calibration_baseline")
  if (any(type(baseline[name]) is not int for name in ("start_ns", "end_ns"))
      or not 0 <= baseline["start_ns"] <= baseline["end_ns"] <= 2**63 - 1
      or type(baseline["code_hashes"]) is not dict or len(baseline["code_hashes"]) > 64
      or any(type(name) is not str or type(sha) is not str or not re.fullmatch("[a-f0-9]{64}", sha) for name, sha in baseline["code_hashes"].items())):
    raise ValueError("invalid_calibration_baseline")
  for name in ("source_collection_sha256", "consumer_evidence_sha256", "mask_profile_sha256", "input_sha256", "profile_sha256"):
    if type(baseline[name]) is not str or re.fullmatch("[a-f0-9]{64}", baseline[name]) is None:
      raise ValueError("invalid_calibration_hash")
  if type(baseline["settings_raw"]) is not dict or any(type(v) not in (int, type(None)) for v in baseline["settings_raw"].values()):
    raise ValueError("invalid_calibration_settings")
  if input_sha256 is not None and baseline["input_sha256"] != input_sha256 or profile_sha256 is not None and baseline["profile_sha256"] != profile_sha256:
    raise ValueError("calibration_source_binding_mismatch")
  if evidence is not None:
    from .candidates import consumer_events
    epoch = evidence["epoch"]
    expected = {"epoch_id": epoch["id"], "start_ns": epoch["start_ns"], "end_ns": epoch["end_ns"], "settings_raw": epoch["settings_raw"],
      "vehicle_identity": evidence["vehicle_context"].get("context", {}).get("vehicle_identity"),
      "code_hashes": evidence["vehicle_context"]["code_hashes"],
      "source_collection_sha256": digest(evidence.get("source_collection", evidence["artifacts"])),
      "consumer_evidence_sha256": digest(consumer_events(evidence))}
    if any(baseline[name] != content for name, content in expected.items()):
      raise ValueError("calibration_source_binding_mismatch")
  for name in ("series", "metrics", "exclusions"):
    if type(value[name]) is not dict or set(value[name]) != {"lateral", "longitudinal"}:
      raise ValueError("invalid_calibration_axes")
  for axis, series in value["series"].items():
    fields = {"unit", "actual_source", "independent_source", "sent_source", "columns", "points", "raw_count", "selected_count", "raw_range", "graph_range"}
    if type(series) is not dict or set(series) != fields or series["columns"] != COLUMNS or series["unit"] != "m/s^2" or type(series["points"]) is not list or len(series["points"]) > 600:
      raise ValueError("invalid_calibration_series")
    previous = -1
    for point in series["points"]:
      if type(point) is not list or len(point) != len(COLUMNS):
        raise ValueError("invalid_calibration_point")
      for index in (0, 3, 4, 6, 8):
        if point[index] is not None and (type(point[index]) is not str or re.fullmatch("0|[1-9][0-9]{0,18}", point[index]) is None or int(point[index]) > 2**63 - 1):
          raise ValueError("invalid_calibration_timestamp")
      if point[0] is None or int(point[0]) <= previous:
        raise ValueError("calibration_time_order")
      previous = int(point[0])
      if (not baseline["start_ns"] <= previous <= baseline["end_ns"]
          or any(point[index] is not None and int(point[index]) > previous for index in (3, 4, 6, 8))):
        raise ValueError("calibration_timestamp_outside_support")
      if any(point[index] is not None and (type(point[index]) not in (int, float) or not math.isfinite(point[index])) for index in (1, 2, 5, 7, 10, 11, 12, 13)) or type(point[9]) is not int or point[9] < 0:
        raise ValueError("invalid_calibration_measurement")
    if series["selected_count"] != len(series["points"]) or type(series["raw_count"]) is not int or series["raw_count"] < len(series["points"]):
      raise ValueError("invalid_calibration_count")
    for name in ("raw_range", "graph_range"):
      span = series[name]
      if span is not None and (type(span) is not dict or set(span) != {"start_ns", "end_ns"}
          or any(type(span[field]) is not int for field in span)
          or not baseline["start_ns"] <= span["start_ns"] <= span["end_ns"] <= baseline["end_ns"]):
        raise ValueError("invalid_calibration_range")
    if bool(series["points"]) != (series["graph_range"] is not None):
      raise ValueError("invalid_calibration_range")
    metrics, excluded = value["metrics"][axis], value["exclusions"][axis]
    fields = {"count", "bias", "rmse", "p95_absolute_error", "raw_count", "usable_count", "positive_target_count", "negative_target_count", "independent_count", "sent_count", "positive_target", "negative_target"}
    if type(metrics) is not dict or set(metrics) != fields or metrics["raw_count"] != series["raw_count"] or metrics["count"] != metrics["usable_count"]:
      raise ValueError("invalid_calibration_metrics")
    for name in ("count", "raw_count", "usable_count", "positive_target_count", "negative_target_count", "independent_count", "sent_count"):
      if type(metrics[name]) is not int or not 0 <= metrics[name] <= series["raw_count"]:
        raise ValueError("invalid_calibration_metric_count")
    for measured in (metrics, metrics["positive_target"], metrics["negative_target"]):
      if type(measured) is not dict or not {"count", "bias", "rmse", "p95_absolute_error"} <= set(measured):
        raise ValueError("invalid_calibration_metrics")
      for name in ("bias", "rmse", "p95_absolute_error"):
        number = measured[name]
        if number is not None and (type(number) not in (int, float) or not math.isfinite(number)):
          raise ValueError("invalid_calibration_metrics")
    if (type(excluded) is not dict or set(excluded) != {"counts", "intervals", "intervals_truncated"}
        or type(excluded["counts"]) is not dict or len(excluded["counts"]) > 32
        or any(type(count) is not int or not 0 <= count <= series["raw_count"] for count in excluded["counts"].values())
        or type(excluded["intervals"]) is not list or len(excluded["intervals"]) > 128 or type(excluded["intervals_truncated"]) is not bool):
      raise ValueError("invalid_calibration_exclusions")
    for interval in excluded["intervals"]:
      if (type(interval) is not dict or set(interval) != {"start_ns", "end_ns", "reasons"}
          or any(type(interval[name]) is not int for name in ("start_ns", "end_ns"))
          or not baseline["start_ns"] <= interval["start_ns"] <= interval["end_ns"] <= baseline["end_ns"]
          or type(interval["reasons"]) is not list or not interval["reasons"] or set(interval["reasons"]) - set(excluded["counts"])):
        raise ValueError("invalid_calibration_interval")
  if type(value["reasons"]) is not list or len(value["reasons"]) > 32 or any(type(reason) is not str or not re.fullmatch("[a-z0-9_]{1,128}", reason) for reason in value["reasons"]):
    raise ValueError("invalid_calibration_reasons")
  options, key = value["trial_options"], value["parameter_key"]
  if (type(options) is not list or len(options) > 2 or value["purpose"] == "manual_trial" and (value["state"] == "ready") != bool(options)
      or options and value["purpose"] != "manual_trial"):
    raise ValueError("invalid_calibration_trials")
  if value["state"] == "ready" and (value["reasons"] or baseline["settings_raw"].get("LateralTorqueKiV") != 0):
    raise ValueError("invalid_calibration_ready_state")
  from .registry import load_registry
  item = next(row for row in load_registry()["items"] if row["key"] == key)
  for option in options:
    if (type(option) is not dict or set(option) != {"id", "purpose", "parameter_key", "current_raw", "candidate_raw", "restore_raw"}
        or option["purpose"] != "manual_trial" or option["parameter_key"] != key
        or any(type(option[name]) is not int for name in ("current_raw", "candidate_raw", "restore_raw"))
        or option["current_raw"] != baseline["settings_raw"].get(key) or option["restore_raw"] != option["current_raw"]
        or abs(option["candidate_raw"] - option["current_raw"]) != item["ui"]["step"]
        or not item["ui"]["min"] <= option["candidate_raw"] <= item["ui"]["max"]
        or str(option["candidate_raw"]) in item.get("special_values", {})
        or str(option["current_raw"]) in item.get("special_values", {}) or not item["ui"]["min"] <= option["current_raw"] <= item["ui"]["max"]
        or option["id"] != trial_option_id(baseline, key, option["current_raw"], option["candidate_raw"])):
      raise ValueError("invalid_calibration_trial_option")
  if options and (not FIXED_KEYS <= set(baseline["settings_raw"]) or baseline["settings_raw"].get("CustomSR", 0) <= 10
      or baseline["settings_raw"].get("LateralTorqueKiV") != 0
      or not baseline["code_hashes"] or type(baseline["vehicle_identity"]) is not dict or baseline["vehicle_identity"].get("state") != "observed"):
    raise ValueError("invalid_calibration_trial_baseline")
  if len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_REVIEW_BYTES:
    raise ValueError("calibration_review_byte_limit")
  return deepcopy(value)
