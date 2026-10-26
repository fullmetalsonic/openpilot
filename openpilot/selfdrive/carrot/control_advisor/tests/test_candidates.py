"""Host contracts and split gates, separate from engine formula parity tests."""
import copy
import json
import math
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import candidates


def candidate_fixture():
  """Independent synthetic geometry reference; no vehicle/source verification."""
  blocks = []
  for number, split in enumerate(("fit", "select", "holdout")):
    begin = 1000 + number * 2000
    rows = []
    for index in range(5):
      stamp, unshifted = begin + index * 10, index * .1
      rows.append({"mono_ns": stamp, "excluded_reasons": [], "signals": {
        "unshifted_path_y": {"value": unshifted, "unit": "m", "source": "synthetic_truth", "source_mono_ns": stamp},
        "reference_path_y": {"value": unshifted + .2, "unit": "m", "source": "synthetic_truth", "source_mono_ns": stamp}}})
    blocks.append({"id": split, "route_id": f"fixture-route-{number}", "start_ns": begin, "end_ns": begin + 40,
                   "finalized_through_ns": begin + 40, "mask_version": "explicit-synthetic-mask-v1", "samples": rows})
  evidence = {"schema_version": 1, "source_kind": "synthetic", "evidence_id": "geometry-fixture-v1",
              "artifacts": [{"id": "synthetic-geometry", "sha256": "a" * 64}],
              "vehicle_context": {"brand": "other", "controller_path": "synthetic-geometry", "mode": "acc",
                                  "code_hashes": {"openpilot/selfdrive/controls/lib/lateral_planner.py": "b" * 64}},
              "epoch": {"id": "fixture-epoch", "start_ns": 1000, "end_ns": 5040, "settings_raw": {"PathOffset": 0},
                        "dependencies": {"path_offset_selected": True}, "completeness": "fixture"},
              "splits": {split: [split] for split in ("fit", "select", "holdout")}, "blocks": blocks,
              "consumer_evidence": [], "model_artifact": None,
              "objectives": {"PathOffset": {"id": "independent-path-reference", "kind": "geometry_reference", "source": "synthetic_truth",
                                            "unit": "m", "reference_signal": "reference_path_y"}}}
  profile = {"profile_id": "explicit-host-fixture-v1", "version": 1, "status": "synthetic_only", "scope": "host_unqualified",
             "allowed_source_kinds": ["synthetic"], "max_rows": 1000, "max_blocks": 16, "max_candidates_per_key": 8,
             "max_evaluations": 64, "max_lag_ns": 100, "dependency_margin_ns": 0, "min_blocks_per_split": 1,
             "min_samples_per_split": 3, "minimum_improvement": .01,
             "guard_tolerances": {"PathOffset": {"max_absolute_error": 1., "max_absolute_prediction": 10.}},
             "raw_step_policy": "ui_step", "per_key_change_limits": {"PathOffset": 50}, "device_qualified": False}
  return evidence, profile


def factor_fixture(*, holdout_offset=.12):
  evidence, profile = candidate_fixture()
  key = "LateralTorqueAccelFactor"
  evidence["epoch"]["settings_raw"] = {key: 2800, "LateralTorqueCustom": 1}
  evidence["epoch"]["dependencies"] = {"torque_ff_path": "linear_standard", "torque_response_alignment": "declared_aligned"}
  evidence["objectives"] = {key: {"id": "independent-factor-reference", "kind": "tracking", "source": "synthetic_truth",
    "unit": "m/s^2", "reference_signal": "lateral_accel_independent"}}
  for block in evidence["blocks"]:
    rows = []
    offset = holdout_offset if block["id"] == "holdout" else .12
    for index, torque in enumerate((-.4, -.3, -.2, -.1, .1, .2, .3, .4)):
      stamp = block["start_ns"] + index * 10
      rows.append({"mono_ns": stamp, "excluded_reasons": [], "signals": {
        name: {"value": value, "unit": unit, "source": source, "source_mono_ns": stamp}
        for name, value, unit, source in (("sent_torque", torque, "1", "consumer_state"),
          ("lateral_accel_independent", 3 * torque + offset, "m/s^2", "synthetic_truth"),
          ("lateral_accel_offset", 0., "m/s^2", "declared_callback_contract"))}})
    block.update(samples=rows, end_ns=rows[-1]["mono_ns"], finalized_through_ns=rows[-1]["mono_ns"])
  evidence["epoch"]["end_ns"] = evidence["blocks"][-1]["end_ns"]
  profile.update(change_limit_policy="manual_experiment", per_key_change_limits=None,
    guard_tolerances={key: {"mode": "baseline_bounded"}}, method_options={key: {"offset_policy": "fit_intercept"}})
  return evidence, profile


class FakeEngine:
  def __init__(self, *, proposals=None, holdout_wrong=False):
    self.calls = []
    self.proposals = [20, 10] if proposals is None else proposals
    self.holdout_wrong = holdout_wrong

  def capabilities(self):
    return {"PathOffset": {"method_id": "geometry-offset-v1", "backend": "pure_geometry", "prediction_kind": "static_formula",
                           "required_signals": {"unshifted_path_y": "m", "reference_path_y": "m"}, "output_unit": "m", "reason_codes": []}}

  def propose_family(self, key, context, profile):
    self.calls.append(("propose", copy.deepcopy(context)))
    return {"raw_estimate": 20., "candidate_raw_values": list(self.proposals), "method_id": "geometry-offset-v1", "reason_codes": []}

  def evaluate_family(self, key, raw, context, split, budget):
    self.calls.append((split, copy.deepcopy(context)))
    offset = raw * .01
    if self.holdout_wrong and split == "holdout" and raw == 20:
      offset = 2.
    return {"status": "evaluated", "prediction_kind": "static_formula", "predictions": [r["signals"]["unshifted_path_y"]["value"] + offset for r in context["records"][split]], "reason_codes": []}


class CandidateContractsTest(unittest.TestCase):
  def calculate(self, evidence=None, profile=None, engine=None):
    fixture, host = candidate_fixture()
    engine = engine or FakeEngine()
    with patch.object(candidates, "_engines", return_value={"lateral": engine, "longitudinal": SimpleNamespace(capabilities=lambda: {})}):
      report = candidates.evaluate_candidates(evidence or fixture, profile or host)
    return report, engine

  def row(self, report, key="PathOffset"):
    return next(row for row in report["items"] if row["key"] == key)

  def test_synthetic_geometry_runs_all_47_rows_without_qualification(self):
    report, _ = self.calculate()
    row = self.row(report)
    self.assertEqual(47, len(report["items"]))
    self.assertEqual("search_candidate", row["status"])
    self.assertEqual((20, .2), (row["candidate_raw"], row["candidate_physical"]))
    self.assertAlmostEqual(0, row["metrics"]["holdout"]["rmse"])
    self.assertEqual("declared_input_only", report["provenance"]["source_verification"])
    self.assertFalse(report["recommendations_enabled"])
    self.assertFalse(report["apply_enabled"])
    self.assertFalse(row["validation"]["independent_route_reproduced"])
    self.assertEqual(report, candidates.validate_candidate_report(json.loads(json.dumps(report, allow_nan=False))))

  def test_actual_geometry_engine_reaches_typed_report(self):
    evidence, profile = candidate_fixture()
    report = candidates.evaluate_candidates(evidence, profile)
    row = self.row(report)
    self.assertEqual(("search_candidate", 20), (row["status"], row["candidate_raw"]))
    self.assertEqual("external_geometry_calibration", row["backend"])

  def test_actual_acceleration_policy_keeps_current_step_anchor(self):
    evidence, profile = candidate_fixture()
    key = "CruiseMaxVals0"
    evidence["epoch"]["settings_raw"] = {f"CruiseMaxVals{i}": 100 for i in range(7)}
    evidence["objectives"] = {key: {"id": "independent-policy", "kind": "policy_reference", "source": "user_specified",
                                         "unit": "m/s^2", "reference_signal": "accel_reference"}}
    for block in evidence["blocks"]:
      for row in block["samples"]:
        stamp = row["mono_ns"]
        row["signals"] = {name: {"value": value, "unit": unit, "source": source, "source_mono_ns": stamp}
                          for name, value, unit, source in (("v_ego", 0., "m/s", "synthetic_truth"),
                                                           ("accel_mode_factor", 1., "1", "synthetic_truth"),
                                                           ("accel_reference", 1.2, "m/s^2", "user_specified"))}
    profile["per_key_change_limits"] = {key: 25}
    profile["guard_tolerances"] = {key: {"max_absolute_error": 1., "max_absolute_prediction": 10.}}
    report = candidates.evaluate_candidates(evidence, profile)
    row = self.row(report, key)
    self.assertEqual(("search_candidate", 120), (row["status"], row["candidate_raw"]))
    self.assertAlmostEqual(1.2, row["candidate_physical"])

  def test_actual_total_lag_is_fit_only_diagnostic_proxy(self):
    evidence, profile = candidate_fixture()
    key, start, period = "SteerActuatorDelay", 1000000000, 100000000
    expected = [math.sin(index * 1.7) + .2 * math.cos(index * .37) for index in range(70)]
    rows = []
    for index, value in enumerate(expected):
      stamp = start + index * period
      actual = expected[index - 2] if index >= 2 else 0.
      rows.append({"mono_ns": stamp, "excluded_reasons": [], "signals": {
        "desired_lateral_accel": {"value": value, "unit": "m/s^2", "source": "consumer_state", "source_mono_ns": stamp},
        "lateral_accel_independent": {"value": actual, "unit": "m/s^2", "source": "synthetic_truth", "source_mono_ns": stamp}}})
    end = rows[-1]["mono_ns"]
    evidence["blocks"] = [{"id": "fit", "route_id": "explicit-synthetic-route", "start_ns": start, "end_ns": end,
                           "finalized_through_ns": end, "mask_version": "synthetic-mask-v1", "samples": rows}]
    evidence["splits"] = {"fit": ["fit"], "select": [], "holdout": []}
    evidence["epoch"].update(start_ns=start, end_ns=end, settings_raw={key: 10})
    evidence["objectives"] = {key: {"id": "independent-lateral-response", "kind": "tracking", "source": "synthetic_truth",
                                        "unit": "m/s^2", "reference_signal": "lateral_accel_independent"}}
    profile["method_options"] = {key: {"lag_grid_seconds": [0., .1, .2, .3], "sample_period_seconds": .1}}
    report = candidates.evaluate_candidates(evidence, profile)
    row = self.row(report, key)
    self.assertEqual("raw_estimate", row["status"])
    self.assertEqual((20., .2, "diagnostic_proxy"), (row["raw_estimate"], row["physical_estimate"], row["estimate_kind"]))
    self.assertIsNone(row["candidate_raw"])
    self.assertIsNone(row["metrics"]["holdout"])
    self.assertIn("effective_delay_mapping_unavailable", row["reason_codes"])

  def test_actual_manual_steer_ratio_calibrates_without_switching_mode(self):
    evidence, profile = candidate_fixture()
    key = "CustomSR"
    evidence["epoch"]["settings_raw"] = {key: 150}
    evidence["objectives"] = {key: {"id": "independent-curvature", "kind": "geometry_reference", "source": "synthetic_truth",
                                        "unit": "1/m", "reference_signal": "curvature_independent"}}
    for block in evidence["blocks"]:
      for index, row in enumerate(block["samples"]):
        angle, stamp = (index - 2) * .1, row["mono_ns"]
        values = {"steering_angle_corrected": (angle, "rad"), "vm_curvature_factor": (.5, "1/m"),
                  "live_steer_ratio": (15., "1"), "roll_curvature": (0., "1/m"), "curvature_independent": (.5 * angle / 16., "1/m")}
        row["signals"] = {name: {"value": value, "unit": unit, "source": "synthetic_truth", "source_mono_ns": stamp}
                          for name, (value, unit) in values.items()}
    profile.update(minimum_improvement=1e-6, per_key_change_limits={key: 50},
                   guard_tolerances={key: {"max_absolute_error": .1, "max_absolute_prediction": 1.}})
    report = candidates.evaluate_candidates(evidence, profile)
    row = self.row(report, key)
    self.assertEqual(("search_candidate", 160, 16.), (row["status"], row["candidate_raw"], row["candidate_physical"]))
    evidence["epoch"]["settings_raw"][key] = 0
    row = self.row(candidates.evaluate_candidates(evidence, profile), key)
    self.assertEqual("blocked", row["status"])
    self.assertIsNone(row["candidate_raw"])
    self.assertIn("mode_switch_required", row["reason_codes"])

  def test_proposal_and_evaluation_never_receive_other_split_rows(self):
    evidence, profile = candidate_fixture()
    before = copy.deepcopy((evidence, profile))
    report, engine = self.calculate(evidence, profile)
    for split, context in engine.calls:
      actual = "fit" if split == "propose" else split
      self.assertTrue(context["records"][actual])
      self.assertTrue(all(not rows for name, rows in context["records"].items() if name != actual))
      self.assertNotIn("blocks", context["evidence"])
      self.assertNotIn("model_artifact", context["evidence"])
    self.assertEqual(before, (evidence, profile))
    again, _ = self.calculate(evidence, profile)
    self.assertEqual(report, again)

  def test_failed_holdout_retains_current_without_reselection(self):
    report, engine = self.calculate(engine=FakeEngine(holdout_wrong=True))
    row = self.row(report)
    self.assertEqual("keep_current", row["status"])
    self.assertIsNone(row["candidate_raw"])
    self.assertFalse(row["validation"]["holdout_objective_pass"])
    self.assertEqual(2, sum(split == "holdout" for split, _ in engine.calls))

  def test_current_value_retained_is_a_normal_result(self):
    evidence, profile = candidate_fixture()
    for block in evidence["blocks"]:
      for sample in block["samples"]:
        sample["signals"]["reference_path_y"]["value"] = sample["signals"]["unshifted_path_y"]["value"]
    report, engine = self.calculate(evidence, profile)
    self.assertEqual("keep_current", self.row(report)["status"])
    self.assertFalse(any(split == "holdout" for split, _ in engine.calls))

  def test_input_budgets_future_nan_and_duplicate_chronology_rejected(self):
    for mutation in ("rows", "blocks", "future", "nan", "duplicate", "claimed_qualified"):
      with self.subTest(mutation=mutation):
        evidence, profile = candidate_fixture()
        sample = evidence["blocks"][0]["samples"][0]
        if mutation == "rows":
          profile["max_rows"] = 1
        elif mutation == "blocks":
          profile["max_blocks"] = 1
        elif mutation == "future":
          sample["signals"]["reference_path_y"]["source_mono_ns"] += 1
        elif mutation == "nan":
          sample["signals"]["reference_path_y"]["value"] = float("nan")
        elif mutation == "duplicate":
          evidence["blocks"][0]["samples"][1]["mono_ns"] = sample["mono_ns"]
        else:
          profile["device_qualified"] = True
        with self.assertRaises(ValueError):
          self.calculate(evidence, profile)

  def test_split_reuse_and_overlapping_windows_are_not_independent(self):
    evidence, profile = candidate_fixture()
    evidence["splits"]["holdout"] = ["select"]
    with self.assertRaises(ValueError):
      self.calculate(evidence, profile)
    evidence, profile = candidate_fixture()
    second = evidence["blocks"][1]
    second["start_ns"], second["end_ns"], second["finalized_through_ns"] = 1000, 1040, 1040
    for row in second["samples"]:
      row["mono_ns"] -= 2000
      for signal in row["signals"].values():
        signal["source_mono_ns"] -= 2000
    report, _ = self.calculate(evidence, profile)
    self.assertIn("split_dependency_overlap", self.row(report)["reason_codes"])
    self.assertFalse(self.row(report)["validation"]["split_independent"])

  def test_excluded_and_unfinalized_tail_do_not_inflate_exposure(self):
    evidence, profile = candidate_fixture()
    block = evidence["blocks"][0]
    block["samples"][1]["excluded_reasons"] = ["driver_intervention"]
    block["finalized_through_ns"] = block["start_ns"] + 20
    report, engine = self.calculate(evidence, profile)
    row = self.row(report)
    self.assertEqual(1, row["provenance"]["excluded_rows"])
    self.assertEqual(2, row["provenance"]["provisional_rows"])
    self.assertIn("insufficient_independent_split_exposure", row["reason_codes"])
    self.assertFalse(engine.calls)

  def test_held_source_cannot_cross_split_or_intervention_support(self):
    evidence, profile = candidate_fixture()
    profile["max_lag_ns"] = 10000
    evidence["blocks"][1]["samples"][0]["signals"]["unshifted_path_y"]["source_mono_ns"] = 1040
    report, engine = self.calculate(evidence, profile)
    self.assertIn("missing_stale_or_mismatched_signal_units", self.row(report)["reason_codes"])
    self.assertEqual(["propose"], [split for split, _ in engine.calls])
    self.assertIsNone(self.row(report)["candidate_raw"])
    evidence, profile = candidate_fixture()
    block = evidence["blocks"][0]
    block["samples"][1]["excluded_reasons"] = ["driver_intervention"]
    for index in (2, 3):
      block["samples"][index]["signals"]["unshifted_path_y"]["source_mono_ns"] = block["start_ns"]
    report, engine = self.calculate(evidence, profile)
    self.assertEqual(3, self.row(report)["provenance"]["excluded_rows"])
    self.assertIn("insufficient_independent_split_exposure", self.row(report)["reason_codes"])
    self.assertFalse(engine.calls)

  def test_missing_limits_guard_and_wrong_units_cannot_be_search_candidates(self):
    for mode in ("limits", "guards", "units", "objective"):
      evidence, profile = candidate_fixture()
      if mode == "limits":
        profile["per_key_change_limits"] = None
      elif mode == "guards":
        profile["guard_tolerances"] = {}
      elif mode == "units":
        evidence["blocks"][0]["samples"][0]["signals"]["unshifted_path_y"]["unit"] = "cm"
      else:
        evidence["objectives"] = {}
      report, _ = self.calculate(evidence, profile)
      row = self.row(report)
      self.assertNotEqual("search_candidate", row["status"])
      self.assertIsNone(row["candidate_raw"])
      if mode in {"limits", "guards"}:
        self.assertEqual("raw_estimate", row["status"])
        self.assertTrue(row["validation"]["numerical_checks_pass"])

  def test_engine_bad_quantization_and_evaluation_budget_are_explicit(self):
    evidence, profile = candidate_fixture()
    report, _ = self.calculate(evidence, profile, FakeEngine(proposals=[51]))
    self.assertIn("engine_contract_or_budget_failed", self.row(report)["reason_codes"])
    profile["max_evaluations"] = 1
    report, _ = self.calculate(evidence, profile)
    self.assertNotEqual("search_candidate", self.row(report)["status"])

  def test_context_only_and_no_effect_keys_stay_separate(self):
    report, _ = self.calculate()
    for key in ("LateralTorqueCustom", "NNFF", "NNFFLite", "LatMpcInputOffset", "LateralTorqueKd"):
      self.assertEqual("unsupported", self.row(report, key)["status"])
      self.assertIsNone(self.row(report, key)["candidate_raw"])

  def test_report_validation_rejects_promotions_and_bad_physical_conversion(self):
    original, _ = self.calculate()
    for mutation in ("apply", "qualification", "physical", "missing_key", "epoch", "source_verified", "nan"):
      report = copy.deepcopy(original)
      row = self.row(report)
      if mutation == "apply":
        report["apply_enabled"] = True
      elif mutation == "qualification":
        row["validation"]["device_qualified"] = True
      elif mutation == "physical":
        row["candidate_physical"] = 20.
      elif mutation == "missing_key":
        report["items"].pop()
      elif mutation == "epoch":
        row["provenance"]["epoch_id"] = "another-epoch"
      elif mutation == "source_verified":
        report["provenance"]["source_verification"] = "verified"
      else:
        row["metrics"]["holdout"]["rmse"] = float("nan")
      with self.subTest(mutation=mutation), self.assertRaises(ValueError):
        candidates.validate_candidate_report(report)

  def test_report_rejects_learned_delay_and_inheritance_transitions(self):
    original, _ = self.calculate()
    valid = copy.deepcopy(self.row(original))
    for key, current, proposed in (("SteerActuatorDelay", 0, 10), ("SteerActuatorDelay", 10, 0),
                                   ("LeadAccelResponseTF1", -1, 0), ("LeadAccelResponseTF1", 0, -1),
                                   ("CustomSR", 0, 150), ("CustomSR", 150, 10)):
      report = copy.deepcopy(original)
      target = self.row(report, key)
      metadata = {name: target[name] for name in ("key", "axis", "family")}
      target.update(copy.deepcopy(valid), **metadata, current_raw=current, candidate_raw=proposed)
      item = next(item for item in candidates.load_registry()["items"] if item["key"] == key)
      target["current_physical"] = candidates._physical(item, current)
      target["candidate_physical"] = candidates._physical(item, proposed)
      target["physical_estimate"] = candidates._physical(item, target["raw_estimate"])
      with self.subTest(key=key, current=current), self.assertRaises(ValueError):
        candidates.validate_candidate_report(report)
    item = next(item for item in candidates.load_registry()["items"] if item["key"] == "TFollowDecelBoost")
    self.assertEqual(0., candidates._physical(item, 0))

  def test_real_wire_consumer_identity_and_past_only_binding(self):
    from openpilot.selfdrive.carrot.control_advisor import consumer_observer as producer
    evidence, profile = candidate_fixture()
    evidence["source_kind"] = "recorded"
    evidence["epoch"].update(completeness="observed", boot_id="00000000-0000-0000-0000-000000000001")
    profile.update(status="not_validated", allowed_source_kinds=["recorded"], max_lag_ns=10000)
    evidence["objectives"]["PathOffset"]["source"] = "independent_measurement"
    for block in evidence["blocks"]:
      for row in block["samples"]:
        for signal in row["signals"].values():
          signal["source"] = "independent_measurement"
    evidence["vehicle_context"]["code_hashes"] = {producer.OBSERVER_PATH: "a" * 64, producer.CONSUMER_PATHS["planner.lateral"]: "b" * 64}
    captured, ready = [], threading.Event()
    class Sink:
      def send(self, packet):
        captured.append(packet)
        ready.set()
      def close(self):
        pass
    pump = producer._Pump(sender=Sink(), identity={"boot_id": evidence["epoch"]["boot_id"], "pid": 123,
                          "process_instance_id": "c" * 32}, hash_provider=lambda cid: copy.deepcopy(evidence["vehicle_context"]["code_hashes"]))
    try:
      observer = producer._Observer("planner.lateral", pump, clock_ns=lambda: 1000)
      producer.observe(observer, lambda: {"fields": {"PathOffset": producer.field(0., read_value=0., state="used", source="stored", read_mono_ns=1000)}, "context": {}})
      self.assertTrue(ready.wait(2), "real producer did not encode fixture")
    finally:
      pump.close()
    event = producer.validate_event(json.loads(captured[0]))
    evidence["consumer_evidence"] = [event]
    report, _ = self.calculate(evidence, profile)
    self.assertEqual("search_candidate", self.row(report)["status"])
    self.assertEqual(["planner.lateral"], self.row(report)["provenance"]["consumer_ids"])
    for defect in ("future", "boot", "restart", "hash"):
      bad = copy.deepcopy(evidence)
      if defect == "future":
        bad["consumer_evidence"][0]["produced_mono_ns"] = 1100
      elif defect == "boot":
        bad["consumer_evidence"][0]["boot_id"] = "00000000-0000-0000-0000-000000000002"
      elif defect == "hash":
        bad["consumer_evidence"][0]["source_hashes"][producer.OBSERVER_PATH] = None
      else:
        second = copy.deepcopy(event)
        second.update(consumer_instance_id="d" * 32, sequence=1, produced_mono_ns=1001)
        bad["consumer_evidence"].append(second)
      report, _ = self.calculate(bad, profile)
      with self.subTest(defect=defect):
        self.assertEqual("blocked", self.row(report)["status"])
        self.assertIsNone(self.row(report)["candidate_raw"])
    # A matching older packet must not hide a newer causal disagreement,
    # bypass or sampling loss. This was reproduced at every block start.
    for defect in ("latest_value", "latest_effective_only", "latest_unknown", "latest_loss", "none"):
      changed = copy.deepcopy(evidence)
      changed["consumer_evidence"] = []
      sequence = 0
      for block in changed["blocks"]:
        for offset in (-1, 0):
          sequence += 1
          latest = copy.deepcopy(event)
          stamp = block["start_ns"] + offset
          latest.update(sequence=sequence, produced_mono_ns=stamp)
          latest["fields"]["PathOffset"]["read_mono_ns"] = stamp
          if offset == 0 and defect == "latest_value":
            latest["fields"]["PathOffset"].update(read_value=1., effective_value=1.)
          elif offset == 0 and defect == "latest_effective_only":
            latest["fields"]["PathOffset"]["effective_value"] = 1.
          elif offset == 0 and defect == "latest_unknown":
            latest["fields"]["PathOffset"]["state"] = "unknown"
          elif defect == "latest_loss":
            latest["dropped_events"] = sequence // 2
          changed["consumer_evidence"].append(latest)
      report, _ = self.calculate(changed, profile)
      with self.subTest(defect=defect):
        self.assertEqual("search_candidate" if defect == "none" else "blocked", self.row(report)["status"])
        if defect != "none":
          self.assertIsNone(self.row(report)["candidate_raw"])
    evidence["consumer_evidence"].append(copy.deepcopy(event))
    with self.assertRaisesRegex(ValueError, "consumer_sequence"):
      self.calculate(evidence, profile)

  def test_all_relevant_consumers_need_reconciled_effective_values(self):
    from openpilot.selfdrive.carrot.control_advisor import consumer_observer as producer
    key = "CruiseMaxVals0"
    evidence, profile = candidate_fixture()
    evidence["source_kind"] = "recorded"
    evidence["epoch"].update(completeness="observed", boot_id="00000000-0000-0000-0000-000000000001", settings_raw={key: 100})
    settings = {f"CruiseMaxVals{i}": 100 for i in range(7)}
    evidence["epoch"]["settings_raw"] = settings
    profile.update(status="not_validated", allowed_source_kinds=["recorded"], max_lag_ns=10000,
                   per_key_change_limits={key: 25}, guard_tolerances={key: {"max_absolute_error": 1., "max_absolute_prediction": 10.}})
    evidence["objectives"] = {key: {"id": "independent-policy", "kind": "policy_reference", "source": "user_specified",
                                        "unit": "m/s^2", "reference_signal": "accel_reference"}}
    for block in evidence["blocks"]:
      for row in block["samples"]:
        stamp = row["mono_ns"]
        row["signals"] = {name: {"value": value, "unit": unit, "source": source, "source_mono_ns": stamp}
                          for name, value, unit, source in (("v_ego", 0., "m/s", "independent_measurement"),
                                                           ("accel_mode_factor", 1., "1", "consumer_state"),
                                                           ("accel_reference", 1.2, "m/s^2", "user_specified"))}
    hashes = {producer.OBSERVER_PATH: "a" * 64, **{producer.CONSUMER_PATHS[cid]: "b" * 64 for cid in ("planner.carrot", "planner.longitudinal")}}
    evidence["vehicle_context"]["code_hashes"] = hashes
    events = []
    for index, consumer in enumerate(("planner.carrot", "planner.longitudinal")):
      events.append(producer.validate_event({"schema_version": 1, "consumer_id": consumer, "boot_id": evidence["epoch"]["boot_id"],
        "pid": 123, "process_instance_id": "c" * 32, "consumer_instance_id": str(index + 1) * 32,
        "sequence": 1, "produced_mono_ns": 1000, "cycle_id": 1, "phase": "sampled_use", "dropped_events": 0,
        "fields": {key: producer.field(1., read_value=1., state="loaded" if consumer == "planner.carrot" else "used", source="stored", read_mono_ns=1000)},
        "context": {}, "source_hashes": {producer.OBSERVER_PATH: "a" * 64, producer.CONSUMER_PATHS[consumer]: "b" * 64}}))
    evidence["consumer_evidence"] = events
    row = self.row(candidates.evaluate_candidates(evidence, profile), key)
    self.assertEqual("search_candidate", row["status"])
    self.assertEqual({"planner.carrot", "planner.longitudinal"}, set(row["provenance"]["consumer_ids"]))
    for defect in ("missing_cache", "contradictory_cache", "contradictory_final"):
      bad = copy.deepcopy(evidence)
      if defect == "missing_cache":
        bad["consumer_evidence"].pop(0)
      else:
        bad["consumer_evidence"][0 if defect == "contradictory_cache" else 1]["fields"][key]["effective_value"] = .8
      row = self.row(candidates.evaluate_candidates(bad, profile), key)
      with self.subTest(defect=defect):
        self.assertEqual("blocked", row["status"])
        self.assertIsNone(row["candidate_raw"])


  def test_actual_factor_fit_context_is_finite_auditable_and_copied_per_evaluation(self):
    from openpilot.selfdrive.carrot.control_advisor import candidate_lateral as lateral
    evidence, profile = factor_fixture()
    before = copy.deepcopy((evidence, profile))
    original = lateral.evaluate_family
    offsets = []
    def evaluate(key, raw, context, split, budget):
      offsets.append((split, context["fit_context"]["lateral_accel_offset"]))
      value = original(key, raw, context, split, budget)
      context["fit_context"]["lateral_accel_offset"] = 999.
      return value
    with patch.object(lateral, "evaluate_family", side_effect=evaluate):
      report = candidates.evaluate_candidates(evidence, profile)
    row = self.row(report, "LateralTorqueAccelFactor")
    self.assertEqual(("search_candidate", 3000), (row["status"], row["candidate_raw"]))
    self.assertAlmostEqual(.12, row["provenance"]["fit_context"]["lateral_accel_offset"])
    self.assertTrue(all(math.isclose(offset, .12) for _, offset in offsets))
    self.assertEqual(before, (evidence, profile))
    for invalid in ({"lateral_accel_offset": float("nan")}, {"lateral_accel_offset": True}, {"lateral_accel_offset": .12, "unexpected": 1}):
      forged = copy.deepcopy(report)
      self.row(forged, "LateralTorqueAccelFactor")["provenance"]["fit_context"] = invalid
      with self.subTest(context=invalid), self.assertRaises(ValueError):
        candidates.validate_candidate_report(forged)
    forged = copy.deepcopy(report)
    self.row(forged, "PathOffset")["provenance"]["fit_context"] = {"lateral_accel_offset": .12}
    with self.assertRaises(ValueError):
      candidates.validate_candidate_report(forged)

  def test_factor_holdout_offset_cannot_refit_and_required_fit_context_cannot_be_omitted(self):
    from openpilot.selfdrive.carrot.control_advisor import candidate_lateral as lateral
    evidence, profile = factor_fixture(holdout_offset=1.12)
    before = copy.deepcopy((evidence, profile))
    with patch.object(lateral, "estimate_torque", wraps=lateral.estimate_torque) as fit:
      report = candidates.evaluate_candidates(evidence, profile)
    row = self.row(report, "LateralTorqueAccelFactor")
    self.assertEqual(1, fit.call_count)
    self.assertEqual("keep_current", row["status"])
    self.assertIsNone(row["candidate_raw"])
    self.assertAlmostEqual(.12, row["provenance"]["fit_context"]["lateral_accel_offset"])
    self.assertAlmostEqual(1., row["metrics"]["holdout"]["rmse"])
    self.assertFalse(row["validation"]["holdout_objective_pass"])
    self.assertEqual(before, (evidence, profile))
    propose = lateral.propose_family
    def missing_context(*args):
      result = propose(*args)
      result.pop("fit_context", None)
      return result
    with patch.object(lateral, "propose_family", side_effect=missing_context):
      missing = candidates.evaluate_candidates(*factor_fixture())
    self.assertIsNone(self.row(missing, "LateralTorqueAccelFactor")["candidate_raw"])
    self.assertIn("engine_contract_or_budget_failed", self.row(missing, "LateralTorqueAccelFactor")["reason_codes"])

  def manual_profile(self):
    evidence, profile = candidate_fixture()
    profile.update(change_limit_policy="manual_experiment", per_key_change_limits={},
                   guard_tolerances={"PathOffset": {"mode": "baseline_bounded"}})
    return evidence, profile

  def test_manual_baseline_candidate_is_numeric_and_failed_holdout_never_reselects(self):
    evidence, profile = self.manual_profile()
    before = copy.deepcopy((evidence, profile))
    report, engine = self.calculate(evidence, profile)
    row = self.row(report)
    self.assertEqual(("search_candidate", 20, .2), (row["status"], row["candidate_raw"], row["candidate_physical"]))
    self.assertIn("manual_experiment_change_limit_unset", row["reason_codes"])
    self.assertIn("host_objective_only_not_vehicle_validation", row["reason_codes"])
    self.assertTrue(row["validation"]["holdout_objective_pass"])
    self.assertEqual(before, (evidence, profile))
    for split, context in engine.calls:
      actual = "fit" if split == "propose" else split
      self.assertTrue(context["records"][actual])
      self.assertTrue(all(not records for name, records in context["records"].items() if name != actual))
    self.assertTrue(all(report[key] is False for key in ("apply_enabled", "recommendations_enabled", "device_qualified")))
    actual = self.row(candidates.evaluate_candidates(evidence, profile))
    self.assertEqual(("search_candidate", 20, "external_geometry_calibration"), (actual["status"], actual["candidate_raw"], actual["backend"]))
    class HoldoutEngine(FakeEngine):
      def __init__(self):
        super().__init__(holdout_wrong=True)
        self.raw_calls = []
      def evaluate_family(self, key, raw, context, split, budget):
        self.raw_calls.append((split, raw))
        return super().evaluate_family(key, raw, context, split, budget)
    failing = HoldoutEngine()
    failed, _ = self.calculate(evidence, profile, failing)
    self.assertEqual("keep_current", self.row(failed)["status"])
    self.assertIsNone(self.row(failed)["candidate_raw"])
    self.assertFalse(self.row(failed)["validation"]["holdout_objective_pass"])
    self.assertIn("manual_experiment_change_limit_unset", self.row(failed)["reason_codes"])
    self.assertEqual([0, 20], [raw for split, raw in failing.raw_calls if split == "holdout"])

  def test_baseline_bounded_filters_better_rmse_with_worse_peak_before_holdout(self):
    evidence, profile = self.manual_profile()
    class Engine(FakeEngine):
      def __init__(self):
        super().__init__()
        self.raw_calls = []
      def evaluate_family(self, key, raw, context, split, budget):
        self.raw_calls.append((split, raw))
        self.calls.append((split, copy.deepcopy(context)))
        errors = ([.2] * 5 if raw == 0 else [.21, 0, 0, 0, 0] if raw == 19 else [.1] * 5 if raw == 20 else [.3] * 5)
        return {"status": "evaluated", "prediction_kind": "static_formula",
          "predictions": [row["reference"] + error for row, error in zip(context["records"][split], errors)], "reason_codes": []}
    engine = Engine()
    report, _ = self.calculate(evidence, profile, engine)
    row = self.row(report)
    self.assertLess(math.sqrt(.21 ** 2 / 5), row["metrics"]["selection"]["rmse"])
    self.assertEqual(("search_candidate", 20), (row["status"], row["candidate_raw"]))
    self.assertEqual([0, 20], [raw for split, raw in engine.raw_calls if split == "holdout"])

  def test_manual_explicit_limits_keep_existing_generation_and_missing_estimates_cannot_pass(self):
    evidence, profile = self.manual_profile()
    profile["per_key_change_limits"] = {"PathOffset": 5}
    rejected, _ = self.calculate(evidence, profile, FakeEngine(proposals=[20]))
    self.assertIn("engine_contract_or_budget_failed", self.row(rejected)["reason_codes"])
    accepted, _ = self.calculate(evidence, profile, FakeEngine(proposals=[5]))
    self.assertEqual(("search_candidate", 5), (self.row(accepted)["status"], self.row(accepted)["candidate_raw"]))
    self.assertNotIn("manual_experiment_change_limit_unset", self.row(accepted)["reason_codes"])
    profile["per_key_change_limits"] = {}
    for estimate in (None, float("nan"), float("inf")):
      class MissingEstimate(FakeEngine):
        def propose_family(self, key, context, profile):
          result = super().propose_family(key, context, profile)
          return {**result, "raw_estimate": estimate}
      with self.subTest(estimate=estimate):
        report, _ = self.calculate(evidence, profile, MissingEstimate())
        self.assertNotEqual("search_candidate", self.row(report)["status"])
        self.assertIsNone(self.row(report)["candidate_raw"])
        if estimate is None:
          self.assertIn("manual_candidate_estimate_unavailable", self.row(report)["reason_codes"])
          self.assertIn("manual_experiment_change_limit_unset", self.row(report)["reason_codes"])

  def test_manual_neighborhood_is_fit_anchored_and_missing_signals_still_block(self):
    evidence, profile = self.manual_profile()
    evaluated = []
    class Engine(FakeEngine):
      def propose_family(self, key, context, profile):
        result = super().propose_family(key, context, profile)
        return {**result, "raw_estimate": 20.25, "candidate_raw_values": [100]}
      def evaluate_family(self, key, raw, context, split, budget):
        if split == "select":
          evaluated.append(raw)
        return super().evaluate_family(key, raw, context, split, budget)
    report, _ = self.calculate(evidence, profile, Engine())
    self.assertEqual("search_candidate", self.row(report)["status"])
    self.assertEqual({-1, 0, 1, 19, 20, 21, 22}, set(evaluated))
    broken = copy.deepcopy(evidence)
    broken["blocks"][0]["samples"][0]["signals"].pop("unshifted_path_y")
    failed, _ = self.calculate(broken, profile)
    self.assertIsNone(self.row(failed)["candidate_raw"])
    self.assertNotEqual("search_candidate", self.row(failed)["status"])

  def test_baseline_guard_keeps_prediction_magnitude_observational_but_finite(self):
    evidence, profile = self.manual_profile()
    for block in evidence["blocks"]:
      for row in block["samples"]:
        for signal in row["signals"].values():
          signal["value"] += 100
    report, _ = self.calculate(evidence, profile)
    self.assertEqual("search_candidate", self.row(report)["status"])
    self.assertGreater(self.row(report)["metrics"]["holdout"]["max_absolute_prediction"], 10)
    class Nonfinite(FakeEngine):
      def evaluate_family(self, key, raw, context, split, budget):
        result = super().evaluate_family(key, raw, context, split, budget)
        if raw == 20:
          result["predictions"][0] = float("nan")
        return result
    failed, _ = self.calculate(evidence, profile, Nonfinite())
    self.assertIsNone(self.row(failed)["candidate_raw"])
    self.assertIn("engine_contract_or_budget_failed", self.row(failed)["reason_codes"])

  def test_policy_and_guard_shapes_are_strict_without_mutating_legacy_profiles(self):
    evidence, profile = candidate_fixture()
    before = copy.deepcopy(profile)
    legacy, _ = self.calculate(evidence, profile)
    explicit, _ = self.calculate(evidence, {**profile, "change_limit_policy": "explicit_limits"})
    self.assertEqual(legacy["items"], explicit["items"])
    self.assertEqual(before, profile)
    for bad_policy in ("unknown", 123, None):
      with self.subTest(policy=bad_policy), self.assertRaises(ValueError):
        self.calculate(evidence, {**profile, "change_limit_policy": bad_policy})
    for guard in ({"mode": "unknown"}, {"mode": True}, {"mode": "baseline_bounded", "max_absolute_error": 1},
                  {"max_absolute_error": 1}, {}, "invalid"):
      changed = copy.deepcopy(profile)
      changed["guard_tolerances"]["PathOffset"] = guard
      with self.subTest(guard=guard), self.assertRaises(ValueError):
        self.calculate(evidence, changed)


if __name__ == "__main__":
  unittest.main()
