"""A collection of immutable journal segments, with bounded retained metadata."""
import hashlib
from pathlib import Path

from .evidence import _sync_directory
from .settings_history import _canonical, _segment_inventory, load_collection_history, validate_checkpoint

_HISTORY_CACHE = None


def byte_inventory(source, history):
  """Verify archived bytes and declared coverage; decoding belongs to each work unit."""
  from .journal_snapshot import _read, _object, committed_archives
  from .evidence import hash_file
  archives = committed_archives(history)
  sources, expected, items = [], [], []
  for identity, receipt in archives.items():
    row = {"archive_id": identity, **{key: receipt.get(key) for key in ("sha256", "bytes", "route", "segment")},
      "start_ns": None, "end_ns": None, "range_kind": "unknown", "coverage_verified": False,
      "state": "unavailable", "reason": "trip_archive_unavailable"}
    try:
      marker_path = source / "archive" / identity / "archive.json"
      data = _read(marker_path, 64 * 1024)
      if hashlib.sha256(data).hexdigest() != receipt["manifest_sha256"]:
        raise ValueError("trip_archive_marker_changed")
      marker = _object(data)
      name = marker["file"]["path"]
      if name not in {"rlog", "rlog.bz2", "rlog.zst"}:
        raise ValueError("trip_archive_payload_path_invalid")
      coverage = marker["coverage"]["services"]["carState"]
      start, end = coverage["mono_min_ns"], coverage["mono_max_ns"]
      if type(start) is not int or type(end) is not int or not 0 <= start <= end:
        raise ValueError("trip_archive_declared_range_invalid")
      row.update(start_ns=start, end_ns=end, range_kind="receipt_declared")
      payload = marker_path.parent / name
      if hash_file(payload, receipt["bytes"]) != (receipt["sha256"], receipt["bytes"]):
        raise ValueError("trip_archive_bytes_changed")
      sources.append({"path": str(payload.resolve()), "archive_id": identity,
        **{key: receipt[key] for key in ("sha256", "bytes", "route", "segment")}, "start_ns": start, "end_ns": end})
      row.update(state="available", reason=None)
      items.append({"archive_id": identity, "state": "verified", "manifest_sha256": receipt["manifest_sha256"]})
    except (OSError, ValueError, KeyError, TypeError):
      items.append({"archive_id": identity, "state": "unavailable", "reason": row["reason"]})
    expected.append(row)
  route = next((entry["record"]["route_observation"] for entry in reversed(history["records"])
                if entry["record"]["type"] == "settings_observation"), None)
  return {"archive_sources": sources, "expected_archive_sources": expected, "route_observation": route,
    "archives": {"items": items}, "history": history, "state": "verified" if len(sources) == len(archives) else "partial"}


def trip_archive_summary(source, target, token, history):
  return {"snapshot_directory": str(target.resolve()), "session_id": token["session_id"], "sequence": token["sequence"],
    "chain_sha256": token["chain_sha256"], "bytes": token["bytes"], "snapshot_prefix_complete": True,
    "source_session_closed": history["closed"], **{key: value for key, value in byte_inventory(source, history).items()
      if key in {"archive_sources", "expected_archive_sources", "route_observation"}}}


def _files(root, token):
  from .journal_snapshot import _prefix_digest
  catalog = token["catalog"]
  files = [{"path": catalog["path"], "bytes": catalog["bytes"],
            "sha256": _prefix_digest(root / catalog["path"], catalog["bytes"]), "copy_mode": "prefix"}]
  for item in _segment_inventory(root, token):
    files.append({"path": item["path"], "bytes": item["bytes"], "sha256": item["content_sha256"], "copy_mode": "prefix"})
  if len(files) > 129 or sum(item["bytes"] for item in files) > 512 * 1024 * 1024:
    raise ValueError("trip_history_collection_budget")
  return files


def create_trip_snapshot(session_directory, destination, checkpoint, analysis_range):
  from .journal_snapshot import _read, _write, _object, _prefix_digest, _pin_pid_chunks, committed_archives, _archive_summary
  source, target = Path(session_directory), Path(destination)
  token = validate_checkpoint(checkpoint)
  if token["schema_version"] != 2 or source.resolve() == target.resolve() or source.resolve() in target.resolve().parents or target.resolve() in source.resolve().parents:
    raise ValueError("trip_snapshot_source_destination_invalid")
  if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (target.parent, *target.parent.parents)):
    raise ValueError("snapshot_directory_link")
  if (type(analysis_range) is not dict or set(analysis_range) != {"start_ns", "end_ns"}
      or not 0 <= analysis_range["start_ns"] <= analysis_range["end_ns"] <= token["cutoff_mono_ns"]):
    raise ValueError("invalid_snapshot_range")
  metadata = _read(source / "recording.json", 64 * 1024)
  if _object(metadata).get("session_id") != token["session_id"]:
    raise ValueError("snapshot_session_mismatch")
  files = _files(source, token)
  target.mkdir(exist_ok=False)
  _write(target / "recording.json", metadata)
  for item in files:
    path = target / item["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if _prefix_digest(source / item["path"], item["bytes"], destination=path) != item["sha256"]:
      raise ValueError("snapshot_source_changed")
  history = load_collection_history(target, token, analysis_range)
  archives = committed_archives(history)
  inventory = _pin_pid_chunks(source, target, history, copy_files=True, analysis_range=analysis_range)
  marker = {"schema_version": 3, "kind": "advisor_trip_journal_collection", "session_id": token["session_id"],
    "checkpoint": token, "analysis_range": analysis_range, "history_files": files,
    "history_byte_count": sum(item["bytes"] for item in files), "retained_byte_count": history["retained_byte_count"],
    "retained_record_count": history["retained_record_count"], "recording_sha256": hashlib.sha256(metadata).hexdigest(),
    "source_session_closed": history["closed"], "archive_ids": sorted(archives)}
  if inventory is not None:
    marker["pid_burst_inventory"] = inventory
  _write(target / "snapshot.json", _canonical(marker) + b"\n")
  _sync_directory(target)
  _sync_directory(target.parent)
  return trip_archive_summary(source, target, token, history)


def load_trip_snapshot(source, root, marker, marker_bytes):
  global _HISTORY_CACHE
  from .journal_snapshot import _read, _object, _pin_pid_chunks, _prefix_digest, committed_archives
  required = {"schema_version", "kind", "session_id", "checkpoint", "analysis_range", "history_files", "history_byte_count",
    "retained_byte_count", "retained_record_count", "recording_sha256", "source_session_closed", "archive_ids"}
  if set(marker) not in (required, required | {"pid_burst_inventory"}) or marker["schema_version"] != 3 or marker["kind"] != "advisor_trip_journal_collection":
    raise ValueError("invalid_trip_snapshot_marker")
  token = validate_checkpoint(marker["checkpoint"])
  if token["schema_version"] != 2 or token["session_id"] != marker["session_id"]:
    raise ValueError("trip_snapshot_checkpoint_mismatch")
  files = _files(root, token)
  if files != marker["history_files"] or sum(item["bytes"] for item in files) != marker["history_byte_count"]:
    raise ValueError("trip_snapshot_file_binding_mismatch")
  for item in files:
    if (_prefix_digest(root / item["path"], item["bytes"]) != item["sha256"]
        or _prefix_digest(source / item["path"], item["bytes"]) != item["sha256"]):
      raise ValueError("snapshot_source_changed")
  metadata = _read(root / "recording.json", 64 * 1024)
  if (hashlib.sha256(metadata).hexdigest() != marker["recording_sha256"]
      or _read(source / "recording.json", 64 * 1024) != metadata or _object(metadata).get("session_id") != token["session_id"]):
    raise ValueError("snapshot_recording_mismatch")
  # Prefix hashes above are always rechecked. Only parsing of the same immutable
  # snapshot is reused; the cache holds at most one bounded metadata view.
  cache_key = (str(root.resolve()), hashlib.sha256(marker_bytes).hexdigest())
  if _HISTORY_CACHE is not None and _HISTORY_CACHE[0] == cache_key:
    history = _HISTORY_CACHE[1]
  else:
    history = load_collection_history(root, token, marker["analysis_range"])
    _HISTORY_CACHE = (cache_key, history)
  archives = committed_archives(history)
  if (history["state"] != "complete" or history["closed"] != marker["source_session_closed"]
      or history["retained_byte_count"] != marker["retained_byte_count"] or history["retained_record_count"] != marker["retained_record_count"]
      or sorted(archives) != marker["archive_ids"] or _read(root / "snapshot.json", 64 * 1024) != marker_bytes
      or _pin_pid_chunks(source, root, history, analysis_range=marker["analysis_range"]) != marker.get("pid_burst_inventory")):
    raise ValueError("trip_snapshot_metadata_binding_mismatch")
  return {"marker": marker, "history": history, "archive_receipts": archives, "recording": _object(metadata),
    "recording_bytes": metadata, "history_bytes": None, "history_byte_count": marker["history_byte_count"],
    "retained_history_byte_count": marker["retained_byte_count"], "history_files": files, "selection": None}
