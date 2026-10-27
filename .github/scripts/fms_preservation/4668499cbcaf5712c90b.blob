"""Owner-operated file transfer for the existing analyzer; no network daemon.

Use the existing authenticated terminal/file connection to move directories.
Only the device publisher can seal a result or authorize a parameter write.
"""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import shutil
import uuid

from .candidate_report import _read, _code_hashes
from .evidence import hash_file

MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 4096


def _root(value):
  path = Path(value)
  if not path.is_absolute() or any(part.is_symlink() or getattr(part, "is_junction", lambda: False)() for part in (path, *path.parents)):
    raise ValueError("remote_path_invalid")
  return path


def _relative(value):
  if (type(value) is not str or not value or "\\" in value or ":" in value
      or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/"))):
    raise ValueError("remote_member_invalid")
  return value


def _inventory(root, *, exclude=()):
  files, total = [], 0
  for path in sorted(root.rglob("*")):
    _root(path)
    if path.is_file() and path.relative_to(root).as_posix() not in exclude:
      size = path.stat().st_size
      total += size
      if len(files) >= MAX_FILES or total > MAX_BYTES:
        raise ValueError("remote_transfer_limit")
      digest, count = hash_file(path, MAX_BYTES)
      files.append({"path": path.relative_to(root).as_posix(), "bytes": count, "sha256": digest})
  return files


def _manifest(root, kind, ticket, **extra):
  from .periodic import _atomic
  value = {"schema_version": 1, "kind": kind, "ticket": ticket,
           "files": _inventory(root, exclude=("remote-manifest.json",)), **extra}
  _atomic(root / "remote-manifest.json", value, durable=True)
  return value


def _verify(root, kind):
  value, digest = _read(root / "remote-manifest.json", 4 * 1024 * 1024)
  if value.get("schema_version") != 1 or value.get("kind") != kind or type(value.get("files")) is not list:
    raise ValueError("remote_manifest_invalid")
  names, total = set(), 0
  for item in value["files"]:
    if type(item) is not dict or set(item) != {"path", "bytes", "sha256"}:
      raise ValueError("remote_manifest_invalid")
    name = _relative(item["path"])
    if name.casefold() in names or type(item["bytes"]) is not int or item["bytes"] < 0:
      raise ValueError("remote_member_invalid")
    names.add(name.casefold())
    total += item["bytes"]
    if len(names) > MAX_FILES or total > MAX_BYTES:
      raise ValueError("remote_transfer_limit")
    path = _root(root / name)
    if not path.is_file() or hash_file(path, MAX_BYTES) != (item["sha256"], item["bytes"]):
      raise ValueError("remote_member_changed")
  actual = set()
  for index, path in enumerate(root.rglob("*")):
    _root(path)  # Empty directory links must not escape a later copy either.
    if index >= MAX_FILES * 8:
      raise ValueError("remote_transfer_limit")
    if path.is_file() and path.relative_to(root).as_posix() != "remote-manifest.json":
      actual.add(path.relative_to(root).as_posix().casefold())
  if actual != names:
    raise ValueError("remote_member_inventory_changed")
  return value, digest


def _disjoint(first, second):
  if first == second or first.is_relative_to(second) or second.is_relative_to(first):
    raise ValueError("remote_output_overlap")


def _copy_packet(source, target, manifest):
  """Copy only declared bytes; never traverse a mutable incoming directory."""
  target.mkdir()
  for item in manifest["files"]:
    destination = _root(target / _relative(item["path"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest, count = hashlib.sha256(), 0
    with _root(source / item["path"]).open("rb") as incoming, destination.open("xb") as outgoing:
      while chunk := incoming.read(min(1024 * 1024, item["bytes"] - count + 1)):
        count += len(chunk)
        if count > item["bytes"]:
          raise ValueError("remote_member_changed")
        outgoing.write(chunk)
        digest.update(chunk)
    if (digest.hexdigest(), count) != (item["sha256"], item["bytes"]):
      raise ValueError("remote_member_changed")
  shutil.copyfile(_root(source / "remote-manifest.json"), target / "remote-manifest.json")


def _ticket(job):
  ticket, _ = _read(job / "remote-ticket.json", 64 * 1024)
  request, digest = _read(job / "request.json", 64 * 1024)
  execution = request["identity"].get("analysis_execution")
  from .periodic import _hash
  if (set(ticket) != {"schema_version", "kind", "job_id", "executor_revision", "request_sha256", "nonce", "resume"}
      or type(ticket.get("resume")) is not bool or type(ticket.get("nonce")) is not str
      or re.fullmatch(r"[a-f0-9]{32}", ticket["nonce"]) is None
      or (job / "remote-cancelled.json").exists() or ticket.get("schema_version") != 1
      or ticket.get("kind") != "remote_analysis" or ticket.get("request_sha256") != digest
      or ticket.get("job_id") != request.get("job_id") or request["job_id"] != _hash(request["identity"]) or execution != {
        "executor": "remote_pc", "revision": ticket.get("executor_revision")}):
    raise ValueError("remote_ticket_unavailable")
  return ticket, request


def export_input(job_directory, destination):
  """Run on comma only when requested; copy the frozen closure, no decoding."""
  from .journal_snapshot import create_journal_snapshot, load_journal_snapshot, _archive_summary, committed_archives
  from .periodic import _atomic
  job, target = _root(job_directory), _root(destination)
  _disjoint(job, target)
  ticket, request = _ticket(job)
  session = _root(request["session_directory"])
  _disjoint(session, target)
  if target.exists():
    value, _ = _verify(target, "remote_analysis_input")
    if value["ticket"] == ticket:
      return value
    raise ValueError("remote_export_exists")
  snapshot = _root(job / "snapshot")
  if not snapshot.exists():
    intent = request["analysis_intent"]
    try:
      sealed = create_journal_snapshot(session, snapshot, request["identity"]["checkpoint"],
        analysis_range={"start_ns": intent["range_start_ns"], "end_ns": intent["requested_end_ns"]})
    except ValueError as error:
      if str(error) != "snapshot_no_committed_archive":
        raise
      if snapshot.exists():
        quarantine = job / ("snapshot-uncommitted-" + uuid.uuid4().hex)
        if not snapshot.resolve().is_relative_to(job.resolve()) or not quarantine.resolve().is_relative_to(job.resolve()):
          raise ValueError("remote_snapshot_path_invalid")
        snapshot.rename(quarantine)
      _atomic(job / "remote-input-waiting.json", {"ticket": ticket, "reason": "snapshot_no_committed_archive"}, durable=True)
      return {"state": "waiting_for_closed_archive", "ticket": ticket, "files": []}
  else:
    loaded = load_journal_snapshot(session, snapshot)
    if loaded["marker"]["schema_version"] == 3:
      from .trip_history import trip_archive_summary
      sealed = trip_archive_summary(session, snapshot, request["identity"]["checkpoint"], loaded["history"])
    else:
      sealed = _archive_summary(session, snapshot, request["identity"]["checkpoint"], loaded["history"], committed_archives(loaded["history"]))
  loaded = load_journal_snapshot(session, snapshot)
  target.mkdir(parents=True)
  shutil.copytree(snapshot, target / "session")
  # Native inspection binds each archive to its journal-referenced diagnostic.
  # Copy that frozen closure only, never unrelated/newer session diagnostics.
  references = loaded["history"]["diagnostic_references"]
  if len(references) > MAX_FILES:
    raise ValueError("remote_transfer_limit")
  for diagnostic in references.values():
    revision, digest = diagnostic.get("revision"), diagnostic.get("sha256")
    if (type(revision) is not int or not 1 <= revision <= 2**63 - 1
        or type(digest) is not str or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
      raise ValueError("remote_diagnostic_reference_invalid")
    relative = Path("diagnostics") / f"{revision}-{digest[:16]}.json"
    source = _root(session / relative)
    before = hash_file(source, 256 * 1024)
    if before[0] != digest:
      raise ValueError("remote_diagnostic_changed")
    copied = target / "session" / relative
    copied.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, copied)
    if hash_file(source, 256 * 1024) != before or hash_file(copied, 256 * 1024) != before:
      raise ValueError("remote_diagnostic_changed")
  for item in sealed["archive_sources"]:
    source = _root(item["path"])
    relative = source.relative_to(session)
    parent = target / "session" / relative.parent
    parent.mkdir(parents=True, exist_ok=True)
    for name in ("archive.json", source.name):
      shutil.copyfile(_root(source.parent / name), parent / name)
  for key, name in (("profile_path", "profile.json"), ("objectives_path", "objectives.json")):
    shutil.copyfile(_root(request[key]), target / name)
  portable = {**request, "session_directory": "session", "profile_path": "profile.json", "objectives_path": "objectives.json"}
  _atomic(target / "request.json", portable, durable=True)
  value = _manifest(target, "remote_analysis_input", ticket)
  # The device retains the exact transfer binding; a PC cannot choose a new one.
  _atomic(job / "remote-input.json", {"manifest_sha256": hash_file(target / "remote-manifest.json", 4 * 1024 * 1024)[0],
      "ticket": ticket}, durable=True)
  return value


def run_input(input_directory, destination):
  """Run in a separate PC CLI process using the same whole-trip engine."""
  from .periodic import _atomic, _child, _verify_job
  source, target = _root(input_directory), _root(destination)
  _disjoint(source, target)
  manifest, input_hash = _verify(source, "remote_analysis_input")
  request, _ = _read(source / "request.json", 64 * 1024)
  if request["identity"]["code_hashes"] != _code_hashes():
    raise ValueError("remote_analyzer_code_mismatch")
  if request["job_id"] != manifest["ticket"]["job_id"]:
    raise ValueError("remote_job_binding_changed")
  target.mkdir(parents=True, exist_ok=False)
  work = target / "work"
  work.mkdir()
  # Preserve the device snapshot bytes, including its durability declaration.
  shutil.copytree(source / "session", work / "snapshot", ignore=shutil.ignore_patterns("archive"))
  local = {**request, "session_directory": str(source / "session"),
           "profile_path": str(source / "profile.json"), "objectives_path": str(source / "objectives.json")}
  _atomic(work / "request.json", local, durable=True)
  for _ in range(128):
    if _child(work / "request.json", detached=True) != 0:
      raise ValueError("remote_analysis_failed")
    result, _, _ = _verify_job(work, request["job_id"])
    if result["state"] != "waiting":
      break
    progress, _ = _read(work / "trip-progress.json", 64 * 1024)
    if progress.get("reason") == "waiting_for_closed_archive":
      break
  else:
    raise ValueError("remote_continuation_limit")
  package = target / "result"
  package.mkdir()
  for name in ("job-result.json", "trip-progress.json"):
    if (work / name).is_file():
      shutil.copyfile(work / name, package / name)
  if (work / "window-0000").exists():
    shutil.copytree(work / "window-0000", package / "window-0000")
  return _manifest(package, "remote_analysis_result", manifest["ticket"], input_manifest_sha256=input_hash,
                   executor="owner_pc_existing_python_analyzer")


def publish_result(job_directory, result_directory):
  """On comma, validate in staging, then let the live parent seal/publish."""
  from .periodic import _atomic, _verify_job
  job, source = _root(job_directory), _root(result_directory)
  _disjoint(job, source)
  ticket, request = _ticket(job)
  value, digest = _verify(source, "remote_analysis_result")
  binding, _ = _read(job / "remote-input.json", 64 * 1024)
  if value["ticket"] != ticket or binding["ticket"] != ticket or value.get("input_manifest_sha256") != binding["manifest_sha256"]:
    raise ValueError("remote_result_binding_changed")
  if (job / "remote-complete.json").exists():
    previous, _ = _read(job / "remote-complete.json", 64 * 1024)
    if previous.get("result_manifest_sha256") == digest:
      return {"state": "already_received", "job_id": request["job_id"]}
    raise ValueError("remote_result_already_received")
  for item in value["files"]:
    path = item["path"]
    if not (path in {"job-result.json", "trip-progress.json"} or path.startswith("window-0000/") and path.endswith(".json")):
      raise ValueError("remote_result_member_not_allowed")
  stage = job / ("remote-staging-" + uuid.uuid4().hex)
  _copy_packet(source, stage, value)
  if _verify(stage, "remote_analysis_result")[1] != digest:
    raise ValueError("remote_staging_changed")
  result, result_hash, loaded = _verify_job(stage, request["job_id"])
  if loaded is not None:
    from .request_evidence import _bundle
    captured = _bundle(stage / "window-0000")
    if captured["code_hashes"] != request["identity"]["code_hashes"] or captured["profile_hash"] != request["identity"]["profile_sha256"]:
      raise ValueError("remote_result_analyzer_binding_changed")
  for name in ("job-result.json", "trip-progress.json", "window-0000"):
    path = stage / name
    if path.exists():
      if (job / name).exists():
        if path.is_dir() and (job / name).is_dir():
          same = _inventory(path) == _inventory(job / name)
        else:
          same = path.is_file() and (job / name).is_file() and hash_file(path, MAX_BYTES) == hash_file(job / name, MAX_BYTES)
        if not same:
          raise ValueError("remote_result_destination_exists")
      else:
        if not path.resolve().is_relative_to(job.resolve()) or not (job / name).resolve().is_relative_to(job.resolve()):
          raise ValueError("remote_result_destination_invalid")
        os.replace(path, job / name)
  _atomic(job / "remote-complete.json", {"schema_version": 1, "ticket": ticket,
      "input_manifest_sha256": value["input_manifest_sha256"], "result_manifest_sha256": digest,
      "job_result_sha256": result_hash}, durable=True)
  return {"state": "received_waiting_for_device_seal", "job_id": request["job_id"]}


def verify_completion(job):
  ticket, _ = _ticket(job)
  complete, _ = _read(job / "remote-complete.json", 64 * 1024)
  binding, _ = _read(job / "remote-input.json", 64 * 1024)
  if (complete.get("ticket") != ticket or binding.get("ticket") != ticket
      or complete.get("input_manifest_sha256") != binding.get("manifest_sha256")
      or hash_file(job / "job-result.json", 4 * 1024 * 1024)[0] != complete.get("job_result_sha256")):
    raise ValueError("remote_completion_changed")
  return deepcopy(complete)
