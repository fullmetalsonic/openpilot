"""Bounded, read-only rlog input for offline tracking diagnostics.

CarState anchors preserve brief pedal/steering interventions. Held targets and
model-derived lateral responses retain their own timestamps; these observations
are not independent-response data or evidence of settings application.
"""
from __future__ import annotations

import bz2
from contextlib import ExitStack, contextmanager
from functools import lru_cache
import math
from pathlib import Path
import re
import struct
from typing import Callable, Iterator

from .normalizer import EventNormalizer, SERVICES


SELECTABLE_SERVICES = frozenset((*SERVICES, "initData", "carParams", "selfdriveState", "carOutput", "radarState",
                                 "liveParameters", "liveTorqueParameters", "livePose", "liveCalibration", "liveDelay"))
_SAFE_FIELDS = {
  "carParams": ("brand", "steerControlType", "openpilotLongitudinalControl", "mass", "wheelbase", "centerToFront",
                "rotationalInertia", "tireStiffnessFront", "tireStiffnessRear", "steerRatioRear", "steerRatio",
                "stopAccel", "startAccel", "stoppingDecelRate"),
  "selfdriveState": ("personality", "enabled", "active", "state"),
  "carOutput": ("actuatorsOutput",),
  "radarState": ("leadOne", "leadTwo"),
  "liveParameters": ("valid", "sensorValid", "posenetValid", "angleOffsetDeg", "stiffnessFactor", "steerRatio", "roll",
                     "angleOffsetValid", "steerRatioValid", "stiffnessFactorValid"),
  "liveTorqueParameters": ("liveValid", "useParams", "latAccelFactorRaw", "latAccelOffsetRaw", "latAccelFactorFiltered",
                           "latAccelOffsetFiltered", "frictionCoefficientFiltered", "version"),
  "livePose": ("orientationNED", "angularVelocityDevice", "inputsOK", "posenetOK", "sensorsOK", "timestamp"),
  "liveCalibration": ("calStatus", "rpyCalib", "rpyCalibSpread", "validBlocks"),
  "liveDelay": ("lateralDelay", "validBlocks", "status"),
}


def decode_recorded_car_params(raw):
  """Project only recorded VM inputs and hashes, never raw CP or VIN fields."""
  from .vehicle_identity import MAX_CP_BYTES, describe_car_params, _schema as car_schema
  from .lateral_observations import CP_FIELDS
  result = {"schema_version": 1, "state": "unavailable", "reason": "recorded_cp_missing",
            "source": "initData.params.CarParams", "values": None, "config_sha256": None,
            "schema_sha256": None, "raw_sha256": None, "recording_schema_verified": False}
  if raw is None:
    return result
  if type(raw) is not bytes or not 0 < len(raw) <= MAX_CP_BYTES:
    result["reason"] = "recorded_cp_empty" if type(raw) is bytes and raw == b"" else "recorded_cp_size_or_type_invalid"
    return result
  identity = describe_car_params(raw)
  if identity["state"] != "observed":
    result["reason"] = "recorded_" + identity["reason"]
    return result
  try:
    _, schema, _ = car_schema()
    with schema.CarParams.from_bytes(raw, traversal_limit_in_words=MAX_CP_BYTES // 8) as message:
      values = {key: float(getattr(message, key)) for key in CP_FIELDS}
      controller_context = {"brand": str(message.brand), "steer_control_type": str(message.steerControlType),
                            "flags": int(message.flags), "openpilot_longitudinal": bool(message.openpilotLongitudinalControl)}
    if (any(not math.isfinite(value) for value in values.values())
        or any(values[key] <= 0 for key in ("mass", "rotationalInertia", "wheelbase", "tireStiffnessFront", "tireStiffnessRear", "steerRatio"))
        or not 0 < values["centerToFront"] < values["wheelbase"] or abs(values["steerRatioRear"]) >= 1):
      result["reason"] = "recorded_cp_vm_values_invalid"
      return result
  except Exception:
    result["reason"] = "recorded_cp_decode_unavailable"
    return result
  return {**result, "state": "observed", "reason": None, "values": values, "controller_context": controller_context,
          **{key: identity[key] for key in ("config_sha256", "schema_sha256", "raw_sha256")}}


def _selected_data(service, data):
  if service in SERVICES:
    return data.to_dict()  # Preserve the existing four-service normalizer contract.
  if service == "initData":
    from .registry import load_registry
    keys = {item["key"] for item in load_registry()["items"]}
    settings, seen, invalid = {}, set(), set()
    recorded_cp, cp_seen = None, False
    if len(data.params.entries) > 4096:
      raise ValueError("rlog initData parameter count limit exceeded")
    for entry in data.params.entries:
      key = str(entry.key)
      if key == "CarParams":
        if cp_seen:
          recorded_cp = {**decode_recorded_car_params(None), "reason": "recorded_cp_duplicate"}
        elif len(entry.value) > 1024 * 1024:
          recorded_cp = {**decode_recorded_car_params(None), "reason": "recorded_cp_size_or_type_invalid"}
        else:
          recorded_cp = decode_recorded_car_params(bytes(entry.value))
        cp_seen = True
        continue
      if key not in keys:
        continue
      if key in seen:
        invalid.add(key)
      seen.add(key)
      raw = bytes(entry.value) if len(entry.value) <= 32 else b""
      if re.fullmatch(rb"[+-]?[0-9]+", raw.strip()) is None:
        invalid.add(key)
        continue
      value = int(raw)
      if abs(value) <= 1_000_000:
        settings[key] = value
      else:
        invalid.add(key)
    for key in invalid:
      settings.pop(key, None)
    commits = {name: str(getattr(data, name)) for name in ("gitCommit", "gitSrcCommit")}
    commits = {name: value.lower() if re.fullmatch(r"[a-fA-F0-9]{40}", value) else None for name, value in commits.items()}
    return {**commits, "dirty": bool(data.dirty),
            "settings_raw": settings, "invalid_setting_keys": sorted(invalid), "advisory_only": True,
            "recorded_car_params": recorded_cp if cp_seen else decode_recorded_car_params(None)}
  # Project before conversion: VIN, device identifiers, raw Params and location
  # are never part of the selectable event interface.
  result = {}
  for name in _SAFE_FIELDS[service]:
    if name not in data.schema.fields:
      continue
    value = getattr(data, name)
    if type(value).__name__ == "_DynamicEnum":
      value = str(value)
    elif hasattr(value, "to_dict"):
      value = value.to_dict()
    elif hasattr(value, "schema") and not isinstance(value, (str, bytes)):
      value = str(value)
    elif type(value).__name__.startswith("_DynamicList"):
      value = list(value)
    result[name] = value
  return result


def _positive_int(value: int, name: str) -> None:
  if type(value) is not int or value <= 0:
    raise ValueError(f"{name} must be a positive integer")


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
  if cancelled is not None and cancelled():
    raise InterruptedError("rlog analysis cancelled")


@lru_cache(maxsize=1)
def _schema():
  try:
    import capnp
  except ImportError as exc:
    raise RuntimeError("rlog decoding requires pycapnp; use the existing analysis Python environment") from exc
  root = Path(__file__).resolve().parents[4]
  cereal = root / "openpilot" / "cereal"
  car = root / "opendbc_repo" / "opendbc" / "car"
  imports = [str(cereal), str(cereal / "include"), str(car), str(car / "include")]
  parser = capnp.SchemaParser()
  log = parser.load(str(cereal / "log.capnp"), "/log.capnp", imports=imports)
  return parser, log


class _LimitedReader:
  def __init__(self, reader, limit: int, cancelled):
    self.reader, self.limit, self.cancelled, self.total = reader, limit, cancelled, 0

  def exact(self, size: int, *, eof: bool = False) -> bytes | None:
    _check_cancelled(self.cancelled)
    if size > self.limit - self.total and not eof:
      raise ValueError("decompressed byte limit exceeded")
    output = bytearray()
    while len(output) < size:
      _check_cancelled(self.cancelled)
      remaining = self.limit - self.total
      if remaining == 0:
        # Probe EOF without retaining data beyond the declared byte budget.
        if self.reader.read(1):
          raise ValueError("decompressed byte limit exceeded")
        if eof and not output:
          return None
        raise ValueError("truncated rlog event or compressed frame")
      block = self.reader.read(min(size - len(output), remaining, 64 * 1024))
      if not block:
        if eof and not output:
          return None
        raise ValueError("truncated rlog event or compressed frame")
      output.extend(block)
      self.total += len(block)
    return bytes(output)


def _validate_zstd_frames(raw, *, byte_limit: int, window_limit: int, max_blocks: int, cancelled) -> None:
  """Check frame completion before streaming decode (RFC 8878 sections 3.1.1/2).

  zstandard.stream_reader can return EOF for a truncated final frame. This
  bounded pass validates framing; libzstd still validates compressed content.
  """
  reader = _LimitedReader(raw, byte_limit, cancelled)
  blocks = frames = 0
  while (magic := reader.exact(4, eof=True)) is not None:
    frames += 1
    if frames > max_blocks:
      raise ValueError("zstd frame count limit exceeded")
    number = int.from_bytes(magic, "little")
    if 0x184D2A50 <= number <= 0x184D2A5F:
      size = int.from_bytes(reader.exact(4), "little")
      while size:
        count = min(size, 64 * 1024)
        reader.exact(count)
        size -= count
      continue
    if magic != b"\x28\xb5\x2f\xfd":
      raise ValueError("invalid zstd frame or trailing data")
    descriptor = reader.exact(1)[0]
    if descriptor & 0x08:
      raise ValueError("reserved zstd frame flag")
    single = bool(descriptor & 0x20)
    content_flag = descriptor >> 6
    dictionary_size = (0, 1, 2, 4)[descriptor & 3]
    content_size = (1 if single else 0) if content_flag == 0 else (2, 4, 8)[content_flag - 1]
    header_data = reader.exact((0 if single else 1) + dictionary_size + content_size)
    if single:
      window_size = int.from_bytes(header_data[dictionary_size:], "little") + (256 if content_flag == 1 else 0)
    else:
      window_descriptor = header_data[0]
      base = 1 << (10 + (window_descriptor >> 3))
      window_size = base + (base >> 3) * (window_descriptor & 7)
    if window_size > window_limit:
      raise ValueError("zstd window byte limit exceeded")
    last = False
    while not last:
      blocks += 1
      if blocks > max_blocks:
        raise ValueError("zstd block count limit exceeded")
      header = int.from_bytes(reader.exact(3), "little")
      last, kind, size = bool(header & 1), (header >> 1) & 3, header >> 3
      if kind == 3 or size > 128 * 1024:
        raise ValueError("invalid zstd block header")
      payload = 1 if kind == 1 else size
      while payload:
        count = min(payload, 64 * 1024)
        reader.exact(count)
        payload -= count
    if descriptor & 4:
      reader.exact(4)


class _BzipReader:
  """Small incremental buffer with strict EOF, including concatenated streams."""
  def __init__(self, raw, limit: int, cancelled):
    self.raw, self.limit, self.cancelled = raw, limit, cancelled
    self.decoder = bz2.BZ2Decompressor()
    self.pending = b""
    self.output = b""
    self.total = 0
    self.finished = False
    self.input_total = 0

  def read(self, size: int) -> bytes:
    while len(self.output) < size and not self.finished:
      _check_cancelled(self.cancelled)
      if self.decoder.eof:
        self.pending = self.decoder.unused_data
        if not self.pending:
          self.pending = self.raw.read(16 * 1024)
          self.input_total += len(self.pending)
        if not self.pending:
          self.finished = True
          break
        self.decoder = bz2.BZ2Decompressor()
      elif self.decoder.needs_input and not self.pending:
        self.pending = self.raw.read(16 * 1024)
        self.input_total += len(self.pending)
        if not self.pending:
          raise ValueError("truncated bzip2 frame")
      if self.input_total > self.limit * 2 + 64 * 1024:
        raise ValueError("compressed byte limit exceeded")
      try:
        chunk = self.decoder.decompress(self.pending, max_length=min(64 * 1024, self.limit - self.total + 1))
      except OSError as exc:
        raise ValueError("invalid bzip2 frame or trailing data") from exc
      self.pending = b""
      self.total += len(chunk)
      if self.total > self.limit:
        raise ValueError("decompressed byte limit exceeded")
      self.output += chunk
    result, self.output = self.output[:size], self.output[size:]
    return result


class _FrozenSizeReader:
  """A validation pass cannot consume bytes appended after its initial stat."""
  def __init__(self, raw, size):
    self.raw, self.size = raw, size

  def read(self, size=-1):
    remaining = self.size - self.raw.tell()
    if remaining == 0:
      if self.raw.read(1):
        raise ValueError("rlog_source_size_changed")
      return b""
    return self.raw.read(remaining if size < 0 else min(size, remaining))

  def seek(self, offset):
    if offset != 0:
      raise ValueError("unsupported_rlog_validation_seek")
    return self.raw.seek(offset)


@contextmanager
def _input(path: Path, limit: int, max_events: int, cancelled, source_size_limit=None):
  with ExitStack() as stack:
    raw = stack.enter_context(path.open("rb"))
    if source_size_limit is not None:
      raw = _FrozenSizeReader(raw, source_size_limit)
    magic = raw.read(4)
    raw.seek(0)
    if magic[:3] == b"BZh":
      yield _BzipReader(raw, limit, cancelled)
    elif magic == b"\x28\xb5\x2f\xfd" or (len(magic) == 4 and 0x184D2A50 <= int.from_bytes(magic, "little") <= 0x184D2A5F):
      try:
        import zstandard
      except ImportError as exc:
        raise RuntimeError("zstd rlog decoding requires zstandard in the analysis Python environment") from exc
      _validate_zstd_frames(raw, byte_limit=limit * 2 + 64 * 1024, window_limit=limit, max_blocks=max_events, cancelled=cancelled)
      raw.seek(0)
      # The frame scan enforces bytes independently of wrapper-version units.
      decoder = zstandard.ZstdDecompressor(max_window_size=max(1024, limit))
      try:
        yield stack.enter_context(decoder.stream_reader(raw, read_size=16 * 1024, read_across_frames=True, closefd=False))
      except zstandard.ZstdError as exc:
        raise ValueError("invalid zstd payload") from exc
    else:
      if path.suffix.lower() in (".zst", ".zstd", ".bz2"):
        raise ValueError("compressed rlog header does not match file extension")
      yield raw


def _frames(path: Path, event_limit: int, byte_limit: int, count_limit: int, cancelled, source_size_limit=None):
  with _input(path, byte_limit, count_limit, cancelled, source_size_limit) as stream:
    reader = _LimitedReader(stream, byte_limit, cancelled)
    count = 0
    while (first := reader.exact(4, eof=True)) is not None:
      count += 1
      if count > count_limit:
        raise ValueError("rlog event count limit exceeded")
      segments = struct.unpack("<I", first)[0] + 1
      if segments > 512:
        raise ValueError("invalid Cap'n Proto segment count")
      table_size = 4 * segments + (4 if segments % 2 == 0 else 0)
      if 4 + table_size + 8 > event_limit:
        raise ValueError("rlog event byte limit exceeded")
      table = reader.exact(table_size)
      body_size = sum(struct.unpack("<" + "I" * segments, table[:segments * 4])) * 8
      if body_size < 8 or 4 + table_size + body_size > event_limit:
        raise ValueError("rlog event byte limit exceeded")
      yield first + table + reader.exact(body_size)


@contextmanager
def _event(log, blob: bytes, event_limit: int):
  import capnp
  try:
    with log.Event.from_bytes(blob, traversal_limit_in_words=event_limit // 8, nesting_limit=64) as event:
      yield event
  except capnp.KjException as exc:
    raise ValueError("invalid Cap'n Proto event payload") from exc


def validate_closed_log(path: Path, *, max_event_bytes: int = 8 * 1024 * 1024,
                        max_decompressed_bytes: int = 256 * 1024 * 1024, max_events: int = 200_000,
                        cancelled: Callable[[], bool] | None = None) -> dict:
  """Validate copied Event bytes and a final valid logger end sentinel.

  This proves the supplied bytes contain a closed segment, not that a live
  source has stopped changing. The archiver separately checks source locks and
  hashes before publishing. Cross-service timestamp regressions are reported.
  """
  for value, name in ((max_event_bytes, "max_event_bytes"), (max_decompressed_bytes, "max_decompressed_bytes"),
                      (max_events, "max_events")):
    _positive_int(value, name)
  _check_cancelled(cancelled)
  _, log = _schema()
  count, minimum, maximum, previous, regressions, decompressed_bytes = 0, None, None, None, 0, 0
  services, last_by_service = {}, {}
  end_sentinel, start_sentinel = None, None
  code_commit, code_consistent, seen_init = None, True, False
  path = Path(path)
  for blob in _frames(path, max_event_bytes, max_decompressed_bytes, max_events, cancelled, path.stat().st_size):
    _check_cancelled(cancelled)
    if end_sentinel is not None:
      raise ValueError("rlog_event_after_end_sentinel")
    with _event(log, blob, max_event_bytes) as event:
      service, mono = event.which(), int(event.logMonoTime)
      valid = bool(event.valid)
      count += 1
      decompressed_bytes += len(blob)
      minimum = mono if minimum is None else min(minimum, mono)
      maximum = mono if maximum is None else max(maximum, mono)
      regressions += int(previous is not None and mono < previous)
      previous = mono
      row = services.setdefault(service, {"count": 0, "valid_count": 0, "mono_min_ns": mono, "mono_max_ns": mono,
                                          "timestamp_regressions": 0})
      row["count"] += 1
      row["valid_count"] += int(valid)
      row["mono_min_ns"] = min(row["mono_min_ns"], mono)
      row["mono_max_ns"] = max(row["mono_max_ns"], mono)
      row["timestamp_regressions"] += int(service in last_by_service and mono < last_by_service[service])
      last_by_service[service] = mono
      if service == "sentinel":
        kind = str(event.sentinel.type)
        if kind in ("endOfSegment", "endOfRoute"):
          if start_sentinel is None:
            raise ValueError("rlog_missing_start_sentinel")
          if not valid:
            raise ValueError("rlog_invalid_end_sentinel")
          end_sentinel = kind
        elif kind in ("startOfSegment", "startOfRoute"):
          if start_sentinel is not None or not valid:
            raise ValueError("rlog_invalid_start_sentinel")
          start_sentinel = kind
      elif service == "initData":
        declared = str(event.initData.gitCommit)
        if len(declared) != 40 or any(char not in "0123456789abcdefABCDEF" for char in declared):
          declared = None
        if not seen_init:
          code_commit = declared
        elif declared != code_commit:
          code_consistent = False
        seen_init = True
      elif start_sentinel is None:
        raise ValueError("rlog_missing_start_sentinel")
  if end_sentinel is None:
    raise ValueError("rlog_missing_end_sentinel")
  return {"event_count": count, "decompressed_bytes": decompressed_bytes, "mono_min_ns": minimum, "mono_max_ns": maximum,
          "timestamp_regressions": regressions, "services": dict(sorted(services.items())),
          "start_sentinel": start_sentinel, "end_sentinel": end_sentinel, "end_sentinel_valid": True,
          "recorded_code_commit": code_commit if code_consistent else None,
          "code_declaration_consistent": code_consistent, "recording_schema_match": "unverified"}


def iter_events(path: Path, *, services=SERVICES, max_event_bytes: int = 8 * 1024 * 1024,
                max_decompressed_bytes: int = 256 * 1024 * 1024, max_events: int = 200_000,
                cancelled: Callable[[], bool] | None = None) -> Iterator[dict]:
  """Decode an explicit, privacy-filtered service selection with framing bounds.

  File order is preserved. A per-service regression flag is diagnostic; callers
  must still refuse future/stale joins. Closure is checked separately by the
  same bounded validator, rather than inferred from this iterator reaching EOF.
  """
  if not isinstance(services, (tuple, list, set, frozenset)) or not services or any(type(name) is not str or name not in SELECTABLE_SERVICES for name in services):
    raise ValueError("unsupported rlog service selection")
  for value, name in ((max_event_bytes, "max_event_bytes"), (max_decompressed_bytes, "max_decompressed_bytes"), (max_events, "max_events")):
    _positive_int(value, name)
  _check_cancelled(cancelled)
  selected, previous = set(services), {}
  _, log = _schema()
  for blob in _frames(Path(path), max_event_bytes, max_decompressed_bytes, max_events, cancelled):
    _check_cancelled(cancelled)
    with _event(log, blob, max_event_bytes) as event:
      service, time = event.which(), int(event.logMonoTime)
      if service not in selected:
        continue
      ordered = service not in previous or time >= previous[service]
      previous[service] = max(time, previous.get(service, time))
      yield {"service": service, "mono_ns": time, "valid": bool(event.valid),
             "service_time_ordered": ordered, "data": _selected_data(service, getattr(event, service))}


def iter_observations(path: Path, *, epoch: str, max_age_ns: int, max_event_bytes: int = 8 * 1024 * 1024,
                      max_decompressed_bytes: int = 256 * 1024 * 1024, max_events: int = 200_000,
                      cancelled: Callable[[], bool] | None = None) -> Iterator[dict]:
  """Yield two observations per CarState with explicit freshness and limitations.

  Settings epochs are supplied by a caller with separate evidence. This reader
  never invents a settings history from an initData snapshot.
  """
  if not isinstance(epoch, str) or not epoch.strip():
    raise ValueError("epoch must be a nonempty string")
  for value, name in ((max_age_ns, "max_age_ns"), (max_event_bytes, "max_event_bytes"),
                      (max_decompressed_bytes, "max_decompressed_bytes"), (max_events, "max_events")):
    _positive_int(value, name)
  _check_cancelled(cancelled)
  normalizer = EventNormalizer(epoch=epoch, max_age_ns=max_age_ns)
  for event in iter_events(path, max_event_bytes=max_event_bytes, max_decompressed_bytes=max_decompressed_bytes,
                           max_events=max_events, cancelled=cancelled):
    yield from normalizer.feed(event["service"], event["mono_ns"], event["valid"], event["data"])
