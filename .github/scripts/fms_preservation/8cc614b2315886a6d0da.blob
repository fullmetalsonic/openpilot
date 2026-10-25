import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.analysis import AnalysisProfile, Observation, analyze
from openpilot.selfdrive.carrot.control_advisor.apply_requests import ApplyRequestController, IsolatedPCStore, verify_request_prefix
from openpilot.selfdrive.carrot.control_advisor.parking_summary import ParkingSummaryService, _relative
from openpilot.selfdrive.carrot.control_advisor.periodic import read_periodic_result
from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver, make_comparison_provider
from openpilot.selfdrive.carrot.control_advisor.review_export import ReviewPackBuilder, verify_review_pack
from openpilot.selfdrive.carrot.control_advisor.rlog import iter_observations
from .test_apply_requests import pc_request_fixture
from .test_request_evidence import GeneratedRequestFixture, generated_comparison_profile, request_body


def cached_session(identifier="session", *, state="running", parking=False):
  profile = AnalysisProfile("explicit-summary-fixture", 100_000_000, 200_000_000, 250_000_000, 100_000_000)
  rows = [Observation(i * 100_000_000, "longitudinal", .7, .6, True, True, False, True, "epoch") for i in range(101)]
  diagnostic = analyze(rows, profile)
  diagnostic.update(revision=1, invalidated=False, partial_window=False, window_start_ns=0, window_end_ns=10_000_000_000)
  return {"session_id": identifier, "source_mode": "live", "state": state, "metadata": {"source_kind": "generated_native"},
    "diagnostic": diagnostic, "history": [{"revision": 1}, {"revision": 2}], "parking_confirmed": parking,
    "parking": {"state": "park_observed" if parking else "moving", "reason": "native_fixture", "observed_mono_ns": 10,
      "offroad_state": "onroad_observed", "source_kind": "native_event", "profile_id": "parking-host-unvalidated-v1"},
    "last_park_observation": None, "age_ms": 0, "received_events": 100}


class ParkingSummaryTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)

  def test_cached_metrics_are_latest_only_and_stop_is_not_parking(self):
    session = cached_session(state="stopped")
    service = ParkingSummaryService(clock_ns=lambda: 10)
    result = service.freeze_cached({"session": session}, "stop")
    self.assertFalse(result["parking_confirmed"])
    self.assertEqual("signal_only", result["source_quality"]["collection_end_confirmation"])
    self.assertEqual("generated_native", result["source_kind"])
    self.assertEqual("latest_only_overlapping_history_not_summed", result["diagnostics"]["history_scope"])
    self.assertEqual(101, result["diagnostics"]["axes"]["longitudinal"]["sample_count"])
    self.assertIsNotNone(result["diagnostics"]["raw_tail"])
    self.assertFalse(result["apply_enabled"])
    self.assertTrue(result["historical_only"])

  def test_ticks_do_not_create_revisions_but_real_changes_and_old_selection_are_immutable(self):
    service = ParkingSummaryService(clock_ns=lambda: 10)
    first_snapshot = {"session": cached_session()}
    first = service.freeze_cached(first_snapshot, "manual")
    ticking = copy.deepcopy(first_snapshot)
    ticking["session"].update(age_ms=999, received_events=900)
    ticking["session"]["parking"]["observed_mono_ns"] = 999
    self.assertEqual(first, service.freeze_cached(ticking, "manual"))
    ticking["session"]["parking"].update(state="unknown", reason="source_stale")
    second = service.freeze_cached(ticking, "manual")
    self.assertEqual(first["summary_id"], second["summary_id"])
    self.assertEqual(first["revision"] + 1, second["revision"])
    self.assertEqual(first, service.selection(first["summary_id"], first["revision"])["summary"])

  def test_cache_eviction_bounds_all_indexes_and_same_session_can_return(self):
    for limit in (2, 64):
      service = ParkingSummaryService(max_summaries=limit)
      first = service.freeze_cached({"session": {"session_id": "a"}}, "manual")
      for number in range(limit):
        service.freeze_cached({"session": {"session_id": str(number)}}, "manual")
      self.assertEqual(limit, len(service._records))
      self.assertLessEqual(len(service._latest), limit)
      self.assertLessEqual(len(service._ids), limit)
      again = service.freeze_cached({"session": {"session_id": "a"}}, "manual")
      self.assertNotEqual(first["summary_id"], again["summary_id"])
      with self.assertRaises(LookupError):
        service.selection(first["summary_id"], first["revision"])

  def test_new_session_never_inherits_an_old_candidate_or_its_sources(self):
    automatic = self.root / "automatic"
    token = {"schema_version": 1, "session_id": "old-session", "sequence": 1, "chain_sha256": "a" * 64,
             "bytes": 1000, "cutoff_mono_ns": 1, "consumer_budget_rejections": 0, "durable": True}
    identity = {"checkpoint": token}
    job_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    name = job_id + "-00000000"
    job = automatic / "jobs" / name
    job.mkdir(parents=True)
    (job / "request.json").write_text(json.dumps({"job_id": job_id, "identity": identity}))
    (automatic / "latest.json").write_text(json.dumps({"result_revision": 1, "job_id": job_id, "directory": name}))
    report = {"state": "ready", "result_revision": 1, "last_success": {"job_id": job_id},
              "report": {"profile_id": "old-profile", "items": [{"status": "search_candidate"}]}}
    service = ParkingSummaryService({"automatic": automatic})
    summary = service.freeze_cached({"session": {"session_id": "new-session"}, "candidate_report": report}, "manual")
    self.assertEqual("previous_session", summary["candidate"]["state"])
    self.assertEqual(0, summary["candidate"]["candidate_count"])
    self.assertIn("candidate_previous_session", summary["source_quality"]["omissions"])
    self.assertEqual([], service.selection(summary["summary_id"], summary["revision"])["files"])
    unlinked = ParkingSummaryService().freeze_cached({"session": {"session_id": "new-session"}, "candidate_report": report}, "manual")
    self.assertEqual("unlinked", unlinked["candidate"]["state"])
    self.assertEqual(0, unlinked["candidate"]["candidate_count"])

  def test_no_diagnostic_durations_or_session_identity_are_invented(self):
    service = ParkingSummaryService()
    summary = service.freeze_cached({"session": {}}, "manual")
    self.assertIsNone(summary["session_id"])
    self.assertEqual({}, summary["diagnostics"]["axes"])
    self.assertIsNone(summary["diagnostics"]["finalized_range"])
    self.assertIn("diagnostic_duration_unknown", summary["source_quality"]["omissions"])
    selection = service.selection(summary["summary_id"], summary["revision"])
    self.assertEqual([], selection["files"])
    self.assertIn("raw_archives_unavailable", selection["source_quality"]["freeze_omissions"])

  def test_cancel_and_deadline_cover_selection_even_when_no_sources_exist(self):
    service = ParkingSummaryService()
    summary = service.freeze_cached({"session": {}}, "manual")
    with self.assertRaises(InterruptedError):
      service.selection(summary["summary_id"], summary["revision"], cancelled=lambda: True)
    with self.assertRaises(TimeoutError):
      service.selection(summary["summary_id"], summary["revision"], deadline=time.monotonic() - 1)
    for path in ("C:/outside", "../outside", "safe\\outside", "/outside"):
      with self.assertRaises(ValueError):
        _relative(path)

  def test_request_prefix_export_does_not_expire_recover_or_write_and_ignores_later_append(self):
    store, resolver, clock, body = pc_request_fixture(self.root)
    self.addCleanup(store.close)
    actor = ApplyRequestController(self.root / "requests", resolver, pc_store=store, enabled=True, clock_ns=clock)
    self.addCleanup(actor.close)
    actor.register(body)
    before = actor.path.read_bytes()
    clock.now += actor.ttl_ns + 1
    with patch.object(actor, "recover", side_effect=AssertionError("readonly recover")), \
         patch.object(actor, "_append", side_effect=AssertionError("readonly write")):
      frozen = actor.export_snapshot()
    self.assertEqual("registered", frozen["requests"][0]["state"])
    self.assertEqual(before, actor.path.read_bytes())
    verified = verify_request_prefix(actor.path, frozen["checkpoint"])
    with actor.path.open("ab") as stream:
      stream.write(b'{"torn_after_cutoff":')
    self.assertEqual(verified, verify_request_prefix(actor.path, frozen["checkpoint"]))
    with actor.path.open("r+b") as stream:
      stream.write(b" ")
    with self.assertRaises(ValueError):
      verify_request_prefix(actor.path, frozen["checkpoint"])

  def test_native_before_after_candidate_journals_raw_and_ledger_export_offline(self):
    self._native_before_after_export()

  def test_segmented_native_summary_exports_selected_history_and_request_ledger_offline(self):
    self._native_before_after_export(recorder_options={"journal_format": "segmented", "max_journal_bytes": 256 * 1024, "max_events": 32})

  def _native_before_after_export(self, *, recorder_options=None):
    fixture = GeneratedRequestFixture(self.root / "native", recorder_options=recorder_options)
    self.addCleanup(fixture.close)
    store = IsolatedPCStore(self.root / "pc-store", "pc-fixture", fixture.values(), session_id=fixture.session_id, clock_ns=fixture.host_clock)
    self.addCleanup(store.close)
    resolver = CandidateEvidenceResolver(fixture.targets(), lambda _: store.request_binding(), clock_ns=fixture.host_clock)
    provider = make_comparison_provider(resolver, generated_comparison_profile(), source_kind="generated_native")
    actor = ApplyRequestController(self.root / "requests", resolver, pc_store=store, enabled=True,
      clock_ns=fixture.host_clock, comparison_provider=provider)
    self.addCleanup(actor.close)
    body = request_body(resolver.candidate_context("pc-fixture"), key=fixture.key)
    actor.register(body)
    actor.execute(body["request_id"])
    after = fixture.observe_store(store)
    self.assertEqual("comparison_ready", actor.review(body["request_id"])["state"])
    mask = AnalysisProfile(**generated_comparison_profile()["analysis_profile"])
    observations = [Observation(**row) for row in iter_observations(after["source"], epoch="native-after", max_age_ns=250_000_000)]
    session = cached_session(fixture.session_id, parking=True)
    session["diagnostic"] = analyze(observations, mask)
    session["diagnostic"].update(revision=2, invalidated=False, partial_window=False,
      window_start_ns=observations[0].mono_ns, window_end_ns=observations[-1].mono_ns)
    roots = {"evidence": fixture.sessions, "automatic": fixture.periodic, "requests": actor.root}
    service = ParkingSummaryService(roots, request_exporter=actor.export_snapshot)
    snapshot = {"session": session, "candidate_report": read_periodic_result(fixture.periodic)}
    summary = service.freeze_cached(snapshot, "parking")
    self.assertEqual(1, summary["requests"]["stored_count"])
    self.assertEqual(1, summary["requests"]["consumer_observed_count"])
    self.assertEqual(1, summary["requests"]["comparison_ready_count"])
    self.assertTrue(summary["parking_confirmed"])
    selection = service.selection(summary["summary_id"], summary["revision"])
    self.assertEqual(["history_selected_closure_only"] if recorder_options else [], selection["source_quality"]["freeze_omissions"], selection["source_quality"])
    self.assertEqual(2, len([row for row in selection["files"] if row["role"] == "original_rlog"]))
    self.assertGreaterEqual(len([row for row in selection["files"] if row["role"] == "candidate_context"]), 12)
    self.assertTrue(any(row["role"] == "request_ledger" for row in selection["files"]))
    self.assertEqual(2, len(selection["expected_inventory"]))
    self.assertTrue(any(row["kind"] == "requests" for row in selection["checkpoints"]))
    self.assertTrue(any(row["kind"] == ("segmented_settings" if recorder_options else "settings") for row in selection["checkpoints"]))
    if recorder_options:
      self.assertTrue(any(row["role"] == "settings_segment" and row["copy_mode"] == "prefix" for row in selection["files"]))
      self.assertFalse(any(row["relative_path"].endswith("/settings.jsonl") for row in selection["files"]))
    # A later publication must not replace an older comparison's after source.
    fixture.observe_store(store)
    later = service.freeze_cached({"session": session, "candidate_report": read_periodic_result(fixture.periodic)}, "parking")
    self.assertGreater(later["revision"], summary["revision"])
    self.assertIn("comparison_after_source_reference_unavailable", later["source_quality"]["omissions"])
    from openpilot.selfdrive.carrot.control_advisor import parking_summary as module
    original_hash = module.hash_file
    def metadata_only(path, *args, **kwargs):
      if Path(path).name in {"rlog", "rlog.bz2", "rlog.zst"}:
        raise AssertionError("selection hashed private raw before opt-in copy")
      return original_hash(path, *args, **kwargs)
    with patch.object(module, "hash_file", side_effect=metadata_only):
      same = service.selection(summary["summary_id"], summary["revision"])
    self.assertEqual(selection, same)
    exports = self.root / "exports"
    exports.mkdir()
    result = ReviewPackBuilder(roots, export_root=exports).create_review_pack(exports / "review", selection, include_private_raw=True)
    self.assertEqual("complete", result["state"])
    actor.close()
    store.close()
    fixture.close()
    # Offline verification really runs with the registered source paths gone.
    for source, destination in ((fixture.root, self.root / "retained-native"), (actor.root, self.root / "retained-requests")):
      self.assertTrue(source.resolve().is_relative_to(self.root.resolve()))
      source.rename(destination)
    self.assertEqual("verified", verify_review_pack(exports / "review")["state"])


if __name__ == "__main__":
  unittest.main()
