"""Pure shared normalization of four selected event dictionaries.

CarState anchors keep brief driver interventions visible. Held target/response
timestamps remain explicit; steering-model response is not independent yaw data.
"""

import math


SERVICES = ("carControl", "controlsState", "longitudinalPlan", "carState")


def _finite(value):
  return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _boolean(data, key):
  value = data.get(key, False)
  if type(value) is not bool:
    raise ValueError(f"{key} must be boolean")
  return value


def _integer(data, key):
  value = data.get(key, 0)
  if type(value) is not int:
    raise ValueError(f"{key} must be integer")
  return value


class EventNormalizer:
  def __init__(self, *, epoch: str, max_age_ns: int, source: str = "rlog_car_state_anchor"):
    if not isinstance(epoch, str) or not epoch.strip():
      raise ValueError("epoch must be a nonempty string")
    if type(max_age_ns) is not int or max_age_ns <= 0:
      raise ValueError("max_age_ns must be a positive integer")
    if not isinstance(source, str) or not source.strip():
      raise ValueError("source must be a nonempty string")
    self.epoch, self.max_age_ns, self.source = epoch, max_age_ns, source
    self._cache = {}
    self._last_anchor = -1

  def feed(self, service: str, mono_ns: int, valid: bool, data: dict, recv_ns: int | None = None) -> list[dict]:
    if service not in SERVICES:
      raise ValueError("unsupported normalization service")
    if type(mono_ns) is not int or mono_ns < 0 or type(valid) is not bool or not isinstance(data, dict):
      raise ValueError("event requires nonnegative integer mono_ns, boolean valid and dictionary data")
    if recv_ns is not None and (type(recv_ns) is not int or recv_ns < 0):
      raise ValueError("recv_ns must be a nonnegative integer or None")
    if service == "carControl":
      values = {"enabled": _boolean(data, "enabled"), "lateral": _boolean(data, "latActive"),
                "longitudinal": _boolean(data, "longActive")}
    elif service == "controlsState":
      lateral = data.get("lateralControlState", {})
      if not isinstance(lateral, dict):
        raise ValueError("lateralControlState must be a dictionary")
      values = {"torque": "torqueState" in lateral, "active_lane_line": _boolean(data, "activeLaneLine")}
      if values["torque"]:
        torque = lateral["torqueState"]
        if not isinstance(torque, dict):
          raise ValueError("torqueState must be a dictionary")
        values.update(target=_finite(torque.get("desiredLateralAccel")), actual=_finite(torque.get("actualLateralAccel")),
                      active=_boolean(torque, "active"), saturated=_boolean(torque, "saturated"))
    elif service == "longitudinalPlan":
      values = {"target": _finite(data.get("aTarget")), "should_stop": _boolean(data, "shouldStop"),
                "x_state": _integer(data, "xState")}
    else:
      if mono_ns <= self._last_anchor:
        raise ValueError("carState timestamps must strictly increase")
      self._last_anchor = mono_ns
      return self._anchor(mono_ns, valid, data, recv_ns)
    previous = self._cache.get(service)
    if previous is None or mono_ns > previous[0]:
      self._cache[service] = (mono_ns, valid, values, recv_ns)
    return []

  def _anchor(self, time, valid, car, recv_ns):
    state_valid = valid and _boolean(car, "canValid") and not _boolean(car, "canTimeout")
    gear_drive = car.get("gearShifter", "unknown") == "drive"
    hold = _integer(car, "softHoldActive") != 0 or _boolean(car, "brakeHoldActive") or _boolean(car, "parkingBrake")
    buttons = car.get("buttonEvents", [])
    if not isinstance(buttons, list) or any(not isinstance(button, dict) for button in buttons):
      raise ValueError("buttonEvents must be a list of dictionaries")
    cancel = _integer(car, "activateCruise") < 0 or any(button.get("type") == "cancel" and _boolean(button, "pressed") for button in buttons)
    control = self._cache.get("carControl")
    control_fresh = control is not None and control[1] and 0 <= time - control[0] <= self.max_age_ns
    observations = []
    for axis, target_service in (("lateral", "controlsState"), ("longitudinal", "longitudinalPlan")):
      source = self._cache.get(target_service)
      source_fresh = source is not None and source[1] and 0 <= time - source[0] <= self.max_age_ns
      source_values = source[2] if source_fresh else {}
      active = bool(control_fresh and control[2]["enabled"] and control[2][axis])
      if axis == "lateral":
        active = active and bool(source_values.get("torque") and source_values.get("active"))
        override = _boolean(car, "steeringPressed") if state_valid else None
        actual = source_values.get("actual")
        fault = _boolean(car, "steerFaultTemporary") or _boolean(car, "steerFaultPermanent") or _boolean(car, "vehicleSensorsInvalid")
      else:
        override = (_boolean(car, "gasPressed") or _boolean(car, "brakePressed") or _boolean(car, "regenBraking")) if state_valid else None
        actual = _finite(car.get("aEgo"))
        fault = _boolean(car, "accFaulted") or _boolean(car, "vehicleSensorsInvalid")
      target = source_values.get("target")
      sample_valid = bool(state_valid and source_fresh and control_fresh and gear_drive and not hold and not fault and not cancel
                          and target is not None and actual is not None)
      context = {
        "source": self.source, "target_mono_ns": source[0] if source is not None else None,
        "actual_mono_ns": source[0] if source_fresh and axis == "lateral" else time if axis == "longitudinal" else None,
        "target_age_ns": time - source[0] if source is not None else None,
        "car_control_mono_ns": control[0] if control is not None else None,
        "response_kind": "steering_model_derived" if axis == "lateral" else "car_state_acceleration",
        "recovery_basis": "state_only_unvalidated", "gear_drive": gear_drive, "hold": hold,
        "cancel": cancel, "fault": fault, "standstill": _boolean(car, "standstill"), "v_ego": _finite(car.get("vEgo")),
        "source_valid": bool(source_fresh), "control_valid": bool(control_fresh),
        "sample_hold": axis == "lateral", "saturated": source_values.get("saturated"),
        "should_stop": source_values.get("should_stop"), "active_lane_line": source_values.get("active_lane_line"),
      }
      if recv_ns is not None:
        context.update(recv_ns=recv_ns, target_recv_ns=source[3] if source is not None else None,
                       car_control_recv_ns=control[3] if control is not None else None)
      observations.append({"mono_ns": time, "axis": axis, "target": target, "actual": actual, "active": active,
                           "valid": sample_valid, "driver_override": override,
                           "recovery_ready": bool(sample_valid and active and override is False),
                           "epoch": self.epoch, "context": context})
    return observations
