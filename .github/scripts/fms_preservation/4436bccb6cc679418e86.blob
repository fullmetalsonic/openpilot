"""Native synthetic closed-log preservation, including interrupted writes."""
import bz2
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import archive
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema


ROUTE = "00000001--abcdef0123"
JOURNAL = {"path": "settings.jsonl", "sequence": 3, "sha256": "a" * 64, "bytes": 100, "durable": True}


class ArchiveTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    _, cls.log = _schema()
    import zstandard
    cls.zstandard = zstandard

  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.logs = self.root / "logs"
    self.session = self.root / "session"
    self.logs.mkdir()
    self.session.mkdir()
    self.archives = []

  def instance(self, **changes):
    values = {"session_id": "synthetic-session", "source_mode": "synthetic", "byte_limit": 2 * 1024 * 1024,
              "max_files": 30, "free_reserve_bytes": 0, **changes}
    instance = archive.SegmentArchive(self.logs, self.session / "archive", **values)
    self.addCleanup(instance.close)
    self.archives.append(instance)
    return instance

  def event(self, service, mono, **values):
    event = self.log.Event.new_message(logMonoTime=mono, valid=True)
    data = event.init(service)
    for key, value in values.items():
      setattr(data, key, value)
    return event

  def source(self, *, suffix=".zst", segment=0, closed=True, start=True, trailing=False, mono_offset=0):
    events = [self.event("initData", 10, gitCommit="1" * 40)]
    if start:
      events.append(self.event("sentinel", 20 + mono_offset, type="startOfRoute" if segment == 0 else "startOfSegment"))
    events += [self.event("carState", 40 + mono_offset, canValid=True), self.event("carState", 60 + mono_offset, canValid=True)]
    if closed:
      events.append(self.event("sentinel", 100 + mono_offset, type="endOfSegment"))
    if trailing:
      events.append(self.event("carState", 110 + mono_offset, canValid=True))
    payload = b"".join(event.to_bytes() for event in events)
    if suffix == ".zst":
      payload = self.zstandard.ZstdCompressor(write_checksum=True).compress(payload)
    elif suffix == ".bz2":
      payload = bz2.compress(payload)
    directory = self.logs / f"{ROUTE}--{segment}"
    directory.mkdir(exist_ok=True)
    path = directory / ("rlog" + suffix)
    path.write_bytes(payload)
    return path

  def preserve(self, store, source, **changes):
    return store.preserve(source, **{"window_ns": (35, 65), "diagnostic_revision": 1,
                                    "settings_revision": 0, "journal_ref": JOURNAL, **changes})

  def test_three_native_formats_preserve_and_verify_without_original(self):
    store = self.instance()
    for index, suffix in enumerate(("", ".bz2", ".zst")):
      with self.subTest(suffix=suffix):
        source = self.source(suffix=suffix, segment=index)
        original = source.read_bytes()
        record = self.preserve(store, source)
        self.assertEqual("complete", record["state"])
        self.assertEqual(hashlib.sha256(original).hexdigest(), record["sha256"])
        self.assertEqual(original, source.read_bytes())
        directory = store.archive_root / record["archive_id"]
        manifest = json.loads((directory / "archive.json").read_text(encoding="utf-8"))
        self.assertEqual(JOURNAL, manifest["settings_history_reference"])
        self.assertEqual(6, len(manifest["code"]["schema_sha256"]))
        self.assertEqual("startOfRoute" if index == 0 else "startOfSegment", manifest["coverage"]["start_sentinel"])
        self.assertGreater(record["decompressed_bytes"], 0)
        source.unlink()
        verified = archive.verify_preserved_segment(directory, byte_limit=store.byte_limit)
        self.assertEqual(record, verified)
    self.assertEqual(3, store.status()["complete_segments"])
    self.assertEqual(6, store.status()["file_count"])

  def test_lock_missing_end_missing_start_or_truncated_frame_never_complete(self):
    store = self.instance()
    for values, expected in (({"closed": False}, "missing_end"), ({"start": False}, "missing_start"),
                             ({"trailing": True}, "after_end")):
      source = self.source(**values)
      with self.subTest(values=values):
        with self.assertRaisesRegex(ValueError, expected):
          self.preserve(store, source)
    source = self.source()
    lock = source.parent / "rlog.lock"
    lock.touch()
    with self.assertRaisesRegex(ValueError, "source_open"):
      self.preserve(store, source)
    lock.unlink()
    source.write_bytes(source.read_bytes()[:-1])
    with self.assertRaises(ValueError):
      self.preserve(store, source)
    self.assertEqual([], list(store.archive_root.iterdir()))
    # The same source can be tried again when compression has actually closed.
    source = self.source()
    self.assertEqual("complete", self.preserve(store, source)["state"])

  def test_nonoverlap_and_path_escape_do_not_copy(self):
    store = self.instance()
    source = self.source()
    for candidate in (self.root / "outside" / "rlog.zst", "../escape/rlog.zst", f"{ROUTE}--0/qlog.zst"):
      with self.subTest(candidate=str(candidate)):
        with self.assertRaises(ValueError):
          self.preserve(store, candidate)
    with self.assertRaisesRegex(ValueError, "window_not_covered"):
      self.preserve(store, source, window_ns=(1000, 2000))
    self.assertEqual([], list(store.archive_root.iterdir()))

  def test_old_route_init_data_does_not_extend_control_time_coverage(self):
    store = self.instance()
    source = self.source(segment=3, mono_offset=2000)
    candidate = store.inspect_candidate(source, window_ns=(2035, 2065))
    self.assertEqual((2040, 2060), (candidate["start_mono_ns"], candidate["end_mono_ns"]))
    self.assertEqual(10, candidate["coverage"]["mono_min_ns"])
    record = self.preserve(store, source, window_ns=(2035, 2065))
    self.assertEqual((2040, 2060), (record["start_mono_ns"], record["end_mono_ns"]))
    with self.assertRaisesRegex(ValueError, "window_not_covered"):
      store.inspect_candidate(source, window_ns=(5, 15))

  def test_new_destination_collision_never_removes_existing_complete_marker(self):
    store = self.instance()
    source = self.source()
    first = self.preserve(store, source)
    marker = store.archive_root / first["archive_id"] / "archive.json"
    before = marker.read_bytes()
    with patch.object(archive.uuid, "uuid4", return_value=SimpleNamespace(hex=first["archive_id"])):
      with self.assertRaises(FileExistsError):
        self.preserve(store, source)
    self.assertEqual(before, marker.read_bytes())
    self.assertEqual(first, archive.verify_preserved_segment(marker.parent, byte_limit=store.byte_limit))

  def test_disk_failure_keeps_partial_and_retry_never_overwrites_it(self):
    store = self.instance()
    source = self.source(suffix="")
    real_reserve = store._reserve
    calls = 0

    def no_space_after_first_chunk(size):
      nonlocal calls
      calls += 1
      if calls >= 3:
        raise OSError("synthetic_ENOSPC")
      real_reserve(size)

    with patch.object(store, "_reserve", no_space_after_first_chunk), patch.object(archive, "CHUNK_BYTES", 64):
      with self.assertRaisesRegex(OSError, "ENOSPC"):
        self.preserve(store, source)
    incomplete = list(store.archive_root.iterdir())
    self.assertEqual(1, len(incomplete))
    partial = next(incomplete[0].iterdir())
    self.assertEqual(64, partial.stat().st_size)
    before = partial.read_bytes()
    self.assertEqual(64, store.status()["used_bytes"])
    record = self.preserve(store, source)
    self.assertNotEqual(incomplete[0].name, record["archive_id"])
    self.assertEqual(before, partial.read_bytes())
    self.assertFalse((incomplete[0] / "archive.json").exists())

  def test_source_changed_or_disappeared_during_copy_never_commits(self):
    for change in ("append", "gone", "lock"):
      store = self.instance()
      source = self.source(suffix="", segment=len(self.archives))
      reserve = store._reserve
      calls = 0

      def mutate(size):
        nonlocal calls
        calls += 1
        if calls == 2:
          if change == "append":
            with source.open("ab") as stream:
              stream.write(b"x")
          elif change == "gone":
            source.unlink()
          else:
            (source.parent / "rlog.lock").touch()
        reserve(size)

      with self.subTest(change=change), patch.object(store, "_reserve", mutate):
        with self.assertRaises((ValueError, OSError)):
          self.preserve(store, source)
      self.assertFalse(any(path.name == "archive.json" for directory in store.archive_root.iterdir() for path in directory.iterdir()))

  def test_cancellation_before_marker_retains_incomplete_and_after_marker_returns_receipt(self):
    store = self.instance()
    source = self.source()

    def during_copy():
      return any(list(directory.glob("*.partial")) for directory in store.archive_root.iterdir())

    with self.assertRaises(InterruptedError):
      self.preserve(store, source, cancelled=during_copy)
    self.assertTrue(any(store.archive_root.iterdir()))
    self.assertFalse(any(path.name == "archive.json" for directory in store.archive_root.iterdir() for path in directory.iterdir()))
    cancel_now = False
    writer = archive._write_json

    def publish_then_cancel(path, value):
      nonlocal cancel_now
      writer(path, value)
      cancel_now = True

    with patch.object(archive, "_write_json", publish_then_cancel):
      record = self.preserve(store, source, cancelled=lambda: cancel_now)
    self.assertEqual("complete", record["state"])
    self.assertEqual(record, archive.verify_preserved_segment(store.archive_root / record["archive_id"], byte_limit=store.byte_limit))

  def test_storage_file_read_and_reserve_budgets_are_separate(self):
    source = self.source()
    for values, error in (({"byte_limit": 32}, "session_budget"), ({"max_files": 1}, "session_budget"),
                          ({"read_byte_limit": 1}, "read_budget")):
      with self.subTest(values=values):
        store = self.instance(**values)
        with self.assertRaisesRegex(ValueError, error):
          self.preserve(store, source)
    store = self.instance()
    with patch.object(archive.shutil, "disk_usage", return_value=SimpleNamespace(free=1)):
      with self.assertRaisesRegex(OSError, "free_space_reserve"):
        self.preserve(store, source)

  def test_restart_inspection_reports_incomplete_and_corrupt_without_overwrite(self):
    store = self.instance()
    source = self.source()
    record = self.preserve(store, source)
    unfinished = store.archive_root / ("b" * 32)
    unfinished.mkdir()
    (unfinished / "rlog.partial").write_bytes(b"incomplete")
    resumed = self.instance()
    result = resumed.inspect_existing()
    self.assertEqual({"complete", "incomplete"}, {row["state"] for row in result})
    directory = store.archive_root / record["archive_id"]
    (directory / "rlog.zst").write_bytes(b"corrupt")
    before = (directory / "archive.json").read_bytes()
    result = resumed.inspect_existing()
    self.assertIn("corrupt", {row["state"] for row in result})
    self.assertEqual(before, (directory / "archive.json").read_bytes())

  def test_discovery_pages_are_bounded_and_never_claim_closure(self):
    store = self.instance()
    for index in range(5):
      self.source(segment=index)
    observed = []
    for _ in range(3):
      batch = store.discover_candidates(max_entries=2, route=ROUTE)
      self.assertLessEqual(len(batch), 2)
      self.assertTrue(all(row["closure"] == "unverified" for row in batch))
      observed.extend(row["segment"] for row in batch)
    self.assertEqual(set(range(5)), set(observed))
    self.assertTrue(store.status()["discovery_complete"])

  def test_actual_directory_link_or_junction_is_rejected(self):
    store = self.instance()
    source = self.source()
    outside = self.root / "external-segment"
    source.parent.rename(outside)
    linked = self.logs / f"{ROUTE}--0"
    if os.name == "nt":
      escaped_link = str(linked).replace("'", "''")
      escaped_target = str(outside).replace("'", "''")
      process = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"New-Item -ItemType Junction -Path '{escaped_link}' -Target '{escaped_target}' | Out-Null"],
                               capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
      self.assertEqual(0, process.returncode, process.stderr)
    else:
      linked.symlink_to(outside, target_is_directory=True)
    with self.assertRaisesRegex(ValueError, "link_not_allowed"):
      self.preserve(store, linked / "rlog.zst")


if __name__ == "__main__":
  unittest.main()
