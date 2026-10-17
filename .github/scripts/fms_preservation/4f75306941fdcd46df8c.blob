"""Actual CAN parser, production card bridge/VCruise and final SCC output.

Global CAN validity is supplied because the focused trace omits unrelated
periodic frames. This same-process host test does not exercise IPC or an ECU.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.hyundai.tests.test_paddle_mode4 import chain, controls_cancel_producer, decode
from opendbc.car.hyundai.values import Buttons
from openpilot.selfdrive.car import cruise
from openpilot.selfdrive.car.paddle_context import publish_paddle_context
from openpilot.selfdrive.car.tests.test_paddle_gap import MemoryParams
from openpilot.selfdrive.car.tests.test_toyota_pcm_mode0 import MemoryReader
from openpilot.cereal import log


@pytest.fixture
def nav_bridge(chain, controls_cancel_producer, monkeypatch):
  from openpilot.selfdrive.car import car_specific
  from openpilot.selfdrive.selfdrived.state import StateMachine
  ci, _, cc = chain
  # The focused CP constructor omits this production-populated scale.
  ci.CP.wheelSpeedFactor = 1.
  params = MemoryParams(2)
  params.values.update(PaddleMode=4, VehicleNaviCanControl=1, VehicleNaviDecelCancel=1,
                       VehicleNaviDecelCancelBox=1, AutoNaviSpeedCtrlMode=2,
                       AutoNaviSpeedSafetyFactor=100, CruiseButtonLongDelay=50,
                       CruiseSpeedUnit=10, CruiseSpeedUnitBasic=1, AutoCruiseControl=1,
                       AutoGasTokSpeed=30, AutoGasCancelSpeed=30, SoftHoldOnCancel=1)
  params.put_bool = params.put_bool_nonblocking = lambda key, value: params.values.__setitem__(key, value)
  params.put_int_nonblocking = lambda key, value: params.values.__setitem__(key, value)
  monkeypatch.setattr(cruise, 'Params', lambda *args: params)
  monkeypatch.setattr(cruise, 'CommandReader', MemoryReader)
  monkeypatch.setattr(car_specific, 'Params', lambda: params)
  helper = cruise.VCruiseCarrot(ci.CP)
  helper.v_cruise_kph = helper.v_cruise_cluster_kph = 80.
  helper.cruise_state_available_last = True
  helper._gas_pressed_count = helper._brake_pressed_count = -2
  ci.CS.op_params = params
  ci.CS.vehicleNaviCanControl = 1
  ci.CS._read_speedcam_params()
  ci.can_parsers = ci.CS.get_can_parsers(ci.CP)
  ci.v_ego_cluster_seen = False
  parser = ci.can_parsers[Bus.pt]
  for name in ('TCS', 'GEAR_SHIFTER', 'WHEEL_SPEEDS', 'HDA_INFO_4A3', ci.CS.accelerator_msg_canfd):
    parser._add_message(name, 100)
  ci.CS.hda_info_4a3 = parser.vl['HDA_INFO_4A3']
  ci.CS.speedcam_policy.add_preview(0xD2, 366, 0.)
  packer = CANPacker('hyundai_canfd_generated')
  root = Path(__file__).resolve().parents[5]
  tree = ast.parse((root/'openpilot/selfdrive/car/card.py').read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Car')
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'state_update')
  def code_for(targets):
    nodes = [n for n in method.body if isinstance(n, ast.Assign) and any(ast.unparse(t) in targets for t in n.targets)
             or isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
             and ast.unparse(n.value.func) in targets]
    assert len(nodes) == len(targets)
    return compile(ast.Module(body=nodes, type_ignores=[]), '<production card nav bridge>', 'exec')
  before = code_for({'self.CI.CS.speedcam_engaged', 'self.CI.CS.speedcam_v_cruise', 'CS'})
  after = code_for({'self.v_cruise_helper.speedcam_gas_tok_consumed', 'paddle_input',
                    'self.v_cruise_helper.update_v_cruise'})
  publish = code_for({'CS.softHoldActive','CS.activateCruise','CS.latEnabled','CS.carrotCruise',
                      'self.CI.CS.softHoldActive'})
  controls_tree=ast.parse((root/'openpilot/selfdrive/controls/controlsd.py').read_text(encoding='utf-8'))
  targets={'CC.enabled','CC.longActive','CC.cruiseControl.resume'}
  assignments=[n for n in ast.walk(controls_tree) if isinstance(n,ast.Assign) and any(ast.unparse(t) in targets for t in n.targets)]
  # Ignore the chained final invalid-input gate; the focused fixture supplies
  # valid input and checks this existing gate in test_paddle_mode4 separately.
  assignments=[n for n in assignments if len(n.targets)==1]
  assert len(assignments)==3
  fresh_control=compile(ast.Module(body=assignments,type_ignores=[]),'<production fresh control flags>','exec')
  lateral_node=next(n for n in controls_tree.body if isinstance(n,ast.FunctionDef) and n.name=='lateral_control_allowed')
  lateral_assignment=next(n for n in ast.walk(controls_tree) if isinstance(n,ast.Assign)
    and any(ast.unparse(t)=='CC.latActive' for t in n.targets)
    and isinstance(n.value,ast.Call) and ast.unparse(n.value.func)=='lateral_control_allowed')
  lateral_control=compile(ast.Module(body=[lateral_node,lateral_assignment],type_ignores=[]),
    '<production lateral permission and CC flag>','exec')
  state_machine=StateMachine()
  car_events=car_specific.CarSpecificEvents(ci.CP)
  class SM(dict):
    seen = {'onroadEvents': True, 'carrotMan': False}
    alive = dict.fromkeys(['longitudinalPlan', 'radarState', 'drivingModelData'], False)
    def all_checks(self, keys): return True
  card = NS(CI=ci, v_cruise_helper=helper, sm=SM(carControl=cc, onroadEvents=[]), is_metric=True)
  class Driver:
    index = 0
    previous = structs.CarState()
    def tick(self, buttons=(0,), *, gas=False, paddle=0, main=0, empty=False, extra_buttons=()):
      if self.index==0:
        state_machine.state=log.SelfdriveState.OpenpilotState.enabled if cc.enabled else log.SelfdriveState.OpenpilotState.disabled
      now = 1_000_000_000+self.index*10_000_000
      self.index += 1
      packets = [] if empty else [packer.make_can_msg('CRUISE_BUTTONS', 0,
        {'CRUISE_BUTTONS': button, 'RIGHT_PADDLE': paddle, 'ADAPTIVE_CRUISE_MAIN_BTN': main}) for button in buttons]
      packets += [packer.make_can_msg('TCS', 0, {'ACC_REQ': 1}),
                  packer.make_can_msg('GEAR_SHIFTER', 0, {'GEAR': 4}),
                  packer.make_can_msg('WHEEL_SPEEDS', 0, {f'WHEEL_SPEED_{i}':72. for i in range(1,5)}),
                  packer.make_can_msg('HDA_INFO_4A3', 0, {'SPEED_LIMIT':60, 'MapSource':2}),
                  packer.make_can_msg(ci.CS.accelerator_msg_canfd, 0, {'ACCELERATOR_PEDAL_PRESSED':int(gas)})]
      ns = {'self':card, 'can_list':[(now,packets)]}
      exec(before, ns)
      decoded = ns['CS']
      decoded.canValid = decoded.cruiseState.available = True
      if extra_buttons:
        # Additional fleet SET/RES events are explicit inputs. MQ4's raw
        # CANCEL is still decoded from the packed receive batch above.
        decoded.buttonEvents=[{'type':str(b.type),'pressed':b.pressed} for b in decoded.buttonEvents]+list(extra_buttons)
      controls_cancel_producer(cc, decoded, ci.CP)
      exec(after, ns)
      exec(publish, ns)
      helper._paddle_gap_writer._queue.join()
      events=car_events.create_common_events(decoded,self.previous,pcm_enable=False)
      state_enabled,_=state_machine.update(events)
      self.previous=decoded.as_reader()
      exec(fresh_control,dict(CC=cc,CS=decoded,speeds=[20.],self=NS(CP=ci.CP,sm={
        'selfdriveState':NS(enabled=state_enabled),'onroadEvents':events.to_msg()})))
      exec(lateral_control,dict(CC=cc,CS=decoded,lateral_enabled=params.get_bool('AlwaysLateral'),
        below_min_speed=False,steer_at_standstill=False,self=NS(sm={'selfdriveState':NS(active=state_enabled)})))
      controls_cancel_producer(cc,decoded,ci.CP)
      publish_paddle_context(ci.CP,ci,helper,card.sm,now)
      _, messages = ci.apply(cc.as_reader(),now)
      msg = next((m for m in messages if m[0] == 0x1a0),None)
      packet = None if msg is None else decode(msg)
      button_parser=CANParser('hyundai_canfd_generated',[('CRUISE_BUTTONS',50)],ci.CC.CAN.ECAN)
      emitted_res=[]
      for message in messages:
        if message[0]==packer.dbc.name_to_msg['CRUISE_BUTTONS'].address:
          button_parser.update([(now,[message])])
          if button_parser.vl['CRUISE_BUTTONS']['CRUISE_BUTTONS']==Buttons.RES_ACCEL:
            emitted_res.append(message[1].hex())
      self.last_record = dict(now=now, frames=[dict(address=m[0], data=m[1].hex(), bus=m[2]) for m in packets],
        buttonEvents=[dict(type=str(b.type), pressed=b.pressed) for b in decoded.buttonEvents],
        raw_cancel_seen=ci.CS.paddle_input.cancel_seen, cruise_cancel_latch=helper._cruise_cancel_state,
        activateCruise=helper._activate_cruise, resume=cc.cruiseControl.resume,
        buttonEnable=decoded.buttonEnable, enabled=cc.enabled,
        latEnabled=decoded.latEnabled,latActive=cc.latActive,
        events=[str(e.name) for e in events.to_msg()], state=str(state_machine.state),
        state_disabled=state_machine.state==log.SelfdriveState.OpenpilotState.disabled,
        emitted_res_can=emitted_res,
        gas_consumed=ci.CS.speedcam_gas_tok_consumed, input_consumed=ci.CS.speedcam_input_consumed,
        packet=None if packet is None else {k:packet[k] for k in ('ACCMode','StopReq','aReqValue')})
      return decoded, packet
  yield Driver(), ci, helper, cc, params
  params.release.set()
  helper._paddle_gap_writer.close()


@pytest.mark.parametrize('master', [False,True])
@pytest.mark.parametrize('enabled', [False,True])
@pytest.mark.parametrize('kind', ['plus','gas'])
def test_nav_consumption_keeps_fms_input_and_can_contract(nav_bridge, master, enabled, kind):
  driver,ci,helper,cc,params = nav_bridge
  params.values['VehicleNaviDecelCancel']=master
  ci.CS._read_speedcam_params()
  cc.enabled=cc.longActive=enabled
  driver.tick()
  if kind=='plus':
    decoded,_=driver.tick((Buttons.RES_ACCEL,))
    consumed=ci.CS.speedcam_input_consumed
    assert not(master and enabled) or consumed, (decoded.vEgo, decoded.speedLimit, ci.CS.speedcam_engaged,
      ci.CS.vehicleNaviCanControl, ci.CS.speedcamSkipBox, ci.CS.speedcam_policy.__dict__, ci.CS.hda_info_4a3)
    assert any(str(b.type)=='accelCruise' and b.pressed for b in decoded.buttonEvents) == (not(master and enabled))
    driver.tick()
  else:
    driver.tick(gas=True)
    decoded,_=driver.tick()
    consumed=ci.CS.speedcam_gas_tok_consumed
    assert helper._gas_tok == (not(master and enabled))
  assert consumed == (master and enabled)
  assert params.writes == []
  if master and enabled:
    assert helper.v_cruise_kph == 80.


@pytest.mark.parametrize('kind',['plus','gas'])
def test_nav_consumed_input_cannot_hide_same_batch_cancel(nav_bridge,kind):
  driver,ci,helper,cc,params=nav_bridge
  driver.tick()
  if kind=='plus': driver.tick((Buttons.RES_ACCEL,))
  else: driver.tick(gas=True)
  decoded,packet=driver.tick((Buttons.CANCEL,0))
  assert ci.CS.paddle_input.cancel_seen
  assert helper._cruise_cancel_state and helper._activate_cruise <= 0
  assert params.writes == []
  assert not cc.cruiseControl.resume
  assert not cc.enabled and not cc.longActive
  assert 'buttonCancel' in driver.last_record['events']
  decoded,_=driver.tick(empty=True)
  assert not ci.CS.paddle_input.cancel_seen  # vl_all must not replay the prior receive batch
  assert helper._cruise_cancel_state and helper._activate_cruise <= 0
  assert not any(str(b.type)=='accelCruise' for b in decoded.buttonEvents)
  _,packet=driver.tick(empty=True)
  assert packet['ACCMode']==0 and not cc.cruiseControl.resume


def test_nav_input_filters_keep_main_and_paddle_events(nav_bridge):
  driver,ci,helper,cc,params=nav_bridge
  driver.tick()
  decoded,_=driver.tick((Buttons.RES_ACCEL,),paddle=1,main=1)
  assert ci.CS.speedcam_input_consumed
  assert {str(b.type) for b in decoded.buttonEvents} == {'paddleRight','mainCruise'}
  assert helper._activate_cruise <= 0


def test_dynamic_registration_does_not_replay_cached_cancel(nav_bridge):
  driver,ci,helper,cc,params=nav_bridge
  driver.tick((Buttons.CANCEL,0))
  assert ci.CS.paddle_input.cancel_seen
  ci.can_parsers[Bus.pt]._add_message('BLINKERS',100)
  decoded,_=driver.tick(empty=True)
  assert not ci.CS.paddle_input.cancel_seen
  assert helper._cruise_cancel_state and helper._activate_cruise <= 0
  assert params.writes == []


@pytest.mark.parametrize('competitor',['gas','resumeCruise','setCruise','carrotGO','bluetoothGO'])
def test_raw_cancel_wins_through_actual_state_machine_and_fresh_can(nav_bridge,competitor):
  driver,ci,helper,cc,params=nav_bridge
  driver.tick()
  helper._soft_hold_active=2
  extra=()
  if competitor in ('resumeCruise','setCruise'):
    extra=({'type':competitor,'pressed':False},)
  if competitor=='carrotGO':
    helper._update_carrot_man=lambda sm:None
    helper.carrot_cmd_index+=1
    helper.carrot_cmd,helper.carrot_arg='CRUISE','GO'
  if competitor=='bluetoothGO': helper.bluetooth_commands.action='accelCruise'
  decoded,_=driver.tick((Buttons.CANCEL,0),gas=competitor=='gas',extra_buttons=extra)
  assert decoded.activateCruise==-1 and helper._cruise_cancel_state
  assert driver.last_record['state_disabled'] and 'buttonCancel' in driver.last_record['events']
  assert not cc.enabled and not cc.longActive and not cc.cruiseControl.resume
  assert helper._soft_hold_active==(0 if competitor=='gas' else 2)
  for _ in range(2):
    _,packet=driver.tick(empty=True)
    assert helper._activate_cruise<=0 and helper._cruise_cancel_state
    assert not cc.enabled and not cc.cruiseControl.resume and not driver.last_record['emitted_res_can']
    assert not any(e=='buttonEnable' for e in driver.last_record['events'])
    if packet is not None: assert packet['ACCMode'] in (0,1)  # independent hold may retain its independent CAN request


@pytest.mark.parametrize('hold_ticks',[2,55])
@pytest.mark.parametrize('button',[Buttons.RES_ACCEL,Buttons.SET_DECEL])
def test_raw_cancel_discards_only_old_speed_gesture_and_fresh_resume_rearms(nav_bridge,hold_ticks,button):
  driver,ci,helper,cc,params=nav_bridge
  params.values['VehicleNaviDecelCancel']=False
  ci.CS._read_speedcam_params()
  driver.tick()
  for _ in range(hold_ticks): driver.tick((button,))
  driver.tick((Buttons.CANCEL,0))
  assert helper.button_cnt==0 and helper.button_prev==cruise.ButtonType.unknown
  for _ in range(3):
    driver.tick(empty=True)
    assert helper._cruise_cancel_state and helper._activate_cruise<=0 and not cc.enabled
  driver.tick((Buttons.RES_ACCEL,))
  driver.tick()
  assert not helper._cruise_cancel_state and cc.enabled
  driver.tick(paddle=0)
  driver.tick(paddle=1)
  assert params.writes  # only this fresh, normal ON gesture can enqueue gap


@pytest.mark.parametrize('auto',[0,1])
def test_physical_cancel_overrides_auto_preferences_and_shift_timer(nav_bridge,auto):
  driver,ci,helper,cc,params=nav_bridge
  params.values['AutoCruiseControl']=auto
  driver.tick()
  helper.autoCruiseControl_cancel_timer=1000
  decoded,_=driver.tick((Buttons.CANCEL,))
  assert decoded.activateCruise==-1 and helper._cruise_cancel_state
  assert not cc.enabled and not cc.longActive


@pytest.mark.parametrize('mode',[0,1])
@pytest.mark.parametrize('always_lateral',[False,True])
def test_eventless_raw_cancel_preserves_mode_specific_lateral_policy(nav_bridge,mode,always_lateral):
  driver,ci,helper,cc,params=nav_bridge
  params.values.update(CancelButtonMode=mode,AlwaysLateral=always_lateral)
  helper._lat_enabled=True
  driver.tick()
  assert cc.latActive
  decoded,_=driver.tick((Buttons.CANCEL,0))
  assert not decoded.buttonEvents and ci.CS.paddle_input.cancel_seen
  assert not cc.enabled and not cc.longActive
  assert decoded.latEnabled==(mode==0)
  assert cc.latActive==(mode==0 and always_lateral)
  for _ in range(2):
    driver.tick(empty=True)
    assert cc.latActive==(mode==0 and always_lateral)
    assert helper._cruise_cancel_state and helper._activate_cruise<=0
    assert not cc.cruiseControl.resume and not driver.last_record['emitted_res_can']


@pytest.mark.parametrize('fresh_action',['accelCruise','decelCruiseLong'])
@pytest.mark.parametrize('publication',['present','delayed'])
def test_raw_cancel_discards_real_bluetooth_reader_backlog_and_accepts_new_input(nav_bridge,tmp_path,monkeypatch,fresh_action,publication):
  from openpilot.selfdrive.carrot.bluetooth import model
  driver,ci,helper,cc,params=nav_bridge
  clock={'now':9.0}
  monkeypatch.setattr(model.time,'monotonic',lambda:clock['now'])
  reader=model.CommandReader('cruise',root=tmp_path)
  helper.bluetooth_commands=reader
  writer=model.CommandWriter(root=tmp_path)
  clock['now']=10.0
  driver.tick()
  pending=[]
  real_atomic=model.atomic_json
  if publication=='delayed':
    monkeypatch.setattr(model,'atomic_json',lambda path,payload:pending.append((path,payload)))
  writer.send('00:11:22:33:44:55','accelCruise',10.005)
  writer.send('00:11:22:33:44:55','decelCruiseLong',10.01)
  clock['now']=10.01
  driver.tick((Buttons.CANCEL,0))
  assert reader.cancel_cutoff==10.01 and not reader.seen and not cc.enabled
  if publication=='delayed':
    monkeypatch.setattr(model,'atomic_json',real_atomic)
    for path,payload in pending: real_atomic(path,payload)
  clock['now']=10.03
  driver.tick(empty=True)
  assert helper._cruise_cancel_state and helper._activate_cruise<=0 and not cc.enabled
  assert not cc.cruiseControl.resume and not driver.last_record['emitted_res_can']
  assert len(reader.seen)==2
  writer.send('00:11:22:33:44:55',fresh_action,10.04)
  clock['now']=10.06
  driver.tick(empty=True)
  assert not helper._cruise_cancel_state and helper._activate_cruise>0 and cc.enabled
  assert len(reader.seen)==3
  writer.publish('cruise')
  clock['now']=10.09
  driver.tick(empty=True)
  assert helper._activate_cruise==0 and len(reader.seen)==3
  assert 'buttonEnable' not in driver.last_record['events']
