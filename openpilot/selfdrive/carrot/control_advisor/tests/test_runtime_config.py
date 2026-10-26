"""Deterministic unit tests for pure runtime configuration resolver.

Asserts PC defaults, native path derivations, explicit overrides, component-specific
isolation of blockers, symlink policies, lack of filesystem/environment mutation,
and eager native library import absence.
"""

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH, resolve_runtime_config


class FakeParams:
  def __init__(self, path: str = "/data/params"):
    self._path = path
    self.put_called = False

  def get_param_path(self, key: str = "") -> str:
    return self._path

  def put(self, key: str, value: Any) -> None:
    self.put_called = True
    raise AssertionError("Params.put must never be called during runtime config resolution")


class BrokenParams:
  def get_param_path(self, key: str = "") -> str:
    raise RuntimeError("params backend unavailable")


def directory_link(target, link):
  try:
    link.symlink_to(target, target_is_directory=True)
  except OSError:
    if os.name != "nt":
      raise
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


class TestRuntimeConfig(unittest.TestCase):
  def test_missing_lazy_hardware_dependency_has_no_fabricated_log_root(self):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
      if name == "openpilot.system.hardware.hw":
        raise ImportError("injected absent host hardware dependency")
      return original(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=missing):
      result = resolve_runtime_config(environment={}, native_comma=True, params=FakeParams())
    self.assertIsNone(result["paths"]["logs"])
    self.assertIn("log_root_unavailable", result["blockers"]["collection"])
    self.assertFalse(result["capabilities"]["collection_ready"])

  def test_explicit_log_override_is_not_replaced_by_host_default_argument(self):
    with tempfile.TemporaryDirectory() as tmp:
      desired, host = str(Path(tmp) / "explicit-logs"), str(Path(tmp) / "host-logs")
      result = resolve_runtime_config(environment={"CARROT_CONTROL_ADVISOR_LOG_ROOT": desired}, native_comma=True,
                                      params=FakeParams(), log_root=host)
      self.assertEqual(result["paths"]["logs"], desired)

  def test_generic_host_environment_aliases_do_not_configure_advisor(self):
    with tempfile.TemporaryDirectory() as tmp:
      env = {key: str(Path(tmp) / key.lower()) for key in
             ("SETTINGS_DIR", "EVIDENCE_ROOT", "AUTO_OUTPUT_ROOT", "REQUEST_ROOT", "EXPORT_ROOT", "CONSUMER_SOCKET", "PROFILE", "OBJECTIVES")}
      result = resolve_runtime_config(environment=env, native_comma=False)
      self.assertTrue(all(value is None for value in result["paths"].values()))

  def test_existing_regular_file_is_not_a_configured_storage_directory(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "regular-file"
      path.write_bytes(b"must remain unchanged")
      result = resolve_runtime_config(environment={"CARROT_CONTROL_ADVISOR_EXPORT_ROOT": str(path)},
                                      native_comma=True, params=FakeParams(), log_root="/data/media/0/realdata")
      self.assertIsNone(result["paths"]["exports"])
      self.assertIn("export_root_not_directory", result["blockers"]["exports"])
      self.assertTrue(result["capabilities"]["collection_ready"])
      self.assertEqual(path.read_bytes(), b"must remain unchanged")

  def test_root_relative_backslash_and_nonstring_overrides_fail_per_component(self):
    for value in ("\\root-relative", 123):
      with self.subTest(value=value):
        result = resolve_runtime_config(environment={"CARROT_CONTROL_ADVISOR_EXPORT_ROOT": value},
                                        native_comma=True, params=FakeParams(), log_root="/data/media/0/realdata")
        self.assertIsNone(result["paths"]["exports"])
        self.assertIn("export_root_not_absolute", result["blockers"]["exports"])
        self.assertTrue(result["capabilities"]["collection_ready"])

  def test_pc_defaults_do_not_infer_native_paths(self):
    res = resolve_runtime_config(environment={}, native_comma=False)
    self.assertEqual(res["runtime_kind"], "pc")
    self.assertFalse(res["capabilities"]["native_runtime_detected"])
    self.assertFalse(res["capabilities"]["params_available"])
    self.assertFalse(res["capabilities"]["collection_ready"])
    self.assertFalse(res["capabilities"]["analysis_configured"])
    self.assertFalse(res["capabilities"]["apply_enabled"])
    self.assertFalse(res["capabilities"]["recommendations_enabled"])
    self.assertFalse(res["capabilities"]["device_qualified"])
    self.assertTrue(res["capabilities"]["paths_valid"])

    for key, val in res["paths"].items():
      self.assertIsNone(val, f"PC default for {key} must not infer native path")

    self.assertIn("native_runtime_required", res["blockers"]["collection"])
    self.assertIn("params_unavailable", res["blockers"]["collection"])
    self.assertIn("candidates_root_missing", res["blockers"]["analysis"])
    self.assertIn("request_root_missing", res["blockers"]["requests"])
    self.assertIn("export_root_missing", res["blockers"]["exports"])

  def test_native_injected_configuration_exact_defaults_no_creation(self):
    fake_p = FakeParams("/data/params")
    existing = {value: Path(value).exists() for value in ("/data/media/0/fms_control_advisor/evidence", "/data/media/0/fms_control_advisor/candidates")}
    with patch("os.mkdir", side_effect=AssertionError("pure resolver attempted mkdir")):
      res = resolve_runtime_config(environment={}, native_comma=True, params=fake_p, log_root="/data/media/0/realdata/")
    self.assertEqual(res["runtime_kind"], "native_comma")
    self.assertTrue(res["capabilities"]["native_runtime_detected"])
    self.assertTrue(res["capabilities"]["params_available"])
    self.assertTrue(res["capabilities"]["collection_ready"])
    self.assertTrue(res["capabilities"]["analysis_configured"])
    self.assertFalse(fake_p.put_called)

    expected = {
        "evidence": "/data/media/0/fms_control_advisor/evidence",
        "candidates": "/data/media/0/fms_control_advisor/candidates",
        "requests": "/data/media/0/fms_control_advisor/requests",
        "exports": "/data/media/0/fms_control_advisor/exports",
        "consumer_socket": "/tmp/fms_control_advisor/consumer.sock",
        "logs": "/data/media/0/realdata",
        "settings": "/data/params",
    }
    for key, path in expected.items():
      self.assertEqual(res["paths"][key], path)

    self.assertEqual(res["blockers"]["collection"], [])
    self.assertEqual(res["blockers"]["requests"], [])
    self.assertEqual(res["blockers"]["exports"], [])
    self.assertEqual(res["paths"]["profile"], BUILTIN_PROFILE_PATH)
    self.assertEqual(res["paths"]["objectives"], BUILTIN_OBJECTIVES_PATH)
    self.assertEqual(res["blockers"]["analysis"], [])

    self.assertEqual(existing, {value: Path(value).exists() for value in existing})

  def test_explicit_environment_overrides_retained(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      ev_root = str(base / "custom_evidence")
      cand_root = str(base / "custom_candidates")
      req_root = str(base / "custom_requests")
      exp_root = str(base / "custom_exports")
      sock_path = str(base / "custom.sock")
      sett_path = str(base / "custom_params")
      log_path = str(base / "custom_logs")

      env = {
          "CARROT_CONTROL_ADVISOR_SETTINGS_DIR": sett_path,
          "CARROT_CONTROL_ADVISOR_LOG_ROOT": log_path,
          "CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT": ev_root,
          "CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT": cand_root,
          "CARROT_CONTROL_ADVISOR_REQUEST_ROOT": req_root,
          "CARROT_CONTROL_ADVISOR_EXPORT_ROOT": exp_root,
          "CARROT_CONTROL_ADVISOR_CONSUMER_SOCKET": sock_path,
      }
      res = resolve_runtime_config(environment=env, native_comma=True, params=FakeParams())
      self.assertEqual(res["paths"]["evidence"], ev_root)
      self.assertEqual(res["paths"]["candidates"], cand_root)
      self.assertEqual(res["paths"]["requests"], req_root)
      self.assertEqual(res["paths"]["exports"], exp_root)
      self.assertEqual(res["paths"]["consumer_socket"], sock_path)
      self.assertEqual(res["paths"]["settings"], sett_path)
      self.assertEqual(res["paths"]["logs"], log_path)
      self.assertTrue(res["capabilities"]["collection_ready"])

  def test_malformed_relative_path_rejected_per_component(self):
    fake_p = FakeParams("/data/params")
    env = {"CARROT_CONTROL_ADVISOR_EXPORT_ROOT": "relative/exports/dir"}
    res = resolve_runtime_config(environment=env, native_comma=True, params=fake_p, log_root="/data/media/0/realdata/")
    self.assertIsNone(res["paths"]["exports"])
    self.assertIn("export_root_not_absolute", res["blockers"]["exports"])
    self.assertFalse(res["capabilities"]["paths_valid"])
    self.assertTrue(res["capabilities"]["collection_ready"])
    self.assertEqual(res["paths"]["evidence"], "/data/media/0/fms_control_advisor/evidence")

  def test_overlapping_paths_rejected_per_component(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      dir_a = base / "data_root"
      dir_nested = dir_a / "nested_candidates"
      env = {
          "CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT": str(dir_a),
          "CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT": str(dir_nested),
      }
      res = resolve_runtime_config(environment=env, native_comma=True, params=FakeParams(), log_root="/data/media/0/realdata/")
      self.assertIsNone(res["paths"]["evidence"])
      self.assertIsNone(res["paths"]["candidates"])
      self.assertIn("evidence_root_overlap", res["blockers"]["collection"])
      self.assertIn("candidates_root_overlap", res["blockers"]["analysis"])
      self.assertFalse(res["capabilities"]["collection_ready"])
      self.assertEqual(res["paths"]["requests"], "/data/media/0/fms_control_advisor/requests")
      self.assertEqual(res["blockers"]["requests"], [])

  def test_socket_overlap_rejected(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      ev_root = base / "evidence"
      sock_in_ev = ev_root / "consumer.sock"
      env = {
          "CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT": str(ev_root),
          "CARROT_CONTROL_ADVISOR_CONSUMER_SOCKET": str(sock_in_ev),
      }
      res = resolve_runtime_config(environment=env, native_comma=True, params=FakeParams(), log_root="/data/media/0/realdata/")
      self.assertIsNone(res["paths"]["consumer_socket"])
      self.assertIn("consumer_socket_overlap", res["blockers"]["collection"])
      self.assertFalse(res["capabilities"]["collection_ready"])

  def test_missing_objectives_leaves_valid_collection_ready(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      prof = base / "profile.json"
      prof.write_text("{}")
      env = {"CARROT_CONTROL_ADVISOR_AUTO_PROFILE": str(prof)}
      res = resolve_runtime_config(environment=env, native_comma=True, params=FakeParams(), log_root="/data/media/0/realdata/")
      self.assertEqual(res["paths"]["profile"], str(prof))
      self.assertIsNone(res["paths"]["objectives"])
      self.assertTrue(res["capabilities"]["collection_ready"])
      self.assertFalse(res["capabilities"]["analysis_configured"])
      self.assertEqual(res["blockers"]["collection"], [])
      self.assertIn("objectives_missing", res["blockers"]["analysis"])
      self.assertNotIn("profile_missing", res["blockers"]["analysis"])

  def test_missing_params_blocks_collection_only(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      prof = base / "profile.json"
      obj = base / "objectives.json"
      cand = base / "candidates"
      prof.write_text("{}")
      obj.write_text("{}")
      env = {
          "CARROT_CONTROL_ADVISOR_AUTO_PROFILE": str(prof),
          "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES": str(obj),
          "CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT": str(cand),
      }
      res = resolve_runtime_config(environment=env, native_comma=True, params=None, log_root="/data/media/0/realdata/")
      self.assertFalse(res["capabilities"]["params_available"])
      self.assertFalse(res["capabilities"]["collection_ready"])
      self.assertIn("params_unavailable", res["blockers"]["collection"])
      self.assertTrue(res["capabilities"]["analysis_configured"])
      self.assertEqual(res["blockers"]["analysis"], [])
      self.assertEqual(res["blockers"]["requests"], [])
      self.assertEqual(res["blockers"]["exports"], [])

  def test_symlink_rejection_where_supported(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      target = base / "real_target"
      target.mkdir()
      link = base / "link_dir"
      try:
        directory_link(target, link)
      except (OSError, NotImplementedError):
        self.skipTest("Symlink creation not supported in test environment")

      env = {"CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT": str(link)}
      res = resolve_runtime_config(environment=env, native_comma=True, params=FakeParams(), log_root="/data/media/0/realdata/")
      self.assertIsNone(res["paths"]["evidence"])
      self.assertIn("evidence_root_link", res["blockers"]["collection"])

      sett_target = base / "params_real"
      sett_target.mkdir()
      sett_link = base / "params_sym"
      directory_link(sett_target, sett_link)
      env2 = {"CARROT_CONTROL_ADVISOR_SETTINGS_DIR": str(sett_link)}
      res2 = resolve_runtime_config(environment=env2, native_comma=True, params=FakeParams(), log_root="/data/media/0/realdata/")
      self.assertEqual(res2["paths"]["settings"], str(sett_link))

  def test_actual_settings_final_link_allowed_on_pc_but_parent_links_rejected(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      target, link = base / "real-params", base / "params-link"
      target.mkdir()
      try:
        directory_link(target, link)
      except (OSError, NotImplementedError):
        self.skipTest("Neither directory symlink nor junction creation is available")
      before = sorted(path.name for path in base.iterdir())
      result = resolve_runtime_config(environment={"CARROT_CONTROL_ADVISOR_SETTINGS_DIR": str(link)}, native_comma=False)
      self.assertEqual(result["paths"]["settings"], str(link))
      self.assertFalse(result["capabilities"]["collection_ready"])
      blocked = resolve_runtime_config(environment={"CARROT_CONTROL_ADVISOR_SETTINGS_DIR": str(link / "child")}, native_comma=False)
      self.assertIsNone(blocked["paths"]["settings"])
      self.assertIn("settings_dir_link", blocked["blockers"]["collection"])
      overlap = resolve_runtime_config(environment={"CARROT_CONTROL_ADVISOR_SETTINGS_DIR": str(link),
        "CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT": str(target / "future-evidence")}, native_comma=False)
      self.assertIsNone(overlap["paths"]["settings"])
      self.assertIsNone(overlap["paths"]["evidence"])
      self.assertEqual(before, sorted(path.name for path in base.iterdir()))

  def test_supplied_global_log_root_does_not_require_or_mutate_hardware(self):
    from openpilot.selfdrive.carrot.control_advisor import runtime_config
    with tempfile.TemporaryDirectory() as tmp:
      env = {"LOG_ROOT": str(Path(tmp) / "explicit-global-log")}
      before = dict(os.environ)
      with patch.object(runtime_config, "_resolve_lazy_hw_log_root", side_effect=AssertionError("explicit log root imported hardware")):
        result = resolve_runtime_config(environment=env, native_comma=True, params=FakeParams())
      self.assertEqual(result["paths"]["logs"], env["LOG_ROOT"])
      self.assertEqual(dict(os.environ), before)

  def test_profile_parent_link_only_blocks_analysis(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      target, link = base / "configs", base / "linked-configs"
      target.mkdir()
      (target / "profile.json").write_text("{}")
      objectives = base / "objectives.json"
      objectives.write_text("{}")
      try:
        directory_link(target, link)
      except (OSError, NotImplementedError):
        self.skipTest("Neither directory symlink nor junction creation is available")
      result = resolve_runtime_config(environment={"CARROT_CONTROL_ADVISOR_AUTO_PROFILE": str(link / "profile.json"),
        "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES": str(objectives)}, native_comma=True,
        params=FakeParams(), log_root="/data/media/0/realdata")
      self.assertTrue(result["capabilities"]["collection_ready"])
      self.assertFalse(result["capabilities"]["analysis_configured"])
      self.assertIn("profile_link", result["blockers"]["analysis"])
      self.assertEqual(result["paths"]["objectives"], str(objectives))

  def test_future_explicit_destinations_are_not_created_and_have_no_write_qualification(self):
    with tempfile.TemporaryDirectory() as tmp:
      base = Path(tmp)
      profile, objectives = base / "profile.json", base / "objectives.json"
      profile.write_text("{}")
      objectives.write_text("{}")
      before = sorted(path.name for path in base.iterdir())
      mapping = {"SETTINGS_DIR": "settings", "LOG_ROOT": "logs", "EVIDENCE_ROOT": "evidence",
                 "AUTO_OUTPUT_ROOT": "candidates", "REQUEST_ROOT": "requests", "EXPORT_ROOT": "exports"}
      env = {"CARROT_CONTROL_ADVISOR_" + key: str(base / name) for key, name in mapping.items()}
      env.update(CARROT_CONTROL_ADVISOR_CONSUMER_SOCKET=str(base / "consumer.sock"),
                 CARROT_CONTROL_ADVISOR_AUTO_PROFILE=str(profile), CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES=str(objectives))
      params = FakeParams(str(base / "settings"))
      result = resolve_runtime_config(environment=env, native_comma=True, params=params)
      self.assertTrue(result["capabilities"]["collection_ready"])
      self.assertTrue(result["capabilities"]["analysis_configured"])
      self.assertFalse(result["capabilities"]["apply_enabled"])
      self.assertFalse(result["capabilities"]["recommendations_enabled"])
      self.assertFalse(result["capabilities"]["device_qualified"])
      self.assertFalse(params.put_called)
      self.assertEqual(before, sorted(path.name for path in base.iterdir()))

  def test_no_environment_mutation(self):
    original_env = dict(os.environ)
    fake_p = FakeParams()
    resolve_runtime_config(environment={"LOG_ROOT": "/custom/env/log"}, native_comma=True, params=fake_p)
    self.assertEqual(dict(os.environ), original_env)
    self.assertFalse(fake_p.put_called)

  def test_params_probe_failure_handling(self):
    broken_p = BrokenParams()
    res = resolve_runtime_config(native_comma=True, params=broken_p, log_root="/data/media/0/realdata/")
    self.assertFalse(res["capabilities"]["params_available"])
    self.assertFalse(res["capabilities"]["collection_ready"])
    self.assertIn("params_unavailable", res["blockers"]["collection"])

  def test_no_eager_native_dependency_import(self):
    program = """import builtins,json
original=builtins.__import__
def guard(name,*args,**kwargs):
  if name in {'openpilot.common.params','openpilot.system.hardware','openpilot.system.hardware.hw'}:
    raise AssertionError('eager native dependency: '+name)
  return original(name,*args,**kwargs)
builtins.__import__=guard
from openpilot.selfdrive.carrot.control_advisor.runtime_config import resolve_runtime_config
result=resolve_runtime_config(environment={},native_comma=False)
print(json.dumps(result['capabilities']))
"""
    process = subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True)
    self.assertEqual(process.returncode, 0, process.stderr)
    self.assertFalse(json.loads(process.stdout)["native_runtime_detected"])


if __name__ == "__main__":
  unittest.main()
