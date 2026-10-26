"""Source-backed one-cycle policies; no vehicle or closed-loop qualification."""
import ast
from copy import deepcopy
from enum import IntEnum
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace as NS
import unittest

import numpy as np

from openpilot.selfdrive.carrot.control_advisor import candidate_longitudinal as engine
from openpilot.selfdrive.carrot.control_advisor import policy_observations as policy
from openpilot.selfdrive.carrot.control_advisor.tests.test_consumer_planner_hooks import (
  Capture, CARROT, MPC, PLANNER, ROOT, carrot_class, carrot_instance, mpc_class, planner_tail,
)
from openpilot.selfdrive.carrot.radar_motion.lane_change_gap import GapLead, LaneChangeGapPlan
from openpilot.selfdrive.controls.lib.longitudinal_preview import LeadAccelResponseState, get_lead_accel_mpc_request


def point(family, pre, inputs, output):
  return {"schema_version": 1, "family": family, "pre": pre, "inputs": inputs, "observed_output": output}


def tf_point(*, base_last=2., a_ego=0.):
  obj = carrot_instance(carrot_class((ROOT / CARROT).read_text(encoding="utf-8")), Capture(False))
  settings = dict(policy.POLICY_DEFAULTS)
  settings.update(TFollowGap1=130, SpeedTFFactor=15, TFollowDecelBoost=20)
  for index in range(1, 5):
    setattr(obj, f"tFollowGap{index}", settings[f"TFollowGap{index}"] * .01)
  obj.speedTFFactor, obj.tFollowDecelBoost = settings["SpeedTFFactor"], settings["TFollowDecelBoost"] * .01
  obj._tf_mode_factor, obj._tf_decel_base, obj._tf_decel_extra = 1.1, 1.2, .03
  obj._tf_base_last, obj._tf_applied = base_last, 99.
  before = [obj._tf_mode_factor, obj._tf_decel_base, obj._tf_decel_extra, obj._tf_base_last]
  inputs = [0, 15., a_ego, obj.myTFollowFactor, .05]
  output = obj.get_T_FOLLOW(*inputs[:3])
  return settings, point("tf", before, inputs, output)


def response_point(*, key=None):
  settings = dict(policy.POLICY_DEFAULTS)
  settings["LeadAccelResponse"] = 4
  state = LeadAccelResponseState()
  state.strength, state.key = (0., None) if key is None else (.2, key)
  before = [state.strength] if key is None else [state.strength, *key]
  values = [1, 1, 1.5, .1, 1., 10., 5., 49, .05, 0, 3, 0]
  request = get_lead_accel_mpc_request(4, enabled=True, source="lead0", lead_status=True,
    a_lead=1.5, a_ego=.1, v_rel=1., gap_margin=10., speed_error=5.)
  output = state.update(request, .05, 49).a_change_cost_factor
  return settings, point("response", before, values, output)


def lc_point(*, primary_id=49):
  settings = dict(policy.POLICY_DEFAULTS)
  settings["DynamicTFollowLC"] = 85
  primary, target = GapLead(49, 35., 0., 0., 15., 0.), GapLead(50, 80., 3.5, 0., 15., 0.)
  plan = LaneChangeGapPlan(active=True, primary_id=primary_id, targets=(target,), confidence=1., clearance_s=.7)
  values = [1, 1., .7, 15., 0., 1.2, 5.5, 3., 1, primary_id]
  output = plan.credit(primary, np.array([3.]), 15., 0., 1.2, 5.5, .85)[0]
  return settings, point("lane_change", [49, 35., 0., 0., 15., 0., 50, 80., 3.5, 0., 15., 0.], values, float(output))


def context(key, settings, points, *, method=policy.POLICY_METHOD_ID, target=None):
  family = policy.policy_family_for_key(key)
  unit = policy.policy_capabilities()[key]["output_unit"] if family else policy.projection_capabilities()[key]["output_unit"]
  objective = {"id": "explicit-policy", "kind": "policy_reference", "source": "user_specified", "unit": unit,
               "method_id": method}
  if target is not None:
    objective["target_raw"] = target
  return {"settings_raw": dict(settings), "dependencies": {}, "objective": objective,
          "records": {"fit": [{"policy_observation" if family else "projection_observation": item,
                                 "reference": policy.policy_reference(key, item) if family else policy.projection_reference(key, item)}
                                for item in points], "select": [], "holdout": []}}


class PolicyPointTests(unittest.TestCase):
  def test_actual_longcontrol_stopping_point_reconciles_real_protocol(self):
    from openpilot.selfdrive.carrot.control_advisor.tests import test_consumer_control_hooks as hooks
    from openpilot.selfdrive.controls.lib.cruise_coasting import CruiseCoastingControl, MAX_PLAN_AGE, coasting_relief, no_coasting_lead

    class State(IntEnum):
      off, pid, stopping, starting = range(4)

      def __str__(self):
        return self.name

    captured = hooks.Capture()
    params = hooks.Parameters({"StoppingAccel": -70})
    env = hooks.ConsumerControlHooksTests().base(captured)
    env.update(LongCtrlState=State, Params=lambda: params, CruiseCoastingControl=CruiseCoastingControl,
               MAX_PLAN_AGE=MAX_PLAN_AGE, coasting_relief=coasting_relief, no_coasting_lead=no_coasting_lead,
               HYUNDAI_LONGITUDINAL_KP=1., HYUNDAI_LONGITUDINAL_KI=0., HYUNDAI_LONGITUDINAL_KF=1.,
               STOPPING_ACCEL_DEFAULT=-50, STOPPING_ACCEL_MIN=-100, STOPPING_ACCEL_MAX=-50)
    hooks.functions("openpilot/selfdrive/controls/lib/longcontrol.py", {"LongControl", "long_control_state_trans"}, env)
    cp = NS(brand="hyundai", longitudinalTuning=NS(kpBP=[0.], kpV=[.9], kiBP=[0.], kiV=[.1], kf=1.),
            startingState=False, vEgoStarting=.5, stopAccel=-.5, startAccel=.1, stoppingDecelRate=.8, openpilotLongitudinalControl=True)
    control = env["LongControl"](cp)
    control.long_control_state, control.last_output_accel = State.stopping, -.55
    cs = NS(softHoldActive=0, vEgo=0., aEgo=0., brakePressed=False, gasPressed=False, carrotCruise=False,
            cruiseState=NS(standstill=True), vCruise=0.)
    output = control.update(True, cs, NS(aTarget=0., vTargetNow=0., jTargetNow=0., shouldStop=True),
                            (-3., 2.), 0., NS(leadOne=NS(status=False, dRel=20.)))
    self.assertEqual(1, len(captured.events))
    observed = policy.extract_policy_points(captured.events[0])["stopping"]
    self.assertAlmostEqual(output[0], policy.assert_current_policy("StoppingAccel", observed, params.values))
    self.assertAlmostEqual(-.558, observed["observed_output"])
    self.assertEqual(-.55, policy.policy_reference("StoppingAccel", observed))

  def test_actual_mpc_producer_points_reconcile_all_five_families(self):
    capture = Capture(True)
    with capture.installed():
      carrot = carrot_instance(carrot_class((ROOT / CARROT).read_text(encoding="utf-8")), capture)
      for _ in range(40):
        carrot._params_update()
      carrot.leadAccelResponse = 3  # actual TF1 inherits the loaded common level
      carrot.lane_change_active = True
      target = GapLead(50, 110., 3.5, 0., 16., 0.)
      carrot.lane_change_gap = LaneChangeGapPlan(active=True, primary_id=49, targets=(target,), confidence=1., clearance_s=.7)
      mpc = mpc_class(ROOT / MPC)(mode="acc")
      mpc.set_cur_state(15., -.3)
      mpc.set_accel_limits(-1.2, 0.)
      lead = GapLead(49, 45., 0., 1., 16., .5, modelProb=1.)
      absent = NS(status=False, radar=False, radarTrackId=-1, vRel=0., aLeadK=0., modelProb=0.)
      for _ in range(3):
        mpc.update(carrot, False, NS(leadOne=lead, leadTwo=absent), 30., *(np.zeros(13) for _ in range(4)),
                   personality=0, lead_accel_response_enabled=True, lead_track_frames=(10, 0), cutout_relief_enabled=True)
      payload = [payload for consumer, payload, _ in capture.payloads if consumer == "planner.long_mpc"][-1]
    points = policy.extract_policy_points({"consumer_id": "planner.long_mpc", **payload})
    self.assertEqual({"tf", "response", "lane_change", "follow_distance", "traffic_offset"}, set(points))
    for key, family in (("TFollowGap1", "tf"), ("LeadAccelResponse", "response"), ("DynamicTFollowLC", "lane_change"),
                        ("StopDistanceCarrot", "follow_distance"), ("TrafficStopDistanceAdjust", "traffic_offset")):
      with self.subTest(key=key):
        self.assertAlmostEqual(points[family]["observed_output"], policy.assert_current_policy(key, points[family], carrot.params.values))

  def test_versioned_default_preset_matches_source_and_is_immutable(self):
    source = (ROOT / "openpilot/common/params_keys.h").read_bytes()
    self.assertEqual(policy.SOURCE_SHA256, hashlib.sha256(source).hexdigest())
    text = source.decode()
    for key, raw in {**policy.POLICY_DEFAULTS, **policy.PROJECTION_DEFAULTS}.items():
      match = re.search(r'\{"' + re.escape(key) + r'", \{PERSISTENT, INT, "(-?\d+)"\}\}', text)
      self.assertIsNotNone(match, key)
      self.assertEqual(raw, int(match[1]), key)
    reference = policy.preset_reference()
    reference["settings_raw"]["TFollowGap1"] = 0
    self.assertEqual(110, policy.preset_reference()["settings_raw"]["TFollowGap1"])
    for change in ({"version": True}, {"source_sha256": "0" * 64}, {"extra": 1}):
      with self.subTest(change=change), self.assertRaises(ValueError):
        policy.validate_preset_reference({**policy.preset_reference(), **change})

  def test_exact_tf_source_state_and_independent_point_order(self):
    settings, first = tf_point(a_ego=-.5)
    _, second = tf_point(base_last=.8, a_ego=.1)
    original = deepcopy([first, second])
    for current in (first, second):
      self.assertAlmostEqual(current["observed_output"], policy.assert_current_policy("TFollowGap1", current, settings))
    forward = [policy.evaluate_policy_point("TFollowGap1", 120, item, settings) for item in (first, second)]
    reverse = [policy.evaluate_policy_point("TFollowGap1", 120, item, settings) for item in (second, first)]
    self.assertEqual(forward, list(reversed(reverse)))
    self.assertEqual(original, [first, second])
    self.assertNotEqual(first["observed_output"], policy.policy_reference("TFollowGap1", first))
    bad = deepcopy(second)
    bad["pre"][3] = 99.
    with self.assertRaisesRegex(ValueError, "current_output_unreconciled"):
      policy.assert_current_policy("TFollowGap1", bad, settings)

  def test_initial_none_and_slew_saturation_are_explicit(self):
    settings, observed = tf_point(base_last=.5)
    observed["pre"] = None
    with self.assertRaisesRegex(ValueError, "initial_state_unavailable"):
      policy.evaluate_policy_point("TFollowGap1", 120, observed, settings)
    settings, observed = tf_point(base_last=.5)
    self.assertEqual(policy.evaluate_policy_point("TFollowGap1", 130, observed, settings),
                     policy.evaluate_policy_point("TFollowGap1", 140, observed, settings))

  def test_response_none_and_changed_keys_match_actual_source(self):
    for key in (None, (2, 20), (4, 49)):
      with self.subTest(key=key):
        settings, observed = response_point(key=key)
        self.assertEqual(observed["observed_output"], policy.assert_current_policy("LeadAccelResponse", observed, settings))
        self.assertEqual(1., policy.policy_reference("LeadAccelResponse", observed))
        self.assertEqual(1., policy.evaluate_policy_point("LeadAccelResponse", 0, observed, settings))
        self.assertEqual(observed["observed_output"], policy.evaluate_policy_point("LeadAccelResponseTF2", 5, observed, settings))

  def test_lane_change_uses_actual_plan_primary_id_and_cardinality(self):
    for identity in (49, 99):
      settings, observed = lc_point(primary_id=identity)
      self.assertEqual(observed["observed_output"], policy.assert_current_policy("DynamicTFollowLC", observed, settings))
      self.assertEqual(0., policy.policy_reference("DynamicTFollowLC", observed))
      if identity == 99:
        self.assertEqual(0., observed["observed_output"])
    observed["inputs"][8] = 2
    with self.assertRaisesRegex(ValueError, "single_target_required"):
      policy.evaluate_policy_point("DynamicTFollowLC", 90, observed, settings)

  def test_follow_distance_uses_native_source_formula(self):
    source = ast.parse((ROOT / "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py").read_text())
    names = {"get_stopped_equivalence_factor", "get_safe_obstacle_distance", "desired_follow_distance"}
    namespace = {"COMFORT_BRAKE": 2.5, "STOP_DISTANCE": 5.5}
    exec(compile(ast.Module(body=[node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names],
                            type_ignores=[]), "actual-follow-policy", "exec"), namespace)
    settings = dict(policy.POLICY_DEFAULTS, StopDistanceCarrot=520)
    output = namespace["desired_follow_distance"](15., 12., 2.4, 5.2, 1.2)
    observed = point("follow_distance", [], [15., 12., 1.2, 2.4, 1], output)
    self.assertEqual(output, policy.assert_current_policy("StopDistanceCarrot", observed, settings))
    self.assertAlmostEqual(output + .3, policy.policy_reference("StopDistanceCarrot", observed))
    observed["inputs"][4] = 0
    with self.assertRaisesRegex(ValueError, "no_exposed_following_path"):
      policy.evaluate_policy_point("StopDistanceCarrot", 550, observed, settings)

  def test_traffic_and_stopping_keep_exact_production_branch_scope(self):
    from openpilot.selfdrive.carrot.traffic_stop import get_traffic_stop_distance_adjust
    settings = dict(policy.POLICY_DEFAULTS, TrafficStopDistanceAdjust=0, StoppingAccel=-70)
    traffic = point("traffic_offset", [], [8., 1.], get_traffic_stop_distance_adjust(0., 8., 1.))
    self.assertEqual(traffic["observed_output"], policy.assert_current_policy("TrafficStopDistanceAdjust", traffic, settings))
    stopping = point("stopping", [], [1, 0, 2, 1, -.55, .8, -3., 2., .01, 2], -.558)
    self.assertAlmostEqual(-.558, policy.assert_current_policy("StoppingAccel", stopping, settings))
    self.assertEqual(-.55, policy.policy_reference("StoppingAccel", stopping))
    stronger = point("stopping", [], [1, 0, 2, 1, -.9, .8, -3., 2., .01, 2], -.9)
    self.assertEqual(-.9, policy.evaluate_policy_point("StoppingAccel", -50, stronger, settings))
    for index, value in ((0, 0), (1, 1), (2, 1), (9, 1)):
      bad = deepcopy(stopping)
      bad["inputs"][index] = value
      with self.subTest(index=index), self.assertRaisesRegex(ValueError, "transition_unresolved"):
        policy.evaluate_policy_point("StoppingAccel", -50, bad, settings)

  def test_strict_shape_and_wire_none_never_become_zero(self):
    settings, observed = tf_point()
    for change in ({"schema_version": True}, {"extra": 0}, {"family": "unknown"}, {"observed_output": float("nan")},
                   {"inputs": [True, 15., 0., 1., .05]}, {"pre": [1., None, 0., 1.]}):
      with self.subTest(change=change), self.assertRaises(ValueError):
        policy.validate_policy_point({**observed, **change})
    event = {"consumer_id": "planner.long_mpc", "context": {"policy_observation_version": 1,
      "tf_pre": None, "tf_input": [0, 15., 0., 1., .05], "tf_output": 1.}}
    extracted = policy.extract_policy_points(event)["tf"]
    self.assertIsNone(extracted["pre"])
    with self.assertRaisesRegex(ValueError, "initial_state_unavailable"):
      policy.evaluate_policy_point("TFollowGap1", 110, extracted, settings)

  def test_explicit_candidate_dispatch_and_current_parity(self):
    settings, observed = tf_point()
    ctx = context("TFollowGap1", settings, [observed])
    budget = {"remaining_evaluations": 10, "max_rows": 10}
    evaluated = engine.evaluate_family("TFollowGap1", 120, ctx, "fit", budget)
    self.assertEqual("evaluated", evaluated["status"], evaluated)
    self.assertEqual([policy.evaluate_policy_point("TFollowGap1", 120, observed, settings)], evaluated["predictions"])
    altered = deepcopy(ctx)
    altered["records"]["fit"][0]["policy_observation"]["observed_output"] += .1
    rejected = engine.evaluate_family("TFollowGap1", 120, altered, "fit", budget)
    self.assertEqual(["policy_current_output_unreconciled"], rejected["reason_codes"])
    no_method = deepcopy(ctx)
    no_method["objective"].pop("method_id")
    self.assertEqual("blocked", engine.evaluate_family("TFollowGap1", 120, no_method, "fit", budget)["status"])

  def test_defaults_keep_old_acceleration_goal_and_quality_budgets(self):
    base = ROOT / "openpilot/selfdrive/carrot/control_advisor/defaults"
    old_profile = json.loads((base / "native_acceleration_profile.json").read_text())
    new_profile = json.loads((base / "native_policy_profile.json").read_text())
    for key in old_profile.keys() - {"profile_id", "guard_tolerances"}:
      self.assertEqual(old_profile[key], new_profile[key], key)
    old = json.loads((base / "native_acceleration_objectives.json").read_text())
    new = json.loads((base / "native_policy_objectives.json").read_text())
    self.assertEqual(27, len(new["objectives"]))
    self.assertNotIn("sampling", new)
    for key, value in old["objectives"].items():
      self.assertEqual(value, new["objectives"][key])
    for key in policy.POLICY_KEYS:
      self.assertEqual(policy.preset_reference(), new["objectives"][key]["reference"])


class ProjectionTests(unittest.TestCase):
  def test_actual_planner_first_kernel_point_has_exact_bounded_wire_and_parity(self):
    from openpilot.selfdrive.carrot.control_advisor import consumer_observer
    captured = Capture(True)
    with captured.installed():
      planner_tail((ROOT / PLANNER).read_text(encoding="utf-8"), captured, "acc")
    event = {"consumer_id": "planner.longitudinal", **captured.payloads[-1][1]}
    self.assertLessEqual(len(event["context"]), 32)
    self.assertLessEqual(set(event["context"]), consumer_observer.CONTEXT_KEYS[event["consumer_id"]])
    consumer_observer._payload(event["consumer_id"], {"fields": event["fields"], "context": event["context"]})
    observed = policy.extract_projection_point(event)
    settings = {key: round(event["fields"][key]["effective_value"] * 100) for key in policy.PROJECTION_DEFAULTS}
    self.assertEqual(17, len(observed["speeds"]))
    self.assertEqual(observed["current_output"], policy.assert_current_projection(observed, settings))
    bad = deepcopy(event)
    bad["context"]["projection_speeds_tail"] = []
    with self.assertRaises(ValueError):
      policy.extract_projection_point(bad)
    bad = deepcopy(event)
    bad["context"]["action_t"] += .01
    with self.assertRaisesRegex(ValueError, "action_time_unreconciled"):
      policy.extract_projection_point(bad)

  def fixture(self, *, low_speed=False):
    get_accel_from_plan = policy._projection_kernel()
    settings = {"LongActuatorDelay": 40, "VEgoStopping": 1}
    times = [0., .1, .2, .5, 1., 2., 3.]
    speeds = [.2, .2, .15, .1, 0., 0., 0.] if low_speed else [2., 1.9, 1.7, 1.2, .6, .1, 0.]
    accels = [-.5] * len(times)
    actual = get_accel_from_plan(np.array(speeds), np.array(accels), np.array(times), action_t=.45, vEgoStopping=.01)
    return settings, {"schema_version": 1, "producer": "planner.longitudinal", "speeds": speeds, "accels": accels,
      "t_idxs": times, "action_time_offset_s": .05, "v_ego_stopping": .01,
      "current_output": [float(actual[0]), int(actual[1]), float(actual[2]), float(actual[3])]}

  def test_projection_matches_source_but_does_not_claim_model_effects(self):
    settings, observed = self.fixture()
    self.assertEqual(observed["current_output"], policy.assert_current_projection(observed, settings))
    self.assertNotEqual(policy.evaluate_projection_point("LongActuatorDelay", 40, observed, settings),
                        policy.projection_reference("LongActuatorDelay", observed))
    settings, observed = self.fixture(low_speed=True)
    self.assertEqual(0., policy.evaluate_projection_point("VEgoStopping", 1, observed, settings))
    self.assertEqual(1., policy.projection_reference("VEgoStopping", observed))
    caps = policy.projection_capabilities()
    self.assertEqual(policy.PROJECTION_SCOPE, caps["LongActuatorDelay"]["scope"])
    self.assertFalse(caps["LongActuatorDelay"]["trajectory_prediction"])
    self.assertFalse(caps["LongActuatorDelay"]["plant_improvement_proven"])

  def test_projection_dispatch_requires_explicit_target_and_current_parity(self):
    settings, observed = self.fixture(low_speed=True)
    ctx = context("VEgoStopping", settings, [observed], method=policy.PROJECTION_METHOD_ID, target=50)
    budget = {"remaining_evaluations": 20, "max_rows": 10}
    result = engine.evaluate_family("VEgoStopping", 50, ctx, "fit", budget)
    self.assertEqual([1.], result["predictions"], result)
    profile = {"max_rows": 10, "max_candidates_per_key": 8,
               "parameter": {"key": "VEgoStopping", "current_raw": 1, "step": 5, "min": 1, "max": 100}}
    proposed = engine.propose_family("VEgoStopping", ctx, profile)
    self.assertEqual(50., proposed["raw_estimate"], proposed)
    ctx["objective"].pop("target_raw")
    self.assertEqual(["explicit_projection_target_required"], engine.propose_family("VEgoStopping", ctx, profile)["reason_codes"])
    ctx["records"]["fit"][0]["projection_observation"]["current_output"][1] = 1
    self.assertEqual(["projection_current_output_unreconciled"],
                     engine.evaluate_family("VEgoStopping", 50, ctx, "fit", budget)["reason_codes"])

  def test_projection_rejects_other_producer_bad_clock_and_threshold_mismatch(self):
    settings, observed = self.fixture()
    for changes in ({"producer": "modeld.action"}, {"action_time_offset_s": .075}, {"t_idxs": [0.] * 7},
                    {"speeds": [-1.] * 7}, {"current_output": [0., True, 0., 0.]}):
      with self.subTest(changes=changes), self.assertRaises(ValueError):
        policy.validate_projection_point({**observed, **changes})
    observed["v_ego_stopping"] = .5
    with self.assertRaisesRegex(ValueError, "setting_unreconciled"):
      policy.assert_current_projection(observed, settings)


if __name__ == "__main__":
  unittest.main()
