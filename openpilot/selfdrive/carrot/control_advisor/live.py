"""Bounded read-only live transport and host-provisional diagnostic session.

No scheduler/affinity, Params, control output or recommendation writer lives
here. Missing native IPC is an unavailable source, never a synthetic fallback.
"""

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import heapq
import time

from .analysis import AnalysisProfile, Observation, analyze
from .normalizer import EventNormalizer, SERVICES
from .parking import ParkingObserver


MAX_EVENT_BYTES = 1024 * 1024
LIVE_SERVICES = (*SERVICES, "deviceState")
TERMINAL_STATES = {"ended", "stopped", "unavailable", "failed"}
TRANSITION_RECOVERY_NS = TRANSITION_MAX_AGE_NS = 2_000_000_000
TRANSITION_REASONS = {"transition_waiting", "transition_ready", "transition_recovering", "transition_driver_override",
                      "transition_invalid_telemetry", "transition_axis_inactive", "transition_observation_gap",
                      "transition_invalidated", "transition_session_ended", "transition_input_stale",
                      "transition_parent_observation_stale", "transition_parent_session_inactive", "transition_parent_heartbeat_stale"}


def unknown_transition(reason="transition_waiting"):
  return {axis: {"ready": False, "reason": reason, "observed_mono_ns": None, "valid_since_mono_ns": None,
                 "last_blocked_mono_ns": None, "recovery_ns": TRANSITION_RECOVERY_NS, "max_age_ns": TRANSITION_MAX_AGE_NS}
          for axis in ("lateral", "longitudinal")}


def validate_transition(value):
  if type(value) is not dict or set(value) != {"lateral", "longitudinal"}:
    raise ValueError("invalid_worker_transition")
  fields = set(unknown_transition()["lateral"])
  for item in value.values():
    if (type(item) is not dict or set(item) != fields or type(item["ready"]) is not bool
        or type(item["reason"]) is not str or item["reason"] not in TRANSITION_REASONS
        or type(item["recovery_ns"]) is not int or item["recovery_ns"] != TRANSITION_RECOVERY_NS
        or type(item["max_age_ns"]) is not int or item["max_age_ns"] != TRANSITION_MAX_AGE_NS):
      raise ValueError("invalid_worker_transition")
    for name in ("observed_mono_ns", "valid_since_mono_ns", "last_blocked_mono_ns"):
      if item[name] is not None and (type(item[name]) is not int or not 0 <= item[name] <= 2**63 - 1):
        raise ValueError("invalid_worker_transition")
    stamp, since, blocked = (item[name] for name in ("observed_mono_ns", "valid_since_mono_ns", "last_blocked_mono_ns"))
    if (since is not None and (stamp is None or since > stamp or blocked is not None and blocked > since)
        or blocked is not None and stamp is not None and blocked > stamp
        or item["ready"] and (stamp is None or since is None or stamp - since < TRANSITION_RECOVERY_NS or item["reason"] != "transition_ready")):
      raise ValueError("invalid_worker_transition")
  return deepcopy(value)


@dataclass(frozen=True)
class LiveEvent:
  service: str
  mono_ns: int
  valid: bool
  data: dict
  recv_ns: int
  source_bytes: int = 1


class NativeTransport:
  def __init__(self, *, messaging_module=None, max_event_bytes=MAX_EVENT_BYTES, clock_ns=time.monotonic_ns):
    if type(max_event_bytes) is not int or max_event_bytes <= 0:
      raise ValueError("max_event_bytes must be positive")
    if messaging_module is None:
      try:
        from openpilot.cereal import messaging as messaging_module
      except (ImportError, OSError, RuntimeError) as exc:
        raise RuntimeError("native_messaging_unavailable") from exc
    self.messaging, self.max_event_bytes, self.clock_ns = messaging_module, max_event_bytes, clock_ns
    self.sockets, self.cursor = {}, 0
    self.received_events = self.rejected_events = self.budget_overruns = 0
    self.last_poll_ns = 0
    try:
      for service in LIVE_SERVICES:
        self.sockets[service] = self.messaging.sub_sock(service, conflate=False)
    except Exception:
      self.close()
      raise

  def poll(self, *, max_events=256, budget_ns=5_000_000) -> list[LiveEvent]:
    if type(max_events) is not int or max_events <= 0 or type(budget_ns) is not int or budget_ns <= 0:
      raise ValueError("poll count/time budgets must be positive integers")
    start, reads, empty = self.clock_ns(), 0, 0
    result = []
    while reads < max_events and self.clock_ns() - start < budget_ns:
      service = LIVE_SERVICES[self.cursor]
      self.cursor = (self.cursor + 1) % len(LIVE_SERVICES)
      raw = self.sockets[service].receive(non_blocking=True)
      if raw is None:
        empty += 1
        if empty >= len(LIVE_SERVICES):
          break
        continue
      reads += 1
      empty = 0
      self.received_events += 1
      recv_ns = self.clock_ns()
      if not isinstance(raw, bytes) or len(raw) > self.max_event_bytes:
        self.rejected_events += 1
        continue
      try:
        # Check bytes before a bounded decode; do not use unlimited log_from_bytes.
        with self.messaging.log.Event.from_bytes(raw, traversal_limit_in_words=max(1, self.max_event_bytes // 8),
                                                  nesting_limit=64) as event:
          if event.which() != service:
            raise ValueError("native service mismatch")
          result.append(LiveEvent(service, int(event.logMonoTime), bool(event.valid),
                                  getattr(event, service).to_dict(), recv_ns, len(raw)))
      except Exception:
        self.rejected_events += 1
    self.last_poll_ns = max(0, self.clock_ns() - start)
    # A single native decode is not preemptible. Exceeding this cooperative host
    # budget is recorded; it is not a proven device scheduling upper bound.
    self.budget_overruns += self.last_poll_ns > budget_ns
    return result

  def close(self):
    for socket in self.sockets.values():
      close = getattr(socket, "close", None)
      if close is not None:
        try:
          close()
        except Exception:
          pass
    self.sockets.clear()


def _compact_diagnostic(diagnostic):
  """Bound display details, retaining exact counts and metrics from the full mask."""
  result = {**diagnostic, "axes": {}}
  for axis, axis_result in diagnostic["axes"].items():
    epochs = {}
    for epoch, summary in axis_result["epochs"].items():
      reasons = {}
      for interval in summary["exclusions"]:
        reasons[interval["reason"]] = reasons.get(interval["reason"], 0) + interval["sample_count"]
      epochs[epoch] = {**summary, "exclusions": summary["exclusions"][:32],
                       "exclusion_intervals_total": len(summary["exclusions"]),
                       "exclusion_intervals_omitted": max(0, len(summary["exclusions"]) - 32),
                       "exclusion_reason_counts": reasons}
    result["axes"][axis] = {"epochs": epochs}
  return result


class LiveSession:
  def __init__(self, session_id: str, *, source_mode: str, interval_seconds: int = 300,
               max_queue_events: int = 4096, max_buffer_bytes: int = 8 * 1024 * 1024,
               lateness_ns: int = 100_000_000, max_observations: int = 100_000,
               max_session_seconds: int | None = None, clock_ns=time.monotonic_ns):
    if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
      raise ValueError("session_id must contain 1..128 characters")
    if source_mode not in ("live", "synthetic") or type(interval_seconds) is not int or interval_seconds not in (180, 300):
      raise ValueError("source mode/interval must be live|synthetic and 180|300")
    for name, value in (("max_queue_events", max_queue_events), ("max_buffer_bytes", max_buffer_bytes),
                        ("max_observations", max_observations)):
      if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if max_session_seconds is not None and (type(max_session_seconds) is not int or max_session_seconds <= 0):
      raise ValueError("max_session_seconds must be None or a positive integer")
    if type(lateness_ns) is not int or lateness_ns < 0:
      raise ValueError("lateness_ns must be a nonnegative integer")
    self.session_id, self.source_mode, self.interval_ns = session_id, source_mode, interval_seconds * 1_000_000_000
    self.max_queue_events, self.max_buffer_bytes = max_queue_events, max_buffer_bytes
    self.lateness_ns, self.max_observations = lateness_ns, max_observations
    self.max_session_ns = None if max_session_seconds is None else max_session_seconds * 1_000_000_000
    self.clock_ns = clock_ns
    self.profile = AnalysisProfile(f"{source_mode}-host-unvalidated-v1", 100_000_000, 2_000_000_000,
                                   250_000_000, 100_000_000, max_observations)
    self.state, self.reason = "waiting_for_data", "waiting_for_source"
    self.worker_runtime = None
    self.received_events = self.observations = self.late_events = self.overflow_events = self.gap_events = self.dropped_events = 0
    self.poll_budget_overruns = self.diagnostic_revision = 0
    self.diagnostic, self.history = None, deque(maxlen=6)
    self.preservation_bookmark = None
    self._bookmark_start_ns = None
    self._bookmark_end_ns = None
    self.nonpark_observed_ns = None
    self._heap, self._window = [], deque()
    self._sequence = self._queued_bytes = 0
    self._max_seen_ns = self._released_through_ns = -1
    self._last_service_ns = {}
    self._last_received_wall_ns = None
    self._started_wall_ns = self.clock_ns()
    self._next_evaluation_ns = self._last_diagnostic_end_ns = None
    self._needs_fresh_window = self._stale_invalidated = False
    self._settings_epoch = "unverified-session"
    self.observed_settings_revision = None
    self.settings_observation_state = "unavailable"
    self._settings_notice_token = None
    self._settings_boundary_ns = None
    self._route_observation = None
    self.recording_status = None
    self.consumer_observations = None
    self.transition = unknown_transition()
    self._normalizer = self._new_normalizer()
    self.parking = ParkingObserver(source_kind="native_event" if source_mode == "live" else "synthetic_event", clock_ns=clock_ns,
                                   source_clock_ns=clock_ns if source_mode == "live" else lambda: max(0, self._max_seen_ns))

  def _new_normalizer(self):
    return EventNormalizer(epoch=self._settings_epoch, max_age_ns=200_000_000,
                           source=f"{self.source_mode}_car_state_anchor")

  def observe_settings(self, notice):
    """Observed storage revisions are uncertain boundaries, never consumer ACKs."""
    if self.state in TERMINAL_STATES:
      return
    revision = notice.get("revision")
    if type(revision) is not int or revision < 0:
      raise ValueError("observed settings revision must be a nonnegative integer")
    state = notice.get("state", "unavailable")
    route_notice = notice.get("route_observation") or {}
    token = (revision, state, notice.get("durable_revision"), notice.get("journal", {}).get("durable"),
             route_notice.get("route"), route_notice.get("state"))
    observation = notice.get("observation") or {}
    gap = observation.get("gap") is True or notice.get("notification_gap") is True
    if token == self._settings_notice_token and not gap:
      return
    initial = self._settings_notice_token is None
    self._settings_notice_token = token
    self.observed_settings_revision = revision if revision > 0 else None
    self.settings_observation_state = state
    self._route_observation = deepcopy(notice.get("route_observation"))
    self._settings_boundary_ns = observation.get("end_ns")
    self._settings_epoch = f"observed-{revision}" if revision > 0 else "unverified-session"
    reason = "stored_settings_observation_gap" if gap else "stored_settings_initial_observation" if initial else "stored_settings_observation_changed"
    self.invalidate(reason)

  def invalidate(self, reason: str, *, overflow: bool = False):
    if self.state in TERMINAL_STATES:
      return
    self.parking.invalidate()
    self.nonpark_observed_ns = None
    self.transition = unknown_transition("transition_invalidated")
    self.dropped_events += len(self._heap)
    self._heap.clear()
    self._queued_bytes = 0
    self._window.clear()
    self._normalizer = self._new_normalizer()
    self._next_evaluation_ns = self._last_diagnostic_end_ns = None
    self._needs_fresh_window = True
    self.state, self.reason = "degraded", reason
    if overflow:
      self.overflow_events += 1
    if self.diagnostic is not None and not self.diagnostic.get("invalidated"):
      self.diagnostic_revision += 1
      self.diagnostic = {**self.diagnostic, "revision": self.diagnostic_revision,
                         "invalidated": True, "invalidation_reason": reason}
      self.history.append({"revision": self.diagnostic_revision, "state": "invalidated", "reason": reason})

  def ingest_batch(self, events):
    if self.state in TERMINAL_STATES:
      return
    for event in events:
      self.received_events += 1
      if not isinstance(event, LiveEvent) or event.service not in LIVE_SERVICES or type(event.mono_ns) is not int or event.mono_ns < 0:
        raise ValueError("malformed live event")
      if type(event.recv_ns) is not int or event.recv_ns < 0 or type(event.valid) is not bool or not isinstance(event.data, dict):
        raise ValueError("malformed live event metadata")
      if type(event.source_bytes) is not int or event.source_bytes <= 0:
        raise ValueError("source_bytes must be a positive integer")
      if event.service == "deviceState":
        # Auxiliary evidence never advances a control watermark or freshness.
        valid = (event.valid and event.source_bytes <= MAX_EVENT_BYTES
                 and event.recv_ns - event.mono_ns <= 2_000_000_000
                 and event.mono_ns - event.recv_ns <= self.lateness_ns)
        self.parking.feed(event.service, event.mono_ns, valid, event.data,
                          event.recv_ns if self.source_mode == "live" else None)
        continue
      self._last_received_wall_ns = self.clock_ns()
      self._stale_invalidated = False
      if self._settings_boundary_ns is not None and event.mono_ns < self._settings_boundary_ns:
        self.dropped_events += 1
        continue
      if event.source_bytes > MAX_EVENT_BYTES:
        self.dropped_events += 1
        self.invalidate("event_byte_limit", overflow=True)
        continue
      if event.recv_ns - event.mono_ns > 2_000_000_000 or event.mono_ns - event.recv_ns > self.lateness_ns:
        self.late_events += 1
        self.dropped_events += 1
        self.invalidate("source_clock_or_delivery_stale")
        continue
      if event.mono_ns <= self._released_through_ns:
        self.late_events += 1
        self.dropped_events += 1
        self.invalidate("late_event_after_watermark")
        continue
      previous = self._last_service_ns.get(event.service)
      if previous is not None and event.mono_ns - previous > 1_000_000_000:
        self.gap_events += 1
        self.invalidate("service_timestamp_gap")
      self._last_service_ns[event.service] = max(event.mono_ns, previous if previous is not None else event.mono_ns)
      if len(self._heap) >= self.max_queue_events or self._queued_bytes + event.source_bytes > self.max_buffer_bytes:
        self.dropped_events += 1
        self.invalidate("event_queue_limit", overflow=True)
        continue
      heapq.heappush(self._heap, (event.mono_ns, SERVICES.index(event.service), self._sequence, event))
      self._sequence += 1
      self._queued_bytes += event.source_bytes
      self._max_seen_ns = max(self._max_seen_ns, event.mono_ns)
    self._drain(self._max_seen_ns - self.lateness_ns)

  def _drain(self, through_ns):
    while self._heap and self._heap[0][0] <= through_ns:
      _, _, _, event = heapq.heappop(self._heap)
      self._queued_bytes -= event.source_bytes
      if event.service in {"carState", "carControl"}:
        self.parking.feed(event.service, event.mono_ns, event.valid, event.data,
                          event.recv_ns if self.source_mode == "live" else None)
      if event.service == "carState":
        self.nonpark_observed_ns = (event.mono_ns if event.valid and event.data.get("canValid") is True
          and event.data.get("canTimeout") is not True and event.data.get("gearShifter") in {"drive", "reverse", "neutral", "low", "sport", "eco", "brake"} else None)
      try:
        rows = self._normalizer.feed(event.service, event.mono_ns, event.valid, event.data, event.recv_ns)
      except ValueError:
        self.invalidate("normalization_rejected")
        self.dropped_events += 1
        break
      if len(self._window) + len(rows) > self.max_observations:
        self.invalidate("observation_limit", overflow=True)
        break
      for row in rows:
        self._observe_transition(row)
        row["context"].update(observed_settings_revision=self.observed_settings_revision,
                              settings_observation_state=self.settings_observation_state)
        self._window.append(Observation(**row))
        self.observations += 1
      if rows:
        if not self._needs_fresh_window:
          self.state, self.reason = "running", "collecting_unvalidated_diagnostics"
        if self._next_evaluation_ns is None:
          self._next_evaluation_ns = rows[0]["mono_ns"] + self.interval_ns
        guard_ns = self.profile.pre_margin_ns + self.profile.recovery_ns + self.profile.dependency_ns
        earliest = rows[-1]["mono_ns"] - guard_ns
        while self._window and self._window[0].mono_ns < earliest:
          self._window.popleft()
        if self._bookmark_start_ns is None:
          self._bookmark_start_ns = rows[0]["mono_ns"]
        if rows[-1]["mono_ns"] - (self._bookmark_end_ns if self._bookmark_end_ns is not None else self._bookmark_start_ns) >= 60_000_000_000:
          self.bookmark()
    self._released_through_ns = max(self._released_through_ns, through_ns)

  def _observe_transition(self, row):
    item = self.transition[row["axis"]]
    stamp, previous = row["mono_ns"], item["observed_mono_ns"]
    gap = previous is not None and not 0 < stamp - previous <= self.profile.max_gap_ns
    if gap:
      item.update(ready=False, valid_since_mono_ns=None, last_blocked_mono_ns=stamp, reason="transition_observation_gap")
    item["observed_mono_ns"] = stamp
    reason = ("transition_driver_override" if row["driver_override"] is True else
              "transition_invalid_telemetry" if row["valid"] is not True or row["driver_override"] is not False else
              "transition_axis_inactive" if row["active"] is not True else
              "transition_invalid_telemetry" if row["recovery_ready"] is not True else None)
    if reason is not None:
      item.update(ready=False, reason=reason, valid_since_mono_ns=None, last_blocked_mono_ns=stamp)
    else:
      if item["valid_since_mono_ns"] is None:
        item["valid_since_mono_ns"] = stamp
      ready = stamp - item["valid_since_mono_ns"] >= TRANSITION_RECOVERY_NS
      item.update(ready=ready, reason="transition_ready" if ready else "transition_recovering")

  def _evaluate(self, *, partial=False):
    if not self._window or self._window[-1].mono_ns == self._last_diagnostic_end_ns:
      return
    first, last = self._window[0].mono_ns, self._window[-1].mono_ns
    incomplete_after_loss = self._needs_fresh_window and last - first < self.interval_ns
    if incomplete_after_loss and not partial:
      return
    diagnostic = _compact_diagnostic(analyze(self._window, self.profile))
    self.diagnostic_revision += 1
    diagnostic.update(revision=self.diagnostic_revision, invalidated=incomplete_after_loss, partial_window=partial,
                      window_start_ns=first, window_end_ns=last, window_includes_pre_history=True,
                      independence="not_established", settings_history="unknown",
                      observed_settings_revision=self.observed_settings_revision,
                      route_observation=deepcopy(self._route_observation),
                      settings_observation_state=self.settings_observation_state,
                      recommendations_enabled=False, apply_enabled=False, device_qualified=False)
    if incomplete_after_loss:
      diagnostic["invalidation_reason"] = "fresh_window_incomplete"
    self.diagnostic = diagnostic
    summary = {axis: {epoch: {key: value for key, value in values.items() if key != "exclusions"}
                      for epoch, values in axes["epochs"].items()} for axis, axes in diagnostic["axes"].items()}
    self.history.append({"revision": self.diagnostic_revision, "state": "invalidated" if incomplete_after_loss else "diagnostic",
                         "partial_window": partial,
                         "window_start_ns": first, "window_end_ns": last, "summary": summary})
    self._last_diagnostic_end_ns, self._next_evaluation_ns = last, last + self.interval_ns
    self._needs_fresh_window = incomplete_after_loss
    self.state, self.reason = "running", "unvalidated_diagnostic_ready"

  def bookmark(self, *, partial=False):
    """Bound a preservation job without evaluating or suggesting settings."""
    if self._bookmark_start_ns is None or not self._window:
      return None
    end = self._window[-1].mono_ns
    if end == self._bookmark_end_ns:
      return self.preservation_bookmark
    self.diagnostic_revision += 1
    self.preservation_bookmark = {"revision": self.diagnostic_revision, "window_start_ns": self._bookmark_start_ns,
      "window_end_ns": end, "profile_id": self.profile.profile_id, "invalidated": False, "partial_window": partial,
      "observed_settings_revision": self.observed_settings_revision, "route_observation": deepcopy(self._route_observation),
      "recording_only": True}
    self._bookmark_start_ns = self._bookmark_end_ns = end
    return self.preservation_bookmark

  def tick(self):
    self.parking.tick()
    if self.state in TERMINAL_STATES:
      return
    now = self.clock_ns()
    if self.max_session_ns is not None and now - self._started_wall_ns >= self.max_session_ns:
      self.finish("session_time_limit")
    elif self._last_received_wall_ns is not None and now - self._last_received_wall_ns > 2_000_000_000 and not self._stale_invalidated:
      self.gap_events += 1
      self.invalidate("input_stale")
      self._stale_invalidated = True

  def finish(self, reason="session_ended"):
    if self.state in TERMINAL_STATES:
      return
    self._drain(self._max_seen_ns)
    self.bookmark(partial=True)
    self.state, self.reason = "ended", reason
    self.parking.stop()
    self.transition = unknown_transition("transition_session_ended")

  def snapshot(self):
    if self.state in TERMINAL_STATES:
      self.parking.stop()
    parking = self.parking.snapshot()
    transition = deepcopy(self.transition)
    source_now = self.clock_ns() if self.source_mode == "live" else self._max_seen_ns
    for item in transition.values():
      stamp = item["observed_mono_ns"]
      if self.state in TERMINAL_STATES:
        item.update(ready=False, reason="transition_session_ended")
      elif stamp is None or not 0 <= source_now - stamp <= TRANSITION_MAX_AGE_NS:
        item.update(ready=False, reason="transition_input_stale")
    age_ms = None if self._last_received_wall_ns is None else max(0, self.clock_ns() - self._last_received_wall_ns) / 1_000_000
    counters = {name: getattr(self, name) for name in ("received_events", "observations", "late_events", "overflow_events",
                                                     "gap_events", "dropped_events", "poll_budget_overruns")}
    result = {"schema_version": 1, "session_id": self.session_id, "source_mode": self.source_mode,
            "state": self.state, **counters, "counters": counters, "age_ms": age_ms,
            "diagnostic_revision": self.diagnostic_revision, "diagnostic": self.diagnostic, "history": list(self.history),
            "reason": self.reason, "settings_history": "unknown", "recommendations_enabled": False,
            "apply_enabled": False, "parking_confirmed": self.source_mode == "live" and parking["state"] == "park_observed",
            "parking": parking, "last_park_observation": self.parking.last_park_observation(),
            "clock_mode": "accelerated_synthetic" if self.source_mode == "synthetic" else "native_monotonic",
            "finalized_through_ns": self.diagnostic.get("finalized_through_ns") if self.diagnostic else None,
            "diagnostic_invalidated": bool(self.diagnostic and self.diagnostic.get("invalidated")),
            "metadata": {"settings_history": "unknown", "gap_detection": "timestamp_heuristic_no_sequence",
                         "device_qualified": False, "overlapping_windows": True}}
    if self.recording_status is not None:
      result["recording"] = self.recording_status
    if self.consumer_observations is not None:
      result["consumer_observations"] = self.consumer_observations
    if self.worker_runtime is not None:
      result["metadata"]["worker_runtime"] = deepcopy(self.worker_runtime)
    result["transition"] = transition
    return result
