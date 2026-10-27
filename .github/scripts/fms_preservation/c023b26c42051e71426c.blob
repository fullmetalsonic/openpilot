"""Durable requests with explicitly injected PC or guarded native storage.

Usage: IsolatedPCStore(root, target_id, initial_values).request_binding() feeds
the trusted evidence resolver. ApplyRequestController(..., enabled=True,
pc_store=store).register(body) only registers; execute(request_id) performs a
single PC CAS. A separate consumer/comparison provider may advance review().
No Params construction, automatic retry, or consumer application ACK exists.
All public methods may perform bounded disk/resolver I/O: call off the UI loop.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
import uuid

from .candidate_report import _read
from .evidence import _sync_directory
from .registry import load_registry


KEYS = frozenset(item["key"] for item in load_registry()["items"])
BODY_FIELDS = {"request_id", "target_id", "session_id", "artifact_sha256", "result_revision", "item_key",
               "expected_current_raw", "expected_settings_revision", "dependency_digest"}
STATES = {"registered", "held", "intent", "stored", "consumer_observed", "comparison_ready", "cancelled", "expired", "rejected", "indeterminate"}
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ZERO = "0" * 64


class RequestConflict(ValueError):
  pass


class RequestDisabled(ValueError):
  pass


class RequestNotFound(LookupError):
  pass


def _canonical(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value):
  return hashlib.sha256(_canonical(value)).hexdigest()


def _integer(value, minimum=0, maximum=2**63 - 1):
  if type(value) is not int or not minimum <= value <= maximum:
    raise ValueError("invalid_request_integer")


def _identifier(value):
  if type(value) is not str or _ID.fullmatch(value) is None:
    raise ValueError("invalid_request_identifier")


def _sha(value):
  if type(value) is not str or _SHA.fullmatch(value) is None:
    raise ValueError("invalid_request_hash")


def _fields(value, required):
  if type(value) is not dict or set(value) != set(required):
    raise ValueError("invalid_request_fields")


def validate_request(body):
  trial = type(body) is dict and body.get("purpose") == "manual_trial"
  _fields(body, BODY_FIELDS | {"purpose", "trial_id"} if trial else BODY_FIELDS)
  if trial:
    if body["item_key"] not in {"SteerActuatorDelay", "LatSmoothSec", "LongActuatorDelay"}:
      raise ValueError("manual_trial_parameter_invalid")
    _sha(body["trial_id"])
  for name in ("request_id", "target_id", "session_id"):
    _identifier(body[name])
  if body["item_key"] not in KEYS:
    raise ValueError("unknown_request_parameter")
  for name in ("artifact_sha256", "dependency_digest"):
    _sha(body[name])
  for name in ("result_revision", "expected_settings_revision"):
    _integer(body[name])
  if body["expected_current_raw"] is not None:
    _integer(body["expected_current_raw"], -1000000, 1000000)
  return deepcopy(body)


def validate_request_status(value):
  fields = {"request_id", "target_id", "session_id", "artifact_sha256", "result_revision", "item_key", "scope", "state", "reason",
            "created_ns", "expires_at_ns", "current_raw", "candidate_raw", "settings_revision", "store_receipt", "consumer_observed",
            "comparison_ready", "comparison", "request_registration_enabled", "pc_simulation_enabled", "recommendations_enabled", "apply_enabled", "device_qualified"}
  trial = type(value) is dict and value.get("purpose") == "manual_trial"
  _fields(value, fields | {"purpose", "trial_id"} if trial else fields)
  if trial:
    if value["item_key"] not in {"SteerActuatorDelay", "LatSmoothSec", "LongActuatorDelay"}:
      raise ValueError("manual_trial_parameter_invalid")
    _sha(value["trial_id"])
  for key in ("request_id", "target_id", "session_id"):
    _identifier(value[key])
  _sha(value["artifact_sha256"])
  if value["item_key"] not in KEYS or value["scope"] not in {"vehicle", "pc_simulation"} or value["state"] not in STATES:
    raise ValueError("invalid_request_status")
  if value["reason"] is not None:
    _identifier(value["reason"])
  for key in ("created_ns", "expires_at_ns", "settings_revision", "result_revision"):
    _integer(value[key])
  for key in ("current_raw", "candidate_raw"):
    if key != "current_raw" or value[key] is not None:
      _integer(value[key], -1000000, 1000000)
  for key in ("consumer_observed", "comparison_ready", "request_registration_enabled", "pc_simulation_enabled"):
    if type(value[key]) is not bool:
      raise ValueError("invalid_request_status_boolean")
  if any(value[key] is not False for key in ("recommendations_enabled", "device_qualified")) or type(value["apply_enabled"]) is not bool:
    raise ValueError("request_qualification_claim")
  if value["consumer_observed"] != (value["state"] in {"consumer_observed", "comparison_ready"}) or value["comparison_ready"] != (value["state"] == "comparison_ready"):
    raise ValueError("request_observation_state_mismatch")
  if value["apply_enabled"] and (value["scope"] != "vehicle" or not value["request_registration_enabled"] or value["state"] != "registered"):
    raise ValueError("request_apply_readiness_invalid")
  if value["state"] == "held" and value["store_receipt"] is not None:
    raise ValueError("held_request_has_receipt")
  if value["store_receipt"] is not None:
    receipt = value["store_receipt"]
    _fields(receipt, {"request_id", "revision", "value"})
    if receipt["request_id"] != value["request_id"] or receipt["value"] != value["candidate_raw"]:
      raise ValueError("request_receipt_mismatch")
    _integer(receipt["revision"], 1)
  if value["comparison"] is not None:
    from .comparison import validate_public_comparison
    validate_public_comparison(value["comparison"])
  if len(_canonical(value)) > 96 * 1024:
    raise ValueError("request_public_status_budget")
  return deepcopy(value)


def _values(values):
  if type(values) is not dict or set(values) - KEYS:
    raise ValueError("invalid_pc_values")
  for value in values.values():
    if value is not None:
      _integer(value, -1000000, 1000000)
  return deepcopy(values)


def _atomic(path, value):
  if path.is_symlink():
    raise ValueError("request_file_link")
  temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
  with temporary.open("xb") as stream:
    stream.write(_canonical(value))
    stream.flush()
    os.fsync(stream.fileno())
  os.replace(temporary, path)
  _sync_directory(path.parent)


def _root(path, marker):
  path = Path(path)
  if not path.is_absolute() or any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (path, *path.parents)):
    raise ValueError("request_root_requires_absolute_unlinked_directory")
  path.mkdir(mode=0o700, parents=False, exist_ok=True)
  marker_path = path / "owner.json"
  if marker_path.exists():
    if _read(marker_path, 1024)[0] != marker:
      raise ValueError("request_root_not_owned")
  elif any(path.iterdir()):
    raise ValueError("request_root_not_empty")
  from .recording import _RootLease
  lease = _RootLease(path)
  try:
    if not marker_path.exists():
      _atomic(marker_path, marker)
    return path, lease
  except BaseException:
    lease.close()
    raise


class IsolatedPCStore:
  """A fake file namespace, with durable atomic values/revision/request receipt."""
  scope = "pc_simulation"

  def __init__(self, root, target_id, initial_values, *, session_id=None, generation=0, clock_ns=time.monotonic_ns):
    _identifier(target_id)
    _integer(generation)
    self.clock_ns, self._lock = clock_ns, threading.RLock()
    self.root, self._lease = _root(root, {"schema_version": 1, "scope": "isolated_pc_simulation_store"})
    self.path, self.target_id = self.root / "pc-store.json", target_id
    self._closed = False
    try:
      if self.path.exists():
        self._state, _ = _read(self.path, 256 * 1024)
        _fields(self._state, {"schema_version", "target_id", "store_id", "session_id", "generation", "revision", "values", "receipts"})
        if self._state["schema_version"] != 1 or self._state["target_id"] != target_id:
          raise ValueError("pc_store_identity_mismatch")
        _values(self._state["values"])
        _integer(self._state["revision"])
        if type(self._state["receipts"]) is not dict or len(self._state["receipts"]) > 128:
          raise ValueError("pc_store_receipt_budget")
      else:
        values = {key: None for key in KEYS}
        values.update(_values(initial_values))
        self._state = {"schema_version": 1, "target_id": target_id, "store_id": uuid.uuid4().hex,
                       "session_id": session_id or uuid.uuid4().hex, "generation": generation, "revision": 0,
                       "values": values, "receipts": {}}
        _identifier(self._state["session_id"])
        _atomic(self.path, self._state)
    except BaseException:
      self._lease.close()
      raise

  def snapshot(self):
    with self._lock:
      if _read(self.path, 256 * 1024)[0] != self._state:
        raise RequestConflict("pc_store_changed")
      return {key: deepcopy(self._state[key]) for key in ("target_id", "store_id", "session_id", "generation", "revision", "values")}

  def request_binding(self):
    value = self.snapshot()
    return {"session_id": value["session_id"], "generation": value["generation"], "source_mode": "pc_simulation", "state": "running",
            "settings_revision": value["revision"], "settings_raw": value["values"], "observed_mono_ns": self.clock_ns(), "settings_observation_fresh": True}

  def request_receipt(self, request_id):
    with self._lock:
      self.snapshot()
      return deepcopy(self._state["receipts"].get(request_id))

  def compare_and_swap(self, *, request_id, request_sha256, item_key, expected_current_raw, expected_settings_revision,
                       expected_dependencies, dependency_digest, candidate_raw):
    _identifier(request_id)
    _sha(request_sha256)
    _sha(dependency_digest)
    _values(expected_dependencies)
    if item_key not in KEYS:
      raise ValueError("unknown_pc_parameter")
    for value in (expected_current_raw, candidate_raw):
      _integer(value, -1000000, 1000000)
    _integer(expected_settings_revision)
    with self._lock:
      if self._closed:
        raise RequestDisabled("pc_store_closed")
      self.snapshot()
      old = self._state["receipts"].get(request_id)
      if old is not None:
        if old["request_sha256"] != request_sha256:
          raise RequestConflict("pc_request_id_conflict")
        return deepcopy(old)
      if len(self._state["receipts"]) >= 128:
        raise RequestConflict("pc_store_receipt_budget")
      if (self._state["revision"] != expected_settings_revision or self._state["values"].get(item_key) != expected_current_raw
          or any(self._state["values"].get(key) != value for key, value in expected_dependencies.items())):
        raise RequestConflict("compare_and_swap_conflict")
      receipt = {"schema_version": 1, "request_id": request_id, "request_sha256": request_sha256, "target_id": self.target_id,
                 "store_id": self._state["store_id"], "item_key": item_key, "previous_raw": expected_current_raw,
                 "value": candidate_raw, "previous_revision": expected_settings_revision, "revision": expected_settings_revision + 1,
                 "dependency_digest": dependency_digest, "stored_mono_ns": self.clock_ns()}
      updated = deepcopy(self._state)
      updated["values"][item_key], updated["revision"] = candidate_raw, receipt["revision"]
      updated["receipts"][request_id] = receipt
      _atomic(self.path, updated)
      self._state = updated
      return deepcopy(receipt)

  def close(self):
    with self._lock:
      if not self._closed:
        self._closed = True
        self._lease.close()


def _context(value):
  required = {"target_id", "scope", "session_id", "session_generation", "artifact_sha256", "result_revision", "item_key", "current_raw", "candidate_raw",
              "settings_revision", "dependencies", "dependency_digest", "pointer_token", "resolved_at_ns", "expires_at_ns", "provenance"}
  _fields(value, required)
  for name in ("target_id", "session_id"):
    _identifier(value[name])
  if value["scope"] not in {"vehicle", "pc_simulation"} or value["item_key"] not in KEYS:
    raise ValueError("invalid_request_scope_or_key")
  if value["scope"] == "pc_simulation" and value["current_raw"] is None:
    raise ValueError("invalid_pc_current_raw")
  for name in ("artifact_sha256", "dependency_digest", "pointer_token"):
    _sha(value[name])
  for name in ("session_generation", "result_revision", "settings_revision", "resolved_at_ns", "expires_at_ns"):
    _integer(value[name])
  for name in ("current_raw", "candidate_raw"):
    if name != "current_raw" or value[name] is not None:
      _integer(value[name], -1000000, 1000000)
  _values(value["dependencies"])
  if value["expires_at_ns"] < value["resolved_at_ns"] or value["candidate_raw"] == value["current_raw"]:
    raise ValueError("invalid_request_candidate_or_expiry")
  provenance = value["provenance"]
  provenance_fields = {"evidence_sha256", "profile_sha256", "code_hashes", "source_epoch_id", "route", "boot_id", "settings_history_reference", "evidence_reference",
                       "comparison_profile", "comparison_profile_sha256"}
  identity_fields = {"identity_version", "vehicle_identity"}
  if type(provenance) is not dict or set(provenance) - identity_fields != provenance_fields:
    raise ValueError("invalid_request_fields")
  if set(provenance) & identity_fields:
    from .vehicle_identity import validate_identity
    if not identity_fields <= set(provenance) or type(provenance["identity_version"]) is not int or provenance["identity_version"] != 1:
      raise ValueError("request_vehicle_identity_invalid")
    validate_identity(provenance["vehicle_identity"])
  for name in ("evidence_sha256", "profile_sha256"):
    _sha(provenance[name])
  comparison_profile = provenance["comparison_profile"]
  if comparison_profile is None:
    if provenance["comparison_profile_sha256"] is not None:
      raise ValueError("comparison_profile_binding_invalid")
  else:
    _sha(provenance["comparison_profile_sha256"])
    if type(comparison_profile) is not dict or len(_canonical(comparison_profile)) > 16 * 1024 or _hash(comparison_profile) != provenance["comparison_profile_sha256"]:
      raise ValueError("comparison_profile_binding_invalid")
  # Analyzer and actual-consumer manifests together exceed 64 source files.
  # Keep the path/digest checks and the overall 32 KiB context budget below.
  if type(provenance["code_hashes"]) is not dict or not 1 <= len(provenance["code_hashes"]) <= 128:
    raise ValueError("invalid_request_code_provenance")
  for path, digest in provenance["code_hashes"].items():
    if type(path) is not str or len(path) > 180 or ".." in path or "\\" in path:
      raise ValueError("invalid_request_code_path")
    _sha(digest)
  if len(_canonical(value)) > 32 * 1024:
    raise ValueError("request_context_budget")
  return deepcopy(value)


class ApplyRequestController:
  def __init__(self, root, resolver, *, pc_store=None, vehicle_store=None, enabled=False, comparison_provider=None, clock_ns=time.monotonic_ns,
               ttl_ns=300_000_000_000, max_requests=128, max_events=2048, max_bytes=4 * 1024 * 1024):
    self._lock, self._records, self._executing = threading.RLock(), {}, set()
    self._sequence, self._chain, self._bytes = 0, _ZERO, 0
    self._closed, self._enabled, self._poisoned, self._lease = False, bool(enabled), False, None
    self.resolver, self.store, self.comparison_provider, self.clock_ns = resolver, pc_store if vehicle_store is None else vehicle_store, comparison_provider, clock_ns
    self.ttl_ns, self.max_requests, self.max_events, self.max_bytes = ttl_ns, max_requests, max_events, max_bytes
    for value in (ttl_ns, max_requests, max_events, max_bytes):
      _integer(value, 1)
    if max_requests > 128 or max_events > 10000 or max_bytes > 16 * 1024 * 1024:
      raise ValueError("request_budget_too_large")
    if pc_store is not None and not isinstance(pc_store, IsolatedPCStore):
      raise ValueError("isolated_pc_store_required")
    if vehicle_store is not None:
      from .vehicle_store import NativeParamsStore
      from .native_runtime import NativeRuntime
      if pc_store is not None or not isinstance(vehicle_store, (NativeParamsStore, NativeRuntime)):
        raise ValueError("exclusive_native_vehicle_store_required")
    if not self._enabled:
      self.root = self.path = None
      return
    self.root, self._lease = _root(root, {"schema_version": 1, "scope": "control_advisor_request_ledger"})
    self.path = self.root / "requests.jsonl"
    try:
      self.recover()
    except BaseException:
      self._lease.close()
      raise

  def _available(self):
    if not self._enabled or self._closed or self._poisoned:
      raise RequestDisabled("request_ledger_uncertain" if self._poisoned else "request_registration_disabled")

  def _append(self, record):
    self._available()
    envelope = {"schema_version": 1, "sequence": self._sequence + 1, "previous_sha256": self._chain, "record": record}
    digest = _hash(envelope)
    encoded = _canonical({**envelope, "sha256": digest}) + b"\n"
    if len(encoded) > 64 * 1024 or self._sequence >= self.max_events or self._bytes + len(encoded) > self.max_bytes:
      raise RequestConflict("request_ledger_budget")
    if self.path.is_symlink():
      raise ValueError("request_ledger_link")
    try:
      with self.path.open("ab") as stream:
        written = stream.write(encoded)
        if written != len(encoded):
          raise OSError("request_ledger_short_write")
        stream.flush()
        os.fsync(stream.fileno())
      if self._sequence == 0:
        _sync_directory(self.root)
      self._replay(record)
      self._sequence, self._chain, self._bytes = envelope["sequence"], digest, self._bytes + len(encoded)
    except BaseException:
      # Disk may contain any prefix of this record, even when fsync failed.
      # Reusing the sequence would corrupt recovery; never append again here.
      self._poisoned = True
      raise

  def _replay(self, event):
    if event["kind"] == "register":
      record = deepcopy(event["record"])
      request_id = record["body"]["request_id"]
      validate_request(record["body"])
      _context(record["context"])
      if request_id in self._records or record["request_sha256"] != _hash(record["body"]) or record["state"] not in {"registered", "held"}:
        raise ValueError("request_ledger_registration_invalid")
      self._records[request_id] = record
      return
    _fields(event, {"kind", "request_id", "state", "reason", "at_ns", "receipt", "comparison"})
    if event["kind"] != "transition" or event["request_id"] not in self._records or event["state"] not in STATES:
      raise ValueError("request_ledger_transition_invalid")
    record = self._records[event["request_id"]]
    allowed = {"registered": {"intent", "cancelled", "expired", "rejected"}, "intent": {"stored", "rejected", "indeterminate"},
               "stored": {"consumer_observed"}, "consumer_observed": {"comparison_ready"}}
    review_transition = False
    if event["comparison"] is not None and record["state"] in {"stored", "consumer_observed", "comparison_ready"}:
      desired = "stored" if event["comparison"]["state"] == "held" else event["comparison"]["state"]
      review_transition = event["state"] == desired or (record["state"] == "stored" and desired == "comparison_ready" and event["state"] == "consumer_observed")
    if event["state"] not in allowed.get(record["state"], set()) and not review_transition:
      raise ValueError("request_ledger_state_regression")
    record.update(state=event["state"], reason=event["reason"])
    if event["receipt"] is not None:
      _integer(event["receipt"]["stored_mono_ns"])
      record["receipt"], record["stored_mono_ns"] = deepcopy(event["receipt"]), event["receipt"]["stored_mono_ns"]
    if event["comparison"] is not None:
      record["comparison"] = deepcopy(event["comparison"])

  def _transition(self, request_id, state, reason=None, *, receipt=None, comparison=None):
    self._append({"kind": "transition", "request_id": request_id, "state": state, "reason": reason, "at_ns": self.clock_ns(),
                  "receipt": receipt, "comparison": comparison})

  def recover(self):
    with self._lock:
      self._available()
      self._records, self._sequence, self._chain, self._bytes = {}, 0, _ZERO, 0
      if self.path.exists():
        if self.path.stat().st_size > self.max_bytes or self.path.is_symlink():
          raise ValueError("request_ledger_budget_or_link")
        with self.path.open("rb") as stream:
          for line in stream:
            if len(line) > 64 * 1024 or not line.endswith(b"\n") or self._sequence >= self.max_events:
              raise ValueError("request_ledger_incomplete")
            from .candidate_report import _json as parse_json
            envelope = parse_json(line)
            _fields(envelope, {"schema_version", "sequence", "previous_sha256", "record", "sha256"})
            digest = envelope.pop("sha256")
            if envelope["schema_version"] != 1 or envelope["sequence"] != self._sequence + 1 or envelope["previous_sha256"] != self._chain or digest != _hash(envelope):
              raise ValueError("request_ledger_corrupt")
            self._replay(envelope["record"])
            self._sequence, self._chain, self._bytes = envelope["sequence"], digest, self._bytes + len(line)
      for request_id, record in list(self._records.items()):
        if record["state"] == "registered":
          self._transition(request_id, "rejected", "restart_reconfirmation_required")
        elif record["state"] == "intent":
          try:
            receipt = self.store.request_receipt(request_id) if self.store is not None else None
          except (OSError, ValueError):
            receipt = None
          if self._receipt_matches(record, receipt):
            self._transition(request_id, "stored", "recovered_native_receipt" if self.store.scope == "vehicle" else "recovered_pc_receipt", receipt=receipt)
          else:
            self._transition(request_id, "indeterminate", "crash_after_intent_uncertain")
      return self.list_requests()

  def _resolve(self, body):
    try:
      callback = self.resolver.resolve if hasattr(self.resolver, "resolve") else self.resolver
      value = _context(callback(deepcopy(body)))
    except RequestDisabled:
      raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
      reason = str(error)
      raise RequestConflict(reason if _ID.fullmatch(reason) else "candidate_evidence_unavailable") from error
    comparisons = (("target_id", "target_id"), ("session_id", "session_id"), ("artifact_sha256", "artifact_sha256"),
                   ("result_revision", "result_revision"), ("item_key", "item_key"), ("expected_current_raw", "current_raw"),
                   ("expected_settings_revision", "settings_revision"), ("dependency_digest", "dependency_digest"))
    now = self.clock_ns()
    if any(body[left] != value[right] for left, right in comparisons) or not value["resolved_at_ns"] <= now <= value["expires_at_ns"]:
      raise RequestConflict("candidate_binding_changed")
    return value

  def register(self, body):
    with self._lock:
      self._available()
    body = validate_request(body)
    request_id, digest = body["request_id"], _hash(body)
    with self._lock:
      self._available()
      if request_id in self._records:
        if self._records[request_id]["request_sha256"] != digest:
          raise RequestConflict("request_id_body_conflict")
        return self._public(self._records[request_id])
    try:
      context = self._resolve(body)
    except (RequestConflict, RequestDisabled):
      # Another identical HTTP registration may finish (and execute) while
      # this resolver is reading. Its durable operation wins over stale data.
      with self._lock:
        self._available()
        if request_id in self._records:
          if self._records[request_id]["request_sha256"] != digest:
            raise RequestConflict("request_id_body_conflict")
          return self._public(self._records[request_id])
      raise
    with self._lock:
      self._available()
      if request_id in self._records:
        if self._records[request_id]["request_sha256"] != digest:
          raise RequestConflict("request_id_body_conflict")
        return self._public(self._records[request_id])
      if len(self._records) >= self.max_requests:
        raise RequestConflict("request_count_budget")
      now = self.clock_ns()
      held = (self.store is None or self.store.target_id != context["target_id"] or self.store.scope != context["scope"]
              or context["scope"] == "vehicle" and not self.store.write_ready)
      if not held and context["scope"] == "vehicle":
        self.store.prepare_request(context)
      record = {"body": body, "request_sha256": digest, "context": context, "created_ns": now,
                "expires_at_ns": min(context["expires_at_ns"], now + self.ttl_ns), "state": "held" if held else "registered",
                "reason": ("vehicle_writer_unavailable" if context["scope"] == "vehicle" else "pc_simulation_unavailable") if held else None,
                "receipt": None, "stored_mono_ns": None, "comparison": None}
      self._append({"kind": "register", "record": record})
      return self._public(self._records[request_id])

  def _receipt_matches(self, record, receipt):
    if self.store is None or type(receipt) is not dict:
      return False
    context, body = record["context"], record["body"]
    expected = {"request_id": body["request_id"], "request_sha256": record["request_sha256"], "target_id": body["target_id"],
                "item_key": body["item_key"], "previous_raw": context["current_raw"], "value": context["candidate_raw"],
                "previous_revision": context["settings_revision"], "revision": context["settings_revision"] + 1, "dependency_digest": context["dependency_digest"]}
    try:
      if self.store.scope == "vehicle":
        return all(receipt.get(key) == value for key, value in expected.items()) and self.store.request_receipt(body["request_id"]) == receipt
      snapshot = self.store.snapshot()
    except (OSError, ValueError):
      return False
    return all(receipt.get(key) == value for key, value in expected.items()) and receipt.get("store_id") == snapshot["store_id"] and snapshot["values"].get(body["item_key"]) == context["candidate_raw"]

  def execute(self, request_id):
    with self._lock:
      self._available()
      record = self._get(request_id)
      if record["state"] != "registered" or request_id in self._executing:
        return self._public(record)
      if self.clock_ns() < record["created_ns"]:
        self._transition(request_id, "rejected", "request_clock_regressed")
        return self._public(record)
      if self.clock_ns() >= record["expires_at_ns"]:
        self._transition(request_id, "expired", "request_expired")
        return self._public(record)
      self._executing.add(request_id)
      body, old_context = deepcopy(record["body"]), deepcopy(record["context"])
    try:
      try:
        current = self._resolve(body)
      except (RequestConflict, RequestDisabled):
        with self._lock:
          if record["state"] == "registered":
            self._transition(request_id, "rejected", "candidate_binding_changed")
          return self._public(record)
      with self._lock:
        self._available()
        if record["state"] != "registered":
          return self._public(record)
        if self.clock_ns() >= record["expires_at_ns"]:
          self._transition(request_id, "expired", "request_expired")
          return self._public(record)
        if any(current[key] != old_context[key] for key in ("pointer_token", "session_generation", "scope", "dependencies", "candidate_raw", "provenance")):
          self._transition(request_id, "rejected", "candidate_binding_changed")
          return self._public(record)
        if self._sequence + 3 > self.max_events or self._bytes + 8192 > self.max_bytes:
          raise RequestConflict("request_ledger_budget")
        if current["scope"] == "vehicle":
          try:
            self.store.prepare_request(current)
          except (RequestConflict, RequestDisabled):
            self._transition(request_id, "rejected", "native_guard_or_observation_changed")
            return self._public(record)
        self._transition(request_id, "intent")
        try:
          receipt = self.store.compare_and_swap(request_id=request_id, request_sha256=record["request_sha256"], item_key=body["item_key"],
            expected_current_raw=current["current_raw"], expected_settings_revision=current["settings_revision"],
            expected_dependencies=current["dependencies"], dependency_digest=current["dependency_digest"], candidate_raw=current["candidate_raw"])
          if not self._receipt_matches(record, receipt):
            self._transition(request_id, "indeterminate", "native_receipt_unconfirmed" if current["scope"] == "vehicle" else "pc_readback_unconfirmed")
          else:
            self._transition(request_id, "stored", receipt=receipt)
        except RequestConflict:
          self._transition(request_id, "rejected", "compare_and_swap_conflict")
        except RequestDisabled:
          self._transition(request_id, "rejected", "write_guard_disabled")
        except Exception as error:
          from .vehicle_store import NativeStoreFailed
          if isinstance(error, NativeStoreFailed):
            self._transition(request_id, "rejected", "native_write_failed_known_nochange")
            return self._public(record)
          if self._poisoned:
            record.update(state="indeterminate", reason="request_ledger_write_uncertain")
          else:
            self._transition(request_id, "indeterminate", "native_write_unconfirmed" if current["scope"] == "vehicle" else "pc_write_unconfirmed")
        return self._public(record)
    finally:
      with self._lock:
        self._executing.discard(request_id)

  def _get(self, request_id):
    _identifier(request_id)
    if request_id not in self._records:
      raise RequestNotFound("request_not_found")
    return self._records[request_id]

  def _public(self, record):
    body, context, receipt = record["body"], record["context"], record["receipt"]
    result = {key: body[key] for key in ("request_id", "target_id", "session_id", "artifact_sha256", "result_revision", "item_key")}
    if body.get("purpose") == "manual_trial":
      result.update(purpose="manual_trial", trial_id=body["trial_id"])
    result.update(scope=context["scope"], state=record["state"], reason=record["reason"], created_ns=record["created_ns"], expires_at_ns=record["expires_at_ns"],
      current_raw=context["current_raw"], candidate_raw=context["candidate_raw"], settings_revision=context["settings_revision"],
      store_receipt=None if receipt is None else {key: receipt[key] for key in ("request_id", "revision", "value")},
      consumer_observed=record["state"] in {"consumer_observed", "comparison_ready"}, comparison_ready=record["state"] == "comparison_ready",
      comparison=None if record["comparison"] is None else deepcopy(record["comparison"]["comparison"]), request_registration_enabled=self._enabled and not self._closed and not self._poisoned,
      pc_simulation_enabled=self._enabled and not self._closed and not self._poisoned and self.store is not None and self.store.scope == "pc_simulation" and context["scope"] == "pc_simulation", recommendations_enabled=False,
      apply_enabled=self._enabled and not self._closed and not self._poisoned and self.store is not None and self.store.scope == "vehicle" and self.store.write_ready and context["scope"] == "vehicle" and record["state"] == "registered", device_qualified=False)
    return validate_request_status(result)

  def status(self, request_id):
    with self._lock:
      record = self._get(request_id)
      if self._enabled and not self._closed and not self._poisoned and record["state"] == "registered" and self.clock_ns() >= record["expires_at_ns"]:
        self._transition(request_id, "expired", "request_expired")
      return self._public(record)

  def list_requests(self, limit=16):
    _integer(limit, 1, 128)
    with self._lock:
      return [self.status(request_id) for request_id in list(self._records)[-limit:][::-1]]

  def export_snapshot(self):
    """Read on the actor/background thread; never expire, recover or append."""
    with self._lock:
      available = self._enabled and not self._poisoned and self._sequence > 0
      checkpoint = {"schema_version": 1, "kind": "request_ledger", "sequence": self._sequence,
        "chain_sha256": self._chain, "bytes": self._bytes, "cutoff_mono_ns": self.clock_ns(), "durable": True} if available else None
      return {"schema_version": 1, "state": "available" if available else "unavailable",
        "reason": None if available else "request_ledger_uncertain" if self._poisoned else "request_ledger_empty_or_disabled",
        "checkpoint": checkpoint, "ledger_relative_path": "requests.jsonl", "inventory_complete": self._enabled and not self._poisoned,
        "requests": [self._public(record) for record in self._records.values()],
        "evidence_references": [{"request_id": record["body"]["request_id"], "session_id": record["body"]["session_id"],
          "artifact_sha256": record["body"]["artifact_sha256"],
          "reference": deepcopy(record["context"]["provenance"]["evidence_reference"])} for record in self._records.values()],
        "comparison_references": [{"request_id": record["body"]["request_id"], "session_id": record["body"]["session_id"],
          "history_reference": deepcopy(record["comparison"]["receipt"]["history_reference"]),
          "source_hashes": deepcopy(record["comparison"]["receipt"]["source_hashes"])}
          for record in self._records.values() if record["comparison"] is not None and record["comparison"].get("receipt") is not None]}

  def cancel(self, request_id):
    with self._lock:
      self._available()
      record = self._get(request_id)
      if record["state"] == "cancelled":
        return self._public(record)
      if record["state"] != "registered":
        raise RequestConflict("request_not_cancellable")
      self._transition(request_id, "cancelled", "operator_cancelled")
      return self._public(record)

  def comparison_record(self, request_id):
    with self._lock:
      record = self._get(request_id)
      body, context, provenance = record["body"], record["context"], record["context"]["provenance"]
      return {"schema_version": 1, "request_id": request_id, "request_sha256": record["request_sha256"],
        "source_kind": "pc_fake" if context["scope"] == "pc_simulation" else "vehicle_held" if record["receipt"] is None else "vehicle_stored", "execution_state": record["state"],
        "target": {"target_id": body["target_id"], "session_id": body["session_id"], "session_generation": context["session_generation"],
                   "route": provenance["route"], "boot_id": provenance["boot_id"]},
        "key": body["item_key"], "old_raw": context["current_raw"], "candidate_raw": context["candidate_raw"], "settings_revision": context["settings_revision"],
        "source_candidate": {"bundle_sha256": body["artifact_sha256"], **{key: deepcopy(provenance[key]) for key in ("evidence_sha256", "profile_sha256", "code_hashes", "source_epoch_id", "evidence_reference")}},
        "baseline": {"settings_history_reference": deepcopy(provenance["settings_history_reference"]), "dependencies": deepcopy(context["dependencies"]),
                     "dependencies_sha256": _hash(context["dependencies"]), "dependency_digest": context["dependency_digest"],
                     "comparison_profile": deepcopy(provenance["comparison_profile"]), "comparison_profile_sha256": provenance["comparison_profile_sha256"]},
        "stored_mono_ns": record["stored_mono_ns"], "receipt_sha256": _hash(record["receipt"]) if record["receipt"] else None}

  def review(self, request_id, token=None):
    with self._lock:
      self._available()
      record = self._get(request_id)
      if record["state"] not in {"stored", "consumer_observed", "comparison_ready"} or self.comparison_provider is None:
        return self._public(record)
      application = self.comparison_record(request_id)
    report = self.comparison_provider(application, token)
    _fields(report, {"state", "reason_codes", "receipt", "comparison"})
    if report["state"] not in {"held", "consumer_observed", "comparison_ready"} or len(_canonical(report)) > 64 * 1024:
      raise ValueError("invalid_comparison_result")
    from .comparison import validate_public_comparison
    validate_public_comparison(report["comparison"])
    if report["comparison"]["state"] != report["state"]:
      raise ValueError("comparison_state_mismatch")
    receipt = report["receipt"]
    if receipt is not None and (receipt.get("request_id") != request_id or receipt.get("request_sha256") != record["request_sha256"] or receipt.get("application_receipt_sha256") != application["receipt_sha256"]):
      raise ValueError("comparison_request_binding_mismatch")
    if receipt is not None:
      _fields(receipt, {"request_id", "request_sha256", "application_receipt_sha256", "state", "profile_sha256", "cohort_sha256",
                        "application_sha256", "comparison_sha256", "history_reference", "source_hashes", "consumer_observed_ns"})
      for key in ("profile_sha256", "cohort_sha256", "application_sha256", "comparison_sha256"):
        _sha(receipt[key])
      if receipt["state"] != report["state"] or receipt["comparison_sha256"] != _hash(report["comparison"]) or receipt["application_sha256"] != _hash(application):
        raise ValueError("comparison_receipt_digest_mismatch")
      if application["baseline"]["comparison_profile_sha256"] is None or receipt["profile_sha256"] != application["baseline"]["comparison_profile_sha256"]:
        raise ValueError("comparison_profile_not_bound")
    for key in ("consumer_application_ack", "causal_improvement_proven", "apply_enabled", "device_qualified"):
      if report["comparison"].get(key) is not False:
        raise ValueError("comparison_qualification_claim")
    with self._lock:
      self._available()
      if record["comparison"] is not None and _hash(record["comparison"]) == _hash(report):
        return self._public(record)
      if report["state"] != "held" and receipt is None:
        raise ValueError("comparison_receipt_required")
      if report["state"] == "held":
        self._transition(request_id, "stored", "comparison_held", comparison=report)
        return self._public(record)
      if report["state"] == "consumer_observed" and record["state"] == "comparison_ready":
        self._transition(request_id, "consumer_observed", comparison=report)
        return self._public(record)
      if report["state"] in {"consumer_observed", "comparison_ready"} and record["state"] == "stored":
        self._transition(request_id, "consumer_observed", comparison=report)
      if report["state"] == "comparison_ready" and record["state"] == "consumer_observed":
        self._transition(request_id, "comparison_ready", comparison=report)
      elif record["comparison"] is None or _hash(record["comparison"]) != _hash(report):
        self._transition(request_id, record["state"], comparison=report)
      return self._public(record)

  def close(self):
    with self._lock:
      if not self._closed:
        self._closed = True
        if self._lease is not None:
          self._lease.close()

  def capabilities(self):
    return {"request_registration_enabled": self._enabled and not self._closed and not self._poisoned,
            "pc_simulation_enabled": self._enabled and not self._closed and not self._poisoned and self.store is not None and self.store.scope == "pc_simulation",
            "recommendations_enabled": False, "apply_enabled": self._enabled and not self._closed and not self._poisoned and self.store is not None and self.store.scope == "vehicle" and self.store.write_ready, "device_qualified": False}


def verify_request_prefix(path, checkpoint, *, max_bytes=16 * 1024 * 1024, max_events=10000):
  """Verify/replay only a fixed durable prefix, without constructing an actor."""
  from .candidate_report import _json as parse_json
  from .journal_snapshot import _read as read_pinned
  required = {"schema_version", "kind", "sequence", "chain_sha256", "bytes", "cutoff_mono_ns", "durable"}
  _fields(checkpoint, required)
  if (type(checkpoint["schema_version"]) is not int or checkpoint["schema_version"] != 1
      or checkpoint["kind"] != "request_ledger" or checkpoint["durable"] is not True):
    raise ValueError("invalid_request_checkpoint")
  for name in ("sequence", "bytes"):
    _integer(checkpoint[name], 1)
  _integer(checkpoint["cutoff_mono_ns"])
  _sha(checkpoint["chain_sha256"])
  _integer(max_bytes, 1, 16 * 1024 * 1024)
  _integer(max_events, 1, 10000)
  if checkpoint["bytes"] > max_bytes or checkpoint["sequence"] > max_events:
    raise ValueError("request_checkpoint_budget")
  data = read_pinned(Path(path), checkpoint["bytes"], prefix=True)
  if not data.endswith(b"\n"):
    raise ValueError("request_checkpoint_truncated")
  memory, sequence, chain, size = SimpleNamespace(_records={}), 0, _ZERO, 0
  for line in data.splitlines(keepends=True):
    if len(line) > 64 * 1024 or sequence >= max_events:
      raise ValueError("request_checkpoint_budget")
    envelope = parse_json(line)
    _fields(envelope, {"schema_version", "sequence", "previous_sha256", "record", "sha256"})
    digest = envelope.pop("sha256")
    if (type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1
        or type(envelope["sequence"]) is not int or envelope["sequence"] != sequence + 1
        or envelope["previous_sha256"] != chain or digest != _hash(envelope)):
      raise ValueError("request_checkpoint_corrupt")
    # This method only mutates memory._records; recover() additionally writes.
    ApplyRequestController._replay(memory, envelope["record"])
    sequence, chain, size = envelope["sequence"], digest, size + len(line)
  if (sequence, chain, size) != (checkpoint["sequence"], checkpoint["chain_sha256"], checkpoint["bytes"]):
    raise ValueError("request_checkpoint_mismatch")
  return {"state": "verified", "scope": "durable_request_prefix", "sequence": sequence, "chain_sha256": chain,
          "bytes": size, "raw_sha256": hashlib.sha256(data).hexdigest(), "request_count": len(memory._records),
          "source_session_closed": False, "consumer_application_ack": False}
