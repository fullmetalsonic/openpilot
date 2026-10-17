import pytest
from openpilot.selfdrive.carrot.tests.test_signal_lead_handover import setup, update
from tools.signal_analysis.test_signal_assist_planner import inputs
from types import SimpleNamespace as NS


def control_snapshot(p):
  from openpilot.selfdrive.carrot.tests.test_signal_lead_handover import final_output
  output = final_output(p)
  return (p.xState.value, p.trafficState.value, p.v_cruise, p.stop_dist, p.actual_stop_distance,
          p.start_decision.hold, p.signal_decision.hold, output.output_should_stop,
          output.output_v_target_now, output.output_a_target)


def test_notice_muting_changes_no_control_output_through_close_lead_and_loss():
  muted, sm = setup()
  audible, other_sm = setup()

  # Restore pre-5405 notice behavior at its isolated event boundary. Both
  # planners still run the same production state machine and final output veto.
  def before_mute(event, *, immediate=False):
    if immediate:
      audible.events.add(event)
    else:
      audible.add_event(event)
  audible.add_signal_event = before_mute
  for i in range(20, 60):
    for state in (sm, other_sm):
      state['radarState'].leadOne = NS(status=25 <= i < 40, dRel=4.15, vLead=0.)
    for p, state in ((muted, sm), (audible, other_sm)):
      update(p, state, i*.05, 'unknown')
    assert control_snapshot(muted) == control_snapshot(audible)
    if 25 <= i < 40:
      assert not muted.events.events

@pytest.mark.parametrize('event', ['trafficSignChanged','trafficSignGreen','trafficStopping','audioRefuse'])
@pytest.mark.parametrize('immediate', [False, True])
def test_lead_mutes_only_signal_notices_and_does_not_consume_cooldown(event, immediate):
  p, sm = setup()
  sm['radarState'].leadOne = NS(status=True, dRel=100.)
  update(p, sm, 1.)
  assert p.start_decision.hold  # far lead: the control veto is unchanged
  assert not p.events.events
  p.last_event_time = 0.
  p.add_signal_event(event, immediate=immediate)
  assert not p.events.events and p.last_event_time == 0.
  p.add_event('radarStationaryLead')
  assert p.events.events == ['radarStationaryLead']
  sm['radarState'].leadOne.status = False
  update(p, sm, 2.)
  p.last_event_time = 0.
  p.add_signal_event(event, immediate=immediate)
  assert event in p.events.events


def test_navigation_signal_notice_uses_current_lead_before_navigation_update():
  p, sm = setup()
  seen = []
  def nav(sm, speed, cruise):
    p.add_signal_event('trafficSignChanged', immediate=True)
    seen.append(p.signal_lead_present)
    return cruise, False
  p._update_carrot_man = nav
  sm['radarState'].leadOne.status = True
  update(p, sm, 1.)
  assert seen == [True] and 'trafficSignChanged' not in p.events.events
  sm['radarState'].leadOne.status = False
  update(p, sm, 2.)
  assert seen == [True, False] and 'trafficSignChanged' in p.events.events
