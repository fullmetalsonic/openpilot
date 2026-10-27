"""Real sealed native pairs, PC-only decoding, and live review ownership."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from .test_request_evidence import GeneratedRequestFixture, generated_comparison_profile, request_body
from openpilot.selfdrive.carrot.control_advisor.apply_requests import IsolatedPCStore, ApplyRequestController
from openpilot.selfdrive.carrot.control_advisor.evidence import hash_file
from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver, make_comparison_provider
from openpilot.selfdrive.carrot.control_advisor.remote_comparison import (RemoteComparisonStore, comparison_status,
  export_comparison, run_comparison, publish_comparison)
from openpilot.selfdrive.carrot.control_advisor.remote_analysis import _verify, _manifest


class RemoteComparisonTests(unittest.TestCase):
  def _check_live_bindings(self, resolver, store, provider, facade, application, ticket, execution, fixture):
    unchanged = store.request_binding()
    unchanged["observed_mono_ns"] -= 1
    with patch.object(resolver, "current_context", return_value=unchanged):
      self.assertEqual(facade.prepare(application), ticket)
    for field in ("baseline", "target"):
      changed = deepcopy(application)
      if field == "baseline":
        changed["baseline"]["dependencies"]["CruiseMaxVals1"] += 1
      else:
        changed["target"]["session_generation"] += 1
      self.assertEqual(provider(changed)["state"], "held")
    execution["revision"] = "b" * 32
    self.assertEqual(provider(application)["state"], "held")
    execution.update(executor="unknown", revision="a" * 32)
    self.assertEqual(provider(application)["state"], "held")
    execution["executor"] = "remote_pc"
    context = store.request_binding()
    for label, current in (("cut", {**context, "analysis_binding": {"trip_binding": {"vehicle_id": "generated-pc-bench",
      "boot_id": fixture.boot_id, "route": fixture.route}, "cut_revision": 2}}),
      ("restore", {**context, "settings_raw": {**context["settings_raw"], fixture.key: application["old_raw"]}}),
      ("stale", {**context, "settings_observation_fresh": False})):
      with self.subTest(label=label), patch.object(resolver, "current_context", return_value=current):
        self.assertEqual(provider(application)["state"], "held")
    self.assertEqual(provider({**application, "execution_state": "failed"})["state"], "held")

  def _check_packets(self, root, package, ticket):
    bad = root / "bad"
    shutil.copytree(package, bad)
    (bad / "comparison-report.json").write_bytes((bad / "comparison-report.json").read_bytes() + b" ")
    with self.assertRaisesRegex(ValueError, "remote_member_changed"):
      publish_comparison(ticket, bad)
    shutil.rmtree(bad)
    shutil.copytree(package, bad)
    (bad / "extra.json").write_text("{}")
    with self.assertRaisesRegex(ValueError, "inventory_changed"):
      publish_comparison(ticket, bad)
    shutil.rmtree(bad)
    shutil.copytree(package, bad)
    badmanifest = json.loads((bad / "remote-manifest.json").read_text())
    badmanifest["ticket"]["nonce"] = "f" * 32
    (bad / "remote-manifest.json").write_text(json.dumps(badmanifest))
    with self.assertRaisesRegex(ValueError, "binding_changed"):
      publish_comparison(ticket, bad)

  def test_shared_journal_prefix_union_preserves_snapshots_and_rejects_conflict(self):
    from openpilot.selfdrive.carrot.control_advisor.remote_comparison import _copy_tree
    with tempfile.TemporaryDirectory(prefix="rc-prefix-") as temporary:
      root = Path(temporary)
      first, second, shared = (root / name for name in ("first", "second", "shared"))
      first.mkdir()
      second.mkdir()
      for directory, data in ((first, b"first\n"), (second, b"first\nsecond\n")):
        (directory / "settings.jsonl").write_bytes(data)
        (directory / "recording.json").write_text('{"session_id":"shared"}')
        (directory / "snapshot.json").write_text("{}")
      original = (first / "settings.jsonl").read_bytes()
      _copy_tree(first, shared, journal_union=True)
      _copy_tree(second, shared, journal_union=True)
      _copy_tree(first, shared, journal_union=True)
      self.assertEqual((shared / "settings.jsonl").read_bytes(), b"first\nsecond\n")
      self.assertEqual((first / "settings.jsonl").read_bytes(), original)
      self.assertFalse((shared / "snapshot.json").exists())
      (second / "settings.jsonl").write_bytes(b"conflicting\n")
      with self.assertRaisesRegex(ValueError, "closure_conflict"):
        _copy_tree(second, shared, journal_union=True)
      (second / "settings.jsonl").write_bytes(original)
      (second / "recording.json").write_text('{"session_id":"wrong"}')
      with self.assertRaisesRegex(ValueError, "closure_conflict"):
        _copy_tree(second, shared, journal_union=True)

  def test_real_v3_page_closure_and_malformed_page_rejected(self):
    import sqlite3
    from openpilot.selfdrive.carrot.control_advisor.comparison_stream import write_interval_pages
    from openpilot.selfdrive.carrot.control_advisor.remote_comparison import _detail_files
    with tempfile.TemporaryDirectory(prefix="rc-pages-") as temporary:
      root = Path(temporary)
      connection = sqlite3.connect(":memory:")
      self.addCleanup(connection.close)
      connection.execute("CREATE TABLE intervals(reason TEXT,start INTEGER,end INTEGER,count INTEGER)")
      connection.executemany("INSERT INTO intervals VALUES(?,?,?,?)", [("invalid_signal", index * 10, index * 10 + 5, 1) for index in range(1100)])
      reference = write_interval_pages(connection, root)
      report = {"comparison": {"before": {"exclusions": reference}, "after": {"exclusions": reference}}}
      files = _detail_files(root, report)
      self.assertEqual(len(files), reference["page_count"] + 1)
      self.assertIn(reference["path"], files)
      page = root / Path(reference["path"]).parent / "000000.json"
      page.write_bytes(page.read_bytes() + b" ")
      with self.assertRaisesRegex(ValueError, "page_changed"):
        _detail_files(root, report)

  def test_partial_input_extra_and_escape_never_run(self):
    from openpilot.selfdrive.carrot.control_advisor.remote_analysis import _copy_packet
    with tempfile.TemporaryDirectory(prefix="rc-invalid-") as temporary:
      root = Path(temporary)
      source = root / "source"
      source.mkdir()
      (source / "sealed.json").write_text("{}")
      manifest = _manifest(source, "remote_comparison_input", {})
      (source / "extra").write_text("partial")
      with self.assertRaisesRegex(ValueError, "inventory_changed"):
        run_comparison(source, root / "output")
      self.assertFalse((root / "output").exists())
      (source / "extra").unlink()
      manifest["files"][0]["path"] = "../sealed.json"
      (source / "remote-manifest.json").write_text(json.dumps(manifest))
      with self.assertRaisesRegex(ValueError, "member_invalid"):
        run_comparison(source, root / "output")
      manifest["files"][0]["path"] = "sealed.json"
      (source / "sealed.json").write_text("changed")
      with self.assertRaisesRegex(ValueError, "member_changed"):
        _copy_packet(source, root / "staged", manifest)
      self.assertFalse((root / "staged/remote-manifest.json").exists())

  def test_real_native_pair_pc_cli_and_live_review_with_negative_bindings(self):
    retained = os.environ.get("FMS_REMOTE_COMPARISON_EVIDENCE")
    temporary = None if retained else tempfile.TemporaryDirectory(prefix="rc-")
    if temporary:
      self.addCleanup(temporary.cleanup)
    root = Path(retained if retained else temporary.name)
    root.mkdir(parents=True, exist_ok=True)
    fixture = GeneratedRequestFixture(root / "native")
    self.addCleanup(fixture.close)
    store = IsolatedPCStore(root / "store", "pc-fixture", fixture.values(), session_id=fixture.session_id, clock_ns=fixture.host_clock)
    self.addCleanup(store.close)
    resolver = CandidateEvidenceResolver(fixture.targets(), lambda _: store.request_binding(), clock_ns=fixture.host_clock)
    execution = {"executor": "remote_pc", "revision": "a" * 32}
    provider = make_comparison_provider(resolver, generated_comparison_profile(), source_kind="generated_native",
      execution_provider=lambda: execution, remote_directory=root / "remote")
    controller = ApplyRequestController(root / "requests", resolver, pc_store=store, enabled=True,
      clock_ns=fixture.host_clock, comparison_provider=provider)
    self.addCleanup(controller.close)
    body = request_body(resolver.candidate_context("pc-fixture"), key=fixture.key)
    from openpilot.selfdrive.carrot.control_advisor.apply_requests import _context
    resolved = resolver.resolve(body)
    self.assertGreater(len(resolved["provenance"]["code_hashes"]), 64)
    self.assertEqual(_context(resolved), resolved)
    too_many = deepcopy(resolved)
    too_many["provenance"]["code_hashes"] = {f"file{index}.py": "a" * 64 for index in range(129)}
    with self.assertRaisesRegex(ValueError, "code_provenance"):
      _context(too_many)
    oversized = deepcopy(resolved)
    oversized["provenance"]["evidence_reference"]["padding"] = "x" * 32768
    with self.assertRaisesRegex(ValueError, "context_budget"):
      _context(oversized)
    controller.register(body)
    self.assertEqual(controller.execute(body["request_id"])["state"], "stored")
    fixture.observe_store(store)
    application = controller.comparison_record(body["request_id"])
    frozen_files = {str(path.relative_to(fixture.session)): hash_file(path, 512 * 1024 * 1024)
                    for path in fixture.session.rglob("*") if path.is_file()}
    facade = RemoteComparisonStore(root / "remote", resolver, lambda: execution, source_kind="generated_native")
    # The real sourcepair is sealed already. Device operations must never
    # invoke either the raw adapter or archive decoding convenience helpers.
    with patch.object(resolver, "comparison_cohort", side_effect=AssertionError("device decoded")), \
         patch("openpilot.selfdrive.carrot.control_advisor.rlog.iter_events", side_effect=AssertionError("device decoded")), \
         patch("openpilot.selfdrive.carrot.control_advisor.journal_snapshot._archive_summary", side_effect=AssertionError("device inspected")):
      held = provider(application)
      self.assertEqual(held["reason_codes"], ["comparison_on_remote_pc"], held)
      ticket = facade.prepare(application)
      self.assertEqual(ticket, facade.prepare(application))
      self.assertEqual(comparison_status(root / "remote")["items"][0]["state"], "pending")
      export_comparison(ticket, root / "input")
      self.assertEqual(comparison_status(ticket)["state"], "input_exported")
      expected_archives = {str(path.relative_to(fixture.session)): hash_file(path, 512 * 1024 * 1024)
                           for path in (fixture.session / "archive").rglob("*") if path.is_file()}
      copied_archives = {str(path.relative_to(root / "input/sessions" / fixture.session_id)): hash_file(path, 512 * 1024 * 1024)
                         for path in (root / "input/sessions" / fixture.session_id / "archive").rglob("*") if path.is_file()}
      self.assertEqual(expected_archives, copied_archives)
    _, input_sha = _verify(root / "input", "remote_comparison_input")
    # Fresh CLI process, optionally against the parent's source-identical
    # ASCII mirror; no patched successful adapter/evaluator is used.
    pc_root = os.environ.get("FMS_REMOTE_PC_ROOT")
    environment = dict(os.environ)
    if pc_root:
      environment.pop("FMS_SCHEMA_CWD_ADAPTER", None)
      site = str(Path(__file__).resolve().parents[5] / ".venv/Lib/site-packages")
      environment["PYTHONPATH"] = os.pathsep.join((pc_root, str(Path(pc_root) / "opendbc_repo"), site,
        str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/site-packages")))
    command = [sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor", "remote-comparison-run",
               str(root / "input"), "--output", str(root / "pc")]
    process = subprocess.run(command, cwd=pc_root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120)
    (root / "pc-command.json").write_text(json.dumps({"command": command, "cwd": pc_root, "returncode": process.returncode,
      "stdout": process.stdout, "stderr": process.stderr}, ensure_ascii=False), encoding="utf-8")
    self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
    package = root / "pc/result"
    manifest, _ = _verify(package, "remote_comparison_result")
    self.assertEqual(manifest["input_manifest_sha256"], input_sha)
    report = json.loads((package / "comparison-report.json").read_text())
    self.assertEqual(report["state"], "comparison_ready", report)
    self.assertFalse(report["comparison"]["causal_improvement_proven"])
    self.assertFalse(report["comparison"]["consumer_application_ack"])
    with patch.object(resolver, "comparison_cohort", side_effect=AssertionError("device decoded")), \
         patch("openpilot.selfdrive.carrot.control_advisor.rlog.iter_events", side_effect=AssertionError("device decoded")):
      publish_comparison(ticket, package)
      self.assertEqual(publish_comparison(ticket, package)["state"], "already_received")
      # Crash after rename/before marker is a recoverable explicit resend.
      (ticket / "remote-complete.json").unlink()
      self.assertIsNone(facade.result(application))
      self.assertEqual(publish_comparison(ticket, package)["state"], "received_waiting_for_device_review")
      reviewed = controller.review(body["request_id"])
      self.assertEqual(reviewed["state"], "comparison_ready", reviewed)
      self.assertEqual(controller.review(body["request_id"])["state"], "comparison_ready")
      self.assertEqual(facade.result(controller.comparison_record(body["request_id"]))["comparison"], report["comparison"])
      self._check_live_bindings(resolver, store, provider, facade, application, ticket, execution, fixture)
    self._check_packets(root, package, ticket)
    self.assertEqual(frozen_files, {str(path.relative_to(fixture.session)): hash_file(path, 512 * 1024 * 1024)
      for path in fixture.session.rglob("*") if path.is_file()})
    _verify(root / "input", "remote_comparison_input")
    (root / "result.json").write_text(json.dumps({"state": "PASS", "input_manifest_sha256": input_sha,
      "comparison_state": report["state"], "public_schema": report["comparison"]["schema_version"],
      "raw_originals_unchanged": True, "command": command, "pc_root": pc_root, "bench_generated": True}, indent=2), encoding="utf-8")


if __name__ == "__main__":
  unittest.main()
