"""Generated CarParams are test evidence, never physical vehicle identification."""
from copy import deepcopy
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import (
  MAX_CP_BYTES, _schema, describe_car_params, same_vehicle_identity, unavailable_identity, validate_identity)
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder, load_history
from openpilot.selfdrive.carrot.control_advisor.log_history import _observations
from openpilot.selfdrive.carrot.control_advisor.tests.test_vehicle_store import VehicleFixture
from openpilot.selfdrive.carrot.control_advisor import apply_requests as requests


def car_params_fixture(*, vin="", mass=1500.0, fingerprint="GENERATED_IDENTITY_FIXTURE"):
  _, schema, _ = _schema()
  message = schema.CarParams.new_message()
  message.carFingerprint, message.carVin, message.mass = fingerprint, vin, mass
  return message.to_bytes()


class VehicleIdentityTests(unittest.TestCase):
  def test_whole_config_ignores_vin_but_vin_has_domain_hash(self):
    first_raw = car_params_fixture(vin="1HGCM82633A004352")
    second_raw = car_params_fixture(vin="1HGCM82633A004353")
    first, second = describe_car_params(first_raw), describe_car_params(second_raw)
    self.assertEqual(first["state"], "observed")
    self.assertEqual(first["config_sha256"], second["config_sha256"])
    self.assertNotEqual(first["raw_sha256"], second["raw_sha256"])
    self.assertNotEqual(first["vin_sha256"], second["vin_sha256"])
    self.assertNotIn("1HGCM82633A004352", json.dumps(first))
    self.assertNotEqual(first["vin_sha256"], hashlib.sha256(b"1HGCM82633A004352").hexdigest())
    changed = describe_car_params(car_params_fixture(vin="1HGCM82633A004352", mass=1600.0))
    self.assertNotEqual(first["config_sha256"], changed["config_sha256"])
    self.assertEqual(first["vin_sha256"], changed["vin_sha256"])

  def test_vinless_fingerprint_never_becomes_physical_identity(self):
    value = describe_car_params(car_params_fixture())
    self.assertIsNone(value["vin_sha256"])
    self.assertFalse(same_vehicle_identity(value, value))
    self.assertTrue(same_vehicle_identity(value, value, require_group=False))
    with self.assertRaisesRegex(ValueError, "unconfirmed"):
      validate_identity(value, require_bound=True)
    value.update(physical_group_id="a" * 32, physical_group_basis="local_confirmation")
    self.assertTrue(same_vehicle_identity(value, value))
    other = {**value, "physical_group_id": "b" * 32}
    self.assertFalse(same_vehicle_identity(value, other))
    for vin in ("00000000000000000", "INVALID", "1HGCM82633A00435I"):
      self.assertIsNone(describe_car_params(car_params_fixture(vin=vin))["vin_sha256"])

  def test_invalid_absent_oversize_cannot_be_observed(self):
    for raw in (None, b"", b"not capnp", b"x" * (MAX_CP_BYTES + 1), "not bytes"):
      with self.subTest(raw_type=type(raw)):
        value = describe_car_params(raw)
        self.assertEqual(value["state"], "unavailable")
        self.assertIsNone(value["config_sha256"])
    self.assertEqual(validate_identity(unavailable_identity()), unavailable_identity())

  def test_mock_params_blocked_without_unknown_brand_inference(self):
    _, schema, _ = _schema()
    for brand, fingerprint, expected in (("mock", "EXPLICIT_FIXTURE", "unavailable"), ("", "mock", "unavailable"),
                                         ("", "EXPLICIT_FIXTURE", "observed")):
      message = schema.CarParams.new_message()
      message.brand, message.carFingerprint = brand, fingerprint
      with self.subTest(brand=brand, fingerprint=fingerprint):
        self.assertEqual(describe_car_params(message.to_bytes())["state"], expected)

  def test_settings_poll_identity_brackets_split_without_exposing_raw_cp(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      source, session = root / "params", root / "session"
      source.mkdir()
      session.mkdir()
      for key in requests.KEYS:
        (source / key).write_text("0", encoding="ascii")
      (source / "CurrentRoute").write_text("00000000--0123456789", encoding="ascii")
      identity = describe_car_params(car_params_fixture(vin="1HGCM82633A004352"))
      clock = [1_000_000_000]
      recorder = SettingsRecorder(source, session, "identity-session", clock_ns=lambda: clock[0], identity_provider=lambda: identity)
      first = recorder.sample()
      clock[0] += 1_000_000_000
      second = recorder.sample()
      self.assertEqual(first["revision"], second["revision"])
      identity = describe_car_params(car_params_fixture(vin="1HGCM82633A004352", mass=1600.0))
      clock[0] += 1_000_000_000
      third = recorder.sample()
      self.assertEqual(third["revision"], first["revision"] + 1)
      self.assertEqual(third["changed_keys"], [])
      recorder.close()
      history = load_history(recorder.path, include_records=True)
      polls = _observations(history["records"])
      self.assertEqual(len(polls), 3)
      self.assertIn("vehicle_identity", polls[-1]["record"])
      self.assertNotIn("1HGCM82633A004352", recorder.path.read_text())

  def test_midscan_identity_change_is_unavailable_not_false_stability(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      source, session = root / "params", root / "session"
      source.mkdir()
      session.mkdir()
      values = [describe_car_params(car_params_fixture()), describe_car_params(car_params_fixture(mass=1600.0))]
      count = [0]
      def provider():
        value = values[count[0] % 2]
        count[0] += 1
        return value
      recorder = SettingsRecorder(source, session, "identity-session", identity_provider=provider)
      value = recorder.sample()["vehicle_identity"]
      self.assertEqual(value["state"], "unavailable")
      self.assertEqual(value["reason"], "car_params_changed_during_settings_scan")
      recorder.close()

  def test_native_cas_binds_carparams_after_durable_intent(self):
    with tempfile.TemporaryDirectory() as directory:
      f = VehicleFixture(Path(directory))
      actor = f.actor()
      try:
        actor.register(f.body)
        original = f.store._persist_operation
        def change_cp(group, operation_id, operation):
          original(group, operation_id, operation)
          if operation["state"] == "intent":
            f.params.values["CarParams"] = car_params_fixture(mass=1800.0)
        f.store._persist_operation = change_cp
        result = actor.execute(f.body["request_id"])
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(f.params.values["PathOffset"], b"0")
        self.assertIn("CarParams", f.params.calls[0][2])
      finally:
        actor.close()
        f.store.close()

  def test_old_identity_missing_native_write_held_but_pc_v1_unchanged(self):
    with tempfile.TemporaryDirectory() as directory:
      f = VehicleFixture(Path(directory))
      try:
        context = f.resolver.resolve(f.body)
        context["provenance"].pop("identity_version")
        context["provenance"].pop("vehicle_identity")
        self.assertEqual(requests._context(context), context)
        with self.assertRaisesRegex(requests.RequestDisabled, "source_vehicle_identity_missing"):
          f.store.prepare_request(context)
        self.assertFalse(f.params.calls)
      finally:
        f.store.close()

  def test_restore_cannot_cross_configuration_or_group(self):
    with tempfile.TemporaryDirectory() as directory:
      f = VehicleFixture(Path(directory))
      try:
        f.store.compare_and_swap(**f.kwargs())
        baseline = (f.store.root / "baseline.json").read_bytes()
        f.params.values["CarParams"] = car_params_fixture(mass=1800.0)
        with self.assertRaisesRegex(requests.RequestConflict, "vehicle_identity_changed"):
          f.store.restore(f.body["request_id"], "restore-other-config")
        self.assertEqual(f.params.values["PathOffset"], b"20")
        self.assertEqual((f.store.root / "baseline.json").read_bytes(), baseline)
      finally:
        f.store.close()

  def test_saved_native_epoch_to_real_resolver_preserves_identity(self):
    from openpilot.selfdrive.carrot.control_advisor.tests.test_request_evidence import GeneratedRequestFixture
    from openpilot.selfdrive.carrot.control_advisor.tests.test_vehicle_store import FakeNativeParams
    from openpilot.selfdrive.carrot.control_advisor.vehicle_store import NativeParamsStore
    from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver
    with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
      root = Path(directory)
      raw_cp = car_params_fixture(vin="1HGCM82633A004352")
      identity = describe_car_params(raw_cp)
      identity.update(physical_group_id="a" * 32, physical_group_basis="vin")
      fixture = GeneratedRequestFixture(root / "native", identity_provider=lambda: identity)
      cleanup.callback(fixture.close)
      params = FakeNativeParams(root / "params")
      params.values.update({key: str(value).encode() for key, value in fixture.values().items()})
      params.values["CarParams"] = raw_cp
      session = {"session_id": fixture.session_id, "generation": 0, "source_mode": "live", "state": "running"}
      store = NativeParamsStore(root / "store", "native-fixture", params, session_context=lambda: session,
        write_guard=lambda _: None, enabled=True, clock_ns=fixture.host_clock, identity_provider=lambda _: identity)
      cleanup.callback(store.close)
      targets = fixture.targets("native-fixture")
      targets["native-fixture"]["scope"] = "vehicle"
      resolver = CandidateEvidenceResolver(targets, lambda _: store.request_binding(), clock_ns=fixture.host_clock)
      current = resolver.candidate_context("native-fixture")
      candidate = next(item for item in current["items"] if item["item_key"] == fixture.key)
      self.assertTrue(candidate["eligible"], candidate)
      source = resolver.capture_evidence("native-fixture", fixture.key)
      self.assertEqual(source["provenance"]["vehicle_identity"], identity)
      self.assertEqual(source["provenance"]["identity_version"], 1)
      source["provenance"]["vehicle_identity"]["config_sha256"] = "f" * 64
      self.assertEqual(resolver.capture_evidence("native-fixture", fixture.key)["provenance"]["vehicle_identity"], identity)
      params.values["CarParams"] = car_params_fixture(vin="1HGCM82633A004352", mass=1600.0)
      def rebind(raw):
        descriptor = describe_car_params(raw)
        descriptor.update(physical_group_id="a" * 32, physical_group_basis="vin")
        return descriptor
      store.identity_provider = rebind
      with self.assertRaisesRegex(requests.RequestConflict, "vehicle_identity_changed"):
        resolver.capture_evidence("native-fixture", fixture.key)
      self.assertFalse(params.calls)


if __name__ == "__main__":
  unittest.main()
