"""Bounded read-only stored-setting observations and a single-writer sidecar.

Polling records uncertainty, not consumer acknowledgement or an atomic snapshot.
The caller owns the sampling thread; this module never starts a thread or Params.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from openpilot.tools.lib.helpers import RE

from .evidence import _sync_directory
from .registry import load_registry


ZERO_HASH = "0" * 64
MAX_LINE_BYTES = 64 * 1024
KEYS = tuple(item["key"] for item in load_registry()["items"])


def _canonical(value):
  return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _is_link(path):
  return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _identity(info):
  return (info.st_dev, info.st_ino)


def _version(info):
  return (*_identity(info), info.st_size, info.st_mtime_ns)


def _number(raw):
  text = raw.strip()
  if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text):
    return None
  value = float(text)
  if not math.isfinite(value):
    return None
  return int(text) if re.fullmatch(r"[+-]?\d+", text) else value


def _unique(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("duplicate_history_key")
    result[key] = value
  return result


def _valid_diagnostic(record):
  required = {"revision", "window_start_ns", "window_end_ns", "profile_id", "invalidated", "partial_window", "sha256"}
  return (isinstance(record, dict) and set(record) == required
          and all(type(record[key]) is int and record[key] >= 0 for key in ("revision", "window_start_ns", "window_end_ns"))
          and record["window_start_ns"] <= record["window_end_ns"]
          and all(type(record[key]) is bool for key in ("invalidated", "partial_window"))
          and isinstance(record["profile_id"], str) and 1 <= len(record["profile_id"]) <= 128
          and isinstance(record["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is not None)


def _valid_route_observation(observation):
  if not isinstance(observation, dict) or set(observation) != {"route", "begin_ns", "end_ns", "state"}:
    return False
  if (any(type(observation[key]) is not int or observation[key] < 0 for key in ("begin_ns", "end_ns"))
      or observation["begin_ns"] > observation["end_ns"]):
    return False
  route, state = observation["route"], observation["state"]
  return ((state == "observed" and isinstance(route, str) and re.fullmatch(RE.LOG_ID, route) is not None)
          or (state in {"missing", "invalid", "error", "unavailable"} and route is None))


def _valid_job_status(record):
  required = {"diagnostic_revision", "settings_revision", "state", "window_start_ns", "window_end_ns", "route_observation",
              "settings_history_reference", "actual_boundary_gaps", "gap_tolerance_ns", "missing_segments", "missing_segments_count",
              "endpoint_start_covered", "endpoint_end_covered", "completion_scope", "coverage_complete", "interpolation_applied"}
  if not isinstance(record, dict) or set(record) != required:
    return False
  if (any(type(record[key]) is not int or record[key] < 1 for key in ("diagnostic_revision", "settings_revision"))
      or any(type(record[key]) is not int or record[key] < 0 for key in ("window_start_ns", "window_end_ns", "missing_segments_count"))
      or record["window_start_ns"] > record["window_end_ns"] or record["state"] not in {"complete", "incomplete", "timeout", "cancelled"}
      or not _valid_route_observation(record["route_observation"])
      or type(record["gap_tolerance_ns"]) is not int or record["gap_tolerance_ns"] != 250_000_000
      or any(type(record[key]) is not bool for key in ("endpoint_start_covered", "endpoint_end_covered"))
      or record["completion_scope"] != "archive_work_only_host_profile"
      or record["coverage_complete"] is not False or record["interpolation_applied"] is not False
      or not _valid_journal_reference(record["settings_history_reference"])):
    return False
  gaps, missing = record["actual_boundary_gaps"], record["missing_segments"]
  if (not isinstance(gaps, list) or len(gaps) > 128 or not isinstance(missing, list) or len(missing) > 128
      or any(type(value) is not int or value < 0 for value in missing) or record["missing_segments_count"] < len(missing)):
    return False
  if any(not isinstance(gap, dict) or set(gap) != {"start_ns", "end_ns"}
         or any(type(gap[key]) is not int or gap[key] < 0 for key in ("start_ns", "end_ns"))
         or gap["end_ns"] <= gap["start_ns"] for gap in gaps):
    return False
  if record["state"] == "complete" and (record["missing_segments_count"] or not record["endpoint_start_covered"]
                                         or not record["endpoint_end_covered"]
                                         or record["route_observation"]["state"] != "observed"
                                         or any(gap["end_ns"] - gap["start_ns"] > record["gap_tolerance_ns"] for gap in gaps)):
    return False
  return len(_canonical(record)) <= 16 * 1024


def _valid_journal_reference(reference):
  if isinstance(reference, dict) and reference.get("schema_version") == 2:
    from .journal_segments import validate_reference
    try:
      validate_reference(reference)
      return True
    except ValueError:
      return False
  return (isinstance(reference, dict) and set(reference) == {"path", "sequence", "sha256", "bytes", "durable"}
          and reference["path"] == "settings.jsonl" and reference["durable"] is True
          and type(reference["sequence"]) is int and reference["sequence"] > 0
          and type(reference["bytes"]) is int and reference["bytes"] > 0
          and isinstance(reference["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]) is not None)


def _job_matches_diagnostic(record, reference, diagnostic):
  expected = record["settings_history_reference"]
  return (_valid_journal_reference(expected)
          and reference == (expected if expected.get("schema_version") == 2 else {"sha256": expected["sha256"], "bytes": expected["bytes"]})
          and diagnostic is not None and diagnostic["revision"] == record["diagnostic_revision"]
          and diagnostic["settings_revision"] == record["settings_revision"]
          and diagnostic["window_start_ns"] == record["window_start_ns"] and diagnostic["window_end_ns"] == record["window_end_ns"]
          and diagnostic["route_observation"] == record["route_observation"])


def validate_checkpoint(checkpoint):
  if isinstance(checkpoint, dict) and checkpoint.get("schema_version") == 2:
    from .journal_segments import validate_checkpoint as validate_segmented_checkpoint
    return validate_segmented_checkpoint(checkpoint)
  required = {"schema_version", "session_id", "sequence", "chain_sha256", "bytes", "cutoff_mono_ns",
              "consumer_budget_rejections", "durable"}
  if (not isinstance(checkpoint, dict) or set(checkpoint) != required or type(checkpoint["schema_version"]) is not int
      or checkpoint["schema_version"] != 1 or checkpoint["durable"] is not True
      or not isinstance(checkpoint["session_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", checkpoint["session_id"])
      or not isinstance(checkpoint["chain_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", checkpoint["chain_sha256"])
      or any(type(checkpoint[key]) is not int or not minimum <= checkpoint[key] <= 2**63 - 1 for key, minimum in (
        ("sequence", 1), ("bytes", 1), ("cutoff_mono_ns", 0), ("consumer_budget_rejections", 0)))):
    raise ValueError("invalid_history_checkpoint")
  return deepcopy(checkpoint)


def load_history(path, *, max_bytes=8 * 1024 * 1024, max_events=10_000, include_records=False, checkpoint=None, selection=None):
  """Verify a read-only prefix. Never truncate, repair, or resume a prior journal."""
  if type(max_bytes) is not int or max_bytes <= 0 or type(max_events) is not int or max_events <= 0:
    raise ValueError("invalid_history_limits")
  path = Path(path)
  if path.is_dir() or isinstance(checkpoint, dict) and checkpoint.get("schema_version") == 2:
    return _load_segmented_history(path, checkpoint=checkpoint, selection=selection, max_bytes=max_bytes,
                                   max_events=max_events, include_records=include_records)
  if selection is not None:
    raise ValueError("history_selection_requires_segmented_checkpoint")
  checkpoint = validate_checkpoint(checkpoint) if checkpoint is not None else None
  if any(_is_link(part) for part in (path, *path.parents)) or not path.is_file():
    raise ValueError("history_file_not_regular")
  result = {"state": "complete", "reason": None, "verified_events": 0, "sequence": 0,
            "sha256": ZERO_HASH, "session_id": None, "last_record": None, "bytes": 0,
            "history_complete": False, "consumer_applied": False, "references": {}, "diagnostics": [],
            "diagnostic_references": {}, "preservation_jobs": {}, "latest_settings": None, "closed": False,
            "consumer_events": 0, "latest_consumers": {}, "consumer_budget_rejections": None}
  if type(include_records) is not bool:
    raise ValueError("invalid_history_records_option")
  raw_digest = hashlib.sha256()
  if include_records:
    result.update(records=[], verified_bytes_sha256=raw_digest.hexdigest())
  if path.stat().st_size > max_bytes:
    return {**result, "state": "incomplete", "reason": "history_byte_limit"}
  with path.open("rb") as stream:
    while True:
      line = stream.readline(min(MAX_LINE_BYTES + 1, max_bytes - result["bytes"] + 1))
      if not line:
        break
      result["bytes"] += len(line)
      if len(line) > MAX_LINE_BYTES or result["bytes"] > max_bytes:
        return {**result, "state": "incomplete", "reason": "history_line_limit"}
      if not line.endswith(b"\n"):
        return {**result, "state": "incomplete", "reason": "history_truncated_suffix"}
      if result["verified_events"] >= max_events:
        return {**result, "state": "incomplete", "reason": "history_event_limit"}
      try:
        envelope = json.loads(line.decode("utf-8"), object_pairs_hook=_unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_history")))
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema_version", "session_id", "sequence", "previous_sha256", "record", "sha256"}:
          raise ValueError("history_envelope")
        digest = envelope["sha256"]
        body = {key: value for key, value in envelope.items() if key != "sha256"}
        if (envelope["schema_version"] != 1 or type(envelope["sequence"]) is not int
            or envelope["sequence"] != result["sequence"] + 1
            or envelope["previous_sha256"] != result["sha256"]
            or not isinstance(envelope["session_id"], str) or not 1 <= len(envelope["session_id"]) <= 128
            or result["session_id"] not in (None, envelope["session_id"])
            or not isinstance(envelope["record"], dict)
            or digest != hashlib.sha256(_canonical(body)).hexdigest()):
          raise ValueError("history_chain")
        record = envelope["record"]
        kind = record.get("type")
        if (result["closed"] or kind not in {"session", "settings_observation", "diagnostic", "archive_preserved", "preservation_job", "consumer_observation", "session_closed"}
            or (result["verified_events"] == 0) != (kind == "session")):
          raise ValueError("history_lifecycle")
        if kind == "consumer_observation":
          from .consumer_observer import validate_event
          validate_event(record.get("event"))
          if type(record.get("recorded_mono_ns")) is not int or record["recorded_mono_ns"] < 0:
            raise ValueError("history_consumer_record_time")
        if kind == "session_closed" and "consumer_budget_rejections" in record:
          if type(record["consumer_budget_rejections"]) is not int or record["consumer_budget_rejections"] < 0:
            raise ValueError("history_consumer_rejections")
        if kind == "diagnostic" and (not _valid_diagnostic(record.get("diagnostic"))
                                     or type(record.get("settings_revision")) is not int or record["settings_revision"] < 0
                                     or not _valid_route_observation(record.get("route_observation"))):
          raise ValueError("history_diagnostic")
        if kind == "preservation_job":
          job = record.get("job")
          if not _valid_job_status(job):
            raise ValueError("history_preservation_job")
          sequence = job["settings_history_reference"].get("sequence")
          if type(sequence) is not int or not _job_matches_diagnostic(
              job, result["references"].get(str(sequence)), result["diagnostic_references"].get(str(sequence))):
            raise ValueError("history_preservation_job_binding")
      except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return {**result, "state": "corrupt", "reason": "history_invalid_suffix"}
      result.update(verified_events=result["verified_events"] + 1, sequence=envelope["sequence"],
                    sha256=digest, session_id=envelope["session_id"], last_record=envelope["record"])
      result["references"][str(envelope["sequence"])] = {"sha256": digest, "bytes": result["bytes"]}
      if include_records:
        raw_digest.update(line)
        result["verified_bytes_sha256"] = raw_digest.hexdigest()
        result["records"].append({"sequence": envelope["sequence"], "sha256": digest,
                                  "bytes": result["bytes"], "record": deepcopy(record)})
      if envelope["record"].get("type") == "diagnostic":
        result["diagnostics"].append(envelope["record"]["diagnostic"])
        result["diagnostic_references"][str(envelope["sequence"])] = {
          **envelope["record"]["diagnostic"], "settings_revision": envelope["record"]["settings_revision"],
          "route_observation": envelope["record"]["route_observation"]}
      if envelope["record"].get("type") == "settings_observation" and "settings" in envelope["record"]:
        result["latest_settings"] = envelope["record"]
      if envelope["record"].get("type") == "preservation_job":
        result["preservation_jobs"][str(envelope["record"]["job"]["diagnostic_revision"])] = envelope["record"]["job"]
      if kind == "consumer_observation":
        result["consumer_events"] += 1
        event = record["event"]
        result["latest_consumers"][event["consumer_id"]] = event
      result["closed"] = envelope["record"].get("type") == "session_closed"
      if result["closed"]:
        result["consumer_budget_rejections"] = record.get("consumer_budget_rejections")
  if result["verified_events"] == 0:
    result.update(state="incomplete", reason="history_empty")
  elif checkpoint is not None:
    if any(result[key] != checkpoint[target] for key, target in (
        ("session_id", "session_id"), ("sequence", "sequence"), ("sha256", "chain_sha256"), ("bytes", "bytes"))) or (
        result["closed"] and result["consumer_budget_rejections"] != checkpoint["consumer_budget_rejections"]):
      result.update(state="incomplete", reason="history_checkpoint_mismatch")
    else:
      result["snapshot_prefix_complete"] = True
      if not result["closed"]:
        result["consumer_budget_rejections"] = checkpoint["consumer_budget_rejections"]
  elif not result["closed"]:
    result.update(state="incomplete", reason="history_session_unclosed")
  return result


def _segment_inventory(root, checkpoint):
  from .journal_segments import iter_verified_catalog
  yield from iter_verified_catalog(root, checkpoint)
  active = checkpoint["active"]
  if active is not None:
    yield {"index": active["index"], "path": active["path"], "first_sequence": active["first_sequence"],
           "last_sequence": checkpoint["sequence"], "bytes": active["bytes"], "content_sha256": active["content_sha256"],
           "first_mono_ns": None, "last_mono_ns": checkpoint["cutoff_mono_ns"]}


def _closure_references(record):
  if record.get("type") == "archive_preserved":
    return [record.get("archive", {}).get("settings_history_reference")]
  if record.get("type") == "preservation_job":
    return [record.get("job", {}).get("settings_history_reference")]
  return []


def select_history_window(root, checkpoint, start_ns, end_ns, required_refs=()):
  """Select original segments and transitive diagnostic receipts; never clone rows."""
  from .journal_segments import iter_verified_records, validate_reference
  checkpoint = validate_checkpoint(checkpoint)
  if checkpoint["schema_version"] != 2 or any(type(value) is not int or value < 0 for value in (start_ns, end_ns)) or end_ns < start_ns:
    raise ValueError("history_window_invalid")
  if not isinstance(required_refs, (list, tuple)) or len(required_refs) > 128:
    raise ValueError("history_required_reference_budget")
  required = {}
  def add_reference(value):
    reference = validate_reference(value)
    if reference.get("schema_version") != 2 or reference["session_id"] != checkpoint["session_id"] or reference["sequence"] > checkpoint["sequence"]:
      raise ValueError("history_reference_session_or_prefix_mismatch")
    old = required.get(reference["sequence"])
    if old is not None and old != reference:
      raise ValueError("history_required_reference_conflict")
    required[reference["sequence"]] = reference
    if len(required) > 128:
      raise ValueError("history_required_reference_budget")
  for reference in required_refs:
    add_reference(reference)
  selected, previous = set(), None
  for entry in _segment_inventory(root, checkpoint):
    active = entry["first_mono_ns"] is None
    if active or entry["first_mono_ns"] <= end_ns and entry["last_mono_ns"] >= start_ns:
      selected.add(entry["index"])
      if previous is not None:
        selected.add(previous)
    previous = entry["index"]
  scanned, actual = set(), {}
  while True:
    available, total, events = set(), checkpoint["catalog"]["bytes"], 0
    previous = None
    for entry in _segment_inventory(root, checkpoint):
      if entry["index"] not in selected and any(entry["first_sequence"] <= seq <= entry["last_sequence"] for seq in required):
        selected.add(entry["index"])
        if previous is not None:
          selected.add(previous)
      if entry["index"] in selected:
        available.add(entry["index"])
        total += entry["bytes"]
        events += entry["last_sequence"] - entry["first_sequence"] + 1
      previous = entry["index"]
    if total > 32 * 1024 * 1024 or events > 40_000 or len(selected) > 128:
      raise ValueError("history_window_budget")
    if available != selected:
      raise ValueError("history_selected_segment_missing")
    pending = selected - scanned
    if not pending:
      break
    for item in iter_verified_records(root, checkpoint, segment_indices=sorted(pending)):
      reference, record = item["reference"], item["envelope"]["record"]
      actual[reference["sequence"]] = reference
      for dependency in _closure_references(record):
        add_reference(dependency)
    scanned.update(pending)
  for sequence, reference in required.items():
    if actual.get(sequence) != reference:
      raise ValueError("history_required_reference_mismatch")
  omitted = []
  for entry in _segment_inventory(root, checkpoint):
    if entry["index"] in selected:
      continue
    if omitted and omitted[-1]["end_sequence"] + 1 == entry["first_sequence"]:
      omitted[-1]["end_sequence"] = entry["last_sequence"]
    else:
      omitted.append({"start_sequence": entry["first_sequence"], "end_sequence": entry["last_sequence"]})
  return {"schema_version": 1, "kind": "settings_history_window", "session_id": checkpoint["session_id"],
          "checkpoint_sha256": hashlib.sha256(_canonical(checkpoint)).hexdigest(), "start_ns": start_ns, "end_ns": end_ns,
          "segment_indices": sorted(selected), "required_refs": [required[key] for key in sorted(required)],
          "verification_scope": "selected_segment_closure", "omitted_sequence_ranges": omitted}


def _load_segmented_history(root, *, checkpoint, selection, max_bytes, max_events, include_records):
  from .journal_segments import iter_verified_records
  if max_bytes > 32 * 1024 * 1024 or max_events > 40_000:
    raise ValueError("history_window_budget")
  root = Path(root)
  if any(_is_link(part) for part in (root, *root.parents)) or not root.is_dir():
    raise ValueError("history_directory_not_regular")
  if checkpoint is None:
    marker = root / "history-checkpoint.json"
    if not marker.is_file() or _is_link(marker) or marker.stat().st_size > 16 * 1024:
      raise ValueError("history_checkpoint_required")
    checkpoint = json.loads(marker.read_bytes(), object_pairs_hook=_unique)
  checkpoint = validate_checkpoint(checkpoint)
  if checkpoint["schema_version"] != 2:
    raise ValueError("history_segmented_checkpoint_required")
  if type(include_records) is not bool:
    raise ValueError("invalid_history_records_option")
  selected = selection is not None
  if selected:
    if not isinstance(selection, dict):
      raise ValueError("history_selection_invalid")
    expected = select_history_window(root, checkpoint, selection.get("start_ns"), selection.get("end_ns"), selection.get("required_refs", []))
    if selection != expected:
      raise ValueError("history_selection_mismatch")
  indices = selection["segment_indices"] if selected else None
  files = []
  catalog = checkpoint["catalog"]
  if catalog["bytes"]:
    catalog_path = root / catalog["path"]
    if any(_is_link(part) for part in (catalog_path, *catalog_path.parents)):
      raise ValueError("history_catalog_link")
    digest, remaining = hashlib.sha256(), catalog["bytes"]
    if remaining > max_bytes:
      raise ValueError("history_window_budget")
    with catalog_path.open("rb") as stream:
      while remaining:
        raw = stream.read(min(65536, remaining))
        if not raw:
          raise ValueError("history_catalog_truncated")
        digest.update(raw)
        remaining -= len(raw)
    files.append({"path": catalog["path"], "bytes": catalog["bytes"], "sha256": digest.hexdigest(), "copy_mode": "prefix"})
  row_bytes, count = 0, 0
  for entry in _segment_inventory(root, checkpoint):
    if indices is None or entry["index"] in indices:
      row_bytes += entry["bytes"]
      count += entry["last_sequence"] - entry["first_sequence"] + 1
      files.append({"path": entry["path"], "bytes": entry["bytes"], "sha256": entry["content_sha256"], "copy_mode": "prefix"})
  selected_bytes = sum(item["bytes"] for item in files)
  if selected_bytes > max_bytes or count > max_events:
    raise ValueError("history_window_budget")
  result = {"state": "complete", "reason": None, "verified_events": 0, "sequence": 0, "sha256": ZERO_HASH,
            "session_id": checkpoint["session_id"], "last_record": None, "bytes": row_bytes,
            "history_complete": False, "consumer_applied": False, "references": {}, "diagnostics": [],
            "diagnostic_references": {}, "preservation_jobs": {}, "latest_settings": None, "closed": False,
            "consumer_events": 0, "latest_consumers": {}, "consumer_budget_rejections": checkpoint["consumer_budget_rejections"],
            "journal_version": 2, "verified_bytes_sha256": None, "snapshot_prefix_complete": not selected,
            "selected_closure_complete": selected, "verification_scope": "selected_segment_closure" if selected else "complete_checkpoint_prefix",
            "selection": deepcopy(selection), "history_files": files, "selected_byte_count": selected_bytes,
            "checkpoint_tip": {"sequence": checkpoint["sequence"], "chain_sha256": checkpoint["chain_sha256"], "total_bytes": checkpoint["bytes"]}}
  if include_records:
    result["records"] = []
  for item in iter_verified_records(root, checkpoint, segment_indices=indices):
    envelope, reference = item["envelope"], item["reference"]
    record, sequence = envelope["record"], envelope["sequence"]
    kind = record.get("type")
    if result["closed"] or kind not in {"session", "settings_observation", "diagnostic", "archive_preserved", "preservation_job", "consumer_observation", "session_closed"}:
      raise ValueError("history_lifecycle")
    if kind == "consumer_observation":
      from .consumer_observer import validate_event
      event = validate_event(record.get("event"))
      if type(record.get("recorded_mono_ns")) is not int or record["recorded_mono_ns"] < 0:
        raise ValueError("history_consumer_record_time")
      result["consumer_events"] += 1
      result["latest_consumers"][event["consumer_id"]] = event
    elif kind == "diagnostic":
      if not _valid_diagnostic(record.get("diagnostic")) or not _valid_route_observation(record.get("route_observation")) or type(record.get("settings_revision")) is not int:
        raise ValueError("history_diagnostic")
      result["diagnostics"].append(record["diagnostic"])
      result["diagnostic_references"][str(sequence)] = {**record["diagnostic"], "settings_revision": record["settings_revision"], "route_observation": record["route_observation"]}
    elif kind == "preservation_job":
      job = record.get("job")
      if not _valid_job_status(job) or not _job_matches_diagnostic(job, result["references"].get(str(job["settings_history_reference"]["sequence"])), result["diagnostic_references"].get(str(job["settings_history_reference"]["sequence"]))):
        raise ValueError("history_preservation_job_binding")
      result["preservation_jobs"][str(job["diagnostic_revision"])] = job
    elif kind == "archive_preserved":
      reference_to_diagnostic = record.get("archive", {}).get("settings_history_reference", {})
      if (result["references"].get(str(reference_to_diagnostic.get("sequence"))) != reference_to_diagnostic
          or str(reference_to_diagnostic.get("sequence")) not in result["diagnostic_references"]):
        raise ValueError("history_archive_diagnostic_binding")
    elif kind == "settings_observation" and "settings" in record:
      result["latest_settings"] = record
    result.update(verified_events=result["verified_events"] + 1, sequence=sequence, sha256=envelope["sha256"], last_record=record,
                  closed=kind == "session_closed")
    result["references"][str(sequence)] = deepcopy(reference)
    if include_records:
      result["records"].append({"sequence": sequence, "sha256": envelope["sha256"], "bytes": reference["bytes"], "reference": deepcopy(reference), "record": deepcopy(record)})
  if result["verified_events"] == 0:
    result.update(state="incomplete", reason="history_empty")
  return result


class SettingsRecorder:
  def __init__(self, source_dir, session_dir, session_id, *, clock_ns=time.monotonic_ns,
               max_gap_ns=2_000_000_000, max_value_bytes=256,
               max_journal_bytes=8 * 1024 * 1024, max_events=10_000, identity_provider=None,
               journal_format="v1", journal_max_total_bytes=512 * 1024 * 1024,
               journal_free_reserve_bytes=256 * 1024 * 1024, cache_window_ns=602_100_000_000):
    if journal_format not in ("v1", "segmented") or type(cache_window_ns) is not int or cache_window_ns <= 0:
      raise ValueError("invalid_history_format_or_cache")
    self.journal_format, self.cache_window_ns = journal_format, cache_window_ns
    self._codec, self._last_full_snapshot_segment = None, None
    self._metadata_seen, self._diagnostic_seen, self._pins = {}, {}, {}
    if identity_provider is not None and not callable(identity_provider):
      raise ValueError("history_identity_provider_invalid")
    self.identity_provider = identity_provider
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
      raise ValueError("invalid_history_session")
    for value in (max_gap_ns, max_value_bytes, max_journal_bytes, max_events):
      if type(value) is not int or value <= 0:
        raise ValueError("invalid_history_limits")
    if max_value_bytes > 256:
      raise ValueError("history_value_limit_exceeded")
    self.source_dir, self.session_dir = Path(source_dir), Path(session_dir)
    if not self.session_dir.is_dir() or any(_is_link(part) for part in (self.session_dir, *self.session_dir.parents)):
      raise ValueError("history_session_directory_not_regular")
    source_resolved, session_resolved = self.source_dir.resolve(), self.session_dir.resolve()
    if source_resolved == session_resolved or source_resolved in session_resolved.parents or session_resolved in source_resolved.parents:
      raise ValueError("settings_source_and_history_overlap")
    # Deliberate root links (the native params/d layout) are pinned and rechecked.
    self._pinned_root = self._pinned_identity = None
    self.clock_ns, self.max_gap_ns, self.max_value_bytes = clock_ns, max_gap_ns, max_value_bytes
    self.max_journal_bytes, self.max_events = max_journal_bytes, max_events
    # Sampled consumers must leave room for settings and preservation receipts.
    self._consumer_bytes = self._consumer_events = self._consumer_budget_rejections = 0
    self.session_id, self.path = session_id, self.session_dir / "settings.jsonl"
    if journal_format == "segmented":
      from .journal_segments import SegmentedJournal
      self._codec = SegmentedJournal(self.session_dir, session_id, max_segment_bytes=max_journal_bytes, max_segment_events=max_events,
        max_consumer_bytes=max_journal_bytes // 2, max_consumer_events=max_events // 2,
        max_total_bytes=journal_max_total_bytes, free_reserve_bytes=journal_free_reserve_bytes)
      self._stream = None
    else:
      self._stream = self.path.open("xb", buffering=0)
    self._closed, self._failed = False, False
    self._references = {}
    self._settings_revisions = set()
    self._revision_metadata = {}
    self._diagnostic_records = {}
    self._previous_fingerprint, self._previous_begin, self._previous_end = None, None, None
    self._status = {"state": "starting", "reason": None, "source_status": "not_observed",
                    "revision": 0, "durable_revision": 0, "poll_sequence": 0, "changed_keys": [],
                    "settings": dict.fromkeys(KEYS), "observation": None, "route_observation": None, "route_changed": False,
                    "journal": {"path": "settings.jsonl", "sequence": 0, "sha256": ZERO_HASH, "bytes": 0, "durable": False},
                    "consumer_applied": False, "atomic_snapshot": False, "history_complete": False}
    self._append({"type": "session", "source_kind": "explicit_stored_settings_directory",
                  "keys": list(KEYS), "ancillary_keys": ["CurrentRoute"], "consumer_applied": False, "atomic_snapshot": False,
                  "history_complete": False, "power_loss_durability":
                  "file_fsync_only" if os.name == "nt" else "file_and_directory_fsync"})

  def _append(self, record):
    if self._failed or self._closed:
      return False
    journal = self._status["journal"]
    try:
      if self._codec is not None:
        reference = self._codec.append(record, mono_ns=self.clock_ns())
        self._status["journal"] = deepcopy(reference)
        if record["type"] == "diagnostic":
          self._references[reference["sequence"]] = deepcopy(reference)
          self._diagnostic_seen[reference["sequence"]] = self.clock_ns()
        return True
      body = {"schema_version": 1, "session_id": self.session_id, "sequence": journal["sequence"] + 1,
              "previous_sha256": journal["sha256"], "record": record}
      digest = hashlib.sha256(_canonical(body)).hexdigest()
      encoded = _canonical({**body, "sha256": digest}) + b"\n"
      is_consumer = record["type"] == "consumer_observation"
      if is_consumer and (self._consumer_bytes + len(encoded) > self.max_journal_bytes // 2
                          or self._consumer_events >= self.max_events // 2
                          or journal["sequence"] >= self.max_events - 1
                          or journal["bytes"] + len(encoded) > self.max_journal_bytes - MAX_LINE_BYTES):
        self._consumer_budget_rejections += 1
        return False
      if len(encoded) > MAX_LINE_BYTES or journal["sequence"] >= self.max_events or journal["bytes"] + len(encoded) > self.max_journal_bytes:
        raise ValueError("history_budget_exceeded")
      written = 0
      while written < len(encoded):
        count = self._stream.write(encoded[written:])
        if count is None or count <= 0:
          raise OSError("history_short_write")
        written += count
      os.fsync(self._stream.fileno())
      if journal["sequence"] == 0:
        _sync_directory(self.session_dir)
      self._status["journal"] = {"path": "settings.jsonl", "sequence": body["sequence"], "sha256": digest,
                                 "bytes": journal["bytes"] + len(encoded), "durable": True}
      self._references[body["sequence"]] = (digest, journal["bytes"] + len(encoded))
      if is_consumer:
        self._consumer_bytes += len(encoded)
        self._consumer_events += 1
      return True
    except (OSError, ValueError, TypeError, OverflowError) as error:
      self._failed = True
      self._status.update(state="failed", reason="storage_budget_exhausted" if str(error) == "storage_budget_exhausted" else "history_write_failed")
      self._status["journal"] = {**journal, "durable": False}
      return False

  def _root(self):
    resolved = self.source_dir.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode):
      raise ValueError("settings_source_not_directory")
    if self._pinned_root is None:
      self._pinned_root, self._pinned_identity = resolved, _identity(info)
    if resolved != self._pinned_root or _identity(info) != self._pinned_identity:
      raise ValueError("settings_source_replaced")
    return resolved

  def _read(self, root, key):
    begin = self.clock_ns()
    entry = {"status": "missing", "raw": None, "value": None, "begin_ns": begin, "end_ns": begin}
    path = root / key
    try:
      before = path.lstat()
      if _is_link(path) or not stat.S_ISREG(before.st_mode):
        raise ValueError("settings_key_not_regular")
      flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
      fd = os.open(path, flags)
      try:
        opened = os.fstat(fd)
        if _version(before) != _version(opened):
          raise ValueError("settings_key_changed")
        data = os.read(fd, self.max_value_bytes + 1)
        after_opened = os.fstat(fd)
      finally:
        os.close(fd)
      after = path.lstat()
      if _is_link(path) or _version(before) != _version(after) or _version(opened) != _version(after_opened):
        raise ValueError("settings_key_changed")
      if len(data) > self.max_value_bytes:
        raise ValueError("settings_value_too_large")
      raw = data.decode("utf-8")
      value = _number(raw)
      entry.update(status="observed" if value is not None else "invalid", raw=raw, value=value,
                   identity={"device": before.st_dev, "inode": before.st_ino})
    except FileNotFoundError:
      pass
    except (OSError, ValueError, UnicodeError, OverflowError):
      entry.update(status="error")
    entry["end_ns"] = self.clock_ns()
    return entry

  def sample(self):
    if self._failed or self._closed:
      return self.snapshot()
    begin = self.clock_ns()
    def identity():
      from .vehicle_identity import validate_identity, unavailable_identity
      try:
        return validate_identity(self.identity_provider())
      except Exception:
        return unavailable_identity("car_params_provider_unavailable")
    identity_before = identity() if self.identity_provider is not None else None
    source_status, entries = "available", {}
    route_observation = {"route": None, "state": "unavailable", "begin_ns": begin, "end_ns": begin}
    try:
      root = self._root()
      entries = {key: self._read(root, key) for key in KEYS}
      route_entry = self._read(root, "CurrentRoute")
      raw_route = route_entry["raw"]
      route = raw_route.strip() if isinstance(raw_route, str) else None
      known_route = route is not None and re.fullmatch(RE.LOG_ID, route) is not None
      route_observation = {"route": route if known_route else None,
                           "state": "observed" if known_route else route_entry["status"],
                           "begin_ns": route_entry["begin_ns"], "end_ns": route_entry["end_ns"]}
      if route_observation["state"] == "observed" and not known_route:
        route_observation["state"] = "invalid"
      self._root()
    except FileNotFoundError:
      source_status = "unavailable"
    except (OSError, ValueError):
      source_status = "source_changed_or_invalid"
    if source_status != "available":
      entries = {key: {"status": "unavailable", "raw": None, "value": None,
                        "begin_ns": begin, "end_ns": self.clock_ns()} for key in KEYS}
      route_observation = {"route": None, "state": "unavailable", "begin_ns": begin, "end_ns": self.clock_ns()}
    vehicle_identity = None
    if self.identity_provider is not None:
      from .vehicle_identity import unavailable_identity
      identity_after = identity()
      vehicle_identity = identity_before if identity_before == identity_after else unavailable_identity("car_params_changed_during_settings_scan")
    end = self.clock_ns()
    previous = self._previous_end
    gap = end < begin or end - begin > self.max_gap_ns or (previous is not None and (
      begin < previous or begin - previous > self.max_gap_ns))
    content = {key: {field: value[field] for field in ("status", "raw", "value")} for key, value in entries.items()}
    route_identity = {key: route_observation[key] for key in ("route", "state")}
    fingerprint_data = {"source_status": source_status, "settings": content, "route": route_identity}
    if vehicle_identity is not None:
      fingerprint_data.update(identity_version=1, vehicle_identity=vehicle_identity)
    fingerprint = hashlib.sha256(_canonical(fingerprint_data)).hexdigest()
    changed = fingerprint != self._previous_fingerprint or gap
    old_settings = getattr(self, "_previous_entries", {})
    route_changed = route_identity != getattr(self, "_previous_route", None)
    changed_keys = [key for key in KEYS if content[key] != old_settings.get(key)] if changed else []
    revision = self._status["revision"] + int(changed)
    observation = {"begin_ns": begin, "end_ns": end, "previous_begin_ns": self._previous_begin,
                   "previous_end_ns": previous, "gap": gap,
                   # An early key can change while the previous scan reads later keys.
                   "change_interval": {"after_ns": self._previous_begin, "before_ns": end} if changed else None,
                   "exact_change_time_known": False}
    record = {"type": "settings_observation", "poll_sequence": self._status["poll_sequence"] + 1,
              "revision": revision, "source_status": source_status, "observation": observation,
              "settings_sha256": fingerprint, "changed_keys": changed_keys,
              "route_observation": route_observation, "route_changed": route_changed,
              "consumer_applied": False, "atomic_snapshot": False}
    if vehicle_identity is not None:
      record.update(identity_version=1, vehicle_identity=vehicle_identity)
    refresh = self._codec is not None and not changed and self._status["journal"].get("segment_index") != self._last_full_snapshot_segment
    if changed or refresh:
      record["settings"] = entries
    if refresh:
      record["snapshot_refresh"] = True
    state = "observed" if all(value["status"] == "observed" for value in entries.values()) else "partial"
    if source_status != "available":
      state = "unavailable"
    self._status.update(state=state, reason="polling_gap" if gap else None, source_status=source_status,
                        revision=revision, poll_sequence=record["poll_sequence"], changed_keys=changed_keys,
                        settings={key: value["value"] for key, value in entries.items()}, observation=observation,
                        route_observation=route_observation, route_changed=route_changed)
    if self._append(record):
      self._status["durable_revision"] = revision
      self._settings_revisions.add(revision)
      if self._codec is not None:
        self._metadata_seen[revision] = end
        if "settings" in record:
          self._last_full_snapshot_segment = self._status["journal"]["segment_index"]
      self._revision_metadata.setdefault(revision, {"settings_revision": revision, "route_observation": deepcopy(route_observation)})
      if vehicle_identity is not None:
        self._revision_metadata[revision].update(identity_version=1, vehicle_identity=deepcopy(vehicle_identity))
        self._status.update(identity_version=1, vehicle_identity=deepcopy(vehicle_identity))
      self._status["route_observation"] = deepcopy(self._revision_metadata[revision]["route_observation"])
      self._previous_fingerprint, self._previous_begin, self._previous_end = fingerprint, begin, end
      self._previous_entries = content
      self._previous_route = route_identity
      self._trim_cache(end)
    return self.snapshot()

  def append_archive(self, record):
    """Called only by the journal-owning thread, with a bounded archive summary."""
    if not isinstance(record, dict) or record.get("state") != "complete":
      raise ValueError("archive_completion_required")
    allowed = {"state", "segment", "sha256", "bytes", "mono_min_ns", "mono_max_ns", "manifest_sha256", "manifest_path",
               "settings_history_reference", "archive_id", "session_id", "source_mode", "route", "start_mono_ns", "end_mono_ns",
               "manifest_relative", "settings_revision", "diagnostic_revision", "window_start_ns", "window_end_ns"}
    if set(record) - allowed or len(_canonical(record)) > 4096:
      raise ValueError("invalid_archive_history_summary")
    reference = record.get("settings_history_reference")
    if not _valid_journal_reference(reference):
      raise ValueError("invalid_archive_history_reference")
    expected = reference if self._codec is not None else (reference["sha256"], reference["bytes"])
    if self._references.get(reference["sequence"]) != expected:
      raise ValueError("archive_history_reference_not_committed")
    if record.get("session_id", self.session_id) != self.session_id:
      raise ValueError("archive_session_mismatch")
    diagnostic = self._diagnostic_records.get(reference["sequence"])
    route = diagnostic["route_observation"] if diagnostic is not None else None
    if route is None or route["state"] != "observed" or record.get("route") != route["route"]:
      raise ValueError("archive_diagnostic_route_mismatch")
    self._append({"type": "archive_preserved", "archive": record})
    return self.snapshot()

  def append_diagnostic(self, record, *, settings_revision=None, route_observation=None):
    if not _valid_diagnostic(record):
      raise ValueError("invalid_history_diagnostic")
    selected_revision = self._status["durable_revision"] if settings_revision is None else settings_revision
    if type(selected_revision) is not int or selected_revision not in self._settings_revisions:
      raise ValueError("history_settings_revision_not_committed")
    selected_route = self._revision_metadata[selected_revision]["route_observation"]
    if route_observation is not None and route_observation != selected_route:
      raise ValueError("history_diagnostic_route_mismatch")
    if self._append({"type": "diagnostic", "diagnostic": record, "settings_revision": selected_revision,
                     "route_observation": selected_route}):
      self._diagnostic_records[self._status["journal"]["sequence"]] = {
        **deepcopy(record), "settings_revision": selected_revision, "route_observation": deepcopy(selected_route)}
    return self.snapshot()

  def revision_metadata(self, revision):
    if type(revision) is not int or revision not in self._revision_metadata:
      raise ValueError("history_settings_revision_not_committed")
    return deepcopy(self._revision_metadata[revision])

  def append_job_status(self, record):
    if not _valid_job_status(record):
      raise ValueError("invalid_history_preservation_job")
    selected = record["settings_history_reference"]
    sequence = selected.get("sequence") if isinstance(selected, dict) else None
    reference = self._references.get(sequence) if type(sequence) is int else None
    expected = reference if self._codec is not None else {"sha256": reference[0], "bytes": reference[1]} if reference is not None else None
    if not _job_matches_diagnostic(record, expected, self._diagnostic_records.get(sequence) if type(sequence) is int else None):
      raise ValueError("history_preservation_job_binding_mismatch")
    self._append({"type": "preservation_job", "job": record})
    self._pins.pop(sequence, None)
    self._trim_cache(self.clock_ns())
    return self.snapshot()

  def pin_diagnostic(self, reference, revision):
    sequence = reference.get("sequence") if isinstance(reference, dict) else None
    if self._codec is not None:
      if self._references.get(sequence) != reference or revision not in self._revision_metadata:
        raise ValueError("history_pin_not_committed")
      if len(self._pins) >= 8 and sequence not in self._pins:
        raise ValueError("history_pin_budget")
      self._pins[sequence] = revision

  def _trim_cache(self, now):
    if self._codec is None:
      return
    keep_revisions = set(self._pins.values()) | {self._status["durable_revision"]}
    for revision, stamp in list(self._metadata_seen.items()):
      if revision not in keep_revisions and (now - stamp > self.cache_window_ns or len(self._metadata_seen) > 1024):
        self._metadata_seen.pop(revision, None)
        self._settings_revisions.discard(revision)
        self._revision_metadata.pop(revision, None)
    for sequence, stamp in list(self._diagnostic_seen.items()):
      if sequence not in self._pins and (now - stamp > self.cache_window_ns or len(self._diagnostic_seen) > 128):
        self._diagnostic_seen.pop(sequence, None)
        self._references.pop(sequence, None)
        self._diagnostic_records.pop(sequence, None)

  def snapshot(self):
    return deepcopy(self._status)

  def checkpoint(self):
    """Journal owner only: describe an already fsynced prefix, without disk I/O."""
    journal = self._status["journal"]
    if self._failed or not journal["durable"] or journal["sequence"] < 1:
      raise ValueError("history_checkpoint_unavailable")
    if self._codec is not None:
      return self._codec.checkpoint(cutoff_mono_ns=self.clock_ns(), consumer_budget_rejections=self._consumer_budget_rejections)
    return validate_checkpoint({"schema_version": 1, "session_id": self.session_id, "sequence": journal["sequence"],
      "chain_sha256": journal["sha256"], "bytes": journal["bytes"], "cutoff_mono_ns": self.clock_ns(),
      "consumer_budget_rejections": self._consumer_budget_rejections, "durable": True})

  def append_consumer_observation(self, event):
    from .consumer_observer import validate_event
    value = validate_event(event)
    # Writer time is not the consumer's read/use time or a settings revision.
    return self._append({"type": "consumer_observation", "event": value, "recorded_mono_ns": self.clock_ns()})

  def close(self):
    if self._closed:
      return self.snapshot()
    if not self._failed:
      self._append({"type": "session_closed", "revision": self._status["durable_revision"],
                    "consumer_budget_rejections": self._consumer_budget_rejections,
                    "history_complete": False, "consumer_applied": False})
    if self._codec is not None:
      try:
        if not self._failed:
          token = self.checkpoint()
          with (self.session_dir / "history-checkpoint.json").open("xb") as stream:
            raw = _canonical(token) + b"\n"
            if stream.write(raw) != len(raw):
              raise OSError("history_closed_checkpoint_short_write")
            stream.flush()
            os.fsync(stream.fileno())
          _sync_directory(self.session_dir)
      except (OSError, ValueError):
        self._failed = True
        self._status.update(state="failed", reason="history_closed_checkpoint_failed")
      finally:
        self._codec.close()
    else:
      self._stream.close()
    self._closed = True
    if not self._failed:
      self._status["state"] = "closed"
    return self.snapshot()
