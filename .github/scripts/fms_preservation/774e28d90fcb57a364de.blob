import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.evidence import create_bundle, hash_file, verify_bundle


class EvidenceTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.source = self.root / "route.rlog"
    self.content = b"original-control-events\x00\xff"
    self.source.write_bytes(self.content)
    self.destination = self.root / "bundle"

  def create(self, **kwargs):
    return create_bundle(self.destination, [self.source], {"apply_enabled": False}, byte_limit=4096, **kwargs)

  def test_roundtrip_preserves_input_and_copies_exact_bytes(self):
    manifest = self.create(expected_sha256=[hashlib.sha256(self.content).hexdigest()])
    self.assertEqual(verify_bundle(self.destination, byte_limit=4096), manifest)
    self.assertEqual(self.source.read_bytes(), self.content)
    self.assertEqual((self.destination / manifest["files"][0]["path"]).read_bytes(), self.content)
    self.assertNotIn(str(self.root), (self.destination / "bundle.json").read_text())

  def test_changed_copy_and_result_are_rejected(self):
    manifest = self.create()
    for entry in [manifest["files"][0], manifest["result"]]:
      target = self.destination / entry["path"]
      original = target.read_bytes()
      target.write_bytes(b"x" * len(original))
      with self.assertRaisesRegex(ValueError, "hash_mismatch"):
        verify_bundle(self.destination, byte_limit=4096)
      target.write_bytes(original)

  def test_source_changed_between_analysis_and_copy(self):
    old_hash = hash_file(self.source, 4096)[0]
    self.source.write_bytes(b"changed-after-analysis")
    with self.assertRaisesRegex(ValueError, "changed_since_analysis"):
      self.create(expected_sha256=[old_hash])
    self.assertFalse((self.destination / "bundle.json").exists())
    self.assertEqual(self.source.read_bytes(), b"changed-after-analysis")

  def test_existing_output_is_never_overwritten(self):
    self.destination.mkdir()
    original = self.destination / "bundle.json"
    original.write_bytes(b"unrelated")
    with self.assertRaises(FileExistsError):
      self.create()
    self.assertEqual(original.read_bytes(), b"unrelated")

  def test_limits_cancel_and_failed_write_never_publish_success(self):
    with self.assertRaisesRegex(ValueError, "byte_limit"):
      create_bundle(self.destination, [self.source], {}, byte_limit=1)
    self.assertFalse(self.destination.exists())
    with self.assertRaises(InterruptedError):
      self.create(cancelled=lambda: True)
    self.assertFalse(self.destination.exists())
    with patch("openpilot.selfdrive.carrot.control_advisor.evidence.os.fsync", side_effect=OSError("disk_full")):
      with self.assertRaisesRegex(OSError, "disk_full"):
        self.create()
    self.assertFalse((self.destination / "bundle.json").exists())
    self.assertEqual(self.source.read_bytes(), self.content)

  def test_midcopy_cancel_keeps_incomplete_output_and_raw(self):
    calls = 0

    def cancel():
      nonlocal calls
      calls += 1
      return calls > 3

    with self.assertRaises(InterruptedError):
      self.create(cancelled=cancel)
    self.assertFalse((self.destination / "bundle.json").exists())
    self.assertEqual(self.source.read_bytes(), self.content)

  def test_manifest_cannot_escape_bundle(self):
    manifest = self.create()
    manifest["files"][0]["path"] = "../route.rlog"
    (self.destination / "bundle.json").write_text(json.dumps(manifest))
    with self.assertRaisesRegex(ValueError, "invalid_evidence_path"):
      verify_bundle(self.destination, byte_limit=4096)

  def test_missing_and_duplicate_sources_rejected(self):
    with self.assertRaises(ValueError):
      create_bundle(self.destination, [self.source, self.source], {}, byte_limit=4096)
    with self.assertRaises(ValueError):
      create_bundle(self.destination, [self.root / "missing"], {}, byte_limit=4096)
    self.assertFalse(self.destination.exists())

  def test_nonfinite_result_and_invalid_budget_rejected(self):
    with self.assertRaises(ValueError):
      create_bundle(self.destination, [self.source], {"value": float("nan")}, byte_limit=4096)
    for budget in (True, 0, -1, 1.5):
      with self.assertRaises(ValueError):
        hash_file(self.source, budget)
    self.assertFalse(self.destination.exists())

  def test_malformed_manifest_is_not_a_complete_bundle(self):
    self.destination.mkdir()
    for value in ([], None, {"schema_version": 1, "state": "complete", "files": [None], "result": {"path": "result.json"}}):
      (self.destination / "bundle.json").write_text(json.dumps(value))
      with self.assertRaises(ValueError):
        verify_bundle(self.destination, byte_limit=4096)


if __name__ == "__main__":
  unittest.main()
