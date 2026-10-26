"""Actual HTTP routes with an injected controller; no native device imports."""
from copy import deepcopy
import asyncio
import importlib.util
import json
import io
import os
import threading
import time
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from openpilot.selfdrive.carrot.control_advisor.session import SessionConflict, SessionController
from openpilot.selfdrive.carrot.control_advisor.apply_requests import IsolatedPCStore


spec = importlib.util.spec_from_file_location("control_advisor_feature", Path(__file__).parents[1] / "features/control_advisor.py")
feature = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feature)
TOKEN = "synthetic-test-token-no-real-secret-1234567890"


class FakeController:
  def __init__(self):
    self.starts, self.stops, self.closed = [], [], False
    self.analysis_requests = []
    self.analysis_cuts = []
    self.snapshot = {"session_id": None, "source_mode": None, "state": "idle", "diagnostic": None,
                     "history": [], "apply_enabled": False, "recommendations_enabled": False}

  def status(self):
    return deepcopy(self.snapshot)

  async def start(self, **values):
    if self.starts:
      if self.starts[0] == values:
        return self.status()
      raise SessionConflict("session_already_active")
    self.starts.append(values)
    self.snapshot.update(session_id="synthetic-session", source_mode=values["mode"], state="running")
    return self.status()

  async def stop(self, session_id):
    if session_id != self.snapshot["session_id"]:
      raise SessionConflict("stale_session_id")
    self.stops.append(session_id)
    self.snapshot["state"] = "stopped"
    return self.status()

  async def close(self):
    self.closed = True

  async def analyze(self, **body):
    if body["session_id"] != self.snapshot["session_id"]:
      raise SessionConflict("analysis_session_not_active")
    self.analysis_requests.append(body)
    from openpilot.selfdrive.carrot.control_advisor.analysis_control import initial_control
    self.snapshot["analysis_control"] = {**initial_control(), "state": "queued", "trigger": "manual", "request_id": body["request_id"]}
    return self.status()

  async def successful_apply(self, session_id, receipt, boot_id):
    self.analysis_cuts.append({"session_id": session_id, "receipt": deepcopy(receipt), "boot_id": boot_id})


class RequestResolverFixture:
  def __init__(self, *, store=None):
    self.store = store
    self.scope = "pc_simulation" if store else "vehicle"
    self.target_id = store.target_id if store else "current_vehicle"

  def binding(self):
    return self.store.request_binding() if self.store else {"session_id": "vehicle-fixture", "generation": 1,
      "settings_revision": 1, "settings_raw": {"PathOffset": 0}}

  def candidate_context(self, target_id):
    current = self.binding()
    return {"target_id": target_id, "scope": self.scope, "session_id": current["session_id"], "artifact_sha256": "a" * 64,
      "result_revision": 1, "settings_revision": current["settings_revision"], "dependency_digest": "b" * 64,
      "items": [{"item_key": "PathOffset", "eligible": True, "reason": None, "expected_current_raw": current["settings_raw"]["PathOffset"]}]}

  def resolve(self, body):
    current = self.binding()
    now = self.store.clock_ns() if self.store else time.monotonic_ns()
    return {"target_id": self.target_id, "scope": self.scope, "session_id": current["session_id"], "session_generation": current["generation"],
      "artifact_sha256": "a" * 64, "result_revision": 1, "item_key": "PathOffset", "current_raw": current["settings_raw"]["PathOffset"],
      "candidate_raw": 20, "settings_revision": current["settings_revision"], "dependencies": {}, "dependency_digest": "b" * 64,
      "pointer_token": "c" * 64, "resolved_at_ns": now, "expires_at_ns": now + 30_000_000_000,
      "provenance": {"evidence_sha256": "d" * 64, "profile_sha256": "e" * 64, "code_hashes": {"fixture.py": "f" * 64},
        "source_epoch_id": "fixture-epoch", "route": None, "boot_id": None, "settings_history_reference": None, "evidence_reference": {},
        "comparison_profile": None, "comparison_profile_sha256": None}}

  def body(self, request_id="http-request-one"):
    current = self.binding()
    return {"request_id": request_id, "target_id": self.target_id, "session_id": current["session_id"], "artifact_sha256": "a" * 64,
      "result_revision": 1, "item_key": "PathOffset", "expected_current_raw": current["settings_raw"]["PathOffset"],
      "expected_settings_revision": current["settings_revision"], "dependency_digest": "b" * 64}


class ControlAdvisorApiTests(unittest.IsolatedAsyncioTestCase):
  async def test_executor_selection_uses_existing_auth_and_keeps_recording_session(self):
    client, controller = await self.make_client()
    await client.post('/api/control_advisor/start', json=self.start_body(mode='live'), headers=self.headers(client))
    session_id = controller.status()['session_id']
    calls = []
    async def setter(**body):
      calls.append(body)
      controller.snapshot['analysis_execution'] = {'executor': body['executor'], 'revision': 'a' * 32, 'pending_job': None}
      return controller.status()
    controller.set_analysis_executor = setter
    body = {'session_id': session_id, 'executor': 'remote_pc'}
    reply = await client.post('/api/control_advisor/analysis-executor', json=body)
    self.assertEqual(reply.status, 403)
    reply = await client.post('/api/control_advisor/analysis-executor', json={**body, 'path': 'private'}, headers=self.headers(client))
    self.assertEqual(reply.status, 400)
    reply = await client.post('/api/control_advisor/analysis-executor', json=body, headers=self.headers(client))
    self.assertEqual(reply.status, 200)
    self.assertEqual((await reply.json())['analysis_execution']['executor'], 'remote_pc')
    self.assertEqual(controller.status()['session_id'], session_id)
    self.assertEqual(len(controller.starts), 1)
    self.assertEqual(calls, [body])

  async def test_manual_analysis_is_authenticated_bounded_and_returns_pollable_ack(self):
    client, controller = await self.make_client()
    await client.post("/api/control_advisor/start", json=self.start_body(mode="live"), headers=self.headers(client))
    body = {"session_id": controller.status()["session_id"], "request_id": "manual-analysis"}
    for headers in ({}, {**self.headers(client), "Origin": "http://other.invalid"}):
      reply = await client.post("/api/control_advisor/analyze", json=body, headers=headers)
      self.assertEqual(reply.status, 403)
    reply = await client.post("/api/control_advisor/analyze", json={**body, "source_path": "private"}, headers=self.headers(client))
    self.assertEqual(reply.status, 400)
    self.assertEqual(controller.analysis_requests, [])
    reply = await client.post("/api/control_advisor/analyze", json=body, headers=self.headers(client))
    self.assertEqual(reply.status, 200)
    payload = await reply.json()
    self.assertEqual(payload["analysis_control"]["state"], "queued")
    self.assertEqual(payload["analysis_control"], payload["session"]["analysis_control"])
    self.assertEqual(controller.analysis_requests, [body])
    from openpilot.selfdrive.carrot.control_advisor.goals import goal_options, PROFILE_ENV, OBJECTIVES_ENV
    from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
    controller.analysis_goal_options = lambda: goal_options({PROFILE_ENV: BUILTIN_PROFILE_PATH, OBJECTIVES_ENV: BUILTIN_OBJECTIVES_PATH})
    goal_body = {**body, "request_id": "same-trip-new-goal", "goal_id": "long_actuator_delay_v1"}
    reply = await client.post("/api/control_advisor/analyze", json=goal_body, headers=self.headers(client))
    self.assertEqual(reply.status, 200)
    self.assertEqual((await reply.json())["session"]["session_id"], body["session_id"])
    self.assertEqual(controller.analysis_requests[-1], goal_body)
    reply = await client.post("/api/control_advisor/analyze", json={**goal_body, "goal_id": "unknown"}, headers=self.headers(client))
    self.assertEqual(reply.status, 400)
    reply = await client.post("/api/control_advisor/analyze", json={**body, "session_id": "stale"}, headers=self.headers(client))
    self.assertEqual(reply.status, 409)

  async def test_request_binding_uses_owned_generation_and_settings_poll_freshness(self):
    now = [10.]
    controller = SessionController(clock=lambda: now[0])
    controller._snapshot.update(session_id="owned-session", source_mode="live", state="running", interval_seconds=300)
    settings = {"state": "observed", "values": {"PathOffset": 0}, "changed_keys": [], "revision": 1,
      "durable_revision": 1, "poll_sequence": 1, "history_complete": False, "consumer_applied": False,
      "atomic_snapshot": False, "completeness": "polled_partial", "observation": {"begin_ns": 1, "end_ns": 2, "gap": False},
      "journal": {"path": "settings.jsonl", "sha256": "a" * 64, "durable": True, "sequence": 1, "bytes": 100}}
    payload = {"schema_version": 1, "session_id": "owned-session", "source_mode": "live", "state": "running",
      "clock_mode": "native_monotonic", "recording": {"state": "recording", "settings": settings,
        "preservation": {"state": "disabled"}}}
    controller._accept(payload, 0)
    binding = controller.request_binding()
    self.assertTrue(binding["settings_observation_fresh"])
    self.assertEqual(0, binding["generation"])
    self.assertEqual(10_000_000_000, binding["observed_mono_ns"])
    binding["settings_raw"]["PathOffset"] = 999
    self.assertEqual(0, controller.request_binding()["settings_raw"]["PathOffset"])
    now[0] = 12.1
    controller._accept(payload, 0)
    self.assertFalse(controller.request_binding()["settings_observation_fresh"])
    self.assertEqual({}, controller.request_binding()["settings_raw"])
    settings["poll_sequence"], settings["observation"]["end_ns"] = 2, 3
    controller._accept(payload, 0)
    self.assertTrue(controller.request_binding()["settings_observation_fresh"])
    payload["state"] = "stopped"
    controller._accept(payload, 0)
    self.assertFalse(controller.request_binding()["settings_observation_fresh"])

  async def make_client(self, **configuration):
    controller = FakeController()
    app = web.Application()
    cookie_jar = configuration.pop("test_cookie_jar", None)
    feature.register(app, controller=controller, **{"token": TOKEN, "live_enabled": True, "synthetic_enabled": True, **configuration})
    client = TestClient(TestServer(app), cookie_jar=cookie_jar)
    await client.start_server()
    self.addAsyncCleanup(client.close)
    return client, controller

  def headers(self, client, **changes):
    return {"Origin": str(client.make_url("")).rstrip("/"), "X-Control-Advisor-Token": TOKEN, **changes}

  def start_body(self, **changes):
    return {"mode": "synthetic", "interval_seconds": 300, "request_id": "user-click-one", **changes}

  async def test_get_is_idle_and_never_discloses_token_or_current_params(self):
    client, controller = await self.make_client()
    response = await client.get("/api/control_advisor/status")
    self.assertEqual(200, response.status)
    payload = await response.json()
    self.assertEqual("no-store", response.headers["Cache-Control"])
    self.assertEqual("idle", payload["session"]["state"])
    self.assertTrue(payload["capabilities"]["control_configured"])
    self.assertFalse(payload["apply_enabled"])
    self.assertFalse(payload["recommendations_enabled"])
    self.assertTrue(all(item["current_raw"] is None and item["apply_enabled"] is False for item in payload["parameters"]))
    self.assertNotIn(TOKEN, json.dumps(payload))
    self.assertEqual([], controller.starts)

  async def test_start_and_stop_require_token_and_exact_same_origin(self):
    client, controller = await self.make_client()
    valid = self.headers(client)
    for headers in ({}, {"Origin": valid["Origin"]}, {"X-Control-Advisor-Token": TOKEN},
                    {**valid, "X-Control-Advisor-Token": "wrong"}, {**valid, "Origin": "http://other.invalid"},
                    {**valid, "Origin": valid["Origin"] + "/"}):
      with self.subTest(headers=list(headers)):
        response = await client.post("/api/control_advisor/start", json=self.start_body(), headers=headers)
        self.assertEqual(403, response.status)
        self.assertNotIn(TOKEN, await response.text())
    self.assertEqual([], controller.starts)
    response = await client.post("/api/control_advisor/start", json=self.start_body(), headers=valid)
    self.assertEqual(200, response.status)
    session = (await response.json())["session"]
    unauthorized = await client.post("/api/control_advisor/stop", json={"session_id": session["session_id"]})
    self.assertEqual(403, unauthorized.status)
    response = await client.post("/api/control_advisor/stop", json={"session_id": session["session_id"]}, headers=valid)
    self.assertEqual(200, response.status)
    self.assertEqual("stopped", (await response.json())["session"]["state"])

  async def test_live_goal_selection_is_catalogued_authenticated_and_path_free(self):
    from openpilot.selfdrive.carrot.control_advisor.goals import goal_options, PROFILE_ENV, OBJECTIVES_ENV
    from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
    client, controller = await self.make_client()
    options = goal_options({PROFILE_ENV: BUILTIN_PROFILE_PATH, OBJECTIVES_ENV: BUILTIN_OBJECTIVES_PATH})
    controller.analysis_goal_options = lambda: deepcopy(options)
    response = await client.get("/api/control_advisor/status")
    public = (await response.json())["analysis_goals"]
    self.assertEqual(["custom_sr_v1", "lateral_torque_kp_v1", "lateral_torque_kf_v1", "steer_actuator_delay_v1",
                      "lat_smooth_sec_v1", "long_actuator_delay_v1"], [item["id"] for item in public["options"]])
    self.assertNotIn("profile_path", json.dumps(public))
    for goal in ("../../other", None, "unknown"):
      response = await client.post("/api/control_advisor/start", headers=self.headers(client),
                                   json=self.start_body(mode="live", goal_id=goal))
      self.assertEqual(400, response.status)
    self.assertEqual([], controller.starts)
    body = self.start_body(mode="live", goal_id="custom_sr_v1")
    denied = await client.post("/api/control_advisor/start", json=body)
    self.assertEqual(403, denied.status)
    response = await client.post("/api/control_advisor/start", headers=self.headers(client), json=body)
    self.assertEqual(200, response.status)
    self.assertEqual([body], controller.starts)

  async def test_current_values_require_a_successful_saved_value_observation(self):
    client, controller = await self.make_client()
    observed = {"state": "observed", "values": {"LateralTorqueAccelFactor": 2800, "LongActuatorDelay": 50}}
    controller.snapshot["recording"] = {"settings": observed}
    response = await client.get("/api/control_advisor/status")
    payload = await response.json()
    rows = {item["key"]: item for item in payload["parameters"]}
    self.assertEqual(2800, rows["LateralTorqueAccelFactor"]["current_raw"])
    self.assertAlmostEqual(2.8, rows["LateralTorqueAccelFactor"]["current_physical"])
    self.assertFalse(rows["LateralTorqueAccelFactor"]["apply_enabled"])
    observed["state"] = "partial"
    observed["values"]["LongActuatorDelay"] = None
    response = await client.get("/api/control_advisor/status")
    partial = {item["key"]: item for item in (await response.json())["parameters"]}
    self.assertEqual(2800, partial["LateralTorqueAccelFactor"]["current_raw"])
    self.assertIsNone(partial["LongActuatorDelay"]["current_raw"])
    for state in ("failed", "unavailable", "closed"):
      observed["state"] = state
      response = await client.get("/api/control_advisor/status")
      self.assertTrue(all(item["current_raw"] is None for item in (await response.json())["parameters"]))

  async def test_configuration_defaults_and_disabled_modes(self):
    for configuration in ({"token": "too-short"}, {"token": ""}, {"live_enabled": False, "synthetic_enabled": False}):
      with self.subTest(configuration=configuration):
        client, controller = await self.make_client(**configuration)
        response = await client.post("/api/control_advisor/start", json=self.start_body(), headers=self.headers(client))
        self.assertEqual(409 if configuration.get("live_enabled") is False else 403, response.status)
        self.assertEqual([], controller.starts)

  async def test_json_types_fields_and_duplicate_keys_are_rejected(self):
    client, controller = await self.make_client()
    for body in ([], {}, self.start_body(interval_seconds=True), self.start_body(interval_seconds=5),
                 self.start_body(mode="other"), self.start_body(request_id="../escape"),
                 {**self.start_body(), "output_path": "not-allowed"}):
      with self.subTest(body=body):
        response = await client.post("/api/control_advisor/start", json=body, headers=self.headers(client))
        self.assertEqual(400, response.status)
    raw_headers = {**self.headers(client), "Content-Type": "application/json"}
    for raw in ('{"mode":"live","mode":"synthetic"}', '{"interval_seconds":NaN}', "[" * 1500 + "]" * 1500):
      with self.subTest(raw_prefix=raw[:20]):
        response = await client.post("/api/control_advisor/start", data=raw, headers=raw_headers)
        self.assertEqual(400, response.status)
    self.assertEqual([], controller.starts)

  async def test_content_type_oversize_chunking_and_url_parameters_are_rejected(self):
    client, controller = await self.make_client()
    response = await client.post("/api/control_advisor/start", data=json.dumps(self.start_body()), headers=self.headers(client))
    self.assertEqual(400, response.status)
    response = await client.post("/api/control_advisor/start?token=ignored", json=self.start_body(), headers=self.headers(client))
    self.assertEqual(400, response.status)
    response = await client.post("/api/control_advisor/start", json={"padding": "x" * 5000}, headers=self.headers(client))
    self.assertEqual(413, response.status)

    async def chunks():
      for _ in range(5):
        yield b"x" * 1000

    response = await client.post("/api/control_advisor/start", data=chunks(),
                                 headers={**self.headers(client), "Content-Type": "application/json"})
    self.assertEqual(413, response.status)
    self.assertEqual([], controller.starts)

  async def test_duplicate_start_active_conflict_and_stale_stop(self):
    client, controller = await self.make_client()
    for _ in range(2):
      response = await client.post("/api/control_advisor/start", json=self.start_body(), headers=self.headers(client))
      self.assertEqual(200, response.status)
    self.assertEqual(1, len(controller.starts))
    response = await client.post("/api/control_advisor/start", json=self.start_body(request_id="another-click"), headers=self.headers(client))
    self.assertEqual(409, response.status)
    response = await client.post("/api/control_advisor/stop", json={"session_id": "old-session"}, headers=self.headers(client))
    self.assertEqual(409, response.status)
    self.assertEqual([], controller.stops)

  async def test_closing_app_closes_its_controller(self):
    client, controller = await self.make_client()
    await client.close()
    self.assertTrue(controller.closed)

  async def test_start_returns_idempotent_result_instead_of_another_current_session(self):
    client, controller = await self.make_client()
    controller.snapshot.update(session_id="new-current-session", state="running")

    async def old_request(**values):
      return {"session_id": "old-stopped-session", "state": "stopped", "apply_enabled": False}

    controller.start = old_request
    response = await client.post("/api/control_advisor/start", json=self.start_body(), headers=self.headers(client))
    self.assertEqual("old-stopped-session", (await response.json())["session"]["session_id"])
    status = await client.get("/api/control_advisor/status")
    self.assertEqual("new-current-session", (await status.json())["session"]["session_id"])

  async def wait_report(self, client, predicate):
    for _ in range(100):
      value = (await (await client.get("/api/control_advisor/status")).json())["candidate_report"]
      if predicate(value):
        return value
      await asyncio.sleep(.01)
    self.fail("background report did not reach the expected state")

  async def test_periodic_refresh_is_off_by_default_and_conflicting_or_relative_roots_are_invalid(self):
    calls = []
    def reader(root, *, cache):
      calls.append(root)
      return {"state": "ready", "report": {}}
    for configuration, state in [({"report_directory": "", "auto_output_root": ""}, "disabled"),
                                 ({"report_directory": "some-static", "auto_output_root": str(Path.cwd())}, "invalid"),
                                 ({"report_directory": "", "auto_output_root": "relative"}, "invalid")]:
      client, _ = await self.make_client(periodic_reader=reader, **configuration)
      report = await self.wait_report(client, lambda value: value["state"] == state)
      self.assertIsNone(report["report"])
      await client.close()
    self.assertEqual([], calls)

  async def test_delayed_periodic_reader_does_not_block_http_or_overlap_and_cleanup_cancels_refresh(self):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def reader(root, *, cache):
      calls.append(root)
      entered.set()
      release.wait(2)
      return {"state": "ready", "report": {"late": True}}
    client, controller = await self.make_client(report_directory="", auto_output_root=str(Path.cwd()), periodic_reader=reader, refresh_interval=.01)
    try:
      for _ in range(100):
        if entered.is_set():
          break
        await asyncio.sleep(.01)
      self.assertTrue(entered.is_set())
      response = await asyncio.wait_for(client.get("/api/control_advisor/status"), .5)
      self.assertEqual("waiting", (await response.json())["candidate_report"]["state"])
      await asyncio.sleep(.03)
      self.assertEqual(1, len(calls))
      await asyncio.wait_for(client.close(), .5)
      self.assertTrue(controller.closed)
    finally:
      release.set()

  async def test_periodic_failure_or_processing_never_reuses_a_previous_ready_candidate(self):
    snapshot = {"state": "ready", "stale": False, "report": {"version": 1}, "result_revision": 1}
    caches = []
    def reader(root, *, cache):
      caches.append(id(cache))
      if snapshot.get("raise_error"):
        raise ValueError("private path /data/private/result")
      return deepcopy(snapshot)
    client, _ = await self.make_client(report_directory="", auto_output_root=str(Path.cwd()), periodic_reader=reader, refresh_interval=.01)
    value = await self.wait_report(client, lambda item: item["state"] == "ready")
    self.assertEqual({"version": 1}, value["report"])
    query = await client.get("/api/control_advisor/status?root=/data/private/other-result")
    self.assertEqual({"version": 1}, (await query.json())["candidate_report"]["report"])
    for state in ("running", "partial", "failed", "invalid", "stopped"):
      snapshot.update(state=state)
      value = await self.wait_report(client, lambda item: item["state"] == state)
      self.assertIsNone(value["report"])
    snapshot.update(state="ready", stale=True)
    value = await self.wait_report(client, lambda item: item["state"] == "stale")
    self.assertIsNone(value["report"])
    snapshot["raise_error"] = True
    value = await self.wait_report(client, lambda item: item.get("reason") == "periodic_result_invalid")
    self.assertIsNone(value["report"])
    self.assertNotIn("private", json.dumps(value))
    self.assertFalse(value["apply_enabled"])
    self.assertEqual(1, len(set(caches)))

  async def test_restart_reads_latest_periodic_snapshot_and_static_reports_remain_startup_only(self):
    snapshots = iter([{"state": "ready", "report": {"version": 1}}, {"state": "ready", "report": {"version": 2}}])
    def reader(root, *, cache):
      return next(snapshots)
    for version in (1, 2):
      client, _ = await self.make_client(report_directory="", auto_output_root=str(Path.cwd()), periodic_reader=reader)
      value = await self.wait_report(client, lambda item: item["state"] == "ready")
      self.assertEqual(version, value["report"]["version"])
      await client.close()
    with patch("openpilot.selfdrive.carrot.control_advisor.candidate_report.load_candidate_bundle", return_value={"state": "ready", "report": {"static": True}}) as loader:
      client, _ = await self.make_client(report_directory=str(Path.cwd()), auto_output_root="")
      for _ in range(3):
        value = (await (await client.get("/api/control_advisor/status")).json())["candidate_report"]
        self.assertEqual({"static": True}, value["report"])
      self.assertEqual(1, loader.call_count)
      await client.close()

  async def wait_request(self, client, request_id, state):
    for _ in range(200):
      response = await client.get("/api/control_advisor/apply-requests/" + request_id)
      if response.status == 200:
        value = (await response.json())["request"]
        if value["state"] == state:
          return value
      await asyncio.sleep(.01)
    self.fail("request lifecycle did not reach the expected state")

  async def test_pc_request_http_path_persists_single_cas_and_keeps_global_apply_disabled(self):
    with TemporaryDirectory() as temporary:
      root = Path(temporary)
      store = IsolatedPCStore(root / "pc-store", "pc-fixture", {"PathOffset": 0}, session_id="pc-session", clock_ns=lambda: 20_000_000_000)
      resolver = RequestResolverFixture(store=store)
      client = None
      try:
        client, _ = await self.make_client(report_directory="", auto_output_root="", request_root=str(root / "requests"),
          request_resolver=resolver, request_target_id=resolver.target_id, pc_store=store, refresh_interval=.01)
        body = resolver.body()
        for extra in ({"candidate_raw": 999}, {"path": "/private/params"}, {"adapter": "vehicle"}):
          response = await client.post("/api/control_advisor/apply-requests", json={**body, **extra}, headers=self.headers(client))
          self.assertEqual(400, response.status)
        response = await client.post("/api/control_advisor/apply-requests", json=body, headers=self.headers(client))
        self.assertEqual(202, response.status)
        stored = await self.wait_request(client, body["request_id"], "stored")
        self.assertEqual(20, stored["store_receipt"]["value"])
        self.assertEqual(20_000_000_000, stored["created_ns"])
        self.assertFalse(stored["consumer_observed"])
        self.assertFalse(stored["comparison_ready"])
        self.assertFalse(stored["apply_enabled"])
        duplicate = await client.post("/api/control_advisor/apply-requests", json=body, headers=self.headers(client))
        self.assertEqual(202, duplicate.status)
        self.assertEqual(1, store.snapshot()["revision"])
        conflict = await client.post("/api/control_advisor/apply-requests", json={**body, "expected_current_raw": 1}, headers=self.headers(client))
        self.assertEqual(409, conflict.status)
        cancelled = await client.post("/api/control_advisor/apply-requests/" + body["request_id"] + "/cancel", json={}, headers=self.headers(client))
        self.assertEqual(409, cancelled.status)
        self.assertEqual(20, store.snapshot()["values"]["PathOffset"])
        payload = await (await client.get("/api/control_advisor/status")).json()
        self.assertTrue(payload["capabilities"]["request_registration_enabled"])
        self.assertTrue(payload["capabilities"]["pc_simulation_enabled"])
        self.assertFalse(payload["apply_enabled"])
        self.assertNotIn(str(root), json.dumps(payload))
        await client.close()
      finally:
        if client is not None:
          await client.close()
        store.close()

  async def test_vehicle_registration_is_durable_held_and_mutations_keep_existing_auth_guards(self):
    with TemporaryDirectory() as temporary:
      resolver = RequestResolverFixture()
      client, _ = await self.make_client(report_directory="", auto_output_root="", request_root=str(Path(temporary) / "requests"), request_resolver=resolver)
      try:
        denied = await client.post("/api/control_advisor/apply-requests", json=resolver.body())
        self.assertEqual(403, denied.status)
        accepted = await client.post("/api/control_advisor/apply-requests", json=resolver.body(), headers=self.headers(client))
        self.assertEqual(202, accepted.status)
        summary = (await accepted.json())["request"]
        self.assertEqual("held", summary["state"])
        self.assertEqual("vehicle_writer_unavailable", summary["reason"])
        self.assertIsNone(summary["store_receipt"])
        missing = await client.post("/api/control_advisor/apply-requests/missing/cancel", json={}, headers=self.headers(client))
        self.assertEqual(404, missing.status)
      finally:
        await client.close()

  async def test_request_root_overlap_disables_only_request_registration(self):
    with TemporaryDirectory() as temporary:
      protected = Path(temporary) / "analysis"
      protected.mkdir()
      request_root = protected / "requests"
      client, controller = await self.make_client(report_directory="", auto_output_root=str(protected), periodic_reader=lambda root, cache: {"state": "waiting", "report": None},
        request_root=str(request_root), request_resolver=RequestResolverFixture())
      payload = await (await client.get("/api/control_advisor/status")).json()
      self.assertFalse(payload["capabilities"]["request_registration_enabled"])
      self.assertEqual("idle", payload["session"]["state"])
      self.assertFalse(request_root.exists())
      self.assertEqual([], controller.starts)
      await client.close()

  async def wait_summary(self, client):
    for _ in range(200):
      payload = await (await client.get("/api/control_advisor/status")).json()
      if payload["parking_summary"]["state"] == "ready":
        return payload["parking_summary"]["summary"]
      await asyncio.sleep(.01)
    self.fail("immutable summary was not published")

  async def wait_export(self, client, job_id, expected):
    for _ in range(300):
      payload = await (await client.get("/api/control_advisor/status")).json()
      job = next((item for item in payload["review_exports"] if item["job_id"] == job_id), None)
      if job and job["state"] == expected:
        return job
      await asyncio.sleep(.01)
    self.fail("review export did not reach " + expected)

  async def test_immutable_summary_export_cookie_download_and_scope_are_real_http(self):
    with TemporaryDirectory() as temporary:
      root = Path(temporary) / "exports"
      client, _ = await self.make_client(report_directory="", auto_output_root="", request_root="", export_root=str(root),
        refresh_interval=.01, test_cookie_jar=CookieJar(unsafe=True))
      try:
        summary = await self.wait_summary(client)
        self.assertTrue(summary["historical_only"])
        body = {"request_id": "export-one", "summary_id": summary["summary_id"], "summary_revision": summary["revision"], "include_raw": False}
        denied = await client.post("/api/control_advisor/export-jobs", json=body)
        self.assertEqual(403, denied.status)
        injected = await client.post("/api/control_advisor/export-jobs", json={**body, "path": "/private/raw"}, headers=self.headers(client))
        self.assertEqual(400, injected.status)
        response = await client.post("/api/control_advisor/export-jobs", json=body, headers=self.headers(client))
        self.assertEqual(202, response.status)
        job_id = (await response.json())["job"]["job_id"]
        ready = await self.wait_export(client, job_id, "ready")
        self.assertFalse(ready["include_raw"])
        duplicate = await client.post("/api/control_advisor/export-jobs", json=body, headers=self.headers(client))
        self.assertEqual(job_id, (await duplicate.json())["job"]["job_id"])
        mismatch = await client.post("/api/control_advisor/export-jobs", json={**body, "include_raw": True}, headers=self.headers(client))
        self.assertEqual(409, mismatch.status)
        individual = "/api/control_advisor/export-jobs/" + job_id
        self.assertEqual(403, (await client.get(individual)).status)
        self.assertEqual(200, (await client.get(individual, headers=self.headers(client))).status)
        location = individual + "/download"
        self.assertEqual(403, (await client.get(location)).status)
        grant = await client.post(location, json={}, headers=self.headers(client))
        self.assertEqual(200, grant.status)
        cookie = grant.cookies["ca_review_download"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual("Strict", cookie["samesite"])
        self.assertEqual(location, cookie["path"])
        self.assertNotIn(TOKEN, json.dumps(await grant.json()))
        downloaded = await client.get(location)
        self.assertEqual(200, downloaded.status)
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])
        data = await downloaded.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
          self.assertIsNone(archive.testzip())
          self.assertIn("summary.json", archive.namelist())
          self.assertNotIn(str(root), archive.read("review.json").decode())
        self.assertEqual(403, (await client.get("/api/control_advisor/export-jobs/" + "f" * 32 + "/download")).status)
        with patch.object(feature, "time", SimpleNamespace(monotonic=lambda: time.monotonic() + 61)):
          self.assertEqual(403, (await client.get(location)).status)
        (root / job_id / "pack" / "review.zip").write_bytes(b"changed completed fixture ZIP")
        self.assertEqual(409, (await client.get(location)).status)
        invalidated = await self.wait_export(client, job_id, "failed")
        self.assertFalse(invalidated["download_ready"])
      finally:
        await client.close()

  async def test_export_restart_revalidates_ready_packs_and_marks_unfinished_jobs_interrupted(self):
    from openpilot.selfdrive.carrot.control_advisor.review_export import verify_review_pack
    with TemporaryDirectory() as temporary:
      root = Path(temporary) / "exports"
      client, _ = await self.make_client(report_directory="", auto_output_root="", request_root="", export_root=str(root), refresh_interval=.01)
      try:
        summary = await self.wait_summary(client)
        body = {"request_id": "export-ready", "summary_id": summary["summary_id"], "summary_revision": summary["revision"], "include_raw": False}
        created = await client.post("/api/control_advisor/export-jobs", json=body, headers=self.headers(client))
        job_id = (await created.json())["job"]["job_id"]
        await self.wait_export(client, job_id, "ready")
      finally:
        await client.close()
      store = feature._ReviewJobs(root, verify_review_pack, feature.EXPORT_BUDGET)
      pending = store.reserve({**body, "request_id": "export-interrupted"})
      store.close()
      (root / job_id / "pack" / "review.zip").write_bytes(b"corrupt fixture ZIP")
      restarted, _ = await self.make_client(report_directory="", auto_output_root="", request_root="", export_root=str(root), refresh_interval=.01)
      try:
        await self.wait_export(restarted, job_id, "failed")
        await self.wait_export(restarted, pending["job_id"], "interrupted")
        refused = await restarted.post("/api/control_advisor/export-jobs/" + job_id + "/download", json={}, headers=self.headers(restarted))
        self.assertEqual(409, refused.status)
      finally:
        await restarted.close()

  async def test_export_cancel_is_cooperative_and_new_work_is_single_flight(self):
    entered = threading.Event()
    with TemporaryDirectory() as temporary:
      root = Path(temporary) / "exports"
      class DelayedBuilder:
        export_root, roots = root, {}
        def create_review_pack(self, destination, selection, *, cancelled, **kwargs):
          entered.set()
          deadline = time.monotonic() + 3
          while not cancelled() and time.monotonic() < deadline:
            time.sleep(.005)
          if cancelled():
            raise InterruptedError("review_cancelled")
          raise TimeoutError("review_timeout")
      client, _ = await self.make_client(report_directory="", auto_output_root="", request_root="", export_root=str(root),
        review_builder=DelayedBuilder(), export_budget={"timeout_seconds": 1}, refresh_interval=.01)
      try:
        summary = await self.wait_summary(client)
        body = {"request_id": "export-cancel", "summary_id": summary["summary_id"], "summary_revision": summary["revision"], "include_raw": True}
        created = await client.post("/api/control_advisor/export-jobs", json=body, headers=self.headers(client))
        job_id = (await created.json())["job"]["job_id"]
        for _ in range(200):
          if entered.is_set():
            break
          await asyncio.sleep(.005)
        self.assertTrue(entered.is_set())
        busy = await asyncio.wait_for(client.post("/api/control_advisor/export-jobs", json={**body, "request_id": "another-export"}, headers=self.headers(client)), .5)
        self.assertEqual(409, busy.status)
        cancelled = await client.post("/api/control_advisor/export-jobs/" + job_id + "/cancel", json={}, headers=self.headers(client))
        self.assertEqual(200, cancelled.status)
        await self.wait_export(client, job_id, "cancelled")
        timed = await client.post("/api/control_advisor/export-jobs", json={**body, "request_id": "export-timeout"}, headers=self.headers(client))
        self.assertEqual(202, timed.status)
        timed_id = (await timed.json())["job"]["job_id"]
        await self.wait_export(client, timed_id, "timed_out")
      finally:
        await client.close()

  async def test_export_root_overlap_leaves_summary_and_collection_available(self):
    with TemporaryDirectory() as temporary:
      root = Path(temporary)
      client, _ = await self.make_client(report_directory="", auto_output_root=str(root), request_root="", export_root=str(root / "exports"),
        periodic_reader=lambda directory, cache: {"state": "waiting", "report": None}, refresh_interval=.01)
      try:
        summary = await self.wait_summary(client)
        payload = await (await client.get("/api/control_advisor/status")).json()
        self.assertTrue(summary["historical_only"])
        self.assertFalse(payload["capabilities"]["review_export_enabled"])
        self.assertEqual("idle", payload["session"]["state"])
        self.assertFalse((root / "exports").exists())
      finally:
        await client.close()

  async def test_export_quota_counts_real_files_beyond_windows_legacy_path_length(self):
    from openpilot.selfdrive.carrot.control_advisor.review_export import _fs_path, verify_review_pack
    with TemporaryDirectory() as temporary:
      root = Path(temporary) / "exports"
      store = feature._ReviewJobs(root, verify_review_pack, feature.EXPORT_BUDGET)
      created = []
      file = None
      try:
        job = store.reserve({"request_id": "long-path-job", "summary_id": "summary", "summary_revision": 1, "include_raw": False})
        base = _fs_path(root / job["job_id"])
        for name in ["pack", "payload", *("deep_" + str(index) + "x" * 65 for index in range(4))]:
          base = base / name
          base.mkdir()
          created.append(base)
        before = store._usage()
        file = base / "quota.bin"
        file.write_bytes(b"x" * 4096)
        self.assertGreater(len(str(file)), 260)
        self.assertEqual(before + 4096, store._usage())
      finally:
        if file is not None and file.exists():
          file.unlink()
        for path in reversed(created):
          path.rmdir()
        store.close()

  async def test_review_retry_near_root_quota_is_read_only_and_new_alias_still_requires_space(self):
    from openpilot.selfdrive.carrot.control_advisor.review_export import review_binding
    from openpilot.selfdrive.carrot.control_advisor.tests.test_review_results import valid_review
    stores = []
    original_init = feature._ReviewJobs.__init__
    def capture(store, *args, **kwargs):
      original_init(store, *args, **kwargs)
      stores.append(store)
    with TemporaryDirectory() as temporary, patch.object(feature._ReviewJobs, "__init__", capture):
      root = Path(temporary) / "exports"
      client, _ = await self.make_client(report_directory="", auto_output_root="", request_root="", export_root=str(root), refresh_interval=.01)
      try:
        summary = await self.wait_summary(client)
        created = await client.post("/api/control_advisor/export-jobs", json={"request_id": "review-quota-export", "summary_id": summary["summary_id"],
          "summary_revision": summary["revision"], "include_raw": False}, headers=self.headers(client))
        job_id = (await created.json())["job"]["job_id"]
        await self.wait_export(client, job_id, "ready")
        location = "/api/control_advisor/export-jobs/" + job_id + "/reviews"
        body = {"request_id": "review-same-id", "review": valid_review(review_binding(root / job_id / "pack"))}
        first = await client.post(location, json=body, headers=self.headers(client))
        self.assertEqual(200, first.status)
        first_hash = (await first.json())["review"]["review_hash"]
        public = await (await client.get("/api/control_advisor/status")).json()
        metadata = next(item for item in public["review_exports"] if item["job_id"] == job_id)
        self.assertEqual(1, metadata["review_count"])
        self.assertEqual(1, metadata["latest_review_revision"])
        self.assertNotIn("declared_reviewer", json.dumps(public))
        self.assertNotIn("review_versions", json.dumps(public))
        queried = await client.post(location + "/query", json={}, headers=self.headers(client))
        self.assertEqual(200, queried.status)
        self.assertEqual(first_hash, (await queried.json())["reviews"][0]["review_hash"])
        before = {str(path.relative_to(root)): path.read_bytes() for path in (root / job_id / "reviews").rglob("*") if path.is_file()}
        stores[0].total_bytes = stores[0]._usage() + 64
        repeated = await client.post(location, json=body, headers=self.headers(client))
        self.assertEqual(200, repeated.status)
        self.assertEqual(first_hash, (await repeated.json())["review"]["review_hash"])
        alias = await client.post(location, json={**body, "request_id": "new-alias-needs-allocation"}, headers=self.headers(client))
        self.assertEqual(409, alias.status)
        self.assertEqual("review_store_limit", (await alias.json())["error"])
        after = {str(path.relative_to(root)): path.read_bytes() for path in (root / job_id / "reviews").rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(403, (await client.post(location + "/query", json={})).status)
        for source in ['{"request_id":"id","review":{"document_state":"final","x":1,"x":2}}',
                       '{"request_id":"id","review":{"value":1e999}}']:
          invalid = await client.post(location, data=source, headers=self.headers(client, **{"Content-Type": "application/json"}))
          self.assertEqual(400, invalid.status)
        oversized = await client.post(location, data=b" " * (128 * 1024 + 1), headers=self.headers(client, **{"Content-Type": "application/json"}))
        self.assertEqual(413, oversized.status)
      finally:
        await client.close()

  async def test_native_grant_cookie_authorizes_only_bound_origin_and_expires_without_a_manual_token(self):
    from openpilot.selfdrive.carrot.control_advisor.native_access import AccessSessions, issue_local_grant
    now = [1_000_000_000]
    boot = "00000000-0000-4000-8000-000000000001"
    with TemporaryDirectory() as temporary:
      root = Path(temporary) / "access"
      access = AccessSessions(root, clock_ns=lambda: now[0], boot_id=boot)
      client, _ = await self.make_client(token="", access_sessions=access, request_root="", export_root="", report_directory="", auto_output_root="",
        test_cookie_jar=CookieJar(unsafe=True))
      try:
        # Native grants require an explicit device IP, excluding loopback. The
        # TestClient transport stays local while Host/Origin exercise that contract.
        host = "192.0.2.2:" + str(client.server.port)
        origin = "http://" + host
        headers = {"Origin": origin, "Host": host}
        status = await (await client.get("/api/control_advisor/status", headers={"Host": host})).json()
        self.assertFalse(status["auth"]["authorized"])
        self.assertTrue(status["auth"]["native_available"])
        self.assertFalse(status["auth"]["manual_available"])
        self.assertEqual(403, (await client.post("/api/control_advisor/start", json=self.start_body(), headers=headers)).status)
        grant = issue_local_grant(origin, root=root, clock_ns=lambda: now[0], boot_id=boot)
        body = {"grant_id": grant["grant_id"], "secret": grant["secret"]}
        bad_origin = await client.post("/api/control_advisor/access/redeem", json=body, headers={"Origin": "http://elsewhere.invalid", "Host": host})
        self.assertEqual(403, bad_origin.status)
        redeemed = await client.post("/api/control_advisor/access/redeem", json=body, headers=headers)
        self.assertEqual(200, redeemed.status)
        cookie = redeemed.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/api/control_advisor", cookie)
        self.assertNotIn(grant["secret"], await redeemed.text())
        self.assertEqual(403, (await client.post("/api/control_advisor/access/redeem", json=body, headers=headers)).status)
        status = await (await client.get("/api/control_advisor/status", headers={"Host": host})).json()
        self.assertTrue(status["auth"]["authorized"])
        self.assertNotIn(grant["secret"], json.dumps(status))
        cross_site = await (await client.get("/api/control_advisor/status", headers={"Sec-Fetch-Site": "cross-site", "Host": host})).json()
        self.assertFalse(cross_site["auth"]["authorized"])
        self.assertEqual(200, (await client.post("/api/control_advisor/start", json=self.start_body(), headers=headers)).status)
        now[0] += 28_801_000_000_000
        expired = await (await client.get("/api/control_advisor/status", headers={"Host": host})).json()
        self.assertFalse(expired["auth"]["authorized"])
        self.assertEqual(403, (await client.post("/api/control_advisor/stop", json={"session_id": "session-one"}, headers=headers)).status)
      finally:
        await client.close()

  async def test_native_confirmation_backup_and_restore_use_only_authenticated_fixed_identifiers(self):
    calls = []
    class Runtime:
      def public_status(self):
        return {"vehicle": {"state": "observed", "car_fingerprint": "TEST CAR", "physical_group_id": "a" * 32, "vin_sha256": "private-vin"},
          "groups": [{"group_id": "a" * 32, "car_fingerprint": "TEST CAR", "vin_known": True}], "vehicle_confirmed": True,
          "writer_available": True, "reason": None, "analysis_blockers": ["profile_missing"]}
      def confirm_vehicle(self, group_id):
        calls.append(("confirm", group_id, threading.get_ident()))
        return self.public_status()
      def backup(self):
        calls.append(("backup", threading.get_ident()))
        return {"schema_version": 1, "integer_settings": {"PathOffset": 0}, "absent_keys": [], "consumer_application_ack": False}
      def restore_request(self, request_id, restore_id, mode="live"):
        calls.append(("restore", request_id, restore_id, threading.get_ident()))
        return {"state": "stored", "request_id": request_id, "restore_id": restore_id, "consumer_observed": False}
      def close(self):
        pass
    client, _ = await self.make_client(native_runtime=Runtime(), request_root="", export_root="", report_directory="", auto_output_root="")
    loop_thread = threading.get_ident()
    try:
      prefix = "/api/control_advisor/vehicle/"
      self.assertEqual(403, (await client.post(prefix + "confirm", json={"group_id": None})).status)
      self.assertEqual(400, (await client.post(prefix + "restore", json={"request_id": "../path", "restore_id": "new"}, headers=self.headers(client))).status)
      self.assertEqual(400, (await client.post(prefix + "backup", json={"path": "/private"}, headers=self.headers(client))).status)
      confirmed = await client.post(prefix + "confirm", json={"group_id": "a" * 32}, headers=self.headers(client))
      self.assertEqual(200, confirmed.status)
      self.assertNotIn("private-vin", json.dumps(await confirmed.json()))
      explicit_new = await client.post(prefix + "confirm", json={"group_id": "new"}, headers=self.headers(client))
      self.assertEqual(200, explicit_new.status)
      backup = await client.post(prefix + "backup", json={}, headers=self.headers(client))
      self.assertEqual(0, (await backup.json())["backup"]["integer_settings"]["PathOffset"])
      restored = await client.post(prefix + "restore", json={"request_id": "stored-request", "restore_id": "restore-one"}, headers=self.headers(client))
      result = (await restored.json())["restore"]
      self.assertEqual("stored", result["state"])
      self.assertFalse(result["consumer_observed"])
      self.assertTrue(all(call[-1] != loop_thread for call in calls))
      self.assertEqual(["confirm", "confirm", "backup", "restore"], [call[0] for call in calls])
      self.assertEqual("new", calls[1][1])
    finally:
      await client.close()

  async def test_native_runtime_config_prepares_live_child_without_token_or_profile_and_preserves_explicit_off(self):
    from openpilot.selfdrive.carrot.control_advisor.runtime_config import resolve_runtime_config
    class Params:
      def get_param_path(self):
        return str(root / "settings")
    class Access:
      def authorized(self, token, origin):
        return False
      def close(self):
        pass
    with TemporaryDirectory() as temporary:
      root = Path(temporary)
      params = Params()
      config = resolve_runtime_config(native_comma=True, params=params, log_root=str(root / "logs"), environment={
        "CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT": str(root / "evidence"), "CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT": str(root / "automatic"),
        "CARROT_CONTROL_ADVISOR_REQUEST_ROOT": str(root / "requests"), "CARROT_CONTROL_ADVISOR_EXPORT_ROOT": str(root / "exports"),
        "CARROT_CONTROL_ADVISOR_CONSUMER_SOCKET": str(root / "consumer.sock")})
      constructed = []
      def create(**kwargs):
        constructed.append(kwargs)
        return FakeController()
      for explicit_off in (False, True):
        environment = {"CARROT_CONTROL_ADVISOR_AUTO_ENABLED": "0", "CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS": "0"}
        if explicit_off:
          environment["CARROT_CONTROL_ADVISOR_LIVE_ENABLED"] = "0"
        with patch.dict(os.environ, environment, clear=True), patch.object(feature, "SessionController", create):
          app = web.Application()
          feature.register(app, token="", access_sessions=Access(), runtime_config=config, native_params=params,
            request_root="", export_root="", auto_output_root="", report_directory="")
          client = TestClient(TestServer(app))
          await client.start_server()
          try:
            status = await (await client.get("/api/control_advisor/status")).json()
            self.assertTrue(status["capabilities"]["control_configured"])
            self.assertEqual(not explicit_off, status["capabilities"]["live_enabled"])
            self.assertTrue(status["native_runtime"]["analysis_configured"])
            self.assertNotIn("profile_missing", status["native_runtime"]["blockers"]["analysis"])
            child = constructed[-1]["live_environment"]
            self.assertEqual(str(root / "settings"), child["CARROT_CONTROL_ADVISOR_SETTINGS_DIR"])
            self.assertEqual("0", child["CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS"])
            self.assertNotIn("CARROT_CONTROL_ADVISOR_TOKEN", child)
            self.assertEqual("0", child["CARROT_CONTROL_ADVISOR_AUTO_ENABLED"])
          finally:
            await client.close()

  async def test_explicit_native_composition_runs_http_request_to_raw_cas_backup_and_request_linked_restore(self):
    from openpilot.selfdrive.carrot.control_advisor.tests.test_native_runtime import NativeRuntimeTests
    from openpilot.selfdrive.carrot.control_advisor.comparison import evaluate_comparison
    from openpilot.selfdrive.carrot.control_advisor.native_runtime import comparison_profile
    fixture = NativeRuntimeTests()
    fixture.setUp()
    self.addCleanup(fixture.doCleanups)
    class Resolver:
      def resolve(self, body):
        return fixture.resolver()(body)
      def candidate_context(self, target_id):
        value = self.resolve({})
        return {key: value[key] for key in ("target_id", "scope", "session_id", "artifact_sha256", "result_revision", "settings_revision", "dependency_digest")} | {
          "items": [{"item_key": "PathOffset", "eligible": True, "reason": None, "expected_current_raw": value["current_raw"]}]}
    provider = lambda application, token: evaluate_comparison(application, {}, comparison_profile())
    client, controller = await self.make_client(native_runtime=fixture.runtime, request_resolver=Resolver(), request_root=str(fixture.root / "requests"),
      export_root="", report_directory="", auto_output_root="", comparison_provider=provider, refresh_interval=.01)
    try:
      prefix = "/api/control_advisor/vehicle/"
      confirmed = await client.post(prefix + "confirm", json={"group_id": None}, headers=self.headers(client))
      self.assertEqual(200, confirmed.status)
      self.assertTrue((await confirmed.json())["native_runtime"]["writer_available"])
      body = fixture.body()
      response = await client.post("/api/control_advisor/apply-requests", json=body, headers=self.headers(client))
      self.assertEqual(202, response.status)
      for _ in range(200):
        status = await (await client.get("/api/control_advisor/apply-requests/" + body["request_id"], headers=self.headers(client))).json()
        if status["request"]["state"] == "stored":
          break
        await asyncio.sleep(.01)
      self.assertEqual("stored", status["request"]["state"])
      self.assertEqual("vehicle", status["request"]["scope"])
      self.assertFalse(status["request"]["consumer_observed"])
      self.assertEqual(b"20", fixture.params.values["PathOffset"])
      self.assertEqual(1, len(fixture.params.calls))
      for _ in range(100):
        if controller.analysis_cuts:
          break
        await asyncio.sleep(.01)
      self.assertEqual(len(controller.analysis_cuts), 1)
      self.assertEqual(controller.analysis_cuts[0]["receipt"]["stored_mono_ns"], fixture.runtime.request_receipt(body["request_id"])["stored_mono_ns"])
      repeated = await client.post("/api/control_advisor/apply-requests", json=body, headers=self.headers(client))
      self.assertEqual(202, repeated.status)
      self.assertEqual(1, len(fixture.params.calls))
      backup = await client.post(prefix + "backup", json={}, headers=self.headers(client))
      self.assertEqual(0, (await backup.json())["backup"]["integer_settings"]["PathOffset"])
      restore_body = {"request_id": body["request_id"], "restore_id": "native-http-restore"}
      restored = await client.post(prefix + "restore", json=restore_body, headers=self.headers(client))
      self.assertEqual(200, restored.status)
      self.assertFalse((await restored.json())["restore"]["consumer_observed"])
      self.assertEqual(b"0", fixture.params.values["PathOffset"])
      retried = await client.post(prefix + "restore", json=restore_body, headers=self.headers(client))
      self.assertEqual(200, retried.status)
      self.assertEqual(2, len(fixture.params.calls))
      self.assertEqual(len(controller.analysis_cuts), 1)  # Restore never starts a new recommendation range.
      second_body = {**fixture.body(), "request_id": "native-second-request"}
      submitted = await client.post("/api/control_advisor/apply-requests", json=second_body, headers=self.headers(client))
      self.assertEqual(202, submitted.status)
      for _ in range(200):
        if fixture.params.values["PathOffset"] == b"20":
          break
        await asyncio.sleep(.01)
      self.assertEqual(b"20", fixture.params.values["PathOffset"])
      fixture.params.values.update(IsOffroad=b"1", IsOnroad=b"0")
      fixture.status["state"] = fixture.binding["state"] = "stopped"
      fixture.binding["settings_observation_fresh"] = False
      offroad = await client.post(prefix + "restore", json={"request_id": second_body["request_id"], "restore_id": "native-offroad-restore", "mode": "offroad"}, headers=self.headers(client))
      self.assertEqual(200, offroad.status)
      restored_offroad = (await offroad.json())["restore"]
      self.assertEqual("restore_stored_awaiting_restart", restored_offroad["reason"])
      self.assertFalse(restored_offroad["consumer_observed"])
      self.assertEqual(b"0", fixture.params.values["PathOffset"])
      self.assertEqual(4, len(fixture.params.calls))
    finally:
      await client.close()

  async def test_cancelled_native_startup_closes_a_completed_owned_constructor(self):
    from openpilot.selfdrive.carrot.control_advisor import native_runtime as native
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Runtime:
      def __init__(self, *args, **kwargs):
        entered.set()
        if not release.wait(3):
          raise TimeoutError("native_fixture_wait")
      def close(self):
        closed.set()
    class Access:
      def authorized(self, token, origin):
        return False
      def close(self):
        pass
    with TemporaryDirectory() as temporary, patch.object(native, "NativeRuntime", Runtime):
      app = web.Application()
      feature.register(app, controller=FakeController(), token=TOKEN, runtime_config={"runtime_kind": "native_comma", "paths": {},
        "capabilities": {"collection_ready": True, "analysis_configured": False}, "blockers": {}}, access_sessions=Access(),
        request_root=str(Path(temporary) / "requests"), auto_output_root="", report_directory="", export_root="")
      client = TestClient(TestServer(app))
      startup = asyncio.create_task(client.start_server())
      try:
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        startup.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
          await startup
        self.assertTrue(closed.is_set())
      finally:
        release.set()
        await client.close()


if __name__ == "__main__":
  unittest.main()
