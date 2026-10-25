"""Paired production-source parity, using the existing recording-solver harness.

This exercises host wiring and observation payloads, not native MPC or a vehicle.
The pinned pre-observation FMS source is read from its Git object or local freeze.
"""
import ast
import builtins
from contextlib import contextmanager
from copy import deepcopy
from enum import Enum
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.selfdrive.carrot import t_follow
from openpilot.selfdrive.controls.lib import longitudinal_preview as preview
from openpilot.selfdrive.controls.lib.longitudinal_gap_recovery import LeadGapState, gap_reference, displayed_follow_distance
from openpilot.selfdrive.controls.lib.longitudinal_braking_gap import LeadComfortState, braking_gap_reference, comfort_path_has_reserve


ROOT = Path(__file__).resolve().parents[5]
BASE = "1b1e019ee6bf5778be751373f269ed4f5d874cf2"
CARROT = "openpilot/selfdrive/carrot/carrot_functions.py"
PLANNER = "openpilot/selfdrive/controls/lib/longitudinal_planner.py"
MPC = "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py"
PROTOCOL = "openpilot.selfdrive.carrot.control_advisor.consumer_observer"
BASE_HASHES = {CARROT: "02314170ee3c574162a1b348afd7ad7f62c7de9d009e00d90c1129b9b044efcb",
               PLANNER: "534a4cf84b9e42b9e055347d7f0433cadb4d17e46b404aa961b07dd353f1707d",
               MPC: "9d0ff1004cd6445aa17092749cb41d8114f8f11337881e95bf14012d8e308efd"}


class DrivingMode(Enum):
  Eco, Safe, Normal, High = 1, 2, 3, 4


def baseline(relative):
  frozen = ROOT / ".analysis/consumer_baseline" / relative
  raw = frozen.read_bytes() if frozen.exists() else subprocess.check_output(["git", "show", f"{BASE}:{relative}"], cwd=ROOT)
  if hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() != BASE_HASHES[relative]:
    raise AssertionError("pre-observation FMS baseline mismatch")
  return raw.replace(b"\r\n", b"\n").decode("utf-8")


class ParamsTrace:
  def __init__(self):
    self.trace = []
    self.values = {"MyDrivingMode": 3, "MyDrivingModeAuto": 0, "TrafficLightDetectMode": 2,
                   "TFollowGap1": 60, "TFollowGap2": 90, "TFollowGap3": 120, "TFollowGap4": 150,
                   "LeadAccelResponse": 3, "DynamicTFollowLC": 80, "SpeedTFFactor": 15, "TFollowDecelBoost": 25,
                   "StopDistanceCarrot": 520, "CruiseEcoControl": 2, "AutoNaviSpeedDecelRate": 120,
                   "AChangeCostStarting": 10, "TrafficStopDistanceAdjust": 30, "LongActuatorDelay": 30, "VEgoStopping": 1}
    self.values.update({f"LeadAccelResponseTF{i + 1}": value for i, value in enumerate((-1, 0, 2, 5))})
    self.values.update({f"CruiseMaxVals{i}": value for i, value in enumerate((180, 160, 130, 110, 90, 80, 60))})

  def get_int(self, key):
    self.trace.append(("int", key))
    return int(self.values[key])

  def get_float(self, key):
    self.trace.append(("float", key))
    return float(self.values[key])


class Capture:
  def __init__(self, enabled, failing=False):
    self.enabled, self.failing, self.payloads = enabled, failing, []
    self.module = ModuleType(PROTOCOL)
    self.module.create_observer = lambda name, **metadata: name if enabled else None
    self.module.field = lambda value=None, **kwargs: {"effective_value": value, "read_value": kwargs.pop("read_value", None),
      "state": kwargs.pop("state", "used"), "source": kwargs.pop("source", "unknown"), "read_mono_ns": kwargs.pop("read_mono_ns", None)}

    def observe(observer, factory, **metadata):
      # A recording endpoint inspects real producer factories. The actual
      # never-raise transport contract is tested in the observer module suite.
      try:
        payload = factory()
        json.dumps(payload, allow_nan=False)
        if self.failing:
          raise OSError("synthetic publisher failure")
        self.payloads.append((observer, payload, metadata))
      except Exception:
        return

    self.module.observe = observe

  @contextmanager
  def installed(self):
    with patch.dict(sys.modules, {PROTOCOL: self.module}):
      yield self


def carrot_class(source):
  tree = ast.parse(source)
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CarrotPlanner")
  names = {"_params_update", "_observe_control_cache", "get_carrot_accel", "_get_base_t_follow", "_apply_speed_t_follow_scale",
           "_apply_decel_hold_and_boost_t_follow", "_clip_t_follow", "get_T_FOLLOW"}
  cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
  namespace = {"np": np, "time": time, "DT_MDL": .05, "DrivingMode": DrivingMode,
               "log": NS(LongitudinalPersonality=NS(aggressive=0, standard=1, relaxed=2, moreRelaxed=3)),
               "CV": NS(MS_TO_KPH=3.6), "A_CRUISE_MAX_BP_CARROT": [0., 10/3.6, 40/3.6, 60/3.6, 80/3.6, 110/3.6, 140/3.6],
               **{name: getattr(t_follow, name) for name in dir(t_follow) if name.startswith(("get_", "ramp_"))}}
  exec(compile(ast.Module(body=[cls], type_ignores=[]), "paired_carrot", "exec"), namespace)
  return namespace["CarrotPlanner"]


def carrot_instance(cls, capture):
  obj = cls()
  obj.params, obj.params_count, obj.frame = ParamsTrace(), 0, 0
  obj.myDrivingMode = obj.myDrivingMode_last = DrivingMode.Normal
  obj.myDrivingModeAuto, obj.myDrivingMode_disable_auto = 0, False
  obj.drivingModeDetector = NS(get_mode=lambda _: DrivingMode.Normal)
  obj.myHighModeFactor, obj.mySafeFactor, obj.myTFollowFactor = 1.2, 1., 1.
  obj.trafficLightDetectMode = 2
  obj.tFollowGap1, obj.tFollowGap2, obj.tFollowGap3, obj.tFollowGap4 = 1.1, 1.3, 1.45, 1.6
  obj.leadAccelResponseBase, obj.leadAccelResponseTF, obj.leadAccelResponse = 0, [-1] * 4, 3
  obj.dynamicTFollowLC, obj.speedTFFactor, obj.tFollowDecelBoost = 1., 10, 0.
  for index, value in enumerate((1.6, 1.6, 1.2, 1., .8, .7, .6)):
    setattr(obj, f"cruiseMaxVals{index}", value)
  obj.stop_distance, obj.trafficStopDistanceAdjust, obj.comfort_brake = 6., 2.5, 2.4
  obj._tf_decel_extra, obj._tf_decel_base, obj._tf_mode_factor = 0., None, None
  obj._tf_base_last, obj.t_follow_last, obj._tf_applied = 1.5, 1.5, 1.5
  obj.lane_change_active, obj.mode, obj.v_cruise, obj.stop_dist = False, "acc", 30., 1000.
  obj._consumer_observer = "planner.carrot" if capture.enabled else None
  obj._consumer_read_mono = {}
  return obj


def mpc_class(path):
  fixture = ast.parse((ROOT / "openpilot/selfdrive/controls/tests/test_longitudinal_gap_recovery.py").read_text(encoding="utf-8"))
  loader = next(node for node in fixture.body if isinstance(node, ast.FunctionDef) and node.name == "load_mpc_update")
  namespace = {"ast": ast, "np": np, "math": math, "struct": struct, "SimpleNamespace": NS,
               "LeadGapState": LeadGapState, "gap_reference": gap_reference, "displayed_follow_distance": displayed_follow_distance,
               "LeadComfortState": LeadComfortState, "braking_gap_reference": braking_gap_reference,
               "comfort_path_has_reserve": comfort_path_has_reserve}
  exec(compile(ast.Module(body=[loader], type_ignores=[]), "existing_recording_solver_harness", "exec"), namespace)
  return namespace["load_mpc_update"](path)


def planner_tail(source, capture, mode):
  tree = ast.parse(source)
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LongitudinalPlanner")
  update = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "update")
  start = next(index for index, node in enumerate(update.body) if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "longitudinalActuatorDelay" for target in node.targets))
  namespace = {**vars(preview), "np": np, "DT_MDL": .05, "time": time}
  helper = ast.parse((ROOT / "openpilot/selfdrive/controls/lib/drive_helpers.py").read_text(encoding="utf-8"))
  calc = next(node for node in helper.body if isinstance(node, ast.FunctionDef) and node.name == "get_accel_from_plan")
  exec(compile(ast.Module(body=[calc], type_ignores=[]), "production_plan_action_calculation", "exec"), namespace)
  obj = NS(params=ParamsTrace(), _consumer_observer="planner.longitudinal" if capture.enabled else None,
           mpc=NS(mode=mode, source="lead0", solution_status=0), lead_preview=.2)
  times = 10 * (np.arange(17) / 32) ** 2
  obj.v_desired_trajectory, obj.a_desired_trajectory = 10 + .3 * times, .3 - .2 * times
  obj.j_desired_trajectory = -.2 * np.ones(17)
  obj.update_coasting = lambda *args: None
  obj.apply_signal_stop_prepare = lambda *args: None
  obj.apply_signal_start_guard = lambda *args: None
  lead = NS(status=True, radar=True, radarTrackId=49, aLeadK=-.4)
  car = carrot_instance(carrot_class((ROOT / CARROT).read_text(encoding="utf-8")), capture)
  car.myDrivingMode = DrivingMode.Normal
  namespace.update(self=obj, carrot=car, CONTROL_N_T_IDX=times, v_ego=10., v_cruise=20., v_cruise_kph=72.,
    reset_state=False, force_slow_decel=False, coasting_turn_blocked=False, cutin_predecel_limit=None,
    accel_limits=[-2., 1.], accel_limits_turns=[-2., .8],
    sm={"carState": NS(gasPressed=False, brakePressed=False, aEgo=0.),
        "radarState": NS(leadOne=lead, leadTwo=lead),
        "modelV2": NS(action=NS(desiredAcceleration=-.2, shouldStop=False, desiredVelocity=10.))})
  tail = ast.FunctionDef(name="run_tail", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                         body=update.body[start:], decorator_list=[])
  module = ast.fix_missing_locations(ast.Module(body=[tail], type_ignores=[]))
  exec(compile(module, "production_longitudinal_tail", "exec"), namespace)
  namespace["run_tail"]()
  return {name: getattr(obj, name) for name in ("output_a_target", "output_a_target_base", "output_v_target_now", "output_should_stop",
         "output_j_target_now", "lead_preview", "lead_preview_action_time")}, obj.params.trace


class PlannerConsumerHookTests(unittest.TestCase):
  def test_optional_observer_import_failure_cannot_abort_any_constructor(self):
    original_import = builtins.__import__

    def broken_optional(name, *args, **kwargs):
      if name == PROTOCOL:
        raise ImportError("synthetic optional observer import failure")
      return original_import(name, *args, **kwargs)

    fake_runtime_modules = {}
    for name, symbol in (("signal_start_guard", "SignalStartRuntime"), ("signal_stop_prepare_runtime", "SignalPrepareRuntime")):
      module = ModuleType(name)
      setattr(module, symbol, lambda: NS())
      fake_runtime_modules[f"openpilot.selfdrive.carrot.{name}"] = module

    def construct(relative, source):
      tree = ast.parse(source)
      name = "CarrotPlanner" if relative == CARROT else "LongitudinalPlanner"
      cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
      constructor = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
      namespace = {"np": np, "DT_MDL": .05, "CONTROL_N": 17, "N": 12, "ACCEL_MIN": -3.5, "ACCEL_MAX": 2.,
        "Params": ParamsTrace, "DrivingMode": DrivingMode, "TrafficState": NS(off=0), "XState": NS(cruise=0),
        "DrivingModeDetector": lambda: NS(), "Events": lambda: NS(), "MyMovingAverage": lambda value: NS(size=value),
        "LaneChangeGapPlan": lambda: NS(), "LaneChangeGapTracker": lambda: NS(), "TrafficStopModelLeadMatcher": lambda: NS(),
        "is_volkswagen_meb": lambda _: False, "LongitudinalMpc": lambda **kwargs: NS(**kwargs),
        "FirstOrderFilter": lambda value, tau, dt: NS(x=value, tau=tau, dt=dt), "PrepareAccel": lambda: NS(),
        "CruiseCoastingPlan": lambda: NS()}
      exec(compile(ast.Module(body=[constructor], type_ignores=[]), "production_constructor", "exec"), namespace)
      obj = NS()
      if relative == CARROT:
        namespace["__init__"](obj, signal_assist=object(), start_guard=object(), stop_prepare=object())
        selected = {key: getattr(obj, key) for key in ("frame", "params_count", "stop_distance", "tFollowGap1", "cruiseMaxVals0", "myDrivingMode")}
      else:
        namespace["__init__"](obj, NS(brand="hyundai"))
        selected = {key: getattr(obj, key) for key in ("a_desired", "output_a_target", "output_should_stop", "lead_preview", "dt")}
      return obj, selected, obj.params.trace

    with patch.dict(sys.modules, fake_runtime_modules), patch("builtins.__import__", side_effect=broken_optional):
      for enabled in ("0", "1"):
        with patch.dict(os.environ, {"CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS": enabled}):
          for relative in (CARROT, PLANNER):
            with self.subTest(consumer=relative, enabled=enabled):
              before, expected, reads = construct(relative, baseline(relative))
              after, actual, actual_reads = construct(relative, (ROOT / relative).read_text(encoding="utf-8"))
              self.assertEqual(expected, actual)
              self.assertEqual(reads, actual_reads)
              self.assertIsNone(after._consumer_observer)
          cls = mpc_class(ROOT / MPC)
          mpc = cls()
          self.assertIsNone(mpc._consumer_observer)
          self.assertEqual((13, 8), mpc.params.shape)

  def test_parameter_read_sequence_tf_state_and_accel_match_original_fms(self):
    expected = None
    for source, enabled, failing in ((baseline(CARROT), False, False), ((ROOT / CARROT).read_text(encoding="utf-8"), False, False),
                                      ((ROOT / CARROT).read_text(encoding="utf-8"), True, False), ((ROOT / CARROT).read_text(encoding="utf-8"), True, True)):
      capture = Capture(enabled, failing)
      with capture.installed():
        obj = carrot_instance(carrot_class(source), capture)
        outputs = []
        for frame in range(120):
          obj._params_update()
          personality = frame % 4
          outputs.append((obj.get_T_FOLLOW(personality, 15., -.7 if frame < 60 else .2), obj.get_carrot_accel(15.)))
          if enabled:
            obj._observe_control_cache({"selfdriveState": NS(personality=personality)})
        state = {key: deepcopy(value) for key, value in vars(obj).items()
                 if key != "params" and not key.startswith("_consumer") and key != "drivingModeDetector"}
        result = outputs, obj.params.trace, state
        if expected is None:
          expected = result
        self.assertEqual(expected, result)
        if enabled and not failing:
          first, last = capture.payloads[0][1], capture.payloads[-1][1]
          self.assertEqual("unknown", first["fields"]["TFollowGap1"]["state"])
          self.assertEqual("loaded", last["fields"]["TFollowGap1"]["state"])
          self.assertEqual(.6, last["fields"]["TFollowGap1"]["read_value"])
          self.assertGreater(last["fields"]["TFollowGap1"]["read_mono_ns"], 0)

  def test_mpc_solver_inputs_weights_and_state_match_original_off_on_failure(self):
    frozen = ROOT / ".analysis/consumer_baseline" / MPC
    # The paired loader needs a path; fresh clones have the exact source in Git.
    if not frozen.exists():
      directory = tempfile.TemporaryDirectory()
      self.addCleanup(directory.cleanup)
      frozen = Path(directory.name) / "long_mpc.py"
      frozen.write_text(baseline(MPC), encoding="utf-8")
    self.assertEqual(hashlib.sha256(baseline(MPC).encode()).hexdigest(), BASE_HASHES[MPC])
    for mode, lane_change in (("acc", False), ("acc", True), ("blended", False)):
      expected = None
      for path, enabled, failing in ((frozen, False, False), (ROOT / MPC, False, False),
                                     (ROOT / MPC, True, False), (ROOT / MPC, True, True)):
        capture = Capture(enabled, failing)
        with capture.installed():
          obj = carrot_instance(carrot_class((ROOT / CARROT).read_text(encoding="utf-8")), capture)
          for _ in range(40):
            obj._params_update()
          obj.mode, obj.lane_change_active = mode, lane_change
          obj.lane_change_gap = NS(active=lane_change, credit=lambda *args: np.full(13, .1))
          cls = mpc_class(path)
          mpc = cls(mode=mode)
          mpc.set_cur_state(15., -.3)
          mpc.set_accel_limits(-1.2, 2.)
          lead = NS(status=True, radar=True, radarTrackId=49, dRel=45., vLead=16., vRel=1., aLeadK=.5, aLeadTau=1.5, modelProb=1.)
          absent = NS(status=False, radar=False, radarTrackId=-1, vRel=0., aLeadK=0., modelProb=0.)
          for _ in range(5):
            mpc.update(obj, False, NS(leadOne=lead, leadTwo=absent), 30., *(np.zeros(13) for _ in range(4)),
                       personality=1, lead_accel_response_enabled=True, lead_track_frames=(10, 0), cutout_relief_enabled=True)
          result = {key: getattr(mpc, key).tobytes() for key in ("params", "yref", "x0", "x_sol", "lead_gap_margins")}
          result.update(source=mpc.source, t_follow=mpc.t_follow, desired_distance=mpc.desired_distance,
                        a_change_cost=mpc.a_change_cost, jerk_cost_factor=mpc.jerk_cost_factor,
                        weights={key: value.tobytes() for key, value in mpc.solver.weights.items()})
          if expected is None:
            expected = result
          self.assertEqual(expected, result)
          if enabled and not failing:
            payloads = [payload for consumer, payload, _ in capture.payloads if consumer == "planner.long_mpc"]
            self.assertTrue(payloads)
            payload = payloads[-1]
            self.assertEqual(mode, payload["context"]["mode"])
            self.assertEqual(lane_change, payload["context"]["lc_credit_used"])
            self.assertEqual("used", payload["fields"]["TFollowGap2"]["state"])
            self.assertEqual("bypassed", payload["fields"]["TFollowGap1"]["state"])
            self.assertEqual("used" if mode == "acc" else "bypassed", payload["fields"]["TrafficStopDistanceAdjust"]["state"])

  def test_planner_direct_reads_and_final_targets_match_original_in_both_modes(self):
    for mode in ("acc", "blended"):
      expected = None
      for source, enabled, failing in ((baseline(PLANNER), False, False), ((ROOT / PLANNER).read_text(encoding="utf-8"), False, False),
                                        ((ROOT / PLANNER).read_text(encoding="utf-8"), True, False), ((ROOT / PLANNER).read_text(encoding="utf-8"), True, True)):
        capture = Capture(enabled, failing)
        with capture.installed():
          result = planner_tail(source, capture, mode)
        if expected is None:
          expected = result
        self.assertEqual(expected, result)
        self.assertEqual([("float", "LongActuatorDelay"), ("float", "VEgoStopping")], result[1])
        if enabled and not failing:
          self.assertEqual(1, len(capture.payloads))
          fields = capture.payloads[0][1]["fields"]
          self.assertEqual(.3, fields["LongActuatorDelay"]["effective_value"])
          self.assertEqual(.01, fields["VEgoStopping"]["effective_value"])
          self.assertEqual("used", fields["LongActuatorDelay"]["state"])
          self.assertEqual("used" if mode == "acc" else "bypassed", fields["CruiseMaxVals0"]["state"])

  def test_real_protocol_validates_all_three_actual_producer_factories(self):
    capture = Capture(True)
    with capture.installed():
      carrot = carrot_instance(carrot_class((ROOT / CARROT).read_text(encoding="utf-8")), capture)
      for _ in range(40):
        carrot._params_update()
      carrot._observe_control_cache({"selfdriveState": NS(personality=1)})
      cls = mpc_class(ROOT / MPC)
      mpc = cls()
      mpc.set_cur_state(15., -.3)
      mpc.set_accel_limits(-1.2, 2.)
      lead = NS(status=True, radar=True, radarTrackId=49, dRel=45., vLead=16., vRel=1., aLeadK=.5, aLeadTau=1.5, modelProb=1.)
      absent = NS(status=False, radar=False, radarTrackId=-1, vRel=0., aLeadK=0., modelProb=0.)
      mpc.update(carrot, False, NS(leadOne=lead, leadTwo=absent), 30., *(np.zeros(13) for _ in range(4)), personality=1)
      planner_tail((ROOT / PLANNER).read_text(encoding="utf-8"), capture, "acc")
    produced = {consumer: (payload, metadata) for consumer, payload, metadata in capture.payloads}
    self.assertEqual({"planner.carrot", "planner.long_mpc", "planner.longitudinal"}, set(produced))
    protocol = importlib.import_module(PROTOCOL)

    class Sender:
      def __init__(self):
        self.data = []

      def send(self, data):
        self.data.append(data)

      def close(self):
        pass

    sender = Sender()
    pump = protocol._Pump(sender=sender, identity={"boot_id": "11111111-1111-1111-1111-111111111111", "pid": 123,
      "process_instance_id": "a" * 32}, hash_provider=lambda consumer: {protocol.OBSERVER_PATH: None, protocol.CONSUMER_PATHS[consumer]: None})
    self.addCleanup(pump.close)
    observers = []
    for consumer, (payload, metadata) in produced.items():
      observer = protocol._Observer(consumer, pump)
      observers.append(observer)
      protocol.observe(observer, lambda payload=payload: payload, **metadata)
    deadline = time.monotonic() + 2.
    while len(sender.data) < 3 and time.monotonic() < deadline:
      time.sleep(.01)
    self.assertEqual(3, len(sender.data), [(observer.consumer_id, observer.dropped) for observer in observers])
    validated = [protocol.validate_event(json.loads(data)) for data in sender.data]
    self.assertEqual(set(produced), {event["consumer_id"] for event in validated})
    self.assertTrue(all(event["phase"] == "sampled_use" and event["fields"] for event in validated))


if __name__ == "__main__":
  unittest.main()
