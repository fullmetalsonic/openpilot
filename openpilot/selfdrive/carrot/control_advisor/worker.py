"""Owned read-only session worker; stdout is bounded JSONL, stdin EOF stops it."""

import argparse
from contextlib import redirect_stdout
import json
import math
import os
import sys
import threading
import time

from .live import LiveEvent, LiveSession, NativeTransport, TERMINAL_STATES
from .recording import RecordingSession
from .consumer_collector import ConsumerCollector
from .worker_runtime import configure_worker_runtime


MAX_STATUS_BYTES = 256 * 1024


def _recording_identity_provider(*, environment=None, params=None, boot_id=None):
  """Recorder-thread CP reads only; consumers independently capture their used CP."""
  from .consumer_observer import _native_runtime, _read_boot_id
  from .vehicle_identity import describe_car_params, unavailable_identity
  environment = os.environ if environment is None else environment
  if params is None:
    if not _native_runtime():
      return None
    try:
      from openpilot.common.params import Params
      params = Params()
    except Exception:
      return lambda: unavailable_identity("car_params_backend_unavailable")
  from .runtime_config import NATIVE_DEFAULTS
  from .vehicle_binding import binding_root, load_bound_identity
  from pathlib import Path
  request_root = environment.get("CARROT_CONTROL_ADVISOR_REQUEST_ROOT")
  if request_root is None and _native_runtime():
    request_root = NATIVE_DEFAULTS["requests"]
  root = binding_root(request_root) if isinstance(request_root, str) and Path(request_root).is_absolute() else None
  boot = _read_boot_id() if boot_id is None else boot_id
  def identity():
    try:
      raw = params.get_many_raw(["CarParams"])["CarParams"]
      descriptor = describe_car_params(raw)
      return load_bound_identity(descriptor, root, boot) if root is not None else descriptor
    except Exception:
      return unavailable_identity("car_params_provider_unavailable")
  return identity


def _synthetic_tick(index):
  """Explicit demo input only: 600 logical seconds, accelerated wall clock."""
  logical = index * 0.05
  mono_ns = 1_000_000_000 + index * 50_000_000
  lateral_target = 0.4 * math.sin(logical / 8)
  longitudinal_target = 0.3 * math.sin(logical / 20)
  data = (
    ("carControl", {"enabled": True, "latActive": True, "longActive": True}),
    ("controlsState", {"activeLaneLine": False, "lateralControlState": {"torqueState": {
      "desiredLateralAccel": lateral_target, "actualLateralAccel": 0.38 * math.sin((logical - 0.2) / 8),
      "active": True, "saturated": False}}}),
    ("longitudinalPlan", {"aTarget": longitudinal_target, "shouldStop": False, "xState": 0}),
    ("carState", {"canValid": True, "gearShifter": "drive", "aEgo": 0.27 * math.sin((logical - 0.1) / 20),
      "vEgo": 15.0, "steeringPressed": 120 <= logical < 121,
      "gasPressed": 220 <= logical < 221, "brakePressed": 400 <= logical < 400.5}),
  )
  return [LiveEvent(service, mono_ns, True, values, mono_ns,
                    max(1, len(json.dumps(values, separators=(",", ":")).encode()))) for service, values in data]


def _watch_stdin(stop):
  try:
    # A buffered reader held by a daemon can fatally lock interpreter shutdown
    # when a natural session end occurs before the parent closes its pipe.
    descriptor = sys.stdin.fileno()
    while os.read(descriptor, 1):
      pass
  except (OSError, ValueError):
    pass
  stop.set()


def _emit(payload):
  encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
  if len(encoded) + 1 > MAX_STATUS_BYTES:
    payload = {**payload, "diagnostic": None, "history": [], "reason": "status_payload_limit",
               "diagnostic_invalidated": True,
               "metadata": {**payload["metadata"], "payload_details_omitted": True}}
    if payload["state"] not in TERMINAL_STATES:
      payload["state"] = "degraded"
    encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
  if len(encoded) + 1 > MAX_STATUS_BYTES:
    raise ValueError("bounded status envelope exceeded")
  sys.stdout.buffer.write(encoded + b"\n")
  sys.stdout.buffer.flush()


def run(*, mode, session_id, interval_seconds, parent_pid=None):
  runtime = configure_worker_runtime(mode)
  session = LiveSession(session_id, source_mode=mode, interval_seconds=interval_seconds)
  session.worker_runtime = runtime
  clock_start = time.monotonic_ns()
  if mode == "synthetic":
    source_clock = lambda: 1_000_000_000 + min(600_000_000_000, max(0, time.monotonic_ns() - clock_start) * 60)
    clock_metadata = {"domain": "accelerated_synthetic_monotonic", "scale": 60,
                      "source_zero_ns": 1_000_000_000, "host_zero_ns": clock_start}
  else:
    source_clock = time.monotonic_ns
    clock_metadata = {"domain": "native_monotonic", "scale": 1}
  recording = RecordingSession(session_id, source_mode=mode, source_clock_ns=source_clock,
                               clock_metadata=clock_metadata, analysis_interval_seconds=interval_seconds,
                               journal_format="segmented" if mode == "live" else "v1",
                               identity_provider=_recording_identity_provider() if mode == "live" else None).start()
  consumers = ConsumerCollector(source_mode=mode, clock_ns=source_clock)
  stop = threading.Event()
  threading.Thread(target=_watch_stdin, args=(stop,), daemon=True, name="advisor-parent-pipe").start()
  transport = None
  last_emitted = None

  def pump_recording():
    recording.submit_consumer_observations(consumers.poll())
    for notice in recording.poll_settings():
      session.observe_settings(notice)
    recording.submit_diagnostic(session.diagnostic)
    session.recording_status = recording.snapshot()
    session.consumer_observations = {**consumers.snapshot(), "persistence": session.recording_status["consumer_history"]}

  def finish_recording():
    pump_recording()
    consumers.close()
    session.recording_status = recording.close()
    session.consumer_observations = {**consumers.snapshot(), "persistence": session.recording_status["consumer_history"]}

  def emit(*, final=False):
    nonlocal last_emitted
    now = time.monotonic()
    if last_emitted is not None and now - last_emitted < 1.0:
      if not final:
        return
      time.sleep(max(0.0, 1.0 - (now - last_emitted)))
    _emit({**session.snapshot(), "automatic_analysis": recording.automatic_snapshot()})
    last_emitted = time.monotonic()

  try:
    if mode == "live":
      try:
        # Third-party native startup diagnostics belong on bounded stderr, not
        # the status protocol. A source failure never switches to synthetic.
        with redirect_stdout(sys.stderr):
          transport = NativeTransport()
      except Exception:
        session.state, session.reason = "unavailable", "native_messaging_unavailable"
        finish_recording()
        emit(final=True)
        return 0
    pump_recording()
    emit()
    next_tick = 0
    while session.state not in TERMINAL_STATES:
      pump_recording()
      if stop.is_set():
        session.finish("parent_pipe_closed")
        break
      if parent_pid is not None and os.name != "nt" and os.getppid() != parent_pid:
        session.finish("parent_process_changed")
        break
      if mode == "synthetic":
        logical_target = min(12_000, (source_clock() - 1_000_000_000) // 50_000_000)
        events = []
        for _ in range(64):
          if next_tick > logical_target:
            break
          events.extend(_synthetic_tick(next_tick))
          next_tick += 1
        session.ingest_batch(events)
        if next_tick > 12_000:
          session.finish("synthetic_logical_600_seconds_complete")
      else:
        before = transport.rejected_events
        events = transport.poll()
        if transport.rejected_events > before:
          session.dropped_events += transport.rejected_events - before
          session.invalidate("native_payload_rejected", overflow=True)
        session.ingest_batch(events)
        session.received_events = transport.received_events
        session.poll_budget_overruns = transport.budget_overruns
      session.tick()
      pump_recording()
      emit()
      stop.wait(0.01)
    finish_recording()
    emit(final=True)
    return 0
  except (BrokenPipeError, OSError):
    return 1
  except KeyboardInterrupt:
    session.finish("interrupted")
    finish_recording()
    emit(final=True)
    return 0
  except Exception:
    session.state, session.reason = "failed", "worker_processing_failed"
    finish_recording()
    try:
      emit(final=True)
    except (BrokenPipeError, OSError):
      pass
    return 1
  finally:
    consumers.close()
    recording.close()
    if transport is not None:
      transport.close()


def main():
  parser = argparse.ArgumentParser(description="Read-only FMS control diagnostic worker")
  parser.add_argument("--mode", choices=("live", "synthetic"), required=True)
  parser.add_argument("--session-id", required=True)
  parser.add_argument("--interval-seconds", type=int, choices=(180, 300), default=300)
  parser.add_argument("--parent-pid", type=int)
  args = parser.parse_args()
  return run(mode=args.mode, session_id=args.session_id, interval_seconds=args.interval_seconds, parent_pid=args.parent_pid)


if __name__ == "__main__":
  raise SystemExit(main())
