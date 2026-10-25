"""Synthetic real-byte receiver, identity, freshness and replay tests."""

from collections import deque
from copy import deepcopy
import json
import os
import time
import unittest
from unittest import mock

from openpilot.selfdrive.carrot.control_advisor import consumer_observer as producer
from openpilot.selfdrive.carrot.control_advisor import consumer_collector as receiver


BOOT = "00000000-0000-0000-0000-000000000001"


class Clock:
  def __init__(self):
    self.now = 10_000_000_000

  def __call__(self):
    return self.now


class BytesTransport:
  credentials_required = False

  def __init__(self, packets=()):
    self.packets, self.closed = deque(packets), False

  def receive(self, max_bytes):
    return self.packets.popleft() if self.packets else None

  def close(self):
    self.closed = True


class CollectorTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    clock, captured = Clock(), []
    class Sink:
      def send(self, value):
        captured.append(value)
      def close(self):
        pass
    pump = producer._Pump(sender=Sink(), identity={"boot_id": BOOT, "pid": 123, "process_instance_id": "c" * 32},
                          hash_provider=lambda cid: {producer.OBSERVER_PATH: "a" * 64, producer.CONSUMER_PATHS[cid]: "b" * 64})
    sampled = producer._Observer("controls.torque", pump, clock_ns=clock)
    producer.observe(sampled, lambda: {"fields": {"LateralTorqueFriction": producer.field(0.0, read_value=0.0,
                     source="stored", read_mono_ns=clock.now)}, "context": {"active": True}})
    deadline = time.monotonic() + 2
    while not captured and time.monotonic() < deadline:
      time.sleep(0.005)
    pump.close()
    if not captured:
      raise AssertionError("real producer did not encode synthetic fixture")
    cls.base = producer.validate_event(json.loads(captured[0]))

  def setUp(self):
    self.clock = Clock()

  def event(self, **changes):
    result = deepcopy(self.base)
    result.update(changes)
    return result

  def collector(self, packets=(), **options):
    transport = BytesTransport(packets)
    consumer = receiver.ConsumerCollector(source_mode="synthetic", transport=transport, expected_boot_id=BOOT,
                                          clock_ns=self.clock, poll_budget_ns=100_000_000, **options)
    self.addCleanup(consumer.close)
    return consumer, transport

  def encoded(self, **changes):
    return json.dumps(self.event(**changes), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

  def entry(self, collector):
    return next(item for item in collector.snapshot()["consumers"] if item["consumer_id"] == "controls.torque")

  def test_real_producer_bytes_become_canonical_events_without_application_ack(self):
    consumer, _ = self.collector([self.encoded()])
    events = consumer.poll()
    self.assertEqual([self.base], events)
    status = consumer.snapshot()
    self.assertEqual("observed", status["state"])
    self.assertEqual(10, len(status["consumers"]))
    self.assertFalse(status["consumer_application_ack"])
    self.assertFalse(status["device_qualified"])
    entry = self.entry(consumer)
    self.assertTrue(entry["fresh"])
    self.assertTrue(entry["read_freshness"]["LateralTorqueFriction"])
    self.assertEqual(0.0, entry["latest_event"]["fields"]["LateralTorqueFriction"]["effective_value"])

  def test_old_constructor_read_does_not_make_current_object_observation_stale(self):
    event = self.event()
    event["fields"]["LateralTorqueFriction"].update(state="latched", read_mono_ns=1, source="inherited")
    consumer, _ = self.collector([json.dumps(event).encode()])
    consumer.poll()
    entry = self.entry(consumer)
    self.assertTrue(entry["fresh"])
    self.assertFalse(entry["read_freshness"]["LateralTorqueFriction"])
    self.assertEqual("latched", entry["latest_event"]["fields"]["LateralTorqueFriction"]["state"])

  def test_stale_samples_stay_in_history_but_never_look_current(self):
    consumer, _ = self.collector([self.encoded()])
    consumer.poll()
    self.clock.now += 3_000_000_000
    entry = self.entry(consumer)
    self.assertEqual("stale", entry["state"])
    self.assertFalse(entry["fresh"])
    self.assertIsNotNone(entry["latest_event"])
    self.assertFalse(entry["read_freshness"]["LateralTorqueFriction"])

  def test_wrong_boot_future_old_and_duplicate_sequence_are_rejected(self):
    packets = [self.encoded(), self.encoded(),
               self.encoded(sequence=2, boot_id="00000000-0000-0000-0000-000000000002"),
               self.encoded(sequence=2, produced_mono_ns=self.clock.now + 250_000_001)]
    consumer, _ = self.collector(packets)
    self.assertEqual(1, len(consumer.poll()))
    self.assertEqual(3, consumer.rejected_events)
    self.clock.now += 3_000_000_000
    consumer.transport.packets.append(self.encoded(sequence=2))
    self.assertEqual([], consumer.poll())
    self.assertEqual(4, consumer.rejected_events)

  def test_gap_and_restart_require_a_subsequent_contiguous_sample(self):
    consumer, transport = self.collector([self.encoded()])
    consumer.poll()
    transport.packets.append(self.encoded(sequence=3, dropped_events=2))
    consumer.poll()
    self.assertEqual("gap", self.entry(consumer)["state"])
    self.assertFalse(self.entry(consumer)["fresh"])
    self.assertEqual(2, consumer.dropped_events)
    transport.packets.append(self.encoded(sequence=4, dropped_events=2))
    consumer.poll()
    self.assertTrue(self.entry(consumer)["fresh"])
    self.clock.now += 100_000_000
    transport.packets.append(self.encoded(pid=124, process_instance_id="d" * 32, consumer_instance_id="e" * 32,
                                         sequence=1, produced_mono_ns=self.clock.now))
    consumer.poll()
    self.assertEqual("restarted", self.entry(consumer)["state"])
    self.assertFalse(self.entry(consumer)["fresh"])
    self.assertEqual(1, consumer.restarts)
    self.clock.now += 100_000_000
    transport.packets.extend([self.encoded(sequence=5, produced_mono_ns=self.clock.now),
                              self.encoded(pid=124, process_instance_id="d" * 32, consumer_instance_id="e" * 32,
                                           sequence=2, produced_mono_ns=self.clock.now)])
    self.assertEqual(1, len(consumer.poll()))
    self.assertEqual(1, consumer.rejected_events)
    self.assertTrue(self.entry(consumer)["fresh"])

  def test_oversize_duplicate_json_and_bad_payloads_fail_before_acceptance(self):
    duplicate = self.encoded().replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')
    invalid = self.event()
    invalid["context"]["internal_path"] = "not allowed"
    consumer, _ = self.collector([b"x" * (producer.MAX_EVENT_BYTES + 1), duplicate, json.dumps(invalid).encode(), b"[]"])
    self.assertEqual([], consumer.poll())
    self.assertEqual(4, consumer.rejected_events)
    self.assertTrue(all(item["latest_event"] is None for item in consumer.snapshot()["consumers"]))

  def test_linux_credential_pid_and_uid_must_match_payload_when_available(self):
    consumer, transport = self.collector([(self.encoded(), {"pid": 999, "uid": 1000}),
                                         (self.encoded(), {"pid": 123, "uid": 1000})])
    transport.credentials_required = True
    with mock.patch.object(receiver.os, "getuid", create=True, return_value=1000):
      self.assertEqual(1, len(consumer.poll()))
    self.assertEqual(1, consumer.rejected_events)

  def test_disabled_and_synthetic_modes_do_not_probe_native_paths(self):
    with mock.patch.object(receiver, "_UnixReceiver", side_effect=AssertionError("OFF socket probe")), \
         mock.patch.object(receiver, "_read_boot_id", side_effect=AssertionError("OFF boot probe")):
      disabled = receiver.ConsumerCollector(environment={})
      self.assertEqual("disabled", disabled.snapshot()["state"])
      disabled.close()
      self.assertEqual("disabled", disabled.snapshot()["state"])
      synthetic = receiver.ConsumerCollector(source_mode="synthetic", environment={producer.ENV_ENABLED: "1", producer.ENV_SOCKET: "/real/socket"})
      self.assertEqual("disabled", synthetic.snapshot()["state"])
    with self.assertRaises(ValueError):
      receiver.ConsumerCollector(source_mode="live", transport=BytesTransport(), expected_boot_id=BOOT)

  def test_native_unavailable_and_poll_fault_survive_close_without_hiding_reason(self):
    with mock.patch.object(receiver, "_UnixReceiver", side_effect=RuntimeError("synthetic AF_UNIX missing")):
      unavailable = receiver.ConsumerCollector(environment={producer.ENV_ENABLED: "1", producer.ENV_SOCKET: "/private/socket"})
    self.assertEqual("native_consumer_transport_unavailable", unavailable.snapshot()["reason"])
    unavailable.close()
    self.assertEqual("unavailable", unavailable.snapshot()["state"])
    self.assertEqual("native_consumer_transport_unavailable", unavailable.snapshot()["reason"])
    consumer, transport = self.collector([self.encoded()])
    consumer.poll()
    with mock.patch.object(transport, "receive", side_effect=OSError("synthetic IPC fault")):
      self.assertEqual([], consumer.poll())
    self.assertEqual("unavailable", consumer.snapshot()["state"])
    self.assertFalse(self.entry(consumer)["fresh"])
    consumer.close()
    consumer.close()
    self.assertEqual("unavailable", consumer.snapshot()["state"])
    self.assertTrue(transport.closed)

  def test_existing_receiver_socket_has_a_distinct_non_destructive_recovery_reason(self):
    with mock.patch.object(receiver, "_UnixReceiver", side_effect=ValueError("consumer_socket_already_exists")), \
         mock.patch("pathlib.Path.unlink", side_effect=AssertionError("must not unlink another receiver")):
      consumer = receiver.ConsumerCollector(environment={producer.ENV_ENABLED: "1", producer.ENV_SOCKET: "/private/socket"})
      self.assertEqual("unavailable", consumer.snapshot()["state"])
      self.assertEqual("consumer_socket_already_exists", consumer.snapshot()["reason"])
      consumer.close()
      self.assertEqual("consumer_socket_already_exists", consumer.snapshot()["reason"])

  def test_poll_count_budget_retains_remaining_datagrams(self):
    consumer, transport = self.collector([self.encoded(sequence=index) for index in range(1, 11)], max_events=3)
    self.assertEqual(3, len(consumer.poll()))
    self.assertEqual(7, len(transport.packets))
    self.assertEqual(3, len(consumer.poll()))
    self.assertEqual(4, len(transport.packets))

  def test_snapshot_validation_preserves_explicit_false_flags_and_rejects_forgery(self):
    consumer, _ = self.collector([self.encoded()])
    consumer.poll()
    status = consumer.snapshot()
    self.assertEqual(status, receiver.validate_snapshot(status))
    status["consumer_application_ack"] = True
    with self.assertRaises(ValueError):
      receiver.validate_snapshot(status)
    for state in ("stale", "gap", "restarted"):
      status = consumer.snapshot()
      item = next(entry for entry in status["consumers"] if entry["latest_event"] is not None)
      item["state"] = state
      with self.subTest(state=state), self.assertRaises(ValueError):
        receiver.validate_snapshot(status)

  def test_retired_identity_set_is_bounded_and_never_accepts_old_instances(self):
    consumer, transport = self.collector([self.encoded()])
    consumer.poll()
    for index in range(1, 10):
      self.clock.now += 1_000_000_000
      transport.packets.append(self.encoded(pid=123 + index, process_instance_id=f"{index:032x}",
                                           consumer_instance_id=f"{100+index:032x}", sequence=1, produced_mono_ns=self.clock.now))
      consumer.poll()
    self.assertEqual(8, consumer.restarts)
    self.assertEqual(1, consumer.rejected_events)
    self.assertEqual(8, len(consumer._retired["controls.torque"]))


if __name__ == "__main__":
  unittest.main()
