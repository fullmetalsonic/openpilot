"""Verified candidate sources and real isolated PC CAS; no vehicle settings."""
from copy import deepcopy
import bz2
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from openpilot.selfdrive.carrot.control_advisor.apply_requests import ApplyRequestController, IsolatedPCStore, RequestConflict
from openpilot.selfdrive.carrot.control_advisor.candidate_report import calculate_bundle
from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver, KEYS, make_comparison_provider
from .test_candidate_longitudinal import dispatch_fixture


class GeneratedRequestFixture:
  """Generated native logs and a separate fake consumer reading the PC store.

  The response is declared bench data, not an identified vehicle or evidence of
  real driving improvement. No successful resolver/consumer callback is stubbed.
  """
  session_id = "generated-native-request"
  route = "00000000--0123456789"
  boot_id = "00000000-0000-0000-0000-000000000001"
  key = "CruiseMaxVals0"

  def __init__(self, root, *, identity_provider=None, recorder_options=None):
    from .test_log_candidates import log_candidate_configs
    from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder
    self.root, self.now, self.host_now, self.phase = Path(root), 2_000_000_000, 20_000_000_000, 0
    self.root.mkdir(parents=True, exist_ok=False)
    self.settings, self.logs, self.sessions, self.periodic = [self.root / name for name in ("bench-settings", "logs", "sessions", "periodic")]
    for path in (self.settings, self.logs, self.sessions, self.periodic):
      path.mkdir()
    self.session = self.sessions / self.session_id
    self.session.mkdir()
    for key in KEYS:
      (self.settings / key).write_text("100" if key.startswith("CruiseMaxVals") else "0", encoding="ascii")
    (self.settings / "CurrentRoute").write_text(self.route, encoding="ascii")
    (self.session / "recording.json").write_text(json.dumps({"session_id": self.session_id, "source_mode": "live",
      "clock": {"domain": "native_monotonic", "scale": 1}}), encoding="utf-8")
    self.recorder = SettingsRecorder(self.settings, self.session, self.session_id, clock_ns=self.clock, identity_provider=identity_provider, **(recorder_options or {}))
    self._status_lock, self._publisher_stop = threading.Lock(), threading.Event()
    self._status = None
    self.archive = None
    self.profile, self.objectives = log_candidate_configs(self.root)
    self.publish_phase()
    self._publisher = threading.Thread(target=self._heartbeat, daemon=True, name="generated-fixture-publisher")
    self._publisher.start()
    (self.root / "GENERATED_NATIVE_FIXTURE.json").write_text(json.dumps({"source_kind": "generated_native",
      "consumer": "separate_fake_pc_store_reader", "vehicle_qualified": False}), encoding="utf-8")

  def clock(self):
    self.now += 1
    return self.now

  def host_clock(self):
    return self.host_now

  def values(self):
    return {key: int((self.settings / key).read_text(encoding="ascii")) for key in KEYS}

  def targets(self, target_id="pc-fixture"):
    return {target_id: {"scope": "pc_simulation", "kind": "periodic", "directory": self.periodic, "session_root": self.sessions}}

  def observe_store(self, store):
    # The fake observer reads the store independently after its actual CAS.
    binding = store.request_binding()
    raw = binding["settings_raw"][self.key]
    if binding["settings_revision"] <= 0 or type(raw) is not int:
      raise AssertionError("PC CAS must precede independent observation")
    (self.settings / self.key).write_text(str(raw), encoding="ascii")
    self.phase += 1
    self.host_now = (self.phase + 1) * 20_000_000_000
    return self.publish_phase()

  def publish_phase(self):
    from openpilot.selfdrive.carrot.control_advisor.archive import SegmentArchive
    from openpilot.selfdrive.carrot.control_advisor.consumer_observer import CONSUMER_PATHS, OBSERVER_PATH
    from openpilot.selfdrive.carrot.control_advisor.journal_snapshot import create_journal_snapshot
    from openpilot.selfdrive.carrot.control_advisor.log_candidates import calculate_log_candidates
    from openpilot.selfdrive.carrot.control_advisor.candidate_report import _code_hashes
    from openpilot.selfdrive.carrot.control_advisor.periodic import initial_periodic_status
    from openpilot.selfdrive.carrot.control_advisor.request_evidence import _digest
    from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
    repo, base = Path(__file__).resolve().parents[5], (2 + self.phase * 20) * 1_000_000_000
    self.now = base
    observed = self.recorder.sample()
    revision = observed["durable_revision"]
    for index in range(10):
      stamp = base + 50 + index * 1_000_000_000
      for consumer in ("planner.carrot", "planner.longitudinal"):
        fields = {f"CruiseMaxVals{i}": {"read_value": self.values()[f"CruiseMaxVals{i}"] * .01,
          "effective_value": self.values()[f"CruiseMaxVals{i}"] * .01,
          "state": "loaded" if consumer == "planner.carrot" else "used", "source": "stored", "read_mono_ns": base} for i in range(7)}
        context = {"mode": "acc", "driving_mode": "Normal"}
        if consumer == "planner.longitudinal":
          context.update(source="cruise", accel_limit_base=self.values()[self.key] * .01,
                         accel_limit_final=self.values()[self.key] * .01)
        paths = (OBSERVER_PATH, CONSUMER_PATHS[consumer])
        self.recorder.append_consumer_observation({"schema_version": 1, "consumer_id": consumer, "boot_id": self.boot_id,
          "pid": 42, "process_instance_id": "a" * 32, "consumer_instance_id": ("b" if consumer == "planner.carrot" else "c") * 32,
          "sequence": self.phase * 10 + index + 1, "produced_mono_ns": stamp, "cycle_id": self.phase * 10 + index,
          "phase": "sampled_use", "fields": fields, "context": context, "dropped_events": 0,
          "source_hashes": {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in paths}})
      self.now = base + (index + 1) * 1_000_000_000
      self.recorder.sample()
    _, log = _schema()
    def event(kind, stamp, data):
      message = log.Event.new_message(logMonoTime=stamp, valid=True)
      message.init(kind).from_dict(data)
      return message.to_bytes()
    chunks = [event("initData", 10, {"gitCommit": "b" * 40}), event("sentinel", base, {"type": "startOfSegment"})]
    raw = self.values()[self.key]
    measured = .6 + (raw - 100) * (.08 / 30.)  # Explicit fake bench response.
    for index in range(197):
      stamp = base + 100_000_000 + index * 50_000_000
      chunks.extend([event("carControl", stamp, {"enabled": True, "latActive": True, "longActive": True}),
        event("controlsState", stamp, {"lateralControlState": {"torqueState": {"active": True, "actualLateralAccel": .3, "desiredLateralAccel": .4}}}),
        event("longitudinalPlan", stamp, {"aTarget": .7, "myDrivingMode": 3}),
        event("carState", stamp, {"canValid": True, "gearShifter": "drive", "vEgo": 0., "aEgo": measured})])
    start, end = base + 100_000_000, base + 9_900_000_000
    chunks.append(event("sentinel", end + 1, {"type": "endOfSegment"}))
    folder = self.logs / f"{self.route}--{self.phase}"
    folder.mkdir()
    source = folder / "rlog.bz2"
    source.write_bytes(bz2.compress(b"".join(chunks)))
    record = {"revision": self.phase + 1, "window_start_ns": start, "window_end_ns": end,
              "profile_id": "generated-native-request-fixture", "invalidated": False, "partial_window": False}
    route = self.recorder.revision_metadata(revision)["route_observation"]
    encoded = json.dumps({**record, "observed_settings_revision": revision, "route_observation": route},
                         sort_keys=True, separators=(",", ":")).encode()
    diagnostics = self.session / "diagnostics"
    diagnostics.mkdir(exist_ok=True)
    (diagnostics / f"{self.phase + 1}-{hashlib.sha256(encoded).hexdigest()[:16]}.json").write_bytes(encoded)
    ref = self.recorder.append_diagnostic({**record, "sha256": hashlib.sha256(encoded).hexdigest()}, settings_revision=revision, route_observation=route)["journal"]
    if self.archive is None:
      self.archive = SegmentArchive(self.logs, self.session / "archive", session_id=self.session_id, source_mode="live",
        byte_limit=16 * 1024 * 1024, max_files=16, free_reserve_bytes=0)
    saved = self.archive.preserve(source, window_ns=(start, end), diagnostic_revision=self.phase + 1, settings_revision=revision, journal_ref=ref)
    self.recorder.append_archive({key: value for key, value in saved.items() if key not in {"source_relative", "decompressed_bytes"}})
    self.recorder.append_job_status({"diagnostic_revision": self.phase + 1, "settings_revision": revision, "state": "complete",
      "window_start_ns": start, "window_end_ns": end, "route_observation": route, "settings_history_reference": ref,
      "actual_boundary_gaps": [], "gap_tolerance_ns": 250_000_000, "missing_segments": [], "missing_segments_count": 0,
      "endpoint_start_covered": True, "endpoint_end_covered": True, "completion_scope": "archive_work_only_host_profile",
      "coverage_complete": False, "interpolation_applied": False})
    checkpoint = self.recorder.checkpoint()
    identity = {"checkpoint": checkpoint, "profile_sha256": hashlib.sha256(self.profile.read_bytes()).hexdigest(),
                "objectives_sha256": hashlib.sha256(self.objectives.read_bytes()).hexdigest(), "code_hashes": _code_hashes()}
    job_id = _digest(identity)
    job_name = job_id + "-12345678"
    job = self.periodic / "jobs" / job_name
    job.mkdir(parents=True)
    request = {"schema_version": 1, "job_id": job_id, "identity": identity, "session_directory": str(self.session),
      "profile_path": str(self.profile), "objectives_path": str(self.objectives), "interval_seconds": 180,
      "completed_source_keys": [], "eligibility_key": "e" * 64, "settling_retry": False}
    (job / "request.json").write_text(json.dumps(request), encoding="utf-8")
    create_journal_snapshot(self.session, job / "snapshot", checkpoint)
    payload = self.session / "archive" / saved["archive_id"] / source.name
    calculate_log_candidates([payload], self.profile, self.objectives, job / "window-0000", session_directory=self.session, session_snapshot=job / "snapshot")
    window = {"source_start_ns": start, "source_end_ns": end, "state": "ready", "reason": None,
              "directory": "window-0000", "range_kind": "verified_car_state", "coverage_verified": True}
    result = {"schema_version": 1, "job_id": job_id, "source_key": "f" * 64, "settling_retry_needed": False, "state": "ready",
              "source_start_ns": start, "source_end_ns": end, "source_count": 1, "windows": [window]}
    (job / "job-result.json").write_text(json.dumps(result), encoding="utf-8")
    (self.periodic / "latest.json").write_text(json.dumps({"schema_version": 1, "job_id": job_id, "directory": job_name,
      "manifest_sha256": hashlib.sha256((job / "job-result.json").read_bytes()).hexdigest(), "result_revision": self.phase + 1}), encoding="utf-8")
    status = initial_periodic_status(180, state="ready")
    status.update(result_revision=self.phase + 1, attempt={"state": "ready", "job_id": job_id, "cutoff_mono_ns": checkpoint["cutoff_mono_ns"],
      "source_start_ns": start, "source_end_ns": end, "source_count": 1, "processed_windows": 1, "blocked_windows": 0,
      "windows": [{key: window[key] for key in ("source_start_ns", "source_end_ns", "state", "reason")}]},
      last_success={"job_id": job_id, "result_revision": self.phase + 1, "source_start_ns": start, "source_end_ns": end, "source_count": 1})
    with self._status_lock:
      self._status = status
    self.refresh_publisher()
    return {"job": job, "checkpoint": checkpoint, "source": payload, "start_ns": start, "end_ns": end}

  def refresh_publisher(self):
    from openpilot.selfdrive.carrot.control_advisor.periodic import _atomic
    with self._status_lock:
      _atomic(self.periodic / "status.json", {"schema_version": 1, "heartbeat_mono_ns": time.monotonic_ns(),
        "producer_instance_id": "d" * 32, "status": deepcopy(self._status)})

  def _heartbeat(self):
    while not self._publisher_stop.wait(.5):
      try:
        self.refresh_publisher()
      except PermissionError:
        # Windows may temporarily deny replace while a reader holds the file.
        # Keep the old timestamp; retry at the next normal heartbeat only.
        continue

  def publisher_state(self, state):
    with self._status_lock:
      self._status["state"] = state
      self._status["reason"] = "generated_health_failure" if state == "failed" else None
    self.refresh_publisher()

  def close(self):
    self._publisher_stop.set()
    self._publisher.join(timeout=1)
    self.recorder.close()
    if self.archive is not None:
      self.archive.close()


def make_static_request_fixture(root):
  root = Path(root)
  pack, profile = dispatch_fixture()
  relative = "openpilot/selfdrive/carrot/carrot_functions.py"
  pack["vehicle_context"]["code_hashes"][relative] = hashlib.sha256((Path(__file__).resolve().parents[5] / relative).read_bytes()).hexdigest()
  evidence_path, profile_path = root / "fixture-evidence.json", root / "fixture-profile.json"
  evidence_path.write_text(json.dumps(pack), encoding="utf-8")
  profile_path.write_text(json.dumps(profile), encoding="utf-8")
  calculate_bundle(evidence_path, profile_path, root / "candidate")
  return pack, root / "candidate"


def request_body(context, *, key="CruiseMaxVals2", request_id="request-fixture"):
  item = next(row for row in context["items"] if row["item_key"] == key)
  return {"request_id": request_id, "target_id": context["target_id"], "session_id": context["session_id"],
          "artifact_sha256": context["artifact_sha256"], "result_revision": context["result_revision"], "item_key": key,
          "expected_current_raw": item["expected_current_raw"], "expected_settings_revision": context["settings_revision"],
          "dependency_digest": context["dependency_digest"]}


def generated_comparison_profile():
  return {"schema_version": 1, "profile_id": "generated-native-comparison", "version": 1,
    "analysis_profile": {"profile_id": "generated-native-mask", "pre_margin_ns": 100_000_000,
      "recovery_ns": 200_000_000, "max_gap_ns": 250_000_000, "dependency_ns": 100_000_000, "max_samples": 2000},
    "settling_ns": 200_000_000, "min_samples": 20, "min_duration_ns": 1_000_000_000,
    "max_consumer_age_ns": 2_000_000_000, "speed_bins_mps": [-1., 1., 50.], "target_bins": [-10., 0., 2., 10.],
    "required_context_keys": ["planner.longitudinal.mode", "planner.longitudinal.driving_mode", "planner.longitudinal.source",
                              "planner.carrot.driving_mode", "log.accel_mode_factor"], "model_identity_key": None}


class RequestEvidenceTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.now = 50_000_000_000
    self.clock = lambda: self.now

  def fixture(self):
    pack, directory = make_static_request_fixture(self.root)
    values = {key: pack["epoch"]["settings_raw"].get(key) for key in KEYS}
    store = IsolatedPCStore(self.root / "pc-store", "pc-fixture", values, session_id="pc-session", clock_ns=self.clock)
    self.addCleanup(store.close)
    resolver = CandidateEvidenceResolver({"pc-fixture": {"scope": "pc_simulation", "kind": "static", "directory": directory}},
      lambda _: store.request_binding(), clock_ns=self.clock)
    return resolver, store

  def test_fresh_pc_revision_zero_to_verified_candidate_real_cas(self):
    resolver, store = self.fixture()
    self.assertEqual(store.request_binding()["settings_revision"], 0)
    public = resolver.candidate_context("pc-fixture")
    self.assertEqual(len(public["items"]), 47)
    body = request_body(public)
    context = resolver.resolve(body)
    self.assertEqual((context["current_raw"], context["candidate_raw"], context["settings_revision"]), (100, 130, 0))
    self.assertNotIn("CruiseMaxVals2", context["dependencies"])
    controller = ApplyRequestController(self.root / "requests", resolver, pc_store=store, enabled=True, clock_ns=self.clock)
    self.addCleanup(controller.close)
    self.assertEqual(controller.register(body)["state"], "registered")
    stored = controller.execute(body["request_id"])
    self.assertEqual(stored["state"], "stored")
    observed = store.request_binding()
    self.assertEqual(observed["settings_raw"]["CruiseMaxVals2"], 130)
    self.assertEqual(observed["settings_revision"], 1)
    self.assertFalse(stored["apply_enabled"])

  def test_no_client_value_or_path_and_static_vehicle_inference(self):
    resolver, _ = self.fixture()
    body = request_body(resolver.candidate_context("pc-fixture"))
    for key, value in (("candidate_raw", 999), ("path", str(self.root))):
      with self.assertRaises(RequestConflict):
        resolver.resolve({**body, key: value})
    with self.assertRaises(ValueError):
      CandidateEvidenceResolver({"vehicle": {"scope": "vehicle", "kind": "static", "directory": self.root / "candidate"}}, lambda _: {})

  def test_session_directory_cannot_escape_registered_root(self):
    from openpilot.selfdrive.carrot.control_advisor.request_evidence import _session_directory
    sessions = self.root / "sessions"
    sessions.mkdir()
    (sessions / "valid-session").mkdir()
    self.assertEqual(_session_directory(sessions, "valid-session"), (sessions / "valid-session").resolve())
    for session_id in (".", "..", "../outside", str(self.root)):
      with self.assertRaises(RequestConflict):
        _session_directory(sessions, session_id)

  def test_missing_archive_comparison_is_typed_held_through_real_review(self):
    resolver, store = self.fixture()
    provider = make_comparison_provider(resolver, generated_comparison_profile(), source_kind="generated_native")
    body = request_body(resolver.candidate_context("pc-fixture"))
    controller = ApplyRequestController(self.root / "requests", resolver, pc_store=store, enabled=True,
                                        clock_ns=self.clock, comparison_provider=provider)
    self.addCleanup(controller.close)
    controller.register(body)
    controller.execute(body["request_id"])
    result = controller.review(body["request_id"])
    self.assertEqual(result["state"], "stored")
    self.assertFalse(result["comparison_ready"])
    self.assertEqual(result["comparison"]["state"], "held")
    self.assertFalse(result["comparison"]["apply_enabled"])

  def test_controller_family_selectors_use_observed_paths_without_fallbacks(self):
    from openpilot.selfdrive.carrot.control_advisor.request_evidence import _controller_source
    from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
    items = {item["key"]: item for item in load_registry()["items"]}
    cases = {"CruiseMaxVals0": ("planner.longitudinal", "planner.longitudinal.mode"),
      "TFollowGap1": ("planner.long_mpc", "planner.long_mpc.mode"),
      "LateralTorqueAccelFactor": ("controls.torque", "controls.lateral_target.path"),
      "LongTuningKpV": ("controls.longitudinal", "controls.longitudinal.long_control_state")}
    for key, expected in cases.items():
      self.assertEqual(_controller_source(items[key]), expected)

  def test_latest_artifact_tamper_and_session_generation_race_rejected(self):
    resolver, store = self.fixture()
    body = request_body(resolver.candidate_context("pc-fixture"))
    calls = 0
    def changed(_):
      nonlocal calls
      calls += 1
      value = store.request_binding()
      if calls > 1:
        value["generation"] += 1
      return value
    resolver.current_context = changed
    with self.assertRaisesRegex(RequestConflict, "current_context_changed"):
      resolver.resolve(body)
    resolver.current_context = lambda _: store.request_binding()
    result = self.root / "candidate/result.json"
    result.write_bytes(result.read_bytes() + b" ")
    with self.assertRaises(RequestConflict):
      resolver.resolve(body)

  def test_generated_native_publication_real_pc_cas_and_separate_consumer(self):
    fixture = GeneratedRequestFixture(self.root / "generated")
    self.addCleanup(fixture.close)
    store = IsolatedPCStore(self.root / "native-pc-store", "pc-fixture", fixture.values(),
                            session_id=fixture.session_id, clock_ns=fixture.host_clock)
    self.addCleanup(store.close)
    resolver = CandidateEvidenceResolver(fixture.targets(), lambda _: store.request_binding(), clock_ns=fixture.host_clock)
    provider = make_comparison_provider(resolver, generated_comparison_profile(), source_kind="generated_native")
    context = resolver.candidate_context("pc-fixture")
    body = request_body(context, key=fixture.key)
    self.assertTrue(next(row for row in context["items"] if row["item_key"] == fixture.key)["eligible"])
    resolved = resolver.resolve(body)
    self.assertEqual(resolved["candidate_raw"], 130)
    self.assertEqual(resolved["provenance"]["evidence_reference"]["checkpoint"]["session_id"], fixture.session_id)
    controller = ApplyRequestController(self.root / "native-requests", resolver, pc_store=store, enabled=True,
                                        clock_ns=fixture.host_clock, comparison_provider=provider)
    self.addCleanup(controller.close)
    controller.register(body)
    self.assertEqual(controller.execute(body["request_id"])["state"], "stored")
    after = fixture.observe_store(store)
    self.assertTrue(after["source"].is_file())
    latest = resolver.candidate_context("pc-fixture")
    self.assertEqual(latest["result_revision"], 2)
    self.assertEqual(next(row for row in latest["items"] if row["item_key"] == fixture.key)["expected_current_raw"], 130)
    self.assertFalse(next(row for row in latest["items"] if row["item_key"] == fixture.key)["eligible"])
    report = provider(controller.comparison_record(body["request_id"]))
    self.assertEqual(report["state"], "comparison_ready", report)
    self.assertFalse(report["comparison"]["causal_improvement_proven"])
    self.assertFalse(report["comparison"]["consumer_application_ack"])
    from unittest.mock import patch
    with patch.object(resolver, "comparison_cohort", side_effect=AssertionError("unchanged pair decoded again")):
      self.assertEqual(provider(controller.comparison_record(body["request_id"])), report)
    reviewed = controller.review(body["request_id"])
    self.assertEqual(reviewed["state"], "comparison_ready")
    # Background refreshes rebuild the application with its transitioned state.
    # They must rebind the receipt without decoding the unchanged native pair.
    with patch.object(resolver, "comparison_cohort", side_effect=AssertionError("refresh decoded unchanged pair")):
      for _ in range(3):
        refreshed = controller.review(body["request_id"])
        self.assertEqual(refreshed["state"], "comparison_ready")
        self.assertTrue(refreshed["comparison_ready"])
        self.assertEqual(resolver.candidate_context("pc-fixture")["settings_revision"], 1)
    application = controller.comparison_record(body["request_id"])
    for changed_field in ("target", "baseline"):
      changed_application = deepcopy(application)
      if changed_field == "target":
        changed_application["target"]["session_generation"] += 1
      else:
        changed_application["baseline"]["dependencies"]["CruiseMaxVals1"] += 1
      with patch.object(resolver, "comparison_cohort", side_effect=ValueError("changed_application_not_cached")) as rebuild:
        rejected = provider(changed_application)
        self.assertEqual(rebuild.call_count, 1)
        self.assertEqual(rejected["state"], "held")
        self.assertIn("changed_application_not_cached", rejected["reason_codes"])
    disallowed = {**application, "execution_state": "failed"}
    self.assertEqual(provider(disallowed)["state"], "held")
    # A restarted provider's new startup profile cannot reinterpret this request.
    changed = generated_comparison_profile()
    changed["min_samples"] = 999
    restarted = make_comparison_provider(resolver, changed, source_kind="generated_native")
    persisted = restarted(controller.comparison_record(body["request_id"]))
    self.assertEqual(persisted["state"], "comparison_ready", persisted)
    self.assertEqual(persisted["receipt"]["profile_sha256"], report["receipt"]["profile_sha256"])
    # A publication can fail while native cohorts are being decoded. This
    # review must discard its computed positive instead of caching it.
    race_provider = make_comparison_provider(resolver, generated_comparison_profile(), source_kind="generated_native")
    real_cohort = resolver.comparison_cohort
    decoded = []
    def fail_after_decode(*args, **kwargs):
      cohort = real_cohort(*args, **kwargs)
      decoded.append(True)
      fixture.publisher_state("failed")
      return cohort
    try:
      with patch.object(resolver, "comparison_cohort", side_effect=fail_after_decode):
        raced = race_provider(controller.comparison_record(body["request_id"]))
      self.assertEqual(decoded, [True])
      self.assertEqual(raced["state"], "held", raced)
      controller.comparison_provider = race_provider
      failed_review = controller.review(body["request_id"])
      self.assertEqual(failed_review["state"], "stored")
      self.assertFalse(failed_review["comparison_ready"])
    finally:
      fixture.publisher_state("ready")
    # A different actual route in the same recording session cannot inherit the
    # before target's route label merely because the settings still match.
    fixture.route = "00000001--0123456789"
    (fixture.settings / "CurrentRoute").write_text(fixture.route, encoding="ascii")
    fixture.observe_store(store)
    wrong_route = provider(controller.comparison_record(body["request_id"]))
    self.assertEqual(wrong_route["state"], "held", wrong_route)
    self.assertIn("comparison_route_or_boot_changed", wrong_route["reason_codes"])


if __name__ == "__main__":
  unittest.main()
