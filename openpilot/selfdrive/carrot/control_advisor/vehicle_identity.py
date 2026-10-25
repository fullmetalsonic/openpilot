"""Bounded CarParams descriptors. Fingerprints name configurations, not vehicles."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re


MAX_CP_BYTES = 1024 * 1024
FIELDS = {"identity_version", "state", "reason", "config_sha256", "schema_sha256", "raw_sha256", "vin_sha256",
          "car_fingerprint", "physical_group_id", "physical_group_basis"}
_SHA = re.compile(r"[a-f0-9]{64}")


def unavailable_identity(reason="car_params_unavailable"):
  if type(reason) is not str or re.fullmatch(r"[a-z0-9_]{1,128}", reason) is None:
    raise ValueError("vehicle_identity_reason_invalid")
  return {"identity_version": 1, "state": "unavailable", "reason": reason,
          **dict.fromkeys(FIELDS - {"identity_version", "state", "reason"})}


def validate_identity(value, *, require_bound=False):
  if type(value) is not dict or set(value) != FIELDS or type(value["identity_version"]) is not int or value["identity_version"] != 1:
    raise ValueError("vehicle_identity_invalid")
  if value["state"] == "unavailable":
    if value != unavailable_identity(value["reason"]) or require_bound:
      raise ValueError("vehicle_identity_unavailable")
    return deepcopy(value)
  if value["state"] != "observed" or value["reason"] is not None:
    raise ValueError("vehicle_identity_invalid")
  for name in ("config_sha256", "schema_sha256", "raw_sha256", "vin_sha256"):
    if name == "vin_sha256" and value[name] is None:
      continue
    if type(value[name]) is not str or _SHA.fullmatch(value[name]) is None:
      raise ValueError("vehicle_identity_hash_invalid")
  label = value["car_fingerprint"]
  if type(label) is not str or not 1 <= len(label) <= 256 or any(ord(char) < 32 for char in label):
    raise ValueError("vehicle_fingerprint_invalid")
  group, basis = value["physical_group_id"], value["physical_group_basis"]
  if group is None and basis is None:
    if require_bound:
      raise ValueError("vehicle_physical_group_unconfirmed")
  elif (type(group) is not str or re.fullmatch(r"[a-f0-9]{32}", group) is None or type(basis) is not str
        or basis not in {"vin", "local_confirmation"} or basis == "vin" and value["vin_sha256"] is None):
    raise ValueError("vehicle_physical_group_invalid")
  return deepcopy(value)


def same_vehicle_identity(first, second, *, require_group=True):
  try:
    first, second = validate_identity(first, require_bound=require_group), validate_identity(second, require_bound=require_group)
  except ValueError:
    return False
  if first["state"] != "observed" or second["state"] != "observed":
    return False
  names = ["config_sha256", "schema_sha256", "vin_sha256", "car_fingerprint"]
  if require_group:
    names.extend(("physical_group_id", "physical_group_basis"))
  return all(first[name] == second[name] for name in names)


@lru_cache(maxsize=1)
def _schema():
  import capnp
  path = Path(__file__).resolve().parents[4] / "opendbc_repo/opendbc/car/car.capnp"
  raw = path.read_bytes()
  parser = capnp.SchemaParser()
  schema = parser.load(str(path), "/car.capnp", imports=[str(path.parent), str(path.parent / "include")])
  return parser, schema, hashlib.sha256(raw).hexdigest()


def _plain(value):
  if isinstance(value, bytes):
    return {"capnp_data_hex": value.hex()}
  if isinstance(value, dict):
    return {key: _plain(item) for key, item in value.items()}
  if isinstance(value, list):
    return [_plain(item) for item in value]
  return value


def describe_car_params(raw):
  """Decode this exact captured message; never reread Params or retain a VIN."""
  if raw is None:
    return unavailable_identity()
  if type(raw) is not bytes or not 0 < len(raw) <= MAX_CP_BYTES:
    return unavailable_identity("car_params_size_or_type_invalid")
  try:
    _, schema, schema_hash = _schema()
    with schema.CarParams.from_bytes(raw, traversal_limit_in_words=MAX_CP_BYTES // 8) as message:
      data = _plain(message.to_dict(verbose=True))
    vin = data.pop("carVin", "")
    fingerprint = data.get("carFingerprint")
    if type(fingerprint) is not str or not fingerprint or len(fingerprint) > 256:
      return unavailable_identity("car_fingerprint_unavailable")
    if str(data.get("brand", "")).strip().lower() == "mock" or fingerprint.strip().lower() == "mock":
      return unavailable_identity("mock_car_params")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(canonical) > 4 * MAX_CP_BYTES:
      return unavailable_identity("car_params_decoded_limit")
    vin = vin.strip().upper() if isinstance(vin, str) else ""
    valid_vin = re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin) is not None and vin != "0" * 17
    result = {"identity_version": 1, "state": "observed", "reason": None,
              "config_sha256": hashlib.sha256(canonical).hexdigest(), "schema_sha256": schema_hash,
              "raw_sha256": hashlib.sha256(raw).hexdigest(),
              "vin_sha256": hashlib.sha256(b"control_advisor.vehicle_vin.v1\0" + vin.encode("ascii")).hexdigest() if valid_vin else None,
              "car_fingerprint": fingerprint, "physical_group_id": None, "physical_group_basis": None}
    return validate_identity(result)
  except Exception:
    return unavailable_identity("car_params_decode_unavailable")
