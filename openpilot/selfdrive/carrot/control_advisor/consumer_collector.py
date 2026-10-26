"""Bounded AF_UNIX observation receiver and per-consumer identity tracker.

Fresh sampled object state, fresh parameter reads and consumer application ACKs
are deliberately separate. No observation here authorizes a setting write.
"""

from copy import deepcopy
import json
import os
import re
import stat
import time

from .consumer_observer import (CONSUMER_BINDINGS, ENV_ENABLED, ENV_SOCKET, MAX_EVENT_BYTES, MAX_INT,
                                _native_runtime, _observation_config, _private_socket_parent, _read_boot_id, validate_event)


ENTRY_STATES = {"observed", "stale", "gap", "restarted", "unknown"}
SNAPSHOT_STATES = {"disabled", "waiting", "observed", "partial", "unavailable", "stopped"}


def _counter(value):
  if type(value) is not int or not 0 <= value <= MAX_INT:
    raise ValueError("invalid_consumer_counter")
  return value


def validate_snapshot(snapshot):
  required = {"schema_version", "state", "reason", "source_mode", "received_events", "rejected_events", "dropped_events",
              "restarts", "consumer_application_ack", "device_qualified", "consumers"}
  if not isinstance(snapshot, dict) or set(snapshot) - required - {"persistence"} or not required <= set(snapshot):
    raise ValueError("invalid_consumer_snapshot")
  if (type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1
      or snapshot["state"] not in SNAPSHOT_STATES or snapshot["source_mode"] not in ("live", "synthetic")
      or snapshot["consumer_application_ack"] is not False or snapshot["device_qualified"] is not False):
    raise ValueError("invalid_consumer_snapshot_state")
  reason = snapshot["reason"]
  if reason is not None and (not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,96}", reason)):
    raise ValueError("invalid_consumer_reason")
  result = {key: snapshot[key] for key in ("schema_version", "state", "reason", "source_mode", "consumer_application_ack", "device_qualified")}
  result.update({key: _counter(snapshot[key]) for key in ("received_events", "rejected_events", "dropped_events", "restarts")})
  entries = snapshot["consumers"]
  if not isinstance(entries, list) or len(entries) > len(CONSUMER_BINDINGS):
    raise ValueError("invalid_consumer_entries")
  consumers, seen = [], set()
  for entry in entries:
    if not isinstance(entry, dict) or set(entry) != {"consumer_id", "state", "fresh", "latest_event", "read_freshness"}:
      raise ValueError("invalid_consumer_entry")
    consumer = entry["consumer_id"]
    if consumer not in CONSUMER_BINDINGS or consumer in seen or entry["state"] not in ENTRY_STATES or type(entry["fresh"]) is not bool:
      raise ValueError("invalid_consumer_entry_state")
    seen.add(consumer)
    event = None if entry["latest_event"] is None else validate_event(entry["latest_event"])
    if event is not None and event["consumer_id"] != consumer:
      raise ValueError("consumer_entry_binding_mismatch")
    reads = entry["read_freshness"]
    expected = set(event["fields"]) if event else set()
    if not isinstance(reads, dict) or set(reads) != expected or any(type(value) is not bool for value in reads.values()):
      raise ValueError("invalid_consumer_read_freshness")
    if entry["fresh"] and (event is None or entry["state"] in ("stale", "unknown", "gap", "restarted")):
      raise ValueError("invalid_consumer_freshness")
    consumers.append({"consumer_id": consumer, "state": entry["state"], "fresh": entry["fresh"],
                      "latest_event": event, "read_freshness": dict(reads)})
  result["consumers"] = consumers
  if "persistence" in snapshot:
    persistence = snapshot["persistence"]
    if (not isinstance(persistence, dict) or set(persistence) != {"state", "recorded_events", "dropped_events"}
        or persistence["state"] not in {"disabled", "waiting", "recorded", "partial", "failed"}):
      raise ValueError("invalid_consumer_persistence")
    result["persistence"] = {"state": persistence["state"], "recorded_events": _counter(persistence["recorded_events"]),
                             "dropped_events": _counter(persistence["dropped_events"])}
  return result


def _unique(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("duplicate_consumer_key")
    result[key] = value
  return result


class _UnixReceiver:
  def __init__(self, path):
    import socket
    if not hasattr(socket, "AF_UNIX"):
      raise RuntimeError("native_consumer_transport_unavailable")
    self.path = _private_socket_parent(path)
    if os.path.lexists(self.path):
      raise ValueError("consumer_socket_already_exists")
    self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    self.credentials_required = hasattr(socket, "SO_PASSCRED") and hasattr(socket, "SCM_CREDENTIALS") and hasattr(self.socket, "recvmsg")
    self._identity = None
    try:
      if self.credentials_required:
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
      self.socket.setblocking(False)
      self.socket.bind(str(self.path))
      os.chmod(self.path, 0o600)
      info = self.path.lstat()
      self._identity = (info.st_dev, info.st_ino)
    except Exception:
      self.close()
      raise

  def receive(self, max_bytes):
    import socket
    import struct
    try:
      if self.credentials_required:
        data, ancillary, flags, _ = self.socket.recvmsg(max_bytes, socket.CMSG_SPACE(struct.calcsize("3i")))
        credentials = None
        for level, kind, value in ancillary:
          if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(value) >= struct.calcsize("3i"):
            pid, uid, _ = struct.unpack("3i", value[:struct.calcsize("3i")])
            credentials = {"pid": pid, "uid": uid}
        if flags & (getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0)):
          return b"x" * (MAX_EVENT_BYTES + 1), None
        return data, credentials
      return self.socket.recv(max_bytes), None
    except BlockingIOError:
      return None

  def close(self):
    self.socket.close()
    if self._identity is None:
      return
    try:
      info = self.path.lstat()
      if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self._identity:
        self.path.unlink()
    except FileNotFoundError:
      pass
    self._identity = None


def _prepare_native_socket_parent(path):
  from pathlib import Path
  target = Path(path)
  if not target.is_absolute() or any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (target, *target.parents)):
    raise ValueError("invalid_consumer_socket_path")
  try:
    target.parent.mkdir(mode=0o700, parents=False, exist_ok=False)
  except FileExistsError:
    pass
  # Existing directories are checked, never chmodded; no unowned socket is removed.
  _private_socket_parent(path)


class ConsumerCollector:
  def __init__(self, *, source_mode="live", transport=None, injected_transport=None, expected_boot_id=None,
               clock_ns=time.monotonic_ns, environment=None, enabled=None, max_events=128,
               poll_budget_ns=2_000_000, max_age_ns=2_000_000_000, read_max_age_ns=2_000_000_000,
               future_tolerance_ns=250_000_000):
    if source_mode not in ("live", "synthetic"):
      raise ValueError("invalid_consumer_source_mode")
    if transport is not None and injected_transport is not None:
      raise ValueError("duplicate_consumer_transport")
    transport = injected_transport if injected_transport is not None else transport
    if transport is not None and source_mode != "synthetic":
      raise ValueError("injected_consumer_transport_requires_synthetic")
    for value in (max_events, poll_budget_ns, max_age_ns, read_max_age_ns, future_tolerance_ns):
      if type(value) is not int or value <= 0:
        raise ValueError("invalid_consumer_budget")
    if enabled is not None and type(enabled) is not bool:
      raise ValueError("invalid_consumer_enable_flag")
    if expected_boot_id is not None and (not isinstance(expected_boot_id, str) or not re.fullmatch(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", expected_boot_id)):
      raise ValueError("invalid_expected_consumer_boot")
    self.source_mode, self.clock_ns = source_mode, clock_ns
    self.max_events, self.poll_budget_ns, self.max_age_ns = max_events, poll_budget_ns, max_age_ns
    self.read_max_age_ns, self.future_tolerance_ns = read_max_age_ns, future_tolerance_ns
    self.received_events = self.rejected_events = self.dropped_events = self.restarts = 0
    self._entries, self._retired = {}, {consumer: set() for consumer in CONSUMER_BINDINGS}
    self._burst_entry, self._burst_retired = None, set()
    self._burst_received = self._burst_rejected = 0
    self._closed = False
    self.transport = None
    self.expected_boot_id = expected_boot_id
    environment = os.environ if environment is None else environment
    self.state, self.reason = "disabled", "consumer_observations_disabled"
    if enabled is False or environment.get(ENV_ENABLED) == "0":
      return
    configured_active, configured_path = (_observation_config(environment) if source_mode == "live"
                                           else (environment.get(ENV_ENABLED) == "1", environment.get(ENV_SOCKET, "")))
    active = (transport is not None or configured_active) if enabled is None else enabled
    if not active:
      return
    if source_mode == "synthetic" and transport is None:
      self.reason = "synthetic_consumer_transport_not_configured"
      return
    if transport is not None:
      if expected_boot_id is None:
        raise ValueError("synthetic_consumer_boot_required")
      self.transport = transport
      self.state, self.reason = "waiting", "waiting_for_consumer_observations"
      return
    path = configured_path
    if not isinstance(path, str) or not os.path.isabs(path):
      self.state, self.reason = "unavailable", "consumer_socket_not_configured"
      return
    try:
      if _native_runtime():
        _prepare_native_socket_parent(path)
      self.transport = _UnixReceiver(path)
      self.expected_boot_id = _read_boot_id() if expected_boot_id is None else expected_boot_id
      self.state, self.reason = "waiting", "waiting_for_consumer_observations"
    except Exception as exc:
      known = {"consumer_socket_already_exists", "consumer_socket_directory_not_private", "invalid_consumer_socket_path"}
      reason = str(exc)
      self.state, self.reason = "unavailable", reason if reason in known else "native_consumer_transport_unavailable"

  def poll(self):
    if self._closed or self.transport is None:
      return []
    result, start = [], time.monotonic_ns()
    for _ in range(self.max_events):
      if time.monotonic_ns() - start >= self.poll_budget_ns:
        break
      try:
        packet = self.transport.receive(MAX_EVENT_BYTES + 1)
      except Exception:
        self.state, self.reason = "unavailable", "consumer_transport_failed"
        break
      if packet is None:
        break
      self.received_events += 1
      burst = False
      try:
        raw, credentials = packet if isinstance(packet, tuple) and len(packet) == 2 else (packet, None)
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_EVENT_BYTES:
          raise ValueError("consumer_datagram_limit")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_consumer_json")))
        burst = isinstance(payload, dict) and payload.get("event_kind") == "pid_burst_chunk"
        if burst:
          from .pid_burst import validate_chunk
          self.received_events -= 1
          self._burst_received += 1
          event = validate_chunk(payload)
        else:
          event = validate_event(payload)
        if getattr(self.transport, "credentials_required", False):
          expected_uid = os.getuid() if hasattr(os, "getuid") else None
          if (not isinstance(credentials, dict) or credentials.get("pid") != event["pid"]
              or expected_uid is None or credentials.get("uid") != expected_uid):
            raise ValueError("consumer_sender_identity_mismatch")
        if burst:
          self._accept_burst(event)
        else:
          self._accept(event)
        result.append(event)
      except (ValueError, TypeError, UnicodeError, KeyError, OverflowError, RecursionError):
        if burst:
          self._burst_rejected += 1
        else:
          self.rejected_events += 1
    return result

  def _accept_burst(self, event):
    now = self.clock_ns()
    if (self.expected_boot_id is not None and event["boot_id"] != self.expected_boot_id
        or event["produced_mono_ns"] > now + self.future_tolerance_ns
        or now - event["produced_mono_ns"] > self.max_age_ns):
      raise ValueError("pid_burst_identity_or_time_mismatch")
    identity = tuple(event[name] for name in ("boot_id", "pid", "process_instance_id", "consumer_instance_id"))
    position = event["round_id"], event["block_id"], event["chunk_sequence"]
    if identity in self._burst_retired:
      raise ValueError("retired_pid_burst_instance")
    if self._burst_entry is not None:
      previous = self._burst_entry
      if identity != previous["identity"]:
        if event["produced_mono_ns"] <= previous["time"] or len(self._burst_retired) >= 8:
          raise ValueError("pid_burst_restart_not_ordered")
        self._burst_retired.add(previous["identity"])
      elif position <= previous["position"] or event["produced_mono_ns"] < previous["time"]:
        raise ValueError("pid_burst_sequence_not_increasing")
    self._burst_entry = {"identity": identity, "position": position, "time": event["produced_mono_ns"]}

  def _accept(self, event):
    now = self.clock_ns()
    if (self.expected_boot_id is not None and event["boot_id"] != self.expected_boot_id
        or event["produced_mono_ns"] > now + self.future_tolerance_ns
        or now - event["produced_mono_ns"] > self.max_age_ns):
      raise ValueError("consumer_event_identity_or_time_mismatch")
    consumer = event["consumer_id"]
    identity = (event["boot_id"], event["pid"], event["process_instance_id"], event["consumer_instance_id"])
    previous = self._entries.get(consumer)
    state = "observed"
    if identity in self._retired[consumer]:
      raise ValueError("retired_consumer_instance")
    if previous is not None:
      old = previous["event"]
      if identity != previous["identity"]:
        if event["produced_mono_ns"] <= old["produced_mono_ns"] or len(self._retired[consumer]) >= 8:
          raise ValueError("consumer_restart_not_ordered")
        self._retired[consumer].add(previous["identity"])
        self.restarts += 1
        state = "restarted"
      else:
        if event["sequence"] <= old["sequence"] or event["produced_mono_ns"] < old["produced_mono_ns"] or event["dropped_events"] < old["dropped_events"]:
          raise ValueError("consumer_sequence_not_increasing")
        gap = max(event["sequence"] - old["sequence"] - 1, event["dropped_events"] - old["dropped_events"])
        if gap:
          self.dropped_events += gap
          state = "gap"
    elif event["dropped_events"]:
      self.dropped_events += event["dropped_events"]
      state = "gap"
    self._entries[consumer] = {"event": event, "identity": identity, "state": state}

  def snapshot(self):
    now, consumers = self.clock_ns(), []
    issue = False
    for consumer in CONSUMER_BINDINGS:
      entry = self._entries.get(consumer)
      if entry is None:
        consumers.append({"consumer_id": consumer, "state": "unknown", "fresh": False, "latest_event": None, "read_freshness": {}})
        continue
      event = entry["event"]
      identity_known = self.expected_boot_id is not None and event["boot_id"] == self.expected_boot_id
      age_fresh = (not self._closed and self.state != "unavailable" and identity_known
                   and -self.future_tolerance_ns <= now - event["produced_mono_ns"] <= self.max_age_ns)
      state = entry["state"] if age_fresh else "stale" if identity_known else "unknown"
      fresh = age_fresh and state not in ("gap", "restarted")
      reads = {key: bool(fresh and item["read_mono_ns"] is not None
                       and 0 <= now - item["read_mono_ns"] <= self.read_max_age_ns) for key, item in event["fields"].items()}
      provenance_known = all(value is not None for value in event["source_hashes"].values())
      issue |= not fresh or state in ("gap", "restarted") or not provenance_known
      consumers.append({"consumer_id": consumer, "state": state, "fresh": bool(fresh),
                        "latest_event": deepcopy(event), "read_freshness": reads})
    state, reason = self.state, self.reason
    if state not in ("disabled", "unavailable", "stopped") and self._entries:
      state = "partial" if issue or self.rejected_events or self.dropped_events else "observed"
      reason = "consumer_observation_limits" if state == "partial" else "sampled_consumer_observations"
    return validate_snapshot({"schema_version": 1, "state": state, "reason": reason, "source_mode": self.source_mode,
                              "received_events": self.received_events, "rejected_events": self.rejected_events,
                              "dropped_events": self.dropped_events, "restarts": self.restarts,
                              "consumer_application_ack": False, "device_qualified": False, "consumers": consumers})

  def close(self):
    if self._closed:
      return
    self._closed = True
    if self.transport is not None:
      try:
        self.transport.close()
      except Exception:
        pass
    if self.state not in ("disabled", "unavailable"):
      self.state, self.reason = "stopped", "consumer_observations_stopped"
