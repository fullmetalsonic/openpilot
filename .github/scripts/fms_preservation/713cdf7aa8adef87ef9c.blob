import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.archive import SegmentArchive
from openpilot.selfdrive.carrot.control_advisor.inspection import inspect_session
from openpilot.selfdrive.carrot.control_advisor.journal_snapshot import create_journal_snapshot, load_journal_snapshot
from openpilot.selfdrive.carrot.control_advisor.log_history import load_log_history
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder, load_history
from openpilot.selfdrive.carrot.control_advisor.tests.test_log_history import NativeSessionFixture


class JournalSnapshotTests(unittest.TestCase):
  def fixture(self, *, active=True):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    if active:
      with patch.object(SettingsRecorder, "close", lambda recorder: recorder.snapshot()):
        fixture = NativeSessionFixture(root)
      self.addCleanup(fixture.recorder.close)
    else:
      fixture = NativeSessionFixture(root)
    return fixture, root / "snapshot"

  def append_segment(self, fixture, index, start, end):
    revision = index + 1
    route = fixture.recorder.revision_metadata(1)["route_observation"]
    diagnostic = {"revision": revision, "window_start_ns": start, "window_end_ns": end,
                  "profile_id": "native-fixture", "invalidated": False, "partial_window": False,
                  "observed_settings_revision": 1, "route_observation": route}
    data = json.dumps(diagnostic, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(data).hexdigest()
    (fixture.session / "diagnostics" / f"{revision}-{digest[:16]}.json").write_bytes(data)
    record = {key: value for key, value in diagnostic.items() if key not in {"observed_settings_revision", "route_observation"}}
    reference = fixture.recorder.append_diagnostic({**record, "sha256": digest}, settings_revision=1)["journal"]
    _, log = _schema()
    frames = []
    for stamp, service, values in ((start - 1, "sentinel", {"type": "startOfSegment"}),
                                    (start, "carState", {"aEgo": .1}), (end, "carState", {"aEgo": .2}),
                                    (end + 1, "sentinel", {"type": "endOfSegment"})):
      event = log.Event.new_message(logMonoTime=stamp, valid=True)
      payload = event.init(service)
      for key, value in values.items():
        setattr(payload, key, value)
      frames.append(event.to_bytes())
    directory = fixture.logs / f'{route["route"]}--{index}'
    directory.mkdir()
    original = directory / "rlog"
    original.write_bytes(b"".join(frames))
    archive = SegmentArchive(fixture.logs, fixture.session / "archive", session_id="native-fixture", source_mode="live",
                              byte_limit=1024 * 1024, max_files=16, free_reserve_bytes=0)
    try:
      saved = archive.preserve(original, window_ns=(start, end), diagnostic_revision=revision,
                               settings_revision=1, journal_ref=reference)
    finally:
      archive.close()
    fixture.recorder.append_archive({key: value for key, value in saved.items() if key not in {"source_relative", "decompressed_bytes"}})
    return saved

  def test_checkpoint_only_captures_an_existing_fsynced_status(self):
    fixture, _ = self.fixture()
    before = fixture.recorder.snapshot()
    with patch("openpilot.selfdrive.carrot.control_advisor.settings_history.os.fsync", side_effect=AssertionError("writer I/O")), \
         patch("pathlib.Path.open", side_effect=AssertionError("file read")):
      token = fixture.recorder.checkpoint()
    self.assertEqual(before, fixture.recorder.snapshot())
    self.assertEqual(before["journal"]["bytes"], token["bytes"])
    self.assertEqual(before["journal"]["sha256"], token["chain_sha256"])
    token["bytes"] = 1
    self.assertEqual(before["journal"]["bytes"], fixture.recorder.checkpoint()["bytes"])

  def test_active_prefix_reuses_real_archive_without_fake_session_close(self):
    fixture, destination = self.fixture()
    token = fixture.recorder.checkpoint()
    before = {path: path.read_bytes() for path in fixture.session.rglob("*") if path.is_file()}
    result = create_journal_snapshot(fixture.session, destination, token)
    self.assertTrue(result["snapshot_prefix_complete"])
    self.assertFalse(result["source_session_closed"])
    self.assertEqual(1, len(result["archive_sources"]))
    self.assertTrue(Path(result["archive_sources"][0]["path"]).is_relative_to(fixture.session / "archive"))
    self.assertEqual({"recording.json", "settings.jsonl", "snapshot.json"}, {path.name for path in destination.iterdir()})
    self.assertEqual(before, {path: path.read_bytes() for path in before})
    sealed = load_journal_snapshot(fixture.session, destination)
    self.assertFalse(sealed["history"]["closed"])
    self.assertNotEqual("session_closed", sealed["history"]["last_record"]["type"])
    self.assertEqual("incomplete", load_history(destination / "settings.jsonl")["state"])
    inspected = inspect_session(fixture.session, session_snapshot=destination)
    self.assertEqual("verified", inspected["state"])
    self.assertFalse(inspected["source_session_closed"])
    linked = load_log_history(fixture.session, [fixture.source], session_snapshot=destination)
    self.assertEqual("linked", linked["state"], linked["reason_codes"])
    self.assertFalse(linked["source_session_closed"])
    self.assertEqual("unlinked", load_log_history(fixture.session, [fixture.source])["state"])

  def test_append_or_torn_suffix_after_checkpoint_cannot_change_the_snapshot(self):
    fixture, destination = self.fixture()
    token = fixture.recorder.checkpoint()
    fixture.time = 4_000_000_000
    (fixture.settings / "PathOffset").write_text("20")
    fixture.recorder.sample()
    fixture.recorder._stream.write(b'{"torn_next_record":')
    created = create_journal_snapshot(fixture.session, destination, token)
    self.assertEqual(token["bytes"], (destination / "settings.jsonl").stat().st_size)
    self.assertEqual(token["sequence"], created["sequence"])
    linked = load_log_history(fixture.session, [fixture.source], session_snapshot=destination)
    self.assertEqual("linked", linked["state"], linked["reason_codes"])
    self.assertEqual(0, linked["epochs"][0]["settings_raw"]["PathOffset"])

  def test_writer_can_append_while_the_child_is_reading_the_pinned_prefix(self):
    fixture, destination = self.fixture()
    token = fixture.recorder.checkpoint()
    start, finished = threading.Event(), threading.Event()
    failures = []
    def append():
      try:
        if not start.wait(3):
          raise AssertionError("prefix read did not start")
        fixture.time = 4_000_000_000
        fixture.recorder.sample()
      except Exception as exc:
        failures.append(exc)
      finally:
        finished.set()
    thread = threading.Thread(target=append)
    thread.start()
    original_read = os.read
    inode = (fixture.session / "settings.jsonl").stat().st_ino
    def read_during_append(fd, count):
      data = original_read(fd, count)
      if not start.is_set() and os.fstat(fd).st_ino == inode:
        start.set()
        if not finished.wait(3):
          raise AssertionError("concurrent append did not finish")
      return data
    try:
      with patch("openpilot.selfdrive.carrot.control_advisor.journal_snapshot.os.read", side_effect=read_during_append):
        result = create_journal_snapshot(fixture.session, destination, token)
    finally:
      start.set()
      thread.join(4)
    self.assertFalse(thread.is_alive())
    self.assertEqual([], failures)
    self.assertGreater(fixture.recorder.snapshot()["journal"]["bytes"], token["bytes"])
    self.assertEqual(token["sequence"], result["sequence"])

  def test_only_an_unchanged_active_tail_reports_a_missing_right_bracket(self):
    for changed in (False, True):
      with self.subTest(changed=changed):
        fixture, destination = self.fixture()
        if changed:
          (fixture.settings / "PathOffset").write_text("20")
          fixture.time = 3_500_000_000
          fixture.recorder.sample()
        fixture.time = 4_100_000_000
        create_journal_snapshot(fixture.session, destination, fixture.recorder.checkpoint())
        result = load_log_history(fixture.session, [fixture.source], session_snapshot=destination)
        self.assertEqual("linked", result["state"], result["reason_codes"])
        self.assertEqual(not changed, "session_right_bracket_missing" in result["reason_codes"])

  def test_future_uncommitted_archive_and_diagnostic_are_not_scanned(self):
    fixture, destination = self.fixture()
    token = fixture.recorder.checkpoint()
    incomplete = fixture.session / "archive" / ("e" * 32)
    incomplete.mkdir()
    (incomplete / "rlog.partial").write_bytes(b"not committed")
    (fixture.session / "diagnostics" / "999-orphan.json").write_bytes(b"not committed")
    result = create_journal_snapshot(fixture.session, destination, token)
    inspected = inspect_session(fixture.session, session_snapshot=destination)
    self.assertEqual(1, len(result["archive_sources"]))
    self.assertEqual("verified", inspected["state"])
    self.assertEqual(0, inspected["diagnostics"]["orphan_files"])
    self.assertEqual(1, len(inspected["archives"]["items"]))

  def test_missing_or_corrupt_middle_copy_stays_in_expected_inventory(self):
    for damage in ("missing", "corrupt"):
      with self.subTest(damage=damage):
        # Inspect both valid copies before the failed decoder spends its allowance.
        with patch("openpilot.selfdrive.carrot.control_advisor.archive.uuid.uuid4", return_value=SimpleNamespace(hex="1" * 32)):
          fixture, destination = self.fixture()
        with patch("openpilot.selfdrive.carrot.control_advisor.archive.uuid.uuid4", return_value=SimpleNamespace(hex="f" * 32)):
          middle = self.append_segment(fixture, 1, 60_000_000_000, 120_000_000_000)
        with patch("openpilot.selfdrive.carrot.control_advisor.archive.uuid.uuid4", return_value=SimpleNamespace(hex="2" * 32)):
          last = self.append_segment(fixture, 2, 120_050_000_000, 180_000_000_000)
        payload = fixture.session / "archive" / middle["manifest_relative"]
        payload = payload.parent / "rlog"
        if damage == "missing":
          # Keep a recoverable copy outside the archive inventory.
          payload.rename(fixture.root / "retained-middle-rlog")
        else:
          with payload.open("r+b") as stream:
            stream.write(b"corrupt")
        fixture.time = 181_000_000_000
        result = create_journal_snapshot(fixture.session, destination, fixture.recorder.checkpoint())
        self.assertEqual(3, result["committed_archive_count"])
        self.assertEqual(2, len(result["archive_sources"]))
        inventory = {entry["segment"]: entry for entry in result["expected_archive_sources"]}
        self.assertEqual({0, 1, 2}, set(inventory))
        self.assertEqual("unavailable", inventory[1]["state"])
        self.assertIsNotNone(inventory[1]["reason"])
        self.assertFalse(inventory[1]["coverage_verified"])
        self.assertEqual("receipt_declared", inventory[1]["range_kind"])
        self.assertEqual((60_000_000_000, 120_000_000_000), (inventory[1]["start_ns"], inventory[1]["end_ns"]))
        self.assertEqual("available", inventory[2]["state"])
        self.assertEqual(last["sha256"], inventory[2]["sha256"])
        self.assertTrue(inventory[2]["coverage_verified"])
        self.assertEqual("verified_car_state", inventory[2]["range_kind"])

  def test_missing_receipt_bounds_never_fall_back_to_diagnostic_request_window(self):
    append = SettingsRecorder.append_archive
    def omit_bounds(recorder, receipt):
      return append(recorder, {key: value for key, value in receipt.items() if key not in {"start_mono_ns", "end_mono_ns"}})
    with patch.object(SettingsRecorder, "append_archive", omit_bounds):
      fixture, destination = self.fixture()
    marker = fixture.session / "archive" / fixture.saved["manifest_relative"]
    (marker.parent / "rlog").rename(fixture.root / "retained-rlog")
    result = create_journal_snapshot(fixture.session, destination, fixture.recorder.checkpoint())
    expected, = result["expected_archive_sources"]
    self.assertEqual("unavailable", expected["state"])
    self.assertEqual("unknown", expected["range_kind"])
    self.assertIsNone(expected["start_ns"])
    self.assertIsNone(expected["end_ns"])
    self.assertFalse(expected["coverage_verified"])

  def test_a_prefix_before_archive_receipt_is_not_a_completed_segment(self):
    fixture, destination = self.fixture()
    records = load_history(fixture.session / "settings.jsonl", include_records=True)["records"]
    prior = next(entry for entry in records if entry["record"]["type"] == "diagnostic")
    token = {**fixture.recorder.checkpoint(), "sequence": prior["sequence"], "chain_sha256": prior["sha256"], "bytes": prior["bytes"]}
    with self.assertRaisesRegex(ValueError, "snapshot_no_committed_archive"):
      create_journal_snapshot(fixture.session, destination, token)
    self.assertFalse((destination / "snapshot.json").exists())

  def test_duplicate_destination_is_never_overwritten_and_duplicate_receipts_do_not_double_count(self):
    fixture, destination = self.fixture()
    summary = {key: value for key, value in fixture.saved.items() if key not in {"source_relative", "decompressed_bytes"}}
    fixture.recorder.append_archive(summary)
    token = fixture.recorder.checkpoint()
    result = create_journal_snapshot(fixture.session, destination, token)
    self.assertEqual(1, len(result["archive_sources"]))
    before = {path: path.read_bytes() for path in destination.iterdir()}
    with self.assertRaises(FileExistsError):
      create_journal_snapshot(fixture.session, destination, token)
    self.assertEqual(before, {path: path.read_bytes() for path in before})

  def test_tampered_token_snapshot_and_origin_are_rejected(self):
    fixture, destination = self.fixture()
    token = fixture.recorder.checkpoint()
    with self.assertRaisesRegex(ValueError, "invalid_history_checkpoint"):
      create_journal_snapshot(fixture.session, destination, {**token, "durable": False})
    create_journal_snapshot(fixture.session, destination, token)
    path = destination / "settings.jsonl"
    original = path.read_bytes()
    path.write_bytes(original[:-1] + b" ")
    with self.assertRaises(ValueError):
      load_journal_snapshot(fixture.session, destination)
    path.write_bytes(original)
    origin = fixture.session / "settings.jsonl"
    data = origin.read_bytes()
    # Replace a byte in the prefix while leaving the immutable copy intact.
    with origin.open("r+b") as stream:
      stream.write(b" " if data[:1] != b" " else b"{")
    with self.assertRaisesRegex(ValueError, "snapshot_prefix_mismatch"):
      load_journal_snapshot(fixture.session, destination)

  def test_fsync_failure_leaves_no_complete_marker(self):
    fixture, destination = self.fixture()
    token = fixture.recorder.checkpoint()
    with patch("openpilot.selfdrive.carrot.control_advisor.journal_snapshot.os.fsync", side_effect=OSError("fixture fsync failure")):
      with self.assertRaises(OSError):
        create_journal_snapshot(fixture.session, destination, token)
    self.assertFalse((destination / "snapshot.json").exists())

  def test_normal_closed_session_contract_and_snapshot_remain_compatible(self):
    fixture, destination = self.fixture(active=False)
    self.assertEqual("complete", load_history(fixture.session / "settings.jsonl")["state"])
    self.assertEqual("verified", inspect_session(fixture.session)["state"])
    token = fixture.recorder.checkpoint()
    before = hashlib.sha256((fixture.session / "settings.jsonl").read_bytes()).hexdigest()
    result = create_journal_snapshot(fixture.session, destination, token)
    self.assertTrue(result["source_session_closed"])
    self.assertEqual("linked", load_log_history(fixture.session, [fixture.source])["state"])
    self.assertEqual(before, hashlib.sha256((fixture.session / "settings.jsonl").read_bytes()).hexdigest())


if __name__ == "__main__":
  unittest.main()
