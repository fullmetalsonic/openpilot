"""Host-only transport/session tests; no native device or control writes."""

from collections import deque
from contextlib import contextmanager
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from openpilot.selfdrive.carrot.control_advisor.live import LiveEvent, LiveSession, NativeTransport, MAX_EVENT_BYTES, LIVE_SERVICES
from openpilot.selfdrive.carrot.control_advisor.normalizer import EventNormalizer, SERVICES
from openpilot.selfdrive.carrot.control_advisor import worker


class Clock:
  def __init__(self, step=0):
    self.now, self.step = 0, step

  def __call__(self):
    self.now += self.step
    return self.now


class FakeSocket:
  def __init__(self, values):
    self.values, self.closed = deque(values), False

  def receive(self, *, non_blocking):
    assert non_blocking is True
    return self.values.popleft() if self.values else None

  def close(self):
    self.closed = True


class FakeMessaging:
  def __init__(self, per_service=3):
    self.calls, self.decode_calls = [], []
    self.sockets = {service: FakeSocket([json.dumps({"service": service, "time": i + 1}).encode()
                                        for i in range(per_service)]) for service in LIVE_SERVICES}
    self.log = SimpleNamespace(Event=SimpleNamespace(from_bytes=self.decode))

  def sub_sock(self, service, **options):
    self.calls.append((service, options))
    return self.sockets[service]

  @contextmanager
  def decode(self, blob, **limits):
    self.decode_calls.append((len(blob), limits))
    raw = json.loads(blob)
    event = SimpleNamespace(logMonoTime=raw["time"], valid=True, which=lambda: raw["service"])
    setattr(event, raw["service"], SimpleNamespace(to_dict=lambda: {}))
    yield event


def ticks(session, start, end):
  for index in range(start, end):
    session.ingest_batch(worker._synthetic_tick(index))


class LiveTests(unittest.TestCase):
  def test_native_poll_is_nonconflated_fair_and_count_bounded(self):
    source = FakeMessaging()
    transport = NativeTransport(messaging_module=source, max_event_bytes=4096, clock_ns=Clock(step=1000))
    self.assertEqual([(service, {"conflate": False}) for service in LIVE_SERVICES], source.calls)
    received = transport.poll(max_events=10, budget_ns=5_000_000)
    self.assertEqual([service for _ in range(2) for service in LIVE_SERVICES], [event.service for event in received])
    self.assertEqual(10, transport.received_events)
    self.assertTrue(all(limits == {"traversal_limit_in_words": 512, "nesting_limit": 64} for _, limits in source.decode_calls))
    transport.close()
    self.assertTrue(all(socket.closed for socket in source.sockets.values()))

  def test_native_oversize_is_rejected_before_decode_and_empty_poll_returns(self):
    source = FakeMessaging(per_service=0)
    source.sockets[SERVICES[0]].values.append(b"x" * 4097)
    transport = NativeTransport(messaging_module=source, max_event_bytes=4096, clock_ns=Clock(step=1000))
    self.assertEqual([], transport.poll())
    self.assertEqual(1, transport.rejected_events)
    self.assertEqual([], source.decode_calls)
    self.assertEqual([], transport.poll())

  def test_native_time_budget_is_cooperative_and_overrun_is_visible(self):
    transport = NativeTransport(messaging_module=FakeMessaging(per_service=20), clock_ns=Clock(step=1_000_000))
    received = transport.poll(max_events=256, budget_ns=5_000_000)
    self.assertLess(len(received), 20)
    self.assertEqual(1, transport.budget_overruns)
    self.assertGreater(transport.last_poll_ns, 5_000_000)

  def test_timestamp_reorder_uses_controls_before_car_state_at_equal_time(self):
    session = LiveSession("order", source_mode="synthetic", clock_ns=Clock())
    session.ingest_batch(list(reversed(worker._synthetic_tick(0))))
    session.ingest_batch(list(reversed(worker._synthetic_tick(3))))
    self.assertGreater(session.observations, 0)
    self.assertTrue(all(row.valid and row.active for row in session._window))
    self.assertTrue(all(row.context["recv_ns"] == row.mono_ns for row in session._window))
    self.assertEqual(0, session.late_events)

  def test_late_intervention_invalidates_live_inputs_without_running_analysis(self):
    session = LiveSession("late", source_mode="synthetic", interval_seconds=180, clock_ns=Clock())
    ticks(session, 0, 3605)
    first = session.preservation_bookmark.copy()
    self.assertIsNotNone(first)
    self.assertTrue(first["recording_only"])
    intervention = LiveEvent("carState", 2_000_000_000, True, {"steeringPressed": True}, 2_050_000_000)
    session.ingest_batch([intervention])
    status = session.snapshot()
    self.assertEqual("degraded", status["state"])
    self.assertEqual(1, status["late_events"])
    self.assertIsNone(status["diagnostic"])
    self.assertFalse(first["invalidated"])  # previous immutable result was not overwritten
    self.assertTrue(all(not item["ready"] for item in status["transition"].values()))
    ticks(session, 3605, 3705)
    self.assertIsNone(session.diagnostic)
    ticks(session, 3705, 7210)
    self.assertIsNone(session.diagnostic)
    self.assertGreater(session.preservation_bookmark["revision"], first["revision"])

  def test_count_byte_and_observation_limits_invalidate_instead_of_silent_drop(self):
    session = LiveSession("queue", source_mode="synthetic", max_queue_events=3, clock_ns=Clock())
    session.ingest_batch(worker._synthetic_tick(0))
    self.assertEqual(1, session.overflow_events)
    self.assertEqual(4, session.dropped_events)
    self.assertEqual(0, session.observations)
    self.assertEqual("degraded", session.state)
    session = LiveSession("bytes", source_mode="synthetic", clock_ns=Clock())
    session.ingest_batch([LiveEvent("carState", 1, True, {}, 1, MAX_EVENT_BYTES + 1)])
    self.assertEqual(1, session.overflow_events)
    session = LiveSession("observations", source_mode="synthetic", max_observations=2, clock_ns=Clock())
    ticks(session, 0, 6)
    self.assertGreater(session.overflow_events, 0)
    self.assertLessEqual(len(session._window), 2)

  def test_ending_after_loss_preserves_a_partial_bookmark_without_analysis(self):
    session = LiveSession("late-end", source_mode="synthetic", interval_seconds=180, clock_ns=Clock())
    ticks(session, 0, 3605)
    session.ingest_batch([LiveEvent("carState", 2_000_000_000, True, {}, 2_050_000_000)])
    ticks(session, 3605, 3705)
    session.finish()
    status = session.snapshot()
    self.assertEqual("ended", status["state"])
    self.assertIsNone(status["diagnostic"])
    self.assertTrue(session.preservation_bookmark["partial_window"])
    self.assertEqual(session.late_events, 1)

  def test_missing_data_gaps_expiry_and_source_clock_are_not_healthy_states(self):
    clock = Clock()
    session = LiveSession("empty", source_mode="live", max_session_seconds=1, clock_ns=clock)
    self.assertEqual("waiting_for_data", session.snapshot()["state"])
    self.assertIsNone(session.snapshot()["age_ms"])
    clock.now = 1_000_000_000
    session.tick()
    self.assertEqual("ended", session.state)
    self.assertEqual("session_time_limit", session.reason)
    self.assertFalse(session.snapshot()["parking_confirmed"])
    session = LiveSession("stale", source_mode="live", clock_ns=Clock())
    session.ingest_batch([LiveEvent("carState", 1, True, {}, 3_000_000_002)])
    self.assertEqual(1, session.late_events)
    self.assertEqual("degraded", session.state)
    session = LiveSession("gap", source_mode="synthetic", clock_ns=Clock())
    ticks(session, 0, 5)
    ticks(session, 50, 55)
    self.assertGreater(session.gap_events, 0)
    self.assertEqual("degraded", session.state)

  def test_recording_window_and_history_remain_bounded_without_diagnostics(self):
    session = LiveSession("bounded", source_mode="synthetic", clock_ns=Clock())
    for index in range(1500):
      events = worker._synthetic_tick(index)
      if index % 10 == 0:
        events[-1].data["steeringPressed"] = True
      session.ingest_batch(events)
    session.finish()
    status = session.snapshot()
    self.assertLessEqual(len(status["history"]), 6)
    self.assertIsNone(status["diagnostic"])
    self.assertLess(len(session._window), 100)
    self.assertTrue(session.preservation_bookmark["recording_only"])
    self.assertFalse(status["recommendations_enabled"])
    self.assertFalse(status["apply_enabled"])

  def test_shared_normalizer_records_freshness_and_rejects_bad_plain_signals(self):
    normalizer = EventNormalizer(epoch="fixture", max_age_ns=100_000_000)
    values = worker._synthetic_tick(0)
    records = []
    for event in values:
      records.extend(normalizer.feed(event.service, event.mono_ns, event.valid, event.data))
    self.assertTrue(all(row["valid"] for row in records))
    self.assertNotIn("recv_ns", records[0]["context"])
    self.assertEqual("steering_model_derived", records[0]["context"]["response_kind"])
    with self.assertRaises(ValueError):
      normalizer.feed("carState", 2_000_000_000, True, {"canValid": "yes"})

  def test_observed_settings_initial_change_and_gap_reset_both_axes(self):
    session = LiveSession("settings", source_mode="synthetic", interval_seconds=180, clock_ns=Clock())
    ticks(session, 0, 3605)
    first = session.preservation_bookmark.copy()
    notice = {"revision": 1, "durable_revision": 1, "state": "partial", "journal": {"durable": True},
              "observation": {"gap": False}}
    session.observe_settings(notice)
    self.assertIsNone(session.diagnostic)
    self.assertFalse(first["invalidated"])
    self.assertEqual(0, len(session._window))
    ticks(session, 3605, 3620)
    self.assertTrue(all(row.epoch == "observed-1" and row.context["observed_settings_revision"] == 1 for row in session._window))
    count = len(session._window)
    session.observe_settings(notice)
    self.assertEqual(count, len(session._window))
    session.observe_settings({**notice, "notification_gap": True})
    self.assertEqual(0, len(session._window))
    session.observe_settings({**notice, "revision": 2, "durable_revision": 2})
    ticks(session, 3620, 3630)
    session.finish()
    self.assertEqual(2, session.preservation_bookmark["observed_settings_revision"])
    self.assertTrue(all(row.epoch == "observed-2" for row in session._window))

  def test_worker_eof_finishes_without_waiting_for_logical_interval(self):
    root = Path(__file__).parents[5]
    command = [sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor.worker", "--mode", "synthetic",
               "--session-id", "eof-fixture", "--interval-seconds", "300"]
    process = subprocess.run(command, input=b"", capture_output=True, cwd=root, timeout=5, check=False)
    self.assertEqual(0, process.returncode, process.stderr.decode(errors="replace"))
    lines = process.stdout.splitlines()
    self.assertTrue(all(len(line) + 1 <= worker.MAX_STATUS_BYTES for line in lines))
    statuses = [json.loads(line) for line in lines]
    self.assertEqual("ended", statuses[-1]["state"])
    self.assertEqual("parent_pipe_closed", statuses[-1]["reason"])
    self.assertTrue(all(status["clock_mode"] == "accelerated_synthetic" for status in statuses))
    self.assertTrue(all(not status["apply_enabled"] and not status["recommendations_enabled"] for status in statuses))

  def test_native_unavailable_has_no_synthetic_fallback(self):
    statuses = []
    with mock.patch.object(worker, "NativeTransport", side_effect=RuntimeError("native missing")), \
         mock.patch.object(worker, "_watch_stdin"), mock.patch.object(worker, "_emit", side_effect=statuses.append):
      self.assertEqual(0, worker.run(mode="live", session_id="native-missing", interval_seconds=300))
    self.assertEqual(1, len(statuses))
    self.assertEqual("unavailable", statuses[0]["state"])
    self.assertEqual("live", statuses[0]["source_mode"])
    self.assertEqual(0, statuses[0]["received_events"])

  def test_synthetic_natural_end_exits_cleanly_while_parent_pipe_stays_open(self):
    root = Path(__file__).parents[5]
    command = [sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor.worker", "--mode", "synthetic",
               "--session-id", "natural-end-fixture", "--interval-seconds", "300"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root)
    self.addCleanup(lambda: process.kill() if process.poll() is None else None)
    # communicate(input=...) would close stdin and miss the natural-end path.
    statuses = []
    def read_output():
      statuses.extend(json.loads(line) for line in process.stdout)
    import threading
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    self.assertEqual(0, process.wait(timeout=20))
    reader.join(timeout=2)
    error = process.stderr.read().decode(errors="replace")
    process.stdin.close()
    process.stdout.close()
    process.stderr.close()
    self.assertFalse(reader.is_alive())
    self.assertEqual("", error)
    self.assertEqual("ended", statuses[-1]["state"])
    self.assertEqual("synthetic_logical_600_seconds_complete", statuses[-1]["reason"])
    self.assertEqual(10, statuses[-1]["diagnostic_revision"])  # One light bookmark per minute, including final tail.
    self.assertIsNone(statuses[-1]["diagnostic"])
    self.assertEqual((0, 0, 0), tuple(statuses[-1][name] for name in ("late_events", "overflow_events", "gap_events")))

  def test_status_byte_limit_is_explicit_instead_of_truncating_json(self):
    session = LiveSession("payload", source_mode="synthetic", clock_ns=Clock())
    status = session.snapshot()
    status["diagnostic"] = {"oversized": "x" * worker.MAX_STATUS_BYTES}
    output = io.BytesIO()
    with mock.patch.object(worker.sys, "stdout", SimpleNamespace(buffer=output)):
      worker._emit(status)
    self.assertLessEqual(len(output.getvalue()), worker.MAX_STATUS_BYTES)
    decoded = json.loads(output.getvalue())
    self.assertEqual("status_payload_limit", decoded["reason"])
    self.assertTrue(decoded["metadata"]["payload_details_omitted"])
    self.assertIsNone(decoded["diagnostic"])


if __name__ == "__main__":
  unittest.main()
