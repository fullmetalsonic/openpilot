"""Host parking profile and source separation, with generated native bytes."""
from collections import deque
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.parking import ParkingObserver, validate_parking
from openpilot.selfdrive.carrot.control_advisor.live import LiveEvent, LiveSession, NativeTransport, LIVE_SERVICES


class Clock:
  def __init__(self): self.now = 0
  def __call__(self): return self.now


CAR = {"gearShifter": "park", "standstill": True, "vEgo": 0., "canValid": True, "canTimeout": False}
CONTROL = {"enabled": False, "latActive": False, "longActive": False}


def park(observer, clock, start=0, count=22):
  for i in range(start, start + count):
    clock.now = 1_000_000_000 + i * 100_000_000
    observer.feed("carControl", clock.now, True, CONTROL)
    observer.feed("carState", clock.now, True, CAR)


class ParkingTests(unittest.TestCase):
  def observer(self):
    clock = Clock()
    return ParkingObserver(clock_ns=clock), clock

  def test_continuous_p_steady_and_inactive_requires_full_hold(self):
    observer, clock = self.observer()
    park(observer, clock, count=20)
    self.assertEqual("park_candidate", observer.snapshot()["state"])
    park(observer, clock, start=20, count=1)
    self.assertEqual("park_observed", observer.snapshot()["state"])
    self.assertEqual("unknown", observer.snapshot()["offroad_state"])

  def test_stationary_drive_missing_invalid_and_active_do_not_confirm(self):
    for car, control, valid in [({**CAR, "gearShifter": "drive"}, CONTROL, True),
                               ({**CAR, "standstill": None}, CONTROL, True),
                               (CAR, {"enabled": False}, True), (CAR, {**CONTROL, "latActive": True}, True),
                               (CAR, CONTROL, False), ({**CAR, "vEgo": float("nan")}, CONTROL, True)]:
      observer, clock = self.observer()
      for i in range(25):
        clock.now = 1_000_000_000 + i * 100_000_000
        observer.feed("carControl", clock.now, True, control)
        observer.feed("carState", clock.now, valid, car)
      self.assertEqual("unknown", observer.snapshot()["state"])

  def test_motion_and_control_reactivation_clear_immediately_history_stays(self):
    observer, clock = self.observer()
    park(observer, clock)
    history = observer.last_park_observation()
    clock.now += 100_000_000
    observer.feed("carState", clock.now, True, {**CAR, "vEgo": .2})
    self.assertEqual("moving", observer.snapshot()["state"])
    observer.feed("carControl", clock.now, True, {**CONTROL, "enabled": True})
    self.assertEqual("unknown", observer.snapshot()["state"])
    self.assertEqual(history, observer.last_park_observation())

  def test_expiry_source_gap_and_duplicate_require_new_hold(self):
    observer, clock = self.observer()
    park(observer, clock)
    clock.now += 201_000_000
    self.assertEqual("unknown", observer.snapshot()["state"])
    park(observer, clock, start=25, count=1)
    self.assertEqual("park_candidate", observer.snapshot()["state"])
    observer.feed("carState", clock.now, True, CAR)
    self.assertEqual("unknown", observer.snapshot()["state"])
    park(observer, clock, start=26, count=20)
    self.assertEqual("park_candidate", observer.snapshot()["state"])

  def test_auxiliary_offroad_is_separate_and_stops_without_replaying_history(self):
    observer, clock = self.observer()
    park(observer, clock)
    clock.now += 300_000_000
    observer.feed("deviceState", clock.now, True, {"started": False})
    actual = observer.snapshot()
    self.assertEqual("unknown", actual["state"])
    self.assertEqual("offroad_observed", actual["offroad_state"])
    clock.now += 2_000_000_001
    self.assertEqual("unknown", observer.snapshot()["offroad_state"])
    observer.stop()
    self.assertEqual("unknown", observer.snapshot()["state"])
    self.assertEqual("park_observed", observer.last_park_observation()["state"])

  def test_aux_does_not_advance_control_watermark_or_keep_input_alive(self):
    clock = Clock()
    session = LiveSession("aux", source_mode="live", clock_ns=clock, lateness_ns=0)
    session.ingest_batch([LiveEvent("carControl", 1, True, CONTROL, 1)])
    watermark, received = session._released_through_ns, session._last_received_wall_ns
    clock.now = 3_000_000_000
    session.ingest_batch([LiveEvent("deviceState", clock.now, True, {"started": False}, clock.now)])
    self.assertEqual(watermark, session._released_through_ns)
    self.assertEqual(received, session._last_received_wall_ns)
    session.tick()
    self.assertEqual("input_stale", session.reason)
    self.assertEqual("offroad_observed", session.snapshot()["parking"]["offroad_state"])
    self.assertFalse(session.snapshot()["parking_confirmed"])

  def test_late_source_or_receive_timestamp_cannot_become_fresh_when_processed(self):
    for delay_source in (True, False):
      clock = Clock()
      session = LiveSession("delayed", source_mode="live", clock_ns=clock, lateness_ns=0)
      for i in range(25):
        source_ns = 1_000_000_000 + i * 100_000_000
        clock.now = source_ns + (1_900_000_000 if delay_source else 0)
        recv_ns = clock.now if delay_source else clock.now - 300_000_000
        session.ingest_batch([LiveEvent("carControl", source_ns, True, CONTROL, recv_ns),
                              LiveEvent("carState", source_ns, True, CAR, recv_ns)])
      self.assertFalse(session.snapshot()["parking_confirmed"])
      self.assertIsNone(session.snapshot()["last_park_observation"])
    observer = ParkingObserver(clock_ns=clock)
    for i in range(25):
      clock.now += 100_000_000
      observer.feed("carControl", clock.now, True, CONTROL, clock.now - 300_000_000)
      observer.feed("carState", clock.now, True, CAR, clock.now - 300_000_000)
    self.assertEqual("unknown", observer.snapshot()["state"])
    self.assertIsNone(observer.last_park_observation())
    observer.feed("deviceState", clock.now - 2_000_000_001, True, {"started": False}, clock.now)
    self.assertEqual("unknown", observer.snapshot()["offroad_state"])

  def test_synthetic_source_clock_is_explicit_and_never_native_confirmation(self):
    host, logical = Clock(), Clock()
    observer = ParkingObserver(source_kind="synthetic_event", clock_ns=host, source_clock_ns=logical)
    for i in range(22):
      logical.now = 1_000_000_000 + i * 100_000_000
      observer.feed("carControl", logical.now, True, CONTROL)
      observer.feed("carState", logical.now, True, CAR)
    self.assertEqual("park_observed", observer.snapshot()["state"])
    self.assertEqual("synthetic_event", observer.snapshot()["source_kind"])
    session = LiveSession("demo-park", source_mode="synthetic", clock_ns=host, lateness_ns=0)
    for i in range(22):
      stamp = 1_000_000_000 + i * 100_000_000
      session.ingest_batch([LiveEvent("carControl", stamp, True, CONTROL, stamp), LiveEvent("carState", stamp, True, CAR, stamp)])
    self.assertEqual("park_observed", session.snapshot()["parking"]["state"])
    self.assertFalse(session.snapshot()["parking_confirmed"])

  def test_native_five_service_bytes_to_session_parking_and_final_tail(self):
    # Native-generated fixture, not a physical vehicle observation.
    from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
    parser, log = _schema()
    class Socket:
      def __init__(self): self.values = deque()
      def receive(self, **_): return self.values.popleft() if self.values else None
      def close(self): pass
    sockets = {service: Socket() for service in LIVE_SERVICES}
    messaging = SimpleNamespace(log=log, sub_sock=lambda name, **_: sockets[name])
    clock = Clock()
    transport = NativeTransport(messaging_module=messaging, clock_ns=clock)
    session = LiveSession("native-fixture", source_mode="live", clock_ns=clock, lateness_ns=0)
    for i in range(22):
      clock.now = 1_000_000_000 + i * 100_000_000
      for service, values in [("carControl", CONTROL), ("carState", CAR), ("deviceState", {"started": True})]:
        message = log.Event.new_message(logMonoTime=clock.now, valid=True)
        part = message.init(service)
        for key, value in values.items(): setattr(part, key, value)
        sockets[service].values.append(message.to_bytes())
      session.ingest_batch(transport.poll())
    status = session.snapshot()
    self.assertTrue(status["parking_confirmed"])
    self.assertEqual("onroad_observed", status["parking"]["offroad_state"])
    with patch("openpilot.selfdrive.carrot.control_advisor.live.analyze", side_effect=AssertionError("record-only finish ran heavy analysis")):
      session.finish()
    ended = session.snapshot()
    self.assertFalse(ended["parking_confirmed"])
    self.assertEqual("unknown", ended["parking"]["state"])
    self.assertEqual(status["parking"], ended["last_park_observation"])
    self.assertIsNone(ended["finalized_through_ns"])
    self.assertIsNone(ended["diagnostic"])
    self.assertEqual([], ended["history"])
    self.assertEqual(status["counters"], ended["counters"])
    self.assertEqual(66, ended["received_events"])
    self.assertEqual([], session._heap)
    self.assertTrue(all(not socket.values for socket in sockets.values()))
    transport.close()


if __name__ == "__main__": unittest.main()
