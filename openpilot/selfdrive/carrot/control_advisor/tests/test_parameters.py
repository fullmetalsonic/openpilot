"""Validate the stage-0 parameter inventory against the unchanged FMS source."""

import ast
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[5]
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "parameters.json"
BASE_COMMIT = "1b1e019ee6bf5778be751373f269ed4f5d874cf2"
NUMERIC_KEYS = {
  "LateralTorqueAccelFactor", "LateralTorqueFriction", "LateralTorqueKpV",
  "LateralTorqueKiV", "LateralTorqueKf", "LateralTorqueKd", "SteerActuatorDelay",
  "LatSmoothSec", "LongActuatorDelay", "LatMpcPathCost", "LatMpcMotionCost",
  "LatMpcAccelCost", "LatMpcJerkCost", "LatMpcSteeringRateCost", "LongTuningKpV",
  "LongTuningKiV", "LongTuningKf", "DynamicTFollowLC", "SpeedTFFactor",
  "TFollowDecelBoost", "LeadAccelResponse", "StoppingAccel", "VEgoStopping",
  "StopDistanceCarrot", "TrafficStopDistanceAdjust", "PathOffset", "SteerRatioRate",
  *(f"CruiseMaxVals{i}" for i in range(7)),
  *(f"TFollowGap{i}" for i in range(1, 5)),
  *(f"LeadAccelResponseTF{i}" for i in range(1, 5)),
}
CONTEXT_KEYS = {"LateralTorqueCustom", "NNFF", "NNFFLite", "LatMpcInputOffset", "CustomSR"}


def source_symbols(path):
  symbols = set()

  def visit(node, prefix=""):
    for child in ast.iter_child_nodes(node):
      if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        name = f"{prefix}.{child.name}" if prefix else child.name
        symbols.add(name)
        visit(child, name)
      else:
        visit(child, prefix)

  visit(ast.parse(path.read_text(encoding="utf-8")))
  return symbols


class ParameterInventoryTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cls.items = cls.manifest["items"]
    cls.by_key = {item["key"]: item for item in cls.items}
    cls.schema_bytes = (ROOT / cls.manifest["metadata"]["source_schema"]).read_bytes()
    cls.schema = {item["name"]: item for item in json.loads(cls.schema_bytes)["params"]}

  def test_complete_unique_expanded_scope(self):
    self.assertEqual(len(self.items), len(self.by_key))
    self.assertEqual(set(self.by_key), NUMERIC_KEYS | CONTEXT_KEYS)
    self.assertEqual(len(NUMERIC_KEYS), 42)
    for key, item in self.by_key.items():
      with self.subTest(key=key):
        expected = "context_only" if key in CONTEXT_KEYS else "numeric_parameter"
        self.assertEqual(item["classification"], expected)

  def test_metadata_does_not_grant_change_or_validation_authority(self):
    metadata = self.manifest["metadata"]
    self.assertEqual(metadata["schema_version"], 1)
    self.assertEqual(metadata["base_commit"], BASE_COMMIT)
    self.assertEqual(metadata["source_schema_sha256"], hashlib.sha256(self.schema_bytes).hexdigest())
    self.assertFalse(metadata["ui_ranges_are_safe_change_limits"])
    self.assertIsNone(metadata["single_change_limits"])
    self.assertIsNone(metadata["cumulative_change_limits"])
    self.assertEqual(metadata["validation_profiles"], [])
    for item in self.items:
      with self.subTest(key=item["key"]):
        self.assertEqual(item["implementation_status"], "not_implemented")
        self.assertEqual(item["validation_status"], "not_validated")
        self.assertNotEqual(item.get("apply_enabled"), True)

  def test_ui_definitions_are_exact_source_values(self):
    for item in self.items:
      with self.subTest(key=item["key"]):
        source = self.schema.get(item["key"])
        if source is None:
          self.assertIn(item["key"], {"NNFFLite", "CustomSR"})
          self.assertIsNone(item["ui"])
        else:
          self.assertEqual(item["ui"], {
            "min": source["min"], "max": source["max"], "step": source["unit"],
          })

  def test_consumer_paths_symbols_and_parameter_keys_exist(self):
    sources = {}
    for item in self.items:
      with self.subTest(key=item["key"]):
        self.assertTrue(item["consumers"])
        self.assertTrue(item["path_notes"])
        found_key = False
        for consumer in item["consumers"]:
          relative = Path(consumer["path"])
          self.assertFalse(relative.is_absolute())
          self.assertNotIn("..", relative.parts)
          path = ROOT / relative
          self.assertTrue(path.is_file(), str(path))
          if path not in sources:
            sources[path] = (path.read_text(encoding="utf-8"), source_symbols(path))
          text, symbols = sources[path]
          self.assertIn(consumer["symbol"], symbols)
          found_key |= item["key"] in text
          if item["key"].startswith("LeadAccelResponseTF"):
            found_key |= 'LeadAccelResponseTF{i}' in text
        self.assertTrue(found_key, "No source-backed parameter read")

  def test_dependencies_and_required_signal_contracts(self):
    for item in self.items:
      with self.subTest(key=item["key"]):
        self.assertIn(item["axis"], {"lateral", "longitudinal"})
        self.assertIsInstance(item["family"], str)
        self.assertIsInstance(item["special_values"], dict)
        self.assertTrue(item["required_signals"])
        for field in ("requires", "dependencies", "required_signals"):
          self.assertTrue(all(isinstance(value, str) and value for value in item[field]))
        self.assertTrue(set(item["dependencies"]) <= self.by_key.keys())
        self.assertIsInstance(item["raw_scale"], (int, float))
        self.assertGreater(item["raw_scale"], 0)

  def test_selected_scales_match_consuming_code(self):
    checks = [
      ("LateralTorqueAccelFactor", 2500, 2.5, "get_float(\"LateralTorqueAccelFactor\")*0.001"),
      ("LateralTorqueFriction", 100, .1, "get_float(\"LateralTorqueFriction\")*0.001"),
      ("LateralTorqueKpV", 100, 1., "get_float(\"LateralTorqueKpV\")*0.01"),
      ("LateralTorqueKiV", 10, .1, "get_float(\"LateralTorqueKiV\")*0.01"),
      ("LateralTorqueKf", 100, 1., "get_float(\"LateralTorqueKf\")*0.01"),
      ("LongTuningKiV", 100, .1, "get_float(\"LongTuningKiV\")*0.001"),
      ("StoppingAccel", -50, -.5, "*0.01"),
      ("VEgoStopping", 1, .01, "get_float(\"VEgoStopping\")*0.01"),
      ("StopDistanceCarrot", 520, 5.2, "get_float(\"StopDistanceCarrot\")/100."),
      ("TrafficStopDistanceAdjust", -150, -1.5, "get_float(\"TrafficStopDistanceAdjust\")/100."),
    ]
    for key, raw, physical, source_fragment in checks:
      with self.subTest(key=key):
        item = self.by_key[key]
        self.assertAlmostEqual(raw * item["raw_scale"], physical)
        sources = "\n".join((ROOT / c["path"]).read_text(encoding="utf-8") for c in item["consumers"])
        self.assertIn(source_fragment, "".join(sources.split()))
    self.assertEqual(self.by_key["LatMpcSteeringRateCost"]["raw_scale"], 1)
    self.assertEqual(self.by_key["SpeedTFFactor"]["raw_scale"], .1)
    self.assertEqual(self.by_key["SteerRatioRate"]["raw_scale"], .01)
    self.assertEqual(self.by_key["LatMpcInputOffset"]["physical_unit"], "1")

  def test_special_modes_and_structural_blockers(self):
    self.assertIn("liveDelay", self.by_key["SteerActuatorDelay"]["special_values"]["0"])
    for index in range(1, 5):
      self.assertIn("Inherit", self.by_key[f"LeadAccelResponseTF{index}"]["special_values"]["-1"])
    self.assertNotIn("0", self.by_key["LeadAccelResponse"]["special_values"])
    self.assertIn("error_rate", self.by_key["LateralTorqueKd"]["structural_blocker"])
    for key in ("LongTuningKpV", "LongTuningKiV", "LongTuningKf"):
      self.assertIn("hyundai", self.by_key[key]["requires"][0])
      self.assertIn("toyota", self.by_key[key]["requires"][0])
    self.assertEqual(self.by_key["VEgoStopping"]["ui"]["min"], 1)
    self.assertEqual(self.by_key["StoppingAccel"]["physical_unit"], "m/s^2")
    self.assertEqual(self.by_key["TrafficStopDistanceAdjust"]["physical_unit"], "m")


if __name__ == "__main__":
  unittest.main()
