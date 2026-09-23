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
  # A removal patch can be a prefix of the original block. Check the complete
  # original first; otherwise a fresh upstream file is incorrectly left intact.
  if original in text:
    if text.count(original) != 1:
      raise RuntimeError(f"Ambiguous upstream code shape; refusing to guess: {name}")
    return text.replace(original, patched, 1)
  if patched in text:
    return text
  raise RuntimeError(f"Upstream code shape changed; refusing to guess: {name}")


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
  # 3bbc3c12 adds `manual` for Bluetooth/HID actions. Keep the upstream
  # signature: SoftHold now holds through CAN without requesting cruise ON.
  legacy_signature = """  def _cruise_control(self, enable, cancel_timer, reason, allow_cancel_state=False):
    if enable > 0 and not self._cruise_available:
"""
  manual_signature = """  def _cruise_control(self, enable, cancel_timer, reason, allow_cancel_state=False, manual=False):
    # Explicit HID button requests bypass automatic-engage preferences only;
    # availability/interlocks below and selfdrived's normal no-entry checks remain.
    if enable > 0 and not self._cruise_available:
"""
  patched_signature = """  def _cruise_control(self, enable, cancel_timer, reason, allow_cancel_state=False, manual=False, allow_unavailable=False,
                      allow_auto_cruise_cancel_timer=False):
    # Manual Bluetooth/HID input keeps upstream's automatic-engage preference
    # bypass. The two allow_* exceptions below are reserved for independent
    # SoftHold and do not bypass steering or hold interlocks.
    if enable > 0 and not self._cruise_available and not allow_unavailable:
"""
  if patched_signature in text:
    text = text.replace(patched_signature, manual_signature, 1)
  elif manual_signature not in text and legacy_signature not in text:
    raise RuntimeError("Upstream code shape changed; refusing to guess: preserve cruise-control signature")

  patched_timer = """      if not manual and self.autoCruiseControl_cancel_timer > 0 and enable != 0 and not allow_auto_cruise_cancel_timer:
"""
  manual_timer = """      if not manual and self.autoCruiseControl_cancel_timer > 0 and enable != 0:
"""
  legacy_timer = """      if self.autoCruiseControl_cancel_timer > 0 and enable != 0:
"""
  if patched_timer in text:
    text = text.replace(patched_timer, manual_timer, 1)
  elif manual_timer not in text and legacy_timer not in text:
    raise RuntimeError("Upstream code shape changed; cannot preserve cruise cancel timer")

  hold_only = """  def _engage_soft_hold(self):
    self._soft_hold_active = 2
    self._add_log("Soft hold active (hold only)")
"""
  upstream_engage = """  def _engage_soft_hold(self):
    self._soft_hold_active = 2
    self._cruise_control(1, -1, "Cruise on (soft hold)", allow_cancel_state=self.soft_hold_on_cancel)
"""
  previous_patch_engage = """  def _engage_soft_hold(self):
    self._soft_hold_active = 2
    if self._cruise_cancel_state:
      self._add_log("Soft hold active (cancel state)")
      return
    self._cruise_control(1, -1, "Cruise on (soft hold)", allow_cancel_state=self.soft_hold_on_cancel,
                         allow_unavailable=True, allow_auto_cruise_cancel_timer=True)
"""
  if hold_only not in text:
    if previous_patch_engage in text:
      text = text.replace(previous_patch_engage, hold_only, 1)
    elif upstream_engage in text:
      text = text.replace(upstream_engage, hold_only, 1)
    else:
      raise RuntimeError("Upstream code shape changed; cannot safely patch SoftHold engagement")

  text = replace_once(
    text,
    """  def _update_cruise_buttons(self, CS, CC, v_cruise_kph):
    remote = self.bluetooth_commands.read(allowed=(CS.canValid and CS.cruiseState.available and
""",
    """  def _update_cruise_buttons(self, CS, CC, v_cruise_kph):
    if any(b.type == ButtonType.cancel and b.pressed for b in CS.buttonEvents):
      # selfdrived cancels on the press edge; latch here too instead of waiting
      # for the release/long-press decoder.
      self._cruise_cancel_state = True
    remote = self.bluetooth_commands.read(allowed=(CS.canValid and CS.cruiseState.available and
""",
    "latch physical cancel on the press edge",
  )

  text = replace_once(
    text,
    """    if self._soft_hold_active > 0:
      #self.events.append(EventName.softHold)
      #self._cruise_cancel_state = False
      pass

    if not self.disengage_on_accelerator and self._gas_tok and self.v_ego_kph_set >= self.autoGasTokSpeed:
""",
    """    if self._soft_hold_active > 0:
      # Keep the real CAN hold active without turning SoftHold itself into a
      # selfdrived enable request. Explicit SET/RES clears SoftHold earlier in
      # the button path; after gas release the normal CruiseOnDist logic remains.
      return self._auto_speed_up(v_cruise_kph)

    if not self.disengage_on_accelerator and self._gas_tok and self.v_ego_kph_set >= self.autoGasTokSpeed:
""",
    "block automatic cruise activation while SoftHold owns the stop",
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
  elif "soft_hold_available = self.autoCruiseControl != 0" not in text:
    raise RuntimeError("Upstream code shape changed; cannot preserve independent SoftHold arming")

  text = replace_once(
    text,
    '      elif self.params.get_bool("ActivateCruiseAfterBrake"):\n',
    '      elif self._soft_hold_active == 0 and self.params.get_bool("ActivateCruiseAfterBrake"):\n',
    "defer GM brake-resume request while independent SoftHold owns the stop",
  )
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
  # Preserve the upstream availability gate for ordinary ACC. Only independent
  # SoftHold may hold the vehicle while OEM cruise is unavailable.
  acc_control_enabled = ((enabled and CS.out.cruiseState.available) or soft_hold_active) and CS.paddle_button_prev == 0 and not interlock_active
""",
    "preserve CANFD SCC2 stop request for independent soft hold",
  )
  text = replace_once(
    text,
    """  soft_hold_active = CS.softHoldActive > 0 and CS.out.cruiseState.available
  acc_control_enabled = (enabled or soft_hold_active) and CS.out.cruiseState.available and not interlock_active
""",
    """  soft_hold_active = CS.softHoldActive > 0
  # Match the SCC2 path: ordinary ACC remains gated by OEM availability while
  # independent SoftHold retains its stop request.
  acc_control_enabled = ((enabled and CS.out.cruiseState.available) or soft_hold_active) and not interlock_active
""",
    "preserve CANFD SCC stop request for independent soft hold",
  )
  text = replace_once(
    text,
    "  soft_hold = CS.softHoldActive > 0 and CS.out.cruiseState.available\n",
    "  soft_hold = CS.softHoldActive > 0\n",
    "recognize independent SoftHold in CANFD stopping (including mandatory default)",
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
