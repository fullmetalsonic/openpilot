"""Scheduler contracts and actual native-generated active-prefix children."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import periodic
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder
from openpilot.selfdrive.carrot.control_advisor.tests.test_log_candidates import NativeLogCandidateFixture, log_candidate_configs


def manual_intent(fixture):
  """Explicit immutable whole-trip request; no clock advance grants authority."""
  if not hasattr(fixture, "_manual_intent"):
    from openpilot.selfdrive.carrot.control_advisor.rlog import validate_closed_log
    paths = getattr(fixture, "sources", [getattr(fixture, "source", None)])
    coverage = [validate_closed_log(path)["services"]["carState"] for path in paths]
    token = fixture.recorder.checkpoint()
    fixture._manual_intent = {"schema_version": 1, "request_id": "manual-fixture", "trigger": "manual",
      "session_id": token["session_id"], "range_start_ns": min(item["mono_min_ns"] for item in coverage),
      "requested_end_ns": max(item["mono_max_ns"] for item in coverage), "cut_revision": 0,
      "trip_binding": {"vehicle_id": "generated-pc-fixture", "boot_id": "00000000-0000-0000-0000-000000000001", "route": "00000000--0123456789"},
      "profile_sha256": hashlib.sha256(fixture.profile.read_bytes()).hexdigest(),
      "objectives_sha256": hashlib.sha256(fixture.objectives.read_bytes()).hexdigest()}
  return json.loads(json.dumps(fixture._manual_intent))


class Clock:
  def __init__(self):
    self.now = 0.
  def __call__(self):
    return self.now


def active_fixture(root, **options):
  captured = []
  def keep_open(recorder):
    captured.append(recorder)
    return recorder.snapshot()
  with patch.object(SettingsRecorder, "close", keep_open):
    fixture = NativeLogCandidateFixture(root, **options)
  fixture.recorder = captured[0]
  fixture.profile, fixture.objectives = log_candidate_configs(root)
  return fixture


def wait_controller(controller, seconds=25):
  deadline = time.monotonic() + seconds
  while time.monotonic() < deadline:
    value = controller.tick()
    if controller._process is None and value["attempt"] is not None and value["state"] in {"ready", "partial", "failed"}:
      return value
    time.sleep(.03)
  raise AssertionError("periodic child exceeded test deadline")


class PeriodicTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)

  def fixture(self, **options):
    fixture = active_fixture(self.root, **options)
    self.addCleanup(fixture.recorder.close)
    return fixture

  def controller(self, fixture, **options):
    clock = Clock()
    controller = periodic.PeriodicAnalyzer(fixture.session, fixture.profile, fixture.objectives, self.root / "output",
                                         interval_seconds=180, clock=clock, **options)
    self.addCleanup(controller.close)
    return controller, clock

  def test_off_does_not_probe_files_and_rejects_apply_claims(self):
    with patch.object(Path, "mkdir", side_effect=AssertionError("OFF wrote files")):
      controller = periodic.PeriodicAnalyzer(None, None, None, None)
      self.assertEqual("disabled", controller.tick()["state"])
      self.assertFalse(controller.offer({}))
      controller.close()
    bad = periodic.initial_periodic_status()
    bad["apply_enabled"] = True
    with self.assertRaises(ValueError):
      periodic.validate_periodic_status(bad)

  def test_actual_child_prefix_publish_cache_new_poll_dedup_and_restart(self):
    fixture = self.fixture()
    controller, clock = self.controller(fixture)
    token = fixture.recorder.checkpoint()
    controller.offer(token, archive_watermark="a" * 64)
    self.assertEqual("waiting", controller.tick()["state"])
    clock.now = 180
    controller.request_analysis(manual_intent(fixture))
    controller.tick()
    fixture.now += 1_000_000_000
    fixture.recorder.sample()
    self.assertEqual("ready", wait_controller(controller)["state"], controller.snapshot())
    cache = {}
    with patch.object(periodic, "_verify_job", wraps=periodic._verify_job) as verify:
      result = periodic.read_periodic_result(controller.root, cache=cache)
      controller._persist(force=True)
      self.assertEqual("ready", periodic.read_periodic_result(controller.root, cache=cache)["state"])
      self.assertEqual(1, verify.call_count)
    self.assertEqual(130, next(row["candidate_raw"] for row in result["report"]["items"] if row["key"] == "CruiseMaxVals0"))
    now_ns = time.monotonic_ns()
    with patch.object(periodic.time, "monotonic_ns", return_value=now_ns + periodic.HEARTBEAT_NS + 1):
      expired = periodic.read_periodic_result(controller.root, cache=cache)
    self.assertEqual("periodic_publisher_unavailable", expired["reason"])
    self.assertTrue(expired["stale"])
    self.assertIsNone(expired["report"])
    count = len(list((controller.root / "jobs").iterdir()))
    controller.offer(fixture.recorder.checkpoint(), archive_watermark="a" * 64)
    controller.request_analysis(manual_intent(fixture))
    clock.now = 360
    controller.tick()
    self.assertIsNone(controller._process)
    self.assertEqual(count, len(list((controller.root / "jobs").iterdir())))
    self.assertEqual(1, controller.snapshot()["result_revision"])
    controller.close()
    restarted, clock2 = self.controller(fixture)
    restarted.offer(token, archive_watermark="a" * 64)
    restarted.request_analysis(manual_intent(fixture))
    clock2.now = 180
    restarted.tick()
    self.assertIsNone(restarted._process)
    self.assertEqual(count, len(list((controller.root / "jobs").iterdir())))

  def test_timeout_and_stop_terminate_owned_process_without_publication(self):
    fixture = self.fixture()
    controller, clock = self.controller(fixture, wall_timeout_seconds=1)
    real_popen = subprocess.Popen
    def hanging(arguments, **options):
      return real_popen([sys.executable, "-c", "import time;time.sleep(60)"], **options)
    with patch.object(periodic.subprocess, "Popen", side_effect=hanging):
      controller.offer(fixture.recorder.checkpoint())
      controller.request_analysis(manual_intent(fixture))
      clock.now = 180
      controller.tick()
      process = controller._process
      clock.now = 182
      controller.tick()
    self.assertIsNotNone(process.poll())
    self.assertEqual("periodic_job_timeout", controller.snapshot()["reason"])
    self.assertFalse((controller.root / "latest.json").exists())
    controller.offer(fixture.recorder.checkpoint())
    controller.request_analysis(manual_intent(fixture))
    clock.now = 362
    with patch.object(periodic.subprocess, "Popen", side_effect=hanging):
      controller.tick()
    process = controller._process
    controller.close(timeout=2.)
    self.assertIsNotNone(process.poll())
    self.assertEqual("stopped", controller.snapshot()["state"])

  def test_explicit_missing_consumer_gets_one_settling_retry_after_late_evidence(self):
    fixture = self.fixture(missing_consumer=True)
    controller, clock = self.controller(fixture)
    controller.offer(fixture.recorder.checkpoint(), archive_watermark="a" * 64)
    controller.request_analysis(manual_intent(fixture))
    clock.now = 180
    controller.tick()
    self.assertEqual("ready", wait_controller(controller)["state"])
    first = json.loads((controller._job / "job-result.json").read_text())
    self.assertTrue(first["settling_retry_needed"])
    from openpilot.selfdrive.carrot.control_advisor.consumer_observer import OBSERVER_PATH, CONSUMER_PATHS
    repo = Path(__file__).resolve().parents[5]
    fixture.now = 13_000_000_000
    for index in range(10):
      event = {"schema_version": 1, "consumer_id": "planner.carrot", "boot_id": "00000000-0000-0000-0000-000000000001",
        "pid": 42, "process_instance_id": "a" * 32, "consumer_instance_id": "b" * 32, "sequence": index + 1,
        "produced_mono_ns": 2_000_000_050 + index * 1_000_000_000, "cycle_id": index, "phase": "sampled_use", "dropped_events": 0,
        "fields": {f"CruiseMaxVals{i}": {"read_value": 1., "effective_value": 1., "state": "loaded", "source": "stored", "read_mono_ns": 2_000_000_000} for i in range(7)},
        "context": {"mode": "acc"}, "source_hashes": {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in (OBSERVER_PATH, CONSUMER_PATHS["planner.carrot"])}}
      fixture.recorder.append_consumer_observation(event)
    controller.offer(fixture.recorder.checkpoint(), archive_watermark="a" * 64)
    controller.request_analysis(manual_intent(fixture))
    clock.now = 360
    controller.tick()
    self.assertEqual("ready", wait_controller(controller)["state"])
    result = periodic.read_periodic_result(controller.root)
    self.assertEqual(130, next(row["candidate_raw"] for row in result["report"]["items"] if row["key"] == "CruiseMaxVals0"))
    self.assertEqual(2, result["result_revision"])
    fixture.recorder.sample()
    controller.offer(fixture.recorder.checkpoint(), archive_watermark="a" * 64)
    controller.request_analysis(manual_intent(fixture))
    clock.now = 540
    controller.tick()
    self.assertIsNone(controller._process)
    self.assertEqual(2, len(list((controller.root / "jobs").iterdir())))

  def test_settling_retry_exhaustion_survives_restart(self):
    fixture = self.fixture(missing_consumer=True)
    controller, clock = self.controller(fixture)
    for number in (1, 2):
      fixture.now += 1_000_000_000
      fixture.recorder.sample()
      controller.offer(fixture.recorder.checkpoint(), archive_watermark="a" * 64)
      controller.request_analysis(manual_intent(fixture))
      clock.now = number * 180
      controller.tick()
      self.assertEqual("ready", wait_controller(controller)["state"])
    controller.close()
    restarted, next_clock = self.controller(fixture)
    fixture.now += 1_000_000_000
    fixture.recorder.sample()
    restarted.offer(fixture.recorder.checkpoint(), archive_watermark="a" * 64)
    restarted.request_analysis(manual_intent(fixture))
    next_clock.now = 180
    restarted.tick()
    self.assertIsNone(restarted._process)
    self.assertEqual(2, len(list((restarted.root / "jobs").iterdir())))

  def test_quota_refusal_preserves_sources(self):
    fixture = self.fixture()
    controller, clock = self.controller(fixture, max_cache_bytes=1024)
    source = fixture.source.read_bytes()
    controller.offer(fixture.recorder.checkpoint())
    controller.request_analysis(manual_intent(fixture))
    clock.now = 180
    self.assertEqual("periodic_cache_quota", controller.tick()["reason"])
    self.assertEqual(source, fixture.source.read_bytes())
    self.assertFalse((controller.root / "latest.json").exists())

  def test_actual_parent_death_closes_pipe_and_child_watchdog_exits(self):
    # This is the real child watchdog with a deliberately blocked decoder,
    # separate from the successful native decoding fixture above.
    code = "import time;from openpilot.selfdrive.carrot.control_advisor import periodic;p=periodic; p._read=lambda *a:time.sleep(60);p._child('unused')"
    parent_code = "import subprocess,sys,time;p=subprocess.Popen([sys.executable,'-c'," + repr(code) + "],stdin=subprocess.PIPE);print(p.pid,flush=True);time.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
      import queue, threading
      lines = queue.Queue()
      threading.Thread(target=lambda: lines.put(parent.stdout.readline()), daemon=True).start()
      child_pid = int(lines.get(timeout=5).strip())
      if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000 | 0x1000, False, child_pid)
        self.assertTrue(handle)
        try:
          parent.kill()
          parent.wait(timeout=3)
          kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
          self.assertEqual(0, kernel.WaitForSingleObject(handle, 5000))
        finally:
          kernel.CloseHandle.argtypes = [ctypes.c_void_p]
          kernel.CloseHandle(handle)
      else:
        parent.kill()
        parent.wait(timeout=3)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
          process = Path(f"/proc/{child_pid}/stat")
          if not process.exists() or process.read_text().split()[2] == "Z":
            break
          time.sleep(.05)
        else:
          self.fail("orphan analysis child did not exit")
    finally:
      if parent.poll() is None:
        parent.kill()
        parent.wait(timeout=3)
      parent.stdout.close()
      parent.stderr.close()


if __name__ == "__main__":
  unittest.main()
