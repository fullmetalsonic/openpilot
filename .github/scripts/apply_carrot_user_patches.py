#!/usr/bin/env python3
"""Reconstruct the reviewed FMS integration from the pinned upstream tree.

All target and asset hashes are checked before any product file is written.
Shared files use exact-context hunks against the latest upstream; pure additions
are bundled blobs. No Git history, network access, Params, or device is needed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).resolve().parent
MANIFEST = ASSETS / "fms_integration_manifest.json"


def normalized(data: bytes) -> bytes:
  return data.replace(b"\r\n", b"\n")


def digest(data: bytes | None) -> str | None:
  return hashlib.sha256(data).hexdigest() if data is not None else None


def safe_path(root: Path, relative: str) -> Path:
  path = PurePosixPath(relative)
  if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in relative or ":" in relative:
    raise RuntimeError(f"Unsafe manifest path: {relative}")
  target = root.joinpath(*path.parts)
  if not target.resolve().is_relative_to(root.resolve()):
    raise RuntimeError(f"Manifest path escapes root: {relative}")
  return target


def replace_once(text: str, original: str, patched: str, name: str) -> str:
  # A deletion result may be a prefix of its original. Original always wins.
  if original in text:
    if text.count(original) != 1:
      raise RuntimeError(f"Ambiguous original block: {name}")
    return text.replace(original, patched, 1)
  if patched in text:
    return text
  raise RuntimeError(f"Source shape changed: {name}")


def apply_exact_hunks(source: bytes, patch: bytes, name: str) -> bytes:
  """Apply ordinary unified hunks without offset search, fuzz, or guessing."""
  lines = source.decode("utf-8").splitlines(keepends=True)
  patch_lines: list[str] = []
  for line in patch.decode("utf-8").splitlines(keepends=True):
    if line == "\\ No newline at end of file\n":
      if not patch_lines or patch_lines[-1][0] not in " +-":
        raise RuntimeError(f"Malformed missing-newline marker: {name}")
      patch_lines[-1] = patch_lines[-1].removesuffix("\n")
    else:
      patch_lines.append(line)
  output: list[str] = []
  cursor = 0
  i = 0
  hunks = 0
  while i < len(patch_lines):
    header = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n?", patch_lines[i])
    if header is None:
      if patch_lines[i].startswith(("--- ", "+++ ")):
        i += 1
        continue
      raise RuntimeError(f"Malformed patch header: {name}")
    old_start, old_count, new_start, new_count = header.groups()
    old_count, new_count = int(old_count or 1), int(new_count or 1)
    position = int(old_start) - (1 if old_count else 0)
    if position < cursor or position > len(lines):
      raise RuntimeError(f"Invalid patch position: {name}")
    output.extend(lines[cursor:position])
    if len(output) != int(new_start) - (1 if new_count else 0):
      raise RuntimeError(f"New patch position mismatch: {name}")
    cursor = position
    seen_old = seen_new = 0
    i += 1
    while i < len(patch_lines) and not patch_lines[i].startswith("@@ "):
      entry = patch_lines[i]
      if not entry or entry[0] not in " +-":
        raise RuntimeError(f"Malformed patch body: {name}")
      kind, value = entry[0], entry[1:]
      if kind in " -":
        if cursor >= len(lines) or lines[cursor] != value:
          raise RuntimeError(f"Exact patch context changed: {name}, line {cursor + 1}")
        cursor += 1
        seen_old += 1
      if kind in " +":
        output.append(value)
        seen_new += 1
      i += 1
    if (seen_old, seen_new) != (old_count, new_count):
      raise RuntimeError(f"Patch hunk count mismatch: {name}")
    hunks += 1
  if not hunks:
    raise RuntimeError(f"Patch contains no hunks: {name}")
  output.extend(lines[cursor:])
  return "".join(output).encode("utf-8")


def apply_integration(root: Path = ROOT, manifest_path: Path = MANIFEST) -> dict[str, int]:
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  if manifest.get("format") != 1 or manifest.get("upstream") != "5e187ab9da726c524ca51aebacc6120802480415":
    raise RuntimeError("Unsupported integration manifest/base")
  prepared: list[tuple[Path, bytes | None]] = []
  visited: set[str] = set()
  for operation in manifest["operations"]:
    name = operation["path"]
    if name in visited:
      raise RuntimeError(f"Duplicate manifest target: {name}")
    visited.add(name)
    destination = safe_path(root, name)
    if destination.exists() and not destination.is_file():
      raise RuntimeError(f"Source target is not a regular file: {name}")
    for parent in destination.parents:
      if parent == root.resolve():
        break
      if parent.exists() and not parent.is_dir():
        raise RuntimeError(f"Source parent is not a directory: {name}")
    current = normalized(destination.read_bytes()) if destination.is_file() else None
    current_hash = digest(current)
    asset_data = None
    if operation.get("asset"):
      asset = safe_path(manifest_path.parent, operation["asset"])
      asset_data = asset.read_bytes()
      if digest(asset_data) != operation["asset_sha256"]:
        raise RuntimeError(f"Preservation asset hash mismatch: {name}")
    # Validate every asset even on a fully applied tree.
    if current_hash == operation["after_sha256"]:
      continue
    if current_hash != operation["before_sha256"]:
      raise RuntimeError(f"Source hash/shape changed; refusing to guess: {name}")
    kind = operation["kind"]
    if kind == "delete":
      candidate = None
    elif kind == "blob":
      candidate = asset_data
    elif kind == "hunks" and current is not None and asset_data is not None:
      candidate = apply_exact_hunks(current, asset_data, name)
    else:
      raise RuntimeError(f"Unsupported preservation operation: {name}")
    if digest(candidate) != operation["after_sha256"]:
      raise RuntimeError(f"Reconstruction result hash mismatch: {name}")
    prepared.append((destination, candidate))
  # Preflight is complete: malformed input cannot leave a partly applied tree.
  for destination, content in prepared:
    if content is None:
      destination.unlink()
    else:
      destination.parent.mkdir(parents=True, exist_ok=True)
      destination.write_bytes(content)
  return {"changed": len(prepared), "verified": len(visited)}


def apply_base_patches() -> None:
  """Compatibility entry point; all overlapping layers are one manifest now."""
  apply_integration()


def main() -> None:
  result = apply_integration()
  print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
  main()
