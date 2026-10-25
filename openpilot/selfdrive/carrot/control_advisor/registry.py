"""Read-only parameter inventory. Menu limits are not tuning or safety limits."""
from __future__ import annotations

import json
from pathlib import Path


def load_registry() -> dict:
  return json.loads(Path(__file__).with_name("parameters.json").read_text(encoding="utf-8"))


def readiness(settings: dict | None = None) -> list[dict]:
  """Report development readiness without treating data quality as implementation."""
  from .consumer_observer import consumer_ids_for_key
  from .candidates import candidate_capabilities
  capabilities = {item["key"]: item for item in candidate_capabilities()}
  settings = settings or {}
  rows = []
  for item in load_registry()["items"]:
    key = item["key"]
    raw = settings.get(key)
    special = item.get("special_values", {})
    special_meaning = special.get(str(raw)) if raw is not None else None
    scale = item.get("raw_scale")
    value = raw * scale if type(raw) in (int, float) and scale is not None and special_meaning is None else None
    calculation = capabilities[key]
    implemented = calculation["backend"] != "unavailable" and not calculation["reason_codes"]
    implementation = "offline_calculation" if implemented else "backend_unavailable"
    if calculation["backend"] in ("raw_proxy_only", "raw_delay_only"):
      implementation = "raw_estimator"
    if "context_only_no_automatic_mode_switch" in calculation["reason_codes"]:
      implementation = "context_only"
    elif "derivative_path_has_no_effect" in calculation["reason_codes"]:
      implementation = "unsupported_path"
    rows.append({"key": key, "axis": item["axis"], "family": item["family"],
                 "classification": "numeric_parameter" if key == "CustomSR" else item.get("classification", "numeric_parameter"),
                 "current_raw": raw, "current_physical": value, "physical_unit": item.get("physical_unit"),
                 "special_meaning": special_meaning, "special_values": special,
                 "implementation_status": implementation,
                 "validation_status": item["validation_status"], "candidate_calculation": calculation,
                 "structural_blocker": item.get("structural_blocker"),
                 "path_requirements": item.get("requires", []),
                 "consumer_ids": consumer_ids_for_key(key), "consumer_application_ack": False,
                 "candidate_status": "offline_evidence_required" if implemented else "diagnostic_estimate_only" if implementation == "raw_estimator" else "backend_unavailable", "apply_enabled": False})
  return rows
