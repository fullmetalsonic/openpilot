"""Stopped signal memory must not become a road-speed stop after lead departure."""
import ast
from types import SimpleNamespace as NS
import time

import numpy as np

import pytest

from openpilot.selfdrive.carrot.tests.test_signal_lead_handover import setup, update
from openpilot.selfdrive.carrot.tests.test_signal_start_guard import held, step
from tools.signal_analysis.replay_signal_assist import REPO


@pytest.mark.parametrize('guards', ['start', 'legacy', 'both'])
@pytest.mark.parametrize('loss', ['missing', 'outside'])
@pytest.mark.parametrize('speed', [.2, 1., 5/3.6, 5/3.6+.001, 21.25])
def test_lead_departure_then_loss(guards, loss, speed):
  p, sm = setup()
  if guards == 'start':
    p.signal_assist = p.signal_decision = None
  elif guards == 'legacy':
    p.start_guard = p.start_decision = None
  sm['radarState'].leadOne = NS(status=True, dRel=4., vLead=speed)
  update(p, sm, 1., 'unknown')
  sm['carState'].vEgo = sm['carState'].vEgoCluster = speed
  update(p, sm, 1.05, 'unknown')
  if loss == 'missing':
    sm['radarState'].leadOne.status = False
  else:
    sm['radarState'].leadOne.dRel = 1000.
  update(p, sm, 1.1, 'unknown')
  holds = [d.hold for d in (p.start_decision, p.signal_decision) if d is not None]
  if speed <= 5/3.6:
    assert all(holds) and p.xState.name == 'e2eStopped' and p.v_cruise == 0
  else:
    assert not any(holds)
    assert p.xState.name not in ('e2eStop', 'e2eStopped') and p.stop_dist == 1000.
    assert p.v_cruise > 0
    # Returning to a stop with no new red cannot revive an earlier intersection.
    sm['carState'].vEgo = sm['carState'].vEgoCluster = 0.
    update(p, sm, 1.15, 'unknown')
    assert not any(d.hold for d in (p.start_decision, p.signal_decision) if d)


@pytest.mark.parametrize('guards', ['start', 'legacy', 'both'])
@pytest.mark.parametrize('driver_hold', ['none', 'soft', 'user', 'stay'])
def test_already_applied_signal_stop_does_not_survive_departure(guards, driver_hold):
  p, sm = setup()
  if guards == 'start':
    p.signal_assist = p.signal_decision = None
  elif guards == 'legacy':
    p.start_guard = p.start_decision = None
  if driver_hold == 'soft':
    sm['carState'].softHoldActive = 1
  elif driver_hold == 'user':
    p.user_stop_distance = 0.
  elif driver_hold == 'stay':
    sm = navigation_inputs(p, sm)
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 21.25
  update(p, sm, 1., 'unknown')
  assert not any(d.hold for d in (p.start_decision, p.signal_decision) if d)
  assert (p.xState.name == 'e2eStopped') is (driver_hold != 'none')
  if driver_hold == 'none':
    assert p.stop_dist == 1000. and p.v_cruise > 0


@pytest.mark.parametrize('context', [{}, {'valid': False}, {'observation': None},
                                   {'observation': {'timestamp': float('nan')}}])
@pytest.mark.parametrize('speed', [21.25, -2.])
def test_held_start_guard_leaves_stopped_scope_even_with_bad_observation(context, speed):
  g = held()
  d = step(g, .7, speed=speed, **context)
  assert not d.hold and not g.hold and not d.green_confirmed and not d.released


def test_clock_rollback_cannot_reapply_stopped_hold_at_speed():
  g = held()
  assert not step(g, -1., speed=21.25).hold
  assert not step(g, 1., 'unknown', speed=0.).hold


@pytest.mark.parametrize('speed', [0., 1., 5/3.6, 21.25, -2.])
def test_final_output_rejects_stale_stopped_hold_at_speed(speed):
  path = REPO/'openpilot/selfdrive/controls/lib/longitudinal_planner.py'
  cls = next(n for n in ast.parse(path.read_text(encoding='utf8')).body
             if isinstance(n, ast.ClassDef) and n.name == 'LongitudinalPlanner')
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'apply_signal_start_guard')
  ns = {}
  exec(compile(ast.Module([method], type_ignores=[]), str(path), 'exec'), ns)
  output = NS(output_should_stop=False, output_v_target_now=20., output_a_target=-.7)
  ns['apply_signal_start_guard'](output, NS(start_decision=NS(hold=True)), speed)
  assert output.output_should_stop is (abs(speed) <= 5/3.6)
  assert output.output_v_target_now == (0. if abs(speed) <= 5/3.6 else 20.)
  assert output.output_a_target == -.7  # existing braking is never removed


@pytest.mark.parametrize('guards', ['start', 'legacy', 'both'])
def test_disable_cannot_leave_an_orphan_signal_stop_after_motion(guards):
  p, sm = setup()
  if guards == 'start':
    p.signal_assist = p.signal_decision = None
  elif guards == 'legacy':
    p.start_guard = p.start_decision = None
  for t, speed in [(1., 0.), (1.05, 21.25)]:
    sm['carState'].vEgo = sm['carState'].vEgoCluster = speed
    ctx = dict(now=t, enabled=False, valid=True, drive=True)
    p.update(sm, 80., 'acc', start_context=ctx, signal_context=ctx)
  assert p.xState.name not in ('e2eStop', 'e2eStopped')
  assert p.stop_dist == 1000. and p.v_cruise > 0


def test_fresh_moving_red_can_still_request_a_model_distance_stop():
  from tools.signal_analysis.test_signal_assist_planner import run
  p, sm = setup()
  sm['radarState'].leadOne = NS(status=True, dRel=4., vLead=10.)
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 10.
  update(p, sm, 1., 'unknown')
  assert not p.start_guard.hold and not p.signal_assist.hold
  sm['radarState'].leadOne.status = False
  sm['modelV2'].position.x = [40.*i/32 for i in range(33)]
  run(p, sm, 'red', start=2., count=40)
  assert p.signal_assist.committed and not p.signal_assist.hold
  assert p.xState.name == 'e2eStop' and 0 < p.stop_dist < 40.


def test_new_stopped_red_needs_new_confirmation_after_departure():
  g = held()
  assert not step(g, .7, speed=21.25).hold
  assert not step(g, 1.05, 'unknown').hold
  assert not step(g, 1.4, 'red').hold
  assert step(g, 1.75, 'red').hold


# These boundaries execute current production Python state/MPC wiring, with a
# recording solver. Native optimization, IPC, ECU and road behavior are unrun.
def navigation_inputs(planner, sm, traffic_state=1):
  from openpilot.selfdrive.carrot.carrot_man_input import get_carrot_man
  class State(dict):
    pass
  state = State(sm)
  state.seen = state.alive = state.valid = {'carrotMan': True}
  state.recv_time = {'carrotMan': time.monotonic()}
  state['carrotMan'] = NS(trafficState=traffic_state, atcType='turn left',
                         activeCarrot=2, xDistToTurn=0., desiredSpeed=80.)
  # Keep the actual age/health/positive-speed admission and navigation producer.
  planner._update_carrot_man.__func__.__globals__['get_carrot_man'] = get_carrot_man
  return state


def current_mpc(planner, sm):
  from openpilot.cereal import log
  planner.get_T_FOLLOW.__func__.__globals__['log'] = log
  from openpilot.selfdrive.controls.tests.test_longitudinal_gap_recovery import load_mpc_update
  path = REPO/'openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py'
  cls = load_mpc_update(path)
  # Match LongitudinalPlanner's production assignment after carrot.update.
  mpc = cls(mode=planner.mode)
  mpc.set_cur_state(sm['carState'].vEgo, sm['carState'].aEgo)
  mpc.set_accel_limits(-1.2, 2.)
  for name in ('leadOne', 'leadTwo'):
    lead = getattr(sm['radarState'], name)
    for key, value in dict(radar=False, radarTrackId=-1, dRel=1000., vLead=0.,
                           vRel=0., aLeadK=0., aLeadTau=1.5, modelProb=0.).items():
      if not hasattr(lead, key):
        setattr(lead, key, value)
  mpc.update(planner, False, sm['radarState'], 80/3.6,
             *(np.zeros(13) for _ in range(4)))
  return mpc


@pytest.mark.parametrize('distance', [0., 30.])
@pytest.mark.parametrize('mode', ['acc', 'blended'])
@pytest.mark.parametrize('lead', [False, True])
@pytest.mark.parametrize('speed', [0., 5/3.6, 5/3.6+.001, 21.25])
def test_explicit_user_stop_keeps_real_mpc_obstacle_for_three_cycles(distance, mode, lead, speed):
  p, sm = setup()
  p.user_stop_distance = distance
  sm['carState'].vEgo = sm['carState'].vEgoCluster = speed
  sm['radarState'].leadOne = NS(status=lead, dRel=4., vLead=speed)
  for i in range(3):
    update(p, sm, 1.+i*.05, 'green', mode=mode)
    assert p.mode == 'acc'
    assert 0 <= p.stop_dist <= distance
    assert p.user_stop_distance >= 0
    mpc = current_mpc(p, sm)
    # The actual final obstacle, not xState alone, stays constrained.
    assert np.all(mpc.params[:, 2] <= distance + p.trafficStopDistanceAdjust)
    if distance == 0:
      assert p.v_cruise == 0.
      assert mpc.source == 'e2e'
      assert 0 <= mpc.params[0, 2] <= p.trafficStopDistanceAdjust


@pytest.mark.parametrize('mode', ['acc', 'blended'])
@pytest.mark.parametrize('speed', [0., 5/3.6, 5/3.6+.001, 21.25])
def test_current_navigation_stay_survives_aux_retirement_and_new_lead(mode, speed):
  p, sm = setup()
  sm = navigation_inputs(p, sm)
  sm['carState'].vEgo = sm['carState'].vEgoCluster = speed
  for i in range(3):
    if i == 1:
      sm['radarState'].leadOne = NS(status=True, dRel=4., vLead=speed)
    sm.recv_time['carrotMan'] = time.monotonic()
    update(p, sm, 1.+i*.05, 'unknown', mode=mode)
    assert p.carrot_stay_stop and p.xState.name == 'e2eStopped'
    assert p.v_cruise == p.stop_dist == 0.
    assert p.mode == 'acc'
    mpc = current_mpc(p, sm)
    assert mpc.source == 'e2e'
    assert 0 <= mpc.params[0, 2] <= p.trafficStopDistanceAdjust


@pytest.mark.parametrize('reason', ['gas', 'brake', 'start'])
def test_current_navigation_stay_obeys_driver_or_new_left_start(reason):
  p, sm = setup()
  sm = navigation_inputs(p, sm)
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 2.
  update(p, sm, 1., 'unknown')
  assert p.v_cruise == p.stop_dist == 0.
  if reason == 'start':
    sm['carrotMan'].trafficState = 3  # actual red -> left producer
  else:
    setattr(sm['carState'], reason+'Pressed', True)
  sm['radarState'].leadOne = NS(status=True, dRel=4., vLead=2.)
  for i in range(3):
    sm.recv_time['carrotMan'] = time.monotonic()
    update(p, sm, 1.05+i*.05, 'unknown', mode='blended')
  assert p.xState.name != 'e2eStopped' and not p._signal_stop_owned
  assert p.v_cruise > 0. and p.mode == 'blended'


@pytest.mark.parametrize('pedal', ['gasPressed', 'brakePressed'])
@pytest.mark.parametrize('distance', [0., 30.])
def test_explicit_user_distance_cancelled_by_pedals_after_retirement(pedal, distance):
  p, sm = setup()
  p.user_stop_distance = distance
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 2.
  update(p, sm, 1., 'unknown')
  assert p.mode == 'acc' and p.stop_dist <= distance
  setattr(sm['carState'], pedal, True)
  for i in range(3):
    update(p, sm, 1.05+i*.05, 'unknown', mode='blended')
  assert p.user_stop_distance == -1 and p.mode == 'blended'


@pytest.mark.parametrize('camera', [True, False])
@pytest.mark.parametrize('active', [True, False])
@pytest.mark.parametrize('hold', [0, 1, 2])
def test_aux_retirement_does_not_clear_soft_hold_longcontrol_or_can(camera, active, hold, monkeypatch):
  from openpilot.selfdrive.controls.tests.test_longcontrol_hyundai_tuning import make_cp, DictParams
  from openpilot.selfdrive.controls.lib import longcontrol
  from opendbc.car.hyundai.tests.test_stopping import make_cs, send
  from opendbc.car.hyundai.stopping import CanfdStopping
  p, sm = setup()
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 2.
  sm['carState'].softHoldActive = hold
  for i in range(3):
    update(p, sm, 1.+i*.05, 'unknown')
  assert p.soft_hold_active == hold
  assert not p.start_decision.hold and not p.signal_decision.hold
  # Execute both production reset-state assignments using the actual hold.
  path = REPO/'openpilot/selfdrive/controls/lib/longitudinal_planner.py'
  cls = next(n for n in ast.parse(path.read_text(encoding='utf8')).body
             if isinstance(n, ast.ClassDef) and n.name == 'LongitudinalPlanner')
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update')
  reset = [n for n in method.body if isinstance(n, ast.Assign)
           and any(isinstance(t, ast.Name) and t.id == 'reset_state' for t in n.targets)]
  ns = dict(self=NS(CP=NS(openpilotLongitudinalControl=True)),
            sm={'selfdriveState': NS(enabled=active)}, long_control_off=not active,
            v_cruise_initialized=True, carrot=p)
  exec(compile(ast.Module(reset, type_ignores=[]), str(path), 'exec'), ns)
  assert bool(ns['reset_state']) == (not active or hold > 0)
  monkeypatch.setattr(longcontrol, 'Params', lambda: DictParams({'StoppingAccel': -50}))
  cp = make_cp(); cp.stopAccel = -.5
  control = longcontrol.LongControl(cp)
  cs = make_cs(); cs.softHoldActive = hold
  cs.out.softHoldActive = hold
  cs.out.vEgo = cs.out.vEgoRaw = 0.
  cs.out.wheelSpeeds = NS(fl=0., fr=0., rl=0., rr=0.)
  cs.out.cruiseState.standstill = False
  plan = NS(shouldStop=False, aTarget=0., vTargetNow=0., jTargetNow=0.)
  state = CanfdStopping(); previous = 0.
  for i in range(70):
    accel, _, _ = control.update(active, cs.out, plan, (-3.5, 2.), 0., sm['radarState'])
    values = send(camera, state, cs, enabled=active,
                  stopping=control.long_control_state == longcontrol.LongCtrlState.stopping,
                  accel=accel, previous_value=previous)
    previous = values['aReqValue']
  if hold:
    assert values['ACCMode'] == values['StopReq'] == 1
    assert values['aReqRaw'] == values['aReqValue'] == pytest.approx(-.5)
    assert control.long_control_state == (longcontrol.LongCtrlState.stopping if active else longcontrol.LongCtrlState.off)
  elif not active:
    assert values['ACCMode'] == values['StopReq'] == 0
    assert values['aReqRaw'] == values['aReqValue'] == 0.
  else:
    assert control.long_control_state == longcontrol.LongCtrlState.pid
    assert values['StopReq'] == 0


@pytest.mark.parametrize('mode', ['acc', 'blended'])
def test_navigation_stay_without_aux_history_keeps_native_lead_policy(mode):
  from tools.signal_analysis.replay_signal_assist import planner_class
  from openpilot.selfdrive.carrot.signal_start_guard import SignalStartGuard
  from openpilot.selfdrive.carrot.signal_assist import SignalAssist
  from tools.signal_analysis.test_signal_assist_planner import inputs
  cls, _ = planner_class((REPO/'openpilot/selfdrive/carrot/carrot_functions.py').read_text(encoding='utf8'))
  p = cls(start_guard=SignalStartGuard(), signal_assist=SignalAssist())
  sm = navigation_inputs(p, inputs(speed=2., lead=True))
  sm['radarState'].leadOne = NS(status=True, dRel=4., vLead=2.)
  for i in range(3):
    sm.recv_time['carrotMan'] = time.monotonic()
    update(p, sm, 1.+i*.05, 'unknown', mode=mode)
    assert p.carrot_stay_stop and not p._signal_stop_owned
    assert p.xState.name == 'lead' and p.mode == mode
    assert p.stop_dist == 1000. and p.v_cruise > 0.
