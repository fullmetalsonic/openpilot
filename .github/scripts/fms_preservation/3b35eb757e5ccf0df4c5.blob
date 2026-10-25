"""Rotating actual journals, original checkpoints and bounded writer state."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.settings_history import KEYS, SettingsRecorder, load_history, select_history_window
from openpilot.selfdrive.carrot.control_advisor.consumer_observer import OBSERVER_PATH, CONSUMER_PATHS
from openpilot.selfdrive.carrot.control_advisor.log_history import _observations
from openpilot.selfdrive.carrot.control_advisor.recording import _job_receipt


class SegmentedHistoryTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.source, self.session = self.root / "params", self.root / "session"
    self.source.mkdir()
    self.session.mkdir()
    for key in KEYS:
      (self.source / key).write_text("0", encoding="ascii")
    (self.source / "CurrentRoute").write_text("00000000--0123456789", encoding="ascii")
    self.now = 1_000_000_000

  def recorder(self, **options):
    recorder = SettingsRecorder(self.source, self.session, "segmented-fixture", journal_format="segmented",
      clock_ns=lambda: self.now, **options)
    self.addCleanup(recorder.close)
    return recorder

  def event(self, sequence):
    return {"schema_version": 1, "consumer_id": "controls.torque", "boot_id": "00000000-0000-0000-0000-000000000001", "pid": 1,
      "process_instance_id": "a" * 32, "consumer_instance_id": "b" * 32, "sequence": sequence, "produced_mono_ns": self.now,
      "cycle_id": sequence, "phase": "sampled_use", "dropped_events": 0,
      "fields": {"LateralTorqueFriction": {"read_value": .3, "effective_value": .3, "state": "used", "source": "stored", "read_mono_ns": self.now}},
      "context": {"active": True}, "source_hashes": {OBSERVER_PATH: "c" * 64, CONSUMER_PATHS["controls.torque"]: "d" * 64}}

  def test_over_5000_consumers_rotate_and_old_checkpoint_pins_original_prefix(self):
    recorder = self.recorder(max_journal_bytes=2 * 1024 * 1024, max_events=2048, cache_window_ns=1_000_000_000)
    first = recorder.sample()
    diagnostic = {"revision": 1, "window_start_ns": self.now, "window_end_ns": self.now + 1,
                  "profile_id": "explicit-host-fixture", "invalidated": False, "partial_window": False, "sha256": "e" * 64}
    fixed = recorder.append_diagnostic(diagnostic)["journal"]
    recorder.pin_diagnostic(fixed, first["revision"])
    old = None
    segments = set()
    for sequence in range(1, 5102):
      self.now += 100_000_000
      self.assertTrue(recorder.append_consumer_observation(self.event(sequence)))
      segments.add(recorder.snapshot()["journal"]["segment_index"])
      if sequence % 10 == 0:
        (self.source / "PathOffset").write_text(str(sequence // 10), encoding="ascii")
        recorder.sample()
      if sequence == 2100:
        old = recorder.checkpoint()
    self.assertGreaterEqual(len(segments), 3)
    self.assertEqual(recorder.revision_metadata(first["revision"])["settings_revision"], first["revision"])
    self.assertLess(len(recorder._references), 10)
    self.assertLess(len(recorder._revision_metadata), 10)
    older = load_history(self.session, checkpoint=old, max_bytes=32 * 1024 * 1024, max_events=40_000)
    self.assertEqual(older["consumer_events"], 2100)
    self.assertEqual(older["sequence"], old["sequence"])
    job = {"diagnostic_revision": 1, "settings_revision": first["revision"], "window_ns": (diagnostic["window_start_ns"], diagnostic["window_end_ns"]),
           "route": "00000000--0123456789",
           "journal_ref": fixed, "route_observation": recorder.revision_metadata(first["revision"])["route_observation"]}
    recorder.append_job_status(_job_receipt(job, "cancelled"))
    with self.assertRaisesRegex(ValueError, "not_committed"):
      recorder.revision_metadata(first["revision"])
    token = recorder.checkpoint()
    selection = select_history_window(self.session, token, self.now - 180_000_000_000, self.now)
    self.assertTrue(selection["omitted_sequence_ranges"])
    selected = load_history(self.session, checkpoint=token, selection=selection, max_bytes=32 * 1024 * 1024, max_events=40_000, include_records=True)
    self.assertTrue(selected["selected_closure_complete"])
    self.assertFalse(selected["snapshot_prefix_complete"])
    self.assertLess(selected["verified_events"], token["sequence"])
    self.assertEqual(selected["consumer_budget_rejections"], 0)
    self.assertEqual(selected["checkpoint_tip"]["sequence"], token["sequence"])
    recorder.close()
    closed = load_history(self.session, max_bytes=32 * 1024 * 1024, max_events=40_000)
    self.assertTrue(closed["closed"])
    self.assertEqual(closed["consumer_events"], 5101)

  def test_same_revision_refresh_is_new_real_read_and_selection_rejects_forgery(self):
    recorder = self.recorder(max_journal_bytes=256 * 1024, max_events=16)
    first = recorder.sample()
    for sequence in range(1, 81):
      self.now += 100_000_000
      recorder.append_consumer_observation(self.event(sequence))
      if sequence % 4 == 0:
        recorder.sample()
    token = recorder.checkpoint()
    history = load_history(self.session, checkpoint=token, max_bytes=32 * 1024 * 1024, max_events=40_000, include_records=True)
    refresh = [item["record"] for item in history["records"] if item["record"].get("snapshot_refresh")]
    self.assertGreaterEqual(len(refresh), 2)
    self.assertEqual({item["revision"] for item in refresh}, {first["revision"]})
    for item in refresh:
      self.assertEqual(item["changed_keys"], [])
      self.assertTrue(all(item["observation"]["begin_ns"] <= value["begin_ns"] <= value["end_ns"] <= item["observation"]["end_ns"] for value in item["settings"].values()))
    self.assertEqual({poll["record"]["revision"] for poll in _observations(history["records"])}, {first["revision"]})
    selection = select_history_window(self.session, token, self.now - 1_000_000_000, self.now)
    changed = deepcopy(selection)
    changed["omitted_sequence_ranges"] = []
    self.assertNotEqual(changed, selection)
    with self.assertRaisesRegex(ValueError, "selection_mismatch"):
      load_history(self.session, checkpoint=token, selection=changed, max_bytes=32 * 1024 * 1024, max_events=40_000)

  def test_explicit_diagnostic_reference_closure_and_tamper_are_not_silently_omitted(self):
    recorder = self.recorder(max_journal_bytes=256 * 1024, max_events=16)
    recorder.sample()
    diagnostic = {"revision": 1, "window_start_ns": self.now, "window_end_ns": self.now + 1,
                  "profile_id": "explicit-host-fixture", "invalidated": False, "partial_window": False, "sha256": "e" * 64}
    reference = recorder.append_diagnostic(diagnostic)["journal"]
    for sequence in range(1, 50):
      self.now += 100_000_000
      recorder.append_consumer_observation(self.event(sequence))
      if sequence % 4 == 0:
        recorder.sample()
    recorder.append_archive({"state": "complete", "session_id": "segmented-fixture", "route": "00000000--0123456789",
      "segment": 1, "sha256": "f" * 64, "bytes": 1, "settings_history_reference": reference})
    token = recorder.checkpoint()
    selection = select_history_window(self.session, token, self.now - 100_000_000, self.now)
    self.assertIn(reference, selection["required_refs"])
    loaded = load_history(self.session, checkpoint=token, selection=selection, max_bytes=32 * 1024 * 1024, max_events=40_000)
    self.assertIn(str(reference["sequence"]), loaded["diagnostic_references"])
    path = self.session / reference["path"]
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'explicit-host-fixture', b'changed-host-fixture!'))
    with self.assertRaises(ValueError):
      load_history(self.session, checkpoint=token, selection=selection, max_bytes=32 * 1024 * 1024, max_events=40_000)


if __name__ == "__main__":
  unittest.main()
