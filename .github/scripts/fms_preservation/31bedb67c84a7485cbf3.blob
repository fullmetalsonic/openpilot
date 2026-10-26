"""Write-once analysis prefixes. Source sessions and archived payloads stay untouched."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .evidence import _regular_file, _sync_directory
from .settings_history import _canonical, _unique, load_history, validate_checkpoint


MAX_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 10_000
WINDOW_MAX_BYTES = 32 * 1024 * 1024
WINDOW_MAX_EVENTS = 40_000
PID_SNAPSHOT_MAX_BYTES = 32 * 1024 * 1024
PID_SNAPSHOT_MAX_FILES = 4096


def _version(info):
  # Windows lstat/fstat disagree on ctime after a writer closes the same file.
  return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read(path, limit, *, prefix=False):
  path = _regular_file(path)
  before = path.lstat()
  fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
  try:
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or before.st_dev != opened.st_dev or before.st_ino != opened.st_ino:
      raise ValueError("session_file_changed")
    if (not prefix and (_version(before) != _version(opened) or opened.st_size > limit)) or (prefix and opened.st_size < limit):
      raise ValueError("session_file_changed_or_oversized")
    chunks, size = [], 0
    while not prefix or size < limit:
      block = os.read(fd, min(64 * 1024, limit - size if prefix else limit - size + 1))
      if not block:
        break
      size += len(block)
      if size > limit:
        raise ValueError("session_file_limit")
      chunks.append(block)
    after = os.fstat(fd)
    if (prefix and size != limit) or (not prefix and _version(opened) != _version(after)):
      raise ValueError("session_file_changed")
  finally:
    os.close(fd)
  final = _regular_file(path).lstat()
  if (prefix and (before.st_dev != final.st_dev or before.st_ino != final.st_ino or final.st_size < limit)) or (
      not prefix and _version(before) != _version(final)):
    raise ValueError("session_file_replaced")
  return b"".join(chunks)


def _object(data):
  value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_session_metadata")))
  if not isinstance(value, dict):
    raise ValueError("invalid_session_metadata")
  return value


def _limits(max_bytes, max_events):
  if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES or type(max_events) is not int or not 0 < max_events <= MAX_EVENTS:
    raise ValueError("invalid_snapshot_budget")


def committed_archives(history):
  """Only completion receipts inside this exact verified journal prefix count."""
  result = {}
  for entry in history["records"]:
    if entry["record"]["type"] != "archive_preserved":
      continue
    record = entry["record"].get("archive")
    if not isinstance(record, dict):
      raise ValueError("snapshot_archive_receipt_invalid")
    identity = record.get("archive_id")
    if (not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity)
        or record.get("state") != "complete" or record.get("session_id") != history["session_id"]
        or record.get("manifest_relative") != f"{identity}/archive.json"
        or any(not isinstance(record.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", record[key])
               for key in ("manifest_sha256", "sha256"))
        or type(record.get("bytes")) is not int or record["bytes"] <= 0):
      raise ValueError("snapshot_archive_receipt_invalid")
    reference = record.get("settings_history_reference")
    if not isinstance(reference, dict):
      raise ValueError("snapshot_archive_receipt_unbound")
    expected = history["references"].get(str(reference["sequence"]))
    diagnostic = history["diagnostic_references"].get(str(reference["sequence"]))
    if ((reference.get("schema_version") != 2 and reference.get("path") != "settings.jsonl") or reference.get("durable") is not True or expected is None or diagnostic is None
        or any(reference[key] != expected[key] for key in ("sha256", "bytes"))
        or diagnostic["invalidated"] is not False
        or any(record.get(left) != diagnostic[right] for left, right in (
          ("diagnostic_revision", "revision"), ("settings_revision", "settings_revision"),
          ("window_start_ns", "window_start_ns"), ("window_end_ns", "window_end_ns")))
        or diagnostic["route_observation"]["state"] != "observed" or record.get("route") != diagnostic["route_observation"]["route"]):
      raise ValueError("snapshot_archive_receipt_unbound")
    if reference.get("schema_version") == 2:
      from .journal_segments import validate_reference
      if validate_reference(reference) != expected:
        raise ValueError("snapshot_archive_receipt_unbound")
    elif set(reference) != {"path", "sequence", "sha256", "bytes", "durable"}:
      raise ValueError("snapshot_archive_receipt_unbound")
    if identity in result and result[identity] != record:
      raise ValueError("snapshot_archive_receipt_conflict")
    result[identity] = record
  if not result:
    raise ValueError("snapshot_no_committed_archive")
  return result


def _write(path, data):
  with path.open("xb") as stream:
    if stream.write(data) != len(data):
      raise OSError("snapshot_short_write")
    stream.flush()
    os.fsync(stream.fileno())


def _prefix_digest(path, size, *, destination=None, exact=False):
  """Copy/hash one immutable prefix with bounded memory, allowing source append."""
  path = _regular_file(path)
  before = path.lstat()
  if type(size) is not int or size < 0 or before.st_size < size or (exact and before.st_size != size):
    raise ValueError("snapshot_prefix_size")
  digest, remaining = hashlib.sha256(), size
  with path.open("rb") as stream:
    opened = os.fstat(stream.fileno())
    if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
      raise ValueError("snapshot_prefix_replaced")
    output = destination.open("xb") if destination is not None else None
    try:
      while remaining:
        data = stream.read(min(64 * 1024, remaining))
        if not data:
          raise ValueError("snapshot_prefix_truncated")
        digest.update(data)
        if output is not None and output.write(data) != len(data):
          raise OSError("snapshot_short_write")
        remaining -= len(data)
      if output is not None:
        output.flush()
        os.fsync(output.fileno())
    finally:
      if output is not None:
        output.close()
    final = _regular_file(path).stat()
    if (before.st_dev, before.st_ino) != (final.st_dev, final.st_ino) or final.st_size < size:
      raise ValueError("snapshot_prefix_replaced")
    if exact and _version(before) != _version(final):
      raise ValueError("snapshot_prefix_changed")
  return digest.hexdigest()


def _selected_files(root, token, selection):
  from .journal_segments import iter_verified_catalog
  wanted = set(selection["segment_indices"])
  catalog = token["catalog"]
  result = [{"path": catalog["path"], "bytes": catalog["bytes"],
             "sha256": _prefix_digest(root / catalog["path"], catalog["bytes"]), "copy_mode": "prefix"}]
  for item in iter_verified_catalog(root, token):
    if item["index"] in wanted:
      result.append({"path": item["path"], "bytes": item["bytes"], "sha256": item["content_sha256"], "copy_mode": "prefix"})
      wanted.remove(item["index"])
  active = token["active"]
  if active is not None and active["index"] in wanted:
    result.append({"path": active["path"], "bytes": active["bytes"], "sha256": active["content_sha256"], "copy_mode": "prefix"})
    wanted.remove(active["index"])
  if wanted or sum(item["bytes"] for item in result) > WINDOW_MAX_BYTES:
    raise ValueError("snapshot_selection_budget_or_missing_segment")
  return result


def select_pid_references(references, analysis_range):
  """Choose complete bursts by time only, one inside each frozen third."""
  if analysis_range is None:
    return references
  start, end = analysis_range["start_ns"], analysis_range["end_ns"]
  if type(start) is not int or type(end) is not int or not 0 <= start <= end:
    raise ValueError("invalid_pid_selection_range")
  groups = {}
  for reference in references:
    groups.setdefault(reference["burst_id"], []).append(reference)
  candidates = []
  for burst_id, parts in groups.items():
    parts.sort(key=lambda row: row["chunk_sequence"])
    if ([row["chunk_sequence"] for row in parts] != list(range(len(parts))) or not parts[-1]["terminal"]
        or any(row["terminal"] for row in parts[:-1]) or len({row["dependency_digest"] for row in parts}) != 1):
      continue
    lo, hi = min(row["mono_start_ns"] for row in parts), max(row["mono_end_ns"] for row in parts)
    if max(row["frame_end"] for row in parts) - min(row["frame_start"] for row in parts) + 1 != 1000:
      continue
    candidates.append((lo, hi, burst_id, parts))
  cuts = [start, start + (end - start) // 3, start + 2 * (end - start) // 3, end]
  chosen = []
  for lo, hi in zip(cuts, cuts[1:]):
    eligible = sorted((row for row in candidates if lo + 2_100_000_000 <= row[0] and row[1] <= hi - 2_100_000_000), key=lambda row: (row[0], row[2]))
    if eligible:
      chosen.extend(eligible[0][3])
  return chosen


def _pin_pid_chunks(source, target, history, *, copy_files=False, analysis_range=None):
  """Pin only payloads named by this verified prefix, with a separate budget."""
  records = [entry["record"] for entry in history["records"] if entry["record"]["type"] == "pid_burst_chunk"]
  if not records:
    return None
  from .pid_burst import MAX_ROUND_BYTES, load_chunk, validate_reference
  references, rounds = {}, {}
  for record in records:
    reference = validate_reference(record["reference"])
    previous = references.get(reference["path"])
    if previous is not None:
      if previous != reference:
        raise ValueError("snapshot_pid_reference_conflict")
      continue
    references[reference["path"]] = reference
    key = reference["path"].split("/")[1], reference["round_id"]
    rounds[key] = rounds.get(key, 0) + reference["bytes"]
    if rounds[key] > MAX_ROUND_BYTES:
      raise ValueError("snapshot_pid_payload_budget")
  all_references = [references[path] for path in sorted(references)]
  ordered = sorted(select_pid_references(all_references, analysis_range), key=lambda row: row["path"])
  if len(ordered) > PID_SNAPSHOT_MAX_FILES or sum(item["bytes"] for item in ordered) > PID_SNAPSHOT_MAX_BYTES:
    raise ValueError("snapshot_pid_payload_budget")
  for reference in ordered:
    load_chunk(source, reference)
    destination = target / reference["path"]
    if copy_files:
      destination.parent.mkdir(parents=True, exist_ok=True)
      if _prefix_digest(source / reference["path"], reference["bytes"], destination=destination, exact=True) != reference["sha256"]:
        raise ValueError("snapshot_pid_source_changed")
    load_chunk(target, reference)
  inventory = {"file_count": len(ordered), "bytes": sum(item["bytes"] for item in ordered),
               "sha256": hashlib.sha256(_canonical(ordered)).hexdigest()}
  if analysis_range is not None:
    inventory.update(selection_policy="complete_burst_each_frozen_time_third_v1", selection_range=analysis_range,
      burst_ids=sorted({row["burst_id"] for row in ordered}), original_reference_count=len(all_references),
      original_reference_sha256=hashlib.sha256(_canonical(all_references)).hexdigest())
  return inventory


def checkpoint_reference(root, checkpoint):
  """Original final row reference, not a synthetic row or a rewritten journal."""
  token = validate_checkpoint(checkpoint)
  if token["schema_version"] == 1:
    return {"path": "settings.jsonl", "sequence": token["sequence"], "sha256": token["chain_sha256"],
            "bytes": token["bytes"], "durable": True}
  from .journal_segments import iter_verified_catalog, validate_reference
  active = token["active"]
  if active is None or not active["bytes"]:
    active = None
    for entry in iter_verified_catalog(root, token):
      active = entry
  if active is None:
    raise ValueError("snapshot_checkpoint_empty")
  return validate_reference({"schema_version": 2, "session_id": token["session_id"], "segment_index": active["index"],
    "path": active["path"], "sequence": token["sequence"], "sha256": token["chain_sha256"],
    "bytes": active["bytes"], "total_bytes": token["bytes"], "durable": True})


def _create_segmented_snapshot(session_directory, destination, token, *, window_seconds, analysis_range=None):
  from .settings_history import select_history_window
  if analysis_range is None and (type(window_seconds) is not int or window_seconds not in (180, 300)):
    raise ValueError("invalid_snapshot_window")
  source, target = Path(session_directory), Path(destination)
  if source.resolve() == target.resolve() or source.resolve() in target.resolve().parents or target.resolve() in source.resolve().parents:
    raise ValueError("snapshot_source_destination_overlap")
  if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (target.parent, *target.parent.parents)):
    raise ValueError("snapshot_directory_link")
  metadata = _read(source / "recording.json", 64 * 1024)
  if _object(metadata).get("session_id") != token["session_id"]:
    raise ValueError("snapshot_session_mismatch")
  # Include the closing log segment and intervention/recovery margin. This is
  # evidence closure, not an assertion that this extra time is tuning eligible.
  end = token["cutoff_mono_ns"]
  if analysis_range is not None:
    if (type(analysis_range) is not dict or set(analysis_range) != {"start_ns", "end_ns"}
        or any(type(value) is not int or value < 0 for value in analysis_range.values())
        or not analysis_range["start_ns"] <= analysis_range["end_ns"] <= end):
      raise ValueError("invalid_snapshot_range")
    start, end = max(0, analysis_range["start_ns"] - 65_000_000_000), analysis_range["end_ns"]
  else:
    start = max(0, end - (window_seconds + 65) * 1_000_000_000)
  selection = select_history_window(source, token, start, end)
  files = _selected_files(source, token, selection)
  target.mkdir(exist_ok=False)
  (target / "history").mkdir()
  _write(target / "recording.json", metadata)
  for item in files:
    if _prefix_digest(source / item["path"], item["bytes"], destination=target / item["path"]) != item["sha256"]:
      raise ValueError("snapshot_source_changed")
  history = load_history(target, checkpoint=token, selection=selection, include_records=True,
                         max_bytes=WINDOW_MAX_BYTES, max_events=WINDOW_MAX_EVENTS)
  if history["state"] != "complete" or history.get("selected_closure_complete") is not True:
    raise ValueError("snapshot_selection_unverified")
  archives = committed_archives(history)
  pid_inventory = _pin_pid_chunks(source, target, history, copy_files=True, analysis_range=analysis_range)
  marker = {"schema_version": 2, "kind": "advisor_journal_snapshot", "session_id": token["session_id"],
            "checkpoint": token, "selection": selection, "history_files": files,
            "history_byte_count": sum(item["bytes"] for item in files),
            "recording_sha256": hashlib.sha256(metadata).hexdigest(), "selected_closure_complete": True,
            "source_session_closed": history["closed"], "archive_ids": sorted(archives),
            "power_loss_durability": "file_fsync_only" if os.name == "nt" else "file_and_directory_fsync"}
  if pid_inventory is not None:
    marker["pid_burst_inventory"] = pid_inventory
  _sync_directory(target / "history")
  _write(target / "snapshot.json", _canonical(marker) + b"\n")
  _sync_directory(target)
  _sync_directory(target.parent)
  _load_segmented_snapshot(source, target, marker, _read(target / "snapshot.json", 64 * 1024))
  return _archive_summary(source, target, token, history, archives)


def _load_segmented_snapshot(source, root, marker, marker_bytes):
  required = {"schema_version", "kind", "session_id", "checkpoint", "selection", "history_files", "history_byte_count",
              "recording_sha256", "selected_closure_complete", "source_session_closed", "archive_ids", "power_loss_durability"}
  if (set(marker) not in (required, required | {"pid_burst_inventory"}) or type(marker["schema_version"]) is not int or marker["schema_version"] != 2
      or marker["kind"] != "advisor_journal_snapshot" or marker["selected_closure_complete"] is not True
      or type(marker["source_session_closed"]) is not bool
      or marker["power_loss_durability"] not in {"file_fsync_only", "file_and_directory_fsync"}):
    raise ValueError("invalid_snapshot_marker")
  token = validate_checkpoint(marker["checkpoint"])
  if token["schema_version"] != 2 or token["session_id"] != marker["session_id"]:
    raise ValueError("snapshot_session_mismatch")
  history = load_history(root, checkpoint=token, selection=marker["selection"], include_records=True,
                         max_bytes=WINDOW_MAX_BYTES, max_events=WINDOW_MAX_EVENTS)
  files = _selected_files(root, token, marker["selection"])
  metadata = _read(root / "recording.json", 64 * 1024)
  if (history["state"] != "complete" or history.get("selected_closure_complete") is not True
      or files != marker["history_files"] or sum(item["bytes"] for item in files) != marker["history_byte_count"]
      or hashlib.sha256(metadata).hexdigest() != marker["recording_sha256"]
      or _object(metadata).get("session_id") != token["session_id"] or history["closed"] != marker["source_session_closed"]):
    raise ValueError("snapshot_selection_mismatch")
  for item in files:
    if _prefix_digest(root / item["path"], item["bytes"], exact=True) != item["sha256"]:
      raise ValueError("snapshot_selection_changed")
    if source.resolve() != root.resolve() and _prefix_digest(source / item["path"], item["bytes"]) != item["sha256"]:
      raise ValueError("snapshot_source_changed")
  if source.resolve() != root.resolve() and _read(source / "recording.json", 64 * 1024) != metadata:
    raise ValueError("snapshot_source_changed")
  archives = committed_archives(history)
  if _pin_pid_chunks(source, root, history, analysis_range=(marker.get("pid_burst_inventory") or {}).get("selection_range")) != marker.get("pid_burst_inventory"):
    raise ValueError("snapshot_pid_inventory_mismatch")
  if sorted(archives) != marker["archive_ids"] or _read(root / "snapshot.json", 64 * 1024) != marker_bytes:
    raise ValueError("snapshot_archive_inventory_mismatch")
  return {"marker": marker, "history": history, "archive_receipts": archives, "recording": _object(metadata),
          "recording_bytes": metadata, "history_bytes": None, "history_byte_count": marker["history_byte_count"],
          "history_files": files, "selection": marker["selection"]}


def create_journal_snapshot(session_directory, destination, checkpoint, *, max_bytes=MAX_BYTES, max_events=MAX_EVENTS, window_seconds=300, analysis_range=None):
  """Child-process I/O only. A failed snapshot keeps an incomplete new directory."""
  _limits(max_bytes, max_events)
  token = validate_checkpoint(checkpoint)
  if token["schema_version"] == 2:
    if analysis_range is not None:
      from .trip_history import create_trip_snapshot
      return create_trip_snapshot(session_directory, destination, token, analysis_range)
    return _create_segmented_snapshot(session_directory, destination, token, window_seconds=window_seconds, analysis_range=analysis_range)
  if token["bytes"] > max_bytes or token["sequence"] > max_events:
    raise ValueError("snapshot_checkpoint_budget")
  source, target = Path(session_directory), Path(destination)
  if source.resolve() == target.resolve() or source.resolve() in target.resolve().parents or target.resolve() in source.resolve().parents:
    raise ValueError("snapshot_source_destination_overlap")
  if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (target.parent, *target.parent.parents)):
    raise ValueError("snapshot_directory_link")
  metadata = _read(source / "recording.json", 64 * 1024)
  if _object(metadata).get("session_id") != token["session_id"]:
    raise ValueError("snapshot_session_mismatch")
  prefix = _read(source / "settings.jsonl", token["bytes"], prefix=True)
  target.mkdir(exist_ok=False)
  _write(target / "recording.json", metadata)
  _write(target / "settings.jsonl", prefix)
  history = load_history(target / "settings.jsonl", max_bytes=max_bytes, max_events=max_events,
                         include_records=True, checkpoint=token)
  if history["state"] != "complete" or history.get("snapshot_prefix_complete") is not True:
    raise ValueError("snapshot_prefix_unverified")
  archives = committed_archives(history)
  pid_inventory = _pin_pid_chunks(source, target, history, copy_files=True, analysis_range=analysis_range)
  marker = {"schema_version": 1, "kind": "advisor_journal_snapshot", "session_id": token["session_id"], "checkpoint": token,
            "settings_sha256": hashlib.sha256(prefix).hexdigest(), "recording_sha256": hashlib.sha256(metadata).hexdigest(),
            "snapshot_prefix_complete": True, "source_session_closed": history["closed"], "archive_ids": sorted(archives),
            "power_loss_durability": "file_fsync_only" if os.name == "nt" else "file_and_directory_fsync"}
  if pid_inventory is not None:
    marker["pid_burst_inventory"] = pid_inventory
  if _read(source / "settings.jsonl", token["bytes"], prefix=True) != prefix or _read(source / "recording.json", 64 * 1024) != metadata:
    raise ValueError("snapshot_source_changed")
  _sync_directory(target)
  _write(target / "snapshot.json", _canonical(marker) + b"\n")
  _sync_directory(target)
  _sync_directory(target.parent)
  return _archive_summary(source, target, token, history, archives)


def _archive_summary(source, target, token, history, archives):
  from .inspection import inspect_session
  inspection = inspect_session(source, session_snapshot=target)
  archive_sources, expected = [], {}
  for identity, receipt in archives.items():
    start, end = receipt.get("start_mono_ns"), receipt.get("end_mono_ns")
    declared = type(start) is int and type(end) is int and 0 <= start <= end
    expected[identity] = {"archive_id": identity, **{key: receipt.get(key) for key in ("sha256", "bytes", "route", "segment")},
                          "start_ns": start if declared else None, "end_ns": end if declared else None,
                          "range_kind": "receipt_declared" if declared else "unknown", "coverage_verified": False,
                          "state": "unavailable", "reason": "archive_not_inspected"}
  for item in inspection["archives"]["items"]:
    declared = expected[item["archive_id"]]
    if item["state"] != "verified":
      declared["reason"] = item.get("reason") or "archive_verification_failed"
      continue
    receipt = archives[item["archive_id"]]
    marker_path = source / "archive" / item["archive_id"] / "archive.json"
    data = _read(marker_path, 64 * 1024)
    if hashlib.sha256(data).hexdigest() != item["manifest_sha256"]:
      raise ValueError("snapshot_archive_changed")
    manifest = _object(data)
    payload = _regular_file(marker_path.parent / manifest["file"]["path"])
    archive_sources.append({"path": str(payload.resolve()), "archive_id": item["archive_id"],
                            **{key: receipt[key] for key in ("sha256", "bytes", "route", "segment")},
                            "start_ns": manifest["coverage"]["services"]["carState"]["mono_min_ns"],
                            "end_ns": manifest["coverage"]["services"]["carState"]["mono_max_ns"]})
    declared.update(start_ns=archive_sources[-1]["start_ns"], end_ns=archive_sources[-1]["end_ns"],
                    range_kind="verified_car_state", coverage_verified=True, state="available", reason=None)
  route = next((entry["record"]["route_observation"] for entry in reversed(history["records"])
                if entry["record"]["type"] == "settings_observation"), None)
  return {"snapshot_directory": str(target.resolve()), "session_id": token["session_id"], "sequence": token["sequence"],
          "chain_sha256": token["chain_sha256"], "bytes": token["bytes"], "snapshot_prefix_complete": token["schema_version"] == 1,
          "source_session_closed": history["closed"], "committed_archive_count": len(archives),
          "archive_sources": archive_sources, "expected_archive_sources": list(expected.values()), "route_observation": route}


def load_journal_snapshot(session_directory, snapshot_directory, *, max_bytes=MAX_BYTES, max_events=MAX_EVENTS):
  if (type(max_bytes) is not int or not 0 < max_bytes <= WINDOW_MAX_BYTES
      or type(max_events) is not int or not 0 < max_events <= WINDOW_MAX_EVENTS):
    raise ValueError("invalid_snapshot_budget")
  source, root = Path(session_directory), Path(snapshot_directory)
  marker_bytes = _read(root / "snapshot.json", 64 * 1024)
  marker = _object(marker_bytes)
  if marker.get("schema_version") == 3:
    from .trip_history import load_trip_snapshot
    return load_trip_snapshot(source, root, marker, marker_bytes)
  if marker.get("schema_version") == 2:
    return _load_segmented_snapshot(source, root, marker, marker_bytes)
  # A caller supporting both formats cannot enlarge the v1 format's bounds.
  max_bytes, max_events = min(max_bytes, MAX_BYTES), min(max_events, MAX_EVENTS)
  required = {"schema_version", "kind", "session_id", "checkpoint", "settings_sha256", "recording_sha256",
              "snapshot_prefix_complete", "source_session_closed", "archive_ids", "power_loss_durability"}
  if (set(marker) not in (required, required | {"pid_burst_inventory"}) or type(marker["schema_version"]) is not int or marker["schema_version"] != 1
      or marker["kind"] != "advisor_journal_snapshot" or marker["snapshot_prefix_complete"] is not True
      or type(marker["source_session_closed"]) is not bool
      or marker["power_loss_durability"] not in {"file_fsync_only", "file_and_directory_fsync"}):
    raise ValueError("invalid_snapshot_marker")
  token = validate_checkpoint(marker["checkpoint"])
  if token["bytes"] > max_bytes or token["sequence"] > max_events:
    raise ValueError("snapshot_checkpoint_budget")
  prefix = _read(root / "settings.jsonl", token["bytes"])
  metadata = _read(root / "recording.json", 64 * 1024)
  history = load_history(root / "settings.jsonl", max_bytes=max_bytes, max_events=max_events,
                         include_records=True, checkpoint=token)
  if (history["state"] != "complete" or history.get("snapshot_prefix_complete") is not True
      or history["verified_bytes_sha256"] != marker["settings_sha256"]
      or hashlib.sha256(prefix).hexdigest() != marker["settings_sha256"]
      or hashlib.sha256(metadata).hexdigest() != marker["recording_sha256"]
      or _object(metadata).get("session_id") != token["session_id"] or marker["session_id"] != token["session_id"]
      or history["closed"] != marker["source_session_closed"]
      or _read(source / "settings.jsonl", token["bytes"], prefix=True) != prefix
      or _read(source / "recording.json", 64 * 1024) != metadata):
    raise ValueError("snapshot_prefix_mismatch")
  archives = committed_archives(history)
  if _pin_pid_chunks(source, root, history, analysis_range=(marker.get("pid_burst_inventory") or {}).get("selection_range")) != marker.get("pid_burst_inventory"):
    raise ValueError("snapshot_pid_inventory_mismatch")
  if marker["archive_ids"] != sorted(archives) or _read(root / "snapshot.json", 64 * 1024) != marker_bytes:
    raise ValueError("snapshot_archive_inventory_mismatch")
  return {"marker": marker, "history": history, "archive_receipts": archives, "recording": _object(metadata),
          "recording_bytes": metadata, "history_bytes": prefix}


def iter_snapshot_consumer_events(session_directory, snapshot_directory):
  """Stream every immutable consumer event, including unselected time strata."""
  sealed = load_journal_snapshot(session_directory, snapshot_directory)
  if sealed["marker"]["schema_version"] == 3:
    from .journal_segments import iter_verified_records
    from .consumer_observer import validate_event
    for item in iter_verified_records(Path(snapshot_directory), sealed["marker"]["checkpoint"]):
      record = item["envelope"]["record"]
      if record.get("type") == "consumer_observation":
        yield validate_event(record["event"])
  else:
    for item in sealed["history"]["records"]:
      if item["record"]["type"] == "consumer_observation":
        yield item["record"]["event"]
