"""Current transition safety gates; no control arithmetic or actuator changes."""
from copy import deepcopy
from dataclasses import replace
import unittest

from openpilot.selfdrive.carrot.control_advisor.live import LiveSession, validate_transition
from openpilot.selfdrive.carrot.control_advisor.session import SessionController
from openpilot.selfdrive.carrot.control_advisor.worker import _synthetic_tick


class Clock:
  def __init__(self):
    self.now = 1_000_000_000
  def __call__(self):
    return self.now


class TransitionTests(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.session = LiveSession("transition-fixture", source_mode="live", clock_ns=self.clock, lateness_ns=0)

  def feed(self, index, *, override=None, invalid=False, inactive=None):
    events = _synthetic_tick(index)
    self.clock.now = events[0].mono_ns
    for position, event in enumerate(events):
      data = deepcopy(event.data)
      if event.service == "carState":
        if override == "lateral":
          data["steeringPressed"] = True
        if override == "longitudinal":
          data["gasPressed"] = True
        if invalid:
          data["canValid"] = False
      if event.service == "carControl" and inactive:
        data["latActive" if inactive == "lateral" else "longActive"] = False
      events[position] = replace(event, data=data)
    self.session.ingest_batch(events)
    return self.session.snapshot()["transition"]

  def test_each_override_requires_two_seconds_continuous_recovery(self):
    for index in range(41):
      result = self.feed(index)
    self.assertTrue(result["lateral"]["ready"])
    self.assertTrue(result["longitudinal"]["ready"])
    for axis in ("lateral", "longitudinal"):
      start = 42 if axis == "lateral" else 100
      result = self.feed(start, override=axis)
      self.assertFalse(result[axis]["ready"])
      self.assertEqual(result[axis]["reason"], "transition_driver_override")
      for index in range(start + 1, start + 41):
        result = self.feed(index)
        self.assertFalse(result[axis]["ready"])
      result = self.feed(start + 41)
      self.assertTrue(result[axis]["ready"])
      validate_transition(result)

  def test_inactive_invalid_gap_stale_stop_never_ready(self):
    for index in range(41):
      result = self.feed(index)
    self.assertTrue(result["lateral"]["ready"])
    result = self.feed(41, inactive="lateral")
    self.assertFalse(result["lateral"]["ready"])
    self.assertTrue(result["longitudinal"]["ready"])
    result = self.feed(42, invalid=True)
    self.assertFalse(any(item["ready"] for item in result.values()))
    for index in range(43, 84):
      result = self.feed(index)
    self.assertTrue(result["longitudinal"]["ready"])
    self.clock.now += 2_000_000_001
    self.assertFalse(any(item["ready"] for item in self.session.snapshot()["transition"].values()))
    result = self.feed(130)
    self.assertFalse(any(item["ready"] for item in result.values()))
    self.session.invalidate("fixture_source_gap")
    self.assertFalse(any(item["ready"] for item in self.session.snapshot()["transition"].values()))
    self.session.finish("fixture_stop")
    self.assertFalse(any(item["ready"] for item in self.session.snapshot()["transition"].values()))

  def test_parent_unique_stamp_age_heartbeat_and_stop(self):
    for index in range(41):
      self.feed(index)
    payload = self.session.snapshot()
    controller = SessionController(clock=lambda: self.clock.now / 1e9)
    controller._snapshot.update(session_id=payload["session_id"], source_mode="live", state="running", interval_seconds=300)
    controller._accept(payload, controller._generation)
    self.assertTrue(controller.status()["transition"]["lateral"]["ready"])
    self.clock.now += 1_500_000_000
    controller._accept(payload, controller._generation)
    self.clock.now += 600_000_000
    result = controller.status()["transition"]["lateral"]
    self.assertFalse(result["ready"])
    self.assertEqual(result["reason"], "transition_parent_observation_stale")
    self.clock.now -= 2_100_000_000
    controller._last_heartbeat = self.clock.now / 1e9 - 3
    self.assertFalse(controller.status()["transition"]["lateral"]["ready"])
    controller._snapshot["state"] = "stopped"
    self.assertFalse(controller.status()["transition"]["lateral"]["ready"])

  def test_strict_parent_schema_rejects_claimed_short_recovery(self):
    for index in range(41):
      self.feed(index)
    result = self.session.snapshot()["transition"]
    for mutate in (lambda value: value["lateral"].update(recovery_ns=0),
                   lambda value: value["lateral"].update(ready=1),
                   lambda value: value["lateral"].update(valid_since_mono_ns=value["lateral"]["observed_mono_ns"]),
                   lambda value: value["lateral"].update(last_blocked_mono_ns=value["lateral"]["observed_mono_ns"]),
                   lambda value: value["lateral"].update(extra="unknown")):
      changed = deepcopy(result)
      mutate(changed)
      with self.assertRaises(ValueError):
        validate_transition(changed)

  def test_default_session_has_no_artificial_hour_deadline_but_explicit_limit_remains(self):
    self.clock.now += 7_200_000_000_000
    self.session.tick()
    self.assertNotEqual(self.session.state, "ended")
    bounded = LiveSession("explicit-limit", source_mode="live", clock_ns=self.clock, max_session_seconds=1)
    self.clock.now += 1_000_000_000
    bounded.tick()
    self.assertEqual(bounded.state, "ended")
    self.assertEqual(bounded.reason, "session_time_limit")


if __name__ == "__main__":
  unittest.main()
