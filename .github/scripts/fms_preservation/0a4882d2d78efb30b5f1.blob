"""Production transport -> guard -> planner green permission boundaries."""
import json

import pytest

from openpilot.selfdrive.carrot.signal_start_guard import SignalStartRuntime
from openpilot.selfdrive.carrot.tests.test_signal_assist_runtime import SM
from openpilot.selfdrive.carrot.tests.test_signal_start_guard import observation
from tools.signal_analysis.replay_signal_assist import REPO, planner_class
from tools.signal_analysis.test_signal_assist_planner import inputs


SERVICES = ('carState', 'modelV2', 'radarState', 'selfdriveState', 'carControl')


def transport(tmp_path):
  flag, path = tmp_path/'enabled', tmp_path/'observation'
  flag.write_text('1')
  return SignalStartRuntime(flag, path), path


def packet(path, t, color):
  path.write_text(json.dumps(dict(version=1, stream='road', size=[1344, 760], **observation(t, color))))


@pytest.mark.parametrize('service', SERVICES)
@pytest.mark.parametrize('age,valid', [(.249, True), (.25, True), (.251, False), (-.01, False)])
def test_each_required_service_has_a_250ms_receipt_boundary(tmp_path, service, age, valid):
  runtime, path = transport(tmp_path)
  packet(path, 10., 'green')
  sm = SM(); sm.recv_time[service] = 10.-age
  _, ctx = runtime.read(sm, 10.)
  assert ctx['valid'] is valid


@pytest.mark.parametrize('veto', ['stale_control', 'stale_model', 'cancel', 'long_inactive', 'invalid_can'])
def test_green_loses_permission_on_context_veto_and_next_fresh_packet(tmp_path, veto):
  runtime, path = transport(tmp_path)
  cls, _ = planner_class((REPO/'openpilot/selfdrive/carrot/carrot_functions.py').read_text(encoding='utf8'))
  p = cls(start_guard=runtime.guard)
  sm = SM(); sm.update(inputs(distance=3.))
  sm['carState'].canValid = True; sm['carState'].gearShifter = 'drive'
  sm['selfdriveState'].enabled = True
  sm['modelV2'].velocity.x = [0.]*33

  def consume(t, color):
    packet(path, t, color)
    obs, ctx = runtime.read(sm, t)
    p.update(sm, 80., 'acc', start_observation=obs, start_context=ctx)

  for i in range(30):
    t = 10.+i*.1
    sm.recv_time = dict.fromkeys(SERVICES, t)
    consume(t, 'red' if i < 5 else 'green')
  assert p.signal_green_active and p.start_decision.green_confirmed
  t = 13.
  sm.recv_time = dict.fromkeys(SERVICES, t)
  if veto == 'stale_control': sm.recv_time['carControl'] = t-.3
  if veto == 'stale_model': sm.recv_time['modelV2'] = t-.3
  if veto == 'cancel': sm['selfdriveState'].enabled = False
  if veto == 'long_inactive': sm['carControl'].longActive = False
  if veto == 'invalid_can': sm['carState'].canValid = False
  consume(t, 'green')
  assert not p.signal_green_active and not p.start_decision.green_confirmed
  sm.recv_time = dict.fromkeys(SERVICES, t+.1)
  sm['selfdriveState'].enabled = sm['carControl'].longActive = sm['carState'].canValid = True
  consume(t+.1, 'green')
  assert not p.signal_green_active and not p.start_decision.green_confirmed
