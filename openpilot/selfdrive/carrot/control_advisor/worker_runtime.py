"""Current-worker execution policy for FMS control advisor worker.

Applies native Linux execution constraints (CPU affinity {0}, SCHED_OTHER,
nice >= 15) before any worker threads or transports start.
"""

from __future__ import annotations

import os
from typing import Any

SCHEMA_VERSION = 1
REQUESTED_POLICY: dict[str, Any] = {
  "cpus": [0],
  "scheduler": "SCHED_OTHER",
  "nice": 15,
}

VALID_STATES = {"applied", "degraded", "not_applicable"}
VALID_REASONS = {
  None,
  "native_worker_policy_incomplete",
  "synthetic_mode",
  "non_native_runtime",
}

CANONICAL_KEYS = {
  "schema_version",
  "state",
  "reason",
  "requested",
  "effective",
  "errors",
}
REQUESTED_KEYS = {"cpus", "scheduler", "nice"}
EFFECTIVE_KEYS = {"cpus", "scheduler", "nice"}
ERROR_CODES = {"runtime_detection_failed", "affinity_set_failed", "affinity_get_failed", "scheduler_set_failed",
               "scheduler_get_failed", "nice_set_failed", "nice_get_failed"}


def _add_error(errors: list[str], code: str) -> None:
  if code not in errors and len(errors) < 16:
    errors.append(code)


def configure_worker_runtime(mode: str) -> dict[str, Any]:
  """Configure calling process/thread execution policy for advisor worker."""
  if not isinstance(mode, str) or mode not in ("live", "synthetic"):
    raise ValueError(f"invalid mode: {mode!r}")

  if mode == "synthetic":
    return {
      "schema_version": SCHEMA_VERSION,
      "state": "not_applicable",
      "reason": "synthetic_mode",
      "requested": {
        "cpus": list(REQUESTED_POLICY["cpus"]),
        "scheduler": REQUESTED_POLICY["scheduler"],
        "nice": REQUESTED_POLICY["nice"],
      },
      "effective": {"cpus": None, "scheduler": None, "nice": None},
      "errors": [],
    }

  # Lazy import native detection: sys.platform linux and /TICI
  try:
    from .consumer_observer import _native_runtime
    is_native = bool(_native_runtime())
  except Exception:
    return {
      "schema_version": SCHEMA_VERSION,
      "state": "degraded",
      "reason": "native_worker_policy_incomplete",
      "requested": {
        "cpus": list(REQUESTED_POLICY["cpus"]),
        "scheduler": REQUESTED_POLICY["scheduler"],
        "nice": REQUESTED_POLICY["nice"],
      },
      "effective": {"cpus": None, "scheduler": None, "nice": None},
      "errors": ["runtime_detection_failed"],
    }

  if not is_native:
    return {
      "schema_version": SCHEMA_VERSION,
      "state": "not_applicable",
      "reason": "non_native_runtime",
      "requested": {
        "cpus": list(REQUESTED_POLICY["cpus"]),
        "scheduler": REQUESTED_POLICY["scheduler"],
        "nice": REQUESTED_POLICY["nice"],
      },
      "effective": {"cpus": None, "scheduler": None, "nice": None},
      "errors": [],
    }

  errors: list[str] = []

  # 1. CPU affinity: target calling thread/process 0 only: {0}
  try:
    if hasattr(os, "sched_setaffinity"):
      os.sched_setaffinity(0, {0})
    else:
      _add_error(errors, "affinity_set_failed")
  except Exception:
    _add_error(errors, "affinity_set_failed")

  effective_cpus: list[int] | None = None
  try:
    if hasattr(os, "sched_getaffinity"):
      raw_affinity = os.sched_getaffinity(0)
      effective_cpus = sorted(int(c) for c in raw_affinity)
    else:
      _add_error(errors, "affinity_get_failed")
  except Exception:
    _add_error(errors, "affinity_get_failed")

  # 2. Scheduler: SCHED_OTHER, sched_param(0)
  try:
    sched_other = getattr(os, "SCHED_OTHER", 0)
    param = os.sched_param(0) if hasattr(os, "sched_param") else 0
    if hasattr(os, "sched_setscheduler"):
      os.sched_setscheduler(0, sched_other, param)
    else:
      _add_error(errors, "scheduler_set_failed")
  except Exception:
    _add_error(errors, "scheduler_set_failed")

  effective_scheduler: int | None = None
  try:
    if hasattr(os, "sched_getscheduler"):
      effective_scheduler = int(os.sched_getscheduler(0))
    else:
      _add_error(errors, "scheduler_get_failed")
  except Exception:
    _add_error(errors, "scheduler_get_failed")

  # 3. Nice: max(15, current_nice); never raise priority
  prio_who = getattr(os, "PRIO_PROCESS", 0)
  current_nice: int | None = None
  try:
    if hasattr(os, "getpriority"):
      current_nice = int(os.getpriority(prio_who, 0))
    else:
      _add_error(errors, "nice_get_failed")
  except Exception:
    _add_error(errors, "nice_get_failed")

  if current_nice is not None:
    target_nice = max(15, current_nice)
    if target_nice > current_nice:
      try:
        if hasattr(os, "setpriority"):
          os.setpriority(prio_who, 0, target_nice)
        else:
          _add_error(errors, "nice_set_failed")
      except Exception:
        _add_error(errors, "nice_set_failed")

  effective_nice: int | None = None
  try:
    if hasattr(os, "getpriority"):
      effective_nice = int(os.getpriority(prio_who, 0))
    else:
      _add_error(errors, "nice_get_failed")
  except Exception:
    _add_error(errors, "nice_get_failed")

  sched_other_target = getattr(os, "SCHED_OTHER", 0)
  applied = (
    not errors
    and effective_cpus == [0]
    and effective_scheduler == sched_other_target
    and effective_nice is not None
    and effective_nice >= 15
  )

  if applied:
    state = "applied"
    reason = None
  else:
    state = "degraded"
    reason = "native_worker_policy_incomplete"

  return {
    "schema_version": SCHEMA_VERSION,
    "state": state,
    "reason": reason,
    "requested": {
      "cpus": list(REQUESTED_POLICY["cpus"]),
      "scheduler": REQUESTED_POLICY["scheduler"],
      "nice": REQUESTED_POLICY["nice"],
    },
    "effective": {
      "cpus": effective_cpus,
      "scheduler": effective_scheduler,
      "nice": effective_nice,
    },
    "errors": errors[:16],
  }


def validate_worker_runtime(value: Any) -> dict[str, Any]:
  """Validate and return detached canonical copy of worker runtime metadata."""
  if not isinstance(value, dict):
    raise ValueError("worker_runtime_payload_must_be_dict")

  if set(value.keys()) != CANONICAL_KEYS:
    raise ValueError("invalid_worker_runtime_keys")

  if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
    raise ValueError("invalid_schema_version")

  state = value["state"]
  if type(state) is not str or state not in VALID_STATES:
    raise ValueError(f"invalid_state: {state!r}")

  reason = value["reason"]
  if reason is not None and (type(reason) is not str or reason not in VALID_REASONS):
    raise ValueError(f"invalid_reason: {reason!r}")

  if state == "applied":
    if reason is not None:
      raise ValueError("applied_must_have_null_reason")
  elif state == "degraded":
    if reason != "native_worker_policy_incomplete":
      raise ValueError("degraded_reason_must_be_native_worker_policy_incomplete")
  elif state == "not_applicable":
    if reason not in ("synthetic_mode", "non_native_runtime"):
      raise ValueError("not_applicable_reason_invalid")

  requested = value["requested"]
  if not isinstance(requested, dict) or set(requested.keys()) != REQUESTED_KEYS:
    raise ValueError("invalid_requested_payload")
  if type(requested["cpus"]) is not list or len(requested["cpus"]) != 1 or type(requested["cpus"][0]) is not int or requested["cpus"] != [0]:
    raise ValueError("invalid_requested_cpus")
  if requested["scheduler"] != "SCHED_OTHER":
    raise ValueError("invalid_requested_scheduler")
  if type(requested["nice"]) is not int or requested["nice"] != 15:
    raise ValueError("invalid_requested_nice")

  errors = value["errors"]
  if not isinstance(errors, list):
    raise ValueError("errors_must_be_list")
  if len(errors) > 16:
    raise ValueError("errors_exceed_max_16")
  for err in errors:
    if type(err) is not str or err not in ERROR_CODES:
      raise ValueError("invalid_error_entry")

  effective = value["effective"]
  if not isinstance(effective, dict) or set(effective.keys()) != EFFECTIVE_KEYS:
    raise ValueError("invalid_effective_payload")

  cpus = effective["cpus"]
  if cpus is not None:
    if not isinstance(cpus, list):
      raise ValueError("effective_cpus_must_be_list_or_null")
    if len(cpus) > 256:
      raise ValueError("effective_cpus_exceed_max_length_256")
    for cpu in cpus:
      if type(cpu) is not int or cpu < 0 or cpu > 65535:
        raise ValueError("invalid_effective_cpu_id")
    if cpus != sorted(set(cpus)):
      raise ValueError("invalid_effective_cpu_order")

  sched = effective["scheduler"]
  if sched is not None:
    if type(sched) is not int or not 0 <= sched <= 255:
      raise ValueError("effective_scheduler_must_be_int_or_null")

  nice_val = effective["nice"]
  if nice_val is not None:
    if type(nice_val) is not int or not (-20 <= nice_val <= 19):
      raise ValueError("effective_nice_out_of_range")

  if state == "not_applicable":
    if cpus is not None or sched is not None or nice_val is not None:
      raise ValueError("not_applicable_effective_values_must_be_null")
    if len(errors) != 0:
      raise ValueError("not_applicable_errors_must_be_empty")

  if state == "applied":
    if len(errors) != 0:
      raise ValueError("applied_state_cannot_have_errors")
    if cpus != [0]:
      raise ValueError("applied_effective_cpus_must_be_single_zero")
    if sched != 0:
      raise ValueError("applied_effective_scheduler_must_be_zero")
    if nice_val is None or nice_val < 15:
      raise ValueError("applied_effective_nice_must_be_at_least_15")

  return {
    "schema_version": SCHEMA_VERSION,
    "state": state,
    "reason": reason,
    "requested": {
      "cpus": [0],
      "scheduler": "SCHED_OTHER",
      "nice": 15,
    },
    "effective": {
      "cpus": list(cpus) if cpus is not None else None,
      "scheduler": sched,
      "nice": nice_val,
    },
    "errors": list(errors),
  }
