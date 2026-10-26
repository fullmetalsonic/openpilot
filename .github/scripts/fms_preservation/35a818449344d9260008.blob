"""Composition tests using generated CP and explicit native-store stand-ins."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor import apply_requests as requests
from openpilot.selfdrive.carrot.control_advisor.native_runtime import NativeRuntime, live_environment, comparison_profile
from openpilot.selfdrive.carrot.control_advisor.tests.test_apply_requests import Clock, FixedResolver
from openpilot.selfdrive.carrot.control_advisor.tests.test_vehicle_binding import BOOT, cp_bytes
from openpilot.selfdrive.carrot.control_advisor.tests.test_vehicle_store import FakeNativeParams
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import describe_car_params


class NativeRuntimeTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.clock = Clock()
    self.params = FakeNativeParams(self.root / "params")
    self.params.values["CarParams"] = cp_bytes()
    self.params.values.update(IsOffroad=b"0", IsOnroad=b"1")
    self.binding = {"source_mode": "live", "state": "collecting", "session_id": "native-session", "generation": 0,
                    "settings_observation_fresh": True}
    self.status = {"source_mode": "live", "state": "collecting", "parking": {"source_kind": "native_event",
                   "state": "park_observed", "observed_mono_ns": self.clock()}, "consumer_observations": {}}
    self.status["transition"] = {"lateral": {"ready": True, "observed_mono_ns": self.clock()}}
    self.config = {"runtime_kind": "native_comma", "paths": {"requests": str(self.root / "requests")},
                   "capabilities": {"analysis_configured": False}, "blockers": {"analysis": ["profile_missing", "objectives_missing"]}}
    self.runtime = NativeRuntime(self.config, self.params, session_status=lambda: deepcopy(self.status),
      session_binding=lambda: deepcopy(self.binding), clock_ns=self.clock, boot_id=BOOT)
    self.addCleanup(self.runtime.close)

  def resolver(self):
    fixed = FixedResolver(self.runtime, self.clock, scope="vehicle")
    def resolve(body):
      context = fixed.resolve(body)
      context["provenance"].update(identity_version=1, vehicle_identity=self.runtime.current_context("current_vehicle")["vehicle_identity"], boot_id=BOOT)
      return context
    return resolve

  def actor(self):
    actor = requests.ApplyRequestController(self.root / "requests", self.resolver(), vehicle_store=self.runtime,
                                             enabled=True, clock_ns=self.clock)
    self.addCleanup(actor.close)
    return actor

  def body(self):
    context = self.resolver()({})
    return {"request_id": "request-1", "target_id": "current_vehicle", "session_id": "native-session",
            "artifact_sha256": "a" * 64, "result_revision": 1, "item_key": "PathOffset", "expected_current_raw": 0,
            "expected_settings_revision": context["settings_revision"], "dependency_digest": context["dependency_digest"]}

  def test_unknown_vehicle_collects_status_but_needs_one_confirmation(self):
    status = self.runtime.public_status()
    self.assertFalse(status["vehicle_confirmed"])
    self.assertFalse(status["writer_available"])
    self.assertEqual(self.config["blockers"]["analysis"], status["analysis_blockers"])
    self.assertEqual(self.params.calls, [])
    confirmed = self.runtime.confirm_vehicle()
    self.assertTrue(confirmed["writer_available"])
    self.assertTrue(confirmed["vehicle_confirmed"])
    self.assertEqual(self.params.calls, [])

  def test_parked_actor_apply_backup_and_explicit_restore(self):
    self.runtime.confirm_vehicle()
    actor, body = self.actor(), self.body()
    self.assertTrue(actor.register(body)["apply_enabled"])
    self.assertIsNone(self.runtime.successful_analysis_cut(body["request_id"]))
    stored = actor.execute(body["request_id"])
    self.assertEqual(stored["state"], "stored")
    self.assertFalse(stored["consumer_observed"])
    cut = self.runtime.successful_analysis_cut(body["request_id"])
    self.assertEqual(cut["boot_id"], BOOT)
    self.assertEqual(cut["receipt"]["stored_mono_ns"], self.runtime.request_receipt(body["request_id"])["stored_mono_ns"])
    self.assertEqual(cut["receipt"]["native_result"]["state"], "stored")
    self.assertEqual(self.params.values["PathOffset"], b"20")
    self.assertEqual(self.params.calls[0][2]["CarParams"], self.params.values["CarParams"])
    backup = self.runtime.backup()
    self.assertEqual(backup["integer_settings"]["PathOffset"], 0)
    self.assertNotIn("raw_bytes", backup)
    result = self.runtime.restore_request(body["request_id"], "restore-1")
    self.assertEqual(result["state"], "stored")
    self.assertFalse(result["consumer_observed"])
    self.assertEqual(self.params.values["PathOffset"], b"0")
    self.assertEqual(self.runtime.restore_request(body["request_id"], "restore-1"), result)
    self.assertEqual(len(self.params.calls), 2)

  def test_config_change_keeps_store_baseline_but_blocks_old_request(self):
    first = self.runtime.confirm_vehicle()
    context = self.resolver()({})
    store_id = self.runtime.snapshot()["store_id"]
    self.params.values["CarParams"] = cp_bytes(steer_ratio=16)
    second = self.runtime.public_status()
    self.assertEqual(first["vehicle"]["physical_group_id"], second["vehicle"]["physical_group_id"])
    self.assertEqual(store_id, self.runtime.snapshot()["store_id"])
    with self.assertRaisesRegex(ValueError, "identity_changed"):
      self.runtime.prepare_request(context)
    self.assertEqual(self.params.calls, [])

  def test_offroad_restore_after_session_stop_requires_both_native_flags_and_cas(self):
    self.runtime.confirm_vehicle()
    actor, body = self.actor(), self.body()
    actor.register(body)
    self.assertEqual(actor.execute(body["request_id"])["state"], "stored")
    self.binding.update(state="stopped", settings_observation_fresh=False)
    self.status.update(state="stopped", parking={})
    with self.assertRaises(requests.RequestDisabled):
      self.runtime.restore_request(body["request_id"], "restore-live")
    for off, on in ((b"1", b"1"), (b"0", b"0"), (None, b"0")):
      self.params.values.update(IsOffroad=off, IsOnroad=on)
      with self.assertRaises(requests.RequestDisabled):
        self.runtime.restore_request(body["request_id"], "restore-offroad", "offroad")
    self.params.values.update(IsOffroad=b"1", IsOnroad=b"0")
    self.assertTrue(self.runtime.public_status()["offroad_restore_available"])
    self.params.before_cas = lambda: self.params.values.update(IsOnroad=b"1")
    with self.assertRaises(requests.RequestConflict):
      self.runtime.restore_request(body["request_id"], "restore-race", "offroad")
    self.assertEqual(self.params.values["PathOffset"], b"20")
    self.params.before_cas = None
    self.params.values["IsOnroad"] = b"0"
    result = self.runtime.restore_request(body["request_id"], "restore-valid", "offroad")
    self.assertEqual(result["reason"], "restore_stored_awaiting_restart")
    self.assertEqual(self.params.values["PathOffset"], b"0")
    self.assertFalse(result["consumer_observed"])
    self.assertEqual(self.params.calls[-1][2]["IsOffroad"], b"1")
    self.assertEqual(self.params.calls[-1][2]["IsOnroad"], b"0")

  def test_stale_live_session_and_park_observation_cannot_authorize(self):
    self.runtime.confirm_vehicle()
    context = self.resolver()({})
    self.binding["settings_observation_fresh"] = False
    with self.assertRaisesRegex(requests.RequestDisabled, "live_observation"):
      self.runtime.prepare_request(context)
    self.binding["settings_observation_fresh"] = True
    self.clock.now += 3_000_000_000
    with self.assertRaisesRegex(requests.RequestDisabled, "telemetry_unready"):
      self.runtime.prepare_request(context)
    self.assertEqual(self.params.calls, [])

  def test_moving_requires_actual_matching_consumer_cp_value_and_code(self):
    self.runtime.confirm_vehicle()
    context = self.resolver()({})
    self.status["parking"]["state"] = "moving"
    identity = describe_car_params(self.params.values["CarParams"])
    event = {"consumer_id": "planner.lateral", "produced_mono_ns": self.clock(), "boot_id": BOOT,
      "vehicle_identity": identity, "fields": {"PathOffset": {"read_value": 0., "effective_value": 0., "state": "used", "source": "stored"}},
      "source_hashes": {"candidates.py": "f" * 64}}
    entry = {"consumer_id": "planner.lateral", "fresh": True, "state": "observed", "latest_event": event}
    self.status["consumer_observations"] = {"source_mode": "live", "consumers": [entry]}
    self.runtime.prepare_request(context)
    self.status["transition"]["lateral"]["ready"] = False
    with self.assertRaisesRegex(requests.RequestDisabled, "intervention"):
      self.runtime.prepare_request(context)
    self.status["transition"]["lateral"]["ready"] = True
    for name, value, reason in (("vehicle_identity", describe_car_params(cp_bytes(steer_ratio=16)), "configuration_changed"),
                               ("source_hashes", {"candidates.py": "0" * 64}, "code_changed")):
      old, event[name] = event[name], value
      with self.assertRaisesRegex(requests.RequestDisabled, reason):
        self.runtime.prepare_request(context)
      event[name] = old
    entry["fresh"] = False
    with self.assertRaisesRegex(requests.RequestDisabled, "not_fresh"):
      self.runtime.prepare_request(context)
    self.assertEqual(self.params.calls, [])

  def test_derived_environment_keeps_explicit_off_and_excludes_token(self):
    config = deepcopy(self.config)
    config["capabilities"]["analysis_configured"] = True
    config["paths"]["consumer_socket"] = "/tmp/fms_control_advisor/consumer.sock"
    env = {"CARROT_CONTROL_ADVISOR_AUTO_ENABLED": "0", "CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS": "0",
           "CARROT_CONTROL_ADVISOR_TOKEN": "secret"}
    derived = live_environment(config, env)
    self.assertEqual(derived["CARROT_CONTROL_ADVISOR_AUTO_ENABLED"], "0")
    self.assertEqual(derived["CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS"], "0")
    self.assertNotIn("CARROT_CONTROL_ADVISOR_TOKEN", derived)
    self.assertEqual(live_environment({"runtime_kind": "pc"}, env), {})
    from openpilot.selfdrive.carrot.control_advisor.comparison import _profile
    _profile(comparison_profile())


if __name__ == "__main__":
  unittest.main()
