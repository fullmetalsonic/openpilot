"""Frozen FMS DM behavior; real DBC bytes, without native IPC or ECU claims."""
import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.hyundai import hyundaicanfd
from opendbc.car.hyundai.values import HyundaiFlags
from openpilot.cereal import log


def _read_frozen_fms_hud_source(path):
  # Pin source and provenance together; normalize checkout CRLF before hashing.
  content = path.read_text(encoding='utf-8')
  assert hashlib.sha256(content.encode('utf-8')).hexdigest() == \
    '847873c5257b8428dc221d2858f6ce8b9deb6f8e1ad54b6e6be47080e684627a', 'Frozen FMS HUD fixture changed'
  fixture = json.loads(content)
  assert fixture['format_version'] == 1
  assert fixture['git_revision'] == 'dca1f33137b8f24dfd23e35bc2bc3d9fd21c5d69'
  name = 'opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py'
  assert set(fixture['sources']) == {name}
  entry = fixture['sources'][name]
  names = ['create_lfahda_cluster', 'create_lfa_icon_non_camera_scc', 'create_ccnc_messages']
  assert entry['selected_names'] == names
  assert hashlib.sha256(entry['source'].encode('utf-8')).hexdigest() == entry['source_sha256']
  assert [node.name for node in ast.parse(entry['source']).body] == names
  return entry['source']


@pytest.mark.parametrize('mutation', ['source', 'revision', 'crlf'])
def test_frozen_fms_hud_fixture_integrity(tmp_path, mutation):
  content = (Path(__file__).with_name('fixtures') / 'fms_hud_dca1f331.json').read_text(encoding='utf-8')
  if mutation == 'source':
    content = content.replace('def create_lfahda_cluster(', 'def changed_cluster(', 1)
  elif mutation == 'revision':
    content = content.replace('dca1f33137b8f24dfd23e35bc2bc3d9fd21c5d69', '0' * 40, 1)
  else:
    content = content.replace('\n', '\r\n')
  path = tmp_path / 'fixture.json'
  path.write_bytes(content.encode('utf-8'))
  if mutation == 'crlf':
    assert _read_frozen_fms_hud_source(path)
  else:
    with pytest.raises(AssertionError, match='Frozen FMS HUD fixture changed'):
      _read_frozen_fms_hud_source(path)


@pytest.fixture(scope='module')
def frozen_builders():
  source = _read_frozen_fms_hud_source(Path(__file__).with_name('fixtures') / 'fms_hud_dca1f331.json')
  functions = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
               and n.name in ('create_lfa_icon_non_camera_scc', 'create_ccnc_messages', 'create_lfahda_cluster')]
  assert len(functions) == 3
  namespace = dict(hyundaicanfd.__dict__)
  exec(compile(ast.Module(body=functions, type_ignores=[]), '<frozen FMS CAN HUD>', 'exec'), namespace)
  return namespace


@pytest.mark.parametrize('camera_scc', [False, True])
@pytest.mark.parametrize('stock_alert', [0, 1, 2, 5, 7, 8, 9, 10, 12, 14, 21])
@pytest.mark.parametrize('sound', [0, 3])
def test_current_oem_warning_filter_matches_frozen_fms(monkeypatch, frozen_builders, camera_scc, stock_alert, sound):
  monkeypatch.setattr(hyundaicanfd, 'Params', lambda: NS(get_int=lambda key: 0, get=lambda key: '0'))
  frozen_builders['Params'] = hyundaicanfd.Params
  packer, reference = CANPacker('hyundai_canfd_generated'), CANPacker('hyundai_canfd_generated')
  stock = {key: 0 for key in packer.dbc.name_to_msg['ADRV_0x161'].sigs}
  stock.update(COUNTER=254, ALERTS_2=stock_alert, SOUNDS_2=sound)
  cs = NS(adrv_0x161=stock, out=structs.CarState(), modelV2=None, lfahda_cluster=None,
          cruise_buttons_msg=None, adrv_0x200=None, adrv_0x1ea=None, ccnc_0x162=None,
          is_metric=True, paddle_button_prev=0, softHoldActive=0, trailer_connected=False)
  original = copy.deepcopy(stock)
  cc, can = structs.CarControl(), NS(ECAN=0, CAM=2)
  for tick in range(5):
    if camera_scc:
      args = (NS(flags=HyundaiFlags.CAMERA_SCC), can, tick * 5, cc, cs, cc.hudControl,
              0, False, False, 0, False, 0, 0)
      actual = hyundaicanfd.create_ccnc_messages(args[0], packer, *args[1:])
      expected = frozen_builders['create_ccnc_messages'](args[0], reference, *args[1:])
    else:
      actual = hyundaicanfd.create_lfa_icon_non_camera_scc(packer, cs, can, cc)
      expected = frozen_builders['create_lfa_icon_non_camera_scc'](reference, cs, can, cc)
    assert actual == expected
    assert stock == original


@pytest.mark.parametrize('popup', range(8))
@pytest.mark.parametrize('secondary', [0, 1, 2, 7])
def test_current_cluster_popup_matches_frozen_fms(frozen_builders, popup, secondary):
  packer, reference = CANPacker('hyundai_canfd_generated'), CANPacker('hyundai_canfd_generated')
  stock = {key: 0 for key in packer.dbc.name_to_msg['LFAHDA_CLUSTER'].sigs}
  stock.update(HDA_InfoPUDis=popup, HDA_InfoPUDis1=secondary, COUNTER=254)
  cs, can = NS(lfahda_cluster=stock), NS(ECAN=0)
  original = stock.copy()
  for active in (False, True, False):
    assert hyundaicanfd.create_lfahda_cluster(packer, cs, can, active, active) == \
      frozen_builders['create_lfahda_cluster'](reference, cs, can, active, active)
    assert stock == original


@pytest.mark.parametrize('disable_dm', [0, 1, 2])
@pytest.mark.parametrize('alert', [0, 1, 2, 3])
@pytest.mark.parametrize('soft_disabling', [False, True])
def test_production_force_decel_keeps_current_disable_dm_semantics(disable_dm, alert, soft_disabling):
  root = Path(__file__).resolve().parents[5]
  tree = ast.parse((root / 'openpilot/selfdrive/controls/controlsd.py').read_text(encoding='utf-8'))
  method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'publish')
  index = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Assign)
               and any(ast.unparse(t) == 'cs.forceDecel' for t in n.targets))
  nodes = method.body[index:index + 2]
  assert isinstance(nodes[1], ast.If)
  state = log.SelfdriveState.OpenpilotState
  sm = {'driverMonitoringState': NS(alertLevel=alert),
        'selfdriveState': NS(state=state.softDisabling if soft_disabling else state.enabled)}
  cs = NS()
  exec(compile(ast.Module(body=nodes, type_ignores=[]), '<production forceDecel>', 'exec'),
       {'self': NS(params=NS(get_int=lambda key: disable_dm), sm=sm), 'cs': cs, 'log': log, 'State': state})
  assert cs.forceDecel == (disable_dm == 0 and (alert == 3 or soft_disabling))
