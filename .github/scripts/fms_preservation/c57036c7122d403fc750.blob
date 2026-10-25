"""Actual producer bytes and durable recording, without a native IPC claim."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.consumer_observer import (
  CONSUMER_PATHS, OBSERVER_PATH, _Observer, _Pump, field, observe, validate_event,
)
from openpilot.selfdrive.carrot.control_advisor.inspection import inspect_session
from openpilot.selfdrive.carrot.control_advisor.recording import RecordingSession
from openpilot.selfdrive.carrot.control_advisor.settings_history import KEYS, SettingsRecorder, load_history


BOOT = "00000000-0000-0000-0000-000000000001"


class ByteSink:
  def __init__(self):
    self.bytes, self.ready = [], threading.Event()

  def send(self, data):
    self.bytes.append(data)
    self.ready.set()

  def close(self):
    pass


def produced_event():
  sink = ByteSink()
  pump = _Pump(sender=sink, identity={"boot_id": BOOT, "pid": os.getpid(), "process_instance_id": "1" * 32},
               hash_provider=lambda consumer: {path: "a" * 64 for path in (OBSERVER_PATH, CONSUMER_PATHS[consumer])})
  try:
    observer = _Observer("controls.vehicle_model", pump, clock_ns=lambda: 2_000_000_000)
    observe(observer, lambda: {"fields": {
      "SteerRatioRate": field(1.0, read_value=1.0, source="stored", read_mono_ns=1_999_000_000),
      "CustomSR": field(None, read_value=0.0, state="bypassed", source="stored")},
      "context": {"resolved_steer_ratio": 15.0, "manual_override": False}}, cycle_id=42)
    if not sink.ready.wait(2):
      raise AssertionError("actual producer did not serialize a bounded observation")
    return validate_event(json.loads(sink.bytes[0])), sink.bytes[0]
  finally:
    pump.close()


def wait_for(predicate):
  deadline = time.monotonic() + 3
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(.01)
  raise AssertionError("consumer recording did not reach its bounded completion condition")


class ConsumerRecordingTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.root = Path(self.directory.name)
    self.settings, self.logs, self.evidence = (self.root / name for name in ("settings", "logs", "evidence"))
    for path in (self.settings, self.logs, self.evidence):
      path.mkdir()
    for key in KEYS:
      (self.settings / key).write_text("0", encoding="ascii")
    self.environment = {"CARROT_CONTROL_ADVISOR_DEMO_" + name: str(path) for name, path in zip(
      ("SETTINGS_DIR", "LOG_ROOT", "EVIDENCE_ROOT"), (self.settings, self.logs, self.evidence))}

  def test_actual_serialized_event_is_preserved_without_inventing_applied_settings_revision(self):
    event, encoded = produced_event()
    self.assertLessEqual(len(encoded), 16 * 1024)
    session = self.evidence / "direct"
    session.mkdir()
    (session / "recording.json").write_text(json.dumps({"session_id": "direct", "source_mode": "synthetic"}))
    recorder = SettingsRecorder(self.settings, session, "direct", clock_ns=lambda: 3_000_000_000)
    self.addCleanup(recorder.close)
    recorder.sample()
    recorder.append_consumer_observation(event)
    recorder.close()
    before = (session / "settings.jsonl").read_bytes()
    history = load_history(session / "settings.jsonl")
    self.assertEqual(1, history["consumer_events"])
    self.assertEqual(event, history["latest_consumers"]["controls.vehicle_model"])
    record = next(json.loads(line)["record"] for line in before.splitlines()
                  if json.loads(line)["record"]["type"] == "consumer_observation")
    self.assertNotIn("settings_revision", record)
    self.assertEqual(2_000_000_000, record["event"]["produced_mono_ns"])
    self.assertEqual(3_000_000_000, record["recorded_mono_ns"])
    report = inspect_session(session)
    self.assertEqual("verified", report["state"])
    self.assertFalse(report["consumer_observations"]["consumer_application_ack"])
    self.assertTrue(report["consumer_observations"]["historical_only"])
    self.assertEqual(before, (session / "settings.jsonl").read_bytes())

  def test_real_io_threads_record_consumer_event_and_keep_settings_sources_unchanged(self):
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.settings.iterdir()}
    recorder = RecordingSession("threads", source_mode="synthetic", environment=self.environment).start()
    self.addCleanup(recorder.close)
    wait_for(lambda: recorder.snapshot()["settings"]["durable_revision"] > 0)
    event, _ = produced_event()
    self.assertEqual(1, recorder.submit_consumer_observations([event]))
    wait_for(lambda: recorder.snapshot()["consumer_history"]["recorded_events"] == 1)
    self.assertEqual("recorded", recorder.snapshot()["consumer_history"]["state"])
    recorder.close(timeout=2)
    history = load_history(self.evidence / "threads" / "settings.jsonl")
    self.assertTrue(history["closed"])
    self.assertEqual(1, history["consumer_events"])
    self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.settings.iterdir()})

  def test_invalid_event_is_dropped_without_stopping_setting_observation(self):
    recorder = RecordingSession("invalid", source_mode="synthetic", environment=self.environment, poll_seconds=.02).start()
    self.addCleanup(recorder.close)
    wait_for(lambda: recorder.snapshot()["settings"]["durable_revision"] > 0)
    poll = recorder.snapshot()["settings"]["poll_sequence"]
    self.assertEqual(1, recorder.submit_consumer_observations([{"arbitrary": "not a valid event"}]))
    wait_for(lambda: recorder.snapshot()["consumer_history"]["dropped_events"] == 1)
    wait_for(lambda: recorder.snapshot()["settings"]["poll_sequence"] > poll)
    self.assertEqual("partial", recorder.snapshot()["consumer_history"]["state"])
    self.assertEqual(0, recorder.snapshot()["consumer_history"]["recorded_events"])

  def test_bounded_pending_queue_never_claims_durable_consumer_history(self):
    recorder = RecordingSession("pending", source_mode="synthetic", environment=self.environment)
    self.addCleanup(recorder.close)
    event, _ = produced_event()
    self.assertEqual(32, recorder.submit_consumer_observations([event] * 33))
    status = recorder.close()["consumer_history"]
    self.assertEqual({"state": "partial", "recorded_events": 0, "dropped_events": 1}, status)
    self.assertFalse((self.evidence / "pending").exists())

  def test_consumer_budget_preserves_settings_writer_and_durable_partial_status(self):
    real_recorder = SettingsRecorder
    def limited(*args, **kwargs):
      return real_recorder(*args, **kwargs, max_journal_bytes=128 * 1024, max_events=20)
    with patch('openpilot.selfdrive.carrot.control_advisor.settings_history.SettingsRecorder', side_effect=limited):
      recorder = RecordingSession('budget', source_mode='synthetic', environment=self.environment, poll_seconds=.02).start()
      self.addCleanup(recorder.close)
      wait_for(lambda: recorder.snapshot()['settings']['durable_revision'] > 0)
      event, _ = produced_event()
      recorder.submit_consumer_observations([event] * 15)
      wait_for(lambda: recorder.snapshot()['consumer_history']['dropped_events'] > 0)
      (self.settings / 'SteerRatioRate').write_text('120', encoding='ascii')
      wait_for(lambda: recorder.snapshot()['settings']['durable_revision'] > 1)
      status = recorder.close(timeout=2)
    self.assertEqual('partial', status['consumer_history']['state'])
    self.assertEqual('closed', status['settings']['state'])
    self.assertEqual(10, status['consumer_history']['recorded_events'])
    self.assertEqual(5, status['consumer_history']['dropped_events'])
    history = load_history(self.evidence / 'budget/settings.jsonl')
    self.assertTrue(history['closed'])
    self.assertEqual(5, history['last_record']['consumer_budget_rejections'])
    self.assertEqual(5, inspect_session(self.evidence / 'budget')['consumer_observations']['budget_rejected_events'])

  def test_consumer_byte_allowance_never_fails_the_existing_writer(self):
    session = self.evidence / 'byte-budget'
    session.mkdir()
    recorder = SettingsRecorder(self.settings, session, 'byte-budget', max_journal_bytes=128 * 1024)
    self.addCleanup(recorder.close)
    recorder.sample()
    event, _ = produced_event()
    for _ in range(100):
      if not recorder.append_consumer_observation(event):
        break
    else:
      self.fail('consumer byte cap was not enforced')
    self.assertLessEqual(recorder._consumer_bytes, 64 * 1024)
    self.assertNotEqual('failed', recorder.snapshot()['state'])
    (self.settings / 'SteerRatioRate').write_text('121', encoding='ascii')
    self.assertEqual(2, recorder.sample()['durable_revision'])
    self.assertEqual('closed', recorder.close()['state'])
    self.assertTrue(load_history(session / 'settings.jsonl')['closed'])


if __name__ == "__main__":
  unittest.main()
