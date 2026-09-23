"""Pure-Python tests for the first-install profile; device Params is not emulated."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace

import pytest


MANAGER_DIR = Path(__file__).resolve().parents[1]
REPO = MANAGER_DIR.parents[2]
SOURCE_PROFILE_DIGEST = "cadb85df3892779fd8a6bd291890882ea08cb073de75440afe7e26a93ca922fc"
TYPES = SimpleNamespace(BOOL=1, INT=2, FLOAT=3, STRING=4)


@pytest.fixture
def first_install(monkeypatch):
  fake_params_module = ModuleType("openpilot.common.params")
  fake_params_module.ParamKeyType = TYPES
  monkeypatch.setitem(sys.modules, "openpilot.common.params", fake_params_module)
  spec = importlib.util.spec_from_file_location("first_install_defaults_under_test", MANAGER_DIR / "first_install_defaults.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


@pytest.fixture
def profile():
  return json.loads((MANAGER_DIR / "first_install_defaults.json").read_text(encoding="utf-8"))


@pytest.fixture
def registry_types():
  header = (REPO / "openpilot" / "common" / "params_keys.h").read_text(encoding="utf-8")
  names = dict(re.findall(r'\{"([A-Za-z0-9_]+)",\s*\{[^,]+,\s*(BOOL|INT|FLOAT|STRING)', header))
  return {key: getattr(TYPES, kind) for key, kind in names.items()}


class FakeParams:
  def __init__(self, path, registry_types):
    self.path = path
    self.registry_types = registry_types
    self.values = {}
    self.silent_fail = set()

  def get_param_path(self):
    return str(self.path)

  def get_type(self, key):
    return self.registry_types[key]

  def get(self, key):
    return self.values.get(key)

  def put(self, key, value):
    if key not in self.silent_fail:
      self.values[key] = value
      (self.path / key).write_text(str(value), encoding="utf-8")

  def clear_manager_start(self):
    self.values.pop("ActivateCruiseAfterBrake", None)
    (self.path / "ActivateCruiseAfterBrake").unlink(missing_ok=True)


def test_profile_matches_all_205_source_values(profile, registry_types, first_install):
  canonical = json.dumps(profile, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
  assert len(profile) == 205
  assert hashlib.sha256(canonical).hexdigest() == SOURCE_PROFILE_DIGEST
  assert set(profile) <= set(registry_types)
  assert profile["DisableDM"] == "2"
  assert profile["MuteDoor"] == profile["MuteSeatbelt"] == "1"
  assert profile["HasAcceptedTerms"] == "2"
  assert profile["CarSelected3"] == "Kia Sorento Hybrid 2021-23"
  header = (REPO / "openpilot" / "common" / "params_keys.h").read_text(encoding="utf-8")
  assert '{"FmsFirstInstallProfile", {PERSISTENT, STRING}}' in header
  assert first_install._typed_value(SimpleNamespace(get_type=lambda _key: TYPES.BOOL), "UseWideCamera", "True") is True
  assert first_install._typed_value(SimpleNamespace(get_type=lambda _key: TYPES.BOOL), "RadarTrackFlip", "False") is False


def test_fresh_store_with_launcher_bootstrap_applies_all_205(tmp_path, registry_types, profile, first_install):
  params = FakeParams(tmp_path, registry_types)
  for key in first_install.BOOTSTRAP_KEYS:
    params.put(key, "bootstrap")
  assert first_install.prepare_first_install_defaults(params) == first_install.PENDING
  params.clear_manager_start()
  first_install.apply_first_install_defaults(params)
  assert params.get(first_install.MARKER) == first_install.COMPLETE
  assert all(params.get(key) == first_install._typed_value(params, key, raw) for key, raw in profile.items())
  assert params.get("CruiseOnDist") == 0
  assert params.get("SoftHoldOnCancel") is True
  assert params.get("RadarTrackFlip") is False
  assert params.get("ActivateCruiseAfterBrake") == 0


@pytest.mark.parametrize("existing_key", ["AutoCruiseControl", "CalibrationParams", "unknown-file"])
def test_any_non_bootstrap_entry_preserves_existing_store(tmp_path, registry_types, first_install, existing_key):
  params = FakeParams(tmp_path, registry_types)
  params.put(existing_key, "existing")
  assert first_install.prepare_first_install_defaults(params) == first_install.EXISTING
  first_install.apply_first_install_defaults(params)
  assert params.get("DisableDM") is None
  assert params.get(existing_key) == "existing"


def test_repeat_and_interrupted_resume_preserve_user_changes(tmp_path, registry_types, first_install):
  params = FakeParams(tmp_path, registry_types)
  assert first_install.prepare_first_install_defaults(params) == first_install.PENDING
  params.silent_fail.add("OnnxLaneIntervalMs")
  with pytest.raises(OSError):
    first_install.apply_first_install_defaults(params)
  assert params.get(first_install.MARKER) == first_install.PENDING
  assert first_install.prepare_first_install_defaults(params) == first_install.PENDING
  params.clear_manager_start()
  params.put("OnnxBsdIntervalMs", 0)
  params.put("SoftHoldOnCancel", False)
  params.silent_fail.clear()
  first_install.apply_first_install_defaults(params)
  assert params.get("OnnxBsdIntervalMs") == 0
  assert params.get("SoftHoldOnCancel") is False
  assert params.get(first_install.MARKER) == first_install.COMPLETE
  params.put("OnnxBsdIntervalMs", 777)
  params.values.pop("DisableDM")
  (tmp_path / "DisableDM").unlink()
  first_install.apply_first_install_defaults(params)
  assert params.get("OnnxBsdIntervalMs") == 777
  assert params.get("DisableDM") is None


def test_silent_marker_write_failure_stops_before_profile(tmp_path, registry_types, first_install):
  params = FakeParams(tmp_path, registry_types)
  params.silent_fail.add(first_install.MARKER)
  with pytest.raises(OSError):
    first_install.prepare_first_install_defaults(params)
  assert params.get("DisableDM") is None


def test_silent_completion_marker_failure_remains_pending(tmp_path, registry_types, first_install):
  params = FakeParams(tmp_path, registry_types)
  assert first_install.prepare_first_install_defaults(params) == first_install.PENDING
  params.silent_fail.add(first_install.MARKER)
  with pytest.raises(OSError):
    first_install.apply_first_install_defaults(params)
  assert params.get(first_install.MARKER) == first_install.PENDING
  params.silent_fail.clear()
  first_install.apply_first_install_defaults(params)
  assert params.get(first_install.MARKER) == first_install.COMPLETE


def test_manager_calls_prepare_before_clear_and_apply_before_default_loop():
  manager = (MANAGER_DIR / "manager.py").read_text(encoding="utf-8")
  assert manager.index("prepare_first_install_defaults(params)") < manager.index("params.clear_all(ParamKeyFlag.CLEAR_ON_MANAGER_START)")
  assert manager.index("params.clear_all(ParamKeyFlag.DEVELOPMENT_ONLY)") < manager.index("apply_first_install_defaults(params)")
  assert manager.index("apply_first_install_defaults(params)") < manager.index("# set unset params to their default value")
