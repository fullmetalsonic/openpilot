"""Local physical-vehicle groups, independent of mutable CarParams configuration.

Only the authenticated server writes this registry. Recorders read it without
creating groups; unidentified historical observations remain unidentified.
"""
from copy import deepcopy
from pathlib import Path
import re
import threading
import uuid

from .apply_requests import _atomic, _root
from .candidate_report import _read
from .vehicle_identity import validate_identity


def binding_root(request_root):
  path = Path(request_root)
  return path.with_name(path.name + "-native")


def _validate(value):
  if type(value) is not dict or set(value) != {"schema_version", "groups", "confirmation"} or value["schema_version"] != 1:
    raise ValueError("vehicle_bindings_invalid")
  groups = value["groups"]
  if type(groups) is not dict or len(groups) > 128:
    raise ValueError("vehicle_bindings_invalid")
  vins = set()
  for key, group in groups.items():
    if (type(key) is not str or re.fullmatch(r"[a-f0-9]{32}", key) is None or type(group) is not dict
        or set(group) != {"car_fingerprint", "vin_sha256"} or type(group["car_fingerprint"]) is not str
        or not 1 <= len(group["car_fingerprint"]) <= 256):
      raise ValueError("vehicle_group_invalid")
    vin = group["vin_sha256"]
    if vin is not None:
      if type(vin) is not str or re.fullmatch(r"[a-f0-9]{64}", vin) is None or vin in vins:
        raise ValueError("vehicle_group_vin_invalid")
      vins.add(vin)
  confirmation = value["confirmation"]
  if confirmation is not None and (type(confirmation) is not dict or set(confirmation) != {"boot_id", "group_id"}
      or confirmation["group_id"] not in groups or type(confirmation["boot_id"]) is not str
      or re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", confirmation["boot_id"]) is None):
    raise ValueError("vehicle_confirmation_invalid")
  return value


def _resolve(descriptor, value, boot_id):
  result = validate_identity(descriptor)
  result.update(physical_group_id=None, physical_group_basis=None)
  if result["state"] != "observed":
    return result
  vin = result["vin_sha256"]
  matches = [key for key, group in value["groups"].items() if vin is not None and group["vin_sha256"] == vin]
  basis = "vin"
  if not matches:
    confirmation = value["confirmation"]
    if confirmation is None or confirmation["boot_id"] != boot_id:
      return result
    key = confirmation["group_id"]
    group = value["groups"][key]
    # A newly observed VIN must be explicitly attached, never silently adopted.
    if group["car_fingerprint"] != result["car_fingerprint"] or vin != group["vin_sha256"]:
      return result
    matches, basis = [key], "local_confirmation"
  group = value["groups"][matches[0]]
  if group["car_fingerprint"] == result["car_fingerprint"]:
    result.update(physical_group_id=matches[0], physical_group_basis=basis)
  return validate_identity(result)


def load_bound_identity(descriptor, root, boot_id):
  """Read-only recorder entry point; absent registry leaves the CP unbound."""
  path = Path(root) / "bindings.json"
  if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (path, *path.parents)):
    raise ValueError("vehicle_bindings_link")
  if not path.exists():
    return _resolve(descriptor, {"groups": {}, "confirmation": None}, boot_id)
  try:
    value = _validate(_read(path, 128 * 1024)[0])
  except FileNotFoundError:
    return _resolve(descriptor, {"groups": {}, "confirmation": None}, boot_id)
  return _resolve(descriptor, value, boot_id)


class VehicleBindings:
  def __init__(self, root, boot_id):
    if type(boot_id) is not str or re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", boot_id) is None:
      raise ValueError("vehicle_boot_identity_required")
    self.root, self._lease = _root(root, {"schema_version": 1, "scope": "native_advisor_vehicles"})
    self.boot_id, self._lock, self._closed = boot_id, threading.RLock(), False
    self.path = self.root / "bindings.json"
    try:
      if self.path.exists():
        self._value = _validate(_read(self.path, 128 * 1024)[0])
      else:
        self._value = {"schema_version": 1, "groups": {}, "confirmation": None}
        _atomic(self.path, self._value)
    except BaseException:
      self._lease.close()
      raise

  def _check(self):
    if self._closed or _validate(_read(self.path, 128 * 1024)[0]) != self._value:
      raise ValueError("vehicle_bindings_changed_or_closed")

  def resolve(self, descriptor):
    with self._lock:
      self._check()
      return _resolve(descriptor, self._value, self.boot_id)

  def groups(self):
    with self._lock:
      self._check()
      return [{"group_id": key, "car_fingerprint": group["car_fingerprint"], "vin_known": group["vin_sha256"] is not None}
              for key, group in self._value["groups"].items()]

  def confirm(self, descriptor, group_id=None):
    descriptor = validate_identity(descriptor)
    if descriptor["state"] != "observed":
      raise ValueError("vehicle_identity_unavailable")
    with self._lock:
      self._check()
      current = _resolve(descriptor, self._value, self.boot_id)
      known_vin = next((key for key, group in self._value["groups"].items()
                        if descriptor["vin_sha256"] is not None and group["vin_sha256"] == descriptor["vin_sha256"]), None)
      if group_id == "new":
        if known_vin or current["physical_group_id"]:
          raise ValueError("vehicle_already_bound")
        group_id = uuid.uuid4().hex
      elif group_id is None:
        group_id = known_vin or current["physical_group_id"]
        if group_id is None:
          if self._value["groups"]:
            raise ValueError("vehicle_group_selection_required")
          group_id = uuid.uuid4().hex
      elif group_id not in self._value["groups"]:
        raise ValueError("vehicle_group_not_found")
      if known_vin is not None and known_vin != group_id:
        raise ValueError("vehicle_vin_already_bound")
      value = deepcopy(self._value)
      group = value["groups"].get(group_id)
      if group is not None and (group["car_fingerprint"] != descriptor["car_fingerprint"]
          or group["vin_sha256"] is not None and group["vin_sha256"] != descriptor["vin_sha256"]):
        raise ValueError("vehicle_group_mismatch")
      value["groups"][group_id] = {"car_fingerprint": descriptor["car_fingerprint"], "vin_sha256": descriptor["vin_sha256"]}
      value["confirmation"] = {"boot_id": self.boot_id, "group_id": group_id}
      _validate(value)
      try:
        _atomic(self.path, value)
      except BaseException:
        # Do not use in-memory authorization after an uncertain registry write.
        self._closed = True
        raise
      self._value = value
      return _resolve(descriptor, value, self.boot_id)

  def close(self):
    self._closed = True
    self._lease.close()
