"""Actual bounded producer/protocol tests with explicitly synthetic byte sinks."""

import builtins
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from openpilot.selfdrive.carrot.control_advisor import consumer_observer as observer
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry


BOOT = "00000000-0000-0000-0000-000000000001"


def wait_for(predicate, timeout=2):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    value = predicate()
    if value:
      return value
    time.sleep(0.005)
  raise AssertionError("bounded producer condition not reached")


class Sink:
  def __init__(self):
    self.bytes, self.closed = [], False

  def send(self, value):
    self.bytes.append(value)

  def close(self):
    self.closed = True


class Clock:
  def __init__(self):
    self.now = 10_000_000_000

  def __call__(self):
    return self.now


def hashes(consumer):
  return {observer.OBSERVER_PATH: "a" * 64, observer.CONSUMER_PATHS[consumer]: "b" * 64}


def payload(value=0.3, stamp=None):
  return {"fields": {"LateralTorqueFriction": observer.field(value, read_value=value, source="stored", read_mono_ns=stamp)},
          "context": {"active": True}}


class ObserverTests(unittest.TestCase):
  def test_torque_path_requires_actual_base_method_identity(self):
    class Base:
      def torque_from_lateral_accel_linear(self):
        return 0.
    class Standard(Base):
      pass
    class Custom(Base):
      def torque_from_lateral_accel_linear(self):
        return 1.
    class BrokenCallback:
      @property
      def __func__(self):
        raise RuntimeError("callback inspection failed")
    loaded = SimpleNamespace(CarInterfaceBase=Base)
    with mock.patch.dict(observer.sys.modules, {"opendbc.car.interfaces": loaded}):
      self.assertEqual("linear_standard", observer.torque_feedforward_path(Standard().torque_from_lateral_accel_linear))
      self.assertEqual("unverified", observer.torque_feedforward_path(Custom().torque_from_lateral_accel_linear))
      self.assertEqual("unverified", observer.torque_feedforward_path(lambda: 0.))
      self.assertEqual("unverified", observer.torque_feedforward_path(Base.torque_from_lateral_accel_linear))
      self.assertEqual("unverified", observer.torque_feedforward_path(BrokenCallback()))
    with mock.patch.dict(observer.sys.modules, {"opendbc.car.interfaces": None}):
      self.assertEqual("unverified", observer.torque_feedforward_path(Standard().torque_from_lateral_accel_linear))

  def test_torque_path_protocol_accepts_only_declared_states(self):
    for path in ("linear_standard", "unverified"):
      value = payload()
      value["context"]["torque_ff_path"] = path
      self.assertEqual(path, observer._payload("controls.torque", value)["context"]["torque_ff_path"])
    value["context"]["torque_ff_path"] = "linear_by_name"
    with self.assertRaisesRegex(ValueError, "invalid_consumer_context_enum"):
      observer._payload("controls.torque", value)

  def pump(self, sender=None, **options):
    sender = Sink() if sender is None else sender
    pump = observer._Pump(sender=sender, identity={"boot_id": BOOT, "pid": 123, "process_instance_id": "c" * 32},
                          hash_provider=hashes, **options)
    self.addCleanup(pump.close)
    return pump, sender

  def test_off_factory_and_observe_do_not_probe_or_call_factory(self):
    with mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch.object(builtins, "open", side_effect=AssertionError("OFF file probe")), \
         mock.patch.object(observer, "_read_boot_id", side_effect=AssertionError("OFF identity probe")), \
         mock.patch.object(observer, "_Pump", side_effect=AssertionError("OFF transport/thread probe")):
      self.assertIsNone(observer.create_observer("controls.torque"))
      observer.observe(None, lambda: (_ for _ in ()).throw(AssertionError("OFF factory ran")))

  def test_enabled_creation_failure_is_isolated_without_udp_fallback(self):
    fake_socket = mock.Mock()
    fake_socket.AF_UNIX = 1
    with mock.patch.dict(os.environ, {observer.ENV_ENABLED: "1", observer.ENV_SOCKET: str(Path.cwd() / "socket")}), \
         mock.patch.dict("sys.modules", {"socket": fake_socket}), \
         mock.patch.object(observer, "_Pump", side_effect=RuntimeError("synthetic thread failure")):
      self.assertIsNone(observer.create_observer("controls.torque"))

  def test_actual_producer_encodes_protocol_once_per_second_and_copies_values(self):
    pump, sink = self.pump()
    clock = Clock()
    sampled = observer._Observer("controls.torque", pump, clock_ns=clock)
    values = [1.0, 2.0]
    calls = []
    def factory():
      calls.append(True)
      return payload(values, stamp=clock.now)
    observer.observe(sampled, factory, cycle_id=7)
    values[0] = 99.0
    observer.observe(sampled, factory, cycle_id=8)
    wait_for(lambda: len(sink.bytes) == 1)
    event = observer.validate_event(json.loads(sink.bytes[0]))
    self.assertEqual(1, len(calls))
    self.assertEqual([1.0, 2.0], event["fields"]["LateralTorqueFriction"]["effective_value"])
    self.assertEqual(7, event["cycle_id"])
    self.assertEqual(1, event["sequence"])
    clock.now += 1_000_000_000
    observer.observe(sampled, factory)
    wait_for(lambda: len(sink.bytes) == 2)
    self.assertEqual(2, json.loads(sink.bytes[-1])["sequence"])
    self.assertTrue(all(len(value) <= observer.MAX_EVENT_BYTES for value in sink.bytes))

  def test_factory_failure_queue_overflow_and_contention_are_visible_later(self):
    class Queue:
      def __init__(self):
        self.items, self.full = [], False
      def enqueue(self, source, item):
        if self.full:
          return False
        self.items.append(item)
        return True
    queue, clock = Queue(), Clock()
    sampled = observer._Observer("controls.torque", queue, clock_ns=clock)
    observer.observe(sampled, lambda: (_ for _ in ()).throw(RuntimeError("synthetic factory failure")))
    clock.now += 1_000_000_000
    queue.full = True
    observer.observe(sampled, lambda: payload())
    sampled.lock.acquire()
    observer.observe(sampled, lambda: payload())
    sampled.lock.release()
    clock.now += 1_000_000_000
    queue.full = False
    observer.observe(sampled, lambda: payload())
    self.assertEqual(3, sampled.dropped)
    self.assertEqual(3, queue.items[-1]["dropped_events"])
    self.assertEqual(3, queue.items[-1]["sequence"])

  def test_socket_rebinding_reconnects_after_transport_failure(self):
    sinks = []
    class ReconnectingSink(Sink):
      def __init__(self, path):
        super().__init__()
        self.fail = not sinks
        sinks.append(self)
      def send(self, value):
        if self.fail:
          raise ConnectionRefusedError("synthetic stale AF_UNIX peer")
        super().send(value)
    with mock.patch.object(observer, "_SocketSender", ReconnectingSink):
      pump = observer._Pump("synthetic-private-socket", identity={"boot_id": BOOT, "pid": 123, "process_instance_id": "c" * 32}, hash_provider=hashes)
      self.addCleanup(pump.close)
      clock = Clock()
      sampled = observer._Observer("controls.torque", pump, clock_ns=clock)
      observer.observe(sampled, lambda: payload())
      wait_for(lambda: sampled.dropped == 1)
      self.assertTrue(sinks[0].closed)
      clock.now += 1_000_000_000
      observer.observe(sampled, lambda: payload())
      wait_for(lambda: len(sinks) == 2 and len(sinks[1].bytes) == 1)
      event = json.loads(sinks[1].bytes[0])
      self.assertEqual(1, event["dropped_events"])

  def test_protocol_rejects_unbounded_values_arbitrary_keys_and_future_reads(self):
    pump, sink = self.pump()
    clock = Clock()
    sampled = observer._Observer("controls.torque", pump, clock_ns=clock)
    observer.observe(sampled, lambda: payload(stamp=clock.now))
    event = json.loads(wait_for(lambda: sink.bytes and sink.bytes[0]))
    for value in (float("nan"), "secret", list(range(17)), [[1]]):
      with self.subTest(value=value):
        changed = json.loads(json.dumps(event))
        changed["fields"]["LateralTorqueFriction"]["effective_value"] = value
        with self.assertRaises(ValueError):
          observer.validate_event(changed)
    changed = json.loads(json.dumps(event))
    changed["context"]["private_path"] = "must never pass"
    with self.assertRaises(ValueError):
      observer.validate_event(changed)
    changed = json.loads(json.dumps(event))
    changed["fields"]["LateralTorqueFriction"]["read_mono_ns"] = event["produced_mono_ns"] + 1
    with self.assertRaises(ValueError):
      observer.validate_event(changed)

  def test_registry_covers_all_47_parameters_and_uses_existing_source_paths(self):
    registry = observer.load_consumer_registry()
    parameters = load_registry()["items"]
    self.assertEqual({item["key"] for item in parameters}, {item["key"] for item in registry["parameters"]})
    self.assertEqual(47, len(registry["parameters"]))
    self.assertEqual(10, len(registry["consumers"]))
    root = Path(__file__).parents[5]
    for item in registry["parameters"]:
      self.assertEqual(observer.consumer_ids_for_key(item["key"]), [binding["consumer_id"] for binding in item["consumers"]])
      self.assertTrue(all((root / binding["source_path"]).is_file() for binding in item["consumers"]))
      self.assertTrue(all(binding["consumer_application_ack"] is False for binding in item["consumers"]))
    self.assertEqual([], observer.consumer_ids_for_key("not-a-setting"))


if __name__ == "__main__":
  unittest.main()
