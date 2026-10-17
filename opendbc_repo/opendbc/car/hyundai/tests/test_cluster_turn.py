from opendbc.car.hyundai.hyundaicanfd import _select_cluster_background
from types import SimpleNamespace as NS

import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import structs
from opendbc.car.hyundai import hyundaicanfd
from opendbc.car.hyundai.values import HyundaiFlags
from openpilot.cereal import log


def test_turn_model_flashes_the_cluster_red_like_a_held_paddle():
  assert _select_cluster_background(True, True, False, 0, turning=True) == 6       # FLASHING RED
  assert _select_cluster_background(False, True, False, 0, turning=True) == 6      # lateral only too
  assert _select_cluster_background(True, True, True, 2) == 6                      # held paddle, unchanged
  assert _select_cluster_background(True, True, False, 2) == 1                     # cruise: blue
  assert _select_cluster_background(False, True, False, 0) == 3                    # lateral only: orange
  assert _select_cluster_background(False, False, False, 0) == 7                   # gray


@pytest.mark.parametrize('desire', range(5))
@pytest.mark.parametrize('lat_active', [False, True])
def test_physical_turn_signal_hold_stays_zero_in_actual_dbc(monkeypatch, desire, lat_active):
  monkeypatch.setattr(hyundaicanfd, 'Params', lambda: NS(get_int=lambda key: 0, get=lambda key: '0'))
  packer=CANPacker('hyundai_canfd_generated')
  definition=packer.dbc.name_to_msg['ADRV_0x1ea']
  stock={key:0 for key in definition.sigs}
  stock.update(COUNTER=17, LEFT_BLINK_HOLD=1, RIGHT_BLINK_HOLD=1)
  model=log.ModelDataV2.new_message()
  model.meta.desire=desire
  state=NS(out=structs.CarState(), modelV2=model, adrv_0x1ea=stock, adrv_0x161=None,
           adrv_0x200=None, ccnc_0x162=None, lfahda_cluster=None, cruise_buttons_msg=None,
           is_metric=True, paddle_button_prev=0, softHoldActive=0, trailer_connected=False)
  cc=structs.CarControl(enabled=True,latActive=lat_active)
  messages=hyundaicanfd.create_ccnc_messages(NS(flags=HyundaiFlags.CAMERA_SCC),packer,NS(ECAN=0,CAM=2),
    5,cc,state,cc.hudControl,0,False,False,0,False,0,0)
  assert len(messages)==1 and messages[0][0]==definition.address
  parser=CANParser('hyundai_canfd_generated',[('ADRV_0x1ea',20)],0)
  parser.update([(1_000_000_000,messages)])
  assert parser.vl['ADRV_0x1ea']['LEFT_BLINK_HOLD']==0
  assert parser.vl['ADRV_0x1ea']['RIGHT_BLINK_HOLD']==0
  assert stock['LEFT_BLINK_HOLD']==stock['RIGHT_BLINK_HOLD']==1
