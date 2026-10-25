"""PC-only durable request/CAS tests; no vehicle writer or Params imports."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import apply_requests as requests


class Clock:
  def __init__(self):
    self.now = 1_000_000_000
  def __call__(self):
    return self.now


class FixedResolver:
  """Only the request controller boundary is under test here, not log parsing."""
  def __init__(self, store, clock, *, scope="pc_simulation"):
    self.store, self.clock, self.scope = store, clock, scope
    self.pointer = "c" * 64
    self.generation = 0
    self.comparison_profile = None
    self.entered = self.release = None

  def resolve(self, body):
    if self.entered is not None:
      self.entered.set()
      if not self.release.wait(3):
        raise ValueError("test_resolver_wait_timeout")
    snapshot = self.store.snapshot()
    dependencies = {key: value for key, value in snapshot["values"].items() if key != "PathOffset"}
    provenance = {"evidence_sha256": "d" * 64, "profile_sha256": "e" * 64,
      "code_hashes": {"candidates.py": "f" * 64}, "source_epoch_id": "explicit-pc-fixture", "route": None, "boot_id": None,
      "settings_history_reference": None,
      "evidence_reference": {"kind": "static", "periodic_directory": None, "window_directory": None, "bundle_directory": None, "checkpoint": None}}
    provenance["comparison_profile"] = deepcopy(self.comparison_profile)
    provenance["comparison_profile_sha256"] = requests._hash(self.comparison_profile) if self.comparison_profile is not None else None
    return {"target_id": self.store.target_id, "scope": self.scope, "session_id": snapshot["session_id"], "session_generation": self.generation,
      "artifact_sha256": "a" * 64, "result_revision": 1, "item_key": "PathOffset", "current_raw": 0, "candidate_raw": 20,
      "settings_revision": snapshot["revision"], "dependencies": dependencies, "dependency_digest": requests._hash(dependencies),
      "pointer_token": self.pointer, "resolved_at_ns": self.clock(), "expires_at_ns": self.clock() + 600_000_000_000,
      "provenance": provenance}


def pc_request_fixture(root, *, scope="pc_simulation", store_class=requests.IsolatedPCStore):
  """Reusable isolated PC setup. Real artifact resolver is tested separately."""
  clock = Clock()
  store = store_class(root / "fake-pc", "pc-demo", {"PathOffset": 0, "SteerRatioRate": 0}, session_id="pc-session", clock_ns=clock)
  resolver = FixedResolver(store, clock, scope=scope)
  body = {"request_id": "pc-request-1", "target_id": "pc-demo", "session_id": "pc-session", "artifact_sha256": "a" * 64,
          "result_revision": 1, "item_key": "PathOffset", "expected_current_raw": 0, "expected_settings_revision": 0,
          "dependency_digest": resolver.resolve({})["dependency_digest"]}
  return store, resolver, clock, body


class ApplyRequestTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)

  def fixture(self, **options):
    store, resolver, clock, body = pc_request_fixture(self.root, **options)
    self.addCleanup(store.close)
    return store, resolver, clock, body

  def controller(self, store, resolver, clock, **options):
    controller = requests.ApplyRequestController(self.root / "ledger", resolver, enabled=True, pc_store=store, clock_ns=clock, **options)
    self.addCleanup(controller.close)
    return controller

  def test_default_off_has_no_files_or_parameter_probes(self):
    with patch.object(Path, "mkdir", side_effect=AssertionError("OFF touched filesystem")):
      controller = requests.ApplyRequestController(None, None)
      self.assertFalse(controller.capabilities()["request_registration_enabled"])
      with self.assertRaises(requests.RequestDisabled):
        controller.register({"request_id": "invalid"})
      controller.close()

  def test_pc_registration_cas_durable_readback_is_not_a_consumer_ack(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock)
    self.assertEqual("registered", controller.register(body)["state"])
    self.assertEqual(0, store.snapshot()["values"]["PathOffset"])
    result = controller.execute(body["request_id"])
    self.assertEqual("stored", result["state"])
    self.assertEqual(20, store.snapshot()["values"]["PathOffset"])
    self.assertEqual(1, result["store_receipt"]["revision"])
    self.assertFalse(result["consumer_observed"])
    self.assertFalse(result["comparison_ready"])
    self.assertTrue(all(result[key] is False for key in ("apply_enabled", "device_qualified", "recommendations_enabled")))
    persisted = json.loads(store.path.read_text())
    self.assertEqual(body["request_id"], persisted["receipts"][body["request_id"]]["request_id"])
    self.assertEqual(20, persisted["values"]["PathOffset"])
    self.assertEqual(result, controller.register(body))
    self.assertEqual(result, controller.execute(body["request_id"]))
    self.assertEqual(1, store.snapshot()["revision"])

  def test_vehicle_is_terminal_held_and_never_calls_store_writer(self):
    store, resolver, clock, body = self.fixture(scope="vehicle")
    controller = self.controller(store, resolver, clock)
    with patch.object(store, "compare_and_swap", side_effect=AssertionError("vehicle wrote")):
      result = controller.register(body)
      self.assertEqual(("held", "vehicle_writer_unavailable"), (result["state"], result["reason"]))
      self.assertEqual(result, controller.execute(body["request_id"]))
      self.assertEqual(result, controller.review(body["request_id"]))
    controller.close()
    reopened = self.controller(store, resolver, clock)
    self.assertEqual("held", reopened.status(body["request_id"])["state"])
    self.assertEqual(0, store.snapshot()["revision"])

  def test_id_conflict_arbitrary_raw_and_stale_body_are_rejected(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock)
    controller.register(body)
    changed = {**body, "expected_current_raw": 1}
    with self.assertRaisesRegex(requests.RequestConflict, "id_body"):
      controller.register(changed)
    with self.assertRaises(ValueError):
      controller.register({**body, "candidate_raw": 9000})
    with self.assertRaisesRegex(requests.RequestConflict, "binding_changed"):
      controller.register({**body, "request_id": "another", "expected_settings_revision": 99})
    resolver.pointer = "b" * 64
    self.assertEqual("rejected", controller.execute(body["request_id"])["state"])
    self.assertEqual(0, store.snapshot()["revision"])

  def test_ttl_and_preintent_cancellation(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock, ttl_ns=100)
    controller.register(body)
    clock.now += 100
    self.assertEqual("expired", controller.execute(body["request_id"])["state"])
    regressed = {**body, "request_id": "same-process-clock-regression"}
    controller.register(regressed)
    clock.now -= 1
    self.assertEqual("request_clock_regressed", controller.execute(regressed["request_id"])["reason"])
    clock.now += 1
    body = {**body, "request_id": "cancel-during-resolve"}
    controller.register(body)
    resolver.entered, resolver.release = threading.Event(), threading.Event()
    results = []
    thread = threading.Thread(target=lambda: results.append(controller.execute(body["request_id"])))
    thread.start()
    self.assertTrue(resolver.entered.wait(2))
    self.assertEqual("cancelled", controller.cancel(body["request_id"])["state"])
    resolver.release.set()
    thread.join(3)
    self.assertFalse(thread.is_alive())
    self.assertEqual("cancelled", results[0]["state"])
    self.assertEqual(0, store.snapshot()["revision"])

  def test_atomic_cas_catches_a_dependency_change_after_evidence_resolution(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock)
    controller.register(body)
    original = store.compare_and_swap
    def real_race(**arguments):
      original(request_id="external-pc-operator", request_sha256="b" * 64, item_key="SteerRatioRate", expected_current_raw=0,
               expected_settings_revision=0, expected_dependencies={}, dependency_digest="c" * 64, candidate_raw=100)
      return original(**arguments)
    with patch.object(store, "compare_and_swap", side_effect=real_race):
      result = controller.execute(body["request_id"])
    self.assertEqual(("rejected", "compare_and_swap_conflict"), (result["state"], result["reason"]))
    self.assertEqual(0, store.snapshot()["values"]["PathOffset"])

  def test_recovery_never_reexecutes_uncertain_intent_or_infers_from_value(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock)
    controller.register(body)
    with patch.object(store, "compare_and_swap", side_effect=SystemExit("simulated crash before write")):
      with self.assertRaises(SystemExit):
        controller.execute(body["request_id"])
    self.assertEqual("intent", controller.status(body["request_id"])["state"])
    controller.close()
    with patch.object(store, "compare_and_swap", side_effect=AssertionError("recovery retried CAS")):
      recovered = self.controller(store, resolver, clock)
    self.assertEqual("indeterminate", recovered.status(body["request_id"])["state"])
    self.assertEqual(0, store.snapshot()["revision"])

  def test_recovery_requires_matching_durable_request_receipt(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock)
    controller.register(body)
    original = store.compare_and_swap
    def write_then_crash(**arguments):
      original(**arguments)
      raise SystemExit("simulated crash after durable PC CAS")
    with patch.object(store, "compare_and_swap", side_effect=write_then_crash):
      with self.assertRaises(SystemExit):
        controller.execute(body["request_id"])
    controller.close()
    with patch.object(store, "compare_and_swap", side_effect=AssertionError("recovery retried CAS")):
      recovered = self.controller(store, resolver, clock)
    self.assertEqual("stored", recovered.status(body["request_id"])["state"])
    self.assertEqual(1, store.snapshot()["revision"])
    self.assertFalse(recovered.status(body["request_id"])["consumer_observed"])

  def test_budget_and_torn_chain_fail_without_repair_or_store_write(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock, max_events=2)
    controller.register(body)
    with self.assertRaisesRegex(requests.RequestConflict, "ledger_budget"):
      controller.execute(body["request_id"])
    self.assertEqual(0, store.snapshot()["revision"])
    controller.close()
    path = self.root / "ledger" / "requests.jsonl"
    with path.open("ab") as stream:
      stream.write(b'{"torn":')
    before = path.read_bytes()
    with self.assertRaises(ValueError):
      self.controller(store, resolver, clock)
    self.assertEqual(before, path.read_bytes())

  def test_uncertain_registration_fsync_poison_prevents_duplicate_sequence(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock)
    original = os.fsync
    failed = []
    def fail_once(fd):
      if not failed:
        failed.append(True)
        raise OSError("injected fsync failure")
      return original(fd)
    with patch.object(requests.os, "fsync", side_effect=fail_once):
      with self.assertRaises(OSError):
        controller.register(body)
      with self.assertRaises(requests.RequestDisabled):
        controller.register(body)
    lines = [json.loads(line) for line in controller.path.read_bytes().splitlines()]
    self.assertEqual([1], [line["sequence"] for line in lines])
    self.assertFalse(controller.capabilities()["request_registration_enabled"])
    self.assertEqual(0, store.snapshot()["revision"])

  def test_stored_fsync_failure_does_not_append_another_uncertain_sequence(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock)
    controller.register(body)
    original, calls = os.fsync, []
    def fail_stored(fd):
      calls.append(fd)
      if len(calls) == 3:  # intent, PC atomic file, then stored ledger record
        raise OSError("injected stored fsync failure")
      return original(fd)
    with patch.object(requests.os, "fsync", side_effect=fail_stored):
      result = controller.execute(body["request_id"])
    self.assertEqual("indeterminate", result["state"])
    self.assertFalse(result["request_registration_enabled"])
    self.assertEqual(20, store.snapshot()["values"]["PathOffset"])
    self.assertEqual([1, 2, 3], [json.loads(line)["sequence"] for line in controller.path.read_bytes().splitlines()])
    with self.assertRaises(requests.RequestDisabled):
      controller.execute(body["request_id"])

  def test_monotonic_clock_reset_cannot_resume_a_registered_request(self):
    store, resolver, clock, body = self.fixture()
    controller = self.controller(store, resolver, clock)
    controller.register(body)
    controller.close()
    clock.now = 1
    reopened = self.controller(store, resolver, clock)
    with patch.object(store, "compare_and_swap", side_effect=AssertionError("restart resumed pending write")):
      result = reopened.execute(body["request_id"])
    self.assertEqual(("rejected", "restart_reconfirmation_required"), (result["state"], result["reason"]))
    self.assertEqual(0, store.snapshot()["revision"])

  def test_later_held_comparison_removes_current_ready_but_keeps_write_receipt(self):
    from openpilot.selfdrive.carrot.control_advisor.comparison import evaluate_comparison
    from openpilot.selfdrive.carrot.control_advisor.tests.test_comparison import comparison_fixture
    source, cohort, profile = comparison_fixture(lateral=True)
    template = evaluate_comparison(source, cohort, profile)
    self.assertEqual("comparison_ready", template["state"])
    store, resolver, clock, body = self.fixture()
    resolver.comparison_profile = profile
    mode = ["comparison_ready"]
    def provider(application, token):
      public = deepcopy(template["comparison"])
      public["state"] = mode[0]
      if mode[0] == "held":
        public.update(axis=None, before=None, after=None, matched=None, boundary=None, reason_codes=["comparison_context_changed"])
        receipt = None
      else:
        receipt = deepcopy(template["receipt"])
        receipt.update(request_id=application["request_id"], request_sha256=application["request_sha256"],
          application_receipt_sha256=application["receipt_sha256"], application_sha256=requests._hash(application), comparison_sha256=requests._hash(public))
      return {"state": mode[0], "reason_codes": public["reason_codes"], "receipt": receipt, "comparison": public}
    controller = self.controller(store, resolver, clock, comparison_provider=provider)
    controller.register(body)
    stored = controller.execute(body["request_id"])
    self.assertFalse(stored["consumer_observed"])
    ready = controller.review(body["request_id"])
    self.assertTrue(ready["comparison_ready"])
    self.assertEqual(ready, controller.review(body["request_id"]))
    # The provider's application snapshot changes once when the milestone
    # advances; after that identical results are not journalled repeatedly.
    stable = controller._sequence
    controller.review(body["request_id"])
    self.assertEqual(stable, controller._sequence)
    mode[0] = "held"
    held = controller.review(body["request_id"])
    self.assertEqual(("stored", "held"), (held["state"], held["comparison"]["state"]))
    self.assertFalse(held["comparison_ready"])
    self.assertFalse(held["consumer_observed"])
    self.assertEqual(stored["store_receipt"], held["store_receipt"])
    controller.close()
    reopened = self.controller(store, resolver, clock, comparison_provider=provider)
    self.assertEqual("stored", reopened.status(body["request_id"])["state"])
    self.assertFalse(reopened.status(body["request_id"])["comparison_ready"])

  def test_comparison_profile_is_frozen_and_changes_before_intent_are_rejected(self):
    store, resolver, clock, body = self.fixture()
    resolver.comparison_profile = {"schema_version": 1, "profile_id": "explicit-pc-profile", "version": 1}
    controller = self.controller(store, resolver, clock)
    controller.register(body)
    original = deepcopy(resolver.comparison_profile)
    resolver.comparison_profile["version"] = 2
    self.assertEqual("rejected", controller.execute(body["request_id"])["state"])
    self.assertEqual(0, store.snapshot()["revision"])
    controller.close()
    reopened = self.controller(store, resolver, clock)
    frozen = reopened.comparison_record(body["request_id"])["baseline"]
    self.assertEqual(original, frozen["comparison_profile"])
    self.assertEqual(requests._hash(original), frozen["comparison_profile_sha256"])
    self.assertNotEqual(requests._hash(resolver.comparison_profile), frozen["comparison_profile_sha256"])

  def test_comparison_profile_hash_mismatch_never_registers(self):
    store, resolver, clock, body = self.fixture()
    resolver.comparison_profile = {"version": 1}
    resolve = resolver.resolve
    def tamper(value):
      context = resolve(value)
      context["provenance"]["comparison_profile"]["version"] = 2
      return context
    controller = self.controller(store, tamper, clock)
    with self.assertRaisesRegex(requests.RequestConflict, "profile_binding_invalid"):
      controller.register(body)
    self.assertEqual([], controller.list_requests())
    self.assertEqual(0, store.snapshot()["revision"])

  def test_concurrent_identical_registration_returns_durable_result_after_first_cas(self):
    store, resolver, clock, body = self.fixture()
    original = resolver.resolve
    second_entered, first_stored = threading.Event(), threading.Event()
    count, gate = [0], threading.Lock()
    def overlapping(value):
      with gate:
        count[0] += 1
        number = count[0]
      if number == 1:
        if not second_entered.wait(3):
          raise ValueError("test_second_request_missing")
      elif number == 2:
        second_entered.set()
        if not first_stored.wait(3):
          raise ValueError("test_first_write_missing")
      return original(value)
    controller = self.controller(store, overlapping, clock)
    results, errors = [], []
    def first():
      try:
        controller.register(body)
        results.append(controller.execute(body["request_id"]))
      except Exception as error:
        errors.append(error)
      finally:
        first_stored.set()
    def second():
      try:
        results.append(controller.register(body))
      except Exception as error:
        errors.append(error)
    a, b = threading.Thread(target=first), threading.Thread(target=second)
    a.start()
    b.start()
    a.join(4)
    b.join(4)
    self.assertFalse(a.is_alive() or b.is_alive())
    self.assertEqual([], errors)
    self.assertEqual(["stored", "stored"], [result["state"] for result in results])
    self.assertEqual(1, store.snapshot()["revision"])
    self.assertEqual(3, controller._sequence)


if __name__ == "__main__":
  unittest.main()
