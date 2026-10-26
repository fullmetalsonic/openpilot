"""Fixed policy goals and child-only session selection; no vehicle mutations."""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.goals import (
  FMS_PRESET_RAW, FMS_PRESET_SOURCE_SHA256, PROFILE_ENV, OBJECTIVES_ENV,
  goal_configuration, goal_options, preset_reference, validate_preset_reference)
from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
from openpilot.selfdrive.carrot.control_advisor.session import SessionController, SessionConflict
from openpilot.selfdrive.carrot.control_advisor.tests.test_session import FakeProcess


ENV = {PROFILE_ENV: BUILTIN_PROFILE_PATH, OBJECTIVES_ENV: BUILTIN_OBJECTIVES_PATH}


class GoalTests(unittest.TestCase):
  def test_exact_builtin_pair_only_and_pure_copy_results(self):
    with patch.object(Path, "read_text", side_effect=AssertionError("unexpected file read")), patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected file read")):
      options = goal_options(ENV)
      self.assertEqual([item["id"] for item in options],
                       ["tracking", "fms_accel_defaults_v1", "torque_pid_experiment_v1", "fms_policy_defaults_v1"])
      configured = goal_configuration("fms_accel_defaults_v1", ENV)
      self.assertEqual(configured["selection"], options[1])
      self.assertEqual(validate_preset_reference(preset_reference()), preset_reference())
    for environment in ({}, {PROFILE_ENV: BUILTIN_PROFILE_PATH}, {**ENV, PROFILE_ENV: str(Path(BUILTIN_PROFILE_PATH).with_name("custom.json"))},
                        {**ENV, OBJECTIVES_ENV: str(Path(BUILTIN_OBJECTIVES_PATH).with_name("custom.json"))}):
      self.assertEqual(goal_options(environment), [])
      with self.assertRaisesRegex(ValueError, "builtin_configuration"):
        goal_configuration("tracking", environment)
    for value in ("../other", "custom", "", True, None):
      with self.assertRaisesRegex(ValueError, "invalid_analysis_goal"):
        goal_configuration(value, ENV)
    options[1]["settings_raw"]["CruiseMaxVals0"] = 0
    self.assertEqual(goal_options(ENV)[1]["settings_raw"], FMS_PRESET_RAW)
    self.assertEqual(set(options[0]), {"id", "label", "version", "description", "settings_raw", "source_sha256"})

  def test_fixed_preset_exact_defaults_source_hash_and_profile(self):
    root = Path(__file__).resolve().parents[5]
    self.assertEqual(hashlib.sha256((root / "openpilot/common/params_keys.h").read_bytes()).hexdigest(), FMS_PRESET_SOURCE_SHA256)
    self.assertEqual(list(FMS_PRESET_RAW.values()), [160, 200, 160, 130, 110, 95, 80])
    selected = goal_configuration("fms_accel_defaults_v1", ENV)
    profile = json.loads(Path(selected["profile_path"]).read_text())
    original = json.loads(Path(BUILTIN_PROFILE_PATH).read_text())
    for key in original:
      if key not in {"profile_id", "guard_tolerances"}:
        self.assertEqual(profile[key], original[key])
    self.assertEqual(profile["method_options"]["LateralTorqueAccelFactor"], {"offset_policy": "fit_intercept"})
    objectives = json.loads(Path(selected["objectives_path"]).read_text())
    self.assertEqual(len(objectives["objectives"]), 10)
    self.assertEqual(objectives["sampling"], {"version": "native_static_response_5hz_v1", "period_ns": 200000000})
    ids = set()
    for key in FMS_PRESET_RAW:
      objective = objectives["objectives"][key]
      self.assertEqual(objective["kind"], "policy_reference")
      self.assertEqual(validate_preset_reference(objective["reference"]), preset_reference())
      self.assertEqual(profile["guard_tolerances"][key], {"mode": "baseline_bounded"})
      ids.add(objective["id"])
    self.assertEqual(len(ids), 7)
    for changes in ({"version": True}, {"source_sha256": "0" * 64}, {"settings_raw": {}}, {"preset_id": "other"}, {"extra": 1}):
      with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "invalid_fms_acceleration_preset"):
        validate_preset_reference({**preset_reference(), **changes})


class GoalSessionTests(unittest.IsolatedAsyncioTestCase):
  async def make_controller(self, *, environment=None, fail=False):
    self.spawned, self.processes = [], []
    async def spawn(*args, **kwargs):
      self.spawned.append((args, kwargs))
      if fail:
        raise OSError("synthetic spawn failure")
      process = FakeProcess()
      self.processes.append(process)
      return process
    async def stopper(process):
      process.finish(-9)
    controller = SessionController(spawn=spawn, stopper=stopper, graceful_timeout=.03,
                                   live_environment=dict(ENV if environment is None else environment))
    self.addAsyncCleanup(controller.close)
    return controller

  async def test_child_selection_parent_environment_and_idempotence(self):
    controller = await self.make_controller()
    baseline_environment = deepcopy(controller._live_environment)
    with patch.dict(os.environ, {"CARROT_CONTROL_ADVISOR_TOKEN": "not-for-child"}):
      original_process_environment = dict(os.environ)
      first = await controller.start(mode="live", interval_seconds=180, request_id="goal", goal_id="fms_accel_defaults_v1")
      self.assertEqual(dict(os.environ), original_process_environment)
    selection = goal_configuration("fms_accel_defaults_v1", ENV)
    self.assertEqual(first["analysis_goal"], selection["selection"])
    child = self.spawned[0][1]["env"]
    self.assertEqual(child[PROFILE_ENV], selection["profile_path"])
    self.assertEqual(child[OBJECTIVES_ENV], selection["objectives_path"])
    self.assertNotIn("CARROT_CONTROL_ADVISOR_TOKEN", child)
    self.assertEqual(controller._live_environment, baseline_environment)
    repeated = await controller.start(mode="live", interval_seconds=180, request_id="goal", goal_id="fms_accel_defaults_v1")
    self.assertEqual(repeated["session_id"], first["session_id"])
    self.assertEqual(len(self.spawned), 1)
    with self.assertRaisesRegex(SessionConflict, "request_id_conflict"):
      await controller.start(mode="live", interval_seconds=180, request_id="goal", goal_id="tracking")
    self.processes[0].emit(first, analysis_goal={"id": "child-forged"})
    for _ in range(100):
      if controller.status()["state"] == "running":
        break
      await asyncio.sleep(.001)
    self.assertEqual(controller.status()["analysis_goal"], selection["selection"])
    stopped = await controller.stop(first["session_id"])
    self.assertEqual(stopped["analysis_goal"], selection["selection"])
    legacy = await controller.start(mode="live", interval_seconds=180, request_id="legacy")
    self.assertNotIn("analysis_goal", legacy)
    self.assertEqual(self.spawned[1][1]["env"][PROFILE_ENV], BUILTIN_PROFILE_PATH)

  async def test_invalid_custom_and_nonlive_selection_never_create_worker(self):
    controller = await self.make_controller(environment={**ENV, PROFILE_ENV: str(Path(BUILTIN_PROFILE_PATH).with_name("custom.json"))})
    with self.assertRaisesRegex(ValueError, "builtin_configuration"):
      await controller.start(mode="live", interval_seconds=180, request_id="custom", goal_id="fms_accel_defaults_v1")
    self.assertEqual(controller.status()["state"], "idle")
    self.assertEqual(self.spawned, [])
    with self.assertRaisesRegex(ValueError, "requires_live_mode"):
      await controller.start(mode="synthetic", interval_seconds=180, request_id="synthetic", goal_id="tracking")
    with self.assertRaisesRegex(ValueError, "invalid_analysis_goal"):
      await controller.start(mode="live", interval_seconds=180, request_id="invalid", goal_id="../path")
    self.assertEqual(self.spawned, [])
    # The existing no-goal custom path remains available.
    legacy = await controller.start(mode="live", interval_seconds=180, request_id="custom-legacy")
    self.assertNotIn("analysis_goal", legacy)

  async def test_start_failure_and_cancellation_keep_owned_cleanup(self):
    controller = await self.make_controller(fail=True)
    failed = await controller.start(mode="live", interval_seconds=180, request_id="failed", goal_id="tracking")
    self.assertEqual(failed["state"], "failed")
    self.assertEqual(failed["analysis_goal"]["id"], "tracking")
    entered, release = asyncio.Event(), asyncio.Event()
    process = FakeProcess()
    async def delayed_spawn(*args, **kwargs):
      entered.set()
      await release.wait()
      return process
    controller = SessionController(spawn=delayed_spawn, graceful_timeout=.03, live_environment=ENV)
    self.addAsyncCleanup(controller.close)
    task = asyncio.create_task(controller.start(mode="live", interval_seconds=180, request_id="cancel", goal_id="fms_accel_defaults_v1"))
    await entered.wait()
    task.cancel()
    release.set()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertIsNotNone(process.returncode)
    self.assertEqual(controller.status()["state"], "stopped")
    self.assertEqual(controller.status()["analysis_goal"]["id"], "fms_accel_defaults_v1")


if __name__ == "__main__":
  unittest.main()
