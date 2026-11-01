from types import SimpleNamespace

import pytest

from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.desire_lib.constants import BLINKER_LEFT, BLINKER_NONE, BLINKER_RIGHT, LaneChangeState
from openpilot.selfdrive.controls.lib.desire_lib.lever import (
  LAMP_DARK_RESET_S, LEVER_LATCHED, LEVER_ONE_TOUCH, LEVER_RELEASED, LEVER_REQUEST_WAIT_S,
  LaneCrossing, LeverForcedTurn, LeverRequestWait, lever_maneuver,
)


@pytest.mark.parametrize("side", [BLINKER_LEFT, BLINKER_RIGHT])
def test_forced_turn_requires_two_lightings_before_latch(side):
  gesture = LeverForcedTurn()
  assert not gesture.update(LEVER_ONE_TOUCH, side)
  assert not gesture.update(LEVER_LATCHED, side)
  assert lever_maneuver(LEVER_LATCHED, 5.0, 10.0, gesture.active) == ("lane_change", False)

  gesture.update(LEVER_RELEASED, BLINKER_NONE)
  gesture.update(LEVER_ONE_TOUCH, side)
  gesture.update(LEVER_RELEASED, BLINKER_NONE)
  gesture.update(LEVER_RELEASED, side)
  assert gesture.update(LEVER_LATCHED, side)
  assert lever_maneuver(LEVER_LATCHED, 5.0, 10.0, gesture.active) == ("turn", False)
  assert lever_maneuver(LEVER_LATCHED, 11.0, 10.0, gesture.active) == ("lane_change", False)
  assert not gesture.update(LEVER_RELEASED, side)


def test_forced_turn_does_not_survive_a_stopped_signal():
  gesture = LeverForcedTurn()
  gesture.update(LEVER_ONE_TOUCH, BLINKER_LEFT)
  for _ in range(int(LAMP_DARK_RESET_S / DT_MDL) + 1):
    gesture.update(LEVER_RELEASED, BLINKER_NONE)
  gesture.update(LEVER_RELEASED, BLINKER_LEFT)
  assert not gesture.update(LEVER_LATCHED, BLINKER_LEFT)


@pytest.mark.parametrize("lever,expected", [(LEVER_ONE_TOUCH, ("lane_change", True)),
                                          (LEVER_LATCHED, ("lane_change", False))])
def test_lever_selection_waits_out_the_one_touch_detent(lever, expected):
  assert lever_maneuver(lever, 5.0, 10.0) == expected
  assert lever_maneuver(lever, 5.0, 0.0) == (None, False)


def test_one_touch_wait_holds_only_the_pending_request_and_expires_after_delay():
  wait = LeverRequestWait()
  delay = 0.5
  assert not wait.update(True, BLINKER_LEFT, LEVER_ONE_TOUCH, BLINKER_LEFT, LaneChangeState.off, delay)
  assert wait.update_hold(LaneChangeState.preLaneChange, BLINKER_LEFT) == BLINKER_LEFT
  for _ in range(int((LEVER_REQUEST_WAIT_S + delay) / DT_MDL) - 1):
    assert not wait.update(True, BLINKER_NONE, LEVER_RELEASED, BLINKER_LEFT, LaneChangeState.preLaneChange, delay)
  for _ in range(2):
    expired = wait.update(True, BLINKER_NONE, LEVER_RELEASED, BLINKER_LEFT, LaneChangeState.preLaneChange, delay)
  assert expired
  assert wait.update_hold(LaneChangeState.preLaneChange, BLINKER_LEFT) == BLINKER_NONE
  assert wait.update(True, BLINKER_NONE, LEVER_RELEASED, BLINKER_LEFT, LaneChangeState.off, delay)
  assert not wait.update(True, BLINKER_NONE, LEVER_RELEASED, BLINKER_NONE, LaneChangeState.off, delay)


@pytest.mark.parametrize("release", ["latched", "starting", "disabled"])
def test_one_touch_hold_ends_when_request_is_handed_off(release):
  wait = LeverRequestWait()
  wait.update(True, BLINKER_RIGHT, LEVER_ONE_TOUCH, BLINKER_RIGHT, LaneChangeState.off)
  enabled = release != "disabled"
  lever = LEVER_LATCHED if release == "latched" else LEVER_RELEASED
  state = LaneChangeState.laneChangeStarting if release == "starting" else LaneChangeState.preLaneChange
  for _ in range(int((LEVER_REQUEST_WAIT_S + 1.0) / DT_MDL)):
    assert not wait.update(enabled, BLINKER_NONE, lever, BLINKER_RIGHT, state)
  assert wait.update_hold(LaneChangeState.preLaneChange, BLINKER_RIGHT) == BLINKER_NONE


@pytest.mark.parametrize("left", [True, False])
def test_crossing_uses_closest_approach_across_a_spread_relabel(left):
  crossing = LaneCrossing()
  for distance in (1.8, 0.7, 1.4, 1.9):
    model = SimpleNamespace(laneLines=[SimpleNamespace(y=[distance])] * 4)
    assert crossing.update(model, left) < 0
  model.laneLines[1 if left else 2] = SimpleNamespace(y=[2.3])
  assert crossing.update(model, left) == 0.0
  assert crossing.update(model, left) == DT_MDL


def test_missing_lane_lines_never_claim_a_crossing():
  crossing = LaneCrossing()
  assert crossing.update(SimpleNamespace(), True) < 0
  assert crossing.update(SimpleNamespace(laneLines=[SimpleNamespace(y=[])] * 4), True) < 0
