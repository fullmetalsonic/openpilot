"""Parity tests between the per-service indexed builder and slow reference history query."""
import unittest

from openpilot.selfdrive.carrot.control_advisor.lateral_observations import (
  LateralObservationBuilder, _Unavailable, _number, SERVICES
)
from openpilot.selfdrive.carrot.control_advisor.tests.test_lateral_observations import (
  CP, RESPONSE, ANCHOR, NS, event, source_cp, fixture_events
)


class SlowReferenceBuilder(LateralObservationBuilder):
  """Independent reference implementation replicating the exact unoptimized query behavior."""

  def _history(self, service, anchor):
    return sorted((item for item in self._events if item["service"] == service and item["effective_ns"] <= anchor),
                  key=lambda item: item["effective_ns"])

  def _latest(self, service, at, supports):
    history = self._history(service, at)
    if not history:
      raise _Unavailable(service + "_missing")
    selected = history[-1]
    self._use(selected, supports)
    if at - selected["mono_ns"] > self.max_age_ns:
      raise _Unavailable(service + "_stale")
    return selected

  def _interpolate(self, service, field, at, anchor, allowance, supports):
    history = self._history(service, anchor)
    left = next((item for item in reversed(history) if item["effective_ns"] <= at), None)
    right = next((item for item in history if item["effective_ns"] >= at), None)
    if left is None or right is None:
      raise _Unavailable(service + "_alignment_support_missing")
    self._use(left, supports)
    self._use(right, supports)
    if right["mono_ns"] - left["mono_ns"] > self.max_alignment_ns:
      raise _Unavailable(service + "_alignment_gap")
    if any(anchor - item["mono_ns"] > self.max_age_ns + allowance for item in (left, right)):
      raise _Unavailable(service + "_alignment_stale")
    a, b = _number(left["data"].get(field)), _number(right["data"].get(field))
    span = right["mono_ns"] - left["mono_ns"]
    value = a if span == 0 else a + (b - a) * ((at - left["mono_ns"]) / span)
    return _number(value), max(left["mono_ns"], right["mono_ns"])

  def _continuous(self, service, key, expected, start, end, supports):
    history = self._history(service, end)
    before = [item for item in history if item["effective_ns"] <= start]
    if not before:
      raise _Unavailable(service + "_engagement_support_missing")
    selected = [before[-1]] + [item for item in history if item["effective_ns"] > start]
    previous = start
    for item in selected:
      self._use(item, supports)
      if item["data"].get(key) is not expected:
        raise _Unavailable("steering_override" if key == "steeringPressed" else "lateral_inactive")
      if abs(item["mono_ns"] - previous) > self.max_age_ns:
        raise _Unavailable(service + "_engagement_gap")
      previous = item["mono_ns"]
    if end - previous > self.max_age_ns:
      raise _Unavailable(service + "_engagement_gap")


def make_pair(events=None, *, buffer=1000):
  opt = LateralObservationBuilder(source_cp(), max_age_ns=500_000_000,
                                  max_alignment_ns=500_000_000, max_buffer_events=buffer)
  ref = SlowReferenceBuilder(source_cp(), max_age_ns=500_000_000,
                             max_alignment_ns=500_000_000, max_buffer_events=buffer)
  if events is not None:
    for item in events:
      opt.feed(item)
      ref.feed(item)
  return opt, ref


class TestLateralObservationIndexParity(unittest.TestCase):
  def test_history_parity_duplicate_effective_times_and_arrival_order(self):
    opt, ref = make_pair()
    # Feed events with duplicate timestamps and regressed timestamps yielding duplicate effective_ns
    stream = [
      event("carState", 100, {"vEgo": 10.0, "steeringAngleDeg": 0.0, "steeringPressed": False}),
      event("carState", 100, {"vEgo": 11.0, "steeringAngleDeg": 1.0, "steeringPressed": False}),
      event("carState", 80, {"vEgo": 12.0, "steeringAngleDeg": 2.0, "steeringPressed": False}),   # regression -> effective 100
      event("carState", 200, {"vEgo": 13.0, "steeringAngleDeg": 3.0, "steeringPressed": False}),
      event("carState", 200, {"vEgo": 14.0, "steeringAngleDeg": 4.0, "steeringPressed": False}),
      event("carState", 150, {"vEgo": 15.0, "steeringAngleDeg": 5.0, "steeringPressed": False}),  # regression -> effective 200
      event("carState", 300, {"vEgo": 16.0, "steeringAngleDeg": 6.0, "steeringPressed": False}),
    ]
    for item in stream:
      opt.feed(item)
      ref.feed(item)

    for anchor in (50, 100, 150, 200, 250, 300, 400):
      with self.subTest(anchor=anchor):
        opt_hist = opt._history("carState", anchor)
        ref_hist = ref._history("carState", anchor)
        self.assertEqual(opt_hist, ref_hist)
        # Arrival order preserved for equal effective times
        if anchor >= 100:
          effective_100_vEgo = [e["data"]["vEgo"] for e in opt_hist if e["effective_ns"] == 100]
          self.assertEqual(effective_100_vEgo, [10.0, 11.0, 12.0])
        if anchor >= 200:
          effective_200_vEgo = [e["data"]["vEgo"] for e in opt_hist if e["effective_ns"] == 200]
          self.assertEqual(effective_200_vEgo, [13.0, 14.0, 15.0])

  def test_history_parity_late_invalid_and_corrupt_data(self):
    opt, ref = make_pair()
    stream = [
      event("carOutput", 1000, {"actuatorsOutput": {"torque": 0.1}}),
      event("carOutput", 900, {"actuatorsOutput": {"torque": 0.2}}, ordered=False),  # unordered regression
      event("carOutput", 1100, {"actuatorsOutput": {"torque": 0.3}}, valid=False),    # invalid event
      event("carOutput", 1200, {"actuatorsOutput": {"torque": float("nan")}}, valid=True),  # invalid numeric
      event("carOutput", 1300, {"actuatorsOutput": {"torque": 0.4}}),
    ]
    for item in stream:
      opt.feed(item)
      ref.feed(item)

    for anchor in (800, 1000, 1100, 1200, 1300, 2000):
      with self.subTest(anchor=anchor):
        self.assertEqual(opt._history("carOutput", anchor), ref._history("carOutput", anchor))

  def test_global_cross_service_eviction_parity(self):
    buffer_cap = 25
    opt, ref = make_pair(buffer=buffer_cap)

    # Interleave 100 events across 4 different services with varying rates
    events = []
    for i in range(100):
      t = (i + 1) * 10_000_000
      events.append(event("carState", t, {"vEgo": float(i), "steeringAngleDeg": 0.0, "steeringPressed": False}))
      if i % 2 == 0:
        events.append(event("carOutput", t, {"actuatorsOutput": {"torque": 0.01 * i}}))
      if i % 3 == 0:
        events.append(event("carControl", t, {"latActive": True}))
      if i % 5 == 0:
        events.append(event("liveDelay", t, {"status": "estimated", "validBlocks": 10, "lateralDelay": 0.2}))

    for idx, item in enumerate(events):
      opt.feed(item)
      ref.feed(item)
      if idx % 10 == 0 or idx >= len(events) - 10:
        self.assertEqual(opt.buffer_count, ref.buffer_count)
        self.assertLessEqual(opt.buffer_count, buffer_cap)
        total_indexed = sum(len(opt._by_service[s]) for s in SERVICES)
        self.assertEqual(total_indexed, opt.buffer_count)
        # Test history parity across all active services at current timestamp
        for svc in ("carState", "carOutput", "carControl", "liveDelay"):
          self.assertEqual(opt._history(svc, item["mono_ns"]), ref._history(svc, item["mono_ns"]))

  def test_arbitrary_and_decreasing_historical_anchor_queries(self):
    opt, ref = make_pair(fixture_events())
    anchors = [
      ANCHOR + 500_000_000,
      ANCHOR + 100_000_000,
      ANCHOR,
      ANCHOR - 50_000_000,
      RESPONSE,
      RESPONSE - 200_000_000,
      3 * NS,
      2 * NS,
      1 * NS,
      0,
    ]
    for a in anchors:
      with self.subTest(anchor=a):
        for svc in SERVICES:
          self.assertEqual(opt._history(svc, a), ref._history(svc, a))
        self.assertEqual(opt.observe(a), ref.observe(a))

  def test_whole_observe_equality_with_slow_reference(self):
    # Complete equality on valid fixture events
    opt, ref = make_pair(fixture_events())
    opt_res = opt.observe(ANCHOR)
    ref_res = ref.observe(ANCHOR)
    self.assertEqual(opt_res, ref_res)
    self.assertTrue(all(v["available"] for v in opt_res["availability"].values()))

    # Representative edge cases: missing calibration, unestimated delay, invalid carParams
    edge_cases = [
      ("missing_liveDelay", [e for e in fixture_events() if e["service"] != "liveDelay"]),
      ("missing_liveCalibration", [e for e in fixture_events() if e["service"] != "liveCalibration"]),
      ("missing_liveParameters", [e for e in fixture_events() if e["service"] != "liveParameters"]),
      ("steering_override", [
        e if not (e["service"] == "carState" and e["mono_ns"] == 4 * NS)
        else {**e, "data": {**e["data"], "steeringPressed": True}}
        for e in fixture_events()
      ]),
    ]
    for name, stream in edge_cases:
      with self.subTest(case=name):
        opt_case, ref_case = make_pair(stream)
        self.assertEqual(opt_case.observe(ANCHOR), ref_case.observe(ANCHOR))

    # carParams changed invalidates anchors from effective stamp onward
    opt_cp, ref_cp = make_pair(fixture_events())
    changed_cp = event("carParams", ANCHOR + 10, {**CP, "mass": CP["mass"] + 50.0})
    opt_cp.feed(changed_cp)
    ref_cp.feed(changed_cp)
    self.assertEqual(opt_cp.observe(ANCHOR), ref_cp.observe(ANCHOR))
    self.assertEqual(opt_cp.observe(ANCHOR + 10), ref_cp.observe(ANCHOR + 10))
    self.assertIn("recorded_cp_changed", opt_cp.observe(ANCHOR + 10)["reason_codes"])


if __name__ == "__main__":
  unittest.main()
