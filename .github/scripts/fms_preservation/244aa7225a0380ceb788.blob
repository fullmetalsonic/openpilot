"""Three native-format closed segments through the real periodic child process."""
import bz2
import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.archive import SegmentArchive
from openpilot.selfdrive.carrot.control_advisor.candidate_report import load_candidate_bundle
from openpilot.selfdrive.carrot.control_advisor.consumer_observer import CONSUMER_PATHS, OBSERVER_PATH
from openpilot.selfdrive.carrot.control_advisor.periodic import PeriodicAnalyzer
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder
from openpilot.selfdrive.carrot.control_advisor.tests.test_log_candidates import BOOT, ROUTE, log_candidate_configs


class PeriodicNativeFixture:
  """Generated 180-second data, not actual driving or a qualified vehicle profile."""
  def __init__(self, root, *, unknown_middle_bounds=False):
    self.root, self.now = Path(root), 2_000_000_000
    self.root.mkdir(parents=True, exist_ok=False)
    self.settings, self.logs, self.session = (self.root / name for name in ("settings", "logs", "session"))
    for directory in (self.settings, self.logs, self.session):
      directory.mkdir()
    for item in load_registry()["items"]:
      (self.settings / item["key"]).write_text("100" if item["key"].startswith("CruiseMaxVals") else "0")
    (self.settings / "CurrentRoute").write_text(ROUTE)
    self.recorder = SettingsRecorder(self.settings, self.session, "periodic-native-windows", clock_ns=self.clock)
    self.recorder.sample()
    (self.session / "recording.json").write_text(json.dumps({"session_id": "periodic-native-windows", "source_mode": "live",
      "clock": {"domain": "native_monotonic", "scale": 1}}))
    repo = Path(__file__).resolve().parents[5]
    hashes = {consumer: {path: hashlib.sha256((repo / path).read_bytes()).hexdigest()
                        for path in (OBSERVER_PATH, CONSUMER_PATHS[consumer])}
              for consumer in ("planner.carrot", "planner.longitudinal")}
    for index in range(180):
      for consumer in hashes:
        event = {"schema_version": 1, "consumer_id": consumer, "boot_id": BOOT, "pid": 42,
          "process_instance_id": "a" * 32, "consumer_instance_id": ("b" if consumer == "planner.carrot" else "c") * 32,
          "sequence": index + 1, "produced_mono_ns": 2_000_000_200 + index * 1_000_000_000,
          "cycle_id": index, "phase": "sampled_use", "dropped_events": 0, "source_hashes": hashes[consumer],
          "context": {"mode": "acc"}, "fields": {
            f"CruiseMaxVals{i}": {"read_value": 1., "effective_value": 1.,
              "state": "loaded" if consumer == "planner.carrot" else "used", "source": "stored", "read_mono_ns": 2_000_000_000}
            for i in range(7)}}
        self.recorder.append_consumer_observation(event)
      self.now = (index + 3) * 1_000_000_000
      self.recorder.sample()
    self.profile, self.objectives = log_candidate_configs(self.root)
    profile = json.loads(self.profile.read_text())
    profile["max_rows"] = 5000
    self.profile.write_text(json.dumps(profile))
    objectives = json.loads(self.objectives.read_text())
    objectives["analysis_profile"]["max_samples"] = 10000
    self.objectives.write_text(json.dumps(objectives))
    start, end = 2_000_000_000, 181_950_000_000
    route = self.recorder.revision_metadata(1)["route_observation"]
    diagnostic = {"revision": 1, "window_start_ns": start, "window_end_ns": end, "profile_id": "periodic-native-fixture",
      "invalidated": False, "partial_window": False, "observed_settings_revision": 1, "route_observation": route}
    data = json.dumps(diagnostic, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(data).hexdigest()
    (self.session / "diagnostics").mkdir()
    (self.session / "diagnostics" / f"1-{digest[:16]}.json").write_bytes(data)
    record = {key: value for key, value in diagnostic.items() if key not in {"observed_settings_revision", "route_observation"}}
    reference = self.recorder.append_diagnostic({**record, "sha256": digest}, settings_revision=1)["journal"]
    _, log = _schema()
    def frame(kind, stamp, values):
      event = log.Event.new_message(logMonoTime=stamp, valid=True)
      event.init(kind).from_dict(values)
      return event.to_bytes()
    self.sources, self.saved = [], []
    archive = SegmentArchive(self.logs, self.session / "archive", session_id="periodic-native-windows", source_mode="live",
                              byte_limit=8 * 1024 * 1024, max_files=16, free_reserve_bytes=0)
    try:
      for segment in range(3):
        lo = start + segment * 60_000_000_000
        chunks = [frame("initData", lo - 2, {"gitCommit": "b" * 40}),
                  frame("sentinel", lo - 1, {"type": "startOfRoute" if segment == 0 else "startOfSegment"})]
        for index in range(1200):
          stamp = lo + index * 50_000_000
          chunks.extend([
            frame("carControl", stamp, {"enabled": True, "latActive": True, "longActive": True}),
            frame("controlsState", stamp, {"lateralControlState": {"torqueState": {
              "active": True, "actualLateralAccel": .3, "desiredLateralAccel": .4, "saturated": False}}}),
            frame("longitudinalPlan", stamp, {"aTarget": .7, "myDrivingMode": 3}),
            frame("carState", stamp, {"canValid": True, "gearShifter": "drive", "vEgo": 0., "aEgo": .6,
              "gasPressed": False, "steeringPressed": False, "brakePressed": False})])
        chunks.append(frame("sentinel", lo + 60_000_000_000 - 1, {"type": "endOfRoute" if segment == 2 else "endOfSegment"}))
        directory = self.logs / f"{ROUTE}--{segment}"
        directory.mkdir()
        source = directory / "rlog.bz2"
        source.write_bytes(bz2.compress(b"".join(chunks)))
        # Deterministic inventory order preserves the verified neighbours even
        # when a failed decoder conservatively spends its remaining allowance.
        identity = ("1", "f", "2")[segment] * 32
        with patch("openpilot.selfdrive.carrot.control_advisor.archive.uuid.uuid4", return_value=SimpleNamespace(hex=identity)):
          saved = archive.preserve(source, window_ns=(start, end), diagnostic_revision=1,
                                    settings_revision=1, journal_ref=reference)
        summary = {key: value for key, value in saved.items() if key not in {"source_relative", "decompressed_bytes"}}
        if unknown_middle_bounds and segment == 1:
          summary = {key: value for key, value in summary.items() if key not in {"start_mono_ns", "end_mono_ns"}}
        self.recorder.append_archive(summary)
        self.sources.append(source)
        self.saved.append(saved)
    finally:
      archive.close()
    self.recorder.append_job_status({"diagnostic_revision": 1, "settings_revision": 1, "state": "complete",
      "window_start_ns": start, "window_end_ns": end, "route_observation": route, "settings_history_reference": reference,
      "actual_boundary_gaps": [{"start_ns": start + offset - 50_000_000, "end_ns": start + offset}
                               for offset in (60_000_000_000, 120_000_000_000)],
      "gap_tolerance_ns": 250_000_000, "missing_segments": [], "missing_segments_count": 0,
      "endpoint_start_covered": True, "endpoint_end_covered": True,
      "completion_scope": "archive_work_only_host_profile", "coverage_complete": False, "interpolation_applied": False})
    self.checkpoint = self.recorder.checkpoint()
    (self.root / "checkpoint.json").write_text(json.dumps(self.checkpoint))

  def clock(self):
    self.now += 1
    return self.now

  def damage_middle(self, mode):
    marker = self.session / "archive" / self.saved[1]["manifest_relative"]
    payload = marker.parent / "rlog.bz2"
    if mode == "missing":
      payload.rename(self.root / "retained-middle-rlog.bz2")
    elif mode == "corrupt":
      with payload.open("r+b") as stream:
        stream.write(b"corrupt")
    else:
      raise ValueError("unknown_fixture_damage")


class PeriodicWindowTests(unittest.TestCase):
  def fixture(self, **options):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    fixture = PeriodicNativeFixture(Path(temporary.name) / "native-fixture", **options)
    self.addCleanup(fixture.recorder.close)
    return fixture

  def analyze(self, fixture):
    clock = [0.]
    analyzer = PeriodicAnalyzer(fixture.session, fixture.profile, fixture.objectives, fixture.root / "window-output",
                                interval_seconds=180, clock=lambda: clock[0])
    self.addCleanup(analyzer.close)
    self.assertTrue(analyzer.offer(fixture.checkpoint, archive_watermark="a" * 64))
    clock[0] = 180.
    status = analyzer.tick()
    self.assertEqual("running", status["state"], status)
    deadline = time.monotonic() + 60
    while analyzer._process is not None and time.monotonic() < deadline:
      time.sleep(.03)
      status = analyzer.tick()
    self.assertIsNone(analyzer._process, "native periodic child exceeded the bounded test duration")
    jobs = list((fixture.root / "window-output" / "jobs").iterdir())
    self.assertEqual(1, len(jobs))
    result = json.loads((jobs[0] / "job-result.json").read_text())
    return status, result, jobs[0]

  def test_actual_child_processes_every_closed_segment_in_the_180_second_target(self):
    fixture = self.fixture()
    status, result, directory = self.analyze(fixture)
    self.assertEqual("ready", status["state"], status)
    self.assertEqual(3, status["attempt"]["source_count"])
    self.assertEqual(3, status["attempt"]["processed_windows"])
    self.assertEqual(0, status["attempt"]["blocked_windows"])
    self.assertEqual(3, len(result["windows"]))
    for window in result["windows"]:
      self.assertEqual("ready", window["state"])
      bundle = load_candidate_bundle(directory / window["directory"])
      row = next(item for item in bundle["report"]["items"] if item["key"] == "CruiseMaxVals0")
      self.assertEqual(("search_candidate", 130), (row["status"], row["candidate_raw"]), row["reason_codes"])
      self.assertFalse(bundle["apply_enabled"])

  def test_middle_missing_or_corrupt_copy_remains_a_blocked_target_window(self):
    for mode in ("missing", "corrupt"):
      with self.subTest(mode=mode):
        fixture = self.fixture()
        fixture.damage_middle(mode)
        status, result, _ = self.analyze(fixture)
        self.assertEqual("partial", status["state"], status)
        self.assertEqual(3, status["attempt"]["source_count"])
        self.assertEqual(3, len(result["windows"]))
        self.assertEqual(2, status["attempt"]["processed_windows"])
        self.assertEqual(1, status["attempt"]["blocked_windows"])
        self.assertNotEqual("ready", result["windows"][1]["state"])

  def test_missing_middle_without_receipt_bounds_stays_explicitly_unknown(self):
    fixture = self.fixture(unknown_middle_bounds=True)
    fixture.damage_middle("missing")
    status, result, _ = self.analyze(fixture)
    self.assertEqual("partial", status["state"], status)
    self.assertEqual(3, status["attempt"]["source_count"])
    self.assertEqual(3, len(result["windows"]))
    middle = result["windows"][1]
    self.assertNotEqual("ready", middle["state"])
    self.assertIsNone(middle["source_start_ns"])
    self.assertIsNone(middle["source_end_ns"])


if __name__ == "__main__":
  unittest.main()
