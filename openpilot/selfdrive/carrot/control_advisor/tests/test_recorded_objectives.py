"""Recorded objective bindings and one-minute candidate input size boundaries."""
import copy
import json
from pathlib import Path
import unittest

from openpilot.selfdrive.carrot.control_advisor.candidates import candidate_capabilities
from openpilot.selfdrive.carrot.control_advisor.log_candidates import _objectives, _pack, _encoded
from openpilot.selfdrive.carrot.control_advisor.candidate_report import MAX_INPUT_BYTES
from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
from openpilot.selfdrive.carrot.control_advisor.tests.test_lateral_log_signals import cp_bytes
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import describe_car_params
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry


NS = 1_000_000_000


def pack_fixture(count=600, step=100_000_000, *, sampling=False):
  profile = json.loads(Path(BUILTIN_PROFILE_PATH).read_text())
  profile["dependency_margin_ns"] = 0
  config = json.loads(Path(BUILTIN_OBJECTIVES_PATH).read_text())
  if not sampling:
    # These legacy cases deliberately omit the new explicit sampling policy.
    config.pop("sampling", None)
  config["analysis_profile"]["dependency_ns"] = 0
  identity = describe_car_params(cp_bytes())
  begin, end = 2 * NS, 2 * NS + (count - 1) * step
  rows = []
  def signal(value, unit, stamp, source="derived_input"):
    return {"value": value, "unit": unit, "source": source, "source_mono_ns": stamp}
  for index in range(count):
    stamp = begin + index * step
    response = begin + (index * step // 50_000_000) * 50_000_000
    signals = {"v_ego": signal(15., "m/s", stamp, "independent_measurement"),
      "sent_torque": signal(.2, "1", response, "controller_output"),
      "lateral_accel_independent": signal(.6, "m/s^2", response, "independent_measurement"),
      "curvature_independent": signal(.002, "1/m", response, "independent_measurement"),
      "steering_angle_corrected": signal(.08, "rad", response), "vm_curvature_factor": signal(.4, "1/m", response),
      "roll_curvature": signal(0., "1/m", response), "live_steer_ratio": signal(16., "1", response, "learned_estimate")}
    rows.append({"mono_ns": stamp, "source_id": "native-source", "route_id": "native-route", "signals": signals,
      "excluded_reasons": {"lateral": [], "longitudinal": []}, "lateral_observation": {
        "epoch_kind": "recorded_cp_span", "recording_schema_verified": False,
        "availability": {key: {"available": True, "reason_codes": []} for key in ("pose", "torque_response", "geometry")},
        "provenance": {"config_sha256": identity["config_sha256"], "source_id": "native-source", "epoch_id": "cp-span-1",
          "response_mono_ns": response, "consumer_mapping_verified": False, "parameter_application_ack": False}}})
  consumers = [{"consumer_id": "controls.torque", "produced_mono_ns": stamp, "boot_id": "boot", "phase": "sampled_use",
     "context": {"active": True, "torque_ff_path": "linear_standard", "nnff": False, "nnff_lite": False, "nnff_model_selected": False},
     "identity_version": 1, "vehicle_identity": identity, "source_hashes": {"torque.py": "a" * 64}}
    for stamp in range(NS, end + 1, NS)]
  raw = {item["key"]: 0 for item in load_registry()["items"]}
  raw.update(LateralTorqueCustom=1, LateralTorqueAccelFactor=2800, SteerRatioRate=100)
  epoch = {"id": "settings-epoch", "start_ns": begin, "end_ns": end, "rows": rows,
    "settings_raw": raw, "dependencies": {}, "completeness": "observed", "consumer_evidence": consumers,
    "route": "native-route", "boot_id": "boot", "vehicle_identity": identity, "code_hashes": {"torque.py": "a" * 64}}
  extracted = {"source_receipts": [{"id": "native-source", "sha256": "b" * 64, "bytes": 100,
      "route": "native-route", "segment": 0, "mono_min_ns": begin, "mono_max_ns": end, "finalized_through_ns": end}],
    "initial_snapshots": [{"source_id": "native-source", "mono_ns": 0, "event_valid": True,
      "recorded_car_params": {"state": "observed", "config_sha256": identity["config_sha256"]}}],
    "source_fields": {}, "limitations": ["host_unqualified"], "mask_version": "fixture-mask", "join_max_age_ns": 250_000_000}
  return epoch, extracted, {"state": "linked", "reason_codes": []}, config, profile, candidate_capabilities()


class RecordedObjectiveTests(unittest.TestCase):
  def test_exact_recorded_refs_and_invalid_mappings(self):
    args = pack_fixture(60)
    _objectives(args[3], args[5])
    for change in ({"kind": "policy_reference"}, {"reference": {"type": "recorded_signal", "signal": "desired_lateral_accel", "unit": "m/s^2"}},
                   {"reference": {"type": "recorded_signal", "signal": "lateral_accel_independent", "unit": "1/m"}}):
      config = copy.deepcopy(args[3])
      config["objectives"]["LateralTorqueAccelFactor"].update(change)
      with self.subTest(change=change), self.assertRaises(ValueError):
        _objectives(config, args[5])

  def test_measured_reference_and_provenance_bound_offset(self):
    args = pack_fixture()
    before = copy.deepcopy(args[:2])
    evidence, receipt = _pack(*args)
    self.assertEqual(args[:2], before)
    self.assertEqual(evidence["objectives"]["LateralTorqueAccelFactor"]["source"], "independent_measurement")
    self.assertEqual(evidence["objectives"]["SteerRatioRate"]["reference_signal"], "curvature_independent")
    self.assertEqual(evidence["epoch"]["dependencies"]["torque_ff_path"], "linear_standard")
    usable = [sample for block in evidence["blocks"] for sample in block["samples"] if "lateral_accel_offset" in sample["signals"]]
    self.assertTrue(usable)
    self.assertTrue(all(sample["signals"]["lateral_accel_offset"]["value"] == 0 for sample in usable))
    self.assertTrue(all(sample["signals"]["lateral_accel_offset"]["source"] == "controller_derived" for sample in usable))
    self.assertNotIn("factor_mapping_unverified", receipt["excluded_counts"])

  def test_cp_missing_mismatch_and_unverified_callback_are_key_scoped(self):
    for mode in ("cp", "missing", "callback", "nnff", "stale", "future"):
      args = list(pack_fixture(60))
      if mode == "cp":
        args[0]["vehicle_identity"]["config_sha256"] = "c" * 64
      elif mode == "missing":
        args[0].pop("vehicle_identity")
      elif mode in ("callback", "nnff"):
        for event in args[0]["consumer_evidence"]:
          event["context"]["torque_ff_path" if mode == "callback" else "nnff"] = "unverified" if mode == "callback" else True
      elif mode == "stale":
        args[0]["consumer_evidence"] = args[0]["consumer_evidence"][:1]
        args[4]["max_lag_ns"] = 1
      else:
        for event in args[0]["consumer_evidence"]:
          event["produced_mono_ns"] += 100 * NS
      with self.subTest(mode=mode):
        evidence, _ = _pack(*args)
        samples = [sample for block in evidence["blocks"] for sample in block["samples"]]
        self.assertTrue(all("lateral_accel_offset" not in sample["signals"] for sample in samples))
        self.assertTrue(all("LateralTorqueAccelFactor" in sample.get("unavailable_for", {}) for sample in samples))
        if mode in ("cp", "missing"):
          self.assertTrue(all("curvature_independent" not in sample["signals"] for sample in samples))
        else:
          self.assertTrue(any("curvature_independent" in sample["signals"] and not sample["excluded_reasons"] for sample in samples))
          self.assertTrue(any("SteerRatioRate" not in sample.get("unavailable_for", {}) for sample in samples))

  def test_minute_100hz_inputs_deduplicate_to_20hz_below_pack_limit(self):
    args = pack_fixture(6000, 10_000_000)
    evidence, receipt = _pack(*args)
    samples = [sample for block in evidence["blocks"] for sample in block["samples"]]
    self.assertEqual(receipt["excluded_counts"]["recorded_response_duplicate"], 4800)
    self.assertLessEqual(len(samples), 1200)
    self.assertLess(len(_encoded(evidence)), MAX_INPUT_BYTES)
    self.assertTrue(any(not sample["excluded_reasons"] for sample in samples))
    self.assertTrue(all("recorded_response_duplicate" not in sample["excluded_reasons"] for sample in samples))

  def test_explicit_native_sampling_retains_full_rate_counts_and_limits_pack_to_5hz(self):
    args = pack_fixture(6000, 10_000_000, sampling=True)
    # A bad first sample cannot be replaced by a later healthy sample in its bin.
    args[0]["rows"][0]["excluded_reasons"]["lateral"] = ["driver_intervention"]
    evidence, receipt = _pack(*args)
    samples = [sample for block in evidence["blocks"] for sample in block["samples"]]
    audit = receipt["sampling_audit"]
    self.assertEqual(6000, audit["raw_anchor_count"])
    self.assertEqual(1200, audit["unique_response_count"])
    self.assertEqual(len(samples), audit["selected_count"])
    self.assertLessEqual(len(samples), 300)
    self.assertEqual(1, audit["full_rate_excluded_counts"]["driver_intervention"])
    self.assertEqual(args[0]["rows"][0]["mono_ns"], samples[0]["mono_ns"])
    self.assertIn("driver_intervention", samples[0]["excluded_reasons"])
    self.assertLess(len(_encoded(evidence)), MAX_INPUT_BYTES)

  def test_policy_curve_path_keeps_original_anchor_count(self):
    args = list(pack_fixture(60, 10_000_000))
    args[3]["objectives"] = {"CruiseMaxVals0": {"id": "original-policy", "kind": "policy_reference", "reference": {
      "type": "policy_curve", "input_signal": "v_ego", "input_unit": "m/s", "unit": "m/s^2", "knots": [[0., 1.], [50., 1.]]}}}
    evidence, receipt = _pack(*args)
    self.assertEqual(sum(len(block["samples"]) for block in evidence["blocks"]), 60)
    self.assertEqual(evidence["objectives"]["CruiseMaxVals0"]["source"], "user_specified")
    self.assertNotIn("recorded_response_duplicate", receipt["excluded_counts"])
    self.assertTrue(all("unavailable_for" not in sample for block in evidence["blocks"] for sample in block["samples"]))

  def test_dependency_missing_is_explicit_per_key_without_global_mask(self):
    args = list(pack_fixture(120))
    for row in args[0]["rows"]:
      row["signals"]["sent_torque"]["source_mono_ns"] = 0
    evidence, _ = _pack(*args)
    samples = [sample for block in evidence["blocks"] for sample in block["samples"]]
    self.assertTrue(any(not sample["excluded_reasons"] for sample in samples))
    eligible_ratios = [sample for sample in samples if not sample["excluded_reasons"] and "SteerRatioRate" not in sample.get("unavailable_for", {})]
    self.assertTrue(eligible_ratios)
    for sample in eligible_ratios:
      self.assertIn("LateralTorqueAccelFactor", sample["unavailable_for"])
      self.assertTrue(set(sample["unavailable_for"]["LateralTorqueAccelFactor"]) <= {
        "recorded_reference_unlinked", "recorded_dependency_unavailable", "factor_mapping_unverified"})


if __name__ == "__main__":
  unittest.main()
