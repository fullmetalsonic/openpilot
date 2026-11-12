import ast
import math
from functools import partial
from numbers import Number
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np
import pytest

from openpilot.cereal import car, log
from openpilot.common.realtime import DT_CTRL
from openpilot.common.constants import CV
from opendbc.car.vehicle_model import VehicleModel
from opendbc.car.interfaces import CarInterfaceBase
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature, get_lag_adjusted_curvature
from openpilot.selfdrive.controls.lib.laneless_curve_centering import LanelessCurveCentering
from openpilot.selfdrive.controls.lib.latcontrol import MIN_LATERAL_CONTROL_SPEED
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.lateral_readiness import LateralStartupGate, lateral_vehicle_parameters
from openpilot.selfdrive.controls.lib.steer_ratio import resolve_vehicle_model_steer_ratio
from openpilot.selfdrive.controls.tests.test_lateral_readiness import Messages


def control_fixture():
  # Execute the complete production state_control and actual torque controller,
  # replacing only IPC/settings, the longitudinal loop and hardware construction.
  path = Path(__file__).parents[1] / 'controlsd.py'
  tree = ast.parse(path.read_text(encoding='utf8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Controls')
  names = {'lateral_control_allowed', 'curve_centered_target', 'state_control'}
  nodes = [n for n in tree.body + cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
  ns = {'math': math, 'np': np, 'Number': Number, 'car': car, 'DT_CTRL': DT_CTRL, 'CV': CV,
        'MIN_LATERAL_CONTROL_SPEED': MIN_LATERAL_CONTROL_SPEED, 'LaneChangeState': log.LaneChangeState,
        'LaneChangeDirection': log.LaneChangeDirection, 'ACTUATOR_FIELDS': tuple(car.CarControl.Actuators.schema.fields),
        'clip_curvature': clip_curvature, 'get_lag_adjusted_curvature': get_lag_adjusted_curvature,
        'resolve_vehicle_model_steer_ratio': resolve_vehicle_model_steer_ratio,
        'lateral_vehicle_parameters': lateral_vehicle_parameters,
        'cloudlog': NS(error=lambda msg: pytest.fail(msg)), 'log': log, 'time': NS(monotonic_ns=lambda: 0)}
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
  cp = car.CarParams.new_message(brand='hyundai', steerRatio=12.81, wheelbase=2.84, mass=1600,
                                 rotationalInertia=2700, centerToFront=1.136,
                                 tireStiffnessFront=100000, tireStiffnessRear=100000)
  torque = cp.lateralTuning.init('torque')
  torque.kp = 1.0
  torque.ki = 0.1
  torque.kf = 1.0
  torque.latAccelFactor = 2.963873863
  torque.friction = 0.07813665
  torque.useSteeringAngle = True
  cp = cp.as_reader()
  ci = NS(use_nnff=False, use_nnff_lite=False,
          torque_from_lateral_accel=lambda: partial(CarInterfaceBase.torque_from_lateral_accel_linear, None),
          get_pid_accel_limits=lambda *args: (-3.0, 2.0))
  sm = Messages()
  sm.update(longitudinalPlan=NS(), radarState=NS(), carrotMan=NS(vTurnSpeed=100), liveDelay=NS(lateralDelay=0.1))
  sm.frame = 1
  sm.recv_frame = {'longitudinalPlan': 1}
  sm['carState'].gearShifter = 'drive'
  sm['carState'].latEnabled = True
  settings = {'AlwaysLateral': True, 'SteerRatioRate': 88, 'CustomSR': 0, 'LatSmoothSec': 13}
  params = NS(get_float=lambda k: float(settings.get(k, 0)), get_int=lambda k: int(settings.get(k, 0)),
              get_bool=lambda k: bool(settings.get(k, False)))
  # Torque tuning is disabled in this fixture; unexpected settings reads fail.
  torque_params = NS(get_int=lambda key: {'LateralTorqueCustom': 0}[key])
  with patch('openpilot.selfdrive.controls.lib.latcontrol_torque.Params', return_value=torque_params):
    lateral_control = LatControlTorque(cp, ci)
  control = NS(CP=cp, CI=ci, sm=sm, params=params, VM=VehicleModel(cp), LaC=lateral_control,
               LoC=NS(reset=lambda: None, update=lambda *args: (0.0, 0.0, 0.0), long_control_state='off'),
               carrot_controls=NS(lat_suspend_control=lambda cs, active: active), is_vw_meb=False,
               desired_curvature=0.0, lateral_startup=LateralStartupGate(), lateral_started=False,
               curve_centering=None,
               steer_limited_by_safety=False)
  control.curve_centered_target = partial(ns['curve_centered_target'], control)
  control.clock = ns['time']
  return control, lambda: ns['state_control'](control)


@pytest.mark.parametrize('enabled', [False, True])
def test_full_control_loop_waits_only_for_first_readiness(enabled):
  control, step = control_fixture()
  sm = control.sm
  sm['selfdriveState'].enabled = sm['selfdriveState'].active = enabled
  live = sm['liveParameters']
  sm['liveParameters'] = log.LiveParametersData.new_message()
  sm.seen['liveParameters'] = sm.seen['modelV2'] = False
  for _ in range(1500):  # longer than either initialization timeout
    cc, state = step()
    assert not cc.latActive and cc.actuators.torque == 0
    assert control.VM.sR == pytest.approx(12.81 * 0.88, rel=1e-6)
    assert abs(control.curvature) < 0.01
  sm['liveParameters'] = live
  sm.seen['liveParameters'] = sm.seen['modelV2'] = True
  control.desired_curvature = -0.15  # previous geometry/activation must not leak through
  cc, state = step()
  assert cc.latActive
  assert math.isfinite(cc.actuators.torque)
  assert abs(cc.actuators.curvature - control.curvature) < 0.001
  control.LaC.pid.i = 0.8
  sm.alive['modelV2'] = False
  cc, state = step()
  assert cc.latActive and control.LaC.pid.i > 0.7
  # Neither disengagement nor later invalidity rearms startup or nominal fallback.
  sm['carState'].latEnabled = False
  sm.valid['liveParameters'] = False
  live.steerRatio = 15.5
  cc, _ = step()
  assert not cc.latActive
  assert control.VM.sR == pytest.approx(15.5 * 0.88, rel=1e-6)
  assert control.LaC.pid.i > 0.7
  sm['carState'].latEnabled = True
  control.desired_curvature = 0.01
  cc, _ = step()
  assert cc.latActive and control.lateral_startup.ready
  assert cc.actuators.curvature > 0.005  # no new measured-curvature reset on reengagement


def test_normal_torque_reset_preserves_existing_integral_and_nn_history():
  control, _ = control_fixture()
  from collections import deque
  lac = control.LaC
  lac.use_nnff = True
  for name in ('lateral_accel_desired_deque', 'roll_deque', 'error_deque'):
    setattr(lac, name, deque([100.0] * 30, maxlen=30))
  lac.pid.i = 0.8
  lac.reset()
  assert lac.pid.i == 0.8
  assert len(lac.roll_deque) == len(lac.error_deque) == len(lac.lateral_accel_desired_deque) == 30


@pytest.mark.parametrize('meb', [False, True])
@pytest.mark.parametrize('active,lane,points', [(False, True, True), (True, False, True), (True, True, False), (True, True, True)])
def test_lane_tuning_reads_only_when_used_and_remains_live(active, lane, points, meb):
  control, step = control_fixture()
  control.lateral_startup.ready = True
  control.is_vw_meb = meb
  control.calibrated_pose = None
  control.meb_curvature_pid = NS(reset=lambda: None, update=lambda *a, **kw: 0.)
  reads = []
  original = control.params.get_float
  control.params.get_float = lambda key: (reads.append(key), original(key))[1]
  control.sm['carState'].latEnabled = active
  plan = control.sm['lateralPlan']
  plan.useLaneLines = lane
  if points:
    plan.psis = [0.] * 17
    plan.curvatures = [0.001] * 17
    plan.distances = list(range(17))
  for _ in range(2):
    reads.clear()
    step()
    for key in ('LatSmoothSec', 'SteerActuatorDelay'):
      assert reads.count(key) == int(active and lane and points)
    assert 'SteerRatioRate' in reads and 'CustomSR' in reads


def curve_model(direction, confidence=0.95):
  x = np.linspace(0., 64., 33)
  middle = direction * .0015 * x**2
  model = log.ModelDataV2.new_message()
  model.position.x, model.position.y = x.tolist(), (middle + direction * .30 * np.minimum(x, 1.)).tolist()
  lines = model.init('laneLines', 4)
  for i, offset in ((1, -1.75), (2, 1.75)):
    lines[i].x, lines[i].y = x.tolist(), (middle + offset).tolist()
  model.laneLineProbs = [0., confidence, confidence, 0.]
  model.laneLineStds = [0., .08, .08, 0.]
  model.action.desiredCurvature = direction * .003
  return model.as_reader()


@pytest.mark.parametrize('direction', [-1., 1.])
def test_curve_centering_requires_fresh_lines_and_fades(direction):
  center = LanelessCurveCentering()
  model, weak = curve_model(direction), curve_model(direction, confidence=.20)
  epoch = 1_000_000_000

  def trim_at(tick, m=model, gain=30, stamp=None):
    t = epoch + tick * 10_000_000
    model_stamp = epoch + tick // 5 * 50_000_000 if stamp is None else stamp
    return center.update(m, model_stamp, t, direction * .06, 20., gain)

  assert all(trim_at(i, stamp=epoch) == 0. for i in range(100))
  center.reset()
  gains = [trim_at(i) for i in range(200)]
  assert all(value == 0. for value in gains[:50])
  assert 0.00025 < -direction * gains[-1] < .00075
  start = abs(gains[-1])
  faded = [abs(trim_at(i, weak)) for i in range(200, 700)]
  assert 0.95 * start < faded[0] <= start
  assert all(a >= b for a, b in zip(faded, faded[1:]))
  assert faded[99] < start * .35
  assert trim_at(700, gain=0) == 0.
  assert all(trim_at(i, gain=100) == 0. for i in range(701, 745))
  limited = [trim_at(i, gain=100) for i in range(745, 1045)]
  assert abs(limited[-1]) > start
  assert max(map(abs, limited)) <= .000602


@pytest.mark.parametrize('veto', ['driver', 'lane_setting', 'blinker'])
def test_curve_centering_reaches_torque_target_without_changing_shared_curvature(veto):
  control, step = control_fixture()
  control.lateral_startup.ready = True
  control.curve_centering = LanelessCurveCentering()
  control.curve_centering_pct = 30
  control.curve_steer_target = None
  control.pose_calibrator = NS(calib_valid=True)
  control.calibrated_pose = NS(angular_velocity=NS(xyz=np.array([0., 0., .06])))
  sm = control.sm
  sm['selfdriveState'].active = True
  sm['carState'].vEgo = 20.
  sm['livePose'].angularVelocityDevice.valid = True
  sm['modelV2'] = curve_model(1.)
  epoch = 1_000_000_000
  now = epoch
  control.clock.monotonic_ns = lambda: now

  for tick in range(150):
    now = epoch + tick * 10_000_000
    sm.logMonoTime = {'modelV2': epoch + tick // 5 * 50_000_000, 'livePose': now}
    cc, torque = step()
  assert cc.latActive
  assert cc.actuators.curvature == pytest.approx(.003, abs=1e-8)
  assert torque.desiredLateralAccel < cc.actuators.curvature * sm['carState'].vEgo**2 - .1

  if veto == 'driver':
    sm['carState'].steeringPressed = True
  elif veto == 'lane_setting':
    sm['carState'].useLaneLineSpeed = 30
  else:
    sm['carState'].leftBlinker = True
  for _ in range(10):
    now += 10_000_000
    cc, torque = step()
  assert torque.desiredLateralAccel == pytest.approx(cc.actuators.curvature * sm['carState'].vEgo**2)
  assert control.curve_steer_target is None
