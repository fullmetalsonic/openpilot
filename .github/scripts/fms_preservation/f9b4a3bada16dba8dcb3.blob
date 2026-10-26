"""One explicitly started diagnostic worker; owns no control or Params state."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

from openpilot.common.async_process import process_group_kwargs, stop_process
from .registry import load_registry
from .parking import unknown_parking, validate_parking
from .live import validate_transition


MAX_LINE_BYTES = 256 * 1024
MAX_STDERR_BYTES = 8 * 1024
CONSUMER_PARENT_MAX_AGE_SECONDS = 2.0
ACTIVE_STATES = {"starting", "running", "waiting", "waiting_for_data", "collecting", "evaluating", "degraded", "stopping"}
TERMINAL_STATES = {"stopped", "ended", "failed", "unavailable"}
COUNTERS = ("received_events", "observations", "late_events", "overflow_events", "gap_events", "dropped_events",
            "poll_budget_overruns", "diagnostic_revision")
SETTING_KEYS = frozenset(item["key"] for item in load_registry()["items"])


def _recording_status(value):
  """Keep the child protocol bounded and exclude filesystem paths/raw settings."""
  if not isinstance(value, dict) or len(json.dumps(value, allow_nan=False).encode()) > 32 * 1024:
    raise ValueError("invalid_recording_status")
  result = {}
  groups = (
    (value, result, {"disabled", "starting", "recording", "partial", "failed", "stopped"}),
    (value.get("settings"), {}, {"unavailable", "observed", "partial", "failed", "closed"}),
    (value.get("preservation"), {}, {"disabled", "waiting", "pending", "copying", "verified", "partial", "failed", "stopped"}),
  )
  for source, output, states in groups:
    if not isinstance(source, dict) or source.get("state") not in states:
      raise ValueError("invalid_recording_state")
    output["state"] = source["state"]
    reason = source.get("reason")
    if reason is not None and (not isinstance(reason, str) or re.fullmatch(r"[a-z0-9_]{1,128}", reason) is None):
      raise ValueError("invalid_recording_reason")
    output["reason"] = reason
  settings, output = groups[1][:2]
  values = settings.get("values", {})
  changed = settings.get("changed_keys", [])
  if not isinstance(values, dict) or set(values) - SETTING_KEYS or any(
      value is not None and (type(value) not in (int, float) or not math.isfinite(value)) for value in values.values()):
    raise ValueError("invalid_recorded_settings")
  if not isinstance(changed, list) or len(changed) > len(SETTING_KEYS) or any(not isinstance(key, str) or key not in SETTING_KEYS for key in changed):
    raise ValueError("invalid_changed_settings")
  output.update(values=values, changed_keys=changed)
  if "identity_version" in settings or "vehicle_identity" in settings:
    from .vehicle_identity import validate_identity
    if type(settings.get("identity_version")) is not int or settings["identity_version"] != 1:
      raise ValueError("invalid_recording_vehicle_identity")
    output.update(identity_version=1, vehicle_identity=validate_identity(settings.get("vehicle_identity")))
  for key in ("history_complete", "consumer_applied", "atomic_snapshot"):
    if settings.get(key) is not False:
      raise ValueError("invalid_recording_claim")
    output[key] = False
  if settings.get("completeness") != "polled_partial":
    raise ValueError("invalid_recording_completeness")
  output["completeness"] = "polled_partial"
  for source, target, keys in (
      (settings, output, ("revision", "durable_revision", "poll_sequence")),
      (groups[2][0], groups[2][1], ("verified_segments", "verified_bytes", "failed_segments", "pending_jobs", "uncovered_windows", "latest_diagnostic_revision"))):
    for key in keys:
      number = source.get(key, 0)
      if key == "latest_diagnostic_revision" and number is None:
        target[key] = None
        continue
      if type(number) is not int or not 0 <= number <= 2**63 - 1:
        raise ValueError("invalid_recording_counter")
      target[key] = number
  committed = groups[2][0].get("journal_committed", False)
  if type(committed) is not bool:
    raise ValueError("invalid_recording_journal_state")
  groups[2][1]["journal_committed"] = committed
  # Per-key raw values and detailed journal records stay in the local evidence.
  observation = settings.get("observation") or {}
  if not isinstance(observation, dict):
    raise ValueError("invalid_recording_observation")
  output["observation"] = {}
  for key in ("begin_ns", "end_ns", "previous_end_ns"):
    stamp = observation.get(key)
    if stamp is not None and (type(stamp) is not int or not 0 <= stamp <= 2**63 - 1):
      raise ValueError("invalid_recording_timestamp")
    output["observation"][key] = stamp
  gap = observation.get("gap", False)
  if type(gap) is not bool:
    raise ValueError("invalid_recording_gap")
  output["observation"]["gap"] = gap
  interval = observation.get("change_interval")
  if interval is not None:
    if not isinstance(interval, dict) or set(interval) != {"after_ns", "before_ns"}:
      raise ValueError("invalid_setting_change_interval")
    after, before = interval["after_ns"], interval["before_ns"]
    if type(before) is not int or not 0 <= before <= 2**63 - 1 or (after is not None and (
        type(after) is not int or not 0 <= after <= before)):
      raise ValueError("invalid_setting_change_interval")
  output["observation"]["change_interval"] = interval
  journal = settings.get("journal", {})
  if isinstance(journal, dict) and journal.get("schema_version") == 2:
    from .journal_segments import validate_reference
    durable = journal.get("durable")
    if type(durable) is not bool:
      raise ValueError("invalid_recording_durability")
    output["journal"] = {**validate_reference({**journal, "durable": True}), "durable": durable}
    result["settings"], result["preservation"] = groups[1][1], groups[2][1]
    return deepcopy(result)
  if not isinstance(journal, dict) or journal.get("path") not in (None, "settings.jsonl"):
    raise ValueError("invalid_recording_journal")
  digest = journal.get("sha256")
  if digest is not None and (not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
    raise ValueError("invalid_recording_journal_hash")
  if type(journal.get("durable", False)) is not bool:
    raise ValueError("invalid_recording_durability")
  output["journal"] = {"path": journal.get("path"), "sha256": digest, "durable": journal.get("durable", False)}
  for key in ("sequence", "bytes"):
    number = journal.get(key, 0)
    if type(number) is not int or not 0 <= number <= 2**63 - 1:
      raise ValueError("invalid_recording_journal_counter")
    output["journal"][key] = number
  result["settings"], result["preservation"] = groups[1][1], groups[2][1]
  return deepcopy(result)


class SessionConflict(ValueError):
  pass


def _consumer_status(value, source_mode):
  from .consumer_collector import validate_snapshot
  if not isinstance(value, dict):
    raise ValueError("invalid_consumer_snapshot")
  result = validate_snapshot({key: item for key, item in value.items() if key != "persistence"})
  if result["source_mode"] != source_mode:
    raise ValueError("consumer_source_mode_mismatch")
  persistence = value.get("persistence", {})
  if not isinstance(persistence, dict) or persistence.get("state") not in {"disabled", "waiting", "recorded", "partial", "failed"}:
    raise ValueError("invalid_consumer_persistence")
  result["persistence"] = {"state": persistence["state"]}
  for key in ("recorded_events", "dropped_events"):
    number = persistence.get(key)
    if type(number) is not int or not 0 <= number <= 2**63 - 1:
      raise ValueError("invalid_consumer_persistence_count")
    result["persistence"][key] = number
  return result


def validate_start(mode, interval_seconds, request_id):
  if mode not in ("live", "synthetic"):
    raise ValueError("invalid_mode")
  if type(interval_seconds) is not int or interval_seconds not in (180, 300):
    raise ValueError("invalid_interval_seconds")
  if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id) is None:
    raise ValueError("invalid_request_id")


def _idle():
  return {"schema_version": 1, "session_id": None, "source_mode": None, "state": "idle", "interval_seconds": None,
          **dict.fromkeys(COUNTERS, 0), "diagnostic": None, "history": [], "reason": None,
          "settings_history": "unknown", "recommendations_enabled": False, "apply_enabled": False,
          "parking_confirmed": False, "parking": unknown_parking(), "last_park_observation": None,
          "age_ms": None, "finalized_through_ns": None, "diagnostic_invalidated": False}


def _worker_json(line):
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError("duplicate_worker_key")
      result[key] = value
    return result

  def reject(_):
    raise ValueError("nonfinite_worker_output")

  return json.loads(line.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject)


async def _settle(task):
  # Cancellation of an HTTP request must not cancel ownership cleanup.
  while True:
    try:
      return await asyncio.shield(task)
    except asyncio.CancelledError:
      if task.done():
        return task.result()


class SessionController:
  def __init__(self, *, python_executable=None, heartbeat_timeout=10.0, graceful_timeout=2.0, spawn=None, stopper=None, clock=None,
               live_environment=None):
    if heartbeat_timeout <= 0 or graceful_timeout <= 0:
      raise ValueError("invalid_session_timeout")
    self.python_executable = python_executable or sys.executable
    path_keys = {"CARROT_CONTROL_ADVISOR_" + key for key in ("SETTINGS_DIR", "LOG_ROOT", "EVIDENCE_ROOT", "AUTO_OUTPUT_ROOT",
                 "REQUEST_ROOT", "EXPORT_ROOT", "CONSUMER_SOCKET", "AUTO_PROFILE", "AUTO_OBJECTIVES")}
    self._live_flag_keys = {"CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS", "CARROT_CONTROL_ADVISOR_AUTO_ENABLED"}
    if live_environment is None:
      live_environment = {}
    if type(live_environment) is not dict or set(live_environment) - path_keys - self._live_flag_keys:
      raise ValueError("invalid_live_environment")
    for key, value in live_environment.items():
      if type(value) is not str or not value or len(value) > 4096 or "\0" in value or (
          value not in {"0", "1"} if key in self._live_flag_keys else not Path(value).is_absolute()):
        raise ValueError("invalid_live_environment")
    self._live_environment = dict(live_environment)
    self.heartbeat_timeout, self.graceful_timeout = heartbeat_timeout, graceful_timeout
    self._spawn = spawn or asyncio.create_subprocess_exec
    self._stopper = stopper or stop_process
    self._lock = asyncio.Lock()
    self._snapshot = _idle()
    self._process = None
    self._supervisor = None
    self._generation = 0
    self._requests = OrderedDict()
    self._current_request = None
    self._last_heartbeat = None
    self._monotonic = clock or time.monotonic
    self._consumer_seen = {}
    self._settings_seen = None
    self._parking_seen = None
    self._transition_seen = {}
    self._stderr_tail = b""
    self._closed = False
    self._analysis_requests = OrderedDict()
    self._analysis_binding = None

  def status(self):
    from .analysis_control import initial_control
    result = self._outward(self._snapshot, self._consumer_seen, self._last_heartbeat, self._parking_seen)
    result.setdefault("analysis_control", initial_control())
    if self._process is None or self._snapshot["state"] not in ACTIVE_STATES - {"starting", "stopping"}:
      result["analysis_control"]["can_analyze"] = False
    return result

  async def _command(self, session_id, command):
    async with self._lock:
      if self._closed or self._process is None or session_id != self._snapshot["session_id"] or self._snapshot["source_mode"] != "live":
        raise SessionConflict("analysis_session_not_active")
      payload = json.dumps({**command, "session_id": session_id}, separators=(",", ":"), allow_nan=False).encode() + b"\n"
      if len(payload) > 8192:
        raise ValueError("analysis_command_limit")
      try:
        self._process.stdin.write(payload)
        await self._process.stdin.drain()
      except (OSError, RuntimeError) as error:
        raise SessionConflict("analysis_worker_unavailable") from error

  async def analyze(self, *, session_id, request_id, goal_id=None):
    if type(request_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id) is None:
      raise ValueError("invalid_analysis_request")
    if goal_id is not None and not any(option["id"] == goal_id for option in self.analysis_goal_options()):
      raise ValueError("invalid_analysis_goal")
    effective_goal = goal_id if goal_id is not None else (self._snapshot.get("analysis_goal") or {}).get("id")
    signature = (session_id, effective_goal)
    previous = self._analysis_requests.get(request_id)
    if previous is not None:
      if previous != signature:
        raise SessionConflict("analysis_request_id_conflict")
      control = self.status()["analysis_control"]
      if control["request_id"] != request_id or control["state"] not in {"failed", "incomplete"}:
        return self.status()
    if not self.status()["analysis_control"]["can_analyze"]:
      raise SessionConflict("analysis_trip_not_ready")
    await self._command(session_id, {"kind": "analyze", "request_id": request_id, **({"goal_id": goal_id} if goal_id is not None else {})})
    self._analysis_requests[request_id] = signature
    while len(self._analysis_requests) > 128:
      self._analysis_requests.popitem(last=False)
    # Queue acknowledgement is distinct from a completed analysis result.
    self._snapshot["analysis_control"].update(state="queued", request_id=request_id, trigger="manual", reason_codes=[])
    return self.status()

  async def successful_apply(self, session_id, receipt, boot_id):
    vehicle = receipt.get("vehicle_identity", {}).get("physical_group_id")
    if not vehicle or not boot_id:
      return False
    await self._command(session_id, {"kind": "successful_apply", "receipt": {
      "stored_mono_ns": receipt["stored_mono_ns"], "revision": receipt["revision"], "request_id": receipt["request_id"]},
      "trip_binding": {"vehicle_id": vehicle, "boot_id": boot_id}})
    return True

  async def set_analysis_executor(self, *, session_id, executor):
    from .execution import validate_executor
    validate_executor(executor)
    await self._command(session_id, {"kind": "executor", "executor": executor})
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(2., self.heartbeat_timeout)
    while loop.time() < deadline:
      status = self.status()
      if status.get("analysis_execution", {}).get("executor") == executor:
        return status
      if status.get("session_id") != session_id or status.get("state") not in ACTIVE_STATES:
        raise SessionConflict("analysis_executor_session_changed")
      await asyncio.sleep(.02)
    raise SessionConflict("analysis_executor_ack_timeout")

  def request_binding(self):
    """Capture on the owning event loop; a repeated heartbeat never renews a poll."""
    now = self._monotonic()
    settings = self._snapshot.get("recording", {}).get("settings", {})
    observation = settings.get("observation", {})
    seen = self._settings_seen
    fresh = (not self._closed and self._snapshot["source_mode"] == "live"
             and self._snapshot["state"] in ACTIVE_STATES - {"starting", "stopping"}
             and self._snapshot.get("clock_mode") == "native_monotonic"
             and self._last_heartbeat is not None and 0 <= now - self._last_heartbeat <= self.heartbeat_timeout
             and seen is not None and 0 <= now - seen["received_at"] <= CONSUMER_PARENT_MAX_AGE_SECONDS
             and settings.get("state") in {"observed", "partial"} and observation.get("gap") is False
             and type(observation.get("begin_ns")) is int and type(observation.get("end_ns")) is int
             and 0 <= observation["begin_ns"] <= observation["end_ns"] and settings.get("poll_sequence", 0) > 0)
    result = {"session_id": self._snapshot["session_id"], "generation": self._generation,
            "source_mode": self._snapshot["source_mode"], "state": self._snapshot["state"],
            "settings_revision": settings.get("revision") if fresh else None,
            "settings_raw": deepcopy(settings.get("values", {})) if fresh else {},
            "observed_mono_ns": int(seen["received_at"] * 1e9) if fresh else None,
            "settings_observation_fresh": bool(fresh)}
    if fresh and self._analysis_binding is not None:
      result["analysis_binding"] = deepcopy(self._analysis_binding)
    return result

  @staticmethod
  def _consumer_marker(event):
    return tuple(event.get(key) for key in ("boot_id", "pid", "process_instance_id", "consumer_instance_id", "sequence"))

  def _outward(self, snapshot, seen, last_heartbeat, parking_seen=None):
    """Age every outward copy using this parent's clock, including request cache.

    A repeated heartbeat carrying the same sampled event does not renew it.
    Historical event bodies remain intact; only current-use freshness is gated.
    """
    result, now = deepcopy(snapshot), self._monotonic()
    heartbeat_age = max(0.0, now - last_heartbeat) if last_heartbeat is not None else None
    result["last_heartbeat_age_seconds"] = round(heartbeat_age, 3) if heartbeat_age is not None else None
    parking = result["parking"]
    parking_active = (not self._closed and result["state"] in ACTIVE_STATES - {"starting", "stopping"}
                      and result["session_id"] == self._snapshot["session_id"]
                      and self._snapshot["state"] in ACTIVE_STATES - {"starting", "stopping"})
    parking_fresh = (parking_seen is not None and parking_seen["marker"] == parking["observed_mono_ns"]
                     and 0 <= now - parking_seen["received_at"] <= CONSUMER_PARENT_MAX_AGE_SECONDS)
    if (not parking_active or not parking_fresh or last_heartbeat is None or now < last_heartbeat
        or heartbeat_age > self.heartbeat_timeout):
      result["parking"] = unknown_parking(parking["source_kind"], "parking_parent_observation_stale" if parking_active else "parking_session_ended")
    result["parking_confirmed"] = (result["parking"]["source_kind"] == "native_event"
                                     and result["parking"]["state"] == "park_observed")
    if "transition" in result:
      for axis, item in result["transition"].items():
        seen_transition = self._transition_seen.get(axis)
        stamp = item["observed_mono_ns"]
        if not parking_active:
          item.update(ready=False, reason="transition_parent_session_inactive")
        elif last_heartbeat is None or not 0 <= now - last_heartbeat <= 2.0:
          item.update(ready=False, reason="transition_parent_heartbeat_stale")
        elif (seen_transition is None or seen_transition["marker"] != stamp
              or not 0 <= now - seen_transition["received_at"] <= 2.0
              or result["source_mode"] == "live" and (stamp is None or not 0 <= int(now * 1e9) - stamp <= 2_000_000_000)):
          item.update(ready=False, reason="transition_parent_observation_stale")
    consumers = result.get("consumer_observations")
    if consumers is None:
      return result
    active = result["state"] in ACTIVE_STATES - {"stopping"}
    source_active = consumers["state"] not in ("disabled", "unavailable", "stopped")
    heartbeat_stale = last_heartbeat is None or now < last_heartbeat or heartbeat_age > self.heartbeat_timeout
    invalidated = False
    for entry in consumers["consumers"]:
      event = entry["latest_event"]
      arrival = seen.get(entry["consumer_id"])
      parent_fresh = (event is not None and arrival is not None
                      and arrival["marker"] == self._consumer_marker(event)
                      and 0 <= now - arrival["received_at"] <= CONSUMER_PARENT_MAX_AGE_SECONDS)
      if not active or not source_active or heartbeat_stale or not parent_fresh:
        invalidated |= event is not None
        entry["fresh"] = False
        entry["read_freshness"] = dict.fromkeys(entry["read_freshness"], False)
        if event is not None:
          entry["state"] = "stale"
    if consumers["state"] not in ("disabled", "unavailable", "stopped"):
      if not active:
        consumers["state"] = "stopped" if result["state"] in ("stopping", "stopped", "ended") else "partial"
        consumers["reason"] = "consumer_session_not_active"
      elif heartbeat_stale or invalidated:
        consumers["state"] = "partial"
        consumers["reason"] = "consumer_parent_heartbeat_stale" if heartbeat_stale else "consumer_parent_observation_stale"
    return result

  def _remember(self):
    record = self._requests.get(self._current_request)
    if record is not None and record["generation"] == self._generation:
      record["snapshot"] = deepcopy(self._snapshot)
      record["consumer_seen"] = deepcopy(self._consumer_seen)
      record["last_heartbeat"] = self._last_heartbeat
      record["parking_seen"] = deepcopy(self._parking_seen)

  def analysis_goal_options(self):
    from .goals import goal_options
    return goal_options(self._live_environment)

  async def start(self, *, mode, interval_seconds, request_id, goal_id=None):
    validate_start(mode, interval_seconds, request_id)
    if goal_id is not None and mode != "live":
      raise ValueError("analysis_goal_requires_live_mode")
    operation = asyncio.create_task(self._start(mode, interval_seconds, request_id, goal_id))
    try:
      snapshot, _ = await asyncio.shield(operation)
      return snapshot
    except asyncio.CancelledError:
      try:
        snapshot, created = await _settle(operation)
        if created:
          await _settle(asyncio.create_task(self.stop(snapshot["session_id"])))
      except (SessionConflict, RuntimeError, OSError):
        pass
      raise

  def _child_environment(self, mode):
    environment = {key: value for key, value in os.environ.items() if key != "CARROT_CONTROL_ADVISOR_TOKEN"}
    if mode == "live":
      environment.update({key: value for key, value in self._live_environment.items()
                          if key not in self._live_flag_keys or environment.get(key) != "0"})
    return environment

  async def _start(self, mode, interval_seconds, request_id, goal_id=None):
    async with self._lock:
      if self._closed:
        raise SessionConflict("controller_closed")
      previous = self._requests.get(request_id)
      if previous is not None:
        if (previous["mode"], previous["interval_seconds"], previous.get("goal_id")) != (mode, interval_seconds, goal_id):
          raise SessionConflict("request_id_conflict")
        return self._outward(previous["snapshot"], previous.get("consumer_seen", {}), previous.get("last_heartbeat"), previous.get("parking_seen")), False
      if self._process is not None:
        raise SessionConflict("session_already_active")
      environment = self._child_environment(mode)
      from .goals import goal_options
      preset_allowed = mode == "live" and bool(goal_options(environment))
      environment["CARROT_CONTROL_ADVISOR_BUILTIN_GOALS"] = "1" if preset_allowed else "0"
      environment["CARROT_CONTROL_ADVISOR_SELECTED_GOAL"] = goal_id or ("tracking" if preset_allowed else "")
      goal = None
      if goal_id is not None:
        from .goals import goal_configuration, PROFILE_ENV, OBJECTIVES_ENV
        goal = goal_configuration(goal_id, environment)
        environment.update({PROFILE_ENV: goal["profile_path"], OBJECTIVES_ENV: goal["objectives_path"]})
      self._generation += 1
      generation = self._generation
      session_id = uuid.uuid4().hex
      self._snapshot = {**_idle(), "session_id": session_id, "source_mode": mode, "state": "starting",
                        "interval_seconds": interval_seconds,
                        "parking": unknown_parking("native_event" if mode == "live" else "synthetic_event")}
      if goal is not None:
        self._snapshot["analysis_goal"] = goal["selection"]
      self._last_heartbeat = None
      self._transition_seen = {}
      self._consumer_seen = {}
      self._settings_seen = None
      self._parking_seen = None
      self._analysis_binding = None
      self._stderr_tail = b""
      self._current_request = request_id
      self._requests[request_id] = {"mode": mode, "interval_seconds": interval_seconds, "generation": generation,
                                    "goal_id": goal_id,
                                    "snapshot": deepcopy(self._snapshot), "consumer_seen": {}, "last_heartbeat": None}
      while len(self._requests) > 16:
        self._requests.popitem(last=False)
      try:
        # The child needs no session-control secret, and never receives shell input.
        self._process = await self._spawn(
          self.python_executable, "-m", "openpilot.selfdrive.carrot.control_advisor.worker",
          "--mode", mode, "--session-id", session_id, "--interval-seconds", str(interval_seconds),
          cwd=str(Path(__file__).resolve().parents[4]), stdin=asyncio.subprocess.PIPE,
          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=MAX_LINE_BYTES + 1,
          env=environment, **process_group_kwargs(),
        )
      except (OSError, RuntimeError):
        self._snapshot.update(state="failed", reason="worker_start_failed")
        self._remember()
        return self.status(), False
      self._supervisor = asyncio.create_task(self._run(self._process, generation))
      self._remember()
      return self.status(), True

  def _accept(self, payload, generation):
    if generation != self._generation:
      return
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
      raise ValueError("invalid_worker_schema")
    recording = _recording_status(payload["recording"]) if "recording" in payload else None
    automatic = None
    if "automatic_analysis" in payload:
      from .periodic import validate_periodic_status
      automatic = validate_periodic_status(payload["automatic_analysis"])
      if automatic["interval_seconds"] != self._snapshot["interval_seconds"]:
        raise ValueError("automatic_analysis_interval_mismatch")
      if self._snapshot["source_mode"] == "synthetic" and automatic["state"] not in {"disabled", "failed", "stopped"}:
        raise ValueError("automatic_analysis_source_mode_mismatch")
    analysis = None
    if "analysis_control" in payload:
      from .analysis_control import validate_control
      analysis = validate_control(payload["analysis_control"])
    if "analysis_binding" in payload:
      if payload["analysis_binding"] is None:
        self._analysis_binding = None
      else:
        from .analysis_control import validate_binding
        self._analysis_binding = validate_binding(payload["analysis_binding"])
    selection = payload.get("analysis_goal")
    execution = None
    if payload.get("analysis_execution") is not None:
      from .execution import validate_execution
      execution = validate_execution(payload["analysis_execution"])
    if selection is not None:
      if selection not in self.analysis_goal_options() or self._snapshot["source_mode"] != "live":
        raise ValueError("invalid_worker_analysis_goal")
    consumers = _consumer_status(payload["consumer_observations"], self._snapshot["source_mode"]) if "consumer_observations" in payload else None
    transition = validate_transition(payload["transition"]) if "transition" in payload else None
    if payload.get("session_id") != self._snapshot["session_id"] or payload.get("source_mode") != self._snapshot["source_mode"]:
      raise ValueError("worker_session_mismatch")
    if payload.get("state") not in ACTIVE_STATES | TERMINAL_STATES:
      raise ValueError("invalid_worker_state")
    if any(payload.get(key, False) is not False for key in ("recommendations_enabled", "apply_enabled")):
      raise ValueError("invalid_worker_capability")
    source_kind = "native_event" if self._snapshot["source_mode"] == "live" else "synthetic_event"
    parking = validate_parking(payload.get("parking", unknown_parking(source_kind)), source_kind=source_kind)
    historical = payload.get("last_park_observation")
    if historical is not None:
      historical = validate_parking(historical, source_kind=source_kind, historical=True)
    confirmed = source_kind == "native_event" and parking["state"] == "park_observed"
    if type(payload.get("parking_confirmed", False)) is not bool or payload.get("parking_confirmed", False) != confirmed:
      raise ValueError("invalid_worker_parking_claim")
    if payload["state"] in TERMINAL_STATES and parking["state"] != "unknown":
      raise ValueError("invalid_terminal_parking")
    history = payload.get("history", [])
    if not isinstance(history, list) or len(history) > 6 or any(not isinstance(item, dict) for item in history):
      raise ValueError("invalid_worker_history")
    if payload.get("diagnostic") is not None and not isinstance(payload["diagnostic"], dict):
      raise ValueError("invalid_worker_diagnostic")
    for key in COUNTERS:
      if key in payload and (type(payload[key]) is not int or payload[key] < 0):
        raise ValueError("invalid_worker_counter")
    age = payload.get("age_ms")
    if age is not None and (type(age) not in (int, float) or not math.isfinite(age) or age < 0):
      raise ValueError("invalid_worker_age")
    watermark = payload.get("finalized_through_ns")
    if watermark is not None and (type(watermark) is not int or watermark < -1):
      raise ValueError("invalid_worker_watermark")
    if "clock_mode" in payload and payload["clock_mode"] not in ("native_monotonic", "accelerated_synthetic"):
      raise ValueError("invalid_worker_clock")
    if "clock_mode" in payload and (payload["clock_mode"] == "accelerated_synthetic") != (self._snapshot["source_mode"] == "synthetic"):
      raise ValueError("worker_clock_mode_mismatch")
    if "diagnostic_invalidated" in payload and type(payload["diagnostic_invalidated"]) is not bool:
      raise ValueError("invalid_worker_invalidation")
    if "counters" in payload:
      counters = payload["counters"]
      if not isinstance(counters, dict) or set(counters) - set(COUNTERS):
        raise ValueError("invalid_worker_counters")
      if any(type(value) is not int or value < 0 or (key in payload and payload[key] != value) for key, value in counters.items()):
        raise ValueError("invalid_worker_counters")
    if "metadata" in payload:
      metadata = payload["metadata"]
      expected = {"settings_history": "unknown", "gap_detection": "timestamp_heuristic_no_sequence",
                  "device_qualified": False, "overlapping_windows": True}
      if not isinstance(metadata, dict) or set(metadata) - set(expected) - {"payload_details_omitted", "worker_runtime"}:
        raise ValueError("invalid_worker_metadata")
      if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("invalid_worker_metadata")
      if "payload_details_omitted" in metadata and metadata["payload_details_omitted"] is not True:
        raise ValueError("invalid_worker_metadata")
      if "worker_runtime" in metadata:
        from .worker_runtime import validate_worker_runtime
        runtime = validate_worker_runtime(metadata["worker_runtime"])
        if self._snapshot["source_mode"] == "synthetic" and (runtime["state"] != "not_applicable" or runtime["reason"] != "synthetic_mode"):
          raise ValueError("invalid_worker_runtime_mode")
        metadata = {**metadata, "worker_runtime": runtime}
        payload = {**payload, "metadata": metadata}
    reason = payload.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 512):
      raise ValueError("invalid_worker_reason")
    allowed = {"state", "diagnostic", "history", "reason", "clock_mode", "age_ms", "finalized_through_ns",
               "diagnostic_invalidated", "counters", "metadata", *COUNTERS}
    stopping = self._snapshot["state"] == "stopping"
    self._snapshot.update({key: value for key, value in payload.items() if key in allowed})
    self._snapshot.update(parking=parking, last_park_observation=historical, parking_confirmed=confirmed)
    if transition is not None:
      self._snapshot["transition"] = transition
      for axis, item in transition.items():
        previous = self._transition_seen.get(axis)
        stamp = item["observed_mono_ns"]
        if stamp is not None and (previous is None or stamp > previous["marker"]):
          self._transition_seen[axis] = {"marker": stamp, "received_at": self._monotonic()}
    marker = parking["observed_mono_ns"]
    if marker is not None and (self._parking_seen is None or self._parking_seen["marker"] != marker):
      self._parking_seen = {"marker": marker, "received_at": self._monotonic()}
    if recording is not None:
      self._snapshot["recording"] = recording
      settings = recording["settings"]
      marker = (settings["revision"], settings["poll_sequence"], settings["observation"].get("end_ns"))
      if self._settings_seen is None or self._settings_seen["marker"] != marker:
        self._settings_seen = {"marker": marker, "received_at": self._monotonic()}
    if automatic is not None:
      self._snapshot["automatic_analysis"] = automatic
    if analysis is not None:
      self._snapshot["analysis_control"] = analysis
    if selection is not None:
      self._snapshot["analysis_goal"] = deepcopy(selection)
    if execution is not None:
      self._snapshot["analysis_execution"] = execution
    elif "analysis_execution" in payload:
      self._snapshot.pop("analysis_execution", None)
    if consumers is not None:
      self._snapshot["consumer_observations"] = consumers
      received_at = self._monotonic()
      for entry in consumers["consumers"]:
        event = entry["latest_event"]
        if event is None:
          continue
        marker = self._consumer_marker(event)
        previous = self._consumer_seen.get(entry["consumer_id"])
        if previous is None or marker != previous["marker"]:
          self._consumer_seen[entry["consumer_id"]] = {"marker": marker, "received_at": received_at}
    if stopping:
      self._snapshot["state"] = "stopping"
    self._last_heartbeat = self._monotonic()
    self._remember()

  async def _drain_stderr(self, proc, generation):
    while True:
      chunk = await proc.stderr.read(1024)
      if not chunk:
        return
      if generation == self._generation:
        self._stderr_tail = (self._stderr_tail + chunk)[-MAX_STDERR_BYTES:]

  async def _run(self, proc, generation):
    stderr_task = asyncio.create_task(self._drain_stderr(proc, generation))
    failure = None
    try:
      while True:
        line = await asyncio.wait_for(proc.stdout.readline(), self.heartbeat_timeout)
        if not line:
          break
        if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
          raise ValueError("worker_output_limit")
        payload = _worker_json(line)
        self._accept(payload, generation)
    except TimeoutError:
      failure = "worker_heartbeat_timeout"
    except (ValueError, UnicodeError, OSError, RecursionError):
      failure = "invalid_worker_output"
    finally:
      try:
        if failure is not None:
          await self._stopper(proc)
        else:
          try:
            await asyncio.wait_for(proc.wait(), self.graceful_timeout)
          except TimeoutError:
            failure = "worker_exit_timeout"
            await self._stopper(proc)
      finally:
        try:
          await asyncio.wait_for(stderr_task, self.graceful_timeout)
        except (TimeoutError, OSError):
          stderr_task.cancel()
          await asyncio.gather(stderr_task, return_exceptions=True)
        if generation == self._generation:
          state = self._snapshot["state"]
          if state == "stopping":
            self._snapshot.update(state="stopped", reason="user_stopped")
          elif failure is not None:
            self._snapshot.update(state="failed", reason=failure)
          elif proc.returncode not in (None, 0):
            self._snapshot.update(state="failed", reason="worker_exited")
          elif state not in TERMINAL_STATES:
            self._snapshot.update(state="stopped", reason="worker_exited")
          self._process = None
          self._remember()

  async def stop(self, session_id):
    operation = asyncio.create_task(self._stop(session_id))
    try:
      return await asyncio.shield(operation)
    except asyncio.CancelledError:
      await _settle(operation)
      raise

  async def _stop(self, session_id):
    async with self._lock:
      return await self._stop_locked(session_id)

  async def _stop_locked(self, session_id):
    if not isinstance(session_id, str) or session_id != self._snapshot["session_id"]:
      raise SessionConflict("stale_session_id")
    if self._process is None:
      return self.status()
    proc, supervisor = self._process, self._supervisor
    self._snapshot.update(state="stopping", reason=None)
    self._remember()
    if proc.stdin is not None:
      proc.stdin.close()
    try:
      await asyncio.wait_for(asyncio.shield(supervisor), self.graceful_timeout)
    except TimeoutError:
      if proc.returncode is None:
        await self._stopper(proc)
      await asyncio.shield(supervisor)
    return self.status()

  async def close(self):
    operation = asyncio.create_task(self._close())
    try:
      await asyncio.shield(operation)
    except asyncio.CancelledError:
      await _settle(operation)
      raise

  async def _close(self):
    async with self._lock:
      self._closed = True
      if self._process is not None:
        await self._stop_locked(self._snapshot["session_id"])
