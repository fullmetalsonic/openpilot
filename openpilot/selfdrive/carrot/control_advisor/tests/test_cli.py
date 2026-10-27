import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.__main__ import main, read_jsonl
from openpilot.selfdrive.carrot.control_advisor.registry import readiness

ROOT = Path(__file__).resolve().parents[5]
PROFILE = Path(__file__).resolve().parents[1] / "examples" / "host_profile.json"


class CliTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.source = self.root / "synthetic.jsonl"
    self.output = self.root / "bundle"
    self.header = {"type": "session", "schema_version": 1, "source_kind": "synthetic", "session_id": "host-test",
                   "settings": {"SteerActuatorDelay": 0, "LateralTorqueAccelFactor": 2800, "ApiToken": "excluded"}}
    self.rows = [dict(mono_ns=t * 100000000, axis=axis, target=1.0, actual=0.9 if t != 30 else 5.0,
                     active=True, valid=True, driver_override=20 <= t <= 22, recovery_ready=True,
                     epoch="configuration-A" if t < 40 else "configuration-B", context={"liveDelay": t / 1000})
                 for t in range(60) for axis in ("lateral", "longitudinal")]
    self.write()

  def write(self):
    self.source.write_text("\n".join(json.dumps(row) for row in [self.header, *self.rows]) + "\n", encoding="utf-8")

  def invoke(self, *args):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
      status = main(list(args))
    return status, output.getvalue(), errors.getvalue()

  def test_end_to_end_original_preserved_and_no_vehicle_import(self):
    original = self.source.read_bytes()
    import builtins
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
      if name in ("openpilot.common.params", "openpilot.system.hardware", "openpilot.cereal.messaging"):
        raise AssertionError(f"vehicle import {name}")
      return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=guarded):
      status, stdout, stderr = self.invoke("analyze-jsonl", str(self.source), "--profile", str(PROFILE), "--output", str(self.output))
    self.assertEqual(status, 0, stderr)
    self.assertEqual(json.loads(stdout)["state"], "complete")
    result = json.loads((self.output / "result.json").read_text())
    self.assertEqual(self.source.read_bytes(), original)
    self.assertFalse(result["apply_enabled"])
    self.assertFalse(result["recommendations_enabled"])
    self.assertEqual(result["source"]["source_kind"], "synthetic")
    self.assertNotIn("ApiToken", result["source"]["settings"])
    self.assertEqual(len(result["parameter_readiness"]), 47)
    normalizer = Path(__file__).resolve().parents[1] / "normalizer.py"
    self.assertEqual(result["analyzer_sha256"]["normalizer.py"], hashlib.sha256(normalizer.read_bytes()).hexdigest())
    status, _, errors = self.invoke("verify", str(self.output))
    self.assertEqual(status, 0, errors)

  def test_compare_cli_creates_verified_offline_result_without_overwriting_sources(self):
    from .test_comparison import comparison_fixture
    application, cohort, profile = comparison_fixture()
    paths = [self.root / name for name in ("application.json", "cohort.json", "comparison-profile.json")]
    for path, value in zip(paths, (application, cohort, profile)):
      path.write_text(json.dumps(value), encoding="utf8")
    before = [path.read_bytes() for path in paths]
    status, stdout, stderr = self.invoke("compare", str(paths[0]), "--cohort", str(paths[1]),
                                        "--profile", str(paths[2]), "--output", str(self.output))
    self.assertEqual(0, status, stderr)
    self.assertEqual("comparison_ready", json.loads(stdout)["state"])
    result = json.loads((self.output / "result.json").read_text())
    self.assertEqual("offline_comparison_diagnostic", result["scope"])
    self.assertFalse(result["apply_enabled"])
    self.assertFalse(result["runtime_connected"])
    self.assertFalse(result["raw_sources_verified"])
    self.assertNotIn("receipt", result)
    self.assertEqual(before, [path.read_bytes() for path in paths])
    status, _, errors = self.invoke("verify", str(self.output))
    self.assertEqual(0, status, errors)
    status, _, _ = self.invoke("compare", str(paths[0]), "--cohort", str(paths[1]),
                               "--profile", str(paths[2]), "--output", str(self.output))
    self.assertEqual(2, status)

  def test_actual_module_entrypoint_and_new_output_only(self):
    process = subprocess.run([sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor", "analyze-jsonl", str(self.source),
                              "--profile", str(PROFILE), "--output", str(self.output)], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8")
    self.assertEqual(process.returncode, 0, process.stderr)
    old = (self.output / "bundle.json").read_bytes()
    status, _, _ = self.invoke("analyze-jsonl", str(self.source), "--profile", str(PROFILE), "--output", str(self.output))
    self.assertEqual(status, 2)
    self.assertEqual((self.output / "bundle.json").read_bytes(), old)

  def test_missing_header_nan_duplicate_key_and_oversized_line_rejected(self):
    for content in ('{}\n', '{"type":"session","type":"other"}\n', '{"value":NaN}\n', ' ' * 65537 + '\n'):
      with self.subTest(content=content[:40]):
        self.source.write_text(content)
        status, _, _ = self.invoke("analyze-jsonl", str(self.source), "--profile", str(PROFILE), "--output", str(self.output))
        self.assertEqual(status, 2)
        self.assertFalse(self.output.exists())

  def test_precision_claims_and_profile_promotion_not_accepted(self):
    fake = self.root / "profile.json"
    profile = json.loads(PROFILE.read_text())
    profile["validation_status"] = "vehicle_validated"
    fake.write_text(json.dumps(profile))
    status, _, stderr = self.invoke("analyze-jsonl", str(self.source), "--profile", str(fake), "--output", str(self.output))
    self.assertEqual(status, 2)
    self.assertIn("does_not_accept", stderr)
    self.assertFalse(self.output.exists())

  def test_qlog_keeps_precision_limitation(self):
    self.header["source_kind"] = "qlog"
    self.write()
    status, _, stderr = self.invoke("analyze-jsonl", str(self.source), "--profile", str(PROFILE), "--output", str(self.output))
    self.assertEqual(status, 0, stderr)
    result = json.loads((self.output / "result.json").read_text())
    self.assertIn("qlog_not_precision_tuning_evidence", result["limitations"])

  def test_units_and_special_modes_are_not_interchangeable(self):
    values = {row["key"]: row for row in readiness({"LateralTorqueAccelFactor": 2800, "SteerActuatorDelay": 0, "LeadAccelResponseTF1": -1})}
    self.assertAlmostEqual(values["LateralTorqueAccelFactor"]["current_physical"], 2.8)
    self.assertIsNone(values["SteerActuatorDelay"]["current_physical"])
    self.assertIsNotNone(values["SteerActuatorDelay"]["special_meaning"])
    self.assertIsNone(values["LeadAccelResponseTF1"]["current_physical"])

  def test_downloaded_review_zip_cli_is_source_independent_and_rejects_invalid_zip(self):
    from openpilot.selfdrive.carrot.control_advisor.review_export import ReviewPackBuilder
    source_root, exports = self.root / "review-source", self.root / "exports"
    source_root.mkdir()
    exports.mkdir()
    selection = {"schema_version": 1, "summary_id": "cli-review", "summary_revision": 1,
      "session_id": "cli-session", "source_kind": "synthetic", "historical_only": True,
      "summary": {"state": "provisional", "apply_enabled": False}, "checkpoints": [], "files": [],
      "source_quality": {"coverage_complete": False}, "expected_inventory": []}
    result = ReviewPackBuilder({"source": source_root}, export_root=exports).create_review_pack(exports / "pack", selection)
    source_root.rename(self.root / "original-source-unavailable")
    code, output, error = self.invoke("verify-review", str(result["download_path"]))
    self.assertEqual(code, 0, error)
    verified = json.loads(output)
    self.assertEqual(verified["state"], "verified")
    self.assertTrue(verified["historical_only"])
    self.assertFalse(verified["current_recommendation"])
    self.assertFalse(verified["apply_enabled"])
    invalid = exports / "invalid.zip"
    invalid.write_bytes(b"not-a-review-archive")
    code, output, error = self.invoke("verify-review", str(invalid))
    self.assertEqual(code, 2)
    self.assertFalse(output)
    self.assertIn("control-advisor:", error)

  def test_review_template_actual_entrypoint_exact_hashes_draft_and_exclusive_output(self):
    from openpilot.selfdrive.carrot.control_advisor.review_export import ReviewPackBuilder, review_binding
    from openpilot.selfdrive.carrot.control_advisor.review_results import validate_review
    source, exports = self.root / "template-source", self.root / "template-exports"
    source.mkdir()
    exports.mkdir()
    data = b'{"analyzer_version":"fixture-only-v1"}'
    (source / "result.json").write_bytes(data)
    selection = {"schema_version": 1, "summary_id": "cli-template", "summary_revision": 3, "session_id": None,
      "source_kind": "generated_native", "historical_only": True, "summary": {"state": "partial"}, "checkpoints": [],
      "files": [{"root_id": "source", "relative_path": "result.json", "export_relative_path": "jobs/fixture/result.json",
                 "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": "candidate_context", "sensitivity": "private_context"}],
      "source_quality": {"coverage_complete": False}, "expected_inventory": []}
    created = ReviewPackBuilder({"source": source}, export_root=exports).create_review_pack(exports / "pack", selection)
    source.rename(self.root / "template-source-unavailable")
    process = subprocess.run([sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor", "review-template", str(created["download_path"])],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    self.assertEqual(process.returncode, 0, process.stderr)
    draft = json.loads(process.stdout)
    binding = review_binding(created["download_path"])
    self.assertEqual(draft["bundle_manifest_hash"], binding["manifest_sha256"])
    self.assertEqual(draft["base_summary_sha256"], binding["base_summary_sha256"])
    self.assertEqual((draft["summary_id"], draft["summary_revision"]), ("cli-template", 3))
    self.assertEqual(draft["document_state"], "draft")
    self.assertEqual(draft["declared_reviewer"], {"tool": "", "model": "", "reviewed_at": ""})
    self.assertEqual(draft["uncertainties"], ["검토 전 초안"])
    with self.assertRaisesRegex(ValueError, "not_final"):
      validate_review(draft, binding)
    output = self.root / "review-draft.json"
    code, stdout, stderr = self.invoke("review-template", str(created["download_path"]), "--output", str(output))
    self.assertEqual(code, 0, stderr)
    self.assertEqual(json.loads(stdout)["state"], "draft_created")
    self.assertEqual(json.loads(output.read_text(encoding="utf-8")), draft)
    preserved = output.read_bytes()
    code, _, _ = self.invoke("review-template", str(created["download_path"]), "--output", str(output))
    self.assertEqual(code, 2)
    self.assertEqual(output.read_bytes(), preserved)
    code, _, error = self.invoke("verify-review", str(created["download_path"]))
    self.assertEqual(code, 0, error)


if __name__ == "__main__":
  unittest.main()
