from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.apply_requests import RequestConflict
from openpilot.selfdrive.carrot.control_advisor.request_evidence import _completion_age_basis, _raw


class CompletionSealTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.job = Path(self.temp.name)
    self.intent = {"trip_binding": {"vehicle_id": "vehicle", "boot_id": "boot", "route": "route"}, "cut_revision": 2}
    self.request = {"job_id": "a" * 64, "analysis_intent": self.intent,
      "identity": {"checkpoint": {"cutoff_mono_ns": 1_000_000_000}, "analysis_intent": deepcopy(self.intent)}}
    self.result = {"analysis_mode": "whole_trip"}
    self.current = {"session_id": "session", "analysis_binding": deepcopy(self.intent), "settings_raw": _raw({})}
    self.seal = {"schema_version": 1, "job_id": "a" * 64, "request_sha256": "b" * 64, "manifest_sha256": "c" * 64,
      "completed_mono_ns": 700_000_000_000,
      "context": {"session_id": "session", "analysis_binding": deepcopy(self.intent), "settings_raw": {}, "vehicle_identity": None}}

  def age_basis(self):
    return _completion_age_basis(self.job, self.request, "b" * 64, self.result, "c" * 64, self.current, "vehicle")

  def write_seal(self, value=None):
    (self.job / "completion-seal.json").write_text(json.dumps(self.seal if value is None else value))

  def test_slow_completion_uses_result_time_without_changing_source_cutoff(self):
    self.assertEqual(self.age_basis(), 1_000_000_000)
    self.write_seal()
    self.assertEqual(self.age_basis(), 700_000_000_000)
    self.assertEqual(self.request["identity"]["checkpoint"]["cutoff_mono_ns"], 1_000_000_000)
    now = 700_000_000_001
    self.assertGreater(now - self.request["identity"]["checkpoint"]["cutoff_mono_ns"], 600_000_000_000)
    self.assertLess(now - self.age_basis(), 600_000_000_000)

  def test_hash_time_identity_and_settings_tampering_fail_closed(self):
    mutations = [lambda s: s.update(job_id="d" * 64), lambda s: s.update(request_sha256="d" * 64),
      lambda s: s.update(manifest_sha256="d" * 64), lambda s: s.update(completed_mono_ns=0),
      lambda s: s["context"].update(session_id="other"),
      lambda s: s["context"]["analysis_binding"].update(cut_revision=3),
      lambda s: s["context"]["analysis_binding"]["trip_binding"].update(route="other"),
      lambda s: s["context"]["settings_raw"].update(PathOffset=10),
      lambda s: s["context"].update(vehicle_identity={"physical_group_id": "other"})]
    for mutation in mutations:
      with self.subTest(mutation=mutation):
        candidate = deepcopy(self.seal)
        mutation(candidate)
        self.write_seal(candidate)
        with self.assertRaises(RequestConflict):
          self.age_basis()

  def test_new_apply_cut_or_trip_cannot_reuse_previous_seal(self):
    self.write_seal()
    self.current["analysis_binding"]["cut_revision"] += 1
    with self.assertRaises(RequestConflict):
      self.age_basis()


if __name__ == "__main__":
  unittest.main()
