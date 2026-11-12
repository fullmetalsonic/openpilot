import math

import numpy as np

from openpilot.common.realtime import DT_CTRL


_SAMPLE_X = np.arange(0., 46., 5.)
_LOOKAHEAD = np.array([2, 4, 6])
_MAX_SAMPLE_AGE_NS = 150_000_000
_MAX_FRAME_GAP_NS = 105_000_000
_STABLE_TIME_NS = 450_000_000
_MIN_CURVATURE = .00055


def _sample_y(points):
  x = np.asarray(points.x, dtype=float)
  y = np.asarray(points.y, dtype=float)
  if len(x) < 2 or x.shape != y.shape:
    return np.full_like(_SAMPLE_X, np.nan)
  valid = np.isfinite(x) & np.isfinite(y)
  valid[1:] &= np.diff(x) > 1e-4
  invalid = np.flatnonzero(~valid)
  count = invalid[0] if len(invalid) else len(x)
  if count < 2:
    return np.full_like(_SAMPLE_X, np.nan)
  return np.interp(_SAMPLE_X, x[:count], y[:count], left=np.nan, right=np.nan)


class LanelessCurveCentering:
  def __init__(self):
    self.reset()

  def reset(self):
    self._trim = 0.
    self._update_ns = None
    self._frame_ns = None
    self._good_since_ns = None
    self._previous = None
    self._inward_offset = 0.
    self._normal = 1.

  def _observe(self, model, frame_ns, yaw_rate, speed):
    old = self._previous
    self._previous = None
    if len(model.laneLines) < 3 or len(model.laneLineProbs) < 3 or len(model.laneLineStds) < 3:
      return False

    left, right = (_sample_y(model.laneLines[i]) for i in (1, 2))
    center = (left + right) * .5
    if not np.all(np.isfinite(center)):
      return False
    self._previous = (center, frame_ns, yaw_rate, speed)
    if old is None:
      return False
    previous_center, previous_ns, previous_yaw, previous_speed = old
    gap = frame_ns - previous_ns
    if not 0 < gap <= _MAX_FRAME_GAP_NS:
      return False

    probs = np.array([model.laneLineProbs[i] for i in (1, 2)])
    stds = np.array([model.laneLineStds[i] for i in (1, 2)])
    if not (np.all((.6 <= probs) & (probs <= 1.)) and np.all((0. <= stds) & (stds <= .25))):
      return False

    slope = np.gradient(center, _SAMPLE_X)
    normal = 1. / np.sqrt(1. + slope**2)
    width = ((right - left) * normal)[_LOOKAHEAD]
    if np.min(width) < 2.5 or np.max(width) > 4.5 or np.ptp(width) > .65:
      return False

    # Compare the same road after accounting for the motion between model frames.
    dt = gap * 1e-9
    turn = dt * (yaw_rate + previous_yaw) * .5
    dx = dt * (speed + previous_speed) * .5
    dy = dx * turn * .5
    cos_turn, sin_turn = math.cos(turn), math.sin(turn)
    prior_x = cos_turn * (_SAMPLE_X - dx) + sin_turn * (previous_center - dy)
    prior_y = -sin_turn * (_SAMPLE_X - dx) + cos_turn * (previous_center - dy)
    lookahead_x = _SAMPLE_X[_LOOKAHEAD]
    if np.any(np.diff(prior_x) <= 0.) or prior_x[0] > lookahead_x[0] or prior_x[-1] < lookahead_x[-1]:
      return False
    if np.max(np.abs(np.interp(lookahead_x, prior_x, prior_y) - center[_LOOKAHEAD])) > .15:
      return False

    curvature = float(model.action.desiredCurvature)
    sign = math.copysign(1., curvature)
    near, middle, far = _LOOKAHEAD
    center_curvature = (center[far] - 2. * center[middle] + center[near]) * normal[middle]**3 / 100.
    if abs(curvature) < _MIN_CURVATURE or yaw_rate * sign < .02 or center_curvature * sign < .0006:
      return False

    displacement = ((_sample_y(model.position) - center) * normal * sign)[_LOOKAHEAD]
    if not np.all(displacement > [.025, .05, .025]):
      return False
    self._inward_offset = float(displacement[1]) * sign
    self._normal = float(normal[middle])
    return True

  def update(self, model, frame_ns, now_ns, yaw_rate, speed, gain_percent):
    elapsed_ns = DT_CTRL * 1e9 if self._update_ns is None else now_ns - self._update_ns
    if (not math.isfinite(speed) or gain_percent <= 0 or
        not 0 < elapsed_ns <= _MAX_FRAME_GAP_NS):
      self.reset()
      return 0.
    self._update_ns = now_ns
    dt = elapsed_ns * 1e-9
    curvature = float(model.action.desiredCurvature) if model is not None else 0.
    target = 0.

    valid = (model is not None and 0 < frame_ns <= now_ns and now_ns - frame_ns <= _MAX_SAMPLE_AGE_NS and
             yaw_rate is not None and math.isfinite(yaw_rate) and abs(yaw_rate) <= 2. and
             math.isfinite(curvature) and speed >= 5.)
    if not valid or (self._frame_ns is not None and frame_ns < self._frame_ns):
      self._previous = self._frame_ns = self._good_since_ns = None
    elif frame_ns != self._frame_ns:
      self._frame_ns = frame_ns
      if self._observe(model, frame_ns, yaw_rate, speed):
        if self._good_since_ns is None:
          self._good_since_ns = frame_ns
      else:
        self._good_since_ns = None

    speed_squared = max(5., speed)**2
    if (self._good_since_ns is not None and frame_ns - self._good_since_ns >= _STABLE_TIME_NS and
        self._inward_offset * curvature > 0. and self._inward_offset * yaw_rate > 0.):
      # The 20 m lateral displacement is approximately 0.5 * distance**2 * delta curvature.
      scale = 200. * self._normal
      magnitude = min(abs(self._inward_offset) * min(gain_percent, 100) / (100. * scale),
                      .12 / scale, .35 / speed_squared, .25 * abs(curvature))
      target = -math.copysign(magnitude, self._inward_offset)

    tau = .65 if abs(target) > abs(self._trim) else .8
    step = (target - self._trim) * -math.expm1(-dt / tau)
    max_step = .45 * dt / speed_squared
    max_trim = .35 / speed_squared
    self._trim = max(-max_trim, min(max_trim, self._trim + max(-max_step, min(max_step, step))))
    if abs(curvature) >= _MIN_CURVATURE and self._trim * curvature > 0.:
      self._trim = 0.
    if target == 0. and abs(self._trim) < 1e-7:
      self._trim = 0.
    return self._trim
