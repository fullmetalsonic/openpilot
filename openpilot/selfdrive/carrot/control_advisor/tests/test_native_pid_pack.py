"""Serialized native PID capture to the real model, with independent generated plant truth."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import pid_burst
from openpilot.selfdrive.carrot.control_advisor.candidates import evaluate_candidates
from openpilot.selfdrive.carrot.control_advisor.evidence import hash_file
from openpilot.selfdrive.carrot.control_advisor.log_candidates import _pack_pid, _native_pid_transport
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.rlog import decode_recorded_car_params
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import describe_car_params
from .test_candidate_pid import fixture
from .test_lateral_log_signals import cp_bytes
from .test_pid_burst import wire, BOOT


class NativePidPackTests(unittest.TestCase):
  def test_serialized_dense_capture_reaches_numeric_gain_search_with_verified_output_quantization(self):
    package = Path(__file__).resolve().parents[1]
    repo = Path(__file__).resolve().parents[5]
    profile = json.loads((package / "defaults/native_pid_profile.json").read_text())
    config = json.loads((package / "defaults/native_pid_objectives.json").read_text())
    raw_cp = cp_bytes(steerControlType="torque")
    identity, decoded = describe_car_params(raw_cp), decode_recorded_car_params(raw_cp)
    hashes = {path: hash_file(repo / path, 4 * 1024 * 1024)[0] for path in pid_burst.SOURCE_PATHS}
    # Fit has broader observed actuation than independent validation. Asking
    # larger gains to extrapolate past a same-amplitude fit is correctly held.
    truth, initial_response = [], 0.
    for phase, amplitude in enumerate((2.7, .6, .7)):
      part = fixture(count=6000, start_ns=(1 + phase * 60) * 1_000_000_000,
        amplitude=amplitude, quantum=.001, transport_lag=2, initial_response=initial_response)
      last = part[-1]["signals"]
      initial_response = .94 * last["actual_lateral_accel"]["value"] + .18 * last["sent_torque_raw"]["value"]
      truth.extend(part)
    buffer, events, rows = pid_burst.PidBurstBuffer(), [], []
    def drain():
      while (body := buffer.drain()) is not None:
        event = wire(body)
        event.update(source_hashes=hashes, vehicle_identity=identity)
        event["dependency_digest"] = pid_burst.dependency_digest(event["configuration"], identity, hashes)
        events.append(pid_burst.validate_chunk(event))
    mode = ("linear_standard", 1, False, False, True, 1., -1., "stored", "stored")
    for index, source in enumerate(truth):
      signals = source["signals"]
      values = tuple(signals[name]["value"] for name in pid_burst.COLUMNS)
      if buffer.capture(index, source["mono_ns"], values, mode):
        drain()
      output, response = signals["sent_torque_raw"], signals["lateral_accel_kinematic"]
      rows.append({"mono_ns": source["mono_ns"], "source_id": "native-source", "route_id": "generated-route",
        "signals": {"sent_torque_raw": {**output, "source": "controller_output"},
          "sent_torque_can_raw": {**output, "value": round(output["value"] * 1000), "source": "controller_output"},
          "lateral_accel_kinematic": {**response, "source": "independent_measurement"}},
        "lateral_observation": {"provenance": {"config_sha256": identity["config_sha256"],
          "pid_output_source_id": "native-source", "pid_kinematic": {"source_id": "native-source",
            "calibration_valid": True, "gravity_corrected": False, "pose_event_mono_ns": response["source_mono_ns"]}}},
        "excluded_reasons": {"lateral": [], "longitudinal": []}})
    drain()
    with tempfile.TemporaryDirectory() as directory:
      store = pid_burst.PidBurstStore(Path(directory))
      # Durability is covered by store tests; this fixture exercises actual wire
      # bytes/hash/reassembly/model coupling without thousands of redundant syncs.
      with patch("os.fsync"):
        refs = [{"event": event, "reference": store.append(event)} for event in events]
      blocks = pid_burst.assemble_blocks([{"event": pid_burst.load_chunk(Path(directory), item["reference"]),
        "reference": item["reference"]} for item in refs])
    self.assertEqual([row["state"] for row in blocks], ["complete"] * 6)
    settings = {item["key"]: 0 for item in load_registry()["items"]}
    settings.update(LateralTorqueCustom=1, LateralTorqueKpV=0, LateralTorqueKiV=0, LateralTorqueKf=80,
                    LateralTorqueAccelFactor=3000, LateralTorqueFriction=0)
    epoch = {"id": "generated-native-pid", "start_ns": rows[0]["mono_ns"], "end_ns": rows[-1]["mono_ns"],
      "rows": rows, "route": "generated-route", "settings_raw": settings, "dependencies": {}, "completeness": "observed",
      "boot_id": BOOT, "vehicle_identity": identity, "code_hashes": hashes, "consumer_evidence": []}
    extracted = {"source_receipts": [{"id": "native-source", "sha256": "a" * 64, "bytes": 100,
      "finalized_through_ns": epoch["end_ns"]}], "initial_snapshots": [{"recorded_car_params": decoded}],
      "mask_version": config["analysis_profile"]["profile_id"], "source_fields": {}, "limitations": []}
    contract = _native_pid_transport(epoch, extracted)
    self.assertEqual(contract["pid_transport_quantum"], .001)
    pack, receipt = _pack_pid(epoch, extracted, {"state": "linked", "pid_blocks": blocks}, config, profile, [])
    self.assertEqual(len(pack["pid_trace_blocks"]), 3)
    self.assertEqual(sum(len(block["rows"]) for block in pack["pid_trace_blocks"]), 3000)
    report = evaluate_candidates(pack, profile)
    by_key = {item["key"]: item for item in report["items"]}
    self.assertEqual(by_key["LateralTorqueKf"]["status"], "search_candidate", by_key["LateralTorqueKf"])
    self.assertGreater(by_key["LateralTorqueKf"]["candidate_raw"], 80)
    self.assertEqual(by_key["LateralTorqueKf"]["provenance"]["candidate_semantics"], "manual_experiment")
    self.assertFalse(by_key["LateralTorqueKf"]["provenance"]["causal_improvement_proven"])
    # Native controller dependencies include exact opendbc files. They must
    # survive report loading and request evidence, without allowing arbitrary
    # filesystem paths or activating a vehicle writer.
    from openpilot.selfdrive.carrot.control_advisor.candidate_report import calculate_bundle
    from openpilot.selfdrive.carrot.control_advisor.request_evidence import _bundle
    from openpilot.selfdrive.carrot.control_advisor.candidates import validate_candidate_report
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      generated = root / "generated.json"
      generated.write_text(json.dumps(pack, sort_keys=True, separators=(",", ":")), encoding="utf8")
      receipt["objectives_sha256"] = hash_file(package / "defaults/native_pid_objectives.json", 64 * 1024)[0]
      calculate_bundle(generated, package / "defaults/native_pid_profile.json", root / "bundle", log_link=receipt)
      captured = _bundle(root / "bundle")
      self.assertEqual(captured["evidence"]["epoch"]["settings_raw"]["LateralTorqueKf"], 80)
      self.assertIn("opendbc_repo/opendbc/car/hyundai/carcontroller.py", captured["source_code_hashes"])
    forged = deepcopy(report)
    forged["provenance"]["code_hashes"]["opendbc_repo/unapproved.py"] = "a" * 64
    with self.assertRaises(ValueError):
      validate_candidate_report(forged)
    bad = deepcopy(epoch)
    bad["rows"][100]["signals"]["sent_torque_can_raw"]["value"] += 1
    self.assertIsNone(_native_pid_transport(bad, extracted))
