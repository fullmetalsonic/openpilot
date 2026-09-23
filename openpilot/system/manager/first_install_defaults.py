"""Apply the owner's saved Params once, on a fresh device settings store only."""

import json
import os
from pathlib import Path

from openpilot.common.params import ParamKeyType


MARKER = "FmsFirstInstallProfile"
PENDING = "pending"
COMPLETE = "complete"
EXISTING = "existing"

# launch_chffrplus.sh writes these before the first manager start.
BOOTSTRAP_KEYS = frozenset({"GithubUsername", "GithubSshKeys", "SshEnabled"})
PROFILE_PATH = Path(__file__).with_name("first_install_defaults.json")


def _put_checked(params, key: str, value) -> None:
  params.put(key, value)
  # Python Params.put discards the C++ write result. A readback is essential
  # before we can advance the marker or let manager fill ordinary defaults.
  if params.get(key) != value:
    raise OSError(f"Could not persist first-install param {key}")


def prepare_first_install_defaults(params) -> str:
  """Classify the store before manager clears any transition-scoped keys."""
  state = params.get(MARKER)
  if state is not None:
    return state

  # No broad 'empty Params' assumption: the launcher creates SSH bootstrap
  # keys first. Any other file is evidence of an existing or restored store.
  entries = set(os.listdir(params.get_param_path()))
  state = PENDING if entries <= BOOTSTRAP_KEYS else EXISTING
  _put_checked(params, MARKER, state)
  return state


def _typed_value(params, key: str, raw: str):
  kind = params.get_type(key)
  if kind == ParamKeyType.BOOL:
    if raw in ("True", "1"):
      return True
    if raw in ("False", "0"):
      return False
    raise ValueError(f"Invalid first-install BOOL for {key}")
  if kind == ParamKeyType.INT:
    return int(raw)
  if kind == ParamKeyType.FLOAT:
    return float(raw)
  if kind == ParamKeyType.STRING:
    return raw
  raise ValueError(f"Unsupported first-install param type for {key}")


def apply_first_install_defaults(params) -> None:
  """Resume only missing values; never replace a value changed by the user."""
  if params.get(MARKER) != PENDING:
    return

  with PROFILE_PATH.open(encoding="utf-8") as file:
    profile = json.load(file)
  if not isinstance(profile, dict) or len(profile) != 205 or not all(isinstance(v, str) for v in profile.values()):
    raise ValueError("Invalid first-install Params profile")

  for key, raw in profile.items():
    expected = _typed_value(params, key, raw)
    if params.get(key) is None:
      _put_checked(params, key, expected)

  _put_checked(params, MARKER, COMPLETE)
