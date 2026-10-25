"""Synthetic math checks; device/profile qualification is deliberately absent."""

import ast
import builtins
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import unittest
from unittest import mock

try:
  import numpy as np
except ImportError:
  np = None


MODULE_PATH = Path(__file__).parents[1] / "torque_fit.py"
SOURCE_PATH = Path(__file__).parents[5] / "openpilot/selfdrive/locationd/torqued.py"


def load_adapter(name="advisor_torque_fit_under_test"):
  spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


adapter = load_adapter()


def fit(points, **changes):
  options = {"max_input_points": 1000, "max_fit_points": 1000, "seed": 17}
  options.update(changes)
  return adapter.estimate_torque(points, **options)


def reference_formula():
  # Extract only constants and math AST. Never import TorqueEstimator/Params.
  source = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
  friction = next(node for node in source.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "FRICTION_FACTOR" for target in node.targets))
  slope = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "slope2rot")
  estimator = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "TorqueEstimator")
  estimate = next(node for node in estimator.body if isinstance(node, ast.FunctionDef) and node.name == "estimate_params")
  math_block = next(node for node in estimate.body if isinstance(node, ast.Try)).body
  fingerprint = hashlib.sha256(ast.dump(ast.Module(body=[friction, *slope.body, *math_block], type_ignores=[]),
                                       include_attributes=False).encode()).hexdigest()
  function = ast.FunctionDef(name="reference_estimate",
                            args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="points")], kwonlyargs=[],
                                               kw_defaults=[], defaults=[]),
                            body=[*math_block, ast.Return(value=ast.Tuple(elts=[ast.Name(id=name, ctx=ast.Load())
                                                                              for name in ("slope", "offset", "friction_coeff")],
                                                                         ctx=ast.Load()))], decorator_list=[])
  namespace = {"np": np}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[friction, slope, function], type_ignores=[])),
               "torqued_math_only", "exec"), namespace)
  return fingerprint, namespace["reference_estimate"]


class TorqueFitTest(unittest.TestCase):
  @unittest.skipUnless(np is not None, "NumPy runtime required for raw fit math")
  def test_known_synthetic_line_is_raw_and_not_a_recommendation(self):
    points = [(x / 10, 3 * x / 10 + 0.15) for x in range(-4, 5)]
    result = fit(points)
    self.assertEqual(result["status"], "available")
    self.assertAlmostEqual(result["raw_fit"]["latAccelFactor"], 3.0, places=13)
    self.assertAlmostEqual(result["raw_fit"]["latAccelOffset"], 0.15, places=13)
    self.assertLess(result["raw_fit"]["frictionCoefficient"], 1e-13)
    self.assertEqual(result["scope"], "raw_fit_only")
    self.assertIsNone(result["recommendation"])
    self.assertFalse(result["apply_enabled"])
    self.assertFalse(result["clipping_applied"])
    self.assertFalse(result["filtering_applied"])
    self.assertFalse(result["profile_qualified"])
    self.assertEqual(result["numpy_version"], np.__version__)
    json.dumps(result, allow_nan=False)

  @unittest.skipUnless(np is not None, "NumPy runtime required for raw fit math")
  def test_source_ast_hash_and_positive_negative_slope_math_match(self):
    fingerprint, reference = reference_formula()
    self.assertEqual(fingerprint, adapter.SOURCE_FORMULA_AST_SHA256)
    for slope in (2.8, -2.3):
      points = [(i / 50, slope * i / 50 + 0.12 + 0.04 * math.sin(i * 1.7)) for i in range(-20, 21)]
      result = fit(points)
      self.assertEqual(result["status"], "available")
      matrix = np.array([[x, 1.0, y] for x, y in points])
      expected = reference(matrix)
      np.testing.assert_allclose([result["raw_fit"][name]
                                  for name in ("latAccelFactor", "latAccelOffset", "frictionCoefficient")],
                                 expected, rtol=1e-13, atol=1e-13)
      self.assertEqual(result["formula"]["source_formula_ast_sha256"], fingerprint)
      self.assertEqual(result["formula"]["friction_factor"], 1.5)

  @unittest.skipUnless(np is not None, "NumPy runtime required for raw fit math")
  def test_loading_and_fitting_need_no_params_or_production_imports(self):
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
      if name.startswith("openpilot") or name in ("cereal", "messaging"):
        raise AssertionError(f"production/Params imports are forbidden: {name}")
      return original_import(name, *args, **kwargs)
    with mock.patch("builtins.__import__", side_effect=guarded_import):
      pure = load_adapter("advisor_torque_pure_guarded")
      result = pure.estimate_torque([(-0.2, -0.4), (0.0, 0.0), (0.2, 0.4)],
                                    max_input_points=3, max_fit_points=3, seed=0)
    self.assertEqual(result["status"], "available")

  @unittest.skipUnless(np is not None, "NumPy runtime required for raw fit math")
  def test_selection_is_deterministic_bounded_and_preserves_inputs(self):
    points = [(i / 200, 2.5 * i / 200 + 0.1 + math.sin(i) * 0.05) for i in range(-100, 100)]
    original_points = list(points)
    first = fit(iter(points), max_input_points=200, max_fit_points=50, seed=7)
    second = fit(iter(points), max_input_points=200, max_fit_points=50, seed=7)
    different = fit(iter(points), max_input_points=200, max_fit_points=50, seed=11)
    self.assertEqual(first, second)
    self.assertNotEqual(first["selected_indices"], different["selected_indices"])
    self.assertEqual((first["input_count"], first["used_count"]), (200, 50))
    self.assertEqual(first["selected_indices"], sorted(first["selected_indices"]))
    self.assertEqual(points, original_points)

  def test_invalid_inputs_fail_instead_of_truncating_or_zero_filling(self):
    consumed = []
    def many_points():
      for i in range(100):
        consumed.append(i)
        yield (float(i), float(i))
    with self.assertRaisesRegex(ValueError, "max_input_points"):
      fit(many_points(), max_input_points=3)
    self.assertEqual(consumed, [0, 1, 2, 3])
    for point in ((True, 1.0), (0.0, float("nan")), (float("inf"), 1.0), ("1", 2.0), (1.0,), "12"):
      with self.subTest(point=point), self.assertRaises(ValueError):
        fit([point])
    for options in ({"seed": True}, {"seed": 1.5}, {"max_fit_points": 2},
                    {"max_input_points": False}, {"max_input_points": -1}):
      with self.subTest(options=options), self.assertRaises(ValueError):
        fit([], **options)

  @unittest.skipUnless(np is not None, "NumPy runtime required for raw fit math")
  def test_insufficient_or_numerically_degenerate_data_are_unavailable(self):
    cases = ([], [(0.0, 0.0), (1.0, 1.0)], [(1.0, float(i)) for i in range(4)],
             [(float(i), 1.0) for i in range(4)],
             [(-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)],
             [(-0.01, -2.0), (-0.01, 2.0), (0.01, -2.0), (0.01, 2.0)])
    reasons = set()
    for points in cases:
      result = fit(points)
      with self.subTest(points=points):
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["raw_fit"])
        self.assertIsNone(result["recommendation"])
        self.assertFalse(result["apply_enabled"])
        self.assertIsNotNone(result["reason"])
        json.dumps(result, allow_nan=False)
      reasons.add(result["reason"])
    self.assertIn("insufficient_points", reasons)
    self.assertIn("insufficient_numerical_spread", reasons)
    self.assertIn("ambiguous_tls_plane", reasons)
    self.assertIn("vertical_tls_solution", reasons)

  def test_numpy_is_lazy_and_missing_dependency_is_explicit(self):
    original_import = builtins.__import__
    attempts = []
    def missing_numpy(name, *args, **kwargs):
      if name == "numpy":
        attempts.append(name)
        raise ImportError("synthetic missing NumPy")
      return original_import(name, *args, **kwargs)
    with mock.patch("builtins.__import__", side_effect=missing_numpy):
      pure = load_adapter("advisor_torque_without_numpy")
      self.assertEqual(attempts, [])
      result = pure.estimate_torque([(-0.2, -0.4), (0.0, 0.0), (0.2, 0.4)],
                                    max_input_points=3, max_fit_points=3, seed=0)
    self.assertEqual(attempts, ["numpy"])
    self.assertEqual(result["reason"], "numpy_unavailable")
    self.assertIsNone(result["numpy_version"])
    self.assertIsNone(result["raw_fit"])

  @unittest.skipUnless(np is not None, "NumPy runtime required for raw fit math")
  def test_solver_failure_is_not_a_numeric_fit(self):
    with mock.patch.object(np.linalg, "svd", side_effect=np.linalg.LinAlgError("synthetic solver failure")):
      result = fit([(-0.2, -0.4), (0.0, 0.0), (0.2, 0.4)])
    self.assertEqual(result["reason"], "numerical_failure")
    self.assertIsNone(result["raw_fit"])
    self.assertFalse(result["profile_qualified"])


if __name__ == "__main__":
  unittest.main()
