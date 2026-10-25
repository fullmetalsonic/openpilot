"""Preserve explicit closed rlogs without modifying the logger or its originals."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from openpilot.tools.lib.helpers import RE

from .evidence import CHUNK_BYTES, _check_cancel, _positive_int, _regular_file, _sync_directory, _write_json, hash_file
from .rlog import validate_closed_log


SEGMENT = re.compile(rf"^(?P<route>{RE.LOG_ID})--(?P<segment>[0-9]+)$")
LOG_NAMES = ("rlog", "rlog.zst", "rlog.bz2")
MAX_MANIFEST_BYTES = 128 * 1024
SCHEMA_FILES = ("openpilot/cereal/log.capnp", "openpilot/cereal/custom.capnp", "openpilot/cereal/deprecated.capnp",
                "openpilot/cereal/include/c++.capnp", "opendbc_repo/opendbc/car/car.capnp",
                "opendbc_repo/opendbc/car/include/c++.capnp")


def _plain_directory(path: Path) -> Path:
  if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (path, *path.parents)):
    raise ValueError("archive_link_not_allowed")
  if not path.is_dir():
    raise ValueError("archive_directory_required")
  return path.resolve()


def _identity(path):
  status = path.stat()
  return status.st_dev, status.st_ino


def _fingerprint(path):
  status = path.stat()
  return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns


def _segment_parts(relative):
  if not isinstance(relative, (str, os.PathLike)):
    raise ValueError("archive_unrecognized_log_path")
  path = Path(relative)
  if path.is_absolute() or len(path.parts) != 2 or any(part in (".", "..") for part in path.parts) or path.name not in LOG_NAMES:
    raise ValueError("archive_unrecognized_log_path")
  match = SEGMENT.fullmatch(path.parent.name)
  if match is None:
    raise ValueError("archive_unrecognized_segment")
  return match["route"], int(match["segment"])


def _integer(value, name, *, minimum=0):
  if type(value) is not int or value < minimum:
    raise ValueError(f"invalid_{name}")


def _window(window):
  if not isinstance(window, (tuple, list)) or len(window) != 2:
    raise ValueError("invalid_archive_window")
  _integer(window[0], "archive_window_start")
  _integer(window[1], "archive_window_end")
  if window[0] > window[1]:
    raise ValueError("invalid_archive_window")
  return window[0], window[1]


def _journal(reference):
  if isinstance(reference, dict) and reference.get("schema_version") == 2:
    from .journal_segments import validate_reference
    return validate_reference(reference)
  required = {"path", "sequence", "sha256", "bytes", "durable"}
  if not isinstance(reference, dict) or set(reference) != required or reference["path"] != "settings.jsonl" or reference["durable"] is not True:
    raise ValueError("invalid_archive_journal_reference")
  _integer(reference["sequence"], "journal_sequence", minimum=1)
  _integer(reference["bytes"], "journal_bytes", minimum=1)
  if not isinstance(reference["sha256"], str) or re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]) is None:
    raise ValueError("invalid_archive_journal_hash")
  return deepcopy(reference)


def _code_metadata(code_commit=None):
  if code_commit is not None and (not isinstance(code_commit, str) or re.fullmatch(r"[a-fA-F0-9]{40}", code_commit) is None):
    raise ValueError("invalid_archive_code_commit")
  package = Path(__file__).resolve().parent
  root = package.parents[3]
  return {"declared_code_commit": code_commit,
          "decoder_sha256": hash_file(package / "rlog.py", 4 * 1024 * 1024)[0],
          "normalizer_sha256": hash_file(package / "normalizer.py", 4 * 1024 * 1024)[0],
          "schema_sha256": {name: hash_file(root / name, 4 * 1024 * 1024)[0] for name in SCHEMA_FILES}}


def _overlap(coverage, window):
  # InitData/sentinel timestamps alone cannot make a segment relevant.
  state = coverage.get("services", {}).get("carState")
  if not state or state["mono_max_ns"] < window[0] or state["mono_min_ns"] > window[1]:
    raise ValueError("archive_window_not_covered")


def _record(manifest, directory, digest):
  source, coverage, analysis = manifest["source"], manifest["coverage"], manifest["analysis"]
  state_bounds = coverage["services"]["carState"]
  return {"state": "complete", "archive_id": manifest["archive_id"], "session_id": manifest["session_id"],
          "source_mode": manifest["source_mode"], "source_relative": source["relative_path"],
          "route": source["route"], "segment": source["segment"], "sha256": source["sha256"], "bytes": source["bytes"],
          "start_mono_ns": state_bounds["mono_min_ns"], "end_mono_ns": state_bounds["mono_max_ns"],
          "decompressed_bytes": coverage["decompressed_bytes"],
          "manifest_relative": f"{directory.name}/archive.json", "manifest_sha256": digest,
          "settings_history_reference": deepcopy(manifest["settings_history_reference"]), **analysis}


def _read_manifest(path):
  path = _regular_file(path)
  if path.stat().st_size > MAX_MANIFEST_BYTES:
    raise ValueError("archive_manifest_limit")

  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError("duplicate_archive_manifest_key")
      result[key] = value
    return result

  try:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_archive_manifest")))
  except (RecursionError, UnicodeError, json.JSONDecodeError) as exc:
    raise ValueError("invalid_archive_manifest") from exc


def verify_preserved_segment(directory: Path, *, byte_limit: int, max_decompressed_bytes=256 * 1024 * 1024,
                             max_events=200_000, cancelled=None) -> dict:
  """Verify an archive using its own bytes; the original log may be gone."""
  _positive_int(byte_limit, "byte_limit")
  directory = _plain_directory(Path(directory))
  marker = directory / "archive.json"
  manifest = _read_manifest(marker)
  if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1 or manifest.get("state") != "complete":
    raise ValueError("incomplete_archive")
  if re.fullmatch(r"[a-f0-9]{32}", directory.name) is None or manifest.get("archive_id") != directory.name:
    raise ValueError("invalid_archive_id")
  if not isinstance(manifest.get("session_id"), str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", manifest["session_id"]) is None:
    raise ValueError("invalid_archive_session")
  if manifest.get("source_mode") not in ("live", "synthetic"):
    raise ValueError("invalid_archive_source_mode")
  source, stored, analysis = manifest.get("source"), manifest.get("file"), manifest.get("analysis")
  if not all(isinstance(item, dict) for item in (source, stored, analysis)):
    raise ValueError("invalid_archive_manifest")
  route, segment = _segment_parts(source.get("relative_path", ""))
  _integer(source.get("segment"), "source_segment")
  if source.get("route") != route or source.get("segment") != segment or stored.get("path") not in LOG_NAMES or stored["path"] != Path(source["relative_path"]).name:
    raise ValueError("invalid_archive_source")
  window = _window((analysis.get("window_start_ns"), analysis.get("window_end_ns")))
  _integer(analysis.get("diagnostic_revision"), "diagnostic_revision", minimum=1)
  _integer(analysis.get("settings_revision"), "settings_revision")
  _journal(manifest.get("settings_history_reference"))
  size = stored.get("bytes")
  _integer(size, "archive_size", minimum=1)
  if size != source.get("bytes") or stored.get("sha256") != source.get("sha256") or size + marker.stat().st_size > byte_limit:
    raise ValueError("archive_byte_limit_or_source_mismatch")
  payload = _regular_file(directory / stored["path"])
  names = set()
  with os.scandir(directory) as entries:
    for entry in entries:
      if len(names) >= 2 or not entry.is_file(follow_symlinks=False):
        raise ValueError("unexpected_archive_file")
      names.add(entry.name)
  if names != {"archive.json", stored["path"]}:
    raise ValueError("unexpected_archive_file")
  if hash_file(payload, byte_limit, cancelled) != (stored["sha256"], size):
    raise ValueError("archive_hash_mismatch")
  code = manifest.get("code")
  if not isinstance(code, dict) or code.get("schema_sha256") != _code_metadata()["schema_sha256"]:
    raise RuntimeError("archive_decoder_schema_unavailable")
  coverage = validate_closed_log(payload, max_decompressed_bytes=max_decompressed_bytes, max_events=max_events, cancelled=cancelled)
  if coverage != manifest.get("coverage"):
    raise ValueError("archive_coverage_mismatch")
  _overlap(coverage, window)
  digest, _ = hash_file(marker, MAX_MANIFEST_BYTES, cancelled)
  return _record(manifest, directory, digest)


class SegmentArchive:
  def __init__(self, log_root: Path, archive_root: Path, *, session_id: str, source_mode: str, byte_limit: int,
               max_files: int, free_reserve_bytes: int, code_commit=None, max_decompressed_bytes=256 * 1024 * 1024,
               max_events=200_000, read_byte_limit=None):
    if not isinstance(session_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id) is None:
      raise ValueError("invalid_archive_session")
    if source_mode not in ("live", "synthetic"):
      raise ValueError("invalid_archive_source_mode")
    for value, name in ((byte_limit, "byte_limit"), (max_files, "max_files"),
                        (max_decompressed_bytes, "max_decompressed_bytes"), (max_events, "max_events")):
      _positive_int(value, name)
    _integer(free_reserve_bytes, "free_reserve_bytes")
    if max_files > 4096:
      raise ValueError("archive_file_count_limit")
    self.log_root = _plain_directory(Path(log_root))
    archive_root = Path(archive_root)
    _plain_directory(archive_root.parent)
    if archive_root.is_symlink() or (hasattr(archive_root, "is_junction") and archive_root.is_junction()):
      raise ValueError("archive_link_not_allowed")
    proposed = archive_root.resolve()
    if proposed == self.log_root or proposed in self.log_root.parents or self.log_root in proposed.parents:
      raise ValueError("archive_roots_overlap")
    archive_root.mkdir(exist_ok=True)
    self.archive_root = _plain_directory(archive_root)
    self._root_identities = _identity(self.log_root), _identity(self.archive_root)
    self.session_id, self.source_mode, self.code = session_id, source_mode, _code_metadata(code_commit)
    self.byte_limit, self.max_files, self.free_reserve_bytes = byte_limit, max_files, free_reserve_bytes
    self.max_decompressed_bytes, self.max_events = max_decompressed_bytes, max_events
    self.read_byte_limit = byte_limit * 16 if read_byte_limit is None else read_byte_limit
    _positive_int(self.read_byte_limit, "read_byte_limit")
    self._read_charged = self._validation_passes = self._hash_passes = 0
    self._lock = threading.Lock()
    self._discovery = None
    self._discovery_route = None
    self._discovery_complete = False
    self._verified = {}
    self._last_inspection = []
    self._usage()

  def _roots(self):
    if (_identity(_plain_directory(self.log_root)), _identity(_plain_directory(self.archive_root))) != self._root_identities:
      raise ValueError("archive_root_changed")

  def _usage(self):
    self._roots()
    size, count, directories = 0, 0, []
    with os.scandir(self.archive_root) as entries:
      for index, entry in enumerate(entries):
        if index >= self.max_files * 2 + 16:
          raise ValueError("archive_scan_limit")
        path = Path(entry.path)
        if entry.is_dir(follow_symlinks=False):
          _plain_directory(path)
          directories.append(path)
          with os.scandir(path) as files:
            for nested in files:
              file_path = _regular_file(Path(nested.path))
              size += file_path.stat().st_size
              count += 1
              if count > self.max_files or size > self.byte_limit:
                raise ValueError("archive_session_budget_exceeded")
        else:
          size += _regular_file(path).stat().st_size
          count += 1
          if count > self.max_files or size > self.byte_limit:
            raise ValueError("archive_session_budget_exceeded")
    return size, count, directories

  def _source(self, source):
    self._roots()
    path = Path(source)
    if path.is_absolute():
      try:
        relative = path.relative_to(self.log_root)
      except ValueError as exc:
        raise ValueError("archive_source_outside_log_root") from exc
    else:
      relative, path = path, self.log_root / path
    route, segment = _segment_parts(relative)
    path = _regular_file(path)
    if path.relative_to(self.log_root) != relative:
      raise ValueError("archive_source_changed")
    self._unlocked(path)
    return path, relative.as_posix(), route, segment

  @staticmethod
  def _unlocked(source):
    if os.path.lexists(source.parent / "rlog.lock") or os.path.lexists(source.with_name(source.name + ".lock")):
      raise ValueError("archive_source_open")

  def inspect_candidate(self, source, *, window_ns=None, cancelled=None):
    path, relative, route, segment = self._source(source)
    initial = _fingerprint(path)
    coverage = self._validate(path, cancelled)
    if window_ns is not None:
      _overlap(coverage, _window(window_ns))
    state_bounds = coverage.get("services", {}).get("carState")
    if not state_bounds:
      raise ValueError("archive_no_control_time_coverage")
    digest, size = self._hash(path, cancelled)
    self._unlocked(path)
    self._roots()
    if _fingerprint(_regular_file(path)) != initial:
      raise ValueError("archive_source_changed")
    return {"source_relative": relative, "route": route, "segment": segment, "sha256": digest, "bytes": size,
            "start_mono_ns": state_bounds["mono_min_ns"], "end_mono_ns": state_bounds["mono_max_ns"], "coverage": coverage,
            "fingerprint": initial}

  def _charge(self, size):
    if self._read_charged + size > self.read_byte_limit:
      raise ValueError("archive_read_budget_exceeded")
    self._read_charged += size

  def _validate(self, path, cancelled):
    # Includes magic probes, an EOF probe, and the zstd framing + decode passes.
    self._charge(path.stat().st_size * 2 + 8)
    self._validation_passes += 1
    return validate_closed_log(path, max_decompressed_bytes=self.max_decompressed_bytes,
                               max_events=self.max_events, cancelled=cancelled)

  def _hash(self, path, cancelled):
    size = path.stat().st_size
    self._charge(size + 1)
    self._hash_passes += 1
    return hash_file(path, max(1, size), cancelled)

  def discover_candidates(self, *, max_entries=200, route=None):
    _positive_int(max_entries, "max_entries")
    if max_entries > 4096:
      raise ValueError("archive_discovery_limit")
    if route is not None and (not isinstance(route, str) or re.fullmatch(RE.LOG_ID, route) is None):
      raise ValueError("archive_unrecognized_route")
    self._roots()
    if self._discovery is None or self._discovery_route != route:
      self.close()
      self._discovery = os.scandir(self.log_root)
      self._discovery_route = route
    self._discovery_complete = False
    candidates = []
    for _ in range(max_entries):
      try:
        entry = next(self._discovery)
      except StopIteration:
        self.close()
        self._discovery_complete = True
        break
      match = SEGMENT.fullmatch(entry.name)
      if match is None or (route is not None and match["route"] != route) or not entry.is_dir(follow_symlinks=False):
        continue
      directory = _plain_directory(Path(entry.path))
      available = [name for name in LOG_NAMES if os.path.lexists(directory / name)]
      if len(available) != 1:
        continue
      try:
        path, relative, selected_route, segment = self._source(directory / available[0])
      except (ValueError, OSError):
        continue
      candidates.append({"source_relative": relative, "route": selected_route, "segment": segment, "closure": "unverified"})
    return candidates

  def _reserve(self, required_bytes):
    if shutil.disk_usage(self.archive_root).free - required_bytes < self.free_reserve_bytes:
      raise OSError("archive_free_space_reserve")

  def preserve(self, source, *, window_ns, diagnostic_revision, settings_revision, journal_ref, cancelled=None):
    window = _window(window_ns)
    _integer(diagnostic_revision, "diagnostic_revision", minimum=1)
    _integer(settings_revision, "settings_revision")
    journal = _journal(journal_ref)
    if not self._lock.acquire(blocking=False):
      raise RuntimeError("archive_busy")
    created_directory = False
    try:
      candidate = self.inspect_candidate(source, window_ns=window, cancelled=cancelled)
      path, _, _, _ = self._source(candidate["source_relative"])
      archive_id = uuid.uuid4().hex
      directory = self.archive_root / archive_id
      manifest = {"schema_version": 1, "state": "complete", "archive_id": archive_id, "session_id": self.session_id,
                  "source_mode": self.source_mode, "source": {key: candidate[key] for key in ("route", "segment", "sha256", "bytes")},
                  "file": {"path": path.name, "sha256": candidate["sha256"], "bytes": candidate["bytes"]},
                  "coverage": candidate["coverage"], "analysis": {"window_start_ns": window[0], "window_end_ns": window[1],
                    "diagnostic_revision": diagnostic_revision, "settings_revision": settings_revision},
                  "settings_history_reference": journal, "code": deepcopy(self.code),
                  "power_loss_durability": "file_fsync_and_directory_fsync" if os.name != "nt" else "file_fsync_only"}
      manifest["source"]["relative_path"] = candidate["source_relative"]
      metadata_bytes = len((json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
      if metadata_bytes > MAX_MANIFEST_BYTES:
        raise ValueError("archive_manifest_limit")
      used, count, _ = self._usage()
      if used + candidate["bytes"] + metadata_bytes > self.byte_limit or count + 2 > self.max_files:
        raise ValueError("archive_session_budget_exceeded")
      _check_cancel(cancelled)
      self._reserve(candidate["bytes"] + metadata_bytes)
      directory.mkdir()
      created_directory = True
      partial = directory / (path.name + ".partial")
      digest, size = hashlib.sha256(), 0
      self._charge(candidate["bytes"] + 1)
      with path.open("rb") as incoming, partial.open("xb") as outgoing:
        while True:
          _check_cancel(cancelled)
          block = incoming.read(min(CHUNK_BYTES, candidate["bytes"] - size + 1))
          if not block:
            break
          size += len(block)
          if size > candidate["bytes"]:
            raise ValueError("archive_source_changed")
          self._reserve(len(block) + metadata_bytes)
          outgoing.write(block)
          digest.update(block)
        outgoing.flush()
        os.fsync(outgoing.fileno())
      if (digest.hexdigest(), size) != (candidate["sha256"], candidate["bytes"]):
        raise ValueError("archive_source_changed")
      coverage = self._validate(partial, cancelled)
      if coverage != candidate["coverage"] or self._hash(partial, cancelled) != (candidate["sha256"], size):
        raise ValueError("archive_copy_verification_failed")
      self._unlocked(path)
      self._roots()
      if _fingerprint(_regular_file(path)) != candidate["fingerprint"] or self._hash(path, cancelled) != (candidate["sha256"], size):
        raise ValueError("archive_source_changed")
      _check_cancel(cancelled)
      self._unlocked(path)
      os.replace(partial, directory / path.name)
      self._reserve(metadata_bytes)
      _sync_directory(directory)
      _check_cancel(cancelled)
      _write_json(directory / "archive.json", manifest)
      try:
        _sync_directory(directory)
        _sync_directory(self.archive_root)
      except BaseException:
        (directory / "archive.json").unlink(missing_ok=True)
        raise
      # The atomic marker commits the archive. Finish the bounded receipt even
      # if cancellation arrives now; recovery can rediscover an unjournaled copy.
      manifest_hash, _ = hash_file(directory / "archive.json", MAX_MANIFEST_BYTES)
      record = _record(manifest, directory, manifest_hash)
      self._verified[archive_id] = record
      return deepcopy(record)
    except BaseException:
      if created_directory:
        (directory / "archive.json").unlink(missing_ok=True)
      raise
    finally:
      self._lock.release()

  def inspect_existing(self, *, cancelled=None):
    _, _, directories = self._usage()
    results, remaining_decoded, remaining_events = [], self.max_decompressed_bytes, self.max_events
    for directory in directories:
      _check_cancel(cancelled)
      if not (directory / "archive.json").exists():
        results.append({"archive_id": directory.name, "state": "incomplete"})
        continue
      if remaining_decoded <= 0 or remaining_events <= 0:
        self._verified.pop(directory.name, None)
        results.append({"archive_id": directory.name, "state": "unverified", "reason": "inspection_budget_exhausted"})
        continue
      try:
        marker = _read_manifest(directory / "archive.json")
        file = marker.get("file", {}) if isinstance(marker, dict) else {}
        if file.get("path") not in LOG_NAMES:
          raise ValueError("invalid_archive_file")
        self._charge((directory / file["path"]).stat().st_size * 3 + (directory / "archive.json").stat().st_size * 2 + 16)
        record = verify_preserved_segment(directory, byte_limit=self.byte_limit, max_decompressed_bytes=remaining_decoded,
                                          max_events=remaining_events, cancelled=cancelled)
        remaining_decoded -= record["decompressed_bytes"]
        remaining_events -= marker["coverage"]["event_count"]
        if record["session_id"] != self.session_id or record["source_mode"] != self.source_mode:
          raise ValueError("archive_session_mismatch")
        self._verified[directory.name] = record
        results.append(record)
      except InterruptedError:
        raise
      except (ValueError, OSError, RuntimeError, RecursionError) as exc:
        # A failing decoder may have consumed its whole allowance. Do not give
        # each subsequent damaged file a fresh decompression budget.
        remaining_decoded = remaining_events = 0
        self._verified.pop(directory.name, None)
        budget_failure = str(exc) in ("archive_read_budget_exceeded", "decompressed byte limit exceeded", "rlog event count limit exceeded")
        state = "unverified" if budget_failure else "unverifiable" if isinstance(exc, RuntimeError) else "corrupt"
        results.append({"archive_id": directory.name, "state": state,
                        "reason": "inspection_budget_exhausted" if budget_failure else type(exc).__name__})
    self._last_inspection = results
    return deepcopy(results)

  def status(self):
    used, count, directories = self._usage()
    return {"used_bytes": used, "file_count": count, "byte_limit": self.byte_limit, "max_files": self.max_files,
            "free_reserve_bytes": self.free_reserve_bytes, "complete_segments": len(self._verified),
            "unverified_or_incomplete_segments": max(0, len(directories) - len(self._verified)),
            "discovery_complete": self._discovery_complete,
            "read_byte_limit": self.read_byte_limit, "read_bytes_charged": self._read_charged,
            "read_accounting": "conservative_file_pass_upper_bound_not_physical_io",
            "validation_passes": self._validation_passes, "hash_passes": self._hash_passes,
            "verification": "creation_or_last_explicit_inspection", "source_mode": self.source_mode}

  def close(self):
    if self._discovery is not None:
      self._discovery.close()
      self._discovery = None
