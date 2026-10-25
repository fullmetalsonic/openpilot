"""Explicit local evidence copies; a manifest is published only after verification."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Sequence

CHUNK_BYTES = 1024 * 1024


def _check_cancel(cancelled: Callable[[], bool] | None) -> None:
  if cancelled is not None and cancelled():
    raise InterruptedError("evidence_cancelled")


def _positive_int(value: int, name: str) -> None:
  if type(value) is not int or value <= 0:
    raise ValueError(f"{name}_must_be_positive_integer")


def _regular_file(path: Path) -> Path:
  # Reject links before resolving them; never copy through a caller's link.
  if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (path, *path.parents)):
    raise ValueError("evidence_link_not_allowed")
  if not path.is_file():
    raise ValueError("evidence_source_not_regular_file")
  return path.resolve()


def hash_file(path: Path, byte_limit: int, cancelled: Callable[[], bool] | None = None) -> tuple[str, int]:
  _positive_int(byte_limit, "byte_limit")
  path = _regular_file(Path(path))
  digest, size = hashlib.sha256(), 0
  with path.open("rb") as stream:
    while True:
      _check_cancel(cancelled)
      block = stream.read(min(CHUNK_BYTES, byte_limit - size + 1))
      if not block:
        break
      size += len(block)
      if size > byte_limit:
        raise ValueError("evidence_byte_limit")
      digest.update(block)
  return digest.hexdigest(), size


def _write_json(path: Path, value: dict) -> None:
  content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
  partial = path.with_suffix(path.suffix + ".partial")
  with partial.open("xb") as stream:
    stream.write(content)
    stream.flush()
    os.fsync(stream.fileno())
  os.replace(partial, path)


def _sync_directory(path: Path) -> None:
  if os.name != "nt":
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(fd)
    finally:
      os.close(fd)


def create_bundle(destination: Path, sources: Sequence[Path], result: dict, *, byte_limit: int,
                  max_files: int = 16, cancelled: Callable[[], bool] | None = None,
                  expected_sha256: Sequence[str] | None = None) -> dict:
  """Create a new bundle. Failures retain an incomplete directory, never edit originals.

  The byte budget covers copied inputs plus result.json. A completed source file
  must be selected by the caller; hash stability alone cannot prove log closure.
  """
  _positive_int(byte_limit, "byte_limit")
  _positive_int(max_files, "max_files")
  if not sources or len(sources) > max_files:
    raise ValueError("evidence_file_count")
  if expected_sha256 is not None and (len(expected_sha256) != len(sources) or any(
      not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
      for value in expected_sha256)):
    raise ValueError("invalid_expected_source_hashes")
  paths = [_regular_file(Path(source)) for source in sources]
  if len(set(paths)) != len(paths):
    raise ValueError("duplicate_evidence_source")
  destination = Path(destination)
  if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (destination, *destination.parents)):
    raise ValueError("evidence_link_not_allowed")
  result_bytes = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
  if sum(path.stat().st_size for path in paths) + len(result_bytes) > byte_limit:
    raise ValueError("evidence_byte_limit")
  _check_cancel(cancelled)
  destination.mkdir(parents=False, exist_ok=False)
  remaining = byte_limit - len(result_bytes)
  entries = []
  try:
    for index, source in enumerate(paths):
      _check_cancel(cancelled)
      initial = source.stat()
      name = f"source-{index:02d}{''.join(source.suffixes)}"
      target = destination / name
      partial = target.with_suffix(target.suffix + ".partial")
      digest, size = hashlib.sha256(), 0
      with source.open("rb") as incoming, partial.open("xb") as outgoing:
        while True:
          _check_cancel(cancelled)
          block = incoming.read(min(CHUNK_BYTES, remaining - size + 1))
          if not block:
            break
          size += len(block)
          if size > remaining:
            raise ValueError("evidence_byte_limit")
          digest.update(block)
          outgoing.write(block)
        outgoing.flush()
        os.fsync(outgoing.fileno())
      final = source.stat()
      if (initial.st_size, initial.st_mtime_ns, initial.st_ino) != (final.st_size, final.st_mtime_ns, final.st_ino):
        raise ValueError("evidence_source_changed")
      expected = digest.hexdigest()
      if expected_sha256 is not None and expected != expected_sha256[index]:
        raise ValueError("evidence_source_changed_since_analysis")
      # Verify both the actual copied bytes and a second pass over the source.
      budget = max(1, remaining)
      if hash_file(partial, budget, cancelled) != (expected, size) or hash_file(source, budget, cancelled) != (expected, size):
        raise ValueError("evidence_hash_mismatch")
      os.replace(partial, target)
      entries.append({"path": name, "original_name": source.name, "bytes": size, "sha256": expected})
      remaining -= size
    _check_cancel(cancelled)
    _write_json(destination / "result.json", result)
    result_digest, result_size = hash_file(destination / "result.json", byte_limit, cancelled)
    manifest = {"schema_version": 1, "state": "complete", "files": entries,
                "result": {"path": "result.json", "sha256": result_digest, "bytes": result_size},
                "power_loss_durability": "file_fsync_and_directory_fsync" if os.name != "nt" else "file_fsync_only"}
    _check_cancel(cancelled)
    _sync_directory(destination)
    _write_json(destination / "bundle.json", manifest)
    _sync_directory(destination)
    return manifest
  except BaseException:
    # No complete marker survives a failed creation, including directory sync.
    (destination / "bundle.json").unlink(missing_ok=True)
    raise


def verify_bundle(directory: Path, *, byte_limit: int, max_files: int = 16) -> dict:
  _positive_int(byte_limit, "byte_limit")
  _positive_int(max_files, "max_files")
  directory = Path(directory)
  marker = _regular_file(directory / "bundle.json")
  if marker.stat().st_size > 128 * 1024:
    raise ValueError("evidence_manifest_limit")
  manifest = json.loads(marker.read_text(encoding="utf-8"))
  if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("state") != "complete":
    raise ValueError("incomplete_evidence")
  files = manifest.get("files")
  if not isinstance(files, list) or not 1 <= len(files) <= max_files or not isinstance(manifest.get("result"), dict):
    raise ValueError("evidence_file_count")
  if manifest["result"].get("path") != "result.json":
    raise ValueError("invalid_result_path")
  seen, total = set(), 0
  for entry in [*files, manifest["result"]]:
    if not isinstance(entry, dict):
      raise ValueError("invalid_evidence_entry")
    name = entry.get("path")
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name or name in seen:
      raise ValueError("invalid_evidence_path")
    seen.add(name)
    if type(entry.get("bytes")) is not int or entry["bytes"] < 0:
      raise ValueError("invalid_evidence_size")
    total += entry["bytes"]
    if total > byte_limit:
      raise ValueError("evidence_byte_limit")
    if hash_file(directory / name, max(1, entry["bytes"])) != (entry.get("sha256"), entry["bytes"]):
      raise ValueError("evidence_hash_mismatch")
  return manifest
