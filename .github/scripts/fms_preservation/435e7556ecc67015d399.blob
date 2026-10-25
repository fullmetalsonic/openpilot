"""Owned periodic offline experiments. No Params, control, or automatic applying.

The recording I/O thread owns tick/close; offer only copies one bounded token.
Heavy decoding runs in a child. Only the parent verifies and publishes results.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import uuid

from .candidate_report import _read, _code_hashes, load_candidate_bundle
from .evidence import hash_file, _sync_directory

STATES = {"disabled", "waiting", "running", "ready", "partial", "failed", "stopped"}
HEARTBEAT_NS = 10_000_000_000  # Host status policy, not device qualification.
_ID = re.compile(r"^[a-zA-Z0-9_.-]{1,128}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _json(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value):
  return hashlib.sha256(_json(value)).hexdigest()


def _int(value, minimum=0):
  if type(value) is not int or not minimum <= value <= 2**63 - 1:
    raise ValueError("invalid_periodic_integer")


def initial_periodic_status(interval_seconds=300, reason="auto_not_enabled", state="disabled"):
  return validate_periodic_status({"state": state, "reason": reason, "interval_seconds": interval_seconds,
    "result_revision": 0, "stale": False, "attempt": None, "last_success": None,
    "recommendations_enabled": False, "apply_enabled": False, "device_qualified": False})


def validate_periodic_status(value):
  required = {"state", "reason", "interval_seconds", "result_revision", "stale", "attempt", "last_success",
              "recommendations_enabled", "apply_enabled", "device_qualified"}
  if type(value) is not dict or set(value) != required or value["state"] not in STATES or type(value["interval_seconds"]) is not int or value["interval_seconds"] not in (180, 300):
    raise ValueError("invalid_periodic_status")
  if value["reason"] is not None and (type(value["reason"]) is not str or _ID.fullmatch(value["reason"]) is None):
    raise ValueError("invalid_periodic_reason")
  _int(value["result_revision"])
  if type(value["stale"]) is not bool or any(value[key] is not False for key in ("recommendations_enabled", "apply_enabled", "device_qualified")):
    raise ValueError("invalid_periodic_claim")
  for name in ("attempt", "last_success"):
    record = value[name]
    if record is None:
      continue
    fields = {"job_id", "source_start_ns", "source_end_ns", "source_count"}
    fields |= {"state", "cutoff_mono_ns", "processed_windows", "blocked_windows", "windows"} if name == "attempt" else {"result_revision"}
    if type(record) is not dict or set(record) != fields or type(record["job_id"]) is not str or _SHA.fullmatch(record["job_id"]) is None:
      raise ValueError("invalid_periodic_attempt")
    _int(record["source_count"])
    if record["source_count"] > 8:
      raise ValueError("periodic_window_limit")
    for key in ("source_start_ns", "source_end_ns"):
      if record[key] is not None:
        _int(record[key])
    if (record["source_start_ns"] is None) != (record["source_end_ns"] is None) or (record["source_start_ns"] is not None and record["source_start_ns"] > record["source_end_ns"]):
      raise ValueError("invalid_periodic_range")
    if name == "last_success":
      _int(record["result_revision"], 1)
      if record["source_start_ns"] is None or not record["source_count"]:
        raise ValueError("invalid_periodic_success")
    else:
      if record["state"] not in {"waiting", "running", "ready", "partial", "failed", "timeout", "cancelled"}:
        raise ValueError("invalid_periodic_attempt_state")
      for key in ("cutoff_mono_ns", "processed_windows", "blocked_windows"):
        _int(record[key])
      if type(record["windows"]) is not list or len(record["windows"]) > 8:
        raise ValueError("invalid_periodic_windows")
      for window in record["windows"]:
        if type(window) is not dict or set(window) != {"source_start_ns", "source_end_ns", "state", "reason"} or window["state"] not in {"ready", "failed", "blocked"}:
          raise ValueError("invalid_periodic_window")
        for key in ("source_start_ns", "source_end_ns"):
          if window[key] is not None:
            _int(window[key])
        if (window["source_start_ns"] is None) != (window["source_end_ns"] is None) or (window["source_start_ns"] is not None and window["source_start_ns"] > window["source_end_ns"]) or (window["reason"] is not None and (type(window["reason"]) is not str or _ID.fullmatch(window["reason"]) is None)):
          raise ValueError("invalid_periodic_window_range")
  if len(_json(value)) > 16 * 1024:
    raise ValueError("periodic_status_limit")
  return deepcopy(value)


def _atomic(path, value, *, durable=False):
  if path.is_symlink():
    raise ValueError("periodic_pointer_link")
  temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
  with temporary.open("xb") as stream:
    stream.write(_json(value))
    stream.flush()
    if durable:
      os.fsync(stream.fileno())
  os.replace(temporary, path)
  if durable:
    _sync_directory(path.parent)


def _usage(root):
  total = count = 0
  for directory, folders, files in os.walk(root, followlinks=False):
    for name in [*folders, *files]:
      path = Path(directory) / name
      info = path.lstat()
      count += 1
      if count > 8192 or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("periodic_cache_structure")
      if stat.S_ISREG(info.st_mode):
        total += info.st_size
      elif not stat.S_ISDIR(info.st_mode):
        raise ValueError("periodic_cache_structure")
  return total


def _verify_job(directory, job_id):
  result, digest = _read(directory / "job-result.json", 64 * 1024)
  if (type(result) is not dict or set(result) != {"schema_version", "job_id", "source_key", "settling_retry_needed", "state", "source_start_ns", "source_end_ns", "source_count", "windows"}
      or result["schema_version"] != 1 or result["job_id"] != job_id or result["state"] not in {"ready", "partial", "waiting"}
      or type(result["source_key"]) is not str or _SHA.fullmatch(result["source_key"]) is None
      or type(result["settling_retry_needed"]) is not bool
      or type(result["windows"]) is not list or len(result["windows"]) > 8):
    raise ValueError("invalid_periodic_job_result")
  for key in ("source_start_ns", "source_end_ns"):
    if result[key] is not None:
      _int(result[key])
  if ((result["source_start_ns"] is None) != (result["source_end_ns"] is None) or (result["source_start_ns"] is not None and result["source_start_ns"] > result["source_end_ns"])
      or type(result["source_count"]) is not int or not 1 <= result["source_count"] <= 8 or (result["state"] != "waiting" and result["source_count"] != len(result["windows"]))):
    raise ValueError("invalid_periodic_job_range")
  if result["state"] == "waiting":
    if result["windows"]:
      raise ValueError("invalid_periodic_waiting_windows")
    return result, digest, None
  latest = None
  for index, window in enumerate(result["windows"]):
    if set(window) != {"source_start_ns", "source_end_ns", "state", "reason", "directory", "range_kind", "coverage_verified"} or window["state"] not in {"ready", "failed", "blocked"}:
      raise ValueError("invalid_periodic_job_window")
    for key in ("source_start_ns", "source_end_ns"):
      if window[key] is not None:
        _int(window[key])
    expected = f"window-{index:04d}"
    if (window["directory"] != expected or (window["source_start_ns"] is None) != (window["source_end_ns"] is None)
        or (window["source_start_ns"] is not None and window["source_start_ns"] > window["source_end_ns"])
        or window["range_kind"] not in {"verified_car_state", "receipt_declared", "unknown"} or type(window["coverage_verified"]) is not bool):
      raise ValueError("invalid_periodic_window_path")
    if window["state"] == "ready":
      if not window["coverage_verified"] or window["source_start_ns"] is None:
        raise ValueError("unverified_periodic_ready_window")
      latest = load_candidate_bundle(directory / expected)
    else:
      latest = None  # Never replace a failed latest window with an older score.
    if index and result["windows"][index - 1]["coverage_verified"] and window["coverage_verified"] and result["windows"][index - 1]["source_end_ns"] >= window["source_start_ns"]:
      raise ValueError("periodic_window_order")
  if (result["state"] == "ready") != all(window["state"] == "ready" for window in result["windows"]):
    raise ValueError("periodic_job_state_mismatch")
  return result, digest, latest


def read_periodic_result(root, *, cache=None):
  """Cache verified immutable reports; always re-evaluate publisher freshness."""
  disabled = {**initial_periodic_status(), "report": None, "raw_sources_verified": False}
  if not root:
    return disabled
  root = Path(root)
  if not root.is_absolute():
    return {**disabled, "state": "invalid", "reason": "periodic_root_requires_absolute"}
  try:
    envelope, status_hash = _read(root / "status.json", 32 * 1024)
    if set(envelope) != {"schema_version", "heartbeat_mono_ns", "producer_instance_id", "status"} or envelope["schema_version"] != 1:
      raise ValueError("invalid_periodic_status_file")
    _int(envelope["heartbeat_mono_ns"])
    status = validate_periodic_status(envelope["status"])
    pointer, pointer_hash = (None, None)
    if (root / "latest.json").exists():
      pointer, pointer_hash = _read(root / "latest.json", 16 * 1024)
    token = (str(root), _hash(status), pointer_hash)
    if cache is not None and cache.get("token") == token:
      value = deepcopy(cache["value"])
    else:
      value = {**status, "report": None, "raw_sources_verified": False}
      if status["state"] == "ready" and not status["stale"]:
        if (type(pointer) is not dict or set(pointer) != {"schema_version", "job_id", "directory", "manifest_sha256", "result_revision"}
            or pointer["schema_version"] != 1 or pointer["result_revision"] != status["result_revision"]
            or re.fullmatch(r"[a-f0-9]{64}-[a-f0-9]{8}", pointer["directory"]) is None):
          raise ValueError("invalid_periodic_pointer")
        result, digest, loaded = _verify_job(root / "jobs" / pointer["directory"], pointer["job_id"])
        if digest != pointer["manifest_sha256"] or result["state"] != "ready" or loaded is None or status["attempt"]["job_id"] != pointer["job_id"]:
          raise ValueError("periodic_publication_binding")
        value = {**loaded, **status}
      if cache is not None:
        cache.clear()
        cache.update(token=token, value=deepcopy(value))
    age = time.monotonic_ns() - envelope["heartbeat_mono_ns"]
    if not 0 <= age <= HEARTBEAT_NS and value["state"] not in {"disabled", "stopped"}:
      value.update(state="failed", reason="periodic_publisher_unavailable", stale=value["last_success"] is not None, report=None)
    if value["state"] != "ready" or value["stale"]:
      value["report"] = None
    return value
  except FileNotFoundError:
    return {**disabled, "state": "waiting", "reason": "periodic_result_not_published"}
  except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
    return {**disabled, "state": "invalid", "reason": "periodic_result_invalid"}


class PeriodicAnalyzer:
  def __init__(self, session_directory, profile_path, objectives_path, output_root, *, interval_seconds=300,
               source_mode="live", wall_timeout_seconds=120, clock=time.monotonic,
               max_cache_bytes=512 * 1024 * 1024, max_jobs=32):
    self.status = initial_periodic_status(interval_seconds)
    self.clock, self.timeout = clock, wall_timeout_seconds
    self._pending = self._process = self._job = self._lease = None
    self._complete, self._source_complete, self._eligible_complete, self._attempts = set(), set(), set(), {}
    self._initialized = self._closed = False
    self._next = clock() + interval_seconds
    self._last_write = -float("inf")
    self._last_status_hash = None
    self._settling_sequences = {}
    self._instance = uuid.uuid4().hex
    self.max_cache_bytes, self.max_jobs = max_cache_bytes, max_jobs
    self.session, self.profile, self.objectives, self.root = [Path(value) if value else None for value in (session_directory, profile_path, objectives_path, output_root)]
    self._configured = all(path is not None and path.is_absolute() for path in (self.session, self.profile, self.objectives, self.root))
    if self._configured and source_mode == "live":
      self.status.update(state="waiting", reason="waiting_for_closed_archive")
    else:
      self._configured = False
      self.status["reason"] = "synthetic_automatic_analysis_unavailable" if source_mode != "live" else "auto_not_enabled"
    if type(wall_timeout_seconds) not in (int, float) or not 0 < wall_timeout_seconds <= 600 or type(max_jobs) is not int or not 1 <= max_jobs <= 32 or type(max_cache_bytes) is not int or not 1 <= max_cache_bytes <= 2**31:
      raise ValueError("invalid_periodic_budget")

  def offer(self, checkpoint, *, archive_watermark=None):
    from .settings_history import validate_checkpoint
    if not self._configured or self._closed:
      return False
    if archive_watermark is not None and (type(archive_watermark) is not str or _SHA.fullmatch(archive_watermark) is None):
      raise ValueError("invalid_archive_watermark")
    self._pending = (deepcopy(validate_checkpoint(checkpoint)), archive_watermark)
    return True

  def snapshot(self):
    return validate_periodic_status(self.status)

  def _persist(self, *, force=False):
    status = self.snapshot()
    status_hash = _hash(status)
    if self._initialized and (force or status_hash != self._last_status_hash or self.clock() - self._last_write >= 1):
      _atomic(self.root / "status.json", {"schema_version": 1, "heartbeat_mono_ns": time.monotonic_ns(),
             "producer_instance_id": self._instance, "status": self.snapshot()})
      self._last_write = self.clock()
      self._last_status_hash = status_hash

  def _initialize(self):
    resolved = self.root.resolve()
    session = self.session.resolve()
    if resolved.is_relative_to(session) or session.is_relative_to(resolved):
      raise ValueError("periodic_root_session_overlap")
    if self.root.is_symlink():
      raise ValueError("periodic_root_link")
    self.root.mkdir(parents=False, exist_ok=True)
    marker = self.root / "periodic-root.json"
    if marker.exists():
      if _read(marker, 1024)[0] != {"schema_version": 1, "scope": "periodic_offline_analysis_cache"}:
        raise ValueError("periodic_root_unowned")
    elif any(self.root.iterdir()):
      raise ValueError("periodic_root_unowned")
    from .recording import _RootLease
    self._lease = _RootLease(self.root)
    if not marker.exists():
      _atomic(marker, {"schema_version": 1, "scope": "periodic_offline_analysis_cache"}, durable=True)
    (self.root / "jobs").mkdir(exist_ok=True)
    if (self.root / "latest.json").exists():
      old = read_periodic_result(self.root)
      if old.get("last_success"):
        self.status["last_success"] = old["last_success"]
      self.status["result_revision"] = old.get("result_revision", 0)
    for directory in (self.root / "jobs").iterdir():
      if directory.is_symlink() or not directory.is_dir():
        raise ValueError("periodic_job_structure")
      request, _ = _read(directory / "request.json", 64 * 1024)
      if request["job_id"] != _hash(request["identity"]):
        raise ValueError("periodic_request_identity")
      eligibility = request["eligibility_key"]
      self._attempts[eligibility] = self._attempts.get(eligibility, 0) + 1
      if (directory / "published.json").exists():
        published, _ = _read(directory / "published.json", 16 * 1024)
        result, digest, _ = _verify_job(directory, published["job_id"])
        if digest != published["manifest_sha256"]:
          raise ValueError("periodic_completed_key_corrupt")
        self._complete.add(result["job_id"])
        if result["state"] == "ready":
          if not result["settling_retry_needed"] or request["settling_retry"]:
            self._source_complete.add(result["source_key"])
            self._eligible_complete.add(eligibility)
          else:
            self._settling_sequences[eligibility] = request["identity"]["checkpoint"]["sequence"]
    self._initialized = True

  def _failure(self, reason, state="failed"):
    self.status.update(state="failed", reason=reason, stale=self.status["last_success"] is not None)
    if self.status["attempt"]:
      self.status["attempt"]["state"] = state

  def _stop_child(self, timeout=2.):
    process = self._process
    if process is None:
      return
    deadline = time.monotonic() + max(0, timeout)
    try:
      process.stdin.close()
    except (OSError, ValueError):
      pass
    for action, duration in ((None, .2), (process.terminate, .5), (process.kill, .5)):
      if process.poll() is not None:
        break
      if action:
        try:
          action()
        except OSError:
          pass
      try:
        process.wait(timeout=max(0., min(duration, deadline - time.monotonic())))
      except subprocess.TimeoutExpired:
        pass
    if process.poll() is None:
      raise ValueError("periodic_shutdown_incomplete")
    self._process = None

  def tick(self):
    if not self._configured or self._closed:
      return self.snapshot()
    try:
      if not self._initialized:
        self._initialize()
      if self._process is not None:
        if any((self._job / name).stat().st_size > 64 * 1024 for name in ("stdout.txt", "stderr.txt")):
          self._stop_child()
          self._failure("periodic_child_output_limit")
        elif self.clock() - self._started > self.timeout:
          self._stop_child()
          self._failure("periodic_job_timeout", "timeout")
        elif self._process.poll() is not None:
          code = self._process.returncode
          self._process.stdin.close()
          self._process = None
          if code != 0:
            self._failure("periodic_child_failed")
          else:
            result, digest, _ = _verify_job(self._job, self.status["attempt"]["job_id"])
            attempt = self.status["attempt"]
            attempt.update({key: result[key] for key in ("source_start_ns", "source_end_ns", "source_count")})
            attempt.update(state=result["state"], windows=[{k: window[k] for k in ("source_start_ns", "source_end_ns", "state", "reason")} for window in result["windows"]],
                           processed_windows=sum(w["state"] == "ready" for w in result["windows"]), blocked_windows=sum(w["state"] != "ready" for w in result["windows"]))
            if result["state"] == "waiting":
              self.status.update(state="waiting", reason="waiting_for_new_closed_archive", stale=self.status["last_success"] is not None)
              marker = {"schema_version": 1, "job_id": result["job_id"], "directory": self._job.name,
                        "manifest_sha256": digest, "result_revision": self.status["result_revision"]}
              _atomic(self._job / "published.json", marker, durable=True)
              self._complete.add(result["job_id"])
              self._persist(force=True)
              return self.snapshot()
            self.status["result_revision"] += 1
            self.status.update(state=result["state"], reason=None if result["state"] == "ready" else "periodic_windows_partial",
                               stale=result["state"] != "ready" and self.status["last_success"] is not None)
            if result["state"] == "ready":
              self.status["last_success"] = {"job_id": attempt["job_id"], "result_revision": self.status["result_revision"],
                                            **{key: result[key] for key in ("source_start_ns", "source_end_ns", "source_count")}}
            pointer = {"schema_version": 1, "job_id": result["job_id"], "directory": self._job.name,
                       "manifest_sha256": digest, "result_revision": self.status["result_revision"]}
            _atomic(self._job / "published.json", pointer, durable=True)
            _atomic(self.root / "latest.json", pointer, durable=True)
            self._complete.add(result["job_id"])
            if result["state"] == "ready":
              if not result["settling_retry_needed"] or self._settling_retry:
                self._source_complete.add(result["source_key"])
                self._eligible_complete.add(self._eligibility)
              else:
                request, _ = _read(self._job / "request.json", 64 * 1024)
                self._settling_sequences[self._eligibility] = request["identity"]["checkpoint"]["sequence"]
      if self._process is None and self._pending is not None and self.clock() >= self._next:
        (checkpoint, watermark), self._pending = self._pending, None
        self._next = self.clock() + self.status["interval_seconds"]
        profile_hash, _ = hash_file(self.profile, 4 * 1024 * 1024)
        objective_hash, _ = hash_file(self.objectives, 64 * 1024)
        identity = {"checkpoint": checkpoint, "profile_sha256": profile_hash, "objectives_sha256": objective_hash, "code_hashes": _code_hashes()}
        job_id = _hash(identity)
        eligibility = _hash({"archive_watermark": watermark or _hash(checkpoint), "session_id": checkpoint["session_id"],
                             **{key: identity[key] for key in ("profile_sha256", "objectives_sha256", "code_hashes")}})
        settling = eligibility in self._settling_sequences
        advanced = not settling or checkpoint["sequence"] > self._settling_sequences[eligibility]
        if eligibility not in self._eligible_complete and job_id not in self._complete and self._attempts.get(eligibility, 0) < 2 and advanced:
          if len(list((self.root / "jobs").iterdir())) >= self.max_jobs or _usage(self.root) + 128 * 1024 * 1024 > self.max_cache_bytes:
            raise ValueError("periodic_cache_quota")
          self._job = self.root / "jobs" / f"{job_id}-{uuid.uuid4().hex[:8]}"
          self._job.mkdir()
          request = {"schema_version": 1, "job_id": job_id, "identity": identity, "session_directory": str(self.session),
                     "profile_path": str(self.profile), "objectives_path": str(self.objectives), "interval_seconds": self.status["interval_seconds"],
                     "completed_source_keys": sorted(self._source_complete), "eligibility_key": eligibility, "settling_retry": settling}
          _atomic(self._job / "request.json", request, durable=True)
          with (self._job / "stdout.txt").open("xb") as stdout, (self._job / "stderr.txt").open("xb") as stderr:
            self._process = subprocess.Popen([sys.executable, "-m", __name__, "--request", str(self._job / "request.json")],
                                             stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
          self._started = self.clock()
          self._eligibility = eligibility
          self._settling_retry = settling
          self._attempts[eligibility] = self._attempts.get(eligibility, 0) + 1
          self.status.update(state="running", reason=None, stale=self.status["last_success"] is not None,
            attempt={"state": "running", "job_id": job_id, "cutoff_mono_ns": checkpoint["cutoff_mono_ns"],
                     "source_start_ns": None, "source_end_ns": None, "source_count": 0, "processed_windows": 0, "blocked_windows": 0, "windows": []})
      self._persist()
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
      if self._process is not None:
        self._stop_child()
      reason = str(error)
      self._failure(reason if _ID.fullmatch(reason) else "periodic_processing_failed")
      self._persist(force=True)
    return self.snapshot()

  def close(self, *, timeout=2.):
    if self._closed:
      return self.snapshot()
    self._closed, self._pending = True, None
    self._stop_child(timeout)
    self.status.update(state="stopped", reason="periodic_stopped", stale=self.status["last_success"] is not None)
    if self.status["attempt"] and self.status["attempt"]["state"] == "running":
      self.status["attempt"]["state"] = "cancelled"
    self._persist(force=True)
    if self._lease is not None:
      self._lease.close()
    return self.snapshot()


def _child(request_path):
  stop, finished = threading.Event(), threading.Event()
  def parent_pipe():
    try:
      os.read(sys.stdin.fileno(), 1)
    except OSError:
      pass
    stop.set()
    if not finished.wait(2.):
      os._exit(75)
  threading.Thread(target=parent_pipe, daemon=True, name="advisor-analysis-parent").start()
  try:
    request, _ = _read(request_path, 64 * 1024)
    identity = request["identity"]
    if request["job_id"] != _hash(identity) or _code_hashes() != identity["code_hashes"] or hash_file(request["profile_path"], 4 * 1024 * 1024)[0] != identity["profile_sha256"] or hash_file(request["objectives_path"], 64 * 1024)[0] != identity["objectives_sha256"]:
      raise ValueError("periodic_input_identity_changed")
    from .journal_snapshot import create_journal_snapshot
    from .log_candidates import calculate_log_candidates
    directory = Path(request_path).parent
    snapshot = create_journal_snapshot(request["session_directory"], directory / "snapshot", identity["checkpoint"], window_seconds=request["interval_seconds"])
    route = snapshot["route_observation"]
    if not route or route["state"] != "observed":
      raise ValueError("periodic_route_unobserved")
    available = {source["archive_id"]: source for source in snapshot["archive_sources"] if source["route"] == route["route"]}
    expected_sources = [source for source in snapshot["expected_archive_sources"] if source["route"] == route["route"]]
    if not expected_sources:
      raise ValueError("periodic_closed_sources_unavailable")
    times = [source["end_ns"] for source in available.values()] or [source["end_ns"] for source in expected_sources if source["end_ns"] is not None]
    end = max(times) if times else None
    start = end - request["interval_seconds"] * 1_000_000_000 if end is not None else None
    candidates = [source["segment"] for source in available.values() if start is None or source["end_ns"] > start]
    low = min(candidates) if candidates else min(source["segment"] for source in expected_sources)
    lower = [source["segment"] for source in expected_sources if start is not None and source["end_ns"] is not None and source["end_ns"] > start]
    low = min([low, *lower])
    unknown_predecessors = [source["segment"] for source in expected_sources if source["segment"] == low - 1 and source["end_ns"] is None]
    low = min([low, *unknown_predecessors])
    high = max(source["segment"] for source in expected_sources)
    if high - low + 1 > 8:
      raise ValueError("periodic_window_budget")
    indexed = {}
    for source in expected_sources:
      if source["segment"] in indexed and source["sha256"] != indexed[source["segment"]]["sha256"]:
        raise ValueError("periodic_source_identity_ambiguous")
      if source["segment"] in indexed and indexed[source["segment"]]["state"] == "available":
        continue
      indexed[source["segment"]] = source
    sources = [indexed.get(segment, {"archive_id": None, "segment": segment, "start_ns": None, "end_ns": None,
               "state": "unavailable", "reason": "periodic_segment_missing", "range_kind": "unknown", "coverage_verified": False}) for segment in range(low, high + 1)]
    if len(sources) > 8:
      raise ValueError("periodic_window_budget")
    source_key = _hash({"sources": sources,
                        **{key: identity[key] for key in ("profile_sha256", "objectives_sha256", "code_hashes")}})
    if source_key in request["completed_source_keys"]:
      _atomic(directory / "job-result.json", {"schema_version": 1, "job_id": request["job_id"], "source_key": source_key, "settling_retry_needed": False, "state": "waiting",
        "source_start_ns": sources[0]["start_ns"] if sources[-1]["end_ns"] is not None else None,
        "source_end_ns": sources[-1]["end_ns"] if sources[0]["start_ns"] is not None else None, "source_count": len(sources), "windows": []}, durable=True)
      return 0
    windows, settling_retry_needed = [], False
    objective_config, _ = _read(request["objectives_path"], 64 * 1024)
    for index, source in enumerate(sources):
      if stop.is_set():
        raise ValueError("periodic_cancelled")
      window = {"source_start_ns": source["start_ns"], "source_end_ns": source["end_ns"], "state": "failed", "reason": None, "directory": f"window-{index:04d}",
                "range_kind": source["range_kind"], "coverage_verified": source["coverage_verified"]}
      if source["state"] != "available" or source["archive_id"] not in available:
        window.update(state="blocked", reason=source["reason"] or "periodic_archive_unavailable")
        windows.append(window)
        continue
      verified_source = available[source["archive_id"]]
      if index and sources[index - 1]["state"] == "available" and source["start_ns"] - sources[index - 1]["end_ns"] > objective_config["analysis_profile"]["max_gap_ns"]:
        window.update(state="blocked", reason="periodic_source_gap")
        windows.append(window)
        continue
      try:
        if _usage(directory) + 32 * 1024 * 1024 > 128 * 1024 * 1024:
          raise ValueError("periodic_job_output_quota")
        calculate_log_candidates([verified_source["path"]], request["profile_path"], request["objectives_path"], directory / window["directory"],
                                 session_directory=request["session_directory"], session_snapshot=snapshot["snapshot_directory"], cancelled=stop.is_set)
        loaded = load_candidate_bundle(directory / window["directory"])
        settling_retry_needed |= any(row["key"] in objective_config["objectives"] and "required_consumer_observation_missing" in row["reason_codes"] for row in loaded["report"]["items"])
        settling_retry_needed |= "session_right_bracket_missing" in loaded.get("log_link", {}).get("reasons", [])
        window["state"] = "ready"
      except (OSError, ValueError, ImportError, RuntimeError) as error:
        reason = str(error)
        window["reason"] = reason if _ID.fullmatch(reason) else "periodic_window_failed"
      windows.append(window)
    if stop.is_set() or _code_hashes() != identity["code_hashes"]:
      raise ValueError("periodic_cancelled_or_code_changed")
    _atomic(directory / "job-result.json", {"schema_version": 1, "job_id": request["job_id"],
      "source_key": source_key, "settling_retry_needed": settling_retry_needed, "state": "ready" if all(w["state"] == "ready" for w in windows) else "partial",
      "source_start_ns": sources[0]["start_ns"] if sources[-1]["end_ns"] is not None else None,
      "source_end_ns": sources[-1]["end_ns"] if sources[0]["start_ns"] is not None else None, "source_count": len(sources), "windows": windows}, durable=True)
    return 0
  finally:
    finished.set()


if __name__ == "__main__":
  import argparse
  parser = argparse.ArgumentParser()
  parser.add_argument("--request", required=True)
  arguments = parser.parse_args()
  try:
    raise SystemExit(_child(arguments.request))
  except Exception:
    raise SystemExit(1)
