"""Pure raw TLS math from torqued; no Params, caches or daemon construction.

Inputs must already be time-aligned, independently observed and filtered by the
caller. The x coordinate follows torqued: negative carOutput actuator torque,
not a measured motor torque in N m. The y coordinate is independent lateral
acceleration in torqued's roll/sign convention. This adapter neither prepares
those observations nor estimates/increases the vehicle's maximum torque.
"""

import math
from numbers import Real
import platform
import random
from typing import Iterable


FRICTION_FACTOR = 1.5
SOURCE_FORMULA_AST_SHA256 = "79863c97d1ab0fda79d95093ea193d0bc607539ab67b720f34cea5a227f32058"


def _slope2rot(slope, np):
  sin = np.sqrt(slope ** 2 / (slope ** 2 + 1))
  cos = np.sqrt(1 / (slope ** 2 + 1))
  return np.array([[cos, -sin], [sin, cos]])


def estimate_torque(points: Iterable[tuple[float, float]], *, max_input_points: int,
                    max_fit_points: int, seed: int) -> dict:
  """Return an unclipped raw fit, or an explicit numerical/unavailable reason.

  The caller sets both count limits; they are not device-qualified budgets.
  Numerical rank tests use floating-point resolution, not driving thresholds.
  No uncertainty, live validity or recommendation is inferred from this fit.
  """
  for name, value in (("max_input_points", max_input_points), ("max_fit_points", max_fit_points)):
    if type(value) is not int or value < 3:
      raise ValueError(f"{name} must be an integer of at least 3")
  if type(seed) is not int:
    raise ValueError("seed must be an integer")
  collected = []
  for point in points:
    if len(collected) >= max_input_points:
      raise ValueError("max_input_points exceeded; input was not silently truncated")
    if not isinstance(point, (tuple, list)) or len(point) != 2:
      raise ValueError("each point must contain exactly two numeric coordinates")
    normalized = []
    for value in point:
      if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("point coordinates must be finite numbers, not booleans")
      try:
        number = float(value)
      except (OverflowError, ValueError) as exc:
        raise ValueError("point coordinates must be finite floats") from exc
      if not math.isfinite(number):
        raise ValueError("point coordinates must be finite floats")
      normalized.append(number)
    collected.append(tuple(normalized))

  count = len(collected)
  selected = sorted(random.Random(seed).sample(range(count), max_fit_points)) if count > max_fit_points else list(range(count))
  result = {
    "schema_version": 1,
    "status": "unavailable",
    "scope": "raw_fit_only",
    "raw_fit": None,
    "clipping_applied": False,
    "filtering_applied": False,
    "reason": None,
    "input_count": count,
    "used_count": len(selected),
    "selected_indices": selected,
    "seed": seed,
    "selection_version": "python-random-sample-sorted-v1",
    "python_version": platform.python_version(),
    "numpy_version": None,
    "formula": {
      "source_path": "openpilot/selfdrive/locationd/torqued.py",
      "source_formula_ast_sha256": SOURCE_FORMULA_AST_SHA256,
      "friction_factor": FRICTION_FACTOR,
      "adapter_version": 1,
    },
    "recommendation": None,
    "apply_enabled": False,
    "profile_qualified": False,
  }
  if count < 3:
    result["reason"] = "insufficient_points"
    return result
  try:
    import numpy as np
  except ImportError:
    result["reason"] = "numpy_unavailable"
    return result
  result["numpy_version"] = np.__version__
  matrix = np.array([[collected[index][0], 1.0, collected[index][1]] for index in selected], dtype=float)
  epsilon = np.finfo(float).eps
  try:
    with np.errstate(over="raise", invalid="raise", divide="raise"):
      coordinates = matrix[:, [0, 2]]
      resolution = epsilon * max(matrix.shape) * np.maximum(1.0, np.max(np.abs(coordinates), axis=0))
      if np.any(np.ptp(coordinates, axis=0) <= resolution):
        result["reason"] = "insufficient_numerical_spread"
        return result
      # This SVD and the raw math below retain the production estimator's
      # uncentered [steer, 1, lateral_accel] TLS convention, including its sign.
      _, singular_values, v = np.linalg.svd(matrix, full_matrices=False)
      tolerance = singular_values[0] * max(matrix.shape) * epsilon
      if singular_values[1] <= tolerance:
        result["reason"] = "insufficient_matrix_rank"
        return result
      if singular_values[1] - singular_values[2] <= tolerance:
        result["reason"] = "ambiguous_tls_plane"
        return result
      if abs(v.T[2, 2]) <= max(matrix.shape) * epsilon:
        result["reason"] = "vertical_tls_solution"
        return result
      slope, offset = -v.T[0:2, 2] / v.T[2, 2]
      _, spread = np.matmul(matrix[:, [0, 2]], _slope2rot(slope, np)).T
      friction_coeff = np.std(spread) * FRICTION_FACTOR
      values = (float(slope), float(offset), float(friction_coeff))
  except (np.linalg.LinAlgError, FloatingPointError, OverflowError):
    result["reason"] = "numerical_failure"
    return result
  if not all(math.isfinite(value) for value in values):
    result["reason"] = "nonfinite_fit"
    return result
  result["status"] = "available"
  result["raw_fit"] = dict(zip(("latAccelFactor", "latAccelOffset", "frictionCoefficient"), values))
  return result
