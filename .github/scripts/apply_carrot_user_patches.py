#!/usr/bin/env python3
"""Reapply the narrow, user-owned CarrotPilot patches after an upstream merge.

Abort instead of guessing when upstream changes any expected code shape.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CRUISE = ROOT / "openpilot/selfdrive/car/cruise.py"
HYUNDAI_CANFD = ROOT / "opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py"
FORK_REMOTE = ROOT / "openpilot/selfdrive/carrot/server/services/fork_remote.py"
TOOLS_DISPATCHER = ROOT / "openpilot/selfdrive/carrot/server/features/tools/dispatcher.py"


def replace_once(text: str, original: str, patched: str, name: str) -> str:
  if patched in text:
    return text
  if original not in text:
    raise RuntimeError(f"Upstream code shape changed; refusing to guess: {name}")
  return text.replace(original, patched, 1)


def patch_cruise() -> None:
  text = CRUISE.read_text(encoding="utf-8")

  text = replace_once(
    text,
    """    if not self._cruise_available:
      self._cruise_ready = False
      self._paddle_decel_active = False
      self._soft_hold_count = 0
      self._soft_hold_active = 0
""",
    """    if not self._cruise_available:
      self._cruise_ready = False
      self._paddle_decel_active = False
""",
    "preserve soft hold while cruise is unavailable",
  )
  text = replace_once(
    text,
    """  def _cruise_control(self, enable, cancel_timer, reason, allow_cancel_state=False):
    if enable > 0 and not self._cruise_available:
""",
    """  def _cruise_control(self, enable, cancel_timer, reason, allow_cancel_state=False, allow_unavailable=False,
                      allow_auto_cruise_cancel_timer=False):
    if enable > 0 and not self._cruise_available and not allow_unavailable:
""",
    "limit cruise-unavailable and cancel-timer exceptions to soft hold",
  )
  text = replace_once(
    text,
    """      if self.autoCruiseControl_cancel_timer > 0 and enable != 0:
""",
    """      if self.autoCruiseControl_cancel_timer > 0 and enable != 0 and not allow_auto_cruise_cancel_timer:
""",
    "keep the post-shift cancel timer for normal automatic cruise",
  )
  text = replace_once(
    text,
    """    self._cruise_control(1, -1, \"Cruise on (soft hold)\", allow_cancel_state=self.soft_hold_on_cancel)
""",
    """    self._cruise_control(1, -1, \"Cruise on (soft hold)\", allow_cancel_state=self.soft_hold_on_cancel,
                         allow_unavailable=True, allow_auto_cruise_cancel_timer=True)
""",
    "allow independent soft hold to engage",
  )
  text = replace_once(
    text,
    """    self._soft_hold_active = 2
    self._cruise_control(1, -1, \"Cruise on (soft hold)\", allow_cancel_state=self.soft_hold_on_cancel,
                         allow_unavailable=True, allow_auto_cruise_cancel_timer=True)
""",
    """    self._soft_hold_active = 2
    if self._cruise_cancel_state:
      self._add_log(\"Soft hold active (cancel state)\")
      return
    self._cruise_control(1, -1, \"Cruise on (soft hold)\", allow_cancel_state=self.soft_hold_on_cancel,
                         allow_unavailable=True, allow_auto_cruise_cancel_timer=True)
""",
    "keep cruise cancelled while independent soft hold is active",
  )
  text = replace_once(
    text,
    """      soft_hold_available = CS.cruiseState.available and self.autoCruiseControl != 0 and not self.CP.pcmCruise and \\
""",
    """      soft_hold_available = self.autoCruiseControl != 0 and not self.CP.pcmCruise and \\
""",
    "remove cruise-availability gate from soft hold arming",
  )

  timer_gate = """                            self.autoCruiseControl_cancel_timer == 0 and \\
"""
  if timer_gate in text:
    text = text.replace(timer_gate, "", 1)
  elif "allow_auto_cruise_cancel_timer=True" not in text:
    raise RuntimeError("Upstream code shape changed; cannot preserve independent soft hold through cancel timer")

  CRUISE.write_text(text, encoding="utf-8")


def patch_blinkers() -> None:
  text = HYUNDAI_CANFD.read_text(encoding="utf-8")
  for name in ("LEFT_BLINK_HOLD", "RIGHT_BLINK_HOLD"):
    pattern = rf"(?m)^(\s*values\['{name}'\]\s*=\s*)[^\r\n]+$"
    text, replacements = re.subn(pattern, rf"\g<1>0", text, count=1)
    if replacements != 1:
      raise RuntimeError(f"Upstream code shape changed; cannot patch {name}")

  text = replace_once(
    text,
    """  soft_hold_active = CS.softHoldActive > 0 and CS.out.cruiseState.available
  acc_control_enabled = (enabled or soft_hold_active) and CS.out.cruiseState.available and CS.paddle_button_prev == 0 and not interlock_active
""",
    """  soft_hold_active = CS.softHoldActive > 0
  acc_control_enabled = (enabled or soft_hold_active) and CS.paddle_button_prev == 0 and not interlock_active
""",
    "preserve CANFD SCC2 stop request for independent soft hold",
  )
  text = replace_once(
    text,
    """  soft_hold_active = CS.softHoldActive > 0 and CS.out.cruiseState.available
  acc_control_enabled = (enabled or soft_hold_active) and CS.out.cruiseState.available and not interlock_active
""",
    """  soft_hold_active = CS.softHoldActive > 0
  acc_control_enabled = (enabled or soft_hold_active) and not interlock_active
""",
    "preserve CANFD SCC stop request for independent soft hold",
  )
  HYUNDAI_CANFD.write_text(text, encoding="utf-8")


def verify_fork_branch_support() -> None:
  if not FORK_REMOTE.is_file():
    raise RuntimeError("Fork branch support file is missing after upstream merge")
  text = TOOLS_DISPATCHER.read_text(encoding="utf-8")
  for marker in ("ensure_fork_remote", "local_branch_name(item_remote, item_name)"):
    if marker not in text:
      raise RuntimeError(f"Fork branch support changed upstream; refusing to guess: {marker}")


def main() -> None:
  patch_cruise()
  patch_blinkers()
  verify_fork_branch_support()


if __name__ == "__main__":
  main()
