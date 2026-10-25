"""Explicit native storage only: raw CAS receipts are never consumer acknowledgements.

Local observation revisions do not imply native transactions or ABA protection.
An intent without a durable successful receipt is never recovered from equal values.
"""
import base64
from copy import deepcopy
from pathlib import Path
import re
import threading
import time
import uuid

from .apply_requests import KEYS, RequestConflict, RequestDisabled, _atomic, _canonical, _context, _hash, _identifier, _integer, _root, _sha
from .candidate_report import _read
from .vehicle_identity import describe_car_params, validate_identity, same_vehicle_identity


class NativeStoreFailed(ValueError):
  """Native API explicitly reported no mutation."""


class NativeStoreUncertain(ValueError):
  """A mutation might have happened; never retry automatically."""


def _encode(raw):
  if type(raw) is not dict or set(raw) != KEYS:
    raise ValueError("native_raw_inventory_invalid")
  result = {}
  for key, value in raw.items():
    if value is not None and (type(value) is not bytes or len(value) > 256):
      raise ValueError("native_raw_value_invalid")
    result[key] = None if value is None else base64.b64encode(value).decode("ascii")
  return result


def _decode(encoded):
  if type(encoded) is not dict or set(encoded) != KEYS:
    raise ValueError("native_raw_inventory_invalid")
  try:
    raw = {key: None if value is None else base64.b64decode(value, validate=True) for key, value in encoded.items()}
  except (ValueError, TypeError) as error:
    raise ValueError("native_raw_encoding_invalid") from error
  if _encode(raw) != encoded:
    raise ValueError("native_raw_encoding_invalid")
  return raw


def _parsed(raw):
  result = {}
  for key, value in raw.items():
    if value is None:
      result[key] = None
      continue
    if re.fullmatch(rb"[ \t\r\n]*[+-]?[0-9]+[ \t\r\n]*", value) is None:
      raise ValueError("native_parameter_not_integer")
    parsed = int(value)
    if not -1000000 <= parsed <= 1000000:
      raise ValueError("native_parameter_integer_range")
    result[key] = parsed
  return result


class NativeParamsStore:
  scope = "vehicle"

  @property
  def write_ready(self):
    return self.enabled and not self._closed and not self._poisoned

  def __init__(self, root, target_id, params, *, session_context, write_guard, enabled=False, clock_ns=time.monotonic_ns, identity_provider=None):
    _identifier(target_id)
    if not all(callable(getattr(params, name, None)) for name in ("get_many_raw", "compare_and_set_raw")):
      raise ValueError("native_params_raw_api_required")
    if not callable(session_context) or not callable(write_guard):
      raise ValueError("native_store_context_and_guard_required")
    self.params, self.target_id, self.session_context, self.write_guard = params, target_id, session_context, write_guard
    self.clock_ns, self.enabled = clock_ns, bool(enabled)
    if identity_provider is not None and not callable(identity_provider):
      raise ValueError("native_identity_provider_invalid")
    self.identity_provider, self._current_cp = identity_provider, None
    self._lock, self._lease, self._closed, self._poisoned, self._prepared = threading.RLock(), None, False, False, None
    self.root = self.path = None
    self._state = None
    if not self.enabled:
      return
    # Never place the advisor journal inside Params, even with a differently spelled root.
    getter = getattr(params, "get_param_path", None)
    if not callable(getter):
      raise ValueError("native_params_path_required")
    native_root = Path(getter()).resolve()
    journal_root = Path(root).resolve()
    if native_root == journal_root or native_root in journal_root.parents or journal_root in native_root.parents:
      raise ValueError("native_store_root_overlap")
    self.root, self._lease = _root(root, {"schema_version": 1, "scope": "native_advisor_params_store"})
    self.path = self.root / "vehicle-store.json"
    try:
      if self.path.exists():
        self._state = _read(self.path, 4 * 1024 * 1024)[0]
        self._validate_state()
        self._baseline()
      else:
        if (self.root / "baseline.json").exists():
          raise ValueError("native_store_orphan_baseline")
        self._state = {"schema_version": 1, "target_id": target_id, "store_id": uuid.uuid4().hex,
                       "revision": 0, "raw": None, "baseline_sha256": None, "operations": {}, "restores": {}}
        self._save(self._state)
    except BaseException:
      self._lease.close()
      raise

  def _available(self):
    if not self.enabled or self._closed or self._poisoned:
      raise RequestDisabled("native_store_uncertain" if self._poisoned else "native_store_disabled")

  def _validate_state(self):
    state = self._state
    if (type(state) is not dict or set(state) - {"identity_version", "vehicle_identity"} != {"schema_version", "target_id", "store_id", "revision", "raw", "baseline_sha256", "operations", "restores"}
        or state["schema_version"] != 1 or state["target_id"] != self.target_id):
      raise ValueError("native_store_identity_invalid")
    _identifier(state["store_id"])
    _integer(state["revision"])
    if "vehicle_identity" in state or "identity_version" in state:
      if type(state.get("identity_version")) is not int or state["identity_version"] != 1:
        raise ValueError("native_vehicle_identity_invalid")
      validate_identity(state.get("vehicle_identity"))
    if state["raw"] is not None:
      _parsed(_decode(state["raw"]))
    for name in ("operations", "restores"):
      if type(state[name]) is not dict or len(state[name]) > 128:
        raise ValueError("native_store_operation_budget")
      for request_id, operation in state[name].items():
        _identifier(request_id)
        if type(operation) is not dict or operation.get("state") not in {"intent", "stored", "failed", "conflict", "uncertain"} or operation.get("sha256") != _hash(operation.get("intent")):
          raise ValueError("native_store_operation_corrupt")
        if operation["state"] == "stored" and (type(operation.get("receipt")) is not dict or operation.get("receipt_sha256") != _hash(operation["receipt"])):
          raise ValueError("native_store_receipt_corrupt")

  def _save(self, state):
    if len(_canonical(state)) > 4 * 1024 * 1024:
      raise RequestConflict("native_store_byte_budget")
    try:
      _atomic(self.path, state)
    except BaseException:
      self._poisoned = True
      raise
    self._state = deepcopy(state)

  def _assert_disk(self):
    if _read(self.path, 4 * 1024 * 1024)[0] != self._state:
      raise RequestConflict("native_store_journal_changed")
    self._baseline()

  def _session(self):
    value = self.session_context()
    if type(value) is not dict or not {"session_id", "generation", "source_mode", "state"} <= set(value):
      raise RequestDisabled("native_session_unavailable")
    _identifier(value["session_id"])
    _integer(value["generation"])
    if value["source_mode"] != "live" or value["state"] != "running" or value.get("target_id", self.target_id) != self.target_id:
      raise RequestDisabled("native_session_not_current_live")
    return value

  def _observe(self):
    self._available()
    self._assert_disk()
    observed = self.params.get_many_raw(sorted((*KEYS, "CarParams")))
    if type(observed) is not dict or set(observed) != KEYS | {"CarParams"}:
      raise ValueError("native_raw_inventory_invalid")
    raw, cp = {key: observed[key] for key in KEYS}, observed["CarParams"]
    identity = describe_car_params(cp)
    if self.identity_provider is not None and identity["state"] == "observed":
      bound = validate_identity(self.identity_provider(cp))
      if any(bound[field] != identity[field] for field in identity if field not in {"physical_group_id", "physical_group_basis"}):
        raise RequestConflict("native_identity_provider_mismatch")
      identity = bound
    encoded, values = _encode(raw), _parsed(raw)
    if encoded != self._state["raw"] or identity != self._state.get("vehicle_identity"):
      state = deepcopy(self._state)
      state.update(raw=encoded, revision=state["revision"] + 1, identity_version=1, vehicle_identity=identity)
      self._save(state)
    self._current_cp = cp
    return raw, values

  def snapshot(self):
    with self._lock:
      raw, values = self._observe()
      session = self._session()
      return {"target_id": self.target_id, "store_id": self._state["store_id"], "session_id": session["session_id"],
              "generation": session["generation"], "revision": self._state["revision"], "values": values,
              "raw_bytes": deepcopy(raw), "revision_scope": "local_raw_observation", "native_revision": None,
              "identity_version": 1, "vehicle_identity": deepcopy(self._state["vehicle_identity"])}

  def request_binding(self):
    value = self.snapshot()
    return {"session_id": value["session_id"], "generation": value["generation"], "source_mode": "live", "state": "running",
            "settings_revision": value["revision"], "settings_raw": value["values"], "observed_mono_ns": self.clock_ns(), "settings_observation_fresh": True,
            "identity_version": 1, "vehicle_identity": deepcopy(self._state["vehicle_identity"])}

  def _authorize(self, context):
    self._available()
    if context["scope"] != "vehicle" or context["target_id"] != self.target_id:
      raise RequestConflict("native_request_target_or_session_changed")
    if context.get("operation") == "offroad_restore":
      state = self.params.get_many_raw(["CarParams", "IsOffroad", "IsOnroad"])
      if state != {"CarParams": self._current_cp, "IsOffroad": b"1", "IsOnroad": b"0"}:
        raise RequestDisabled("native_offroad_restore_state_changed")
    else:
      session = self._session()
      if context["session_id"] != session["session_id"] or context["session_generation"] != session["generation"]:
        raise RequestConflict("native_request_target_or_session_changed")
    provenance = context.get("provenance", {})
    if provenance.get("identity_version") != 1 or "vehicle_identity" not in provenance:
      raise RequestDisabled("native_source_vehicle_identity_missing")
    try:
      validate_identity(provenance["vehicle_identity"], require_bound=True)
      validate_identity(self._state.get("vehicle_identity"), require_bound=True)
    except ValueError as error:
      raise RequestDisabled("native_vehicle_identity_unconfirmed") from error
    if not same_vehicle_identity(provenance["vehicle_identity"], self._state["vehicle_identity"]):
      raise RequestConflict("native_vehicle_identity_changed")
    if self.write_guard(deepcopy(context)) is not None:
      raise RequestDisabled("native_write_guard_not_authorized")

  def prepare_request(self, context):
    with self._lock:
      context = _context(context)
      snapshot = self.snapshot()
      self._authorize(context)
      if (snapshot["revision"] != context["settings_revision"] or snapshot["values"][context["item_key"]] != context["current_raw"]
          or any(snapshot["values"][key] != value for key, value in context["dependencies"].items())):
        raise RequestConflict("native_observation_changed")
      self._prepared = {"context": context, "raw": _encode(snapshot["raw_bytes"]), "car_params": self._current_cp}

  def _baseline(self):
    digest = self._state["baseline_sha256"]
    path = self.root / "baseline.json"
    if digest is None:
      if path.exists():
        raise ValueError("native_store_orphan_baseline")
      return None
    value = _read(path, 64 * 1024)[0]
    if (_hash(value) != digest or value.get("store_id") != self._state["store_id"] or value.get("target_id") != self.target_id):
      raise ValueError("native_baseline_changed")
    _decode(value["raw"])
    return value

  def _ensure_baseline(self):
    if self._baseline() is not None:
      return
    baseline = {"schema_version": 1, "scope": "native_raw_baseline", "target_id": self.target_id,
                "store_id": self._state["store_id"], "raw": deepcopy(self._state["raw"]), "observed_mono_ns": self.clock_ns()}
    try:
      _atomic(self.root / "baseline.json", baseline)
      state = deepcopy(self._state)
      state["baseline_sha256"] = _hash(baseline)
      self._save(state)
    except BaseException:
      self._poisoned = True
      raise

  def request_receipt(self, request_id):
    with self._lock:
      self._available()
      self._assert_disk()
      operation = self._state["operations"].get(request_id)
      return deepcopy(operation["receipt"]) if operation and operation["state"] == "stored" else None

  def _existing(self, group, operation_id, intent):
    operation = self._state[group].get(operation_id)
    if operation is None:
      return None
    if operation["sha256"] != _hash(intent):
      raise RequestConflict("native_operation_id_conflict")
    if operation["state"] == "stored":
      return deepcopy(operation["receipt"])
    if operation["state"] == "failed":
      raise NativeStoreFailed("native_write_failed_known_nochange")
    if operation["state"] == "conflict":
      raise RequestConflict("native_compare_and_swap_conflict")
    raise NativeStoreUncertain("native_intent_without_successful_receipt")

  def _persist_operation(self, group, operation_id, operation):
    state = deepcopy(self._state)
    state[group][operation_id] = deepcopy(operation)
    self._save(state)

  def _finish_native(self, state):
    try:
      self._save(state)
    except Exception as error:
      # Even a known journal/quota failure is uncertain after native mutation.
      self._poisoned = True
      raise NativeStoreUncertain("native_result_receipt_not_durable") from error

  def _finish_operation(self, group, operation_id, operation):
    state = deepcopy(self._state)
    state[group][operation_id] = deepcopy(operation)
    self._finish_native(state)

  def _cas(self, group, operation_id, intent, context, key, new, expected):
    if len(self._state[group]) >= 128:
      raise RequestConflict("native_store_operation_budget")
    operation = {"state": "intent", "intent": intent, "sha256": _hash(intent), "receipt": None, "receipt_sha256": None}
    if len(_canonical(self._state)) + len(_canonical(operation)) + 8192 > 4 * 1024 * 1024:
      raise RequestConflict("native_store_receipt_capacity")
    self._ensure_baseline()
    self._persist_operation(group, operation_id, operation)
    try:
      self._authorize(context)
    except (RequestConflict, RequestDisabled):
      operation["state"] = "failed"
      self._persist_operation(group, operation_id, operation)
      raise
    try:
      result = self.params.compare_and_set_raw(key, new, expected)
    except Exception as error:
      operation["state"] = "uncertain"
      self._finish_operation(group, operation_id, operation)
      raise NativeStoreUncertain("native_call_outcome_unknown") from error
    if (type(result) is not dict or set(result) != {"state", "errno", "changed"} or type(result["changed"]) is not bool
        or type(result["errno"]) is not int or result["state"] not in {"stored", "failed", "conflict", "uncertain"}):
      result = {"state": "uncertain", "errno": 0, "changed": False}
    if result["state"] == "stored" and (result["errno"] != 0 or not result["changed"]):
      result = {**result, "state": "uncertain"}
    if result["state"] != "stored":
      outcome = result["state"] if result["state"] in {"failed", "conflict"} and not result["changed"] else "uncertain"
      operation.update(state=outcome, native_result=result)
      self._finish_operation(group, operation_id, operation)
      if outcome == "failed":
        raise NativeStoreFailed("native_write_failed_known_nochange")
      if outcome == "conflict":
        raise RequestConflict("native_compare_and_swap_conflict")
      raise NativeStoreUncertain("native_write_outcome_uncertain")
    # Native successful CAS is the storage receipt, not an inferred value equality.
    before = self._state["revision"]
    receipt = {"schema_version": 1, "request_id": operation_id, "request_sha256": intent["request_sha256"],
               "target_id": self.target_id, "store_id": self._state["store_id"], "item_key": key,
               "previous_raw": intent["previous_raw"], "value": intent["value"], "previous_revision": before, "revision": before + 1,
               "dependency_digest": intent["dependency_digest"], "stored_mono_ns": self.clock_ns(),
               "revision_scope": "local_raw_observation", "native_revision": None, "native_result": result,
               "previous_bytes": intent["previous_bytes"], "new_bytes": intent["new_bytes"], "consumer_application_ack": False}
    receipt.update(identity_version=1, vehicle_identity=deepcopy(self._state["vehicle_identity"]))
    state = deepcopy(self._state)
    state["raw"][key], state["revision"] = intent["new_bytes"], before + 1
    operation.update(state="stored", receipt=receipt, receipt_sha256=_hash(receipt))
    state[group][operation_id] = operation
    self._finish_native(state)
    return deepcopy(receipt)

  def compare_and_swap(self, *, request_id, request_sha256, item_key, expected_current_raw, expected_settings_revision,
                       expected_dependencies, dependency_digest, candidate_raw):
    _identifier(request_id)
    _sha(request_sha256)
    _sha(dependency_digest)
    if item_key not in KEYS or type(expected_dependencies) is not dict or set(expected_dependencies) - KEYS:
      raise ValueError("native_request_parameter_invalid")
    _integer(candidate_raw, -1000000, 1000000)
    if expected_current_raw is not None:
      _integer(expected_current_raw, -1000000, 1000000)
    _integer(expected_settings_revision, 1)
    with self._lock:
      self._available()
      self._assert_disk()
      saved = self._state["operations"].get(request_id)
      if saved is not None:
        if saved["intent"]["request_sha256"] != request_sha256:
          raise RequestConflict("native_operation_id_conflict")
        return self._existing("operations", request_id, saved["intent"])
      prepared = self._prepared
      if prepared is None:
        raise RequestDisabled("native_trusted_context_not_prepared")
      context = prepared["context"]
      expected_context = {"item_key": item_key, "current_raw": expected_current_raw, "candidate_raw": candidate_raw,
                          "settings_revision": expected_settings_revision, "dependencies": expected_dependencies, "dependency_digest": dependency_digest}
      if any(context[key] != value for key, value in expected_context.items()):
        raise RequestConflict("native_prepared_context_mismatch")
      raw, values = self._observe()
      if self._state["revision"] != expected_settings_revision or _encode(raw) != prepared["raw"] or self._current_cp != prepared["car_params"]:
        raise RequestConflict("native_observation_changed")
      self._authorize(context)
      encoded_new = base64.b64encode(str(candidate_raw).encode("ascii")).decode("ascii")
      intent = {"request_sha256": request_sha256, "item_key": item_key, "previous_raw": expected_current_raw, "value": candidate_raw,
                "previous_bytes": prepared["raw"][item_key], "new_bytes": encoded_new, "dependency_digest": dependency_digest,
                "guard_context": context, "expected_revision": expected_settings_revision,
                "identity_version": 1, "vehicle_identity": deepcopy(self._state["vehicle_identity"])}
      # Every observed allowlisted dependency is included, even if a resolver passed a subset.
      return self._cas("operations", request_id, intent, context, item_key, str(candidate_raw).encode("ascii"), {**raw, "CarParams": self._current_cp})

  def restore(self, request_id, restore_id, *, offroad=False, boot_id=None):
    _identifier(request_id)
    _identifier(restore_id)
    if type(offroad) is not bool or offroad and (type(boot_id) is not str or not boot_id):
      raise ValueError("native_restore_mode_invalid")
    with self._lock:
      self._available()
      self._assert_disk()
      operation = self._state["operations"].get(request_id)
      if operation is None or operation["state"] != "stored":
        raise RequestConflict("native_restore_successful_operation_required")
      original = operation["intent"]
      intent = {"request_sha256": _hash({"request_id": request_id, "restore_id": restore_id, "operation_sha256": operation["sha256"]}),
                "item_key": original["item_key"], "previous_raw": original["value"], "value": original["previous_raw"],
                "previous_bytes": original["new_bytes"], "new_bytes": original["previous_bytes"],
                "dependency_digest": original["dependency_digest"], "original_request_id": request_id}
      if offroad:
        intent.update(execution_mode="offroad_restore", boot_id=boot_id)
      existing = self._existing("restores", restore_id, intent)
      if existing is not None:
        return existing
      raw, _ = self._observe()
      key = original["item_key"]
      if _encode(raw)[key] != original["new_bytes"]:
        raise RequestConflict("native_restore_current_value_changed")
      context = deepcopy(original["guard_context"])
      context.update(operation="offroad_restore" if offroad else "restore", original_request_id=request_id,
                     restore_id=restore_id, current_raw=int(raw[key]), candidate_raw=original["previous_raw"])
      if offroad:
        context["offroad_boot_id"] = boot_id
      else:
        session = self._session()
        context.update(session_id=session["session_id"], session_generation=session["generation"])
      self._authorize(context)
      previous = None if original["previous_bytes"] is None else base64.b64decode(original["previous_bytes"], validate=True)
      expected = {**raw, "CarParams": self._current_cp}
      if offroad:
        expected.update(IsOffroad=b"1", IsOnroad=b"0")
      return self._cas("restores", restore_id, intent, context, key, previous, expected)

  def export_backup(self):
    with self._lock:
      self._available()
      self._assert_disk()
      baseline = self._baseline()
      if baseline is None:
        raise RequestDisabled("native_baseline_not_created")
      raw = _decode(baseline["raw"])
      return {"schema_version": 1, "baseline_sha256": self._state["baseline_sha256"], "baseline": deepcopy(baseline),
              "raw_bytes": raw, "integer_settings": {key: value for key, value in _parsed(raw).items() if value is not None},
              "absent_keys": sorted(key for key, value in raw.items() if value is None), "legacy_absence_restore_supported": False,
              "consumer_application_ack": False, "device_qualified": False}

  def close(self):
    with self._lock:
      if not self._closed:
        self._closed = True
        if self._lease is not None:
          self._lease.close()
