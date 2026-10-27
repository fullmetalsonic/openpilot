"""Immutable generated evidence through trial resolution, existing CAS and export."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.apply_requests import ApplyRequestController, IsolatedPCStore, RequestConflict, validate_request
from openpilot.selfdrive.carrot.control_advisor.calibration_review import build_calibration_review
from openpilot.selfdrive.carrot.control_advisor.candidate_report import calculate_bundle, load_candidate_bundle
from openpilot.selfdrive.carrot.control_advisor.evidence import hash_file
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver
from openpilot.selfdrive.carrot.control_advisor.review_export import ReviewPackBuilder, verify_review_pack
from .test_candidates import candidate_fixture
from .test_request_evidence import request_body
from .test_six_parameter_backend import manual_fixture


def trial_bundle(root, key="LongActuatorDelay"):
  """Controlled recorded-format input, explicitly not a vehicle recording."""
  minimal, profile, config, summary = manual_fixture(key)
  evidence, _ = candidate_fixture()
  evidence.update(source_kind="recorded", evidence_id="controlled-trial-input", objectives={})
  evidence["epoch"] = {**minimal["epoch"], "dependencies": {}}
  evidence["artifacts"] = deepcopy(minimal["artifacts"])
  evidence["consumer_evidence"] = deepcopy(minimal["consumer_evidence"])
  evidence["vehicle_context"].update(code_hashes=deepcopy(minimal["vehicle_context"]["code_hashes"]),
                                     context=deepcopy(minimal["vehicle_context"]["context"]))
  repo = Path(__file__).resolve().parents[5]
  for name in evidence["vehicle_context"]["code_hashes"]:
    evidence["vehicle_context"]["code_hashes"][name] = hash_file(repo / name, 4 * 1024 * 1024)[0]
  for event in evidence["consumer_evidence"]:
    event["source_hashes"] = {name: evidence["vehicle_context"]["code_hashes"][name] for name in event["source_hashes"]}
  for index, block in enumerate(evidence["blocks"]):
    start = evidence["epoch"]["start_ns"] + index * 130_000_000
    block.update(route_id="controlled-route", start_ns=start, end_ns=start + 120_000_000,
                 finalized_through_ns=start + 120_000_000,
                 samples=[{"mono_ns": start + offset * 10_000_000, "signals": {}, "excluded_reasons": []} for offset in range(13)])
  source, settings = root / "input.json", root / "profile.json"
  source.write_text(json.dumps(evidence), encoding="utf8")
  settings.write_text(json.dumps(profile), encoding="utf8")
  review = build_calibration_review(evidence, config, profile, summary,
    input_sha256=hash_file(source, 4 * 1024 * 1024)[0], profile_sha256=hash_file(settings, 4 * 1024 * 1024)[0])
  if review["state"] != "ready":
    raise AssertionError(review["reasons"])
  directory = root / "candidate"
  calculate_bundle(source, settings, directory, calibration_review=review)
  return evidence, directory, load_candidate_bundle(directory)


class ManualCalibrationFlowTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)

  def fixture(self, key="LongActuatorDelay"):
    evidence, directory, envelope = trial_bundle(self.root, key)
    values = {row["key"]: evidence["epoch"]["settings_raw"].get(row["key"]) for row in load_registry()["items"]}
    store = IsolatedPCStore(self.root / "store", "trial-pc", values, session_id="trial-session", clock_ns=lambda: 2_000_000_000)
    self.addCleanup(store.close)
    resolver = CandidateEvidenceResolver({"trial-pc": {"scope": "pc_simulation", "kind": "static", "directory": directory}},
                                        lambda _: store.request_binding(), clock_ns=lambda: 2_000_000_000)
    context = resolver.candidate_context("trial-pc")
    body = request_body(context, key=key, request_id="controlled-manual-trial")
    body.update(purpose="manual_trial", trial_id=envelope["calibration_review"]["trial_options"][0]["id"])
    return store, resolver, body, directory, envelope

  def test_trial_resolver_existing_cas_status_and_replay_preserve_purpose(self):
    store, resolver, body, _, envelope = self.fixture()
    candidate = envelope["calibration_review"]["trial_options"][0]["candidate_raw"]
    actor = ApplyRequestController(self.root / "ledger", resolver, pc_store=store, enabled=True, clock_ns=lambda: 2_000_000_000)
    try:
      self.assertEqual("registered", actor.register(body)["state"])
      stored = actor.execute(body["request_id"])
      self.assertEqual(("stored", "manual_trial", candidate), (stored["state"], stored["purpose"], stored["candidate_raw"]))
      self.assertEqual(candidate, store.snapshot()["values"]["LongActuatorDelay"])
      self.assertFalse(stored["consumer_observed"])
      self.assertFalse(stored["comparison_ready"])
    finally:
      actor.close()
    replay = ApplyRequestController(self.root / "ledger", resolver, pc_store=store, enabled=True, clock_ns=lambda: 2_000_000_000)
    try:
      self.assertEqual("manual_trial", replay.status(body["request_id"])["purpose"])
    finally:
      replay.close()

  def test_forged_trial_and_client_value_cannot_bypass_candidate_gates(self):
    store, resolver, body, _, _ = self.fixture()
    for changed in ({**body, "trial_id": "0" * 64}, {key: value for key, value in body.items() if key not in {"purpose", "trial_id"}}):
      with self.assertRaises(RequestConflict):
        resolver.resolve(changed)
    with self.assertRaises(ValueError):
      validate_request({**body, "candidate_raw": 999})
    self.assertEqual(0, store.snapshot()["revision"])

  def test_export_contains_same_time_series_without_numeric_candidate_or_sources(self):
    _, directory, envelope = trial_bundle(self.root)
    self.assertFalse(any(row["status"] == "search_candidate" for row in envelope["report"]["items"]))
    files = [{"root_id": "candidate", "relative_path": path.relative_to(directory).as_posix(),
      "export_relative_path": "jobs/controlled/" + path.relative_to(directory).as_posix(),
      "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
      "role": "candidate", "sensitivity": "private_context", "copy_mode": "whole"} for path in directory.rglob("*") if path.is_file()]
    selection = {"schema_version": 1, "summary_id": "trial-summary", "summary_revision": 1, "session_id": "trial-session",
      "source_kind": "generated_native", "historical_only": True, "summary": {"state": "ready", "apply_enabled": False,
      "candidate": {"calibration_artifact_sha256": envelope["artifact_sha256"]}}, "files": files, "checkpoints": [],
      "source_quality": {"coverage_complete": False}, "expected_inventory": []}
    (self.root / "exports").mkdir()
    builder = ReviewPackBuilder({"candidate": directory}, export_root=self.root / "exports")
    result = builder.create_review_pack(self.root / "exports/pack", selection)
    payload = self.root / "exports/pack/payload"
    self.assertIn("LongActuatorDelay", (payload / "calibration.csv").read_text())
    self.assertIn("lateral response", (payload / "calibration.svg").read_text())
    analysis = json.loads((payload / "analysis.json").read_text())
    self.assertEqual(envelope["calibration_review"]["diagnostics"], analysis["diagnostics"])
    directory.rename(self.root / "source-offline")
    self.assertEqual("verified", verify_review_pack(result["download_path"])["state"])


if __name__ == "__main__":
  unittest.main()
