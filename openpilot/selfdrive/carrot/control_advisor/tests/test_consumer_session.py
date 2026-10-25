"""Parent-clock freshness for actual producer bytes across child lifecycles."""

import asyncio
from copy import deepcopy
import json
import time
import unittest

from openpilot.selfdrive.carrot.control_advisor import consumer_observer as producer
from openpilot.selfdrive.carrot.control_advisor.consumer_collector import ConsumerCollector
from openpilot.selfdrive.carrot.control_advisor.session import SessionController
from openpilot.selfdrive.carrot.control_advisor.tests.test_session import FakeProcess


BOOT = "00000000-0000-0000-0000-000000000001"


class Clock:
  def __init__(self, now):
    self.now = now

  def __call__(self):
    return self.now


class Transport:
  def __init__(self, value):
    self.value = value

  def receive(self, limit):
    value, self.value = self.value, None
    return value

  def close(self):
    pass


class ConsumerSessionTests(unittest.IsolatedAsyncioTestCase):
  @classmethod
  def setUpClass(cls):
    captured = []
    class Sender:
      def send(self, value):
        captured.append(value)
      def close(self):
        pass
    source_clock = Clock(10_000_000_000)
    pump = producer._Pump(sender=Sender(), identity={"boot_id": BOOT, "pid": 123, "process_instance_id": "c" * 32},
                          hash_provider=lambda cid: {producer.OBSERVER_PATH: "a" * 64, producer.CONSUMER_PATHS[cid]: "b" * 64})
    observed = producer._Observer("controls.torque", pump, clock_ns=source_clock)
    producer.observe(observed, lambda: {"fields": {"LateralTorqueFriction": producer.field(0.3, read_value=0.3,
                     source="stored", read_mono_ns=source_clock.now)}, "context": {"active": True}})
    deadline = time.monotonic() + 2
    while not captured and time.monotonic() < deadline:
      time.sleep(0.005)
    pump.close()
    if not captured:
      raise AssertionError("actual producer fixture not generated")
    cls.event = producer.validate_event(json.loads(captured[0]))

  async def make_controller(self, *, heartbeat=10, close_exits=True):
    self.parent_clock = Clock(100.0)  # Deliberately a different domain than event nanoseconds.
    self.processes, self.killed = [], []
    async def spawn(*args, **kwargs):
      process = FakeProcess(close_exits=close_exits)
      self.processes.append(process)
      return process
    async def stopper(process):
      self.killed.append(process)
      process.finish(-9)
    controller = SessionController(spawn=spawn, stopper=stopper, heartbeat_timeout=heartbeat,
                                   graceful_timeout=.02, clock=self.parent_clock)
    self.addAsyncCleanup(controller.close)
    return controller

  def consumers(self, sequence=1):
    event = deepcopy(self.event)
    event["sequence"] = sequence
    collector = ConsumerCollector(source_mode="synthetic", transport=Transport(json.dumps(event).encode()),
                                   expected_boot_id=BOOT, clock_ns=Clock(event["produced_mono_ns"]))
    self.assertEqual(1, len(collector.poll()))
    status = collector.snapshot()
    collector.close()
    return {**status, "persistence": {"state": "recorded", "recorded_events": 1, "dropped_events": 0}}

  async def accepted(self, controller, session, consumers, **changes):
    expected = self.parent_clock.now
    self.processes[-1].emit(session, consumer_observations=consumers, **changes)
    async def wait():
      while controller._last_heartbeat != expected or controller._snapshot["state"] == "starting":
        await asyncio.sleep(.001)
    try:
      await asyncio.wait_for(wait(), .5)
    except TimeoutError:
      self.fail(f"worker payload was not accepted: {controller.status()}")
    return controller.status()

  async def state(self, controller, value):
    async def wait():
      while controller.status()["state"] != value:
        await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), 1)
    return controller.status()

  def entry(self, snapshot):
    return next(item for item in snapshot["consumer_observations"]["consumers"] if item["consumer_id"] == "controls.torque")

  def assert_historical(self, snapshot):
    for entry in snapshot["consumer_observations"]["consumers"]:
      self.assertFalse(entry["fresh"])
      self.assertTrue(all(value is False for value in entry["read_freshness"].values()))
    entry = self.entry(snapshot)
    self.assertEqual(self.event, entry["latest_event"])
    self.assertEqual("stale", entry["state"])

  async def test_parent_ttl_ages_status_and_duplicate_request_without_cross_clock_math(self):
    controller = await self.make_controller()
    session = await controller.start(mode="synthetic", interval_seconds=300, request_id="fresh")
    initial = await self.accepted(controller, session, self.consumers())
    self.assertTrue(self.entry(initial)["fresh"])
    self.parent_clock.now = 102.0
    self.assertTrue(self.entry(controller.status())["fresh"])
    self.parent_clock.now = 102.001
    expired = controller.status()
    self.assert_historical(expired)
    duplicate = await controller.start(mode="synthetic", interval_seconds=300, request_id="fresh")
    self.assert_historical(duplicate)
    self.assertEqual(expired["consumer_observations"], duplicate["consumer_observations"])
    self.assertTrue(self.entry(controller._snapshot)["fresh"])  # immutable child history remains intact

  async def test_repeated_worker_heartbeats_do_not_renew_the_same_consumer_sample(self):
    controller = await self.make_controller()
    session = await controller.start(mode="synthetic", interval_seconds=300, request_id="repeat")
    consumers = self.consumers()
    await self.accepted(controller, session, consumers)
    self.parent_clock.now = 101.5
    await self.accepted(controller, session, consumers)
    self.parent_clock.now = 102.01
    self.assert_historical(controller.status())
    self.assertEqual(.51, controller.status()["last_heartbeat_age_seconds"])
    self.parent_clock.now = 102.1
    updated = await self.accepted(controller, session, self.consumers(sequence=2))
    self.assertTrue(self.entry(updated)["fresh"])

  async def test_eof_and_nonzero_exit_invalidate_current_values_immediately(self):
    for code, state in ((0, "stopped"), (7, "failed")):
      with self.subTest(code=code):
        controller = await self.make_controller()
        session = await controller.start(mode="synthetic", interval_seconds=300, request_id="exit")
        await self.accepted(controller, session, self.consumers())
        self.processes[-1].finish(code)
        ended = await self.state(controller, state)
        self.assert_historical(ended)
        duplicate = await controller.start(mode="synthetic", interval_seconds=300, request_id="exit")
        self.assert_historical(duplicate)

  async def test_heartbeat_timeout_and_forced_stop_do_not_keep_fresh_values(self):
    # Allow first-payload delivery across Windows event-loop scheduling; the
    # assertion below still requires an actual silent-child heartbeat timeout.
    controller = await self.make_controller(heartbeat=.2, close_exits=False)
    session = await controller.start(mode="synthetic", interval_seconds=300, request_id="timeout")
    await self.accepted(controller, session, self.consumers())
    failed = await self.state(controller, "failed")
    self.assertEqual("worker_heartbeat_timeout", failed["reason"])
    self.assert_historical(failed)
    self.assertTrue(self.killed)
    controller = await self.make_controller(close_exits=False)
    session = await controller.start(mode="synthetic", interval_seconds=300, request_id="forced")
    await self.accepted(controller, session, self.consumers())
    stopping = asyncio.create_task(controller.stop(session["session_id"]))
    await self.processes[-1].stdin.closed.wait()
    self.assert_historical(controller.status())
    stopped = await stopping
    self.assertEqual("stopped", stopped["state"])
    self.assert_historical(stopped)
    self.assertTrue(self.killed)

  async def test_heartbeat_age_can_invalidate_before_supervisor_timeout_is_scheduled(self):
    controller = await self.make_controller(heartbeat=.5)
    session = await controller.start(mode="synthetic", interval_seconds=300, request_id="parent-watch")
    await self.accepted(controller, session, self.consumers())
    self.parent_clock.now = 100.6
    self.assert_historical(controller.status())
    self.assertEqual("consumer_parent_heartbeat_stale", controller.status()["consumer_observations"]["reason"])

  async def test_old_request_age_and_terminal_state_are_not_reset_by_a_new_session(self):
    controller = await self.make_controller()
    first = await controller.start(mode="synthetic", interval_seconds=300, request_id="one")
    await self.accepted(controller, first, self.consumers())
    await controller.stop(first["session_id"])
    self.parent_clock.now = 101.0
    second = await controller.start(mode="synthetic", interval_seconds=300, request_id="two")
    await self.accepted(controller, second, self.consumers(sequence=2))
    old = await controller.start(mode="synthetic", interval_seconds=300, request_id="one")
    self.assert_historical(old)
    self.assertEqual(first["session_id"], old["session_id"])
    self.assertEqual(second["session_id"], controller.status()["session_id"])
    self.assertTrue(self.entry(controller.status())["fresh"])

  async def test_disabled_and_unavailable_source_reasons_are_preserved_on_worker_exit(self):
    for source_state, reason in (("disabled", "consumer_observations_disabled"),
                                 ("unavailable", "native_consumer_transport_unavailable")):
      with self.subTest(source_state=source_state):
        controller = await self.make_controller()
        session = await controller.start(mode="synthetic", interval_seconds=300, request_id="source")
        consumers = self.consumers()
        consumers.update(state=source_state, reason=reason)
        for entry in consumers["consumers"]:
          entry["fresh"] = False
          entry["read_freshness"] = dict.fromkeys(entry["read_freshness"], False)
        await self.accepted(controller, session, consumers)
        self.processes[-1].finish(7)
        ended = await self.state(controller, "failed")
        self.assertEqual(source_state, ended["consumer_observations"]["state"])
        self.assertEqual(reason, ended["consumer_observations"]["reason"])
        self.assert_historical(ended)


if __name__ == "__main__":
  unittest.main()
