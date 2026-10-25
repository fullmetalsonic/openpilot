"""Bounded offline candidate experiments; never a vehicle recommendation or writer.

The profile is an explicit host experiment contract. UI limits only constrain
serialization of a search; they are not safe change limits. Engines receive
only the requested split, and holdout data cannot choose a second candidate.
"""
from __future__ import annotations

import copy
from bisect import bisect_left, bisect_right
import hashlib
import importlib
import json
import math
import re

from .registry import load_registry


_ID = re.compile(r"^[A-Za-z0-9_.:/+^ -]{1,96}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_KINDS = {"static_formula", "frozen_input_replay", "closed_loop_model"}
_STATES = {"raw_estimate", "search_candidate", "keep_current", "blocked", "unsupported"}
_CONTEXT_ONLY = {"LateralTorqueCustom", "NNFF", "NNFFLite", "LatMpcInputOffset"}
_MODE_SENTINELS = {"SteerActuatorDelay": {0}, **{f"LeadAccelResponseTF{i}": {-1} for i in range(1, 5)}}
_METRIC_KEYS = {"rows", "blocks", "rmse", "max_absolute_error", "max_absolute_prediction", "trace_digest"}


def _keys(value, required, optional=()):
  if type(value) is not dict or set(value) - set(required) - set(optional) or set(required) - set(value):
    raise ValueError("invalid_object_fields")


def _identifier(value):
  if type(value) is not str or _ID.fullmatch(value) is None:
    raise ValueError("invalid_identifier")
  return value


def _integer(value, minimum=0, maximum=2**63 - 1):
  if type(value) is not int or not minimum <= value <= maximum:
    raise ValueError("invalid_integer")
  return value


def _number(value, minimum=None):
  try:
    valid = type(value) in (int, float) and math.isfinite(value) and (minimum is None or value >= minimum)
  except (OverflowError, ValueError):
    valid = False
  if not valid:
    raise ValueError("invalid_finite_number")
  return value


def _digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sha(value):
  if type(value) is not str or _SHA.fullmatch(value) is None:
    raise ValueError("invalid_sha256")
  return value


def _small_tree(value, depth=0, counter=None):
  """Bound dependency/context metadata without importing vehicle classes."""
  counter = [0] if counter is None else counter
  counter[0] += 1
  if depth > 6 or counter[0] > 4096:
    raise ValueError("metadata_budget_exceeded")
  if value is None or type(value) is bool:
    return
  if type(value) in (int, float):
    _number(value)
  elif type(value) is str:
    _identifier(value)
  elif type(value) is list and len(value) <= 64:
    for item in value:
      _small_tree(item, depth + 1, counter)
  elif type(value) is dict and len(value) <= 128:
    for key, item in value.items():
      _identifier(key)
      _small_tree(item, depth + 1, counter)
  else:
    raise ValueError("invalid_bounded_metadata")


def _engines():
  return {axis: importlib.import_module(f"{__package__}.candidate_{axis}") for axis in ("lateral", "longitudinal")}


def candidate_capabilities() -> list[dict]:
  """Inventory all 47 routes, including explicit absent backend/context gates."""
  engines = _engines()
  caps = {axis: module.capabilities() for axis, module in engines.items()}
  rows = []
  for item in load_registry()["items"]:
    cap = caps[item["axis"]].get(item["key"], {})
    reason = list(cap.get("reason_codes", ["backend_unavailable"]))
    if item["key"] in _CONTEXT_ONLY:
      reason = ["context_only_no_automatic_mode_switch"]
    if item["key"] == "LateralTorqueKd":
      reason = ["derivative_path_has_no_effect"]
    rows.append({"key": item["key"], "axis": item["axis"], "family": item["family"],
                 "method_id": cap.get("method_id", "unavailable"), "backend": cap.get("backend", "unavailable"),
                 "prediction_kind": cap.get("prediction_kind"), "required_signals": cap.get("required_signals", {}),
                 "output_unit": cap.get("output_unit"), "estimate_kind": None if not cap or cap.get("backend") == "unavailable" or item["key"] in _CONTEXT_ONLY else "diagnostic_proxy" if cap.get("backend") in {"raw_proxy_only", "raw_delay_only"} else "parameter_calibration",
                 "reason_codes": reason,
                 "device_qualified": False, "recommendations_enabled": False, "apply_enabled": False})
  return rows


def _profile(profile):
  _keys(profile, {"profile_id", "version", "status", "scope", "allowed_source_kinds", "max_rows", "max_blocks",
                  "max_candidates_per_key", "max_evaluations", "max_lag_ns", "dependency_margin_ns",
                  "min_blocks_per_split", "min_samples_per_split", "minimum_improvement", "guard_tolerances",
                  "raw_step_policy", "per_key_change_limits", "device_qualified"}, {"method_options"})
  _identifier(profile["profile_id"])
  _integer(profile["version"], 1)
  if profile["status"] not in {"synthetic_only", "not_validated"} or profile["scope"] != "host_unqualified" or profile["device_qualified"] is not False:
    raise ValueError("profile_not_host_unqualified")
  if type(profile["allowed_source_kinds"]) is not list or not profile["allowed_source_kinds"] or set(profile["allowed_source_kinds"]) - {"synthetic", "recorded"}:
    raise ValueError("invalid_allowed_sources")
  for key, cap in (("max_rows", 100000), ("max_blocks", 128), ("max_candidates_per_key", 32), ("max_evaluations", 4096)):
    _integer(profile[key], 1, cap)
  for key in ("max_lag_ns", "dependency_margin_ns"):
    _integer(profile[key])
  for key in ("min_blocks_per_split", "min_samples_per_split"):
    _integer(profile[key], 1)
  _number(profile["minimum_improvement"], 0)
  if profile["raw_step_policy"] != "ui_step":
    raise ValueError("raw_step_policy_unsupported")
  known = {item["key"] for item in load_registry()["items"]}
  limits = profile["per_key_change_limits"]
  if limits is not None:
    if type(limits) is not dict or set(limits) - known:
      raise ValueError("invalid_change_limits")
    for value in limits.values():
      _number(value, 0)
  guards = profile["guard_tolerances"]
  if type(guards) is not dict or set(guards) - known:
    raise ValueError("invalid_guard_tolerances")
  for value in guards.values():
    _keys(value, {"max_absolute_error", "max_absolute_prediction"})
    for number in value.values():
      _number(number, 0)
  options = profile.get("method_options", {})
  if type(options) is not dict or set(options) - known:
    raise ValueError("invalid_method_options")
  _small_tree(options)


def _evidence(pack, profile):
  _keys(pack, {"schema_version", "source_kind", "evidence_id", "artifacts", "vehicle_context", "epoch", "splits", "blocks",
               "consumer_evidence", "objectives", "model_artifact"})
  if pack["schema_version"] != 1 or type(pack["schema_version"]) is not int or pack["source_kind"] not in {"synthetic", "recorded"}:
    raise ValueError("invalid_evidence_version_or_source")
  _identifier(pack["evidence_id"])
  if type(pack["artifacts"]) is not list or not 1 <= len(pack["artifacts"]) <= 32:
    raise ValueError("artifact_budget_exceeded")
  seen = set()
  for artifact in pack["artifacts"]:
    _keys(artifact, {"id", "sha256"})
    _identifier(artifact["id"])
    _sha(artifact["sha256"])
    if artifact["id"] in seen:
      raise ValueError("duplicate_artifact_id")
    seen.add(artifact["id"])
  vehicle = pack["vehicle_context"]
  _keys(vehicle, {"brand", "controller_path", "mode", "code_hashes"}, {"context"})
  for key in ("brand", "controller_path", "mode"):
    _identifier(vehicle[key])
  if type(vehicle["code_hashes"]) is not dict or not 1 <= len(vehicle["code_hashes"]) <= 32:
    raise ValueError("invalid_code_provenance")
  for path, sha in vehicle["code_hashes"].items():
    if type(path) is not str or len(path) > 180 or not path.startswith("openpilot/") or ".." in path or "\\" in path:
      raise ValueError("invalid_code_path")
    _sha(sha)
  _small_tree(vehicle.get("context", {}))
  epoch = pack["epoch"]
  _keys(epoch, {"id", "start_ns", "end_ns", "settings_raw", "dependencies", "completeness"}, {"boot_id"})
  _identifier(epoch["id"])
  _integer(epoch["start_ns"])
  _integer(epoch["end_ns"], epoch["start_ns"])
  if epoch["completeness"] not in {"observed", "fixture", "unknown"}:
    raise ValueError("invalid_epoch_completeness")
  if epoch.get("boot_id") is not None:
    _identifier(epoch["boot_id"])
  known = {item["key"] for item in load_registry()["items"]}
  if type(epoch["settings_raw"]) is not dict or set(epoch["settings_raw"]) - known:
    raise ValueError("invalid_raw_settings")
  for raw in epoch["settings_raw"].values():
    if raw is not None:
      _integer(raw, -1000000, 1000000)
  if type(epoch["dependencies"]) is not dict:
    raise ValueError("invalid_dependencies")
  _small_tree(epoch["dependencies"])
  _keys(pack["splits"], {"fit", "select", "holdout"})
  memberships = {}
  for split, ids in pack["splits"].items():
    if type(ids) is not list or len(ids) > profile["max_blocks"]:
      raise ValueError("split_budget_exceeded")
    for block_id in ids:
      _identifier(block_id)
      if block_id in memberships:
        raise ValueError("split_block_reused")
      memberships[block_id] = split
  if type(pack["blocks"]) is not list or len(pack["blocks"]) > profile["max_blocks"]:
    raise ValueError("block_budget_exceeded")
  ids, row_count, ranges = set(), 0, []
  for block in pack["blocks"]:
    _keys(block, {"id", "route_id", "start_ns", "end_ns", "finalized_through_ns", "mask_version", "samples"})
    for key in ("id", "route_id", "mask_version"):
      _identifier(block[key])
    if block["id"] in ids or block["id"] not in memberships:
      raise ValueError("unassigned_or_duplicate_block")
    ids.add(block["id"])
    _integer(block["start_ns"], epoch["start_ns"], epoch["end_ns"])
    _integer(block["end_ns"], block["start_ns"], epoch["end_ns"])
    _integer(block["finalized_through_ns"], block["start_ns"], block["end_ns"])
    ranges.append((block["start_ns"], block["end_ns"], memberships[block["id"]]))
    if type(block["samples"]) is not list:
      raise ValueError("invalid_sample_rows")
    row_count += len(block["samples"])
    if row_count > profile["max_rows"]:
      raise ValueError("sample_budget_exceeded")
    previous = -1
    for row in block["samples"]:
      _keys(row, {"mono_ns", "signals", "excluded_reasons"})
      _integer(row["mono_ns"], block["start_ns"], block["end_ns"])
      if row["mono_ns"] <= previous:
        raise ValueError("nonmonotonic_or_duplicate_sample")
      previous = row["mono_ns"]
      if type(row["excluded_reasons"]) is not list or len(row["excluded_reasons"]) > 24:
        raise ValueError("invalid_exclusion_reasons")
      for reason in row["excluded_reasons"]:
        _identifier(reason)
      if type(row["signals"]) is not dict or len(row["signals"]) > 64:
        raise ValueError("signal_budget_exceeded")
      for name, signal in row["signals"].items():
        _identifier(name)
        _keys(signal, {"value", "unit", "source", "source_mono_ns"})
        _number(signal["value"])
        _identifier(signal["unit"])
        _identifier(signal["source"])
        if pack["source_kind"] == "recorded" and signal["source"] == "synthetic_truth":
          raise ValueError("recorded_signal_is_synthetic")
        _integer(signal["source_mono_ns"], 0, row["mono_ns"])
  if ids != set(memberships):
    raise ValueError("missing_split_block")
  margin = profile["dependency_margin_ns"]
  independent = all(a[1] + margin < b[0] or b[1] + margin < a[0]
                    for index, a in enumerate(ranges) for b in ranges[index + 1:])
  if type(pack["objectives"]) is not dict or set(pack["objectives"]) - known:
    raise ValueError("invalid_objectives")
  for objective in pack["objectives"].values():
    _keys(objective, {"id", "kind", "source", "unit", "reference_signal"})
    for key in ("id", "unit", "reference_signal"):
      _identifier(objective[key])
    if objective["kind"] not in {"tracking", "policy_reference", "geometry_reference"} or objective["source"] not in {"independent_measurement", "user_specified", "synthetic_truth"}:
      raise ValueError("objective_not_independent")
    if pack["source_kind"] == "recorded" and objective["source"] == "synthetic_truth":
      raise ValueError("recorded_objective_is_synthetic")
  if type(pack["consumer_evidence"]) is not list or len(pack["consumer_evidence"]) > 2048:
    raise ValueError("consumer_evidence_budget_exceeded")
  from .consumer_observer import validate_event
  consumer_sequences = {}
  consumer_times = {}
  for event in pack["consumer_evidence"]:
    validate_event(event)
    identity = (event["consumer_id"], event["boot_id"], event["pid"], event["process_instance_id"], event["consumer_instance_id"])
    previous = consumer_sequences.get(identity)
    if previous and (event["sequence"] <= previous[0] or event["produced_mono_ns"] <= previous[1]):
      raise ValueError("consumer_sequence_replay_or_regression")
    if event["produced_mono_ns"] <= consumer_times.get(event["consumer_id"], -1):
      raise ValueError("consumer_time_regression_or_ambiguous_event")
    consumer_sequences[identity] = (event["sequence"], event["produced_mono_ns"])
    consumer_times[event["consumer_id"]] = event["produced_mono_ns"]
  model = pack["model_artifact"]
  if model is not None:
    _keys(model, {"id", "sha256", "type", "domain", "fit_blocks", "validation_blocks", "error_bounds"})
    _identifier(model["id"])
    _sha(model["sha256"])
    for key in ("type", "domain"):
      _identifier(model[key])
    _small_tree(model["error_bounds"])
    for key in ("fit_blocks", "validation_blocks"):
      if type(model[key]) is not list or len(model[key]) > profile["max_blocks"] or set(model[key]) - ids:
        raise ValueError("invalid_model_blocks")
  return independent


def _records(pack, profile, objective, required):
  result = {split: [] for split in ("fit", "select", "holdout")}
  counts = {"excluded": 0, "provisional": 0, "missing": 0, "missing_fit": 0}
  margin = profile["dependency_margin_ns"]
  for block in pack["blocks"]:
    split = next(split for split in result if block["id"] in pack["splits"][split])
    excluded = [row["mono_ns"] for row in block["samples"] if row["excluded_reasons"]]
    for row in block["samples"]:
      stamp = row["mono_ns"]
      near = bisect_left(excluded, stamp - margin)
      if row["excluded_reasons"] or (near < len(excluded) and excluded[near] <= stamp + margin):
        counts["excluded"] += 1
        continue
      if stamp < block["start_ns"] + margin or stamp > block["finalized_through_ns"] - margin:
        counts["provisional"] += 1
        continue
      needed = dict(required)
      needed[objective["reference_signal"]] = objective["unit"]
      signals = row["signals"]
      if any(name not in signals or signals[name]["unit"] != unit
             or stamp - signals[name]["source_mono_ns"] > profile["max_lag_ns"]
             or signals[name]["source_mono_ns"] < block["start_ns"] + margin for name, unit in needed.items()):
        counts["missing"] += 1
        counts["missing_fit"] += int(split == "fit")
        continue
      # A held input depends on its entire source-to-use interval. Neither a
      # driver mask nor a split boundary may be crossed by that dependency.
      support_start = min(signals[name]["source_mono_ns"] for name in needed) - margin
      near = bisect_left(excluded, support_start)
      if near < len(excluded) and excluded[near] <= stamp + margin:
        counts["excluded"] += 1
        continue
      reference = signals[objective["reference_signal"]]
      if reference["source"] != objective["source"]:
        counts["missing"] += 1
        counts["missing_fit"] += int(split == "fit")
        continue
      result[split].append({"block_id": block["id"], "route_id": block["route_id"], "mono_ns": stamp,
                            "signals": {name: copy.deepcopy(signals[name]) for name in needed}, "reference": reference["value"]})
  return result, counts


def _context(pack, records, objective, split):
  # Full source metadata never includes held-out sample values in an engine call.
  metadata = {key: copy.deepcopy(pack[key]) for key in ("source_kind", "evidence_id", "artifacts", "vehicle_context", "epoch")}
  selected = {name: copy.deepcopy(rows) if name == split else [] for name, rows in records.items()}
  return {"evidence": metadata, "settings_raw": copy.deepcopy(pack["epoch"]["settings_raw"]),
          "dependencies": copy.deepcopy(pack["epoch"]["dependencies"]), "vehicle_context": copy.deepcopy(pack["vehicle_context"]),
          "records": selected, "objective": copy.deepcopy(objective)}


def _metric(evaluation, rows, expected_kind):
  _keys(evaluation, {"status", "prediction_kind", "predictions", "reason_codes"})
  if evaluation["status"] != "evaluated":
    return None
  if evaluation["prediction_kind"] != expected_kind:
    raise ValueError("prediction_kind_changed")
  predictions = evaluation["predictions"]
  if type(predictions) is not list or len(predictions) != len(rows) or not rows:
    raise ValueError("prediction_count_mismatch")
  for value in predictions:
    _number(value)
  errors = [value - row["reference"] for value, row in zip(predictions, rows)]
  values = {"rows": len(rows), "blocks": len({row["block_id"] for row in rows}),
            "rmse": math.sqrt(math.fsum(error * error for error in errors) / len(errors)),
            "max_absolute_error": max(abs(error) for error in errors), "max_absolute_prediction": max(abs(value) for value in predictions),
            "trace_digest": _digest(predictions)}
  for key in ("rmse", "max_absolute_error", "max_absolute_prediction"):
    _number(values[key], 0)
  return values


def _physical(item, raw):
  if raw is None or item["key"] in _CONTEXT_ONLY or raw in _MODE_SENTINELS.get(item["key"], set()) or (item["key"] == "CustomSR" and raw <= 10):
    return None
  return raw * item["raw_scale"]


def _consumer_gate(pack, profile, item, records):
  if pack["source_kind"] == "synthetic":
    return ([], []) if pack["epoch"]["completeness"] == "fixture" else (["synthetic_epoch_not_fixture"], [])
  if pack["epoch"]["completeness"] != "observed" or not pack["epoch"].get("boot_id"):
    return ["consumer_identity_or_epoch_unknown"], []
  from .consumer_observer import consumer_ids_for_key
  timelines = {}
  raw = pack["epoch"]["settings_raw"].get(item["key"])
  for event in pack["consumer_evidence"]:
    if event["consumer_id"] not in consumer_ids_for_key(item["key"]):
      continue
    timeline = timelines.setdefault(event["consumer_id"], [])
    identity = (event["boot_id"], event["pid"], event["process_instance_id"], event["consumer_instance_id"])
    previous = timeline[-1][0] if timeline else None
    previous_identity = (previous["boot_id"], previous["pid"], previous["process_instance_id"], previous["consumer_instance_id"]) if previous else None
    gap = (event["dropped_events"] > 0) if previous is None else (identity != previous_identity or event["sequence"] != previous["sequence"] + 1 or event["dropped_events"] != previous["dropped_events"])
    timeline.append((event, not gap))
  expected = raw if item["key"] == "SpeedTFFactor" else raw * item["raw_scale"]
  required_consumers = consumer_ids_for_key(item["key"])
  if set(required_consumers) - set(timelines):
    return ["required_consumer_observation_missing"], []
  observed = []
  for consumer_id in required_consumers:
    timeline = timelines[consumer_id]
    stamps = [event["produced_mono_ns"] for event, _ in timeline]
    selected_identity, valid = None, True
    for row in (row for rows in records.values() for row in rows):
      index = bisect_right(stamps, row["mono_ns"]) - 1
      if index < 0 or row["mono_ns"] - stamps[index] > profile["max_lag_ns"]:
        valid = False
        break
      event, contiguous = timeline[index]
      field = event["fields"].get(item["key"])
      identity = (event["boot_id"], event["pid"], event["process_instance_id"], event["consumer_instance_id"])
      read = field["read_value"] if field else None
      effective = field["effective_value"] if field else None
      hashes_match = all(sha is not None and pack["vehicle_context"]["code_hashes"].get(path) == sha for path, sha in event["source_hashes"].items())
      allowed_states = {"loaded"} if consumer_id == "planner.carrot" else {"used", "latched"}
      if not contiguous or event["boot_id"] != pack["epoch"]["boot_id"] or not hashes_match or not field or field["state"] not in allowed_states or (selected_identity is not None and identity != selected_identity):
        valid = False
        break
      # Read values describe storage/cache observations. Only a reconciled
      # effective value can establish the baseline used by this experiment.
      if type(effective) not in (int, float) or not math.isclose(effective, expected, rel_tol=1e-9, abs_tol=1e-12) or field["source"] in {"unknown", "platform_fixed"}:
        return ["consumer_effective_value_unreconciled"], []
      if (read is not None and (type(read) not in (int, float) or not math.isclose(read, expected, rel_tol=1e-9, abs_tol=1e-12))) or (consumer_id == "planner.carrot" and read is None):
        return ["consumer_read_value_unreconciled"], []
      selected_identity = identity
    if valid and selected_identity is not None:
      observed.append(consumer_id)
    else:
      return ["consumer_evidence_not_time_aligned_or_identity_changed"], []
  return [], observed


def evaluate_candidates(evidence: dict, profile: dict) -> dict:
  """Run a bounded, explicit split experiment and return all 47 inventory rows."""
  _profile(profile)
  independent = _evidence(evidence, profile)
  engines = _engines()
  caps = {axis: module.capabilities() for axis, module in engines.items()}
  budget = {"remaining_evaluations": profile["max_evaluations"], "max_rows": profile["max_rows"]}
  report = {"schema_version": 1, "profile_id": profile["profile_id"], "profile_version": profile["version"],
            "evidence_id": evidence["evidence_id"], "source_kind": evidence["source_kind"], "scope": "host_unqualified",
            "device_qualified": False, "recommendations_enabled": False, "apply_enabled": False,
            "provenance": {"artifacts": copy.deepcopy(evidence["artifacts"]), "epoch_id": evidence["epoch"]["id"],
                           "code_hashes": copy.deepcopy(evidence["vehicle_context"]["code_hashes"]),
                           "mask_versions": sorted({block["mask_version"] for block in evidence["blocks"]}),
                           "profile_sha256": _digest(profile), "evidence_sha256": _digest(evidence),
                           "source_verification": "declared_input_only"}, "items": []}
  for item in load_registry()["items"]:
    key, axis = item["key"], item["axis"]
    cap = caps[axis].get(key, {})
    current = evidence["epoch"]["settings_raw"].get(key)
    row = {"key": key, "axis": axis, "family": item["family"], "status": "blocked", "current_raw": current,
           "current_physical": _physical(item, current), "candidate_raw": None, "candidate_physical": None,
           "raw_estimate": None, "physical_estimate": None, "method_id": cap.get("method_id", "unavailable"),
           "estimate_kind": None,
           "backend": cap.get("backend", "unavailable"), "prediction_kind": cap.get("prediction_kind"),
           "metrics": {"baseline": {"select": None, "holdout": None}, "selection": None, "holdout": None},
           "validation": {"numerical_checks_pass": False, "split_independent": independent, "holdout_objective_pass": False,
                          "independent_route_reproduced": False, "device_qualified": False}, "reason_codes": [],
           "provenance": {"epoch_id": evidence["epoch"]["id"], "objective_id": None, "consumer_ids": [], "excluded_rows": 0, "provisional_rows": 0}}
    report["items"].append(row)
    def block(*reasons):
      row["reason_codes"] = list(dict.fromkeys(reasons))[:24]
    if key in _CONTEXT_ONLY:
      row["status"] = "unsupported"
      block("context_only_no_automatic_mode_switch")
      continue
    if key == "LateralTorqueKd":
      row["status"] = "unsupported"
      block("derivative_path_has_no_effect")
      continue
    if key.startswith("LongTuning") and evidence["vehicle_context"]["brand"] in {"hyundai", "toyota"}:
      block("platform_fixed_longitudinal_pid")
      continue
    if not cap or cap.get("backend") == "unavailable":
      block(*cap.get("reason_codes", ["backend_unavailable"]))
      continue
    if current is None:
      block("current_raw_unknown")
      continue
    if evidence["source_kind"] not in profile["allowed_source_kinds"] or (profile["status"] == "synthetic_only" and evidence["source_kind"] != "synthetic"):
      block("profile_source_kind_not_allowed")
      continue
    objective = evidence["objectives"].get(key)
    if objective is None:
      block("independent_objective_missing")
      continue
    row["provenance"]["objective_id"] = objective["id"]
    if objective["unit"] != cap.get("output_unit"):
      block("objective_unit_mismatch")
      continue
    if not independent:
      block("split_dependency_overlap")
      continue
    records, counts = _records(evidence, profile, objective, cap.get("required_signals", {}))
    row["provenance"].update(excluded_rows=counts["excluded"], provisional_rows=counts["provisional"])
    if counts["missing_fit"]:
      block("missing_stale_or_mismatched_signal_units")
      continue
    def exposure(rows):
      return len(rows) >= profile["min_samples_per_split"] and len({r["block_id"] for r in rows}) >= profile["min_blocks_per_split"]
    if not exposure(records["fit"]):
      block("insufficient_independent_split_exposure")
      continue
    reasons, observed_consumers = _consumer_gate(evidence, profile, item, records)
    if reasons:
      block(*reasons)
      continue
    row["provenance"]["consumer_ids"] = observed_consumers
    engine = engines[axis]
    try:
      engine_profile = copy.deepcopy(profile)
      ui = item["ui"]
      engine_profile["parameter"] = {"key": key, "current_raw": current, **ui, "raw_scale": item["raw_scale"],
                                     "max_delta_raw": (profile["per_key_change_limits"] or {}).get(key)}
      proposal = engine.propose_family(key, _context(evidence, records, objective, "fit"), engine_profile)
      _keys(proposal, {"raw_estimate", "candidate_raw_values", "method_id", "reason_codes"})
      if proposal["method_id"] != row["method_id"]:
        raise ValueError("proposal_method_changed")
      if proposal["raw_estimate"] is not None:
        _number(proposal["raw_estimate"])
        row["raw_estimate"] = proposal["raw_estimate"]
        row["physical_estimate"] = _physical(item, proposal["raw_estimate"])
        row["estimate_kind"] = "diagnostic_proxy" if cap.get("backend") in {"raw_proxy_only", "raw_delay_only"} else "parameter_calibration"
        row["status"] = "raw_estimate"
        row["validation"]["numerical_checks_pass"] = True
      if proposal["reason_codes"]:
        block(*proposal["reason_codes"])
        continue
      if cap.get("backend") in {"raw_proxy_only", "raw_delay_only"}:
        block(*(cap.get("reason_codes") or ["diagnostic_proxy_only"]))
        continue
      if counts["missing"]:
        block("missing_stale_or_mismatched_signal_units")
        continue
      if not all(exposure(records[split]) for split in ("select", "holdout")):
        block("insufficient_independent_split_exposure")
        continue
      values = proposal["candidate_raw_values"]
      if type(values) is not list or len(values) > profile["max_candidates_per_key"]:
        raise ValueError("candidate_budget_exceeded")
      limits = profile["per_key_change_limits"]
      guard = profile["guard_tolerances"].get(key)
      if limits is None or key not in limits:
        block("single_change_limit_missing")
        continue
      if guard is None:
        block("objective_guard_missing")
        continue
      candidates = []
      for value in values:
        _integer(value, ui["min"], ui["max"])
        if (value - current) % ui["step"] or abs(value - current) > limits[key]:
          raise ValueError("candidate_quantization_or_change_limit")
        if value != current and (({value, current} & _MODE_SENTINELS.get(key, set())) or (key == "CustomSR" and min(value, current) <= 10)):
          continue
        if value != current and value not in candidates:
          candidates.append(value)
      def evaluate(value, split):
        if budget["remaining_evaluations"] <= 0:
          raise ValueError("evaluation_budget_exhausted")
        budget["remaining_evaluations"] -= 1
        output = engine.evaluate_family(key, value, _context(evidence, records, objective, split), split, copy.deepcopy(budget))
        metric = _metric(output, records[split], row["prediction_kind"])
        if metric is None:
          raise ValueError("engine_evaluation_unavailable")
        return metric
      baseline_select = evaluate(current, "select")
      row["metrics"]["baseline"]["select"] = baseline_select
      scored = [(evaluate(value, "select"), value) for value in candidates]
      best = min(scored, key=lambda pair: (pair[0]["rmse"], abs(pair[1] - current), pair[1])) if scored else None
      row["validation"]["numerical_checks_pass"] = True
      if best is None or best[0]["rmse"] >= baseline_select["rmse"] - profile["minimum_improvement"]:
        row["status"] = "keep_current"
        block("current_value_retained")
        continue
      selected, candidate = best
      row["metrics"]["selection"] = selected
      baseline_holdout = evaluate(current, "holdout")
      holdout = evaluate(candidate, "holdout")
      row["metrics"]["baseline"]["holdout"] = baseline_holdout
      row["metrics"]["holdout"] = holdout
      checks = all(metric["max_absolute_error"] <= guard["max_absolute_error"] and metric["max_absolute_prediction"] <= guard["max_absolute_prediction"] for metric in (selected, holdout))
      passed = checks and holdout["rmse"] < baseline_holdout["rmse"] - profile["minimum_improvement"]
      row["validation"]["holdout_objective_pass"] = passed
      if passed:
        row.update(status="search_candidate", candidate_raw=candidate, candidate_physical=_physical(item, candidate))
        block("host_objective_only_not_vehicle_validation")
      else:
        row["status"] = "keep_current"
        block("holdout_or_objective_guard_failed")
    except (ValueError, TypeError, KeyError, OverflowError, ArithmeticError):
      row["status"] = "blocked"
      row["candidate_raw"] = row["candidate_physical"] = None
      block("engine_contract_or_budget_failed")
    except Exception:
      row["status"] = "blocked"
      row["candidate_raw"] = row["candidate_physical"] = None
      block("engine_error")
  return validate_candidate_report(report)


def validate_candidate_report(report: dict) -> dict:
  """Reject forged qualification/current fields; return a bounded canonical copy."""
  _keys(report, {"schema_version", "profile_id", "profile_version", "evidence_id", "source_kind", "scope", "device_qualified",
                 "recommendations_enabled", "apply_enabled", "provenance", "items"})
  if type(report["schema_version"]) is not int or report["schema_version"] != 1 or report["scope"] != "host_unqualified" or report["source_kind"] not in {"synthetic", "recorded"}:
    raise ValueError("invalid_candidate_report")
  for key in ("device_qualified", "recommendations_enabled", "apply_enabled"):
    if report[key] is not False:
      raise ValueError("qualification_or_apply_claim_forbidden")
  for key in ("profile_id", "evidence_id"):
    _identifier(report[key])
  _integer(report["profile_version"], 1)
  provenance = report["provenance"]
  _keys(provenance, {"artifacts", "epoch_id", "code_hashes", "mask_versions", "profile_sha256", "evidence_sha256", "source_verification"})
  if provenance["source_verification"] != "declared_input_only":
    raise ValueError("source_authentication_claim_forbidden")
  _identifier(provenance["epoch_id"])
  for key in ("profile_sha256", "evidence_sha256"):
    _sha(provenance[key])
  if type(provenance["artifacts"]) is not list or not 1 <= len(provenance["artifacts"]) <= 32:
    raise ValueError("invalid_report_artifacts")
  for artifact in provenance["artifacts"]:
    _keys(artifact, {"id", "sha256"})
    _identifier(artifact["id"])
    _sha(artifact["sha256"])
  if type(provenance["code_hashes"]) is not dict or not 1 <= len(provenance["code_hashes"]) <= 32:
    raise ValueError("invalid_report_code_hashes")
  for path, sha in provenance["code_hashes"].items():
    if type(path) is not str or len(path) > 180 or not path.startswith("openpilot/") or ".." in path or "\\" in path:
      raise ValueError("invalid_report_code_path")
    _sha(sha)
  if type(provenance["mask_versions"]) is not list or len(provenance["mask_versions"]) > 128:
    raise ValueError("invalid_report_masks")
  for mask in provenance["mask_versions"]:
    _identifier(mask)
  registry = load_registry()["items"]
  if type(report["items"]) is not list or len(report["items"]) != len(registry):
    raise ValueError("report_requires_all_47_keys")
  for row, item in zip(report["items"], registry):
    _keys(row, {"key", "axis", "family", "status", "current_raw", "current_physical", "candidate_raw", "candidate_physical", "raw_estimate", "estimate_kind",
                "physical_estimate", "method_id", "backend", "prediction_kind", "metrics", "validation", "reason_codes", "provenance"})
    if any(row[key] != item[key] for key in ("key", "axis", "family")) or row["status"] not in _STATES:
      raise ValueError("invalid_report_inventory_or_status")
    for key in ("method_id", "backend"):
      _identifier(row[key])
    if row["prediction_kind"] is not None and row["prediction_kind"] not in _KINDS:
      raise ValueError("invalid_prediction_kind")
    for key in ("current_raw", "candidate_raw"):
      if row[key] is not None:
        _integer(row[key], -1000000, 1000000)
    for key in ("current_physical", "candidate_physical", "raw_estimate", "physical_estimate"):
      if row[key] is not None:
        _number(row[key])
    if row["estimate_kind"] not in {None, "parameter_calibration", "diagnostic_proxy"} or ((row["raw_estimate"] is None) != (row["estimate_kind"] is None)):
      raise ValueError("invalid_estimate_kind")
    if any(row[physical] != _physical(item, row[raw]) for raw, physical in (("current_raw", "current_physical"), ("candidate_raw", "candidate_physical"), ("raw_estimate", "physical_estimate"))):
      raise ValueError("report_physical_conversion_mismatch")
    _keys(row["metrics"], {"baseline", "selection", "holdout"})
    _keys(row["metrics"]["baseline"], {"select", "holdout"})
    for metric in [*row["metrics"]["baseline"].values(), row["metrics"]["selection"], row["metrics"]["holdout"]]:
      if metric is not None:
        _keys(metric, _METRIC_KEYS)
        _integer(metric["rows"], 1, 100000)
        _integer(metric["blocks"], 1, 128)
        for key in ("rmse", "max_absolute_error", "max_absolute_prediction"):
          _number(metric[key], 0)
        _sha(metric["trace_digest"])
    validation = row["validation"]
    _keys(validation, {"numerical_checks_pass", "split_independent", "holdout_objective_pass", "independent_route_reproduced", "device_qualified"})
    if any(type(value) is not bool for value in validation.values()) or validation["independent_route_reproduced"] or validation["device_qualified"]:
      raise ValueError("invalid_report_validation_claim")
    if row["status"] == "search_candidate":
      if row["estimate_kind"] == "diagnostic_proxy":
        raise ValueError("diagnostic_proxy_is_not_candidate")
      if row["candidate_raw"] is None or not all(validation[key] for key in ("numerical_checks_pass", "split_independent", "holdout_objective_pass")) or row["metrics"]["selection"] is None or row["metrics"]["holdout"] is None:
        raise ValueError("candidate_missing_validation")
      ui = item.get("ui")
      if row["key"] in _CONTEXT_ONLY or row["key"] == "LateralTorqueKd" or not ui or row["current_raw"] is None or not ui["min"] <= row["candidate_raw"] <= ui["max"] or (row["candidate_raw"] - row["current_raw"]) % ui["step"]:
        raise ValueError("candidate_not_quantized_numeric_key")
      if row["candidate_raw"] == row["current_raw"] or {row["candidate_raw"], row["current_raw"]} & _MODE_SENTINELS.get(row["key"], set()) or (row["key"] == "CustomSR" and min(row["candidate_raw"], row["current_raw"]) <= 10):
        raise ValueError("candidate_changes_mode_or_inheritance")
    elif row["candidate_raw"] is not None:
      raise ValueError("noncandidate_contains_candidate")
    if type(row["reason_codes"]) is not list or not 1 <= len(row["reason_codes"]) <= 24:
      raise ValueError("invalid_report_reasons")
    for reason in row["reason_codes"]:
      _identifier(reason)
    binding = row["provenance"]
    _keys(binding, {"epoch_id", "objective_id", "consumer_ids", "excluded_rows", "provisional_rows"})
    if binding["epoch_id"] != provenance["epoch_id"]:
      raise ValueError("report_epoch_binding_mismatch")
    if binding["objective_id"] is not None:
      _identifier(binding["objective_id"])
    if type(binding["consumer_ids"]) is not list or len(binding["consumer_ids"]) > 10:
      raise ValueError("invalid_report_consumer_ids")
    from .consumer_observer import consumer_ids_for_key
    if set(binding["consumer_ids"]) - set(consumer_ids_for_key(item["key"])):
      raise ValueError("invalid_report_consumer_binding")
    _integer(binding["excluded_rows"], 0, 100000)
    _integer(binding["provisional_rows"], 0, 100000)
  encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
  if len(encoded.encode()) > 256 * 1024:
    raise ValueError("candidate_report_budget_exceeded")
  return json.loads(encoded)
