"""Real parser/DBC and same-process control tests, not ECU/IPC validation."""
from copy import copy
import ast
from pathlib import Path
import subprocess
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.hyundai import carstate, carcontroller, hyundaicanfd
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.paddle_mode4 import PaddleInput, PaddleContext, PaddleGesture, GestureState, read_paddle_input, make_paddle_context
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.selfdrive.car.paddle_context import publish_paddle_context
from openpilot.cereal import log
from openpilot.selfdrive.car.tests.test_paddle_gap import mode4


@pytest.fixture
def chain(monkeypatch):
  params = Mock()
  params.get_int.side_effect = lambda k: 4 if k in ("PaddleMode", "LongitudinalPersonalityMax") else 0
  params.get_float.return_value = 0.
  params.get_bool.return_value = False
  params.get.return_value = '{0: {}, 1: {}, 2: {}}'
  monkeypatch.setattr(carstate, "Params", lambda: params)
  monkeypatch.setattr(carcontroller, "Params", lambda: params)
  monkeypatch.setattr(hyundaicanfd, "Params", lambda: params)
  cp = structs.CarParams(carFingerprint=CAR.KIA_SORENTO_HEV_4TH_GEN, brand="hyundai",
                         flags=int(HyundaiFlags.CANFD | HyundaiFlags.CAMERA_SCC),
                         openpilotLongitudinalControl=True, pcmCruise=False,
                         wheelbase=2.81, steerRatio=14., stoppingDecelRate=.8, safetyConfigs=[{}])
  state = carstate.CarState(cp)
  state.out = structs.CarState(canValid=True, gearShifter="drive", vEgo=20., vEgoRaw=20., cruiseState={"available": True})
  state.scc_control = {"InfoDisplay": 0}
  state.softHoldActive = 0
  state.modelV2 = state.radarState = None
  controller = carcontroller.CarController({Bus.pt: "hyundai_canfd_generated"}, cp)
  controller.hyundai_jerk.carrot_cruise = 0
  controller.hyundai_jerk.jerk_u, controller.hyundai_jerk.jerk_l = 2., 1.
  ci = CarInterface.__new__(CarInterface)
  ci.CP, ci.CS, ci.CC = cp, state, controller
  helper = NS(_paddle_mode=4, CP=cp, params=params, _cruise_cancel_state=False,
              _activate_cruise=0, _paddle_decel_active=False, carrot_cruise_active=False)
  cc = structs.CarControl(enabled=True, longActive=True, actuators={"longControlState": "pid", "accel": -.6, "jerk": 0.},
                          hudControl={"leadDistanceBars": 3})
  return ci, helper, cc


@pytest.fixture
def legacy_builder():
  # Execute the immutable pre-change builder, not a duplicate current call.
  root = Path(__file__).resolve().parents[5]
  result = subprocess.run(['git', 'show', '0a3d206b:opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py'], cwd=root, capture_output=True)
  if result.returncode:
    pytest.skip('Pre-change 0a3d206b is unavailable in this shallow checkout; current functional tests still run')
  source = result.stdout.decode('utf-8')
  function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'create_acc_control_scc2')
  namespace = dict(vars(hyundaicanfd))
  exec(compile(ast.Module(body=[function], type_ignores=[]), '<pre-change SCC2>', 'exec'), namespace)
  return namespace['create_acc_control_scc2']


def input_batch(parser, packer, ticks, sequence):
  packets = [(timestamp, [packer.make_can_msg("CRUISE_BUTTONS", 0, dict(LEFT_PADDLE=l, RIGHT_PADDLE=r, CRUISE_BUTTONS=c))])
             for timestamp, l, r, c in ticks]
  parser.update(packets)
  return read_paddle_input(parser, "CRUISE_BUTTONS", "CRUISE_BUTTONS", sequence)


def apply_tick(chain, parser, packer, sequence, ticks, *, valid=True, apply=True, initializing=False):
  ci, helper, cc = chain
  input_batch(parser, packer, ticks, sequence)
  ci.CS._update_paddle_input(parser)
  ci.CS.paddle_button_prev = 1 if parser.vl['CRUISE_BUTTONS']['LEFT_PADDLE'] else 2 if parser.vl['CRUISE_BUTTONS']['RIGHT_PADDLE'] else 0
  now = ticks[-1][0]
  class SM(dict):
    seen = {'onroadEvents': True}
    def all_checks(self, keys):
      return valid
  events = [NS(name=log.OnroadEvent.EventName.selfdriveInitializing)] if initializing else []
  publish_paddle_context(ci.CP, ci, helper, SM(carControl=cc, onroadEvents=events), now)
  if not apply:
    return None
  _, messages = ci.apply(cc.as_reader(), now)
  return next((m for m in messages if m[0] == 0x1a0), None)


def decode(msg):
  parser = CANParser("hyundai_canfd_generated", [("SCC_CONTROL", 50)], 0)
  parser.update([(1_000_000_000, [msg])])
  return parser.vl['SCC_CONTROL']


@pytest.mark.parametrize('long_state', ['pid', 'stopping', 'starting'])
def test_real_ci_controller_accepts_fresh_normal_gesture(chain, long_state):
  ci, _, cc = chain
  cc.actuators.longControlState = long_state
  if long_state != 'pid':
    ci.CS.out.vEgo = ci.CS.out.vEgoRaw = 0.
    ci.CS.softHoldActive = 1  # Active-cruise normal hold is not independent.
  parser, packer = CANParser('hyundai_canfd_generated', [('CRUISE_BUTTONS', 50)], 0), CANPacker('hyundai_canfd_generated')
  apply_tick(chain, parser, packer, 1, [(1_000_000_000, 0, 0, 0)])
  apply_tick(chain, parser, packer, 2, [(1_010_000_000, 0, 1, 0)])  # odd control tick
  msg = apply_tick(chain, parser, packer, 3, [(1_020_000_000, 0, 1, 0)])
  assert ci.CC.paddle_gesture.state == GestureState.ACCEPTED
  assert decode(msg)['ACCMode'] == 1


def test_skipped_apply_invalid_epoch_cannot_reaccept_same_hold(chain):
  parser, packer = CANParser('hyundai_canfd_generated', [('CRUISE_BUTTONS', 50)], 0), CANPacker('hyundai_canfd_generated')
  apply_tick(chain, parser, packer, 1, [(1_000_000_000, 0, 0, 0)])
  apply_tick(chain, parser, packer, 2, [(1_010_000_000, 0, 1, 0)])
  apply_tick(chain, parser, packer, 3, [(1_020_000_000, 0, 1, 0)], valid=False, apply=False)
  msg = apply_tick(chain, parser, packer, 4, [(1_030_000_000, 0, 1, 0)])
  assert decode(msg)['ACCMode'] == 0
  apply_tick(chain, parser, packer, 5, [(1_040_000_000, 0, 0, 0)])
  msg = apply_tick(chain, parser, packer, 6, [(1_050_000_000, 0, 1, 0)])
  assert decode(msg)['ACCMode'] == 1


def test_initializing_apply_skip_revokes_even_with_valid_carcontrol(chain):
  parser, packer = CANParser('hyundai_canfd_generated', [('CRUISE_BUTTONS', 50)], 0), CANPacker('hyundai_canfd_generated')
  apply_tick(chain, parser, packer, 1, [(1_000_000_000, 0, 0, 0)])
  apply_tick(chain, parser, packer, 2, [(1_010_000_000, 0, 1, 0)])
  assert chain[0].CC.paddle_gesture.state == GestureState.ACCEPTED
  apply_tick(chain, parser, packer, 3, [(1_020_000_000, 0, 1, 0)], initializing=True, apply=False)
  assert not chain[0].CS.paddle_context.eligible
  msg = apply_tick(chain, parser, packer, 4, [(1_030_000_000, 0, 1, 0)])
  assert decode(msg)['ACCMode'] == 0
  apply_tick(chain, parser, packer, 5, [(1_040_000_000, 0, 0, 0)])
  msg = apply_tick(chain, parser, packer, 6, [(1_050_000_000, 0, 1, 0)])
  assert decode(msg)['ACCMode'] == 1


def test_template_loss_epoch_requires_release_after_recovery(chain):
  parser, packer = CANParser('hyundai_canfd_generated', [('CRUISE_BUTTONS', 50)], 0), CANPacker('hyundai_canfd_generated')
  apply_tick(chain, parser, packer, 1, [(1_000_000_000, 0, 0, 0)])
  apply_tick(chain, parser, packer, 2, [(1_010_000_000, 0, 1, 0)])
  chain[0].CS.scc_control = None
  apply_tick(chain, parser, packer, 3, [(1_020_000_000, 0, 1, 0)], apply=False)
  chain[0].CS.scc_control = {'InfoDisplay': 0}
  msg = apply_tick(chain, parser, packer, 4, [(1_030_000_000, 0, 1, 0)])
  assert decode(msg)['ACCMode'] == 0


def test_full_carstate_raw_decode_and_freshness_metadata(chain):
  ci, _, _ = chain
  ci.can_parsers = ci.CS.get_can_parsers(ci.CP)
  ci.v_ego_cluster_seen = False
  packer = CANPacker('hyundai_canfd_generated')
  now = 1_000_000_000
  decoded = ci.update([(now, [packer.make_can_msg('CRUISE_BUTTONS', 0, {'RIGHT_PADDLE': 1}),
                              packer.make_can_msg('GEAR_SHIFTER', 0, {'GEAR': 4})])])
  assert ci.CS.paddle_button_prev == 2
  assert ci.CS.paddle_input.samples == ((now, 0, 1),)
  assert ci.CS.paddle_input.valid
  assert any(b.type == structs.CarState.ButtonEvent.Type.paddleRight and b.pressed for b in decoded.buttonEvents)


def test_gap_writer_then_production_bridge_and_real_can_controller(chain, mode4):
  ci, _, cc = chain
  helper, _, _, params, forbidden_calls = mode4
  helper.CP = ci.CP
  ci.can_parsers = ci.CS.get_can_parsers(ci.CP)
  ci.v_ego_cluster_seen = False
  packer = CANPacker('hyundai_canfd_generated')
  class SM(dict):
    seen = {'onroadEvents': True}
    def all_checks(self, keys): return True
  for index, raw in enumerate([0, 1, 1, 0]):
    now = 1_000_000_000 + index * 20_000_000
    decoded = ci.update([(now, [packer.make_can_msg('CRUISE_BUTTONS', 0, {'RIGHT_PADDLE': raw})])])
    # Synthetic valid normal-driving telemetry; the complete CarState decoder
    # above supplies the real raw-paddle events and source metadata.
    decoded.canValid, decoded.gearShifter, decoded.cruiseState.available = True, 'drive', True
    decoded.accFaulted = decoded.brakePressed = decoded.gasPressed = False
    decoded.vEgo = decoded.vEgoRaw = 20.
    ci.CS.scc_control = {'InfoDisplay': 0}
    helper._update_cruise_buttons(decoded, cc, 80)
    helper._paddle_gap_writer._queue.join()
    cc.hudControl.leadDistanceBars = params.get_int('LongitudinalPersonality') + 1
    publish_paddle_context(ci.CP, ci, helper, SM(carControl=cc, onroadEvents=[]), now)
    _, messages = ci.apply(cc.as_reader(), now)
    _, odd_messages = ci.apply(cc.as_reader(), now + 10_000_000)
    messages += odd_messages
    msg = next(m for m in messages if m[0] == 0x1a0)
    assert decode(msg)['ACCMode'] == 1
    assert decode(msg)['DISTANCE_SETTING'] == cc.hudControl.leadDistanceBars
  assert params.writes == [2]
  assert not forbidden_calls and helper._activate_cruise == 0


@pytest.mark.parametrize('source', ['CRUISE_BUTTONS', 'GEAR'])
def test_input_parser_bad_latest_dlc_is_not_a_release(source):
  parser, packer = CANParser('hyundai_canfd_generated', [(source, 50)], 0), CANPacker('hyundai_canfd_generated')
  msg = packer.make_can_msg(source, 0, {'LEFT_PADDLE': 0, 'RIGHT_PADDLE': 0})
  parser.update([(1_000_000_000, [msg])])
  assert read_paddle_input(parser, source, 'CRUISE_BUTTONS', 1).valid
  parser.dat[msg[0]] = b''
  assert not read_paddle_input(parser, source, 'CRUISE_BUTTONS', 2).valid


def test_batch_cancel_press_release_veto_even_when_final_button_zero(chain):
  parser, packer = CANParser('hyundai_canfd_generated', [('CRUISE_BUTTONS', 50)], 0), CANPacker('hyundai_canfd_generated')
  apply_tick(chain, parser, packer, 1, [(1_000_000_000, 0, 0, 0)])
  apply_tick(chain, parser, packer, 2, [(1_010_000_000, 0, 1, 0)])
  apply_tick(chain, parser, packer, 3, [(1_020_000_000, 0, 1, 4), (1_020_000_000, 0, 1, 0)])
  assert chain[0].CS.paddle_input.cancel_seen
  assert chain[0].CC.paddle_gesture.state == GestureState.WAIT_RELEASE


def test_odd_control_tick_cancel_is_latched_before_next_even_send(chain):
  parser, packer = CANParser('hyundai_canfd_generated', [('CRUISE_BUTTONS', 50)], 0), CANPacker('hyundai_canfd_generated')
  apply_tick(chain, parser, packer, 1, [(1_000_000_000, 0, 0, 0)])
  apply_tick(chain, parser, packer, 2, [(1_010_000_000, 0, 1, 0)])
  apply_tick(chain, parser, packer, 3, [(1_020_000_000, 0, 1, 0)])
  assert chain[0].CC.frame % 2 == 1
  assert apply_tick(chain, parser, packer, 4, [(1_030_000_000, 0, 1, 4)]) is None
  assert chain[0].CC.paddle_gesture.state == GestureState.WAIT_RELEASE
  msg = apply_tick(chain, parser, packer, 5, [(1_040_000_000, 0, 1, 0)])
  assert decode(msg)['ACCMode'] == 0


def gesture():
  return PaddleGesture(), PaddleContext(4, True, 0, 1_000_000_000, True)


def step(g, context, seq, bits, timestamp, **kwargs):
  samples = tuple((timestamp, l, r) for l, r in bits)
  sample = PaddleInput(seq, kwargs.pop('source', 'CRUISE_BUTTONS'), timestamp, samples, kwargs.pop('valid', True))
  context = replace(context, timestamp=timestamp, **kwargs)
  raw = 1 if bits[-1][0] else 2 if bits[-1][1] else 0
  return g.update(sample, context, True, timestamp, raw)


def test_start_held_side_switch_and_full_release():
  g, c = gesture()
  assert not step(g, c, 1, [(0, 1)], 1_000_000_000)
  assert not step(g, c, 2, [(1, 0)], 1_010_000_000)
  assert not step(g, c, 3, [(0, 0)], 1_020_000_000)
  assert step(g, c, 4, [(0, 1)], 1_030_000_000)
  assert step(g, c, 5, [(1, 0)], 1_040_000_000)


def test_same_packet_release_press_is_ordered_but_cached_release_is_not_new():
  g, c = gesture()
  assert step(g, c, 1, [(0, 0), (1, 0)], 1_000_000_000)
  assert not step(g, c, 2, [(0, 0)], 1_010_000_000, eligible=False)
  assert not step(g, c, 3, [(0, 0), (0, 1)], 1_010_000_000)
  assert step(g, c, 4, [(0, 0), (0, 1)], 1_020_000_000)


@pytest.mark.parametrize('fault', ['stale', 'source', 'clock', 'generation', 'invalid', 'call_gap'])
def test_faults_revoke_and_require_future_release(fault):
  g, c = gesture()
  assert step(g, c, 1, [(0, 0), (1, 0)], 1_000_000_000)
  sample = PaddleInput(2, 'GEAR' if fault == 'source' else 'CRUISE_BUTTONS', 1_010_000_000,
                       ((1_010_000_000, 1, 0),), fault != 'invalid')
  context = replace(c, timestamp=1_010_000_000, generation=1 if fault == 'generation' else 0)
  if fault == 'call_gap':
    sample = replace(sample, timestamp=1_111_000_000, samples=((1_111_000_000, 1, 0),))
    context = replace(context, timestamp=1_111_000_000)
  now = 1_111_000_000 if fault in ('stale', 'call_gap') else 999_000_000 if fault == 'clock' else 1_010_000_000
  assert not g.update(sample, context, True, now, 1)
  assert g.state == GestureState.WAIT_RELEASE


@pytest.mark.parametrize('fault', ['disabled', 'longOff', 'off', 'cancel', 'override', 'brake', 'gas', 'avh', 'parking', 'unavailable', 'invalid', 'gear', 'fault', 'carrot', 'helperCancel', 'negative', 'decel', 'helperCarrot', 'mode', 'levels', 'fingerprint', 'pcm', 'nonCamera', 'jerkCarrot'])
def test_disallowed_states_keep_existing_paddle_cutoff(chain, fault, legacy_builder):
  ci, helper, cc = chain
  actions = {
    'disabled': (cc, 'enabled', False), 'longOff': (cc, 'longActive', False), 'off': (cc.actuators, 'longControlState', 'off'),
    'cancel': (cc.cruiseControl, 'cancel', True), 'override': (cc.cruiseControl, 'override', True),
    'brake': (ci.CS.out, 'brakePressed', True), 'gas': (ci.CS.out, 'gasPressed', True),
    'avh': (ci.CS.out, 'brakeHoldActive', True), 'parking': (ci.CS.out, 'parkingBrake', True),
    'unavailable': (ci.CS.out.cruiseState, 'available', False), 'invalid': (ci.CS.out, 'canValid', False),
    'gear': (ci.CS.out, 'gearShifter', 'park'), 'fault': (ci.CS.out, 'accFaulted', True),
    'carrot': (ci.CS.out, 'carrotCruise', 1), 'helperCancel': (helper, '_cruise_cancel_state', True),
    'negative': (helper, '_activate_cruise', -1), 'decel': (helper, '_paddle_decel_active', True),
    'helperCarrot': (helper, 'carrot_cruise_active', True), 'mode': (helper, '_paddle_mode', 2),
    'fingerprint': (ci.CP, 'carFingerprint', CAR.KIA_EV6), 'pcm': (ci.CP, 'pcmCruise', True),
    'nonCamera': (ci.CP, 'flags', int(HyundaiFlags.CANFD)), 'jerkCarrot': (ci.CC.hyundai_jerk, 'carrot_cruise', 1),
  }
  if fault == 'levels':
    helper.params.get_int.side_effect = lambda key: 3
  else:
    obj, attr, value = actions[fault]
    setattr(obj, attr, value)
  now = 1_000_000_000
  ci.CS.paddle_input = PaddleInput(1, 'CRUISE_BUTTONS', now, ((now, 0, 0), (now, 1, 0)), True)
  ci.CS.paddle_button_prev = 1
  ci.CS.paddle_context = make_paddle_context(None, helper, ci.CS, cc, True, now)
  assert not ci.CC._paddle_gap_override(cc, ci.CS, now)
  if fault == 'disabled':
    ci.CS.softHoldActive = 1
  a = legacy_builder(CANPacker('hyundai_canfd_generated'), NS(ECAN=0), cc.enabled, -.6, -.6, False,
                                        cc.cruiseControl.override, 80., cc.hudControl, ci.CC.hyundai_jerk, ci.CS, ci.CC.canfd_stopping)
  b = hyundaicanfd.create_acc_control_scc2(CANPacker('hyundai_canfd_generated'), NS(ECAN=0), cc.enabled, -.6, -.6, False,
                                        cc.cruiseControl.override, 80., cc.hudControl, ci.CC.hyundai_jerk, ci.CS, ci.CC.canfd_stopping,
                                        paddle_gap_override=False)
  assert a == b


@pytest.mark.parametrize('stopping,soft_hold,speed', [(False, 0, 20.), (True, 0, 2.), (True, 1, 0.), (False, 0, .5)])
def test_accepted_acc_trajectory_bytes_match_prechange_no_paddle(chain, legacy_builder, stopping, soft_hold, speed):
  from opendbc.car.hyundai.stopping import CanfdStopping
  ci, _, cc = chain
  ci.CS.softHoldActive = soft_hold
  ci.CS.out.vEgo = ci.CS.out.vEgoRaw = speed
  ci.CS.out.wheelSpeeds.fl = ci.CS.out.wheelSpeeds.fr = ci.CS.out.wheelSpeeds.rl = ci.CS.out.wheelSpeeds.rr = speed
  baseline = copy(ci.CS)
  baseline.scc_control = dict(ci.CS.scc_control)
  baseline.paddle_button_prev = 0
  baseline_stop, candidate_stop = CanfdStopping(), CanfdStopping()
  old_packer, new_packer = CANPacker('hyundai_canfd_generated'), CANPacker('hyundai_canfd_generated')
  old_value = new_value = -.62
  for index in range(24):
    ci.CS.paddle_button_prev = 2 if 3 <= index <= 18 else 0
    ci.CS.scc_control['COUNTER'] = baseline.scc_control['COUNTER'] = index
    target = -.8 if stopping else -.4
    expected, old_value = legacy_builder(old_packer, NS(ECAN=0), True, old_value, target, stopping, False, 80.,
                                         cc.hudControl, ci.CC.hyundai_jerk, baseline, baseline_stop)
    actual, new_value = hyundaicanfd.create_acc_control_scc2(new_packer, NS(ECAN=0), True, new_value, target, stopping, False, 80.,
                                                          cc.hudControl, ci.CC.hyundai_jerk, ci.CS, candidate_stop,
                                                          paddle_gap_override=True)
    assert actual == expected, {k: (v, decode(actual)[k]) for k, v in decode(expected).items() if v != decode(actual)[k]}
    assert new_value == old_value


def test_legacy_failure_reproduces_request_drop_and_restart(chain, legacy_builder):
  ci, _, cc = chain
  packer = CANPacker('hyundai_canfd_generated')
  value = -.62
  requests = []
  for raw in [0, 2, 0]:
    ci.CS.paddle_button_prev = raw
    msg, value = legacy_builder(packer, NS(ECAN=0), True, value, -.8, False, False, 80., cc.hudControl,
                               ci.CC.hyundai_jerk, ci.CS, ci.CC.canfd_stopping)
    requests.append((decode(msg)['ACCMode'], value))
  assert requests[0][0] == 1 and requests[0][1] < -.62
  assert requests[1] == (0, 0)
  assert requests[2][0] == 1 and requests[2][1] == pytest.approx(-.02)
