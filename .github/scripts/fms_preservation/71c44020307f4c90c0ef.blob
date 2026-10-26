import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.analysis_control import AnalysisControl, validate_control


class AnalysisControlTests(unittest.TestCase):
  def setUp(self):
    self.control = AnalysisControl("session", "boot", profile_sha256="1" * 64, objectives_sha256="2" * 64)
    self.route = {"state": "observed", "route": "route"}
    self.identity = {"state": "observed", "physical_group_id": "vehicle"}

  def observe(self, seconds, state="unknown", nonpark=False):
    stamp = int(seconds * 1e9)
    self.control.observe(stamp, self.route, self.identity,
      {"state": state, "source_kind": "native_event", "observed_mono_ns": stamp}, stamp if nonpark else None)

  def test_initial_park_and_repeated_park_never_trigger(self):
    for n in range(1, 8):
      self.observe(n, "park_observed")
    self.assertIsNone(self.control.take())

  def test_real_edge_debounced_and_stale_recovery_does_not_trigger(self):
    self.observe(1, "moving")
    self.observe(2, "unknown")
    self.observe(5, "park_observed")
    self.assertIsNone(self.control.take())
    self.observe(6, nonpark=True)
    self.observe(7, "park_candidate")
    self.observe(9, "park_observed")
    intent = self.control.take()
    self.assertEqual(intent["trigger"], "park")
    self.assertEqual(intent["range_start_ns"], 1_000_000_000)
    self.control.update({"state": "ready", "reason": None})
    self.observe(10, "unknown")
    self.observe(11, "park_observed")
    self.assertIsNone(self.control.take())

  def test_manual_whole_range_idempotency_and_latest_pending(self):
    self.observe(1)
    self.observe(1000)
    self.control.request("first")
    first = self.control.take()
    self.assertEqual(first["range_start_ns"], 1_000_000_000)
    self.assertEqual(first["requested_end_ns"], 1_000_000_000_000)
    self.assertFalse(self.control.request("first"))
    self.control.request("second")
    self.observe(1001)
    self.control.request("latest")
    self.assertIsNone(self.control.take())
    self.control.update({"state": "ready", "reason": None})
    self.assertEqual(self.control.take()["request_id"], "latest")
    self.assertEqual(first["requested_end_ns"], 1_000_000_000_000)

  def test_cut_only_verified_bound_receipt_never_triggers(self):
    self.observe(1)
    self.observe(300)
    self.control.request("before")
    old = self.control.take()
    trip = {"vehicle_id": "vehicle", "boot_id": "boot"}
    self.assertFalse(self.control.apply_receipt({}, trip))
    self.assertFalse(self.control.apply_receipt({"stored_mono_ns": 200_000_000_000, "revision": 1}, {**trip, "boot_id": "other"}))
    receipt = {"stored_mono_ns": 200_000_000_000, "revision": 1}
    self.assertTrue(self.control.apply_receipt(receipt, trip))
    self.assertFalse(self.control.apply_receipt(receipt, trip))
    self.assertIsNone(self.control.take())
    self.observe(301)
    self.control.request("after")
    after = self.control.take()
    self.assertEqual(after["range_start_ns"], 200_000_000_000)
    self.assertEqual(old["range_start_ns"], 1_000_000_000)
    self.assertEqual(after["cut_revision"], 1)
    self.assertEqual(validate_control(self.control.snapshot())["apply_cut_ns"], "200000000000")

  def test_trip_identity_change_does_not_autoanalyze(self):
    self.observe(1, "moving")
    self.observe(2)
    self.control.request("old")
    self.route["route"] = "next"
    self.observe(3, "park_observed")
    self.assertIsNone(self.control.take())
    self.assertEqual(self.control.start_ns, 3_000_000_000)

  def test_unknown_current_route_or_stale_source_cannot_renew_trip_binding(self):
    self.observe(1)
    self.observe(2)
    self.assertTrue(self.control.binding_valid)
    self.control.observe(3_000_000_000, {"state": "unavailable"}, self.identity, {})
    self.assertFalse(self.control.binding_valid)
    self.assertFalse(self.control.snapshot()["can_analyze"])
    self.observe(4)
    self.assertTrue(self.control.binding_valid)
    self.control.observe(None, self.route, self.identity, {})
    self.assertFalse(self.control.binding_valid)
    self.assertFalse(self.control.snapshot()["can_analyze"])

  def test_goal_switch_preserves_trip_cut_and_frozen_old_intent(self):
    self.observe(1)
    self.observe(300)
    self.control.apply_receipt({"stored_mono_ns": 200_000_000_000, "revision": 1}, {"vehicle_id": "vehicle", "boot_id": "boot"})
    self.observe(301)
    self.control.request("old-goal")
    old = self.control.take()
    trip = dict(self.control.trip)
    self.control.change_goal("3" * 64, "4" * 64, "fms_accel_defaults_v1")
    self.assertEqual(self.control.trip, trip)
    self.assertEqual(self.control.cut_ns, 200_000_000_000)
    self.assertIsNone(self.control.active)
    self.assertIsNone(self.control.pending)
    self.control.request("new-goal")
    current = self.control.take()
    self.assertEqual(current["range_start_ns"], 200_000_000_000)
    self.assertEqual(current["profile_sha256"], "3" * 64)
    self.assertEqual(current["goal_id"], "fms_accel_defaults_v1")
    self.assertEqual(old["profile_sha256"], "1" * 64)
    with self.assertRaises(ValueError):
      self.control.request("old-goal")

  def test_recording_bookmarks_never_call_analyze_even_at_finish(self):
    from openpilot.selfdrive.carrot.control_advisor.live import LiveSession
    from openpilot.selfdrive.carrot.control_advisor.worker import _synthetic_tick
    session = LiveSession("record-only", source_mode="synthetic")
    with patch("openpilot.selfdrive.carrot.control_advisor.live.analyze", side_effect=AssertionError("heavy analysis while recording")):
      for tick in range(0, 12_002):
        session.ingest_batch(_synthetic_tick(tick))
      session.finish()
    self.assertIsNone(session.diagnostic)
    self.assertTrue(session.preservation_bookmark["recording_only"])
    self.assertLess(len(session._window), 100)


if __name__ == "__main__":
  unittest.main()
