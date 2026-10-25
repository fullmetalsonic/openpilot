"""Native composition for the optional advisor, called on server worker threads.

The existing controllers remain the only source of effective-use observations.
This object routes explicit requests to the physical vehicle's durable store.
"""
from copy import deepcopy
from pathlib import Path
import threading
import time

from .apply_requests import RequestDisabled
from .consumer_observer import _read_boot_id, consumer_ids_for_key
from .registry import load_registry
from .vehicle_binding import VehicleBindings, binding_root
from .vehicle_identity import describe_car_params, same_vehicle_identity, validate_identity
from .vehicle_store import NativeParamsStore


_ACTIVE = {"running", "waiting", "waiting_for_data", "collecting", "evaluating", "degraded"}
_PATH_ENV = {"settings": "SETTINGS_DIR", "logs": "LOG_ROOT", "evidence": "EVIDENCE_ROOT",
             "candidates": "AUTO_OUTPUT_ROOT", "requests": "REQUEST_ROOT", "exports": "EXPORT_ROOT",
             "consumer_socket": "CONSUMER_SOCKET", "profile": "AUTO_PROFILE", "objectives": "AUTO_OBJECTIVES"}


def live_environment(config, environment):
  """Explicit child configuration; no token and no modification of os.environ."""
  if config.get("runtime_kind") != "native_comma":
    return {}
  result = {"CARROT_CONTROL_ADVISOR_" + key: str(config["paths"][name])
            for name, key in _PATH_ENV.items() if config["paths"].get(name)}
  if config["paths"].get("consumer_socket"):
    result["CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS"] = environment.get("CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS", "1")
  if config.get("capabilities", {}).get("analysis_configured"):
    result["CARROT_CONTROL_ADVISOR_AUTO_ENABLED"] = environment.get("CARROT_CONTROL_ADVISOR_AUTO_ENABLED", "1")
  return result


def comparison_profile():
  """Versioned comparison windows, not vehicle-qualified tuning limits.

Family-specific dependency requirements are always supplied by comparison.py;
an empty additional-context list does not remove those requirements.
"""
  return {"schema_version": 1, "profile_id": "native-observational-comparison-v1", "version": 1,
    "analysis_profile": {"profile_id": "native-comparison-mask-v1", "pre_margin_ns": 100_000_000,
      "recovery_ns": 2_000_000_000, "max_gap_ns": 250_000_000, "dependency_ns": 100_000_000, "max_samples": 100_000},
    "settling_ns": 2_000_000_000, "min_samples": 30, "min_duration_ns": 5_000_000_000,
    "max_consumer_age_ns": 2_000_000_000, "speed_bins_mps": [0., 5., 10., 15., 20., 25., 35., 50., 80.],
    "target_bins": [-10., -3., -1., -.2, .2, 1., 3., 10.], "required_context_keys": [], "model_identity_key": None}


class NativeRuntime:
  # ApplyRequestController uses this object as a store; selection is rechecked
  # against current CP at every operation, never against a browser target path.
  scope = "vehicle"
  target_id = "current_vehicle"

  def __init__(self, config, params, *, session_status, session_binding, clock_ns=time.monotonic_ns, boot_id=None):
    if config.get("runtime_kind") != "native_comma" or not config["paths"].get("requests"):
      raise ValueError("native_runtime_configuration_required")
    if not all(callable(getattr(params, name, None)) for name in ("get_many_raw", "compare_and_set_raw")):
      raise ValueError("native_params_raw_api_required")
    self.config, self.params = deepcopy(config), params
    self.session_status, self.session_binding, self.clock_ns = session_status, session_binding, clock_ns
    self.boot_id = boot_id or _read_boot_id()
    self.root = binding_root(config["paths"]["requests"])
    if not self.root.is_absolute() or any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) for path in (self.root, *self.root.parents)):
      raise ValueError("native_runtime_root_invalid")
    self.root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    self.bindings = VehicleBindings(self.root, self.boot_id)
    self.store = self
    self._lock, self._stores, self._current, self._closed = threading.RLock(), {}, None, False
    self._items = {item["key"]: item for item in load_registry()["items"]}
    self._last_reason = "vehicle_confirmation_required"

  @property
  def write_ready(self):
    return not self._closed and self._current is not None and self._current.write_ready

  def _descriptor(self):
    if self._closed:
      raise RequestDisabled("native_runtime_closed")
    raw = self.params.get_many_raw(["CarParams"])["CarParams"]
    return self.bindings.resolve(describe_car_params(raw))

  def _identity_provider(self, raw):
    return self.bindings.resolve(describe_car_params(raw))

  def _session(self):
    value = self.session_binding()
    if (type(value) is not dict or value.get("source_mode") != "live" or value.get("state") not in _ACTIVE
        or value.get("settings_observation_fresh") is not True):
      raise RequestDisabled("native_live_observation_required")
    return {"session_id": value["session_id"], "generation": value["generation"], "source_mode": "live",
            "state": "running", "target_id": self.target_id}

  def _select(self):
    descriptor = self._descriptor()
    group = descriptor["physical_group_id"]
    if descriptor["state"] != "observed" or group is None:
      self._current = None
      self._last_reason = "vehicle_identity_unavailable" if descriptor["state"] != "observed" else "vehicle_confirmation_required"
      raise RequestDisabled(self._last_reason)
    if group not in self._stores:
      self._stores[group] = NativeParamsStore(self.root / group, self.target_id, self.params,
        session_context=self._session, write_guard=self._guard, identity_provider=self._identity_provider,
        enabled=True, clock_ns=self.clock_ns)
    self._current = self._stores[group]
    self._last_reason = None
    return self._current

  def confirm_vehicle(self, group_id=None):
    with self._lock:
      self.bindings.confirm(self._descriptor(), group_id)
      self._select()
      return self.public_status()

  def current_context(self, target_id):
    if target_id != self.target_id:
      raise ValueError("request_target_mismatch")
    with self._lock:
      self._session()
      return self._select().request_binding()

  def _consumer_reason(self, key, raw, status, descriptor):
    from .comparison import _field_matches
    snapshot = status.get("consumer_observations", {})
    if snapshot.get("source_mode") != "live":
      return "native_consumer_source_unavailable"
    entries = {item["consumer_id"]: item for item in snapshot.get("consumers", [])}
    required = consumer_ids_for_key(key)
    if not required or type(raw) is not int:
      return "native_parameter_path_unknown"
    scale = 1 if key == "SpeedTFFactor" else self._items[key].get("raw_scale")
    if scale is None:
      return "native_parameter_units_unknown"
    for consumer in required:
      entry = entries.get(consumer)
      if not entry or entry.get("fresh") is not True or entry.get("state") != "observed":
        return "native_consumer_not_fresh"
      event = entry.get("latest_event") or {}
      stamp = event.get("produced_mono_ns")
      if event.get("boot_id") != self.boot_id or type(stamp) is not int or not 0 <= self.clock_ns() - stamp <= 2_000_000_000:
        return "native_consumer_boot_or_age_changed"
      identity = event.get("vehicle_identity")
      if identity is None or not same_vehicle_identity(descriptor, identity, require_group=False):
        return "native_consumer_configuration_changed"
      if not _field_matches(event, key, raw * scale):
        return "native_consumer_value_not_effective"
    return None

  def _guard(self, context):
    current = self._descriptor()
    original = context.get("provenance", {}).get("vehicle_identity")
    if original is None or not same_vehicle_identity(current, original):
      raise RequestDisabled("native_vehicle_identity_changed")
    if context.get("operation") == "offroad_restore":
      if context.get("offroad_boot_id") != self.boot_id or not self._offroad():
        raise RequestDisabled("native_offroad_restore_state_changed")
      return None
    self._session()
    status = self.session_status()
    if status.get("source_mode") != "live" or status.get("state") not in _ACTIVE:
      raise RequestDisabled("native_session_inactive")
    if context.get("operation") != "restore" and context.get("provenance", {}).get("boot_id") != self.boot_id:
      raise RequestDisabled("native_candidate_boot_changed")
    # Parked values may be stored for the next actual consumer read. Neither the
    # store receipt nor this branch is an assertion that a controller used them.
    parking = status.get("parking", {})
    stamp = parking.get("observed_mono_ns")
    parked = (parking.get("source_kind") == "native_event" and parking.get("state") == "park_observed"
              and type(stamp) is int and 0 <= self.clock_ns() - stamp <= 2_000_000_000)
    if not parked:
      transition = status.get("transition", {}).get(self._items[context["item_key"]]["axis"], {})
      transition_stamp = transition.get("observed_mono_ns")
      if (transition.get("ready") is not True or type(transition_stamp) is not int
          or not 0 <= self.clock_ns() - transition_stamp <= 2_000_000_000):
        raise RequestDisabled("native_driver_intervention_recovery_or_telemetry_unready")
      reason = self._consumer_reason(context["item_key"], context["current_raw"], status, current)
      if reason:
        raise RequestDisabled(reason)
      code = context.get("provenance", {}).get("code_hashes", {})
      required = set(consumer_ids_for_key(context["item_key"]))
      for entry in status.get("consumer_observations", {}).get("consumers", []):
        if entry["consumer_id"] in required:
          hashes = entry["latest_event"].get("source_hashes", {})
          if not hashes or any(value is None or code.get(path) != value for path, value in hashes.items()):
            raise RequestDisabled("native_consumer_code_changed")
    return None

  def _offroad(self):
    return self.params.get_many_raw(["IsOffroad", "IsOnroad"]) == {"IsOffroad": b"1", "IsOnroad": b"0"}

  def public_status(self):
    with self._lock:
      descriptor = self._descriptor()
      try:
        self._select()
      except RequestDisabled:
        pass
      return {"runtime_kind": "native_comma", "vehicle": descriptor,
              "groups": self.bindings.groups(), "vehicle_confirmed": descriptor["physical_group_id"] is not None,
              "offroad_restore_available": self._offroad() and descriptor["physical_group_id"] is not None,
              "writer_available": self.write_ready, "reason": self._last_reason,
              "analysis_blockers": deepcopy(self.config["blockers"]["analysis"]), "device_qualified": False}

  def prepare_request(self, context):
    with self._lock:
      return self._select().prepare_request(context)

  def snapshot(self):
    with self._lock:
      return self._select().snapshot()

  def request_receipt(self, request_id):
    with self._lock:
      return self._select().request_receipt(request_id)

  def compare_and_swap(self, **kwargs):
    with self._lock:
      return self._select().compare_and_swap(**kwargs)

  def backup(self):
    with self._lock:
      value = self._select().export_backup()
      # The baseline already contains exact base64 bytes and absent markers.
      value.pop("raw_bytes", None)
      value["physical_group_id"] = self._descriptor()["physical_group_id"]
      return value

  def restore_request(self, request_id, restore_id, mode="live"):
    if mode not in {"live", "offroad"}:
      raise ValueError("native_restore_mode_invalid")
    with self._lock:
      receipt = self._select().restore(request_id, restore_id, offroad=mode == "offroad", boot_id=self.boot_id)
      return {"state": "stored", "request_id": request_id, "restore_id": restore_id,
              "item_key": receipt["item_key"], "value": receipt["value"], "consumer_observed": False,
              "reason": "restore_stored_awaiting_restart" if mode == "offroad" else "restore_stored_awaiting_consumers"}

  def close(self):
    with self._lock:
      self._closed = True
      for store in self._stores.values():
        store.close()
      self.bindings.close()
