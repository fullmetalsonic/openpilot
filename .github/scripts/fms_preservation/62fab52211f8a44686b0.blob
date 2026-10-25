"""Read-only verification of a persisted session; never repairs or resumes it."""

import json
import os
from pathlib import Path
import re

from .evidence import _regular_file, hash_file
from .settings_history import load_history


def _object(path, byte_limit=64 * 1024):
  path = _regular_file(path)
  if path.stat().st_size > byte_limit:
    raise ValueError("recording_metadata_limit")

  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError("duplicate_recording_key")
      result[key] = value
    return result

  value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_recording")))
  if not isinstance(value, dict):
    raise ValueError("invalid_recording_metadata")
  return value


def inspect_session(directory, *, byte_limit=512 * 1024 * 1024, max_segments=128, session_snapshot=None,
                    checkpoint=None, history_selection=None):
  from .archive import verify_preserved_segment

  if type(byte_limit) is not int or byte_limit <= 0 or type(max_segments) is not int or max_segments <= 0:
    raise ValueError("invalid_inspection_budget")
  root = Path(directory)
  if session_snapshot is not None:
    from .journal_snapshot import load_journal_snapshot
    sealed = load_journal_snapshot(root, session_snapshot, max_bytes=min(byte_limit, 8 * 1024 * 1024))
  else:
    sealed = None
  if sealed is not None:
    history = sealed["history"]
    history_bytes = sealed.get("history_byte_count")
    if history_bytes is None:
      history_bytes = len(sealed["history_bytes"])
  else:
    segmented = isinstance(checkpoint, dict) and checkpoint.get("kind") == "segmented_settings_history" or not (root / "settings.jsonl").exists()
    options = {"max_bytes": min(byte_limit, (32 if segmented else 8) * 1024 * 1024), "max_events": 40_000 if segmented else 10_000}
    if segmented:
      options["include_records"] = True
    if checkpoint is not None:
      options["checkpoint"] = checkpoint
    if history_selection is not None:
      options["selection"] = history_selection
    history = load_history(root if segmented else root / "settings.jsonl", **options)
    history_bytes = history.get("selected_byte_count", history["bytes"]) if segmented else (root / "settings.jsonl").stat().st_size
  remaining = byte_limit - history_bytes
  selected_history = history.get("selected_closure_complete") is True
  remaining_decoded = byte_limit
  session_id = history["session_id"]
  metadata = sealed["recording"] if sealed is not None else _object(root / "recording.json")
  if metadata.get("session_id") != session_id or metadata.get("source_mode") not in ("live", "synthetic"):
    raise ValueError("recording_session_mismatch")
  remaining -= (root / "recording.json").stat().st_size
  diagnostics, valid_diagnostic_sequences, expected_diagnostics = [], set(), set()
  for sequence, diagnostic in history["diagnostic_references"].items():
    if len(diagnostics) >= max_segments:
      raise ValueError("diagnostic_inspection_count_limit")
    if (type(diagnostic.get("revision")) is not int or not 1 <= diagnostic["revision"] <= 2**63 - 1
        or not isinstance(diagnostic.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", diagnostic["sha256"])):
      raise ValueError("invalid_history_diagnostic_identity")
    name = f'{diagnostic["revision"]}-{diagnostic["sha256"][:16]}.json'
    expected_diagnostics.add(name)
    item = {"revision": diagnostic["revision"], "state": "invalid"}
    try:
      path = _regular_file(root / "diagnostics" / name)
      size = path.stat().st_size
      remaining -= size
      if remaining < 0 or size > 256 * 1024:
        raise ValueError("diagnostic_inspection_byte_limit")
      if hash_file(path, 256 * 1024)[0] != diagnostic["sha256"]:
        raise ValueError("diagnostic_hash_mismatch")
      value = _object(path, 256 * 1024)
      for key in ("revision", "window_start_ns", "window_end_ns", "profile_id", "invalidated", "partial_window"):
        if value.get(key) != diagnostic[key]:
          raise ValueError("diagnostic_metadata_mismatch")
      if value.get("observed_settings_revision") != diagnostic["settings_revision"]:
        raise ValueError("diagnostic_settings_mismatch")
      if value.get("route_observation") != diagnostic.get("route_observation"):
        raise ValueError("diagnostic_route_mismatch")
      item["state"] = "verified"
      valid_diagnostic_sequences.add(sequence)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
      pass
    diagnostics.append(item)
    if remaining < 0:
      raise ValueError("recording_inspection_byte_limit")
  diagnostic_root = root / "diagnostics"
  orphan_diagnostics = 0
  if diagnostic_root.exists() and sealed is None and not selected_history:
    if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction())
           for part in (diagnostic_root, *diagnostic_root.parents)):
      raise ValueError("diagnostic_directory_link")
    with os.scandir(diagnostic_root) as entries:
      for index, entry in enumerate(entries):
        if index >= max_segments:
          raise ValueError("diagnostic_inspection_count_limit")
        if entry.name not in expected_diagnostics:
          orphan_diagnostics += 1
          remaining -= _regular_file(Path(entry.path)).stat().st_size
  if remaining < 0:
    raise ValueError("recording_inspection_byte_limit")
  archive_root = root / "archive"
  items = []
  verified_ranges = {}
  conflicting_segments = set()
  paths = []
  verified_bytes = 0
  if sealed is not None and not archive_root.exists():
    raise ValueError("snapshot_archive_root_missing")
  if archive_root.exists():
    if not archive_root.is_dir() or any(part.is_symlink() or (
        hasattr(part, "is_junction") and part.is_junction()) for part in (archive_root, *archive_root.parents)):
      raise ValueError("archive_root_not_regular")
    if sealed is not None or selected_history:
      from .journal_snapshot import committed_archives
      receipts = sealed["archive_receipts"] if sealed is not None else committed_archives(history)
      if len(receipts) > max_segments:
        raise ValueError("archive_inspection_count_limit")
      paths = [archive_root / identity for identity in receipts]
    else:
      with os.scandir(archive_root) as entries:
        for entry in entries:
          if len(paths) >= max_segments:
            raise ValueError("archive_inspection_count_limit")
          paths.append(Path(entry.path))
    for path in sorted(paths):
      item = {"archive_id": path.name, "state": "incomplete", "reason": "completion_marker_missing"}
      try:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or not path.is_dir():
          raise ValueError("archive_entry_not_directory")
        size = 0
        with os.scandir(path) as files:
          for index, entry in enumerate(files):
            if index >= 8:
              raise ValueError("archive_entry_file_limit")
            size += _regular_file(Path(entry.path)).stat().st_size
        remaining -= size
        if remaining < 0:
          raise ValueError("archive_inspection_byte_limit")
        marker = path / "archive.json"
        if marker.exists():
          if remaining_decoded <= 0:
            raise ValueError("archive_inspection_decode_limit")
          decode_budget, remaining_decoded = remaining_decoded, 0
          verified = verify_preserved_segment(path, byte_limit=max(1, size), max_decompressed_bytes=decode_budget)
          remaining_decoded = decode_budget - verified["decompressed_bytes"]
          manifest = _object(marker)
          reference = manifest.get("settings_history_reference", {})
          sequence = str(reference.get("sequence"))
          expected = history["references"].get(sequence)
          diagnostic = history["diagnostic_references"].get(sequence)
          analysis = manifest.get("analysis", {})
          if (manifest.get("session_id") != session_id or manifest.get("source_mode") != metadata["source_mode"]
              or expected is None or diagnostic is None or sequence not in valid_diagnostic_sequences):
            raise ValueError("archive_history_binding_mismatch")
          matches = reference == expected if reference.get("schema_version") == 2 else (
            reference.get("path") == "settings.jsonl" and reference.get("durable") is True
            and all(reference.get(key) == expected[key] for key in ("sha256", "bytes")))
          if not matches:
            raise ValueError("archive_history_binding_mismatch")
          route = diagnostic.get("route_observation")
          if (not isinstance(route, dict) or route.get("state") != "observed"
              or route.get("route") != manifest.get("source", {}).get("route")):
            raise ValueError("archive_route_binding_mismatch")
          for archive_key, history_key in (("window_start_ns", "window_start_ns"), ("window_end_ns", "window_end_ns"),
                                           ("diagnostic_revision", "revision"), ("settings_revision", "settings_revision")):
            if analysis.get(archive_key) != diagnostic.get(history_key):
              raise ValueError("archive_diagnostic_binding_mismatch")
          if diagnostic.get("invalidated") is not False:
            raise ValueError("archive_references_invalid_diagnostic")
          copied = manifest["file"]["bytes"]
          item.update(state="verified", reason=None, bytes=copied, diagnostic_revision=diagnostic["revision"],
                      settings_revision=diagnostic["settings_revision"], manifest_sha256=hash_file(marker, 64 * 1024)[0])
          if sealed is not None or selected_history:
            receipt = receipts[path.name]
            if (item["manifest_sha256"] != receipt["manifest_sha256"]
                or any(verified[key] != receipt.get(key) for key in (
                  "sha256", "bytes", "route", "segment", "settings_history_reference", "session_id", "source_mode"))):
              raise ValueError("snapshot_archive_receipt_mismatch")
          # The self-contained verifier proves the copy; it does not prove full session coverage.
          if verified.get("session_id") != session_id:
            raise ValueError("archive_verified_session_mismatch")
          verified_bytes += copied
          identity = (verified["route"], verified["segment"])
          previous = verified_ranges.get(identity)
          if previous is not None and previous["sha256"] != verified["sha256"]:
            conflicting_segments.add(identity)
          verified_ranges[identity] = {"route": verified["route"], "segment": verified["segment"],
                                       "start": verified["start_mono_ns"], "end": verified["end_mono_ns"],
                                       "sha256": verified["sha256"]}
      except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError, RecursionError) as exc:
        reason = str(exc)
        item.update(state="invalid", reason=reason if re.fullmatch(r"[a-z_]{1,128}", reason) else type(exc).__name__)
      items.append(item)
      if remaining < 0 or remaining_decoded <= 0:
        break
  verified_count = sum(item["state"] == "verified" for item in items)
  uninspected = len(paths) - len(items)
  jobs = list(history["preservation_jobs"].values())
  incomplete_jobs = sum(job["state"] != "complete" for job in jobs)
  expected_jobs = {str(record["revision"]) for record in history["diagnostic_references"].values()
                   if record["invalidated"] is False}
  missing_jobs = sorted(expected_jobs - set(history["preservation_jobs"]))
  unverified_complete_jobs = []
  from .recording import _window_assessment
  for job in jobs:
    if job["state"] != "complete":
      continue
    route = job["route_observation"]["route"]
    window = (job["window_start_ns"], job["window_end_ns"])
    ranges = [item for identity, item in verified_ranges.items() if identity not in conflicting_segments
              and item["route"] == route and item["end"] >= window[0] and item["start"] <= window[1]]
    if not _window_assessment(ranges, window, route, job["gap_tolerance_ns"])["complete"]:
      unverified_complete_jobs.append(job["diagnostic_revision"])
  complete = (history["state"] == "complete" and verified_count == len(items) and uninspected == 0
              and len(valid_diagnostic_sequences) == len(diagnostics) and orphan_diagnostics == 0 and incomplete_jobs == 0
              and not missing_jobs and not unverified_complete_jobs and not conflicting_segments)
  result = {"schema_version": 1, "state": "verified" if complete else "partial", "scope": "persisted_recording_integrity",
          "session_id": session_id, "history": {key: history[key] for key in (
            "state", "reason", "closed", "verified_events", "sequence", "sha256", "bytes")},
          "diagnostics": {"items": diagnostics, "orphan_files": orphan_diagnostics},
          "consumer_observations": {"recorded_events": history["consumer_events"],
                                    "budget_rejected_events": history["consumer_budget_rejections"],
                                    "latest_consumers": history["latest_consumers"],
                                    "consumer_application_ack": False, "historical_only": True},
          "preservation_jobs": {"items": jobs, "incomplete_jobs": incomplete_jobs,
                                "missing_terminal_revisions": missing_jobs, "unverified_complete_revisions": unverified_complete_jobs,
                                "completion_scope": "archive_work_only_host_profile"},
          "archives": {"verified_segments": verified_count, "verified_bytes": verified_bytes, "uninspected_segments": uninspected,
                       "incomplete_or_invalid_segments": len(items) - verified_count, "items": items},
          "coverage_complete": False, "settings_history_complete": False, "consumer_applied": False,
          "recommendations_enabled": False, "apply_enabled": False, "read_only": True}
  if sealed is not None:
    selected = history.get("selected_closure_complete") is True
    result.update(scope="persisted_recording_selected_closure_integrity" if selected else "persisted_recording_prefix_integrity",
                  snapshot_prefix_complete=not selected, source_session_closed=sealed["marker"]["source_session_closed"])
    result["history"]["snapshot_prefix_complete"] = not selected
  if history.get("selected_closure_complete") is True:
    result.update(scope="persisted_recording_selected_closure_integrity", selected_closure_complete=True, snapshot_prefix_complete=False)
    result["history"].update(selected_closure_complete=True, snapshot_prefix_complete=False,
      selected_byte_count=history.get("selected_byte_count", history_bytes),
      checkpoint_tip=history.get("checkpoint_tip"), omitted_sequence_ranges=history.get("omitted_sequence_ranges", []))
  return result
