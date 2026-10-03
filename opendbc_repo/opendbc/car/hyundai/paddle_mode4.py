"""Narrow MQ4 normal-ACC paddle gesture permission; raw CAN is never masked."""
from dataclasses import dataclass
from enum import Enum, auto
import math


MAX_PADDLE_AGE_NS = 100_000_000


@dataclass(frozen=True)
class PaddleInput:
  sequence: int
  source: str | None
  timestamp: int
  samples: tuple[tuple[int, int, int], ...]
  valid: bool
  cancel_seen: bool = False


@dataclass(frozen=True)
class PaddleContext:
  mode: int
  supported: bool
  generation: int
  timestamp: int
  eligible: bool


def read_paddle_input(cp, source, cancel_source, sequence):
  """Copy parser-accepted samples (counter and configured checksum checks)."""
  invalid = PaddleInput(sequence, source, 0, (), False)
  if source is None or source not in cp.vl:
    return invalid
  values = cp.vl_all[source]
  left, right = values.get("LEFT_PADDLE", []), values.get("RIGHT_PADDLE", [])
  state = cp.message_states[cp.dbc.name_to_msg[source].address]
  if len(cp.dat.get(state.address, b"")) != state.size:
    return invalid
  times = list(state.timestamps)
  latest = cp.ts_nanos[source]
  timestamp = latest.get("LEFT_PADDLE", 0)
  if (len(left) != len(right) or len(left) > len(times) or len(left) > 500
      or timestamp <= 0 or timestamp != latest.get("RIGHT_PADDLE", 0)
      or not times or timestamp != times[-1]
      or state.counter_fail >= 5):
    return invalid
  samples = tuple(zip(times[-len(left):], left, right)) if left else ()
  if (any(l not in (0, 1) or r not in (0, 1) for _, l, r in samples)
      or any(a[0] > b[0] for a, b in zip(samples, samples[1:]))):
    return invalid
  # CANCEL may be followed by release in the same receive batch. The original
  # buttonEvents exposes only the final transition, so retain this veto too.
  cancel_seen = 4 in cp.vl_all.get(cancel_source, {}).get("CRUISE_BUTTONS", [])
  return PaddleInput(sequence, source, timestamp, samples, True, cancel_seen)


def normal_acc_eligible(CC, out):
  return bool(CC.enabled and CC.longActive
              and str(CC.actuators.longControlState) in ("pid", "stopping", "starting")
              and not CC.cruiseControl.cancel and not CC.cruiseControl.override
              and out.canValid and out.cruiseState.available and not out.accFaulted
              and str(out.gearShifter) == "drive"
              and not (out.brakePressed or out.gasPressed or out.brakeHoldActive or out.parkingBrake)
              and not out.carrotCruise
              and all(math.isfinite(v) for v in (out.vEgo, out.aEgo, CC.actuators.accel, CC.actuators.jerk)))


def make_paddle_context(previous, helper, state, CC, controls_valid, now_nanos):
  """Run on every card state update, including updates with no CI.apply."""
  mode = helper._paddle_mode
  supported = helper.CP.openpilotLongitudinalControl and helper.params.get_int("LongitudinalPersonalityMax") == 4
  sample = getattr(state, "paddle_input", None)
  eligible = bool(controls_valid and mode == 4 and supported
                  and not (helper._cruise_cancel_state or helper._activate_cruise < 0
                           or helper._paddle_decel_active or helper.carrot_cruise_active)
                  and sample is not None and sample.valid and not sample.cancel_seen
                  and getattr(state, "scc_control", None) is not None
                  and 0 <= now_nanos - sample.timestamp <= MAX_PADDLE_AGE_NS
                  and normal_acc_eligible(CC, state.out))
  generation = 0 if previous is None else previous.generation
  if not eligible or (previous is not None and now_nanos < previous.timestamp):
    generation += 1
  return PaddleContext(mode, bool(supported), generation, now_nanos, eligible)


class GestureState(Enum):
  WAIT_RELEASE = auto()
  READY = auto()
  ACCEPTED = auto()


class PaddleGesture:
  def __init__(self):
    self.state = GestureState.WAIT_RELEASE
    self.source = None
    self.sequence = -1
    self.timestamp = 0
    self.generation = None
    self.now = 0

  def update(self, sample, context, eligible, now_nanos, paddle_button):
    changed = (self.generation is not None and context is not None and self.generation != context.generation)
    source_changed = sample is not None and self.source is not None and sample.source != self.source
    clock_bad = (now_nanos < self.now or (self.now > 0 and now_nanos - self.now > MAX_PADDLE_AGE_NS)
                 or (sample is not None and sample.timestamp < self.timestamp))
    valid = bool(eligible and sample is not None and sample.valid and context is not None and context.eligible
                 and 0 <= now_nanos - sample.timestamp <= MAX_PADDLE_AGE_NS
                 and 0 <= now_nanos - context.timestamp <= MAX_PADDLE_AGE_NS)
    self.now = now_nanos
    self.generation = None if context is None else context.generation
    if sample is not None:
      self.source = sample.source
    if not valid or changed or source_changed or clock_bad:
      self.state = GestureState.WAIT_RELEASE
      if sample is not None:
        self.sequence, self.timestamp = sample.sequence, sample.timestamp
      return False  # A veto's own batch can never rearm the gesture.
    if sample.sequence < self.sequence:
      self.state = GestureState.WAIT_RELEASE
      self.sequence, self.timestamp = sample.sequence, sample.timestamp
      return False
    if sample.sequence != self.sequence:
      for timestamp, left, right in sample.samples:
        # Equal timestamps within one packet are ordered by the CAN batch.
        # A timestamp already consumed by a previous update is not a release.
        if timestamp <= self.timestamp:
          continue
        if not left and not right:
          self.state = GestureState.READY
        elif self.state == GestureState.READY:
          self.state = GestureState.ACCEPTED
      self.sequence, self.timestamp = sample.sequence, sample.timestamp
    return self.state == GestureState.ACCEPTED and paddle_button != 0
