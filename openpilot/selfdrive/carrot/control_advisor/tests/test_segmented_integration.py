"""Real log/parser/CAS fixture across journal rotation; no vehicle claims."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.apply_requests import ApplyRequestController, IsolatedPCStore
from openpilot.selfdrive.carrot.control_advisor.journal_snapshot import load_journal_snapshot
from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver, make_comparison_provider
from .test_request_evidence import GeneratedRequestFixture, generated_comparison_profile, request_body


class SegmentedIntegrationTests(unittest.TestCase):
  def test_rotation_preserves_before_after_and_historical_snapshot(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      fixture = GeneratedRequestFixture(root / "generated", recorder_options={
        "journal_format": "segmented", "max_journal_bytes": 256 * 1024, "max_events": 24})
      try:
        pointer = json.loads((fixture.periodic / "latest.json").read_text())
        before = fixture.periodic / "jobs" / pointer["directory"] / "snapshot"
        original_hashes = {path.relative_to(before): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in before.rglob("*") if path.is_file()}
        sealed = load_journal_snapshot(fixture.session, before)
        self.assertTrue(sealed["history"]["selected_closure_complete"])
        self.assertFalse(sealed["history"]["snapshot_prefix_complete"])
        store = IsolatedPCStore(root / "pc-store", "pc-fixture", fixture.values(),
                                session_id=fixture.session_id, clock_ns=fixture.host_clock)
        try:
          resolver = CandidateEvidenceResolver(fixture.targets(), lambda _: store.request_binding(), clock_ns=fixture.host_clock)
          provider = make_comparison_provider(resolver, generated_comparison_profile(), source_kind="generated_native")
          context = resolver.candidate_context("pc-fixture")
          body = request_body(context, key=fixture.key)
          self.assertTrue(next(row for row in context["items"] if row["item_key"] == fixture.key)["eligible"])
          actor = ApplyRequestController(root / "requests", resolver, pc_store=store, enabled=True,
                                         clock_ns=fixture.host_clock, comparison_provider=provider)
          try:
            actor.register(body)
            self.assertEqual(actor.execute(body["request_id"])["state"], "stored")
            fixture.observe_store(store)
            report = provider(actor.comparison_record(body["request_id"]))
            self.assertEqual(report["state"], "comparison_ready", report)
            self.assertFalse(report["comparison"]["causal_improvement_proven"])
            self.assertEqual(130, store.request_binding()["settings_raw"][fixture.key])
          finally:
            actor.close()
        finally:
          store.close()
        self.assertGreaterEqual(fixture.recorder.checkpoint()["catalog"]["entries"], 3)
        historical = load_journal_snapshot(fixture.session, before)
        self.assertEqual(sealed["marker"], historical["marker"])
        self.assertEqual(original_hashes, {path.relative_to(before): hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in before.rglob("*") if path.is_file()})
        standalone = load_journal_snapshot(before, before)
        self.assertEqual(sealed["marker"], standalone["marker"])
        selected = next(item for item in sealed["history_files"] if item["path"].startswith("history/segment-"))
        path = before / selected["path"]
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        with self.assertRaises(ValueError):
          load_journal_snapshot(before, before)
      finally:
        fixture.close()


if __name__ == "__main__":
  unittest.main()
