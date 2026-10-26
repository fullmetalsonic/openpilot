"""Whole-trip selection and disk masks retain the original exclusion contract."""
from copy import deepcopy
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.analysis import AnalysisProfile, Observation, _mask_axis
from openpilot.selfdrive.carrot.control_advisor.candidates import source_collection, encode_consumer_evidence, consumer_events
from openpilot.selfdrive.carrot.control_advisor.trip_analysis import _base_mask, _finalize_masks, _in_windows
from openpilot.selfdrive.carrot.control_advisor.journal_snapshot import _pin_pid_chunks, select_pid_references


class WholeTripTests(unittest.TestCase):
  def test_collection_verifies_over_32mib_without_raising_the_retained_view_limit(self):
    from .test_consumer_collector import CollectorTests
    from openpilot.selfdrive.carrot.control_advisor.journal_segments import SegmentedJournal
    from openpilot.selfdrive.carrot.control_advisor.settings_history import load_history, load_collection_history
    CollectorTests.setUpClass()
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      journal = SegmentedJournal(root, "history-stress", max_segment_bytes=8 * 1024 * 1024,
        max_segment_events=10000, max_consumer_bytes=4 * 1024 * 1024, max_consumer_events=5000,
        max_total_bytes=512 * 1024 * 1024, free_reserve_bytes=0)
      origin = 10_000_000_000
      # This test measures decoding/retention, not power-loss durability. The
      # generated fixture is sealed normally before the readers inspect it.
      with patch("openpilot.selfdrive.carrot.control_advisor.journal_segments.os.fsync"):
        journal.append({"type": "session", "source_kind": "padded_generated_stress"}, mono_ns=origin)
        for index in range(4000):
          stamp = origin + index * 1_000_000_000
          event = deepcopy(CollectorTests.base)
          event.update(sequence=index + 1, produced_mono_ns=stamp)
          journal.append({"type": "consumer_observation", "recorded_mono_ns": stamp + 1,
            "event": event, "fixture_padding": "x" * 9000}, mono_ns=stamp + 1)
      checkpoint = journal.checkpoint(cutoff_mono_ns=stamp + 2, consumer_budget_rejections=0)
      journal.close()
      self.assertGreater(checkpoint["bytes"], 32 * 1024 * 1024)
      with self.assertRaises(ValueError):
        load_history(root, checkpoint=checkpoint, max_bytes=32 * 1024 * 1024, max_events=40000, include_records=True)
      history = load_collection_history(root, checkpoint, {"start_ns": origin, "end_ns": stamp})
      self.assertEqual(history["state"], "complete")
      self.assertEqual(history["verified_events"], 4001)
      self.assertEqual(history["consumer_events"], 4000)
      self.assertLess(history["retained_byte_count"], 32 * 1024 * 1024)
      self.assertLess(history["retained_record_count"], 1000)
      self.assertEqual(history["consumer_gap_stamps"], [])

  def test_pid_snapshot_pins_complete_time_strata_and_rejects_selected_payload_tampering(self):
    from .test_pid_burst import complete_blocks
    from openpilot.selfdrive.carrot.control_advisor.pid_burst import PidBurstStore, load_chunk, assemble_blocks
    with tempfile.TemporaryDirectory() as directory:
      source, target = Path(directory) / "source", Path(directory) / "snapshot"
      source.mkdir()
      target.mkdir()
      store = PidBurstStore(source)
      references = [store.append(event) for event in complete_blocks()]
      history = {"records": [{"record": {"type": "pid_burst_chunk", "reference": row}} for row in references]}
      raw_digest = json.dumps(history, sort_keys=True)
      analysis_range = {"start_ns": 100_000_000_000, "end_ns": 280_000_000_000}
      inventory = _pin_pid_chunks(source, target, history, copy_files=True, analysis_range=analysis_range)
      self.assertEqual(inventory["original_reference_count"], len(references))
      self.assertEqual(len(inventory["burst_ids"]), 3)
      chosen = select_pid_references(references, analysis_range)
      assembled = assemble_blocks([{"event": load_chunk(target, row), "reference": row} for row in chosen])
      self.assertEqual([row["state"] for row in assembled], ["complete"] * 3)
      self.assertEqual([len(row["rows"]) for row in assembled], [1000] * 3)
      self.assertEqual(raw_digest, json.dumps(history, sort_keys=True))
      self.assertEqual(_pin_pid_chunks(source, target, history, analysis_range=analysis_range), inventory)
      damaged = target / chosen[0]["path"]
      damaged.write_bytes(b"{}")
      with self.assertRaises(ValueError):
        _pin_pid_chunks(source, target, history, analysis_range=analysis_range)

  def test_many_raw_pid_references_do_not_increase_selected_payload_budget(self):
    refs = []
    for burst in range(40):
      for chunk in range(125):
        frame = burst * 3000 + chunk * 8
        refs.append({"burst_id": str(burst), "chunk_sequence": chunk, "terminal": chunk == 124,
          "dependency_digest": "a" * 64, "frame_start": frame, "frame_end": frame + 7,
          "mono_start_ns": frame * 10_000_000, "mono_end_ns": (frame + 7) * 10_000_000})
    chosen = select_pid_references(refs, {"start_ns": 0, "end_ns": 1200_000_000_000})
    self.assertEqual(len(refs), 5000)
    self.assertEqual(len(chosen), 375)
    self.assertEqual(len({row["burst_id"] for row in chosen}), 3)

  def test_disk_masks_equal_original_across_driver_gap_invalid_and_epoch_boundaries(self):
    profile = AnalysisProfile("parity", 300, 200, 150, 100, 2000)
    observations = []
    for index in range(140):
      stamp = index * 100 + (500 if index >= 65 else 0)
      observations.append(Observation(stamp, "lateral", 1., 1.1, index != 13,
        index not in (18, 19), None if index == 25 else index in (39, 40, 90),
        index != 55, "a" if index < 99 else "b", {"saturated": index == 110}))
    expected = _mask_axis(observations, profile)
    database = sqlite3.connect(":memory:")
    self.addCleanup(database.close)
    database.execute("CREATE TABLE obs(stamp INTEGER,axis TEXT,driver INTEGER,reasons TEXT,epoch_start INTEGER,pre INTEGER DEFAULT 0,final TEXT,PRIMARY KEY(axis,stamp))")
    state = {}
    # JSON round trips emulate checkpoints at several real source boundaries.
    for index, item in enumerate(observations):
      if index in (35, 65, 100):
        state = json.loads(json.dumps(state))
      reasons, epoch_start = _base_mask(item, state, profile)
      database.execute("INSERT INTO obs(stamp,axis,driver,reasons,epoch_start) VALUES(?,?,?,?,?)",
        (item.mono_ns, item.axis, item.driver_override, json.dumps(reasons), epoch_start))
    _finalize_masks(database, profile, observations[-1].mono_ns)
    actual = [set(json.loads(row[0])) for row in database.execute("SELECT final FROM obs ORDER BY stamp")]
    self.assertEqual(actual, expected)

  def test_time_selection_covers_start_middle_and_end_without_scoring_on_errors(self):
    selected = [stamp for stamp in range(0, 3600 * 10**9, 200_000_000) if _in_windows(stamp, 0, 3600 * 10**9)]
    self.assertGreater(len(selected), 1400)
    self.assertLess(len(selected), 1550)
    self.assertTrue(any(stamp < 120 * 10**9 for stamp in selected))
    self.assertTrue(any(1700 * 10**9 < stamp < 1900 * 10**9 for stamp in selected))
    self.assertTrue(any(stamp > 3480 * 10**9 for stamp in selected))

  def test_collection_keeps_128_original_hashes_with_a_separate_artifact_type(self):
    collection = {"version": 1, "sources": [{"id": f"log-{i}", "sha256": f"{i:064x}", "bytes": 100} for i in range(128)]}
    self.assertEqual(len(source_collection(collection)), 64)
    bad = deepcopy(collection)
    bad["sources"].append({"id": "extra", "sha256": "f" * 64, "bytes": 1})
    with self.assertRaises(ValueError):
      source_collection(bad)
    bad = deepcopy(collection)
    bad["sources"][-1] = deepcopy(bad["sources"][0])
    with self.assertRaises(ValueError):
      source_collection(bad)

  def test_lossless_consumer_wire_block_rejects_forged_digest_and_trailing_stream(self):
    events = [{"test": i, "same_context": "abc" * 30} for i in range(100)]
    block = encode_consumer_evidence(events)
    self.assertEqual(consumer_events({"consumer_evidence": [], "consumer_evidence_block": block}), events)
    forged = deepcopy(block)
    forged["sha256"] = "0" * 64
    with self.assertRaises(ValueError):
      consumer_events({"consumer_evidence": [], "consumer_evidence_block": forged})
    with self.assertRaises(ValueError):
      consumer_events({"consumer_evidence": events, "consumer_evidence_block": block})
