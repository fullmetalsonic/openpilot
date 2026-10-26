"""One-cycle policy comparisons from exact pre-state, never plant prediction."""
from copy import deepcopy
from functools import lru_cache
import math
from numbers import Real


POLICY_METHOD_ID = "fms_policy_state_conditioned_one_step_v1"
POLICY_SCOPE = "one_cycle_policy_effect"
PROJECTION_METHOD_ID = "fms_frozen_longitudinal_plan_projection_v1"
PROJECTION_SCOPE = "frozen_plan_projection"
SOURCE_SHA256 = "d3b8b78a73366eda592d1d8b58bce9b35b7b7ccf9aca5b2e7d0682ed4ac8a788"
POLICY_DEFAULTS = {
  "TFollowGap1": 110, "TFollowGap2": 120, "TFollowGap3": 140, "TFollowGap4": 160,
  "SpeedTFFactor": 10, "TFollowDecelBoost": 0, "LeadAccelResponse": 0,
  "LeadAccelResponseTF1": -1, "LeadAccelResponseTF2": -1, "LeadAccelResponseTF3": -1,
  "LeadAccelResponseTF4": -1, "DynamicTFollowLC": 100, "StopDistanceCarrot": 550,
  "TrafficStopDistanceAdjust": -150, "StoppingAccel": -50,
}
PROJECTION_DEFAULTS = {"LongActuatorDelay": 20, "VEgoStopping": 50}
KEY_FAMILIES = {**dict.fromkeys(tuple(POLICY_DEFAULTS)[:6], "tf"),
                **dict.fromkeys(tuple(POLICY_DEFAULTS)[6:11], "response"),
                "DynamicTFollowLC": "lane_change", "StopDistanceCarrot": "follow_distance",
                "TrafficStopDistanceAdjust": "traffic_offset", "StoppingAccel": "stopping"}
POLICY_KEYS = tuple(KEY_FAMILIES)
_SOURCES = ("lead0", "lead1", "cruise", "e2e")
_UNITS = {"tf": "s", "response": "1", "lane_change": "m", "follow_distance": "m",
          "traffic_offset": "m", "stopping": "m/s^2"}


def policy_family_for_key(key):
  return KEY_FAMILIES.get(key)


def _capability(method, backend, unit, scope):
  return {"method_id": method, "backend": backend, "required_signals": {}, "output_unit": unit,
          "prediction_kind": "frozen_input_replay", "reason_codes": [], "scope": scope,
          "trajectory_prediction": False, "plant_improvement_proven": False}


def policy_capabilities():
  return {key: _capability(POLICY_METHOD_ID, "state_conditioned_source_policy", _UNITS[family], POLICY_SCOPE)
          for key, family in KEY_FAMILIES.items()}


def projection_capabilities():
  return {key: _capability(PROJECTION_METHOD_ID, "frozen_source_projection",
                          "bool" if key == "VEgoStopping" else "m/s^2", PROJECTION_SCOPE)
          for key in PROJECTION_DEFAULTS}


def _reference(kind, defaults):
  return {"type": kind, "preset_id": "fms_policy_defaults_v1" if defaults is POLICY_DEFAULTS else "fms_plan_defaults_v1",
          "version": 1, "settings_raw": dict(defaults), "source_sha256": SOURCE_SHA256}


def preset_reference():
  return _reference("fms_policy_point_preset", POLICY_DEFAULTS)


def projection_preset_reference():
  return _reference("fms_frozen_plan_preset", PROJECTION_DEFAULTS)


def _validate_reference(value, expected):
  if (type(value) is not dict or set(value) != set(expected) or type(value.get("version")) is not int
      or type(value.get("settings_raw")) is not dict
      or any(type(raw) is not int for raw in value["settings_raw"].values()) or value != expected):
    raise ValueError("invalid_fixed_policy_preset")
  return deepcopy(value)


def validate_preset_reference(value):
  return _validate_reference(value, preset_reference())


def validate_projection_preset_reference(value):
  return _validate_reference(value, projection_preset_reference())


def _number(value):
  if isinstance(value, bool) or not isinstance(value, Real):
    raise ValueError("invalid_policy_number")
  try:
    value = float(value)
  except (ValueError, OverflowError) as exc:
    raise ValueError("invalid_policy_number") from exc
  if not math.isfinite(value):
    raise ValueError("invalid_policy_number")
  return value


def _array(value, lengths):
  if type(value) not in (list, tuple) or len(value) not in lengths:
    raise ValueError("invalid_policy_array")
  return [_number(item) for item in value]


def _integer(value, minimum, maximum):
  value = _number(value)
  if not value.is_integer() or not minimum <= value <= maximum:
    raise ValueError("invalid_policy_selector")
  return int(value)


def validate_policy_point(point):
  if (type(point) is not dict or set(point) != {"schema_version", "family", "pre", "inputs", "observed_output"}
      or type(point["schema_version"]) is not int or point["schema_version"] != 1):
    raise ValueError("invalid_policy_point")
  family = point["family"]
  if family not in _UNITS:
    raise ValueError("invalid_policy_family")
  sizes = {"tf": (4, 5), "response": ((1, 3), 12), "lane_change": (12, 10),
           "follow_distance": (0, 5), "traffic_offset": (0, 2), "stopping": (0, 10)}
  pre_size, input_size = sizes[family]
  pre = None if point["pre"] is None and family in {"tf", "lane_change"} else _array(
    point["pre"], pre_size if isinstance(pre_size, tuple) else (pre_size,))
  values = _array(point["inputs"], (input_size,))
  if family == "tf":
    _integer(values[0], 0, 3)
    if values[1] < 0 or not 0 < values[3] <= 3 or values[4] != .05:
      raise ValueError("invalid_policy_tf_inputs")
    if pre is not None and (not 0 < pre[0] <= 3 or min(pre[1:]) < 0):
      raise ValueError("invalid_policy_tf_state")
  elif family == "response":
    for index in (0, 1):
      _integer(values[index], 0, 1)
    _integer(values[7], -1, 2**53 - 1)
    _integer(values[9], 0, 3)
    _integer(values[10], 1, 4)
    _integer(values[11], 0, 3)
    if not 0 < values[8] <= .2 or not 0 <= pre[0] <= 1:
      raise ValueError("invalid_policy_response_state")
    if len(pre) == 1:
      if pre[0] != 0:
        raise ValueError("invalid_policy_response_key")
    else:
      _integer(pre[1], 0, 5)
      _integer(pre[2], -1, 2**53 - 1)
  elif family == "lane_change":
    _integer(values[0], 0, 1)
    _integer(values[8], 0, 16)
    _integer(values[9], -1, 2**53 - 1)
    if not 0 <= values[1] <= 1 or not 0 <= values[2] <= 2.5 or min(values[3], values[5], values[6], values[7]) < 0 or values[7] > 10:
      raise ValueError("invalid_policy_lane_inputs")
    if pre is not None:
      _integer(pre[0], 0, 2**53 - 1)
      _integer(pre[6], 0, 2**53 - 1)
  elif family == "follow_distance":
    _integer(values[4], 0, 1)
    if min(values[:3]) < 0 or values[3] <= 0:
      raise ValueError("invalid_policy_follow_inputs")
  elif family == "traffic_offset":
    if values[0] < 0:
      raise ValueError("invalid_policy_traffic_inputs")
  else:
    for index in (0, 1, 3):
      _integer(values[index], 0, 1)
    for index in (2, 9):
      _integer(values[index], 0, 3)
    if values[5] < 0 or values[6] > values[7] or values[8] != .01:
      raise ValueError("invalid_policy_stopping_inputs")
  return {"schema_version": 1, "family": family, "pre": pre, "inputs": values,
          "observed_output": _number(point["observed_output"])}


def extract_policy_points(event):
  """Decode only fixed numeric payloads; the caller validates event bindings."""
  context = event.get("context", {})
  if context.get("policy_observation_version") != 1 or type(context.get("policy_observation_version")) is not int:
    return {}
  consumer = event.get("consumer_id")
  result = {}
  def add(family, keys, build):
    if not any(key in context for key in keys):
      return
    if any(key not in context for key in keys):
      raise ValueError("incomplete_policy_observation")
    pre, inputs, output = build()
    result[family] = validate_policy_point({"schema_version": 1, "family": family, "pre": pre,
                                            "inputs": inputs, "observed_output": output})
  if consumer == "planner.long_mpc":
    add("tf", ("tf_pre", "tf_input", "tf_output"), lambda: (context["tf_pre"], context["tf_input"], context["tf_output"]))
    def response():
      if context["response_source"] not in _SOURCES:
        raise ValueError("invalid_policy_response_source")
      return context["response_pre"], [*_array(context["response_input"], (11,)), _SOURCES.index(context["response_source"])], context["response_output"]
    add("response", ("response_pre", "response_input", "response_source", "response_output"), response)
    add("lane_change", ("lc_leads", "lc_input", "lc_output"), lambda: (context["lc_leads"], context["lc_input"], context["lc_output"]))
    add("follow_distance", ("follow_input", "follow_output"), lambda: ([], context["follow_input"], context["follow_output"]))
    def traffic():
      values = _array(context["traffic_policy"], (3,))
      return [], values[:2], values[2]
    add("traffic_offset", ("traffic_policy",), traffic)
  elif consumer == "controls.longitudinal":
    add("stopping", ("stopping_input", "stopping_output"), lambda: ([], context["stopping_input"], context["stopping_output"]))
  return result


@lru_cache(maxsize=1)
def _menus():
  from .registry import load_registry
  return {item["key"]: item["ui"] for item in load_registry()["items"]}


def _raw(key, value):
  menu = _menus().get(key)
  if type(value) is not int or menu is None or not menu["min"] <= value <= menu["max"]:
    raise ValueError("invalid_policy_parameter")
  return value


def evaluate_policy_point(key, candidate_raw, point, settings_raw):
  point = validate_policy_point(point)
  family = KEY_FAMILIES.get(key)
  if family is None or family != point["family"]:
    raise ValueError("policy_family_mismatch")
  settings = dict(settings_raw)
  settings[key] = _raw(key, candidate_raw)
  values, pre = point["inputs"], point["pre"]
  from .candidate_longitudinal import _tf_step, _response_step
  if family == "tf":
    if pre is None:
      raise ValueError("policy_initial_state_unavailable")
    signals = dict(zip(("personality", "v_ego", "a_ego", "tf_mode_factor_target", "planner_dt"), values))
    signals["personality"] = int(signals["personality"])
    output, _ = _tf_step(settings, signals, list(pre))
  elif family == "response":
    from openpilot.selfdrive.controls.lib.longitudinal_preview import LeadAccelResponseState
    state = LeadAccelResponseState()
    state.strength, state.key = pre[0], None if len(pre) == 1 else (int(pre[1]), int(pre[2]))
    names = ("response_enabled", "lead_status", "a_lead", "a_ego", "v_rel", "gap_margin", "speed_error",
             "radar_track_id", "planner_dt", "personality", "driving_mode", "source")
    signals = dict(zip(names, values))
    for name in ("radar_track_id", "personality", "driving_mode", "source"):
      signals[name] = int(signals[name])
    for name in ("response_enabled", "lead_status"):
      signals[name] = bool(signals[name])
    output = _response_step(settings, signals, state)
  elif family == "lane_change":
    if pre is None or values[8] != 1:
      raise ValueError("policy_lc_single_target_required")
    import numpy as np
    from openpilot.selfdrive.carrot.radar_motion.lane_change_gap import GapLead, LaneChangeGapPlan
    primary, target = GapLead(int(pre[0]), *pre[1:6]), GapLead(int(pre[6]), *pre[7:12])
    plan = LaneChangeGapPlan(active=bool(values[0]), primary_id=int(values[9]), targets=(target,),
                             confidence=values[1], clearance_s=values[2])
    output = plan.credit(primary, np.array([values[7]]), values[3], values[4], values[5], values[6], candidate_raw * .01)[0]
  elif family == "follow_distance":
    if not values[4]:
      raise ValueError("no_exposed_following_path")
    v_ego, lead, t_follow, comfort = values[:4]
    output = v_ego * v_ego / (2. * comfort) + t_follow * v_ego + candidate_raw * .01 - lead * lead / 5.
  elif family == "traffic_offset":
    from openpilot.selfdrive.carrot.traffic_stop import get_traffic_stop_distance_adjust
    output = get_traffic_stop_distance_adjust(candidate_raw * .01, *values)
  else:
    active, soft_hold, state, _, previous, rate, minimum, maximum, dt, initial_state = values
    if not active or soft_hold or state != 2 or initial_state != 2:
      raise ValueError("one_step_transition_unresolved")
    output = previous
    if output > max(-1., min(-.5, candidate_raw * .01)):
      output = min(output, 0.) - rate * dt
    output = max(minimum, min(maximum, output))
  return _number(output)


def policy_reference(key, point, preset=None):
  reference = validate_preset_reference(preset_reference() if preset is None else preset)
  return evaluate_policy_point(key, reference["settings_raw"][key], point, reference["settings_raw"])


def assert_current_policy(key, point, settings_raw):
  point = validate_policy_point(point)
  value = evaluate_policy_point(key, settings_raw.get(key), point, settings_raw)
  if not math.isclose(value, point["observed_output"], rel_tol=1e-9, abs_tol=1e-7):
    raise ValueError("policy_current_output_unreconciled")
  return value


def validate_projection_point(point):
  fields = {"schema_version", "producer", "speeds", "accels", "t_idxs", "action_time_offset_s", "v_ego_stopping", "current_output"}
  if (type(point) is not dict or set(point) != fields or type(point["schema_version"]) is not int
      or point["schema_version"] != 1 or point["producer"] != "planner.longitudinal"):
    raise ValueError("invalid_projection_point")
  speeds = _array(point["speeds"], range(2, 34))
  accels, times = _array(point["accels"], (len(speeds),)), _array(point["t_idxs"], (len(speeds),))
  if min(speeds) < 0 or times[0] != 0 or any(right <= left for left, right in zip(times, times[1:])):
    raise ValueError("invalid_projection_trajectory")
  offset, stopping = _number(point["action_time_offset_s"]), _number(point["v_ego_stopping"])
  if offset != .05 or not .01 <= stopping <= 1.:
    raise ValueError("unsupported_projection_scope")
  output = _array(point["current_output"], (4,))
  _integer(output[1], 0, 1)
  return {"schema_version": 1, "producer": point["producer"], "speeds": speeds, "accels": accels,
          "t_idxs": times, "action_time_offset_s": offset, "v_ego_stopping": stopping, "current_output": output}


def extract_projection_point(event):
  """Read the first plan-kernel invocation, before preview and final guards."""
  context = event.get("context", {})
  if (event.get("consumer_id") != "planner.longitudinal"
      or type(context.get("projection_observation_version")) is not int
      or context["projection_observation_version"] != 1):
    return None
  arrays = {}
  for name in ("speeds", "accels", "times"):
    head = _array(context.get(f"projection_{name}_head"), (16,))
    tail = _array(context.get(f"projection_{name}_tail"), (1,))
    arrays[name] = head + tail
  fields = event.get("fields", {})
  values = {}
  for key in PROJECTION_DEFAULTS:
    field = fields.get(key, {})
    if field.get("state") != "used":
      raise ValueError("projection_read_unavailable")
    values[key] = _number(field.get("effective_value"))
  if not math.isclose(_number(context.get("action_t")), values["LongActuatorDelay"] + .05, rel_tol=1e-9, abs_tol=1e-12):
    raise ValueError("projection_action_time_unreconciled")
  return validate_projection_point({"schema_version": 1, "producer": "planner.longitudinal", "speeds": arrays["speeds"],
    "accels": arrays["accels"], "t_idxs": arrays["times"], "action_time_offset_s": .05,
    "v_ego_stopping": values["VEgoStopping"], "current_output": context.get("projection_output")})


@lru_cache(maxsize=1)
def _projection_kernel():
  # Keep the actual pure source kernel without importing Linux hardware modules.
  import ast
  from pathlib import Path
  import numpy as np
  path = Path(__file__).resolve().parents[2] / "controls/lib/drive_helpers.py"
  tree = ast.parse(path.read_text(encoding="utf-8"))
  function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_accel_from_plan")
  namespace = {"np": np, "DT_MDL": .05, "__builtins__": {"len": len}}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
  return namespace["get_accel_from_plan"]


def _projection(point, settings_raw):
  import numpy as np
  delay = _raw("LongActuatorDelay", settings_raw.get("LongActuatorDelay")) * .01
  stopping = _raw("VEgoStopping", settings_raw.get("VEgoStopping")) * .01
  result = _projection_kernel()(np.array(point["speeds"]), np.array(point["accels"]), np.array(point["t_idxs"]),
                               action_t=delay + point["action_time_offset_s"], vEgoStopping=stopping)
  return [float(result[0]), float(bool(result[1])), float(result[2]), float(result[3])]


def assert_current_projection(point, settings_raw):
  point = validate_projection_point(point)
  if not math.isclose(settings_raw.get("VEgoStopping", -1) * .01, point["v_ego_stopping"], rel_tol=1e-9, abs_tol=1e-12):
    raise ValueError("projection_setting_unreconciled")
  result = _projection(point, settings_raw)
  if any(not math.isclose(actual, declared, rel_tol=1e-9, abs_tol=1e-7) for actual, declared in zip(result, point["current_output"])):
    raise ValueError("projection_current_output_unreconciled")
  return result


def evaluate_projection_point(key, candidate_raw, point, settings_raw):
  point = validate_projection_point(point)
  if key not in PROJECTION_DEFAULTS:
    raise ValueError("projection_key_unsupported")
  settings = dict(settings_raw)
  settings[key] = _raw(key, candidate_raw)
  result = _projection(point, settings)
  return result[1 if key == "VEgoStopping" else 0]


def projection_reference(key, point, preset=None):
  reference = validate_projection_preset_reference(projection_preset_reference() if preset is None else preset)
  return evaluate_projection_point(key, reference["settings_raw"][key], point, reference["settings_raw"])
