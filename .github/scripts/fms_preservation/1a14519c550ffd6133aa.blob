"""Whole-event identification fixtures; none claim vehicle or causal validation."""
from copy import deepcopy
import json
import math
import random
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import candidates, candidate_lateral, candidate_pid
from openpilot.selfdrive.carrot.control_advisor.candidate_report import calculate_bundle, load_candidate_bundle
from .test_candidates import candidate_fixture
from .test_candidate_pid import context as pid_context, profile as pid_profile


def sr_fixture(*, holdout_ratio=14., direction_conflict=False, outliers=False):
  evidence, profile = candidate_fixture()
  blocks = []
  for phase, split in enumerate(("fit", "select", "holdout")):
    rows = []
    for event in range(4):
      direction = 1 if event % 2 else -1
      for frame in range(40):
        seconds = frame * .2
        angle = direction * (.06 * math.sin(math.pi * seconds / 4) ** 2 if seconds < 4 else 0.)
        stamp = 1_000_000_000 + phase * 60_000_000_000 + event * 8_000_000_000 + frame * 200_000_000
        ratio = holdout_ratio if split == "holdout" else 14.
        if direction_conflict and direction < 0:
          ratio = 16.
        curvature = .35 * angle / ratio + .0002
        if outliers and frame in (10, 17):
          curvature += .003 * direction
        values = {"steering_angle_corrected": (angle, "rad"), "vm_curvature_factor": (.35, "1/m"),
          "roll_curvature": (.0002, "1/m"), "curvature_independent": (curvature, "1/m"), "v_ego": (20., "m/s")}
        rows.append({"mono_ns": stamp, "excluded_reasons": [], "signals": {
          name: {"value": value, "unit": unit, "source": "synthetic_truth", "source_mono_ns": stamp}
          for name, (value, unit) in values.items()}})
    blocks.append({"id": split, "route_id": "independent-geometry", "start_ns": rows[0]["mono_ns"], "end_ns": rows[-1]["mono_ns"],
      "finalized_through_ns": rows[-1]["mono_ns"], "mask_version": "fixture", "samples": rows})
  evidence.update(blocks=blocks, splits={split: [split] for split in ("fit", "select", "holdout")},
    objectives={"CustomSR": {"id": "independent-yaw-geometry", "kind": "geometry_reference", "source": "synthetic_truth", "unit": "1/m", "reference_signal": "curvature_independent"}})
  evidence["epoch"].update(start_ns=blocks[0]["start_ns"], end_ns=blocks[-1]["end_ns"], settings_raw={"CustomSR": 150, "LateralTorqueKiV": 0})
  profile.update(profile_id="custom_sr_v1", max_rows=10000, max_lag_ns=300_000_000, min_samples_per_split=10,
    dependency_margin_ns=0, minimum_improvement=1e-10, per_key_change_limits=None, change_limit_policy="manual_experiment",
    guard_tolerances={"CustomSR": {"mode": "baseline_bounded"}})
  return evidence, profile


def pid_event_fixture(key="LateralTorqueKf", *, colored_noise=False, events=4, quantum=0.):
  """An independently integrated plant with known tau/DC gain and stored PID trace."""
  evidence, profile = candidate_fixture()
  gains = (0, 0, 80)
  base = pid_context([], gains=gains)
  base["settings_raw"]["CustomSR"] = 140
  blocks = []
  for phase, split in enumerate(("fit", "select", "holdout")):
    y, noise, rows, pose, pose_stamp = 0., 0., [], 0., 0
    random_source = random.Random(400 + phase)
    amplitude = 2.7 if split == "fit" else .6
    for frame in range(events * 300 + 60):
      stamp = 1_000_000_000 + phase * 30_000_000_000 + frame * 10_000_000
      logical = frame - 60
      within = logical % 300
      sign = 1 if logical // 300 % 2 else -1
      target = sign * amplitude * math.sin(math.pi * within / 40) ** 2 if logical >= 0 and within < 40 else 0.
      if colored_noise:
        noise = .9 * noise + .004 * math.sin(frame * 1.37)
      measured = y + noise + random_source.gauss(0., .0003)
      if frame % 5 == 0:
        pose, pose_stamp = measured, stamp
      error, ff = (target - measured) / 3., target / 3.
      command = error * gains[0] * .01 + ff * gains[2] * .01
      sent = round(command / quantum) * quantum if quantum else command
      values = {"desired_lateral_accel": target, "actual_lateral_accel": measured, "setpoint": target, "measurement": measured,
        "low_speed_factor": 0., "v_ego": 20., "a_ego": 0., "roll_compensation": 0., "lateral_accel_deadzone": 0.,
        "pid_error": error, "feedforward": ff, "integral_before": 0., "p_after": 0., "i_after": 0., "f_after": command,
        "output_torque": command, "effective_kp": 0., "effective_ki": 0., "effective_kf": .8, "factor": 3., "friction": 0.,
        "freeze_integrator": 0, "steer_limited_by_controls": 0, "curvature_limited": 0, "steering_pressed": 0,
        "pid_frame": frame, "sent_torque_raw": sent, "lateral_accel_kinematic": pose}
      rows.append({"mono_ns": stamp, "excluded_reasons": [], "signals": {name: {"value": value,
        "unit": candidate_pid.REQUIRED_SIGNALS[name], "source": "synthetic_truth" if name in ("desired_lateral_accel", "lateral_accel_kinematic") else
        "controller_output" if name == "sent_torque_raw" else "controller_trace", "source_mono_ns": pose_stamp if name == "lateral_accel_kinematic" else stamp}
        for name, value in values.items()}})
      pole = math.exp(-.01 / .16)
      y = pole * y + 3. * (1 - pole) * sent
    blocks.append({"id": split, "route_id": "independently-integrated-plant", "start_ns": rows[0]["mono_ns"], "end_ns": rows[-1]["mono_ns"],
      "finalized_through_ns": rows[-1]["mono_ns"], "mask_version": "fixture", "samples": rows})
  evidence.update(blocks=blocks, splits={split: [split] for split in ("fit", "select", "holdout")},
    objectives={key: {"id": "recorded-controller-target", "kind": "tracking", "source": "synthetic_truth", "unit": "m/s^2", "reference_signal": "desired_lateral_accel"}})
  evidence["epoch"].update(start_ns=blocks[0]["start_ns"], end_ns=blocks[-1]["end_ns"], settings_raw=base["settings_raw"], dependencies=base["dependencies"])
  profile.update(profile_id="lateral_torque_kf_v1" if key.endswith("Kf") else "lateral_torque_kp_v1", max_rows=10000,
    max_lag_ns=100_000_000, dependency_margin_ns=0, min_samples_per_split=20, minimum_improvement=.0001,
    guard_tolerances={key: {"mode": "baseline_bounded"}}, per_key_change_limits=None, change_limit_policy="manual_experiment",
    method_options={key: deepcopy(pid_profile()["method_options"][key])})
  return evidence, profile


class IdentifiedNumericTests(unittest.TestCase):
  def row(self, report, key):
    return next(row for row in report["items"] if row["key"] == key)

  def test_actual_sr_bundle_is_deterministic_and_has_event_interval(self):
    evidence, profile = sr_fixture()
    original = deepcopy(evidence)
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      for name, value in (("input", evidence), ("profile", profile)):
        (root / (name + ".json")).write_text(json.dumps(value), encoding="utf8")
      for index in range(2):
        calculate_bundle(root / "input.json", root / "profile.json", root / str(index))
      first, second = (load_candidate_bundle(root / str(index))["report"] for index in range(2))
    self.assertEqual(first, second)
    row = self.row(first, "CustomSR")
    self.assertEqual(("search_candidate", 140), (row["status"], row["candidate_raw"]), row["reason_codes"])
    self.assertEqual({"fit": 4, "select": 4, "holdout": 4}, row["provenance"]["inference"]["event_counts"])
    self.assertEqual(1, row["provenance"]["inference"]["holdout_evaluations"])
    self.assertEqual(original, evidence)

  def test_holdout_failure_never_selects_a_second_candidate(self):
    evidence, profile = sr_fixture(holdout_ratio=15.)
    calls, real = [], candidate_lateral.evaluate_family
    def observed(key, raw, context, split, budget):
      calls.append((raw, split))
      return real(key, raw, context, split, budget)
    with patch.object(candidate_lateral, "evaluate_family", side_effect=observed):
      row = self.row(candidates.evaluate_candidates(evidence, profile), "CustomSR")
    self.assertEqual("keep_current", row["status"], row["reason_codes"])
    self.assertEqual([(150, "holdout"), (140, "holdout")], [call for call in calls if call[1] == "holdout"])
    self.assertIn("holdout_event_inference_failed", row["reason_codes"])

  def test_sr_robust_fit_resists_sparse_outliers_and_uncertainty_is_bound(self):
    evidence, profile = sr_fixture(outliers=True)
    objective = evidence["objectives"]["CustomSR"]
    records, _ = candidates._records(evidence, profile, objective, candidate_lateral.capabilities()["CustomSR"]["required_signals"], "CustomSR")
    records, _ = candidates._event_records(records, "CustomSR")
    context = candidates._context(evidence, records, objective, "fit", inference_config=candidate_pid.EVENT_INFERENCE_CONFIG)
    engine_profile = {**profile, "parameter": {"current_raw": 150, "min": 11, "max": 300, "step": 1}}
    proposal = candidate_lateral.propose_family("CustomSR", context, engine_profile)
    self.assertAlmostEqual(140., proposal["raw_estimate"], places=3, msg=proposal["reason_codes"])
    clean, profile = sr_fixture()
    report = candidates.evaluate_candidates(clean, profile)
    changed = deepcopy(report)
    self.row(changed, "CustomSR")["provenance"]["inference"]["fit_uncertainty"]["slope"] *= 2
    with self.assertRaises(ValueError):
      candidates.validate_candidate_report(changed)

  def test_direction_conflict_and_cross_partition_event_are_not_identified(self):
    evidence, profile = sr_fixture(direction_conflict=True)
    row = self.row(candidates.evaluate_candidates(evidence, profile), "CustomSR")
    self.assertIn("ratio_direction_or_speed_disagreement", row["reason_codes"])
    records = {split: [{"route_id": "same", "block_id": split, "mono_ns": phase * 100_000_000 + frame * 10_000_000,
      "reference": .01, "signals": {"steering_angle_corrected": {"value": .1}, "v_ego": {"value": 20.}}} for frame in range(10)]
      for phase, split in enumerate(("fit", "select", "holdout"))}
    partitioned, summary = candidates._event_records(records, "CustomSR")
    self.assertFalse(any(partitioned.values()))
    self.assertEqual(30, summary["split_excluded_rows"])

  def test_kf_and_kp_reach_real_normalized_candidate_path(self):
    for key in ("LateralTorqueKf", "LateralTorqueKpV"):
      evidence, profile = pid_event_fixture(key)
      row = self.row(candidates.evaluate_candidates(evidence, profile), key)
      self.assertEqual("search_candidate", row["status"], (key, row["reason_codes"]))
      self.assertEqual(0, evidence["epoch"]["settings_raw"]["LateralTorqueKiV"])
      self.assertGreater(row["candidate_raw"], row["current_raw"])
      self.assertTrue(row["provenance"]["inference"]["holdout"]["passed"])

  def test_nonwhite_pid_noise_and_insufficient_events_are_explicitly_held(self):
    evidence, profile = pid_event_fixture(colored_noise=True)
    row = self.row(candidates.evaluate_candidates(evidence, profile), "LateralTorqueKf")
    self.assertNotEqual("search_candidate", row["status"])
    self.assertTrue(set(row["reason_codes"]) & {"pid_prediction_error_not_white", "pid_residual_input_dependence", "pid_identification_uncertain"}, row["reason_codes"])
    evidence, profile = pid_event_fixture(events=1)
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      # Missing events use the same bundle path without manufacturing a model.
      for block in evidence["blocks"]:
        block["samples"] = block["samples"][:25]
      for name, value in (("input", evidence), ("profile", profile)):
        (root / (name + ".json")).write_text(json.dumps(value), encoding="utf8")
      calculate_bundle(root / "input.json", root / "profile.json", root / "result")
      row = self.row(load_candidate_bundle(root / "result")["report"], "LateralTorqueKf")
    self.assertIn("independent_events_insufficient", row["reason_codes"])


if __name__ == "__main__":
  unittest.main()
