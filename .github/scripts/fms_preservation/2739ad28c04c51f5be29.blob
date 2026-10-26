"""Link closed session evidence to conservative stored-setting observation brackets.

These brackets witness sampled co-origin; they prove neither uninterrupted
settings nor consumer acknowledgement. No current Params value is consulted.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from .consumer_observer import CONSUMER_PATHS, OBSERVER_PATH
from .evidence import _regular_file, hash_file
from .inspection import inspect_session
from .journal_snapshot import _object, _read, _version, load_journal_snapshot
from .settings_history import KEYS, _canonical, _number, _valid_route_observation, load_history


DEFAULT_BUDGET = {"max_bytes": 512 * 1024 * 1024, "max_segments": 128,
                  "max_history_bytes": 32 * 1024 * 1024, "max_events": 40_000}


def _observations(records, *, selected=False):
  result, settings, previous_poll, revision, previous_digest, group = [], None, 0, 0, None, 0
  for envelope in records:
    record = envelope["record"]
    if record["type"] != "settings_observation":
      continue
    poll, current = record.get("poll_sequence"), record.get("revision")
    segmented = envelope.get("reference", {}).get("schema_version") == 2
    bootstrap = selected and segmented and (not previous_poll or poll != previous_poll + 1)
    if bootstrap:
      settings, previous_digest = None, None
      group += 1
      if "settings" not in record:
        continue
    observation, route = record.get("observation"), record.get("route_observation")
    if (type(poll) is not int or not bootstrap and poll != previous_poll + 1 or type(current) is not int
        or not bootstrap and current not in (revision, revision + 1) or current < 1
        or not isinstance(observation, dict) or not _valid_route_observation(route)):
      raise ValueError("settings_observation_invalid")
    begin, end = observation.get("begin_ns"), observation.get("end_ns")
    if (type(begin) is not int or type(end) is not int or not 0 <= begin <= end
        or type(observation.get("gap")) is not bool
        or not begin <= route["begin_ns"] <= route["end_ns"] <= end):
      raise ValueError("settings_observation_time_invalid")
    if "settings" in record:
      settings = record["settings"]
      if not isinstance(settings, dict) or set(settings) != set(KEYS):
        raise ValueError("settings_snapshot_invalid")
      for entry in settings.values():
        if not isinstance(entry, dict) or entry.get("status") not in {"observed", "invalid", "missing", "error", "unavailable"}:
          raise ValueError("settings_entry_invalid")
        raw, value = entry.get("raw"), entry.get("value")
        if raw is not None and (not isinstance(raw, str) or len(raw.encode("utf-8")) > 256 or _number(raw) != value):
          raise ValueError("settings_raw_invalid")
        if entry["status"] == "observed" and (raw is None or type(value) not in (int, float)):
          raise ValueError("settings_value_unobserved")
        if entry["status"] != "observed" and value is not None:
          raise ValueError("settings_unknown_value")
        a, b = entry.get("begin_ns"), entry.get("end_ns")
        if type(a) is not int or type(b) is not int or not begin <= a <= b <= end:
          raise ValueError("settings_read_time_invalid")
    elif current != revision:
      raise ValueError("settings_revision_without_snapshot")
    if settings is None:
      raise ValueError("settings_snapshot_missing")
    content = {key: {field: entry[field] for field in ("status", "raw", "value")} for key, entry in settings.items()}
    fingerprint_data = {"source_status": record.get("source_status"), "settings": content,
                        "route": {key: route[key] for key in ("route", "state")}}
    if "identity_version" in record or "vehicle_identity" in record:
      from .vehicle_identity import validate_identity
      if type(record.get("identity_version")) is not int or record["identity_version"] != 1:
        raise ValueError("settings_vehicle_identity_invalid")
      fingerprint_data.update(identity_version=1, vehicle_identity=validate_identity(record.get("vehicle_identity")))
    digest = hashlib.sha256(_canonical(fingerprint_data)).hexdigest()
    if digest != record.get("settings_sha256"):
      raise ValueError("settings_snapshot_hash_mismatch")
    changed = previous_digest != digest or observation["gap"]
    refresh = record.get("snapshot_refresh") is True
    if refresh and (not segmented or "settings" not in record):
      raise ValueError("settings_refresh_invalid")
    if (not bootstrap and (current != revision + int(changed)
        or ("settings" in record) != (changed or refresh) or refresh and changed)):
      raise ValueError("settings_revision_transition_invalid")
    raw_values = {key: int(entry["raw"].strip()) if entry["status"] == "observed"
                  and re.fullmatch(r"[+-]?\d+", entry["raw"].strip()) else None for key, entry in settings.items()}
    result.append({"record": record, "reference": deepcopy(envelope.get("reference")) if segmented else {k: envelope[k] for k in ("sequence", "sha256", "bytes")},
                   "settings_raw": raw_values, "continuity_group": group})
    previous_poll, revision, previous_digest = poll, current, digest
  return result


def _right_bracket_missing(polls, receipts, cutoff):
  routes = {receipt["route"] for receipt in receipts}
  if len(routes) != 1 or not polls:
    return False
  start, end = min(item["start_ns"] for item in receipts), max(item["end_ns"] for item in receipts)
  last = polls[-1]["record"]
  if not last["observation"]["end_ns"] < end <= min(cutoff, last["observation"]["end_ns"] + 2_000_000_000):
    return False
  left = next((index for index in range(len(polls) - 1, -1, -1)
               if polls[index]["record"]["observation"]["end_ns"] <= start), None)
  if left is None:
    return False
  baseline = polls[left]["record"]
  for poll in polls[left:]:
    record, route = poll["record"], poll["record"]["route_observation"]
    if (record["source_status"] != "available" or record["observation"]["gap"]
        or record["revision"] != baseline["revision"] or record["settings_sha256"] != baseline["settings_sha256"]
        or route["state"] != "observed" or route["route"] not in routes
        or any(value is None for value in poll["settings_raw"].values())):
      return False
  return True


def load_log_history(session_directory, sources, budget=None, *, session_snapshot=None):
  """Read verified saved evidence. Unavailable associations return no epochs.

  ``sources`` are independently decoded source receipts with sha256/route/segment.
  Returned consumer samples include the latest causal predecessor of each bracket.
  """
  limits = dict(DEFAULT_BUDGET)
  if budget is not None:
    if not isinstance(budget, dict) or set(budget) - set(limits):
      raise ValueError("invalid_session_history_budget")
    limits.update(budget)
  if any(type(v) is not int or not 0 < v <= DEFAULT_BUDGET[k] for k, v in limits.items()):
    raise ValueError("invalid_session_history_budget")
  output = {"state": "unlinked", "reason_codes": [], "session_id": None, "epochs": [], "consumer_evidence": [],
            "source_receipts": [], "code_hashes": {}, "clock_binding": "unlinked", "raw_sources_verified": False,
            "consumer_applied": False, "helper_source_provenance": "not_recorded"}
  try:
    root = Path(session_directory)
    if not isinstance(sources, list) or not 1 <= len(sources) <= limits["max_segments"]:
      raise ValueError("session_sources_missing_or_over_budget")
    for source in sources:
      if (not isinstance(source, dict) or not isinstance(source.get("sha256"), str)
          or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
          or type(source.get("bytes")) is not int or source["bytes"] <= 0
          or (source.get("route") is not None and not isinstance(source["route"], str))
          or (source.get("segment") is not None and (type(source["segment"]) is not int or source["segment"] < 0))
          or (source.get("id") is not None and (not isinstance(source["id"], str) or not 1 <= len(source["id"]) <= 128))):
        raise ValueError("session_source_identity_invalid")
    sealed = load_journal_snapshot(root, session_snapshot, max_bytes=limits["max_history_bytes"],
                                   max_events=limits["max_events"]) if session_snapshot is not None else None
    journal_root = Path(session_snapshot) if sealed is not None else root
    metadata_bytes = sealed["recording_bytes"] if sealed is not None else _read(root / "recording.json", 64 * 1024)
    metadata = sealed["recording"] if sealed is not None else _object(metadata_bytes)
    clock = metadata.get("clock")
    if (metadata.get("source_mode") != "live" or not isinstance(clock, dict)
        or clock.get("domain") != "native_monotonic" or type(clock.get("scale")) is not int or clock["scale"] != 1):
      raise ValueError("session_native_clock_unlinked")
    segmented_source = sealed is None and (root / "history-checkpoint.json").exists()
    history_bytes = sealed["history_bytes"] if sealed is not None else None if segmented_source else _read(root / "settings.jsonl", limits["max_history_bytes"])
    history = sealed["history"] if sealed is not None else load_history(root if segmented_source else root / "settings.jsonl", max_bytes=limits["max_history_bytes"],
                                                                      max_events=limits["max_events"], include_records=True)
    selected = history.get("selected_closure_complete") is True
    if (history["state"] != "complete" or (not history["closed"] and sealed is None)
        or history.get("journal_version") != 2 and history["verified_bytes_sha256"] != hashlib.sha256(history_bytes).hexdigest()
        or metadata.get("session_id") != history["session_id"]):
      raise ValueError("session_history_incomplete_or_mismatched")
    warnings = []
    if history["consumer_budget_rejections"] != 0:
      warnings.append("session_consumer_history_loss_unknown_or_recorded")
    output["session_id"] = history["session_id"]
    if sealed is not None and sealed["marker"].get("schema_version") == 3:
      from .trip_history import byte_inventory
      inspection = byte_inventory(root, history)
    else:
      inspection = inspect_session(root, byte_limit=limits["max_bytes"], max_segments=limits["max_segments"],
                                   **({"session_snapshot": session_snapshot} if sealed is not None else {}))
    if any(inspection["history"][key] != history[key] for key in ("sequence", "sha256", "bytes")):
      raise ValueError("session_integrity_incomplete")
    if inspection["state"] != "verified":
      warnings.append("session_has_incomplete_evidence")
    available_receipts, copies = [], {}
    pinned = [(journal_root / "recording.json", metadata_bytes)]
    if history_bytes is not None:
      pinned.append((journal_root / "settings.jsonl", history_bytes))
    elif sealed is None:
      pinned.append((root / "history-checkpoint.json", _read(root / "history-checkpoint.json", 16 * 1024)))
    for item in inspection["archives"]["items"]:
      if item["state"] != "verified":
        continue
      marker = root / "archive" / item["archive_id"] / "archive.json"
      data = _read(marker, 64 * 1024)
      if hashlib.sha256(data).hexdigest() != item["manifest_sha256"]:
        raise ValueError("session_archive_changed")
      manifest = _object(data)
      source = manifest["source"]
      identity = (source["sha256"], source["route"], source["segment"])
      copies.setdefault((identity[1], identity[2]), set()).add(identity[0])
      available_receipts.append({"sha256": identity[0], "route": identity[1], "segment": identity[2], "bytes": source["bytes"],
                         "archive_id": item["archive_id"], "manifest_sha256": item["manifest_sha256"],
                         "settings_history_reference": manifest["settings_history_reference"],
                         "settings_revision": manifest["analysis"]["settings_revision"],
                         "start_ns": manifest["coverage"]["services"]["carState"]["mono_min_ns"],
                         "end_ns": manifest["coverage"]["services"]["carState"]["mono_max_ns"],
                         "_payload": marker.parent / manifest["file"]["path"]})
      pinned.append((marker, data))
    receipts, identities = [], set()
    for requested in sources:
      matches = [receipt for receipt in available_receipts if receipt["sha256"] == requested["sha256"]
                 and receipt["bytes"] == requested["bytes"]
                 and (requested.get("route") is None or requested["route"] == receipt["route"])
                 and (requested.get("segment") is None or requested["segment"] == receipt["segment"])]
      if not matches:
        raise ValueError("session_archive_source_unlinked")
      if len({(receipt["route"], receipt["segment"]) for receipt in matches}) != 1:
        raise ValueError("session_archive_source_ambiguous")
      receipt = dict(matches[0])
      if len(copies[(receipt["route"], receipt["segment"])]) != 1:
        raise ValueError("session_selected_archive_conflicting_copy")
      payload = receipt.pop("_payload")
      before = _version(_regular_file(payload).lstat())
      if (hash_file(payload, receipt["bytes"]) != (receipt["sha256"], receipt["bytes"])
          or before != _version(_regular_file(payload).lstat())):
        raise ValueError("session_archive_payload_changed")
      receipt["source_id"] = requested.get("id") or f'log-{requested["sha256"][:16]}'
      identities.add((receipt["sha256"], receipt["route"], receipt["segment"]))
      receipts.append(receipt)
    events = [entry["record"]["event"] for entry in history["records"] if entry["record"]["type"] == "consumer_observation"]
    code_hashes, current_hashes, blocked = {}, {}, set()
    code_root = Path(__file__).resolve().parents[4]
    timelines, gaps = {}, []
    for event in events:
      old = timelines.get(event["consumer_id"])
      if old and (event["sequence"] != old["sequence"] + 1 or event["dropped_events"] != old["dropped_events"]
                  or event["produced_mono_ns"] <= old["produced_mono_ns"]
                  or any(event[k] != old[k] for k in ("boot_id", "pid", "process_instance_id", "consumer_instance_id"))):
        gaps.append(event["produced_mono_ns"])
      elif old is None and event["dropped_events"]:
        gaps.append(event["produced_mono_ns"])
      timelines[event["consumer_id"]] = event
    if "consumer_gap_stamps" in history:
      gaps = list(history["consumer_gap_stamps"])
    events.sort(key=lambda event: (event["produced_mono_ns"], event["consumer_id"], event["sequence"]))

    def causal_events(start, end):
      selected, predecessors = [], {}
      for event in events:
        if event["produced_mono_ns"] < start:
          predecessors[event["consumer_id"]] = event
        elif event["produced_mono_ns"] <= end:
          selected.append(event)
      return sorted([*predecessors.values(), *selected], key=lambda e: (e["produced_mono_ns"], e["consumer_id"]))

    polls, epochs = _observations(history["records"], selected=selected), []
    routes = {identity[1] for identity in identities}
    for left, right in zip(polls, polls[1:]):
      a, b = left["record"], right["record"]
      start, end = a["observation"]["end_ns"], b["observation"]["begin_ns"]
      route = a["route_observation"]
      if (a["revision"] != b["revision"] or a["settings_sha256"] != b["settings_sha256"]
          or left.get("continuity_group") != right.get("continuity_group")
          or a["source_status"] != "available" or b["source_status"] != "available"
          or a["observation"]["gap"] or b["observation"]["gap"] or not start < end
          or end - start > 2_000_000_000 or any(start <= gap <= end for gap in gaps)
          or route["state"] != "observed" or route["route"] not in routes
          or b["route_observation"]["route"] != route["route"]):
        continue
      covered = sorted({(max(start, receipt["start_ns"]), min(end, receipt["end_ns"])) for receipt in receipts
                        if receipt["route"] == route["route"] and start < receipt["end_ns"] and end > receipt["start_ns"]})
      intervals = []
      for lo, hi in covered:
        if lo >= hi:
          continue
        if intervals and lo <= intervals[-1]["end_ns"]:
          intervals[-1]["end_ns"] = max(hi, intervals[-1]["end_ns"])
        else:
          intervals.append({"start_ns": lo, "end_ns": hi})
      if not intervals:
        continue
      start, end = intervals[0]["start_ns"], intervals[-1]["end_ns"]
      selected = causal_events(start, end)
      boots = {event["boot_id"] for event in selected}
      if len(boots) != 1 or None in boots:
        blocked.add("session_consumer_boot_unlinked")
        continue
      selected_hashes, code_valid = {}, True
      for event in selected:
        for relative, digest in event["source_hashes"].items():
          if relative not in {OBSERVER_PATH, CONSUMER_PATHS[event["consumer_id"]]} or digest is None:
            blocked.add("session_consumer_code_unlinked")
            code_valid = False
            continue
          if relative not in current_hashes:
            current_hashes[relative] = hash_file(code_root / relative, 4 * 1024 * 1024)[0]
          if digest != current_hashes[relative]:
            blocked.add("session_consumer_code_mismatch")
            code_valid = False
          selected_hashes[relative] = digest
      if not code_valid:
        continue
      identity = tuple(sorted({(event["consumer_id"], event["boot_id"], event["pid"], event["process_instance_id"],
                                event["consumer_instance_id"]) for event in selected}))
      if len(identity) != len({event["consumer_id"] for event in selected}):
        continue
      code_hashes.update(selected_hashes)
      bracket = {"left_poll": left["reference"], "right_poll": right["reference"]}
      if (epochs and epochs[-1]["settings_revision"] == a["revision"] and epochs[-1]["route"] == route["route"]
          and epochs[-1]["_identity"] == identity and epochs[-1]["_last_poll"] == a["poll_sequence"]
          and not any(epochs[-1]["end_ns"] <= gap <= start for gap in gaps)):
        epochs[-1]["end_ns"] = end
        epochs[-1]["eligible_intervals"].extend(intervals)
        epochs[-1]["provenance"]["observation_brackets"].append(bracket)
        epochs[-1]["_last_poll"] = b["poll_sequence"]
        continue
      epochs.append({"id": f'{history["session_id"]}-r{a["revision"]}-p{a["poll_sequence"]}',
                     "start_ns": start, "end_ns": end, "settings_raw": left["settings_raw"],
                     "settings_revision": a["revision"], "route": route["route"], "boot_id": next(iter(boots)),
                     "completeness": "observed", "dependencies": {}, "eligible_intervals": intervals,
                     "code_hashes": selected_hashes,
                     "_identity": identity, "_last_poll": b["poll_sequence"],
                     "provenance": {"observation_brackets": [bracket],
                                    "continuous_settings_proven": False, "consumer_applied": False}})
      if "vehicle_identity" in a:
        epochs[-1].update(identity_version=1, vehicle_identity=deepcopy(a["vehicle_identity"]))
    right_missing = False
    if sealed is not None and not history["closed"] and not blocked and _right_bracket_missing(
        polls, receipts, sealed["marker"]["checkpoint"]["cutoff_mono_ns"]):
      lo, hi = min(item["start_ns"] for item in receipts), max(item["end_ns"] for item in receipts)
      selected = causal_events(lo, hi)
      boots = {event["boot_id"] for event in selected}
      identities = {(event["consumer_id"], event["boot_id"], event["pid"], event["process_instance_id"], event["consumer_instance_id"])
                    for event in selected}
      code_valid = True
      for event in selected:
        for relative, digest in event["source_hashes"].items():
          if relative not in current_hashes:
            current_hashes[relative] = hash_file(code_root / relative, 4 * 1024 * 1024)[0]
          code_valid &= digest is not None and digest == current_hashes[relative]
      right_missing = (len(boots) == 1 and None not in boots and code_valid
                       and len(identities) == len({event["consumer_id"] for event in selected})
                       and not any(lo <= gap <= hi for gap in gaps))
    if right_missing:
      warnings.append("session_right_bracket_missing")
    if not epochs:
      if right_missing:
        raise ValueError("session_right_bracket_missing")
      raise ValueError(sorted(blocked)[0] if blocked else "session_settings_brackets_unavailable")
    for epoch in epochs:
      epoch.pop("_identity")
      epoch.pop("_last_poll")
      epoch["consumer_evidence"] = causal_events(epoch["start_ns"], epoch["end_ns"])
    for path, data in pinned:
      try:
        unchanged = _read(path, len(data)) == data
      except (OSError, ValueError):
        unchanged = False
      if not unchanged:
        raise ValueError("session_evidence_changed")
    if sealed is not None:
      load_journal_snapshot(root, session_snapshot, max_bytes=limits["max_history_bytes"], max_events=limits["max_events"])
    elif history.get("journal_version") == 2:
      repeated = load_history(root, max_bytes=limits["max_history_bytes"], max_events=limits["max_events"])
      if repeated["checkpoint_tip"] != history["checkpoint_tip"]:
        raise ValueError("session_evidence_changed")
    output.update(state="linked", epochs=deepcopy(epochs), consumer_evidence=deepcopy(events), source_receipts=receipts,
                  code_hashes=code_hashes, clock_binding="witnessed_coorigin", raw_sources_verified=True,
                  reason_codes=sorted(set(warnings) | blocked))
    output["pid_chunk_refs"] = [deepcopy(item["record"]["reference"]) for item in history.get("records", [])
                                if item.get("record", {}).get("type") == "pid_burst_chunk"]
    if sealed is not None:
      selection = sealed["marker"].get("pid_burst_inventory", {})
      if "burst_ids" in selection:
        output["pid_chunk_selection"] = deepcopy(selection)
        output["pid_chunk_refs"] = [row for row in output["pid_chunk_refs"] if row["burst_id"] in selection["burst_ids"]]
    if sealed is not None:
      output.update(snapshot_prefix_complete=history.get("snapshot_prefix_complete", True),
                    selected_closure_complete=history.get("selected_closure_complete", False), source_session_closed=history["closed"],
                    checkpoint_sequence=sealed["marker"]["checkpoint"]["sequence"])
  except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError, UnicodeError, RecursionError, OverflowError) as exc:
    reason = str(exc)
    output["reason_codes"] = [reason if re.fullmatch(r"[a-z_]{1,128}", reason) else "session_evidence_unavailable"]
  return output
