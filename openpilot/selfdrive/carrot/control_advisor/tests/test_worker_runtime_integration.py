"""Worker-only placement metadata stays separate from source/diagnostic state."""

from copy import deepcopy
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from openpilot.selfdrive.carrot.control_advisor import worker
from openpilot.selfdrive.carrot.control_advisor.live import LiveSession
from openpilot.selfdrive.carrot.control_advisor.session import SessionController
from openpilot.selfdrive.carrot.control_advisor.tests.test_session import FakeProcess


def runtime_status(state="applied"):
  return {"schema_version": 1, "state": state,
          "reason": None if state == "applied" else "native_worker_policy_incomplete",
          "requested": {"cpus": [0], "scheduler": "SCHED_OTHER", "nice": 15},
          "effective": {"cpus": [0] if state == "applied" else [0, 1], "scheduler": 0, "nice": 15},
          "errors": [] if state == "applied" else ["affinity_set_failed"]}


class RuntimeWorkerTests(unittest.TestCase):
  @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "sched_getaffinity"), "Linux process scheduling required")
  def test_actual_isolated_worker_and_grandchild_inherit_without_changing_test_parent(self):
    before = (sorted(os.sched_getaffinity(0)), os.sched_getscheduler(0), os.getpriority(os.PRIO_PROCESS, 0))
    if 0 not in before[0]:
      self.skipTest("CPU0 is not available to this isolated test process")
    child = '''
import json, os, subprocess, sys
from unittest.mock import patch
from openpilot.selfdrive.carrot.control_advisor.worker_runtime import configure_worker_runtime
with patch('openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime', return_value=True):
    result = configure_worker_runtime('live')
code = "import os,json; print(json.dumps({'cpus':sorted(os.sched_getaffinity(0)),'scheduler':os.sched_getscheduler(0),'nice':os.getpriority(os.PRIO_PROCESS,0)}))"
grandchild = json.loads(subprocess.check_output([sys.executable, '-c', code], timeout=5))
print(json.dumps({'runtime':result,'grandchild':grandchild}))
'''
    result = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=10, check=False)
    after = (sorted(os.sched_getaffinity(0)), os.sched_getscheduler(0), os.getpriority(os.PRIO_PROCESS, 0))
    self.assertEqual(before, after)
    self.assertEqual(0, result.returncode, result.stderr)
    payload = json.loads(result.stdout)
    self.assertEqual("applied", payload["runtime"]["state"])
    self.assertEqual({"cpus": [0], "scheduler": os.SCHED_OTHER, "nice": max(15, before[2])}, payload["grandchild"])
    self.assertEqual(payload["runtime"]["effective"], payload["grandchild"])

  def test_placement_precedes_session_recording_threads_transport_and_failure_keeps_source_state(self):
    for placement in (runtime_status(), runtime_status("degraded")):
      with self.subTest(state=placement["state"]):
        order, statuses = [], []
        recorder = MagicMock()
        recorder.start.return_value = recorder
        recorder.poll_settings.return_value = []
        recorder.snapshot.return_value = recorder.close.return_value = {"consumer_history": {"state": "disabled"}}

        def make_recording(*args, **kwargs):
          order.append("recording")
          return recorder

        def make_session(*args, **kwargs):
          order.append("session")
          return LiveSession(*args, **kwargs)

        def unavailable():
          order.append("transport")
          raise RuntimeError("native unavailable")

        with patch.object(worker, "configure_worker_runtime", side_effect=lambda mode: (order.append("runtime"), deepcopy(placement))[1]), \
             patch.object(worker, "LiveSession", side_effect=make_session), \
             patch.object(worker, "RecordingSession", side_effect=make_recording), \
             patch.object(worker.threading, "Thread", side_effect=lambda **kwargs: order.append("thread") or MagicMock()), \
             patch.object(worker, "NativeTransport", side_effect=unavailable), \
             patch.object(worker, "_recording_identity_provider", return_value=None), \
             patch.object(worker, "_emit", side_effect=statuses.append):
          self.assertEqual(0, worker.run(mode="live", session_id="placement", interval_seconds=300))
        self.assertEqual(["runtime", "session", "recording", "thread", "transport"], order)
        self.assertEqual("unavailable", statuses[-1]["state"])
        self.assertEqual(placement, statuses[-1]["metadata"]["worker_runtime"])


class RuntimeProtocolTests(unittest.IsolatedAsyncioTestCase):
  async def fixture(self, mode="live"):
    async def spawn(*args, **kwargs):
      return FakeProcess()
    controller = SessionController(spawn=spawn, heartbeat_timeout=30)
    self.addAsyncCleanup(controller.close)
    initial = await controller.start(mode=mode, interval_seconds=300, request_id="runtime-test")
    packet = LiveSession(initial["session_id"], source_mode=mode).snapshot()
    return controller, packet

  async def test_applied_and_degraded_are_propagated_without_changing_diagnostic_state(self):
    controller, packet = await self.fixture()
    for runtime in (runtime_status(), runtime_status("degraded")):
      packet["metadata"]["worker_runtime"] = runtime
      controller._accept(packet, controller._generation)
      result = controller.status()
      self.assertEqual(runtime, result["metadata"]["worker_runtime"])
      self.assertEqual("waiting_for_data", result["state"])
      self.assertFalse(result["apply_enabled"])
    packet["metadata"]["worker_runtime"]["effective"]["cpus"].append(7)
    self.assertEqual([0, 1], controller.status()["metadata"]["worker_runtime"]["effective"]["cpus"])

  async def test_forged_or_synthetic_native_claims_rejected_legacy_metadata_still_accepted(self):
    controller, packet = await self.fixture("synthetic")
    controller._accept(packet, controller._generation)
    packet["metadata"]["worker_runtime"] = runtime_status()
    with self.assertRaises(ValueError):
      controller._accept(packet, controller._generation)
    packet["metadata"]["worker_runtime"].update(state="not_applicable", reason="synthetic_mode",
      effective={"cpus": None, "scheduler": None, "nice": None})
    controller._accept(packet, controller._generation)
    packet["metadata"]["worker_runtime"].update(state="applied", reason=None)
    with self.assertRaises(ValueError):
      controller._accept(packet, controller._generation)


if __name__ == "__main__":
  unittest.main()
