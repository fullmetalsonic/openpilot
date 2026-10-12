"""FMS baseline and live signal-planner -> parser/CI/controller/DBC boundaries.

Same-process host evidence only; no native transport, Panda or ECU claim.
"""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.hyundai import radar_interface as radar_module
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.tests.test_paddle_mode4 import (chain, production_chain, controls_cancel_producer, decode)
from openpilot.selfdrive.car.tests.test_paddle_gap import mode4
from openpilot.selfdrive.carrot import signal_mode
from openpilot.selfdrive.carrot.signal_assist_runtime import SignalAssistRuntime
from openpilot.selfdrive.carrot.signal_stop_prepare import SignalStopPrepare
from openpilot.selfdrive.carrot.signal_start_guard import SignalStartGuard
from openpilot.selfdrive.carrot.tests.test_signal_stop_prepare import observation
from tools.signal_analysis.replay_signal_assist import REPO, planner_class
from tools.signal_analysis.test_signal_assist_planner import inputs

BASELINE_PATH = Path(__file__).with_name('fixtures') / 'fms_signal_radar_3f86e7d3.json'
BASELINE_SHA = '6e8b138fbd269d5c16075813c0f794047792dec34129f6bfa494660bd05d42d3'


def baseline_sources(content):
  assert hashlib.sha256(content.encode()).hexdigest() == BASELINE_SHA
  data = json.loads(content)
  assert data['format_version'] == 1
  assert data['git_revision'] == '3f86e7d325c35f03bdfc6145463a1eea37389515'
  expected = {'openpilot/selfdrive/carrot/carrot_functions.py': ['XState', 'get_driving_mode_factors', 'get_driving_mode_comfort_brake_factor', 'TrafficState', 'CarrotPlanner'],
              'opendbc_repo/opendbc/car/hyundai/radar_interface.py': ['RadarInterface']}
  assert set(data['sources']) == set(expected)
  for path, names in expected.items():
    entry = data['sources'][path]
    assert entry['selected_names'] == names
    assert hashlib.sha256(entry['source'].encode()).hexdigest() == entry['source_sha256']
    assert [n.name for n in ast.parse(entry['source']).body] == names
  return {p: entry['source'] for p, entry in data['sources'].items()}


@pytest.mark.parametrize('mutation', ['source', 'revision', 'crlf'])
def test_baseline_provenance_boundary(mutation, tmp_path):
  content = BASELINE_PATH.read_text(encoding='utf-8')
  if mutation == 'source': content = content.replace('class RadarInterface(', 'class WrongRadar(')
  elif mutation == 'revision': content = content.replace('3f86e7d325c35f03bdfc6145463a1eea37389515', '0'*40)
  else: content = content.replace('\n', '\r\n')
  path = tmp_path/'baseline.json'; path.write_bytes(content.encode())
  if mutation == 'crlf': baseline_sources(path.read_text())
  else:
    with pytest.raises(AssertionError): baseline_sources(path.read_text())


@pytest.mark.parametrize('speed,distance,lead,hold,gas,brake', [
  (15.,160.,False,0,False,False), (0.,3.,False,0,False,False),
  (10.,40.,True,0,False,False), (0.,3.,False,2,False,False),
  (15.,160.,False,0,True,False), (15.,160.,False,0,False,True),
])
def test_mode_zero_matches_frozen_fms_planner(monkeypatch, speed, distance, lead, hold, gas, brake):
  monkeypatch.setattr(signal_mode, 'signal_mode', lambda: 0)
  frozen = baseline_sources(BASELINE_PATH.read_text())
  old, _ = planner_class(frozen['openpilot/selfdrive/carrot/carrot_functions.py'])
  new, _ = planner_class((REPO/'openpilot/selfdrive/carrot/carrot_functions.py').read_text())
  planners = [old(), new()]
  sm = inputs(speed=speed, distance=distance, lead=lead)
  sm['radarState'].leadTwo = NS(status=False)
  sm['carState'].softHoldActive, sm['carState'].gasPressed, sm['carState'].brakePressed = hold, gas, brake
  for _ in range(35):
    outputs = [p.update(sm,80.,'acc') for p in planners]
    assert outputs[0] == outputs[1]
    for name in ('xState', 'v_cruise', 'stop_dist', 'comfort_brake', 'trafficState', 'soft_hold_active'):
      values = [getattr(p,name) for p in planners]
      if name in ('xState', 'trafficState'): values = [v.value for v in values]
      assert values[0] == values[1], name
    assert planners[0].events.events == planners[1].events.events
  assert not planners[1].prepare_control_active


@pytest.mark.parametrize('mode', [0,1,2,3])
@pytest.mark.parametrize('canfd', [True,False])
def test_non_bosch_radar_matches_frozen_fms_twenty_hz(monkeypatch,mode,canfd):
  monkeypatch.setattr(radar_module,'Params',lambda:NS(get_int=lambda k:mode if k=='EnableRadarTracks' else 0))
  frozen = baseline_sources(BASELINE_PATH.read_text())
  ns = dict(vars(radar_module))
  exec(compile(frozen['opendbc_repo/opendbc/car/hyundai/radar_interface.py'],'<frozen FMS radar>','exec'),ns)
  cp = structs.CarParams(carFingerprint=CAR.KIA_SORENTO_HEV_4TH_GEN if canfd else CAR.HYUNDAI_SONATA,
                        flags=int(HyundaiFlags.CANFD|HyundaiFlags.CAMERA_SCC) if canfd else int(HyundaiFlags.CAMERA_SCC),
                        radarTimeStep=.05, radarUnavailable=False, extFlags=0, safetyConfigs=[{}])
  old,new = ns['RadarInterface'](cp),radar_module.RadarInterface(cp)
  assert new.bosch is None
  packer = CANPacker('hyundai_canfd_generated' if canfd else 'hyundai_kia_generic')
  radar_packer = CANPacker('hyundai_canfd_radar_generated' if canfd else 'hyundai_kia_mando_front_radar_generated')
  publish_ticks = []
  for i in range(50):
    msg = packer.make_can_msg('SCC_CONTROL' if canfd else 'SCC11',2,
      {'ACC_ObjDist':40.,'ACC_ObjRelSpd':-1.} if canfd else {'ACC_ObjDist':40.,'ACC_ObjRelSpd':-1.,'ObjValid':1})
    packets = [msg]
    if mode > 0 and (i+1) % 5 == 0:
      for addr in range(new.radar_start_addr, new.radar_start_addr+new.radar_required_msg_count):
        fields = {'VALID_CNT':255,'VALID':2,'LONG_DIST':40.} if canfd else {'STATE':3,'LONG_DIST':40.}
        packets.append(radar_packer.make_can_msg(f'RADAR_TRACK_{addr:x}',1,fields))
    batch = [(1_000_000_000+i*10_000_000,packets)]
    a,b = old.update(batch),new.update(batch)
    assert (None if a is None else a.to_bytes()) == (None if b is None else b.to_bytes())
    assert (old.frame,old.trigger_msg_tracks,old.trigger_msg_scc) == (new.frame,new.trigger_msg_tracks,new.trigger_msg_scc)
    if b is not None: publish_ticks.append(i+1)
  assert publish_ticks == list(range(5,51,5))


@pytest.fixture
def signal_cap():
  source=REPO/'openpilot/selfdrive/controls/lib/longitudinal_planner.py'
  tree=ast.parse(source.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LongitudinalPlanner')
  node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='apply_signal_stop_prepare')
  ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
  return ns['apply_signal_stop_prepare']


@pytest.mark.parametrize('mode',[0,1,2])
@pytest.mark.parametrize('fault',['none','cancel','off','soft_hold','gas','brake','lead_one','lead_two','turn',
                                  'model_stop','stale','invalid','session','clock','speed_low','speed_high'])
def test_signal_admission_to_real_can(production_chain,monkeypatch,tmp_path,signal_cap,mode,fault):
  monkeypatch.setattr(signal_mode,'signal_mode',lambda:mode)
  cls,_=planner_class((REPO/'openpilot/selfdrive/carrot/carrot_functions.py').read_text())
  planner=cls(start_guard=SignalStartGuard(),stop_prepare=SignalStopPrepare())
  driver,ci,helper,cc,_,_=production_chain
  sm=inputs(speed=15.,distance=160.)
  sm['radarState'].leadTwo=NS(status=False)
  sm['carState'].canValid=True;sm['carState'].gearShifter='drive'
  sm['selfdriveState'].enabled=True;sm['carControl']=cc
  sm=type('State',(dict,),{})(sm)
  services=('carState','modelV2','radarState','selfdriveState','carControl')
  sm.seen=dict.fromkeys(services,True);sm.valid=dict.fromkeys(services,True);sm.alive=dict.fromkeys(services,True)
  runtime=SignalAssistRuntime(signal_mode.DIRECTORY/'stop_prepare_enabled',tmp_path/'obs')
  target=NS(output_a_target=.4,output_v_target_now=20.,output_j_target_now=1.,output_should_stop=False,
            mpc=NS(mode='acc'),dt=.05,a_desired=.4,coasting_target=70.)
  # Use live production context admission, planner state machine and final cap.
  for i in range(60):
    t=10.+i*.05;sm.recv_time=dict.fromkeys(services,t)
    runtime.observation_path.write_text(json.dumps(observation(t)))
    obs,ctx=runtime.read(sm,t);ctx.update(turning=False,geometry=lambda b:(30.,70.),actuate=mode==2)
    planner.update(sm,80.,'acc',prepare_observation=obs,prepare_context=ctx)
    target.output_a_target=.4;signal_cap(target,sm,planner,False,now=t)
  assert planner.prepare_decision.active==(mode>0)
  assert -.5 <= target.output_a_target <= .4
  assert (target.output_a_target < 0)==(mode==2)
  t=13.05;obs=observation(t);reset=False
  if fault in ('cancel','off'): sm['selfdriveState'].enabled=False;cc.enabled=cc.longActive=False;reset=True
  elif fault=='soft_hold': sm['carState'].softHoldActive=2;reset=True
  elif fault in ('gas','brake'): setattr(sm['carState'],fault+'Pressed',True)
  elif fault.startswith('lead_'): getattr(sm['radarState'],'leadOne' if fault=='lead_one' else 'leadTwo').status=True
  elif fault=='turn': sm['carState'].leftBlinker=True
  elif fault=='model_stop': target.output_should_stop=True
  elif fault=='stale': obs=observation(10.)
  elif fault=='invalid': sm.valid['carState']=False
  elif fault=='session': obs['session']='new'
  elif fault=='clock': t=12.9
  elif fault=='speed_low': sm['carState'].vEgo=5/3.6
  elif fault=='speed_high': sm['carState'].vEgo=82/3.6
  sm.recv_time=dict.fromkeys(services,t);runtime.observation_path.write_text(json.dumps(obs))
  obs,ctx=runtime.read(sm,t);ctx.update(turning=False,geometry=lambda b:(30.,70.),actuate=mode==2)
  planner.update(sm,80.,'acc',prepare_observation=obs,prepare_context=ctx)
  target.output_a_target=.4;signal_cap(target,sm,planner,reset,now=t)
  active=mode==2 and fault=='none'
  assert (target.output_a_target<0)==active
  if not active: assert target.output_a_target==.4
  cc.actuators.accel=target.output_a_target
  # Preserve production cancel producer and real parser -> VCruise -> CI.apply
  # -> controller -> DBC path. Driver/CANCEL disables normal ACC, no signal RES.
  message=None
  for _ in range(2):
    message=driver.tick(0,buttons=(4,) if fault=='cancel' else (0,),
                        acc_req=0 if fault in ('cancel','off') else 1,wheel_speed=54.) or message
  values=decode(message)
  if fault in ('cancel','off'):
    assert values['ACCMode']==0 and values['aReqRaw']==values['aReqValue']==0
  else:
    assert values['ACCMode']==1
    assert values['aReqRaw']==pytest.approx(target.output_a_target,abs=.006)
  assert values['StopReq']==0
  assert helper._activate_cruise<=0

@pytest.mark.parametrize('mode',[0,1,2,3])
def test_bosch_passive_init_never_writes_radar_ecu(monkeypatch,mode):
  from opendbc.car.hyundai import interface
  from opendbc.car.hyundai.values import HyundaiExtFlags
  calls=[]
  monkeypatch.setattr(interface,'Params',lambda:NS(get_int=lambda key:mode,put_int=lambda *args:None,
                                                put_bool=lambda *args:calls.append(('result',args))))
  monkeypatch.setattr(interface,'disable_ecu',lambda *args,**kwargs:calls.append(('disable',kwargs)))
  monkeypatch.setattr(interface,'enable_radar_tracks',lambda *args:calls.append(('write',args)))
  cp=structs.CarParams(flags=0,extFlags=int(HyundaiExtFlags.BOSCH_RADAR),openpilotLongitudinalControl=True)
  interface.CarInterface.init(cp,None,None)
  assert [kind for kind,_ in calls]==(['disable'] if mode==0 else [])


@pytest.mark.parametrize('first,second',[(2,1),(2,0),(1,2),(0,2)])
def test_live_mode_change_discards_confirmed_prepare(monkeypatch,first,second):
  from openpilot.selfdrive.carrot.signal_stop_prepare_runtime import SignalPrepareRuntime
  from openpilot.selfdrive.carrot.tests.test_signal_stop_prepare import confirmed
  state=[first];monkeypatch.setattr(signal_mode,'signal_mode',lambda:state[0])
  runtime=SignalPrepareRuntime();runtime.enabled=False
  monkeypatch.setattr(SignalAssistRuntime,'read',lambda *args:(observation(20.),dict(now=20.,enabled=True,valid=True,drive=True)))
  runtime.read({});runtime.prepare=confirmed()
  assert runtime.prepare.active
  state[0]=second
  monkeypatch.setattr(SignalAssistRuntime,'read',lambda *args:(observation(20.),dict(now=20.1,enabled=True,valid=True,drive=True)))
  obs,context=runtime.read({})
  assert obs is None and not runtime.prepare.active and not runtime.prepare.histories
  assert context['actuate']==(second==2)


def test_reenable_needs_fresh_confirmation_not_old_packet(monkeypatch,tmp_path):
  monkeypatch.setattr(signal_mode,'signal_mode',lambda:2)
  cls,_=planner_class((REPO/'openpilot/selfdrive/carrot/carrot_functions.py').read_text())
  p=cls(start_guard=SignalStartGuard(),stop_prepare=SignalStopPrepare())
  sm=inputs(speed=15.,distance=160.);sm['radarState'].leadTwo=NS(status=False)
  def tick(now,packet,enabled):
    p.update(sm,80.,'acc',prepare_observation=packet,prepare_context=dict(now=now,enabled=enabled,
      valid=True,drive=True,turning=False,geometry=lambda b:(30.,70.),actuate=True))
    return p.prepare_control_active
  for i in range(16): active=tick(20.+i*.05,observation(20.+i*.05),True)
  assert active
  assert not tick(20.8,observation(20.75),False)
  assert not tick(20.85,observation(20.75),True)
  for i in range(12): assert not tick(20.9+i*.05,observation(20.9+i*.05),True)
  assert tick(21.5,observation(21.5),True)


def test_prepare_state_does_not_expand_vcruise_auto_on_policy():
  source=(REPO/'openpilot/selfdrive/car/cruise.py').read_text()
  # Preserve the exact current policy boundary; prepare=6 is observation/cap,
  # not a request for automatic cruise engagement.
  tree=ast.parse(source)
  comparisons=[n for n in ast.walk(tree) if isinstance(n,ast.Compare)
               and any(isinstance(op,ast.In) for op in n.ops)
               and any(isinstance(c,ast.List) and all(isinstance(v,ast.Constant) for v in c.elts)
                       and [v.value for v in c.elts]==[3,5] for c in n.comparators)]
  assert comparisons
