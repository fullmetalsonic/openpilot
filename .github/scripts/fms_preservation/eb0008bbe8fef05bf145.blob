from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.review_export import ReviewPackBuilder, verify_review_pack
from openpilot.selfdrive.carrot.control_advisor.review_results import ReviewResultStore, validate_review, review_version_status


def valid_review(binding, revision=1, previous_review_hash=None, scope="summary_only"):
  """Explicit fixture declaration, never an authenticated AI or vehicle review."""
  results = [row for row in binding["files"] if row["role"] == "candidate_context" and Path(row["path"]).name == "result.json"]
  return {"schema_version": 1, "document_state": "final", "bundle_manifest_hash": binding["manifest_sha256"],
          "base_summary_sha256": binding["base_summary_sha256"], "summary_id": binding["summary_id"],
          "summary_revision": binding["summary_revision"], "review_revision": revision, "previous_review_hash": previous_review_hash,
          "scope": scope, "declared_reviewer": {"tool": "test-fixture", "model": "generated-fixture", "reviewed_at": "2026-10-25T00:00:00+09:00"},
          "verdict": "needs_more_data", "base_results": [{"path": row["path"], "base_result_hash": row["sha256"]} for row in results] if scope == "analysis_results" else [],
          "versions": {"raw": [], **{name: {"state": "unknown", "value": None, "evidence_refs": []} for name in ("code", "dbc", "analyzer")}},
          "reviewed_scopes": [{"file": "summary.json", "path_sha256": binding["base_summary_sha256"], "range_ns": None, "checks": ["provenance", "uncertainty"]}],
          "unreviewed_scopes": [{"file": None, "reason": "Generated fixture; no vehicle validation."}], "findings": [],
          "uncertainties": ["Reviewer identity is self-declared."], "follow_up": ["Collect independently measured evidence."]}


class ReviewResultTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.binding = {"manifest_sha256": "a" * 64, "base_summary_sha256": "b" * 64, "summary_id": "summary-fixture", "summary_revision": 2,
                    "session_id": "session-fixture", "files": [
                      {"path": "summary.json", "sha256": "b" * 64, "role": "summary", "sensitivity": "summary"},
                      {"path": "jobs/one/result.json", "sha256": "c" * 64, "role": "candidate_context", "sensitivity": "private_context"},
                      {"path": "session/rlog.bz2", "sha256": "d" * 64, "role": "raw_log", "sensitivity": "private_raw"}]}
    self.path = self.root / "reviews"
    self.store = ReviewResultStore(self.path, self.binding)

  def test_exact_pack_binding_preserves_pack_and_offline_history(self):
    source = self.root / "source"
    source.mkdir()
    data = b'{"state":"fixture_only","apply_enabled":false}\n'
    (source / "result.json").write_bytes(data)
    selection = {"schema_version": 1, "summary_id": "real-pack", "summary_revision": 1, "session_id": None,
                 "source_kind": "generated_native", "historical_only": True, "summary": {"state": "partial"}, "checkpoints": [],
                 "files": [{"root_id": "evidence", "relative_path": "result.json", "export_relative_path": "jobs/one/result.json",
                            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": "candidate_context", "sensitivity": "private_context"}],
                 "source_quality": {"coverage_complete": False}, "expected_inventory": []}
    export_root = self.root / "exports"
    export_root.mkdir()
    pack = export_root / "pack"
    ReviewPackBuilder({"evidence": source}, export_root=export_root).create_review_pack(pack, selection)
    verified = verify_review_pack(pack)
    manifest = json.loads((pack / "payload/review.json").read_bytes())
    summary = next(row for row in manifest["files"] if row["path"] == "summary.json")
    binding = {**verified, "base_summary_sha256": summary["sha256"], "files": manifest["files"]}
    before = {str(path.relative_to(pack)): hashlib.sha256(path.read_bytes()).hexdigest() for path in pack.rglob("*") if path.is_file()}
    store = ReviewResultStore(self.root / "pack-reviews", binding)
    result = store.record("actual-binding", valid_review(binding, scope="analysis_results"))
    self.assertFalse(result["apply_enabled"])
    self.assertFalse(result["identity_verified"])
    self.assertEqual(before, {str(path.relative_to(pack)): hashlib.sha256(path.read_bytes()).hexdigest() for path in pack.rglob("*") if path.is_file()})
    store = ReviewResultStore(self.root / "pack-reviews")
    self.assertEqual(store.binding_state, "unavailable")
    self.assertEqual(store.get(result["review_hash"]), result)
    with self.assertRaisesRegex(ValueError, "review_source_unavailable"):
      store.record("unavailable", valid_review(binding, 2, result["review_hash"]))

  def test_idempotency_aliases_revision_chain_restart(self):
    first = valid_review(self.binding)
    result = self.store.record("one", first)
    contents = {str(p): p.read_bytes() for p in self.path.rglob("*") if p.is_file()}
    self.assertEqual(self.store.record("one", first), result)
    self.assertEqual(contents, {str(p): p.read_bytes() for p in self.path.rglob("*") if p.is_file()})
    alias = self.store.record("alias", first)
    self.assertEqual(alias["review_hash"], result["review_hash"])
    second = valid_review(self.binding, 2, result["review_hash"])
    self.store.record("two", second)
    restored = ReviewResultStore(self.path, self.binding)
    self.assertEqual(len(restored.list_reviews()), 2)
    self.assertEqual(restored.record("alias", first), alias)
    with self.assertRaisesRegex(ValueError, "review_request_conflict"):
      restored.record("alias", second)
    values = restored.list_reviews()
    values[0]["review"]["verdict"] = "retain"
    self.assertEqual(restored.list_reviews()[0]["review"]["verdict"], "needs_more_data")

  def test_revision_and_previous_hash_conflicts(self):
    first = self.store.record("one", valid_review(self.binding))
    cases = [(valid_review(self.binding, 3, first["review_hash"]), "revision_conflict"),
             (valid_review(self.binding, 2, "0" * 64), "previous_hash_mismatch")]
    changed = valid_review(self.binding)
    changed["verdict"] = "retain"
    cases.append((changed, "revision_conflict"))
    for index, (review, reason) in enumerate(cases):
      with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
        self.store.record(f"bad-{index}", review)

  def test_bindings_and_every_reference_are_exact(self):
    for key in ("bundle_manifest_hash", "base_summary_sha256", "summary_id", "summary_revision"):
      review = valid_review(self.binding)
      review[key] = 3 if key == "summary_revision" else "wrong"
      with self.subTest(key=key), self.assertRaisesRegex(ValueError, "binding_mismatch"):
        self.store.record("bad", review)
    mutations = [lambda r: r["reviewed_scopes"][0].update(path_sha256="e" * 64),
                 lambda r: r["reviewed_scopes"][0].update(file="C:/outside"),
                 lambda r: r["findings"].append({"id": "f", "severity": "high", "statement": "Missing source", "evidence_refs": ["not-in-pack"]}),
                 lambda r: r["base_results"][0].update(base_result_hash="e" * 64)]
    for mutate in mutations:
      review = valid_review(self.binding, scope="analysis_results")
      mutate(review)
      with self.subTest(mutate=mutate), self.assertRaises(ValueError):
        self.store.record("bad", review)
    self.assertFalse(self.path.exists())

  def test_no_draft_privilege_or_summary_only_recommendation(self):
    for patch_value in ({"document_state": "draft"}, {"apply_enabled": True}, {"device_qualified": True},
                        {"identity_verified": True}, {"verdict": "reviewed_recommendation"}):
      review = valid_review(self.binding)
      review.update(patch_value)
      with self.subTest(patch_value=patch_value), self.assertRaises(ValueError):
        self.store.record("bad", review)
    review = valid_review(self.binding, scope="analysis_results")
    review["verdict"] = "reviewed_recommendation"
    review["reviewed_scopes"].append({"file": "jobs/one/result.json", "path_sha256": "c" * 64, "range_ns": None, "checks": ["numerical_method"]})
    review["findings"].append({"id": "fixture", "severity": "info", "statement": "Fixture-only review.", "evidence_refs": ["jobs/one/result.json"]})
    result = self.store.record("declared", review)
    self.assertFalse(result["current_recommendation"])
    self.assertFalse(result["device_qualified"])
    self.assertEqual(result["reviewer_identity"], "self_declared")

  def test_raw_and_version_claims_not_promoted(self):
    review = valid_review(self.binding)
    review["versions"]["raw"] = [{"path": "session/rlog.bz2", "sha256": "d" * 64}]
    review["versions"]["code"] = {"state": "declared", "value": "unverified-revision", "evidence_refs": ["summary.json"]}
    self.assertEqual(validate_review(review, self.binding)["versions"]["code"]["value"], "unverified-revision")
    bound = {**self.binding, "version_assertions": {"code": "verified-revision"}}
    with self.assertRaisesRegex(ValueError, "version_mismatch"):
      validate_review(review, bound)
    bound["version_assertions"]["code"] = "unverified-revision"
    self.assertEqual(review_version_status(review, bound)["code"], "matched_export_metadata")
    self.assertEqual(review_version_status(review, bound, source_available=False)["code"], "declared_only")
    self.assertEqual(review_version_status(review, self.binding)["code"], "declared_only")
    review["versions"]["raw"] = [{"path": "summary.json", "sha256": "b" * 64}]
    with self.assertRaisesRegex(ValueError, "raw_version_invalid"):
      validate_review(review, self.binding)
    review["versions"]["raw"] = []
    review["versions"]["dbc"]["value"] = "invented"
    with self.assertRaisesRegex(ValueError, "version_invalid"):
      validate_review(review, self.binding)

  def test_json_limits_duplicate_nonfinite_and_nested_shapes(self):
    raw = json.dumps(valid_review(self.binding))
    invalid = [raw.replace('"schema_version": 1', '"schema_version": 1,"schema_version": 1'),
               raw.replace('"summary_revision": 2', '"summary_revision": NaN'), b"x" * (128 * 1024 + 1)]
    for value in invalid:
      with self.assertRaises(ValueError):
        self.store.record("bad", value)
    for mutate in (lambda r: r.update(uncertainties=["x" * 2049]), lambda r: r.update(findings=[{}] * 65),
                   lambda r: r["reviewed_scopes"][0].update(checks=[{}]), lambda r: r["reviewed_scopes"][0].update(range_ns=[2, 1]),
                   lambda r: r["declared_reviewer"].update(reviewed_at="2026-10-25T00:00:00")):
      review = valid_review(self.binding)
      mutate(review)
      with self.assertRaises(ValueError):
        self.store.record("bad", review)

  def test_restart_tamper_and_binding_substitution_fail_closed(self):
    self.store.record("one", valid_review(self.binding))
    other = deepcopy(self.binding)
    other["manifest_sha256"] = "f" * 64
    with self.assertRaisesRegex(ValueError, "binding_mismatch"):
      ReviewResultStore(self.path, other)
    path = self.path / "0001/record.json"
    path.write_bytes(path.read_bytes().replace(b"needs_more_data", b"retain"))
    saved = path.read_bytes()
    with self.assertRaisesRegex(ValueError, "store_corrupt"):
      ReviewResultStore(self.path, self.binding)
    self.assertEqual(path.read_bytes(), saved)

  def test_partial_and_fsync_failure_never_claim_durable(self):
    with patch("openpilot.selfdrive.carrot.control_advisor.review_results.os.fsync", side_effect=OSError("disk failure")):
      with self.assertRaisesRegex(OSError, "disk failure"):
        self.store.record("one", valid_review(self.binding))
    with self.assertRaisesRegex(ValueError, "store_incomplete"):
      self.store.list_reviews()
    # Even parseable owner bytes are not a completed first review transaction.
    with self.assertRaisesRegex(ValueError, "store_incomplete"):
      ReviewResultStore(self.path, self.binding)
    self.path.joinpath("0001").mkdir()
    before = {str(p): p.read_bytes() for p in self.path.rglob("*") if p.is_file()}
    with self.assertRaisesRegex(ValueError, "store_incomplete"):
      ReviewResultStore(self.path, self.binding)
    self.assertEqual(before, {str(p): p.read_bytes() for p in self.path.rglob("*") if p.is_file()})

  def test_completion_write_failure_retained_and_no_overwrite(self):
    original = self.store._write
    def failing(path, value):
      if path.name == "complete.json":
        raise OSError("completion failure")
      return original(path, value)
    with patch.object(self.store, "_write", side_effect=failing):
      with self.assertRaises(OSError):
        self.store.record("one", valid_review(self.binding))
    path = self.path / "0001/record.json"
    content = path.read_bytes()
    with self.assertRaisesRegex(ValueError, "store_incomplete"):
      ReviewResultStore(self.path, self.binding)
    self.assertEqual(path.read_bytes(), content)

  def test_capacity_preflight_does_not_damage_existing_history(self):
    store = ReviewResultStore(self.path, self.binding, max_reviews=1)
    first = store.record("one", valid_review(self.binding))
    with self.assertRaisesRegex(ValueError, "store_limit"):
      store.record("two", valid_review(self.binding, 2, first["review_hash"]))
    self.assertEqual(len(ReviewResultStore(self.path, self.binding).list_reviews()), 1)
    small = ReviewResultStore(self.root / "small", self.binding, max_bytes=1)
    with self.assertRaisesRegex(ValueError, "store_limit"):
      small.record("one", valid_review(self.binding))
    self.assertFalse((self.root / "small").exists())

  def test_link_root_rejected(self):
    target = self.root / "other"
    target.mkdir()
    link = self.root / "linked"
    try:
      link.symlink_to(target, target_is_directory=True)
    except OSError:
      return
    with self.assertRaisesRegex(ValueError, "path_invalid"):
      ReviewResultStore(link, self.binding)

  def test_recommendation_requires_actual_result_review_and_finding(self):
    review = valid_review(self.binding, scope="analysis_results")
    review["verdict"] = "reviewed_recommendation"
    review["reviewed_scopes"] = []
    with self.assertRaisesRegex(ValueError, "evidence_missing"):
      self.store.record("empty", review)
    review["reviewed_scopes"] = [{"file": "jobs/one/result.json", "path_sha256": "c" * 64, "range_ns": None, "checks": ["numerical_method"]}]
    with self.assertRaisesRegex(ValueError, "evidence_missing"):
      self.store.record("unexplained", review)
    review["findings"] = [{"id": "reason", "severity": "info", "statement": "Declared fixture rationale.", "evidence_refs": ["jobs/one/result.json"]}]
    extra = {"path": "jobs/two/result.json", "sha256": "e" * 64, "role": "candidate_context"}
    binding = {**self.binding, "files": [*self.binding["files"], extra]}
    review["base_results"].append({"path": extra["path"], "base_result_hash": extra["sha256"]})
    with self.assertRaisesRegex(ValueError, "evidence_missing"):
      validate_review(review, binding)
    review["unreviewed_scopes"].append({"file": extra["path"], "reason": "Not reviewed."})
    self.assertEqual(validate_review(review, binding)["verdict"], "reviewed_recommendation")

  def test_binding_owner_bytes_use_same_write_and_load_bound(self):
    binding = deepcopy(self.binding)
    base = binding["files"][1]
    for index in range(508):
      binding["files"].append({**base, "path": f"{index}/" + "x" * 200})
    from openpilot.selfdrive.carrot.control_advisor.review_results import _binding, _canonical
    target = 256 * 1024 - 10
    remaining = target - len(_canonical(_binding(binding)))
    for entry in binding["files"][3:]:
      extra = min(remaining, 512 - len(entry["path"]))
      entry["path"] += "x" * extra
      remaining -= extra
    self.assertEqual(remaining, 0)
    self.assertEqual(len(_canonical(_binding(binding))), target)
    store = ReviewResultStore(self.root / "large", binding)
    owner_bytes = len(_canonical({"schema_version": 1, "kind": "control_advisor_review_results", "binding": store.binding})) + 1
    self.assertGreater(owner_bytes, 256 * 1024)
    with self.assertRaisesRegex(ValueError, "binding_limit"):
      store.record("one", valid_review(binding))
    self.assertFalse((self.root / "large").exists())


if __name__ == "__main__":
  unittest.main()
