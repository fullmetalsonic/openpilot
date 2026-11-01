from types import SimpleNamespace
import pytest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.selfdrive.controls.lib.desire_lib.lever import LEVER_REQUEST_WAIT_S
from openpilot.common.realtime import DT_MDL


def make_car_state(left_blinker=False):
  return SimpleNamespace(
    canValid=True,
    leftBlinker=left_blinker,
    rightBlinker=False,
    vEgo=20.0,
    aEgo=0.0,
    trailerConnected=False,
    steeringTorque=0.0,
    steeringPressed=False,
  )


class TestDesireHelperDriverIntent:
  def setup_method(self):
    self.helper = DesireHelper()
    self.helper._update_params_periodic = lambda: None
    self.helper._process_sides = lambda car, model, radar: None
    self.helper._check_desire_state = lambda model, car, maneuver: None
    self.helper.laneChangeNeedTorque = 0
    self.helper.laneChangeBsd = 0
    self.helper.laneLineCheck = 0

    side = self.helper.left
    side.lane_available = True
    side.edge_available = False
    side.dist_to_edge_far = 2.0
    side.lane_change_available_geom = True
    side.lane_change_available = True
    side.side_object_detected = False
    side.bsd_hold_counter = 0
    side.lane_line_info_mod = 0
    side.lane_line_info_edge_detect = False
    side.lane_change_available_released = False
    side.lane_available_trigger = False
    side.lane_appeared = False
    side.lane_exist_count.counter = 10

    self.carrot_man = SimpleNamespace(
      atcType="fork left",
      carrotCmdIndex=0,
      carrotCmd="",
      carrotArg="",
    )

  def update(self, left_blinker=False, lateral_active=True):
    self.helper.update(
      make_car_state(left_blinker),
      SimpleNamespace(),
      lateral_active,
      0.1,
      self.carrot_man,
      SimpleNamespace(),
    )

  def test_driver_blinker_rearms_lane_change_while_atc_desire_is_already_active(self):
    # The first ATC frame is intentionally ignored when its type changes.
    self.update()

    # ATC keeps the combined desire high while lateral control leaves the FSM off.
    self.update(lateral_active=False)
    assert self.helper.prev_desire_enabled
    assert self.helper.lane_change_state == log.LaneChangeState.off

    # A new physical blinker request must be treated as fresh driver intent even
    # though the combined ATC/driver desire has no rising edge.
    self.update(left_blinker=True)
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange

    self.update(left_blinker=True)
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeStarting

  def test_atc_desire_alone_does_not_rearm_lane_change(self):
    self.update()
    self.update(lateral_active=False)

    self.update()

    assert self.helper.lane_change_state == log.LaneChangeState.off

  @pytest.mark.parametrize('block', ['inactive', 'can', 'speed', 'trailer'])
  def test_bluetooth_lane_request_rejected_by_existing_gates(self, block):
    state = make_car_state()
    state.canValid = block != 'can'
    state.vEgo = 0 if block == 'speed' else 20
    state.trailerConnected = block == 'trailer'
    self.carrot_man.atcType = ''
    seen = []
    self.helper.bluetooth_commands = SimpleNamespace(read=lambda allowed: seen.append(allowed))
    self.helper.update(state, SimpleNamespace(), block != 'inactive', .1, self.carrot_man, SimpleNamespace())
    assert seen == [False]
    assert self.helper.lane_change_state == log.LaneChangeState.off

  def test_bluetooth_lane_request_does_not_skip_blindspot(self):
    self.carrot_man.atcType = ''
    self.helper.laneChangeBsd = 1
    self.helper.left.bsd_hold_counter = 10
    self.helper.left.lane_change_available = False
    self.helper.bluetooth_commands = SimpleNamespace(read=lambda allowed: 'laneLeft' if allowed else None)
    self.update()
    self.update()
    assert self.helper.lane_change_state != log.LaneChangeState.laneChangeStarting


class TestDesireHelperLever:
  def setup_method(self):
    fixture = TestDesireHelperDriverIntent()
    fixture.setup_method()
    self.helper = fixture.helper
    self.carrot_man = fixture.carrot_man
    self.carrot_man.atcType = ""
    self.helper.bluetooth_commands = SimpleNamespace(read=lambda allowed: None)
    self.car = make_car_state()
    self.car.leftBlinkerStalkCount = 0
    self.car.rightBlinkerStalkCount = 0
    self.car.blinkerLever = 0
    self.car.blinkerLamp = 0
    self.model = SimpleNamespace(laneLines=[SimpleNamespace(y=[1.8])] * 4)
    self.step()

  def step(self, probability=0.1, active=True):
    self.helper.update(self.car, self.model, active, probability, self.carrot_man, SimpleNamespace())

  def press(self):
    self.car.leftBlinker = True
    self.car.leftBlinkerStalkCount += 1
    self.car.blinkerLamp = 1
    self.car.blinkerLever = 1
    self.step()
    self.car.blinkerLever = 0

  def start(self):
    self.press()
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeStarting

  def cross(self):
    self.model.laneLines[1] = SimpleNamespace(y=[0.6])
    self.step()
    self.model.laneLines[1] = SimpleNamespace(y=[2.3])
    self.step()
    assert self.helper.crossing.since == 0.0

  def finish(self):
    for _ in range(50):
      self.step(probability=0.0)
      if self.helper.lane_change_state == log.LaneChangeState.preLaneChange:
        return
    raise AssertionError("Lane change did not finish")

  def test_a_press_with_no_lamp_edge_rearms_once(self):
    self.start()
    self.step(active=False)
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.off
    self.press()
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeStarting

  def test_presses_before_crossing_do_not_queue_another_lane(self):
    self.start()
    self.press()
    assert self.helper.queued_lane_change == 0
    self.finish()
    assert self.helper.next_lane_change
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange

  def test_press_after_crossing_queues_exactly_one_change(self):
    self.start()
    self.cross()
    self.press()
    self.press()
    assert self.helper.queued_lane_change == 1
    assert self.helper.blinker_hold_direction == log.LaneChangeDirection.left
    self.finish()
    assert not self.helper.next_lane_change
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeStarting
    assert self.helper.queued_lane_change == 0
    self.finish()
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange
    assert self.helper.next_lane_change

  def test_finishing_press_waits_for_the_original_guard_without_a_detected_crossing(self):
    self.start()
    for _ in range(20):
      self.step(probability=0.0)
      if self.helper.lane_change_state == log.LaneChangeState.laneChangeFinishing:
        break
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeFinishing
    self.press()
    assert self.helper.queued_lane_change == 0
    for _ in range(6):
      self.step()
    self.press()
    assert self.helper.queued_lane_change == 1

  def test_a_new_press_while_waiting_confirms_the_next_lane_only_once(self):
    self.start()
    self.finish()
    self.step()
    assert self.helper.next_lane_change
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange
    self.press()
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeStarting
    self.finish()
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange
    assert self.helper.next_lane_change

  def test_queue_never_bypasses_the_required_torque_setting(self):
    self.helper.laneChangeNeedTorque = 1
    self.car.steeringPressed = True
    self.car.steeringTorque = 1.0
    self.start()
    self.car.steeringPressed = False
    self.car.steeringTorque = 0.0
    self.cross()
    self.press()
    self.finish()
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange
    self.car.steeringPressed = True
    self.car.steeringTorque = 1.0
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeStarting

  @pytest.mark.parametrize("block", ["bsd", "solid", "object"])
  def test_queued_change_retains_existing_interlocks(self, block):
    self.start()
    self.cross()
    self.press()
    self.finish()
    side = self.helper.left
    side.lane_change_available = False
    if block == "bsd":
      self.helper.laneChangeBsd = 1
      side.bsd_hold_counter = 10
    elif block == "solid":
      self.helper.laneLineCheck = 2
      side.lane_change_available_geom = False
    else:
      side.side_object_detected = True
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange
    assert self.helper.blinker_hold_direction == log.LaneChangeDirection.left
    for _ in range(int(LEVER_REQUEST_WAIT_S / DT_MDL) + 1):
      self.step()
    assert self.helper.queued_lane_change == 0
    assert self.helper.blinker_hold_direction == log.LaneChangeDirection.none

  @pytest.mark.parametrize("cancel", ["opposite_torque", "inactive", "trailer"])
  def test_queue_and_hold_end_on_existing_cancel_paths(self, cancel):
    self.start()
    self.cross()
    self.press()
    if cancel == "opposite_torque":
      self.car.steeringPressed = True
      self.car.steeringTorque = -1.0
    elif cancel == "trailer":
      self.car.trailerConnected = True
    self.step(active=cancel != "inactive")
    assert self.helper.lane_change_state == log.LaneChangeState.off
    assert self.helper.queued_lane_change == 0
    assert self.helper.blinker_hold_direction == log.LaneChangeDirection.none

  def test_same_direction_torque_during_change_keeps_the_fsm_and_request(self):
    self.start()
    self.cross()
    self.press()
    self.car.steeringPressed = True
    self.car.steeringTorque = 1.0
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeStarting
    assert self.helper.queued_lane_change == 1

  def test_missing_stalk_fields_preserve_the_lamp_based_entry(self):
    del self.car.leftBlinkerStalkCount
    del self.car.rightBlinkerStalkCount
    self.car.leftBlinker = True
    self.step()
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.laneChangeStarting

  def test_stalk_baseline_does_not_replay_a_preexisting_press_and_wrap_is_fresh(self):
    self.helper.stalk_counts = None
    self.car.leftBlinkerStalkCount = 255
    assert self.helper._update_stalk(self.car) == 0
    assert self.helper._update_stalk(self.car) == 0
    self.car.leftBlinkerStalkCount = 0
    assert self.helper._update_stalk(self.car) == 1
    assert self.helper._update_stalk(self.car) == 0

  def test_latched_turn_gesture_is_published_without_changing_the_lane_fsm(self):
    self.helper.blinkerLatchedTurn = 10.0
    self.car.vEgo = 5.0
    self.press()
    self.car.blinkerLamp = 0
    self.step()
    self.car.blinkerLamp = 1
    self.step()
    self.car.blinkerLever = 2
    self.step()
    assert self.helper.desire == log.Desire.turnLeft
    assert self.helper.lever_turn
    assert self.helper.lane_change_state == log.LaneChangeState.off

  def test_lever_wait_holds_a_blocked_request_and_ignores_its_remaining_flashes(self):
    self.helper.laneChangeLeverWait = True
    self.helper.left.lane_change_available = False
    self.press()
    assert self.helper.blinker_hold_direction == log.LaneChangeDirection.left
    for _ in range(int(LEVER_REQUEST_WAIT_S / DT_MDL) + 1):
      self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.off
    assert self.helper.blinker_hold_direction == log.LaneChangeDirection.none
    self.helper.left.lane_change_available = True
    self.step()
    assert self.helper.lane_change_state == log.LaneChangeState.off
    self.press()
    assert self.helper.lane_change_state == log.LaneChangeState.preLaneChange
