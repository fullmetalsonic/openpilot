"""Journal-to-scheduler wiring; process containment is tested by the scheduler."""
from copy import deepcopy
from pathlib import Path
import json
import threading
import time
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.recording import RecordingSession, _RootLease, _automatic_initial
from . import test_recording as recording_fixture

diagnostic, wait_for = recording_fixture.diagnostic, recording_fixture.wait_for


class FakeAnalyzer:
  instances = []
  blocked_tick = None
  blocked_close = None

  def __init__(self, session_directory, profile, objectives, output_root, **options):
    self.options, self.directory = options, session_directory
    self.thread_name = threading.current_thread().name
    self.offers, self.watermarks, self.ticks, self.closed = [], [], 0, False
    self.public = _automatic_initial(options["interval_seconds"], state="waiting", reason="waiting_for_checkpoint")
    self.waiting = threading.Event()
    self.intents, self.invalidations = [], []
    type(self).instances.append(self)

  def offer(self, token, *, archive_watermark=None):
    self.offers.append(deepcopy(token))
    self.watermarks.append(archive_watermark)
    return True

  def tick(self):
    self.ticks += 1
    if self.blocked_tick is not None:
      self.waiting.set()
      self.blocked_tick.wait(3)

  def snapshot(self):
    return deepcopy(self.public)

  def request_analysis(self, intent):
    self.intents.append(deepcopy(intent))
    self.public.update(state="running", reason=None)

  def invalidate_analysis(self, reason):
    self.invalidations.append(reason)
    self.public.update(state="waiting", reason=reason)

  def close(self, *, timeout):
    if self.blocked_close is not None:
      self.blocked_close.wait(3)
    self.closed = True
    self.public.update(state="stopped", reason="closed")


class PeriodicRecordingTests(unittest.TestCase):
  setUp = recording_fixture.RecordingTests.setUp
  environment = recording_fixture.RecordingTests.environment

  def test_executor_switch_keeps_recording_threads_and_persists_separate_preference(self):
    class LocationAnalyzer(FakeAnalyzer):
      def set_executor(self, executor):
        self.location = {'executor': executor, 'revision': 'a' * 32, 'pending_job': None}
      def execution_snapshot(self):
        return deepcopy(self.location)
    environment = self.auto_environment()
    recorder = self.start_recorder(environment=environment, factory=LocationAnalyzer)
    wait_for(lambda: recorder.execution_snapshot() is not None)
    threads = tuple(recorder._threads)
    recorder.request_executor('remote_pc')
    wait_for(lambda: recorder.execution_snapshot()['executor'] == 'remote_pc')
    self.assertEqual(tuple(recorder._threads), threads)
    self.assertNotIn(recorder.snapshot()['state'], {'failed', 'stopped'})
    from openpilot.selfdrive.carrot.control_advisor.execution import preference
    self.assertEqual(preference(Path(environment['CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT'])), 'remote_pc')

  def auto_environment(self, *, enabled=True, **changes):
    for name in ("explicit-profile.json", "explicit-objectives.json"):
      (self.root / name).write_text("{}")
    values = self.environment(demo=False)
    values.update({"CARROT_CONTROL_ADVISOR_AUTO_ENABLED": "1" if enabled else "0",
                   "CARROT_CONTROL_ADVISOR_AUTO_PROFILE": str(self.root / "explicit-profile.json"),
                   "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES": str(self.root / "explicit-objectives.json"),
                   "CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT": str(self.root / "automatic-results")})
    values.update(changes)
    return values

  def start_recorder(self, *, environment=None, factory=FakeAnalyzer, interval=180, session_id="periodic-fixture", identity_provider=None):
    FakeAnalyzer.instances, FakeAnalyzer.blocked_tick, FakeAnalyzer.blocked_close = [], None, None
    recorder = RecordingSession(session_id, source_mode="live", environment=environment or self.auto_environment(),
                                 source_clock_ns=self.clock, poll_seconds=.02, archive_poll_seconds=.02,
                                 analysis_interval_seconds=interval, analyzer_factory=factory, identity_provider=identity_provider).start()
    self.addCleanup(recorder.close)
    wait_for(lambda: recorder.snapshot()["settings"]["durable_revision"] > 0)
    return recorder

  def test_goal_switch_keeps_writer_trip_and_successful_apply_cut(self):
    from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
    from openpilot.selfdrive.carrot.control_advisor.goals import goal_configuration
    identity = {"identity_version": 1, "state": "observed", "reason": None, "config_sha256": "1" * 64,
      "schema_sha256": "2" * 64, "raw_sha256": "3" * 64, "vin_sha256": None, "car_fingerprint": "GENERATED_UNIT",
      "physical_group_id": "a" * 32, "physical_group_basis": "local_confirmation"}
    environment = self.auto_environment(CARROT_CONTROL_ADVISOR_AUTO_PROFILE=BUILTIN_PROFILE_PATH,
      CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES=BUILTIN_OBJECTIVES_PATH,
      CARROT_CONTROL_ADVISOR_BUILTIN_GOALS="1", CARROT_CONTROL_ADVISOR_SELECTED_GOAL="tracking")
    with patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._read_boot_id", return_value="unit-boot"):
      recorder = self.start_recorder(environment=environment, identity_provider=lambda: identity)
      analyzer = wait_for(lambda: FakeAnalyzer.instances and FakeAnalyzer.instances[0])
      threads, directory = list(recorder._threads), recorder._session_dir
      recorder.observe_analysis(self.clock(), {})
      wait_for(lambda: recorder.analysis_binding() is not None)
      recorder.observe_analysis(self.clock(), {})
      wait_for(lambda: recorder.analysis_snapshot()["can_analyze"])
      recorder.request_analysis("first")
      wait_for(lambda: len(analyzer.intents) == 1)
      stored = self.clock()
      recorder.successful_apply({"stored_mono_ns": stored, "revision": 1}, {"vehicle_id": "a" * 32, "boot_id": "unit-boot"})
      wait_for(lambda: recorder.analysis_snapshot()["apply_cut_ns"] == str(stored))
      recorder.observe_analysis(self.clock(), {})
      wait_for(lambda: recorder.analysis_snapshot()["can_analyze"])
      recorder.request_analysis("second", "fms_accel_defaults_v1")
      wait_for(lambda: len(analyzer.intents) == 2)
      expected = goal_configuration("fms_accel_defaults_v1", environment)
      self.assertEqual(analyzer.profile, Path(expected["profile_path"]))
      self.assertEqual(analyzer.intents[1]["range_start_ns"], stored)
      self.assertEqual(analyzer.intents[1]["cut_revision"], 1)
      self.assertEqual(analyzer.intents[0]["goal_id"], "tracking")
      self.assertEqual(analyzer.intents[1]["goal_id"], "fms_accel_defaults_v1")
      self.assertIn("analysis_goal_changed", analyzer.invalidations)
      self.assertEqual(recorder._session_dir, directory)
      self.assertEqual(recorder._threads, threads)
      recorder.close()
      self.assertEqual(recorder.analysis_snapshot()["state"], "incomplete")
      self.assertFalse(recorder.analysis_snapshot()["can_analyze"])

  def submit(self, recorder):
    revision = recorder.snapshot()["settings"]["durable_revision"]
    packet = diagnostic(settings_revision=revision)
    packet["route_observation"] = {"state": "observed", "route": "00000000--0123456789"}
    # Use the recorder's actual observed route metadata rather than fabricating
    # a successful command; the existing fake archive only replaces file I/O.
    packet["route_observation"] = deepcopy(recorder._latest_route_observation)
    self.assertTrue(recorder.submit_diagnostic(packet))

  def test_default_off_never_constructs_scheduler_or_changes_recording_threads(self):
    recorder = self.start_recorder(environment=self.auto_environment(enabled=False))
    self.assertEqual(len(recorder._threads), 2)
    self.assertEqual(FakeAnalyzer.instances, [])
    self.assertEqual(recorder.automatic_snapshot()["state"], "disabled")
    self.submit(recorder)
    wait_for(lambda: recorder.snapshot()["preservation"]["verified_segments"] == 1)
    self.assertTrue(recorder._checkpoints.empty())

  def test_only_durable_archive_prefix_is_offered_and_later_polls_refresh_it(self):
    recorder = self.start_recorder(interval=300)
    analyzer = wait_for(lambda: FakeAnalyzer.instances and FakeAnalyzer.instances[0])
    self.assertEqual(analyzer.thread_name, "advisor-automatic-analysis")
    self.assertEqual(analyzer.options["interval_seconds"], 300)
    self.assertEqual(analyzer.options["source_mode"], "live")
    time.sleep(.06)
    self.assertEqual(analyzer.offers, [])
    self.submit(recorder)
    token = wait_for(lambda: analyzer.offers and analyzer.offers[-1])
    self.assertEqual(set(token), {"schema_version", "session_id", "sequence", "chain_sha256", "bytes",
                                 "cutoff_mono_ns", "consumer_budget_rejections", "durable"})
    self.assertTrue(token["durable"])
    prefix = (self.evidence / "periodic-fixture/settings.jsonl").read_bytes()[:token["bytes"]]
    records = [json.loads(line) for line in prefix.splitlines()]
    self.assertTrue(any(row["record"]["type"] == "archive_preserved" for row in records))
    self.assertEqual(records[-1]["sequence"], token["sequence"])
    wait_for(lambda: analyzer.offers[-1]["sequence"] > token["sequence"])
    self.assertEqual(len(set(analyzer.watermarks)), 1)
    self.assertEqual(len(analyzer.watermarks[0]), 64)
    self.assertNotEqual(analyzer.watermarks[0], "0" * 64)
    recorder.close()
    wait_for(lambda: analyzer.closed)
    offered = len(analyzer.offers)
    recorder._publish_checkpoint(type("Forbidden", (), {"checkpoint": lambda _: self.fail("checkpoint after stop")})())
    self.assertEqual(len(analyzer.offers), offered)

  def test_slow_scheduler_never_holds_snapshot_or_journal_and_pending_is_one(self):
    recorder = self.start_recorder()
    analyzer = wait_for(lambda: FakeAnalyzer.instances and FakeAnalyzer.instances[0])
    release = threading.Event()
    self.addCleanup(release.set)
    FakeAnalyzer.blocked_tick = release
    wait_for(analyzer.waiting.is_set)
    self.submit(recorder)
    wait_for(lambda: recorder.snapshot()["preservation"]["verified_segments"] == 1)
    first = recorder.snapshot()["settings"]["poll_sequence"]
    wait_for(lambda: recorder.snapshot()["settings"]["poll_sequence"] > first + 1)
    before = time.monotonic()
    recorder.snapshot()
    recorder.automatic_snapshot()
    self.assertLess(time.monotonic() - before, .05)
    self.assertEqual(recorder._checkpoints.qsize(), 1)
    release.set()
    wait_for(lambda: analyzer.offers)

  def test_scheduler_failure_is_isolated_from_existing_recording(self):
    def failed(*args, **kwargs):
      raise ImportError("optional analyzer unavailable")
    recorder = self.start_recorder(factory=failed)
    wait_for(lambda: recorder.automatic_snapshot()["state"] == "failed")
    self.assertNotEqual(recorder.snapshot()["state"], "failed")
    self.submit(recorder)
    wait_for(lambda: recorder.snapshot()["preservation"]["verified_segments"] == 1)
    self.assertEqual(recorder.automatic_snapshot()["reason"], "automatic_analysis_failed")

  def test_output_root_cannot_write_into_settings_logs_or_recording_evidence(self):
    for source in (self.settings, self.logs, self.evidence):
      recorder = self.start_recorder(session_id="overlap-" + source.name,
        environment=self.auto_environment(CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT=str(source / "analysis")))
      wait_for(lambda: recorder.automatic_snapshot()["state"] == "failed")
      self.assertEqual(FakeAnalyzer.instances, [])
      self.assertFalse((source / "analysis").exists())
      self.assertNotEqual(recorder.snapshot()["state"], "failed")
      self.assertEqual(recorder.automatic_snapshot()["reason"], "automatic_output_root_overlap")
      recorder.close()

  def test_shutdown_holds_root_lease_until_analysis_cleanup_finishes(self):
    recorder = self.start_recorder()
    analyzer = wait_for(lambda: FakeAnalyzer.instances and FakeAnalyzer.instances[0])
    release = threading.Event()
    self.addCleanup(release.set)
    FakeAnalyzer.blocked_close = release
    self.assertEqual(recorder.close(timeout=.01)["state"], "partial")
    with self.assertRaises((OSError, ValueError)):
      _RootLease(self.evidence)
    release.set()
    wait_for(lambda: analyzer.closed and recorder._automatic_finished.is_set() and recorder._root_lease is None)
    lease = _RootLease(self.evidence)
    lease.close()

  def test_explicit_paths_and_mode_isolation_preserve_default_behavior(self):
    env = self.auto_environment(CARROT_CONTROL_ADVISOR_AUTO_PROFILE="relative.json")
    recorder = self.start_recorder(environment=env)
    self.assertEqual(recorder.automatic_snapshot()["reason"], "automatic_analysis_configuration_invalid")
    self.assertEqual(len(recorder._threads), 2)
    recorder.close()
    demo = self.environment()
    demo.update(self.auto_environment())
    demo["CARROT_CONTROL_ADVISOR_DEMO_AUTO_ENABLED"] = "1"
    isolated = RecordingSession("demo-isolated", source_mode="synthetic", environment=demo, analyzer_factory=FakeAnalyzer)
    self.assertEqual(isolated.automatic_snapshot()["state"], "disabled")
    self.assertEqual(isolated.automatic_snapshot()["reason"], "synthetic_automatic_analysis_unavailable")


if __name__ == "__main__":
  unittest.main()
