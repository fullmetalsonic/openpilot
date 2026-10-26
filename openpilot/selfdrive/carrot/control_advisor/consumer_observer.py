"""Optional sampled consumer observations, isolated from analysis and Params.

OFF performs no identity, file, hash or socket probes. Enabled hooks sample a
bounded factory at most once per second and only enqueue bounded primitive
copies; a separate process-local thread performs all encoding and transport.
"""

from collections import deque
import math
from numbers import Integral, Real
import os
import re
import sys
import threading
import time


ENV_ENABLED = "CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS"
ENV_SOCKET = "CARROT_CONTROL_ADVISOR_CONSUMER_SOCKET"
DEFAULT_SOCKET = "/tmp/fms_control_advisor/consumer.sock"
MAX_EVENT_BYTES = 16 * 1024
MAX_INT = 2**63 - 1
STATES = {"loaded", "used", "latched", "bypassed", "unsupported", "unknown"}
SOURCES = {"stored", "learned", "platform_fixed", "inherited", "derived", "retained_previous", "unknown"}
PHASES = {"sampled_use", "parameter_read", "constructor_latch", "unknown"}
OBSERVER_PATH = "openpilot/selfdrive/carrot/control_advisor/consumer_observer.py"
_TORQUE = ("LateralTorqueAccelFactor", "LateralTorqueFriction", "LateralTorqueKpV", "LateralTorqueKiV", "LateralTorqueKf",
           "LateralTorqueKd", "LateralTorqueCustom", "NNFF", "NNFFLite", "SteerActuatorDelay")
_GAPS = tuple(f"TFollowGap{i}" for i in range(1, 5))
_LEAD = ("LeadAccelResponse", *(f"LeadAccelResponseTF{i}" for i in range(1, 5)))
_FOLLOW = (*_GAPS, *_LEAD, "DynamicTFollowLC", "SpeedTFFactor", "TFollowDecelBoost", "StopDistanceCarrot", "TrafficStopDistanceAdjust")
_ACCEL = tuple(f"CruiseMaxVals{i}" for i in range(7))
CONSUMER_BINDINGS = {
  "controls.vehicle_model": ("SteerRatioRate", "CustomSR"),
  "controls.lateral_target": ("SteerActuatorDelay", "LatSmoothSec"),
  "controls.torque": _TORQUE,
  "controls.longitudinal": ("LongTuningKpV", "LongTuningKiV", "LongTuningKf", "StoppingAccel"),
  "modeld.action": ("SteerActuatorDelay", "LatSmoothSec", "LongActuatorDelay", "VEgoStopping"),
  "planner.lateral": ("LatMpcPathCost", "LatMpcMotionCost", "LatMpcAccelCost", "LatMpcJerkCost", "LatMpcSteeringRateCost", "PathOffset"),
  "planner.lane_path": ("LatMpcInputOffset",),
  "planner.longitudinal": ("LongActuatorDelay", "VEgoStopping", *_ACCEL),
  "planner.carrot": (*_FOLLOW, *_ACCEL),
  "planner.long_mpc": _FOLLOW,
}
CONSUMER_PATHS = {
  "controls.vehicle_model": "openpilot/selfdrive/controls/controlsd.py",
  "controls.lateral_target": "openpilot/selfdrive/controls/controlsd.py",
  "controls.torque": "openpilot/selfdrive/controls/lib/latcontrol_torque.py",
  "controls.longitudinal": "openpilot/selfdrive/controls/lib/longcontrol.py",
  "modeld.action": "openpilot/selfdrive/modeld/modeld.py",
  "planner.lateral": "openpilot/selfdrive/controls/lib/lateral_planner.py",
  "planner.lane_path": "openpilot/selfdrive/controls/lib/lane_planner_2.py",
  "planner.longitudinal": "openpilot/selfdrive/controls/lib/longitudinal_planner.py",
  "planner.carrot": "openpilot/selfdrive/carrot/carrot_functions.py",
  "planner.long_mpc": "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py",
}
CONTEXT_KEYS = {
  "controls.vehicle_model": {"resolved_steer_ratio", "live_steer_ratio", "stiffness_factor", "manual_override"},
  "controls.lateral_target": {"active", "path", "lane_selected", "live_delay_used"},
  "controls.torque": {"active", "torque_custom_mode", "nnff", "nnff_lite", "nnff_model_selected", "nnff_jerk_lookahead_s", "derivative_effective", "torque_ff_path"},
  "controls.longitudinal": {"active", "brand", "long_control_state", "fixed_tuning"},
  "modeld.action": {"frame", "lat_action_t", "long_action_t", "lat_delay_used_s", "next_lat_delay_s", "lat_smooth_dynamic_s", "model_action_head"},
  "planner.lateral": {"lane_mode", "lanes_active", "model_valid"},
  "planner.lane_path": {"lanefull_mode", "lanes_active", "time_warp_factor"},
  "planner.longitudinal": {"mode", "driving_mode", "v_ego", "a_ego", "accel_limit_base", "accel_limit_final", "source",
                           "reset_state", "action_t", "preview_action_t", "solution_status", "group_read_complete"},
  "planner.carrot": {"personality", "mode", "driving_mode", "params_count", "v_ego", "a_ego", "group_read_complete", "group_read_basis", "lead_response_level"},
  "planner.long_mpc": {"personality", "mode", "driving_mode", "v_ego", "a_ego", "t_follow", "tf_base", "tf_mode_factor", "tf_decel_extra",
                       "tf_decel_base", "stop_distance", "traffic_adjust", "lead_response_active", "lead_response_level", "lane_change_active",
                       "reset_state", "source", "solution_status", "action_t", "preview_action_t", "lc_credit_used"},
}
CONTEXT_KEYS["planner.long_mpc"].update({"policy_observation_version", "tf_pre", "tf_input", "tf_output", "response_pre",
  "response_input", "response_source", "response_output", "lc_leads", "lc_input", "lc_output", "follow_input", "follow_output", "traffic_policy"})
CONTEXT_KEYS["controls.longitudinal"].update({"policy_observation_version", "stopping_input", "stopping_output"})
CONTEXT_KEYS["planner.longitudinal"].update({"projection_observation_version", "projection_speeds_head", "projection_speeds_tail",
  "projection_accels_head", "projection_accels_tail", "projection_times_head", "projection_times_tail", "projection_output"})
CONTEXT_ENUMS = {
  "path": {"inactive", "vw_lane", "lane", "vw_model", "model"},
  "brand": {"hyundai", "toyota", "other"},
  "long_control_state": {"off", "pid", "stopping", "starting"},
  "mode": {"acc", "blended"},
  "driving_mode": {"Eco", "Safe", "Normal", "High"},
  "source": {"lead0", "lead1", "cruise", "e2e"},
  "group_read_basis": {"group_read_complete"},
  "torque_ff_path": {"linear_standard", "unverified"},
  "response_source": {"lead0", "lead1", "cruise", "e2e"},
}
_PID_BURST_NOTICE = object()


def torque_feedforward_path(callback):
  """Identify the actual loaded base callback without importing vehicle modules."""
  try:
    module = sys.modules.get("opendbc.car.interfaces")
    base = getattr(module, "CarInterfaceBase", None)
    linear = getattr(base, "torque_from_lateral_accel_linear", None)
    return "linear_standard" if linear is not None and getattr(callback, "__func__", None) is linear else "unverified"
  except Exception:
    return "unverified"


def consumer_ids_for_key(key):
  """Static coverage only; it does not claim that any consumer acknowledged use."""
  return [consumer for consumer, keys in CONSUMER_BINDINGS.items() if key in keys]


def load_consumer_registry():
  import json
  from pathlib import Path
  return json.loads(Path(__file__).with_name("consumers.json").read_text(encoding="utf-8"))


def field(value=None, *, read_value=None, state="used", source="unknown", read_mono_ns=None):
  """Values use consumer physical units; absent captured read values stay None."""
  return {"read_value": read_value, "effective_value": value, "state": state, "source": source, "read_mono_ns": read_mono_ns}


def _integer(value, minimum=0):
  if type(value) is not int or not minimum <= value <= MAX_INT:
    raise ValueError("invalid_consumer_integer")
  return value


def _value(value):
  if value is None or type(value) is bool:
    return value
  if isinstance(value, Integral):
    result = int(value)
    if not -MAX_INT <= result <= MAX_INT:
      raise ValueError("consumer_value_out_of_range")
    return result
  if isinstance(value, Real):
    result = float(value)
    if not math.isfinite(result):
      raise ValueError("consumer_value_nonfinite")
    return result
  if isinstance(value, (list, tuple)) and len(value) <= 16:
    if any(isinstance(item, bool) or not isinstance(item, Real) for item in value):
      raise ValueError("invalid_consumer_array")
    return [_value(item) for item in value]
  raise ValueError("invalid_consumer_value")


def _payload(consumer_id, payload):
  if not isinstance(payload, dict) or set(payload) != {"fields", "context"}:
    raise ValueError("invalid_consumer_payload")
  fields, context = payload["fields"], payload["context"]
  if not isinstance(fields, dict) or not 1 <= len(fields) <= 32 or not set(fields) <= set(CONSUMER_BINDINGS[consumer_id]):
    raise ValueError("invalid_consumer_fields")
  result = {}
  for key, item in fields.items():
    if not isinstance(item, dict) or set(item) != {"read_value", "effective_value", "state", "source", "read_mono_ns"}:
      raise ValueError("invalid_consumer_field")
    if item["state"] not in STATES or item["source"] not in SOURCES:
      raise ValueError("invalid_consumer_field_state")
    stamp = None if item["read_mono_ns"] is None else _integer(item["read_mono_ns"])
    result[key] = {"read_value": _value(item["read_value"]), "effective_value": _value(item["effective_value"]),
                   "state": item["state"], "source": item["source"], "read_mono_ns": stamp}
  if not isinstance(context, dict) or len(context) > 32 or not set(context) <= CONTEXT_KEYS[consumer_id]:
    raise ValueError("invalid_consumer_context")
  copied_context = {}
  for key, value in context.items():
    if isinstance(value, str):
      if value not in CONTEXT_ENUMS.get(key, set()):
        raise ValueError("invalid_consumer_context_enum")
      copied_context[key] = value
    else:
      copied_context[key] = _value(value)
  return {"fields": result, "context": copied_context}


def validate_event(event):
  """Canonical bounded protocol copy, suitable for journal revalidation."""
  required = {"schema_version", "consumer_id", "boot_id", "pid", "process_instance_id", "consumer_instance_id", "sequence",
              "produced_mono_ns", "cycle_id", "phase", "fields", "context", "dropped_events", "source_hashes"}
  identity_fields = {"identity_version", "vehicle_identity"}
  if not isinstance(event, dict) or set(event) not in (required, required | identity_fields) or type(event["schema_version"]) is not int or event["schema_version"] != 1:
    raise ValueError("invalid_consumer_event")
  consumer = event["consumer_id"]
  if not isinstance(consumer, str) or consumer not in CONSUMER_BINDINGS:
    raise ValueError("unknown_consumer")
  if event["boot_id"] is not None and (not isinstance(event["boot_id"], str) or not re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", event["boot_id"])):
    raise ValueError("invalid_consumer_boot_identity")
  for key in ("process_instance_id", "consumer_instance_id"):
    if not isinstance(event[key], str) or not re.fullmatch(r"[a-f0-9]{32}", event[key]):
      raise ValueError("invalid_consumer_instance")
  if event["phase"] not in PHASES:
    raise ValueError("invalid_consumer_phase")
  output = {"schema_version": 1, "consumer_id": consumer, "boot_id": event["boot_id"],
            "pid": _integer(event["pid"], 1), "process_instance_id": event["process_instance_id"],
            "consumer_instance_id": event["consumer_instance_id"], "sequence": _integer(event["sequence"], 1),
            "produced_mono_ns": _integer(event["produced_mono_ns"]),
            "cycle_id": None if event["cycle_id"] is None else _integer(event["cycle_id"]), "phase": event["phase"],
            **_payload(consumer, {"fields": event["fields"], "context": event["context"]}),
            "dropped_events": _integer(event["dropped_events"])}
  if any(item["read_mono_ns"] is not None and item["read_mono_ns"] > output["produced_mono_ns"] for item in output["fields"].values()):
    raise ValueError("consumer_read_time_in_future")
  hashes = event["source_hashes"]
  allowed = {OBSERVER_PATH, CONSUMER_PATHS[consumer]}
  if not isinstance(hashes, dict) or set(hashes) != allowed or any(value is not None and (
      not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)) for value in hashes.values()):
    raise ValueError("invalid_consumer_source_hashes")
  output["source_hashes"] = dict(hashes)
  if identity_fields <= set(event):
    from .vehicle_identity import validate_identity
    if type(event["identity_version"]) is not int or event["identity_version"] != 1:
      raise ValueError("invalid_consumer_vehicle_identity")
    output.update(identity_version=1, vehicle_identity=validate_identity(event["vehicle_identity"]))
  return output


def _read_boot_id():
  try:
    with open("/proc/sys/kernel/random/boot_id", "r", encoding="ascii") as stream:
      value = stream.read(65).strip()
    return value if re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", value) else None
  except (OSError, UnicodeError):
    return None


def _private_socket_parent(path):
  from pathlib import Path
  import stat
  target = Path(path)
  if not target.is_absolute() or any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (target, *target.parents)):
    raise ValueError("invalid_consumer_socket_path")
  info = target.parent.stat()
  if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
    raise ValueError("consumer_socket_directory_not_private")
  return target


class _SocketSender:
  def __init__(self, path):
    import socket
    if not hasattr(socket, "AF_UNIX"):
      raise RuntimeError("native_consumer_transport_unavailable")
    target = _private_socket_parent(path)
    import stat
    info = target.stat()
    if not stat.S_ISSOCK(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
      raise ValueError("consumer_socket_not_owned")
    self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
      self.socket.setblocking(False)
      self.socket.connect(str(target))
    except Exception:
      self.socket.close()
      raise

  def send(self, value):
    if self.socket.send(value) != len(value):
      raise OSError("short_consumer_datagram")

  def close(self):
    self.socket.close()


class _Observer:
  def __init__(self, consumer_id, pump, clock_ns=time.monotonic_ns, car_params=None):
    self.consumer_id, self.pump, self.clock_ns = consumer_id, pump, clock_ns
    self.lock, self.next_ns, self.sequence, self.dropped = threading.Lock(), 0, 0, 0
    self.instance_id = None
    self.contended = False
    self.cp_requested, self.cp_raw, self.cp_error, self.vehicle_identity = car_params is not None, None, None, None
    self._pid_burst, self._pid_burst_ids = None, {}
    if consumer_id == "controls.torque":
      from .pid_burst import PidBurstBuffer
      self._pid_burst = PidBurstBuffer()
    if self.cp_requested:
      try:
        from .vehicle_identity import MAX_CP_BYTES
        if hasattr(car_params, "as_builder"):
          raw = car_params.as_builder().to_bytes()
        elif hasattr(car_params, "as_reader"):
          raw = car_params.as_reader().as_builder().to_bytes()
        else:
          raw = car_params.to_bytes()
        if type(raw) is bytes and 0 < len(raw) <= MAX_CP_BYTES:
          self.cp_raw = raw
        else:
          self.cp_error = "car_params_size_or_type_invalid"
      except Exception:
        self.cp_error = "car_params_snapshot_failed"


class _Pump:
  def __init__(self, socket_path=None, *, sender=None, identity=None, hash_provider=None, queue_limit=64):
    self.socket_path, self.sender = socket_path, sender
    self._sender_is_native = False
    self.identity, self.hash_provider = identity, hash_provider
    self.queue_limit = queue_limit
    self.queue, self.lock, self.stop = deque(), threading.Lock(), threading.Event()
    self.creator_pid = os.getpid()
    self.thread = threading.Thread(target=self._run, name="consumer-observation-send", daemon=True)
    self.thread.start()

  def enqueue(self, observer, item):
    if self.stop.is_set():
      return False
    if not self.lock.acquire(False):
      return False
    try:
      if len(self.queue) >= self.queue_limit:
        return False
      self.queue.append((observer, item))
      return True
    finally:
      self.lock.release()

  def _hashes(self, consumer, cache):
    paths = (OBSERVER_PATH, CONSUMER_PATHS[consumer])
    return self._path_hashes(paths, cache)

  def _path_hashes(self, paths, cache):
    import hashlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[4]
    for path in paths:
      if path in cache:
        continue
      try:
        with (root / path).open("rb") as stream:
          data = stream.read(1024 * 1024 + 1)
        cache[path] = hashlib.sha256(data).hexdigest() if len(data) <= 1024 * 1024 else None
      except OSError:
        cache[path] = None
    return {path: cache[path] for path in paths}

  def _run(self):
    import json
    import uuid
    cache = {}
    identity = self.identity
    try:
      while not self.stop.is_set():
        with self.lock:
          pending = self.queue.popleft() if self.queue else None
        if pending is None:
          self.stop.wait(0.01)
          continue
        observer, item = pending
        try:
          if identity is None:
            identity = {"boot_id": _read_boot_id(), "pid": os.getpid(), "process_instance_id": uuid.uuid4().hex}
          if observer.instance_id is None:
            observer.instance_id = uuid.uuid4().hex
          vehicle = {}
          if observer.cp_requested:
            from .vehicle_identity import describe_car_params, unavailable_identity
            if observer.vehicle_identity is None:
              observer.vehicle_identity = unavailable_identity(observer.cp_error) if observer.cp_error else describe_car_params(observer.cp_raw)
              observer.cp_raw = None
            vehicle = {"identity_version": 1, "vehicle_identity": observer.vehicle_identity}
          burst = item is _PID_BURST_NOTICE
          if burst:
            from .pid_burst import SOURCE_PATHS, dependency_digest, validate_chunk
            from .vehicle_identity import unavailable_identity
            body = observer._pid_burst.drain()
            if body is None:
              continue
            key = body["round_id"], body["block_id"]
            # Only current/next fixed-size rounds are cached; old IDs are not reused.
            observer._pid_burst_ids = {entry: value for entry, value in observer._pid_burst_ids.items() if entry[0] >= key[0] - 1}
            if key not in observer._pid_burst_ids:
              observer._pid_burst_ids[key] = uuid.uuid4().hex
            burst_id = observer._pid_burst_ids[key]
            hashes = self.hash_provider("pid_burst") if self.hash_provider else self._path_hashes(SOURCE_PATHS, cache)
            descriptor = vehicle.get("vehicle_identity", unavailable_identity())
            event = validate_chunk({"schema_version": 1, "event_kind": "pid_burst_chunk", "consumer_id": "controls.torque",
                                    **identity, "consumer_instance_id": observer.instance_id, "burst_id": burst_id,
                                    "identity_version": 1, "vehicle_identity": descriptor, "source_hashes": hashes,
                                    "dependency_digest": dependency_digest(body["configuration"], descriptor, hashes),
                                    "produced_mono_ns": observer.clock_ns(), **body})
          else:
            event = validate_event({"schema_version": 1, "consumer_id": observer.consumer_id,
                                    **identity, "consumer_instance_id": observer.instance_id, **item,
                                    **vehicle,
                                    "source_hashes": self.hash_provider(observer.consumer_id) if self.hash_provider else self._hashes(observer.consumer_id, cache)})
          encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
          if len(encoded) > MAX_EVENT_BYTES:
            raise ValueError("consumer_datagram_limit")
          if self.sender is None:
            self.sender = _SocketSender(self.socket_path)
            self._sender_is_native = True
          self.sender.send(encoded)
        except Exception as exc:
          if isinstance(exc, OSError) and self._sender_is_native and self.sender is not None:
            try:
              self.sender.close()
            except Exception:
              pass
            self.sender = None
            self._sender_is_native = False
          if item is _PID_BURST_NOTICE:
            observer._pid_burst.mark_dropped("burst_transport_failed")
          else:
            with observer.lock:
              observer.dropped += 1
    finally:
      if self.sender is not None:
        try:
          self.sender.close()
        except Exception:
          pass

  def close(self, timeout=0.2):
    self.stop.set()
    self.thread.join(timeout=max(0.0, timeout))


_pump = None
_pump_lock = threading.Lock()


def _native_runtime():
  return sys.platform.startswith("linux") and os.path.exists("/TICI")


def _observation_config(environment):
  flag = environment.get(ENV_ENABLED)
  # Explicit OFF returns before native detection or any socket/path probe.
  if flag not in (None, "1"):
    return False, None
  native = _native_runtime() if flag is None else False
  if flag is None and not native:
    return False, None
  path = environment.get(ENV_SOCKET, "")
  if not path and (native or _native_runtime()):
    path = DEFAULT_SOCKET
  return True, path


def create_observer(consumer_id, *, car_params=None):
  """Never raise into constructors; native defaults and explicit OFF are isolated."""
  try:
    enabled, path = _observation_config(os.environ)
    if not enabled:
      return None
    if consumer_id not in CONSUMER_BINDINGS or not isinstance(path, str) or not os.path.isabs(path):
      return None
    import socket
    if not hasattr(socket, "AF_UNIX"):
      return None
    global _pump
    with _pump_lock:
      if _pump is None or _pump.creator_pid != os.getpid() or not _pump.thread.is_alive() or _pump.socket_path != path:
        if _pump is not None:
          _pump.close(timeout=0)
        _pump = _Pump(path)
      return _Observer(consumer_id, _pump, car_params=car_params)
  except Exception:
    return None


def observe(observer, payload_factory, *, cycle_id=None, phase="sampled_use"):
  """No hot-path I/O, JSON, waiting for queue space or propagated failures."""
  if observer is None:
    return
  acquired = False
  try:
    acquired = observer.lock.acquire(False)
    if not acquired:
      # Coalesced contention is a visible lower-bound loss marker, not a count
      # of every control tick. Do not wait for another factory or sender.
      observer.contended = True
      return
    if observer.contended:
      observer.dropped += 1
      observer.contended = False
    now = observer.clock_ns()
    if now < observer.next_ns:
      return
    observer.next_ns = now + 1_000_000_000
    observer.sequence += 1
    if phase not in PHASES or (cycle_id is not None and (type(cycle_id) is not int or not 0 <= cycle_id <= MAX_INT)):
      raise ValueError("invalid_consumer_sample_context")
    copied = _payload(observer.consumer_id, payload_factory())
    item = {"sequence": observer.sequence, "produced_mono_ns": observer.clock_ns(), "cycle_id": cycle_id,
            "phase": phase, **copied, "dropped_events": observer.dropped}
    if not observer.pump.enqueue(observer, item):
      observer.dropped += 1
  except Exception:
    if acquired:
      observer.dropped += 1
  finally:
    if acquired:
      try:
        observer.lock.release()
      except Exception:
        pass


def observe_pid_burst(observer, values, mode, *, cycle_id):
  """No serialization, identity probes or waiting in the control thread."""
  if observer is None:
    return
  try:
    buffer = observer._pid_burst
    if buffer is not None and buffer.capture(cycle_id, observer.clock_ns(), values, mode):
      if not observer.pump.enqueue(observer, _PID_BURST_NOTICE):
        buffer.mark_dropped("burst_notice_queue_full")
  except Exception:
    # A diagnostic failure never propagates into a control calculation.
    try:
      if getattr(observer, "_pid_burst", None) is not None:
        observer._pid_burst.mark_dropped("burst_capture_failed")
    except Exception:
      pass
