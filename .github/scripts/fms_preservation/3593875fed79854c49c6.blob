"""Server-owned candidate resolution. Client fields never choose paths or values."""
from copy import deepcopy
from functools import wraps
import hashlib
import json
import math
import re
import time
from pathlib import Path

from .candidate_report import MAX_INPUT_BYTES, MAX_RESULT_BYTES, _code_hashes, _load_reports, _read
from .evidence import hash_file, verify_bundle
from .registry import load_registry


KEYS = frozenset(item["key"] for item in load_registry()["items"])
BODY_KEYS = {"request_id", "target_id", "session_id", "artifact_sha256", "result_revision", "item_key",
             "expected_current_raw", "expected_settings_revision", "dependency_digest"}
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _fail(reason, *, disabled=False):
  from .apply_requests import RequestConflict, RequestDisabled
  raise (RequestDisabled if disabled else RequestConflict)(reason)


def _guarded(function):
  @wraps(function)
  def checked(*args, **kwargs):
    from .apply_requests import RequestConflict, RequestDisabled
    try:
      return function(*args, **kwargs)
    except (RequestConflict, RequestDisabled):
      raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ImportError, OverflowError):
      _fail("request_evidence_unavailable")
  return checked


def _directory(path):
  path = Path(path)
  if not path.is_absolute() or any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (path, *path.parents)) or not path.is_dir():
    _fail("request_evidence_directory_invalid")
  return path.resolve()


def _session_directory(root, session_id):
  root = _directory(root)
  if not isinstance(session_id, str) or session_id in {".", ".."} or not _ID.fullmatch(session_id):
    _fail("request_current_session_unavailable")
  session = _directory(root / session_id)
  if session.parent != root:
    _fail("request_candidate_session_root_mismatch")
  return session


def _status_read(path):
  # A publisher atomically replaces its tiny heartbeat file. Retry only this
  # recognized read race, never malformed metadata or a failed latest result.
  for attempt in range(2):
    try:
      return _read(path, 32 * 1024)
    except ValueError as error:
      if str(error) != "candidate_input_changed" or attempt:
        raise


def _raw(values):
  if type(values) is not dict or set(values) - KEYS or any(value is not None and
      (type(value) not in (int, float) or not math.isfinite(value) or value != int(value) or abs(value) > 1_000_000) for value in values.values()):
    _fail("request_settings_invalid")
  return {key: int(values[key]) if values.get(key) is not None else None for key in sorted(KEYS)}


def _completion_age_basis(job, request, request_hash, result, manifest_hash, current, scope):
  """Freshness of a verified result, independent from its immutable source end."""
  checkpoint = request["identity"]["checkpoint"]
  if result.get("analysis_mode") != "whole_trip":
    return checkpoint["cutoff_mono_ns"]
  intent = request.get("analysis_intent", {})
  if intent != request["identity"].get("analysis_intent"):
    _fail("request_analysis_intent_changed")
  expected = {"trip_binding": intent.get("trip_binding"), "cut_revision": intent.get("cut_revision")}
  if (scope == "vehicle" or "analysis_binding" in current) and current.get("analysis_binding") != expected:
    _fail("request_analysis_binding_changed")
  seal_path = job / "completion-seal.json"
  if not seal_path.exists():
    return checkpoint["cutoff_mono_ns"]
  seal, _ = _read(seal_path, 32 * 1024)
  context = seal.get("context", {})
  if (set(seal) != {"schema_version", "job_id", "request_sha256", "manifest_sha256", "completed_mono_ns", "context"}
      or type(seal["schema_version"]) is not int or seal["schema_version"] != 1 or seal["job_id"] != request["job_id"]
      or seal["request_sha256"] != request_hash or seal["manifest_sha256"] != manifest_hash
      or type(seal["completed_mono_ns"]) is not int or seal["completed_mono_ns"] < checkpoint["cutoff_mono_ns"]
      or context.get("session_id") != current["session_id"] or context.get("analysis_binding") != expected
      or _raw(context.get("settings_raw")) != current["settings_raw"]
      or context.get("vehicle_identity") != current.get("vehicle_identity")):
    _fail("request_completion_seal_invalid")
  return seal["completed_mono_ns"]


def _bundle(directory):
  """Reuse immutable bundle validation, then read its bound source objects."""
  directory = _directory(directory)
  loaded = _load_reports(directory)
  envelope = loaded[-1][0]
  selected, bundle_name = directory, None
  if (directory / "index.json").exists():
    index, _ = _read(directory / "index.json", 256 * 1024)
    bundle_name = index["latest"]
    selected = _directory(directory / bundle_name)
  manifest = verify_bundle(selected, byte_limit=8 * 1024 * 1024, max_files=3)
  evidence, evidence_hash = _read(selected / manifest["files"][0]["path"], MAX_INPUT_BYTES)
  profile, profile_hash = _read(selected / manifest["files"][1]["path"], MAX_INPUT_BYTES)
  result, result_hash = _read(selected / "result.json", MAX_RESULT_BYTES)
  code = _code_hashes()
  if result.get("analyzer_sha256") != code or result_hash != envelope["artifact_sha256"]:
    _fail("request_candidate_code_changed")
  if evidence_hash != result["input_sha256"] or profile_hash != result["profile_sha256"]:
    _fail("request_candidate_source_changed")
  source_code = evidence["vehicle_context"]["code_hashes"]
  repo = Path(__file__).resolve().parents[4]
  from .pid_burst import SOURCE_PATHS
  for relative, expected in source_code.items():
    if not isinstance(relative, str) or not (relative.startswith("openpilot/") or relative in SOURCE_PATHS) or ".." in Path(relative).parts or "\\" in relative:
      _fail("request_source_code_path_invalid")
    if hash_file(repo / relative, MAX_INPUT_BYTES)[0] != expected:
      _fail("request_source_code_changed")
  return {"envelope": envelope, "evidence": evidence, "profile": profile, "profile_hash": profile_hash,
          "evidence_hash": evidence_hash, "code_hashes": code, "bundle_directory": bundle_name,
          "bound_code_hashes": {**code, **source_code}, "source_code_hashes": source_code,
          "selected_directory": selected, "result_hash": result_hash}


def _controller_source(item):
  family = item["family"]
  if family == "acceleration_limit":
    return "planner.longitudinal", "planner.longitudinal.mode"
  if family in {"following_time", "following_speed_scale", "following_deceleration", "lead_response_profile", "lane_change_gap"}:
    return "planner.long_mpc", "planner.long_mpc.mode"
  if family in {"torque", "control_mode"}:
    return "controls.torque", "controls.lateral_target.path"
  if family in {"vehicle_model", "vehicle_model_context"}:
    return "controls.vehicle_model", "controls.lateral_target.path"
  if family in {"lane_mpc_cost", "path_geometry"}:
    return "planner.lateral", "planner.lateral.lane_mode"
  if family == "lane_geometry_context":
    return "planner.lane_path", "controls.lateral_target.path"
  if family in {"delay", "smoothing"}:
    return "modeld.action", "modeld.action.model_action_head"
  return "controls.longitudinal", "controls.longitudinal.long_control_state"


class CandidateEvidenceResolver:
  def __init__(self, targets, current_context, *, clock_ns=time.monotonic_ns, max_age_ns=600_000_000_000,
               request_ttl_ns=30_000_000_000):
    if type(targets) is not dict or len(targets) > 32 or not callable(current_context):
      raise ValueError("invalid_request_targets")
    for identifier, target in targets.items():
      if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or type(target) is not dict or set(target) - {"scope", "kind", "directory", "session_root", "result_revision"}:
        raise ValueError("invalid_request_target")
      if target.get("scope") not in {"vehicle", "pc_simulation"} or target.get("kind") not in {"periodic", "static"}:
        raise ValueError("invalid_request_target_kind")
      if target["kind"] == "static" and target["scope"] != "pc_simulation":
        raise ValueError("static_request_requires_explicit_pc_target")
      if not Path(target.get("directory", "")).is_absolute() or (target["kind"] == "periodic" and not Path(target.get("session_root", "")).is_absolute()):
        raise ValueError("request_target_root_requires_absolute")
    if any(type(value) is not int or value <= 0 for value in (max_age_ns, request_ttl_ns)):
      raise ValueError("invalid_request_evidence_age")
    self.targets, self.current_context = deepcopy(targets), current_context
    self.clock_ns, self.max_age_ns, self.request_ttl_ns = clock_ns, max_age_ns, request_ttl_ns
    self._comparison_profile = None

  def bind_comparison_profile(self, profile):
    from .comparison import _profile
    _profile(profile)
    if len(json.dumps(profile, allow_nan=False).encode()) > 32 * 1024:
      raise ValueError("comparison_profile_budget")
    self._comparison_profile = deepcopy(profile)

  def _current(self, target_id, scope):
    value = deepcopy(self.current_context(target_id))
    required = {"session_id", "generation", "source_mode", "state", "settings_revision", "settings_raw",
                "observed_mono_ns", "settings_observation_fresh"}
    optional = {"identity_version", "vehicle_identity", "analysis_binding"}
    if type(value) is not dict or set(value) - optional != required or value["settings_observation_fresh"] is not True:
      _fail("request_current_observation_unavailable")
    if "analysis_binding" in value:
      from .analysis_control import validate_binding
      value["analysis_binding"] = validate_binding(value["analysis_binding"])
    if set(value) & {"identity_version", "vehicle_identity"}:
      from .vehicle_identity import validate_identity
      if not {"identity_version", "vehicle_identity"} <= set(value) or type(value["identity_version"]) is not int or value["identity_version"] != 1:
        _fail("request_current_vehicle_identity_invalid")
      value["vehicle_identity"] = validate_identity(value["vehicle_identity"])
    if not isinstance(value["session_id"], str) or value["session_id"] in {".", ".."} or not _ID.fullmatch(value["session_id"]) or type(value["generation"]) is not int or value["generation"] < 0:
      _fail("request_current_session_unavailable")
    minimum_revision = 1 if scope == "vehicle" else 0
    if type(value["settings_revision"]) is not int or value["settings_revision"] < minimum_revision or type(value["observed_mono_ns"]) is not int:
      _fail("request_current_revision_unavailable")
    if not 0 <= self.clock_ns() - value["observed_mono_ns"] <= self.max_age_ns:
      _fail("request_current_observation_stale")
    allowed_mode = "live" if scope == "vehicle" else "pc_simulation"
    if value["source_mode"] != allowed_mode or value["state"] not in {"running", "collecting", "waiting_for_data", "evaluating", "degraded"}:
      _fail("request_current_session_inactive")
    value["settings_raw"] = _raw(value["settings_raw"])
    return value

  @staticmethod
  def _same_current(first, second):
    # A fresh later observation of unchanged settings is not a settings change.
    fields = ("session_id", "generation", "source_mode", "state", "settings_revision", "settings_raw")
    return all(first[key] == second[key] for key in fields) and all(first.get(key) == second.get(key) for key in ("identity_version", "vehicle_identity", "analysis_binding"))

  def _capture(self, target_id):
    if target_id not in self.targets:
      _fail("request_target_not_configured", disabled=True)
    target = self.targets[target_id]
    current = self._current(target_id, target["scope"])
    directory = _directory(target["directory"])
    reference = {"kind": target["kind"], "periodic_directory": None, "window_directory": None,
                 "bundle_directory": None, "checkpoint": None}
    pointer_state = None
    route, boot, history_ref = None, None, None
    if target["kind"] == "periodic":
      from .periodic import read_periodic_result, _verify_job
      from .settings_history import validate_checkpoint
      public = read_periodic_result(directory)
      if public.get("reason") == "periodic_result_invalid":
        public = read_periodic_result(directory)
      if public.get("state") != "ready" or public.get("stale") is not False or public.get("report") is None:
        _fail("request_latest_candidate_unavailable")
      pointer, pointer_hash = _read(directory / "latest.json", 16 * 1024)
      status, _ = _status_read(directory / "status.json")
      job_name = pointer.get("directory")
      if not isinstance(job_name, str) or re.fullmatch(r"[a-f0-9]{64}-[a-f0-9]{8}", job_name) is None:
        _fail("request_latest_pointer_invalid")
      job = _directory(directory / "jobs" / job_name)
      request, request_hash = _read(job / "request.json", 64 * 1024)
      identity = request.get("identity")
      if type(identity) is not dict or request.get("job_id") != pointer["job_id"] or _digest(identity) != pointer["job_id"]:
        _fail("request_job_identity_invalid")
      checkpoint = validate_checkpoint(identity.get("checkpoint"))
      if checkpoint["session_id"] != current["session_id"]:
        _fail("request_candidate_session_changed")
      expected_session = _session_directory(target["session_root"], current["session_id"])
      if _directory(request.get("session_directory", "")) != expected_session:
        _fail("request_candidate_session_root_mismatch")
      now = self.clock_ns()
      result, manifest_hash, _ = _verify_job(job, pointer["job_id"])
      if result["state"] != "ready" or manifest_hash != pointer["manifest_sha256"] or not result["windows"]:
        _fail("request_latest_window_unavailable")
      age_basis = _completion_age_basis(job, request, request_hash, result, manifest_hash, current, target["scope"])
      if not 0 <= now - age_basis <= self.max_age_ns:
        _fail("request_candidate_expired")
      window_name = result["windows"][-1]["directory"]
      captured = _bundle(job / window_name)
      if captured["code_hashes"] != identity.get("code_hashes") or hash_file(request["profile_path"], MAX_INPUT_BYTES)[0] != identity.get("profile_sha256") or hash_file(request["objectives_path"], 64 * 1024)[0] != identity.get("objectives_sha256"):
        _fail("request_candidate_configuration_changed")
      if captured["profile_hash"] != identity["profile_sha256"] or captured["result_hash"] != public["artifact_sha256"]:
        _fail("request_candidate_publication_changed")
      revision = pointer["result_revision"]
      reference.update(periodic_directory=job_name, window_directory=window_name, checkpoint=checkpoint)
      from .journal_snapshot import checkpoint_reference
      history_ref = checkpoint_reference(job / "snapshot", checkpoint)
      pointer_state = {"pointer": pointer_hash, "request": request_hash, "status": _digest(status["status"])}
    else:
      captured = _bundle(directory)
      revision = target.get("result_revision", 1)
      if type(revision) is not int or revision < 1:
        _fail("request_static_revision_invalid")
      pointer_state = {"artifact": captured["result_hash"], "revision": revision}
    reference["bundle_directory"] = captured["bundle_directory"]
    evidence = captured["evidence"]
    identity = None
    if target["scope"] == "vehicle":
      from .vehicle_identity import validate_identity, same_vehicle_identity
      source_context = evidence["vehicle_context"].get("context", {})
      try:
        if type(source_context.get("identity_version")) is not int or source_context["identity_version"] != 1 or current.get("identity_version") != 1:
          _fail("request_vehicle_identity_not_recorded")
        identity = validate_identity(source_context.get("vehicle_identity"), require_bound=True)
        validate_identity(current.get("vehicle_identity"), require_bound=True)
      except ValueError:
        _fail("request_vehicle_identity_unconfirmed")
      if not same_vehicle_identity(identity, current["vehicle_identity"]):
        _fail("request_vehicle_identity_changed")
    settings = _raw(evidence["epoch"]["settings_raw"])
    if settings != current["settings_raw"]:
      _fail("request_candidate_settings_changed")
    if evidence["epoch"].get("boot_id") is not None:
      boot = evidence["epoch"]["boot_id"]
    routes = {block["route_id"] for block in evidence["blocks"]}
    route = next(iter(routes)) if len(routes) == 1 else None
    dependency = _digest({"settings_raw": settings, "dependencies": evidence["epoch"]["dependencies"],
                          "vehicle_context": evidence["vehicle_context"], "code_hashes": captured["code_hashes"],
                          "profile_sha256": captured["profile_hash"], "comparison_profile_sha256":
                          _digest(self._comparison_profile) if self._comparison_profile is not None else None})
    captured.update(target_id=target_id, scope=target["scope"], current=current, settings_raw=settings,
      result_revision=revision, dependency_digest=dependency, evidence_reference=reference,
      settings_history_reference=history_ref, route=route, boot_id=boot, pointer_state=pointer_state)
    if identity is not None:
      captured.update(identity_version=1, vehicle_identity=identity)
    return captured

  def _recheck(self, capture):
    current = self._current(capture["target_id"], capture["scope"])
    if not self._same_current(capture["current"], current):
      _fail("request_current_context_changed")
    if _code_hashes() != capture["code_hashes"]:
      _fail("request_candidate_code_changed")
    repo = Path(__file__).resolve().parents[4]
    if any(hash_file(repo / path, MAX_INPUT_BYTES)[0] != digest for path, digest in capture["source_code_hashes"].items()):
      _fail("request_source_code_changed")
    target = self.targets[capture["target_id"]]
    if target["kind"] == "periodic":
      pointer, pointer_hash = _read(Path(target["directory"]) / "latest.json", 16 * 1024)
      status, _ = _status_read(Path(target["directory"]) / "status.json")
      request_path = Path(target["directory"]) / "jobs" / capture["evidence_reference"]["periodic_directory"] / "request.json"
      _, request_hash = _read(request_path, 64 * 1024)
      if {"pointer": pointer_hash, "request": request_hash, "status": _digest(status["status"])} != capture["pointer_state"]:
        _fail("request_latest_pointer_changed")
    else:
      if _bundle(target["directory"])["result_hash"] != capture["result_hash"]:
        _fail("request_latest_pointer_changed")

  def _provenance(self, capture):
    value = {"evidence_sha256": capture["evidence_hash"], "profile_sha256": capture["profile_hash"],
      "code_hashes": capture["bound_code_hashes"], "source_epoch_id": capture["evidence"]["epoch"]["id"],
      "route": capture["route"], "boot_id": capture["boot_id"], "settings_history_reference": capture["settings_history_reference"],
      "evidence_reference": capture["evidence_reference"], "comparison_profile": deepcopy(self._comparison_profile),
      "comparison_profile_sha256": _digest(self._comparison_profile) if self._comparison_profile is not None else None}
    if "vehicle_identity" in capture:
      value.update(identity_version=1, vehicle_identity=deepcopy(capture["vehicle_identity"]))
    return value

  @_guarded
  def capture_evidence(self, target_id, item_key):
    """Private after-capture; keeping the current setting needs no new candidate."""
    if item_key not in KEYS:
      _fail("request_item_unknown")
    capture = self._capture(target_id)
    self._recheck(capture)
    return {"target_id": target_id, "session_id": capture["current"]["session_id"],
      "session_generation": capture["current"]["generation"], "artifact_sha256": capture["result_hash"],
      "result_revision": capture["result_revision"], "settings_revision": capture["current"]["settings_revision"],
      "provenance": self._provenance(capture)}

  def _historical(self, target_id, reference, artifact):
    """Resolve only a frozen server selector, never a caller supplied pathname."""
    target = self.targets.get(target_id)
    if target is None or target["kind"] != "periodic" or type(reference) is not dict or reference.get("kind") != "periodic":
      _fail("comparison_archive_reference_unavailable")
    from .periodic import _verify_job
    from .settings_history import validate_checkpoint
    job_name, window_name = reference.get("periodic_directory"), reference.get("window_directory")
    if not isinstance(job_name, str) or re.fullmatch(r"[a-f0-9]{64}-[a-f0-9]{8}", job_name) is None or not isinstance(window_name, str) or re.fullmatch(r"window-[0-9]{4}", window_name) is None:
      _fail("comparison_reference_invalid")
    root = _directory(target["directory"])
    job = _directory(root / "jobs" / job_name)
    request, _ = _read(job / "request.json", 64 * 1024)
    checkpoint = validate_checkpoint(reference.get("checkpoint"))
    if request.get("identity", {}).get("checkpoint") != checkpoint or _digest(request["identity"]) != request.get("job_id") or not job_name.startswith(request["job_id"] + "-"):
      _fail("comparison_checkpoint_binding_changed")
    session = _session_directory(target["session_root"], checkpoint["session_id"])
    if _directory(request["session_directory"]) != session:
      _fail("comparison_session_root_mismatch")
    result, _, _ = _verify_job(job, request["job_id"])
    if not any(window["directory"] == window_name and window["state"] == "ready" for window in result["windows"]):
      _fail("comparison_window_unavailable")
    captured = _bundle(job / window_name)
    if captured["result_hash"] != artifact or captured["bundle_directory"] != reference.get("bundle_directory"):
      _fail("comparison_artifact_binding_changed")
    captured.update(session_directory=session, snapshot_directory=job / "snapshot", checkpoint=checkpoint)
    return captured

  def _cohort_side(self, capture, item_key, profile, *, force_disk=False):
    from .comparison import _minimum_context
    from .journal_snapshot import load_journal_snapshot
    from .log_history import load_log_history
    from .normalizer import EventNormalizer, SERVICES
    from .rlog import iter_events, validate_closed_log
    item = next(row for row in load_registry()["items"] if row["key"] == item_key)
    pack, token = capture["evidence"], capture["checkpoint"]
    disk_mode = force_disk or "source_collection" in pack
    output_root = capture["snapshot_directory"].parents[2]
    sealed = load_journal_snapshot(capture["session_directory"], capture["snapshot_directory"])
    if sealed["marker"]["checkpoint"] != token:
      _fail("comparison_snapshot_changed")
    requested = {entry["sha256"]: entry["id"] for entry in pack.get("source_collection", {}).get("sources", pack["artifacts"])}
    receipts, paths = [], []
    for receipt in sealed["archive_receipts"].values():
      if receipt["sha256"] not in requested:
        continue
      marker_path = capture["session_directory"] / "archive" / receipt["archive_id"] / "archive.json"
      marker, marker_hash = _read(marker_path, 128 * 1024)
      if marker_hash != receipt["manifest_sha256"] or marker["file"]["path"] not in {"rlog", "rlog.zst", "rlog.bz2"}:
        _fail("comparison_archive_marker_changed")
      receipts.append({"id": requested[receipt["sha256"]], "sha256": receipt["sha256"], "bytes": receipt["bytes"],
                       "route": receipt["route"], "segment": receipt["segment"]})
      paths.append(marker_path.parent / marker["file"]["path"])
    if len(receipts) != len(requested) or not 1 <= len(paths) <= (128 if "source_collection" in pack else 8):
      _fail("comparison_archive_sources_unavailable")
    history = load_log_history(capture["session_directory"], receipts, session_snapshot=capture["snapshot_directory"])
    epochs = [epoch for epoch in history.get("epochs", []) if epoch["id"] == pack["epoch"]["id"]]
    if history["state"] != "linked" or len(epochs) != 1:
      _fail("comparison_settings_epoch_unlinked")
    epoch = epochs[0]
    required = set(profile["required_context_keys"]) | set(_minimum_context(item)[0])
    controller_source, mode_source = _controller_source(item)
    required.add(mode_source)
    context, inconsistent = {}, set()
    if disk_mode:
      from .journal_snapshot import iter_snapshot_consumer_events
      events = iter_snapshot_consumer_events(capture["session_directory"], capture["snapshot_directory"])
    else:
      events = history["consumer_evidence"]
    controller_observed = False
    start, end = pack["epoch"]["start_ns"], pack["epoch"]["end_ns"]
    for event in events:
      controller_observed |= event["consumer_id"] == controller_source and item_key in event["fields"]
      if not start - profile["max_consumer_age_ns"] <= event["produced_mono_ns"] <= end:
        continue
      for name, value in event["context"].items():
        key = event["consumer_id"] + "." + name
        if key in required:
          entry = {"value": value, "source": f"consumer:{event['consumer_id']}.context.{name}"}
          if key in context and context[key] != entry:
            inconsistent.add(key)
          context[key] = entry
    observations, mode_values = [], set()
    writer, last_observed = None, None
    if disk_mode:
      from .comparison_stream import InputWriter
      writer = InputWriter(output_root / "comparison-cache")
    try:
      input_bytes = decoded_bytes = count = 0
      for source, receipt in sorted(zip(paths, receipts), key=lambda pair: pair[1]["segment"]):
        before = hash_file(source, 256 * 1024 * 1024)
        input_bytes += before[1]
        if before != (receipt["sha256"], receipt["bytes"]) or input_bytes > (512 if disk_mode else 256) * 1024 * 1024:
          _fail("comparison_original_source_changed")
        remaining = 256 * 1024 * 1024 if disk_mode else 256 * 1024 * 1024 - decoded_bytes
        if remaining <= 0:
          _fail("comparison_decode_budget")
        coverage = validate_closed_log(source, max_events=200_000 if disk_mode else 200_000 - count, max_decompressed_bytes=remaining)
        if coverage["decompressed_bytes"] * 2 > remaining:
          _fail("comparison_decode_budget")
        decoded_bytes += coverage["decompressed_bytes"] * 2
        normalizer = EventNormalizer(epoch=epoch["id"], max_age_ns=profile["analysis_profile"]["max_gap_ns"])
        # The same bounded event decoder and normalizer retain source timestamps;
        # no candidate objective is substituted for the native response.
        for event in iter_events(source, services=SERVICES, max_events=coverage["event_count"], max_decompressed_bytes=coverage["decompressed_bytes"]):
          if event["service"] == "longitudinalPlan" and event["valid"] and start <= event["mono_ns"] <= end:
            mode = event["data"].get("myDrivingMode")
            if type(mode) is int and mode in (1, 2, 3, 4):
              mode_values.add(mode)
            else:
              inconsistent.add("log.accel_mode_factor")
          for observation in normalizer.feed(event["service"], event["mono_ns"], event["valid"], event["data"]):
            if observation["axis"] == item["axis"]:
              last_observed = observation["mono_ns"]
              if writer is not None:
                writer.append(observation)
              else:
                observations.append(observation)
                if len(observations) > profile["analysis_profile"]["max_samples"]:
                  _fail("comparison_sample_budget")
        count += coverage["event_count"]
        if hash_file(source, receipt["bytes"]) != before:
          _fail("comparison_original_source_changed")
      if writer is not None:
        observations = writer.finish()
    finally:
      if writer is not None:
        writer.close()
    if len(mode_values) == 1 and "log.accel_mode_factor" not in inconsistent:
      context["log.accel_mode_factor"] = {"value": {1: .9, 2: .8, 3: 1., 4: 1.2}[next(iter(mode_values))], "source": "longitudinalPlan.myDrivingMode"}
    for key in inconsistent:
      context.pop(key, None)
    normalized = _raw(epoch["settings_raw"])
    dependencies = {key: value for key, value in normalized.items() if key != item_key}
    mode = context.get(mode_source, {}).get("value", "unknown")
    controller = controller_source if controller_observed else "unknown"
    from .journal_snapshot import checkpoint_reference
    reference = checkpoint_reference(capture["snapshot_directory"], token)
    side = {"settings_raw": normalized, "dependencies": dependencies, "code_hashes": capture["bound_code_hashes"],
      "controller_path": controller, "mode": mode, "target_unit": "m/s^2", "context": context,
      "window": {"start_ns": start, "end_ns": end, "finalized_through_ns": min(end, last_observed -
        profile["analysis_profile"]["pre_margin_ns"] - profile["analysis_profile"]["dependency_ns"]) if last_observed is not None else start},
      "observations": observations, "eligible_intervals": deepcopy(epoch["eligible_intervals"]), "history_reference": reference}
    if disk_mode:
      side["comparison_output_root"] = str(output_root)
    return side, history, sealed, {receipt["id"]: receipt["sha256"] for receipt in receipts}

  @_guarded
  def comparison_cohort(self, application, after_capture, profile, *, source_kind="recorded"):
    # This method runs on the existing advisor calculation thread. Linux policy
    # target 0 is the calling thread; server/control threads are not retargeted.
    from .worker_runtime import configure_worker_runtime
    execution = configure_worker_runtime("live")
    if execution["state"] != "applied" and not (execution["state"] == "not_applicable" and execution["reason"] == "non_native_runtime"):
      _fail("comparison_worker_policy_incomplete")
    target = application["target"]
    if after_capture["target_id"] != target["target_id"] or after_capture["session_id"] != target["session_id"] or after_capture["session_generation"] != target["session_generation"]:
      _fail("comparison_current_target_changed")
    before = self._historical(target["target_id"], application["source_candidate"]["evidence_reference"], application["source_candidate"]["bundle_sha256"])
    after = self._historical(target["target_id"], after_capture["provenance"]["evidence_reference"], after_capture["artifact_sha256"])
    if application["source_kind"] == "vehicle_stored":
      from .vehicle_identity import same_vehicle_identity
      old_identity = before["evidence"]["vehicle_context"].get("context", {}).get("vehicle_identity")
      new_identity = after["evidence"]["vehicle_context"].get("context", {}).get("vehicle_identity")
      if (not same_vehicle_identity(old_identity, new_identity)
          or not same_vehicle_identity(new_identity, after_capture["provenance"].get("vehicle_identity"))):
        _fail("comparison_vehicle_identity_unconfirmed_or_changed")
    for capture in (before, after):
      routes = {block["route_id"] for block in capture["evidence"]["blocks"]}
      if routes != {target["route"]} or capture["evidence"]["epoch"].get("boot_id") != target["boot_id"]:
        _fail("comparison_route_or_boot_changed")
    if any(after_capture["provenance"].get(key) != target[key] for key in ("route", "boot_id")):
      _fail("comparison_route_or_boot_changed")
    if before["evidence_hash"] != application["source_candidate"]["evidence_sha256"] or before["profile_hash"] != application["source_candidate"]["profile_sha256"]:
      _fail("comparison_before_candidate_changed")
    disk_mode = any("source_collection" in capture["evidence"] for capture in (before, after))
    old_side, old_history, old_sealed, old_hashes = self._cohort_side(before, application["key"], profile, force_disk=disk_mode)
    new_side, new_history, new_sealed, new_hashes = self._cohort_side(after, application["key"], profile, force_disk=disk_mode)
    event_reader = None
    if disk_mode:
      from .comparison_stream import InputWriter, InputReader
      from .journal_snapshot import iter_snapshot_consumer_events
      writer = InputWriter(Path(old_side["comparison_output_root"]) / "comparison-cache", kind="events")
      try:
        for capture in (before, after):
          for event in iter_snapshot_consumer_events(capture["session_directory"], capture["snapshot_directory"]):
            writer.append(event)
        events = writer.finish()
      finally:
        writer.close()
      event_reader = InputReader(events, kind="events")
      event_iterable = event_reader.rows()
    else:
      events = { _digest(event): event for event in [*old_history["consumer_evidence"], *new_history["consumer_evidence"]] }
      events = sorted(events.values(), key=lambda event: (event["produced_mono_ns"], event["consumer_id"], event["sequence"]))
      event_iterable = iter(events)
    change = next((entry["record"]["observation"].get("change_interval") for entry in new_sealed["history"]["records"]
      if entry["record"]["type"] == "settings_observation" and application["key"] in entry["record"].get("changed_keys", [])
      and entry["record"]["observation"]["end_ns"] >= application["stored_mono_ns"]), None)
    if change is None:
      if event_reader is not None:
        event_reader.close()
      _fail("comparison_setting_change_unobserved")
    from .consumer_observer import consumer_ids_for_key
    from .comparison import _field_matches
    required = set(consumer_ids_for_key(application["key"]))
    boundaries = {}
    scale = 1 if application["key"] == "SpeedTFFactor" else next(item["raw_scale"] for item in load_registry()["items"] if item["key"] == application["key"])
    try:
      for event in event_iterable:
        if (event["consumer_id"] in required and event["produced_mono_ns"] >= application["stored_mono_ns"]
            and _field_matches(event, application["key"], application["candidate_raw"] * scale)):
          boundaries.setdefault(event["consumer_id"], event["produced_mono_ns"])
    finally:
      if event_reader is not None:
        event_reader.close()
    if disk_mode:
      from .periodic import _usage
      if _usage(Path(old_side["comparison_output_root"])) > 512 * 1024 * 1024:
        _fail("comparison_cache_budget")
    floor = max(change["before_ns"], application["stored_mono_ns"], max(boundaries.values(), default=application["stored_mono_ns"])) + profile["settling_ns"]
    new_side["window"]["start_ns"] = max(new_side["window"]["start_ns"], floor)
    # Consumer events remain intact. The pure evaluator determines the last old
    # and first new observation for every required consumer independently.
    return {"schema_version": 1, "request_id": application["request_id"], "request_sha256": application["request_sha256"],
      "source_kind": source_kind, "target": deepcopy(target), "session_id": target["session_id"], "route": target["route"],
      "boot_id": target["boot_id"], "history_reference": new_side["history_reference"], "source_hashes": {**old_hashes, **new_hashes},
      "consumer_events": events, "setting_change_interval": change, "before": old_side, "after": new_side}

  @_guarded
  def candidate_context(self, target_id):
    capture = self._capture(target_id)
    self._recheck(capture)
    items = []
    for item in capture["envelope"]["report"]["items"]:
      raw = capture["settings_raw"][item["key"]]
      eligible = item["status"] == "search_candidate" and type(item["candidate_raw"]) is int and type(raw) is int
      items.append({"item_key": item["key"], "eligible": eligible,
                    "reason": None if eligible else "request_candidate_not_available", "expected_current_raw": raw})
    return {"target_id": target_id, "scope": capture["scope"], "session_id": capture["current"]["session_id"],
            "artifact_sha256": capture["result_hash"], "result_revision": capture["result_revision"],
            "settings_revision": capture["current"]["settings_revision"], "dependency_digest": capture["dependency_digest"], "items": items}

  @_guarded
  def resolve(self, body):
    if type(body) is not dict or set(body) != BODY_KEYS or body.get("item_key") not in KEYS:
      _fail("request_body_invalid")
    capture = self._capture(body["target_id"])
    if any(body[key] != value for key, value in (("session_id", capture["current"]["session_id"]),
        ("artifact_sha256", capture["result_hash"]), ("result_revision", capture["result_revision"]))):
      _fail("request_candidate_identity_changed")
    item = next(item for item in capture["envelope"]["report"]["items"] if item["key"] == body["item_key"])
    raw = capture["settings_raw"][item["key"]]
    if item["status"] != "search_candidate" or type(item["candidate_raw"]) is not int or type(raw) is not int:
      _fail("request_candidate_not_available")
    self._recheck(capture)
    now = self.clock_ns()
    dependencies = {key: value for key, value in capture["settings_raw"].items() if key != item["key"]}
    for key, value in capture["evidence"]["epoch"]["dependencies"].items():
      if key in KEYS and key != item["key"] and (value is None or type(value) is int):
        dependencies[key] = value
    provenance = self._provenance(capture)
    pointer_token = _digest({"pointer": capture["pointer_state"], "artifact": capture["result_hash"],
                            "generation": capture["current"]["generation"], "dependency_digest": capture["dependency_digest"]})
    return {"target_id": body["target_id"], "scope": capture["scope"], "session_id": capture["current"]["session_id"],
      "session_generation": capture["current"]["generation"], "artifact_sha256": capture["result_hash"],
      "result_revision": capture["result_revision"], "item_key": item["key"], "current_raw": raw,
      "candidate_raw": item["candidate_raw"], "settings_revision": capture["current"]["settings_revision"],
      "dependencies": dependencies, "dependency_digest": capture["dependency_digest"], "pointer_token": pointer_token,
      "resolved_at_ns": now, "expires_at_ns": now + self.request_ttl_ns, "provenance": provenance}

  __call__ = resolve


def make_comparison_provider(resolver, profile, *, source_kind="recorded"):
  """Server-only source declaration; unchanged immutable pairs decode once."""
  resolver.bind_comparison_profile(profile)
  cache = {}
  def captured_identity(after):
    value = {key: after[key] for key in ("target_id", "session_id", "session_generation", "settings_revision", "artifact_sha256", "result_revision")}
    value["provenance"] = {key: after["provenance"][key] for key in ("evidence_sha256", "profile_sha256", "code_hashes", "evidence_reference", "route", "boot_id")}
    return value
  def provider(application, token=None):
    try:
      from .comparison import evaluate_comparison
      frozen = application["baseline"].get("comparison_profile")
      if type(frozen) is not dict or _digest(frozen) != application["baseline"].get("comparison_profile_sha256"):
        _fail("comparison_request_profile_unbound")
      if application.get("execution_state") not in {"stored", "consumer_observed", "comparison_ready"}:
        _fail("application_not_executed_pc")
      after = resolver.capture_evidence(application["target"]["target_id"], application["key"])
      after_identity = captured_identity(after)
      immutable_application = {key: value for key, value in application.items() if key != "execution_state"}
      identity = _digest({"application": immutable_application, "after": after_identity, "profile": frozen, "source_kind": source_kind})
      if identity in cache:
        result = deepcopy(cache[identity])
        if result["receipt"] is not None:
          # Review itself advances this one allowed lifecycle field. The native
          # pair and all other request evidence remain immutable, while core
          # requires each receipt to bind the application it is reviewing now.
          result["receipt"]["application_sha256"] = _digest(application)
        return result
      cohort = resolver.comparison_cohort(application, after, frozen, source_kind=source_kind)
      result = evaluate_comparison(application, cohort, frozen)
      final = resolver.capture_evidence(application["target"]["target_id"], application["key"])
      if captured_identity(final) != after_identity:
        _fail("comparison_current_context_changed")
      if len(cache) >= 2:
        cache.pop(next(iter(cache)))
      cache[identity] = deepcopy(result)
      return result
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ImportError) as exc:
      from .comparison import evaluate_comparison
      reason = str(exc) if re.fullmatch(r"[a-z0-9_]{1,128}", str(exc)) else "comparison_evidence_unavailable"
      held = evaluate_comparison(application, {}, profile)
      held.update(state="held", reason_codes=[reason], receipt=None)
      held["comparison"].update(state="held", reason_codes=[reason])
      return held
  return provider
