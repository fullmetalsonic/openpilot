"""Real temporary settings/journal I/O with a bounded fake archive transport."""

from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType
import unittest
from unittest import mock

from openpilot.selfdrive.carrot.control_advisor.recording import RecordingSession
from openpilot.selfdrive.carrot.control_advisor import recording as recording_module
from openpilot.selfdrive.carrot.control_advisor.settings_history import KEYS, SettingsRecorder, load_history


ARCHIVE_NAME = "openpilot.selfdrive.carrot.control_advisor.archive"


class FakeArchive:
  instances = []
  block_copy = None
  uncooperative_release = None
  end_ns = 1_000_000_000_000

  def __init__(self, log_root, archive_root, **options):
    self.options, self.jobs, self.closed, self.discover_calls = options, [], False, []
    Path(archive_root).mkdir()
    type(self).instances.append(self)

  def discover_candidates(self, **options):
    self.discover_calls.append(options)
    return [{"source_relative": "00000000--0123456789--0/rlog", "route": "00000000--0123456789", "segment": 0}]

  def inspect_candidate(self, source, **options):
    return {"start_mono_ns": 0, "end_mono_ns": type(self).end_ns, "sha256": "a" * 64,
            "route": "00000000--0123456789", "segment": 0}

  def preserve(self, source, **job):
    self.jobs.append(deepcopy({key: value for key, value in job.items() if key != "cancelled"}))
    if self.block_copy is not None:
      self.block_copy.set()
      if self.uncooperative_release is not None:
        self.uncooperative_release.wait(timeout=3)
        raise InterruptedError("synthetic delayed cancellation")
      while not job["cancelled"]():
        time.sleep(0.01)
      raise InterruptedError("synthetic cooperative cancellation")
    return {"state": "complete", "source_relative": source, "archive_id": "a" * 32,
            "session_id": self.options["session_id"], "source_mode": self.options["source_mode"],
            "route": "00000000--0123456789", "segment": 0, "sha256": "a" * 64, "bytes": 128,
            "start_mono_ns": 0, "end_mono_ns": type(self).end_ns,
            "manifest_relative": "a" * 32 + "/archive.json", "manifest_sha256": "b" * 64,
            "diagnostic_revision": job["diagnostic_revision"], "settings_revision": job["settings_revision"],
            "window_start_ns": job["window_ns"][0], "window_end_ns": job["window_ns"][1],
            "settings_history_reference": deepcopy(job["journal_ref"])}

  def close(self):
    self.closed = True


def wait_for(predicate, timeout=3):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    value = predicate()
    if value:
      return value
    time.sleep(0.01)
  raise AssertionError("bounded recording condition not reached")


def diagnostic(revision=1, settings_revision=1):
  return {"schema_version": 1, "revision": revision, "window_start_ns": 1_000_000_000,
          "window_end_ns": 1_600_000_000, "profile_id": "synthetic-recording-fixture",
          "invalidated": False, "partial_window": False, "observed_settings_revision": settings_revision,
          "settings_history": "unknown", "axes": {}, "recommendations_enabled": False, "apply_enabled": False}


class RecordingTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.root = Path(self.directory.name)
    self.settings, self.logs, self.evidence = (self.root / name for name in ("settings", "logs", "evidence"))
    for path in (self.settings, self.logs, self.evidence):
      path.mkdir()
    for key in KEYS:
      (self.settings / key).write_text("1", encoding="utf-8")
    (self.settings / "CurrentRoute").write_text("00000000--0123456789", encoding="ascii")
    FakeArchive.instances, FakeArchive.block_copy = [], None
    FakeArchive.uncooperative_release = None
    FakeArchive.end_ns = 1_000_000_000_000
    fake_module = ModuleType(ARCHIVE_NAME)
    fake_module.SegmentArchive = FakeArchive
    patcher = mock.patch.dict(sys.modules, {ARCHIVE_NAME: fake_module})
    patcher.start()
    self.addCleanup(patcher.stop)
    counter = itertools.count(1_000_000_000, 1_000_000)
    self.clock = lambda: next(counter)

  def environment(self, *, demo=True, **changes):
    prefix = "CARROT_CONTROL_ADVISOR_" + ("DEMO_" if demo else "")
    result = {prefix + name: str(value) for name, value in zip(("SETTINGS_DIR", "LOG_ROOT", "EVIDENCE_ROOT"),
                                                             (self.settings, self.logs, self.evidence))}
    result.update(changes)
    return result

  def recorder(self, session_id="fixture", **options):
    recorder = RecordingSession(session_id, source_mode="synthetic", environment=self.environment(),
                                 source_clock_ns=self.clock, poll_seconds=0.02, archive_poll_seconds=0.02, **options).start()
    self.addCleanup(recorder.close)
    return recorder

  def test_modes_require_all_three_explicit_isolated_roots(self):
    disabled = RecordingSession("isolated", source_mode="synthetic", environment=self.environment(demo=False)).start()
    self.assertEqual("disabled", disabled.snapshot()["state"])
    self.assertEqual([], disabled._threads)
    self.assertFalse((self.evidence / "isolated").exists())
    partial = RecordingSession("partial", source_mode="live", environment={"CARROT_CONTROL_ADVISOR_SETTINGS_DIR": str(self.settings)}).start()
    self.assertEqual("disabled", partial.snapshot()["state"])
    self.assertFalse(partial.submit_diagnostic(diagnostic()))

  def test_first_pass_file_and_old_revision_are_bound_to_fixed_committed_prefix(self):
    recorder = self.recorder()
    wait_for(lambda: recorder.snapshot()["settings"]["revision"] == 1)
    (self.settings / KEYS[0]).write_text("2", encoding="utf-8")
    (self.settings / "CurrentRoute").write_text("00000001--0123456789", encoding="ascii")
    wait_for(lambda: recorder.snapshot()["settings"]["revision"] == 2)
    result = diagnostic(settings_revision=1)
    self.assertTrue(recorder.submit_diagnostic(result))
    wait_for(lambda: recorder.snapshot()["preservation"]["verified_segments"] == 1
             and recorder.snapshot()["preservation"]["journal_committed"])
    archive = FakeArchive.instances[0]
    self.assertEqual(1, archive.jobs[0]["settings_revision"])
    self.assertEqual("00000000--0123456789", archive.discover_calls[0]["route"])
    fixed_ref = deepcopy(archive.jobs[0]["journal_ref"])
    self.assertTrue(fixed_ref["durable"])
    self.assertEqual("settings.jsonl", fixed_ref["path"])
    recorder.close()
    loaded = load_history(self.evidence / "fixture" / "settings.jsonl")
    self.assertEqual("complete", loaded["state"])
    self.assertEqual(fixed_ref["sha256"], loaded["references"][str(fixed_ref["sequence"])]["sha256"])
    self.assertEqual(1, loaded["diagnostics"][0]["revision"])
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    path = self.evidence / "fixture" / "diagnostics" / f"1-{digest[:16]}.json"
    self.assertEqual(encoded, path.read_bytes())
    self.assertEqual(digest, loaded["diagnostics"][0]["sha256"])
    self.assertGreater(recorder.snapshot()["preservation"]["uncovered_windows"], 0)
    self.assertTrue(archive.closed)

  def test_source_and_existing_session_are_never_overwritten(self):
    session = self.evidence / "existing"
    session.mkdir()
    original = b"original evidence stays intact\n"
    (session / "settings.jsonl").write_bytes(original)
    before = {path.name: path.read_bytes() for path in self.settings.iterdir()}
    recorder = self.recorder("existing")
    wait_for(lambda: recorder.snapshot()["state"] == "failed")
    self.assertEqual(original, (session / "settings.jsonl").read_bytes())
    self.assertEqual(before, {path.name: path.read_bytes() for path in self.settings.iterdir()})

  def test_root_overlap_and_relative_roots_fail_before_session_creation(self):
    for name, value in (("overlap", self.logs / "nested"), ("relative", Path("relative-evidence"))):
      environment = self.environment(CARROT_CONTROL_ADVISOR_DEMO_EVIDENCE_ROOT=str(value))
      recorder = RecordingSession(name, source_mode="synthetic", environment=environment).start()
      self.addCleanup(recorder.close)
      wait_for(lambda: recorder.snapshot()["state"] == "failed")
      self.assertFalse((value / name).exists())
    with self.assertRaises(ValueError):
      RecordingSession("../unsafe", source_mode="synthetic", environment=self.environment())

  def test_blocked_settings_io_does_not_hold_main_snapshot_or_command_queue(self):
    entered, release = threading.Event(), threading.Event()
    original_sample = SettingsRecorder.sample
    def blocked_sample(instance):
      entered.set()
      release.wait(timeout=3)
      return original_sample(instance)
    with mock.patch.object(SettingsRecorder, "sample", blocked_sample):
      recorder = self.recorder("blocked")
      self.assertTrue(entered.wait(timeout=2))
      start = time.monotonic()
      recorder.snapshot()
      recorder.poll_settings()
      self.assertTrue(recorder.submit_diagnostic(diagnostic()))
      self.assertLess(time.monotonic() - start, 0.1)
      release.set()
      wait_for(lambda: recorder.snapshot()["settings"]["revision"] >= 1)

  def test_command_queue_has_an_explicit_bound(self):
    recorder = RecordingSession("queue", source_mode="synthetic", environment=self.environment())
    accepted = [recorder.submit_diagnostic(diagnostic(revision=index)) for index in range(1, 41)]
    self.assertEqual(32, sum(accepted))
    self.assertEqual("recording_command_queue_full", recorder.snapshot()["preservation"]["reason"])
    self.assertFalse((self.evidence / "queue").exists())

  def test_archive_cancellation_and_bounded_shutdown_leave_partial_state(self):
    started = threading.Event()
    FakeArchive.block_copy = started
    recorder = self.recorder("cancel")
    wait_for(lambda: recorder.snapshot()["settings"]["revision"] == 1)
    recorder.submit_diagnostic(diagnostic())
    self.assertTrue(started.wait(timeout=2))
    start = time.monotonic()
    status = recorder.close(timeout=1)
    self.assertLess(time.monotonic() - start, 1.1)
    self.assertEqual("stopped", status["state"])
    self.assertEqual("partial", status["preservation"]["state"])
    self.assertEqual(0, status["preservation"]["verified_segments"])

  def test_notifications_and_diagnostic_size_are_bounded_and_public_paths_are_relative(self):
    recorder = self.recorder("status")
    wait_for(lambda: recorder.snapshot()["settings"]["revision"] == 1)
    notices = recorder.poll_settings()
    self.assertEqual(1, notices[-1]["revision"])
    status = recorder.snapshot()
    self.assertEqual(set(KEYS), set(status["settings"]["values"]))
    self.assertFalse(status["settings"]["consumer_applied"])
    self.assertFalse(status["settings"]["history_complete"])
    self.assertNotIn(str(self.root), json.dumps(status))
    oversized = {**diagnostic(), "large": "x" * (256 * 1024)}
    self.assertFalse(recorder.submit_diagnostic(oversized))
    self.assertEqual("diagnostic_file_limit", recorder.snapshot()["preservation"]["reason"])

  def test_root_budget_counts_prior_partials_and_reserves_current_session(self):
    prior = self.evidence / "prior-partial"
    prior.mkdir()
    (prior / "settings.jsonl").write_bytes(b"x" * 500)
    with mock.patch.object(recording_module, "ROOT_BYTE_LIMIT", 512), \
         mock.patch.object(recording_module, "SESSION_RESERVATION_BYTES", 64):
      with self.assertRaisesRegex(ValueError, "root_budget"):
        recording_module._root_budget(self.evidence)
    with mock.patch.object(recording_module, "ROOT_SESSION_LIMIT", 1):
      with self.assertRaisesRegex(ValueError, "root_budget"):
        recording_module._root_budget(self.evidence)
    artifact = prior / "archive" / ("a" * 32)
    artifact.mkdir(parents=True)
    (artifact / "archive.json.partial").write_bytes(b"x")
    self.assertEqual(501, recording_module._root_budget(self.evidence)["bytes"])
    (prior / "unexpected.bin").write_bytes(b"unknown")
    with self.assertRaisesRegex(ValueError, "unknown_structure"):
      recording_module._root_budget(self.evidence)

  def test_exclusive_root_lease_releases_without_deleting_a_sentinel(self):
    first = self.recorder("first")
    wait_for(lambda: first.snapshot()["settings"]["revision"] == 1)
    second = self.recorder("second")
    wait_for(lambda: second.snapshot()["state"] == "failed")
    self.assertFalse((self.evidence / "second").exists())
    first.close()
    second.close()
    self.assertTrue((self.evidence / ".control-advisor.lock").is_file())
    third = self.recorder("third")
    wait_for(lambda: third.snapshot()["settings"]["revision"] == 1)
    self.assertNotEqual("failed", third.snapshot()["state"])

  def test_root_lease_is_released_by_the_os_after_abrupt_process_exit(self):
    root = Path(__file__).parents[5]
    code = ("import os,sys; from pathlib import Path; "
            "from openpilot.selfdrive.carrot.control_advisor.recording import _RootLease; "
            "lease=_RootLease(Path(sys.argv[1])); os._exit(0)")
    result = subprocess.run([sys.executable, "-c", code, str(self.evidence)], cwd=root, capture_output=True, timeout=5)
    self.assertEqual(0, result.returncode, result.stderr)
    lease = recording_module._RootLease(self.evidence)
    lease.close()
    self.assertTrue((self.evidence / ".control-advisor.lock").exists())

  def test_route_unknown_never_falls_back_to_scanning_historic_routes(self):
    (self.settings / "CurrentRoute").unlink()
    recorder = self.recorder("unknown-route")
    wait_for(lambda: recorder.snapshot()["settings"]["revision"] == 1)
    recorder.submit_diagnostic(diagnostic())
    wait_for(lambda: recorder.snapshot()["preservation"]["reason"] == "route_association_unknown")
    self.assertTrue(all(not archive.discover_calls for archive in FakeArchive.instances))
    recorder.close()
    history = load_history(self.evidence / "unknown-route" / "settings.jsonl")
    self.assertEqual("incomplete", history["preservation_jobs"]["1"]["state"])
    self.assertEqual("missing", history["diagnostic_references"][str(next(iter(history["diagnostic_references"])))]["route_observation"]["state"])

  def test_union_requires_ordered_contiguous_segments_and_retains_actual_gaps(self):
    route = "00000000--0123456789"
    first = {"start": 0, "end": 1_000_000_000, "route": route, "segment": 0}
    second = {"start": 1_010_000_000, "end": 2_000_000_000, "route": route, "segment": 1}
    result = recording_module._window_assessment([first, second], (0, 2_000_000_000), route)
    self.assertTrue(result["complete"])
    self.assertEqual([{"start_ns": 1_000_000_000, "end_ns": 1_010_000_000}], result["actual_boundary_gaps"])
    missing = recording_module._window_assessment([first, {**second, "segment": 2}], (0, 2_000_000_000), route)
    self.assertFalse(missing["complete"])
    self.assertEqual([1], missing["missing_segments"])
    distant = recording_module._window_assessment([first, {**second, "start": 61_000_000_000, "end": 62_000_000_000}],
                                                  (0, 62_000_000_000), route)
    self.assertFalse(distant["complete"])
    self.assertFalse(recording_module._window_assessment([first, {**second, "route": "other"}], (0, 2_000_000_000), route)["complete"])

  def test_incomplete_job_times_out_durably_and_releases_a_pending_slot(self):
    FakeArchive.end_ns = 1_100_000_000
    recorder = self.recorder("timeout", analysis_interval_seconds=180)
    wait_for(lambda: recorder.snapshot()["settings"]["revision"] == 1)
    recorder.submit_diagnostic(diagnostic())
    wait_for(lambda: recorder.snapshot()["preservation"]["reason"] == "preservation_timeout", timeout=5)
    self.assertEqual(0, recorder.snapshot()["preservation"]["pending_jobs"])
    recorder.close()
    history = load_history(self.evidence / "timeout" / "settings.jsonl")
    self.assertEqual("timeout", history["preservation_jobs"]["1"]["state"])
    self.assertFalse(history["preservation_jobs"]["1"]["coverage_complete"])

  def test_full_preservation_queue_leaves_a_durable_incomplete_job(self):
    recorder = self.recorder("full-preservation")
    wait_for(lambda: recorder.snapshot()["settings"]["revision"] == 1)
    with recorder._lock:
      recorder._pending_jobs = 4
      recorder._public["preservation"]["pending_jobs"] = 4
    recorder.submit_diagnostic(diagnostic())
    wait_for(lambda: recorder.snapshot()["preservation"]["reason"] == "preservation_queue_full")
    recorder.close()
    history = load_history(self.evidence / "full-preservation" / "settings.jsonl")
    self.assertEqual("incomplete", history["preservation_jobs"]["1"]["state"])
    self.assertFalse(history["preservation_jobs"]["1"]["endpoint_end_covered"])

  def test_failed_settings_writer_keeps_root_lease_until_archive_io_has_stopped(self):
    copy_started, release_copy, fail_sample = threading.Event(), threading.Event(), threading.Event()
    FakeArchive.block_copy, FakeArchive.uncooperative_release = copy_started, release_copy
    original_sample = SettingsRecorder.sample
    def failing_sample(instance):
      if fail_sample.is_set():
        raise RuntimeError("synthetic settings failure")
      return original_sample(instance)
    with mock.patch.object(SettingsRecorder, "sample", failing_sample):
      recorder = self.recorder("lease-error")
      wait_for(lambda: recorder.snapshot()["settings"]["revision"] == 1)
      recorder.submit_diagnostic(diagnostic())
      self.assertTrue(copy_started.wait(timeout=2))
      fail_sample.set()
      wait_for(lambda: recorder.snapshot()["state"] == "failed")
      with self.assertRaises(OSError):
        recording_module._RootLease(self.evidence)
      self.assertFalse(recorder._archive_finished.is_set())
      release_copy.set()
      wait_for(recorder._archive_finished.is_set)
      wait_for(recorder._settings_finished.is_set)
      lease = recording_module._RootLease(self.evidence)
      lease.close()


if __name__ == "__main__":
  unittest.main()
