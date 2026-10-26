"""Explicit request gating around the actual job request/publisher boundaries."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import periodic


class Child:
  def __init__(self):
    self.stdin, self.returncode = io.BytesIO(), None

  def poll(self):
    return self.returncode

  def wait(self, timeout=None):
    self.returncode = 0
    return 0

  def terminate(self):
    self.returncode = -15

  kill = terminate


class ExplicitTriggerTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.session = self.root / "session"
    self.session.mkdir()
    self.profile, self.objectives = self.root / "profile.json", self.root / "objectives.json"
    for path in (self.profile, self.objectives):
      path.write_text("{}")
    self.now = 0
    self.controller = periodic.PeriodicAnalyzer(self.session, self.profile, self.objectives, self.root / "out", clock=lambda: self.now)
    self.addCleanup(self.controller.close)
    self.token = {"schema_version": 1, "session_id": "session", "sequence": 1, "chain_sha256": "a" * 64,
                  "bytes": 1, "cutoff_mono_ns": 1_000_000_000_000, "consumer_budget_rejections": 0, "durable": True}
    self.intent = {"schema_version": 1, "session_id": "session", "request_id": "manual", "trigger": "manual",
      "range_start_ns": 1, "requested_end_ns": 999_000_000_000, "cut_revision": 0,
      "trip_binding": {"vehicle_id": "vehicle", "boot_id": "boot", "route": "route"},
      "profile_sha256": periodic.hash_file(self.profile, 100)[0], "objectives_sha256": periodic.hash_file(self.objectives, 100)[0]}

  def test_arbitrary_timer_polls_and_checkpoint_offers_never_start_a_job(self):
    with patch.object(periodic.subprocess, "Popen", side_effect=AssertionError("timer started analysis")):
      for sequence in range(1, 10):
        self.controller.offer({**self.token, "sequence": sequence})
        self.now += 10000
        self.controller.tick()
    self.assertEqual(list((self.controller.root / "jobs").iterdir()), [])

  def test_only_manual_intent_launches_and_cuts_cancel_without_new_work(self):
    child = Child()
    self.controller.offer(self.token)
    self.controller.request_analysis(self.intent)
    with patch.object(periodic.subprocess, "Popen", return_value=child) as spawn:
      self.controller.tick()
    self.assertEqual(spawn.call_count, 1)
    request = json.loads((self.controller._job / "request.json").read_text())
    self.assertEqual(request["analysis_intent"], self.intent)
    self.assertEqual(request["analysis_intent"]["range_start_ns"], 1)
    self.controller.invalidate_analysis()
    self.assertIsNone(self.controller._process)
    self.assertTrue(child.stdin.closed)
    self.assertFalse((self.controller.root / "latest.json").exists())
    with patch.object(periodic.subprocess, "Popen", side_effect=AssertionError("cut triggered a new job")):
      self.now += 10000
      self.controller.tick()


if __name__ == "__main__":
  unittest.main()
