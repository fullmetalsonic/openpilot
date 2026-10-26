"""Two bounded I/O workers for settings observations and selected raw evidence.

The socket/analysis loop only exchanges bounded in-memory notices/commands.
Explicit mode-specific roots are required; this module never guesses device
paths, changes Params, starts a raw logger, or resumes an existing writer.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import stat
import threading
import time


ZERO_HASH = "0" * 64
ROOT_NAMES = ("SETTINGS_DIR", "LOG_ROOT", "EVIDENCE_ROOT")
ROOT_BYTE_LIMIT = 4 * 1024 * 1024 * 1024
ROOT_SESSION_LIMIT = 32
SESSION_RESERVATION_BYTES = (512 + 512 + 32) * 1024 * 1024


class _RootLease:
  """Exclusive advisory lease; the OS releases it on process exit/crash."""
  def __init__(self, root):
    path = root / ".control-advisor.lock"
    if _is_link(path):
      raise ValueError("recording_root_lock_link")
    self.fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
    self.acquired = False
    try:
      if not stat.S_ISREG(os.fstat(self.fd).st_mode):
        raise ValueError("recording_root_lock_not_regular")
      if os.fstat(self.fd).st_size == 0:
        os.write(self.fd, b"0")
      os.lseek(self.fd, 0, os.SEEK_SET)
      if os.name == "nt":
        import msvcrt
        msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
      else:
        import fcntl
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
      self.acquired = True
    except BaseException:
      os.close(self.fd)
      raise

  def close(self):
    if not self.acquired:
      return
    try:
      os.lseek(self.fd, 0, os.SEEK_SET)
      if os.name == "nt":
        import msvcrt
        msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
      else:
        import fcntl
        fcntl.flock(self.fd, fcntl.LOCK_UN)
    finally:
      self.acquired = False
      os.close(self.fd)


def _root_budget(root):
  """Count partials too; reject unknown layouts rather than deleting anything."""
  total = sessions = entries = 0
  stack = [(root, ())]
  while stack:
    directory, relative = stack.pop()
    with os.scandir(directory) as children:
      for child in children:
        entries += 1
        if entries > 10_000:
          raise ValueError("recording_root_entry_limit")
        path = Path(child.path)
        if _is_link(path):
          raise ValueError("recording_root_link")
        info = child.stat(follow_symlinks=False)
        parts = (*relative, child.name)
        if stat.S_ISDIR(info.st_mode):
          allowed = ((not relative and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", child.name))
                     or (len(relative) == 1 and child.name in ("diagnostics", "archive", "history"))
                     or (len(relative) == 2 and relative[1] == "archive" and re.fullmatch(r"[a-f0-9]{32}", child.name)))
          if not allowed:
            raise ValueError("recording_root_unknown_structure")
          sessions += not relative
          stack.append((path, parts))
        elif stat.S_ISREG(info.st_mode):
          allowed = ((not relative and child.name == ".control-advisor.lock" and info.st_size <= 1)
                     or (len(relative) == 1 and child.name in ("recording.json", "settings.jsonl", "history-checkpoint.json"))
                     or (len(relative) == 2 and relative[1] == "history" and (child.name == "catalog.jsonl" or re.fullmatch(r"segment-[0-9]{6}\.jsonl", child.name)))
                     or (len(relative) == 2 and relative[1] == "diagnostics"
                         and re.fullmatch(r"[1-9][0-9]*-[a-f0-9]{16}\.json", child.name))
                     or (len(relative) == 3 and relative[1] == "archive"
                         and child.name in ("archive.json", "archive.json.partial", "rlog", "rlog.zst", "rlog.bz2",
                                            "rlog.partial", "rlog.zst.partial", "rlog.bz2.partial")))
          if not allowed:
            raise ValueError("recording_root_unknown_structure")
          total += info.st_size
        else:
          raise ValueError("recording_root_not_regular")
  if sessions >= ROOT_SESSION_LIMIT or total + SESSION_RESERVATION_BYTES > ROOT_BYTE_LIMIT:
    raise ValueError("recording_root_budget_exceeded")
  return {"bytes": total, "sessions": sessions}


def _is_link(path):
  return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _roots(values):
  result = [Path(value) for value in values]
  if any(not path.is_absolute() for path in result):
    raise ValueError("recording_roots_not_absolute")
  for index, path in enumerate(result):
    # Only the explicit settings root's final link is allowed: native params/d
    # is deliberate, and SettingsRecorder pins/rechecks its resolved identity.
    checked = path.parents if index == 0 else (path, *path.parents)
    if any(_is_link(part) for part in checked):
      raise ValueError("recording_root_link")
    resolved = path.resolve()
    if any(_is_link(part) for part in (resolved, *resolved.parents)):
      raise ValueError("recording_root_link")
  resolved = [path.resolve() for path in result]
  for index, first in enumerate(resolved):
    for second in resolved[index + 1:]:
      if first == second or first in second.parents or second in first.parents:
        raise ValueError("recording_roots_overlap")
  return result


def _settings_public(status=None):
  status = status or {}
  observation = status.get("observation")
  if observation is not None:
    observation = {key: observation[key] for key in ("begin_ns", "end_ns", "previous_end_ns", "gap", "change_interval")}
  result = {"state": status.get("state", "unavailable") if status.get("state") != "starting" else "unavailable",
          "reason": status.get("reason"), "revision": status.get("revision", 0),
          "durable_revision": status.get("durable_revision", 0), "poll_sequence": status.get("poll_sequence", 0),
          "values": deepcopy(status.get("settings", {})), "changed_keys": list(status.get("changed_keys", [])),
          "observation": observation,
          "journal": deepcopy(status.get("journal", {"path": "settings.jsonl", "sequence": 0,
                              "sha256": ZERO_HASH, "bytes": 0, "durable": False})),
          "consumer_applied": False, "atomic_snapshot": False, "history_complete": False,
          "completeness": "polled_partial"}
  if "identity_version" in status or "vehicle_identity" in status:
    from .vehicle_identity import validate_identity
    if type(status.get("identity_version")) is not int or status["identity_version"] != 1:
      raise ValueError("invalid_recording_vehicle_identity")
    result.update(identity_version=1, vehicle_identity=validate_identity(status.get("vehicle_identity")))
  return result


def _window_assessment(ranges, window, route, tolerance_ns=250_000_000):
  """Only continuous carState ranges from consecutive segments may settle a job.

  The existing unvalidated host max-gap is used only for adjacent-file work
  completion. Actual gaps are retained, never interpolated into measurements.
  """
  result = {"complete": False, "actual_boundary_gaps": [], "missing_segments": [], "missing_segments_count": 0,
            "endpoint_start_covered": False, "endpoint_end_covered": False}
  if not ranges or not route or any(item["route"] != route for item in ranges):
    return result
  segments = sorted({item["segment"] for item in ranges})
  result["missing_segments_count"] = segments[-1] - segments[0] + 1 - len(segments)
  for first, second in zip(segments, segments[1:]):
    if len(result["missing_segments"]) < 128:
      result["missing_segments"].extend(range(first + 1, min(second, first + 1 + 128 - len(result["missing_segments"]))))
  ordered = sorted(ranges, key=lambda item: item["segment"])
  result["endpoint_start_covered"] = ordered[0]["start"] <= window[0] <= ordered[0]["end"]
  result["endpoint_end_covered"] = ordered[-1]["start"] <= window[1] <= ordered[-1]["end"]
  chronological = True
  for first, second in zip(ordered, ordered[1:]):
    if second["start"] < first["start"] or second["end"] < first["end"]:
      chronological = False
    if second["start"] > first["end"]:
      result["actual_boundary_gaps"].append({"start_ns": first["end"], "end_ns": second["start"]})
  result["complete"] = (chronological and not result["missing_segments_count"]
                        and result["endpoint_start_covered"] and result["endpoint_end_covered"]
                        and all(gap["end_ns"] - gap["start_ns"] <= tolerance_ns for gap in result["actual_boundary_gaps"]))
  return result


def _job_receipt(job, state):
  assessed = _window_assessment(job.get("ranges", []), job["window_ns"], job["route"])
  return {"diagnostic_revision": job["diagnostic_revision"], "settings_revision": job["settings_revision"],
          "state": state, "window_start_ns": job["window_ns"][0], "window_end_ns": job["window_ns"][1],
          "route_observation": deepcopy(job["route_observation"]), "settings_history_reference": deepcopy(job["journal_ref"]),
          **{key: value for key, value in assessed.items() if key != "complete"}, "gap_tolerance_ns": 250_000_000,
          "completion_scope": "archive_work_only_host_profile", "coverage_complete": False, "interpolation_applied": False}


def _automatic_initial(interval, *, state="disabled", reason="automatic_analysis_not_enabled"):
  return {"state": state, "reason": reason, "interval_seconds": interval, "result_revision": 0,
          "stale": False, "attempt": None, "last_success": None,
          "recommendations_enabled": False, "apply_enabled": False, "device_qualified": False}


class RecordingSession:
  def __init__(self, session_id, *, source_mode, environment=None, source_clock_ns=time.monotonic_ns,
               clock_metadata=None, poll_seconds=1.0, archive_poll_seconds=5.0, analysis_interval_seconds=300,
               analyzer_factory=None, identity_provider=None, journal_format="v1"):
    if journal_format not in ("v1", "segmented"):
      raise ValueError("invalid_recording_journal_format")
    self.journal_format = journal_format
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
      raise ValueError("invalid_recording_session")
    if source_mode not in ("live", "synthetic") or not 0 < poll_seconds <= 60 or not 0 < archive_poll_seconds <= 60:
      raise ValueError("invalid_recording_mode_or_period")
    environment = os.environ if environment is None else environment
    self._analysis_environment = dict(environment)
    self._analysis_goal = None
    if identity_provider is not None and not callable(identity_provider):
      raise ValueError("invalid_recording_identity_provider")
    self._identity_provider = identity_provider
    prefix = "CARROT_CONTROL_ADVISOR_" + ("DEMO_" if source_mode == "synthetic" else "")
    values = [environment.get(prefix + suffix, "") for suffix in ROOT_NAMES]
    configured = all(isinstance(value, str) and value.strip() for value in values)
    self.session_id, self.source_mode = session_id, source_mode
    self._root_values = values if configured else None
    self.source_clock_ns = source_clock_ns
    self.clock_metadata = clock_metadata or {"domain": "native_monotonic", "scale": 1}
    self.poll_seconds, self.archive_poll_seconds = poll_seconds, archive_poll_seconds
    if type(analysis_interval_seconds) is not int or analysis_interval_seconds not in (180, 300):
      raise ValueError("invalid_recording_analysis_interval")
    self.analysis_interval_seconds = analysis_interval_seconds
    self.job_lifetime_seconds = analysis_interval_seconds / (60 if source_mode == "synthetic" else 1)
    self._lock, self._stop, self._ready, self._archive_finished = threading.Lock(), threading.Event(), threading.Event(), threading.Event()
    self._settings_finished = threading.Event()
    self._root_lease = None
    self._commands, self._archive_jobs, self._notices = queue.Queue(maxsize=32), queue.Queue(maxsize=4), queue.Queue(maxsize=8)
    self._notice_gap = False
    self._threads, self._session_dir, self._paths = [], None, None
    self._journal_ready = False
    self._diagnostic_bytes = self._diagnostic_files = 0
    self._pending_jobs = 0
    self._pending_consumer_events = 0
    self._pending_pid_burst_events = self._pid_burst_rejections = 0
    self._analyzer_factory = analyzer_factory
    self._automatic_config = None
    self._automatic_public = _automatic_initial(analysis_interval_seconds)
    self._automatic_finished = threading.Event()
    self._automatic_finished.set()
    self._checkpoints = queue.Queue(maxsize=1)
    self._archive_available = False
    self._archive_identities = set()  # Bounded by the archive's 128-file quota.
    self._archive_watermark = ZERO_HASH
    self._last_checkpoint_sequence = None
    self._analysis_commands = queue.Queue(maxsize=32)
    self._analysis_observation = None
    from .analysis_control import initial_control
    self._analysis_public = initial_control()
    self._analysis_execution = None  # Not a device-mode acknowledgement yet.
    self._analysis_binding = None
    if environment.get(prefix + "AUTO_ENABLED") == "1":
      raw = [environment.get(prefix + name, "") for name in ("AUTO_PROFILE", "AUTO_OBJECTIVES", "AUTO_OUTPUT_ROOT")]
      if source_mode == "synthetic":
        self._automatic_public = _automatic_initial(analysis_interval_seconds, reason="synthetic_automatic_analysis_unavailable")
      elif not configured:
        self._automatic_public = _automatic_initial(analysis_interval_seconds, state="failed", reason="recording_roots_not_configured")
      elif any(not isinstance(value, str) or not value.strip() or len(value) > 4096 or not Path(value).is_absolute() for value in raw):
        self._automatic_public = _automatic_initial(analysis_interval_seconds, state="failed", reason="automatic_analysis_configuration_invalid")
      else:
        self._automatic_config = tuple(Path(value) for value in raw)
        self._automatic_public = _automatic_initial(analysis_interval_seconds, state="waiting", reason="waiting_for_recording")
    self._last_settings_notice = None
    self._latest_route_observation = None
    self._last_submitted_revision = None
    self._public = {
      "state": "starting" if configured else "disabled", "reason": None if configured else "roots_not_configured",
      "settings": _settings_public(),
      "consumer_history": {"state": "disabled", "recorded_events": 0, "dropped_events": 0},
      "preservation": {"state": "waiting" if configured else "disabled", "reason": None,
                       "verified_segments": 0, "verified_bytes": 0, "failed_segments": 0,
                       "pending_jobs": 0, "uncovered_windows": 0, "journal_committed": False,
                       "latest_diagnostic_revision": None},
    }

  def start(self):
    if self._root_values is None or self._threads:
      return self
    self._threads = [threading.Thread(target=self._settings_loop, daemon=True, name="advisor-settings-journal"),
                     threading.Thread(target=self._archive_loop, daemon=True, name="advisor-evidence-copy")]
    if self._automatic_config is not None:
      self._automatic_finished.clear()
      self._threads.append(threading.Thread(target=self._automatic_loop, daemon=True, name="advisor-automatic-analysis"))
    for thread in self._threads:
      thread.start()
    return self

  def snapshot(self):
    with self._lock:
      return deepcopy(self._public)

  def automatic_snapshot(self):
    with self._lock:
      return deepcopy(self._automatic_public)

  def analysis_snapshot(self):
    with self._lock:
      return deepcopy(self._analysis_public)

  def analysis_binding(self):
    with self._lock:
      return deepcopy(self._analysis_binding)

  def analysis_goal(self):
    with self._lock:
      return deepcopy(self._analysis_goal)

  def execution_snapshot(self):
    with self._lock:
      return deepcopy(self._analysis_execution)

  def request_executor(self, executor):
    from .execution import validate_executor
    validate_executor(executor)
    if self._automatic_config is None:
      raise ValueError("analysis_not_configured")
    try:
      self._analysis_commands.put_nowait({"kind": "executor", "executor": executor})
    except queue.Full as error:
      raise ValueError("analysis_command_queue_full") from error

  def observe_analysis(self, stamp, parking, nonpark_ns=None):
    if type(stamp) is not int or not 0 <= self.source_clock_ns() - stamp <= 2_000_000_000:
      stamp = None
    if type(nonpark_ns) is not int or not 0 <= self.source_clock_ns() - nonpark_ns <= 250_000_000:
      nonpark_ns = None
    with self._lock:
      self._analysis_observation = (stamp, deepcopy(self._latest_route_observation),
                                    deepcopy(self._public["settings"].get("vehicle_identity", {})), deepcopy(parking), nonpark_ns)

  def request_analysis(self, request_id, goal_id=None):
    if self._automatic_config is None or self._stop.is_set():
      raise ValueError("analysis_not_configured")
    try:
      self._analysis_commands.put_nowait({"kind": "analyze", "request_id": request_id, "goal_id": goal_id})
    except queue.Full as error:
      raise ValueError("analysis_command_queue_full") from error

  def successful_apply(self, receipt, trip_binding):
    try:
      self._analysis_commands.put_nowait({"kind": "apply", "receipt": deepcopy(receipt), "trip_binding": deepcopy(trip_binding)})
    except queue.Full as error:
      raise ValueError("analysis_command_queue_full") from error

  def _publish_checkpoint(self, recorder):
    # Only the journal writer calls this, after durable archive acceptance.
    if self._automatic_config is None or not self._archive_available or self._stop.is_set():
      return
    try:
      token = recorder.checkpoint()
    except (ValueError, OSError):
      return
    if token["sequence"] == self._last_checkpoint_sequence:
      return
    try:
      self._checkpoints.get_nowait()
    except queue.Empty:
      pass
    self._checkpoints.put_nowait({"checkpoint": token, "archive_watermark": self._archive_watermark})
    self._last_checkpoint_sequence = token["sequence"]

  def _automatic_loop(self):
    analyzer = None
    control = None
    reaped = True
    try:
      while not self._ready.wait(0.05):
        if self._stop.is_set():
          return
      if self._stop.is_set():
        return
      if not self._journal_ready:
        raise ValueError("automatic_recording_unavailable")
      factory = self._analyzer_factory
      if factory is None:
        from .periodic import PeriodicAnalyzer
        factory = PeriodicAnalyzer
      profile, objectives, output = self._automatic_config
      resolved_output = output.resolve()
      for root in self._paths:
        source = root.resolve()
        if resolved_output == source or resolved_output in source.parents or source in resolved_output.parents:
          with self._lock:
            self._automatic_public.update(state="failed", reason="automatic_output_root_overlap", stale=True)
          return
      analyzer = factory(self._session_dir, profile, objectives, output,
                         interval_seconds=self._automatic_public["interval_seconds"], source_mode=self.source_mode)
      from .execution import preference
      if hasattr(analyzer, "set_executor"):
        analyzer.set_executor(preference(output))
      from .analysis_control import AnalysisControl
      from .consumer_observer import _read_boot_id
      from .evidence import hash_file
      from .periodic import _atomic
      control = AnalysisControl(self.session_id, _read_boot_id(), profile_sha256=hash_file(profile, 4 * 1024 * 1024)[0],
                                objectives_sha256=hash_file(objectives, 64 * 1024)[0])
      goal_environment = dict(self._analysis_environment)
      if goal_environment.get("CARROT_CONTROL_ADVISOR_BUILTIN_GOALS") == "1":
        from .runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
        from .goals import PROFILE_ENV, OBJECTIVES_ENV, goal_configuration
        goal_environment.update({PROFILE_ENV: BUILTIN_PROFILE_PATH, OBJECTIVES_ENV: BUILTIN_OBJECTIVES_PATH})
        selected_goal = goal_environment.get("CARROT_CONTROL_ADVISOR_SELECTED_GOAL")
        if selected_goal:
          control.goal_id = selected_goal
          with self._lock:
            self._analysis_goal = goal_configuration(selected_goal, goal_environment)["selection"]
      durable_revision = -1
      while not self._stop.is_set():
        with self._lock:
          observation = deepcopy(self._analysis_observation)
        if observation is not None:
          previous_trip = deepcopy(control.trip)
          control.observe(*observation)
          if previous_trip is not None and previous_trip != control.trip:
            analyzer.invalidate_analysis("analysis_trip_changed")
            control.active = None
        for _ in range(32):
          try:
            command = self._analysis_commands.get_nowait()
          except queue.Empty:
            break
          try:
            if command["kind"] == "executor":
              if not hasattr(analyzer, "set_executor"):
                raise ValueError("analysis_executor_unavailable")
              previous_executor = analyzer.execution_snapshot()["executor"]
              preference(output, command["executor"])
              analyzer.set_executor(command["executor"])
              if previous_executor != command["executor"]:
                control.change_goal(control.profile_hash, control.objective_hash, control.goal_id)
                control.public["reason_codes"] = ["analysis_executor_changed"]
            elif command["kind"] == "analyze":
              goal_id = command.get("goal_id")
              if goal_id is not None:
                from .goals import goal_configuration
                if self._analysis_environment.get("CARROT_CONTROL_ADVISOR_BUILTIN_GOALS") != "1":
                  raise ValueError("analysis_goal_requires_builtin_configuration")
                goal = goal_configuration(goal_id, goal_environment)
                new_profile, new_objectives = Path(goal["profile_path"]), Path(goal["objectives_path"])
                new_hashes = (hash_file(new_profile, 4 * 1024 * 1024)[0], hash_file(new_objectives, 64 * 1024)[0])
                if new_hashes != (control.profile_hash, control.objective_hash):
                  analyzer.invalidate_analysis("analysis_goal_changed")
                  analyzer.profile, analyzer.objectives = new_profile, new_objectives
                  control.change_goal(*new_hashes, goal_id)
                  profile, objectives = new_profile, new_objectives
                with self._lock:
                  self._analysis_goal = goal["selection"]
              control.request(command["request_id"])
            else:
              if control.apply_receipt(command["receipt"], command["trip_binding"], observed_now_ns=self.source_clock_ns()):
                analyzer.invalidate_analysis("analysis_cut_changed")
          except (ValueError, OSError) as error:
            reason = str(error)
            control.public.update(state="failed", request_id=command.get("request_id"), trigger="manual",
              reason_codes=[reason if re.fullmatch(r"[a-z0-9_]{1,128}", reason) else "analysis_request_unavailable"])
        intent = control.take()
        # An explicit intent is durable before the model subprocess can start.
        if durable_revision != control.revision:
          _atomic(self._session_dir / "analysis-control.json", control.durable(), durable=True)
          durable_revision = control.revision
        if intent is not None:
          analyzer.request_analysis(intent)
        with self._lock:
          settings_context = deepcopy(self._public["settings"])
        if control.binding_valid and hasattr(analyzer, "update_current_context"):
          observation_end = (settings_context.get("observation") or {}).get("end_ns")
          if type(observation_end) is int and 0 <= self.source_clock_ns() - observation_end <= 2_000_000_000:
            analyzer.update_current_context({"session_id": self.session_id,
              "analysis_binding": {"trip_binding": deepcopy(control.trip), "cut_revision": control.cut_revision},
              "settings_raw": settings_context["values"], "vehicle_identity": settings_context.get("vehicle_identity")})
          else:
            analyzer.update_current_context(None)
        elif hasattr(analyzer, "update_current_context"):
          analyzer.update_current_context(None)
        try:
          offered = self._checkpoints.get_nowait()
        except queue.Empty:
          offered = None
        if offered is not None:
          analyzer.offer(offered["checkpoint"], archive_watermark=offered["archive_watermark"])
        analyzer.tick()
        control.update(analyzer.snapshot())
        with self._lock:
          self._automatic_public = deepcopy(analyzer.snapshot())
          self._analysis_public = control.snapshot()
          if hasattr(analyzer, "execution_snapshot"):
            self._analysis_execution = analyzer.execution_snapshot()
          self._analysis_binding = None if not control.binding_valid else {"trip_binding": deepcopy(control.trip), "cut_revision": control.cut_revision}
        self._stop.wait(0.1)
    except Exception:
      with self._lock:
        self._automatic_public.update(state="failed", reason="automatic_analysis_failed", stale=True)
    finally:
      if analyzer is not None:
        try:
          analyzer.close(timeout=2.0)
          with self._lock:
            self._automatic_public = deepcopy(analyzer.snapshot())
        except Exception:
          # Keep the quota lease until this worker exits if child cleanup failed.
          reaped = False
          with self._lock:
            self._automatic_public.update(state="failed", reason="automatic_analysis_shutdown_failed", stale=True)
      elif self._stop.is_set():
        with self._lock:
          self._automatic_public.update(state="stopped", reason="automatic_analysis_stopped")
      if control is not None:
        control.public["can_analyze"] = False
        if control.public["state"] in {"queued", "running", "waiting_for_tail"}:
          control.public.update(state="incomplete", reason_codes=["analysis_interrupted"])
        with self._lock:
          self._analysis_public = control.snapshot()
        try:
          _atomic(self._session_dir / "analysis-control.json", control.durable(), durable=True)
        except (OSError, ValueError):
          with self._lock:
            self._analysis_public.update(state="failed", reason_codes=["analysis_interruption_not_durable"])
      if reaped:
        self._automatic_finished.set()
      self._release_lease_if_finished()

  def poll_settings(self):
    result = []
    while len(result) < 8:
      try:
        result.append(self._notices.get_nowait())
      except queue.Empty:
        break
    with self._lock:
      gap, self._notice_gap = self._notice_gap, False
      latest = deepcopy(self._public["settings"])
      route = deepcopy(self._latest_route_observation)
    if gap:
      result = [{**latest, "notification_gap": True, "route_observation": route}]
    return result

  def submit_diagnostic(self, diagnostic):
    if self._root_values is None or diagnostic is None:
      return False
    revision = diagnostic.get("revision")
    if type(revision) is not int or revision <= 0 or revision == self._last_submitted_revision:
      return False
    required = ("window_start_ns", "window_end_ns", "profile_id", "invalidated", "partial_window")
    if any(key not in diagnostic for key in required):
      return False
    record = {key: diagnostic[key] for key in required}
    encoded = json.dumps(diagnostic, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 256 * 1024:
      self._preservation(state="partial", reason="diagnostic_file_limit", journal_committed=False)
      return False
    record.update(revision=revision, sha256=hashlib.sha256(encoded).hexdigest())
    command = {"kind": "diagnostic", "record": record, "encoded": encoded,
               "route_observation": deepcopy(diagnostic.get("route_observation")),
               "settings_revision": diagnostic.get("observed_settings_revision")}
    try:
      self._commands.put_nowait(command)
      self._last_submitted_revision = revision
      return True
    except queue.Full:
      self._preservation(state="partial", reason="recording_command_queue_full", journal_committed=False)
      return False

  def submit_consumer_observations(self, events):
    """Queue already validated observations; never infer an applied revision."""
    if self._root_values is None:
      return 0
    accepted = 0
    for event in events:
      with self._lock:
        if event.get("event_kind") == "pid_burst_chunk":
          if self._stop.is_set() or self._public["state"] == "failed":
            self._pid_burst_rejections += 1
            continue
          try:
            self._commands.put_nowait({"kind": "pid_burst", "event": deepcopy(event)})
          except queue.Full:
            self._pid_burst_rejections += 1
          else:
            self._pending_pid_burst_events += 1
            accepted += 1
          continue
        history = self._public["consumer_history"]
        if self._stop.is_set() or self._public["state"] == "failed":
          history.update(state="partial", dropped_events=history["dropped_events"] + 1)
          continue
        try:
          self._commands.put_nowait({"kind": "consumer", "event": deepcopy(event)})
        except queue.Full:
          history.update(state="partial", dropped_events=history["dropped_events"] + 1)
        else:
          self._pending_consumer_events += 1
          if history["state"] == "disabled":
            history["state"] = "waiting"
          accepted += 1
    return accepted

  def _preservation(self, **changes):
    with self._lock:
      self._public["preservation"].update(changes)
      if self._public["state"] not in ("disabled", "failed", "stopped"):
        self._public["state"] = "partial" if changes.get("state") in ("partial", "failed") else "recording"

  def _publish_settings(self, status):
    public = _settings_public(status)
    token = (public["revision"], public["state"], public["journal"]["durable"])
    with self._lock:
      self._public["settings"] = public
      self._latest_route_observation = deepcopy(status.get("route_observation"))
      if self._public["state"] == "starting":
        self._public["state"] = "recording" if public["state"] == "observed" else "partial"
      if public["state"] in ("failed", "unavailable") and self._public["state"] not in ("failed", "stopped"):
        self._public["state"] = "partial"
    if token != self._last_settings_notice:
      self._last_settings_notice = token
      try:
        self._notices.put_nowait({**deepcopy(public), "route_observation": deepcopy(status.get("route_observation"))})
      except queue.Full:
        with self._lock:
          self._notice_gap = True

  def _settings_loop(self):
    recorder = lease = None
    try:
      from .settings_history import SettingsRecorder
      from .evidence import _sync_directory
      self._paths = _roots(self._root_values)
      settings_dir, _, evidence_root = self._paths
      evidence_root.mkdir(parents=True, exist_ok=True)
      lease = _RootLease(evidence_root)
      with self._lock:
        self._root_lease = lease
      _root_budget(evidence_root)
      self._session_dir = evidence_root / self.session_id
      self._session_dir.mkdir(exist_ok=False)
      (self._session_dir / "diagnostics").mkdir()
      metadata = {"schema_version": 1, "session_id": self.session_id, "source_mode": self.source_mode,
                  "clock": self.clock_metadata, "settings_history_complete": False, "consumer_applied": False,
                  "recommendations_enabled": False, "apply_enabled": False,
                  "limits_device_qualified": False, "root_byte_limit": ROOT_BYTE_LIMIT,
                  "root_session_limit": ROOT_SESSION_LIMIT, "session_reservation_bytes": SESSION_RESERVATION_BYTES}
      with (self._session_dir / "recording.json").open("xb") as stream:
        stream.write(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
      _sync_directory(self._session_dir)
      scale = 60 if self.source_mode == "synthetic" else 1
      recorder = SettingsRecorder(settings_dir, self._session_dir, self.session_id,
                                  clock_ns=self.source_clock_ns, max_gap_ns=2_000_000_000 * scale,
                                  journal_format=self.journal_format,
                                  cache_window_ns=(2 * self.analysis_interval_seconds * 1_000_000_000 + 2_100_000_000) * scale,
                                  **({"identity_provider": self._identity_provider} if self._identity_provider is not None else {}))
      self._publish_settings(recorder.sample())
      self._journal_ready = True
      self._ready.set()
      next_poll = time.monotonic() + self.poll_seconds
      while not self._stop.is_set() or not self._commands.empty() or not self._archive_finished.is_set():
        if not self._stop.is_set() and time.monotonic() >= next_poll:
          status = recorder.sample()
          self._publish_settings(status)
          self._publish_checkpoint(recorder)
          next_poll = time.monotonic() + self.poll_seconds
        try:
          command = self._commands.get(timeout=0.05)
        except queue.Empty:
          continue
        if command["kind"] == "pid_burst":
          committed = False
          try:
            committed = recorder.append_pid_burst_chunk(command["event"])
            self._publish_checkpoint(recorder)
          except (ValueError, OSError):
            pass
          with self._lock:
            self._pending_pid_burst_events -= 1
            if not committed:
              self._pid_burst_rejections += 1
          continue
        if command["kind"] == "consumer":
          committed = False
          try:
            committed = recorder.append_consumer_observation(command["event"])
            status = recorder.snapshot()
            self._publish_settings(status)
            self._publish_checkpoint(recorder)
          except (ValueError, OSError):
            pass
          with self._lock:
            history = self._public["consumer_history"]
            self._pending_consumer_events -= 1
            history["recorded_events" if committed else "dropped_events"] += 1
            history["state"] = "partial" if history["dropped_events"] else "recorded"
          continue
        if command["kind"] == "job_status":
          try:
            status = recorder.append_job_status(command["record"])
            self._preservation(journal_committed=status["journal"]["durable"] and status["state"] != "failed")
            self._publish_settings(status)
            self._publish_checkpoint(recorder)
          except (ValueError, OSError):
            self._preservation(state="partial", reason="job_status_journal_rejected", journal_committed=False)
          continue
        if command["kind"] == "archive":
          try:
            summary = {key: value for key, value in command["record"].items()
                       if key not in {"source_relative", "decompressed_bytes"}}
            status = recorder.append_archive(summary)
            committed = status["journal"]["durable"] and status["state"] != "failed"
            self._preservation(journal_committed=committed,
                               **({} if committed else {"state": "partial", "reason": "archive_journal_uncommitted"}))
            self._publish_settings(status)
            if committed:
              self._archive_available = True
              identity = tuple(command["record"][key] for key in ("route", "segment", "sha256"))
              if identity not in self._archive_identities:
                encoded = json.dumps(identity, separators=(",", ":")).encode()
                self._archive_watermark = hashlib.sha256(self._archive_watermark.encode("ascii") + encoded).hexdigest()
                self._archive_identities.add(identity)
              self._publish_checkpoint(recorder)
          except (ValueError, OSError):
            self._preservation(state="partial", reason="archive_journal_rejected", journal_committed=False)
          continue
        record = command["record"]
        try:
          if type(command["settings_revision"]) is not int or command["settings_revision"] <= 0:
            raise ValueError("diagnostic_has_no_observed_settings_revision")
          encoded = command["encoded"]
          if self._diagnostic_files >= 128 or self._diagnostic_bytes + len(encoded) > 8 * 1024 * 1024:
            raise ValueError("diagnostic_storage_budget")
          path = self._session_dir / "diagnostics" / f"{record['revision']}-{record['sha256'][:16]}.json"
          with path.open("xb") as stream:
            if stream.write(encoded) != len(encoded):
              raise OSError("diagnostic_short_write")
            stream.flush()
            os.fsync(stream.fileno())
          self._diagnostic_files += 1
          self._diagnostic_bytes += len(encoded)
          _sync_directory(path.parent)
          status = recorder.append_diagnostic(record, settings_revision=command["settings_revision"],
                                              route_observation=command["route_observation"])
          self._publish_settings(status)
          self._publish_checkpoint(recorder)
          journal = deepcopy(status["journal"])
          if not journal["durable"] or status["state"] == "failed" or command["settings_revision"] is None:
            raise ValueError("diagnostic_not_bound")
          self._preservation(latest_diagnostic_revision=record["revision"], journal_committed=True)
          if record["invalidated"]:
            self._preservation(state="partial", reason="diagnostic_invalidated")
            continue
          job = {"window_ns": (record["window_start_ns"], record["window_end_ns"]),
                 "diagnostic_revision": record["revision"], "settings_revision": command["settings_revision"],
                 "journal_ref": journal, "route_observation": recorder.revision_metadata(command["settings_revision"])["route_observation"],
                 "deadline": time.monotonic() + self.job_lifetime_seconds}
          route = job["route_observation"]
          job["route"] = route["route"] if route["state"] == "observed" else None
          with self._lock:
            self._public["preservation"]["uncovered_windows"] += 1
          if job["route"] is None:
            recorder.append_job_status(_job_receipt(job, "incomplete"))
            self._preservation(state="pending", reason="route_association_unknown")
            continue
          recorder.pin_diagnostic(journal, command["settings_revision"])
          with self._lock:
            if self._pending_jobs >= 4:
              raise queue.Full
            self._pending_jobs += 1
            self._public["preservation"]["pending_jobs"] = self._pending_jobs
          try:
            self._archive_jobs.put_nowait(job)
          except queue.Full:
            with self._lock:
              self._pending_jobs -= 1
              self._public["preservation"]["pending_jobs"] = self._pending_jobs
            raise
          self._preservation(state="pending", reason="waiting_for_closed_overlap")
        except (ValueError, OSError):
          self._preservation(state="partial", reason="diagnostic_journal_uncommitted", journal_committed=False)
        except queue.Full:
          try:
            status = recorder.append_job_status(_job_receipt(job, "incomplete"))
            self._publish_settings(status)
            self._preservation(journal_committed=status["journal"]["durable"] and status["state"] != "failed")
          except (ValueError, OSError):
            self._preservation(journal_committed=False)
          self._preservation(state="partial", reason="preservation_queue_full")
    except Exception:
      self._stop.set()
      with self._lock:
        self._public.update(state="failed", reason="recording_initialization_failed")
      self._preservation(state="failed", reason="recording_initialization_failed")
      status = recorder.snapshot() if recorder is not None else {}
      self._publish_settings({**status, "state": "failed", "reason": "recording_io_failed"})
    finally:
      self._ready.set()
      if recorder is not None:
        try:
          self._publish_settings(recorder.close())
        except (OSError, ValueError):
          pass
      self._settings_finished.set()
      self._release_lease_if_finished()

  def _archive_loop(self):
    archive = None
    pending = []
    completed_sources = {}
    failed_sources = set()
    try:
      while not self._ready.wait(0.1):
        if self._stop.is_set():
          return
      if not self._journal_ready:
        return
      from .archive import SegmentArchive
      archive = SegmentArchive(self._paths[1], self._session_dir / "archive", session_id=self.session_id,
                               source_mode=self.source_mode, byte_limit=512 * 1024 * 1024, max_files=128,
                               free_reserve_bytes=256 * 1024 * 1024)
      while not self._stop.is_set():
        try:
          job = self._archive_jobs.get(timeout=0.1 if pending else 0.2)
          pending.append({**job, "ranges": [], "attempted": set(), "failures": {}})
        except queue.Empty:
          pass
        now = time.monotonic()
        for job in list(pending):
          if now >= job["deadline"]:
            self._settle_job(job, "timeout")
            pending.remove(job)
            continue
          if now < job.get("next_scan", 0):
            continue
          job["next_scan"] = now + self.archive_poll_seconds
          route = job["route"]
          self._preservation(state="copying", reason="checking_closed_overlap")
          candidates = archive.discover_candidates(max_entries=8, route=route)
          for candidate in candidates:
            if self._stop.is_set():
              break
            source = candidate["source_relative"]
            if source in job["attempted"]:
              continue
            copying = False
            try:
              inspected = archive.inspect_candidate(source, window_ns=job["window_ns"], cancelled=self._stop.is_set)
              if source in completed_sources:
                if completed_sources[source]["sha256"] != inspected["sha256"]:
                  raise ValueError("source_changed_after_preservation")
                job["attempted"].add(source)
                job["ranges"].append({"start": inspected["start_mono_ns"], "end": inspected["end_mono_ns"],
                                      "route": inspected["route"], "segment": inspected["segment"]})
                continue
              copying = True
              record = archive.preserve(source, window_ns=job["window_ns"], diagnostic_revision=job["diagnostic_revision"],
                                        settings_revision=job["settings_revision"], journal_ref=job["journal_ref"],
                                        cancelled=self._stop.is_set)
              if record["state"] != "complete":
                raise ValueError("archive_incomplete")
              completed_sources[source] = {"sha256": record["sha256"]}
              job["attempted"].add(source)
              job["ranges"].append({"start": record["start_mono_ns"], "end": record["end_mono_ns"],
                                    "route": record["route"], "segment": record["segment"]})
              with self._lock:
                self._public["preservation"]["verified_segments"] += 1
                self._public["preservation"]["verified_bytes"] += record["bytes"]
              try:
                self._commands.put_nowait({"kind": "archive", "record": record})
              except queue.Full:
                self._preservation(state="partial", reason="archive_completion_queue_full", journal_committed=False)
              else:
                self._preservation(state="verified", reason="individual_segment_verified", journal_committed=False)
            except InterruptedError:
              break
            except (ValueError, OSError, RuntimeError) as exc:
              # Open/nonoverlapping/changing sources remain pending. No source is
              # copied on a range mismatch and no failure becomes complete.
              self._preservation(state="partial", reason="closed_overlap_not_verified")
              if str(exc) == "archive_window_not_covered":
                job["attempted"].add(source)
              elif copying:
                job["failures"][source] = job["failures"].get(source, 0) + 1
                if source not in failed_sources:
                  failed_sources.add(source)
                  with self._lock:
                    self._public["preservation"]["failed_segments"] += 1
                if job["failures"][source] >= 3:
                  job["attempted"].add(source)
          if len(job["attempted"]) >= 200:
            self._preservation(state="partial", reason="archive_candidate_limit")
            pending.remove(job)
            self._settle_job(job, "incomplete")
            continue
          if _window_assessment(job["ranges"], job["window_ns"], route)["complete"]:
            pending.remove(job)
            self._settle_job(job, "complete")
          elif not self._stop.is_set():
            self._preservation(state="pending", reason="waiting_for_closed_overlap")
    except Exception:
      self._preservation(state="failed", reason="archive_initialization_or_scan_failed")
    finally:
      try:
        for job in pending:
          self._settle_job(job, "cancelled")
        while True:
          try:
            job = self._archive_jobs.get_nowait()
          except queue.Empty:
            break
          self._settle_job(job, "cancelled")
      finally:
        try:
          if archive is not None:
            archive.close()
        finally:
          self._archive_finished.set()
          self._release_lease_if_finished()

  def _release_lease_if_finished(self):
    # A failed settings writer must not release the quota lease while its
    # archive writer can still copy. On forced process exit the OS releases it.
    with self._lock:
      if (self._settings_finished.is_set() and self._archive_finished.is_set() and self._automatic_finished.is_set()
          and self._root_lease is not None):
        self._root_lease.close()
        self._root_lease = None

  def _settle_job(self, job, state):
    try:
      self._commands.put_nowait({"kind": "job_status", "record": _job_receipt(job, state)})
    except queue.Full:
      self._preservation(state="partial", reason="job_status_queue_full", journal_committed=False)
    with self._lock:
      self._pending_jobs = max(0, self._pending_jobs - 1)
      self._public["preservation"]["pending_jobs"] = self._pending_jobs
    if state != "complete":
      self._preservation(state="partial", reason="preservation_" + state)

  def close(self, *, timeout=1.0):
    if self._root_values is None:
      return self.snapshot()
    self._stop.set()
    deadline = time.monotonic() + max(0.0, timeout)
    for thread in self._threads:
      thread.join(timeout=max(0.0, deadline - time.monotonic()))
    unfinished = any(thread.is_alive() for thread in self._threads)
    with self._lock:
      if self._public["state"] != "failed":
        self._public["state"] = "partial" if unfinished else "stopped"
        self._public["reason"] = "recording_shutdown_incomplete" if unfinished else "recording_stopped"
      if self._public["preservation"]["state"] not in ("failed", "verified", "disabled"):
        self._public["preservation"]["state"] = "partial" if (
          unfinished or self._pending_jobs or self._public["preservation"]["uncovered_windows"]) else "stopped"
        if unfinished:
          self._public["preservation"]["reason"] = "recording_shutdown_incomplete"
      if self._pending_consumer_events:
        self._public["consumer_history"]["state"] = "partial"
    return self.snapshot()
