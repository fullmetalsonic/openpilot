"""Actual production smoothing and exact applicability, never a native IPC test."""
from copy import deepcopy
import ast
from pathlib import Path
import unittest
import numpy as np

from openpilot.selfdrive.carrot.control_advisor.consumer_mapping import special_field_matches
from openpilot.selfdrive.carrot.control_advisor.consumer_observer import validate_event
from openpilot.selfdrive.carrot.control_advisor.candidates import _consumer_gate
from .test_six_parameter_backend import manual_fixture


def _production_smoothing():
  # Execute the unchanged production function without Windows importing GPIO.
  root = Path(__file__).resolve().parents[5]
  path = root / "openpilot/selfdrive/controls/lib/drive_helpers.py"
  function = next(node for node in ast.parse(path.read_text(encoding="utf8")).body if isinstance(node, ast.FunctionDef) and node.name == "smooth_value")
  realtime = ast.parse((root / "openpilot/common/realtime.py").read_text(encoding="utf8"))
  period = next(ast.literal_eval(node.value) for node in realtime.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "DT_MDL" for target in node.targets))
  namespace = {"np": np, "DT_MDL": period}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
  return namespace["smooth_value"]


smooth_value = _production_smoothing()


def smoothing_event(base=.1):
  evidence, profile, _, _ = manual_fixture("LatSmoothSec")
  event = deepcopy(next(event for event in evidence["consumer_evidence"] if event["consumer_id"] == "modeld.action"))
  dynamic = min(.6, base * 3)
  event["fields"]["LatSmoothSec"].update(read_value=base, effective_value=dynamic, state="used", source="derived")
  event["context"] = {"lat_smooth_y_std": .25, "lat_smooth_dynamic_s": dynamic, "lat_pre_smooth_curvature": .01,
    "lat_previous_curvature": .003, "lat_post_smooth_curvature": float(smooth_value(.01, .003, dynamic)),
    "lat_smoothing_applied": True, "v_ego": 20., "lat_delay_used_s": .4, "lat_action_t": .475, "next_lat_delay_s": .2 + dynamic}
  return validate_event(event)


def bypass_event(event, *, torque=False):
  event = deepcopy(event)
  for field in event["fields"].values():
    field.update(state="bypassed", read_value=None, effective_value=None, source="unknown")
  event["context"] = {"active": True, "nnff": False, "nnff_lite": False} if torque else {
    "active": True, "path": "model", "lane_selected": False, "model_target_selected": True, "live_delay_used": False}
  return validate_event(event)


class ConsumerMappingTests(unittest.TestCase):
  def test_derived_smoothing_matches_production_exponential_and_prior_frame_time(self):
    for base in (0., .1, .3):
      event = smoothing_event(base)
      self.assertTrue(special_field_matches(event, "LatSmoothSec", base))
      event["context"]["lat_post_smooth_curvature"] += .0001
      self.assertFalse(special_field_matches(event, "LatSmoothSec", base))

  def test_forged_derived_read_dynamic_context_and_inactive_paths_fail(self):
    event = smoothing_event()
    for section, name, value in (("fields", "read_value", .11), ("fields", "effective_value", .1),
      ("context", "lat_smooth_y_std", .2), ("context", "lat_smoothing_applied", False),
      ("context", "lat_action_t", .575), ("context", "v_ego", .2)):
      changed = deepcopy(event)
      (changed["fields"]["LatSmoothSec"] if section == "fields" else changed["context"])[name] = value
      self.assertFalse(special_field_matches(changed, "LatSmoothSec", .1), name)

  def test_only_exact_model_and_non_nnff_bypass_are_applicable_exceptions(self):
    evidence, _, _, _ = manual_fixture("SteerActuatorDelay")
    for event in evidence["consumer_evidence"]:
      if event["consumer_id"] not in {"controls.torque", "controls.lateral_target"}:
        continue
      mapped = bypass_event(event, torque=event["consumer_id"] == "controls.torque")
      self.assertTrue(special_field_matches(mapped, "SteerActuatorDelay", .2))
      mapped["context"]["active"] = False
      self.assertFalse(special_field_matches(mapped, "SteerActuatorDelay", .2))
      mapped["context"]["active"] = True
      if event["consumer_id"] == "controls.torque":
        mapped["context"]["nnff"] = True
        self.assertIsNone(special_field_matches(mapped, "SteerActuatorDelay", .2))
      else:
        mapped["context"]["model_target_selected"] = False
        self.assertFalse(special_field_matches(mapped, "SteerActuatorDelay", .2))

  def test_actual_candidate_consumer_gate_preserves_other_consumers_hashes_and_boot(self):
    for key in ("LatSmoothSec", "SteerActuatorDelay"):
      evidence, profile, _, summary = manual_fixture(key)
      for index, event in enumerate(evidence["consumer_evidence"]):
        if event["consumer_id"] == "controls.lateral_target":
          evidence["consumer_evidence"][index] = bypass_event(event)
        elif event["consumer_id"] == "controls.torque":
          evidence["consumer_evidence"][index] = bypass_event(event, torque=True)
        elif key == "LatSmoothSec":
          template = smoothing_event()
          event["fields"], event["context"] = template["fields"], template["context"]
      from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
      item = next(item for item in load_registry()["items"] if item["key"] == key)
      records = {"fit": [{"mono_ns": 1_000_000_000}], "select": [], "holdout": []}
      self.assertEqual([], _consumer_gate(evidence, profile, item, records)[0])
      for event in evidence["consumer_evidence"]:
        event["boot_id"] = "1" * 8 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 12
      self.assertTrue(_consumer_gate(evidence, profile, item, records)[0])


if __name__ == "__main__":
  unittest.main()
