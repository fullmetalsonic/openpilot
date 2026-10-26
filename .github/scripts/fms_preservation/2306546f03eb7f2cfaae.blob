"""Lossless comparison exclusion-interval codecs for public comparison summaries."""
import re

ENCODING = "reason-indexed-v1"

_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_REASON_RE = re.compile(r"^[a-z_]{1,128}$")
_MAX_INT64 = (1 << 63) - 1
_MAX_SAFE_INT = (1 << 53) - 1
_MAX_BOUND = 100_000
_LEGACY_KEYS = {"reason", "start_ns", "end_ns", "sample_count"}
_V2_KEYS = {"encoding", "reasons", "rows"}


def _parse_decimal_ns(s):
  if type(s) is not str or not _DECIMAL_RE.fullmatch(s):
    raise ValueError("invalid_public_comparison_exclusions")
  if len(s) > 19:
    raise ValueError("invalid_public_comparison_exclusions")
  val = int(s)
  if val > _MAX_INT64:
    raise ValueError("invalid_public_comparison_exclusions")
  return val


def encode_exclusions(intervals):
  """Encode legacy exclusion interval dicts into v2 reason-indexed wire representation."""
  if type(intervals) is not list or len(intervals) > _MAX_BOUND:
    raise ValueError("invalid_public_comparison_exclusions")

  reasons = []
  reason_to_idx = {}
  rows = []

  for item in intervals:
    if type(item) is not dict or set(item.keys()) != _LEGACY_KEYS:
      raise ValueError("invalid_public_comparison_exclusions")

    reason = item["reason"]
    if type(reason) is not str or not _REASON_RE.fullmatch(reason):
      raise ValueError("invalid_public_comparison_exclusions")

    start_ns = item["start_ns"]
    if type(start_ns) is not int or isinstance(start_ns, bool) or not (0 <= start_ns <= _MAX_INT64):
      raise ValueError("invalid_public_comparison_exclusions")

    end_ns = item["end_ns"]
    if type(end_ns) is not int or isinstance(end_ns, bool) or not (0 <= end_ns <= _MAX_INT64):
      raise ValueError("invalid_public_comparison_exclusions")

    if start_ns > end_ns:
      raise ValueError("invalid_public_comparison_exclusions")

    sample_count = item["sample_count"]
    if type(sample_count) is not int or isinstance(sample_count, bool) or not (0 <= sample_count <= _MAX_SAFE_INT):
      raise ValueError("invalid_public_comparison_exclusions")

    idx = reason_to_idx.get(reason)
    if idx is None:
      idx = len(reasons)
      if idx >= _MAX_BOUND:
        raise ValueError("invalid_public_comparison_exclusions")
      reason_to_idx[reason] = idx
      reasons.append(reason)

    rows.append([idx, str(start_ns), str(end_ns), sample_count])

  return {
    "encoding": ENCODING,
    "reasons": reasons,
    "rows": rows,
  }


def decode_exclusions(value, schema_version=1):
  """Decode public exclusion representation into fresh legacy-shaped interval dicts."""
  if type(schema_version) is not int or isinstance(schema_version, bool) or schema_version not in (1, 2):
    raise ValueError("invalid_public_comparison_exclusions")

  if schema_version == 1:
    if type(value) is not list or len(value) > _MAX_BOUND:
      raise ValueError("invalid_public_comparison_exclusions")

    decoded = []
    for item in value:
      if type(item) is not dict or set(item.keys()) != _LEGACY_KEYS:
        raise ValueError("invalid_public_comparison_exclusions")

      reason = item["reason"]
      if type(reason) is not str or not _REASON_RE.fullmatch(reason):
        raise ValueError("invalid_public_comparison_exclusions")

      start_ns = item["start_ns"]
      if type(start_ns) is not int or isinstance(start_ns, bool) or not (0 <= start_ns <= _MAX_INT64):
        raise ValueError("invalid_public_comparison_exclusions")

      end_ns = item["end_ns"]
      if type(end_ns) is not int or isinstance(end_ns, bool) or not (0 <= end_ns <= _MAX_INT64):
        raise ValueError("invalid_public_comparison_exclusions")

      if start_ns > end_ns:
        raise ValueError("invalid_public_comparison_exclusions")

      sample_count = item["sample_count"]
      if type(sample_count) is not int or isinstance(sample_count, bool) or not (0 <= sample_count <= _MAX_SAFE_INT):
        raise ValueError("invalid_public_comparison_exclusions")

      decoded.append({
        "reason": reason,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "sample_count": sample_count,
      })

    return decoded

  if type(value) is not dict or set(value.keys()) != _V2_KEYS:
    raise ValueError("invalid_public_comparison_exclusions")

  if value["encoding"] != ENCODING:
    raise ValueError("invalid_public_comparison_exclusions")

  reasons = value["reasons"]
  rows = value["rows"]

  if type(reasons) is not list or len(reasons) > _MAX_BOUND:
    raise ValueError("invalid_public_comparison_exclusions")
  if type(rows) is not list or len(rows) > _MAX_BOUND:
    raise ValueError("invalid_public_comparison_exclusions")

  for r in reasons:
    if type(r) is not str or not _REASON_RE.fullmatch(r):
      raise ValueError("invalid_public_comparison_exclusions")

  if len(set(reasons)) != len(reasons):
    raise ValueError("invalid_public_comparison_exclusions")

  next_expected_idx = 0
  decoded = []

  for row in rows:
    if type(row) is not list or len(row) != 4:
      raise ValueError("invalid_public_comparison_exclusions")

    idx, start_str, end_str, count = row

    if type(idx) is not int or isinstance(idx, bool) or idx < 0 or idx >= len(reasons):
      raise ValueError("invalid_public_comparison_exclusions")

    if idx == next_expected_idx:
      next_expected_idx += 1
    elif idx > next_expected_idx:
      raise ValueError("invalid_public_comparison_exclusions")

    start_ns = _parse_decimal_ns(start_str)
    end_ns = _parse_decimal_ns(end_str)
    if start_ns > end_ns:
      raise ValueError("invalid_public_comparison_exclusions")

    if type(count) is not int or isinstance(count, bool) or not (0 <= count <= _MAX_SAFE_INT):
      raise ValueError("invalid_public_comparison_exclusions")

    decoded.append({
      "reason": reasons[idx],
      "start_ns": start_ns,
      "end_ns": end_ns,
      "sample_count": count,
    })

  if next_expected_idx != len(reasons):
    raise ValueError("invalid_public_comparison_exclusions")

  return decoded
