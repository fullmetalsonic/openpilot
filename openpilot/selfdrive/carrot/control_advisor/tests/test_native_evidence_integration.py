"""Typed native evidence boundaries; observational results remain unqualified."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor import candidates, policy_observations as policy
from openpilot.selfdrive.carrot.control_advisor.candidates import candidate_capabilities
from openpilot.selfdrive.carrot.control_advisor.journal_snapshot import _pin_pid_chunks
from openpilot.selfdrive.carrot.control_advisor.lateral_observations import LateralObservationBuilder
from openpilot.selfdrive.carrot.control_advisor.log_candidates import _objectives, _sampling_policy
from openpilot.selfdrive.carrot.control_advisor.pid_burst import PidBurstStore
from openpilot.selfdrive.carrot.control_advisor.tests.test_candidates import candidate_fixture
from openpilot.selfdrive.carrot.control_advisor.tests.test_lateral_observations import source_cp, fixture_events, ANCHOR, RESPONSE
from openpilot.selfdrive.carrot.control_advisor.tests.test_pid_burst import complete_blocks
from openpilot.selfdrive.carrot.control_advisor.tests.test_policy_observations import tf_point


def typed_fixture():
  pack, profile = candidate_fixture()
  settings, point = tf_point()
  key = "TFollowGap1"
  pack["epoch"].update(settings_raw=settings, dependencies={})
  pack["objectives"] = {key: {"id": "fixed-tf-default", "kind": "policy_reference", "source": "user_specified",
    "unit": "s", "reference_signal": "fixed_tf", "method_id": policy.POLICY_METHOD_ID,
    "target_raw": policy.POLICY_DEFAULTS[key]}}
  for block in pack["blocks"]:
    for row in block["samples"]:
      row["signals"] = {"fixed_tf": {"value": policy.policy_reference(key, point), "unit": "s", "source": "user_specified", "source_mono_ns": row["mono_ns"]}}
      row["policy_observations"] = {"tf": {"consumer_event_sha256": None, "point": deepcopy(point)}}
  profile.update(per_key_change_limits={key: 50}, guard_tolerances={key: {"mode": "baseline_bounded"}}, minimum_improvement=0)
  return pack, profile


class NativeEvidenceIntegrationTests(unittest.TestCase):
  def test_typed_policy_reaches_numeric_candidate_and_preserves_scope(self):
    pack, profile = typed_fixture()
    report = candidates.evaluate_candidates(pack, profile)
    row = next(row for row in report["items"] if row["key"] == "TFollowGap1")
    self.assertEqual("search_candidate", row["status"], row)
    self.assertEqual(110, row["candidate_raw"])
    self.assertEqual(5, row["metrics"]["selection"]["rows"])
    self.assertLess(row["metrics"]["holdout"]["rmse"], row["metrics"]["baseline"]["holdout"]["rmse"])
    self.assertEqual("one_cycle_policy_effect", row["provenance"]["evaluation_scope"])
    self.assertFalse(row["provenance"]["trajectory_prediction"])
    self.assertFalse(row["provenance"]["plant_improvement_proven"])

  def test_recorded_typed_point_requires_original_event_and_exact_shape(self):
    pack, profile = typed_fixture()
    row = pack["blocks"][0]["samples"][0]
    pack["source_kind"] = "recorded"
    with self.assertRaises(ValueError):
      candidates._policy_observations(pack, row, profile)
    pack["source_kind"] = "synthetic"
    row["policy_observations"]["tf"]["point"]["extra"] = True
    with self.assertRaises(ValueError):
      candidates._policy_observations(pack, row, profile)

  def test_raw_kinematic_is_opt_in_original_clock_and_no_gravity_correction(self):
    args = dict(max_age_ns=500_000_000, max_alignment_ns=500_000_000, max_buffer_events=1000)
    old, native = LateralObservationBuilder(source_cp(), **args), LateralObservationBuilder(source_cp(), include_pid_inputs=True, **args)
    for event in fixture_events():
      old.feed(event)
      native.feed({**event, "source_id": "pose-source"})
    original, result = old.observe(ANCHOR), native.observe(ANCHOR)
    self.assertNotIn("lateral_accel_kinematic", original["signals"])
    for name, value in original["signals"].items():
      self.assertEqual(value, result["signals"][name])
    signal = result["signals"]["lateral_accel_kinematic"]
    self.assertAlmostEqual(15.05 * result["signals"]["yaw_rate_calibrated"]["value"], signal["value"])
    self.assertEqual(RESPONSE, signal["source_mono_ns"])
    self.assertFalse(result["provenance"]["pid_kinematic"]["gravity_corrected"])
    self.assertEqual("pose-source", result["provenance"]["pid_kinematic"]["source_id"])

  def test_snapshot_pins_referenced_pid_chunk_and_detects_tampering(self):
    with tempfile.TemporaryDirectory() as folder:
      source, target = Path(folder) / "session", Path(folder) / "snapshot"
      source.mkdir()
      target.mkdir()
      reference = PidBurstStore(source).append(complete_blocks(1)[0])
      history = {"records": [{"record": {"type": "pid_burst_chunk", "reference": reference}}]}
      inventory = _pin_pid_chunks(source, target, history, copy_files=True)
      self.assertEqual(1, inventory["file_count"])
      self.assertEqual(inventory, _pin_pid_chunks(source, target, history))
      path = target / reference["path"]
      path.write_bytes(path.read_bytes() + b" ")
      with self.assertRaisesRegex(ValueError, "hash_mismatch"):
        _pin_pid_chunks(source, target, history)

  def test_native_goal_configs_and_old_sampling_contract_are_strict(self):
    defaults = Path(__file__).resolve().parents[1] / "defaults"
    for name in ("native_pid_objectives.json", "native_policy_objectives.json"):
      config = json.loads((defaults / name).read_text(encoding="utf8"))
      _objectives(config, candidate_capabilities())
    config["sampling"] = {"version": "native_static_response_5hz_v1", "period_ns": 200_000_000}
    with self.assertRaisesRegex(ValueError, "unsupported_objectives"):
      _sampling_policy(config)
