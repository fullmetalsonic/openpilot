"""FMS defers e944a8c5: a lead must not bypass an acquired auxiliary red veto.

The upstream field report records motion without pedals during a transient lead
exception. These are host state/output regressions, not a vehicle repair claim.
"""
import ast
from types import SimpleNamespace as NS

import pytest

from openpilot.selfdrive.carrot.signal_assist import SignalAssist
from openpilot.selfdrive.carrot.signal_start_guard import SignalStartGuard
from openpilot.selfdrive.carrot.tests.test_signal_start_guard import observation
from tools.signal_analysis.replay_signal_assist import REPO, planner_class
from tools.signal_analysis.test_signal_assist_planner import inputs


def setup():
  cls, _ = planner_class((REPO/'openpilot/selfdrive/carrot/carrot_functions.py').read_text(encoding='utf8'))
  p = cls(start_guard=SignalStartGuard(), signal_assist=SignalAssist())
  sm = inputs(distance=320./31.)
  for i in range(15):
    update(p, sm, i*.05)
  assert p.start_decision.hold and p.signal_decision.hold
  return p, sm


def update(p, sm, t, color='red', mode='acc'):
  ctx = dict(now=t, enabled=True, valid=True, drive=True)
  old = dict(timestamp=t, frame_id=round(t*1000), session='camera', tracks=[dict(
    id=1, box=[640,250,680,270], state=color, age=0., observations=10, evidence={'raw':color})])
  p.update(sm, 80., mode, start_observation=observation(t, color), start_context=ctx,
           signal_observation=old, signal_context=ctx)


def final_output(p):
  path = REPO/'openpilot/selfdrive/controls/lib/longitudinal_planner.py'
  cls = next(n for n in ast.parse(path.read_text(encoding='utf8')).body if isinstance(n, ast.ClassDef) and n.name=='LongitudinalPlanner')
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name=='apply_signal_start_guard')
  ns = {}; exec(compile(ast.Module([method], type_ignores=[]), str(path), 'exec'), ns)
  output = NS(output_should_stop=False, output_v_target_now=1., output_a_target=.2)
  ns['apply_signal_start_guard'](output, p)
  return output


def assert_red_veto(p):
  assert p.start_decision.hold and p.signal_decision.hold
  assert p.xState.name == 'e2eStopped' and p.v_cruise == 0 and p.stop_dist == 0
  assert not p.start_decision.green_confirmed and not p.start_decision.released
  output = final_output(p)
  assert output.output_should_stop and output.output_v_target_now == 0 and output.output_a_target <= 0


@pytest.mark.parametrize('distance', [4., 11.99, 12., 12.01, 100., 0., -1., float('nan'), float('inf')])
@pytest.mark.parametrize('mode', ['acc', 'blended'])
def test_lead_range_cannot_remove_either_acquired_red_veto(distance, mode):
  p, sm = setup()
  sm['radarState'].leadOne = NS(status=True, dRel=distance, vLead=1.)
  update(p, sm, 1., mode=mode)
  assert_red_veto(p)
  assert 'audioRefuse' not in p.events.events  # notice muting has no control permission
  assert p.signal_observation_color == 1


@pytest.mark.parametrize('loss', ['missing', 'outside'])
def test_red_veto_stays_continuous_through_lead_entry_unknown_and_loss(loss):
  p, sm = setup()
  sm['radarState'].leadOne = NS(status=True, dRel=4., vLead=0.)
  for i in range(20, 40):
    update(p, sm, i*.05, 'unknown')
    assert_red_veto(p)
  if loss == 'missing':
    sm['radarState'].leadOne.status = False
  else:
    sm['radarState'].leadOne.dRel = 12.
  update(p, sm, 2., 'unknown')
  assert_red_veto(p)


@pytest.mark.parametrize('inhibit', ['soft_hold', 'user_stop'])
def test_driver_holds_survive_qualifying_lead(inhibit):
  p, sm = setup()
  sm['radarState'].leadOne = NS(status=True, dRel=4.)
  if inhibit == 'soft_hold':
    sm['carState'].softHoldActive = 1
  else:
    p.user_stop_distance = 0.
  update(p, sm, 1.)
  assert_red_veto(p)


def set_model_stop(sm, distance):
  sm['modelV2'].position.x = [distance*i/31. for i in range(33)]


def test_recorded_transient_lead_does_not_open_red_departure_for_450ms():
  p, sm = setup()
  set_model_stop(sm, 32.216)
  for i in range(20, 45):
    update(p, sm, i*.05, 'unknown')
  assert p.xStop == pytest.approx(32.216)
  sm['radarState'].leadOne = NS(status=True, dRel=32.605, vLead=1.)
  for i in range(45, 54):
    update(p, sm, i*.05, 'unknown')
    assert sm['radarState'].leadOne.dRel - p.xStop < 2.
    assert_red_veto(p)
  sm['radarState'].leadOne.status = False
  for i in range(54, 65):
    update(p, sm, i*.05, 'unknown')
    assert_red_veto(p)


def test_recorded_fixed_lead_and_model_boundary_crossings_never_toggle_red_veto():
  p, sm = setup()
  sm['radarState'].leadOne = NS(status=True, dRel=4.15, vLead=0.)
  sides = set()
  frame = 20
  for stop in (2.152, 2.146)*4:
    set_model_stop(sm, stop)
    for _ in range(25):
      update(p, sm, frame*.05, 'unknown')
      frame += 1
      sides.add(sm['radarState'].leadOne.dRel - p.xStop < 2.)
      assert_red_veto(p)
  assert sides == {True, False}  # actual production filters crossed the old exception


@pytest.mark.parametrize('condition', ['mode_off', 'no_red', 'confirmed_green'])
def test_native_stop_to_lead_handover_remains_when_auxiliary_red_does_not_hold(condition):
  p, sm = setup()
  if condition == 'mode_off':
    p.start_guard.reset(); p.signal_assist.reset()
  elif condition == 'no_red':
    cls, _ = planner_class((REPO/'openpilot/selfdrive/carrot/carrot_functions.py').read_text(encoding='utf8'))
    p = cls(start_guard=SignalStartGuard(), signal_assist=SignalAssist())
  else:
    for i in range(20, 45):
      update(p, sm, i*.05, 'green')
    assert not p.start_decision.hold and not p.signal_decision.hold
  p.xState = type(p.xState).e2eStopped
  sm['radarState'].leadOne = NS(status=True, dRel=4., vLead=1.)
  if condition == 'mode_off':
    ctx = dict(now=3., enabled=False, valid=True, drive=True)
    p.update(sm, 80., 'acc', start_context=ctx, signal_context=ctx)
  else:
    update(p, sm, 3., 'green' if condition == 'confirmed_green' else 'unknown')
  assert p.xState.name == 'lead' and not p.start_decision.hold and not p.signal_decision.hold
  assert not final_output(p).output_should_stop
