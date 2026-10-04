"""Preserve the user's stored stop-speed threshold without a runtime floor."""

import json
from pathlib import Path

import numpy as np
import pytest

from openpilot.selfdrive.controls.lib.drive_helpers import get_accel_from_plan


ROOT = Path(__file__).resolve().parents[4]


class Params:
  def __init__(self, value):
    self.value = value
    self.writes = []

  def get_float(self, key):
    assert key == "VEgoStopping"
    return float(self.value)

  def put_int(self, key, value):
    self.writes.append((key, value))

  def put_int_nonblocking(self, key, value):
    self.writes.append((key, value))


@pytest.mark.parametrize("value", [1, 2, 5, 9, 10, 50, 100])
def test_stored_value_scales_without_mutation(value):
  params = Params(value)
  threshold = params.get_float("VEgoStopping") * .01
  assert threshold == pytest.approx(value * .01)
  assert params.value == value
  assert params.writes == []


def test_lower_threshold_can_delay_stop_recognition():
  times = np.array([0., .3, 1.3])
  speeds = np.array([.1, .066716544, .030907153])
  accels = np.array([-.13, -.08, 0.])
  assert not get_accel_from_plan(speeds, accels, times, action_t=.3, vEgoStopping=.02)[1]
  assert get_accel_from_plan(speeds, accels, times, action_t=.3, vEgoStopping=.10)[1]
  speeds[-1] = .2
  assert not get_accel_from_plan(speeds, accels, times, action_t=.3, vEgoStopping=.10)[1]


def test_catalog_and_live_reads_keep_user_value():
  data = json.loads((ROOT / "openpilot/selfdrive/carrot_settings.json").read_text(encoding="utf-8"))

  def settings(value):
    if isinstance(value, dict):
      if value.get("name") == "VEgoStopping":
        yield value
      for child in value.values():
        yield from settings(child)
    elif isinstance(value, list):
      for child in value:
        yield from settings(child)

  setting, = settings(data)
  assert (setting["min"], setting["max"], setting["default"], setting["unit"]) == (1, 100, 50, 5)
  manager = (ROOT / "openpilot/system/manager/manager.py").read_text(encoding="utf-8")
  planner = (ROOT / "openpilot/selfdrive/controls/lib/longitudinal_planner.py").read_text(encoding="utf-8")
  modeld = (ROOT / "openpilot/selfdrive/modeld/modeld.py").read_text(encoding="utf-8")
  assert "get_stopping_speed" not in manager + planner + modeld
  assert 'self.params.get_float("VEgoStopping") * 0.01' in planner
  assert modeld.count('params.get_float("VEgoStopping") * 0.01') == 2
