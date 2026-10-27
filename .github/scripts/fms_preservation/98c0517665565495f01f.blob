"""Owner-operated comparison packets; only the live request actor seals review.

Device preparation/export/import verifies metadata and bytes. Native decoding
and the existing cohort evaluator run only in the explicit PC command.
"""
from copy import deepcopy
import os
import json
from pathlib import Path
import re
import shutil
import uuid

from .candidate_report import _code_hashes, _read
from .evidence import hash_file
from .periodic import _atomic
from .remote_analysis import MAX_BYTES, _root, _relative, _disjoint, _manifest, _verify, _copy_packet, _inventory
from .request_evidence import CandidateEvidenceResolver, _digest

INPUT = "remote_comparison_input"
RESULT = "remote_comparison_result"
_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def _immutable(application):
  if application.get("execution_state") not in {"stored", "consumer_observed", "comparison_ready"}:
    raise ValueError("application_not_executed_pc")
  return {key: value for key, value in application.items() if key != "execution_state"}


def _identity(after):
  return {key: deepcopy(after[key]) for key in ("target_id", "session_id", "session_generation", "settings_revision",
                                              "artifact_sha256", "result_revision", "provenance")}


def _ticket(directory):
  value, _ = _read(_root(directory) / "remote-ticket.json", 256 * 1024)
  fields = {"schema_version", "kind", "nonce", "execution", "application", "after", "current", "code_hashes", "target_config", "source_kind"}
  if (set(value) != fields or type(value["schema_version"]) is not int or value["schema_version"] != 1
      or value["kind"] != "remote_comparison" or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"])
      or value["execution"].get("executor") != "remote_pc" or not re.fullmatch(r"[a-f0-9]{32}", value["execution"].get("revision", ""))
      or "execution_state" in value["application"] or (directory / "remote-cancelled.json").exists()):
    raise ValueError("remote_ticket_unavailable")
  return value


def _report(value, application):
  from .comparison import validate_public_comparison
  if (type(value) is not dict or set(value) != {"state", "reason_codes", "receipt", "comparison"}
      or len(json.dumps(value, allow_nan=False).encode()) > 64 * 1024):
    raise ValueError("invalid_comparison_result")
  validate_public_comparison(value["comparison"])
  if value["state"] != value["comparison"]["state"] or value["reason_codes"] != value["comparison"]["reason_codes"]:
    raise ValueError("comparison_state_mismatch")
  receipt = value["receipt"]
  if receipt is not None:
    profile = application["baseline"]["comparison_profile"]
    expected = {"request_id": application["request_id"], "state": value["state"],
                "request_sha256": application["request_sha256"], "application_sha256": _digest(application),
                "application_receipt_sha256": application["receipt_sha256"], "profile_sha256": _digest(profile),
                "comparison_sha256": _digest(value["comparison"])}
    if (set(receipt) != set(expected) | {"cohort_sha256", "history_reference", "source_hashes", "consumer_observed_ns"}
        or any(receipt.get(key) != field for key, field in expected.items())
        or not re.fullmatch(r"[a-f0-9]{64}", receipt.get("cohort_sha256", ""))):
      raise ValueError("comparison_receipt_changed")
  elif value["state"] in {"consumer_observed", "comparison_ready"}:
    raise ValueError("comparison_receipt_missing")
  return value


def _detail_files(root, report):
  """Validate the real v3 reference codec and return its complete file closure."""
  from .comparison_stream import validate_interval_reference, read_interval_page, PAGE_BYTES
  result = set()
  for name in ("before", "after"):
    ref = (report["comparison"].get(name) or {}).get("exclusions")
    if not isinstance(ref, dict) or ref.get("encoding") != "reason-indexed-pages-v1":
      continue
    validate_interval_reference(ref)
    manifest, digest = _read(_root(root / ref["path"]), PAGE_BYTES)
    if digest != ref["sha256"]:
      raise ValueError("comparison_interval_manifest_changed")
    if ref["page_count"] == 0:
      if manifest != {"schema_version": 1, "kind": "comparison_exclusion_pages", "interval_count": 0, "pages": []}:
        raise ValueError("comparison_interval_manifest_changed")
    for index in range(ref["page_count"]):
      read_interval_page(root, ref, index)
      result.add(str(Path(ref["path"]).parent / f"{index:06d}.json").replace("\\", "/"))
    result.add(ref["path"])
  return result


def _copy_file(source, destination):
  source, destination = _root(source), _root(destination)
  before = hash_file(source, MAX_BYTES)
  if destination.exists():
    if hash_file(destination, MAX_BYTES) != before:
      raise ValueError("remote_closure_conflict")
    return
  destination.parent.mkdir(parents=True, exist_ok=True)
  with source.open("rb") as incoming, destination.open("xb") as outgoing:
    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
  if hash_file(source, MAX_BYTES) != before or hash_file(destination, MAX_BYTES) != before:
    raise ValueError("remote_member_changed")


def _copy_tree(source, destination, *, journal_union=False):
  for item in _inventory(_root(source)):
    relative = item["path"]
    if journal_union and relative == "snapshot.json":
      continue
    target = destination / relative
    if journal_union and target.exists() and hash_file(target, MAX_BYTES) != (item["sha256"], item["bytes"]):
      # Two sealed checkpoints of one journal can pin different immutable
      # prefixes. Keep the longest only after verifying exact prefix equality.
      if relative != "settings.jsonl" and not relative.startswith("history/"):
        raise ValueError("remote_closure_conflict")
      from .journal_snapshot import _prefix_digest
      count = min(target.stat().st_size, item["bytes"])
      if _prefix_digest(target, count) != _prefix_digest(source / relative, count):
        raise ValueError("remote_closure_conflict")
      if target.stat().st_size < item["bytes"]:
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".partial")
        _copy_file(source / relative, temporary)
        os.replace(temporary, target)
    else:
      _copy_file(source / relative, target)


class RemoteComparisonStore:
  def __init__(self, directory, resolver, execution_provider, *, source_kind="recorded"):
    self.root, self.resolver = _root(directory), resolver
    self.execution_provider, self.source_kind = execution_provider, source_kind
    self.root.mkdir(parents=True, exist_ok=True)

  def _binding(self, application):
    immutable = _immutable(application)
    frozen = application["baseline"]["comparison_profile"]
    if _digest(frozen) != application["baseline"]["comparison_profile_sha256"]:
      raise ValueError("comparison_request_profile_unbound")
    execution = self.execution_provider()
    if (not isinstance(execution, dict) or execution.get("executor") != "remote_pc"
        or not re.fullmatch(r"[a-f0-9]{32}", execution.get("revision", ""))):
      raise ValueError("analysis_executor_unconfirmed")
    execution = {key: execution[key] for key in ("executor", "revision")}
    target_id = application["target"]["target_id"]
    config = self.resolver.targets[target_id]
    if config["kind"] != "periodic":
      raise ValueError("comparison_archive_reference_unavailable")
    capture = self.resolver._capture(target_id)
    self.resolver._recheck(capture)
    current = capture["current"]
    if current["settings_raw"].get(application["key"]) != application["candidate_raw"] or application["old_raw"] == application["candidate_raw"]:
      raise ValueError("comparison_current_settings_mismatch")
    if application["source_kind"] == "vehicle_stored" and "analysis_binding" not in current:
      raise ValueError("comparison_analysis_binding_missing")
    after = self.resolver.capture_evidence(target_id, application["key"])
    target = application["target"]
    if (after["session_id"] != target["session_id"] or after["session_generation"] != target["session_generation"]
        or any(after["provenance"].get(key) != target[key] for key in ("route", "boot_id"))):
      raise ValueError("comparison_current_target_changed")
    self.resolver._historical(target_id, application["source_candidate"]["evidence_reference"], application["source_candidate"]["bundle_sha256"])
    self.resolver._recheck(capture)
    return {"execution": execution, "application": immutable, "after": _identity(after),
            "current": {key: value for key, value in current.items() if key not in {"observed_mono_ns", "settings_observation_fresh"}},
            "code_hashes": _code_hashes(), "target_config": {key: str(value) if isinstance(value, Path) else value for key, value in config.items()},
            "source_kind": self.source_kind}

  def prepare(self, application):
    if not isinstance(application.get("request_id"), str) or not _ID.fullmatch(application["request_id"]) or application["request_id"] in {".", ".."}:
      raise ValueError("invalid_request_id")
    binding = self._binding(application)
    directory = self.root / _digest(binding)
    directory.mkdir(exist_ok=True)
    if (directory / "remote-ticket.json").exists():
      ticket = _ticket(directory)
      if {key: ticket[key] for key in binding} != binding:
        raise ValueError("remote_ticket_binding_changed")
    else:
      ticket = {"schema_version": 1, "kind": "remote_comparison", "nonce": uuid.uuid4().hex, **binding}
      _atomic(directory / "remote-ticket.json", ticket, durable=True)
    # Recheck before making a ticket discoverable; no ledger/Params mutation.
    if self._binding(application) != binding:
      raise ValueError("comparison_current_context_changed")
    _atomic(self.root / (application["request_id"] + ".json"), {"ticket_id": directory.name}, durable=True)
    return directory

  def result(self, application):
    directory = self.prepare(application)
    marker = directory / "remote-complete.json"
    if not marker.exists():
      return None
    ticket = _ticket(directory)
    complete, _ = _read(marker, 16 * 1024)
    result = directory / "received"
    manifest, digest = _verify(result, RESULT)
    binding, _ = _read(directory / "remote-input.json", 16 * 1024)
    if (complete != {"result_manifest_sha256": digest} or manifest["ticket"] != ticket
        or manifest.get("input_manifest_sha256") != binding["manifest_sha256"]):
      raise ValueError("remote_completion_changed")
    application_on_pc = {**ticket["application"], "execution_state": "stored"}
    report, _ = _read(result / "comparison-report.json", 64 * 1024)
    _report(report, application_on_pc)
    files = _detail_files(result, report)
    output = _root(self.resolver.targets[application["target"]["target_id"]]["directory"])
    for relative in files:
      _copy_file(result / relative, output / relative)
    final = self._binding(application)
    if final != {key: ticket[key] for key in final}:
      raise ValueError("comparison_current_context_changed")
    if report["receipt"] is not None:
      report["receipt"]["application_sha256"] = _digest(application)
    return report


def comparison_status(directory):
  root = _root(directory)
  if (root / "remote-ticket.json").exists():
    ticket = _ticket(root)
    return {"ticket_id": root.name, "request_id": ticket["application"]["request_id"],
            "state": "result_published" if (root / "remote-complete.json").exists() else "input_exported" if (root / "remote-input.json").exists() else "pending",
            "directory": str(root)}
  items = []
  for pointer in sorted(root.glob("*.json")):
    value, _ = _read(pointer, 4096)
    if set(value) != {"ticket_id"} or not re.fullmatch(r"[a-f0-9]{64}", value["ticket_id"]):
      raise ValueError("remote_status_pointer_invalid")
    items.append(comparison_status(root / value["ticket_id"]))
    if len(items) > 128:
      raise ValueError("remote_status_limit")
  return {"state": "pending" if items else "idle", "items": items}


def export_comparison(ticket_directory, destination):
  from .journal_snapshot import load_journal_snapshot
  source, target = _root(ticket_directory), _root(destination)
  _disjoint(source, target)
  ticket = _ticket(source)
  config = ticket["target_config"]
  periodic, sessions = _root(config["directory"]), _root(config["session_root"])
  _disjoint(periodic, target)
  _disjoint(sessions, target)
  if target.exists():
    manifest, digest = _verify(target, INPUT)
    if manifest["ticket"] != ticket:
      raise ValueError("remote_export_exists")
  else:
    target.mkdir(parents=True)
    _copy_file(source / "remote-ticket.json", target / "remote-ticket.json")
    resolver = CandidateEvidenceResolver({ticket["application"]["target"]["target_id"]: config}, lambda _: None)
    selectors = [(ticket["application"]["source_candidate"]["evidence_reference"], ticket["application"]["source_candidate"]["bundle_sha256"]),
                 (ticket["after"]["provenance"]["evidence_reference"], ticket["after"]["artifact_sha256"])]
    roots = {}
    for reference, artifact in selectors:
      capture = resolver._historical(ticket["application"]["target"]["target_id"], reference, artifact)
      jobname, window = reference["periodic_directory"], reference["window_directory"]
      job = periodic / "jobs" / jobname
      snapshot = job / "snapshot"
      session = capture["session_directory"]
      # Each job snapshot remains byte-identical. A shared session uses only
      # verified compatible journal prefixes and deduplicates shared archives.
      portable_session = target / "sessions" / reference["checkpoint"]["session_id"]
      _copy_tree(snapshot, portable_session, journal_union=True)
      _copy_tree(snapshot, target / "jobs" / jobname / "snapshot")
      _copy_tree(job / window, target / "jobs" / jobname / window)
      for name in ("request.json", "job-result.json"):
        _copy_file(job / name, target / "jobs" / jobname / name)
      request, _ = _read(job / "request.json", 64 * 1024)
      roots[jobname] = {"original": request["session_directory"], "root": "sessions"}
      sealed = load_journal_snapshot(session, snapshot)
      for diagnostic in sealed["history"]["diagnostic_references"].values():
        relative = f"diagnostics/{diagnostic['revision']}-{diagnostic['sha256'][:16]}.json"
        if hash_file(_root(session / relative), 256 * 1024)[0] != diagnostic["sha256"]:
          raise ValueError("remote_diagnostic_changed")
        _copy_file(session / relative, portable_session / relative)
      for receipt in sealed["archive_receipts"].values():
        relative = "archive/" + receipt["archive_id"] + "/archive.json"
        marker, sha = _read(_root(session / relative), 128 * 1024)
        if sha != receipt["manifest_sha256"] or marker["file"]["path"] not in {"rlog", "rlog.zst", "rlog.bz2"}:
          raise ValueError("remote_archive_changed")
        native = Path(relative).parent / marker["file"]["path"]
        if hash_file(_root(session / native), MAX_BYTES) != (receipt["sha256"], receipt["bytes"]):
          raise ValueError("remote_archive_changed")
        _copy_file(session / relative, portable_session / relative)
        _copy_file(session / native, portable_session / native)
    _atomic(target / "portable-roots.json", roots, durable=True)
    manifest = _manifest(target, INPUT, ticket)
    digest = hash_file(target / "remote-manifest.json", 4 * 1024 * 1024)[0]
  _atomic(source / "remote-input.json", {"manifest_sha256": digest}, durable=True)
  return manifest


def run_comparison(input_directory, destination):
  from .comparison import evaluate_comparison
  source, target = _root(input_directory), _root(destination)
  _disjoint(source, target)
  manifest, digest = _verify(source, INPUT)
  ticket = _ticket(source)
  if ticket != manifest["ticket"] or ticket["code_hashes"] != _code_hashes():
    raise ValueError("remote_analyzer_code_mismatch")
  target.mkdir(parents=True, exist_ok=False)
  # Work on a private copy: SQLite caches never modify the input packet.
  work = target / "work"
  _copy_packet(source, work, manifest)
  roots, _ = _read(work / "portable-roots.json", 16 * 1024)
  portable = {}
  for jobname, value in roots.items():
    if not re.fullmatch(r"[a-f0-9]{64}-[a-f0-9]{8}", jobname) or value.get("root") != "sessions" or set(value) != {"root", "original"}:
      raise ValueError("remote_portable_root_invalid")
    portable[jobname] = {"original": value["original"], "root": str(work / value["root"])}
  config = {**ticket["target_config"], "directory": str(work), "session_root": str(work / "sessions")}
  resolver = CandidateEvidenceResolver({ticket["application"]["target"]["target_id"]: config}, lambda _: (_ for _ in ()).throw(ValueError("remote_current_access_forbidden")), portable_sessions=portable)
  application = {**ticket["application"], "execution_state": "stored"}
  profile = application["baseline"]["comparison_profile"]
  cohort = resolver.comparison_cohort(application, ticket["after"], profile, source_kind=ticket["source_kind"])
  report = _report(evaluate_comparison(application, cohort, profile), application)
  package = target / "result"
  package.mkdir()
  _atomic(package / "comparison-report.json", report, durable=True)
  for relative in _detail_files(work, report):
    _copy_file(work / relative, package / relative)
  _verify(source, INPUT)
  return _manifest(package, RESULT, ticket, input_manifest_sha256=digest)


def publish_comparison(ticket_directory, result_directory):
  directory, source = _root(ticket_directory), _root(result_directory)
  _disjoint(directory, source)
  ticket = _ticket(directory)
  manifest, digest = _verify(source, RESULT)
  binding, _ = _read(directory / "remote-input.json", 16 * 1024)
  if manifest["ticket"] != ticket or manifest.get("input_manifest_sha256") != binding["manifest_sha256"]:
    raise ValueError("remote_result_binding_changed")
  application = {**ticket["application"], "execution_state": "stored"}
  report, _ = _read(source / "comparison-report.json", 64 * 1024)
  _report(report, application)
  allowed = {"comparison-report.json"} | _detail_files(source, report)
  if {entry["path"] for entry in manifest["files"]} != allowed:
    raise ValueError("remote_result_member_not_allowed")
  complete = directory / "remote-complete.json"
  if complete.exists():
    previous, _ = _read(complete, 16 * 1024)
    if previous != {"result_manifest_sha256": digest} or _verify(directory / "received", RESULT)[1] != digest:
      raise ValueError("remote_result_already_received")
    return {"state": "already_received"}
  received = directory / "received"
  if received.exists():
    # A process can stop between the immutable directory rename and marker
    # fsync. Complete only that exact verified import on an explicit retry.
    if _verify(received, RESULT)[1] != digest:
      raise ValueError("remote_result_already_received")
  else:
    stage = directory / ("incoming-" + uuid.uuid4().hex)
    _copy_packet(source, stage, manifest)
    if _verify(stage, RESULT)[1] != digest:
      raise ValueError("remote_staging_changed")
    os.rename(stage, received)
  _atomic(complete, {"result_manifest_sha256": digest}, durable=True)
  return {"state": "received_waiting_for_device_review"}
