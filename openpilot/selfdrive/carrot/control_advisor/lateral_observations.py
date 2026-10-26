"""Pure, bounded lateral measurements from one explicitly identified recording.

The caller binds the recorded CP, settings epoch and consumer evidence. These
measurements do not establish a selected torque callback or a parameter ACK.
"""
from bisect import bisect_left, bisect_right
from collections import deque
from copy import deepcopy
from itertools import islice
import math
import re


CP_FIELDS = ("mass", "rotationalInertia", "wheelbase", "centerToFront", "steerRatioRear",
             "tireStiffnessFront", "tireStiffnessRear", "steerRatio")
SERVICES = frozenset(("carState", "carOutput", "carControl", "liveParameters", "liveCalibration",
                      "livePose", "liveDelay", "carParams"))
G = 9.81
ENGAGEMENT_NS = 2_000_000_000
MAX_SUPPORTS = 4096


class _Unavailable(ValueError):
  pass


def _number(value):
  if type(value) not in (int, float) or abs(value) > 1e100 or not math.isfinite(value):
    raise _Unavailable("invalid_numeric_input")
  return float(value)


def _timestamp(value):
  if type(value) is not int or not 0 <= value <= 2**63 - 1:
    raise ValueError("invalid_timestamp")
  return value


def _measurement(value):
  if type(value) is not dict or value.get("valid") is not True:
    raise _Unavailable("pose_measurement_invalid")
  return [_number(value.get(axis)) for axis in ("x", "y", "z")]


def _scalar(value):
  if value is None or type(value) is bool:
    return value
  if type(value) in (int, float):
    return value if abs(value) <= 1e12 and math.isfinite(value) else None
  return value if type(value) is str and len(value) <= 64 else None


def _project(service, data):
  """Copy only scalar fields needed here, never arbitrary event payloads."""
  if type(data) is not dict:
    raise _Unavailable("event_data_invalid")
  if service == "carState":
    return {key: _scalar(data.get(key)) for key in ("vEgo", "steeringAngleDeg", "steeringPressed")}
  if service == "carOutput":
    output = data.get("actuatorsOutput")
    return {"torque": _scalar(output.get("torque")) if type(output) is dict else None}
  if service == "carControl":
    return {"latActive": _scalar(data.get("latActive"))}
  if service == "livePose":
    result = {key: _scalar(data.get(key)) for key in ("inputsOK", "sensorsOK", "posenetOK")}
    stamp = data.get("timestamp")
    result["timestamp"] = stamp if type(stamp) is int and 0 <= stamp <= 2**63 - 1 else None
    for key in ("angularVelocityDevice", "orientationNED"):
      value = data.get(key)
      result[key] = {name: _scalar(value.get(name)) for name in ("x", "y", "z", "valid")} if type(value) is dict else None
    return result
  if service == "liveCalibration":
    rpy = data.get("rpyCalib")
    return {"calStatus": _scalar(data.get("calStatus")), "rpyCalib": [_scalar(value) for value in rpy] if type(rpy) in (list, tuple) and len(rpy) == 3 else None}
  if service == "liveDelay":
    return {key: _scalar(data.get(key)) for key in ("lateralDelay", "status", "validBlocks")}
  if service == "liveParameters":
    return {key: _scalar(data.get(key)) for key in ("valid", "sensorValid", "posenetValid", "angleOffsetDeg",
            "stiffnessFactor", "steerRatio", "roll", "angleOffsetValid", "steerRatioValid", "stiffnessFactorValid") if key in data}
  return {key: _scalar(data.get(key)) for key in CP_FIELDS}


class LateralObservationBuilder:
  def __init__(self, source_cp, *, max_age_ns, max_alignment_ns, max_buffer_events, include_pid_inputs=False):
    if type(include_pid_inputs) is not bool:
      raise ValueError("invalid_pid_input_mode")
    self.include_pid_inputs = include_pid_inputs
    if (type(source_cp) is not dict or set(source_cp) != {"schema_version", "source_id", "epoch_id", "config_sha256", "mono_ns", "values"}
        or source_cp.get("schema_version") != 1 or type(source_cp.get("schema_version")) is not int):
      raise ValueError("recorded_cp_required")
    for key in ("source_id", "epoch_id"):
      if type(source_cp[key]) is not str or not 1 <= len(source_cp[key]) <= 128 or not source_cp[key].strip():
        raise ValueError("recorded_cp_identity_invalid")
    if type(source_cp["config_sha256"]) is not str or re.fullmatch("[a-f0-9]{64}", source_cp["config_sha256"]) is None:
      raise ValueError("recorded_cp_identity_invalid")
    _timestamp(source_cp["mono_ns"])
    if type(source_cp["values"]) is not dict or set(source_cp["values"]) != set(CP_FIELDS):
      raise ValueError("recorded_cp_values_invalid")
    cp = {key: _number(source_cp["values"][key]) for key in CP_FIELDS}
    if (any(cp[key] <= 0 for key in ("mass", "rotationalInertia", "wheelbase", "tireStiffnessFront", "tireStiffnessRear", "steerRatio"))
        or not 0 < cp["centerToFront"] < cp["wheelbase"] or abs(cp["steerRatioRear"]) >= 1):
      raise ValueError("recorded_cp_values_invalid")
    for value in (max_age_ns, max_alignment_ns):
      if type(value) is not int or not 0 < value <= 60_000_000_000:
        raise ValueError("invalid_time_bound")
    if type(max_buffer_events) is not int or not 1 <= max_buffer_events <= 100_000:
      raise ValueError("invalid_buffer_bound")
    self._source = deepcopy(source_cp)
    self._cp = cp
    self.max_age_ns, self.max_alignment_ns = max_age_ns, max_alignment_ns
    self._events = deque(maxlen=max_buffer_events)
    self._by_service = {service: deque() for service in SERVICES}
    self._keys = {service: deque() for service in SERVICES}
    self._last = {}
    self._cp_invalid_from = None

  @property
  def buffer_count(self):
    return len(self._events)

  def feed(self, event):
    if type(event) is not dict:
      raise ValueError("invalid_event")
    service = event.get("service")
    if type(service) is not str or service not in SERVICES:
      return
    stamp = _timestamp(event.get("mono_ns"))
    previous = self._last.get(service, stamp)
    effective = max(stamp, previous)
    self._last[service] = effective
    reason = None
    if event.get("service_time_ordered") is not True or stamp < previous:
      reason = "source_time_regression"
    elif event.get("valid") is not True:
      reason = "source_event_invalid"
    try:
      data = _project(service, event.get("data"))
    except _Unavailable as exc:
      data, reason = {}, str(exc)
    if service == "carParams":
      try:
        if reason or any(_number(data.get(key)) != self._cp[key] for key in CP_FIELDS):
          raise _Unavailable("recorded_cp_changed")
      except _Unavailable:
        self._cp_invalid_from = effective if self._cp_invalid_from is None else min(effective, self._cp_invalid_from)
    item = {"service": service, "mono_ns": stamp, "effective_ns": effective, "data": data, "reason": reason}
    if self.include_pid_inputs:
      item["source_id"] = event.get("source_id")
    if len(self._events) == self._events.maxlen:
      oldest = self._events[0]
      old_svc = oldest["service"]
      self._by_service[old_svc].popleft()
      self._keys[old_svc].popleft()
    self._events.append(item)
    self._by_service[service].append(item)
    self._keys[service].append(effective)

  def _history(self, service, anchor):
    events = self._by_service.get(service)
    if not events:
      return []
    idx = bisect_right(self._keys[service], anchor)
    if idx == 0:
      return []
    if idx == len(events):
      return list(events)
    return list(islice(events, idx))

  def _latest(self, service, at, supports):
    events = self._by_service.get(service)
    if not events:
      raise _Unavailable(service + "_missing")
    idx = bisect_right(self._keys[service], at)
    if idx == 0:
      raise _Unavailable(service + "_missing")
    event = events[idx - 1]
    self._use(event, supports)
    if at - event["mono_ns"] > self.max_age_ns:
      raise _Unavailable(service + "_stale")
    return event

  @staticmethod
  def _use(event, supports):
    if event["reason"]:
      raise _Unavailable(event["reason"])
    used = supports.setdefault(event["service"], set())
    used.add(event["mono_ns"])
    if sum(len(items) for items in supports.values()) > MAX_SUPPORTS:
      raise _Unavailable("provenance_support_budget")

  def _interpolate(self, service, field, at, anchor, allowance, supports):
    events = self._by_service.get(service)
    if not events:
      raise _Unavailable(service + "_alignment_support_missing")
    keys = self._keys[service]
    anchor_idx = bisect_right(keys, anchor)
    left_idx = bisect_right(keys, at, 0, anchor_idx) - 1
    right_idx = bisect_left(keys, at, 0, anchor_idx)
    if left_idx < 0 or right_idx >= anchor_idx:
      raise _Unavailable(service + "_alignment_support_missing")
    left, right = events[left_idx], events[right_idx]
    self._use(left, supports)
    self._use(right, supports)
    if right["mono_ns"] - left["mono_ns"] > self.max_alignment_ns:
      raise _Unavailable(service + "_alignment_gap")
    if any(anchor - event["mono_ns"] > self.max_age_ns + allowance for event in (left, right)):
      raise _Unavailable(service + "_alignment_stale")
    a, b = _number(left["data"].get(field)), _number(right["data"].get(field))
    span = right["mono_ns"] - left["mono_ns"]
    value = a if span == 0 else a + (b - a) * ((at - left["mono_ns"]) / span)
    return _number(value), max(left["mono_ns"], right["mono_ns"])

  def _continuous(self, service, key, expected, start, end, supports):
    events = self._by_service.get(service)
    if not events:
      raise _Unavailable(service + "_engagement_support_missing")
    keys = self._keys[service]
    end_idx = bisect_right(keys, end)
    first_idx = bisect_right(keys, start, 0, end_idx) - 1
    if first_idx < 0:
      raise _Unavailable(service + "_engagement_support_missing")
    previous = start
    for event in islice(events, first_idx, end_idx):
      self._use(event, supports)
      if event["data"].get(key) is not expected:
        raise _Unavailable("steering_override" if key == "steeringPressed" else "lateral_inactive")
      if abs(event["mono_ns"] - previous) > self.max_age_ns:
        raise _Unavailable(service + "_engagement_gap")
      previous = event["mono_ns"]
    if end - previous > self.max_age_ns:
      raise _Unavailable(service + "_engagement_gap")

  def observe(self, anchor_ns):
    anchor = _timestamp(anchor_ns)
    signals, supports, availability = {}, {}, {}
    provenance = {key: self._source[key] for key in ("source_id", "epoch_id", "config_sha256")}
    provenance.update(response_mono_ns=None, pose_event_mono_ns=None, supports={},
                      alignment={"kind": "none", "delay_ns": None}, consumer_mapping_verified=False,
                      parameter_application_ack=False, closed_loop_improvement_proven=False)

    def signal(name, value, unit, source, stamp):
      signals[name] = {"value": _number(value), "unit": unit, "source": source, "source_mono_ns": stamp}

    def state(branch, reason=None):
      availability[branch] = {"available": reason is None, "reason_codes": [] if reason is None else [reason]}

    try:
      if anchor < self._source["mono_ns"]:
        raise _Unavailable("recorded_cp_future")
      if self._cp_invalid_from is not None and anchor >= self._cp_invalid_from:
        raise _Unavailable("recorded_cp_changed")
      pose = self._latest("livePose", anchor, supports)
      data = pose["data"]
      response = _timestamp(data.get("timestamp"))
      if response < self._source["mono_ns"] or response > pose["mono_ns"]:
        raise _Unavailable("pose_timestamp_invalid")
      if anchor - response > self.max_age_ns:
        raise _Unavailable("pose_response_stale")
      if any(data.get(key) is not True for key in ("inputsOK", "sensorsOK", "posenetOK")):
        raise _Unavailable("pose_inputs_invalid")
      angular, orientation = _measurement(data.get("angularVelocityDevice")), _measurement(data.get("orientationNED"))
      calibration = self._latest("liveCalibration", response, supports)["data"]
      if calibration.get("calStatus") != "calibrated" or calibration.get("rpyCalib") is None:
        raise _Unavailable("calibration_invalid")
      roll, pitch, yaw = [_number(value) for value in calibration["rpyCalib"]]
      cx, sx, cy, sy, cz, sz = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
      calibrated_yaw = _number((cz * sy * cx + sz * sx) * angular[0] + (sz * sy * cx - cz * sx) * angular[1] + cy * cx * angular[2])
      provenance.update(response_mono_ns=response, pose_event_mono_ns=pose["mono_ns"])
      signal("yaw_rate_calibrated", calibrated_yaw, "rad/s", "independent_measurement", response)
      state("pose")
    except (ValueError, OverflowError) as exc:
      reason = str(exc) if isinstance(exc, _Unavailable) else "pose_timestamp_invalid"
      for branch in ("pose", "torque_response", "geometry", "manual_geometry"):
        state(branch, reason)
      return self._result(signals, availability, provenance, supports)

    if self.include_pid_inputs:
      try:
        speed, speed_stamp = self._interpolate("carState", "vEgo", response, anchor, 0, supports)
        if speed <= 0:
          raise _Unavailable("positive_speed_required")
        # Raw kinematic response at the pose clock: no gravity correction or
        # actuator-delay shift. Repeated poses retain their original timestamp.
        signal("lateral_accel_kinematic", speed * calibrated_yaw, "m/s^2", "independent_measurement", response)
        provenance["pid_kinematic"] = {"source_id": pose.get("source_id"), "source_mono_ns": response,
          "pose_event_mono_ns": pose["mono_ns"], "speed_support_mono_ns": speed_stamp,
          "calibration_valid": True, "gravity_corrected": False}
        state("pid_kinematic")
      except (ValueError, OverflowError) as exc:
        state("pid_kinematic", str(exc) if isinstance(exc, _Unavailable) else "kinematic_numeric_invalid")

    try:
      delay = self._latest("liveDelay", response, supports)["data"]
      if delay.get("status") != "estimated" or type(delay.get("validBlocks")) is not int or delay["validBlocks"] <= 0:
        raise _Unavailable("lateral_delay_unestimated")
      seconds = _number(delay.get("lateralDelay"))
      if seconds < 0 or seconds * 1e9 > self.max_alignment_ns:
        raise _Unavailable("lateral_delay_out_of_bounds")
      lag = round(seconds * 1e9)
      target = response - lag
      if target < 0:
        raise _Unavailable("lateral_delay_support_missing")
      self._continuous("carControl", "latActive", True, response - ENGAGEMENT_NS - lag, response, supports)
      self._continuous("carState", "steeringPressed", False, response - ENGAGEMENT_NS - lag, response, supports)
      speed, _ = self._interpolate("carState", "vEgo", target, anchor, lag, supports)
      torque, stamp = self._interpolate("carOutput", "torque", target, anchor, lag, supports)
      if speed <= 0:
        raise _Unavailable("positive_speed_required")
      accel = _number(speed * calibrated_yaw - math.sin(orientation[0]) * G)
      signal("sent_torque", -torque, "1", "controller_output", stamp)
      signal("lateral_accel_independent", accel, "m/s^2", "independent_measurement", response)
      provenance["alignment"] = {"kind": "recorded_lateral_delay", "delay_ns": lag}
      state("torque_response")
    except (ValueError, OverflowError) as exc:
      state("torque_response", str(exc) if isinstance(exc, _Unavailable) else "torque_numeric_invalid")

    try:
      live_event = self._latest("liveParameters", response, supports)
      live = live_event["data"]
      if (any(live.get(key) is not True for key in ("valid", "sensorValid", "posenetValid"))
          or any(key in live and live[key] is not True for key in ("angleOffsetValid", "stiffnessFactorValid"))):
        raise _Unavailable("live_parameters_invalid")
      speed, speed_stamp = self._interpolate("carState", "vEgo", response, anchor, 0, supports)
      angle, angle_stamp = self._interpolate("carState", "steeringAngleDeg", response, anchor, 0, supports)
      stiffness = _number(live.get("stiffnessFactor"))
      road_roll, offset = _number(live.get("roll")), _number(live.get("angleOffsetDeg"))
      if min(speed, stiffness) <= 0:
        raise _Unavailable("geometry_positive_values_required")
      cp = self._cp
      front, rear = stiffness * cp["tireStiffnessFront"], stiffness * cp["tireStiffnessRear"]
      slip = cp["mass"] * (front * cp["centerToFront"] - rear * (cp["wheelbase"] - cp["centerToFront"])) / (cp["wheelbase"] ** 2 * front * rear)
      denominator = 1 - slip * speed ** 2
      if not math.isfinite(denominator) or abs(denominator) <= 1e-12:
        raise _Unavailable("vehicle_model_singular")
      factor = _number((1 - cp["steerRatioRear"]) / denominator / cp["wheelbase"])
      roll_curvature = _number(0. if abs(slip) < 1e-6 else G * road_roll * slip / denominator)
      corrected = _number(math.radians(angle - offset))
      # Prepare all values before publishing: failed geometry never leaves partial signals.
      values = (("curvature_independent", calibrated_yaw / speed, "1/m", "independent_measurement", response),
                ("lateral_accel_kinematic", speed * calibrated_yaw, "m/s^2", "independent_measurement", response),
                ("steering_angle_corrected", corrected, "rad", "derived_input", max(angle_stamp, live_event["mono_ns"])),
                ("vm_curvature_factor", factor, "1/m", "derived_input", max(speed_stamp, live_event["mono_ns"])),
                ("roll_curvature", roll_curvature, "1/m", "derived_input", max(speed_stamp, live_event["mono_ns"])))
      for args in values:
        _number(args[1])
      for args in values:
        signal(*args)
      state("manual_geometry")
      if live.get("steerRatioValid", True) is not True:
        raise _Unavailable("live_parameters_invalid")
      ratio = _number(live.get("steerRatio"))
      if ratio <= 0:
        raise _Unavailable("geometry_positive_values_required")
      signal("live_steer_ratio", ratio, "1", "learned_estimate", live_event["mono_ns"])
      state("geometry")
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
      if "manual_geometry" not in availability:
        state("manual_geometry", str(exc) if isinstance(exc, _Unavailable) else "vehicle_model_numeric_invalid")
      state("geometry", str(exc) if isinstance(exc, _Unavailable) else "vehicle_model_numeric_invalid")
    return self._result(signals, availability, provenance, supports)

  @staticmethod
  def _result(signals, availability, provenance, supports):
    provenance["supports"] = {key: sorted(values) for key, values in supports.items()}
    return {"signals": signals, "availability": availability,
            "reason_codes": sorted({reason for item in availability.values() for reason in item["reason_codes"]}),
            "provenance": provenance}
