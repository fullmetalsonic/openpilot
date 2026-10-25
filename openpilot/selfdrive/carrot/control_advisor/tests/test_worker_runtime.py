"""Unit tests for control advisor worker runtime execution policy."""

import os
import unittest
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import MagicMock, patch

from openpilot.selfdrive.carrot.control_advisor.worker_runtime import (
  REQUESTED_POLICY,
  configure_worker_runtime,
  validate_worker_runtime,
)


class TestWorkerRuntime(unittest.TestCase):
  def test_scheduler_and_nice_failures_keep_actual_readback_and_do_not_abort(self):
    for failed, code in (("sched_setscheduler", "scheduler_set_failed"),
                         ("sched_getscheduler", "scheduler_get_failed"),
                         ("setpriority", "nice_set_failed")):
      with self.subTest(syscall=failed), ExitStack() as stack:
        stack.enter_context(patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime", return_value=True))
        stack.enter_context(patch.object(os, "SCHED_OTHER", 0, create=True))
        stack.enter_context(patch.object(os, "PRIO_PROCESS", 0, create=True))
        calls = {name: stack.enter_context(patch.object(os, name, create=True)) for name in
                 ("sched_setaffinity", "sched_getaffinity", "sched_setscheduler", "sched_getscheduler", "sched_param", "getpriority", "setpriority")}
        calls["sched_getaffinity"].return_value = {0}
        calls["sched_getscheduler"].return_value = 0
        calls["getpriority"].side_effect = [0, 0 if failed == "setpriority" else 15]
        calls[failed].side_effect = OSError("private error detail")
        result = configure_worker_runtime("live")
        self.assertEqual("degraded", result["state"])
        self.assertEqual([code], result["errors"])
        self.assertEqual(0 if failed == "setpriority" else 15, result["effective"]["nice"])
        if failed == "sched_getscheduler":
          self.assertIsNone(result["effective"]["scheduler"])
        else:
          self.assertEqual(0, result["effective"]["scheduler"])
        calls["setpriority"].assert_called_once()
        validate_worker_runtime(result)

  def test_validator_rejects_malformed_and_unbounded_metadata(self):
    base = {"schema_version": 1, "state": "applied", "reason": None,
            "requested": {"cpus": [0], "scheduler": "SCHED_OTHER", "nice": 15},
            "effective": {"cpus": [0], "scheduler": 0, "nice": 15}, "errors": []}
    mutations = [lambda p: p.update(state=[]), lambda p: p.update(reason={}),
                 lambda p: p["requested"].update(cpus=[False]), lambda p: p["effective"].update(nice=True),
                 lambda p: p["effective"].update(scheduler=2**80), lambda p: p.update(extra=True),
                 lambda p: p.update(state="degraded", reason="native_worker_policy_incomplete", errors=["private message"]),
                 lambda p: p["effective"].update(cpus=[0, 0])]
    for mutate in mutations:
      payload = deepcopy(base)
      mutate(payload)
      with self.assertRaises(ValueError):
        validate_worker_runtime(payload)

  def test_invalid_mode_raises(self):
    for invalid in ("invalid", "", None, 123, ["live"]):
      with self.assertRaises(ValueError):
        configure_worker_runtime(invalid)  # type: ignore

  @patch("os.sched_setaffinity", create=True)
  @patch("os.sched_getaffinity", create=True)
  @patch("os.sched_setscheduler", create=True)
  @patch("os.sched_getscheduler", create=True)
  @patch("os.setpriority", create=True)
  @patch("os.getpriority", create=True)
  def test_synthetic_mode_zero_os_calls(self, *os_mocks):
    result = configure_worker_runtime("synthetic")
    self.assertEqual(result["schema_version"], 1)
    self.assertEqual(result["state"], "not_applicable")
    self.assertEqual(result["reason"], "synthetic_mode")
    self.assertEqual(result["requested"], REQUESTED_POLICY)
    self.assertEqual(result["effective"], {"cpus": None, "scheduler": None, "nice": None})
    self.assertEqual(result["errors"], [])
    for mock_fn in os_mocks:
      mock_fn.assert_not_called()
    canonical = validate_worker_runtime(result)
    self.assertEqual(canonical, result)

  @patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime", return_value=False)
  @patch("os.sched_setaffinity", create=True)
  @patch("os.sched_getaffinity", create=True)
  @patch("os.sched_setscheduler", create=True)
  @patch("os.sched_getscheduler", create=True)
  @patch("os.setpriority", create=True)
  @patch("os.getpriority", create=True)
  def test_non_native_live_zero_os_calls(self, *os_mocks):
    result = configure_worker_runtime("live")
    self.assertEqual(result["state"], "not_applicable")
    self.assertEqual(result["reason"], "non_native_runtime")
    self.assertEqual(result["requested"], REQUESTED_POLICY)
    self.assertEqual(result["effective"], {"cpus": None, "scheduler": None, "nice": None})
    self.assertEqual(result["errors"], [])
    for mock_fn in os_mocks[:-1]:  # exclude _native_runtime mock
      mock_fn.assert_not_called()
    canonical = validate_worker_runtime(result)
    self.assertEqual(canonical, result)

  @patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime", side_effect=RuntimeError("probe_error"))
  @patch("os.sched_setaffinity", create=True)
  def test_native_detection_failure_degraded(self, mock_affinity, _mock_native):
    result = configure_worker_runtime("live")
    self.assertEqual(result["state"], "degraded")
    self.assertEqual(result["reason"], "native_worker_policy_incomplete")
    self.assertEqual(result["errors"], ["runtime_detection_failed"])
    self.assertEqual(result["effective"], {"cpus": None, "scheduler": None, "nice": None})
    mock_affinity.assert_not_called()
    canonical = validate_worker_runtime(result)
    self.assertEqual(canonical, result)

  @patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime", return_value=True)
  @patch("os.sched_setaffinity", create=True)
  @patch("os.sched_getaffinity", create=True, return_value={0})
  @patch("os.sched_setscheduler", create=True)
  @patch("os.sched_getscheduler", create=True, return_value=0)
  @patch("os.sched_param", create=True, return_value=MagicMock())
  @patch("os.setpriority", create=True)
  @patch("os.getpriority", create=True, side_effect=[0, 15])
  @patch.object(os, "SCHED_OTHER", 0, create=True)
  @patch.object(os, "PRIO_PROCESS", 0, create=True)
  def test_native_live_success_default_and_pid0_setters(
    self, mock_getprio, mock_setprio, mock_param,
    mock_getsched, mock_setsched, mock_getaff, mock_setaff, _mock_native
  ):
    result = configure_worker_runtime("live")
    self.assertEqual(result["state"], "applied")
    self.assertIsNone(result["reason"])
    self.assertEqual(result["requested"], REQUESTED_POLICY)
    self.assertEqual(result["effective"], {"cpus": [0], "scheduler": 0, "nice": 15})
    self.assertEqual(result["errors"], [])

    # Verify all setters target calling thread/pid 0
    mock_setaff.assert_called_once_with(0, {0})
    mock_setsched.assert_called_once_with(0, 0, mock_param.return_value)
    mock_setprio.assert_called_once_with(0, 0, 15)

    canonical = validate_worker_runtime(result)
    self.assertEqual(canonical["state"], "applied")

  @patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime", return_value=True)
  @patch("os.sched_setaffinity", create=True)
  @patch("os.sched_getaffinity", create=True, return_value={0})
  @patch("os.sched_setscheduler", create=True)
  @patch("os.sched_getscheduler", create=True, return_value=0)
  @patch("os.sched_param", create=True, return_value=MagicMock())
  @patch("os.setpriority", create=True)
  @patch("os.getpriority", create=True, return_value=19)
  @patch.object(os, "SCHED_OTHER", 0, create=True)
  @patch.object(os, "PRIO_PROCESS", 0, create=True)
  def test_nice_19_preserved_never_raise_priority(
    self, mock_getprio, mock_setprio, _mock_param,
    _getsched, _setsched, _getaff, _setaff, _mock_native
  ):
    result = configure_worker_runtime("live")
    self.assertEqual(result["state"], "applied")
    self.assertIsNone(result["reason"])
    self.assertEqual(result["effective"], {"cpus": [0], "scheduler": 0, "nice": 19})
    self.assertEqual(result["errors"], [])
    # Priority was already lower than 15; setter must not have been called
    mock_setprio.assert_not_called()
    canonical = validate_worker_runtime(result)
    self.assertEqual(canonical["effective"]["nice"], 19)

  @patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime", return_value=True)
  @patch("os.sched_setaffinity", create=True, side_effect=OSError("EPERM"))
  @patch("os.sched_getaffinity", create=True, return_value={0, 1})
  @patch("os.sched_setscheduler", create=True)
  @patch("os.sched_getscheduler", create=True, return_value=0)
  @patch("os.sched_param", create=True, return_value=MagicMock())
  @patch("os.setpriority", create=True)
  @patch("os.getpriority", create=True, side_effect=[0, 15])
  @patch.object(os, "SCHED_OTHER", 0, create=True)
  @patch.object(os, "PRIO_PROCESS", 0, create=True)
  def test_affinity_set_failure_degraded_and_independent_remaining_calls(
    self, mock_getprio, mock_setprio, _mock_param,
    _getsched, mock_setsched, _getaff, _setaff, _mock_native
  ):
    result = configure_worker_runtime("live")
    self.assertEqual(result["state"], "degraded")
    self.assertEqual(result["reason"], "native_worker_policy_incomplete")
    self.assertEqual(result["errors"], ["affinity_set_failed"])
    self.assertEqual(result["effective"], {"cpus": [0, 1], "scheduler": 0, "nice": 15})
    # Remaining independent setters still executed
    mock_setsched.assert_called_once()
    mock_setprio.assert_called_once()

  @patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime", return_value=True)
  @patch("os.sched_setaffinity", create=True)
  @patch("os.sched_getaffinity", create=True, side_effect=OSError("EACCES"))
  @patch("os.sched_setscheduler", create=True)
  @patch("os.sched_getscheduler", create=True, return_value=0)
  @patch("os.setpriority", create=True)
  @patch("os.getpriority", create=True, side_effect=[0, 15])
  @patch.object(os, "SCHED_OTHER", 0, create=True)
  @patch.object(os, "PRIO_PROCESS", 0, create=True)
  def test_null_getter_failures_reported(self, *mocks):
    result = configure_worker_runtime("live")
    self.assertEqual(result["state"], "degraded")
    self.assertIsNone(result["effective"]["cpus"])
    self.assertIn("affinity_get_failed", result["errors"])

  @patch("openpilot.selfdrive.carrot.control_advisor.consumer_observer._native_runtime", return_value=True)
  @patch("os.sched_setaffinity", create=True)
  @patch("os.sched_getaffinity", create=True, return_value={0})
  @patch("os.sched_setscheduler", create=True)
  @patch("os.sched_getscheduler", create=True, return_value=0)
  @patch("os.setpriority", create=True)
  @patch("os.getpriority", create=True, side_effect=OSError("EPERM"))
  @patch.object(os, "SCHED_OTHER", 0, create=True)
  @patch.object(os, "PRIO_PROCESS", 0, create=True)
  def test_nice_get_failure_skips_adjustment_and_reports_degraded(
    self, _mock_getprio, mock_setprio, _getsched, _setsched, _getaff, _setaff, _mock_native
  ):
    result = configure_worker_runtime("live")
    self.assertEqual(result["state"], "degraded")
    self.assertEqual(result["errors"], ["nice_get_failed"])
    self.assertIsNone(result["effective"]["nice"])
    # Do not guess an unsafe adjustment
    mock_setprio.assert_not_called()

  def test_validate_worker_runtime_detached_and_canonical(self):
    raw = {
      "schema_version": 1,
      "state": "applied",
      "reason": None,
      "requested": {"cpus": [0], "scheduler": "SCHED_OTHER", "nice": 15},
      "effective": {"cpus": [0], "scheduler": 0, "nice": 15},
      "errors": [],
    }
    validated = validate_worker_runtime(raw)
    self.assertEqual(validated, raw)
    # Ensure mutations do not bleed
    validated["effective"]["cpus"].append(1)
    self.assertEqual(raw["effective"]["cpus"], [0])

  def test_validate_worker_runtime_rejects_forged_applied(self):
    base = {
      "schema_version": 1,
      "state": "applied",
      "reason": None,
      "requested": {"cpus": [0], "scheduler": "SCHED_OTHER", "nice": 15},
      "effective": {"cpus": [0], "scheduler": 0, "nice": 15},
      "errors": [],
    }
    # Forged with errors
    with self.assertRaises(ValueError):
      validate_worker_runtime({**base, "errors": ["affinity_set_failed"]})
    # Forged with wrong cpu
    with self.assertRaises(ValueError):
      validate_worker_runtime({**base, "effective": {**base["effective"], "cpus": [0, 1]}})
    # Forged with non-zero scheduler
    with self.assertRaises(ValueError):
      validate_worker_runtime({**base, "effective": {**base["effective"], "scheduler": 1}})
    # Forged with insufficient nice
    with self.assertRaises(ValueError):
      validate_worker_runtime({**base, "effective": {**base["effective"], "nice": 14}})
    # Forged with null effective nice
    with self.assertRaises(ValueError):
      validate_worker_runtime({**base, "effective": {**base["effective"], "nice": None}})
    # Forged with non-null reason
    with self.assertRaises(ValueError):
      validate_worker_runtime({**base, "reason": "synthetic_mode"})
