"""Mode-4 input, delayed persistence, and unchanged downstream CAN contracts."""
from threading import Event
from types import SimpleNamespace as NS

import pytest

from openpilot.cereal import car
from openpilot.selfdrive.car.cruise import ButtonType, VCruiseCarrot
from openpilot.selfdrive.car.tests.test_carrot_cruise_buttons import make_remote_helper
from openpilot.selfdrive.carrot.paddle_gap import PaddleGapWriter, effective_paddle_mode


class MemoryParams:
  def __init__(self, current=3):
    self.values = {"LongitudinalPersonality": current, "LongitudinalPersonalityMax": 4, "CruiseGapLevels": 4}
    self.writes = []
    self.started = Event()
    self.release = Event()
    self.release.set()
    self.failure = None

  def get_int(self, key):
    return self.values.get(key, 0)

  def get(self, key, return_default=False):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.values.get(key, 0))

  def get_float(self, key):
    return float(self.values.get(key, 0))

  def put_int(self, key, value):
    self.started.set()
    assert self.release.wait(3), "test storage gate not released"
    if self.failure == "exception":
      raise OSError("test disk failure")
    if self.failure == "negative":
      return -1
    if self.failure == "silent":
      return None
    self.values[key] = value
    self.writes.append(value)


@pytest.fixture
def mode4():
  helper, CS, CC = make_remote_helper(None, enabled=True)
  helper.CP = NS(openpilotLongitudinalControl=True)
  helper._paddle_mode = 4
  params = MemoryParams()
  helper.params = params
  writer = helper._paddle_gap_writer = PaddleGapWriter(lambda: params)
  calls = []
  helper._cruise_control = lambda *args: calls.append(args)
  yield helper, CS, CC, params, calls
  params.release.set()
  writer.close()


def drive_events(fixture, *events):
  helper, CS, CC, _, _ = fixture
  CS.buttonEvents = [car.CarState.ButtonEvent.new_message(type=t, pressed=p) for t, p in events]
  assert helper._update_cruise_buttons(CS, CC, 80) == 80


def press(fixture, side):
  drive_events(fixture, (side, True))
  drive_events(fixture, (side, False))


@pytest.mark.parametrize("current", range(4))
@pytest.mark.parametrize("side,delta", [(ButtonType.paddleLeft, 1), (ButtonType.paddleRight, -1)])
@pytest.mark.parametrize("enabled", [False, True])
def test_each_step_and_boundaries_without_cruise_actions(mode4, current, side, delta, enabled):
  helper, CS, CC, params, calls = mode4
  params.values["LongitudinalPersonality"] = current
  CC.enabled = enabled
  helper._cruise_cancel_state = not enabled
  press(mode4, side)
  helper._paddle_gap_writer._queue.join()
  assert params.get_int("LongitudinalPersonality") == min(3, max(0, current + delta))
  assert not calls and helper._activate_cruise == 0
  assert not helper._paddle_decel_active and not helper.carrot_cruise_active
  assert helper._cruise_cancel_state == (not enabled)


def test_hold_no_repeat_and_release_no_step(mode4):
  helper, _, _, params, _ = mode4
  drive_events(mode4, (ButtonType.paddleRight, True))
  for _ in range(150):
    drive_events(mode4)
  drive_events(mode4, (ButtonType.paddleRight, False))
  helper._paddle_gap_writer._queue.join()
  assert params.writes == [2]


@pytest.mark.parametrize("initial,deltas,expected", [
  (3, [-1, -1, -1, -1], [2, 1, 0]),
  (0, [1, 1, 1, 1], [1, 2, 3]),
  (1, [-1, 1, -1], [0, 1, 0]),
  (3, [-1, 1, -1, -1], [2, 3, 2, 1]),
])
def test_delayed_storage_rapid_input_and_aba(mode4, initial, deltas, expected):
  helper, _, _, params, _ = mode4
  params.values["LongitudinalPersonality"] = initial
  params.release.clear()
  for i, delta in enumerate(deltas):
    press(mode4, ButtonType.paddleLeft if delta > 0 else ButtonType.paddleRight)
    if i == 0:
      assert params.started.wait(2)
  assert params.writes == []  # control-loop requests returned while storage blocked
  params.release.set()
  helper._paddle_gap_writer._queue.join()
  assert params.writes == expected


def test_gap_button_and_external_setting_share_latest_value(mode4):
  helper, CS, _, params, _ = mode4
  params.release.clear()
  press(mode4, ButtonType.paddleRight)  # 4 -> 3
  assert params.started.wait(2)
  press(mode4, ButtonType.gapAdjustCruise)  # 3 -> 2, in same queue
  press(mode4, ButtonType.paddleLeft)  # 2 -> 3
  params.release.set()
  helper._paddle_gap_writer._queue.join()
  assert params.writes == [2, 1, 2]
  params.values["LongitudinalPersonality"] = 0  # web change after completed request
  press(mode4, ButtonType.paddleLeft)
  helper._paddle_gap_writer._queue.join()
  assert params.get_int("LongitudinalPersonality") == 1
  CS.pcmCruiseGap = 4  # existing physical gap-button PCM absolute setting
  press(mode4, ButtonType.gapAdjustCruise)
  press(mode4, ButtonType.paddleRight)
  helper._paddle_gap_writer._queue.join()
  assert params.writes[-2:] == [3, 2]


@pytest.mark.parametrize("cycle_levels", [2, 3, 4])
def test_paddles_use_four_levels_independent_of_button_cycle(mode4, cycle_levels):
  helper, _, _, params, _ = mode4
  params.values.update(LongitudinalPersonality=2, CruiseGapLevels=cycle_levels)
  press(mode4, ButtonType.paddleLeft)
  helper._paddle_gap_writer._queue.join()
  assert params.get_int("LongitudinalPersonality") == 3


@pytest.mark.parametrize("reverse", [False, True])
def test_cancel_press_precedence(mode4, reverse):
  helper, _, _, params, calls = mode4
  events = [(ButtonType.cancel, True), (ButtonType.paddleRight, True)]
  drive_events(mode4, *(events[::-1] if reverse else events))
  helper._paddle_gap_writer._queue.join()
  assert helper._cruise_cancel_state and not calls and not params.writes


@pytest.mark.parametrize("invalid", ["can", "park", "stock_long", "three_levels"])
def test_unsupported_or_invalid_input_is_inert(mode4, invalid):
  helper, CS, _, params, calls = mode4
  if invalid == "can":
    CS.canValid = False
  elif invalid == "park":
    CS.gearShifter = "park"
  elif invalid == "stock_long":
    helper.CP.openpilotLongitudinalControl = False
  else:
    params.values["LongitudinalPersonalityMax"] = 3
  press(mode4, ButtonType.paddleRight)
  helper._paddle_gap_writer._queue.join()
  assert not params.writes and not calls


@pytest.mark.parametrize("active", range(5))
@pytest.mark.parametrize("requested", range(5))
def test_mode4_requires_restart_but_legacy_modes_remain_live(active, requested):
  expected = active if 4 in (active, requested) else requested
  assert effective_paddle_mode(active, requested) == expected


@pytest.mark.parametrize("failure", ["exception", "negative", "silent"])
def test_storage_failure_stops_relative_requests(mode4, failure):
  helper, _, _, params, _ = mode4
  params.failure = failure
  press(mode4, ButtonType.paddleRight)
  helper._paddle_gap_writer._thread.join(2)
  assert helper._paddle_gap_writer.failed
  assert not helper._paddle_gap_writer.request("step", -1)
  assert not params.writes


def test_pcm_gap_still_clamped_to_vehicle_max(mode4):
  helper, CS, _, params, _ = mode4
  params.values.update(LongitudinalPersonality=0, LongitudinalPersonalityMax=3)
  CS.pcmCruiseGap = 4
  press(mode4, ButtonType.gapAdjustCruise)
  helper._paddle_gap_writer._queue.join()
  assert params.get_int("LongitudinalPersonality") == 2


@pytest.mark.parametrize("bad", [None, "invalid", -1, 4, True, 1.5])
def test_bad_stored_value_fails_closed(mode4, bad):
  helper, _, _, params, _ = mode4
  params.values["LongitudinalPersonality"] = bad
  press(mode4, ButtonType.paddleRight)
  helper._paddle_gap_writer._thread.join(2)
  assert helper._paddle_gap_writer.failed and not params.writes


@pytest.mark.parametrize("initial,requested,expected", [(2, 4, 2), (4, 2, 4), (2, 3, 3)])
def test_actual_constructor_and_param_refresh_pin_mode(monkeypatch, initial, requested, expected):
  from openpilot.selfdrive.car import cruise
  params = MemoryParams()
  params.values["PaddleMode"] = initial
  monkeypatch.setattr(cruise, "Params", lambda *args: params)
  monkeypatch.setattr(cruise, "CommandReader", lambda *args: NS(read=lambda **kwargs: None))
  helper = VCruiseCarrot(NS(openpilotLongitudinalControl=True))
  helper._paddle_decel_active = True
  try:
    params.values["PaddleMode"] = requested
    helper.update_params(True)
    assert helper._paddle_mode == expected
    assert helper._paddle_decel_active  # shared deceleration state never cleared
  finally:
    if helper._paddle_gap_writer is not None:
      helper._paddle_gap_writer.close()


@pytest.mark.parametrize("mode", range(4))
def test_legacy_physical_paddle_actions_preserved(mode4, mode):
  helper, _, _, params, calls = mode4
  helper._paddle_mode = mode
  press(mode4, ButtonType.paddleRight)
  assert [c[:2] for c in calls] == ([(-2, -1)] if mode in (1, 2) else [])
  assert helper._paddle_decel_active == (mode == 2)
  assert helper.carrot_cruise_active == (mode == 3)
  assert not params.writes


def test_gap_change_preserves_existing_soft_hold_state(mode4):
  helper, _, _, params, calls = mode4
  helper._soft_hold_active = 2
  helper._cruise_cancel_state = True
  press(mode4, ButtonType.paddleRight)
  helper._paddle_gap_writer._queue.join()
  assert params.get_int("LongitudinalPersonality") == 2
  assert helper._soft_hold_active == 2 and helper._cruise_cancel_state
  assert not calls and helper._activate_cruise == 0


@pytest.mark.parametrize("message", ["CRUISE_BUTTONS", "GEAR"])
@pytest.mark.parametrize("side", ["LEFT_PADDLE", "RIGHT_PADDLE"])
def test_raw_can_to_gap_planner_and_scc_output(mode4, message, side):
  """Host chain with real DBC packing/parsing and source-extracted CarState tail.

  Full carstate update and process IPC require the Linux/device integration run.
  Extracting the unchanged block avoids duplicating its button conversion logic.
  """
  import ast
  from pathlib import Path
  from opendbc.can import CANPacker, CANParser
  from opendbc.car import create_button_events, structs
  from opendbc.car.hyundai.carstate import BUTTONS_DICT
  from opendbc.car.hyundai.hyundaicanfd import create_acc_control, create_acc_control_scc2
  from openpilot.cereal import log

  root = Path(__file__).resolve().parents[4]
  planner_tree = ast.parse((root / "openpilot/selfdrive/carrot/carrot_functions.py").read_text(encoding="utf-8"))
  planner_cls = next(n for n in planner_tree.body if isinstance(n, ast.ClassDef) and n.name == "CarrotPlanner")
  base_tf = next(n for n in planner_cls.body if isinstance(n, ast.FunctionDef) and n.name == "_get_base_t_follow")
  planner_ns = {"log": log}
  exec(compile(ast.Module(body=[base_tf], type_ignores=[]), "<CarrotPlanner gap selection>", "exec"), planner_ns)
  tree = ast.parse((root / "opendbc_repo/opendbc/car/hyundai/carstate.py").read_text(encoding="utf-8"))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CarState")
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "update_canfd")
  start = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Assign) and
               any(isinstance(t, ast.Name) and t.id == "paddle_button" for t in n.targets))
  decoder = compile(ast.Module(body=method.body[start:-1], type_ignores=[]), "<CarState paddle tail>", "exec")
  helper, CS, _, params, calls = mode4
  params.values["LongitudinalPersonality"] = 1
  packer = CANPacker("hyundai_canfd_generated")
  parser = CANParser("hyundai_canfd_generated", [(message, 50)], 0)
  state = NS(paddle_button_prev=0, cruise_btns_msg_canfd=message, gear_msg_canfd=message,
             cruise_buttons=[0], main_buttons=[0])
  target = 2 if side == "LEFT_PADDLE" else 0
  for frame, pressed in enumerate([0, 1, 1, 0]):
    frame_msg = packer.make_can_msg(message, 0, {side: pressed})
    parser.update([1_000_000_000 + frame * 20_000_000, [frame_msg]])
    decoded = NS(buttonEvents=[])
    exec(decoder, {"self": state, "cp": parser, "ret": decoded, "prev_cruise_buttons": 0,
                   "prev_main_buttons": 0, "BUTTONS_DICT": BUTTONS_DICT,
                   "ButtonType": structs.CarState.ButtonEvent.Type, "create_button_events": create_button_events})
    CS.buttonEvents = [{"type": b.type, "pressed": b.pressed} for b in decoded.buttonEvents]
    helper._update_cruise_buttons(CS, mode4[2], 80)
    helper._paddle_gap_writer._queue.join()
    personality = params.get_int("LongitudinalPersonality")
    assert personality == (1 if frame == 0 else target)
    planner = NS()
    planner.tFollowGap1, planner.tFollowGap2, planner.tFollowGap3, planner.tFollowGap4 = 1.1, 1.3, 1.5, 1.7
    assert planner_ns["_get_base_t_follow"](planner, personality, 20.) == [1.1, 1.3, 1.5, 1.7][personality]
    hud = NS(leadDistanceBars=personality + 1, leadVisible=False)
    can_state = NS(scc_control={"InfoDisplay": 0}, softHoldActive=0, paddle_button_prev=state.paddle_button_prev,
                   out=NS(vEgo=20., aEgo=0., brakeHoldActive=False, parkingBrake=False, cruiseState=NS(available=True)))
    for camera in (True, False):
      if camera:
        msg, _ = create_acc_control_scc2(packer, NS(ECAN=0), True, .5, .5, False, False, 80., hud,
                                        NS(carrot_cruise=0, jerk_u=2., jerk_l=1.), can_state)
      else:
        msg, _ = create_acc_control(packer, NS(ECAN=0), True, .5, .5, False, False, 80., hud, 2., 1., can_state)
      output = CANParser("hyundai_canfd_generated", [("SCC_CONTROL", 50)], 0)
      output.update([1_000_000_000, [msg]])
      values = output.vl["SCC_CONTROL"]
      assert values["DISTANCE_SETTING"] == personality + 1
      assert values["ACCMode"] == (0 if camera and pressed else 1)
      assert values["aReqRaw"] == pytest.approx(0. if camera and pressed else .5)
  assert not calls
