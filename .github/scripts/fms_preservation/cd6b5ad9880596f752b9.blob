"""Whole-trip native inspection with bounded, deterministic time selection.

Original archives remain authoritative. SQLite is disposable analysis scratch;
every native anchor is masked before a selected row can reach the calculator.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .analysis import Observation, _error
from .candidate_report import MAX_INPUT_BYTES, _read
from .candidates import candidate_capabilities
from .journal_snapshot import create_journal_snapshot, load_journal_snapshot
from .log_candidates import _objectives, _static_projection, calculate_log_candidates
from .log_history import load_log_history
from .log_signals import extract_log_signals


def _json(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
  return hashlib.sha256(_json(value).encode()).hexdigest()


def _in_windows(stamp, start, end):
  """Thirty fixed time strata, each centred on at most ten seconds."""
  if not start <= stamp <= end:
    return False
  span = end - start + 1
  if span <= 300_000_000_000:
    return True
  width = span / 30
  index = min(29, int((stamp - start) / width))
  centre = start + (index + .5) * width
  return abs(stamp - centre) <= 5_000_000_000


def _base_mask(item, state, profile):
  """The forward portion of analysis._mask_axis, with explicit durable state."""
  reasons = set()
  previous = state.get("previous")
  if previous is None or item.epoch != previous[1]:
    state.update(stable_since=None, epoch_start=item.mono_ns)
  if previous is not None and item.mono_ns - previous[0] > profile.max_gap_ns:
    reasons.add("signal_gap")
  if not item.active:
    reasons.add("control_inactive")
  if not item.valid:
    reasons.add("invalid_signal")
  if not item.recovery_ready:
    reasons.add("recovery_not_ready")
  if item.context.get("saturated") is True or item.context.get("output_limited") is True:
    reasons.add("output_limited")
  if item.driver_override is None:
    reasons.add("intervention_unknown")
  elif item.driver_override:
    reasons.add("driver_intervention")
  _, missing = _error(item)
  if missing:
    reasons.add(missing)
  if reasons:
    state["stable_since"] = None
  else:
    if state.get("stable_since") is None:
      state["stable_since"] = item.mono_ns
    if item.mono_ns - state["stable_since"] < profile.recovery_ns:
      reasons.add("recovery")
  state["previous"] = [item.mono_ns, item.epoch]
  return sorted(reasons), state["epoch_start"]


def _finalize_masks(database, profile, finalized):
  """Complete pre-margin and dependency masks, including file boundaries."""
  database.execute("UPDATE obs SET pre=EXISTS(SELECT 1 FROM obs d WHERE d.axis=obs.axis AND d.driver=1 AND d.stamp>=obs.stamp AND d.stamp<=obs.stamp+?) WHERE driver IS NOT 1", (profile.pre_margin_ns,))
  counts = Counter()
  for axis in ("lateral", "longitudinal"):
    previous_unsafe_end = -1
    cursor = database.execute("SELECT stamp,reasons,pre,epoch_start,lead(stamp,1,stamp+1) OVER (ORDER BY stamp) FROM obs WHERE axis=? ORDER BY stamp", (axis,))
    for stamp, encoded, pre, epoch_start, next_stamp in cursor:
      reasons = set(json.loads(encoded))
      if pre:
        reasons.add("intervention_pre_margin")
      if reasons:
        previous_unsafe_end = max(previous_unsafe_end, next_stamp, stamp + ("signal_gap" in reasons))
      elif profile.dependency_ns:
        if stamp - profile.dependency_ns < epoch_start:
          reasons.add("dependency_unobserved")
        if previous_unsafe_end > stamp - profile.dependency_ns:
          reasons.add("dependency_window")
      if stamp > finalized:
        reasons.add("unfinalized_tail")
      counts.update(reasons)
      database.execute("UPDATE obs SET final=? WHERE axis=? AND stamp=?", (_json(sorted(reasons)), axis, stamp))
  database.commit()
  return dict(counts)


def run_trip_analysis(session_directory, profile_path, objectives_path, destination, intent, checkpoint,
                      *, cancelled=None, soft_deadline_seconds=75):
  began = time.monotonic()
  destination = Path(destination)
  destination.mkdir(parents=True, exist_ok=True)
  job = destination.parent
  start, end = intent["range_start_ns"], intent["requested_end_ns"]
  if type(start) is not int or type(end) is not int or not 0 <= start < end:
    raise ValueError("invalid_trip_range")
  profile, profile_hash = _read(profile_path, MAX_INPUT_BYTES)
  config, objectives_hash = _read(objectives_path, 64 * 1024)
  if (profile_hash != intent["profile_sha256"] or objectives_hash != intent["objectives_sha256"]):
    raise ValueError("trip_configuration_changed")
  mask = _objectives(config, candidate_capabilities())
  pid_mode = all(spec["reference"]["type"] == "recorded_pid_target" for spec in config["objectives"].values())
  snapshot = job / "snapshot"
  if snapshot.exists():
    sealed = load_journal_snapshot(session_directory, snapshot)
    if sealed["marker"]["checkpoint"] != checkpoint:
      # Retain the old immutable prefix when the last open archive closes.
      snapshot.rename(job / ("snapshot-" + str(sealed["marker"]["checkpoint"]["sequence"])))
  if not snapshot.exists():
    try:
      snap = create_journal_snapshot(session_directory, snapshot, checkpoint, analysis_range={"start_ns": start, "end_ns": end})
    except ValueError as error:
      if str(error) != "snapshot_no_committed_archive":
        raise
      if snapshot.exists():
        snapshot.rename(job / ("snapshot-uncommitted-" + str(checkpoint["sequence"])))
      return {"state": "continuation", "reason": "waiting_for_closed_archive", "source_start_ns": None,
        "source_end_ns": None, "source_count": 0, "processed_source_count": 0, "cursor": 0,
        "manifest_reference": None, "candidate_directory": None}
  else:
    from .journal_snapshot import _archive_summary
    sealed = load_journal_snapshot(session_directory, snapshot)
    if sealed["marker"]["schema_version"] == 3:
      from .trip_history import trip_archive_summary
      snap = trip_archive_summary(Path(session_directory), snapshot, checkpoint, sealed["history"])
    else:
      snap = _archive_summary(Path(session_directory), snapshot, checkpoint, sealed["history"], sealed["archive_receipts"])
  sources = sorted((row for row in snap["archive_sources"] if row["start_ns"] <= end and row["end_ns"] >= max(0, start - mask.dependency_ns - mask.pre_margin_ns)), key=lambda row: row["start_ns"])
  source_refs = [{"id": "log-" + row["sha256"][:16], **{key: row[key] for key in ("sha256", "bytes", "route", "segment")}} for row in sources]
  if len(sources) > 128 or sum(row["bytes"] for row in sources) > 512 * 1024 * 1024:
    raise ValueError("trip_archive_budget_exceeded")
  history = load_log_history(session_directory, source_refs, session_snapshot=snapshot) if sources else {}
  pid_windows = {}
  for reference in history.get("pid_chunk_refs", []):
    span = pid_windows.setdefault(reference["burst_id"], [reference["mono_start_ns"], reference["mono_end_ns"]])
    span[0], span[1] = min(span[0], reference["mono_start_ns"]), max(span[1], reference["mono_end_ns"])
  database = sqlite3.connect(destination / "progress.sqlite3")
  database.execute("PRAGMA cache_size=-8192")
  database.executescript("CREATE TABLE IF NOT EXISTS state(value TEXT); CREATE TABLE IF NOT EXISTS obs(stamp INTEGER,axis TEXT,driver INTEGER,reasons TEXT,epoch_start INTEGER,pre INTEGER DEFAULT 0,final TEXT,PRIMARY KEY(axis,stamp)); CREATE INDEX IF NOT EXISTS intervention ON obs(axis,driver,stamp); CREATE TABLE IF NOT EXISTS selected(stamp INTEGER PRIMARY KEY,row TEXT);")
  saved = database.execute("SELECT value FROM state").fetchone()
  state = json.loads(saved[0]) if saved else {"intent": intent, "sources": [], "stream": None, "masks": {}, "receipts": [], "snapshots": [], "fields": {}, "last_bucket": None, "raw_count": 0}
  if state["intent"] != intent or source_refs[:len(state["sources"])] != state["sources"]:
    database.close()
    raise ValueError("trip_checkpoint_binding_changed")
  def outcome(status, reason):
    expected_count = len([row for row in snap["expected_archive_sources"] if row["start_ns"] is None
      or row["start_ns"] <= end and row["end_ns"] >= start])
    return {"state": status, "reason": reason, "source_start_ns": start, "source_end_ns": end,
      "source_count": max(len(sources), expected_count), "processed_source_count": len(state["sources"]), "cursor": len(state["sources"]),
      "manifest_reference": {"sha256": _digest(state["sources"]), "source_count": len(sources)},
      "candidate_directory": str(job / "window-0000") if status == "complete" else None}
  try:
    missing = [row for row in snap["expected_archive_sources"] if row["state"] != "available" and
               (row["start_ns"] is None or row["start_ns"] <= end and row["end_ns"] >= start)]
    if missing:
      return outcome("partial", "trip_source_coverage_incomplete")
    if any(b["segment"] != a["segment"] + 1 or b["route"] != a["route"] or
           b["start_ns"] - a["end_ns"] > mask.max_gap_ns
           for a, b in zip(sources, sources[1:])):
      return outcome("partial", "trip_middle_source_missing_or_gap")
    for number in range(len(state["sources"]), len(sources)):
      if cancelled is not None and cancelled():
        raise InterruptedError("trip_analysis_cancelled")
      source = sources[number]
      def sink(row, observations):
        state["raw_count"] += 1
        for raw in observations:
          item = Observation(**raw)
          reasons, epoch_start = _base_mask(item, state["masks"].setdefault(item.axis, {}), mask)
          database.execute("INSERT INTO obs(stamp,axis,driver,reasons,epoch_start) VALUES(?,?,?,?,?)",
                           (item.mono_ns, item.axis, item.driver_override, _json(reasons), epoch_start))
        period = 10_000_000 if pid_mode else 200_000_000
        bucket = row["mono_ns"] // period
        selected = any(lo - 2_100_000_000 <= row["mono_ns"] <= hi + 2_100_000_000 for lo, hi in pid_windows.values()) if pid_mode else _in_windows(row["mono_ns"], start, end)
        if selected and state["last_bucket"] != bucket:
          database.execute("INSERT INTO selected VALUES(?,?)", (row["mono_ns"], _json(row)))
          state["last_bucket"] = bucket
      extracted = extract_log_signals([Path(source["path"])], analysis_profile=mask,
        budget={"max_events": 200000, "max_samples": 100000, "max_decompressed_bytes": 256 * 1024 * 1024,
                "max_input_bytes": 256 * 1024 * 1024, "max_event_bytes": 8 * 1024 * 1024,
                "max_age_ns": min(mask.max_gap_ns, profile["max_lag_ns"])}, cancelled=cancelled, continuous=True,
        source_identities={source["sha256"]: {key: source[key] for key in ("route", "segment")}},
        static_projection=None if pid_mode else _static_projection(config, candidate_capabilities()), include_pid_inputs=pid_mode,
        stream_sink=sink, stream_state=state["stream"])
      actual = extracted["source_receipts"][0]
      if (actual["sha256"] != source["sha256"] or actual["bytes"] != source["bytes"]
          or actual["mono_min_ns"] != source["start_ns"] or actual["mono_max_ns"] != source["end_ns"]):
        raise ValueError("trip_decoded_source_coverage_mismatch")
      state["stream"] = extracted["stream_state"]
      state["sources"].append(source_refs[number])
      state["receipts"].extend(extracted["source_receipts"])
      state["snapshots"].extend(extracted["initial_snapshots"])
      state["fields"] = extracted["source_fields"]
      database.execute("DELETE FROM state")
      database.execute("INSERT INTO state VALUES(?)", (_json(state),))
      database.commit()
      if (destination / "progress.sqlite3").stat().st_size > 512 * 1024 * 1024:
        raise ValueError("trip_scratch_byte_limit")
      if time.monotonic() - began >= soft_deadline_seconds:
        return outcome("continuation", "trip_sources_remaining")
    missing = [row for row in snap["expected_archive_sources"] if row["state"] != "available" and
               (row["start_ns"] is None or row["start_ns"] <= end and row["end_ns"] >= start)]
    if missing:
      return outcome("partial", "trip_source_coverage_incomplete")
    if not sources or sources[-1]["end_ns"] + mask.max_gap_ns < end:
      return outcome("continuation", "waiting_for_closed_archive")
    if sources[0]["start_ns"] > start + mask.max_gap_ns:
      return outcome("partial", "trip_source_coverage_incomplete")
    if any(b["segment"] != a["segment"] + 1 or b["route"] != a["route"] or
           b["start_ns"] - a["end_ns"] > mask.max_gap_ns
           for a, b in zip(sources, sources[1:])):
      return outcome("partial", "trip_middle_source_missing_or_gap")
    finalized = min(end, sources[-1]["end_ns"] - mask.pre_margin_ns - mask.dependency_ns)
    counts = _finalize_masks(database, mask, finalized)
    rows = []
    for stamp, encoded in database.execute("SELECT stamp,row FROM selected ORDER BY stamp"):
      row = json.loads(encoded)
      row["excluded_reasons"] = {axis: json.loads(reasons) for axis, reasons in database.execute("SELECT axis,final FROM obs WHERE stamp=?", (stamp,))}
      rows.append(row)
    if not rows:
      return outcome("partial", "trip_anchor_samples_unavailable")
    # Preserve contiguous local consumer runs around selected time strata. All
    # original events remain in the snapshot; gaps remain visible to the engine.
    def selected_event(event):
      stamp = event["produced_mono_ns"]
      return any(_in_windows(stamp + offset, start, end) for offset in (-2_000_000_000, 0, 2_000_000_000))
    for epoch in history.get("epochs", []):
      epoch["consumer_evidence"] = [event for event in epoch["consumer_evidence"] if selected_event(event)]
    for receipt in state["receipts"]:
      receipt["finalized_through_ns"] = min(receipt["mono_max_ns"], finalized)
    manifest = {"schema_version": 1, "kind": "whole_trip_analysis", "intent": intent,
      "sources": state["sources"], "raw_anchor_count": state["raw_count"], "selected_anchor_count": len(rows),
      "selection_policy": "thirty_time_strata_centred_ten_seconds_native_5hz_v1" if not pid_mode else "complete_burst_each_frozen_time_third_v1",
      "full_rate_excluded_counts": counts, "finalized_through_ns": finalized,
      "source_range": {"start_ns": start, "end_ns": end}, "causal_improvement_proven": False}
    (destination / "trip-manifest.json").write_text(_json(manifest), encoding="utf8")
    prepared = {"extracted": {"rows": rows, "source_receipts": state["receipts"], "initial_snapshots": state["snapshots"],
      "source_fields": state["fields"], "limitations": ["whole_trip_full_rate_mask_time_stratified_candidates"],
      "join_max_age_ns": min(mask.max_gap_ns, profile["max_lag_ns"]), "mask_version": mask.profile_id},
      "history": history, "manifest_sha256": _digest(manifest), "manifest": manifest}
    calculate_log_candidates([Path(row["path"]) for row in sources], profile_path, objectives_path, job / "window-0000",
      session_directory=session_directory, session_snapshot=snapshot, cancelled=cancelled, continuous=True, _prepared=prepared)
    return outcome("complete", None)
  finally:
    database.close()
