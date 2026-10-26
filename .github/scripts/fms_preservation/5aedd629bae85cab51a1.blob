"""Local archive bytes, append-safe prefixes and hostile ZIP boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.review_export import ReviewPackBuilder, review_binding, review_template, verify_review_pack
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder


class ReviewExportTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.evidence, self.exports, self.settings = [self.root / name for name in ("evidence", "exports", "settings")]
    for directory in (self.evidence, self.exports, self.settings):
      directory.mkdir()
    for item in load_registry()["items"]:
      (self.settings / item["key"]).write_text("0", encoding="ascii")
    (self.settings / "CurrentRoute").write_text("00000000--0123456789", encoding="ascii")
    self.recorder = SettingsRecorder(self.settings, self.evidence, "review-session", clock_ns=lambda: 1_000_000_000)
    self.addCleanup(self.recorder.close)
    self.recorder.sample()
    self.token = self.recorder.checkpoint()
    self.raw = self.evidence / "rlog.bz2"
    self.raw.write_bytes(b"private-bench-log-not-a-vehicle-log")
    self.builder = ReviewPackBuilder({"evidence": self.evidence}, export_root=self.exports)

  def selection(self):
    files = []
    for path, role, sensitivity, mode, size in (
        (self.evidence / "settings.jsonl", "settings_history", "private_context", "prefix", self.token["bytes"]),
        (self.raw, "raw_log", "private_raw", "whole", self.raw.stat().st_size)):
      files.append({"root_id": "evidence", "relative_path": path.name, "export_relative_path": "session/" + path.name,
                    "sha256": hashlib.sha256(path.read_bytes()[:size]).hexdigest(), "bytes": size, "role": role,
                    "sensitivity": sensitivity, "copy_mode": mode})
    return {"schema_version": 1, "summary_id": "review-fixture", "summary_revision": 1, "session_id": "review-session",
      "source_kind": "generated_native", "historical_only": True, "summary": {"state": "partial", "apply_enabled": False},
      "checkpoints": [{"kind": "settings", "path": "session/settings.jsonl", "token": self.token}], "files": files,
      "source_quality": {"source_session_closed": False, "coverage_complete": False, "raw_schema_decoded": False},
      "expected_inventory": [{"id": "unavailable-middle", "state": "missing", "reason": "source_not_preserved"}]}

  def create(self, name="pack", **kwargs):
    return self.builder.create_review_pack(self.exports / name, self.selection(), **kwargs)

  def test_summary_only_zip_and_directory_verify_after_original_removed(self):
    before = {path.name: path.read_bytes() for path in self.evidence.iterdir() if path.is_file()}
    created = self.create()
    self.assertEqual(before, {path.name: path.read_bytes() for path in self.evidence.iterdir() if path.is_file()})
    self.recorder.close()
    shutil.rmtree(self.evidence)
    for path in (self.exports / "pack", created["download_path"]):
      result = verify_review_pack(path)
      self.assertEqual(result["state"], "verified")
      self.assertEqual(result["zip_sha256"], created["zip_sha256"])
      self.assertEqual(result["manifest_sha256"], created["manifest_sha256"])
      self.assertEqual(result["bytes"], created["bytes"])
      self.assertTrue(result["historical_only"])
      self.assertFalse(result["current_recommendation"])
      self.assertFalse(result["apply_enabled"])
      self.assertEqual(result["expected_inventory"][0]["state"], "missing")
      self.assertEqual(result["omitted_files"][0]["reason"], "excluded_by_policy")
    with zipfile.ZipFile(created["download_path"]) as archive:
      self.assertNotIn("session/rlog.bz2", archive.namelist())
      self.assertIn(b"bundle_manifest_hash", archive.read("REVIEW_README.md"))
      self.assertNotIn(str(self.evidence).encode(), archive.read("review.json"))

  def test_unconfigured_sources_can_export_only_a_summary_without_fake_roots(self):
    from openpilot.selfdrive.carrot.control_advisor.parking_summary import ParkingSummaryService
    service = ParkingSummaryService({})
    summary = service.freeze_cached({"session": {}}, "manual")
    selection = service.selection(summary["summary_id"], summary["revision"])
    builder = ReviewPackBuilder({}, export_root=self.exports)
    created = builder.create_review_pack(self.exports / "unconfigured", selection)
    verified = verify_review_pack(created["download_path"])
    self.assertEqual(verified["state"], "verified")
    self.assertEqual(verified["files"], 2)
    self.assertIsNone(verified["session_id"])
    self.assertIn("settings_checkpoint_unavailable", verified["captured_source_quality"]["freeze_omissions"])

  def test_matched_comparison_exports_exact_metrics_and_a_self_contained_safe_graph(self):
    selection = self.selection()
    selection["summary"]["requests"] = {"items": [{"request_id": "trial-1", "item_key": "Kf<untrusted>",
      "comparison": {"state": "comparison_ready", "before_rmse": 0.125, "after_rmse": 0.25,
                     "export_source_state": "verified"}}]}
    created = self.builder.create_review_pack(self.exports / "graph", selection)
    self.assertEqual(verify_review_pack(created["download_path"])["state"], "verified")
    with zipfile.ZipFile(created["download_path"]) as archive:
      csv_data = archive.read("comparison.csv").decode()
      svg = archive.read("comparison.svg").decode()
      self.assertIn("0.125,0.25,0.125,verified,false", csv_data)
      self.assertIn("Kf&lt;untrusted&gt;", svg)
      self.assertIn("Observational comparison", svg)
      self.assertNotIn("<script", svg)
      manifest = json.loads(archive.read("review.json"))
      for name in ("comparison.csv", "comparison.svg"):
        entry = next(row for row in manifest["files"] if row["path"] == name)
        self.assertEqual(entry["sha256"], hashlib.sha256(archive.read(name)).hexdigest())

  def test_whole_trip_detail_pages_and_graph_travel_together_in_a_verified_export(self):
    from openpilot.selfdrive.carrot.control_advisor.comparison_stream import write_interval_pages, read_interval_page
    from openpilot.selfdrive.carrot.control_advisor.comparison_intervals import decode_exclusions
    from openpilot.selfdrive.carrot.control_advisor.parking_summary import ParkingSummaryService
    automatic = self.root / "automatic"
    automatic.mkdir()
    database = sqlite3.connect(":memory:")
    self.addCleanup(database.close)
    database.execute("CREATE TABLE intervals(reason TEXT,start INTEGER,end INTEGER,count INTEGER)")
    database.executemany("INSERT INTO intervals VALUES(?,?,?,?)", [("driver_intervention", index * 1000, index * 1000 + 500, 1) for index in range(1000)])
    reference = write_interval_pages(database, automatic)
    row = {"session_id": "paged-session", "request_id": "paged-request", "item_key": "CruiseMaxVals0",
      "scope": "vehicle", "state": "comparison_ready", "comparison_ready": True, "consumer_observed": True,
      "comparison": {"schema_version": 3, "state": "comparison_ready", "scope": "observational_vehicle_acceleration",
        "before": {"exclusions": reference}, "after": {"exclusions": reference},
        "matched": {"before": {"rmse": .3}, "after": {"rmse": .2}, "after_minus_before": {"rmse": -.1}}}}
    service = ParkingSummaryService({"automatic": automatic})
    summary = service.freeze_cached({"session": {"session_id": "paged-session", "source_mode": "live", "state": "stopped"},
      "request_export": {"inventory_complete": True, "requests": [row]}}, "manual")
    selection = service.selection(summary["summary_id"], summary["revision"])
    self.assertNotIn("comparison_interval_details_unavailable", selection["source_quality"]["freeze_omissions"])
    builder = ReviewPackBuilder({"automatic": automatic}, export_root=self.exports)
    created = builder.create_review_pack(self.exports / "paged", selection)
    shutil.rmtree(automatic)
    self.assertEqual(verify_review_pack(created["download_path"])["state"], "verified")
    payload = self.exports / "paged" / "payload"
    intervals = [row for index in range(reference["page_count"]) for row in decode_exclusions(read_interval_page(payload, reference, index), 2)]
    self.assertEqual(len(intervals), 1000)
    self.assertEqual(intervals[-1]["start_ns"], 999000)
    self.assertTrue((payload / "comparison.svg").is_file())
    self.assertTrue((payload / "comparison.csv").is_file())

  def test_explicit_private_raw_preserves_exact_bytes_and_stored_zip(self):
    created = self.create(include_private_raw=True)
    result = verify_review_pack(created["download_path"])
    self.assertTrue(result["include_private_raw"])
    self.assertFalse(result["captured_source_quality"]["raw_schema_decoded"])
    with zipfile.ZipFile(created["download_path"]) as archive:
      self.assertEqual(archive.read("session/rlog.bz2"), self.raw.read_bytes())
      self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))

  def test_binding_and_template_use_exact_zip_hashes_and_direct_versions_only(self):
    from openpilot.selfdrive.carrot.control_advisor import review_export
    from openpilot.selfdrive.carrot.control_advisor.review_results import validate_review
    data = json.dumps({"versions": {"analyzer": "fixture-analyzer-v3"}, "code_version": "fixture-code-2",
                       "analyzer_sha256": {"analysis.py": "a" * 64}}).encode()
    result_path = self.evidence / "result.json"
    result_path.write_bytes(data)
    selection = self.selection()
    selection["files"].append({"root_id": "evidence", "relative_path": "result.json", "export_relative_path": "jobs/fixture/result.json",
      "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": "candidate_context", "sensitivity": "private_context"})
    created = self.builder.create_review_pack(self.exports / "binding", selection, include_private_raw=True)
    with patch.object(review_export, "_verify_review_pack", wraps=review_export._verify_review_pack) as validate_once:
      binding = review_binding(created["download_path"])
      self.assertEqual(validate_once.call_count, 1)
    self.assertEqual(binding["manifest_sha256"], created["manifest_sha256"])
    self.assertEqual(binding["version_assertions"], {"code": "fixture-code-2", "analyzer": "fixture-analyzer-v3"})
    self.assertNotIn("captured_source_quality", binding)
    with zipfile.ZipFile(created["download_path"]) as archive:
      self.assertEqual(binding["base_summary_sha256"], hashlib.sha256(archive.read("summary.json")).hexdigest())
    self.assertEqual(review_binding(self.exports / "binding"), binding)
    draft = review_template(created["download_path"])
    self.assertEqual(draft["document_state"], "draft")
    self.assertEqual(draft["scope"], "analysis_results")
    self.assertEqual(draft["base_results"], [{"path": "jobs/fixture/result.json", "base_result_hash": hashlib.sha256(data).hexdigest()}])
    self.assertEqual(draft["versions"]["raw"], [{"path": "session/rlog.bz2", "sha256": hashlib.sha256(self.raw.read_bytes()).hexdigest()}])
    self.assertEqual(draft["versions"]["dbc"], {"state": "unknown", "value": None, "evidence_refs": []})
    self.assertEqual(draft["versions"]["analyzer"]["evidence_refs"], ["jobs/fixture/result.json"])
    self.assertEqual({row["file"] for row in draft["unreviewed_scopes"]}, {row["path"] for row in binding["files"]})
    with self.assertRaisesRegex(ValueError, "not_final"):
      validate_review(draft, binding)
    completed = deepcopy(draft)
    completed.update(document_state="final", declared_reviewer={"tool": "fixture", "model": "declared-fixture", "reviewed_at": "2026-10-25T12:00:00+09:00"})
    self.assertEqual(validate_review(completed, binding), completed)
    completed["versions"]["analyzer"]["value"] = "wrong-version"
    with self.assertRaisesRegex(ValueError, "version_mismatch"):
      validate_review(completed, binding)

  def test_template_map_versions_stay_unknown_and_tampered_zip_is_rejected(self):
    result_path = self.evidence / "result.json"
    data = json.dumps({"analyzer_sha256": {"analysis.py": "c" * 64}}).encode()
    result_path.write_bytes(data)
    selection = self.selection()
    selection["files"].append({"root_id": "evidence", "relative_path": "result.json", "export_relative_path": "result.json", "sha256": hashlib.sha256(data).hexdigest(),
      "bytes": len(data), "role": "candidate_result", "sensitivity": "private_context"})
    created = self.builder.create_review_pack(self.exports / "map", selection)
    self.assertEqual(review_binding(created["download_path"])["version_assertions"], {})
    self.assertEqual(review_template(created["download_path"])["versions"]["analyzer"]["state"], "unknown")
    created["download_path"].write_bytes(b"invalid")
    with self.assertRaises(ValueError):
      review_binding(created["download_path"])

  def test_mixed_or_oversized_version_metadata_stays_unknown_without_current_code(self):
    from openpilot.selfdrive.carrot.control_advisor import review_export
    selection = self.selection()
    for index, version in enumerate(("analyzer-one", "analyzer-two")):
      path = self.evidence / f"result-{index}.json"
      data = json.dumps({"analyzer_version": version}).encode()
      path.write_bytes(data)
      selection["files"].append({"root_id": "evidence", "relative_path": path.name, "export_relative_path": f"jobs/{index}/result.json",
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": "candidate_context", "sensitivity": "private_context"})
    created = self.builder.create_review_pack(self.exports / "mixed", selection)
    self.assertNotIn("analyzer", review_binding(created["download_path"])["version_assertions"])
    self.assertEqual(review_template(created["download_path"])["versions"]["analyzer"], {"state": "unknown", "value": None, "evidence_refs": []})
    with patch.object(review_export, "_capture_versions", side_effect=AssertionError("ordinary verification parsed result metadata")):
      self.assertEqual(verify_review_pack(created["download_path"])["state"], "verified")
    large = self.evidence / "large-result.json"
    data = json.dumps({"analyzer_version": "not-read", "padding": "x" * (512 * 1024)}).encode()
    large.write_bytes(data)
    selection = self.selection()
    selection["files"].append({"root_id": "evidence", "relative_path": large.name, "export_relative_path": "result.json",
      "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": "analysis_result", "sensitivity": "private_context"})
    oversized = self.builder.create_review_pack(self.exports / "large-metadata", selection)
    self.assertEqual(review_binding(oversized["download_path"])["version_assertions"], {})
    self.assertEqual(review_template(oversized["download_path"])["versions"]["analyzer"]["state"], "unknown")

  def test_template_document_budget_fails_without_silently_dropping_files(self):
    from openpilot.selfdrive.carrot.control_advisor.review_export import _fs_path
    self.assertEqual(self.exports.parent, self.root)
    self.addCleanup(shutil.rmtree, _fs_path(self.exports))
    selection = self.selection()
    selection.update(files=[], checkpoints=[])
    digest, size = hashlib.sha256(self.raw.read_bytes()).hexdigest(), self.raw.stat().st_size
    for index in range(126):
      destination = "files/" + "/".join(["p" * 90] * 4) + "/" + f"f{index:03d}" + "x" * 120 + ".bin"
      selection["files"].append({"root_id": "evidence", "relative_path": self.raw.name, "export_relative_path": destination,
        "sha256": digest, "bytes": size, "role": "original_rlog", "sensitivity": "private_raw"})
    created = self.builder.create_review_pack(self.exports / "large-draft", selection, include_private_raw=True)
    self.assertEqual(len(review_binding(created["download_path"])["files"]), 128)
    with self.assertRaisesRegex(ValueError, "review_template_limit"):
      review_template(created["download_path"])

  def test_real_closed_native_archive_receipt_survives_offline_copy(self):
    from .test_inspection import InspectionTests
    from openpilot.selfdrive.carrot.control_advisor.archive import verify_preserved_segment
    from openpilot.selfdrive.carrot.control_advisor.journal_snapshot import create_journal_snapshot
    fixture = InspectionTests("runTest")
    fixture.setUp()
    self.addCleanup(fixture.doCleanups)
    fixture.complete_job()
    token = fixture.recorder.checkpoint()
    frozen = create_journal_snapshot(fixture.session, fixture.root / "frozen", token)
    selection = self.selection()
    selection.update(session_id="session", source_kind="synthetic", checkpoints=[{"kind": "settings", "path": "session/settings.jsonl", "token": token}])
    selection["files"] = []
    for path in fixture.session.rglob("*"):
      if not path.is_file():
        continue
      relative = path.relative_to(fixture.session).as_posix()
      raw = path.name == "rlog"
      data = path.read_bytes()
      if relative == "settings.jsonl":
        data = data[:token["bytes"]]
      selection["files"].append({"root_id": "native", "relative_path": relative, "export_relative_path": "session/" + relative,
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": "settings_history" if relative == "settings.jsonl" else "raw_log" if raw else "archive_context",
        "sensitivity": "private_raw" if raw else "private_context", "copy_mode": "prefix" if relative == "settings.jsonl" else "whole"})
    selection["source_quality"] = {"snapshot_prefix_complete": True, "source_session_closed": False, "archive_sources": len(frozen["archive_sources"])}
    builder = ReviewPackBuilder({"native": fixture.session}, export_root=self.exports)
    created = builder.create_review_pack(self.exports / "native", selection, include_private_raw=True)
    fixture.recorder.close()
    shutil.rmtree(fixture.session)
    result = verify_review_pack(created["download_path"])
    self.assertEqual(result["state"], "verified")
    archived = self.exports / "native/payload/session/archive" / fixture.saved["archive_id"]
    verified = verify_preserved_segment(archived, byte_limit=1024 * 1024)
    self.assertEqual(verified["sha256"], fixture.saved["sha256"])

  def test_append_after_frozen_prefix_and_torn_tail_are_not_exported(self):
    selection = self.selection()
    original = (self.evidence / "settings.jsonl").read_bytes()
    (self.settings / "PathOffset").write_text("20", encoding="ascii")
    self.recorder.sample()
    with (self.evidence / "settings.jsonl").open("ab") as stream:
      stream.write(b'{"torn_later_record":')
    created = self.builder.create_review_pack(self.exports / "prefix", selection)
    self.assertEqual((self.exports / "prefix/payload/session/settings.jsonl").read_bytes(), original)
    self.assertEqual(verify_review_pack(created["download_path"])["state"], "verified")

  def test_actual_request_ledger_prefix_is_readonly_and_not_recovered(self):
    from .test_apply_requests import pc_request_fixture
    from openpilot.selfdrive.carrot.control_advisor.apply_requests import ApplyRequestController
    store, resolver, clock, body = pc_request_fixture(self.root)
    self.addCleanup(store.close)
    ledger = self.root / "ledger"
    controller = ApplyRequestController(ledger, resolver, pc_store=store, enabled=True, clock_ns=clock)
    self.addCleanup(controller.close)
    controller.register(body)
    snapshot = controller.export_snapshot()
    token = snapshot["checkpoint"]
    data = (ledger / "requests.jsonl").read_bytes()[:token["bytes"]]
    controller.execute(body["request_id"])
    before = (ledger / "requests.jsonl").read_bytes()
    selection = self.selection()
    selection["files"].append({"root_id": "requests", "relative_path": "requests.jsonl", "export_relative_path": "requests/requests.jsonl",
      "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": "request_ledger", "sensitivity": "private_context", "copy_mode": "prefix"})
    selection["checkpoints"].append({"kind": "requests", "path": "requests/requests.jsonl", "token": token})
    builder = ReviewPackBuilder({"evidence": self.evidence, "requests": ledger}, export_root=self.exports)
    with patch.object(ApplyRequestController, "recover", side_effect=AssertionError("export invoked mutating recover")):
      created = builder.create_review_pack(self.exports / "ledger-pack", selection)
      self.assertEqual(verify_review_pack(created["download_path"])["state"], "verified")
    self.assertEqual((ledger / "requests.jsonl").read_bytes(), before)
    self.assertEqual((self.exports / "ledger-pack/payload/requests/requests.jsonl").read_bytes(), data)

  def test_long_absolute_root_nested_payload_and_zip_prefix_verification(self):
    from openpilot.selfdrive.carrot.control_advisor.review_export import _fs_path
    anchor = self.root / "long-anchor"
    self.assertEqual(anchor.parent, self.root)
    root = anchor / ("a" * 90) / ("b" * 90) / ("c" * 50)
    _fs_path(root).mkdir(parents=True)
    self.addCleanup(shutil.rmtree, _fs_path(anchor))
    job = root / ("d" * 32)
    _fs_path(job).mkdir()
    selection = self.selection()
    nested = "jobs/" + "e" * 64 + "-12345678/window-0000/epoch-0000/settings.jsonl"
    selection["files"][0]["export_relative_path"] = nested
    selection["checkpoints"][0]["path"] = nested
    builder = ReviewPackBuilder({"evidence": self.evidence}, export_root=root)
    created = builder.create_review_pack(job / "pack", selection)
    self.assertGreater(len(str(job / "pack/payload" / nested)) + 8, 260)
    self.assertEqual(builder.export_root, root)
    self.assertFalse(str(created["download_path"]).startswith("\\\\?\\"))
    self.assertEqual(verify_review_pack(job / "pack")["state"], "verified")
    self.assertEqual(verify_review_pack(created["download_path"])["state"], "verified")
    with zipfile.ZipFile(_fs_path(created["download_path"])) as archive:
      self.assertIn(nested, archive.namelist())
      self.assertEqual(archive.read(nested), (self.evidence / "settings.jsonl").read_bytes())

  def test_tampered_copy_zip_or_checkpoint_is_rejected(self):
    created = self.create()
    path = self.exports / "pack/payload/summary.json"
    path.write_bytes(path.read_bytes().replace(b"partial", b"unknown"))
    with self.assertRaisesRegex(ValueError, "hash_mismatch"):
      verify_review_pack(self.exports / "pack")
    modified = self.exports / "modified.zip"
    with zipfile.ZipFile(created["download_path"]) as source, zipfile.ZipFile(modified, "w") as target:
      for info in source.infolist():
        data = source.read(info)
        if info.filename == "session/settings.jsonl":
          data = data.replace(b'"revision":1', b'"revision":2')
        target.writestr(info, data)
    with self.assertRaisesRegex(ValueError, "hash_mismatch"):
      verify_review_pack(modified)
    selection = self.selection()
    selection["checkpoints"][0]["token"] = {**self.token, "chain_sha256": "f" * 64}
    with self.assertRaisesRegex(ValueError, "prefix_invalid"):
      self.builder.create_review_pack(self.exports / "invalid-prefix", selection)
    self.assertFalse((self.exports / "invalid-prefix/complete.json").exists())

  def test_cancel_and_limits_keep_partial_and_completed_pack_unchanged(self):
    ready = self.create()
    digest = ready["zip_sha256"]
    calls = 0
    def cancel():
      nonlocal calls
      calls += 1
      return calls > 3
    with self.assertRaises(InterruptedError):
      self.create("cancelled", cancelled=cancel)
    self.assertTrue((self.exports / "cancelled").exists())
    self.assertFalse((self.exports / "cancelled/complete.json").exists())
    with self.assertRaisesRegex(ValueError, "byte_limit"):
      self.create("small", budget={"max_bytes": 200})
    self.assertFalse((self.exports / "small/complete.json").exists())
    with self.assertRaises(TimeoutError), patch("openpilot.selfdrive.carrot.control_advisor.review_export.time.monotonic", side_effect=[0., 999.]):
      self.create("timeout")
    self.assertEqual(verify_review_pack(self.exports / "pack")["zip_sha256"], digest)

  def test_source_changes_during_copy_never_publish_completion(self):
    from openpilot.selfdrive.carrot.control_advisor import review_export
    original = review_export._source_digest
    def changing(path, entry, limits, target=None):
      result = original(path, entry, limits, target)
      if Path(path).name == self.raw.name and target is not None:
        data = self.raw.read_bytes()
        self.raw.write_bytes(b"!" + data[1:])
      return result
    with patch.object(review_export, "_source_digest", side_effect=changing), self.assertRaisesRegex(ValueError, "hash_changed"):
      self.create("changed", include_private_raw=True)
    self.assertFalse((self.exports / "changed/complete.json").exists())

  def test_malformed_missing_manifest_and_bad_crc_are_value_errors(self):
    bad = self.exports / "bad.zip"
    bad.write_bytes(b"not a zip")
    with self.assertRaises(ValueError):
      verify_review_pack(bad)
    with zipfile.ZipFile(bad, "w") as archive:
      archive.writestr("other", b"no manifest")
    with self.assertRaisesRegex(ValueError, "review_pack_invalid"):
      verify_review_pack(bad)
    created = self.create()
    bad.write_bytes(created["download_path"].read_bytes())
    with zipfile.ZipFile(bad) as archive:
      info = archive.getinfo("summary.json")
      offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    with bad.open("r+b") as stream:
      stream.seek(offset)
      stream.write(b"!")
    with self.assertRaisesRegex(ValueError, "review_pack_invalid"):
      verify_review_pack(bad)

  def test_directory_also_verifies_zip_contents_and_manifest(self):
    self.create()
    pack = self.exports / "pack"
    marker = json.loads((pack / "complete.json").read_text())
    download = pack / "review.zip"
    download.write_bytes(b"not-a-zip")
    marker.update(zip_bytes=download.stat().st_size, zip_sha256=hashlib.sha256(download.read_bytes()).hexdigest())
    (pack / "complete.json").write_text(json.dumps(marker))
    with self.assertRaisesRegex(ValueError, "zip_footer_invalid"):
      verify_review_pack(pack)
    # A different valid ZIP is also insufficient: its inner manifest must be
    # identical to the independently verified directory payload manifest.
    selection = self.selection()
    selection["summary_revision"] = 2
    other = self.builder.create_review_pack(self.exports / "revision-two", selection)
    download.write_bytes(other["download_path"].read_bytes())
    marker.update(zip_bytes=download.stat().st_size, zip_sha256=hashlib.sha256(download.read_bytes()).hexdigest())
    (pack / "complete.json").write_text(json.dumps(marker))
    with self.assertRaisesRegex(ValueError, "zip_manifest_mismatch"):
      verify_review_pack(pack)

  def test_paths_roots_and_duplicate_zip_members_are_rejected(self):
    for name in ("../escape", "/absolute", "C:/escape", "a\\escape"):
      selection = self.selection()
      selection["files"][0]["export_relative_path"] = name
      with self.assertRaises(ValueError):
        self.builder.create_review_pack(self.exports / "bad", selection)
    with self.assertRaisesRegex(ValueError, "overlap"):
      ReviewPackBuilder({"evidence": self.evidence}, export_root=self.evidence)
    created = self.create()
    for kind in ("traversal", "duplicate", "symlink", "compression"):
      path = self.exports / (kind + ".zip")
      with zipfile.ZipFile(created["download_path"]) as source, zipfile.ZipFile(path, "w") as target:
        for info in source.infolist():
          target.writestr(info, source.read(info))
        if kind == "traversal":
          target.writestr("../outside", b"bad")
        elif kind == "duplicate":
          with self.assertWarns(UserWarning):
            target.writestr("summary.json", b"bad")
        elif kind == "symlink":
          info = zipfile.ZipInfo("link")
          info.external_attr = (stat.S_IFLNK | 0o777) << 16
          target.writestr(info, b"../outside")
        else:
          target.writestr("compressed", b"x" * 100000, compress_type=zipfile.ZIP_DEFLATED)
      with self.assertRaises(ValueError):
        verify_review_pack(path)


  def test_segmented_selected_history_exports_and_verifies_offline_without_omitted_old_segments(self):
    from openpilot.selfdrive.carrot.control_advisor.journal_segments import iter_verified_catalog
    from openpilot.selfdrive.carrot.control_advisor.journal_snapshot import create_journal_snapshot, load_journal_snapshot
    from openpilot.selfdrive.carrot.control_advisor.settings_history import select_history_window
    from openpilot.selfdrive.carrot.control_advisor.tests.test_log_history import NativeSessionFixture
    fixture_root = self.root / "segmented-source"
    fixture_root.mkdir()
    fixture = NativeSessionFixture(fixture_root, extra_polls=400,
      recorder_options={"journal_format": "segmented", "max_journal_bytes": 256 * 1024, "max_events": 32})
    source, snapshot = fixture.session, self.root / "segmented-snapshot"
    token = fixture.recorder.checkpoint()
    selection = select_history_window(source, token, fixture.time - 245_000_000_000, fixture.time)
    self.assertTrue(selection["omitted_sequence_ranges"])
    omitted = next(row for row in iter_verified_catalog(source, token) if row["index"] not in selection["segment_indices"])
    omitted_path = source / omitted["path"]
    omitted_path.rename(omitted_path.with_suffix(".retained"))
    create_journal_snapshot(source, snapshot, token, window_seconds=180)
    sealed = load_journal_snapshot(source, snapshot)
    self.assertTrue(sealed["history"]["selected_closure_complete"])
    self.assertFalse(sealed["history"]["snapshot_prefix_complete"])
    from openpilot.selfdrive.carrot.control_advisor.inspection import inspect_session
    inspected = inspect_session(source, session_snapshot=snapshot)
    selected_source = inspect_session(source, checkpoint=token, history_selection=sealed["selection"])
    for result in (inspected, selected_source):
      self.assertEqual("persisted_recording_selected_closure_integrity", result["scope"])
      self.assertTrue(result["selected_closure_complete"])
      self.assertFalse(result["snapshot_prefix_complete"])
      self.assertFalse(result["settings_history_complete"])
      self.assertEqual(sealed["history_byte_count"], result["history"]["selected_byte_count"])
    files = []
    for name in ("recording.json", "snapshot.json"):
      data = (snapshot / name).read_bytes()
      files.append({"root_id": "snapshot", "relative_path": name, "export_relative_path": "session/snapshot/" + name,
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": "settings_history" if name == "snapshot.json" else "session_metadata",
        "sensitivity": "private_context", "copy_mode": "whole"})
    for row in sealed["history_files"]:
      files.append({"root_id": "snapshot", "relative_path": row["path"], "export_relative_path": "session/snapshot/" + row["path"],
        "sha256": row["sha256"], "bytes": row["bytes"], "role": "settings_segment", "sensitivity": "private_context", "copy_mode": "prefix"})
    pack_selection = {"schema_version": 1, "summary_id": "segmented-summary", "summary_revision": 1, "session_id": "native-fixture",
      "source_kind": "generated_native", "historical_only": True, "summary": {"historical_only": True, "apply_enabled": False},
      "checkpoints": [{"kind": "segmented_settings", "path": "session/snapshot/snapshot.json", "token": token}], "files": files,
      "source_quality": {"scope": "selected_segment_closure", "omitted_sequence_ranges": sealed["selection"]["omitted_sequence_ranges"]}, "expected_inventory": []}
    builder = ReviewPackBuilder({"snapshot": snapshot}, export_root=self.exports)
    created = builder.create_review_pack(self.exports / "segmented", pack_selection)
    self.assertEqual("verified", verify_review_pack(created["download_path"])["state"])
    bad = deepcopy(pack_selection)
    bad["checkpoints"][0]["token"]["chain_sha256"] = "f" * 64
    with self.assertRaisesRegex(ValueError, "checkpoint_mismatch"):
      builder.create_review_pack(self.exports / "segmented-bad-binding", bad)
    snapshot.rename(self.root / "retained-segmented-snapshot")
    source.rename(self.root / "retained-segmented-source")
    self.assertEqual("verified", verify_review_pack(self.exports / "segmented")["state"])
    self.assertEqual("verified", verify_review_pack(created["download_path"])["state"])


if __name__ == "__main__":
  unittest.main()
