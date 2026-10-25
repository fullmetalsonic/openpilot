"""Tests for stdlib-only segmented journal codec."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import tracemalloc
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.journal_segments import (
  SegmentedJournal, StorageBudgetExhausted, ZERO_HASH, _canonical,
  iter_verified_records, iter_verified_catalog, validate_checkpoint, validate_reference,
)


def _make_single_file_journal(records, session_id):
  lines = []
  prev_sha = ZERO_HASH
  for seq, rec in enumerate(records, start=1):
    body = {
      "schema_version": 1,
      "session_id": session_id,
      "sequence": seq,
      "previous_sha256": prev_sha,
      "record": rec,
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    lines.append(_canonical({**body, "sha256": digest}) + b"\n")
    prev_sha = digest
  return b"".join(lines), prev_sha


class TestSegmentedJournal(unittest.TestCase):
  def setUp(self):
    self.temp_dir = tempfile.mkdtemp()
    self.session_dir = Path(self.temp_dir)
    self.session_id = "session-test-20261025"

  def tearDown(self):
    shutil.rmtree(self.temp_dir, ignore_errors=True)

  def test_rotations_unicode_and_single_file_equality(self):
    records = [
      {"type": "settings_observation", "text": "차량 설정 1 #1234 🚗", "val": 10},
      {"type": "settings_observation", "text": "차량 설정 2 #5678 🚀", "val": 20},
      {"type": "settings_observation", "text": "차량 설정 3 #9999 한국어", "val": 30},
      {"type": "settings_observation", "text": "차량 설정 4 #aaaa 🌟", "val": 40},
      {"type": "settings_observation", "text": "차량 설정 5 #bbbb 🇰🇷", "val": 50},
      {"type": "settings_observation", "text": "차량 설정 6 #cccc ⚡", "val": 60},
      {"type": "settings_observation", "text": "차량 설정 7 #dddd 🏁", "val": 70},
    ]
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=2,
      max_consumer_bytes=512 * 1024, max_consumer_events=2,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    for idx, rec in enumerate(records):
      journal.append(rec, mono_ns=1_000_000 * (idx + 1))
    cp = journal.checkpoint(cutoff_mono_ns=8_000_000, consumer_budget_rejections=0)
    journal.close()

    concatenated = b""
    for i in range(4):
      seg_path = self.session_dir / f"history/segment-{i:06d}.jsonl"
      self.assertTrue(seg_path.is_file())
      concatenated += seg_path.read_bytes()

    expected_bytes, expected_last_sha = _make_single_file_journal(records, self.session_id)
    self.assertEqual(concatenated, expected_bytes)
    self.assertEqual(cp["chain_sha256"], expected_last_sha)
    self.assertEqual(cp["sequence"], len(records))
    self.assertEqual(cp["bytes"], len(expected_bytes))

    yielded = list(iter_verified_records(self.session_dir, cp))
    self.assertEqual(len(yielded), len(records))
    for idx, item in enumerate(yielded):
      self.assertEqual(item["envelope"]["sequence"], idx + 1)
      self.assertEqual(item["envelope"]["record"], records[idx])
      self.assertEqual(item["reference"]["sequence"], idx + 1)
      self.assertEqual(item["reference"]["durable"], True)

  def test_consumer_budget_rotation(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=100,
      max_consumer_bytes=1024 * 1024, max_consumer_events=2,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    ref1 = journal.append({"type": "consumer_observation", "cid": "c1"}, mono_ns=100)
    self.assertEqual(ref1["segment_index"], 0)
    ref2 = journal.append({"type": "consumer_observation", "cid": "c2"}, mono_ns=200)
    self.assertEqual(ref2["segment_index"], 0)
    # Third consumer observation exceeds max_consumer_events (2) in segment 0 -> rotates to segment 1
    ref3 = journal.append({"type": "consumer_observation", "cid": "c3"}, mono_ns=300)
    self.assertEqual(ref3["segment_index"], 1)
    self.assertEqual(ref3["sequence"], 3)
    ref4 = journal.append({"type": "consumer_observation", "cid": "c4"}, mono_ns=400)
    self.assertEqual(ref4["segment_index"], 1)
    self.assertEqual(ref4["sequence"], 4)
    journal.close()

  def test_checkpoint_active_and_after_close(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=2,
      max_consumer_bytes=512 * 1024, max_consumer_events=2,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    journal.append({"type": "settings_observation", "k": 1}, mono_ns=10)
    journal.append({"type": "settings_observation", "k": 2}, mono_ns=20)
    journal.append({"type": "settings_observation", "k": 3}, mono_ns=30)

    cp_active = journal.checkpoint(cutoff_mono_ns=50, consumer_budget_rejections=1)
    validated_active = validate_checkpoint(cp_active)
    self.assertIsNotNone(validated_active["active"])
    self.assertEqual(validated_active["active"]["index"], 1)
    self.assertEqual(validated_active["catalog"]["entries"], 1)

    records_active = list(iter_verified_records(self.session_dir, validated_active))
    self.assertEqual(len(records_active), 3)

    journal.close()
    cp_closed = journal.checkpoint(cutoff_mono_ns=100, consumer_budget_rejections=1)
    validated_closed = validate_checkpoint(cp_closed)
    self.assertIsNone(validated_closed["active"])
    self.assertEqual(validated_closed["catalog"]["entries"], 2)

    records_closed = list(iter_verified_records(self.session_dir, validated_closed))
    self.assertEqual(len(records_closed), 3)

  def test_old_checkpoint_verifies_after_future_appends_and_rotation(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=2,
      max_consumer_bytes=512 * 1024, max_consumer_events=2,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    journal.append({"type": "settings_observation", "a": 1}, mono_ns=1)
    cp_early = journal.checkpoint(cutoff_mono_ns=5, consumer_budget_rejections=0)

    journal.append({"type": "settings_observation", "a": 2}, mono_ns=10)
    journal.append({"type": "settings_observation", "a": 3}, mono_ns=20)
    journal.append({"type": "settings_observation", "a": 4}, mono_ns=30)
    journal.close()

    verified = list(iter_verified_records(self.session_dir, cp_early))
    self.assertEqual(len(verified), 1)
    self.assertEqual(verified[0]["envelope"]["sequence"], 1)
    self.assertEqual(verified[0]["envelope"]["record"]["a"], 1)

  def test_subset_verification(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=2,
      max_consumer_bytes=512 * 1024, max_consumer_events=2,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    for i in range(6):
      journal.append({"type": "settings_observation", "i": i}, mono_ns=10 * (i + 1))
    journal.close()
    cp = journal.checkpoint(cutoff_mono_ns=100, consumer_budget_rejections=0)

    subset_1 = list(iter_verified_records(self.session_dir, cp, segment_indices=[1]))
    self.assertEqual(len(subset_1), 2)
    self.assertEqual(subset_1[0]["envelope"]["sequence"], 3)
    self.assertEqual(subset_1[1]["envelope"]["sequence"], 4)

    with self.assertRaises(ValueError):
      list(iter_verified_records(self.session_dir, cp, segment_indices=[99]))

  def test_catalog_tamper_and_rejection(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=2,
      max_consumer_bytes=512 * 1024, max_consumer_events=2,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    for i in range(4):
      journal.append({"type": "settings_observation", "v": i}, mono_ns=10 * (i + 1))
    journal.close()
    cp = journal.checkpoint(cutoff_mono_ns=100, consumer_budget_rejections=0)

    cat_path = self.session_dir / "history/catalog.jsonl"
    original_cat = cat_path.read_bytes()

    # 1. Truncate catalog
    cat_path.write_bytes(original_cat[:len(original_cat) // 2])
    with self.assertRaises(ValueError):
      list(iter_verified_records(self.session_dir, cp))

    # 2. Tamper entry
    tampered = original_cat.replace(b"segment-000000", b"segment-999999")
    cat_path.write_bytes(tampered)
    with self.assertRaises(ValueError):
      list(iter_verified_records(self.session_dir, cp))

    # 3. Missing catalog
    cat_path.unlink()
    with self.assertRaises(ValueError):
      list(iter_verified_records(self.session_dir, cp))

  def test_segment_tamper_rejected(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=2,
      max_consumer_bytes=512 * 1024, max_consumer_events=2,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    for i in range(2):
      journal.append({"type": "settings_observation", "v": i}, mono_ns=10 * (i + 1))
    journal.close()
    cp = journal.checkpoint(cutoff_mono_ns=100, consumer_budget_rejections=0)

    seg0 = self.session_dir / "history/segment-000000.jsonl"
    content = seg0.read_bytes()
    seg0.write_bytes(content + b"extra_garbage\n")
    with self.assertRaises(ValueError):
      list(iter_verified_records(self.session_dir, cp))

  def test_symlink_rejection(self):
    sym_target = self.session_dir / "real_dir"
    sym_target.mkdir()
    sym_dir = self.session_dir / "sym_dir"
    try:
      os.symlink(sym_target, sym_dir)
    except (OSError, NotImplementedError):
      self.skipTest("Symlinks not supported on this platform/privilege")

    with self.assertRaises(ValueError):
      SegmentedJournal(
        sym_dir, self.session_id,
        max_segment_bytes=1024 * 1024, max_segment_events=10,
        max_consumer_bytes=512 * 1024, max_consumer_events=5,
        max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
      )

  def test_oversize_records_rejected_no_rotation(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=2048, max_segment_events=10,
      max_consumer_bytes=1024, max_consumer_events=5,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    # Normal row
    journal.append({"type": "settings_observation", "x": 1}, mono_ns=10)
    self.assertEqual(journal._segment_index, 0)

    # Huge record > max_segment_bytes
    huge = {"type": "settings_observation", "data": "x" * 3000}
    with self.assertRaises(ValueError):
      journal.append(huge, mono_ns=20)
    self.assertEqual(journal._segment_index, 0)

    # Consumer record > max_consumer_bytes
    consumer_too_big = {"type": "consumer_observation", "data": "c" * 1500}
    with self.assertRaises(ValueError):
      journal.append(consumer_too_big, mono_ns=30)
    self.assertEqual(journal._segment_index, 0)
    journal.close()

  def test_injected_fsync_failure_poisons_writer(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=10,
      max_consumer_bytes=512 * 1024, max_consumer_events=5,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    journal.append({"type": "settings_observation", "x": 1}, mono_ns=10)

    with patch("os.fsync", side_effect=OSError("injected_fsync_failure")):
      with self.assertRaises(OSError):
        journal.append({"type": "settings_observation", "x": 2}, mono_ns=20)

    self.assertTrue(journal._poisoned)
    # Poisoned writer fails subsequent operations
    with self.assertRaises(ValueError):
      journal.append({"type": "settings_observation", "x": 3}, mono_ns=30)
    with self.assertRaises(ValueError):
      journal.checkpoint(cutoff_mono_ns=50, consumer_budget_rejections=0)

    # Close on poisoned writer closes handles without throwing
    journal.close()
    # Originals preserved
    seg0 = self.session_dir / "history/segment-000000.jsonl"
    self.assertTrue(seg0.is_file())
    self.assertGreater(seg0.stat().st_size, 0)

  def test_storage_budget_and_disk_reserve(self):
    # max_total_bytes exceeded
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024, max_segment_events=10,
      max_consumer_bytes=512, max_consumer_events=5,
      max_total_bytes=350, free_reserve_bytes=0,
    )
    journal.append({"type": "settings_observation", "x": 1}, mono_ns=10)
    with self.assertRaises(StorageBudgetExhausted):
      journal.append({"type": "settings_observation", "x": "long_data_string_that_exceeds_budget" * 10}, mono_ns=20)
    journal.close()

    # free reserve exceeded
    sub_dir = self.session_dir / "sub_reserve"
    sub_dir.mkdir()
    journal2 = SegmentedJournal(
      sub_dir, self.session_id,
      max_segment_bytes=1024, max_segment_events=10,
      max_consumer_bytes=512, max_consumer_events=5,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=1000,
    )
    with patch("shutil.disk_usage") as mock_du:
      mock_du.return_value.free = 500  # less than free_reserve_bytes (1000)
      with self.assertRaises(StorageBudgetExhausted):
        journal2.append({"type": "settings_observation", "x": 1}, mono_ns=10)
    journal2.close()

  def test_streaming_bounded_memory(self):
    journal = SegmentedJournal(
      self.session_dir, self.session_id,
      max_segment_bytes=1024 * 1024, max_segment_events=10,
      max_consumer_bytes=512 * 1024, max_consumer_events=5,
      max_total_bytes=10 * 1024 * 1024, free_reserve_bytes=0,
    )
    for i in range(5):
      journal.append({"type": "settings_observation", "i": i}, mono_ns=10 * (i + 1))
    cp = journal.checkpoint(cutoff_mono_ns=100, consumer_budget_rejections=0)
    journal.close()

    gen = iter_verified_records(self.session_dir, cp)
    self.assertTrue(hasattr(gen, "__next__"))
    first = next(gen)
    self.assertEqual(first["envelope"]["sequence"], 1)
    self.assertEqual(first["reference"]["segment_index"], 0)
    rest = list(gen)
    self.assertEqual(len(rest), 4)


class TestCodecBoundaries(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)

  def journal(self, **changes):
    options = dict(max_segment_bytes=4 * 1024 * 1024, max_segment_events=2,
      max_consumer_bytes=2 * 1024 * 1024, max_consumer_events=2,
      max_total_bytes=16 * 1024 * 1024, free_reserve_bytes=0)
    return SegmentedJournal(self.root, "boundary-session", **{**options, **changes})

  def test_public_catalog_refs_and_selected_scope(self):
    journal = self.journal()
    refs = [journal.append({"type": "settings_observation", "i": i}, mono_ns=i) for i in range(7)]
    journal.close()
    cp = journal.checkpoint(cutoff_mono_ns=8, consumer_budget_rejections=0)
    catalog = list(iter_verified_catalog(self.root, cp))
    self.assertEqual([1, 3, 5, 7], [row["first_sequence"] for row in catalog])
    self.assertEqual([0, 2, 4, 6], [row["first_mono_ns"] for row in catalog])
    for ref in refs:
      self.assertEqual(ref, validate_reference(ref))
    legacy = {"path": "settings.jsonl", "sequence": 1, "sha256": refs[0]["sha256"], "bytes": refs[0]["bytes"], "durable": True}
    self.assertEqual(legacy, validate_reference(legacy))
    with self.assertRaises(ValueError): validate_reference({**refs[0], "path": "history/../escape"})
    # An explicitly selected closure never claims unselected source coverage.
    (self.root / "history/segment-000000.jsonl").unlink()
    subset = list(iter_verified_records(self.root, cp, segment_indices=[1, 3]))
    self.assertEqual([3, 4, 7], [row["envelope"]["sequence"] for row in subset])
    with self.assertRaises(ValueError): list(iter_verified_records(self.root, cp))
    with self.assertRaises(ValueError): list(iter_verified_records(self.root, cp, segment_indices=[1, 1]))

  def test_rotation_catalog_failure_poison_preserves_historical_checkpoint(self):
    journal = self.journal()
    journal.append({"i": 0}, mono_ns=0)
    journal.append({"i": 1}, mono_ns=1)
    old = journal.checkpoint(cutoff_mono_ns=1, consumer_budget_rejections=0)
    catalog_fd, real_fsync = journal._catalog_file.fileno(), os.fsync
    def fail_catalog(fd):
      if fd == catalog_fd: raise OSError("injected catalog fsync failure")
      return real_fsync(fd)
    with patch("os.fsync", side_effect=fail_catalog):
      with self.assertRaises(OSError): journal.append({"i": 2}, mono_ns=2)
    with self.assertRaises(ValueError): journal.checkpoint(cutoff_mono_ns=2, consumer_budget_rejections=0)
    with self.assertRaises(ValueError): journal.append({"i": 3}, mono_ns=3)
    journal.close()
    self.assertFalse((self.root / "history/segment-000001.jsonl").exists())
    self.assertEqual([1, 2], [item["envelope"]["sequence"] for item in iter_verified_records(self.root, old)])

  def test_directory_sync_failure_is_not_swallowed(self):
    journal = self.journal(max_segment_events=1, max_consumer_events=1)
    journal.append({"i": 0}, mono_ns=0)
    old = journal.checkpoint(cutoff_mono_ns=0, consumer_budget_rejections=0)
    with patch("openpilot.selfdrive.carrot.control_advisor.journal_segments._sync_dir", side_effect=OSError("injected directory fsync")):
      with self.assertRaises(OSError): journal.append({"i": 1}, mono_ns=1)
    self.assertTrue(journal._poisoned)
    journal.close()
    self.assertEqual(1, len(list(iter_verified_records(self.root, old))))

  def test_streaming_peak_memory_below_whole_large_segment(self):
    journal = self.journal(max_segment_events=1000, max_consumer_events=500)
    payload = "x" * 32768
    for i in range(160): journal.append({"i": i, "payload": payload}, mono_ns=i)
    journal.close()
    cp = journal.checkpoint(cutoff_mono_ns=160, consumer_budget_rejections=0)
    self.assertGreater(cp["bytes"], 5 * 1024 * 1024)
    tracemalloc.start()
    try:
      count = 0
      for row in iter_verified_records(self.root, cp):
        count += 1
        self.assertEqual(payload, row["envelope"]["record"]["payload"])
      _, peak = tracemalloc.get_traced_memory()
    finally:
      tracemalloc.stop()
    self.assertEqual(160, count)
    self.assertLess(peak, 2 * 1024 * 1024)


if __name__ == "__main__":
  unittest.main()
