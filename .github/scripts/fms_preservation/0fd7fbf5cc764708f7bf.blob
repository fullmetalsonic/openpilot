"""Read-only parking/offroad observations; no shutdown or control decisions.

Thresholds are a named host profile, not device qualification. Offroad messages
are auxiliary evidence and can neither confirm parking nor refresh car data.
"""
from collections import deque
from copy import deepcopy
import math
import re
import time


PROFILE_ID = "parking-host-unvalidated-v1"
HOLD_NS = 2_000_000_000
CAR_MAX_AGE_NS = 250_000_000
CONTROL_MAX_AGE_NS = 200_000_000
DEVICE_MAX_AGE_NS = 2_000_000_000
MAX_PARK_SPEED = 0.1
STATES = {"unknown", "moving", "park_candidate", "park_observed"}
SOURCES = {"native_event", "synthetic_event"}


def unknown_parking(source_kind="native_event", reason="parking_source_unavailable"):
  return {"state": "unknown", "reason": reason, "observed_mono_ns": None, "offroad_state": "unknown",
          "source_kind": source_kind, "profile_id": PROFILE_ID}


def validate_parking(value, *, source_kind=None, historical=False):
  if type(value) is not dict or set(value) != {"state", "reason", "observed_mono_ns", "offroad_state", "source_kind", "profile_id"}:
    raise ValueError("invalid_parking_fields")
  if (any(type(value[key]) is not str for key in ("state", "source_kind", "profile_id", "offroad_state"))
      or value["state"] not in STATES or value["source_kind"] not in SOURCES or value["profile_id"] != PROFILE_ID
      or value["offroad_state"] not in {"unknown", "onroad_observed", "offroad_observed"}
      or type(value["reason"]) is not str or re.fullmatch(r"[a-z_]{1,128}", value["reason"]) is None):
    raise ValueError("invalid_parking_state")
  stamp = value["observed_mono_ns"]
  if stamp is not None and (type(stamp) is not int or not 0 <= stamp <= 2**63 - 1):
    raise ValueError("invalid_parking_timestamp")
  if (value["state"] != "unknown" and stamp is None) or (historical and value["state"] != "park_observed"):
    raise ValueError("invalid_parking_observation")
  if source_kind is not None and value["source_kind"] != source_kind:
    raise ValueError("parking_source_mismatch")
  return deepcopy(value)


class ParkingObserver:
  def __init__(self, *, source_kind="native_event", clock_ns=time.monotonic_ns, source_clock_ns=None):
    if source_kind not in SOURCES:
      raise ValueError("invalid_parking_source")
    self.source_kind, self.clock_ns = source_kind, clock_ns
    self.source_clock_ns = source_clock_ns or clock_ns
    self._car = self._device = self._basis_control = None
    self._controls = deque(maxlen=64)
    self._last = {}
    self._candidate_start = None
    self._state, self._reason, self._observed = "unknown", "parking_source_unavailable", None
    self._historical = None
    self._stopped = False

  def _unknown(self, reason):
    self._state, self._reason, self._observed, self._candidate_start = "unknown", reason, None, None

  def invalidate(self, reason="parking_source_invalidated"):
    self._car = self._basis_control = None
    self._controls.clear()
    self._unknown(reason if re.fullmatch(r"[a-z_]{1,128}", reason) else "parking_source_invalidated")

  def _fresh(self, event, max_age_ns, now):
    return (0 <= now - event["received_ns"] <= max_age_ns
            and 0 <= self.source_clock_ns() - event["mono_ns"] <= max_age_ns)

  def feed(self, service, mono_ns, valid, data, recv_ns=None):
    if self._stopped:
      return
    if service not in {"carState", "carControl", "deviceState"} or type(mono_ns) is not int or mono_ns < 0 or type(valid) is not bool or type(data) is not dict:
      raise ValueError("invalid_parking_event")
    self.tick()
    previous = self._last.get(service)
    if previous is not None and mono_ns <= previous:
      if service == "deviceState":
        self._device = None
      else:
        self.invalidate("parking_source_time_regression")
      return
    self._last[service] = mono_ns
    now = self.clock_ns()
    if recv_ns is not None and (type(recv_ns) is not int or recv_ns < 0):
      raise ValueError("invalid_parking_receive_timestamp")
    event = {"mono_ns": mono_ns, "received_ns": now if recv_ns is None else recv_ns, "valid": valid}
    if service == "deviceState":
      event["started"] = data.get("started")
      self._device = event
      return
    if service == "carControl":
      if previous is not None and mono_ns - previous > CONTROL_MAX_AGE_NS:
        self._unknown("parking_control_source_gap")
      event.update({key: data.get(key) for key in ("enabled", "latActive", "longActive")})
      self._controls.append(event)
      if not valid or not self._fresh(event, CONTROL_MAX_AGE_NS, now) or any(type(event[key]) is not bool for key in ("enabled", "latActive", "longActive")):
        self._unknown("parking_control_unknown")
      elif any(event[key] for key in ("enabled", "latActive", "longActive")):
        self._unknown("parking_control_active")
      return
    if previous is not None and mono_ns - previous > CAR_MAX_AGE_NS:
      self._unknown("parking_car_source_gap")
    event.update({key: data.get(key) for key in ("gearShifter", "standstill", "vEgo", "canValid", "canTimeout")})
    self._car = event
    speed = event["vEgo"]
    if (not valid or not self._fresh(event, CAR_MAX_AGE_NS, now) or event["canValid"] is not True
        or event["canTimeout"] is True or type(speed) not in (int, float) or not math.isfinite(speed)):
      self._unknown("parking_car_unknown")
      return
    if abs(speed) > MAX_PARK_SPEED or event["standstill"] is False:
      self._state, self._reason, self._observed, self._candidate_start = "moving", "parking_motion_observed", mono_ns, None
      return
    if event["gearShifter"] != "park" or event["standstill"] is not True:
      self._unknown("parking_stationary_park_unconfirmed")
      return
    control = next((item for item in reversed(self._controls) if item["mono_ns"] <= mono_ns), None)
    if (control is None or not control["valid"] or mono_ns - control["mono_ns"] > CONTROL_MAX_AGE_NS
        or not self._fresh(control, CONTROL_MAX_AGE_NS, now)
        or any(type(control[key]) is not bool for key in ("enabled", "latActive", "longActive"))):
      self._unknown("parking_control_unknown")
      return
    if any(control[key] for key in ("enabled", "latActive", "longActive")):
      self._unknown("parking_control_active")
      return
    self._basis_control = control
    if self._candidate_start is None:
      self._candidate_start = mono_ns
    self._state = "park_observed" if mono_ns - self._candidate_start >= HOLD_NS else "park_candidate"
    self._reason = "parking_park_observed" if self._state == "park_observed" else "parking_hold_pending"
    self._observed = mono_ns
    if self._state == "park_observed":
      self._historical = self.snapshot()

  def tick(self):
    if self._stopped:
      return
    now = self.clock_ns()
    if self._car is not None and not self._fresh(self._car, CAR_MAX_AGE_NS, now):
      self._unknown("parking_car_stale")
    elif self._state in {"park_candidate", "park_observed"} and (self._basis_control is None or not self._fresh(self._basis_control, CONTROL_MAX_AGE_NS, now)):
      self._unknown("parking_control_stale")

  def snapshot(self):
    self.tick()
    offroad, device_stamp = "unknown", None
    device = self._device
    if not self._stopped and device is not None and device["valid"] and type(device["started"]) is bool and self._fresh(device, DEVICE_MAX_AGE_NS, self.clock_ns()):
      offroad = "onroad_observed" if device["started"] else "offroad_observed"
      device_stamp = device["mono_ns"]
    return validate_parking({"state": self._state, "reason": self._reason, "observed_mono_ns": self._observed if self._observed is not None else device_stamp,
      "offroad_state": offroad, "source_kind": self.source_kind, "profile_id": PROFILE_ID})

  def last_park_observation(self):
    return deepcopy(self._historical)

  def stop(self):
    self._stopped = True
    self._device = None
    self._unknown("parking_session_ended")
