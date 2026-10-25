"""Actual worker, disk settings, closed native logs and persisted inspection."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from openpilot.selfdrive.carrot.control_advisor.inspection import inspect_session
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
from openpilot.selfdrive.carrot.control_advisor.settings_history import load_history
from openpilot.selfdrive.carrot.control_advisor.worker import _synthetic_tick


class RecordingFlowTests(unittest.TestCase):
  def test_actual_worker_observes_change_and_preserves_closed_logs_without_source_writes(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      settings, logs, evidence = (root / name for name in ("settings", "logs", "evidence"))
      for path in (settings, logs, evidence):
        path.mkdir()
      for item in load_registry()["items"]:
        (settings / item["key"]).write_bytes(b"0")
      key = settings / "LateralTorqueAccelFactor"
      key.write_bytes(b"2800")
      route = "00000000--0123456789"
      (settings / "CurrentRoute").write_text(route)
      _, log = _schema()

      def sentinel(stamp, kind):
        event = log.Event.new_message(logMonoTime=stamp, valid=True)
        event.init("sentinel").type = kind
        return event.to_bytes()

      for part in range(6):
        directory = logs / f"{route}--{part}"
        directory.mkdir()
        chunks = [sentinel(1_000_000_000 + part * 100_000_000_000, "startOfSegment")]
        for second in range(part * 100, (part + 1) * 100):
          for item in _synthetic_tick(second * 20):
            event = log.Event.new_message(logMonoTime=item.mono_ns, valid=True)
            event.init(item.service).from_dict(item.data)
            chunks.append(event.to_bytes())
        chunks.append(sentinel(1_000_000_000 + (part + 1) * 100_000_000_000, "endOfSegment"))
        (directory / "rlog").write_bytes(b"".join(chunks))
      before_logs = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in logs.rglob("rlog")}
      before_settings = {path.name: path.read_bytes() for path in settings.iterdir()}
      environment = {name: value for name, value in os.environ.items() if not name.startswith("CARROT_CONTROL_ADVISOR_")}
      environment.update({"CARROT_CONTROL_ADVISOR_DEMO_SETTINGS_DIR": str(settings),
                          "CARROT_CONTROL_ADVISOR_DEMO_LOG_ROOT": str(logs),
                          "CARROT_CONTROL_ADVISOR_DEMO_EVIDENCE_ROOT": str(evidence)})
      command = [sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor.worker", "--mode", "synthetic",
                 "--session-id", "recording-flow", "--interval-seconds", "180"]
      process = subprocess.Popen(command, cwd=Path(__file__).parents[5], env=environment,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
      statuses, failures, changed = [], [], threading.Event()

      def consume():
        try:
          for line in process.stdout:
            status = json.loads(line)
            statuses.append(status)
            if status.get("diagnostic_revision", 0) >= 1 and not changed.is_set():
              replacement = settings / "replacement"
              replacement.write_bytes(b"2900")
              os.replace(replacement, key)
              changed.set()
        except Exception as exc:
          failures.append(repr(exc))

      reader = threading.Thread(target=consume, daemon=True)
      reader.start()
      try:
        self.assertEqual(0, process.wait(timeout=25))
        reader.join(timeout=2)
        self.assertFalse(reader.is_alive())
        self.assertEqual([], failures)
        self.assertEqual(b"", process.stderr.read())
      finally:
        if process.poll() is None:
          process.kill()
          process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
          stream.close()
      self.assertTrue(changed.is_set())
      self.assertTrue(any(item.get("recording", {}).get("settings", {}).get("revision", 0) >= 2 for item in statuses))
      self.assertTrue(any(item.get("recording", {}).get("preservation", {}).get("verified_segments", 0) >= 1 for item in statuses), statuses[-1])
      self.assertTrue(all(not item["apply_enabled"] for item in statuses))
      self.assertEqual(before_logs, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before_logs})
      after_settings = {path.name: path.read_bytes() for path in settings.iterdir()}
      self.assertEqual({**before_settings, key.name: b"2900"}, after_settings)
      session = evidence / "recording-flow"
      history = load_history(session / "settings.jsonl")
      self.assertTrue(history["closed"])
      self.assertGreaterEqual(history["latest_settings"]["revision"], 2)
      report = inspect_session(session)
      self.assertGreaterEqual(report["archives"]["verified_segments"], 1, report)
      self.assertEqual(0, report["archives"]["incomplete_or_invalid_segments"])
      self.assertFalse(report["coverage_complete"])


if __name__ == "__main__":
  unittest.main()
