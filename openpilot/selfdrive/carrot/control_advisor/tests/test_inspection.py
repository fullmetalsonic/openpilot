import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.archive import SegmentArchive
from openpilot.selfdrive.carrot.control_advisor.inspection import inspect_session
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder


class InspectionTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.source, self.logs, self.session = [self.root / name for name in ("settings", "logs", "session")]
    for path in (self.source, self.logs, self.session):
      path.mkdir()
    for item in load_registry()["items"]:
      (self.source / item["key"]).write_bytes(b"0")
    (self.source / "CurrentRoute").write_bytes(b"00000000--0123456789")
    self.recorder = SettingsRecorder(self.source, self.session, "session", clock_ns=lambda: next(self.clock))
    self.clock = itertools.count(1)
    self.addCleanup(self.recorder.close)
    self.recorder.sample()
    (self.session / "recording.json").write_text(json.dumps({"session_id": "session", "source_mode": "synthetic"}))
    self.diagnostic = {"revision": 1, "window_start_ns": 2_000_000_000, "window_end_ns": 4_000_000_000,
                       "profile_id": "synthetic-inspection", "invalidated": False, "partial_window": False,
                       "observed_settings_revision": 1,
                       "route_observation": self.recorder.revision_metadata(1)["route_observation"]}
    data = json.dumps(self.diagnostic, sort_keys=True, separators=(",", ":")).encode()
    self.digest = hashlib.sha256(data).hexdigest()
    (self.session / "diagnostics").mkdir()
    self.result_path = self.session / "diagnostics" / f"1-{self.digest[:16]}.json"
    self.result_path.write_bytes(data)
    record = {key: value for key, value in self.diagnostic.items() if key not in ("observed_settings_revision", "route_observation")}
    self.reference = self.recorder.append_diagnostic({**record, "sha256": self.digest}, settings_revision=1)["journal"]
    _, log = _schema()
    events = []
    for stamp, service, values in (
        (1_000_000_000, "sentinel", {"type": "startOfRoute"}),
        (2_000_000_000, "carState", {"aEgo": .1}),
        (4_000_000_000, "carState", {"aEgo": .2}),
        (5_000_000_000, "sentinel", {"type": "endOfRoute"})):
      event = log.Event.new_message(logMonoTime=stamp, valid=True)
      payload = event.init(service)
      for key, value in values.items():
        setattr(payload, key, value)
      events.append(event.to_bytes())
    segment = self.logs / "00000000--0123456789--0"
    segment.mkdir()
    self.original = segment / "rlog"
    self.original.write_bytes(b"".join(events))
    self.archive = SegmentArchive(self.logs, self.session / "archive", session_id="session", source_mode="synthetic",
                                  byte_limit=1024 * 1024, max_files=16, free_reserve_bytes=0)
    self.addCleanup(self.archive.close)
    self.saved = self.archive.preserve(self.original, window_ns=(2_000_000_000, 4_000_000_000),
                                       diagnostic_revision=1, settings_revision=1, journal_ref=self.reference)
    self.marker = self.session / "archive" / self.saved["manifest_relative"]
    summary = {key: value for key, value in self.saved.items() if key not in ("source_relative", "decompressed_bytes")}
    self.recorder.append_archive(summary)

  def complete_job(self):
    self.recorder.append_job_status({
      "diagnostic_revision": 1, "settings_revision": 1, "state": "complete",
      "window_start_ns": 2_000_000_000, "window_end_ns": 4_000_000_000,
      "route_observation": self.diagnostic["route_observation"], "settings_history_reference": self.reference,
      "actual_boundary_gaps": [], "gap_tolerance_ns": 250_000_000, "missing_segments": [], "missing_segments_count": 0,
      "endpoint_start_covered": True, "endpoint_end_covered": True,
      "completion_scope": "archive_work_only_host_profile", "coverage_complete": False, "interpolation_applied": False,
    })

  def test_complete_session_checks_original_copy_diagnostic_and_history_without_writes(self):
    self.complete_job()
    self.recorder.close()
    before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
    result = inspect_session(self.session)
    self.assertEqual("verified", result["state"])
    self.assertEqual(1, result["archives"]["verified_segments"])
    self.assertFalse(result["coverage_complete"])
    self.assertFalse(result["consumer_applied"])
    self.assertEqual(before, {path: path.read_bytes() for path in before})

  def test_well_formed_but_unclosed_history_keeps_verified_copy_and_marks_partial(self):
    result = inspect_session(self.session)
    self.assertEqual("partial", result["state"])
    self.assertFalse(result["history"]["closed"])
    self.assertEqual(1, result["archives"]["verified_segments"])

  def test_changed_diagnostic_is_not_accepted_from_journal_digest_alone(self):
    self.recorder.close()
    self.result_path.write_bytes(b"{}")
    result = inspect_session(self.session)
    self.assertEqual("partial", result["state"])
    self.assertEqual(0, result["archives"]["verified_segments"])

  def test_cross_session_and_different_diagnostic_reference_are_rejected(self):
    self.recorder.close()
    original = self.marker.read_bytes()
    for mutation in (lambda m: m.update(session_id="another"),
                     lambda m: m["analysis"].update(settings_revision=2),
                     lambda m: m["settings_history_reference"].update(sequence=1)):
      manifest = json.loads(original)
      mutation(manifest)
      self.marker.write_text(json.dumps(manifest), encoding="utf-8")
      result = inspect_session(self.session)
      self.assertEqual("partial", result["state"])
      self.assertEqual(0, result["archives"]["verified_segments"])

  def test_orphan_partial_and_budget_exhaustion_do_not_report_verified(self):
    self.recorder.close()
    partial = self.session / "archive" / "unfinished"
    partial.mkdir()
    (partial / "rlog.partial").write_bytes(b"unfinished")
    result = inspect_session(self.session)
    self.assertEqual("partial", result["state"])
    self.assertEqual(1, result["archives"]["incomplete_or_invalid_segments"])
    with self.assertRaises(ValueError):
      inspect_session(self.session, byte_limit=10)

  def test_actual_inspection_cli_has_nonzero_status_for_incomplete_evidence(self):
    self.complete_job()
    self.recorder.close()
    command = [sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor", "inspect-session", str(self.session)]
    result = subprocess.run(command, cwd=Path(__file__).parents[5], capture_output=True, text=True, timeout=10)
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertEqual("verified", json.loads(result.stdout)["state"])
    self.result_path.write_bytes(b"{}")
    result = subprocess.run(command, cwd=Path(__file__).parents[5], capture_output=True, text=True, timeout=10)
    self.assertEqual(2, result.returncode, result.stderr)
    self.assertEqual("partial", json.loads(result.stdout)["state"])

  def test_missing_terminal_outcome_does_not_claim_verified_recording(self):
    self.recorder.close()
    result = inspect_session(self.session)
    self.assertEqual("partial", result["state"])
    self.assertEqual(["1"], result["preservation_jobs"]["missing_terminal_revisions"])

  def test_complete_job_cannot_replace_a_missing_original_copy(self):
    self.complete_job()
    self.recorder.close()
    source, destination = self.marker.parent.resolve(), (self.root / "removed-copy").resolve()
    self.assertTrue(source.is_relative_to(self.root.resolve()) and destination.is_relative_to(self.root.resolve()))
    source.rename(destination)
    result = inspect_session(self.session)
    self.assertEqual("partial", result["state"])
    self.assertEqual([1], result["preservation_jobs"]["unverified_complete_revisions"])

  def test_terminal_incomplete_job_survives_read_only_inspection(self):
    self.recorder.append_job_status({
      "diagnostic_revision": 1, "settings_revision": 1, "state": "timeout",
      "window_start_ns": 2_000_000_000, "window_end_ns": 4_000_000_000,
      "route_observation": self.diagnostic["route_observation"], "settings_history_reference": self.reference,
      "actual_boundary_gaps": [{"start_ns": 2_500_000_000, "end_ns": 3_500_000_000}],
      "gap_tolerance_ns": 250_000_000, "missing_segments": [1], "missing_segments_count": 1,
      "endpoint_start_covered": True, "endpoint_end_covered": True,
      "completion_scope": "archive_work_only_host_profile", "coverage_complete": False, "interpolation_applied": False,
    })
    self.recorder.close()
    result = inspect_session(self.session)
    self.assertEqual("partial", result["state"])
    self.assertEqual(1, result["archives"]["verified_segments"])
    self.assertEqual(1, result["preservation_jobs"]["incomplete_jobs"])
    self.assertEqual("timeout", result["preservation_jobs"]["items"][0]["state"])


if __name__ == "__main__":
  unittest.main()
