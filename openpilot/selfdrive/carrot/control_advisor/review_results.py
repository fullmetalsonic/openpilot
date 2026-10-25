"""Local, self-declared second reviews bound to immutable completed exports.

The caller owns the export-job lock. This module never uploads, applies settings,
edits a pack, or authenticates the declared reviewer.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

from .evidence import _sync_directory
from .review_export import _fs_path, _has_link, _relative


MAX_DOCUMENT_BYTES = 128 * 1024
CHECKS = ("integrity", "provenance", "units", "exclusions", "consumer_observation", "settings_binding",
          "comparison", "numerical_method", "coverage", "uncertainty")
SEVERITIES = ("info", "low", "medium", "high", "critical")
VERDICTS = ("retain", "candidate_revision_requested", "reviewed_recommendation", "needs_more_data")
FIELDS = {"schema_version", "document_state", "bundle_manifest_hash", "base_summary_sha256", "summary_id", "summary_revision",
          "review_revision", "previous_review_hash", "scope", "declared_reviewer", "verdict", "base_results", "versions",
          "reviewed_scopes", "unreviewed_scopes", "findings", "uncertainties", "follow_up"}
FLAGS = {"historical_only": True, "current_recommendation": False, "apply_enabled": False, "device_qualified": False,
         "identity_verified": False, "reviewer_identity": "self_declared",
         "power_loss_durability": "file_fsync_only" if os.name == "nt" else "file_and_directory_fsync"}
_SHA = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _canonical(value):
  try:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
  except (TypeError, ValueError, OverflowError, RecursionError) as error:
    raise ValueError("review_json_invalid") from error


def _hash(value):
  return hashlib.sha256(_canonical(value)).hexdigest()


def _object(value, fields):
  if type(value) is not dict or set(value) != fields:
    raise ValueError("review_fields_invalid")


def _text(value, maximum=2048):
  if type(value) is not str or not value.strip() or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
    raise ValueError("review_text_invalid")


def _sha(value):
  if type(value) is not str or _SHA.fullmatch(value) is None:
    raise ValueError("review_hash_invalid")


def _integer(value, minimum=1):
  if type(value) is not int or not minimum <= value <= 2 ** 63 - 1:
    raise ValueError("review_integer_invalid")


def _array(value, maximum=128):
  if type(value) is not list or len(value) > maximum:
    raise ValueError("review_array_invalid")


def _bounded(value, depth=0):
  if depth > 12:
    raise ValueError("review_depth_limit")
  if type(value) is dict:
    if len(value) > 512:
      raise ValueError("review_object_limit")
    for key, item in value.items():
      if type(key) is not str:
        raise ValueError("review_json_invalid")
      _bounded(item, depth + 1)
  elif type(value) is list:
    if len(value) > 512:
      raise ValueError("review_array_invalid")
    for item in value:
      _bounded(item, depth + 1)
  elif type(value) not in (str, int, float, bool, type(None)):
    raise ValueError("review_json_invalid")


def _decode(raw, limit):
  if len(raw) > limit:
    raise ValueError("review_document_limit")
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError("review_duplicate_key")
      result[key] = value
    return result
  try:
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("review_json_invalid")))
  except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
    raise ValueError("review_json_invalid") from error
  _bounded(value)
  return value


def _binding(value):
  if type(value) is not dict:
    raise ValueError("review_binding_invalid")
  required = {"manifest_sha256", "base_summary_sha256", "summary_id", "summary_revision", "session_id", "files"}
  if not required <= set(value):
    raise ValueError("review_binding_invalid")
  result = {key: deepcopy(value[key]) for key in required}
  result["version_assertions"] = deepcopy(value.get("version_assertions", {}))
  for key in ("manifest_sha256", "base_summary_sha256"):
    _sha(result[key])
  if type(result["summary_id"]) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", result["summary_id"]) is None:
    raise ValueError("review_binding_invalid")
  _integer(result["summary_revision"])
  if result["session_id"] is not None:
    _text(result["session_id"], 128)
  _array(result["files"], 512)
  inventory, names = [], set()
  for entry in result["files"]:
    if type(entry) is not dict or not {"path", "sha256", "role"} <= set(entry):
      raise ValueError("review_binding_invalid")
    path = str(_relative(entry["path"]))
    if path.casefold() in names:
      raise ValueError("review_binding_invalid")
    names.add(path.casefold())
    _sha(entry["sha256"])
    _text(entry["role"], 128)
    inventory.append({"path": path, "sha256": entry["sha256"], "role": entry["role"],
                      "sensitivity": entry.get("sensitivity", "private_context")})
  result["files"] = sorted(inventory, key=lambda row: row["path"])
  summaries = [row for row in inventory if row["path"] == "summary.json" and row["role"] == "summary"]
  if len(summaries) != 1 or summaries[0]["sha256"] != result["base_summary_sha256"]:
    raise ValueError("review_binding_invalid")
  assertions = result["version_assertions"]
  if type(assertions) is not dict or set(assertions) - {"code", "dbc", "analyzer"}:
    raise ValueError("review_binding_invalid")
  for version in assertions.values():
    _text(version, 256)
  if len(_canonical(result)) > 256 * 1024:
    raise ValueError("review_binding_limit")
  return result


def is_result_entry(entry):
  return (entry["role"] in {"candidate_result", "analysis_result", "result"}
          or entry["role"] == "candidate_context" and PurePosixPath(entry["path"]).name == "result.json")


def _validate_review(review, binding):
  """Validate caller JSON against server-verified inventory; return a detached value."""
  if type(review) in (bytes, str):
    review = _decode(review, MAX_DOCUMENT_BYTES)
  _bounded(review)
  if len(_canonical(review)) > MAX_DOCUMENT_BYTES:
    raise ValueError("review_document_limit")
  _object(review, FIELDS)
  if type(review["schema_version"]) is not int or review["schema_version"] != 1 or review["document_state"] != "final":
    raise ValueError("review_document_not_final")
  binding = _binding(binding)
  for field, source in (("bundle_manifest_hash", "manifest_sha256"), ("base_summary_sha256", "base_summary_sha256"),
                        ("summary_id", "summary_id"), ("summary_revision", "summary_revision")):
    if type(review[field]) is not type(binding[source]) or review[field] != binding[source]:
      raise ValueError("review_binding_mismatch")
  _integer(review["review_revision"])
  if review["previous_review_hash"] is not None:
    _sha(review["previous_review_hash"])
  if review["scope"] not in ("summary_only", "analysis_results") or review["verdict"] not in VERDICTS:
    raise ValueError("review_scope_invalid")
  _object(review["declared_reviewer"], {"tool", "model", "reviewed_at"})
  for item in review["declared_reviewer"].values():
    _text(item, 256)
  try:
    stamp = datetime.fromisoformat(review["declared_reviewer"]["reviewed_at"].replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
      raise ValueError("timezone missing")
  except ValueError as error:
    raise ValueError("review_timestamp_invalid") from error
  inventory = {row["path"]: row for row in binding["files"]}
  def reference(path, digest=None):
    path = str(_relative(path))
    if path not in inventory or digest is not None and inventory[path]["sha256"] != digest:
      raise ValueError("review_reference_mismatch")
    return inventory[path]
  def references(values):
    _array(values)
    if len(set(values)) != len(values):
      raise ValueError("review_reference_invalid")
    for path in values:
      reference(path)
  _array(review["base_results"])
  paths = set()
  for entry in review["base_results"]:
    _object(entry, {"path", "base_result_hash"})
    _sha(entry["base_result_hash"])
    if not is_result_entry(reference(entry["path"], entry["base_result_hash"])) or entry["path"] in paths:
      raise ValueError("review_base_result_invalid")
    paths.add(entry["path"])
  if (review["scope"] == "summary_only" and (paths or review["verdict"] == "reviewed_recommendation")
      or review["scope"] == "analysis_results" and not paths):
    raise ValueError("review_scope_invalid")
  _object(review["versions"], {"raw", "code", "dbc", "analyzer"})
  _array(review["versions"]["raw"])
  raw_paths = set()
  for entry in review["versions"]["raw"]:
    _object(entry, {"path", "sha256"})
    row = reference(entry["path"], entry["sha256"])
    _sha(entry["sha256"])
    if row["sensitivity"] != "private_raw" and row["role"] not in {"raw_log", "raw_rlog"} or row["path"] in raw_paths:
      raise ValueError("review_raw_version_invalid")
    raw_paths.add(row["path"])
  for name in ("code", "dbc", "analyzer"):
    version = review["versions"][name]
    _object(version, {"state", "value", "evidence_refs"})
    if version["state"] not in {"declared", "unknown"}:
      raise ValueError("review_version_invalid")
    references(version["evidence_refs"])
    if version["state"] == "unknown":
      if version["value"] is not None:
        raise ValueError("review_version_invalid")
    else:
      _text(version["value"], 256)
      assertion = binding["version_assertions"].get(name)
      if assertion is not None and assertion != version["value"]:
        raise ValueError("review_version_mismatch")
  _array(review["reviewed_scopes"])
  for scope in review["reviewed_scopes"]:
    _object(scope, {"file", "path_sha256", "range_ns", "checks"})
    _sha(scope["path_sha256"])
    reference(scope["file"], scope["path_sha256"])
    span = scope["range_ns"]
    if span is not None:
      if type(span) is not list or len(span) != 2:
        raise ValueError("review_range_invalid")
      for value in span:
        _integer(value, 0)
      if span[1] <= span[0]:
        raise ValueError("review_range_invalid")
    _array(scope["checks"], len(CHECKS))
    if not scope["checks"] or any(check not in CHECKS for check in scope["checks"]) or len(set(scope["checks"])) != len(scope["checks"]):
      raise ValueError("review_checks_invalid")
  _array(review["unreviewed_scopes"])
  for scope in review["unreviewed_scopes"]:
    _object(scope, {"file", "reason"})
    if scope["file"] is not None:
      reference(scope["file"])
    _text(scope["reason"])
  _array(review["findings"], 64)
  finding_ids = set()
  for finding in review["findings"]:
    _object(finding, {"id", "severity", "statement", "evidence_refs"})
    if type(finding["id"]) is not str or _ID.fullmatch(finding["id"]) is None or finding["id"] in finding_ids or finding["severity"] not in SEVERITIES:
      raise ValueError("review_finding_invalid")
    finding_ids.add(finding["id"])
    _text(finding["statement"])
    references(finding["evidence_refs"])
  for name in ("uncertainties", "follow_up"):
    _array(review[name])
    for item in review[name]:
      _text(item)
  if review["verdict"] == "reviewed_recommendation":
    reviewed = {scope["file"] for scope in review["reviewed_scopes"]}
    unreviewed = {scope["file"] for scope in review["unreviewed_scopes"]}
    if (not paths.intersection(reviewed) or not paths <= reviewed | unreviewed
        or not any(finding["evidence_refs"] for finding in review["findings"])):
      raise ValueError("review_recommendation_evidence_missing")
  return deepcopy(review)


def validate_review(review, binding):
  """Malformed nested JSON always fails through the ordinary validation path."""
  try:
    return _validate_review(review, binding)
  except (KeyError, TypeError, OverflowError, RecursionError) as error:
    raise ValueError("review_fields_invalid") from error


def review_version_status(review, binding, *, source_available=True):
  """Metadata matching is not reviewer authentication or vehicle qualification."""
  review = validate_review(review, binding)
  assertions = _binding(binding)["version_assertions"]
  return {name: ("unknown" if review["versions"][name]["state"] == "unknown" else
                 ("matched_export_metadata" if source_available else "declared_only")
                 if assertions.get(name) == review["versions"][name]["value"] else "declared_only")
          for name in ("code", "dbc", "analyzer")}


class ReviewResultStore:
  """Synchronous per-job store. Serialize construction and calls under the job owner."""
  def __init__(self, directory, binding=None, *, max_reviews=32, max_bytes=4 * 1024 * 1024):
    self.directory = Path(directory)
    if not self.directory.is_absolute() or _has_link(self.directory):
      raise ValueError("review_store_path_invalid")
    self.max_reviews, self.max_bytes = max_reviews, max_bytes
    if type(max_reviews) is not int or not 1 <= max_reviews <= 32 or type(max_bytes) is not int or not 1 <= max_bytes <= 4 * 1024 * 1024:
      raise ValueError("review_store_limit")
    self.binding = None if binding is None else _binding(binding)
    self.binding_state = "unavailable" if binding is None else "verified_export"
    self._records, self._reviews, self._bytes, self._last_record = [], {}, 0, None
    self._poisoned = False
    self._load()

  def _read(self, path, limit):
    if _has_link(path) or not _fs_path(path).is_file():
      raise ValueError("review_store_corrupt")
    with _fs_path(path).open("rb") as stream:
      raw = stream.read(limit + 1)
    self._bytes += len(raw)
    if self._bytes > self.max_bytes:
      raise ValueError("review_store_limit")
    return _decode(raw, limit)

  def _load(self):
    root = _fs_path(self.directory)
    if not root.exists():
      return
    names = []
    for entry in root.iterdir():
      names.append(entry.name)
      if len(names) > 129:
        raise ValueError("review_store_limit")
    names.sort()
    if "binding.json" not in names or any(name != "binding.json" and re.fullmatch(r"[0-9]{4}", name) is None for name in names):
      raise ValueError("review_store_incomplete")
    if len(names) == 1:
      raise ValueError("review_store_incomplete")
    saved = self._read(self.directory / "binding.json", 256 * 1024)
    _object(saved, {"schema_version", "kind", "binding"})
    if saved["schema_version"] != 1 or saved["kind"] != "control_advisor_review_results":
      raise ValueError("review_store_corrupt")
    binding = _binding(saved["binding"])
    if self.binding is not None and self.binding != binding:
      raise ValueError("review_binding_mismatch")
    self.binding = binding
    for index, name in enumerate((n for n in names if n != "binding.json"), 1):
      if name != f"{index:04d}" or index > 128:
        raise ValueError("review_store_corrupt")
      folder = self.directory / name
      children = []
      if not _has_link(folder) and _fs_path(folder).is_dir():
        for item in _fs_path(folder).iterdir():
          children.append(item.name)
          if len(children) > 2:
            break
      if set(children) != {"record.json", "complete.json"}:
        raise ValueError("review_store_incomplete")
      record = self._read(folder / "record.json", MAX_DOCUMENT_BYTES + 4096)
      seal = self._read(folder / "complete.json", 4096)
      _object(record, {"sequence", "previous_record_hash", "wrapper"})
      _object(seal, {"schema_version", "state", "record_sha256"})
      if seal != {"schema_version": 1, "state": "complete", "record_sha256": _hash(record)} or record["sequence"] != index or record["previous_record_hash"] != self._last_record:
        raise ValueError("review_store_corrupt")
      wrapper = record["wrapper"]
      _object(wrapper, {"schema_version", "request_id", "review_hash", "review", *FLAGS})
      if type(wrapper["schema_version"]) is not int or wrapper["schema_version"] != 1 or any(type(wrapper[key]) is not type(value) or wrapper[key] != value for key, value in FLAGS.items()):
        raise ValueError("review_store_corrupt")
      review = validate_review(wrapper["review"], self.binding)
      if _hash(review) != wrapper["review_hash"]:
        raise ValueError("review_store_corrupt")
      self._check_request(wrapper["request_id"], review, wrapper["review_hash"])
      self._records.append(wrapper)
      self._reviews.setdefault(review["review_revision"], wrapper)
      self._last_record = _hash(record)

  def _check_request(self, request_id, review, digest):
    if type(request_id) is not str or _ID.fullmatch(request_id) is None:
      raise ValueError("review_request_invalid")
    previous = next((row for row in self._records if row["request_id"] == request_id), None)
    if previous is not None:
      if previous["review_hash"] != digest:
        raise ValueError("review_request_conflict")
      return previous
    revision = review["review_revision"]
    if revision in self._reviews:
      if self._reviews[revision]["review_hash"] != digest:
        raise ValueError("review_revision_conflict")
      return None
    if revision != len(self._reviews) + 1:
      raise ValueError("review_revision_conflict")
    last_hash = self._reviews[revision - 1]["review_hash"] if revision > 1 else None
    if review["previous_review_hash"] != last_hash:
      raise ValueError("review_previous_hash_mismatch")
    if len(self._reviews) >= self.max_reviews:
      raise ValueError("review_store_limit")
    return None

  def _write(self, path, value):
    raw = _canonical(value) + b"\n"
    if self._bytes + len(raw) > self.max_bytes:
      raise ValueError("review_store_limit")
    with _fs_path(path).open("xb") as stream:
      stream.write(raw)
      stream.flush()
      os.fsync(stream.fileno())
    self._bytes += len(raw)

  def record(self, request_id, review):
    if self._poisoned:
      raise ValueError("review_store_incomplete")
    if self.binding_state != "verified_export":
      raise ValueError("review_source_unavailable")
    review = validate_review(review, self.binding)
    digest = _hash(review)
    existing = self._check_request(request_id, review, digest)
    if existing is not None:
      return deepcopy(existing)
    if len(self._records) >= 128:
      raise ValueError("review_store_limit")
    wrapper = {"schema_version": 1, "request_id": request_id, "review_hash": digest, "review": review, **FLAGS}
    record = {"sequence": len(self._records) + 1, "previous_record_hash": self._last_record, "wrapper": wrapper}
    seal = {"schema_version": 1, "state": "complete", "record_sha256": _hash(record)}
    owner = {"schema_version": 1, "kind": "control_advisor_review_results", "binding": self.binding}
    if len(_canonical(owner)) + 1 > 256 * 1024:
      raise ValueError("review_binding_limit")
    needed = sum(len(_canonical(value)) + 1 for value in (record, seal))
    if not _fs_path(self.directory).exists():
      needed += len(_canonical(owner)) + 1
    if self._bytes + needed > self.max_bytes:
      raise ValueError("review_store_limit")
    try:
      if _has_link(self.directory):
        raise ValueError("review_store_path_invalid")
      if not _fs_path(self.directory).exists():
        _fs_path(self.directory).mkdir(mode=0o700)
        self._write(self.directory / "binding.json", owner)
        _sync_directory(self.directory)
        _sync_directory(self.directory.parent)
      folder = self.directory / f"{record['sequence']:04d}"
      _fs_path(folder).mkdir(mode=0o700)
      self._write(folder / "record.json", record)
      self._write(folder / "complete.json", seal)
      _sync_directory(folder)
      _sync_directory(self.directory)
    except BaseException:
      self._poisoned = True
      raise
    self._records.append(wrapper)
    self._reviews.setdefault(review["review_revision"], wrapper)
    self._last_record = seal["record_sha256"]
    return deepcopy(wrapper)

  def list_reviews(self):
    if self._poisoned:
      raise ValueError("review_store_incomplete")
    return deepcopy(list(self._reviews.values()))

  def get(self, review_hash):
    _sha(review_hash)
    return next((row for row in self.list_reviews() if row["review_hash"] == review_hash), None)

  def get_request(self, request_id):
    if self._poisoned:
      raise ValueError("review_store_incomplete")
    if type(request_id) is not str or _ID.fullmatch(request_id) is None:
      raise ValueError("review_request_invalid")
    return deepcopy(next((row for row in self._records if row["request_id"] == request_id), None))
