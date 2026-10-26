"""Immutable cached review summaries and server-owned, explicit source selections."""
from collections import Counter, OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid

from .evidence import _regular_file, hash_file, verify_bundle
from .journal_snapshot import _object, _read, committed_archives, load_journal_snapshot
from .review_export import _relative as _export_relative
from .settings_history import validate_checkpoint


def _hash(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _number(value):
  return value if type(value) is int and value >= 0 else None


def _sum(rows, key):
  values = [_number(row.get(key)) for row in rows]
  return sum(values) if values and None not in values else None


def _relative(value):
  return _export_relative(value).as_posix()


def _diagnostics(session, omissions):
  diagnostic = session.get("diagnostic")
  diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
  start, end = _number(diagnostic.get("window_start_ns")), _number(diagnostic.get("window_end_ns"))
  final = _number(diagnostic.get("finalized_through_ns"))
  invalid = diagnostic.get("invalidated") is True or session.get("diagnostic_invalidated") is True
  finalized = {"start_ns": start, "end_ns": final} if not invalid and None not in (start, end, final) and start <= final <= end else None
  tail = {"start_ns": max(start, final + 1) if finalized else start, "end_ns": end} if None not in (start, end) and start <= end and (finalized is None or final < end) else None
  axes = {}
  fields = ("sample_count", "eligible_count", "excluded_count", "provisional_count", "observed_duration_ns", "eligible_duration_ns")
  for axis in ("lateral", "longitudinal"):
    source = diagnostic.get("axes", {}).get(axis, {}).get("epochs", {})
    epochs = []
    if isinstance(source, dict):
      for index, row in enumerate(source.values()):
        if not isinstance(row, dict):
          continue
        epochs.append({"epoch_index": index, **{field: _number(row.get(field)) for field in fields},
                       "raw_metrics": deepcopy(row.get("raw_metrics")), "eligible_metrics": deepcopy(row.get("eligible_metrics"))})
    if epochs:
      axes[axis] = {**{field: _sum(epochs, field) for field in fields}, "epochs": epochs}
  if not diagnostic:
    omissions.append("diagnostic_unavailable")
  if invalid:
    omissions.append("latest_diagnostic_invalidated")
  if diagnostic.get("partial_window") is True:
    omissions.append("latest_diagnostic_partial")
  if finalized is None:
    omissions.append("finalized_range_unknown")
  if tail is not None:
    omissions.append("raw_tail_unfinalized")
  if not axes or any(axis["observed_duration_ns"] is None for axis in axes.values()):
    omissions.append("diagnostic_duration_unknown")
  history = session.get("history", [])
  return {"latest_revision": _number(diagnostic.get("revision")), "invalidated": invalid,
          "partial_window": diagnostic.get("partial_window") is True, "window_start_ns": start, "window_end_ns": end,
          "finalized_range": finalized, "raw_tail": tail, "axes": axes,
          "history_scope": "latest_only_overlapping_history_not_summed",
          "history_revisions": [row["revision"] for row in history if isinstance(row, dict) and _number(row.get("revision")) is not None][:6]}


def _requests(snapshot, exported, session_id, omissions):
  complete = isinstance(exported, dict) and exported.get("inventory_complete") is True
  rows = exported.get("requests", []) if isinstance(exported, dict) else snapshot.get("apply_requests", [])
  rows = rows if isinstance(rows, list) else []
  rows = [row for row in rows if isinstance(row, dict) and (session_id is None or row.get("session_id") == session_id)]
  if not complete:
    omissions.append("request_inventory_cached_only")
  if not isinstance(exported, dict) or exported.get("checkpoint") is None:
    omissions.append("request_ledger_unavailable")
  items = []
  for row in rows[-16:]:
    comparison = row.get("comparison")
    compact = None
    if isinstance(comparison, dict):
      matched = comparison.get("matched") or {}
      compact = {"state": comparison.get("state"), "scope": comparison.get("scope"),
        "before_rmse": matched.get("before", {}).get("rmse"), "after_rmse": matched.get("after", {}).get("rmse"),
        "rmse_delta": matched.get("after_minus_before", {}).get("rmse"), "consumer_application_ack": False,
        "causal_improvement_proven": False, "historical_only": True, "export_source_state": "unverified"}
    items.append({**{key: row.get(key) for key in ("request_id", "item_key", "scope", "state", "reason")},
      "storage_observed": isinstance(row.get("store_receipt"), dict), "consumer_observed": row.get("consumer_observed") is True,
      "comparison_ready": row.get("comparison_ready") is True, "comparison": compact})
  return {"count": len(rows), "inventory_scope": "ledger_snapshot" if complete else "cached_items_only",
    "state_counts": dict(sorted(Counter(str(row.get("state", "unknown")) for row in rows).items())),
    "stored_count": sum(isinstance(row.get("store_receipt"), dict) for row in rows),
    "consumer_observed_count": sum(row.get("consumer_observed") is True for row in rows),
    "comparison_ready_count": sum(row.get("comparison_ready") is True for row in rows), "items": items}


class ParkingSummaryService:
  def __init__(self, roots=None, *, request_exporter=None, clock_ns=time.monotonic_ns, max_summaries=64):
    if type(max_summaries) is not int or not 1 <= max_summaries <= 128:
      raise ValueError("invalid_summary_budget")
    if set(roots or {}) - {"evidence", "automatic", "requests"}:
      raise ValueError("summary_roots_invalid")
    self.roots = {}
    for key, value in (roots or {}).items():
      original = Path(value)
      if not original.is_absolute() or any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (original, *original.parents)):
        raise ValueError("summary_roots_invalid")
      self.roots[key] = original.resolve()
    self.request_exporter, self.clock_ns, self.maximum = request_exporter, clock_ns, max_summaries
    self._lock, self._records, self._latest, self._ids = threading.RLock(), OrderedDict(), {}, {}

  def freeze_cached(self, snapshot, trigger):
    """Call in the server background: metrics are cached; no raw log decoding."""
    if trigger not in {"parking", "offroad", "stop", "manual"} or not isinstance(snapshot, dict):
      raise ValueError("summary_trigger_invalid")
    session = snapshot.get("session") or {}
    exported = snapshot.get("request_export")
    if exported is None and self.request_exporter is not None:
      exported = self.request_exporter()
    exported = deepcopy(exported)
    omissions = []
    source_kind = session.get("metadata", {}).get("source_kind", session.get("source_mode") or "unknown")
    if source_kind not in {"live", "synthetic", "generated_native", "recorded", "unknown"}:
      source_kind = "unknown"
    parking = deepcopy(session.get("parking"))
    last_park = deepcopy(session.get("last_park_observation"))
    confirmed = (isinstance(parking, dict) and parking.get("source_kind") == "native_event"
                 and parking.get("state") == "park_observed" and session.get("parking_confirmed") is True)
    candidate = snapshot.get("candidate_report") or {}
    report = candidate.get("report") or {}
    summary = {"schema_version": 1, "session_id": session.get("session_id"), "trigger": trigger, "source_kind": source_kind,
      "state": "ready", "session_state": session.get("state", "unknown"), "historical_only": True,
      "parking": parking, "parking_confirmed": bool(confirmed), "offroad_state": parking.get("offroad_state", "unknown") if parking else "unknown",
      "last_park_observation": last_park, "diagnostics": _diagnostics(session, omissions),
      "requests": _requests(snapshot, exported, session.get("session_id"), omissions),
      "candidate": {"historical_only": True, "state": "unlinked", "profile_id": None,
        "result_revision": None, "candidate_count": 0},
      "source_quality": {"omissions": omissions, "settings_history_complete": False, "consumer_application_ack": False,
        "coverage_complete": False, "available_duration_scope": "latest_diagnostic_observation_timing",
        "collection_end_confirmation": "signal_only" if session.get("state") in {"stopped", "ended"} else "not_signaled"},
      "recommendations_enabled": False, "apply_enabled": False, "device_qualified": False}
    fingerprint = deepcopy(summary)
    for field in ("parking", "last_park_observation"):
      if isinstance(fingerprint[field], dict):
        fingerprint[field].pop("observed_mono_ns", None)
    if exported and exported.get("checkpoint"):
      fingerprint["request_ledger_sequence"] = exported["checkpoint"]["sequence"]
    fingerprint["settings_revision"] = session.get("recording", {}).get("settings", {}).get("revision")
    fingerprint["candidate_watermark"] = {"state": candidate.get("state"), "revision": candidate.get("result_revision"),
      "artifact_sha256": candidate.get("artifact_sha256"), "job_id": (candidate.get("last_success") or candidate.get("attempt") or {}).get("job_id")}
    digest, session_key = _hash(fingerprint), (session.get("session_id"), source_kind)
    with self._lock:
      previous = self._latest.get(session_key)
      if previous is not None and previous[1] not in self._records:
        self._latest.pop(session_key, None)
        self._ids.pop(session_key, None)
        previous = None
      if previous is not None and previous[0] == digest:
        return deepcopy(self._records[previous[1]]["summary"])
      identifier = self._ids.setdefault(session_key, uuid.uuid4().hex)
      revision = previous[1][1] + 1 if previous else 1
      summary.update(summary_id=identifier, revision=revision, created_mono_ns=self.clock_ns())
      bindings = self._capture_bindings(snapshot, exported, summary)
      if isinstance(exported, dict):
        # Full public comparisons are already represented by compact summary
        # metrics and the fixed ledger prefix; do not duplicate them 64 times.
        exported["requests"] = [{"request_id": row.get("request_id"), "session_id": row.get("session_id"),
          **({"comparison_details": [deepcopy(row["comparison"][side]["exclusions"]) for side in ("before", "after") if row["comparison"].get(side) is not None]}
             if (row.get("comparison") or {}).get("schema_version") == 3 else {})}
          for row in exported.get("requests", [])]
      self._records[(identifier, revision)] = {"summary": deepcopy(summary), "request_export": exported, "bindings": bindings}
      self._latest[session_key] = (digest, (identifier, revision))
      while len(self._records) > self.maximum:
        evicted, _ = self._records.popitem(last=False)
        for key, latest in list(self._latest.items()):
          if latest[1] == evicted:
            self._latest.pop(key)
            self._ids.pop(key, None)
      return deepcopy(summary)

  def _capture_bindings(self, snapshot, exported, summary):
    """Capture small immutable selectors now; selection must never reread latest."""
    references = []
    for row in (exported or {}).get("evidence_references", []):
      if row.get("session_id") != summary["session_id"]:
        continue
      token = row.get("reference", {}).get("checkpoint")
      if token is not None:
        try:
          validate_checkpoint(token)
        except ValueError:
          summary["source_quality"]["omissions"].append("request_checkpoint_unavailable")
          continue
      if token is not None and token.get("session_id") != summary["session_id"]:
        summary["source_quality"]["omissions"].append("request_checkpoint_session_mismatch")
        continue
      references.append(deepcopy(row))
    candidate = snapshot.get("candidate_report") or {}
    root = self.roots.get("automatic")
    if root is not None and (root / "latest.json").exists():
      candidate_begin = len(references)
      try:
        pointer = _object(_read(root / "latest.json", 16 * 1024))
        expected = candidate.get("last_success") or candidate.get("attempt") or {}
        if (pointer.get("result_revision") != candidate.get("result_revision")
            or pointer.get("job_id") != expected.get("job_id") or not re.fullmatch(r"[a-f0-9]{64}-[a-f0-9]{8}", pointer.get("directory", ""))):
          raise ValueError("summary_candidate_reference_mismatch")
        job = root / "jobs" / pointer["directory"]
        request = _object(_read(job / "request.json", 64 * 1024))
        token = validate_checkpoint(request.get("identity", {}).get("checkpoint"))
        if token["session_id"] != summary["session_id"]:
          summary["candidate"].update(state="previous_session", candidate_count=0, profile_id=None, result_revision=None)
          raise ValueError("summary_candidate_previous_session")
        result_bytes = _read(job / "job-result.json", 64 * 1024)
        result = _object(result_bytes)
        if (hashlib.sha256(result_bytes).hexdigest() != pointer.get("manifest_sha256") or result.get("job_id") != pointer["job_id"]
            or request.get("job_id") != pointer["job_id"] or _hash(request.get("identity")) != pointer["job_id"]
            or type(result.get("windows")) is not list or len(result["windows"]) > 8):
          raise ValueError("summary_candidate_reference_mismatch")
        ready = [window for window in result["windows"] if window["state"] == "ready"]
        if not ready:
          raise ValueError("summary_candidate_reference_unavailable")
        for index_number, window in enumerate(ready):
          name = _relative(window["directory"])
          index = _object(_read(job / name / "index.json", 256 * 1024))
          bundle = _relative(index["latest"])
          artifact = hash_file(job / name / bundle / "result.json", 1024 * 1024)[0]
          if index_number == len(ready) - 1 and candidate.get("artifact_sha256") is not None and artifact != candidate["artifact_sha256"]:
            raise ValueError("summary_candidate_artifact_mismatch")
          references.append({"session_id": summary["session_id"], "artifact_sha256": artifact,
            "reference": {"kind": "periodic", "periodic_directory": pointer["directory"], "window_directory": name,
                          "bundle_directory": bundle, "checkpoint": request["identity"]["checkpoint"]}})
        report = candidate.get("report") or {}
        summary["candidate"].update(state=candidate.get("state", "unavailable"), profile_id=report.get("profile_id"),
          result_revision=candidate.get("result_revision"),
          candidate_count=sum(row.get("status") == "search_candidate" for row in report.get("items", []) if isinstance(row, dict)))
        if candidate.get("calibration_review") is not None:
          summary["candidate"]["calibration_artifact_sha256"] = candidate["artifact_sha256"]
      except (OSError, ValueError, KeyError, TypeError) as error:
        del references[candidate_begin:]
        summary["source_quality"]["omissions"].append("candidate_previous_session" if str(error) == "summary_candidate_previous_session" else "candidate_reference_unavailable")
    if not self.roots:
      summary["source_quality"]["omissions"].append("source_roots_unconfigured")
    if summary["candidate"]["state"] == "unlinked" and candidate.get("report") is not None:
      summary["source_quality"]["omissions"].append("candidate_reference_unavailable")
    for comparison in (exported or {}).get("comparison_references", []):
      if comparison.get("session_id") != summary["session_id"]:
        continue
      matching = False
      for binding in references:
        token = binding["reference"].get("checkpoint")
        if token is not None:
          try:
            from .journal_snapshot import checkpoint_reference
            root = self.roots["automatic"] / "jobs" / _relative(binding["reference"]["periodic_directory"]) / "snapshot"
            matching = comparison["history_reference"] == checkpoint_reference(root, token)
          except (KeyError, OSError, ValueError):
            matching = False
          if matching:
            break
      if not matching:
        summary["source_quality"]["omissions"].append("comparison_after_source_reference_unavailable")
    return references

  def selection(self, summary_id, revision, *, cancelled=None, deadline=None):
    """Heavy export preparation, on a background thread, for exactly one version."""
    with self._lock:
      stored = self._records.get((summary_id, revision))
      if stored is None:
        raise LookupError("summary_revision_not_found")
      frozen = deepcopy(stored)
    summary, files, checkpoints, expected, reasons = frozen["summary"], {}, [], [], []
    end_time = time.monotonic() + 30 if deadline is None else deadline
    read_bytes = 0
    max_read_bytes = (512 if any((binding.get("reference", {}).get("checkpoint") or {}).get("schema_version") == 2
      for binding in frozen["bindings"]) else 64) * 1024 * 1024
    def check():
      if cancelled is not None and cancelled():
        raise InterruptedError("summary_selection_cancelled")
      if time.monotonic() >= end_time:
        raise TimeoutError("summary_selection_deadline")
    def charge(size):
      nonlocal read_bytes
      check()
      read_bytes += size
      if read_bytes > max_read_bytes:
        raise ValueError("summary_selection_read_budget")
    def stop_hash():
      check()
      return False
    def prefix_hash(path, size):
      digest, remaining = hashlib.sha256(), size
      with path.open("rb") as stream:
        while remaining:
          check()
          block = stream.read(min(64 * 1024, remaining))
          if not block:
            raise ValueError("summary_history_prefix_truncated")
          digest.update(block)
          remaining -= len(block)
      return digest.hexdigest()
    def bind_checkpoint(kind, destination, token):
      binding = {"kind": kind, "path": destination, "token": deepcopy(token)}
      old = next((row for row in checkpoints if row["path"] == destination), None)
      if old is not None and old != binding:
        raise ValueError("summary_export_checkpoint_conflict")
      if old is None:
        if len(checkpoints) >= 32:
          raise ValueError("summary_selection_checkpoint_budget")
        checkpoints.append(binding)
    def add(root_id, relative, destination, role, sensitivity="private_context", *, checkpoint=None, prefix_info=None):
      check()
      relative, destination = _relative(relative), _relative(destination)
      root = self.roots.get(root_id)
      if root is None:
        raise ValueError("summary_source_root_unavailable")
      path = _regular_file(root / relative)
      if not path.resolve().is_relative_to(root):
        raise ValueError("summary_source_root_escape")
      if destination in files:
        return
      if len(files) >= 512:
        raise ValueError("summary_selection_file_budget")
      charge(checkpoint["bytes"] if checkpoint else prefix_info["bytes"] if prefix_info is not None else path.stat().st_size)
      if checkpoint is not None:
        if role == "request_ledger":
          from .apply_requests import verify_request_prefix
          verified = verify_request_prefix(path, checkpoint)
          digest, size = verified["raw_sha256"], verified["bytes"]
          kind = "requests"
        else:
          size, kind = checkpoint["bytes"], "settings"
          digest = prefix_hash(path, size)
        bind_checkpoint(kind, destination, checkpoint)
      elif prefix_info is not None:
        size = prefix_info["bytes"]
        digest = prefix_hash(path, size)
        if digest != prefix_info["sha256"]:
          raise ValueError("summary_history_prefix_changed")
      else:
        digest, size = hash_file(path, 64 * 1024 * 1024, stop_hash)
      item = {"root_id": root_id, "relative_path": relative, "export_relative_path": destination, "sha256": digest, "bytes": size,
              "role": role, "sensitivity": sensitivity, "copy_mode": "prefix" if checkpoint or prefix_info is not None else "whole"}
      if destination in files and files[destination] != item:
        raise ValueError("summary_export_destination_conflict")
      files[destination] = item
    exported = frozen["request_export"] or {}
    current_requests = [row for row in exported.get("requests", []) if summary["session_id"] is None or row.get("session_id") == summary["session_id"]]
    for request in current_requests:
      details = request.get("comparison_details", [])
      if not details:
        continue
      previous_files = dict(files)
      try:
        from .comparison_stream import validate_interval_reference, read_interval_page
        for detail in details:
          ref = validate_interval_reference(detail)
          root = self.roots["automatic"]
          manifest_path = _relative(ref["path"])
          interval_manifest = _object(_read(root / manifest_path, 32 * 1024))
          if hash_file(root / manifest_path, 32 * 1024)[0] != ref["sha256"]:
            raise ValueError("summary_interval_manifest_changed")
          if (interval_manifest.get("kind") != "comparison_exclusion_pages" or interval_manifest.get("schema_version") != 1
              or len(interval_manifest.get("pages", [])) != ref["page_count"]
              or interval_manifest.get("interval_count") != ref["interval_count"]):
            raise ValueError("summary_interval_manifest_changed")
          add("automatic", manifest_path, manifest_path, "comparison_exclusion_manifest")
          for index, page in enumerate(interval_manifest["pages"]):
            check()
            read_interval_page(root, ref, index)
            relative = _relative((Path(manifest_path).parent / page["path"]).as_posix())
            add("automatic", relative, relative, "comparison_exclusion_page")
            if any(files[relative][key] != page[key] for key in ("sha256", "bytes")):
              raise ValueError("summary_interval_page_changed")
      except (OSError, ValueError, KeyError, TypeError):
        files = previous_files
        reasons.append("comparison_interval_details_unavailable")
    if exported.get("checkpoint") is not None and current_requests:
      try:
        add("requests", exported["ledger_relative_path"], "requests/requests.jsonl", "request_ledger", checkpoint=exported["checkpoint"])
      except (OSError, ValueError, KeyError):
        check()
        reasons.append("request_ledger_unavailable")
    seen, processed_bindings = set(), set()
    for binding in frozen["bindings"]:
      check()
      previous_files, previous_checkpoints, previous_expected = dict(files), list(checkpoints), len(expected)
      try:
        reference = binding["reference"]
        if reference["kind"] != "periodic":
          reasons.append("static_candidate_reference_unavailable")
          continue
        job_name = _relative(reference["periodic_directory"])
        window_name, bundle_name = _relative(reference["window_directory"]), _relative(reference["bundle_directory"])
        binding_key = (job_name, window_name, bundle_name, binding["artifact_sha256"])
        if binding_key in processed_bindings:
          continue
        processed_bindings.add(binding_key)
        job = self.roots["automatic"] / "jobs" / job_name
        bundle = job / window_name / bundle_name
        charge(8 * 1024 * 1024)
        manifest = verify_bundle(bundle, byte_limit=8 * 1024 * 1024, max_files=3)
        if hash_file(bundle / "result.json", 1024 * 1024, stop_hash)[0] != binding["artifact_sha256"]:
          raise ValueError("summary_candidate_artifact_changed")
        base = f"jobs/{job_name}"
        token = reference["checkpoint"]
        segmented = token.get("schema_version") == 2
        names = ("request.json", "job-result.json", "snapshot/recording.json", "snapshot/snapshot.json") if segmented else (
          "request.json", "job-result.json", "snapshot/recording.json", "snapshot/settings.jsonl", "snapshot/snapshot.json")
        for name in names:
          history_file = name.endswith("settings.jsonl")
          add("automatic", f"{base}/{name}", f"{base}/{name}", "settings_history" if history_file or segmented and name.endswith("snapshot.json") else "candidate_context",
              checkpoint=token if history_file else None)
        selected = f"{base}/{window_name}/{bundle_name}"
        for name in ("bundle.json", "result.json", *(row["path"] for row in manifest["files"])):
          add("automatic", f"{selected}/{name}", f"{selected}/{name}", "candidate_context")
        session_id = token["session_id"]
        if session_id != summary["session_id"] or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id) is None:
          raise ValueError("summary_session_reference_mismatch")
        if not segmented:
          charge(3 * token["bytes"] + 256 * 1024)
        else:
          marker = _object(_read(job / "snapshot" / "snapshot.json", 64 * 1024))
          selected_bytes = marker.get("history_byte_count")
          limit = 512 * 1024 * 1024 if marker.get("schema_version") == 3 else 32 * 1024 * 1024
          if type(selected_bytes) is not int or not 0 <= selected_bytes <= limit:
            raise ValueError("summary_history_selection_budget")
          charge(3 * selected_bytes + 256 * 1024)
        sealed = load_journal_snapshot(self.roots["evidence"] / session_id, job / "snapshot")
        if segmented and sealed["history_byte_count"] != selected_bytes:
          raise ValueError("summary_history_selection_changed")
        if sealed["marker"]["checkpoint"] != token:
          raise ValueError("summary_checkpoint_reference_changed")
        if segmented:
          for history_file in sealed["history_files"]:
            relative = _relative(history_file["path"])
            add("automatic", f"{base}/snapshot/{relative}", f"{base}/snapshot/{relative}", "settings_segment", prefix_info=history_file)
          inventory = sealed["marker"].get("pid_burst_inventory") or {}
          chosen = inventory.get("burst_ids")
          for entry in sealed["history"]["records"]:
            if entry["record"].get("type") != "pid_burst_chunk":
              continue
            ref = entry["record"]["reference"]
            if chosen is not None and ref["burst_id"] not in chosen:
              continue
            relative = _relative(ref["path"])
            add("automatic", f"{base}/snapshot/{relative}", f"{base}/snapshot/{relative}", "pid_cycle_evidence")
          bind_checkpoint("segmented_settings", f"{base}/snapshot/snapshot.json", token)
          reasons.append("history_complete_segment_collection" if sealed["marker"].get("schema_version") == 3 else "history_selected_closure_only")
          if (sealed.get("selection") or {}).get("omitted_sequence_ranges"):
            reasons.append("history_older_ranges_omitted")
        if (session_id, token["sequence"], token["chain_sha256"]) in seen:
          continue
        prefix = f"sessions/{session_id}/checkpoints/{token['sequence']}-{token['chain_sha256'][:16]}"
        if not segmented:
          add("evidence", f"{session_id}/settings.jsonl", f"{prefix}/settings.jsonl", "settings_history", checkpoint=token)
        add("evidence", f"{session_id}/recording.json", f"sessions/{session_id}/recording.json", "session_metadata")
        for diagnostic in sealed["history"]["diagnostic_references"].values():
          name = f'{diagnostic["revision"]}-{diagnostic["sha256"][:16]}.json'
          add("evidence", f"{session_id}/diagnostics/{name}", f"sessions/{session_id}/diagnostics/{name}", "diagnostic")
        for identity, receipt in committed_archives(sealed["history"]).items():
          if any(row["archive_id"] == identity for row in expected):
            continue
          item = {"archive_id": identity, "route": receipt["route"], "segment": receipt["segment"], "state": "unavailable",
                  "reason": "archive_source_unavailable", "sha256": receipt["sha256"], "bytes": receipt["bytes"]}
          expected.append(item)
          try:
            check()
            relative = f"{session_id}/archive/{identity}/archive.json"
            metadata = _object(_read(self.roots["evidence"] / relative, 64 * 1024))
            if hash_file(self.roots["evidence"] / relative, 64 * 1024)[0] != receipt["manifest_sha256"]:
              raise ValueError("summary_archive_manifest_changed")
            add("evidence", relative, f"sessions/{session_id}/archive/{identity}/archive.json", "archive_manifest")
            raw = metadata["file"]["path"]
            raw_destination = f"sessions/{session_id}/archive/{identity}/{raw}"
            raw_relative = _relative(f"{session_id}/archive/{identity}/{raw}")
            raw_path = _regular_file(self.roots["evidence"] / raw_relative)
            if raw_path.stat().st_size != receipt["bytes"]:
              raise ValueError("summary_archive_size_changed")
            files[raw_destination] = {"root_id": "evidence", "relative_path": raw_relative, "export_relative_path": raw_destination,
              "sha256": receipt["sha256"], "bytes": receipt["bytes"], "role": "original_rlog", "sensitivity": "private_raw", "copy_mode": "whole"}
            item.update(state="declared", reason="copy_time_verification_required")
          except (OSError, ValueError, KeyError):
            check()
            reasons.append("raw_archive_unavailable")
        seen.add((session_id, token["sequence"], token["chain_sha256"]))
      except (OSError, ValueError, KeyError, TypeError) as error:
        check()
        files, checkpoints = previous_files, previous_checkpoints
        del expected[previous_expected:]
        code = str(error)
        if code.startswith("summary_selection_") and re.fullmatch(r"[a-z_]{1,128}", code):
          reasons.append(code)
        reasons.append("frozen_candidate_source_unavailable")
    if not checkpoints:
      reasons.append("settings_checkpoint_unavailable")
    if not expected:
      reasons.append("raw_archives_unavailable")
    check()
    return {"schema_version": 1, "summary_id": summary_id, "summary_revision": revision, "session_id": summary["session_id"],
      "source_kind": summary["source_kind"], "historical_only": True, "summary": summary, "checkpoints": checkpoints,
      "files": list(files.values()), "source_quality": {"summary": summary["source_quality"], "freeze_omissions": sorted(set(reasons))},
      "expected_inventory": expected}
