"""Bounded explicit analysis intents; recording observations never run a model."""
from collections import OrderedDict
from copy import deepcopy
import re


STATES = {"recording", "queued", "running", "waiting_for_tail", "ready", "failed", "incomplete"}


def initial_control():
  return {"schema_version": 1, "mode": "manual_or_park", "scope": "trip_since_apply", "state": "recording",
          "request_id": None, "trigger": None, "range_start_ns": None, "range_end_ns": None, "apply_cut_ns": None,
          "progress": {"completed_sources": 0, "total_sources": 0}, "reason_codes": ["trip_observation_required"], "can_analyze": False}


def validate_control(value):
  expected = initial_control()
  if type(value) is not dict or set(value) != set(expected) or value["schema_version"] != 1 or value["mode"] != expected["mode"] or value["scope"] != expected["scope"] or value["state"] not in STATES:
    raise ValueError("invalid_analysis_control")
  for key in ("range_start_ns", "range_end_ns", "apply_cut_ns"):
    stamp = value[key]
    if stamp is not None and (type(stamp) is not str or re.fullmatch(r"0|[1-9][0-9]{0,18}", stamp) is None or int(stamp) > 2**63 - 1):
      raise ValueError("invalid_analysis_control_time")
  if (value["request_id"] is not None and (type(value["request_id"]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value["request_id"]) is None)
      or value["trigger"] not in {None, "manual", "park"} or type(value["can_analyze"]) is not bool):
    raise ValueError("invalid_analysis_control_request")
  if type(value["progress"]) is not dict or set(value["progress"]) != {"completed_sources", "total_sources"} or any(type(n) is not int or not 0 <= n <= 128 for n in value["progress"].values()):
    raise ValueError("invalid_analysis_control_progress")
  if type(value["reason_codes"]) is not list or len(value["reason_codes"]) > 16 or any(type(reason) is not str or re.fullmatch(r"[a-z0-9_]{1,128}", reason) is None for reason in value["reason_codes"]):
    raise ValueError("invalid_analysis_control_reasons")
  return deepcopy(value)


def validate_binding(value):
  if (type(value) is not dict or set(value) != {"trip_binding", "cut_revision"}
      or type(value["cut_revision"]) is not int or value["cut_revision"] < 0
      or type(value["trip_binding"]) is not dict or set(value["trip_binding"]) != {"vehicle_id", "boot_id", "route"}
      or any(type(item) is not str or not 1 <= len(item) <= 256 for item in value["trip_binding"].values())):
    raise ValueError("invalid_analysis_binding")
  return deepcopy(value)


class AnalysisControl:
  """Owned by the recorder lock; at most one active and one latest intent."""
  def __init__(self, session_id, boot_id, *, profile_sha256, objectives_sha256):
    self.session_id, self.boot_id = session_id, boot_id
    self.profile_hash, self.objective_hash = profile_sha256, objectives_sha256
    self.goal_id = None
    self.trip = self.start_ns = self.end_ns = self.cut_ns = None
    self.binding_valid = False
    self.cut_revision = 0
    self.active = self.pending = None
    self.seen = OrderedDict()
    self.armed = False
    self.park_seen = False
    self.revision = 0
    self.public = initial_control()

  def observe(self, stamp, route, identity, parking, nonpark_ns=None):
    self.binding_valid = False
    if type(stamp) is not int or stamp < 0:
      self.public.update(can_analyze=False, reason_codes=["analysis_source_stale"])
      self.armed = False
      return
    vehicle = identity.get("physical_group_id") if type(identity) is dict and identity.get("state") == "observed" else None
    if not vehicle or not self.boot_id or not route or route.get("state") != "observed":
      self.public.update(can_analyze=False, reason_codes=["trip_observation_required"])
      return
    trip = {"vehicle_id": vehicle, "boot_id": self.boot_id, "route": route["route"]}
    self.binding_valid = True
    if trip != self.trip:
      self.trip, self.start_ns, self.end_ns = trip, stamp, stamp
      self.cut_ns, self.cut_revision = None, 0
      self.armed = self.park_seen = False
      self.pending = None
      self.public = initial_control()
      self.revision += 1
    self.end_ns = max(self.end_ns, stamp)
    self.public["can_analyze"] = self.end_ns > max(self.start_ns, self.cut_ns or 0)
    if self.public["state"] == "recording":
      self.public["reason_codes"] = []
    state = parking.get("state") if parking.get("source_kind") == "native_event" else "unknown"
    # Unknown never arms or creates an edge. A confirmed P consumes the armed
    # edge, so stale->P cannot repeat it. Moving observations rearm explicitly.
    if state == "moving" or type(nonpark_ns) is int and 0 <= stamp - nonpark_ns <= 250_000_000:
      self.armed, self.park_seen = True, False
    elif state == "unknown":
      self.armed = False
    elif state == "park_observed" and self.armed and not self.park_seen:
      self.armed, self.park_seen = False, True
      self.request("park_" + str(parking["observed_mono_ns"]), "park")

  def request(self, request_id, trigger="manual"):
    if type(request_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id) is None or trigger not in {"manual", "park"}:
      raise ValueError("invalid_analysis_request")
    if request_id in self.seen:
      previous = self.seen[request_id]
      if previous["profile_sha256"] != self.profile_hash or previous["objectives_sha256"] != self.objective_hash:
        raise ValueError("analysis_request_id_conflict")
      if (self.active is None and self.public["state"] in {"failed", "incomplete"}
          and self.public["request_id"] == request_id and previous["trip_binding"] == self.trip
          and previous["cut_revision"] == self.cut_revision):
        self.pending = deepcopy(previous)
        self._show(previous, "queued")
        self.revision += 1
        return True
      return False
    if len(self.seen) >= 128:
      raise ValueError("analysis_request_budget")
    if not self.public["can_analyze"]:
      raise ValueError("analysis_trip_not_ready")
    intent = {"schema_version": 1, "session_id": self.session_id, "request_id": request_id, "trigger": trigger,
              "range_start_ns": max(self.start_ns, self.cut_ns or 0), "requested_end_ns": self.end_ns,
              "cut_revision": self.cut_revision, "trip_binding": deepcopy(self.trip),
              "profile_sha256": self.profile_hash, "objectives_sha256": self.objective_hash}
    if self.goal_id is not None:
      intent["goal_id"] = self.goal_id
    self.seen[request_id] = deepcopy(intent)
    self.pending = intent
    if self.active is None:
      self._show(intent, "queued")
    self.revision += 1
    return True

  def change_goal(self, profile_sha256, objectives_sha256, goal_id):
    self.profile_hash, self.objective_hash, self.goal_id = profile_sha256, objectives_sha256, goal_id
    self.active = self.pending = None
    self.public.update(state="recording", request_id=None, trigger=None, reason_codes=["analysis_goal_changed"])
    self.revision += 1

  def _show(self, intent, state, reason=None):
    self.public.update(state=state, request_id=intent["request_id"], trigger=intent["trigger"],
                       range_start_ns=str(intent["range_start_ns"]), range_end_ns=str(intent["requested_end_ns"]),
                       apply_cut_ns=None if self.cut_ns is None else str(self.cut_ns), reason_codes=[] if reason is None else [reason])

  def take(self):
    if self.active is None and self.pending is not None:
      self.active, self.pending = self.pending, None
      self._show(self.active, "queued")
      self.revision += 1
      return deepcopy(self.active)
    return None

  def update(self, status):
    if self.active is None:
      return
    state = {"waiting": "waiting_for_tail", "running": "running", "ready": "ready", "partial": "incomplete", "failed": "failed", "stopped": "incomplete"}.get(status["state"], "queued")
    if status.get("reason") == "analysis_continuation":
      state = "running"
    self._show(self.active, state, status.get("reason"))
    attempt = status.get("attempt") or {}
    self.public["progress"] = {"completed_sources": attempt.get("processed_windows", 0), "total_sources": attempt.get("source_count", 0)}
    if state == "ready":
      self.public["progress"]["completed_sources"] = attempt.get("source_count", 0)
    if state in {"ready", "failed", "incomplete"}:
      self.active = None
      self.revision += 1

  def apply_receipt(self, receipt, trip_binding, *, observed_now_ns=None):
    # Receipts arrive only after native durable CAS/readback. Registration and
    # restoration never call this path. A receipt for an earlier trip is inert.
    stamp, revision = receipt.get("stored_mono_ns"), receipt.get("revision")
    if (self.trip is None or any(self.trip.get(key) != value for key, value in trip_binding.items())
        or not {"vehicle_id", "boot_id"} <= set(trip_binding) or type(stamp) is not int or type(revision) is not int
        or self.start_ns is None or not self.start_ns <= stamp <= (self.end_ns if observed_now_ns is None else observed_now_ns)
        or stamp <= (self.cut_ns or 0) or revision <= self.cut_revision):
      return False
    self.cut_ns, self.cut_revision = stamp, revision
    self.active = self.pending = None
    self.public.update(state="recording", apply_cut_ns=str(stamp), can_analyze=False, reason_codes=["recording_since_successful_apply"])
    self.revision += 1
    return True

  def durable(self):
    return {"schema_version": 1, "session_id": self.session_id, "trip_binding": deepcopy(self.trip),
            "state": self.public["state"], "profile_sha256": self.profile_hash,
            "objectives_sha256": self.objective_hash, "goal_id": self.goal_id,
            "range_start_ns": self.start_ns, "range_end_ns": self.end_ns, "cut_ns": self.cut_ns, "cut_revision": self.cut_revision,
            "active": deepcopy(self.active), "pending": deepcopy(self.pending), "requests": list(self.seen.values())}

  def snapshot(self):
    return validate_control(self.public)
