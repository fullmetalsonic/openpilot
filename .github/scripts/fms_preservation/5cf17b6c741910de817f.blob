"""Pure checks of exceptional observed consumers; identity/freshness stay with callers."""
import math


def _finite(value):
  return type(value) in (int, float) and math.isfinite(value)


def _same(first, second):
  return _finite(first) and _finite(second) and math.isclose(first, second, rel_tol=1e-9, abs_tol=1e-12)


def special_field_matches(event, key, expected_physical):
  """None delegates ordinary stored-value matching; booleans are final checks."""
  consumer = event.get("consumer_id")
  field = event.get("fields", {}).get(key)
  context = event.get("context", {})
  if consumer == "controls.lateral_target" and key in {"SteerActuatorDelay", "LatSmoothSec"}:
    if type(field) is not dict or context.get("active") is not True:
      return False
    if context.get("path") in {"model", "vw_model"}:
      return (context.get("lane_selected") is False and context.get("model_target_selected") is True
              and field.get("state") == "bypassed" and field.get("read_value") is None and field.get("effective_value") is None)
    if context.get("path") not in {"lane", "vw_lane"} or context.get("lane_selected") is not True:
      return False
    return None
  if consumer == "controls.torque" and key == "SteerActuatorDelay":
    if type(field) is not dict or context.get("active") is not True or any(type(context.get(name)) is not bool for name in ("nnff", "nnff_lite")):
      return False
    if context["nnff"] is False and context["nnff_lite"] is False:
      return field.get("state") == "bypassed" and field.get("read_value") is None and field.get("effective_value") is None
    return None
  if consumer != "modeld.action" or key != "LatSmoothSec":
    return None
  if (type(field) is not dict or field.get("state") != "used" or field.get("source") != "derived"
      or not _finite(expected_physical) or expected_physical < 0 or not _same(field.get("read_value"), expected_physical)
      or context.get("lat_smoothing_applied") is not True or not _finite(context.get("v_ego")) or context["v_ego"] <= .3):
    return False
  names = ("lat_smooth_y_std", "lat_smooth_dynamic_s", "lat_pre_smooth_curvature", "lat_previous_curvature",
           "lat_post_smooth_curvature", "lat_delay_used_s", "lat_action_t")
  if any(not _finite(context.get(name)) for name in names) or context["lat_smooth_y_std"] < 0 or context["lat_delay_used_s"] < 0:
    return False
  fraction = min(1., max(0., (context["lat_smooth_y_std"] - .15) / .1))
  dynamic = min(.6, max(0., expected_physical * (1 + 2 * fraction)))
  if not _same(field.get("effective_value"), dynamic) or not _same(context["lat_smooth_dynamic_s"], dynamic):
    return False
  alpha = -math.expm1(-.05 / dynamic) if dynamic > 0 else 1.
  post = alpha * context["lat_pre_smooth_curvature"] + (1 - alpha) * context["lat_previous_curvature"]
  # action_t consumes the prior frame's delay; the current dynamic is next-frame input.
  return (_same(context["lat_post_smooth_curvature"], post)
          and _same(context["lat_action_t"], context["lat_delay_used_s"] + .075))
