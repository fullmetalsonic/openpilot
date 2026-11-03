"""Real parser/DBC/capnp/controller chain; native IPC and physical ECU/lamp timing are outside this test."""
from types import SimpleNamespace

import pytest

from opendbc.car import Bus
from opendbc.car.hyundai import carcontroller, hyundaicanfd
from opendbc.car.hyundai.tests.test_blinker_hold import chain, decode_hold
from openpilot.cereal import log
from openpilot.selfdrive.controls.lib import desire_helper
from openpilot.selfdrive.modeld.constants import ModelConstants


class HoldWithoutDriverIntervention(hyundaicanfd.BlinkerHold):
  """Reference differs only in the new steering-press edge suppression."""
  def update(self, model, state, lateral_active):
    self.steering_pressed = state.steeringPressed
    return super().update(model, state, lateral_active)


class BlinkerChain:
  def __init__(self, chain, monkeypatch, side):
    self.state, self.parsers, self.controller, self.control, self.packer = chain
    self.state.CP.wheelSpeedFactor = 1.0  # CarInterfaceBase production default; the minimal HOLD fixture omits it.
    self.parsers[Bus.pt]._add_message("WHEEL_SPEEDS", ignore_counter=True)
    self.need_torque = 1
    params = carcontroller.Params()
    params.get_int.side_effect = lambda key: self.need_torque if key == "LaneChangeNeedTorque" else 0
    params.get_bool.side_effect = lambda key: key == "LaneChangeLeverWait"
    monkeypatch.setattr(desire_helper, "Params", lambda: params)
    self.desire = desire_helper.DesireHelper()
    self.desire.frame = 99  # Read the configured policy through the production periodic reader.
    self.desire.bluetooth_commands = SimpleNamespace(read=lambda allowed: None)
    self.carrot_man = SimpleNamespace(atcType="", carrotCmdIndex=0, carrotCmd="", carrotArg="")
    self.radar = log.RadarState.new_message()
    self.reference = carcontroller.CarController({Bus.pt: "hyundai_canfd_generated"}, self.state.CP)
    self.reference.hyundai_jerk.carrot_cruise = 0
    self.reference.blinker_hold = HoldWithoutDriverIntervention()
    self.control.actuators.torque = .2
    self.control.actuators.accel = -.4
    self.control.actuators.longControlState = "pid"
    self.control.hudControl.leadDistanceBars = 3
    self.side, self.sign = side, 1 if side == "left" else -1
    self.stalk, self.frame, self.probability = {}, 0, .1
    self.lamp_on = None
    self.can_valid = True
    self.trailer_connected = False
    self.lines = [-5.4, -1.8, 1.8, 5.4]
    self.state.modelV2 = self.model()
    self.holds = []
    self.differences = 0

  def model(self):
    length = len(ModelConstants.T_IDXS)
    return log.ModelDataV2.new_message(
      laneLines=[{"y": [y] * length} for y in self.lines],
      roadEdges=[{"y": [y] * length} for y in (-7.2, 7.2)],
      laneLineProbs=[1.] * 4, orientationRate={"z": [0.] * length},
      meta={"desireState": [0.] * 8},
    )

  def step(self, torque=0):
    self.frame += 1
    speed = {f"WHEEL_SPEED_{index}": 72. for index in range(1, 5)}
    values = [("WHEEL_SPEEDS", speed), ("BLINKER_STALKS", self.stalk),
              ("MDPS", {"STEERING_COL_TORQUE": torque, "STEERING_OUT_TORQUE": 500}),
              ("BLINKERS", {f"{self.side.upper()}_LAMP": self.frame > 50 if self.lamp_on is None else self.lamp_on})]
    messages = [self.packer.make_can_msg(name, self.parsers[Bus.pt].bus, value) for name, value in values]
    assert messages[1][0] == 0x3C1
    for parser in self.parsers.values():
      parser.update([(self.frame * 10_000_000, messages)])
    self.state.out = self.state.update_canfd(self.parsers)
    # Unrelated vehicle messages are absent from this bench; isolate their existing gates.
    self.state.out.canValid = self.can_valid
    self.state.out.trailerConnected = self.trailer_connected
    self.state.out.gearShifter = "drive"
    self.state.out.cruiseState.available = True
    self.state.out.cruiseState.enabled = True
    if self.frame % 5 == 0:
      model = self.model()
      self.desire.update(self.state.out, model, self.control.latActive, self.probability,
                         self.carrot_man, self.radar)
      model.meta.laneChangeState = self.desire.lane_change_state
      model.meta.laneChangeCompletedMonoTime = self.desire.lane_change_completed_mono_time
      model.meta.laneChangeDirection = self.desire.lane_change_direction
      model.meta.laneChangeBlinkerHold = self.desire.blinker_hold_direction
      model.meta.leverTurn = self.desire.lever_turn
      model.meta.desire = self.desire.desire
      model.meta.autoTurnSignal = self.desire.auto_turn_signal
      model.meta.autoTurnSignalLaneChange = self.desire.auto_turn_signal_lane_change
      # Cross the actual schema serialization boundary before the controller consumes the metadata.
      with log.ModelDataV2.from_bytes(model.to_bytes()) as wire:
        self.state.modelV2 = wire.as_builder()

    def maneuver_state():
      meta = self.state.modelV2.meta
      return (self.desire.lane_change_state, self.desire.queued_lane_change, self.desire.lane_change_direction,
              self.desire.desire, self.desire.blinker_hold_direction, str(meta.laneChangeState),
              str(meta.laneChangeDirection), str(meta.laneChangeBlinkerHold), str(meta.desire), meta.leverTurn)

    before = maneuver_state()
    actual_actuators, actual = self.controller.update(self.control.as_reader(), self.state, self.frame * 10_000_000)
    expected_actuators, expected = self.reference.update(self.control.as_reader(), self.state, self.frame * 10_000_000)
    assert actual_actuators.to_dict() == expected_actuators.to_dict()
    assert len(actual) == len(expected)
    for packet, reference in zip(actual, expected, strict=True):
      if packet[0] != 0x1EA:
        assert packet == reference
        continue
      assert (packet[0], packet[2]) == (reference[0], reference[2])
      # DBC HOLD bits 29/30 are byte 3 bits 5/6; the first two bytes are their derived CRC.
      normalize = lambda data: data[2:3] + bytes([data[3] & ~0x60]) + data[4:]
      assert normalize(packet[1]) == normalize(reference[1])
      hold, reference_hold = decode_hold(packet), decode_hold(reference)
      self.holds.append((hold, reference_hold))
      self.differences += hold != reference_hold
    assert before == maneuver_state()

  def repeat(self, count, torque=0):
    for _ in range(count):
      self.step(torque)

  def press(self, count=10):
    upper = self.side.upper()
    self.stalk = {f"{upper}_BLINKER": 1, f"{upper}_BLINKER_TAP": 1}
    self.repeat(count)
    self.stalk = {}


@pytest.mark.parametrize("side,requested,hold", [("left", 1, (1, 0)), ("right", 2, (0, 1))])
def test_physical_stalk_to_desire_and_packed_hold_intervention(chain, monkeypatch, side, requested, hold):
  rig = BlinkerChain(chain, monkeypatch, side)
  rig.repeat(50)
  assert rig.state.out.vEgo > 10.
  rig.press()
  assert rig.desire.lane_change_state == log.LaneChangeState.preLaneChange
  assert rig.holds[-1] == (hold, hold)
  assert getattr(rig.state.out, f"{side}BlinkerStalkCount") == 1

  # Torque which approves the first change must keep HOLD enabled while it remains pressed.
  rig.repeat(15, 300 * rig.sign)
  assert rig.state.out.steeringPressed
  assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeStarting
  assert rig.holds[-1] == (hold, hold)
  rig.repeat(10)
  assert not rig.state.out.steeringPressed

  target = 1 if side == "left" else 2
  other = 2 if side == "left" else 1
  rig.lines[target] = -.6 * rig.sign
  rig.repeat(5)
  rig.lines[target], rig.lines[other] = -2.3 * rig.sign, 1.3 * rig.sign
  rig.repeat(5)
  assert rig.desire.crossing.since >= 0.
  rig.press()
  assert rig.desire.queued_lane_change == requested
  assert rig.holds[-1] == (hold, hold)

  # A later manual press suppresses HOLD without cancelling the same-direction FSM or its queue.
  rig.repeat(10, 300 * rig.sign)
  assert rig.state.out.steeringPressed
  assert rig.holds[-1] == ((0, 0), hold)
  rig.repeat(15)
  assert not rig.state.out.steeringPressed
  assert rig.holds[-1] == ((0, 0), hold)
  assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeStarting
  assert rig.desire.queued_lane_change == requested
  assert rig.differences > 0

  # Only a fresh physical lever request rearms the suppressed hold within the episode.
  rig.press()
  assert getattr(rig.state.out, f"{side}BlinkerStalkCount") == 3
  assert rig.holds[-1] == (hold, hold)
  assert rig.desire.queued_lane_change == requested
  rig.repeat(10, -300 * rig.sign)
  assert rig.desire.lane_change_state == log.LaneChangeState.off
  assert rig.desire.queued_lane_change == 0
  rig.repeat(5, -300 * rig.sign)  # Include the first 0x1EA transmission after the 20 Hz cancellation.
  assert rig.holds[-1] == ((0, 0), (0, 0))


@pytest.mark.parametrize("ordinary_torque", [False, True])
@pytest.mark.parametrize("next_request", ["atc", "physical_stalk"])
def test_completed_mq4_change_ordinary_steering_and_next_request(chain, monkeypatch, ordinary_torque, next_request):
  rig = BlinkerChain(chain, monkeypatch, "left")
  rig.repeat(50)
  rig.press()
  rig.repeat(15, 300)
  assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeStarting
  rig.repeat(10)
  rig.repeat(200)
  rig.probability = 0.
  rig.repeat(5)
  assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeFinishing
  assert rig.desire.lane_change_completed_mono_time == 0
  rig.lamp_on = False
  rig.repeat(110)
  assert rig.desire.lane_change_state == log.LaneChangeState.off
  completed = rig.state.modelV2.meta.laneChangeCompletedMonoTime
  assert completed > 0
  rig.repeat(500)
  assert rig.state.modelV2.meta.laneChangeCompletedMonoTime == completed
  assert rig.holds[-1] == ((0, 0), (0, 0))
  counts = (rig.state.out.leftBlinkerStalkCount, rig.state.out.rightBlinkerStalkCount)
  assert counts == (1, 0)

  rig.repeat(50, 300 if ordinary_torque else 0)
  assert rig.state.out.steeringPressed == ordinary_torque
  rig.repeat(50)
  assert not rig.state.out.steeringPressed
  assert rig.control.hudControl.physicalBlinkerHold == 0
  rig.probability = .1
  if next_request == "atc":
    # The production parameter reader and navigation command create a fresh automatic request.
    rig.need_torque = 0
    rig.repeat(100)
    rig.carrot_man.atcType = "atc right"
    rig.carrot_man.carrotCmdIndex = 1
    rig.carrot_man.carrotCmd = "LANECHANGE"
    rig.carrot_man.carrotArg = "RIGHT"
    rig.repeat(30)
    assert (rig.state.out.leftBlinkerStalkCount, rig.state.out.rightBlinkerStalkCount) == counts
    assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeStarting
    assert str(rig.state.modelV2.meta.desire) == "laneChangeRight"
    assert str(rig.state.modelV2.meta.autoTurnSignal) == "right"
    assert rig.holds[-1] == ((0, 1), (0, 1))
  else:
    rig.press()
    rig.lamp_on = True
    rig.repeat(15, 300)
    assert rig.state.out.leftBlinkerStalkCount == 2
    assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeStarting
    assert rig.holds[-1] == ((1, 0), (1, 0))
  assert not rig.controller.blinker_hold.blocked


@pytest.mark.parametrize("at_completion", [False, True])
def test_mq4_finishing_opposite_torque_cancel_never_publishes_completion(chain, monkeypatch, at_completion):
  rig = BlinkerChain(chain, monkeypatch, "left")
  rig.repeat(50)
  rig.press()
  rig.repeat(15, 300)
  rig.repeat(210)
  rig.probability = 0.
  rig.repeat(5)
  assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeFinishing
  assert rig.desire.lane_change_completed_mono_time == 0
  if at_completion:
    while rig.desire.lane_change_ll_prob < .9:
      rig.repeat(5)
    assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeFinishing
  rig.repeat(10, -300)
  assert rig.state.out.steeringPressed
  assert rig.desire.lane_change_state == log.LaneChangeState.off
  assert rig.desire.lane_change_completed_mono_time == 0
  assert rig.state.modelV2.meta.laneChangeCompletedMonoTime == 0
  assert rig.controller.blinker_hold.blocked
  rig.repeat(5, -300)  # Include the first packed transmission after the 20 Hz model cancellation.
  assert rig.holds[-1] == ((0, 0), (0, 0))


def test_mq4_normal_completion_to_pre_persists_through_model_serialization(chain, monkeypatch):
  rig = BlinkerChain(chain, monkeypatch, "left")
  rig.repeat(50)
  rig.press()
  rig.repeat(15, 300)
  rig.repeat(210)
  rig.probability = 0.
  rig.repeat(115)
  assert rig.desire.lane_change_state == log.LaneChangeState.preLaneChange
  completed = rig.state.modelV2.meta.laneChangeCompletedMonoTime
  assert completed == rig.desire.lane_change_completed_mono_time and completed > 0
  assert not rig.controller.blinker_hold.in_change
  rig.repeat(50)
  assert rig.state.modelV2.meta.laneChangeCompletedMonoTime == completed


@pytest.mark.parametrize("gate", ["can_invalid", "lat_inactive", "trailer"])
def test_mq4_invalid_completion_does_not_publish_a_retirement_signal(chain, monkeypatch, gate):
  rig = BlinkerChain(chain, monkeypatch, "left")
  rig.repeat(50)
  rig.press()
  rig.repeat(15, 300)
  rig.repeat(210)
  rig.probability = 0.
  rig.repeat(5)
  assert rig.desire.lane_change_state == log.LaneChangeState.laneChangeFinishing
  while rig.desire.lane_change_ll_prob < .95:
    rig.repeat(5)
  rig.can_valid = gate != "can_invalid"
  rig.control.latActive = gate != "lat_inactive"
  rig.trailer_connected = gate == "trailer"
  rig.repeat(5)
  assert rig.desire.lane_change_completed_mono_time == 0
  assert rig.state.modelV2.meta.laneChangeCompletedMonoTime == 0
  assert rig.controller.blinker_hold.blocked
  rig.can_valid = rig.control.latActive = True
  rig.trailer_connected = False
  rig.repeat(10)
  assert rig.controller.blinker_hold.blocked
  assert rig.holds[-1][0] == (0, 0)
