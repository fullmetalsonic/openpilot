"""Bounded closed-log signals for host experiments, with shared driver masks.

Settings in initData are advisory startup snapshots. Neither those snapshots
nor the measured ego trajectory provide an independent desired driving policy.
"""
from __future__ import annotations

import math
from collections import deque
from copy import deepcopy
from pathlib import Path
import re

from openpilot.tools.lib.helpers import RE

from .analysis import AXES, AnalysisProfile, Observation, _mask_axis
from .evidence import _regular_file, hash_file
from .normalizer import EventNormalizer, SERVICES
from .rlog import iter_events, validate_closed_log, _check_cancelled, _positive_int
from .lateral_observations import LateralObservationBuilder


_SELECTED = (*SERVICES, "initData", "selfdriveState", "carOutput", "radarState", "liveParameters", "liveTorqueParameters",
             "carParams", "livePose", "liveCalibration", "liveDelay")
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
  "yaw_rate_calibrated": {"service": "livePose+liveCalibration", "field": "calib_from_device @ angularVelocityDevice", "unit": "rad/s"},
  "lateral_accel_independent": {"service": "livePose+carState+liveDelay", "field": "aligned_v*yaw-sin(device_roll)*9.81", "unit": "m/s^2"},
  "curvature_independent": {"service": "livePose+carState", "field": "calibrated_yaw/response_time_v", "unit": "1/m"},
  "vm_curvature_factor": {"service": "initData+liveParameters+carState", "field": "recorded_CP.VehicleModel.curvature_factor", "unit": "1/m"},
  "roll_curvature": {"service": "initData+liveParameters+carState", "field": "recorded_CP.VehicleModel.roll_compensation", "unit": "1/m"},
}
LIMITATIONS = ("host_unqualified", "init_settings_advisory_only", "consumer_path_not_verified", "recording_schema_match_unverified",
               "no_independent_policy_inferred", "independent_lateral_requires_recorded_cp_sensors",
               "repeated_pose_responses_not_independent_samples", "hidden_tf_response_lc_state_unavailable",
               "asof_joins_not_exact_consumer_reads", "source_hash_stability_not_atomic_snapshot")
_WIRE_FIELDS = {key: value["service"] + "." + value["field"] for key, value in SOURCE_FIELDS.items()}
_WIRE_FIELDS.update(accel_mode_factor="longitudinalPlan.myDrivingMode", sent_torque="carOutput.actuatorsOutput.torque",
                    steering_angle_corrected="carState.steeringAngleDeg_liveParameters.angleOffsetDeg",
                    yaw_rate_calibrated="livePose.angularVelocityDevice_liveCalibration.rpyCalib",
                    lateral_accel_independent="carState.vEgo_liveDelay.lateralDelay_livePose.angularVelocityDevice_orientationNED",
                    curvature_independent="livePose.angularVelocityDevice_liveCalibration.rpyCalib_carState.vEgo",
                    vm_curvature_factor="initData.params.CarParams_liveParameters.stiffnessFactor_carState.vEgo",
                    roll_curvature="initData.params.CarParams_liveParameters.roll_stiffnessFactor_carState.vEgo")


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


def _signals(car, stamp, cache, max_age, include_pid_inputs=False):
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
    if include_pid_inputs and value is not None:
      result["sent_torque_raw"] = {"value": -value, "unit": "1", "source": "controller_output", "source_mono_ns": output["mono_ns"]}
      can_value = output["data"].get("actuatorsOutput", {}).get("torqueOutputCan")
      if type(can_value) in (int, float) and math.isfinite(can_value) and can_value == int(can_value):
        result["sent_torque_can_raw"] = {"value": -int(can_value), "unit": "1", "source": "controller_output", "source_mono_ns": output["mono_ns"]}
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


class _LateralStream:
  """One bounded CP span; source-file transitions never imply settings epochs."""
  def __init__(self, max_age, max_alignment, max_buffer, include_pid_inputs=False):
    self.bounds = (max_age, max_alignment, max_buffer)
    self.include_pid_inputs = include_pid_inputs
    self.builder, self.cp, self.previous = None, None, None
    self.span = 0
    self.reason = "recorded_cp_missing"
    self.source_id = None
    self.header_validated = False

  def begin(self, source_id, route, segment, coverage):
    start, end = coverage.get("mono_min_ns"), coverage.get("mono_max_ns")
    previous = self.previous
    contiguous = (previous is not None and route is not None and route == previous[0]
                  and type(segment) is int and segment == previous[1] + 1
                  and type(start) is int and type(previous[2]) is int
                  and 0 <= start - previous[2] <= self.bounds[0])
    if not contiguous:
      self.builder, self.cp = None, None
    self.previous = (route, segment, end)
    self.source_id, self.header_validated = source_id, False
    self.reason = "recorded_cp_missing"

  def header(self, event):
    cp = event["data"].get("recorded_car_params")
    if event["valid"] is not True or type(cp) is not dict or cp.get("state") != "observed":
      self.reason = cp.get("reason", "recorded_cp_missing") if type(cp) is dict else "recorded_cp_missing"
      if event["valid"] is not True:
        self.reason = "recorded_cp_header_invalid"
      self.builder, self.cp, self.header_validated = None, None, False
      return
    self.header_validated = True
    if self.builder is not None and self.cp == cp:
      return  # Repeated native headers retain their original timestamp and span.
    self.cp = cp
    self.span += 1
    source = {"schema_version": 1, "source_id": self.source_id,
              "epoch_id": f"cp-span-{self.span}-{cp['raw_sha256'][:16]}",
              "config_sha256": cp["config_sha256"], "mono_ns": event["mono_ns"], "values": cp["values"]}
    try:
      self.builder = LateralObservationBuilder(source, max_age_ns=self.bounds[0],
                      max_alignment_ns=self.bounds[1], max_buffer_events=self.bounds[2], include_pid_inputs=self.include_pid_inputs)
    except ValueError:
      self.builder, self.reason = None, "recorded_cp_adapter_bounds_or_values_invalid"

  def feed(self, event):
    if self.builder is not None and self.header_validated:
      self.builder.feed(event)

  def observe(self, anchor):
    if self.builder is not None and self.header_validated:
      result = self.builder.observe(anchor)
    else:
      result = {"signals": {}, "availability": {key: {"available": False, "reason_codes": [self.reason]}
                for key in ("pose", "torque_response", "geometry")}, "reason_codes": [self.reason],
                "provenance": {"source_id": None, "epoch_id": None, "config_sha256": None,
                  "response_mono_ns": None, "pose_event_mono_ns": None, "supports": {},
                  "alignment": {"kind": "none", "delay_ns": None}, "consumer_mapping_verified": False,
                  "parameter_application_ack": False, "closed_loop_improvement_proven": False}}
    signals = result.pop("signals")
    result["epoch_kind"] = "recorded_cp_span"
    result["recording_schema_verified"] = False
    return signals, result


def _stream_state(lateral, normalizer, cache, previous, initial_epoch, splits):
  builder = lateral.builder
  return {"version": 1, "previous_source": list(previous), "initial_epoch": initial_epoch, "epoch_splits": splits,
    "normalizer": {"epoch": normalizer.epoch, "last_anchor": normalizer._last_anchor, "cache": deepcopy(normalizer._cache)},
    "cache": deepcopy(cache), "lateral": {"cp": deepcopy(lateral.cp), "previous": deepcopy(lateral.previous),
      "span": lateral.span, "reason": lateral.reason, "source_id": lateral.source_id, "header_validated": lateral.header_validated,
      "builder": None if builder is None else {"source": deepcopy(builder._source), "events": list(builder._events),
        "last": dict(builder._last), "cp_invalid_from": builder._cp_invalid_from}}}


def _restore_stream(state, lateral, max_age, alignment, buffer):
  if type(state) is not dict or set(state) != {"version", "previous_source", "initial_epoch", "epoch_splits", "normalizer", "cache", "lateral"} or state["version"] != 1:
    raise ValueError("invalid_extraction_checkpoint")
  saved = state["normalizer"]
  normalizer = EventNormalizer(epoch=saved["epoch"], max_age_ns=max_age)
  normalizer._last_anchor = saved["last_anchor"]
  normalizer._cache = {key: tuple(value) for key, value in saved["cache"].items()}
  saved_lateral = state["lateral"]
  for name in ("cp", "previous", "span", "reason", "source_id", "header_validated"):
    setattr(lateral, name, deepcopy(saved_lateral[name]))
  saved_builder = saved_lateral["builder"]
  if saved_builder is not None:
    builder = LateralObservationBuilder(saved_builder["source"], max_age_ns=max_age, max_alignment_ns=alignment,
      max_buffer_events=buffer, include_pid_inputs=lateral.include_pid_inputs)
    if type(saved_builder["events"]) is not list or len(saved_builder["events"]) > buffer:
      raise ValueError("extraction_checkpoint_buffer_budget")
    builder._events = deque(deepcopy(saved_builder["events"]), maxlen=buffer)
    for item in builder._events:
      builder._by_service[item["service"]].append(item)
      builder._keys[item["service"]].append(item["effective_ns"])
    builder._last = dict(saved_builder["last"])
    builder._cp_invalid_from = saved_builder["cp_invalid_from"]
    lateral.builder = builder
  return normalizer, deepcopy(state["cache"])


def extract_log_signals(log_paths, *, analysis_profile: AnalysisProfile, budget: dict, cancelled=None,
                        continuous: bool = False, source_identities: dict | None = None,
                        static_projection: dict | None = None, include_pid_inputs: bool = False,
                        stream_sink=None, stream_state: dict | None = None) -> dict:
  """Read explicit files completely or fail; never truncate samples to a budget.

  Decompression is conservatively charged twice: strict closure validation then
  selected-event extraction. Source byte limits count each immutable input once.
  """
  if not isinstance(analysis_profile, AnalysisProfile):
    raise ValueError("analysis_profile must be an AnalysisProfile")
  if not isinstance(log_paths, (tuple, list)) or not 1 <= len(log_paths) <= 32:
    raise ValueError("log input count limit exceeded")
  if type(continuous) is not bool:
    raise ValueError("invalid_continuous_mode")
  streaming = stream_sink is not None
  if streaming and (not callable(stream_sink) or not continuous or len(log_paths) != 1) or stream_state is not None and not streaming:
    raise ValueError("invalid_streaming_mode")
  if type(include_pid_inputs) is not bool or (include_pid_inputs and static_projection is not None):
    raise ValueError("invalid_pid_input_mode")
  retained_signals = None
  if static_projection is not None:
    if (type(static_projection) is not dict or set(static_projection) != {"version", "signals"}
        or static_projection["version"] != "native_static_rows_v1"
        or type(static_projection["signals"]) is not list or not 1 <= len(static_projection["signals"]) <= 64
        or any(type(name) is not str or name not in {*SOURCE_FIELDS, "lateral_accel_offset"}
               for name in static_projection["signals"])
        or len(set(static_projection["signals"])) != len(static_projection["signals"])
        or "v_ego" not in static_projection["signals"]):
      raise ValueError("invalid_static_projection")
    retained_signals = frozenset(static_projection["signals"])
  if continuous and len(log_paths) > 8:
    raise ValueError("continuous log input count limit exceeded")
  if source_identities is not None:
    if not isinstance(source_identities, dict):
      raise ValueError("invalid source_identities")
    for key, value in source_identities.items():
      if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("invalid source_identities key")
      if not isinstance(value, dict) or set(value) != {"route", "segment"}:
        raise ValueError("invalid source_identities entry")
      if type(value["route"]) is not str or not re.fullmatch(RE.LOG_ID, value["route"]):
        raise ValueError("invalid source_identities route")
      if type(value["segment"]) is not int or not 0 <= value["segment"] <= 2**31 - 1:
        raise ValueError("invalid source_identities segment")
  required = ("max_events", "max_samples", "max_decompressed_bytes", "max_input_bytes", "max_event_bytes")
  if (type(budget) is not dict or set(budget) - {*required, "max_age_ns", "lateral_max_alignment_ns", "lateral_max_buffer_events"}
      or any(name not in budget for name in required)):
    raise ValueError("invalid log signal budget")
  for name in required:
    _positive_int(budget[name], name)
  max_age = budget.get("max_age_ns", analysis_profile.max_gap_ns)
  _positive_int(max_age, "max_age_ns")
  alignment, buffer = budget.get("lateral_max_alignment_ns", 1_000_000_000), budget.get("lateral_max_buffer_events", 4096)
  if type(alignment) is not int or not 0 < alignment <= 60_000_000_000 or type(buffer) is not int or not 0 < buffer <= 100_000:
    raise ValueError("invalid lateral observation bounds")

  lateral = _LateralStream(max_age, alignment, buffer, include_pid_inputs)
  all_rows, receipts, snapshots, seen = [], [], [], set()
  used_bytes = used_decode = used_events = 0
  num_files = len(log_paths)
  agg_max_bytes = budget["max_input_bytes"] * num_files if continuous else budget["max_input_bytes"]
  agg_max_events = budget["max_events"] * num_files if continuous else budget["max_events"]
  agg_max_decode = budget["max_decompressed_bytes"] * num_files if continuous else budget["max_decompressed_bytes"]

  prev_route = None
  prev_segment = None
  prev_car_max_ns = None
  connected_normalizer = None
  connected_cache = None
  connected_observations = None
  initial_normalizer_epoch = None
  normalizer_epoch_splits = 0
  file_row_groups = []
  streamed_samples = 0
  if stream_state is not None:
    connected_normalizer, connected_cache = _restore_stream(stream_state, lateral, max_age, alignment, buffer)
    connected_observations = {axis: [] for axis in AXES}
    prev_route, prev_segment, prev_car_max_ns = stream_state["previous_source"]
    initial_normalizer_epoch, normalizer_epoch_splits = stream_state["initial_epoch"], stream_state["epoch_splits"]

  for file_idx, supplied in enumerate(log_paths):
    _check_cancelled(cancelled)
    path = _regular_file(Path(supplied))
    fingerprint = _fingerprint(path)
    if path in seen:
      raise ValueError("duplicate log input")
    seen.add(path)

    remaining_agg_bytes = agg_max_bytes - used_bytes
    if remaining_agg_bytes <= 0:
      raise ValueError("log input byte budget exceeded")
    max_file_bytes = min(budget["max_input_bytes"], remaining_agg_bytes) if continuous else remaining_agg_bytes
    before, size = hash_file(path, max_file_bytes, cancelled)
    if size != fingerprint[2]:
      raise ValueError("log source changed")

    remaining_agg_events = agg_max_events - used_events
    remaining_agg_decode = agg_max_decode - used_decode
    if remaining_agg_events <= 0 or remaining_agg_decode <= 0:
      raise ValueError("log decoding budget exceeded")
    max_file_events = min(budget["max_events"], remaining_agg_events) if continuous else remaining_agg_events
    max_file_decode = min(budget["max_decompressed_bytes"], remaining_agg_decode) if continuous else remaining_agg_decode

    coverage = validate_closed_log(path, max_event_bytes=budget["max_event_bytes"], max_events=max_file_events,
                                   max_decompressed_bytes=max_file_decode, cancelled=cancelled)
    charged = coverage["decompressed_bytes"] * 2
    if charged > max_file_decode or (continuous and charged > remaining_agg_decode):
      raise ValueError("log decompression pass budget exceeded")

    source_id = "log-" + before[:16]
    if any(receipt["sha256"] == before for receipt in receipts):
      raise ValueError("duplicate log content")
    if any(receipt["id"] == source_id for receipt in receipts):
      raise ValueError("log source identity collision")

    inferred_route, inferred_segment = _route_segment(path)
    if source_identities is not None:
      if before not in source_identities:
        raise ValueError("unverified_log_source")
      v_info = source_identities[before]
      v_route, v_segment = v_info["route"], v_info["segment"]
      if inferred_route is not None and inferred_route != v_route:
        raise ValueError("verified_log_route_conflict")
      if inferred_segment is not None and inferred_segment != v_segment:
        raise ValueError("verified_log_segment_conflict")
      route, segment = v_route, v_segment
    else:
      route, segment = inferred_route, inferred_segment

    car_coverage = coverage["services"].get("carState", {})
    car_min_ns, car_max_ns = car_coverage.get("mono_min_ns"), car_coverage.get("mono_max_ns")

    if continuous:
      if route is None or segment is None:
        raise ValueError("continuous_route_or_segment_missing")
      if car_min_ns is None or car_max_ns is None:
        raise ValueError("continuous_car_state_missing")
      if prev_route is not None:
        if route != prev_route:
          raise ValueError("continuous_route_mismatch")
        if segment != prev_segment + 1:
          raise ValueError("continuous_segment_not_consecutive")
        if car_min_ns <= prev_car_max_ns:
          raise ValueError("continuous_time_overlap_or_regression")
        if car_min_ns - prev_car_max_ns > max_age:
          raise ValueError("continuous_gap_exceeded")
      prev_route, prev_segment, prev_car_max_ns = route, segment, car_max_ns

    lateral.begin(source_id, route, segment, car_coverage)

    if continuous:
      if connected_normalizer is None:
        initial_normalizer_epoch = source_id
        connected_normalizer = EventNormalizer(epoch=source_id, max_age_ns=max_age)
        connected_cache = {}
        connected_observations = {axis: [] for axis in AXES}
      normalizer = connected_normalizer
      cache = connected_cache
      observations = connected_observations
    else:
      normalizer = EventNormalizer(epoch=source_id, max_age_ns=max_age)
      cache, observations = {}, {axis: [] for axis in AXES}

    file_rows, regressions = [], 0
    for event in iter_events(path, services=_SELECTED, max_event_bytes=budget["max_event_bytes"],
                             max_events=coverage["event_count"], max_decompressed_bytes=coverage["decompressed_bytes"], cancelled=cancelled):
      _check_cancelled(cancelled)
      service, stamp, data = event["service"], event["mono_ns"], event["data"]
      if include_pid_inputs:
        event["source_id"] = source_id
      regressions += int(not event["service_time_ordered"])
      if service == "initData":
        prev_cp = lateral.cp
        prev_builder = lateral.builder
        lateral.header(event)
        snapshots.append({"source_id": source_id, "mono_ns": stamp, "event_valid": event["valid"], **data})
        if len(snapshots) > 64:
          raise ValueError("log init snapshot count limit exceeded")
        if continuous:
          cp = event["data"].get("recorded_car_params")
          valid_cp = (event["valid"] is True and type(cp) is dict and cp.get("state") == "observed")
          if not valid_cp or (prev_cp is not None and prev_cp != cp):
            if prev_cp is not None or prev_builder is not None:
              connected_cache.clear()
              normalizer_epoch_splits += 1
              last_anchor = normalizer._last_anchor
              connected_normalizer = EventNormalizer(epoch=f"{initial_normalizer_epoch}-cp-{normalizer_epoch_splits}", max_age_ns=max_age)
              connected_normalizer._last_anchor = last_anchor
              normalizer = connected_normalizer
        continue

      lateral.feed(event)
      if service != "carState":
        previous = cache.get(service)
        if previous is None or stamp > previous["mono_ns"]:
          cache[service] = event
      normalized = normalizer.feed(service, stamp, event["valid"], data) if service in SERVICES else []
      if not normalized:
        continue
      if len(all_rows) + len(file_rows) >= budget["max_samples"] or (len(all_rows) + len(file_rows) + 1) * 2 > analysis_profile.max_samples:
        raise ValueError("log sample budget exceeded")
      for observation in normalized:
        observation["context"] = {key: observation["context"][key] for key in ("saturated", "output_limited")
                                  if key in observation["context"]}
        if not streaming:
          observations[observation["axis"]].append(Observation(**observation))
      signals, lateral_observation = lateral.observe(stamp)
      signals = {**_signals(data, stamp, cache, max_age, include_pid_inputs), **signals}
      if include_pid_inputs and "sent_torque_raw" in signals:
        lateral_observation["provenance"]["pid_output_source_id"] = cache["carOutput"].get("source_id")
      if retained_signals is not None:
        signals = {name: value for name, value in signals.items() if name in retained_signals}
        # Observe validated every support; static packing needs no timestamp arrays.
        lateral_observation["provenance"].pop("supports", None)
      row = {"mono_ns": stamp, "route_id": route or source_id, "source_id": source_id,
                        "signals": signals,
                        "lateral_observation": lateral_observation, "excluded_reasons": {}}
      if streaming:
        streamed_samples += 1
        if streamed_samples > budget["max_samples"] or streamed_samples * 2 > analysis_profile.max_samples:
          raise ValueError("log sample budget exceeded")
        stream_sink(row, normalized)
      else:
        file_rows.append(row)

    if not continuous:
      if file_rows:
        finalized = file_rows[-1]["mono_ns"] - analysis_profile.pre_margin_ns - analysis_profile.dependency_ns
        for axis, values in observations.items():
          masks = _mask_axis(values, analysis_profile)
          for row, reasons in zip(file_rows, masks):
            row["excluded_reasons"][axis] = sorted(reasons | ({"unfinalized_tail"} if row["mono_ns"] > finalized else set()))
      else:
        finalized = None
    else:
      finalized = None

    after, after_size = hash_file(path, max_file_bytes if continuous else remaining_agg_bytes, cancelled)
    if before != after or size != after_size or _fingerprint(path) != fingerprint:
      raise ValueError("log source changed")

    receipts.append({"id": source_id, "sha256": before, "bytes": size, "route": route, "segment": segment, "closed": True,
                     "event_count": coverage["event_count"], "decompressed_bytes": coverage["decompressed_bytes"],
                     "decoded_bytes_charged": charged, "service_counts": {name: value["count"] for name, value in coverage["services"].items()},
                     "mono_min_ns": car_coverage.get("mono_min_ns"), "mono_max_ns": car_coverage.get("mono_max_ns"),
                     "finalized_through_ns": finalized, "code_commit": coverage["recorded_code_commit"],
                     "schema_match": "unverified", "selected_service_regressions": regressions})
    file_row_groups.append(file_rows)
    all_rows.extend(file_rows)
    used_bytes += size
    used_decode += charged
    used_events += coverage["event_count"]

  if continuous and not streaming:
    if all_rows:
      run_finalized = all_rows[-1]["mono_ns"] - analysis_profile.pre_margin_ns - analysis_profile.dependency_ns
      for axis, values in connected_observations.items():
        masks = _mask_axis(values, analysis_profile)
        for row, reasons in zip(all_rows, masks):
          row["excluded_reasons"][axis] = sorted(reasons | ({"unfinalized_tail"} if row["mono_ns"] > run_finalized else set()))
    else:
      run_finalized = None
    for group, receipt in zip(file_row_groups, receipts):
      if group and run_finalized is not None:
        receipt["finalized_through_ns"] = min(group[-1]["mono_ns"], run_finalized)
      else:
        receipt["finalized_through_ns"] = None

  result = {"schema_version": 1, "rows": all_rows, "source_receipts": receipts, "initial_snapshots": snapshots,
          "source_fields": {**_WIRE_FIELDS, **({"sent_torque_raw": "carOutput.actuatorsOutput.torque_positive_pid_domain",
            "lateral_accel_kinematic": "livePose.original_timestamp_calibrated_yaw*carState.response_time_v_no_gravity_correction"} if include_pid_inputs else {})}, "limitations": list(LIMITATIONS),
          "join_max_age_ns": max_age, "mask_version": analysis_profile.profile_id,
          "budget_usage": {"input_bytes": used_bytes, "decoded_bytes_charged": used_decode, "events": used_events, "samples": len(all_rows)}}
  if streaming:
    result["budget_usage"]["samples"] = streamed_samples
    result["stream_state"] = _stream_state(lateral, connected_normalizer, connected_cache,
      (prev_route, prev_segment, prev_car_max_ns), initial_normalizer_epoch, normalizer_epoch_splits)
  return result
