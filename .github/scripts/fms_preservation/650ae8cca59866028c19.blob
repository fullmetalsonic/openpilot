import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.session import MAX_LINE_BYTES, SessionConflict, SessionController, _recording_status


class RecordingProtocolTests(unittest.TestCase):
  def test_actual_recorder_initial_state_has_no_diagnostic_revision_yet(self):
    from openpilot.selfdrive.carrot.control_advisor.recording import RecordingSession
    for environment in ({}, {"CARROT_CONTROL_ADVISOR_DEMO_SETTINGS_DIR": "unused"}):
      recorder = RecordingSession("protocol", source_mode="synthetic", environment=environment)
      actual = _recording_status(recorder.snapshot())
      self.assertIsNone(actual["preservation"]["latest_diagnostic_revision"])

  def fixture(self):
    return {"state": "recording", "reason": None,
            "settings": {"state": "observed", "reason": None, "values": {"LateralTorqueAccelFactor": 2800},
                         "changed_keys": ["LateralTorqueAccelFactor"], "revision": 2, "durable_revision": 2,
                         "poll_sequence": 2, "history_complete": False, "consumer_applied": False,
                         "atomic_snapshot": False, "completeness": "polled_partial",
                         "observation": {"begin_ns": 20, "end_ns": 30, "previous_end_ns": 10, "gap": False,
                                         "change_interval": {"after_ns": 10, "before_ns": 30}},
                         "journal": {"path": "settings.jsonl", "sequence": 3, "bytes": 200,
                                     "sha256": "a" * 64, "durable": True}},
            "preservation": {"state": "verified", "reason": None, "verified_segments": 1,
                             "verified_bytes": 100, "failed_segments": 0, "pending_jobs": 0,
                             "uncovered_windows": 1, "journal_committed": True, "latest_diagnostic_revision": 1}}

  def test_public_snapshot_preserves_uncertainty_without_raw_values_or_absolute_paths(self):
    packet = self.fixture()
    packet["settings"]["private_path"] = "C:/private/store"
    actual = _recording_status(packet)
    self.assertNotIn("private_path", actual["settings"])
    self.assertEqual({"after_ns": 10, "before_ns": 30}, actual["settings"]["observation"]["change_interval"])
    packet["settings"]["values"]["LateralTorqueAccelFactor"] = 3100
    self.assertEqual(2800, actual["settings"]["values"]["LateralTorqueAccelFactor"])
    self.assertEqual(1, actual["preservation"]["uncovered_windows"])

  def test_unknown_setting_keys_and_success_claims_are_rejected(self):
    mutations = [lambda p: p["settings"].update(consumer_applied=True),
                 lambda p: p["settings"].update(history_complete=True),
                 lambda p: p["settings"]["values"].update(ApiToken=123),
                 lambda p: p["settings"]["journal"].update(path="../elsewhere"),
                 lambda p: p["preservation"].update(verified_segments=-1),
                 lambda p: p["settings"].update(changed_keys=[{}]),
                 lambda p: p["settings"]["observation"].update(change_interval={"after_ns": 40, "before_ns": 30})]
    for mutate in mutations:
      packet = deepcopy(self.fixture())
      mutate(packet)
      with self.assertRaises(ValueError):
        _recording_status(packet)


class FakeInput:
  def __init__(self, process, close_exits):
    self.process, self.close_exits = process, close_exits
    self.closed = asyncio.Event()
    self.commands = []

  def write(self, data):
    self.commands.append(json.loads(data))

  async def drain(self):
    pass

  def close(self):
    self.closed.set()
    if self.close_exits:
      self.process.finish(0)


class FakeProcess:
  def __init__(self, *, close_exits=True):
    self.stdout = asyncio.StreamReader(limit=MAX_LINE_BYTES + 1)
    self.stderr = asyncio.StreamReader()
    self.stdin = FakeInput(self, close_exits)
    self.returncode = None
    self.result = asyncio.get_running_loop().create_future()
    self.pid = 12345

  def finish(self, code):
    if self.returncode is None:
      self.returncode = code
      self.stdout.feed_eof()
      self.stderr.feed_eof()
      self.result.set_result(code)

  async def wait(self):
    return await asyncio.shield(self.result)

  def emit(self, session, **changes):
    payload = {"schema_version": 1, "session_id": session["session_id"], "source_mode": session["source_mode"],
               "state": "running", "received_events": 1, "observations": 2, "history": [],
               "diagnostic": None, "apply_enabled": False, "recommendations_enabled": False, **changes}
    self.stdout.feed_data(json.dumps(payload).encode() + b"\n")


class SessionTests(unittest.IsolatedAsyncioTestCase):
  async def test_explicit_analysis_uses_same_owned_pipe_and_idempotent_ack(self):
    from openpilot.selfdrive.carrot.control_advisor.analysis_control import initial_control
    controller = await self.make_controller()
    started = await controller.start(mode="live", interval_seconds=300, request_id="record")
    control = {**initial_control(), "can_analyze": True, "reason_codes": []}
    self.processes[0].emit(started, analysis_control=control)
    await self.state(controller, "running")
    ack = await controller.analyze(session_id=started["session_id"], request_id="manual")
    self.assertEqual(ack["analysis_control"]["state"], "queued")
    await controller.analyze(session_id=started["session_id"], request_id="manual")
    self.assertEqual(self.processes[0].stdin.commands, [{"kind": "analyze", "session_id": started["session_id"], "request_id": "manual"}])
    with self.assertRaises(SessionConflict):
      await controller.analyze(session_id="different", request_id="manual")

  async def test_unready_analysis_and_malformed_worker_control_fail_closed(self):
    controller = await self.make_controller()
    started = await controller.start(mode="live", interval_seconds=300, request_id="record")
    with self.assertRaises(SessionConflict):
      await controller.analyze(session_id=started["session_id"], request_id="manual")
    self.processes[0].emit(started, analysis_control={"state": "ready"})
    failed = await self.state(controller, "failed")
    self.assertEqual(failed["reason"], "invalid_worker_output")

  async def test_goal_switch_command_keeps_recording_process_and_same_session(self):
    from openpilot.selfdrive.carrot.control_advisor.analysis_control import initial_control
    from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
    controller = await self.make_controller(live_environment={"CARROT_CONTROL_ADVISOR_AUTO_PROFILE": BUILTIN_PROFILE_PATH,
      "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES": BUILTIN_OBJECTIVES_PATH})
    started = await controller.start(mode="live", interval_seconds=300, request_id="record", goal_id="tracking")
    process = self.processes[0]
    process.emit(started, analysis_control={**initial_control(), "can_analyze": True, "reason_codes": []})
    await self.state(controller, "running")
    switched = await controller.analyze(session_id=started["session_id"], request_id="new-goal", goal_id="fms_accel_defaults_v1")
    self.assertEqual(switched["session_id"], started["session_id"])
    self.assertIs(controller._process, process)
    self.assertEqual(len(self.spawned), 1)
    self.assertEqual(process.stdin.commands[-1]["goal_id"], "fms_accel_defaults_v1")
    self.assertEqual(self.spawned[0][1]["env"]["CARROT_CONTROL_ADVISOR_BUILTIN_GOALS"], "1")
    with self.assertRaises(SessionConflict):
      await controller.analyze(session_id=started["session_id"], request_id="new-goal", goal_id="tracking")
    with self.assertRaises(ValueError):
      await controller.analyze(session_id=started["session_id"], request_id="path", goal_id="../../other")

  async def test_automatic_analysis_partial_summary_is_validated_and_propagated(self):
    from openpilot.selfdrive.carrot.control_advisor.recording import _automatic_initial
    controller = await self.make_controller()
    started = await controller.start(mode="live", interval_seconds=180, request_id="automatic-summary")
    automatic = _automatic_initial(180, state="partial", reason="window_blocked")
    automatic.update(result_revision=1, attempt={"state": "partial", "job_id": "a" * 64, "cutoff_mono_ns": 300,
      "source_start_ns": 100, "source_end_ns": 200, "source_count": 2, "processed_windows": 1, "blocked_windows": 1,
      "windows": [{"source_start_ns": 100, "source_end_ns": 150, "state": "ready", "reason": None},
                  {"source_start_ns": 151, "source_end_ns": 200, "state": "blocked", "reason": "missing_observation"}]})
    self.processes[0].emit(started, automatic_analysis=automatic)
    received = await self.state(controller, "running")
    self.assertEqual(received["automatic_analysis"], automatic)
    self.assertFalse(received["automatic_analysis"]["apply_enabled"])

  async def test_automatic_summary_rejects_paths_claims_wrong_period_and_demo_execution(self):
    from openpilot.selfdrive.carrot.control_advisor.recording import _automatic_initial
    cases = [(_automatic_initial(300), "live"),
             ({**_automatic_initial(180), "output_root": "C:/private/path"}, "live"),
             ({**_automatic_initial(180), "apply_enabled": True}, "live"),
             (_automatic_initial(180, state="running"), "synthetic")]
    for index, (automatic, mode) in enumerate(cases):
      controller = await self.make_controller()
      started = await controller.start(mode=mode, interval_seconds=180, request_id=f"automatic-bad-{index}")
      self.processes[0].emit(started, automatic_analysis=automatic)
      failed = await self.state(controller, "failed")
      self.assertEqual(failed["reason"], "invalid_worker_output")

  async def make_controller(self, *, close_exits=True, heartbeat=.5, live_environment=None):
    self.spawned, self.stopped, self.processes = [], [], []

    async def spawn(*args, **kwargs):
      self.spawned.append((args, kwargs))
      process = FakeProcess(close_exits=close_exits)
      self.processes.append(process)
      return process

    async def stopper(process):
      self.stopped.append(process)
      process.finish(-9)

    controller = SessionController(spawn=spawn, stopper=stopper, heartbeat_timeout=heartbeat, graceful_timeout=.03,
                                   live_environment=live_environment)
    self.addAsyncCleanup(controller.close)
    return controller

  async def state(self, controller, expected):
    async def wait():
      while controller.status()["state"] != expected:
        await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), 1.0)
    return controller.status()

  async def test_idle_does_not_start_worker_and_start_stop_are_owned(self):
    controller = await self.make_controller()
    self.assertEqual("idle", controller.status()["state"])
    self.assertEqual([], self.spawned)
    with patch.dict(os.environ, {"CARROT_CONTROL_ADVISOR_TOKEN": "secret-not-for-child"}):
      started = await controller.start(mode="synthetic", interval_seconds=300, request_id="first")
    args, kwargs = self.spawned[0]
    self.assertEqual(("--mode", "synthetic", "--session-id", started["session_id"], "--interval-seconds", "300"), args[-6:])
    self.assertNotIn("CARROT_CONTROL_ADVISOR_TOKEN", kwargs["env"])
    self.assertEqual(Path(__file__).resolve().parents[5], Path(kwargs["cwd"]))
    self.processes[0].emit(started)
    active = await self.state(controller, "running")
    self.assertEqual(1, active["received_events"])
    stopped = await controller.stop(started["session_id"])
    self.assertEqual("stopped", stopped["state"])
    self.assertTrue(self.processes[0].stdin.closed.is_set())
    self.assertEqual([], self.stopped)

  async def test_runtime_child_overrides_are_live_only_copied_and_never_forward_token(self):
    path = str(Path.cwd() / "native-evidence")
    supplied = {"CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT": path, "CARROT_CONTROL_ADVISOR_AUTO_ENABLED": "1"}
    controller = await self.make_controller(live_environment=supplied)
    supplied["CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT"] = str(Path.cwd() / "changed-after-construction")
    with patch.dict(os.environ, {"CARROT_CONTROL_ADVISOR_TOKEN": "not-for-worker", "CARROT_CONTROL_ADVISOR_AUTO_ENABLED": "0"}, clear=True):
      original = dict(os.environ)
      live = await controller.start(mode="live", interval_seconds=180, request_id="native-environment")
      self.assertEqual(dict(os.environ), original)
      child = self.spawned[-1][1]["env"]
      self.assertEqual(child["CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT"], path)
      self.assertEqual(child["CARROT_CONTROL_ADVISOR_AUTO_ENABLED"], "0")
      self.assertNotIn("CARROT_CONTROL_ADVISOR_TOKEN", child)
      await controller.stop(live["session_id"])
      await controller.start(mode="synthetic", interval_seconds=180, request_id="demo-environment")
      self.assertNotIn("CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT", self.spawned[-1][1]["env"])
      self.assertEqual(dict(os.environ), original)

  async def test_runtime_child_overrides_reject_secrets_flags_and_relative_paths(self):
    for value in ({"CARROT_CONTROL_ADVISOR_TOKEN": "secret"}, {"PATH": str(Path.cwd())},
                  {"CARROT_CONTROL_ADVISOR_AUTO_ENABLED": "yes"}, {"CARROT_CONTROL_ADVISOR_SETTINGS_DIR": "relative"},
                  {"CARROT_CONTROL_ADVISOR_SETTINGS_DIR": 1}):
      with self.subTest(value=list(value)), self.assertRaisesRegex(ValueError, "invalid_live_environment"):
        SessionController(live_environment=value)

  async def test_duplicate_request_never_restarts_and_stale_stop_is_rejected(self):
    controller = await self.make_controller()
    first = await controller.start(mode="live", interval_seconds=180, request_id="one")
    duplicate = await controller.start(mode="live", interval_seconds=180, request_id="one")
    self.assertEqual(first["session_id"], duplicate["session_id"])
    self.assertEqual(1, len(self.spawned))
    with self.assertRaises(SessionConflict):
      await controller.start(mode="synthetic", interval_seconds=300, request_id="one")
    with self.assertRaises(SessionConflict):
      await controller.start(mode="live", interval_seconds=300, request_id="two")
    with self.assertRaises(SessionConflict):
      await controller.stop("stale")
    await controller.stop(first["session_id"])
    repeated = await controller.start(mode="live", interval_seconds=180, request_id="one")
    self.assertEqual("stopped", repeated["state"])
    self.assertEqual(1, len(self.spawned))
    second = await controller.start(mode="synthetic", interval_seconds=300, request_id="two")
    self.assertNotEqual(first["session_id"], second["session_id"])
    controller._accept({"arbitrary": "late"}, 1)
    self.assertEqual(second["session_id"], controller.status()["session_id"])
    old_duplicate = await controller.start(mode="live", interval_seconds=180, request_id="one")
    self.assertEqual(first["session_id"], old_duplicate["session_id"])
    self.assertEqual(second["session_id"], controller.status()["session_id"])
    self.assertEqual(2, len(self.spawned))

  async def test_no_heartbeat_or_oversized_output_terminates_owned_worker(self):
    for hostile in (False, True):
      with self.subTest(hostile=hostile):
        # Oversized output must exercise decoding, not race a 30 ms wall-clock
        # deadline under a busy host. The separate silence case tests timeout.
        controller = await self.make_controller(heartbeat=.5 if hostile else .03)
        await controller.start(mode="live", interval_seconds=300, request_id="hostile")
        process = self.processes[0]
        if hostile:
          process.stdout.feed_data(b"x" * (MAX_LINE_BYTES + 2) + b"\n")
        result = await self.state(controller, "failed")
        self.assertEqual("invalid_worker_output" if hostile else "worker_heartbeat_timeout", result["reason"])
        self.assertIsNotNone(process.returncode)
        self.assertIn(process, self.stopped)

  async def test_invalid_and_capability_claiming_worker_is_rejected(self):
    for change in ({"apply_enabled": True}, {"history": [{}] * 7}, {"observations": True}, {"schema_version": True}):
      with self.subTest(change=change):
        controller = await self.make_controller()
        session = await controller.start(mode="synthetic", interval_seconds=300, request_id="bad")
        self.processes[0].emit(session, **change)
        failed = await self.state(controller, "failed")
        self.assertEqual("invalid_worker_output", failed["reason"])
        self.assertFalse(failed["apply_enabled"])

  async def test_recursive_duplicate_and_nonfinite_worker_json_fail_closed(self):
    for content in (b"[" * 1500 + b"]" * 1500, b'{"schema_version":1,"schema_version":1}', b'{"age_ms":NaN}'):
      with self.subTest(prefix=content[:25]):
        controller = await self.make_controller()
        await controller.start(mode="live", interval_seconds=300, request_id="malformed")
        self.processes[0].stdout.feed_data(content + b"\n")
        result = await self.state(controller, "failed")
        self.assertEqual("invalid_worker_output", result["reason"])
        self.assertIsNotNone(self.processes[0].returncode)

  async def test_worker_metadata_is_validated_and_propagated(self):
    controller = await self.make_controller()
    session = await controller.start(mode="live", interval_seconds=300, request_id="metadata")
    self.processes[0].emit(session, clock_mode="native_monotonic", age_ms=125.5, finalized_through_ns=100,
                           diagnostic_invalidated=True, dropped_events=2, counters={"dropped_events": 2},
                           metadata={"settings_history": "unknown", "gap_detection": "timestamp_heuristic_no_sequence",
                                     "device_qualified": False, "overlapping_windows": True})
    result = await self.state(controller, "running")
    self.assertEqual(125.5, result["age_ms"])
    self.assertEqual(100, result["finalized_through_ns"])
    self.assertTrue(result["diagnostic_invalidated"])
    self.assertEqual(2, result["counters"]["dropped_events"])
    self.assertFalse(result["metadata"]["device_qualified"])

  async def test_oversized_payload_envelope_remains_explicitly_degraded(self):
    controller = await self.make_controller()
    session = await controller.start(mode="synthetic", interval_seconds=300, request_id="omitted")
    self.processes[0].emit(session, state="degraded", reason="status_payload_limit", diagnostic=None, history=[],
                           diagnostic_invalidated=True,
                           metadata={"settings_history": "unknown", "gap_detection": "timestamp_heuristic_no_sequence",
                                     "device_qualified": False, "overlapping_windows": True, "payload_details_omitted": True})
    result = await self.state(controller, "degraded")
    self.assertEqual("status_payload_limit", result["reason"])
    self.assertTrue(result["diagnostic_invalidated"])
    self.assertTrue(result["metadata"]["payload_details_omitted"])
    self.assertIsNone(self.processes[0].returncode)

  async def test_simultaneous_starts_serialize_and_request_cache_is_bounded(self):
    controller = await self.make_controller()
    results = await asyncio.gather(
      controller.start(mode="synthetic", interval_seconds=300, request_id="race-a"),
      controller.start(mode="synthetic", interval_seconds=300, request_id="race-b"), return_exceptions=True)
    self.assertEqual(1, len(self.spawned))
    self.assertEqual(1, sum(isinstance(result, SessionConflict) for result in results))
    await controller.stop(controller.status()["session_id"])
    for index in range(17):
      session = await controller.start(mode="synthetic", interval_seconds=300, request_id=f"history-{index}")
      await controller.stop(session["session_id"])
    self.assertEqual(16, len(controller._requests))

  async def test_nonzero_exit_and_spawn_failure_do_not_restart(self):
    controller = await self.make_controller()
    await controller.start(mode="live", interval_seconds=300, request_id="exit")
    self.processes[0].finish(7)
    result = await self.state(controller, "failed")
    self.assertEqual("worker_exited", result["reason"])
    self.assertEqual(1, len(self.spawned))

    async def cannot_spawn(*args, **kwargs):
      raise OSError("private-host-path")

    controller = SessionController(spawn=cannot_spawn)
    self.addAsyncCleanup(controller.close)
    failed = await controller.start(mode="live", interval_seconds=300, request_id="spawn")
    self.assertEqual("failed", failed["state"])
    self.assertNotIn("private-host-path", json.dumps(failed))

  async def test_stderr_is_drained_bounded_and_not_public(self):
    controller = await self.make_controller()
    session = await controller.start(mode="synthetic", interval_seconds=300, request_id="stderr")
    self.processes[0].stderr.feed_data(b"private-stderr" * 4000)
    self.processes[0].emit(session)
    await self.state(controller, "running")
    await asyncio.sleep(.01)
    self.assertLessEqual(len(controller._stderr_tail), 8192)
    self.assertNotIn("private-stderr", json.dumps(controller.status()))

  async def test_unavailable_exit_has_no_synthetic_fallback(self):
    controller = await self.make_controller()
    session = await controller.start(mode="live", interval_seconds=300, request_id="native")
    self.processes[0].emit(session, state="unavailable", reason="native_unavailable")
    self.processes[0].finish(0)
    result = await self.state(controller, "unavailable")
    self.assertEqual("live", result["source_mode"])
    self.assertEqual(1, len(self.spawned))

  async def test_cancelled_start_waits_for_spawn_then_cleans_up(self):
    entered, release = asyncio.Event(), asyncio.Event()
    process = FakeProcess()

    async def spawn(*args, **kwargs):
      entered.set()
      await release.wait()
      return process

    async def stopper(proc):
      proc.finish(-9)

    controller = SessionController(spawn=spawn, stopper=stopper, heartbeat_timeout=.5, graceful_timeout=.03)
    self.addAsyncCleanup(controller.close)
    task = asyncio.create_task(controller.start(mode="synthetic", interval_seconds=300, request_id="cancel"))
    await entered.wait()
    task.cancel()
    release.set()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertIsNotNone(process.returncode)
    self.assertIsNone(controller._process)

  async def test_cancelled_stop_still_forces_unresponsive_worker_exit(self):
    controller = await self.make_controller(close_exits=False)
    session = await controller.start(mode="live", interval_seconds=300, request_id="stop")
    process = self.processes[0]
    task = asyncio.create_task(controller.stop(session["session_id"]))
    await process.stdin.closed.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertIsNotNone(process.returncode)
    self.assertEqual("stopped", controller.status()["state"])

  async def test_close_stops_session_and_disallows_late_start(self):
    controller = await self.make_controller()
    await controller.start(mode="synthetic", interval_seconds=300, request_id="close")
    await controller.close()
    self.assertIsNone(controller._process)
    with self.assertRaises(SessionConflict):
      await controller.start(mode="live", interval_seconds=300, request_id="late")

  async def test_actual_synthetic_worker_records_bookmarks_without_analysis_and_eof_cleanup(self):
    controller = SessionController(heartbeat_timeout=10, graceful_timeout=3)
    self.addAsyncCleanup(controller.close)
    session = await controller.start(mode="synthetic", interval_seconds=180, request_id="actual-child")
    process = controller._process

    async def bookmark_ready():
      while True:
        status = controller.status()
        if status["state"] in ("failed", "unavailable", "ended", "stopped"):
          self.fail(f"synthetic child ended before bookmark: {status['reason']}")
        if status["diagnostic_revision"] >= 1:
          return status
        await asyncio.sleep(.02)

    status = await asyncio.wait_for(bookmark_ready(), 20)
    self.assertIsNone(status["diagnostic"])
    self.assertEqual(status["analysis_control"]["state"], "recording")
    self.assertEqual("synthetic", status["source_mode"])
    self.assertEqual("accelerated_synthetic", status["clock_mode"])
    self.assertGreater(status["received_events"], 0)
    self.assertGreater(status["observations"], 0)
    self.assertLessEqual(len(status["history"]), 6)
    self.assertFalse(status["recommendations_enabled"])
    self.assertFalse(status["apply_enabled"])
    self.assertEqual(session["session_id"], status["session_id"])
    await controller.close()
    self.assertIsNone(controller._process)
    self.assertEqual(0, process.returncode)
    self.assertTrue(process.stdin.is_closing())
    self.assertEqual("stopped", controller.status()["state"])

  async def test_invalid_start_never_spawns(self):
    controller = await self.make_controller()
    for values in (("live", True, "ok"), ("live", 181, "ok"), ("unknown", 300, "ok"), ("live", 300, "../bad")):
      with self.subTest(values=values):
        with self.assertRaises(ValueError):
          await controller.start(mode=values[0], interval_seconds=values[1], request_id=values[2])
    self.assertEqual([], self.spawned)


if __name__ == "__main__":
  unittest.main()
