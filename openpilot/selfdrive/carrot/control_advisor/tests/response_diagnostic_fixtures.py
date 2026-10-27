"""Independent forward generators, not equations copied from the ARX estimator."""
import math

import numpy as np


def response_rows(event_count=8, *, noise=0., second_order=False, saturated=False, missing_time=False, amplitudes=None):
  period, step = 9., .01
  times = np.arange(0., event_count * period, step)
  command = np.zeros(len(times))
  for event in range(event_count):
    active = (times >= event * period + .8) & (times < event * period + 3.)
    command[active] = (1 if event % 2 == 0 else -1) * (amplitudes[event] if amplitudes is not None else 1 + .05 * event)

  def forward(source, lag, tau):
    result, state = np.zeros(len(source)), 0.
    delayed = np.concatenate((np.zeros(round(lag / step)), source))[:len(source)]
    for index, forcing in enumerate(delayed):
      # Ten fine Euler integration substeps, independent of the fitted ARX formula.
      for _ in range(10):
        state += (forcing - state) / tau * (step / 10)
      result[index] = state
    return result

  sent = forward(command, .05, .08)
  steering = forward(sent, .2, .3)
  yaw = forward(steering, .1, .15) * .01
  actual = forward(sent, .2, .3)
  if second_order:
    actual = forward(actual, 0., .22)
  if noise:
    actual += np.random.default_rng(903).normal(0., noise, len(actual))
  rows = []
  for index, time in enumerate(times):
    stamp = 1_000_000_000 + index * 10_000_000
    for axis in ("lateral", "longitudinal"):
      rows.append({"axis": axis, "mono_ns": stamp, "target": float(command[index]),
        "actual": float(steering[index] if axis == "lateral" else actual[index]), "target_mono_ns": stamp,
        "actual_mono_ns": stamp, "independent_actual": float(yaw[index] * 15), "independent_mono_ns": stamp,
        "sent_request": float(sent[index]), "sent_mono_ns": None if missing_time else stamp,
        "steering_angle_deg": float(steering[index]), "steering_mono_ns": stamp,
        "yaw_rate": float(yaw[index]), "yaw_mono_ns": stamp, "v_ego": 15., "saturated": saturated,
        "excluded_reasons": []})
  return rows


def smoothing_context(index, *, base=.1, y_std=.15):
  raw, previous = (1 if index % 2 == 0 else -1) * .002, 0.
  dynamic = min(.6, base * (1 + 2 * min(1., max(0., (y_std - .15) / .1))))
  alpha = -math.expm1(-.05 / dynamic)
  return {"frame": index * 180, "lat_action_t": .4, "lat_delay_used_s": .325, "lat_smooth_dynamic_s": dynamic,
    "lat_smooth_y_std": y_std, "lat_pre_smooth_curvature": raw, "lat_previous_curvature": previous,
    "lat_post_smooth_curvature": alpha * raw + (1 - alpha) * previous, "v_ego": 15., "lat_smoothing_applied": True}
