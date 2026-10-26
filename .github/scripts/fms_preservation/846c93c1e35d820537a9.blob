"""Complete controller-cycle observations and durable references, not vehicle tuning."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import pid_burst as burst
from openpilot.selfdrive.carrot.control_advisor import consumer_observer as observer
from openpilot.selfdrive.carrot.control_advisor.consumer_collector import ConsumerCollector
from openpilot.selfdrive.carrot.control_advisor.settings_history import KEYS, SettingsRecorder, load_history


BOOT = "00000000-0000-0000-0000-000000000001"
MODE = ("linear_standard", 1, False, False, True, 1.0, -1.0, "stored", "stored")
IDENTITY = {"identity_version": 1, "state": "observed", "reason": None, "config_sha256": "a" * 64,
            "schema_sha256": "b" * 64, "raw_sha256": "c" * 64, "vin_sha256": None,
            "car_fingerprint": "synthetic-test-configuration", "physical_group_id": "d" * 32,
            "physical_group_basis": "local_confirmation"}


def values(frame=0):
  return (.2, .15, .21, .16, 100., 15., .1, 0., .01, .02, .04, .001,
          .004, .00102, .04, .04502, .2, .1, 1., 3., .05, 0, 0, 0, 0)


def wire(body):
  result = {"schema_version": 1, "event_kind": "pid_burst_chunk", "consumer_id": "controls.torque",
            "boot_id": BOOT, "pid": 123, "process_instance_id": "1" * 32, "consumer_instance_id": "2" * 32,
            "burst_id": f"{body['round_id'] * burst.BLOCKS_PER_ROUND + body['block_id'] + 1:032x}",
            "produced_mono_ns": body["first_mono_ns"] + (body["rows"][-1][1] if body["rows"] else 0),
            "source_hashes": {path: "e" * 64 for path in burst.SOURCE_PATHS},
            "identity_version": 1, "vehicle_identity": deepcopy(IDENTITY), **deepcopy(body)}
  result["dependency_digest"] = burst.dependency_digest(result["configuration"], result["vehicle_identity"], result["source_hashes"])
  return burst.validate_chunk(result)


def drain(buffer):
  output = []
  while (item := buffer.drain()) is not None:
    output.append(wire(item))
  return output


def complete_blocks(seconds=180):
  buffer, output = burst.PidBurstBuffer(), []
  origin = 100_000_000_000
  for frame in range(seconds * 100):
    if buffer.capture(frame, origin + frame * burst.STEP_NS, values(frame), MODE):
      output.extend(drain(buffer))
  output.extend(drain(buffer))
  return output


def wait_for(predicate, timeout=3):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(.002)
  raise AssertionError("bounded PID observation operation did not finish")


class PidBurstTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.events = complete_blocks()

  def test_six_complete_blocks_and_same_round_row_budget(self):
    blocks = burst.assemble_blocks(self.events)
    self.assertEqual(6, len(blocks))
    self.assertEqual(6000, sum(len(item["rows"]) for item in blocks))
    self.assertEqual(list(range(6)), [item["block_id"] for item in blocks])
    for block in blocks:
      self.assertEqual("complete", block["state"])
      self.assertEqual(1000, block["source_row_count"])
      self.assertFalse(block["consumer_application_ack"])
      self.assertFalse(block["causal_improvement_proven"])
      self.assertEqual(list(values()), [block["rows"][0][name] for name in burst.COLUMNS])

  def test_periodic_capture_and_arbitrary_three_five_minute_phase(self):
    blocks = burst.assemble_blocks(complete_blocks(540))
    self.assertEqual(18, len(blocks))
    origin = blocks[0]["rows"][0]["mono_ns"]
    for window_seconds in (180, 300):
      for offset in range(180):
        for split in range(3):
          lower = origin + (offset + window_seconds * split / 3) * 1e9
          upper = origin + (offset + window_seconds * (split + 1) / 3) * 1e9
          selected = [item for item in blocks if item["state"] == "complete"
                      and lower <= item["rows"][0]["mono_ns"] and item["rows"][-1]["mono_ns"] < upper]
          self.assertTrue(selected, (window_seconds, offset, split))

  def test_ordinary_one_hz_or_static_five_hz_rows_are_not_invented(self):
    buffer = burst.PidBurstBuffer()
    buffer.capture(0, 0, values(), MODE)
    buffer.capture(20, 200_000_000, values(), MODE)
    data = drain(buffer)
    self.assertIn("producer_cycle_gap", burst.assemble_blocks(data)[0]["reason_codes"])
    buffer = burst.PidBurstBuffer()
    buffer.capture(0, 0, values(), MODE)
    buffer.capture(1000, burst.BLOCK_NS, values(), MODE)
    self.assertIn("producer_cycle_gap", burst.assemble_blocks(drain(buffer))[0]["reason_codes"])

  def test_backlog_is_bounded_and_overflow_invalidates_block(self):
    buffer = burst.PidBurstBuffer()
    for frame in range(201):
      buffer.capture(frame, frame * burst.STEP_NS, values(), MODE)
    self.assertEqual(200, buffer.buffered_rows)
    block = burst.assemble_blocks(drain(buffer))[0]
    self.assertEqual("invalid", block["state"])
    self.assertIn("producer_buffer_full", block["reason_codes"])
    self.assertIn("pid_burst_recorded_drop", block["reason_codes"])

  def test_stopped_pump_has_bounded_terminal_metadata_across_rounds(self):
    buffer = burst.PidBurstBuffer()
    for frame in range(201):
      buffer.capture(frame, frame * burst.STEP_NS, values(), MODE)
    for block in range(1, 1000):
      frame = block * 3000
      buffer.capture(frame, block * burst.BLOCK_SPACING_NS, values(), MODE)
    self.assertEqual(200, buffer.buffered_rows)
    self.assertLessEqual(len(buffer.queue), burst.MAX_BUFFER_ROWS + 1)
    self.assertTrue(all(item["state"] == "invalid" for item in burst.assemble_blocks(drain(buffer))))

  def test_lock_contention_and_time_regression_fail_closed(self):
    for failure in ("lock", "time"):
      buffer = burst.PidBurstBuffer()
      buffer.capture(0, 10_000_000, values(), MODE)
      if failure == "lock":
        with buffer.lock:
          self.assertFalse(buffer.capture(1, 20_000_000, values(), MODE))
        buffer.capture(2, 30_000_000, values(), MODE)
      else:
        buffer.capture(1, 9_000_000, values(), MODE)
      self.assertEqual("invalid", burst.assemble_blocks(drain(buffer))[0]["state"])

  def test_initial_inherited_coincidence_is_not_stored_use(self):
    buffer = burst.PidBurstBuffer()
    self.assertFalse(buffer.capture(0, 0, values(), MODE[:-2] + ("inherited", "inherited")))
    self.assertEqual([], drain(buffer))
    event = deepcopy(self.events[0])
    event["configuration"]["pid_source"] = "inherited"
    event["dependency_digest"] = burst.dependency_digest(event["configuration"], event["vehicle_identity"], event["source_hashes"])
    with self.assertRaisesRegex(ValueError, "unverified"):
      burst.validate_header({key: event[key] for key in burst.HEADER_FIELDS}, require_verified=True)

  def test_configuration_change_is_observed_without_changing_control_values(self):
    buffer = burst.PidBurstBuffer()
    buffer.capture(0, 0, values(), MODE)
    changed = list(values())
    changed[16] = .3
    buffer.capture(1, burst.STEP_NS, tuple(changed), MODE)
    self.assertIn("producer_dependency_changed", burst.assemble_blocks(drain(buffer))[0]["reason_codes"])

  def test_missing_duplicate_reordered_and_gapped_chunks_are_not_usable(self):
    block_events = [item for item in self.events if item["block_id"] == 0]
    for case in (block_events[:-1], block_events[:1] + block_events[:1] + block_events[1:],
                 block_events[1:2] + block_events[:1] + block_events[2:], block_events[:2] + block_events[3:]):
      self.assertEqual("invalid", burst.assemble_blocks(case)[0]["state"])
    prefix = burst.assemble_blocks(block_events[1:])[0]
    self.assertTrue(prefix["missing_prefix"])
    suffix = burst.assemble_blocks(block_events[1:], allow_subtrace=True)[0]
    self.assertEqual("complete", suffix["state"])
    self.assertTrue(suffix["missing_prefix"])

  def test_changed_cp_and_source_code_do_not_mix_in_one_block(self):
    for field in ("vehicle_identity", "source_hashes"):
      events = deepcopy([item for item in self.events if item["block_id"] == 0])
      if field == "vehicle_identity":
        events[1][field]["config_sha256"] = "f" * 64
      else:
        events[1][field][burst.SOURCE_PATHS[-1]] = "f" * 64
      events[1]["dependency_digest"] = burst.dependency_digest(events[1]["configuration"], events[1]["vehicle_identity"], events[1]["source_hashes"])
      self.assertIn("pid_burst_header_changed", burst.assemble_blocks(events)[0]["reason_codes"])

  def test_strict_numeric_shape_flags_digest_and_bytes(self):
    for mutate in (lambda value: value["rows"][0].__setitem__(2, float("nan")),
                   lambda value: value["rows"][0].__setitem__(-1, True),
                   lambda value: value.__setitem__("dependency_digest", "0" * 64),
                   lambda value: value.__setitem__("step_ns", 200_000_000),
                   lambda value: value["rows"].append(deepcopy(value["rows"][-1]))):
      value = deepcopy(self.events[0])
      mutate(value)
      with self.assertRaises((ValueError, TypeError)):
        burst.validate_chunk(value)
    self.assertTrue(all(len(burst.canonical(item)) <= burst.MAX_CHUNK_BYTES for item in self.events))

  def test_off_and_hot_path_have_no_factory_serialization_or_identity_io(self):
    class Poison:
      def __len__(self):
        raise AssertionError("OFF inspected payload")
    with patch.object(observer, "_native_runtime", side_effect=AssertionError("OFF identity probe")), \
         patch.dict(os.environ, {observer.ENV_ENABLED: "0"}):
      self.assertIsNone(observer.create_observer("controls.torque"))
      observer.observe_pid_burst(None, Poison(), Poison(), cycle_id=0)
    buffer = burst.PidBurstBuffer()
    with patch.object(burst, "canonical", side_effect=AssertionError("control serialization")), \
         patch.object(hashlib, "sha256", side_effect=AssertionError("control hash")):
      for frame in range(16):
        buffer.capture(frame, frame * burst.STEP_NS, values(), MODE)

  def test_failure_in_diagnostic_queue_does_not_propagate(self):
    class Reject:
      def enqueue(self, owner, item):
        raise RuntimeError("collector disconnected")
    clock = lambda: 1_000_000_000
    owner = observer._Observer("controls.torque", Reject(), clock_ns=clock)
    for frame in range(8):
      owner.clock_ns = lambda frame=frame: frame * burst.STEP_NS
      observer.observe_pid_burst(owner, values(), MODE, cycle_id=frame)
    self.assertEqual("invalid", burst.assemble_blocks(drain(owner._pid_burst))[0]["state"])

  def test_immutable_store_round_budget_hash_and_journal_reference(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      store = burst.PidBurstStore(root)
      event = self.events[0]
      reference = store.append(event)
      self.assertEqual(event, burst.load_chunk(root, reference))
      with self.assertRaises(FileExistsError):
        store.append(event)
      with patch.object(burst, "MAX_ROUND_BYTES", 1):
        with self.assertRaisesRegex(ValueError, "storage_budget"):
          burst.PidBurstStore(root).append(self.events[1])
      target = root / reference["path"]
      target.write_bytes(b"changed")
      with self.assertRaisesRegex(ValueError, "hash_mismatch"):
        burst.load_chunk(root, reference)

  def test_v1_and_segmented_journals_keep_burst_refs_out_of_latest_consumer_ack(self):
    for journal_format in ("v1", "segmented"):
      with self.subTest(format=journal_format), tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        settings, session = root / "settings", root / "session"
        settings.mkdir()
        session.mkdir()
        for key in KEYS:
          (settings / key).write_text("0", encoding="ascii")
        recorder = SettingsRecorder(settings, session, "pid-session", journal_format=journal_format,
                                    clock_ns=lambda: 200_000_000_000)
        recorder.sample()
        self.assertTrue(recorder.append_pid_burst_chunk(self.events[0]))
        token = recorder.checkpoint()
        recorder.close()
        history = load_history(session if journal_format == "segmented" else session / "settings.jsonl", include_records=True)
        self.assertEqual("complete", history["state"])
        self.assertEqual(0, history["consumer_events"])
        self.assertEqual({}, history["latest_consumers"])
        record = next(item["record"] for item in history["records"] if item["record"]["type"] == "pid_burst_chunk")
        self.assertNotIn("settings_revision", record)
        self.assertEqual(self.events[0], burst.load_chunk(session, record["reference"]))
        self.assertTrue(token["durable"])

  def test_actual_async_pump_and_collector_preserve_separate_event_kind(self):
    class Sink:
      def __init__(self):
        self.values = []
      def send(self, value):
        self.values.append(value)
      def close(self):
        pass
    class Transport:
      def __init__(self, values):
        self.values = deque(values)
      def receive(self, maximum):
        return self.values.popleft() if self.values else None
      def close(self):
        pass
    from collections import deque
    sink, clock = Sink(), [100_000_000_000]
    pump = observer._Pump(sender=sink, identity={"boot_id": BOOT, "pid": 123, "process_instance_id": "1" * 32},
                          hash_provider=lambda consumer: {path: "e" * 64 for path in burst.SOURCE_PATHS})
    self.addCleanup(pump.close)
    owner = observer._Observer("controls.torque", pump, clock_ns=lambda: clock[0])
    owner.cp_requested, owner.vehicle_identity = True, deepcopy(IDENTITY)
    for frame in range(1000):
      clock[0] = 100_000_000_000 + frame * burst.STEP_NS
      observer.observe_pid_burst(owner, values(frame), MODE, cycle_id=frame)
      if frame % 8 == 7:
        wait_for(lambda count=(frame + 1) // 8: len(sink.values) >= count)
    wait_for(lambda: len(sink.values) == 125)
    collector = ConsumerCollector(source_mode="synthetic", injected_transport=Transport(sink.values), expected_boot_id=BOOT,
                                  clock_ns=lambda: clock[0], max_age_ns=20_000_000_000, poll_budget_ns=1_000_000_000)
    self.addCleanup(collector.close)
    events = collector.poll()
    self.assertEqual(125, len(events))
    self.assertEqual(0, collector.received_events)
    self.assertEqual([], [entry for entry in collector.snapshot()["consumers"] if entry["latest_event"] is not None])
    block = burst.assemble_blocks(events)[0]
    self.assertEqual("complete", block["state"])
    self.assertEqual(1000, len(block["rows"]))
    from openpilot.selfdrive.carrot.control_advisor.recording import RecordingSession
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      settings, logs, evidence = (root / name for name in ("settings", "logs", "evidence"))
      for path in (settings, logs, evidence):
        path.mkdir()
      for key in KEYS:
        (settings / key).write_text("0", encoding="ascii")
      before = {path.name: path.read_bytes() for path in settings.iterdir()}
      environment = {"CARROT_CONTROL_ADVISOR_DEMO_" + name: str(path)
                     for name, path in zip(("SETTINGS_DIR", "LOG_ROOT", "EVIDENCE_ROOT"), (settings, logs, evidence))}
      recording = RecordingSession("pid-pipeline", source_mode="synthetic", environment=environment,
                                   source_clock_ns=lambda: clock[0], poll_seconds=.05, archive_poll_seconds=.05).start()
      self.addCleanup(recording.close)
      wait_for(lambda: recording._journal_ready)
      for first in range(0, len(events), 8):
        batch = events[first:first + 8]
        self.assertEqual(len(batch), recording.submit_consumer_observations(batch))
        wait_for(lambda: recording._pending_pid_burst_events == 0)
      status = recording.close()
      self.assertEqual(0, recording._pid_burst_rejections)
      self.assertEqual(0, status["consumer_history"]["recorded_events"])
      session = evidence / "pid-pipeline"
      history = load_history(session / "settings.jsonl", include_records=True)
      self.assertEqual("complete", history["state"])
      references = [item["record"]["reference"] for item in history["records"] if item["record"]["type"] == "pid_burst_chunk"]
      self.assertEqual(125, len(references))
      restored = burst.assemble_blocks([{"event": burst.load_chunk(session, reference), "reference": reference}
                                       for reference in references])[0]
      self.assertEqual("complete", restored["state"])
      self.assertEqual(block["rows"], restored["rows"])
      self.assertEqual(125, len(restored["chunk_refs"]))
      self.assertEqual(before, {path.name: path.read_bytes() for path in settings.iterdir()})


if __name__ == "__main__":
  unittest.main()
