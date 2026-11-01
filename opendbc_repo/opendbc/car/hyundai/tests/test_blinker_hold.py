"""Host parser/controller/packed-CAN checks; these do not establish physical lamp behavior."""
import binascii
import copy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.hyundai import carcontroller, carstate, hyundaicanfd
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.cereal import log


def model(state="laneChangeStarting", side="left", requested="none", lines=None):
  return log.ModelDataV2.new_message(
    meta={"desire": "laneChangeLeft" if side == "left" else "laneChangeRight",
          "laneChangeState": state, "laneChangeDirection": side, "laneChangeBlinkerHold": requested},
    laneLines=[{"y": [y]} for y in lines] if lines else [],
  )


@pytest.mark.parametrize("side,hold", [("left", 3), ("right", 4)])
def test_current_change_release_at_sixty_percent(side, hold):
  state = {}
  for target, other, expected in [(1.8, 1.8, hold), (.28, 3.32, hold), (1.36, .15, hold), (3.24, .36, 0), (1.8, 1.8, 0)]:
    left, right = (-target, other) if side == "left" else (-other, target)
    md = model(side=side, lines=[-5., left, right, 5.])
    assert hyundaicanfd._blink_hold_side(md, hold, state) == expected
  assert state["released"]
  assert hyundaicanfd._blink_hold_side(None, 0, state) == 0
  assert not state["released"]
  assert hyundaicanfd._blink_hold_side(model(side=side), hold, state) == hold


@pytest.mark.parametrize("phase", ["preLaneChange", "laneChangeStarting", "laneChangeFinishing"])
@pytest.mark.parametrize("side,hold", [("left", 3), ("right", 4)])
def test_waiting_and_queued_requests_override_current_change(phase, side, hold):
  md = model(state=phase, requested=side)
  helper = hyundaicanfd.BlinkerHold()
  assert helper.update(md, structs.CarState(canValid=True), True) == hold


@pytest.mark.parametrize("initial_pressed", [False, True])
@pytest.mark.parametrize("side", ["left", "right"])
def test_start_approval_is_not_intervention_and_later_press_latches(initial_pressed, side):
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True, steeringPressed=initial_pressed)
  md = model(side=side)
  expected = 3 if side == "left" else 4
  assert helper.update(md, cs, True) == expected
  assert helper.update(md, cs, True) == expected
  cs.steeringPressed = False
  assert helper.update(md, cs, True) == expected
  cs.steeringPressed = True
  assert helper.update(md, cs, True) == 0
  cs.steeringPressed = False
  md.meta.laneChangeBlinkerHold = "right" if side == "left" else "left"
  assert helper.update(md, cs, True) == 0
  md.meta.laneChangeState = "off"
  assert helper.update(md, cs, True) == 0
  md.meta.laneChangeState = "preLaneChange"
  assert helper.update(md, cs, True) == 0
  cs.leftBlinkerStalkCount = 1
  assert helper.update(md, cs, True) != 0


@pytest.mark.parametrize("phase", ["laneChangeStarting", "laneChangeFinishing"])
def test_model_cancel_precedes_debounced_driver_intervention(phase):
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True)
  md = model(state=phase)
  assert helper.update(md, cs, True) == 3
  md.meta.laneChangeState = "off"
  md.meta.desire = "none"
  assert helper.update(md, cs, True) == 0
  cs.steeringPressed = True
  assert helper.update(md, cs, True) == 0
  md.meta.laneChangeState = "preLaneChange"
  md.meta.laneChangeBlinkerHold = "left"
  assert helper.update(md, cs, True) == 0


@pytest.mark.parametrize("side,hold,next_side,next_hold", [("left", 3, "right", 4), ("right", 4, "left", 3)])
@pytest.mark.parametrize("next_state", ["off", "preLaneChange"])
def test_completed_change_does_not_treat_later_steering_as_intervention(side, hold, next_side, next_hold, next_state):
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True)
  md = model(side=side)
  assert helper.update(md, cs, True) == hold
  md.meta.laneChangeState = "laneChangeFinishing"
  assert helper.update(md, cs, True) == hold
  md.meta.laneChangeState = next_state
  md.meta.laneChangeDirection = "none"
  md.meta.desire = "none"
  md.meta.laneChangeCompletedMonoTime = 1
  assert helper.update(md, cs, True) == 0
  # Normal steering after completion has no authority over the next ATC/queued request.
  cs.steeringPressed = True
  assert helper.update(md, cs, True) == 0
  cs.steeringPressed = False
  assert helper.update(md, cs, True) == 0
  assert helper.update(model(side=next_side), cs, True) == next_hold
  assert not helper.blocked


@pytest.mark.parametrize("next_state", ["off", "preLaneChange"])
def test_completion_does_not_rearm_a_driver_blocked_request(next_state):
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True)
  md = model()
  assert helper.update(md, cs, True) == 3
  cs.steeringPressed = True
  assert helper.update(md, cs, True) == 0
  cs.steeringPressed = False
  md.meta.laneChangeState = "laneChangeFinishing"
  assert helper.update(md, cs, True) == 0
  md.meta.laneChangeState = next_state
  md.meta.desire = "none"
  md.meta.laneChangeCompletedMonoTime = 1
  assert helper.update(md, cs, True) == 0
  md.meta.laneChangeState = "preLaneChange"
  md.meta.laneChangeBlinkerHold = "right"
  assert helper.update(md, cs, True) == 0
  cs.rightBlinkerStalkCount = 1
  assert helper.update(md, cs, True) == 4


@pytest.mark.parametrize("interruption", ["missing_model", "can_invalid", "trailer", "lat_inactive"])
def test_interrupted_finishing_is_not_normal_completion(interruption):
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True)
  md = model(state="laneChangeFinishing")
  assert helper.update(md, cs, True) == 3
  md.meta.laneChangeState = "off"
  md.meta.desire = "none"
  cs.canValid = interruption != "can_invalid"
  cs.trailerConnected = interruption == "trailer"
  if interruption != "missing_model":
    md.meta.laneChangeCompletedMonoTime = 1
  assert helper.update(None if interruption == "missing_model" else md, cs, interruption != "lat_inactive") == 0
  cs.canValid, cs.trailerConnected, cs.steeringPressed = True, False, True
  assert helper.update(md, cs, True) == 0
  cs.steeringPressed = False
  assert helper.update(model(requested="left"), cs, True) == 0


def test_finishing_cancel_without_completion_signal_keeps_the_block():
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True)
  md = model(state="laneChangeFinishing")
  assert helper.update(md, cs, True) == 3
  md.meta.laneChangeState = "off"
  md.meta.desire = "none"
  cs.steeringPressed = True
  assert helper.update(md, cs, True) == 0
  cs.steeringPressed = False
  assert helper.update(model(requested="left"), cs, True) == 0


def test_normal_completion_precedes_an_observed_same_cycle_steering_edge():
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True)
  md = model(state="laneChangeFinishing")
  assert helper.update(md, cs, True) == 3
  md.meta.laneChangeState = "off"
  md.meta.desire = "none"
  md.meta.laneChangeCompletedMonoTime = 1
  cs.steeringPressed = True
  assert helper.update(md, cs, True) == 0
  assert not helper.blocked and not helper.in_change
  cs.steeringPressed = False
  assert helper.update(model(side="right"), cs, True) == 4


@pytest.mark.parametrize("stale_completion", [0, 4, 5])
def test_persistent_completion_survives_apply_gap_but_cannot_rearm_the_next_episode(stale_completion):
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True)
  assert helper.update(model(), cs, True) == 3
  # The controller missed completion/off/pre: the next Starting still carries the cumulative stamp.
  md = model(side="right")
  md.meta.laneChangeCompletedMonoTime = 5
  cs.steeringPressed = True
  assert helper.update(md, cs, True) == 4  # New start-confirmation torque, not prior-change intervention.
  cs.steeringPressed = False
  assert helper.update(md, cs, True) == 4
  cs.steeringPressed = True
  assert helper.update(md, cs, True) == 0
  cs.steeringPressed = False
  md.meta.laneChangeCompletedMonoTime = stale_completion
  assert helper.update(md, cs, True) == 0
  assert helper.completed_mono_time == 5


def test_off_stale_request_lat_inactive_and_counter_wrap():
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True, leftBlinkerStalkCount=255)
  md = model()
  assert helper.update(md, cs, True) == 3
  assert helper.update(md, cs, False) == 0
  assert helper.update(md, cs, True) == 0
  cs.leftBlinkerStalkCount = 0
  assert helper.update(md, cs, True) == 3
  md.meta.laneChangeState = "off"
  md.meta.laneChangeBlinkerHold = "left"
  assert helper.update(md, cs, True) == 0
  md.meta.laneChangeState = "preLaneChange"
  assert helper.update(md, cs, True) == 0
  assert hyundaicanfd.BlinkerHold().update(None, structs.CarState(canValid=True), True) == 0
  assert hyundaicanfd.BlinkerHold().update(model(), structs.CarState(canValid=True), True) == 3


@pytest.mark.parametrize("reason", ["canValid", "trailerConnected"])
def test_input_gate_revokes_hold_and_invalid_stalk_cannot_rearm(reason):
  helper = hyundaicanfd.BlinkerHold()
  cs = structs.CarState(canValid=True)
  md = model()
  assert helper.update(md, cs, True) == 3
  setattr(cs, reason, reason == "trailerConnected")
  cs.leftBlinkerStalkCount = 1
  assert helper.update(md, cs, True) == 0
  setattr(cs, reason, reason == "canValid")
  assert helper.update(md, cs, True) == 0
  cs.leftBlinkerStalkCount = 2
  assert helper.update(md, cs, True) == 3


@pytest.fixture
def chain(monkeypatch):
  params = Mock()
  params.get_int.return_value = 0
  params.get_float.return_value = 0.
  params.get_bool.return_value = False
  params.get.return_value = '{0: {}, 1: {}, 2: {}}'
  for module in (carstate, carcontroller, hyundaicanfd):
    monkeypatch.setattr(module, "Params", lambda: params)
  cp = structs.CarParams(carFingerprint=CAR.KIA_SORENTO_HEV_4TH_GEN,
                        flags=int(HyundaiFlags.CANFD | HyundaiFlags.CAMERA_SCC),
                        openpilotLongitudinalControl=True, wheelbase=2.81, steerRatio=14.,
                        stoppingDecelRate=.8, safetyConfigs=[{}])
  state = carstate.CarState(cp)
  state.modelV2 = model()
  state.radarState = None
  state.lfa = {}
  template_parser = CANParser("hyundai_canfd_generated", [("ADRV_0x1ea", 0)], 2)
  state.adrv_0x1ea = dict(template_parser.vl["ADRV_0x1ea"])
  state.adrv_0x1ea.update(COUNTER=254, CHECKSUM=1234, LEFT_BLINK_HOLD=1, RIGHT_BLINK_HOLD=1)
  parsers = state.get_can_parsers(cp)
  for parser in parsers.values():
    parser.controls_ready = True
  for name in ("BLINKER_STALKS", "BLINKERS", "MDPS"):
    parsers[Bus.pt]._add_message(name, ignore_counter=True)
  state.blinker_stalks = parsers[Bus.pt].vl["BLINKER_STALKS"]
  state.blinkers = parsers[Bus.pt].vl["BLINKERS"]
  controller = carcontroller.CarController({Bus.pt: "hyundai_canfd_generated"}, cp)
  controller.hyundai_jerk.carrot_cruise = 0
  cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
  return state, parsers, controller, cc, CANPacker("hyundai_canfd_generated")


def receive(chain, frame, stalk=None, torque=0, eps=0, lamps=None):
  state, parsers, _, _, packer = chain
  values = [("MDPS", {"STEERING_COL_TORQUE": torque, "STEERING_OUT_TORQUE": eps})]
  if stalk is not None:
    values.append(("BLINKER_STALKS", stalk))
  if lamps is not None:
    values.append(("BLINKERS", lamps))
  messages = [packer.make_can_msg(name, parsers[Bus.pt].bus, value) for name, value in values]
  for parser in parsers.values():
    parser.update([(frame * 10_000_000, messages)])
  state.out = state.update_canfd(parsers)
  state.out.canValid = True
  state.out.gearShifter = "drive"


def decode_hold(message):
  parser = CANParser("hyundai_canfd_generated", [("ADRV_0x1ea", 0)], 0)
  parser.update([(1_000_000_000, [message])])
  values = parser.vl["ADRV_0x1ea"]
  return values["LEFT_BLINK_HOLD"], values["RIGHT_BLINK_HOLD"]


def test_parser_stalk_edges_detents_raw_lamp_and_wrap(chain):
  state = chain[0]
  state.left_blinker_stalk_count = 255
  for frame, stalk, lever, count in [
    (1, {"LEFT_BLINKER": 1, "LEFT_BLINKER_TAP": 1}, 1, 0),
    (2, {"LEFT_BLINKER": 1}, 2, 0),
    (3, {}, 0, 0),
    (4, {"LEFT_BLINKER": 1}, 2, 1),
  ]:
    receive(chain, frame, stalk=stalk, lamps={"LEFT_LAMP": frame == 1})
    assert state.out.blinkerLever == lever
    assert state.out.leftBlinkerStalkCount == count
    assert state.out.rightBlinkerStalkCount == 0
    assert state.out.blinkerLamp == (1 if frame == 1 else 0)
  assert state.out.leftBlinker  # Existing filtered lamp remains independent of raw OFF.


@pytest.mark.parametrize("torque", [-300, 300])
def test_real_parser_driver_debounce_controller_and_packed_hold(chain, torque):
  state, _, controller, cc, _ = chain
  original = copy.deepcopy(state.adrv_0x1ea)
  outputs = []
  for frame in range(15):
    receive(chain, frame + 1, torque=torque if frame >= 1 else 0, eps=500)
    _, messages = controller.update(cc.as_reader(), state, (frame + 1) * 10_000_000)
    outputs.extend(message for message in messages if message[0] == 0x1EA)
  assert state.out.steeringPressed
  assert [decode_hold(message) for message in outputs] == [(1, 0), (1, 0), (0, 0)]
  assert state.adrv_0x1ea == original
  for index, (address, data, bus) in enumerate(outputs):
    assert data[2] == (255 + index) % 256 and bus == 0
    crc = binascii.crc_hqx(data[2:] + address.to_bytes(2, "little"), 0) ^ {32: 0x9F5B}[len(data)]
    assert int.from_bytes(data[:2], "little") == crc


def test_motor_torque_alone_does_not_release_hold(chain):
  state, _, controller, cc, _ = chain
  for frame in range(12):
    receive(chain, frame + 1, torque=0, eps=500)
    _, messages = controller.update(cc.as_reader(), state, (frame + 1) * 10_000_000)
    for message in messages:
      if message[0] == 0x1EA:
        assert decode_hold(message) == (1, 0)
  assert not state.out.steeringPressed


@pytest.mark.parametrize("bus", [Bus.pt, Bus.cam, Bus.alt])
def test_stalk_discovery_uses_only_pt_bus(chain, monkeypatch, bus):
  state, parsers, _, _, packer = chain
  state.blinker_stalks = None
  # A fresh parser inventory isolates dynamic discovery from the chain fixture.
  parsers = state.get_can_parsers(state.CP)
  for parser in parsers.values():
    parser.controls_ready = True
  monkeypatch.setattr(carstate, "Params", lambda: NS(get_bool=lambda key: True))
  state.controls_ready_count = 124
  packet = packer.make_can_msg("BLINKER_STALKS", parsers[bus].bus, {"LEFT_BLINKER": 1})
  for parser in parsers.values():
    parser.update([(1_000_000_000, [packet])])
  state.monitor_fingerprint(parsers, True)
  assert (state.blinker_stalks is not None) == (bus == Bus.pt)


@pytest.mark.parametrize("external", [2, 3])
def test_external_hold_cannot_bypass_driver_latch_and_new_stalk_rearms(chain, external):
  state, _, controller, cc, _ = chain
  cc.hudControl.physicalBlinkerHold = external
  before, after = [], []
  for frame in range(15):
    receive(chain, frame + 1, torque=300 if frame >= 1 else 0)
    _, messages = controller.update(cc.as_reader(), state, (frame + 1) * 10_000_000)
    for message in messages:
      if message[0] == 0x1EA:
        (after if state.out.steeringPressed else before).append(decode_hold(message))
  assert before and all(value == ((1, 0) if external == 2 else (0, 1)) for value in before)
  assert after and all(value == (0, 0) for value in after)
  # Another producer direction/request does not constitute a real lever press.
  cc.hudControl.physicalBlinkerHold = 3 if external == 2 else 2
  for frame in range(15, 20):
    receive(chain, frame + 1)
    _, messages = controller.update(cc.as_reader(), state, (frame + 1) * 10_000_000)
    for message in messages:
      if message[0] == 0x1EA:
        assert decode_hold(message) == (0, 0)
  receive(chain, 21, stalk={"LEFT_BLINKER": 1, "LEFT_BLINKER_TAP": 1})
  _, messages = controller.update(cc.as_reader(), state, 210_000_000)
  assert not controller.blinker_hold.blocked
  holds = [decode_hold(message) for message in messages if message[0] == 0x1EA]
  assert holds == ([(0, 1)] if external == 2 else [(1, 0)])


def test_external_parked_hold_without_lane_episode_keeps_upstream_behavior(chain):
  state, _, controller, cc, _ = chain
  state.modelV2 = None
  cc.enabled = cc.latActive = cc.longActive = False
  cc.hudControl.physicalBlinkerHold = 2
  receive(chain, 1)
  _, messages = controller.update(cc.as_reader(), state, 10_000_000)
  assert [decode_hold(message) for message in messages if message[0] == 0x1EA] == [(1, 0)]
