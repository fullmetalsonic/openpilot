"""Stdlib-only segmented journal codec for control advisor history.

Persists settings history across bounded segment files with a seal catalog.
Maintains byte-for-byte row and hash chain compatibility with single-file journals.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from contextlib import contextmanager


ZERO_HASH = "0" * 64
MAX_LINE_BYTES = 64 * 1024
HARD_MAX_SEGMENT_BYTES = 8 * 1024 * 1024
HARD_MAX_SEGMENT_EVENTS = 10_000
HARD_MAX_CONSUMER_BYTES = 4 * 1024 * 1024
HARD_MAX_CONSUMER_EVENTS = 5_000


class StorageBudgetExhausted(OSError):
  """Raised when segment, catalog, or total storage budget / free reserve is exhausted."""


def _canonical(value):
  return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _is_link(path):
  p = Path(path)
  return p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())


def _sync_dir(path):
  if os.name == "nt":
    return
  fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


def _unique(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("duplicate_history_key")
    result[key] = value
  return result


def _check_safe_relative_path(base_dir: Path, rel_path: str, expected_pattern: str | None = None) -> Path:
  if not isinstance(rel_path, str) or not rel_path:
    raise ValueError("invalid_relative_path")
  if "\\" in rel_path or "\0" in rel_path:
    raise ValueError("invalid_path_separators")
  if expected_pattern is not None and not re.fullmatch(expected_pattern, rel_path):
    raise ValueError(f"unexpected_relative_path_format: {rel_path}")
  parts = rel_path.split("/")
  if any(part in ("", ".", "..") for part in parts):
    raise ValueError("path_traversal")
  current = base_dir
  for part in parts:
    current = current / part
    if _is_link(current):
      raise ValueError("symlink_in_path")
  return current


def validate_checkpoint(checkpoint):
  required = {
    "schema_version", "kind", "session_id", "sequence", "chain_sha256",
    "bytes", "cutoff_mono_ns", "consumer_budget_rejections", "durable",
    "catalog", "active",
  }
  if not isinstance(checkpoint, dict) or set(checkpoint) != required:
    raise ValueError("invalid_history_checkpoint")
  if type(checkpoint["schema_version"]) is not int or checkpoint["schema_version"] != 2:
    raise ValueError("invalid_history_checkpoint")
  if checkpoint["kind"] != "segmented_settings_history":
    raise ValueError("invalid_history_checkpoint")
  if not isinstance(checkpoint["session_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", checkpoint["session_id"]):
    raise ValueError("invalid_history_checkpoint")
  if type(checkpoint["durable"]) is not bool or checkpoint["durable"] is not True:
    raise ValueError("invalid_history_checkpoint")
  for key, minimum in (("sequence", 0), ("bytes", 0), ("cutoff_mono_ns", 0), ("consumer_budget_rejections", 0)):
    val = checkpoint[key]
    if type(val) is not int or type(val) is bool or not (minimum <= val <= 2**63 - 1):
      raise ValueError("invalid_history_checkpoint")
  if not isinstance(checkpoint["chain_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", checkpoint["chain_sha256"]):
    raise ValueError("invalid_history_checkpoint")

  cat = checkpoint["catalog"]
  cat_required = {"path", "entries", "bytes", "chain_sha256"}
  if not isinstance(cat, dict) or set(cat) != cat_required:
    raise ValueError("invalid_history_checkpoint")
  if cat["path"] != "history/catalog.jsonl":
    raise ValueError("invalid_history_checkpoint")
  for key in ("entries", "bytes"):
    val = cat[key]
    if type(val) is not int or type(val) is bool or not (0 <= val <= 2**63 - 1):
      raise ValueError("invalid_history_checkpoint")
  if not isinstance(cat["chain_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", cat["chain_sha256"]):
    raise ValueError("invalid_history_checkpoint")

  act = checkpoint["active"]
  if act is not None:
    act_required = {"index", "path", "first_sequence", "previous_sha256", "bytes", "content_sha256"}
    if not isinstance(act, dict) or set(act) != act_required:
      raise ValueError("invalid_history_checkpoint")
    if type(act["index"]) is not int or not (0 <= act["index"] <= 999999):
      raise ValueError("invalid_history_checkpoint")
    if act["path"] != f"history/segment-{act['index']:06d}.jsonl":
      raise ValueError("invalid_history_checkpoint")
    if type(act["first_sequence"]) is not int or type(act["first_sequence"]) is bool or not (1 <= act["first_sequence"] <= 2**63 - 1):
      raise ValueError("invalid_history_checkpoint")
    if not isinstance(act["previous_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", act["previous_sha256"]):
      raise ValueError("invalid_history_checkpoint")
    if type(act["bytes"]) is not int or not (1 <= act["bytes"] <= HARD_MAX_SEGMENT_BYTES):
      raise ValueError("invalid_history_checkpoint")
    if not isinstance(act["content_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", act["content_sha256"]):
      raise ValueError("invalid_history_checkpoint")
    if act["index"] != cat["entries"]:
      raise ValueError("invalid_history_checkpoint")
    if checkpoint["bytes"] < act["bytes"] or checkpoint["sequence"] < act["first_sequence"]:
      raise ValueError("invalid_history_checkpoint")
  else:
    if cat["entries"] == 0 and (checkpoint["sequence"] != 0 or checkpoint["bytes"] != 0 or checkpoint["chain_sha256"] != ZERO_HASH or cat["bytes"] != 0 or cat["chain_sha256"] != ZERO_HASH):
      raise ValueError("invalid_history_checkpoint")

  if (cat["entries"] > 1_000_000 or cat["entries"] == 0 and (cat["bytes"] != 0 or cat["chain_sha256"] != ZERO_HASH)
      or cat["entries"] > 0 and cat["bytes"] == 0 or cat["bytes"] > cat["entries"] * MAX_LINE_BYTES
      or checkpoint["sequence"] > checkpoint["bytes"]):
    raise ValueError("invalid_history_checkpoint")

  return deepcopy(checkpoint)


class SegmentedJournal:
  def __init__(self, session_directory, session_id, *, max_segment_bytes, max_segment_events,
               max_consumer_bytes, max_consumer_events, max_total_bytes, free_reserve_bytes):
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
      raise ValueError("invalid_history_session")
    self.session_id = session_id

    for name, val, hard_max in (
      ("max_segment_bytes", max_segment_bytes, HARD_MAX_SEGMENT_BYTES),
      ("max_segment_events", max_segment_events, HARD_MAX_SEGMENT_EVENTS),
      ("max_consumer_bytes", max_consumer_bytes, HARD_MAX_CONSUMER_BYTES),
      ("max_consumer_events", max_consumer_events, HARD_MAX_CONSUMER_EVENTS),
      ("max_total_bytes", max_total_bytes, None),
    ):
      if type(val) is not int or type(val) is bool or val <= 0:
        raise ValueError(f"invalid_history_limits: {name}")
      if hard_max is not None and val > hard_max:
        raise ValueError(f"invalid_history_limits: {name} exceeds hard maximum")
    if type(free_reserve_bytes) is not int or type(free_reserve_bytes) is bool or free_reserve_bytes < 0:
      raise ValueError("invalid_history_limits: free_reserve_bytes")
    if max_consumer_bytes > max_segment_bytes or max_consumer_events > max_segment_events:
      raise ValueError("invalid_history_limits: consumer budget exceeds segment budget")

    self.max_segment_bytes = max_segment_bytes
    self.max_segment_events = max_segment_events
    self.max_consumer_bytes = max_consumer_bytes
    self.max_consumer_events = max_consumer_events
    self.max_total_bytes = max_total_bytes
    self.free_reserve_bytes = free_reserve_bytes

    self.session_dir = Path(session_directory)
    if any(_is_link(part) for part in (self.session_dir, *self.session_dir.parents)):
      raise ValueError("history_session_directory_not_regular")
    if not self.session_dir.is_dir():
      raise ValueError("history_session_directory_not_dir")

    self.history_dir = self.session_dir / "history"
    if _is_link(self.history_dir):
      raise ValueError("history_session_directory_not_regular")
    if self.history_dir.exists():
      if not self.history_dir.is_dir() or any(self.history_dir.iterdir()):
        raise ValueError("history_already_exists")
    else:
      self.history_dir.mkdir(parents=False, exist_ok=False)
      _sync_dir(self.session_dir)

    self.catalog_path = self.history_dir / "catalog.jsonl"
    self._catalog_file = self.catalog_path.open("xb", buffering=0)
    self._sync_history_dir()

    self._segment_index = 0
    self._segment_path = self.history_dir / f"segment-{self._segment_index:06d}.jsonl"
    self._segment_file = self._segment_path.open("xb", buffering=0)
    self._sync_history_dir()

    self._sequence = 0
    self._chain_sha256 = ZERO_HASH
    self._total_journal_bytes = 0
    self._last_mono_ns = None

    self._catalog_entries = 0
    self._catalog_bytes = 0
    self._catalog_chain_sha256 = ZERO_HASH

    self._segment_bytes = 0
    self._segment_events = 0
    self._consumer_bytes = 0
    self._consumer_events = 0
    self._segment_first_sequence = None
    self._segment_first_mono_ns = None
    self._segment_last_sequence = None
    self._segment_last_mono_ns = None
    self._segment_previous_journal_sha256 = ZERO_HASH
    self._segment_start_total_bytes = 0
    self._segment_hasher = hashlib.sha256()

    self._closed = False
    self._poisoned = False
    self._active_sealed = False

  def _sync_history_dir(self):
    _sync_dir(self.history_dir)

  def _check_disk_space(self, needed_bytes: int):
    physical_total = self._total_journal_bytes + self._catalog_bytes + needed_bytes
    if physical_total > self.max_total_bytes:
      raise StorageBudgetExhausted("storage_budget_exhausted")
    try:
      free_space = shutil.disk_usage(self.history_dir).free
    except OSError as e:
      raise StorageBudgetExhausted("storage_budget_exhausted") from e
    if free_space - needed_bytes < self.free_reserve_bytes:
      raise StorageBudgetExhausted("storage_budget_exhausted")

  def _rotate(self):
    try:
      if self._segment_index >= 999999:
        raise StorageBudgetExhausted("storage_budget_exhausted")
      os.fsync(self._segment_file.fileno())
      self._segment_file.close()
      self._segment_file = None

      seal_entry = {
        "schema_version": 1,
        "index": self._segment_index,
        "path": f"history/segment-{self._segment_index:06d}.jsonl",
        "first_sequence": self._segment_first_sequence,
        "last_sequence": self._segment_last_sequence,
        "start_total_bytes": self._segment_start_total_bytes,
        "bytes": self._segment_bytes,
        "previous_journal_sha256": self._segment_previous_journal_sha256,
        "last_journal_sha256": self._chain_sha256,
        "content_sha256": self._segment_hasher.hexdigest(),
        "first_mono_ns": self._segment_first_mono_ns,
        "last_mono_ns": self._segment_last_mono_ns,
        "previous_catalog_sha256": self._catalog_chain_sha256,
      }
      seal_sha = hashlib.sha256(_canonical(seal_entry)).hexdigest()
      persisted_catalog = {**seal_entry, "sha256": seal_sha}
      catalog_line = _canonical(persisted_catalog) + b"\n"

      self._check_disk_space(len(catalog_line))
      written = 0
      while written < len(catalog_line):
        n = self._catalog_file.write(catalog_line[written:])
        if n is None or n <= 0:
          raise OSError("history_short_write")
        written += n
      os.fsync(self._catalog_file.fileno())
      self._sync_history_dir()

      self._catalog_entries += 1
      self._catalog_bytes += len(catalog_line)
      self._catalog_chain_sha256 = seal_sha

      next_index = self._segment_index + 1
      next_path = self.history_dir / f"segment-{next_index:06d}.jsonl"
      self._check_disk_space(0)
      self._segment_file = next_path.open("xb", buffering=0)
      self._sync_history_dir()

      self._segment_index = next_index
      self._segment_path = next_path
      self._segment_bytes = 0
      self._segment_events = 0
      self._consumer_bytes = 0
      self._consumer_events = 0
      self._segment_first_sequence = None
      self._segment_first_mono_ns = None
      self._segment_last_sequence = None
      self._segment_last_mono_ns = None
      self._segment_previous_journal_sha256 = self._chain_sha256
      self._segment_start_total_bytes = self._total_journal_bytes
      self._segment_hasher = hashlib.sha256()
    except BaseException:
      self._poisoned = True
      raise

  def append(self, record: dict, *, mono_ns: int) -> dict:
    if self._poisoned:
      raise ValueError("journal_poisoned")
    if self._closed:
      raise ValueError("journal_closed")
    if not isinstance(record, dict):
      raise ValueError("record_must_be_dict")
    if type(mono_ns) is not int or type(mono_ns) is bool or mono_ns < 0:
      raise ValueError("invalid_mono_ns")
    if self._last_mono_ns is not None and mono_ns < self._last_mono_ns:
      raise ValueError("mono_ns_decreasing")

    next_seq = self._sequence + 1
    prev_sha = self._chain_sha256
    body = {
      "schema_version": 1,
      "session_id": self.session_id,
      "sequence": next_seq,
      "previous_sha256": prev_sha,
      "record": deepcopy(record),
    }
    try:
      digest = hashlib.sha256(_canonical(body)).hexdigest()
      encoded = _canonical({**body, "sha256": digest}) + b"\n"
    except (TypeError, ValueError, OverflowError, RecursionError) as e:
      raise ValueError("record_not_serializable_or_finite") from e

    line_len = len(encoded)
    if line_len > MAX_LINE_BYTES:
      raise ValueError("record_line_exceeds_max_line_bytes")
    if line_len > self.max_segment_bytes:
      raise ValueError("record_exceeds_max_segment_bytes")
    is_consumer = record.get("type") == "consumer_observation"
    if is_consumer and line_len > self.max_consumer_bytes:
      raise ValueError("consumer_record_exceeds_max_consumer_bytes")

    needs_rotation = False
    if self._segment_events > 0:
      if self._segment_bytes + line_len > self.max_segment_bytes or self._segment_events + 1 > self.max_segment_events:
        needs_rotation = True
      elif is_consumer and (self._consumer_bytes + line_len > self.max_consumer_bytes or self._consumer_events + 1 > self.max_consumer_events):
        needs_rotation = True

    if needs_rotation:
      self._rotate()

    try:
      self._check_disk_space(line_len)
      written = 0
      while written < line_len:
        n = self._segment_file.write(encoded[written:])
        if n is None or n <= 0:
          raise OSError("history_short_write")
        written += n
      os.fsync(self._segment_file.fileno())
      self._segment_hasher.update(encoded)
    except BaseException:
      self._poisoned = True
      raise

    if self._segment_events == 0:
      self._segment_first_sequence = next_seq
      self._segment_first_mono_ns = mono_ns
    self._segment_last_sequence = next_seq
    self._segment_last_mono_ns = mono_ns
    self._segment_bytes += line_len
    self._segment_events += 1
    if is_consumer:
      self._consumer_bytes += line_len
      self._consumer_events += 1

    self._sequence = next_seq
    self._chain_sha256 = digest
    self._total_journal_bytes += line_len
    self._last_mono_ns = mono_ns

    return {
      "schema_version": 2,
      "session_id": self.session_id,
      "segment_index": self._segment_index,
      "path": f"history/segment-{self._segment_index:06d}.jsonl",
      "sequence": next_seq,
      "sha256": digest,
      "bytes": self._segment_bytes,
      "total_bytes": self._total_journal_bytes,
      "durable": True,
    }

  def checkpoint(self, *, cutoff_mono_ns: int, consumer_budget_rejections: int) -> dict:
    if self._poisoned:
      raise ValueError("history_checkpoint_unavailable")
    for key, val in (("cutoff_mono_ns", cutoff_mono_ns), ("consumer_budget_rejections", consumer_budget_rejections)):
      if type(val) is not int or type(val) is bool or not (0 <= val <= 2**63 - 1):
        raise ValueError("invalid_checkpoint_arguments")
    if self._last_mono_ns is not None and cutoff_mono_ns < self._last_mono_ns:
      raise ValueError("history_checkpoint_before_last_record")

    catalog_dict = {
      "path": "history/catalog.jsonl",
      "entries": self._catalog_entries,
      "bytes": self._catalog_bytes,
      "chain_sha256": self._catalog_chain_sha256,
    }

    active_dict = None
    if not self._closed and not self._active_sealed and self._segment_events > 0:
      active_dict = {
        "index": self._segment_index,
        "path": f"history/segment-{self._segment_index:06d}.jsonl",
        "first_sequence": self._segment_first_sequence,
        "previous_sha256": self._segment_previous_journal_sha256,
        "bytes": self._segment_bytes,
        "content_sha256": self._segment_hasher.hexdigest(),
      }

    raw = {
      "schema_version": 2,
      "kind": "segmented_settings_history",
      "session_id": self.session_id,
      "sequence": self._sequence,
      "chain_sha256": self._chain_sha256,
      "bytes": self._total_journal_bytes,
      "cutoff_mono_ns": cutoff_mono_ns,
      "consumer_budget_rejections": consumer_budget_rejections,
      "durable": True,
      "catalog": catalog_dict,
      "active": active_dict,
    }
    return validate_checkpoint(raw)

  def close(self):
    if self._closed:
      return
    if self._poisoned:
      if self._segment_file is not None and not self._segment_file.closed:
        self._segment_file.close()
      if self._catalog_file is not None and not self._catalog_file.closed:
        self._catalog_file.close()
      self._closed = True
      return

    if self._segment_events > 0 and not self._active_sealed:
      try:
        os.fsync(self._segment_file.fileno())
        self._segment_file.close()
        self._segment_file = None

        seal_entry = {
          "schema_version": 1,
          "index": self._segment_index,
          "path": f"history/segment-{self._segment_index:06d}.jsonl",
          "first_sequence": self._segment_first_sequence,
          "last_sequence": self._segment_last_sequence,
          "start_total_bytes": self._segment_start_total_bytes,
          "bytes": self._segment_bytes,
          "previous_journal_sha256": self._segment_previous_journal_sha256,
          "last_journal_sha256": self._chain_sha256,
          "content_sha256": self._segment_hasher.hexdigest(),
          "first_mono_ns": self._segment_first_mono_ns,
          "last_mono_ns": self._segment_last_mono_ns,
          "previous_catalog_sha256": self._catalog_chain_sha256,
        }
        seal_sha = hashlib.sha256(_canonical(seal_entry)).hexdigest()
        persisted_catalog = {**seal_entry, "sha256": seal_sha}
        catalog_line = _canonical(persisted_catalog) + b"\n"

        self._check_disk_space(len(catalog_line))
        written = 0
        while written < len(catalog_line):
          n = self._catalog_file.write(catalog_line[written:])
          if n is None or n <= 0:
            raise OSError("history_short_write")
          written += n
        os.fsync(self._catalog_file.fileno())
        self._sync_history_dir()

        self._catalog_entries += 1
        self._catalog_bytes += len(catalog_line)
        self._catalog_chain_sha256 = seal_sha
        self._active_sealed = True
      except BaseException:
        self._poisoned = True
        if self._segment_file is not None and not self._segment_file.closed:
          self._segment_file.close()
        if self._catalog_file is not None and not self._catalog_file.closed:
          self._catalog_file.close()
        self._closed = True
        raise
    else:
      if self._segment_file is not None and not self._segment_file.closed:
        self._segment_file.close()
        self._segment_file = None

    if self._catalog_file is not None and not self._catalog_file.closed:
      self._catalog_file.close()
      self._catalog_file = None
    self._closed = True



def _uint(value, minimum=0, maximum=2**63 - 1):
  if type(value) is not int or not minimum <= value <= maximum:
    raise ValueError("history_integer_invalid")
  return value


def _sha(value):
  if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
    raise ValueError("history_hash_invalid")


def validate_reference(value):
  """Detach a durable legacy v1 or segmented v2 physical journal reference."""
  legacy = {"path", "sequence", "sha256", "bytes", "durable"}
  current = legacy | {"schema_version", "session_id", "segment_index", "total_bytes"}
  if type(value) is not dict or set(value) not in (legacy, current) or value["durable"] is not True:
    raise ValueError("history_reference_invalid")
  _uint(value["sequence"])
  _uint(value["bytes"])
  _sha(value["sha256"])
  if set(value) == legacy:
    if value["path"] != "settings.jsonl":
      raise ValueError("history_reference_invalid")
  else:
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
      raise ValueError("history_reference_invalid")
    if type(value["session_id"]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["session_id"]) is None:
      raise ValueError("history_reference_invalid")
    index = _uint(value["segment_index"], maximum=999999)
    if value["path"] != f"history/segment-{index:06d}.jsonl":
      raise ValueError("history_reference_invalid")
    _uint(value["bytes"], 1, HARD_MAX_SEGMENT_BYTES)
    _uint(value["total_bytes"], value["bytes"])
    _uint(value["sequence"], 1)
  if value["sequence"] == 0 and (value["bytes"] or value["sha256"] != ZERO_HASH):
    raise ValueError("history_reference_invalid")
  return deepcopy(value)


@contextmanager
def _source(session_directory, relative):
  base = Path(session_directory)
  if any(_is_link(part) for part in (base, *base.parents)) or not base.is_dir():
    raise ValueError("history_session_directory_not_regular")
  pattern = r"history/(?:catalog|segment-[0-9]{6})\.jsonl"
  path = _check_safe_relative_path(base, relative, pattern)
  if not path.is_file():
    raise ValueError("history_file_missing_or_not_regular")
  before = path.stat()
  fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
  try:
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
      raise ValueError("history_file_changed")
    with os.fdopen(fd, "rb", closefd=False) as stream:
      yield stream
    after = path.lstat()
    if _is_link(path) or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
      raise ValueError("history_file_changed")
  finally:
    os.close(fd)


def _lines(stream, limit):
  offset = 0
  while offset < limit:
    line = stream.readline(min(MAX_LINE_BYTES + 1, limit - offset + 1))
    offset += len(line)
    if not line or len(line) > MAX_LINE_BYTES or offset > limit or not line.endswith(b"\n"):
      raise ValueError("history_partial_or_oversized_line")
    try:
      value = json.loads(line.decode("utf-8"), object_pairs_hook=_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_history")))
    except (UnicodeError, RecursionError) as error:
      raise ValueError("history_json_invalid") from error
    yield line, value, offset


def iter_verified_catalog(session_directory, checkpoint):
  """Stream the exact catalog prefix; exhaust to verify its checkpoint closure.

  This verifies metadata anchors, not any unselected segment contents.
  """
  cp = validate_checkpoint(checkpoint)
  fields = {"schema_version", "index", "path", "first_sequence", "last_sequence", "start_total_bytes", "bytes",
            "previous_journal_sha256", "last_journal_sha256", "content_sha256", "first_mono_ns", "last_mono_ns",
            "previous_catalog_sha256", "sha256"}
  count, sequence, total, chain, journal, mono = 0, 0, 0, ZERO_HASH, ZERO_HASH, 0
  with _source(session_directory, cp["catalog"]["path"]) as stream:
    if os.fstat(stream.fileno()).st_size < cp["catalog"]["bytes"]:
      raise ValueError("catalog_file_truncated")
    for _, entry, _ in _lines(stream, cp["catalog"]["bytes"]):
      if type(entry) is not dict or set(entry) != fields:
        raise ValueError("catalog_entry_fields")
      if type(entry["schema_version"]) is not int or entry["schema_version"] != 1 or _uint(entry["index"], maximum=999999) != count:
        raise ValueError("catalog_index_reordered_or_mismatched")
      if entry["path"] != f"history/segment-{count:06d}.jsonl":
        raise ValueError("catalog_segment_path_mismatch")
      first = _uint(entry["first_sequence"], 1)
      last = _uint(entry["last_sequence"], first)
      size = _uint(entry["bytes"], 1, HARD_MAX_SEGMENT_BYTES)
      if first != sequence + 1 or last - first + 1 > HARD_MAX_SEGMENT_EVENTS or _uint(entry["start_total_bytes"]) != total:
        raise ValueError("catalog_sequence_or_byte_offset_mismatch")
      for key in ("previous_journal_sha256", "last_journal_sha256", "content_sha256", "previous_catalog_sha256", "sha256"):
        _sha(entry[key])
      if entry["previous_journal_sha256"] != journal or entry["previous_catalog_sha256"] != chain:
        raise ValueError("catalog_chain_mismatch")
      start = _uint(entry["first_mono_ns"], mono)
      mono = _uint(entry["last_mono_ns"], start, cp["cutoff_mono_ns"])
      if hashlib.sha256(_canonical({key: val for key, val in entry.items() if key != "sha256"})).hexdigest() != entry["sha256"]:
        raise ValueError("catalog_entry_hash_mismatch")
      count, sequence, total = count + 1, last, total + size
      chain, journal = entry["sha256"], entry["last_journal_sha256"]
      if count > cp["catalog"]["entries"]:
        raise ValueError("catalog_entry_count_mismatch")
      yield entry
  if count != cp["catalog"]["entries"] or chain != cp["catalog"]["chain_sha256"]:
    raise ValueError("catalog_prefix_closure_mismatch")
  active = cp["active"]
  if active is not None:
    if (active["first_sequence"] != sequence + 1 or active["previous_sha256"] != journal
        or total + active["bytes"] != cp["bytes"] or cp["sequence"] - sequence > HARD_MAX_SEGMENT_EVENTS):
      raise ValueError("catalog_active_anchor_mismatch")
  elif (sequence, total, journal) != (cp["sequence"], cp["bytes"], cp["chain_sha256"]):
    raise ValueError("catalog_checkpoint_closure_mismatch")


def _descriptors(session_directory, cp):
  for entry in iter_verified_catalog(session_directory, cp):
    yield {"index": entry["index"], "path": entry["path"], "first_sequence": entry["first_sequence"],
           "previous_sha256": entry["previous_journal_sha256"], "bytes": entry["bytes"],
           "content_sha256": entry["content_sha256"], "start_total_bytes": entry["start_total_bytes"],
           "last_sequence": entry["last_sequence"], "last_journal_sha256": entry["last_journal_sha256"], "active": False}
  if cp["active"] is not None:
    active = cp["active"]
    yield {**active, "start_total_bytes": cp["bytes"] - active["bytes"], "last_sequence": cp["sequence"],
           "last_journal_sha256": cp["chain_sha256"], "active": True}


def iter_verified_records(session_directory, checkpoint, *, segment_indices=None):
  """Stream verified rows as {envelope, reference} without retaining the session.

  Complete catalog closure is checked before source data. Full-prefix source
  verification requires exhausting the generator with segment_indices=None.
  A subset verifies only selected source closures, never unselected contents.
  """
  cp = validate_checkpoint(checkpoint)
  count = cp["catalog"]["entries"] + (cp["active"] is not None)
  selected = None
  if segment_indices is not None:
    if type(segment_indices) not in (list, tuple, set, frozenset) or len(segment_indices) > count:
      raise ValueError("invalid_segment_selection")
    selected = set()
    for index in segment_indices:
      _uint(index, maximum=count - 1)
      if index in selected:
        raise ValueError("duplicate_segment_selection")
      selected.add(index)
  for _ in iter_verified_catalog(session_directory, cp):
    pass
  last_seq, last_hash, last_bytes = 0, ZERO_HASH, 0
  for desc in _descriptors(session_directory, cp):
    if selected is not None and desc["index"] not in selected:
      continue
    with _source(session_directory, desc["path"]) as stream:
      size = os.fstat(stream.fileno()).st_size
      if size < desc["bytes"] or not desc["active"] and size != desc["bytes"]:
        raise ValueError("segment_file_size_mismatch")
      digest, remaining = hashlib.sha256(), desc["bytes"]
      while remaining:
        block = stream.read(min(65536, remaining))
        if not block:
          raise ValueError("segment_prefix_truncated")
        digest.update(block)
        remaining -= len(block)
      if digest.hexdigest() != desc["content_sha256"]:
        raise ValueError("segment_content_hash_mismatch")
      stream.seek(0)
      sequence, chain, digest = desc["first_sequence"] - 1, desc["previous_sha256"], hashlib.sha256()
      offset = 0
      for line, envelope, offset in _lines(stream, desc["bytes"]):
        if type(envelope) is not dict or set(envelope) != {"schema_version", "session_id", "sequence", "previous_sha256", "record", "sha256"}:
          raise ValueError("invalid_history_envelope")
        if (type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1 or envelope["session_id"] != cp["session_id"]
            or _uint(envelope["sequence"], 1) != sequence + 1 or envelope["previous_sha256"] != chain or type(envelope["record"]) is not dict):
          raise ValueError("history_envelope_chain_mismatch")
        _sha(envelope["sha256"])
        if hashlib.sha256(_canonical({key: value for key, value in envelope.items() if key != "sha256"})).hexdigest() != envelope["sha256"]:
          raise ValueError("history_envelope_hash_mismatch")
        digest.update(line)
        sequence, chain = envelope["sequence"], envelope["sha256"]
        last_seq, last_hash, last_bytes = sequence, chain, desc["start_total_bytes"] + offset
        yield {"envelope": envelope, "reference": {"schema_version": 2, "session_id": cp["session_id"],
          "segment_index": desc["index"], "path": desc["path"], "sequence": sequence, "sha256": chain,
          "bytes": offset, "total_bytes": last_bytes, "durable": True}}
      if (sequence, chain, digest.hexdigest()) != (desc["last_sequence"], desc["last_journal_sha256"], desc["content_sha256"]):
        raise ValueError("segment_closure_anchor_mismatch")
      if not desc["active"] and os.fstat(stream.fileno()).st_size != desc["bytes"]:
        raise ValueError("sealed_segment_size_changed")
  if selected is None and (last_seq, last_hash, last_bytes) != (cp["sequence"], cp["chain_sha256"], cp["bytes"]):
    raise ValueError("checkpoint_prefix_verification_mismatch")
