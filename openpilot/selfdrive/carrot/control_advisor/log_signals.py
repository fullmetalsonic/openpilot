"""Bounded closed-log signals for host experiments, with shared driver masks.

Settings in initData are advisory startup snapshots. Neither those snapshots
nor the measured ego trajectory provide an independent desired driving policy.
"""
from __future__ import annotations

import math
from pathlib import Path
import re

from openpilot.tools.lib.helpers import RE

from .analysis import AXES, AnalysisProfile, Observation, _mask_axis
from .evidence import _regular_file, hash_file
from .normalizer import EventNormalizer, SERVICES
from .rlog import iter_events, validate_closed_log, _check_cancelled, _positive_int


_SELECTED = (*SERVICES, "initData", "selfdriveState", "carOutput", "radarState", "liveParameters", "liveTorqueParameters")
_SEGMENT = re.compile(rf"^(?P<route>{RE.LOG_ID})--(?P<segment>[0-9]+)$")
SOURCE_FIELDS = {
  "v_ego": {"service": "carState", "field": "vEgo", "unit": "m/s"},
  "measured_a_ego": {"service": "carState", "field": "aEgo", "unit": "m/s^2"},
  "steering_angle_deg": {"service": "carState", "field": "steeringAngleDeg", "unit": "deg"},
  "sent_torque": {"service": "carOutput", "field": "-actuatorsOutput.torque", "unit": "1"},
  "personality": {"service": "selfdriveState", "field": "personality", "unit": "enum"},
  "driving_mode": {"service": "longitudinalPlan", "field": "myDrivingMode", "unit": "enum"},
  "accel_mode_factor": {"service": "longitudinalPlan", "field": "myDrivingMode mapped Eco .9 Safe .8 Normal 1 High 1.2", "unit": "1"},
  "t_follow": {"service": "longitudinalPlan", "field": "tFollow", "unit": "s"},
  "model_lead_offset": {"service": "longitudinalPlan", "field": "trafficStopModelLeadOffset", "unit": "m"},
  "desired_lateral_accel": {"service": "controlsState", "field": "lateralControlState.torqueState.desiredLateralAccel", "unit": "m/s^2"},
  "live_steer_ratio": {"service": "liveParameters", "field": "steerRatio", "unit": "1"},
  "steering_angle_corrected": {"service": "carState+liveParameters", "field": "radians(steeringAngleDeg-angleOffsetDeg)", "unit": "rad"},
  "lead_one_speed": {"service": "radarState", "field": "leadOne.vLead", "unit": "m/s"},
}
LIMITATIONS = ("host_unqualified", "init_settings_advisory_only", "consumer_path_not_verified", "recording_schema_match_unverified",
               "no_independent_policy_inferred", "no_independent_lateral_response_derived", "hidden_tf_response_lc_state_unavailable",
               "asof_joins_not_exact_consumer_reads", "source_hash_stability_not_atomic_snapshot")
_WIRE_FIELDS = {key: value["service"] + "." + value["field"] for key, value in SOURCE_FIELDS.items()}
_WIRE_FIELDS.update(accel_mode_factor="longitudinalPlan.myDrivingMode", sent_torque="carOutput.actuatorsOutput.torque",
                    steering_angle_corrected="carState.steeringAngleDeg_liveParameters.angleOffsetDeg")


def _finite(value):
  return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _route_segment(path):
  if path.name in ("rlog", "rlog.zst", "rlog.bz2"):
    name = path.parent.name
  else:
    name = path.name
    for suffix in (".rlog.zst", ".rlog.bz2", ".rlog"):
      if name.endswith(suffix):
        name = name[:-len(suffix)]
        break
  match = _SEGMENT.fullmatch(name)
  return (match["route"], int(match["segment"])) if match else (None, None)


def _fingerprint(path):
  stat = path.stat()
  return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _signals(car, stamp, cache, max_age):
  result = {}
  def put(name, value, source, source_stamp):
    number = _finite(value)
    if number is not None:
      result[name] = {"value": number, "unit": SOURCE_FIELDS[name]["unit"], "source": source, "source_mono_ns": source_stamp}
  def held(service):
    event = cache.get(service)
    return event if event is not None and event["valid"] and 0 <= stamp - event["mono_ns"] <= max_age else None
  for name, field in (("v_ego", "vEgo"), ("measured_a_ego", "aEgo"), ("steering_angle_deg", "steeringAngleDeg")):
    put(name, car.get(field), "independent_measurement", stamp)
  output = held("carOutput")
  if output:
    value = _finite(output["data"].get("actuatorsOutput", {}).get("torque"))
    put("sent_torque", -value if value is not None else None, "controller_output", output["mono_ns"])
  selfdrive = held("selfdriveState")
  if selfdrive:
    personality = {"aggressive": 0, "standard": 1, "relaxed": 2, "moreRelaxed": 3}.get(str(selfdrive["data"].get("personality")))
    put("personality", personality, "consumer_state", selfdrive["mono_ns"])
  plan = held("longitudinalPlan")
  if plan:
    data, time = plan["data"], plan["mono_ns"]
    mode = data.get("myDrivingMode")
    if type(mode) is int and mode in (1, 2, 3, 4):
      put("driving_mode", mode, "published_planner_state", time)
      put("accel_mode_factor", {1: .9, 2: .8, 3: 1., 4: 1.2}[mode], "derived_input", time)
    follow = _finite(data.get("tFollow"))
    if follow is not None and follow > 0.:
      put("t_follow", follow, "published_planner_state", time)
    put("model_lead_offset", data.get("trafficStopModelLeadOffset"), "published_planner_state", time)
  controls = held("controlsState")
  if controls:
    torque = controls["data"].get("lateralControlState", {}).get("torqueState", {})
    put("desired_lateral_accel", torque.get("desiredLateralAccel"), "controller_target", controls["mono_ns"])
  live = held("liveParameters")
  if live and live["data"].get("valid") is True and live["data"].get("sensorValid") is True:
    data = live["data"]
    put("live_steer_ratio", data.get("steerRatio"), "learned_estimate", live["mono_ns"])
    angle, offset = _finite(car.get("steeringAngleDeg")), _finite(data.get("angleOffsetDeg"))
    if angle is not None and offset is not None:
      put("steering_angle_corrected", math.radians(angle - offset), "derived_input", min(stamp, live["mono_ns"]))
  radar = held("radarState")
  if radar:
    lead = radar["data"].get("leadOne", {})
    if lead.get("status") is True:
      put("lead_one_speed", lead.get("vLead"), "radar_measurement", radar["mono_ns"])
  return result


def extract_log_signals(log_paths, *, analysis_profile: AnalysisProfile, budget: dict, cancelled=None) -> dict:
  """Read explicit files completely or fail; never truncate samples to a budget.

  Decompression is conservatively charged twice: strict closure validation then
  selected-event extraction. Source byte limits count each immutable input once.
  """
  if not isinstance(analysis_profile, AnalysisProfile):
    raise ValueError("analysis_profile must be an AnalysisProfile")
  if not isinstance(log_paths, (tuple, list)) or not 1 <= len(log_paths) <= 32:
    raise ValueError("log input count limit exceeded")
  required = ("max_events", "max_samples", "max_decompressed_bytes", "max_input_bytes", "max_event_bytes")
  if type(budget) is not dict or set(budget) - {*required, "max_age_ns"} or any(name not in budget for name in required):
    raise ValueError("invalid log signal budget")
  for name in required:
    _positive_int(budget[name], name)
  max_age = budget.get("max_age_ns", analysis_profile.max_gap_ns)
  _positive_int(max_age, "max_age_ns")
  rows, receipts, snapshots, seen = [], [], [], set()
  used_bytes = used_decode = used_events = 0
  for supplied in log_paths:
    _check_cancelled(cancelled)
    path = _regular_file(Path(supplied))
    fingerprint = _fingerprint(path)
    if path in seen:
      raise ValueError("duplicate log input")
    seen.add(path)
    remaining_bytes = budget["max_input_bytes"] - used_bytes
    if remaining_bytes <= 0:
      raise ValueError("log input byte budget exceeded")
    before, size = hash_file(path, remaining_bytes, cancelled)
    if size != fingerprint[2]:
      raise ValueError("log source changed")
    remaining_events = budget["max_events"] - used_events
    remaining_decode = budget["max_decompressed_bytes"] - used_decode
    if remaining_events <= 0 or remaining_decode <= 0:
      raise ValueError("log decoding budget exceeded")
    coverage = validate_closed_log(path, max_event_bytes=budget["max_event_bytes"], max_events=remaining_events,
                                   max_decompressed_bytes=remaining_decode, cancelled=cancelled)
    charged = coverage["decompressed_bytes"] * 2
    if charged > remaining_decode:
      raise ValueError("log decompression pass budget exceeded")
    source_id = "log-" + before[:16]
    if any(receipt["sha256"] == before for receipt in receipts):
      raise ValueError("duplicate log content")
    if any(receipt["id"] == source_id for receipt in receipts):
      raise ValueError("log source identity collision")
    route, segment = _route_segment(path)
    normalizer = EventNormalizer(epoch=source_id, max_age_ns=max_age)
    cache, observations = {}, {axis: [] for axis in AXES}
    file_rows, regressions = [], 0
    for event in iter_events(path, services=_SELECTED, max_event_bytes=budget["max_event_bytes"],
                             max_events=coverage["event_count"], max_decompressed_bytes=coverage["decompressed_bytes"], cancelled=cancelled):
      _check_cancelled(cancelled)
      service, stamp, data = event["service"], event["mono_ns"], event["data"]
      regressions += int(not event["service_time_ordered"])
      if service == "initData":
        snapshots.append({"source_id": source_id, "mono_ns": stamp, "event_valid": event["valid"], **data})
        if len(snapshots) > 64:
          raise ValueError("log init snapshot count limit exceeded")
        continue
      if service != "carState":
        previous = cache.get(service)
        if previous is None or stamp > previous["mono_ns"]:
          cache[service] = event
      normalized = normalizer.feed(service, stamp, event["valid"], data) if service in SERVICES else []
      if not normalized:
        continue
      if len(rows) + len(file_rows) >= budget["max_samples"] or (len(rows) + len(file_rows) + 1) * 2 > analysis_profile.max_samples:
        raise ValueError("log sample budget exceeded")
      for observation in normalized:
        observations[observation["axis"]].append(Observation(**observation))
      file_rows.append({"mono_ns": stamp, "route_id": route or source_id, "source_id": source_id,
                        "signals": _signals(data, stamp, cache, max_age), "excluded_reasons": {}})
    if file_rows:
      finalized = file_rows[-1]["mono_ns"] - analysis_profile.pre_margin_ns - analysis_profile.dependency_ns
      for axis, values in observations.items():
        masks = _mask_axis(values, analysis_profile)
        for row, reasons in zip(file_rows, masks):
          row["excluded_reasons"][axis] = sorted(reasons | ({"unfinalized_tail"} if row["mono_ns"] > finalized else set()))
    else:
      finalized = None
    after, after_size = hash_file(path, remaining_bytes, cancelled)
    if before != after or size != after_size or _fingerprint(path) != fingerprint:
      raise ValueError("log source changed")
    car = coverage["services"].get("carState", {})
    receipts.append({"id": source_id, "sha256": before, "bytes": size, "route": route, "segment": segment, "closed": True,
                     "event_count": coverage["event_count"], "decompressed_bytes": coverage["decompressed_bytes"],
                     "decoded_bytes_charged": charged, "service_counts": {name: value["count"] for name, value in coverage["services"].items()},
                     "mono_min_ns": car.get("mono_min_ns"), "mono_max_ns": car.get("mono_max_ns"),
                     "finalized_through_ns": finalized, "code_commit": coverage["recorded_code_commit"],
                     "schema_match": "unverified", "selected_service_regressions": regressions})
    rows.extend(file_rows)
    used_bytes += size
    used_decode += charged
    used_events += coverage["event_count"]
  return {"schema_version": 1, "rows": rows, "source_receipts": receipts, "initial_snapshots": snapshots,
          "source_fields": dict(_WIRE_FIELDS), "limitations": list(LIMITATIONS),
          "join_max_age_ns": max_age, "mask_version": analysis_profile.profile_id,
          "budget_usage": {"input_bytes": used_bytes, "decoded_bytes_charged": used_decode, "events": used_events, "samples": len(rows)}}
