"""Generate offline experiments from closed logs, never from inferred settings.

Original samples stay in the native files. Explicit split gaps remain in those
files and are counted; selected blocks retain their masked anchors. Published
planner values and sampled sidecars do not establish a vehicle improvement.
"""
from bisect import bisect_right
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re

from .analysis import AnalysisProfile
from .candidate_report import MAX_INPUT_BYTES, _code_hashes, _read, calculate_bundle, load_candidate_bundle
from .candidates import _profile, candidate_capabilities
from .evidence import hash_file


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_LIMIT = 32 * 1024 * 1024


def _encoded(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
  return hashlib.sha256(_encoded(value)).hexdigest()


def _positive(value, name, maximum):
  if type(value) is not int or not 1 <= value <= maximum:
    raise ValueError(f"invalid_{name}")


def _fields(value, names):
  if type(value) is not dict or set(value) != set(names):
    raise ValueError("invalid_log_objective_fields")


def _finite(value):
  try:
    valid = type(value) in (int, float) and math.isfinite(value)
  except OverflowError:
    valid = False
  if not valid:
    raise ValueError("nonfinite_log_objective")
  return value


def _objectives(config, capabilities):
  _fields(config, {"schema_version", "analysis_profile", "objectives"})
  if type(config["schema_version"]) is not int or config["schema_version"] != 1:
    raise ValueError("invalid_log_objective_version")
  _fields(config["analysis_profile"], {"profile_id", "pre_margin_ns", "recovery_ns", "max_gap_ns", "dependency_ns", "max_samples"})
  mask = AnalysisProfile(**config["analysis_profile"])
  if not _IDENTIFIER.fullmatch(mask.profile_id) or len(mask.profile_id) > 64:
    raise ValueError("invalid_mask_profile_identifier")
  requested = config["objectives"]
  caps = {row["key"]: row for row in capabilities}
  if type(requested) is not dict or len(requested) > 47 or set(requested) - set(caps):
    raise ValueError("invalid_log_objective_keys")
  for key, spec in requested.items():
    _fields(spec, {"id", "kind", "reference"})
    if type(spec["id"]) is not str or _IDENTIFIER.fullmatch(spec["id"]) is None or spec["kind"] != "policy_reference":
      raise ValueError("log_objective_kind_not_supported")
    reference = spec["reference"]
    _fields(reference, {"type", "input_signal", "input_unit", "unit", "knots"})
    if (reference["type"] != "policy_curve" or reference["input_signal"] != "v_ego" or reference["input_unit"] != "m/s"
        or reference["unit"] != caps[key]["output_unit"] or reference["unit"] is None):
      raise ValueError("log_policy_reference_mapping_not_supported")
    knots = reference["knots"]
    if type(knots) is not list or not 2 <= len(knots) <= 64:
      raise ValueError("log_policy_curve_budget")
    previous = None
    for pair in knots:
      if type(pair) is not list or len(pair) != 2:
        raise ValueError("invalid_log_policy_curve")
      x, _ = (_finite(value) for value in pair)
      if previous is not None and x <= previous:
        raise ValueError("nonincreasing_log_policy_curve")
      previous = x
  return mask


def _curve(knots, x):
  if x < knots[0][0] or x > knots[-1][0]:
    return None
  index = min(bisect_right([pair[0] for pair in knots], x), len(knots) - 1)
  if index == 0:
    return knots[0][1]
  left, right = knots[index - 1], knots[index]
  return left[1] + (right[1] - left[1]) * (x - left[0]) / (right[0] - left[0])


def _epoch_rows(rows, history, maximum):
  """Assign each actual anchor once; settings outside brackets remain unknown."""
  groups, known = {}, history.get("epochs", []) if history.get("state") == "linked" else []
  for row in rows:
    groups.setdefault(row["route_id"], []).append(row)
  result = []
  for route, anchors in groups.items():
    anchors.sort(key=lambda row: row["mono_ns"])
    if any(a["mono_ns"] >= b["mono_ns"] for a, b in zip(anchors, anchors[1:])):
      raise ValueError("duplicate_or_overlapping_native_anchors")
    intervals = sorted((item for item in known if item["route"] == route), key=lambda item: item["start_ns"])
    if any(a["end_ns"] >= b["start_ns"] for a, b in zip(intervals, intervals[1:])):
      raise ValueError("overlapping_observed_setting_epochs")
    selected = {}
    unknown, previous = [], None
    def append(item, items):
      if not items:
        return
      if len(result) >= maximum:
        raise ValueError("log_epoch_budget_exceeded")
      item = copy.deepcopy(item) if item else {"id": f"unknown-{len(result):04d}", "settings_raw": {}, "dependencies": {},
                                             "completeness": "unknown", "consumer_evidence": [], "route": route, "boot_id": None}
      item["start_ns"], item["end_ns"], item["rows"] = items[0]["mono_ns"], items[-1]["mono_ns"], items
      result.append(item)
    starts = [item["start_ns"] for item in intervals]
    for row in anchors:
      index = bisect_right(starts, row["mono_ns"]) - 1
      item = intervals[index] if index >= 0 and row["mono_ns"] <= intervals[index]["end_ns"] else None
      if item is None:
        if previous is not None:
          append(previous, selected.pop(previous["id"]))
          previous = None
        unknown.append(row)
      else:
        if unknown:
          append(None, unknown)
          unknown = []
        if previous is not None and previous["id"] != item["id"]:
          append(previous, selected.pop(previous["id"]))
        selected.setdefault(item["id"], []).append(row)
        previous = item
    if previous is not None:
      append(previous, selected.pop(previous["id"]))
    append(None, unknown)
  return sorted(result, key=lambda item: (item["end_ns"], item["start_ns"], item["id"]))


def _pack(epoch, extracted, history, config, profile, capabilities):
  requested = config["objectives"]
  caps = {row["key"]: row for row in capabilities}
  axes = {caps[key]["axis"] for key in requested}
  required = {name for key in requested for name in caps[key]["required_signals"]} | ({"v_ego"} if requested else set())
  # Objective IDs remain separate. Identical independently specified policy
  # functions need one sampled reference, rather than seven copies per anchor.
  reference_names = {key: f"policy_reference_{_digest(spec['reference'])}" for key, spec in requested.items()}
  references = {reference_names[key]: spec["reference"] for key, spec in requested.items()}
  if len(required | set(reference_names.values())) > 64:
    raise ValueError("generated_log_signal_budget_exceeded")
  objectives = {key: {"id": spec["id"], "kind": "policy_reference", "source": "user_specified",
                      "unit": spec["reference"]["unit"], "reference_signal": reference_names[key]} for key, spec in requested.items()}
  begin, end = epoch["start_ns"], epoch["end_ns"]
  cuts = [begin + (end - begin) // 3, begin + 2 * (end - begin) // 3]
  partitions = {name: [] for name in ("fit", "select", "holdout")}
  margin = max(profile["dependency_margin_ns"], config["analysis_profile"]["dependency_ns"])
  exclusions = Counter()
  for row in epoch["rows"]:
    if any(abs(row["mono_ns"] - cut) <= margin for cut in cuts):
      exclusions.update({"split_dependency_support", *(reason for axis in axes for reason in row["excluded_reasons"][axis])})
      continue
    split = "fit" if row["mono_ns"] < cuts[0] else "select" if row["mono_ns"] < cuts[1] else "holdout"
    partitions[split].append(row)
  receipts = {item["id"]: item for item in extracted["source_receipts"]}
  intervals = epoch.get("eligible_intervals", [{"start_ns": begin, "end_ns": end}])
  interval_starts = [interval["start_ns"] for interval in intervals]
  blocks, splits = [], {name: [] for name in partitions}
  for split, rows in partitions.items():
    if not rows:
      continue
    block_start, block_end = rows[0]["mono_ns"], rows[-1]["mono_ns"]
    samples = []
    for row in rows:
      stamp = row["mono_ns"]
      excluded = {reason for axis in axes for reason in row["excluded_reasons"][axis]}
      interval_index = bisect_right(interval_starts, stamp) - 1
      if epoch["completeness"] == "observed" and (interval_index < 0 or stamp > intervals[interval_index]["end_ns"]):
        excluded.add("settings_observation_uncertain")
      signals = {name: copy.deepcopy(row["signals"][name]) for name in required if name in row["signals"]}
      if epoch["completeness"] == "observed" and interval_index >= 0:
        support_start = min((signal["source_mono_ns"] for signal in signals.values()), default=stamp) - margin
        interval = intervals[interval_index]
        if support_start < interval["start_ns"] or stamp + margin > interval["end_ns"]:
          excluded.add("settings_dependency_uncertain")
      if any(signal["source_mono_ns"] < block_start + margin for signal in signals.values()) or stamp < block_start + margin or stamp > block_end - margin:
        excluded.add("split_dependency_support")
      speed = row["signals"].get("v_ego")
      for reference_name, reference in references.items():
        value = _curve(reference["knots"], speed["value"]) if speed else None
        if value is not None:
          signals[reference_name] = {"value": value, "unit": reference["unit"], "source": "user_specified", "source_mono_ns": stamp}
      exclusions.update(excluded)
      samples.append({"mono_ns": stamp, "signals": signals, "excluded_reasons": sorted(excluded)})
    final = min(block_end, max(receipts[row["source_id"]]["finalized_through_ns"] for row in rows))
    # Finalized bounds cannot erase raw samples; a wholly provisional block is
    # retained with an empty usable prefix and its explicit finalization masks.
    final = max(block_start, final)
    block_id = f"{split}-{len(blocks):04d}"
    blocks.append({"id": block_id, "route_id": epoch["route"], "start_ns": block_start, "end_ns": block_end,
                   "finalized_through_ns": final, "mask_version": extracted["mask_version"], "samples": samples})
    splits[split].append(block_id)
  code_hashes = dict(epoch.get("code_hashes", {}))
  relative = "openpilot/selfdrive/carrot/control_advisor/log_candidates.py"
  code_hashes[relative] = hash_file(Path(__file__), MAX_INPUT_BYTES)[0]
  raw = {key: int(value) if type(value) in (int, float) and math.isfinite(value) and value == int(value) and -1000000 <= value <= 1000000 else None
         for key, value in epoch["settings_raw"].items()}
  source_ids = {row["source_id"] for row in epoch["rows"]}
  sources = [receipt for receipt in extracted["source_receipts"] if receipt["id"] in source_ids]
  evidence = {"schema_version": 1, "source_kind": "recorded", "evidence_id": f"log-epoch-{_digest([epoch['id'], begin, end])[:24]}",
              "artifacts": [{"id": item["id"], "sha256": item["sha256"]} for item in sources],
              "vehicle_context": {"brand": "unknown", "controller_path": "unknown", "mode": "unknown", "code_hashes": code_hashes},
              "epoch": {"id": epoch["id"], "start_ns": begin, "end_ns": end, "settings_raw": raw,
                        "dependencies": copy.deepcopy(epoch["dependencies"]), "completeness": epoch["completeness"], "boot_id": epoch["boot_id"]},
              "splits": splits, "blocks": blocks, "consumer_evidence": copy.deepcopy(epoch["consumer_evidence"]),
              "objectives": objectives, "model_artifact": None}
  if "vehicle_identity" in epoch:
    evidence["vehicle_context"]["context"] = {"identity_version": 1, "vehicle_identity": copy.deepcopy(epoch["vehicle_identity"])}
  linked = history.get("state") == "linked" and epoch["completeness"] == "observed"
  reasons = list(dict.fromkeys([*extracted["limitations"], *history.get("reason_codes", []), "masks_scoped_to_requested_axes",
                               "helper_source_provenance_not_recorded", "recording_schema_match_unverified",
                               "clock_binding_witnessed_coorigin" if linked else "settings_epoch_unlinked"]))
  receipt = {"schema_version": 1, "epoch_id": epoch["id"], "source_bytes_verified_at_generation": True, "schema_decoded": True,
             "session_link": "verified_archive_receipt" if linked else "unlinked", "semantic_mapping": "partial",
             "originals_preservation": "verified_session_archive" if linked else "reference_only", "originals_current_availability": "not_checked",
             "sources": [{name: item[name] for name in ("id", "sha256", "bytes")} for item in extracted["source_receipts"]],
             "source_fields": extracted["source_fields"], "objectives": copy.deepcopy(config), "mask_profile_sha256": _digest(config["analysis_profile"]),
             "excluded_counts": dict(exclusions), "reasons": reasons}
  return evidence, receipt


def _write(path, value):
  payload = _encoded(value)
  with path.open("xb") as stream:
    stream.write(payload)
    stream.flush()
    os.fsync(stream.fileno())
  return len(payload)


def calculate_log_candidates(log_paths, profile_path, objectives_path, destination, *, session_directory=None,
                             session_snapshot=None, cancelled=None,
                             max_files=32, max_events=200000, max_samples=100000,
                             max_decompressed_bytes=256 * 1024 * 1024, max_input_bytes=256 * 1024 * 1024,
                             max_epochs=32, max_output_bytes=_LIMIT):
  """Build a new bounded collection; select latest source time, never best score."""
  def check_cancelled():
    if cancelled is not None and cancelled():
      raise ValueError("log_analysis_cancelled")
  check_cancelled()
  for value, name, cap in ((max_files, "max_files", 32), (max_events, "max_events", 2000000), (max_samples, "max_samples", 100000),
                           (max_decompressed_bytes, "max_decompressed_bytes", 2**31), (max_input_bytes, "max_input_bytes", 2**31),
                           (max_epochs, "max_epochs", 32), (max_output_bytes, "max_output_bytes", _LIMIT)):
    _positive(value, name, cap)
  if isinstance(log_paths, (str, bytes, Path)):
    raise ValueError("log_paths_must_be_a_collection")
  paths = []
  for path in log_paths:
    if len(paths) >= max_files:
      raise ValueError("log_file_budget_exceeded")
    resolved = Path(path).absolute()
    if resolved in paths:
      raise ValueError("duplicate_log_input")
    paths.append(resolved)
  if not paths:
    raise ValueError("log_inputs_required")
  destination = Path(destination)
  if destination.exists() or destination.is_symlink():
    raise ValueError("log_candidate_destination_exists")
  profile, profile_hash = _read(profile_path, MAX_INPUT_BYTES)
  _profile(profile)
  if profile["status"] != "not_validated" or "recorded" not in profile["allowed_source_kinds"]:
    raise ValueError("recorded_host_profile_required")
  config, objective_hash = _read(objectives_path, 64 * 1024)
  capabilities = candidate_capabilities()
  mask = _objectives(config, capabilities)
  adapter_hashes = _code_hashes()
  from .log_signals import extract_log_signals
  extracted = extract_log_signals(paths, analysis_profile=mask, budget={"max_events": max_events, "max_samples": max_samples,
    "max_decompressed_bytes": max_decompressed_bytes, "max_input_bytes": max_input_bytes, "max_event_bytes": 8 * 1024 * 1024,
    "max_age_ns": min(mask.max_gap_ns, profile["max_lag_ns"])}, cancelled=cancelled)
  receipts = extracted["source_receipts"]
  if len({item["sha256"] for item in receipts}) != len(receipts):
    raise ValueError("duplicate_log_content")
  if not extracted["rows"]:
    raise ValueError("log_anchor_samples_unavailable")
  from .log_history import load_log_history
  history = load_log_history(session_directory, receipts, session_snapshot=session_snapshot) if session_directory is not None else {
    "state": "unlinked", "reason_codes": ["session_directory_not_provided"], "epochs": [], "code_hashes": {}}
  resolved = {}
  if history["state"] == "linked":
    # Preserved copies have neutral filenames. A verified exact-byte receipt,
    # rather than that filename, may establish their recorded route identity.
    resolved = {item["sha256"]: item["route"] for item in history["source_receipts"]}
    for source in receipts:
      route = resolved.get(source["sha256"])
      if route is not None:
        if source["route"] is not None and source["route"] != route:
          raise ValueError("verified_log_route_conflict")
        for row in extracted["rows"]:
          if row["source_id"] == source["id"]:
            row["route_id"] = route
  route_keys = {resolved.get(source["sha256"]) or source["route"] or source["id"] for source in receipts}
  if len(route_keys) != 1:
    raise ValueError("multi_route_clock_unlinked")
  epochs = _epoch_rows(extracted["rows"], history, max_epochs)
  destination.mkdir(parents=False)
  index = {"schema_version": 1, "scope": "log_candidate_collection", "epochs": [], "latest": None,
           "apply_enabled": False, "device_qualified": False}
  used = 0
  for number, epoch in enumerate(epochs):
    check_cancelled()
    evidence, receipt = _pack(epoch, extracted, history, config, profile, capabilities)
    receipt["objectives_sha256"] = objective_hash
    payload = _encoded(evidence)
    if len(payload) > MAX_INPUT_BYTES:
      raise ValueError("generated_log_pack_byte_limit")
    # Reserve a complete bounded result/manifest in addition to copied inputs.
    upper = 2 * len(payload) + Path(profile_path).stat().st_size + len(_encoded(receipt)) + 1024 * 1024 + 64 * 1024
    if used + upper + 64 * 1024 > max_output_bytes:
      raise ValueError("log_candidate_output_budget_exceeded")
    name = f"epoch-{number:04d}"
    generated = destination / f"generated-{name}.json"
    used += _write(generated, evidence)
    calculate_bundle(generated, profile_path, destination / name, log_link=receipt)
    load_candidate_bundle(destination / name)
    used += sum(path.stat().st_size for path in (destination / name).rglob("*") if path.is_file())
    index["epochs"].append({"epoch_id": epoch["id"], "start_ns": epoch["start_ns"], "end_ns": epoch["end_ns"], "bundle": name, "status": "complete"})
    index["latest"] = name
  for path, receipt in zip(paths, receipts):
    check_cancelled()
    if hash_file(path, max_input_bytes) != (receipt["sha256"], receipt["bytes"]):
      raise ValueError("original_log_changed_after_analysis")
  if _read(profile_path, MAX_INPUT_BYTES)[1] != profile_hash or _read(objectives_path, 64 * 1024)[1] != objective_hash:
    raise ValueError("log_candidate_configuration_changed")
  if _code_hashes() != adapter_hashes:
    raise ValueError("log_candidate_adapter_changed")
  if used + len(_encoded(index)) > max_output_bytes:
    raise ValueError("log_candidate_output_budget_exceeded")
  check_cancelled()
  _write(destination / "index.json", index)
  return {"state": "complete", "scope": index["scope"], "output": str(destination), "epoch_count": len(epochs),
          "latest_bundle": str(destination / index["latest"]), "parameter_count": 47, "source_count": len(paths),
          "session_link": history["state"], "apply_enabled": False, "device_qualified": False}
