"""Source-policy experiments; these are not native MPC or road validations."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import candidate_longitudinal as engine
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry


ROOT = Path(__file__).resolve().parents[5]
MENU = {item["key"]: item["ui"] for item in load_registry()["items"]}
BUDGET = {"remaining_evaluations": 100, "max_rows": 1000}


def context(key, settings, values, references=None, *, split="fit"):
  cap = engine.capabilities()[key]
  if isinstance(values, dict):
    values = [values]
  references = [0.] * len(values) if references is None else references
  rows = []
  for index, (sample, reference) in enumerate(zip(values, references)):
    stamp = 1_000_000_000 + index * 50_000_000
    rows.append({"block_id": "block-0", "route_id": "route-0", "mono_ns": stamp,
                 "signals": {name: {"value": sample[name], "unit": unit, "source": "consumer_state", "source_mono_ns": stamp}
                             for name, unit in cap["required_signals"].items()}, "reference": reference})
  return {"settings_raw": dict(settings), "dependencies": {}, "vehicle_context": {}, "evidence": {},
          "records": {name: rows if name == split else [] for name in ("fit", "select", "holdout")},
          "objective": {"id": "independent-policy", "kind": "policy_reference", "source": "synthetic_truth",
                        "unit": cap["output_unit"], "reference_signal": "policy_reference"}}


def profile(key, current, **overrides):
  result = {"max_rows": 1000, "max_candidates_per_key": 16,
            "parameter": {"key": key, "current_raw": current, **MENU[key], "max_delta_raw": 1000}}
  result.update(overrides)
  return result


def evaluate(key, raw, ctx, split="fit"):
  return engine.evaluate_family(key, raw, ctx, split, BUDGET)


class LongitudinalCandidateTests(unittest.TestCase):
  def test_metadata_import_works_without_numpy_or_hardware(self):
    script = """
import builtins,json
original=builtins.__import__
def guarded(name,*args,**kwargs):
  if name.split('.')[0] in ('numpy','capnp','casadi') or name.startswith(('openpilot.common.params','openpilot.system.hardware')):
    raise ImportError('blocked optional native dependency')
  return original(name,*args,**kwargs)
builtins.__import__=guarded
from openpilot.selfdrive.carrot.control_advisor.candidate_longitudinal import capabilities
print(json.dumps(capabilities()))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(len(json.loads(result.stdout)), 27)

  def test_curve_quantized_numeric_proposal_and_independent_target(self):
    settings = {key: 100 for key in engine.CRUISE_KEYS}
    key = "CruiseMaxVals2"
    ctx = context(key, settings, {"v_ego": 40/3.6, "accel_mode_factor": 1.}, [1.3])
    before = deepcopy(ctx)
    proposal = engine.propose_family(key, ctx, profile(key, 100))
    self.assertAlmostEqual(proposal["raw_estimate"], 130.)
    self.assertIn(130, proposal["candidate_raw_values"])
    self.assertTrue(all((value - 100) % 5 == 0 for value in proposal["candidate_raw_values"]))
    self.assertEqual(evaluate(key, 130, ctx)["predictions"], [1.3])
    self.assertEqual(ctx, before)
    ctx["objective"].update(kind="tracking", source="independent_measurement")
    self.assertEqual(evaluate(key, 130, ctx)["reason_codes"], ["independent_policy_objective_required"])

  def test_curve_matches_actual_source_at_knots_and_mode_factors(self):
    import numpy as np
    from .test_consumer_planner_hooks import carrot_class, carrot_instance, Capture, CARROT, DrivingMode
    obj = carrot_instance(carrot_class((ROOT / CARROT).read_text(encoding="utf-8")), Capture(False))
    settings = {key: int(round(getattr(obj, f"cruiseMaxVals{index}") * 100)) for index, key in enumerate(engine.CRUISE_KEYS)}
    for factor in (.8, .9, 1., 1.2):
      obj.myDrivingMode, obj.mySafeFactor = DrivingMode.Normal, factor
      for speed in (-1., 0., 10., 25., 40., 60., 80., 110., 140., 160.):
        ctx = context("CruiseMaxVals0", settings, {"v_ego": speed/3.6, "accel_mode_factor": factor})
        value = evaluate("CruiseMaxVals0", settings["CruiseMaxVals0"], ctx)["predictions"][0]
        self.assertAlmostEqual(value, float(obj.get_carrot_accel(speed/3.6)), places=12)
    self.assertEqual(np.geterr()["divide"], "warn")

  def test_tf_sequential_replay_matches_actual_carrot_methods(self):
    from .test_consumer_planner_hooks import carrot_class, carrot_instance, Capture, CARROT
    obj = carrot_instance(carrot_class((ROOT / CARROT).read_text(encoding="utf-8")), Capture(False))
    settings = {**{key: 120 + index * 20 for index, key in enumerate(engine.GAP_KEYS)}, "SpeedTFFactor": 18, "TFollowDecelBoost": 50}
    for index in range(4):
      setattr(obj, f"tFollowGap{index + 1}", settings[f"TFollowGap{index + 1}"] * .01)
    obj.speedTFFactor, obj.tFollowDecelBoost = 18, .5
    obj._tf_mode_factor, obj._tf_decel_base, obj._tf_decel_extra, obj._tf_base_last = 1.2, 1.5, .1, 1.6
    inputs, expected = [], []
    for index in range(80):
      acceleration = -1.5 if index < 30 else .5
      mode_factor = 1.2 if index < 40 else 1.
      speed = 18. - index * .1
      obj.myTFollowFactor = mode_factor
      inputs.append({"v_ego": speed, "a_ego": acceleration, "personality": 1, "tf_mode_factor_target": mode_factor,
                     "tf_initial_mode_factor": 1.2, "tf_initial_decel_base": 1.5, "tf_initial_decel_extra": .1,
                     "tf_initial_applied": 1.6, "planner_dt": .05})
      expected.append(obj.get_T_FOLLOW(1, speed, acceleration))
    ctx = context("TFollowGap2", settings, inputs, expected)
    actual = evaluate("TFollowGap2", settings["TFollowGap2"], ctx)
    self.assertEqual(actual["status"], "evaluated")
    for lhs, rhs in zip(actual["predictions"], expected):
      self.assertAlmostEqual(lhs, rhs, places=12)
    self.assertEqual(actual, evaluate("TFollowGap2", settings["TFollowGap2"], ctx))
    missing = deepcopy(ctx)
    del missing["records"]["fit"][0]["signals"]["tf_initial_applied"]
    self.assertEqual(evaluate("TFollowGap2", 140, missing)["reason_codes"], ["required_signal_missing"])
    # A missing/excluded planner frame must not silently advance a state machine.
    ctx["records"]["fit"].pop(20)
    self.assertEqual(evaluate("TFollowGap2", 140, ctx)["reason_codes"], ["replay_signal_gap"])

  def test_response_discrete_source_table_and_mode_inheritance(self):
    settings = {"LeadAccelResponse": 3, **{key: -1 for key in engine.RESPONSE_KEYS[1:]}}
    values = {"personality": 1, "driving_mode": 3, "response_enabled": 1, "source": 0, "lead_status": 1,
              "a_lead": 1., "a_ego": 0., "v_rel": 2., "gap_margin": 10., "speed_error": 5., "radar_track_id": 1,
              "planner_dt": .05, "response_initial_strength": 0., "response_initial_level": 0, "response_initial_track_id": -1}
    ctx = context("LeadAccelResponse", settings, [values] * 25, [.05] * 25)
    proposal = engine.propose_family("LeadAccelResponse", ctx, profile("LeadAccelResponse", 3))
    self.assertEqual(proposal["raw_estimate"], 5.)
    self.assertEqual(evaluate("LeadAccelResponse", 5, ctx)["predictions"], [.05] * 25)
    inherited = context("LeadAccelResponseTF2", settings, values)
    self.assertEqual(engine.propose_family("LeadAccelResponseTF2", inherited, profile("LeadAccelResponseTF2", -1))["reason_codes"],
                     ["inherited_profile_requires_explicit_selection"])
    eco = context("LeadAccelResponse", settings, {**values, "driving_mode": 1})
    self.assertEqual(evaluate("LeadAccelResponse", 5, eco)["predictions"], evaluate("LeadAccelResponse", 2, eco)["predictions"])

  def test_lane_credit_uses_full_source_guards_and_retains_tf(self):
    values = {"v_ego": 10., "max_accel": 0., "t_follow": 1.5, "stop_distance": 6., "horizon": 3.,
              "lc_active": 1, "lc_confidence": 1., "lc_clearance": .5, "lc_target_count": 1,
              "primary_track_id": 1, "primary_distance": 35., "primary_y_rel": 0., "primary_v_rel": 0.,
              "primary_speed": 10., "primary_accel": 0., "target_track_id": 2, "target_distance": 100.,
              "target_y_rel": 3.5, "target_v_rel": 0., "target_speed": 10., "target_accel": 0.}
    ctx = context("DynamicTFollowLC", {"DynamicTFollowLC": 80}, values)
    self.assertAlmostEqual(evaluate("DynamicTFollowLC", 80, ctx)["predictions"][0], 2.5)
    self.assertEqual(evaluate("DynamicTFollowLC", 100, ctx)["predictions"], [0.])
    close_target = context("DynamicTFollowLC", {"DynamicTFollowLC": 80}, {**values, "target_distance": 7.})
    self.assertEqual(evaluate("DynamicTFollowLC", 80, close_target)["predictions"], [0.])
    multiple = context("DynamicTFollowLC", {"DynamicTFollowLC": 80}, {**values, "lc_target_count": 2})
    self.assertEqual(evaluate("DynamicTFollowLC", 80, multiple)["reason_codes"], ["multi_target_replay_state_unavailable"])

  def test_stop_geometry_matches_native_source_formula_without_solver(self):
    source = (ROOT / "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py").read_text(encoding="utf-8")
    wanted = {"get_safe_obstacle_distance", "get_stopped_equivalence_factor", "desired_follow_distance"}
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {"COMFORT_BRAKE": 2.5, "STOP_DISTANCE": 6.}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "actual_follow_geometry", "exec"), namespace)
    values = {"v_ego": 15., "lead_speed": 12., "t_follow": 1.7, "comfort_brake": 2.4}
    ctx = context("StopDistanceCarrot", {"StopDistanceCarrot": 520}, values)
    value = evaluate("StopDistanceCarrot", 550, ctx)["predictions"][0]
    self.assertEqual(value, namespace["desired_follow_distance"](15., 12., 2.4, 5.5, 1.7))
    self.assertEqual(ctx["settings_raw"]["StopDistanceCarrot"], 520)

  def test_traffic_offset_and_stopping_mapping_never_claim_stop_distance_outcome(self):
    for speed, offset, expected in ((10., 0., 3.), (0., 0., -2.), (10., 2., 2.)):
      ctx = context("TrafficStopDistanceAdjust", {"TrafficStopDistanceAdjust": 200}, {"v_ego": speed, "model_lead_offset": offset})
      self.assertEqual(evaluate("TrafficStopDistanceAdjust", 300, ctx)["predictions"], [expected])
    values = {"active": 1, "soft_hold": 0, "long_control_state": 2, "should_stop": 1,
              "previous_output_accel": -.55, "stopping_decel_rate": .8, "accel_min": -3.5, "accel_max": 2.}
    ctx = context("StoppingAccel", {"StoppingAccel": -60}, values)
    self.assertAlmostEqual(evaluate("StoppingAccel", -60, ctx)["predictions"][0], -.558)
    hold = context("StoppingAccel", {"StoppingAccel": -60}, {**values, "soft_hold": 1})
    self.assertEqual(evaluate("StoppingAccel", -60, hold)["reason_codes"], ["ordinary_stopping_path_required"])

  def test_unavailable_backend_budgets_missing_state_and_fit_isolation(self):
    for key in engine.UNAVAILABLE_KEYS:
      output = evaluate(key, 50, {})
      self.assertEqual(output["status"], "blocked")
      self.assertIn("backend_unavailable", output["reason_codes"])
      self.assertEqual(output["predictions"], [])
    key = "CruiseMaxVals2"
    ctx = context(key, {name: 100 for name in engine.CRUISE_KEYS}, {"v_ego": 40/3.6, "accel_mode_factor": 1.}, [1.3])
    self.assertEqual(engine.evaluate_family(key, 130, ctx, "fit", {"max_rows": 0, "remaining_evaluations": 2})["reason_codes"], ["evaluation_budget_exhausted"])
    self.assertEqual(engine.evaluate_family(key, 130, ctx, "fit", {"max_rows": 1, "remaining_evaluations": 0})["status"], "evaluated")
    ctx["records"]["holdout"] = deepcopy(ctx["records"]["fit"])
    self.assertEqual(engine.propose_family(key, ctx, profile(key, 100))["reason_codes"], ["proposal_received_nonfit_data"])
    del ctx["settings_raw"]["CruiseMaxVals0"]
    self.assertEqual(evaluate(key, 130, ctx)["reason_codes"], ["dependency_missing"])

  def test_missing_optional_numpy_backend_is_explicit(self):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
      if name == "openpilot.selfdrive.carrot.traffic_stop":
        raise ImportError("synthetic absent dependency")
      return original(name, *args, **kwargs)
    ctx = context("TrafficStopDistanceAdjust", {"TrafficStopDistanceAdjust": 200}, {"v_ego": 10., "model_lead_offset": 0.})
    with patch("builtins.__import__", side_effect=guarded):
      self.assertEqual(evaluate("TrafficStopDistanceAdjust", 300, ctx)["reason_codes"], ["numeric_backend_unavailable"])

  def test_actual_dispatch_scores_quantized_candidate_and_holds_failed_guard(self):
    from openpilot.selfdrive.carrot.control_advisor.candidates import evaluate_candidates
    key = "CruiseMaxVals2"
    pack, search_profile = dispatch_fixture(key)
    before = deepcopy(pack)
    report = evaluate_candidates(pack, search_profile)
    row = next(item for item in report["items"] if item["key"] == key)
    self.assertEqual(row["status"], "search_candidate", row)
    self.assertEqual(row["candidate_raw"], 130)
    self.assertEqual(row["metrics"]["holdout"]["rmse"], 0.)
    self.assertAlmostEqual(row["metrics"]["baseline"]["holdout"]["rmse"], .3)
    self.assertFalse(report["recommendations_enabled"])
    self.assertFalse(report["apply_enabled"])
    self.assertFalse(report["device_qualified"])
    self.assertEqual(pack, before)
    # A strict independent output guard vetoes the better policy fit.
    search_profile["guard_tolerances"][key]["max_absolute_prediction"] = 1.2
    guarded = next(item for item in evaluate_candidates(pack, search_profile)["items"] if item["key"] == key)
    self.assertEqual(guarded["status"], "keep_current")
    self.assertIsNone(guarded["candidate_raw"])
    self.assertIn("holdout_or_objective_guard_failed", guarded["reason_codes"])


def dispatch_fixture(key="CruiseMaxVals2"):
  """A declared synthetic policy target, separate blocks and routes, no vehicle."""
  pack = {"schema_version": 1, "source_kind": "synthetic", "evidence_id": "synthetic-long-policy",
          "artifacts": [{"id": "synthetic-fixture", "sha256": "a" * 64}],
          "vehicle_context": {"brand": "fixture", "controller_path": "policy_mapping", "mode": "acc",
                              "code_hashes": {"openpilot/selfdrive/carrot/carrot_functions.py": "b" * 64}},
          "epoch": {"id": "fixture-epoch", "start_ns": 0, "end_ns": 10_000_000_000,
                    "settings_raw": {name: 100 for name in engine.CRUISE_KEYS}, "dependencies": {}, "completeness": "fixture"},
          "splits": {name: [name] for name in ("fit", "select", "holdout")}, "blocks": [], "consumer_evidence": [],
          "objectives": {key: {"id": "user-accel-policy", "kind": "policy_reference", "source": "user_specified",
                               "unit": "m/s^2", "reference_signal": "policy_reference"}}, "model_artifact": None}
  for index, split in enumerate(("fit", "select", "holdout")):
    start = (index * 2 + 1) * 1_000_000_000
    samples = []
    for offset in (0, 50_000_000):
      stamp = start + offset
      samples.append({"mono_ns": stamp, "excluded_reasons": [], "signals": {
        "v_ego": {"value": 40/3.6, "unit": "m/s", "source": "derived_input", "source_mono_ns": stamp},
        "accel_mode_factor": {"value": 1., "unit": "1", "source": "consumer_state", "source_mono_ns": stamp},
        "policy_reference": {"value": 1.3, "unit": "m/s^2", "source": "user_specified", "source_mono_ns": stamp}}})
    pack["blocks"].append({"id": split, "route_id": f"synthetic-route-{index}", "start_ns": start,
                           "end_ns": start + 100_000_000, "finalized_through_ns": start + 100_000_000,
                           "mask_version": "synthetic-mask-v1", "samples": samples})
  search_profile = {"profile_id": "synthetic-long-policy-v1", "version": 1, "status": "synthetic_only", "scope": "host_unqualified",
                    "allowed_source_kinds": ["synthetic"], "max_rows": 1000, "max_blocks": 6,
                    "max_candidates_per_key": 16, "max_evaluations": 100, "max_lag_ns": 0, "dependency_margin_ns": 0,
                    "min_blocks_per_split": 1, "min_samples_per_split": 2, "minimum_improvement": .001,
                    "guard_tolerances": {key: {"max_absolute_error": .5, "max_absolute_prediction": 2.5}},
                    "raw_step_policy": "ui_step", "per_key_change_limits": {key: 40}, "device_qualified": False}
  return pack, search_profile


if __name__ == "__main__":
  unittest.main()
