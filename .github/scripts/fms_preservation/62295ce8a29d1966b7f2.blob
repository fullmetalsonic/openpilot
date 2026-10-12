"""card's same-process bridge from applied cruise policy to Hyundai control."""
from openpilot.cereal import log
from opendbc.car.hyundai.paddle_mode4 import make_paddle_context


def publish_paddle_context(CP, CI, helper, sm, now_nanos):
  if CP.brand != "hyundai":
    return
  initialized = (sm.seen['onroadEvents'] and
                 not any(e.name == log.OnroadEvent.EventName.selfdriveInitializing for e in sm['onroadEvents']))
  may_apply = initialized and not CP.passive and not CP.dashcamOnly
  state = CI.CS
  state.paddle_context = make_paddle_context(getattr(state, "paddle_context", None), helper, state,
                                           sm['carControl'], may_apply and sm.all_checks(['carControl']), now_nanos)
