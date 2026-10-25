import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.settings_history import KEYS, SettingsRecorder, load_history


ROUTE_A = "2026-10-25--12-00-00"
ROUTE_B = "00000001--abcdefghij"


class Clock:
  def __init__(self):
    self.now = 1_000_000_000

  def __call__(self):
    self.now += 1_000
    return self.now


class SettingsHistoryTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.source = self.root / "params"
    self.output = self.root / "evidence"
    self.source.mkdir()
    self.output.mkdir()
    self.clock = Clock()

  def recorder(self, **kwargs):
    recorder = SettingsRecorder(self.source, self.output, "test-session", clock_ns=self.clock, **kwargs)
    self.addCleanup(recorder.close)
    return recorder

  def write_all(self, value="100"):
    for key in KEYS:
      (self.source / key).write_text(value)

  def records(self):
    return [json.loads(line)["record"] for line in (self.output / "settings.jsonl").read_text().splitlines()]

  def test_source_values_are_allowlisted_not_defaults_and_same_values_are_not_atomic_proof(self):
    (self.source / KEYS[0]).write_text("2800")
    (self.source / "ApiToken").write_text("private-do-not-record")
    recorder = self.recorder()
    first = recorder.sample()
    self.assertEqual(47, len(first["settings"]))
    self.assertEqual(2800, first["settings"][KEYS[0]])
    self.assertIsNone(first["settings"][KEYS[1]])
    self.assertEqual("partial", first["state"])
    self.assertFalse(first["atomic_snapshot"])
    self.assertFalse(first["consumer_applied"])
    self.assertFalse(first["history_complete"])
    again = recorder.sample()
    self.assertEqual(first["revision"], again["revision"])
    self.assertEqual(2, again["poll_sequence"])
    self.assertNotIn("settings", self.records()[-1])
    contents = (self.output / "settings.jsonl").read_text()
    self.assertNotIn("ApiToken", contents)
    self.assertNotIn("private-do-not-record", contents)
    first["settings"][KEYS[0]] = -10
    self.assertEqual(2800, recorder.snapshot()["settings"][KEYS[0]])

  def test_atomic_file_replacement_changes_revision_with_observed_interval(self):
    self.write_all()
    recorder = self.recorder()
    first = recorder.sample()
    replacement = self.source / "new-value"
    replacement.write_bytes(b"2800\n")
    os.replace(replacement, self.source / KEYS[0])
    second = recorder.sample()
    self.assertEqual("observed", second["state"])
    self.assertEqual(first["revision"] + 1, second["revision"])
    self.assertEqual([KEYS[0]], second["changed_keys"])
    bounds = second["observation"]["change_interval"]
    self.assertEqual(first["observation"]["begin_ns"], bounds["after_ns"])
    self.assertEqual(second["observation"]["end_ns"], bounds["before_ns"])
    value = self.records()[-1]["settings"][KEYS[0]]
    self.assertEqual("2800\n", value["raw"])
    self.assertLess(value["begin_ns"], value["end_ns"])

  def test_change_after_early_key_read_inside_previous_scan_is_in_uncertainty_interval(self):
    self.write_all()
    recorder = self.recorder()
    real_read = os.read
    reads = 0
    changed_at = None

    def replace_earlier_key(fd, length):
      nonlocal reads, changed_at
      data = real_read(fd, length)
      reads += 1
      if reads == 2:
        replacement = self.source / "new-value"
        replacement.write_bytes(b"2800")
        os.replace(replacement, self.source / KEYS[0])
        changed_at = self.clock.now
      return data

    with patch("openpilot.selfdrive.carrot.control_advisor.settings_history.os.read", side_effect=replace_earlier_key):
      first = recorder.sample()
    self.assertEqual(100, first["settings"][KEYS[0]])
    self.assertLess(first["observation"]["begin_ns"], changed_at)
    self.assertLess(changed_at, first["observation"]["end_ns"])
    changed = recorder.sample()
    self.assertEqual(2800, changed["settings"][KEYS[0]])
    interval = changed["observation"]["change_interval"]
    self.assertLessEqual(interval["after_ns"], changed_at)
    self.assertGreaterEqual(interval["before_ns"], changed_at)
    self.assertEqual(first["observation"]["end_ns"], changed["observation"]["previous_end_ns"])

  def test_gap_is_new_revision_even_without_observed_value_change(self):
    self.write_all()
    recorder = self.recorder()
    first = recorder.sample()
    self.clock.now += 3_000_000_000
    next_value = recorder.sample()
    self.assertTrue(next_value["observation"]["gap"])
    self.assertEqual("polling_gap", next_value["reason"])
    self.assertEqual(first["revision"] + 1, next_value["revision"])

  def test_missing_source_is_not_created_and_recovery_is_an_observation(self):
    missing = self.root / "never-created"
    recorder = SettingsRecorder(missing, self.output, "test-session", clock_ns=self.clock)
    self.addCleanup(recorder.close)
    first = recorder.sample()
    self.assertEqual("unavailable", first["state"])
    self.assertFalse(missing.exists())
    missing.mkdir()
    (missing / KEYS[0]).write_text("0")
    recovered = recorder.sample()
    self.assertEqual(first["revision"] + 1, recovered["revision"])
    self.assertEqual(0, recovered["settings"][KEYS[0]])
    self.assertIsNone(recovered["settings"][KEYS[1]])

  def test_invalid_utf8_nonnumeric_and_overlong_values_are_not_numbers(self):
    (self.source / KEYS[0]).write_bytes(b"\xff")
    (self.source / KEYS[1]).write_text("NaN")
    (self.source / KEYS[2]).write_text("1" * 257)
    (self.source / KEYS[3]).write_text("1e999")
    recorder = self.recorder()
    values = recorder.sample()
    self.assertTrue(all(values["settings"][key] is None for key in KEYS[:4]))
    entries = self.records()[-1]["settings"]
    self.assertEqual("error", entries[KEYS[0]]["status"])
    self.assertEqual("invalid", entries[KEYS[1]]["status"])
    self.assertEqual("NaN", entries[KEYS[1]]["raw"])
    self.assertIsNone(entries[KEYS[2]]["raw"])

  def test_source_directory_identity_replacement_stays_unknown(self):
    recorder = self.recorder()
    recorder.sample()
    self.source.rename(self.root / "old-params")
    self.source.mkdir()
    (self.source / KEYS[0]).write_text("999")
    observed = recorder.sample()
    self.assertEqual("source_changed_or_invalid", observed["source_status"])
    self.assertTrue(all(value is None for value in observed["settings"].values()))
    self.assertEqual("source_changed_or_invalid", recorder.sample()["source_status"])

  def test_key_link_is_rejected_and_explicit_root_link_is_pinned(self):
    outside = self.root / "outside"
    outside.mkdir()
    secret = outside / "key"
    secret.write_text("secret-value")
    try:
      (self.source / KEYS[0]).symlink_to(secret)
      linked = self.root / "d"
      linked.symlink_to(self.source, target_is_directory=True)
    except OSError as exc:
      if os.name != "nt":
        raise
      # Windows without symlink privilege still supports directory junctions.
      linked = self.root / "d"
      for name, target in ((self.source / KEYS[0], outside), (linked, self.source)):
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(name), str(target)], capture_output=True)
        self.assertEqual(0, created.returncode, (exc, created.stderr))
    recorder = SettingsRecorder(linked, self.output, "test-session", clock_ns=self.clock)
    self.addCleanup(recorder.close)
    self.assertIsNone(recorder.sample()["settings"][KEYS[0]])
    self.assertNotIn("secret-value", (self.output / "settings.jsonl").read_text())
    if linked.is_symlink():
      linked.unlink()
      linked.symlink_to(outside, target_is_directory=True)
    else:
      linked.rmdir()
      created = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(outside)], capture_output=True)
      self.assertEqual(0, created.returncode, created.stderr)
    self.assertEqual("source_changed_or_invalid", recorder.sample()["source_status"])

  def test_write_failure_keeps_prefix_but_never_claims_durable_new_revision(self):
    recorder = self.recorder()
    before = recorder.snapshot()
    with patch("openpilot.selfdrive.carrot.control_advisor.settings_history.os.fsync", side_effect=OSError("disk failed")):
      failed = recorder.sample()
    self.assertEqual("failed", failed["state"])
    self.assertFalse(failed["journal"]["durable"])
    self.assertEqual(before["durable_revision"], failed["durable_revision"])
    bytes_after = (self.output / "settings.jsonl").read_bytes()
    recorder.sample()
    self.assertEqual(bytes_after, (self.output / "settings.jsonl").read_bytes())

  def test_event_budget_and_existing_journal_are_not_overwritten(self):
    recorder = self.recorder(max_events=1)
    initial = (self.output / "settings.jsonl").read_bytes()
    self.assertEqual("failed", recorder.sample()["state"])
    self.assertEqual(initial, (self.output / "settings.jsonl").read_bytes())
    with self.assertRaises(FileExistsError):
      SettingsRecorder(self.source, self.output, "new-session")

  def test_partial_write_is_retained_and_recovery_exposes_only_verified_prefix(self):
    recorder = self.recorder()
    real = recorder._stream

    class FailingStream:
      written = False

      def write(self, data):
        if self.written:
          raise OSError("disk full")
        self.written = True
        return real.write(data[:13])

      def close(self):
        real.close()

    recorder._stream = FailingStream()
    self.assertEqual("failed", recorder.sample()["state"])
    path = self.output / "settings.jsonl"
    current = path.read_bytes()
    recovered = load_history(path)
    self.assertEqual("incomplete", recovered["state"])
    self.assertEqual(1, recovered["verified_events"])
    self.assertEqual(current, path.read_bytes())

  def test_byte_budget_rejects_before_appending_and_clock_bounds_are_preserved(self):
    recorder = self.recorder(max_journal_bytes=1800)
    before = (self.output / "settings.jsonl").read_bytes()
    self.assertEqual("failed", recorder.sample()["state"])
    self.assertEqual(before, (self.output / "settings.jsonl").read_bytes())
    self.assertLessEqual(len(before), 1800)

  def test_file_changed_during_read_is_unknown_not_a_mixed_value(self):
    (self.source / KEYS[0]).write_text("100")
    recorder = self.recorder()
    real_read = os.read
    changed = False

    def mutate(fd, length):
      nonlocal changed
      data = real_read(fd, length)
      if not changed:
        changed = True
        (self.source / KEYS[0]).write_text("99999")
      return data

    with patch("openpilot.selfdrive.carrot.control_advisor.settings_history.os.read", side_effect=mutate):
      sampled = recorder.sample()
    self.assertIsNone(sampled["settings"][KEYS[0]])
    self.assertIsNone(self.records()[-1]["settings"][KEYS[0]]["raw"])

  def test_slow_sampling_itself_is_reported_as_observation_gap(self):
    self.write_all()
    recorder = self.recorder()
    first = recorder.sample()
    real_read = os.read
    delayed = False

    def delay(fd, length):
      nonlocal delayed
      if not delayed:
        delayed = True
        self.clock.now += 3_000_000_000
      return real_read(fd, length)

    with patch("openpilot.selfdrive.carrot.control_advisor.settings_history.os.read", side_effect=delay):
      sampled = recorder.sample()
    self.assertTrue(sampled["observation"]["gap"])
    self.assertEqual(first["revision"] + 1, sampled["revision"])

  def test_source_and_journal_cannot_overlap(self):
    with self.assertRaisesRegex(ValueError, "overlap"):
      SettingsRecorder(self.source, self.source, "test-session")
    self.assertFalse((self.source / "settings.jsonl").exists())

  def test_diagnostic_and_archive_bind_to_committed_prefix_and_session(self):
    (self.source / "CurrentRoute").write_text(ROUTE_A)
    recorder = self.recorder()
    recorder.sample()
    diagnostic = {"revision": 1, "window_start_ns": 10, "window_end_ns": 20, "profile_id": "unvalidated",
                  "invalidated": False, "partial_window": False, "sha256": hashlib.sha256(b"diagnostic").hexdigest()}
    recorded = recorder.append_diagnostic(diagnostic)
    reference = recorded["journal"]
    archive = {"state": "complete", "session_id": "test-session", "route": ROUTE_A, "segment": 1, "sha256": "a" * 64,
               "bytes": 12, "settings_history_reference": reference}
    recorder.append_archive(archive)
    with self.assertRaisesRegex(ValueError, "reference_not_committed"):
      recorder.append_archive({**archive, "settings_history_reference": {**reference, "sha256": "b" * 64}})
    with self.assertRaisesRegex(ValueError, "session_mismatch"):
      recorder.append_archive({**archive, "session_id": "another-session"})
    recorder.close()
    recovered = load_history(self.output / "settings.jsonl")
    self.assertEqual("complete", recovered["state"])
    self.assertTrue(recovered["closed"])
    self.assertEqual("test-session", recovered["session_id"])
    self.assertEqual(diagnostic, recovered["diagnostics"][0])
    self.assertEqual(reference["sha256"], recovered["references"][str(reference["sequence"])]["sha256"])
    self.assertEqual({**diagnostic, **recorder.revision_metadata(1)}, recovered["diagnostic_references"][str(reference["sequence"])])
    self.assertFalse(recovered["history_complete"])

  def test_complete_line_interruption_is_unclosed_with_usable_diagnostic_prefix(self):
    recorder = self.recorder()
    recorder.sample()
    diagnostic = {"revision": 7, "window_start_ns": 10, "window_end_ns": 20, "profile_id": "unvalidated",
                  "invalidated": False, "partial_window": False, "sha256": hashlib.sha256(b"diagnostic").hexdigest()}
    reference = recorder.append_diagnostic(diagnostic)["journal"]
    path = self.output / "settings.jsonl"
    original = path.read_bytes()
    recovered = load_history(path)
    self.assertEqual("incomplete", recovered["state"])
    self.assertEqual("history_session_unclosed", recovered["reason"])
    self.assertFalse(recovered["closed"])
    self.assertEqual(7, recovered["diagnostic_references"][str(reference["sequence"])]["revision"])
    self.assertEqual(reference["sha256"], recovered["references"][str(reference["sequence"])]["sha256"])
    self.assertEqual(original, path.read_bytes())

  def test_queued_diagnostic_preserves_original_committed_settings_revision(self):
    recorder = self.recorder()
    first = recorder.sample()
    (self.source / KEYS[0]).write_text("2200")
    later = recorder.sample()
    self.assertGreater(later["revision"], first["revision"])
    diagnostic = {"revision": 1, "window_start_ns": 10, "window_end_ns": 20, "profile_id": "unvalidated",
                  "invalidated": False, "partial_window": False, "sha256": hashlib.sha256(b"diagnostic").hexdigest()}
    saved = recorder.append_diagnostic(diagnostic, settings_revision=first["revision"])
    self.assertEqual(first["revision"], self.records()[-1]["settings_revision"])
    with self.assertRaisesRegex(ValueError, "revision_not_committed"):
      recorder.append_diagnostic(diagnostic, settings_revision=999)
    with self.assertRaisesRegex(ValueError, "revision_not_committed"):
      recorder.append_diagnostic(diagnostic, settings_revision=True)
    recovered = load_history(self.output / "settings.jsonl")
    self.assertEqual(first["revision"], recovered["diagnostic_references"][str(saved["journal"]["sequence"])]["settings_revision"])

  def test_route_change_revises_context_without_entering_parameter_maps(self):
    self.write_all()
    (self.source / "CurrentRoute").write_text(ROUTE_A)
    recorder = self.recorder()
    first = recorder.sample()
    self.assertEqual(ROUTE_A, first["route_observation"]["route"])
    (self.source / "CurrentRoute").write_text(ROUTE_B)
    changed = recorder.sample()
    self.assertEqual(first["revision"] + 1, changed["revision"])
    self.assertTrue(changed["route_changed"])
    self.assertEqual([], changed["changed_keys"])
    self.assertNotIn("CurrentRoute", changed["settings"])
    self.assertEqual(47, len(changed["settings"]))
    frozen = changed["route_observation"]
    stable = recorder.sample()
    self.assertFalse(stable["route_changed"])
    self.assertEqual(changed["revision"], stable["revision"])
    self.assertEqual(frozen, stable["route_observation"])
    self.assertEqual(ROUTE_B, self.records()[-1]["route_observation"]["route"])
    self.assertGreater(self.records()[-1]["route_observation"]["begin_ns"], frozen["begin_ns"])

  def test_missing_invalid_and_linked_routes_never_invent_identity(self):
    recorder = self.recorder()
    first = recorder.sample()
    self.assertIsNone(first["route_observation"]["route"])
    self.assertEqual("missing", first["route_observation"]["state"])
    (self.source / "CurrentRoute").write_text("../private-outside-route")
    invalid = recorder.sample()
    self.assertIsNone(invalid["route_observation"]["route"])
    self.assertEqual("invalid", invalid["route_observation"]["state"])
    self.assertNotIn("private-outside-route", (self.output / "settings.jsonl").read_text())
    self.assertGreater(invalid["revision"], first["revision"])

  def test_queued_old_revision_keeps_route_and_unknown_route_cannot_authorize_archive(self):
    (self.source / "CurrentRoute").write_text(ROUTE_A)
    recorder = self.recorder()
    first = recorder.sample()
    (self.source / "CurrentRoute").write_text(ROUTE_B)
    later = recorder.sample()
    diagnostic = {"revision": 1, "window_start_ns": 10, "window_end_ns": 20, "profile_id": "unvalidated",
                  "invalidated": False, "partial_window": False, "sha256": hashlib.sha256(b"diagnostic").hexdigest()}
    saved = recorder.append_diagnostic(diagnostic, settings_revision=first["revision"])
    route = self.records()[-1]["route_observation"]
    self.assertEqual(ROUTE_A, route["route"])
    metadata = recorder.revision_metadata(first["revision"])
    metadata["route_observation"]["route"] = "changed-copy"
    self.assertEqual(ROUTE_A, recorder.revision_metadata(first["revision"])["route_observation"]["route"])
    with self.assertRaisesRegex(ValueError, "diagnostic_route_mismatch"):
      recorder.append_diagnostic(diagnostic, settings_revision=first["revision"], route_observation=later["route_observation"])
    recovered = load_history(self.output / "settings.jsonl")
    self.assertEqual(ROUTE_A, recovered["diagnostic_references"][str(saved["journal"]["sequence"])]["route_observation"]["route"])
    archive = {"state": "complete", "session_id": "test-session", "route": ROUTE_B,
               "settings_history_reference": saved["journal"]}
    with self.assertRaisesRegex(ValueError, "diagnostic_route_mismatch"):
      recorder.append_archive(archive)
    (self.source / "CurrentRoute").unlink()
    missing = recorder.sample()
    unknown = recorder.append_diagnostic(diagnostic, settings_revision=missing["revision"])
    with self.assertRaisesRegex(ValueError, "diagnostic_route_mismatch"):
      recorder.append_archive({**archive, "route": None, "settings_history_reference": unknown["journal"]})

  def test_preservation_job_status_is_bound_and_work_completion_keeps_actual_gaps(self):
    (self.source / "CurrentRoute").write_text(ROUTE_A)
    recorder = self.recorder()
    settings = recorder.sample()
    diagnostic = {"revision": 1, "window_start_ns": 1_000_000_000, "window_end_ns": 2_000_000_000,
                  "profile_id": "unvalidated", "invalidated": False, "partial_window": False,
                  "sha256": hashlib.sha256(b"diagnostic").hexdigest()}
    reference = recorder.append_diagnostic(diagnostic)["journal"]
    gap = {"start_ns": 1_500_000_000, "end_ns": 1_550_000_000}
    job = {"diagnostic_revision": 1, "settings_revision": settings["revision"], "state": "complete",
           "window_start_ns": diagnostic["window_start_ns"], "window_end_ns": diagnostic["window_end_ns"],
           "route_observation": settings["route_observation"], "settings_history_reference": reference,
           "actual_boundary_gaps": [gap], "gap_tolerance_ns": 250_000_000,
           "missing_segments": [], "missing_segments_count": 0, "endpoint_start_covered": True,
           "endpoint_end_covered": True, "completion_scope": "archive_work_only_host_profile",
           "coverage_complete": False, "interpolation_applied": False}
    recorder.append_job_status(job)
    recovered = load_history(self.output / "settings.jsonl")
    preserved = recovered["preservation_jobs"]["1"]
    self.assertEqual("complete", preserved["state"])
    self.assertEqual([gap], preserved["actual_boundary_gaps"])
    self.assertFalse(preserved["coverage_complete"])
    self.assertFalse(preserved["interpolation_applied"])
    timeout = {**job, "state": "timeout", "missing_segments": [1, 2], "missing_segments_count": 3,
               "endpoint_end_covered": False}
    recorder.append_job_status(timeout)
    self.assertEqual(timeout, load_history(self.output / "settings.jsonl")["preservation_jobs"]["1"])
    for invalid in ({**job, "settings_revision": 999}, {**job, "window_end_ns": 3_000_000_000},
                    {**job, "route_observation": {**job["route_observation"], "route": ROUTE_B}},
                    {**job, "coverage_complete": True}, {**job, "missing_segments": [1], "missing_segments_count": 1},
                    {**job, "actual_boundary_gaps": [{"start_ns": 1, "end_ns": 500_000_001}]}):
      with self.subTest(invalid=invalid), self.assertRaises(ValueError):
        recorder.append_job_status(invalid)
    recorder.close()
    self.assertTrue(load_history(self.output / "settings.jsonl")["closed"])

  def test_preservation_job_reference_must_be_a_diagnostic_and_lists_are_bounded(self):
    recorder = self.recorder()
    settings = recorder.sample()
    job = {"diagnostic_revision": 1, "settings_revision": settings["revision"], "state": "incomplete",
           "window_start_ns": 1, "window_end_ns": 2, "route_observation": settings["route_observation"],
           "settings_history_reference": settings["journal"], "actual_boundary_gaps": [], "gap_tolerance_ns": 250_000_000,
           "missing_segments": [], "missing_segments_count": 0, "endpoint_start_covered": False,
           "endpoint_end_covered": False, "completion_scope": "archive_work_only_host_profile",
           "coverage_complete": False, "interpolation_applied": False}
    with self.assertRaisesRegex(ValueError, "binding_mismatch"):
      recorder.append_job_status(job)
    with self.assertRaises(ValueError):
      recorder.append_job_status({**job, "missing_segments": list(range(129)), "missing_segments_count": 129})

  def test_tampered_and_truncated_suffix_are_retained_with_verified_prefix(self):
    recorder = self.recorder()
    recorder.sample()
    recorder.close()
    path = self.output / "settings.jsonl"
    original = path.read_bytes()
    path.write_bytes(original + b'{"partial":')
    incomplete = load_history(path)
    self.assertEqual("incomplete", incomplete["state"])
    self.assertEqual(3, incomplete["verified_events"])
    self.assertEqual(original + b'{"partial":', path.read_bytes())
    corrupt = original.replace(b'"test-session"', b'"fake-session"', 1)
    path.write_bytes(corrupt)
    self.assertEqual("corrupt", load_history(path)["state"])
    self.assertEqual(corrupt, path.read_bytes())


if __name__ == "__main__":
  unittest.main()
