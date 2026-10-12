import json
from pathlib import Path

import pytest

from openpilot.selfdrive.carrot import carrot_navi
from openpilot.selfdrive.carrot.server.services import params as params_service


class FakeUnknownKeyName(Exception):
  pass


class StaleParams:
  def __init__(self, root: Path) -> None:
    self.root = root

  def get_type(self, key: str):
    raise FakeUnknownKeyName(key)

  def get(self, key: str):
    raise FakeUnknownKeyName(key)

  def get_int(self, key: str):
    raise FakeUnknownKeyName(key)

  def put(self, key: str, value):
    raise FakeUnknownKeyName(key)

  def put_int(self, key: str, value: int):
    raise FakeUnknownKeyName(key)

  def get_param_path(self, key: str = "") -> str:
    return str(self.root / key) if key else str(self.root)


def int_setting(default: int = 0) -> dict:
  return {"min": 0, "max": 60, "default": default}


def test_unregistered_json_setting_uses_atomic_file_fallback(tmp_path, monkeypatch):
  stale_params = StaleParams(tmp_path)
  monkeypatch.setattr(params_service, "HAS_PARAMS", True)
  monkeypatch.setattr(params_service, "Params", lambda: stale_params)
  monkeypatch.setattr(params_service, "UnknownKeyName", FakeUnknownKeyName)

  params_service.set_param_value("FutureIntSetting", 42, int_setting())

  assert (tmp_path / "FutureIntSetting").read_text(encoding="utf-8") == "42"
  assert params_service._read_param_value(stale_params, "FutureIntSetting", 0) == 42


def test_unregistered_fallback_rejects_names_outside_settings(tmp_path, monkeypatch):
  stale_params = StaleParams(tmp_path)
  monkeypatch.setattr(params_service, "HAS_PARAMS", True)
  monkeypatch.setattr(params_service, "Params", lambda: stale_params)
  monkeypatch.setattr(params_service, "UnknownKeyName", FakeUnknownKeyName)

  with pytest.raises(FakeUnknownKeyName):
    params_service.set_param_value("NotInCarrotSettings", 1)


def test_unregistered_read_fallback_rejects_path_traversal(tmp_path):
  stale_params = StaleParams(tmp_path)
  outside = tmp_path.parent / f"{tmp_path.name}_outside"
  outside.write_text("123", encoding="utf-8")

  try:
    assert params_service._read_param_value(stale_params, f"../{outside.name}", 0) == 0
  finally:
    outside.unlink(missing_ok=True)


def test_unregistered_fallback_does_not_hide_other_write_failures(tmp_path, monkeypatch):
  class BrokenParams(StaleParams):
    def put_int(self, key: str, value: int):
      raise OSError("disk full")

  broken_params = BrokenParams(tmp_path)
  monkeypatch.setattr(params_service, "HAS_PARAMS", True)
  monkeypatch.setattr(params_service, "Params", lambda: broken_params)
  monkeypatch.setattr(params_service, "UnknownKeyName", FakeUnknownKeyName)

  with pytest.raises(OSError, match="disk full"):
    params_service.set_param_value("FutureIntSetting", 1, int_setting())


















def test_map_param_reader_reads_map_fps_file_while_native_registry_is_stale(tmp_path):
  stale_params = StaleParams(tmp_path)
  (tmp_path / "ClusterNaviMapFps").write_text("3", encoding="utf-8")

  reader = carrot_navi.ClusterNaviMapParamReader(stale_params)

  assert reader._read_int("ClusterNaviMapFps", 1) == 3


def test_jetson_update_state_remains_internal():
  with pytest.raises(ValueError, match="controlled internally"):
    params_service.set_param_value("JetsonLegacyUpdatePending", True)


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_disable_dm_survives_file_qr_and_restore(mode, tmp_path, monkeypatch):
  class FakeParamKeyType:
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    TIME = "time"
    STRING = "string"
    JSON = "json"
    BYTES = "bytes"

  writes = []

  class LegacyParams:
    def all_keys(self):
      return ["DisableDM", "JetsonLegacyUpdatePending"]

    def get_type(self, key):
      return FakeParamKeyType.INT if key == "DisableDM" else FakeParamKeyType.BOOL

    def get_default_value(self, key):
      return "0"

    def get(self, key, **_kwargs):
      return str(mode) if key == "DisableDM" else "1"

    def put_int(self, key, value):
      writes.append((key, value))

  monkeypatch.setattr(params_service, "HAS_PARAMS", True)
  monkeypatch.setattr(params_service, "ParamKeyType", FakeParamKeyType)
  monkeypatch.setattr(params_service, "Params", LegacyParams)
  monkeypatch.setattr(params_service, "_catalog_definitions", lambda: {
    "DisableDM": {"min": 0, "max": 2, "default": 0, "unit": 1},
  })
  monkeypatch.setattr(params_service, "get_param_values", lambda names, defaults: {"DisableDM": (mode + 1) % 3})

  backup = params_service.get_all_param_values_for_backup()
  assert backup == {"DisableDM": str(mode)}
  path = tmp_path / "params_backup.json"
  path.write_text(json.dumps({**backup, "JetsonLegacyUpdatePending": "1"}), encoding="utf-8")
  assert params_service.read_param_backup_values(str(path)) == backup
  qr = params_service.build_params_qr_payload({**backup, "JetsonLegacyUpdatePending": "1"})
  assert params_service.parse_params_qr_payload(qr["payload"]) == backup
  restored = params_service.restore_param_values_validated(backup)
  assert restored["result"] == {"ok_cnt": 1, "fail_cnt": 0, "fails": []}
  assert writes == [("DisableDM", mode)]
