"""Deterministic unit tests for native analysis defaults and pure config resolution.

Validates builtin profile and objectives JSON schema adherence, resolution behaviors
under native and PC environments, explicit override precedence, blocker isolation,
and lack of filesystem/Params/environment mutation.
"""

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import runtime_config
from openpilot.selfdrive.carrot.control_advisor.runtime_config import (
    BUILTIN_OBJECTIVES_PATH,
    BUILTIN_PROFILE_PATH,
    _canonical_path_str,
    resolve_runtime_config,
)


class FakeParams:
  def __init__(self, path: str = "/data/params"):
    self._path = path
    self.put_called = False

  def get_param_path(self, key: str = "") -> str:
    return self._path

  def put(self, key: str, value: Any) -> None:
    self.put_called = True
    raise AssertionError("Params.put must never be called during runtime config resolution")


class TestRuntimeDefaults(unittest.TestCase):
  def _assert_finite_numbers(self, obj: Any) -> None:
    if isinstance(obj, dict):
      for v in obj.values():
        self._assert_finite_numbers(v)
    elif isinstance(obj, list):
      for item in obj:
        self._assert_finite_numbers(item)
    elif isinstance(obj, (int, float)):
      self.assertTrue(math.isfinite(obj))

  def test_builtin_files_strict_finite_json_and_exact_structure(self):
    self.assertTrue(Path(BUILTIN_PROFILE_PATH).is_file())
    self.assertTrue(Path(BUILTIN_OBJECTIVES_PATH).is_file())

    prof_data = json.loads(Path(BUILTIN_PROFILE_PATH).read_text(encoding="utf-8"))
    obj_data = json.loads(Path(BUILTIN_OBJECTIVES_PATH).read_text(encoding="utf-8"))

    self._assert_finite_numbers(prof_data)
    self._assert_finite_numbers(obj_data)

    expected_prof_keys = {
        "profile_id", "version", "status", "scope", "allowed_source_kinds",
        "max_rows", "max_blocks", "max_candidates_per_key", "max_evaluations",
        "max_lag_ns", "dependency_margin_ns", "min_blocks_per_split",
        "min_samples_per_split", "minimum_improvement", "guard_tolerances",
        "raw_step_policy", "per_key_change_limits", "change_limit_policy",
        "device_qualified", "method_options",
    }
    self.assertEqual(set(prof_data.keys()), expected_prof_keys)
    self.assertEqual(prof_data["profile_id"], "native-direct-calibration-host-unqualified-v1")
    self.assertEqual(prof_data["version"], 1)
    self.assertEqual(prof_data["status"], "not_validated")
    self.assertEqual(prof_data["scope"], "host_unqualified")
    self.assertEqual(prof_data["allowed_source_kinds"], ["recorded"])
    self.assertEqual(prof_data["max_rows"], 100000)
    self.assertEqual(prof_data["max_blocks"], 64)
    self.assertEqual(prof_data["max_candidates_per_key"], 8)
    self.assertEqual(prof_data["max_evaluations"], 192)
    self.assertEqual(prof_data["max_lag_ns"], 2000000000)
    self.assertEqual(prof_data["dependency_margin_ns"], 100000000)
    # The native pack has one disjoint block in each chronological split.
    self.assertEqual(prof_data["min_blocks_per_split"], 1)
    self.assertEqual(prof_data["min_samples_per_split"], 30)
    self.assertEqual(prof_data["minimum_improvement"], 0)
    self.assertEqual(prof_data["raw_step_policy"], "ui_step")
    self.assertIsNone(prof_data["per_key_change_limits"])
    self.assertEqual(prof_data["change_limit_policy"], "manual_experiment")
    self.assertFalse(prof_data["device_qualified"])

    expected_keys = {"LateralTorqueAccelFactor", "SteerRatioRate", "CustomSR"}
    self.assertEqual(set(prof_data["guard_tolerances"].keys()), expected_keys)
    for k in expected_keys:
      self.assertEqual(prof_data["guard_tolerances"][k], {"mode": "baseline_bounded"})

    self.assertEqual(
        prof_data["method_options"],
        {"LateralTorqueAccelFactor": {"offset_policy": "fit_intercept"}},
    )

    expected_obj_keys = {"schema_version", "analysis_profile", "objectives", "sampling"}
    self.assertEqual(set(obj_data.keys()), expected_obj_keys)
    self.assertEqual(obj_data["schema_version"], 1)
    self.assertEqual(obj_data["sampling"], {"version": "native_static_response_5hz_v1", "period_ns": 200000000})
    self.assertEqual(
        obj_data["analysis_profile"],
        {
            "profile_id": "native-control-mask-host-unqualified-v1",
            "pre_margin_ns": 100000000,
            "recovery_ns": 2000000000,
            "max_gap_ns": 250000000,
            "dependency_ns": 100000000,
            "max_samples": 100000,
        },
    )
    self.assertEqual(set(obj_data["objectives"].keys()), expected_keys)
    self.assertEqual(
        obj_data["objectives"]["LateralTorqueAccelFactor"],
        {
            "id": "native-measured-lateral-accel-v1",
            "kind": "tracking",
            "reference": {
                "type": "recorded_signal",
                "signal": "lateral_accel_independent",
                "unit": "m/s^2",
            },
        },
    )
    for k in ("SteerRatioRate", "CustomSR"):
      self.assertEqual(
          obj_data["objectives"][k],
          {
              "id": "native-measured-curvature-v1",
              "kind": "geometry_reference",
              "reference": {
                  "type": "recorded_signal",
                  "signal": "curvature_independent",
                  "unit": "1/m",
              },
          },
      )

  def test_native_both_absent_selects_repo_defaults_no_mutation(self):
    fake_p = FakeParams("/data/params")
    before_env = dict(os.environ)
    with patch("os.mkdir", side_effect=AssertionError("mkdir forbidden")):
      res = resolve_runtime_config(
          environment={},
          native_comma=True,
          params=fake_p,
          log_root="/data/media/0/realdata",
      )

    self.assertEqual(res["paths"]["profile"], _canonical_path_str(BUILTIN_PROFILE_PATH))
    self.assertEqual(res["paths"]["objectives"], _canonical_path_str(BUILTIN_OBJECTIVES_PATH))
    self.assertTrue(res["capabilities"]["analysis_configured"])
    self.assertFalse(res["capabilities"]["apply_enabled"])
    self.assertFalse(res["capabilities"]["recommendations_enabled"])
    self.assertFalse(res["capabilities"]["device_qualified"])
    self.assertFalse(fake_p.put_called)
    self.assertEqual(dict(os.environ), before_env)
    self.assertEqual(res["blockers"]["analysis"], [])

  def test_explicit_valid_pair_retained(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      custom_prof = base / "custom_profile.json"
      custom_obj = base / "custom_objectives.json"
      custom_prof.write_text("{}", encoding="utf-8")
      custom_obj.write_text("{}", encoding="utf-8")

      env = {
          "CARROT_CONTROL_ADVISOR_AUTO_PROFILE": str(custom_prof),
          "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES": str(custom_obj),
      }
      res = resolve_runtime_config(
          environment=env,
          native_comma=True,
          params=FakeParams(),
          log_root="/data/media/0/realdata",
      )
      self.assertEqual(res["paths"]["profile"], _canonical_path_str(str(custom_prof)))
      self.assertEqual(res["paths"]["objectives"], _canonical_path_str(str(custom_obj)))
      self.assertTrue(res["capabilities"]["analysis_configured"])
      self.assertEqual(res["blockers"]["analysis"], [])

  def test_single_explicit_key_leaves_other_missing(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      prof = base / "prof.json"
      obj = base / "obj.json"
      prof.write_text("{}", encoding="utf-8")
      obj.write_text("{}", encoding="utf-8")

      res1 = resolve_runtime_config(
          environment={"CARROT_CONTROL_ADVISOR_AUTO_PROFILE": str(prof)},
          native_comma=True,
          params=FakeParams(),
          log_root="/data/media/0/realdata",
      )
      self.assertEqual(res1["paths"]["profile"], _canonical_path_str(str(prof)))
      self.assertIsNone(res1["paths"]["objectives"])
      self.assertIn("objectives_missing", res1["blockers"]["analysis"])
      self.assertFalse(res1["capabilities"]["analysis_configured"])

      res2 = resolve_runtime_config(
          environment={"CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES": str(obj)},
          native_comma=True,
          params=FakeParams(),
          log_root="/data/media/0/realdata",
      )
      self.assertEqual(res2["paths"]["objectives"], _canonical_path_str(str(obj)))
      self.assertIsNone(res2["paths"]["profile"])
      self.assertIn("profile_missing", res2["blockers"]["analysis"])
      self.assertFalse(res2["capabilities"]["analysis_configured"])

  def test_invalid_and_empty_explicit_values_never_fallback(self):
    invalid_cases = [
        ("", "profile_missing"),
        ("   ", "profile_missing"),
        (None, "profile_missing"),
        (12345, "profile_not_absolute"),
        ("relative/file.json", "profile_not_absolute"),
        ("/nonexistent/file.json", "profile_not_found"),
    ]
    for val, expected_err in invalid_cases:
      with self.subTest(val=val, expected_err=expected_err):
        env = {"CARROT_CONTROL_ADVISOR_AUTO_PROFILE": val}
        res = resolve_runtime_config(
            environment=env,
            native_comma=True,
            params=FakeParams(),
            log_root="/data/media/0/realdata",
        )
        self.assertNotEqual(res["paths"]["profile"], _canonical_path_str(BUILTIN_PROFILE_PATH))
        self.assertIsNone(res["paths"]["profile"])
        self.assertIsNone(res["paths"]["objectives"])
        self.assertIn(expected_err, res["blockers"]["analysis"])
        self.assertIn("objectives_missing", res["blockers"]["analysis"])
        self.assertFalse(res["capabilities"]["analysis_configured"])

  def test_pc_defaults_do_not_use_builtins(self):
    res = resolve_runtime_config(environment={}, native_comma=False)
    self.assertIsNone(res["paths"]["profile"])
    self.assertIsNone(res["paths"]["objectives"])
    self.assertFalse(res["capabilities"]["analysis_configured"])
    self.assertIn("profile_missing", res["blockers"]["analysis"])
    self.assertIn("objectives_missing", res["blockers"]["analysis"])

  def test_invalid_or_missing_builtin_blocks_analysis(self):
    fake_p = FakeParams("/data/params")
    with patch.object(runtime_config, "BUILTIN_PROFILE_PATH", "/nonexistent/builtin_profile.json"):
      res = resolve_runtime_config(
          environment={},
          native_comma=True,
          params=fake_p,
          log_root="/data/media/0/realdata",
      )
      self.assertIsNone(res["paths"]["profile"])
      self.assertIn("profile_not_found", res["blockers"]["analysis"])
      self.assertFalse(res["capabilities"]["analysis_configured"])
      self.assertFalse(res["capabilities"]["paths_valid"])

    with patch.object(runtime_config, "BUILTIN_OBJECTIVES_PATH", "/nonexistent/builtin_objectives.json"):
      res2 = resolve_runtime_config(
          environment={},
          native_comma=True,
          params=fake_p,
          log_root="/data/media/0/realdata",
      )
      self.assertIsNone(res2["paths"]["objectives"])
      self.assertIn("objectives_not_found", res2["blockers"]["analysis"])
      self.assertFalse(res2["capabilities"]["analysis_configured"])
      self.assertFalse(res2["capabilities"]["paths_valid"])

  def test_no_eager_native_dependency_import(self):
    program = """import builtins, json
original = builtins.__import__
def guard(name, *args, **kwargs):
  if name in {'openpilot.common.params', 'openpilot.system.hardware', 'openpilot.system.hardware.hw'}:
    raise AssertionError('eager native dependency: ' + name)
  return original(name, *args, **kwargs)
builtins.__import__ = guard
from openpilot.selfdrive.carrot.control_advisor.runtime_config import resolve_runtime_config
res = resolve_runtime_config(environment={}, native_comma=False)
print(json.dumps(res['capabilities']))
"""
    repo_root = Path(__file__).resolve().parents[5]
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    self.assertEqual(proc.returncode, 0, proc.stderr)
    caps = json.loads(proc.stdout)
    self.assertFalse(caps["native_runtime_detected"])


if __name__ == "__main__":
  unittest.main()
