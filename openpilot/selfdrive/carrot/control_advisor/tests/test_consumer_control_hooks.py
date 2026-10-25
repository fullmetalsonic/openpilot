"""Production control bodies with deterministic inputs; no IPC, GPU, or Params store."""
import ast
from collections import deque
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest

try:
  import numpy as np
except ImportError:
  np = None

from openpilot.selfdrive.carrot.control_advisor import consumer_observer as observer


ROOT = Path(__file__).resolve().parents[5]
BASELINE_AST = {
  'openpilot/selfdrive/controls/controlsd.py': '1ea89cc4997dac407434070b07adcc86dfdb205f91e955012109c1e74913703c',
  'openpilot/selfdrive/controls/lib/latcontrol_torque.py': '2cf717514b1b5caef9e72f0af869198dea07965c44f0762fba7716f6042bbefd',
  'openpilot/selfdrive/controls/lib/longcontrol.py': '6c2cc4882f3bf35b11acdaeb67fd5c76eb812fc9ac4fd7f46d5dc8299d8c4b7e',
  'openpilot/selfdrive/controls/lib/lateral_planner.py': '848f00a75f172296dfdfe865d12b34403a0641f49018cf67b15d417cc3da04f6',
  'openpilot/selfdrive/controls/lib/lane_planner_2.py': 'e495f66874accf267c48d314cd51b86e33d6c96e9e3680fbc98a8f5e2ad801a0',
  'openpilot/selfdrive/modeld/modeld.py': 'e595c12e2803a396263d91e0b15958457540a8156c19f1e121b1ce23435b2fec',
}


def tree(path):
  return ast.parse((ROOT / path).read_text(encoding='utf8'))


class WithoutObservation(ast.NodeTransformer):
  """Undo only observation guards and the five order-preserving read bindings."""
  def __init__(self, module):
    names = {'_advisor_live_sr', '_advisor_sr_rate', '_advisor_custom_sr', '_advisor_nnff_delay', '_advisor_live_delay'}
    self.lifts = {node.targets[0].id: node.value for node in ast.walk(module)
                  if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id in names}

  def visit_Try(self, node):
    if any(isinstance(item, ast.ImportFrom) and item.module == observer.__name__ for item in node.body):
      return None
    return self.generic_visit(node)

  def visit_If(self, node):
    if any((isinstance(item, ast.Name) and item.id.startswith('_advisor_'))
           or (isinstance(item, ast.Attribute) and item.attr.startswith('_advisor_'))
           or (isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.startswith('_advisor_'))
           for item in ast.walk(node.test)):
      return None
    return self.generic_visit(node)

  def visit_Assign(self, node):
    if any((isinstance(item, ast.Name) and item.id.startswith('_advisor_'))
           or (isinstance(item, ast.Attribute) and item.attr.startswith('_advisor_'))
           for target in node.targets for item in ast.walk(target)):
      return None
    return self.generic_visit(node)

  def visit_Name(self, node):
    return deepcopy(self.lifts[node.id]) if node.id in self.lifts else node

  def visit_ClassDef(self, node):
    if node.name == 'LanePlanner':
      constructor = next(item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == '__init__')
      if [arg.arg for arg in constructor.args.args] == ['self', 'CP']:
        constructor.args.args.pop()
        constructor.args.defaults.pop()
    return self.generic_visit(node)

  def visit_Call(self, node):
    if isinstance(node.func, ast.Name) and node.func.id == 'LanePlanner':
      node.keywords = [keyword for keyword in node.keywords if keyword.arg != 'CP']
    return self.generic_visit(node)


def execute(nodes, environment):
  module = ast.fix_missing_locations(ast.Module(body=deepcopy(nodes), type_ignores=[]))
  exec(compile(module, '<actual-control-source>', 'exec'), environment)
  return environment


def functions(path, names, environment):
  nodes = [node for node in tree(path).body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
  return execute(nodes, environment)


def method(path, name):
  return next(node for node in ast.walk(tree(path)) if isinstance(node, ast.FunctionDef) and node.name == name)


class Parameters:
  def __init__(self, values):
    self.values, self.reads = values, []

  def get_float(self, key):
    self.reads.append(key)
    return float(self.values.get(key, 0))

  def get_int(self, key):
    self.reads.append(key)
    return int(self.values.get(key, 0))


class Capture:
  def __init__(self, *, fail=False, pump=None):
    self.events, self.observers, self.now, self.fail, self.external_pump = [], [], 0, fail, pump

  def clock(self):
    self.now += 1_000_000_000
    return self.now

  def create(self, consumer, *, car_params=None):
    result = observer._Observer(consumer, self.external_pump or self, clock_ns=self.clock)
    self.observers.append(result)
    return result

  def enqueue(self, owner, sample):
    if self.fail:
      return False
    event = observer.validate_event({
      'schema_version': 1, 'consumer_id': owner.consumer_id, 'boot_id': '00000000-0000-0000-0000-000000000001',
      'pid': 123, 'process_instance_id': '1' * 32, 'consumer_instance_id': '2' * 32,
      'source_hashes': {observer.OBSERVER_PATH: None, observer.CONSUMER_PATHS[owner.consumer_id]: None}, **sample,
    })
    # Acceptance includes the real serializer boundary, not only control parity.
    self.events.append(json.loads(json.dumps(event, allow_nan=False)))
    return True


class ControlAstTests(unittest.TestCase):
  def test_observation_removal_recovers_original_full_control_ast(self):
    for path, expected in BASELINE_AST.items():
      with self.subTest(path=path):
        module = tree(path)
        restored = WithoutObservation(module).visit(module)
        digest = hashlib.sha256(ast.dump(restored, include_attributes=False).encode()).hexdigest()
        self.assertEqual(expected, digest)


@unittest.skipIf(np is None, 'NumPy is required for the actual control calculations')
class ConsumerControlHooksTests(unittest.TestCase):
  def base(self, capture=None, *, bad_factory=False):
    from openpilot.common.pid import PIDController
    from openpilot.selfdrive.modeld.constants import ModelConstants, Plan
    def fail_field(*args, **kwargs):
      raise RuntimeError('injected observation factory failure')
    return {'np': np, 'math': math, 'deque': deque, 'NS': NS, 'DT_CTRL': .01, 'DT_MDL': .05,
            'ModelConstants': ModelConstants, 'Plan': Plan, 'PIDController': PIDController,
            'create_observer': capture.create if capture else lambda *args, **kwargs: None, 'observe': observer.observe,
            'field': fail_field if bad_factory else observer.field}

  def drive_helpers(self, environment):
    source = tree('openpilot/selfdrive/controls/lib/drive_helpers.py')
    names = {'clamp', 'smooth_value', 'clip_curvature', 'get_lag_adjusted_curvature',
             'get_accel_from_plan', 'curv_from_psis', 'get_curvature_from_plan'}
    constants = {'MIN_SPEED', 'CONTROL_N', 'MAX_CURVATURE', 'MAX_LATERAL_JERK',
                 'MAX_LATERAL_ACCEL_NO_ROLL', 'MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED'}
    nodes = [node for node in source.body if (isinstance(node, ast.FunctionDef) and node.name in names)
             or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets))]
    environment['ACCELERATION_DUE_TO_GRAVITY'] = 9.81
    return execute(nodes, environment)

  def test_vehicle_model_resolved_values_and_read_order_match_off_on(self):
    from openpilot.selfdrive.controls.lib.steer_ratio import resolve_vehicle_model_steer_ratio
    source = method('openpilot/selfdrive/controls/controlsd.py', 'state_control')
    stop = next(index for index, node in enumerate(source.body) if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'steer_angle_without_offset' for target in node.targets))
    source.body = source.body[:stop]
    for rate, custom, expected in ((88, 0, 12.32), (88, 150, 15.0), (0, 0, 14.0)):
      results = []
      for enabled in (False, True):
        captured = Capture() if enabled else None
        params, updates = Parameters({'SteerRatioRate': rate, 'CustomSR': custom}), []
        live = NS(stiffnessFactor=.8, steerRatio=14.)
        sm = type('Messages', (dict,), {'frame': 7})(carState=NS(), liveParameters=live)
        control = NS(sm=sm, CP=NS(), params=params, lateral_startup=NS(update=lambda *a: True),
                     VM=NS(update_params=lambda *args: updates.append(args)))
        if captured:
          control._advisor_vehicle_model = captured.create('controls.vehicle_model')
        env = self.base(captured)
        env.update(resolve_vehicle_model_steer_ratio=resolve_vehicle_model_steer_ratio, lateral_vehicle_parameters=lambda *args: live)
        execute([source], env)['state_control'](control)
        results.append((updates, params.reads))
        self.assertAlmostEqual(expected, updates[-1][1])
        if captured:
          self.assertEqual(1, len(captured.events))
          event = captured.events[0]
          self.assertAlmostEqual(expected, event['context']['resolved_steer_ratio'])
          self.assertEqual('bypassed' if custom else 'used', event['fields']['SteerRatioRate']['state'])
      self.assertEqual(results[0], results[1])
      self.assertEqual(['SteerRatioRate', 'CustomSR'], results[0][1])

  def lateral_target(self, enabled, *, lane, active=True, capture=None, bad_factory=False):
    captured = capture if capture is not None else (Capture() if enabled else None)
    original = method('openpilot/selfdrive/controls/controlsd.py', 'state_control')
    begin = next(index for index, node in enumerate(original.body) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'lat_plan' for target in node.targets))
    end = next(index for index, node in enumerate(original.body) if isinstance(node, ast.Assign)
               and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'clip_curvature')
    env = self.drive_helpers(self.base(captured, bad_factory=bad_factory))
    plan = NS(useLaneLines=lane, curvatures=[.001] * 17, psis=[.001 * i for i in range(17)], distances=list(range(17)))
    sm = type('Messages', (dict,), {'frame': 12})(lateralPlan=plan, carrotMan=NS(vTurnSpeed=100), liveDelay=NS(lateralDelay=.2))
    params = Parameters({'LatSmoothSec': 13, 'SteerActuatorDelay': 0})
    params.get_int = lambda _: 0
    control = NS(sm=sm, CP=NS(), params=params, is_vw_meb=False, desired_curvature=.0001, curvature=.0002)
    if captured:
      control._advisor_lateral_target = captured.create('controls.lateral_target')
    env.update(self=control, CC=NS(latActive=active), CS=NS(vEgo=15.), lp=NS(roll=0.), model_v2=NS(action=NS(desiredCurvature=.002)))
    execute(original.body[begin:end + 1], env)
    return (control.desired_curvature, params.reads), captured

  def test_selected_lane_target_uses_live_delay_and_bypassed_path_does_not_read(self):
    for lane, active in ((False, True), (True, False), (True, True)):
      old, _ = self.lateral_target(False, lane=lane, active=active)
      new, captured = self.lateral_target(True, lane=lane, active=active)
      self.assertEqual(old, new)
      self.assertEqual(['LatSmoothSec', 'SteerActuatorDelay'] if lane and active else [], new[1])
      self.assertEqual(1, len(captured.events))
      delay = captured.events[0]['fields']['SteerActuatorDelay']
      self.assertEqual('used' if lane and active else 'bypassed', delay['state'])
      if lane and active:
        self.assertEqual(.2, delay['effective_value'])
        self.assertEqual(0., delay['read_value'])

  def test_factory_and_enqueue_failure_preserve_target_output_and_reads(self):
    baseline, _ = self.lateral_target(False, lane=True)
    faulty, capture = self.lateral_target(True, lane=True, bad_factory=True)
    self.assertEqual(baseline, faulty)
    self.assertEqual([], capture.events)
    self.assertGreater(capture.observers[0].dropped, 0)
    failed, capture = self.lateral_target(True, lane=True, capture=Capture(fail=True))
    self.assertEqual(baseline, failed)
    self.assertGreater(capture.observers[0].dropped, 0)

  def long_run(self, brand, enabled):
    from openpilot.selfdrive.controls.lib.cruise_coasting import CruiseCoastingControl, MAX_PLAN_AGE, coasting_relief, no_coasting_lead
    captured = Capture() if enabled else None
    params = Parameters({'StoppingAccel': -70, 'LongTuningKpV': 85, 'LongTuningKiV': 25, 'LongTuningKf': 120})
    env = self.base(captured)
    env.update(LongCtrlState=NS(off='off', pid='pid', stopping='stopping', starting='starting'), Params=lambda: params,
               CruiseCoastingControl=CruiseCoastingControl, MAX_PLAN_AGE=MAX_PLAN_AGE, coasting_relief=coasting_relief,
               no_coasting_lead=no_coasting_lead, HYUNDAI_LONGITUDINAL_KP=1., HYUNDAI_LONGITUDINAL_KI=0.,
               HYUNDAI_LONGITUDINAL_KF=1., STOPPING_ACCEL_DEFAULT=-50, STOPPING_ACCEL_MIN=-100, STOPPING_ACCEL_MAX=-50)
    functions('openpilot/selfdrive/controls/lib/longcontrol.py', {'LongControl', 'long_control_state_trans'}, env)
    cp = NS(brand=brand, longitudinalTuning=NS(kpBP=[0.], kpV=[.9], kiBP=[0.], kiV=[.1], kf=1.),
            startingState=False, vEgoStarting=.5, stopAccel=-.5, startAccel=.1, stoppingDecelRate=.8, openpilotLongitudinalControl=True)
    control = env['LongControl'](cp)
    cs = NS(softHoldActive=0, vEgo=15., aEgo=.1, brakePressed=False, gasPressed=False, carrotCruise=False,
            cruiseState=NS(standstill=False), vCruise=15.)
    plan = NS(aTarget=.2, vTargetNow=15.1, jTargetNow=.1, shouldStop=False)
    radar = NS(leadOne=NS(status=False, dRel=20.))
    outputs = []
    for frame in range(205):
      plan.shouldStop = 80 <= frame < 110
      cs.softHoldActive = int(95 <= frame < 105)
      output = control.update(frame >= 5, cs, plan, (-3., 2.), 0., radar)
      outputs.append((*output, control.long_control_state, control.pid.i))
    return (outputs, params.reads), captured

  def test_longitudinal_fixed_and_adjustable_paths_match_and_validate_wire_payloads(self):
    for brand, expected in (('hyundai', (1., 0., 1.)), ('toyota', (0., 0., 1.)), ('mock', (.85, .025, 1.2))):
      with self.subTest(brand=brand):
        old, _ = self.long_run(brand, False)
        new, captured = self.long_run(brand, True)
        self.assertEqual(old, new)
        self.assertEqual(205, len(captured.events))
        last = captured.events[-1]['fields']
        self.assertEqual(expected, tuple(last[key]['effective_value'] for key in ('LongTuningKpV', 'LongTuningKiV', 'LongTuningKf')))
        self.assertEqual(-70 * .01, last['StoppingAccel']['effective_value'])
        if brand in ('hyundai', 'toyota'):
          self.assertNotIn('LongTuningKpV', new[1])

  def test_planner_weights_payload_uses_actual_post_solver_values(self):
    captured = Capture()
    env = self.base(captured)
    obj = NS(_advisor_lateral=captured.create('planner.lateral'), lateralPathCost=np.float64(1.2), lateralMotionCost=.11,
             pathOffset=.04, lanelines_active=True, useLaneLineMode=True)
    env.update(self=obj, LATERAL_ACCEL_COST=.2, LATERAL_JERK_COST=.04, STEERING_RATE_COST=700., model_active=True)
    node = next(node for node in method('openpilot/selfdrive/controls/lib/lateral_planner.py', 'update').body
                if isinstance(node, ast.If) and '_advisor_lateral' in ast.unparse(node.test))
    execute([node], env)
    self.assertEqual(1, len(captured.events))
    fields = captured.events[0]['fields']
    self.assertEqual(1.2, fields['LatMpcPathCost']['effective_value'])
    self.assertEqual(700., fields['LatMpcSteeringRateCost']['effective_value'])
    obj.lanelines_active = False
    execute([node], env)
    self.assertTrue(all(item['state'] == 'used' for item in captured.events[-1]['fields'].values()))
    self.assertFalse(captured.events[-1]['context']['lanes_active'])

  def torque_run(self, enabled, *, nnff=False, override=False, model_good=False):
    from abc import ABC, abstractmethod
    from openpilot.common.filter_simple import FirstOrderFilter
    captured = Capture() if enabled else None
    params = Parameters({'LateralTorqueCustom': 1, 'LateralTorqueAccelFactor': 2500, 'LateralTorqueFriction': 150,
                         'LateralTorqueKpV': 60, 'LateralTorqueKiV': 10, 'LateralTorqueKf': 90,
                         'LateralTorqueKd': 40, 'SteerActuatorDelay': 10})
    env = self.base(captured)
    env.update(ABC=ABC, abstractmethod=abstractmethod, FirstOrderFilter=FirstOrderFilter, Params=lambda: params,
               ACCELERATION_DUE_TO_GRAVITY=9.81, LatControlInputs=lambda *values: values, CONTROL_N=17)
    functions('openpilot/selfdrive/controls/lib/latcontrol.py', {'LatControl'}, env)
    source = tree('openpilot/selfdrive/controls/lib/latcontrol_torque.py')
    nodes = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign))]
    execute(nodes, env)
    tuning = NS(kp=1., ki=.1, kf=1., useSteeringAngle=True, steeringAngleDeadzoneDeg=0.,
                latAccelFactor=3., latAccelOffset=0., friction=.1)
    cp = NS(steerLimitTimer=1., steerActuatorDelay=.2, lateralTuning=NS(torque=NS(as_builder=lambda: deepcopy(tuning))))
    torque_calls = []
    def torque_function(inputs, parameters, *args, **kwargs):
      torque_calls.append(inputs)
      return inputs[0] / parameters.latAccelFactor
    ci = NS(use_nnff=nnff, use_nnff_lite=not nnff, get_ff_nn=lambda values: values[1] * .3,
            lat_torque_nn_model=NS(friction_override=override), torque_from_lateral_accel=lambda: torque_function)
    env['log'] = NS(ControlsState=NS(LateralTorqueState=NS(new_message=lambda: NS(error=0.))))
    control = env['LatControlTorque'](cp, ci)
    cs = NS(vEgo=15., aEgo=.1, steeringAngleDeg=.3, steeringRateDeg=.05, steeringPressed=False)
    vm = NS(get_steer_from_curvature=lambda curvature, speed, roll: curvature * 15,
            calc_curvature=lambda angle, speed, roll: angle / 15)
    model = NS(orientation=NS(x=[0.] * 33, y=[0.] * 33), acceleration=NS(y=[.2] * 33)) if model_good else None
    outputs = []
    for frame in range(40):
      if frame == 15:
        params.values['LateralTorqueCustom'] = 0
      if frame == 25:
        params.values['SteerActuatorDelay'] = 80
      control.update_live_torque_params(3.5, .01, .3)
      output, angle, log = control.update(frame >= 3, cs, vm, NS(roll=0., angleOffsetDeg=0.), False, .001,
                                          NS(angularVelocity=[], orientationNED=[]), False, model_data=model)
      outputs.append((output, angle, vars(log)))
    return (outputs, params.reads, torque_calls), captured

  def test_torque_custom_to_live_retains_actual_gains_and_nnff_constructor_delay(self):
    old, _ = self.torque_run(False)
    new, captured = self.torque_run(True)
    self.assertEqual(old, new)
    self.assertEqual(40, len(captured.events))
    self.assertEqual(1, new[1].count('SteerActuatorDelay'))
    last = captured.events[-1]['fields']
    self.assertEqual(3.5, last['LateralTorqueAccelFactor']['effective_value'])
    self.assertEqual('learned', last['LateralTorqueAccelFactor']['source'])
    self.assertEqual(.6, last['LateralTorqueKpV']['effective_value'])
    self.assertEqual('stored', last['LateralTorqueKpV']['source'])
    self.assertEqual(0., last['LateralTorqueKd']['effective_value'])
    self.assertEqual('unsupported', last['LateralTorqueKd']['state'])
    self.assertEqual(.1, last['SteerActuatorDelay']['effective_value'])
    self.assertEqual(.1, last['SteerActuatorDelay']['read_value'])
    self.assertEqual(.4, captured.events[-1]['context']['nnff_jerk_lookahead_s'])

  def test_nnff_marks_torque_parameters_used_only_when_actual_function_is_called(self):
    for good, override, used in ((True, False, False), (True, True, True), (False, False, True)):
      with self.subTest(model_good=good, override=override):
        old, _ = self.torque_run(False, nnff=True, override=override, model_good=good)
        new, captured = self.torque_run(True, nnff=True, override=override, model_good=good)
        self.assertEqual(old, new)
        self.assertEqual(used, bool(new[2]))
        last = captured.events[-1]
        for key in ('LateralTorqueAccelFactor', 'LateralTorqueFriction'):
          self.assertEqual('used' if used else 'bypassed', last['fields'][key]['state'])

  def lane_run(self, enabled):
    from openpilot.common.filter_simple import FirstOrderFilter
    captured = Capture() if enabled else None
    params = Parameters({'LatMpcInputOffset': 6, 'AdjustLaneOffset': 0})
    env = self.base(captured)
    env.update(FirstOrderFilter=FirstOrderFilter, Params=lambda: params)
    source = tree('openpilot/selfdrive/controls/lib/lane_planner_2.py')
    execute([node for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign))], env)
    control = env['LanePlanner']()
    control.lanefull_mode = True
    control.ll_t, control.ll_x = np.linspace(0, 3, 33), np.linspace(0, 90, 33)
    control.lll_y, control.rll_y = np.full(33, -1.8), np.full(33, 1.8)
    control.lll_prob = control.rll_prob = .99
    control.lll_std = control.rll_std = .1
    control.lane_width_left = control.lane_width_right = 3.6
    outputs = []
    for frame in range(24):
      path = np.column_stack([np.linspace(0, 90, 33), np.linspace(0, .5, 33), np.zeros(33)])
      result, active = control.get_d_path(NS(), 15., np.linspace(0, 3, 33), path, 80.)
      outputs.append((result.tolist(), active))
    return (outputs, params.reads), captured

  def test_lane_input_time_warp_payload_is_selected_only_after_actual_lane_blend(self):
    old, _ = self.lane_run(False)
    new, captured = self.lane_run(True)
    self.assertEqual(old, new)
    self.assertEqual(24, len(captured.events))
    self.assertEqual('bypassed', captured.events[0]['fields']['LatMpcInputOffset']['state'])
    self.assertEqual('used', captured.events[-1]['fields']['LatMpcInputOffset']['state'])
    self.assertEqual(.06, captured.events[-1]['fields']['LatMpcInputOffset']['effective_value'])
    self.assertEqual(1.06, captured.events[-1]['context']['time_warp_factor'])

  def model_action(self, enabled):
    captured = Capture() if enabled else None
    env = self.drive_helpers(self.base(captured))
    env.update(log=NS(ModelDataV2=NS(Action=lambda **kwargs: NS(**kwargs))), LAT_SMOOTH_SECONDS=0.,
               LONG_SMOOTH_SECONDS=.3, MIN_LAT_CONTROL_SPEED=.3)
    functions('openpilot/selfdrive/modeld/modeld.py', {'get_lat_smooth_seconds_dynamic', 'get_action_from_model'}, env)
    body = method('openpilot/selfdrive/modeld/modeld.py', 'main').body
    def assigns(node, name):
      return ((isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))
              or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name))
    loop = next(node for node in body if isinstance(node, ast.While) and any(assigns(item, 'frame_delay') for item in node.body))
    begin = next(index for index, node in enumerate(loop.body) if assigns(node, 'frame_delay'))
    end = next(index for index, node in enumerate(loop.body) if assigns(node, 'inputs'))
    output = next(node for node in loop.body if isinstance(node, ast.If) and ast.unparse(node.test) == 'model_output is not None')
    first = next(index for index, node in enumerate(output.body) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Tuple) and any(isinstance(item, ast.Name) and item.id == 'lat_smooth_seconds_dynamic'
                                                               for item in target.elts) for target in node.targets))
    last = next(index for index, node in enumerate(output.body) if assigns(node, 'prev_action'))
    class StandardDeviation:
      def __getitem__(self, _index):
        return .25
    plan = np.zeros((1, 33, 15))
    plan[0, :, 3] = 15.
    plan[0, :, 6] = .2
    env.update(_advisor_action=captured.create('modeld.action') if captured else None,
               _advisor_next_lat_component=.08, _advisor_next_lat_source='learned', lat_delay_dynamic=.1,
               long_delay=.3, lat_smooth_seconds=.2, custom_lat_delay=.4, vEgoStopping=.05,
               sm={'liveDelay': NS(lateralDelay=.2)}, frame=100, v_ego=15.,
               vec_desire=np.zeros(8), traffic_convention=np.array([1., 0.]),
               model_output={'plan': plan, 'plan_stds': StandardDeviation(), 'action': np.array([[.2, .3]])},
               prev_action=NS(desiredCurvature=.001, desiredAcceleration=.1, desiredVelocity=15.))
    execute(loop.body[begin:end + 1], env)
    execute(output.body[first:last + 1], env)
    return (vars(env['action']), env['inputs']['action_t'].tolist(), env['lat_delay_dynamic']), captured

  def test_model_action_reports_prior_delay_used_and_current_next_delay_separately(self):
    old, _ = self.model_action(False)
    new, captured = self.model_action(True)
    self.assertEqual(old, new)
    self.assertEqual(1, len(captured.events))
    event = captured.events[0]
    self.assertAlmostEqual(.175, event['context']['lat_action_t'])
    self.assertEqual(.1, event['context']['lat_delay_used_s'])
    self.assertAlmostEqual(1., event['context']['next_lat_delay_s'])
    self.assertEqual(.08, event['fields']['SteerActuatorDelay']['effective_value'])
    self.assertAlmostEqual(.6, event['fields']['LatSmoothSec']['effective_value'])

  def test_real_sender_failure_is_contained_after_valid_source_factory(self):
    class Sender:
      attempted = threading.Event()
      def send(self, _data):
        self.attempted.set()
        raise OSError('injected unavailable sender')
      def close(self):
        pass
    sender = Sender()
    pump = observer._Pump(sender=sender, identity={'boot_id': None, 'pid': 123, 'process_instance_id': '1' * 32},
                          hash_provider=lambda cid: {observer.OBSERVER_PATH: None, observer.CONSUMER_PATHS[cid]: None})
    try:
      baseline, _ = self.lateral_target(False, lane=True)
      result, captured = self.lateral_target(True, lane=True, capture=Capture(pump=pump))
      self.assertEqual(baseline, result)
      self.assertTrue(sender.attempted.wait(1.))
    finally:
      pump.close(timeout=1.)
    self.assertGreater(captured.observers[0].dropped, 0)


if __name__ == '__main__':
  unittest.main()
