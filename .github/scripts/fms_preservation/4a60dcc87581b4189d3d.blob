"""Actual mode/runtime admission to auxiliary ownership and final MPC inputs.

Recording-solver host boundaries, not native MPC or vehicle evidence. Legacy
SignalAssist is absent on the production modes exercised here.
"""
import ast
import json
import subprocess
import time
from functools import lru_cache
from types import SimpleNamespace as NS

import numpy as np
import pytest

from openpilot.selfdrive.carrot import signal_mode
from openpilot.selfdrive.carrot.carrot_man_input import get_carrot_man
from openpilot.selfdrive.carrot.signal_start_guard import SignalStartRuntime, ENABLE_PATH
from openpilot.selfdrive.carrot.signal_stop_prepare import SignalStopPrepare
from openpilot.selfdrive.carrot.tests.test_signal_assist_runtime import SM
from openpilot.selfdrive.carrot.tests.test_signal_departure import current_mpc
from openpilot.selfdrive.carrot.tests.test_signal_start_guard import observation
from tools.signal_analysis.replay_signal_assist import REPO, planner_class
from tools.signal_analysis.test_signal_assist_planner import inputs


class RunningPlanner:
  def __init__(self, monkeypatch, tmp_path, *, mode=1, stay=True, lead=True, speed=2.,
               planner_mode='blended', source=None, acquire=True):
    self.clock, self.selection = [10.], [mode]
    monkeypatch.setattr(time, 'monotonic', lambda: self.clock[0])
    monkeypatch.setattr(signal_mode, 'signal_mode', lambda: self.selection[0])
    self.path = tmp_path/'observation'
    self.runtime = SignalStartRuntime(ENABLE_PATH, self.path)
    cls, _ = planner_class(source or (REPO/'openpilot/selfdrive/carrot/carrot_functions.py').read_text(encoding='utf8'))
    self.p = cls(start_guard=self.runtime.guard, stop_prepare=SignalStopPrepare())
    self.p._start_runtime = self.runtime
    self.runtime.record = lambda *args: None  # only log transport is out of scope
    self.p._update_carrot_man.__func__.__globals__['get_carrot_man'] = get_carrot_man
    self.sm = SM(); self.sm.update(inputs(distance=320/31))
    self.sm['selfdriveState'].enabled = True
    self.publish = True
    self.sm['carState'].canValid = True
    self.sm['carState'].gearShifter = 'drive'
    self.sm['carState'].standstill = True
    self.sm['carState'].vCruise = 80.
    self.sm['carrotMan'] = NS(trafficState=1 if stay else 0, atcType='turn left' if stay else '',
                              activeCarrot=2, xDistToTurn=0., desiredSpeed=80.)
    for d in (self.sm.seen, self.sm.alive, self.sm.valid):
      d['carrotMan'] = True
    self.lead, self.speed = lead, speed
    self.planner_mode = planner_mode
    if not acquire:
      self.sm['carState'].vEgo = self.sm['carState'].vEgoCluster = speed
      self.sm['carState'].standstill = speed == 0.
      self.sm['radarState'].leadOne = NS(status=lead, dRel=4., vLead=2.)
      return
    for _ in range(15):
      self.tick(color='red')
    assert self.runtime.guard.hold and self.p._signal_stop_owned
    assert self.p.signal_assist is None and self.p._signal_runtime is None
    self.sm['carState'].vEgo = self.sm['carState'].vEgoCluster = speed
    self.sm['carState'].standstill = speed == 0.
    self.sm['radarState'].leadOne = NS(status=lead, dRel=4., vLead=2.)
    self.tick()

  def tick(self, *, color='unknown', elapsed=.1, mpc=True):
    self.clock[0] += elapsed
    now = self.clock[0]
    self.sm.recv_time = dict.fromkeys(self.sm.seen, now)
    record = dict(version=1, stream='road', size=[1344, 760], **observation(now, color))
    if self.publish:
      self.path.write_text(json.dumps(record), encoding='utf8')
    self.p.update(self.sm, 80., self.planner_mode)
    return current_mpc(self.p, self.sm) if mpc else None


@pytest.mark.parametrize('mode', [1, 2])
@pytest.mark.parametrize('stay', [False, True])
@pytest.mark.parametrize('lead', [False, True])
@pytest.mark.parametrize('withdraw', ['option_zero', 'cruise_off', 'cancel', 'park'])
def test_current_runtime_withdrawal_releases_owned_override_and_cannot_replay(monkeypatch, tmp_path, mode, stay, lead, withdraw):
  run = RunningPlanner(monkeypatch, tmp_path, mode=mode, stay=stay, lead=lead)
  if withdraw == 'option_zero':
    run.selection[0] = 0
  elif withdraw in ('cruise_off', 'cancel'):
    run.sm['selfdriveState'].enabled = False
    run.sm['carControl'].longActive = False
  else:
    run.sm['carState'].gearShifter = 'park'
  # Cross the existing 0.5 s runtime mode poll; there is no new immediate poll.
  first = run.tick(elapsed=.6)
  run.tick()
  assert not run.runtime.guard.hold
  assert not run.p._signal_stop_owned
  assert run.p.mode == 'blended' and first.mode == 'blended'
  if lead:
    assert run.p.xState.name == 'lead'
    assert first.source == 'lead0'
    assert first.params[0, 2] == pytest.approx(4.8)
  if withdraw == 'option_zero':
    assert not run.runtime.enabled
    run.selection[0] = mode
  elif withdraw in ('cruise_off', 'cancel'):
    run.sm['selfdriveState'].enabled = True
    run.sm['carControl'].longActive = True
  else:
    run.sm['carState'].gearShifter = 'drive'
  for _ in range(3):
    mpc = run.tick(elapsed=.6)
    assert not run.p._signal_stop_owned and not run.runtime.guard.hold
    assert run.p.mode == mpc.mode == 'blended'
  # A new stopped red still needs fresh confirmation after a new session.
  run.sm['carState'].vEgo = run.sm['carState'].vEgoCluster = 0.
  run.sm['carState'].standstill = True
  run.tick(color='red')
  assert not run.runtime.guard.hold
  for _ in range(10):
    run.tick(color='red')
  assert run.runtime.guard.hold and run.p._signal_stop_owned


@pytest.mark.parametrize('mode', [1, 2])
@pytest.mark.parametrize('fault', ['missing_observation', 'invalid_can', 'invalid_model'])
@pytest.mark.parametrize('speed', [0., 2.])
def test_invalid_evidence_does_not_equal_admission_withdrawal(monkeypatch, tmp_path, mode, fault, speed):
  run = RunningPlanner(monkeypatch, tmp_path, mode=mode, speed=speed)
  if fault == 'invalid_can':
    run.sm['carState'].canValid = False
  elif fault == 'invalid_model':
    run.sm.valid['modelV2'] = False
  else:
    run.path.write_text('{}', encoding='utf8')
    # Preserve the actual read path but with a packet schema rejection.
    run.publish = False
  for _ in range(3):
    mpc = run.tick()
    assert run.p._signal_stop_owned and run.p.carrot_stay_stop
    assert run.p.mode == mpc.mode == 'acc'
    assert run.p.v_cruise == run.p.stop_dist == 0.


@pytest.mark.parametrize('mode', [1, 2])
@pytest.mark.parametrize('speed', [0., 5/3.6, 5/3.6+.001, 21.25])
def test_active_departed_navigation_stay_keeps_its_authorized_episode(monkeypatch, tmp_path, mode, speed):
  run = RunningPlanner(monkeypatch, tmp_path, mode=mode, speed=speed)
  for _ in range(3):
    mpc = run.tick()
    assert run.p.carrot_stay_stop and run.p._signal_stop_owned
    assert run.p.v_cruise == run.p.stop_dist == 0.
    assert run.p.mode == mpc.mode == 'acc'
    assert 0 <= mpc.params[0, 2] <= run.p.trafficStopDistanceAdjust


@pytest.mark.parametrize('camera', [False, True])
@pytest.mark.parametrize('hold', [0, 1, 2])
def test_runtime_off_keeps_independent_hold_and_unavailable_acc_boundary(monkeypatch, tmp_path, camera, hold):
  from openpilot.selfdrive.controls.tests.test_longcontrol_hyundai_tuning import make_cp, DictParams
  from openpilot.selfdrive.controls.lib import longcontrol
  from opendbc.car.hyundai.tests.test_stopping import make_cs, send
  from opendbc.car.hyundai.stopping import CanfdStopping

  run = RunningPlanner(monkeypatch, tmp_path, speed=0.)
  run.sm['carState'].softHoldActive = hold
  run.sm['selfdriveState'].enabled = run.sm['carControl'].longActive = False
  run.tick()
  assert not run.p._signal_stop_owned and not run.runtime.guard.hold
  assert run.p.soft_hold_active == hold
  monkeypatch.setattr(longcontrol, 'Params', lambda: DictParams({'StoppingAccel': -50}))
  cp = make_cp(); cp.stopAccel = -.5
  control = longcontrol.LongControl(cp)
  cs = make_cs()
  cs.softHoldActive = cs.out.softHoldActive = run.p.soft_hold_active
  cs.out.cruiseState.available = False
  cs.out.cruiseState.standstill = False
  cs.out.vEgo = cs.out.vEgoRaw = 0.
  cs.out.wheelSpeeds = NS(fl=0., fr=0., rl=0., rr=0.)
  plan = NS(shouldStop=run.runtime.guard.hold, aTarget=0., vTargetNow=0., jTargetNow=0.)
  state = CanfdStopping(); previous = 0.
  for _ in range(70):
    accel, _, _ = control.update(False, cs.out, plan, (-3.5, 2.), 0., run.sm['radarState'])
    packet = send(camera, state, cs, enabled=False,
                  stopping=control.long_control_state == longcontrol.LongCtrlState.stopping,
                  accel=accel, previous_value=previous)
    previous = packet['aReqValue']
  assert control.long_control_state == longcontrol.LongCtrlState.off
  assert packet['ACCMode'] == packet['StopReq'] == (1 if hold else 0)
  assert packet['aReqRaw'] == packet['aReqValue'] == pytest.approx(-.5 if hold else 0.)


@pytest.mark.parametrize('cancelled', [False, True])
def test_off_planner_state_reaches_existing_brake_resume_cancel_latch(monkeypatch, tmp_path, cancelled):
  from openpilot.selfdrive.car.cruise import VCruiseCarrot
  run = RunningPlanner(monkeypatch, tmp_path)
  run.sm['selfdriveState'].enabled = run.sm['carControl'].longActive = False
  run.tick()
  assert not run.p._signal_stop_owned and run.p.xState.name == 'lead'
  helper = VCruiseCarrot.__new__(VCruiseCarrot)
  for name, value in dict(_cruise_available=True, _cruise_cancel_state=cancelled,
      _steering_interlock_active=False, _hold_interlock_active=False,
      _cancel_timer=0, _activate_cruise=0, autoCruiseControl=1,
      autoCruiseControl_cancel_timer=0, _soft_hold_active=0, _brake_pressed_count=-1,
      _gas_pressed_count=-2, _gas_pressed_count_last=0, _gas_tok_timer=40,
      _gas_tok=False, disengage_on_accelerator=False,
      v_cruise_kph=80, v_ego_kph_set=0, autoGasTokSpeed=10,
      d_rel=4., _paddle_decel_active=False, desiredSpeed=80,
      _cruise_ready=False, _pause_auto_speed_up=False).items():
    setattr(helper, name, value)
  helper.params = NS(get_bool=lambda key: False)
  helper._auto_speed_up = lambda speed: speed
  helper._add_log = lambda text: None
  # Match the production longitudinalPlan -> VCruise state assignments. The
  # recording MPC does not solve acceleration; no field timing claim is made.
  helper.xState, helper.trafficState, helper.aTarget = run.p.xState.value, run.p.trafficState.value, 0.
  cs = NS(vEgo=.5, steeringAngleDeg=0., leftBlinker=False, rightBlinker=False, aEgo=0.)
  assert helper._update_cruise_state(cs, NS(enabled=False), 80) == 80
  assert (helper._activate_cruise > 0) is (not cancelled)


@pytest.mark.parametrize('reenable', [False, True])
def test_runtime_zero_speed_off_then_motion_retires_same_episode(monkeypatch, tmp_path, reenable):
  run = RunningPlanner(monkeypatch, tmp_path, speed=0., stay=False, lead=False)
  run.selection[0] = 0
  run.tick(elapsed=.6)
  assert not run.p._signal_stop_owned
  run.sm['carState'].vEgo = run.sm['carState'].vEgoCluster = 21.25
  run.sm['carState'].standstill = False
  if reenable:
    run.selection[0] = 1
  for _ in range(3):
    mpc = run.tick(elapsed=.6)
    assert not run.p._signal_stop_owned and not run.runtime.guard.hold
    assert run.p.xState.name not in ('e2eStop', 'e2eStopped')
    assert run.p.stop_dist == 1000. and run.p.v_cruise > 0
    assert mpc.mode == 'blended'


@pytest.mark.parametrize('speed', [0., 2.])
@pytest.mark.parametrize('accel', [0., -1.5])
def test_overlapping_native_model_stop_still_reaches_final_stop_output(monkeypatch, tmp_path, speed, accel):
  from openpilot.selfdrive.controls.lib.drive_helpers import get_accel_from_plan
  run = RunningPlanner(monkeypatch, tmp_path, speed=0., stay=False, lead=False)
  run.sm['modelV2'].position.x = [3.*i/32 for i in range(33)]
  run.sm['modelV2'].velocity.x = [0.]*33
  for _ in range(25):
    run.tick()
  assert run.p.stopSignCount > 0 and run.runtime.guard.hold
  run.selection[0] = 0
  run.sm['carState'].vEgo = run.sm['carState'].vEgoCluster = speed
  run.sm['carState'].aEgo = accel
  mpc = run.tick(elapsed=.6)
  assert not run.p._signal_stop_owned and not run.runtime.guard.hold
  # Experimental/blended mode returns to its actual model trajectory. Its MPC
  # obstacle column contains lead obstacles, not the model stop target; execute
  # the production final MPC/model stop selection instead of conflating them.
  assert mpc.mode == 'blended'
  _, model_stop, _, _ = get_accel_from_plan(np.zeros(3), np.zeros(3), np.array([0., .3, 1.3]),
                                          action_t=.3, vEgoStopping=.5)
  assert model_stop
  path = REPO/'openpilot/selfdrive/controls/lib/longitudinal_planner.py'
  node = next(n for n in ast.parse(path.read_text(encoding='utf8')).body
              if isinstance(n, ast.ClassDef) and n.name == 'LongitudinalPlanner')
  method = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'update')
  first = next(i for i, n in enumerate(method.body) if isinstance(n, ast.If) and any(
    isinstance(x, ast.Assign) and any(ast.unparse(t) == 'self.output_should_stop' for t in x.targets)
    for x in n.body))
  last = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Expr)
              and isinstance(n.value, ast.Call) and ast.unparse(n.value.func) == 'self.update_coasting')
  result = NS(mpc=NS(mode=mpc.mode), j_desired_trajectory=[0.])
  scope = dict(self=result, output_should_stop_mpc=False, output_should_stop_e2e=model_stop,
               output_a_target_mpc=0., output_a_target_e2e=0., output_a_target_base=0.,
               output_v_target_mpc=0., output_v_target_now_e2e=0.)
  exec(compile(ast.Module(method.body[first:last], type_ignores=[]), str(path), 'exec'), scope)
  assert result.output_should_stop and result.output_v_target_now == 0.


def test_departure_retirement_keeps_existing_model_deceleration_filter_order(monkeypatch, tmp_path):
  run = RunningPlanner(monkeypatch, tmp_path, speed=0., stay=False, lead=False)
  run.selection[0] = 0
  run.sm['carState'].vEgo = run.sm['carState'].vEgoCluster = 2.
  run.sm['carState'].aEgo = -1.5
  run.sm['modelV2'].velocity.x = [2.]+[0.]*32
  for _ in range(3):
    run.tick(elapsed=.6)
    assert not run.p._signal_stop_owned
    # The pre-existing velocity filter may still report green from preceding
    # model-go samples. The deceleration rule forbids a false red stop, not green.
    assert run.p.stopSignCount == 0 and run.p.trafficState.name != 'red'
    assert run.p.xState.name == 'e2eCruise'


@pytest.mark.parametrize('mode', [1, 2])
def test_runtime_context_is_read_only_once_per_planner_cycle(monkeypatch, tmp_path, mode):
  run = RunningPlanner(monkeypatch, tmp_path, mode=mode)
  actual_read, calls = run.runtime.read, []
  def read(*args, **kwargs):
    calls.append(True)
    return actual_read(*args, **kwargs)
  run.runtime.read = read
  run.tick()
  assert len(calls) == 1


@lru_cache(maxsize=1)
def published_source():
  # Immutable comparison evidence for this regression; no deployed Git use.
  return subprocess.check_output([
    'git', 'show', 'f76bb193aa6f3063507857134fe37bb4a42bb9b2:openpilot/selfdrive/carrot/carrot_functions.py',
  ], cwd=REPO).decode('utf8')


def model_input(run, kind):
  distance = 3. if kind == 'stop' else 320.
  run.sm['modelV2'].position.x = [distance*i/32 for i in range(33)]
  run.sm['modelV2'].velocity.x = [.29]+[10. if kind == 'go' else 0.]*32


def native_episode(monkeypatch, tmp_path, order, planner_mode, source=None):
  run = RunningPlanner(monkeypatch, tmp_path, stay=False, lead=False, speed=.29,
                       planner_mode=planner_mode, source=source, acquire=False)
  model_input(run, 'stop' if order == 'native_first' else 'go')
  for _ in range(30):
    run.tick(color='unknown' if order == 'native_first' else 'red', elapsed=.05, mpc=False)
  if order == 'native_first':
    assert not run.runtime.guard.hold and run.p.xState.name == 'e2eStopped'
  else:
    assert run.runtime.guard.hold and run.p.stopSignCount == 0
    model_input(run, 'stop')
  for _ in range(30):
    run.tick(color='red' if order == 'native_first' else 'unknown', elapsed=.05, mpc=False)
  assert run.runtime.guard.hold and run.p.stopSignCount > 0
  return run


def stop_output(run, mpc):
  return dict(state=run.p.xState.name, traffic=run.p.trafficState.name,
              mode=run.p.mode, v_cruise=run.p.v_cruise, stop_dist=run.p.stop_dist,
              source=mpc.source, params=mpc.params.tolist())


@pytest.mark.parametrize('order', ['native_first', 'auxiliary_first'])
@pytest.mark.parametrize('planner_mode', ['acc', 'blended'])
@pytest.mark.parametrize('speed', [0., .29, 2.])
@pytest.mark.parametrize('accel', [0., -1.5])
def test_runtime_off_preserves_native_episode_and_real_mpc(monkeypatch, tmp_path, order, planner_mode, speed, accel):
  traces = []
  for source in (published_source(), None):
    with monkeypatch.context() as patch:
      run = native_episode(patch, tmp_path, order, planner_mode, source)
      run.selection[0] = 0
      run.sm['carState'].vEgo = run.sm['carState'].vEgoCluster = speed
      run.sm['carState'].standstill = speed == 0.
      run.sm['carState'].aEgo = accel
      trace = []
      for _ in range(3):
        mpc = run.tick(elapsed=.6)
        assert not run.runtime.enabled and not run.runtime.guard.hold
        assert run.sm['selfdriveState'].enabled and run.sm['carControl'].longActive
        trace.append(stop_output(run, mpc))
      traces.append(trace)
  assert traces[1] == traces[0]
  assert all(row['state'] == 'e2eStopped' and row['v_cruise'] == 0. for row in traces[1])
  if planner_mode == 'acc':
    # Python MPC's actual obstacle column, not an unrelated blended model-stop OR.
    assert all(row['source'] == 'e2e' for row in traces[1])
    if speed == .29:
      assert np.allclose(np.array(traces[1][0]['params'])[:, 2], 3.*31/32+2.5)


@pytest.mark.parametrize('gate', ['braking', 'lead', 'steering', 'restart_timer', 'mode_high', 'detection_off'])
def test_auxiliary_only_state_cannot_certify_a_forbidden_native_stop(monkeypatch, tmp_path, gate):
  run = RunningPlanner(monkeypatch, tmp_path, stay=False, lead=False, speed=.29, planner_mode='acc')
  assert run.p._signal_stop_owned and not run.p._signal_stop_native
  model_input(run, 'stop')
  if gate == 'braking':
    run.sm['carState'].aEgo = -1.5
  elif gate == 'lead':
    run.sm['radarState'].leadOne.status = True
    run.sm['radarState'].leadOne.dRel = 50.
  elif gate == 'steering':
    run.sm['carState'].steeringAngleDeg = 90.
  elif gate == 'restart_timer':
    run.p.traffic_starting_count = 200
  elif gate == 'mode_high':
    run.p.myDrivingMode = type(run.p.myDrivingMode).High
  else:
    run.p.trafficLightDetectMode = 0
  for _ in range(25):
    run.tick(mpc=False)
    assert run.runtime.guard.hold and run.p._signal_stop_owned
    assert not run.p._signal_stop_native
  run.selection[0] = 0
  mpc = run.tick(elapsed=.6)
  assert not run.p._signal_stop_owned and not run.p._signal_stop_native
  assert run.p.xState.name in ('e2eCruise', 'lead') and run.p.stop_dist == 1000.
  assert mpc.source != 'e2e'


@pytest.mark.parametrize('order', ['native_first', 'auxiliary_first'])
@pytest.mark.parametrize('planner_mode', ['acc', 'blended'])
def test_native_unknown_during_off_keeps_existing_memory_then_red(monkeypatch, tmp_path, order, planner_mode):
  traces = []
  for source in (published_source(), None):
    with monkeypatch.context() as patch:
      run = native_episode(patch, tmp_path, order, planner_mode, source)
      if source is None:
        assert run.p._signal_stop_native
      model_input(run, 'unknown')
      run.tick(mpc=False)
      run.selection[0] = 0
      run.sm['carState'].aEgo = -1.5
      trace = [stop_output(run, run.tick(elapsed=.6))]
      assert run.p.trafficState.name == 'off' and run.p.xState.name == 'e2eStopped'
      if source is None:
        assert not run.p._signal_stop_native and not run.p._signal_stop_owned
      model_input(run, 'stop')
      for _ in range(3):
        trace.append(stop_output(run, run.tick()))
      traces.append(trace)
  assert traces[1] == traces[0]
  assert traces[1][-1]['state'] == 'e2eStopped' and traces[1][-1]['v_cruise'] == 0.


def test_native_entry_can_join_active_navigation_stay_after_guard_departure(monkeypatch, tmp_path):
  run = RunningPlanner(monkeypatch, tmp_path, stay=True, lead=False, speed=2., planner_mode='acc')
  assert run.p._signal_stop_owned and not run.runtime.guard.hold and not run.p._signal_stop_native
  model_input(run, 'stop')
  for _ in range(25):
    run.tick(mpc=False)
  assert run.p._signal_stop_native and run.p.carrot_stay_stop
  run.selection[0] = 0
  run.sm['carState'].aEgo = -1.5
  mpc = run.tick(elapsed=.6)
  assert run.p.xState.name == 'e2eStopped' and run.p.v_cruise == 0.
  assert not run.p._signal_stop_owned and not run.p._signal_stop_native
  assert mpc.source == 'e2e'


@pytest.mark.parametrize('exit_kind', ['model_green', 'lead', 'gas', 'navigation'])
def test_native_exit_does_not_protect_the_next_auxiliary_only_episode(monkeypatch, tmp_path, exit_kind):
  run = native_episode(monkeypatch, tmp_path, 'native_first', 'acc')
  assert run.p._signal_stop_native
  if exit_kind == 'gas':
    run.sm['carState'].gasPressed = True
    run.tick(mpc=False)
  elif exit_kind == 'navigation':
    # Actual CarrotMan red -> left start retires the old shared state before the
    # next native model check. At departure speed the red guard also retires.
    run.sm['carrotMan'].atcType = 'turn left'
    run.sm['carrotMan'].trafficState = 1
    run.tick(mpc=False)
    run.sm['carrotMan'].trafficState = 3
    run.sm['carState'].vEgo = run.sm['carState'].vEgoCluster = 2.
    run.tick(mpc=False)
  else:
    run.selection[0] = 0
    run.tick(elapsed=.6, mpc=False)
    if exit_kind == 'lead':
      run.sm['radarState'].leadOne.status = True
      run.sm['radarState'].leadOne.dRel = 4.
    else:
      model_input(run, 'go')
    for _ in range(20):
      run.tick(mpc=False)
  assert run.p.xState.name not in ('e2eStop', 'e2eStopped')
  assert not run.p._signal_stop_owned and not run.p._signal_stop_native
  # Clear external inputs, allow the existing gas/start cooldown to expire and
  # confirm a new red with a model-go trajectory; previous native ownership is gone.
  run.sm['carState'].gasPressed = False
  run.sm['carState'].vEgo = run.sm['carState'].vEgoCluster = .29
  run.sm['radarState'].leadOne.status = False
  run.sm['carrotMan'].trafficState = 0
  run.sm['carrotMan'].atcType = ''
  run.selection[0] = 1
  model_input(run, 'go')
  run.tick(elapsed=11., mpc=False)
  for _ in range(15):
    run.tick(color='red', mpc=False)
  assert run.runtime.guard.hold and run.p._signal_stop_owned
  assert not run.p._signal_stop_native
  run.selection[0] = 0
  mpc = run.tick(elapsed=.6)
  assert run.p.xState.name == 'e2eCruise' and run.p.stop_dist == 1000.
  assert mpc.source == 'cruise'


@pytest.mark.parametrize('hold_kind', ['soft', 'internal_distance'])
def test_explicit_hold_handoff_retires_native_provenance_without_clearing_hold(monkeypatch, tmp_path, hold_kind):
  run = native_episode(monkeypatch, tmp_path, 'native_first', 'acc')
  if hold_kind == 'soft':
    run.sm['carState'].softHoldActive = 1
  else:
    # Synthetic internal branch only; production has no >=0 distance producer.
    run.p.user_stop_distance = 0.
  run.tick(mpc=False)
  assert not run.p._signal_stop_native
  run.selection[0] = 0
  run.tick(elapsed=.6)
  assert not run.p._signal_stop_owned and not run.p._signal_stop_native
  assert run.p.xState.name == 'e2eStopped'
  if hold_kind == 'soft':
    assert run.p.soft_hold_active == 1
  else:
    assert run.p.user_stop_distance == 0. and run.p.stop_dist == 0.


def test_brake_alone_does_not_erase_existing_native_stop(monkeypatch, tmp_path):
  run = native_episode(monkeypatch, tmp_path, 'native_first', 'acc')
  run.sm['carState'].brakePressed = True
  run.sm['carState'].aEgo = -1.5
  run.tick(mpc=False)
  assert run.p._signal_stop_native
  run.selection[0] = 0
  mpc = run.tick(elapsed=.6)
  assert run.p.xState.name == 'e2eStopped' and run.p.v_cruise == 0.
  assert mpc.source == 'e2e' and np.allclose(mpc.params[:, 2], 3.*31/32+2.5)


def test_native_lead_loss_entry_is_not_mistaken_for_auxiliary_cruise_braking(monkeypatch, tmp_path):
  traces = []
  # First establish that the independent native FSM really enters on this lead
  # loss. Then compare the same auxiliary episode against published FMS.
  for source, enabled in ((None, False), (published_source(), True), (None, True)):
    with monkeypatch.context() as patch:
      run = RunningPlanner(patch, tmp_path, mode=int(enabled), stay=False, lead=True,
                           speed=.29, planner_mode='acc', source=source, acquire=False)
      run.sm['radarState'].leadOne.dRel = 50.
      model_input(run, 'stop')
      for _ in range(30):
        run.tick(color='red', mpc=False)
      assert run.runtime.guard.hold is enabled
      run.sm['carState'].aEgo = -1.5
      for _ in range(10):
        run.tick(mpc=False)
      run.sm['radarState'].leadOne.status = False
      for _ in range(3):
        run.tick(mpc=False)
      if not enabled:
        assert run.p.xState.name == 'e2eStopped' and run.p.v_cruise == 0.
        continue
      run.selection[0] = 0
      traces.append(stop_output(run, run.tick(elapsed=.6)))
  assert traces[1] == traces[0]
  assert traces[1]['state'] == 'e2eStopped' and traces[1]['source'] == 'e2e'


def test_old_lead_cannot_certify_a_later_model_stop_during_cruise_braking(monkeypatch, tmp_path):
  for enabled in (False, True):
    with monkeypatch.context() as patch:
      run = RunningPlanner(patch, tmp_path, mode=int(enabled), stay=False, lead=True,
                           speed=.29, planner_mode='acc', acquire=False)
      model_input(run, 'go')
      for _ in range(30):
        run.tick(color='red', mpc=False)
      run.sm['carState'].aEgo = -1.5
      run.sm['radarState'].leadOne.status = False
      run.tick(mpc=False)
      model_input(run, 'stop')
      for _ in range(25):
        run.tick(mpc=False)
      assert run.p.stopSignCount == 0 and not run.p._signal_stop_native
      run.selection[0] = 0
      mpc = run.tick(elapsed=.6)
      assert run.p.xState.name == 'e2eCruise' and run.p.stop_dist == 1000.
      assert mpc.source == 'cruise'


def test_off_and_lead_loss_same_cycle_keep_independent_native_entry(monkeypatch, tmp_path):
  traces = []
  for enabled in (False, True):
    with monkeypatch.context() as patch:
      run = RunningPlanner(patch, tmp_path, mode=int(enabled), stay=False, lead=True,
                           speed=.29, planner_mode='acc', acquire=False)
      run.sm['radarState'].leadOne.dRel = 50.
      model_input(run, 'stop')
      for _ in range(30):
        run.tick(color='red', mpc=False)
      run.sm['carState'].aEgo = -1.5
      for _ in range(10):
        run.tick(mpc=False)
      assert not run.p._signal_stop_native
      # Native admission happens now, not in a preceding auxiliary-held cycle.
      run.selection[0] = 0
      run.sm['radarState'].leadOne.status = False
      traces.append([stop_output(run, run.tick(elapsed=.6)) for _ in range(3)])
  assert traces[1] == traces[0]
  assert [row['state'] for row in traces[1]] == ['e2eStop', 'e2eStopped', 'e2eStopped']
  assert all(row['source'] == 'e2e' for row in traces[1])
