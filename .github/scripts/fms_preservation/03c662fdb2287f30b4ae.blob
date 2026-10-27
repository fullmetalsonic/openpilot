"""Bounded local review copies. No upload, source repair, or current recommendation."""
from copy import deepcopy
import csv
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tempfile
import time
import zipfile

from .evidence import _regular_file, _sync_directory, _write_json


_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_SHA = re.compile(r"[a-f0-9]{64}")
CHUNK = 64 * 1024
SEGMENTED_HISTORY_BYTES = 32 * 1024 * 1024
DEFAULT_BUDGET = {"max_bytes": 512 * 1024 * 1024, "max_files": 512, "timeout_seconds": 120}
SELECTION_FIELDS = {"schema_version", "summary_id", "summary_revision", "session_id", "source_kind", "historical_only",
                    "summary", "checkpoints", "files", "source_quality", "expected_inventory"}
ENTRY_FIELDS = {"root_id", "relative_path", "export_relative_path", "sha256", "bytes", "role", "sensitivity"}
REVIEW_README = """# Control advisor: local second review

Verify review.json and every declared file SHA-256 before analysing this pack.
This is historical evidence, never an active recommendation or apply permission.
Check source_kind/generated provenance, private_context/private_raw classifications,
cutoff/prefix boundaries, omitted or corrupt inputs and captured_source_quality.
Explain reviewed and unreviewed conditions, driver/recovery exclusions, parameter
evidence and uncertainty. Keep objective-policy scores separate from vehicle
tracking errors. Stored settings, consumer observations and causal improvement
are different claims. No vehicle qualification or completed Codex review is implied.

Any separate review result should include schema_version,
bundle_manifest_hash (SHA-256 of review.json bytes), base_summary_sha256
(the declared summary.json hash), and review_revision. Review-result import is
separate from pack generation. Run `review-template PATH [--output review.json]`
to generate a source-independent JSON draft with the exact bundle_manifest_hash
and summary binding. The draft deliberately cannot be imported. Complete the
reviewer tool/model and timezone-aware reviewed_at, check every referenced file,
record actual reviewed checks and remaining omissions, then set document_state
to final. Never treat filling fields as a completed review. Revisions must name
the prior stored review hash. Unknown versions stay unknown unless the pack has
direct supporting metadata; never insert the current machine's code version.
Machine comparison reads result JSON metadata only up to 512 KiB per result,
sequentially within the pack's total byte/file/time budgets. Oversized or absent
metadata and mixed version values remain unknown; the current source tree never
fills those gaps. A version is comparable only when direct text metadata has one
matching value across the parsed results. Hash maps are not synthesized versions.
Drafts must fit the review document's 128 KiB and 128-scope budgets; generation
fails explicitly when the complete inventory cannot fit, never silently truncates.
Never upload private data without separate authorization.
"""


def _json(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _object(data):
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError("review_duplicate_key")
      result[key] = value
    return result
  try:
    value = json.loads(data, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("review_nonfinite")))
    if type(value) is not dict:
      raise ValueError("review_object_required")
    return value
  except (RecursionError, UnicodeError) as error:
    raise ValueError("review_metadata_invalid") from error


def _identifier(value):
  if not isinstance(value, str) or value in {".", ".."} or _ID.fullmatch(value) is None:
    raise ValueError("review_identifier_invalid")
  return value


def _relative(value):
  if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value or ":" in value:
    raise ValueError("review_path_invalid")
  path = PurePosixPath(value)
  devices = {"CON", "PRN", "AUX", "NUL", *(f"COM{index}" for index in range(1, 10)), *(f"LPT{index}" for index in range(1, 10))}
  if path.is_absolute() or str(path) != value or any(ord(char) < 32 for char in value) or any(
      part in {".", ".."} or part.rstrip(" .") != part or part.split(".")[0].upper() in devices for part in path.parts):
    raise ValueError("review_path_invalid")
  return path


def _fs_path(value):
  """Use Win32 extended spelling for I/O; exported relative names stay portable."""
  path = Path(value)
  if os.name != "nt":
    return path
  absolute = os.path.abspath(path)
  if absolute.startswith("\\\\?\\"):
    return Path(absolute)
  return Path("\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute)


def _plain_path(value):
  spelling = os.fspath(value)
  if os.name == "nt" and spelling.startswith("\\\\?\\"):
    spelling = "\\\\" + spelling[8:] if spelling.startswith("\\\\?\\UNC\\") else spelling[4:]
  return Path(spelling)


def _directory(value):
  path = Path(value)
  if not path.is_absolute():
    raise ValueError("review_root_invalid")
  path = _fs_path(path)
  if _has_link(path) or not path.is_dir():
    raise ValueError("review_root_invalid")
  return _fs_path(path.resolve())


def _has_link(path):
  path = _fs_path(path)
  return any(part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400)
             for part in (path, *path.parents))


class _Limits:
  def __init__(self, budget, cancelled):
    if budget is not None and type(budget) is not dict:
      raise ValueError("review_budget_invalid")
    self.values = {**DEFAULT_BUDGET, **(budget or {})}
    if set(self.values) != set(DEFAULT_BUDGET):
      raise ValueError("review_budget_invalid")
    for key, upper in (("max_bytes", 1024 * 1024 * 1024), ("max_files", 1024), ("timeout_seconds", 600)):
      if type(self.values[key]) is not int or not 0 < self.values[key] <= upper:
        raise ValueError("review_budget_invalid")
    self.deadline, self.cancelled = time.monotonic() + self.values["timeout_seconds"], cancelled

  def check(self):
    if self.cancelled is not None and self.cancelled():
      raise InterruptedError("review_cancelled")
    if time.monotonic() > self.deadline:
      raise TimeoutError("review_timeout")


def _entry(value):
  if type(value) is not dict or set(value) - ENTRY_FIELDS - {"copy_mode"} or not ENTRY_FIELDS <= set(value):
    raise ValueError("review_entry_invalid")
  _identifier(value["root_id"])
  _relative(value["relative_path"])
  destination = str(_relative(value["export_relative_path"]))
  if destination in {"review.json", "summary.json", "REVIEW_README.md", "comparison.csv", "comparison.svg", "calibration.csv", "calibration.svg", "analysis.json"}:
    raise ValueError("review_reserved_path")
  if not isinstance(value["sha256"], str) or _SHA.fullmatch(value["sha256"]) is None or type(value["bytes"]) is not int or value["bytes"] < 0:
    raise ValueError("review_entry_digest_invalid")
  _identifier(value["role"])
  if value["sensitivity"] not in {"summary", "private_context", "private_raw"}:
    raise ValueError("review_sensitivity_invalid")
  if value["role"] in {"raw_log", "raw_rlog"} and value["sensitivity"] != "private_raw":
    raise ValueError("review_raw_classification_invalid")
  mode = value.get("copy_mode", "whole")
  if mode not in {"whole", "prefix"} or (mode == "prefix" and value["role"] not in {"settings_history", "settings_segment", "request_ledger"}):
    raise ValueError("review_copy_mode_invalid")
  return {**value, "copy_mode": mode}


def _selection(value, limits):
  if type(value) is not dict or set(value) != SELECTION_FIELDS or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["historical_only"] is not True:
    raise ValueError("review_selection_invalid")
  for key in ("summary_id", "session_id", "source_kind"):
    if key != "session_id" or value[key] is not None:
      _identifier(value[key])
  if type(value["summary_revision"]) is not int or value["summary_revision"] < 1:
    raise ValueError("review_summary_revision_invalid")
  if type(value["summary"]) is not dict or type(value["source_quality"]) is not dict or type(value["expected_inventory"]) is not list or type(value["checkpoints"]) is not list:
    raise ValueError("review_selection_metadata_invalid")
  has_graph = any(isinstance(row.get("comparison"), dict) for row in value["summary"].get("requests", {}).get("items", [])[:16])
  generated_count = 5 if has_graph else 3
  if value["summary"].get("candidate", {}).get("calibration_artifact_sha256") is not None:
    generated_count += 2
  if type(value["files"]) is not list or len(value["files"]) + generated_count > limits.values["max_files"] or len(value["checkpoints"]) > 32:
    raise ValueError("review_file_limit")
  # Metadata is bounded before copying or retaining the frozen selection.
  if len(_json(value)) > 2 * 1024 * 1024:
    raise ValueError("review_metadata_limit")
  entries = [_entry(entry) for entry in value["files"]]
  paths = [entry["export_relative_path"].casefold() for entry in entries]
  if len(paths) != len(set(paths)):
    raise ValueError("review_duplicate_path")
  return deepcopy(value), entries


def _open_source(path):
  if _has_link(path):
    raise ValueError("review_link_not_allowed")
  path = _regular_file(path)
  initial = path.stat()
  fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
  opened = os.fstat(fd)
  if not stat.S_ISREG(opened.st_mode) or (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino):
    os.close(fd)
    raise ValueError("review_source_replaced")
  return path, fd, opened


def _source_digest(path, entry, limits, target=None):
  path, fd, before = _open_source(path)
  size, digest = 0, hashlib.sha256()
  prefix = entry["copy_mode"] == "prefix"
  try:
    if before.st_size < entry["bytes"] or (not prefix and before.st_size != entry["bytes"]):
      raise ValueError("review_source_size_changed")
    while size < entry["bytes"]:
      limits.check()
      block = os.read(fd, min(CHUNK, entry["bytes"] - size))
      if not block:
        raise ValueError("review_source_truncated")
      digest.update(block)
      size += len(block)
      if target is not None and target.write(block) != len(block):
        raise OSError("review_short_write")
    after = os.fstat(fd)
  finally:
    os.close(fd)
  current = _regular_file(path).stat()
  if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino) or current.st_size < size or (not prefix and
      (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)) or (not prefix and
      (before.st_size, before.st_mtime_ns) != (current.st_size, current.st_mtime_ns)):
    raise ValueError("review_source_changed")
  if digest.hexdigest() != entry["sha256"]:
    raise ValueError("review_source_hash_changed")
  return digest.hexdigest()


def _unchecked_digest(path, size, limits):
  digest, total = hashlib.sha256(), 0
  with _regular_file(path).open("rb") as stream:
    while True:
      limits.check()
      block = stream.read(min(CHUNK, size - total + 1))
      if not block:
        break
      total += len(block)
      if total > size:
        raise ValueError("review_file_size_changed")
      digest.update(block)
  if total != size:
    raise ValueError("review_file_size_changed")
  return digest.hexdigest()


def _checkpoint_shape(checkpoints, entries):
  by_path = {entry["path"]: entry for entry in entries}
  seen = set()
  for checkpoint in checkpoints:
    if type(checkpoint) is not dict or set(checkpoint) != {"kind", "path", "token"} or checkpoint["kind"] not in {"settings", "segmented_settings", "requests"}:
      raise ValueError("review_checkpoint_invalid")
    path = str(_relative(checkpoint["path"]))
    if path in seen:
      raise ValueError("review_checkpoint_duplicate")
    seen.add(path)
    entry = by_path.get(path)
    if entry is None or entry["role"] != {"settings": "settings_history", "segmented_settings": "settings_history", "requests": "request_ledger"}[checkpoint["kind"]]:
      raise ValueError("review_checkpoint_file_missing")
    if type(checkpoint["token"]) is not dict:
      raise ValueError("review_checkpoint_size_mismatch")
    if checkpoint["kind"] == "segmented_settings":
      if checkpoint["token"].get("schema_version") != 2 or PurePosixPath(path).name != "snapshot.json":
        raise ValueError("review_segmented_checkpoint_invalid")
    elif checkpoint["token"].get("bytes") != entry["bytes"]:
      raise ValueError("review_checkpoint_size_mismatch")
  if any(entry["role"] in {"settings_history", "request_ledger"} and entry["path"] not in seen for entry in entries):
    raise ValueError("review_checkpoint_missing")


def _segmented_members(manifest, checkpoint, marker):
  base = PurePosixPath(checkpoint["path"]).parent
  by_path = {entry["path"]: entry for entry in manifest["files"]}
  rows = marker.get("history_files") if type(marker) is dict else None
  if type(rows) is not list or len(rows) > 256:
    raise ValueError("review_segmented_inventory_invalid")
  members, total = {checkpoint["path"], str(base / "recording.json")}, 0
  for row in rows:
    if (type(row) is not dict or set(row) != {"path", "bytes", "sha256", "copy_mode"} or row["copy_mode"] != "prefix"
        or type(row["bytes"]) is not int or row["bytes"] < 0 or type(row["sha256"]) is not str or _SHA.fullmatch(row["sha256"]) is None):
      raise ValueError("review_segmented_inventory_invalid")
    relative = _relative(row["path"])
    path = str(base / relative)
    entry = by_path.get(path)
    if path in members or entry is None or entry["role"] != "settings_segment" or any(entry[key] != row[key] for key in ("bytes", "sha256")):
      raise ValueError("review_segmented_inventory_mismatch")
    members.add(path)
    total += row["bytes"]
  limit = 512 * 1024 * 1024 if marker.get("schema_version") == 3 else SEGMENTED_HISTORY_BYTES
  if total > limit or any(path not in by_path for path in members):
    raise ValueError("review_segmented_byte_limit")
  pid_prefix = str(base / "pid_bursts") + "/"
  for entry in manifest["files"]:
    if entry["role"] == "pid_cycle_evidence" and entry["path"].startswith(pid_prefix):
      members.add(entry["path"])
  if any(by_path[path]["bytes"] > 64 * 1024 for path in (checkpoint["path"], str(base / "recording.json"))):
    raise ValueError("review_segmented_metadata_limit")
  return members


def _verify_checkpoint_files(root, manifest, limits):
  covered = set()
  for checkpoint in manifest["checkpoints"]:
    limits.check()
    path = root / checkpoint["path"]
    if checkpoint["kind"] == "segmented_settings":
      from .journal_snapshot import load_journal_snapshot
      parent = path.parent
      sealed = load_journal_snapshot(parent, parent)
      complete = sealed["history"].get("snapshot_prefix_complete") is True if sealed["marker"].get("schema_version") == 3 else sealed["history"].get("selected_closure_complete") is True
      if sealed["marker"]["checkpoint"] != checkpoint["token"] or not complete:
        raise ValueError("review_segmented_checkpoint_mismatch")
      if checkpoint["token"].get("session_id") != manifest["session_id"]:
        raise ValueError("review_settings_session_mismatch")
      covered.update(_segmented_members(manifest, checkpoint, sealed["marker"]))
    elif checkpoint["kind"] == "settings":
      from .settings_history import load_history
      token = checkpoint["token"]
      if token.get("session_id") != manifest["session_id"]:
        raise ValueError("review_settings_session_mismatch")
      history = load_history(path, checkpoint=token, max_bytes=max(1, token["bytes"]), max_events=10_000)
      if history["state"] != "complete" or history.get("snapshot_prefix_complete") is not True:
        raise ValueError("review_settings_prefix_invalid")
    else:
      from .apply_requests import verify_request_prefix
      result = verify_request_prefix(path, checkpoint["token"])
      if result["state"] != "verified":
        raise ValueError("review_request_prefix_invalid")
  if any(entry["role"] == "settings_segment" and entry["path"] not in covered for entry in manifest["files"]):
    raise ValueError("review_segmented_checkpoint_missing")


def _manifest(selection, entries, omitted, include_private_raw):
  return {"schema_version": 1, "kind": "control_advisor_review", "state": "complete",
          **{key: selection[key] for key in ("summary_id", "summary_revision", "session_id", "source_kind", "historical_only")},
          "current_recommendation": False, "apply_enabled": False, "device_qualified": False,
          "include_private_raw": include_private_raw, "files": entries, "omitted_files": omitted,
          "checkpoints": selection["checkpoints"], "captured_source_quality": selection["source_quality"],
          "expected_inventory": selection["expected_inventory"], "privacy": {"private_context": True, "raw_may_include_location_and_secrets": True,
          "external_upload_performed": False}, "power_loss_durability": "file_fsync_only" if os.name == "nt" else "file_and_directory_fsync"}


def _validate_manifest(value, limits):
  required = {"schema_version", "kind", "state", "summary_id", "summary_revision", "session_id", "source_kind", "historical_only",
              "current_recommendation", "apply_enabled", "device_qualified", "include_private_raw", "files", "omitted_files", "checkpoints",
              "captured_source_quality", "expected_inventory", "privacy", "power_loss_durability"}
  if type(value) is not dict or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "control_advisor_review" or value["state"] != "complete":
    raise ValueError("review_manifest_invalid")
  if value["historical_only"] is not True or any(value[key] is not False for key in ("current_recommendation", "apply_enabled", "device_qualified")) or type(value["include_private_raw"]) is not bool:
    raise ValueError("review_qualification_invalid")
  for key in ("summary_id", "session_id", "source_kind"):
    if key != "session_id" or value[key] is not None:
      _identifier(value[key])
  if type(value["summary_revision"]) is not int or value["summary_revision"] < 1 or type(value["captured_source_quality"]) is not dict or type(value["expected_inventory"]) is not list or type(value["omitted_files"]) is not list:
    raise ValueError("review_manifest_metadata_invalid")
  if value["privacy"] != {"private_context": True, "raw_may_include_location_and_secrets": True, "external_upload_performed": False} or value["power_loss_durability"] not in {"file_fsync_only", "file_and_directory_fsync"}:
    raise ValueError("review_privacy_metadata_invalid")
  if type(value["files"]) is not list or not 1 <= len(value["files"]) < limits.values["max_files"]:
    raise ValueError("review_file_limit")
  names, total = set(), 0
  for entry in value["files"]:
    if type(entry) is not dict or set(entry) != {"path", "sha256", "bytes", "role", "sensitivity"}:
      raise ValueError("review_manifest_entry_invalid")
    name = str(_relative(entry["path"]))
    if name.casefold() in names or name == "review.json" or type(entry["bytes"]) is not int or entry["bytes"] < 0 or not isinstance(entry["sha256"], str) or _SHA.fullmatch(entry["sha256"]) is None:
      raise ValueError("review_manifest_entry_invalid")
    names.add(name.casefold())
    total += entry["bytes"]
    _identifier(entry["role"])
    if entry["sensitivity"] not in {"summary", "private_context", "private_raw"} or (entry["sensitivity"] == "private_raw" and not value["include_private_raw"]):
      raise ValueError("review_raw_opt_in_missing")
    if entry["role"] in {"raw_log", "raw_rlog"} and entry["sensitivity"] != "private_raw":
      raise ValueError("review_raw_classification_invalid")
  if total > limits.values["max_bytes"]:
    raise ValueError("review_byte_limit")
  if type(value["checkpoints"]) is not list or len(value["checkpoints"]) > 32:
    raise ValueError("review_checkpoint_invalid")
  _checkpoint_shape(value["checkpoints"], value["files"])
  if sum(entry["path"] == "summary.json" and entry["role"] == "summary" for entry in value["files"]) != 1:
    raise ValueError("review_summary_missing")
  return value


def _read_bounded(path, limit):
  path = _regular_file(path)
  if path.stat().st_size > limit:
    raise ValueError("review_metadata_limit")
  with path.open("rb") as stream:
    data = stream.read(limit + 1)
  if len(data) > limit:
    raise ValueError("review_metadata_limit")
  return data


def _zip_central_bound(path, limits):
  # Bound ZipFile's metadata allocation before it materializes every ZipInfo.
  with path.open("rb") as stream:
    size = stream.seek(0, os.SEEK_END)
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
  position = tail.rfind(b"PK\x05\x06")
  if position < 0 or len(tail) - position < 22:
    raise ValueError("review_zip_footer_invalid")
  _, disk, start_disk, disk_entries, entries, central_size, central_offset, comment = struct.unpack_from("<4s4H2LH", tail, position)
  if disk or start_disk or entries != disk_entries or entries > limits.values["max_files"] or central_size > limits.values["max_files"] * 1024 or central_offset + central_size > size or position + 22 + comment != len(tail):
    raise ValueError("review_zip_metadata_limit")


def _directory_inventory(root, max_files, limits):
  names, count, pending = set(), 0, [root]
  while pending:
    with os.scandir(pending.pop()) as entries:
      for entry in entries:
        limits.check()
        count += 1
        if count > max_files * 4:
          raise ValueError("review_file_limit")
        path = Path(entry.path)
        if _has_link(path):
          raise ValueError("review_link_not_allowed")
        if entry.is_dir(follow_symlinks=False):
          pending.append(path)
        else:
          names.add(path.relative_to(root).as_posix())
          if len(names) > max_files:
            raise ValueError("review_file_limit")
  return names


def _comparison_graph_files(summary):
  """Render frozen, matched metrics only; never label a change causal."""
  rows = []
  for item in summary.get("requests", {}).get("items", [])[:16]:
    comparison = item.get("comparison")
    if not isinstance(comparison, dict):
      continue
    before, after = comparison.get("before_rmse"), comparison.get("after_rmse")
    numeric = lambda value: type(value) in (int, float) and math.isfinite(value) and value >= 0
    valid = comparison.get("state") == "comparison_ready" and numeric(before) and numeric(after)
    rows.append({"request": str(item.get("request_id", ""))[:128], "key": str(item.get("item_key", ""))[:128],
      "state": str(comparison.get("state", "unknown"))[:64], "before": before if valid else None,
      "after": after if valid else None, "source": str(comparison.get("export_source_state", "unverified"))[:64]})
  if not rows:
    return []
  output = io.StringIO(newline="")
  writer = csv.writer(output, lineterminator="\n")
  writer.writerow(["request_id", "parameter", "state", "before_rmse", "after_rmse", "after_minus_before", "source_state", "causal_improvement_proven"])
  safe = lambda value: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
  for row in rows:
    writer.writerow([safe(row["request"]), safe(row["key"]), safe(row["state"]), row["before"], row["after"],
      row["after"] - row["before"] if row["before"] is not None else None, safe(row["source"]), "false"])
  height = 100 + len(rows) * 100
  svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" viewBox="0 0 900 {height}" role="img">',
    '<title>Matched RMSE before and after manual parameter application</title>',
    '<rect width="100%" height="100%" fill="white"/>',
    '<g font-family="sans-serif" font-size="15" fill="#111">',
    '<text x="20" y="28">Matched RMSE: blue = before, orange = after. Each row has its own scale.</text>',
    '<text x="20" y="52">Observational comparison; driving conditions and source verification must be checked.</text>']
  for index, row in enumerate(rows):
    y = 95 + index * 100
    label = html.escape(f'{row["key"]} / {row["request"]}')
    svg.append(f'<text x="20" y="{y}">{label}</text>')
    if row["before"] is None:
      svg.append(f'<text x="30" y="{y + 28}">Comparison unavailable: {html.escape(row["state"])}</text>')
      continue
    scale = max(row["before"], row["after"], 1e-12)
    for offset, value, color in ((12, row["before"], "#2463ac"), (36, row["after"], "#d05e14")):
      width = 570 * value / scale
      svg.extend([f'<rect x="30" y="{y + offset}" width="{width:.3f}" height="18" fill="{color}"/>',
        f'<text x="620" y="{y + offset + 15}">{value:.8g} (source: {html.escape(row["source"])})</text>'])
  svg.append('</g></svg>')
  return [("comparison.csv", output.getvalue().encode("utf-8"), "comparison_metrics"),
          ("comparison.svg", "\n".join(svg).encode("utf-8"), "comparison_graph")]


def _calibration_graph_files(payload, entries, artifact):
  """Use the same immutable time series shown by WebCarrot, including held data."""
  if artifact is None:
    return []
  if type(artifact) is not str or _SHA.fullmatch(artifact) is None:
    raise ValueError("calibration_export_binding_invalid")
  matches = [entry for entry in entries if entry["sha256"] == artifact and entry["path"].endswith("/result.json")]
  if len(matches) != 1:
    raise ValueError("calibration_export_source_missing")
  from .candidate_report import _read, MAX_RESULT_BYTES
  from .calibration_review import validate_calibration_review
  result, actual_hash = _read(payload / matches[0]["path"], MAX_RESULT_BYTES)
  if actual_hash != artifact:
    raise ValueError("calibration_export_source_changed")
  review = validate_calibration_review(result.get("calibration_review"))
  output = io.StringIO(newline="")
  writer = csv.writer(output, lineterminator="\n")
  columns = ["mono_ns", "target", "actual", "target_mono_ns", "actual_mono_ns", "independent_actual", "independent_mono_ns",
             "sent_request", "sent_mono_ns", "plot_segment", "sent_raw", "stop_req", "jerk_upper", "jerk_lower"]
  writer.writerow(["parameter", "purpose", "state", "current_raw", "axis", "response_unit", "sent_unit",
                   "actual_source", "independent_source", "sent_source", *columns])
  safe = lambda value: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
  panels = []
  for axis in ("lateral", "longitudinal"):
    source = review["series"][axis]
    unit = "normalized torque" if axis == "lateral" else "m/s^2"
    metadata = [review["parameter_key"], review["purpose"], review["state"],
                review["baseline"]["settings_raw"][review["parameter_key"]], axis, "m/s^2", unit,
                source["actual_source"], source["independent_source"], source["sent_source"]]
    for point in source["points"]:
      writer.writerow([*[safe(value) for value in metadata], *point])
    panels.append((axis + " response (m/s^2)", source["points"], [(1, "#2463ac"), (2, "#d05e14"), (5, "#16803c")]))
    panels.append((axis + " output request (" + unit + ")", source["points"],
                   [(7, "#7e42bb")] + ([(10, "#bf3977")] if axis == "longitudinal" else [])))
  svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1080" viewBox="0 0 1000 1080" role="img">',
         '<title>Frozen calibration time series</title><rect width="100%" height="100%" fill="white"/>',
         '<g font-family="sans-serif" font-size="14" fill="#111">',
         f'<text x="20" y="26">{html.escape(review["parameter_key"])} / {html.escape(review["purpose"])} / {html.escape(review["state"])}</text>',
         '<text x="20" y="49">Blue: target. Orange: controller observation. Green: independent sensor response.</text>',
         '<text x="20" y="71">Purple: sent request. Pink: raw SCC request. Grey points: excluded observations. Gaps are not joined.</text>',
         '<text x="20" y="93">Observed data only; neither optimal settings nor causal improvement are asserted.</text>']
  for index, (title, points, traces) in enumerate(panels):
    top = 126 + index * 232
    svg.append(f'<text x="20" y="{top}">{html.escape(title)}</text>')
    values = [point[column] for point in points for column, _ in traces
              if type(point[column]) in (int, float) and math.isfinite(point[column])]
    if not values or not points:
      svg.append(f'<text x="30" y="{top + 40}">Signal unavailable; inspect the frozen reasons and exclusions.</text>')
      continue
    low, high = min(values), max(values)
    span = max(high - low, 1e-6)
    first, last = int(points[0][0]), int(points[-1][0])
    duration = max(1, last - first)
    for column, color in traces:
      run, segment, excluded = [], None, []
      def flush():
        if len(run) > 1:
          svg.append(f'<polyline points="{" ".join(run)}" fill="none" stroke="{color}" stroke-width="1.5"/>')
        run.clear()
      for point in points:
        value = point[column]
        if type(value) not in (int, float) or not math.isfinite(value):
          flush(); segment = None; continue
        x = 65 + 905 * (int(point[0]) - first) / duration
        y = top + 170 - 140 * (value - low) / span
        if point[9] is None:
          flush(); segment = None; excluded.append(f'M{x:.2f},{y:.2f}h.1'); continue
        if segment != point[9]:
          flush(); segment = point[9]
        run.append(f'{x:.2f},{y:.2f}')
      flush()
      if excluded:
        svg.append(f'<path d="{" ".join(excluded)}" fill="none" stroke="#94a3b8" stroke-width="2.5" stroke-linecap="round"/>')
    svg.extend([f'<text x="10" y="{top + 35}">{high:.5g}</text>', f'<text x="10" y="{top + 173}">{low:.5g}</text>',
                f'<text x="65" y="{top + 198}">0 s</text>', f'<text x="830" y="{top + 198}">{(last-first)/1e9:.3f} s</text>'])
  svg.append('</g></svg>')
  files = [("calibration.csv", output.getvalue().encode("utf8"), "calibration_timeseries"),
           ("calibration.svg", "\n".join(svg).encode("utf8"), "calibration_graph")]
  if review.get("diagnostics") is not None:
    analysis = {"schema_version": 1, "artifact_sha256": artifact, "parameter_key": review["parameter_key"],
                "diagnostics": review["diagnostics"], "numeric_inference": {
                  row["key"]: row["provenance"]["inference"] for row in result["report"]["items"]
                  if row.get("provenance", {}).get("inference") is not None}}
    files.append(("analysis.json", json.dumps(analysis, ensure_ascii=False, allow_nan=False, indent=2).encode("utf8"), "calibration_analysis"))
  return files


class ReviewPackBuilder:
  def __init__(self, roots, *, export_root):
    self._export_io = _directory(export_root)
    self.export_root = _plain_path(self._export_io)
    if type(roots) is not dict or len(roots) > 8:
      raise ValueError("review_roots_invalid")
    self.roots = {_identifier(key): _directory(value) for key, value in roots.items()}
    paths = [self._export_io, *self.roots.values()]
    if any(first == second or first in second.parents or second in first.parents for index, first in enumerate(paths) for second in paths[index + 1:]):
      raise ValueError("review_roots_overlap")

  def create_review_pack(self, destination, selection, *, include_private_raw=False, budget=None, cancelled=None):
    limits = _Limits(budget, cancelled)
    if type(include_private_raw) is not bool:
      raise ValueError("review_raw_opt_in_invalid")
    selection, sources = _selection(selection, limits)
    destination = Path(destination)
    if not destination.is_absolute():
      raise ValueError("review_destination_invalid")
    parent = _directory(destination.parent)
    destination = parent / destination.name
    if (parent != self._export_io and parent.parent != self._export_io) or destination.exists() or destination.is_symlink():
      raise ValueError("review_destination_invalid")
    _identifier(destination.name)
    limits.check()
    destination.mkdir(exist_ok=False)
    payload = destination / "payload"
    payload.mkdir()
    entries, omitted, total = [], [], 0
    for source in sources:
      limits.check()
      if source["root_id"] not in self.roots:
        raise ValueError("review_source_root_unknown")
      public = {"path": source["export_relative_path"], **{key: source[key] for key in ("sha256", "bytes", "role", "sensitivity")}}
      if source["sensitivity"] == "private_raw" and not include_private_raw:
        omitted.append({**public, "reason": "excluded_by_policy"})
        continue
      total += source["bytes"]
      if total * 2 > limits.values["max_bytes"]:
        raise ValueError("review_byte_limit")
      root = self.roots[source["root_id"]]
      path = root.joinpath(*_relative(source["relative_path"]).parts)
      target = payload.joinpath(*_relative(source["export_relative_path"]).parts)
      target.parent.mkdir(parents=True, exist_ok=True)
      with target.with_name(target.name + ".partial").open("xb") as outgoing:
        _source_digest(path, source, limits, outgoing)
        outgoing.flush()
        os.fsync(outgoing.fileno())
      partial = target.with_name(target.name + ".partial")
      if _unchecked_digest(partial, source["bytes"], limits) != source["sha256"]:
        raise ValueError("review_copy_hash_mismatch")
      _source_digest(path, source, limits)
      os.replace(partial, target)
      entries.append(public)
    generated = [("summary.json", _json(selection["summary"]) + b"\n", "summary"),
                 ("REVIEW_README.md", REVIEW_README.encode(), "review_instructions")]
    generated.extend(_comparison_graph_files(selection["summary"]))
    generated.extend(_calibration_graph_files(payload, entries, selection["summary"].get("candidate", {}).get("calibration_artifact_sha256")))
    for name, data, role in generated:
      total += len(data)
      if total * 2 > limits.values["max_bytes"]:
        raise ValueError("review_byte_limit")
      with (payload / name).open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
      entries.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "role": role, "sensitivity": "summary"})
    manifest = _validate_manifest(_manifest(selection, entries, omitted, include_private_raw), limits)
    _verify_checkpoint_files(payload, manifest, limits)
    manifest_data = _json(manifest) + b"\n"
    if len(manifest_data) > 2 * 1024 * 1024:
      raise ValueError("review_manifest_limit")
    total += len(manifest_data)
    zip_overhead = sum(200 + 2 * len(entry["path"].encode()) for entry in entries) + 4096
    if total * 2 + zip_overhead > limits.values["max_bytes"]:
      raise ValueError("review_byte_limit")
    with (payload / "review.json").open("xb") as stream:
      stream.write(manifest_data)
      stream.flush()
      os.fsync(stream.fileno())
    zip_path = destination / "review.zip"
    with zipfile.ZipFile(destination / "review.zip.partial", "x", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
      for name in ["review.json", *(entry["path"] for entry in entries)]:
        limits.check()
        info = zipfile.ZipInfo(name)
        info.compress_type, info.external_attr = zipfile.ZIP_STORED, (stat.S_IFREG | 0o600) << 16
        with (payload / name).open("rb") as incoming, archive.open(info, "w") as outgoing:
          while True:
            limits.check()
            block = incoming.read(CHUNK)
            if not block:
              break
            outgoing.write(block)
        if total + (destination / "review.zip.partial").stat().st_size > limits.values["max_bytes"]:
          raise ValueError("review_byte_limit")
    temporary = destination / "review.zip.partial"
    zip_size = temporary.stat().st_size
    if total + zip_size + 4096 > limits.values["max_bytes"]:
      raise ValueError("review_byte_limit")
    with temporary.open("r+b") as stream:
      os.fsync(stream.fileno())
    zip_digest = _unchecked_digest(temporary, zip_size, limits)
    limits.check()
    os.replace(temporary, zip_path)
    _sync_directory(payload)
    _sync_directory(destination)
    result = {"schema_version": 1, "state": "complete", "summary_id": selection["summary_id"], "summary_revision": selection["summary_revision"],
              "session_id": selection["session_id"], "historical_only": True, "include_private_raw": include_private_raw, "files": len(entries),
              "bytes": total + zip_size, "zip_bytes": zip_size, "zip_sha256": zip_digest, "manifest_sha256": hashlib.sha256(manifest_data).hexdigest()}
    # Commit boundary: cancellation is checked before this final bounded marker.
    try:
      _write_json(destination / "complete.json", result)
      _sync_directory(destination)
    except BaseException:
      (destination / "complete.json").unlink(missing_ok=True)
      raise
    return {**result, "download_path": _plain_path(zip_path)}


def _capture_versions(capture, entry, data):
  try:
    metadata = _object(data)
  except ValueError:
    return
  declared = metadata.get("versions")
  declared = declared if type(declared) is dict else {}
  source = metadata.get("source")
  source = source if type(source) is dict else {}
  for name in ("code", "dbc", "analyzer"):
    candidates = [declared.get(name), metadata.get(name + "_version"), metadata.get(name + "_sha256")]
    if name == "code":
      candidates.extend((metadata.get("declared_code_commit"), source.get("declared_code_commit")))
    for value in candidates:
      if type(value) is str and value.strip() and len(value) <= 256 and not any(ord(char) < 32 for char in value):
        by_value = capture.setdefault("versions", {}).setdefault(name, {})
        by_value.setdefault(value, set()).add(entry["path"])


def _verify_review_pack(directory, limits, capture=None):
  """Verify local directory or ZIP bytes without consulting original roots."""
  limits.check()
  path = _fs_path(directory)
  if path.is_dir():
    root = _directory(path)
    root_names = set()
    with os.scandir(root) as entries:
      for entry in entries:
        root_names.add(entry.name)
        if len(root_names) > 3:
          raise ValueError("review_directory_inventory_mismatch")
    if root_names != {"payload", "review.zip", "complete.json"}:
      raise ValueError("review_directory_inventory_mismatch")
    marker = _object(_read_bounded(root / "complete.json", 16 * 1024))
    payload = _directory(root / "payload")
    raw = _read_bounded(payload / "review.json", 2 * 1024 * 1024)
    if len(raw) > 2 * 1024 * 1024 or marker.get("manifest_sha256") != hashlib.sha256(raw).hexdigest():
      raise ValueError("review_manifest_hash_mismatch")
    manifest = _validate_manifest(_object(raw), limits)
    if _directory_inventory(payload, limits.values["max_files"], limits) != {"review.json", *(entry["path"] for entry in manifest["files"])}:
      raise ValueError("review_directory_inventory_mismatch")
    for entry in manifest["files"]:
      limits.check()
      if _unchecked_digest(payload / entry["path"], entry["bytes"], limits) != entry["sha256"]:
        raise ValueError("review_file_hash_mismatch")
    _verify_checkpoint_files(payload, manifest, limits)
    zipped = root / "review.zip"
    zipped_result = _verify_review_pack(zipped, limits, capture)
    size, zip_digest = zipped_result["zip_bytes"], zipped_result["zip_sha256"]
    if size != marker.get("zip_bytes") or zip_digest != marker.get("zip_sha256"):
      raise ValueError("review_zip_hash_mismatch")
    if zipped_result["manifest_sha256"] != hashlib.sha256(raw).hexdigest():
      raise ValueError("review_zip_manifest_mismatch")
    actual_bytes = sum(entry["bytes"] for entry in manifest["files"]) + len(raw) + size
    if actual_bytes + (root / "complete.json").stat().st_size > limits.values["max_bytes"]:
      raise ValueError("review_byte_limit")
    if marker.get("state") != "complete" or any(marker.get(key) != manifest[key] for key in ("summary_id", "summary_revision", "session_id", "historical_only", "include_private_raw")):
      raise ValueError("review_completion_identity_mismatch")
  else:
    path = _regular_file(path)
    if path.stat().st_size > limits.values["max_bytes"]:
      raise ValueError("review_byte_limit")
    _zip_central_bound(path, limits)
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix="advisor-review-verify-") as temporary:
      infos = archive.infolist()
      if len(infos) > limits.values["max_files"]:
        raise ValueError("review_file_limit")
      names, expanded = set(), 0
      for info in infos:
        name = str(_relative(info.filename))
        mode = info.external_attr >> 16
        if name.casefold() in names or info.orig_filename != info.filename or info.is_dir() or info.compress_type != zipfile.ZIP_STORED or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}) or info.flag_bits & 1 or info.file_size != info.compress_size:
          raise ValueError("review_zip_entry_invalid")
        names.add(name.casefold())
        expanded += info.file_size
      if expanded + path.stat().st_size > limits.values["max_bytes"]:
        raise ValueError("review_byte_limit")
      info = archive.getinfo("review.json")
      if info.file_size > 2 * 1024 * 1024:
        raise ValueError("review_manifest_limit")
      raw = archive.read(info)
      manifest = _validate_manifest(_object(raw), limits)
      expected = {"review.json", *(entry["path"] for entry in manifest["files"])}
      if {info.filename for info in infos} != expected:
        raise ValueError("review_zip_inventory_mismatch")
      prefix_paths = {checkpoint["path"]: checkpoint for checkpoint in manifest["checkpoints"] if checkpoint["kind"] != "segmented_settings"}
      for entry in manifest["files"]:
        digest, size = hashlib.sha256(), 0
        checkpoint_file = None
        metadata = None
        if capture is not None and entry["bytes"] <= 512 * 1024:
          from .review_results import is_result_entry
          if is_result_entry(entry):
            metadata = bytearray()
        if entry["path"] in prefix_paths:
          if entry["bytes"] > 16 * 1024 * 1024:
            raise ValueError("review_checkpoint_byte_limit")
          target = Path(temporary) / "checkpoint.jsonl"
          checkpoint_file = target.open("xb")
        try:
          with archive.open(entry["path"]) as incoming:
            while True:
              limits.check()
              block = incoming.read(CHUNK)
              if not block:
                break
              size += len(block)
              if size > entry["bytes"]:
                raise ValueError("review_byte_limit")
              digest.update(block)
              if metadata is not None:
                metadata.extend(block)
              if checkpoint_file is not None:
                checkpoint_file.write(block)
        finally:
          if checkpoint_file is not None:
            checkpoint_file.close()
        if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
          raise ValueError("review_file_hash_mismatch")
        if metadata is not None:
          _capture_versions(capture, entry, metadata)
        if entry["path"] in prefix_paths:
          _verify_checkpoint_files(Path(temporary), {**manifest, "files": [entry], "checkpoints": [{**prefix_paths[entry["path"]], "path": "checkpoint.jsonl"}]}, limits)
          target.unlink()
      _verify_zip_segmented(archive, manifest, limits, temporary)
    size = path.stat().st_size
    zip_digest = _unchecked_digest(path, size, limits)
    actual_bytes = expanded + size
  if capture is not None:
    capture["manifest"] = manifest
    capture["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
  return {"schema_version": 1, "state": "verified", "scope": "historical_review_integrity", "historical_only": True,
          "current_recommendation": False, "apply_enabled": False, "device_qualified": False,
          **{key: manifest[key] for key in ("summary_id", "summary_revision", "session_id", "source_kind", "include_private_raw")},
          "bytes": actual_bytes, "zip_bytes": size, "zip_sha256": zip_digest, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
          "files": len(manifest["files"]), "omitted_files": manifest["omitted_files"],
          "captured_source_quality": manifest["captured_source_quality"], "expected_inventory": manifest["expected_inventory"]}


def _verify_zip_segmented(archive, manifest, limits, temporary):
  entries = {entry["path"]: entry for entry in manifest["files"]}
  covered = set()
  for checkpoint in manifest["checkpoints"]:
    if checkpoint["kind"] != "segmented_settings":
      continue
    if entries[checkpoint["path"]]["bytes"] > 64 * 1024:
      raise ValueError("review_segmented_metadata_limit")
    with tempfile.TemporaryDirectory(dir=temporary) as directory:
      root = _fs_path(directory)
      def extract(path):
        limits.check()
        entry = entries[path]
        target = root.joinpath(*_relative(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        with archive.open(path) as source, target.open("xb") as output:
          while True:
            limits.check()
            block = source.read(CHUNK)
            if not block:
              break
            size += len(block)
            if size > entry["bytes"]:
              raise ValueError("review_byte_limit")
            digest.update(block)
            output.write(block)
        if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
          raise ValueError("review_file_hash_mismatch")
        return target
      anchor = extract(checkpoint["path"])
      marker = _object(_read_bounded(anchor, 64 * 1024))
      members = _segmented_members(manifest, checkpoint, marker)
      size_limit = 512 * 1024 * 1024 if marker.get("schema_version") == 3 else SEGMENTED_HISTORY_BYTES
      if sum(entries[path]["bytes"] for path in members) > size_limit + 32 * 1024 * 1024 + 128 * 1024:
        raise ValueError("review_segmented_byte_limit")
      for path in sorted(members - {checkpoint["path"]}):
        extract(path)
      _verify_checkpoint_files(root, {**manifest, "files": [entries[path] for path in members], "checkpoints": [checkpoint]}, limits)
      covered.update(members)
  if any(entry["role"] == "settings_segment" and entry["path"] not in covered for entry in manifest["files"]):
    raise ValueError("review_segmented_checkpoint_missing")


def _checked_review(directory, budget, cancelled, capture=None):
  try:
    if _has_link(Path(directory)):
      raise ValueError("review_link_not_allowed")
    return _verify_review_pack(directory, _Limits(budget, cancelled), capture)
  except (zipfile.BadZipFile, KeyError, TypeError, OverflowError, RecursionError) as error:
    raise ValueError("review_pack_invalid") from error


def verify_review_pack(directory, *, budget=None, cancelled=None):
  """Invalid containers fail through the CLI/server's ordinary ValueError path."""
  return _checked_review(directory, budget, cancelled)


def _review_binding(directory, budget, cancelled):
  capture = {}
  verified = _checked_review(directory, budget, cancelled, capture)
  manifest = capture["manifest"]
  files = deepcopy(manifest["files"])
  summaries = [entry for entry in files if entry["path"] == "summary.json" and entry["role"] == "summary"]
  if len(summaries) != 1:
    raise ValueError("review_summary_missing")
  assertions, evidence = {}, {}
  for name, values in capture.get("versions", {}).items():
    if len(values) == 1:
      value, paths = next(iter(values.items()))
      assertions[name], evidence[name] = value, sorted(paths)
  return {"manifest_sha256": capture["manifest_sha256"], "zip_sha256": verified["zip_sha256"], "base_summary_sha256": summaries[0]["sha256"],
          **{key: manifest[key] for key in ("summary_id", "summary_revision", "session_id", "source_kind")},
          "files": files, "version_assertions": assertions}, evidence


def review_binding(directory, *, budget=None, cancelled=None):
  """Return only inventory and exact content bindings from one bounded validation."""
  return _review_binding(directory, budget, cancelled)[0]


def review_template(directory, *, budget=None, cancelled=None):
  """Create an explicitly unfinished review; it grants no importing or apply rights."""
  from .review_results import MAX_DOCUMENT_BYTES, is_result_entry
  binding, evidence = _review_binding(directory, budget, cancelled)
  results = [{"path": entry["path"], "base_result_hash": entry["sha256"]} for entry in binding["files"] if is_result_entry(entry)]
  versions = {name: {"state": "declared" if name in binding["version_assertions"] else "unknown",
                     "value": binding["version_assertions"].get(name), "evidence_refs": evidence.get(name, [])}
              for name in ("code", "dbc", "analyzer")}
  versions["raw"] = [{"path": entry["path"], "sha256": entry["sha256"]} for entry in binding["files"]
                     if entry["sensitivity"] == "private_raw" or entry["role"] in {"raw_log", "raw_rlog"}]
  draft = {"schema_version": 1, "document_state": "draft", "bundle_manifest_hash": binding["manifest_sha256"],
           "base_summary_sha256": binding["base_summary_sha256"], "summary_id": binding["summary_id"],
           "summary_revision": binding["summary_revision"], "review_revision": 1, "previous_review_hash": None,
           "scope": "analysis_results" if results else "summary_only", "declared_reviewer": {"tool": "", "model": "", "reviewed_at": ""},
           "verdict": "needs_more_data", "base_results": results, "versions": versions, "reviewed_scopes": [],
           "unreviewed_scopes": [{"file": entry["path"], "reason": "미검토"} for entry in binding["files"]],
           "findings": [], "uncertainties": ["검토 전 초안"], "follow_up": []}
  if len(_json(draft)) + 1 > MAX_DOCUMENT_BYTES or len(results) > 128 or len(binding["files"]) > 128 or len(versions["raw"]) > 128:
    raise ValueError("review_template_limit")
  return draft
