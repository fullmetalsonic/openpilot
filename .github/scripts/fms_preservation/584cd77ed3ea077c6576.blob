"""Explicit fake-native state tests; real compiled Params is validated separately."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import apply_requests as requests
from openpilot.selfdrive.carrot.control_advisor.vehicle_store import NativeParamsStore, NativeStoreFailed, NativeStoreUncertain
from openpilot.selfdrive.carrot.control_advisor.tests.test_apply_requests import Clock, FixedResolver
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import _schema, describe_car_params


class FakeNativeParams:
  def __init__(self, root):
    self.root = root
    root.mkdir()
    self.values = {key: b"0" for key in requests.KEYS}
    _, schema, _ = _schema()
    cp = schema.CarParams.new_message()
    cp.carFingerprint = "GENERATED_NATIVE_STORE_FIXTURE"
    self.values["CarParams"] = cp.to_bytes()
    self.calls, self.reads, self.next_result, self.before_cas = [], 0, None, None

  def get_param_path(self):
    return str(self.root)

  def get_many_raw(self, keys):
    self.reads += 1
    return {key: self.values[key] for key in keys}

  def compare_and_set_raw(self, key, value, expected):
    self.calls.append((key, value, deepcopy(expected)))
    if self.before_cas:
      self.before_cas()
    if any(self.values[k] != v for k, v in expected.items()):
      return {"state": "conflict", "errno": 0, "changed": False}
    result = self.next_result or {"state": "stored", "errno": 0, "changed": True}
    if result["state"] == "stored" or result["changed"]:
      self.values[key] = value
    return result


class VehicleFixture:
  def __init__(self, root, *, absent=False, enabled=True):
    self.root, self.clock = root, Clock()
    self.params = FakeNativeParams(root / "params")
    if absent:
      self.params.values["PathOffset"] = None
    self.session = {"session_id": "native-session", "generation": 0, "source_mode": "live", "state": "running", "target_id": "native-target"}
    self.guards, self.denied = [], False
    self.store = NativeParamsStore(root / "native-store", "native-target", self.params, session_context=lambda: self.session,
                                   write_guard=self.guard, enabled=enabled, clock_ns=self.clock, identity_provider=self.identity)
    self.resolver = FixedResolver(self.store, self.clock, scope="vehicle")
    old_resolve = self.resolver.resolve
    def resolve(body):
      result = old_resolve(body)
      result["current_raw"] = None if absent else 0
      result["provenance"].update(identity_version=1, vehicle_identity=self.identity(self.params.values["CarParams"]))
      return result
    self.resolver.resolve = resolve
    if enabled:
      snapshot = self.store.snapshot()
      context = self.resolver.resolve({})
      self.body = {"request_id": "native-request", "target_id": self.store.target_id, "session_id": self.session["session_id"],
                   "artifact_sha256": "a" * 64, "result_revision": 1, "item_key": "PathOffset", "expected_current_raw": snapshot["values"]["PathOffset"],
                   "expected_settings_revision": snapshot["revision"], "dependency_digest": context["dependency_digest"]}

  def guard(self, context):
    self.guards.append(deepcopy(context))
    if self.denied:
      raise requests.RequestDisabled("fixture_permission_revoked")

  def identity(self, raw):
    descriptor = describe_car_params(raw)
    if descriptor["state"] == "observed":
      descriptor.update(physical_group_id="a" * 32, physical_group_basis="local_confirmation")
    return descriptor

  def actor(self):
    return requests.ApplyRequestController(self.root / "ledger", self.resolver, vehicle_store=self.store, enabled=True, clock_ns=self.clock)

  def kwargs(self):
    context = self.resolver.resolve(self.body)
    self.store.prepare_request(context)
    return {"request_id": self.body["request_id"], "request_sha256": requests._hash(self.body), "item_key": context["item_key"],
            "expected_current_raw": context["current_raw"], "expected_settings_revision": context["settings_revision"],
            "expected_dependencies": context["dependencies"], "dependency_digest": context["dependency_digest"], "candidate_raw": context["candidate_raw"]}


class NativeStoreTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)

  def fixture(self, **kwargs):
    fixture = VehicleFixture(self.root, **kwargs)
    self.addCleanup(fixture.store.close)
    return fixture

  def test_native_actor_storage_is_not_consumer_ack(self):
    f = self.fixture()
    actor = f.actor()
    self.addCleanup(actor.close)
    self.assertTrue(actor.register(f.body)["apply_enabled"])
    result = actor.execute(f.body["request_id"])
    self.assertEqual(result["state"], "stored")
    self.assertEqual(f.params.values["PathOffset"], b"20")
    self.assertFalse(result["consumer_observed"])
    self.assertFalse(result["comparison_ready"])
    self.assertFalse(result["device_qualified"])
    self.assertFalse(result["recommendations_enabled"])
    self.assertFalse(result["apply_enabled"])
    self.assertGreaterEqual(len(f.guards), 4)
    self.assertEqual(set(f.params.calls[0][2]), requests.KEYS | {"CarParams"})
    self.assertEqual(actor.execute(f.body["request_id"]), result)
    self.assertEqual(len(f.params.calls), 1)

  def test_disabled_has_no_io_or_native_probe(self):
    f = self.fixture(enabled=False)
    self.assertFalse((self.root / "native-store").exists())
    self.assertEqual(f.params.reads, 0)
    with self.assertRaises(requests.RequestDisabled):
      f.store.snapshot()

  def test_missing_contract_guard_and_overlap_rejected(self):
    with self.assertRaisesRegex(ValueError, "raw_api_required"):
      NativeParamsStore(None, "target", object(), session_context=lambda: {}, write_guard=lambda _: None)
    params = FakeNativeParams(self.root / "params")
    with self.assertRaisesRegex(ValueError, "guard_required"):
      NativeParamsStore(self.root / "store", "target", params, session_context=lambda: {}, write_guard=None)
    with self.assertRaisesRegex(ValueError, "root_overlap"):
      NativeParamsStore(params.root / "journal", "target", params, session_context=lambda: {}, write_guard=lambda _: None, enabled=True)

  def test_scope_types_and_current_session_mismatch(self):
    f = self.fixture()
    with self.assertRaisesRegex(ValueError, "exclusive_native"):
      requests.ApplyRequestController(None, None, vehicle_store=object())
    context = f.resolver.resolve(f.body)
    for field, value in (("scope", "pc_simulation"), ("target_id", "other"), ("session_id", "other"), ("session_generation", 1)):
      changed = {**context, field: value}
      with self.subTest(field=field), self.assertRaises(requests.RequestConflict):
        f.store.prepare_request(changed)
    f.session["source_mode"] = "synthetic"
    with self.assertRaises(requests.RequestDisabled):
      f.store.prepare_request(context)
    self.assertFalse(f.params.calls)

  def test_exact_raw_format_change_increments_revision_and_invalidates(self):
    f = self.fixture()
    kwargs = f.kwargs()
    old = f.store.snapshot()
    f.params.values["SteerRatioRate"] = b"+000"
    changed = f.store.snapshot()
    self.assertEqual(old["values"], changed["values"])
    self.assertEqual(changed["revision"], old["revision"] + 1)
    with self.assertRaises(requests.RequestConflict):
      f.store.compare_and_swap(**kwargs)
    self.assertFalse(f.params.calls)

  def test_malformed_empty_or_missing_are_not_defaults(self):
    f = self.fixture()
    f.params.values["SteerRatioRate"] = None
    snapshot = f.store.snapshot()
    self.assertIsNone(snapshot["values"]["SteerRatioRate"])
    for raw in (b"", b"n/a", b"1.5", b"9" * 257):
      f.params.values["SteerRatioRate"] = raw
      with self.subTest(raw=raw), self.assertRaises(ValueError):
        f.store.snapshot()
    self.assertFalse(f.params.calls)

  def test_guard_changes_before_execution_and_immediately_before_cas(self):
    f = self.fixture()
    actor = f.actor()
    self.addCleanup(actor.close)
    actor.register(f.body)
    f.denied = True
    self.assertEqual(actor.execute(f.body["request_id"])["state"], "rejected")
    self.assertFalse(f.params.calls)
    f.denied = False
    kwargs = f.kwargs()
    original = f.store._persist_operation
    def revoke(group, operation_id, operation):
      original(group, operation_id, operation)
      if operation["state"] == "intent":
        f.denied = True
    with patch.object(f.store, "_persist_operation", side_effect=revoke):
      with self.assertRaises(requests.RequestDisabled):
        f.store.compare_and_swap(**kwargs)
    self.assertFalse(f.params.calls)

  def test_native_conflict_catches_write_after_last_snapshot(self):
    f = self.fixture()
    kwargs = f.kwargs()
    f.params.before_cas = lambda: f.params.values.update(SteerRatioRate=b"1")
    with self.assertRaises(requests.RequestConflict):
      f.store.compare_and_swap(**kwargs)
    self.assertEqual(f.params.values["PathOffset"], b"0")
    self.assertEqual(len(f.params.calls), 1)

  def test_native_failed_is_known_nochange_but_uncertain_is_indeterminate(self):
    for state, changed, expected in (("failed", False, "rejected"), ("uncertain", True, "indeterminate")):
      with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
        f = VehicleFixture(Path(temporary))
        actor = f.actor()
        try:
          actor.register(f.body)
          f.params.next_result = {"state": state, "errno": 5, "changed": changed}
          self.assertEqual(actor.execute(f.body["request_id"])["state"], expected)
          self.assertEqual(len(f.params.calls), 1)
        finally:
          actor.close()
          f.store.close()

  def test_intent_write_failure_prevents_cas(self):
    f = self.fixture()
    kwargs = f.kwargs()
    original = f.store._save
    def fail_intent(state):
      if state["operations"]:
        raise OSError("intent disk failure")
      original(state)
    with patch.object(f.store, "_save", side_effect=fail_intent), self.assertRaises(OSError):
      f.store.compare_and_swap(**kwargs)
    self.assertFalse(f.params.calls)

  def test_crash_after_native_store_receipt_failure_never_value_recovers(self):
    f = self.fixture()
    kwargs = f.kwargs()
    actor = f.actor()
    actor.register(f.body)
    original = f.store._save
    def fail_receipt(state):
      if any(value["state"] == "stored" for value in state["operations"].values()):
        raise OSError("receipt disk failure")
      original(state)
    with patch.object(f.store, "_save", side_effect=fail_receipt):
      self.assertEqual(actor.execute(f.body["request_id"])["state"], "indeterminate")
    self.assertEqual(f.params.values["PathOffset"], b"20")
    actor.close()
    f.store.close()
    f.store = NativeParamsStore(self.root / "native-store", "native-target", f.params, session_context=lambda: f.session,
                                write_guard=f.guard, enabled=True, clock_ns=f.clock)
    self.addCleanup(f.store.close)
    self.assertIsNone(f.store.request_receipt(f.body["request_id"]))
    with self.assertRaises(NativeStoreUncertain):
      f.store.compare_and_swap(**kwargs)
    self.assertEqual(len(f.params.calls), 1)

  def test_restart_intent_only_actor_requires_actual_durable_receipt(self):
    f = self.fixture()
    actor = f.actor()
    actor.register(f.body)
    actor._transition(f.body["request_id"], "intent")
    f.params.values["PathOffset"] = b"20"
    actor.close()
    restored = f.actor()
    self.addCleanup(restored.close)
    self.assertEqual(restored.status(f.body["request_id"])["state"], "indeterminate")
    self.assertFalse(f.params.calls)

  def test_receipt_retry_restart_and_baseline_immutable(self):
    f = self.fixture()
    kwargs = f.kwargs()
    receipt = f.store.compare_and_swap(**kwargs)
    baseline = (f.store.root / "baseline.json").read_bytes()
    self.assertEqual(f.store.compare_and_swap(**kwargs), receipt)
    with self.assertRaises(requests.RequestConflict):
      f.store.compare_and_swap(**{**kwargs, "request_sha256": "f" * 64})
    backup = f.store.export_backup()
    self.assertEqual(backup["raw_bytes"]["PathOffset"], b"0")
    self.assertFalse(backup["legacy_absence_restore_supported"])
    f.store.close()
    f.store = NativeParamsStore(self.root / "native-store", "native-target", f.params, session_context=lambda: f.session,
                                write_guard=f.guard, enabled=True, clock_ns=f.clock)
    self.addCleanup(f.store.close)
    self.assertEqual(f.store.request_receipt(f.body["request_id"]), receipt)
    self.assertEqual(f.store.compare_and_swap(**kwargs), receipt)
    self.assertEqual((f.store.root / "baseline.json").read_bytes(), baseline)
    self.assertEqual(len(f.params.calls), 1)

  def test_restore_exact_bytes_and_absence_are_idempotent(self):
    for absent in (False, True):
      with self.subTest(absent=absent), tempfile.TemporaryDirectory() as temporary:
        f = VehicleFixture(Path(temporary), absent=absent)
        try:
          kwargs = f.kwargs()
          f.store.compare_and_swap(**kwargs)
          before = (f.store.root / "baseline.json").read_bytes()
          restored = f.store.restore(f.body["request_id"], "restore-one")
          self.assertEqual(f.params.values["PathOffset"], None if absent else b"0")
          self.assertEqual(f.store.restore(f.body["request_id"], "restore-one"), restored)
          self.assertEqual(len(f.params.calls), 2)
          self.assertEqual((f.store.root / "baseline.json").read_bytes(), before)
          backup = f.store.export_backup()
          self.assertEqual("PathOffset" in backup["absent_keys"], absent)
        finally:
          f.store.close()

  def test_restore_external_change_conflicts_and_guard_rechecks(self):
    f = self.fixture()
    f.store.compare_and_swap(**f.kwargs())
    f.params.values["PathOffset"] = b"21"
    with self.assertRaises(requests.RequestConflict):
      f.store.restore(f.body["request_id"], "restore-conflict")
    f.params.values["PathOffset"] = b"20"
    f.denied = True
    with self.assertRaises(requests.RequestDisabled):
      f.store.restore(f.body["request_id"], "restore-denied")
    self.assertEqual(len(f.params.calls), 1)

  def test_actor_receipt_recovery_does_not_reapply(self):
    f = self.fixture()
    actor = f.actor()
    actor.register(f.body)
    actor._transition(f.body["request_id"], "intent")
    receipt = f.store.compare_and_swap(**f.kwargs())
    actor.close()
    restored = f.actor()
    self.addCleanup(restored.close)
    self.assertEqual(restored.status(f.body["request_id"])["state"], "stored")
    self.assertEqual(restored.comparison_record(f.body["request_id"])["source_kind"], "vehicle_stored")
    self.assertFalse(restored.status(f.body["request_id"])["consumer_observed"])
    self.assertEqual(len(f.params.calls), 1)

  def test_post_cas_receipt_budget_error_is_uncertain_not_rejected(self):
    f = self.fixture()
    actor = f.actor()
    self.addCleanup(actor.close)
    actor.register(f.body)
    original = f.store._save
    def fail_receipt(state):
      if any(value["state"] == "stored" for value in state["operations"].values()):
        raise requests.RequestConflict("injected_receipt_quota")
      original(state)
    with patch.object(f.store, "_save", side_effect=fail_receipt):
      result = actor.execute(f.body["request_id"])
    self.assertEqual(result["state"], "indeterminate")
    self.assertEqual(f.params.values["PathOffset"], b"20")
    self.assertFalse(result["consumer_observed"])
    self.assertEqual(len(f.params.calls), 1)
    self.assertFalse(actor.capabilities()["apply_enabled"])

  def test_disabled_adapter_recovery_is_indeterminate_without_native_probe(self):
    f = self.fixture()
    actor = f.actor()
    actor.register(f.body)
    actor._transition(f.body["request_id"], "intent")
    actor.close()
    f.store.close()
    disabled = NativeParamsStore(self.root / "native-store", "native-target", f.params, session_context=lambda: f.session,
                                 write_guard=f.guard, enabled=False, clock_ns=f.clock)
    self.addCleanup(disabled.close)
    before = f.params.reads
    actor = requests.ApplyRequestController(self.root / "ledger", f.resolver, vehicle_store=disabled, enabled=True, clock_ns=f.clock)
    self.addCleanup(actor.close)
    self.assertEqual(actor.status(f.body["request_id"])["state"], "indeterminate")
    self.assertEqual(f.params.reads, before)
    self.assertFalse(actor.capabilities()["apply_enabled"])

  def test_restore_receipt_failure_cannot_recover_from_absent_value(self):
    f = self.fixture(absent=True)
    f.store.compare_and_swap(**f.kwargs())
    original = f.store._save
    def fail_restore_receipt(state):
      if any(operation["state"] == "stored" for operation in state["restores"].values()):
        raise OSError("restore receipt lost")
      original(state)
    with patch.object(f.store, "_save", side_effect=fail_restore_receipt), self.assertRaises(NativeStoreUncertain):
      f.store.restore(f.body["request_id"], "restore-one")
    self.assertIsNone(f.params.values["PathOffset"])
    self.assertEqual(len(f.params.calls), 2)
    f.store.close()
    f.store = NativeParamsStore(self.root / "native-store", "native-target", f.params, session_context=lambda: f.session,
                                write_guard=f.guard, enabled=True, clock_ns=f.clock)
    self.addCleanup(f.store.close)
    with self.assertRaises(NativeStoreUncertain):
      f.store.restore(f.body["request_id"], "restore-one")
    self.assertEqual(len(f.params.calls), 2)


if __name__ == "__main__":
  unittest.main()
