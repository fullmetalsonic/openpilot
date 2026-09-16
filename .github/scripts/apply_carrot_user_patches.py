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
    """  def _cruise_control(self, enable, cancel_timer, reason, allow_cancel_state=False, allow_unavailable=False):
    if enable > 0 and not self._cruise_available and not allow_unavailable:
""",
    "limit cruise-unavailable exception to soft hold",
  )
  text = replace_once(
    text,
    """    self._cruise_control(1, -1, \"Cruise on (soft hold)\", allow_cancel_state=self.soft_hold_on_cancel)
""",
    """    self._cruise_control(1, -1, \"Cruise on (soft hold)\", allow_cancel_state=self.soft_hold_on_cancel, allow_unavailable=True)
""",
    "allow soft hold to engage while cruise is unavailable",
  )
  text = replace_once(
    text,
    """      soft_hold_available = CS.cruiseState.available and self.autoCruiseControl != 0 and not self.CP.pcmCruise and \\
""",
    """      soft_hold_available = self.autoCruiseControl != 0 and not self.CP.pcmCruise and \\
""",
    "remove cruise-availability gate from soft hold arming",
  )

  CRUISE.write_text(text, encoding="utf-8")


def patch_blinkers() -> None:
  text = HYUNDAI_CANFD.read_text(encoding="utf-8")
  for name in ("LEFT_BLINK_HOLD", "RIGHT_BLINK_HOLD"):
    pattern = rf"(?m)^(\s*values\['{name}'\]\s*=\s*)[^\r\n]+$"
    text, replacements = re.subn(pattern, rf"\g<1>0", text, count=1)
    if replacements != 1:
      raise RuntimeError(f"Upstream code shape changed; cannot patch {name}")
  HYUNDAI_CANFD.write_text(text, encoding="utf-8")


def main() -> None:
  patch_cruise()
  patch_blinkers()


if __name__ == "__main__":
  main()
