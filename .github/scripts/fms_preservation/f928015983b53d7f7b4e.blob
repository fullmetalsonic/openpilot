"""Bounded PID-cycle observations; never a controller or a setting writer.

Only ``PidBurstBuffer.capture`` runs in a control thread. Protocol encoding,
identity, hashing, storage and reassembly belong to the existing background path.
"""
from collections import deque
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import threading


COLUMN_SCHEMA_ID = "torque_pid_cycle_v1"
COLUMNS = (
  "desired_lateral_accel", "actual_lateral_accel", "setpoint", "measurement", "low_speed_factor", "v_ego", "a_ego",
  "roll_compensation", "lateral_accel_deadzone", "pid_error", "feedforward", "integral_before", "p_after", "i_after",
  "f_after", "output_torque", "effective_kp", "effective_ki", "effective_kf", "factor", "friction", "freeze_integrator",
  "steer_limited_by_controls", "curvature_limited", "steering_pressed",
)
STEP_NS = 10_000_000
BLOCK_NS = 10_000_000_000
BLOCK_SPACING_NS = 30_000_000_000
ROUND_NS = 180_000_000_000
BLOCKS_PER_ROUND = 6
MAX_BLOCK_ROWS = 1000
MAX_ROUND_ROWS = 6000
MAX_BUFFER_ROWS = 200
MAX_CHUNK_ROWS = 8
MAX_CHUNK_BYTES = 8 * 1024
MAX_ROUND_BYTES = 8 * 1024 * 1024
OUTPUT_SIGN = "positive_pid_domain"
SOURCE_PATHS = (
  "openpilot/selfdrive/carrot/control_advisor/pid_burst.py",
  "openpilot/selfdrive/carrot/control_advisor/consumer_observer.py",
  "openpilot/selfdrive/controls/lib/latcontrol_torque.py",
  "openpilot/common/pid.py",
  "opendbc_repo/opendbc/car/interfaces.py",
  "opendbc_repo/opendbc/car/__init__.py",
  "opendbc_repo/opendbc/car/hyundai/carcontroller.py",
  "opendbc_repo/opendbc/car/hyundai/values.py",
  "openpilot/selfdrive/car/card.py",
)
_MAX_INT = 2**63 - 1
_SHA = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[a-f0-9]{32}")
_BOOT = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")
_RELATIVE = re.compile(r"pid_bursts/[a-f0-9]{32}-[a-f0-9]{32}/[0-9]{6,19}-[0-5]/[0-9]{6}\.json")
_CONFIG_KEYS = {"torque_ff_path", "torque_custom_mode", "nnff", "nnff_lite", "use_steering_angle", "pos_limit", "neg_limit",
                "output_sign", "pid_source", "torque_source", "effective_kp", "effective_ki", "effective_kf", "factor", "friction"}
_FIELDS = {"schema_version", "event_kind", "consumer_id", "boot_id", "pid", "process_instance_id", "consumer_instance_id",
           "burst_id", "produced_mono_ns", "source_hashes", "identity_version", "vehicle_identity", "dependency_digest",
           "round_id", "block_id", "chunk_sequence", "column_schema_id", "first_frame", "first_mono_ns", "step_ns",
           "row_offset", "rows", "configuration", "terminal", "produced_count", "dropped_count", "reason_codes"}
_REFERENCE_KEYS = {"schema_version", "path", "sha256", "bytes", "round_id", "block_id", "burst_id", "chunk_sequence",
                   "frame_start", "frame_end", "mono_start_ns", "mono_end_ns", "terminal", "dependency_digest"}
HEADER_FIELDS = ("boot_id", "pid", "process_instance_id", "consumer_instance_id", "column_schema_id", "step_ns",
                 "configuration", "vehicle_identity", "source_hashes", "dependency_digest")


def canonical(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _integer(value, minimum=0, maximum=_MAX_INT):
  if type(value) is not int or not minimum <= value <= maximum:
    raise ValueError("invalid_pid_burst_integer")
  return value


def _number(value):
  if type(value) not in (int, float) or not math.isfinite(value):
    raise ValueError("invalid_pid_burst_number")
  return value


def _configuration(value):
  if type(value) is not dict or set(value) != _CONFIG_KEYS:
    raise ValueError("invalid_pid_burst_configuration")
  result = dict(value)
  if value["torque_ff_path"] not in ("linear_standard", "unverified") or value["output_sign"] != OUTPUT_SIGN:
    raise ValueError("invalid_pid_burst_mapping")
  for name in ("pid_source", "torque_source"):
    if value[name] not in ("stored", "inherited", "learned", "unknown"):
      raise ValueError("invalid_pid_burst_source")
  _integer(value["torque_custom_mode"], 0, 100)
  for name in ("nnff", "nnff_lite", "use_steering_angle"):
    if type(value[name]) is not bool:
      raise ValueError("invalid_pid_burst_mode")
  for name in ("pos_limit", "neg_limit", "effective_kp", "effective_ki", "effective_kf", "factor", "friction"):
    _number(value[name])
  if value["pos_limit"] <= 0 or value["neg_limit"] >= 0 or value["factor"] <= 0:
    raise ValueError("invalid_pid_burst_limits")
  return result


def dependency_digest(configuration, vehicle_identity, source_hashes):
  return hashlib.sha256(canonical({"configuration": configuration, "vehicle_identity": vehicle_identity,
                                   "source_hashes": source_hashes})).hexdigest()


def validate_header(value, *, require_verified=False):
  """Shared dense-pack header validation without fabricating a wire chunk."""
  from .vehicle_identity import validate_identity
  if type(value) is not dict or set(value) != set(HEADER_FIELDS):
    raise ValueError("invalid_pid_burst_header")
  if value["boot_id"] is not None and (type(value["boot_id"]) is not str or _BOOT.fullmatch(value["boot_id"]) is None):
    raise ValueError("invalid_pid_burst_boot")
  _integer(value["pid"], 1)
  for name in ("process_instance_id", "consumer_instance_id"):
    if type(value[name]) is not str or _ID.fullmatch(value[name]) is None:
      raise ValueError("invalid_pid_burst_identity")
  if value["column_schema_id"] != COLUMN_SCHEMA_ID or type(value["step_ns"]) is not int or value["step_ns"] != STEP_NS:
    raise ValueError("invalid_pid_burst_schema")
  configuration = _configuration(value["configuration"])
  identity = validate_identity(value["vehicle_identity"])
  hashes = value["source_hashes"]
  if type(hashes) is not dict or set(hashes) != set(SOURCE_PATHS) or any(
      item is not None and (type(item) is not str or _SHA.fullmatch(item) is None) for item in hashes.values()):
    raise ValueError("invalid_pid_burst_hashes")
  if value["dependency_digest"] != dependency_digest(configuration, identity, hashes):
    raise ValueError("pid_burst_dependency_digest_mismatch")
  if require_verified and (value["boot_id"] is None or identity["state"] != "observed" or any(item is None for item in hashes.values())
      or configuration["pid_source"] != "stored" or configuration["torque_source"] != "stored"
      or configuration["torque_ff_path"] != "linear_standard" or configuration["torque_custom_mode"] <= 0
      or configuration["nnff"] or configuration["nnff_lite"]):
    raise ValueError("pid_burst_header_unverified")
  return {**value, "configuration": configuration, "vehicle_identity": identity, "source_hashes": dict(hashes)}


def validate_chunk(event):
  """Strict wire copy. This validates observations, not a parameter-use receipt."""
  from .vehicle_identity import validate_identity
  if type(event) is not dict or set(event) != _FIELDS or type(event["schema_version"]) is not int or event["schema_version"] != 1:
    raise ValueError("invalid_pid_burst_event")
  if event["event_kind"] != "pid_burst_chunk" or event["consumer_id"] != "controls.torque" or event["column_schema_id"] != COLUMN_SCHEMA_ID:
    raise ValueError("invalid_pid_burst_schema")
  if event["boot_id"] is not None and (type(event["boot_id"]) is not str or _BOOT.fullmatch(event["boot_id"]) is None):
    raise ValueError("invalid_pid_burst_boot")
  for name in ("process_instance_id", "consumer_instance_id", "burst_id"):
    if type(event[name]) is not str or _ID.fullmatch(event[name]) is None:
      raise ValueError("invalid_pid_burst_identity")
  _integer(event["pid"], 1)
  for name in ("round_id", "chunk_sequence", "first_frame", "first_mono_ns", "produced_mono_ns", "row_offset", "produced_count", "dropped_count"):
    _integer(event[name])
  if event["chunk_sequence"] > (MAX_BLOCK_ROWS + MAX_CHUNK_ROWS - 1) // MAX_CHUNK_ROWS + 1:
    raise ValueError("pid_burst_chunk_budget")
  _integer(event["block_id"], 0, BLOCKS_PER_ROUND - 1)
  if event["step_ns"] != STEP_NS or type(event["step_ns"]) is not int:
    raise ValueError("invalid_pid_burst_step")
  if event["produced_count"] > MAX_BLOCK_ROWS or event["row_offset"] > event["produced_count"]:
    raise ValueError("pid_burst_row_budget")
  if type(event["terminal"]) is not bool:
    raise ValueError("invalid_pid_burst_terminal")
  reasons = event["reason_codes"]
  if type(reasons) is not list or len(reasons) > 8 or any(
      type(reason) is not str or re.fullmatch(r"[a-z0-9_]{1,96}", reason) is None for reason in reasons) or len(set(reasons)) != len(reasons):
    raise ValueError("invalid_pid_burst_reason")
  hashes = event["source_hashes"]
  if type(hashes) is not dict or set(hashes) != set(SOURCE_PATHS) or any(
      value is not None and (type(value) is not str or _SHA.fullmatch(value) is None) for value in hashes.values()):
    raise ValueError("invalid_pid_burst_hashes")
  if type(event["identity_version"]) is not int or event["identity_version"] != 1:
    raise ValueError("invalid_pid_burst_vehicle")
  identity = validate_identity(event["vehicle_identity"])
  configuration = _configuration(event["configuration"])
  if event["dependency_digest"] != dependency_digest(configuration, identity, hashes):
    raise ValueError("pid_burst_dependency_digest_mismatch")
  rows = event["rows"]
  if type(rows) is not list or len(rows) > MAX_CHUNK_ROWS or (not rows and not event["terminal"]):
    raise ValueError("invalid_pid_burst_rows")
  previous = None
  copied = []
  signature = tuple(configuration[name] for name in ("effective_kp", "effective_ki", "effective_kf", "factor", "friction"))
  for row in rows:
    if type(row) is not list or len(row) != len(COLUMNS) + 2:
      raise ValueError("invalid_pid_burst_row")
    frame, stamp = _integer(row[0]), _integer(row[1])
    if previous is None and (frame != 0 or stamp != 0) or previous is not None and (frame != previous[0] + 1 or stamp <= previous[1]):
      raise ValueError("pid_burst_row_order")
    values = [_number(value) for value in row[2:]]
    if any(type(values[index]) is not int or values[index] not in (0, 1) for index in range(21, 25)):
      raise ValueError("invalid_pid_burst_flag")
    if tuple(values[16:21]) != signature:
      raise ValueError("pid_burst_configuration_changed")
    copied.append([frame, stamp, *values])
    previous = frame, stamp
  if event["row_offset"] + len(copied) > event["produced_count"] or event["first_mono_ns"] > event["produced_mono_ns"]:
    raise ValueError("pid_burst_counter_or_time")
  if copied and event["first_mono_ns"] + copied[-1][1] > event["produced_mono_ns"]:
    raise ValueError("pid_burst_future_row")
  result = {**event, "rows": copied, "configuration": configuration, "vehicle_identity": identity,
            "source_hashes": dict(hashes), "reason_codes": list(reasons)}
  if len(canonical(result)) > MAX_CHUNK_BYTES:
    raise ValueError("pid_burst_chunk_byte_budget")
  return result


class PidBurstBuffer:
  """A nonblocking producer buffer; all wire work stays in ``drain``'s caller."""
  def __init__(self):
    self.lock = threading.Lock()
    self.queue = deque()
    self.origin_ns = None
    self.current = None
    self.buffered_rows = 0
    self.contended = False

  def _close(self, reason=None):
    block = self.current
    if block is None or block["closed"]:
      return False
    if reason is not None:
      block["reasons"].add(reason)
      block["dropped"] += 1
    block["closed"] = True
    # A stopped pump must not accumulate one terminal object per later round.
    if len(self.queue) < MAX_BUFFER_ROWS + 1:
      self.queue.append((block, None))
    return True

  def mark_dropped(self, reason="producer_queue_full"):
    if not self.lock.acquire(False):
      self.contended = True
      return
    try:
      self._close(reason)
    finally:
      self.lock.release()

  def capture(self, frame, mono_ns, values, mode):
    """Copy one fixed primitive row. A true return requests one pump notice."""
    if not self.lock.acquire(False):
      self.contended = True
      return False
    try:
      notify = self._close("producer_lock_contended") if self.contended else False
      self.contended = False
      if values is None:
        return self._close("inactive_control") or notify
      # mode=(callback,custom,nnff,nnff_lite,use_angle,pos,neg,pid_source,torque_source).
      if len(values) != len(COLUMNS) or len(mode) != 9:
        return self._close("invalid_producer_shape") or notify
      supported = mode[0] == "linear_standard" and mode[1] > 0 and not mode[2] and not mode[3] and mode[7:] == ("stored", "stored")
      if not supported:
        return self._close("producer_mapping_unavailable") or notify
      if self.origin_ns is None:
        self.origin_ns = mono_ns
      elapsed = mono_ns - self.origin_ns
      if elapsed < 0:
        return self._close("producer_time_regressed") or notify
      if self.current is not None and not self.current["closed"] and self.current["last_frame"] is not None and (
          frame != self.current["last_frame"] + 1 or mono_ns <= self.current["last_ns"]):
        notify = self._close("producer_cycle_gap") or notify
      round_id, phase = divmod(elapsed, ROUND_NS)
      block_id, offset = divmod(phase, BLOCK_SPACING_NS)
      key = round_id, block_id
      if self.current is not None and self.current["key"] != key:
        notify = self._close() or notify
      if offset >= BLOCK_NS:
        return self._close() or notify
      signature = mode + tuple(values[16:21])
      if self.current is None or self.current["key"] != key:
        self.current = {"key": key, "mode": mode, "signature": signature, "produced": 0, "drained": 0,
                        "dropped": 0, "reasons": set(), "closed": False, "sequence": 0,
                        "last_frame": None, "last_ns": None}
      block = self.current
      if block["closed"]:
        return notify
      if signature != block["signature"]:
        return self._close("producer_dependency_changed") or notify
      if block["last_frame"] is not None and (frame != block["last_frame"] + 1 or mono_ns <= block["last_ns"]):
        return self._close("producer_cycle_gap") or notify
      if self.buffered_rows >= MAX_BUFFER_ROWS:
        return self._close("producer_buffer_full") or notify
      self.queue.append((block, (frame, mono_ns, tuple(values))))
      self.buffered_rows += 1
      block["produced"] += 1
      block["last_frame"], block["last_ns"] = frame, mono_ns
      if block["produced"] == MAX_BLOCK_ROWS:
        return self._close() or notify
      return block["produced"] % MAX_CHUNK_ROWS == 0 or notify
    finally:
      self.lock.release()

  def drain(self):
    """Background pump only. Drain at most eight rows without merging blocks."""
    with self.lock:
      if not self.queue:
        return None
      block = self.queue[0][0]
      rows, terminal = [], False
      while self.queue and self.queue[0][0] is block and len(rows) < MAX_CHUNK_ROWS:
        _, row = self.queue.popleft()
        if row is None:
          terminal = True
          break
        rows.append(row)
        self.buffered_rows -= 1
      if self.queue and self.queue[0][0] is block and self.queue[0][1] is None:
        self.queue.popleft()
        terminal = True
      first_frame = rows[0][0] if rows else block["last_frame"]
      first_ns = rows[0][1] if rows else block["last_ns"]
      mode, signature = block["mode"], block["signature"]
      result = {"round_id": block["key"][0], "block_id": block["key"][1], "chunk_sequence": block["sequence"],
                "column_schema_id": COLUMN_SCHEMA_ID, "first_frame": first_frame, "first_mono_ns": first_ns,
                "step_ns": STEP_NS, "row_offset": block["drained"],
                "terminal": terminal, "produced_count": block["produced"], "dropped_count": block["dropped"],
                "reason_codes": tuple(block["reasons"])}
      block["drained"] += len(rows)
      block["sequence"] += 1
    # Construct payloads outside the producer's lock to minimize lost cycles.
    result["configuration"] = {"torque_ff_path": mode[0], "torque_custom_mode": mode[1], "nnff": mode[2], "nnff_lite": mode[3],
      "use_steering_angle": mode[4], "pos_limit": mode[5], "neg_limit": mode[6], "pid_source": mode[7], "torque_source": mode[8],
      "output_sign": OUTPUT_SIGN, **dict(zip(("effective_kp", "effective_ki", "effective_kf", "factor", "friction"), signature[-5:]))}
    result["rows"] = [[frame - first_frame, stamp - first_ns, *values] for frame, stamp, values in rows]
    result["reason_codes"] = sorted(result["reason_codes"])
    return result


def assemble_blocks(events, *, allow_subtrace=False):
  """Reassemble observations; no settings revision or application ACK is inferred."""
  groups = {}
  for item in events:
    reference = item.get("reference") if type(item) is dict and "event" in item else None
    event = validate_chunk(item["event"] if reference is not None else item)
    identity = tuple(event[name] for name in ("boot_id", "pid", "process_instance_id", "consumer_instance_id", "burst_id", "round_id", "block_id"))
    groups.setdefault(identity, []).append((event, reference))
  output = []
  for identity, chunks in groups.items():
    first = chunks[0][0]
    configuration = first["configuration"]
    header = validate_header({name: deepcopy(first[name]) for name in HEADER_FIELDS})
    reasons, rows, references = set(), [], []
    missing_prefix = first["chunk_sequence"] != 0 or first["row_offset"] != 0
    if missing_prefix and not allow_subtrace:
      reasons.add("pid_burst_prefix_missing")
    if first["boot_id"] is None or first["vehicle_identity"]["state"] != "observed" or any(value is None for value in first["source_hashes"].values()):
      reasons.add("pid_burst_source_unverified")
    if configuration["torque_ff_path"] != "linear_standard" or configuration["nnff"] or configuration["nnff_lite"] or configuration["torque_custom_mode"] <= 0:
      reasons.add("pid_burst_mapping_unavailable")
    if configuration["pid_source"] != "stored" or configuration["torque_source"] != "stored":
      reasons.add("pid_burst_effective_source_unverified")
    sequence, offset = first["chunk_sequence"], first["row_offset"]
    terminal = False
    last_frame = last_ns = None
    for event, reference in chunks:
      if event["dependency_digest"] != first["dependency_digest"] or any(event[name] != first[name] for name in header):
        reasons.add("pid_burst_header_changed")
      if terminal or event["chunk_sequence"] != sequence or event["row_offset"] != offset:
        reasons.add("pid_burst_chunk_gap_or_order")
      sequence = event["chunk_sequence"] + 1
      offset = event["row_offset"] + len(event["rows"])
      reasons.update(event["reason_codes"])
      if event["dropped_count"]:
        reasons.add("pid_burst_recorded_drop")
      for wire in event["rows"]:
        frame, stamp = event["first_frame"] + wire[0], event["first_mono_ns"] + wire[1]
        if last_frame is not None and (frame != last_frame + 1 or stamp <= last_ns):
          reasons.add("pid_burst_cycle_gap_or_order")
        rows.append({"pid_frame": frame, "mono_ns": stamp, **dict(zip(COLUMNS, wire[2:]))})
        last_frame, last_ns = frame, stamp
      if reference is not None:
        references.append(deepcopy(reference))
      terminal = event["terminal"]
    final = chunks[-1][0]
    if not terminal:
      reasons.add("pid_burst_terminal_missing")
    if final["produced_count"] != offset or not rows:
      reasons.add("pid_burst_count_mismatch")
    if len(rows) > MAX_BLOCK_ROWS or rows and rows[-1]["mono_ns"] - rows[0]["mono_ns"] > BLOCK_NS + STEP_NS:
      reasons.add("pid_burst_block_budget")
    output.append({"state": "complete" if not reasons else "invalid", "burst_id": identity[4], "round_id": identity[5], "block_id": identity[6],
                   "header": header, "rows": rows, "chunk_refs": references, "reason_codes": sorted(reasons),
                   "missing_prefix": missing_prefix, "source_row_count": final["produced_count"],
                   "consumer_application_ack": False, "parameter_application_ack": False, "causal_improvement_proven": False})
  return output


def validate_reference(value):
  if type(value) is not dict or set(value) != _REFERENCE_KEYS or type(value["schema_version"]) is not int or value["schema_version"] != 1:
    raise ValueError("invalid_pid_burst_reference")
  if type(value["path"]) is not str or _RELATIVE.fullmatch(value["path"]) is None:
    raise ValueError("invalid_pid_burst_reference_path")
  for name in ("sha256", "dependency_digest"):
    if type(value[name]) is not str or _SHA.fullmatch(value[name]) is None:
      raise ValueError("invalid_pid_burst_reference_hash")
  if type(value["burst_id"]) is not str or _ID.fullmatch(value["burst_id"]) is None or type(value["terminal"]) is not bool:
    raise ValueError("invalid_pid_burst_reference_identity")
  for name in ("round_id", "chunk_sequence", "frame_start", "frame_end", "mono_start_ns", "mono_end_ns"):
    _integer(value[name])
  _integer(value["block_id"], 0, BLOCKS_PER_ROUND - 1)
  _integer(value["bytes"], 1, MAX_CHUNK_BYTES)
  if value["frame_start"] > value["frame_end"] or value["mono_start_ns"] > value["mono_end_ns"]:
    raise ValueError("invalid_pid_burst_reference_range")
  return dict(value)


def _reference(event, encoded):
  last = event["rows"][-1] if event["rows"] else (0, 0)
  return {"schema_version": 1,
          "path": f"pid_bursts/{event['process_instance_id']}-{event['consumer_instance_id']}/{event['round_id']:06d}-{event['block_id']}/{event['chunk_sequence']:06d}.json",
          "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded),
          **{name: event[name] for name in ("round_id", "block_id", "burst_id", "chunk_sequence", "terminal", "dependency_digest")},
          "frame_start": event["first_frame"], "frame_end": event["first_frame"] + last[0],
          "mono_start_ns": event["first_mono_ns"], "mono_end_ns": event["first_mono_ns"] + last[1]}


def _safe_path(root, relative):
  current = Path(root)
  if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (current, *current.parents)):
    raise ValueError("pid_burst_storage_link")
  for part in relative.split("/"):
    current = current / part
    if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
      raise ValueError("pid_burst_storage_link")
  return current


class PidBurstStore:
  """Journal-owner-only immutable files with an independent per-round budget."""
  def __init__(self, session_directory):
    self.root = Path(session_directory)
    self.round_key, self.round_bytes = None, 0
    self.retired_streams = set()

  def append(self, event):
    import os
    from .evidence import _sync_directory
    event = validate_chunk(event)
    encoded = canonical(event)
    reference = _reference(event, encoded)
    key = event["process_instance_id"], event["consumer_instance_id"], event["round_id"]
    if key[:2] in self.retired_streams:
      raise ValueError("pid_burst_storage_retired_instance")
    if self.round_key is not None and key[:2] != self.round_key[:2]:
      if len(self.retired_streams) >= 8:
        raise ValueError("pid_burst_storage_restart_budget")
      self.retired_streams.add(self.round_key[:2])
    if self.round_key is not None and key[:2] == self.round_key[:2] and key[2] < self.round_key[2]:
      raise ValueError("pid_burst_storage_round_regressed")
    if key != self.round_key:
      self.round_key, self.round_bytes = key, 0
    # Reserve the bounded small journal envelope too, not only the chunk bytes.
    cost = len(encoded) + len(canonical({"type": "pid_burst_chunk", "reference": reference, "recorded_mono_ns": _MAX_INT})) + 512
    if self.round_bytes + cost > MAX_ROUND_BYTES:
      raise ValueError("pid_burst_round_storage_budget")
    path = _safe_path(self.root, reference["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
      stream.write(encoded)
      stream.flush()
      os.fsync(stream.fileno())
    _sync_directory(path.parent)
    self.round_bytes += cost
    return reference


def load_chunk(session_directory, reference):
  reference = validate_reference(reference)
  path = _safe_path(session_directory, reference["path"])
  with path.open("rb") as stream:
    encoded = stream.read(MAX_CHUNK_BYTES + 1)
  if len(encoded) != reference["bytes"] or hashlib.sha256(encoded).hexdigest() != reference["sha256"]:
    raise ValueError("pid_burst_chunk_hash_mismatch")
  from .consumer_collector import _unique
  event = validate_chunk(json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique,
                                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_pid_burst_json"))))
  if _reference(event, encoded) != reference:
    raise ValueError("pid_burst_chunk_reference_mismatch")
  return event
