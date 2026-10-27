"""Actual bounded PID wire/store/pin/pack supply, with independently generated truth."""
from copy import deepcopy
import json
import math
import random
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import pid_burst, candidates
from openpilot.selfdrive.carrot.control_advisor.candidate_report import calculate_bundle, load_candidate_bundle
from openpilot.selfdrive.carrot.control_advisor.evidence import hash_file
from openpilot.selfdrive.carrot.control_advisor.journal_snapshot import _pin_pid_chunks, select_pid_references
from openpilot.selfdrive.carrot.control_advisor.log_candidates import _pack_pid, _encoded
from openpilot.selfdrive.carrot.control_advisor.rlog import decode_recorded_car_params
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import describe_car_params
from .test_identified_numeric import pid_event_fixture
from .test_lateral_log_signals import cp_bytes
from .test_pid_burst import wire, BOOT


def native_multi_fixture(directory):
  root = Path(directory)
  generated, _ = pid_event_fixture(events=24, quantum=.001)
  raw_cp = cp_bytes(steerControlType="torque")
  identity, decoded = describe_car_params(raw_cp), decode_recorded_car_params(raw_cp)
  repo, package = Path(__file__).resolve().parents[5], Path(__file__).resolve().parents[1]
  profile = json.loads((package / "defaults/lateral_torque_kf_v1_profile.json").read_text())
  config = json.loads((package / "defaults/lateral_torque_kf_v1_objectives.json").read_text())
  hashes = {path: hash_file(repo / path, 4 * 1024 * 1024)[0] for path in pid_burst.SOURCE_PATHS}
  buffer, captured, rows = pid_burst.PidBurstBuffer(), [], []
  def drain():
    while (body := buffer.drain()) is not None:
      event = wire(body)
      event.update(source_hashes=hashes, vehicle_identity=identity)
      event["dependency_digest"] = pid_burst.dependency_digest(event["configuration"], identity, hashes)
      captured.append(pid_burst.validate_chunk(event))
  mode = ("linear_standard", 1, False, False, True, 1., -1., "stored", "stored")
  for phase, block in enumerate(generated["blocks"]):
    offset = (100 + phase * 60) * 1_000_000_000 - block["start_ns"]
    for frame, source in enumerate(block["samples"][:6000]):
      signals = deepcopy(source["signals"])
      stamp = source["mono_ns"] + offset
      for signal in signals.values():
        signal["source_mono_ns"] += offset
      values = tuple(signals[name]["value"] for name in pid_burst.COLUMNS)
      if buffer.capture(phase * 6000 + frame, stamp, values, mode):
        drain()
      output, response = signals["sent_torque_raw"], signals["lateral_accel_kinematic"]
      rows.append({"mono_ns": stamp, "source_id": "native-source", "route_id": "generated-route",
        "signals": {"sent_torque_raw": {**output, "source": "controller_output"},
          "sent_torque_can_raw": {**output, "value": round(output["value"] * 1000), "source": "controller_output"},
          "lateral_accel_kinematic": {**response, "source": "independent_measurement"}},
        "lateral_observation": {"provenance": {"config_sha256": identity["config_sha256"],
          "pid_output_source_id": "native-source", "pid_kinematic": {"source_id": "native-source", "calibration_valid": True,
            "gravity_corrected": False, "pose_event_mono_ns": response["source_mono_ns"]}}},
        "excluded_reasons": {"lateral": [], "longitudinal": []}})
  drain()
  prelude = []
  for tick in range(1000):
    row = deepcopy(rows[0])
    row["mono_ns"] = 90_000_000_000 + tick * 10_000_000
    for signal in row["signals"].values():
      signal.update(value=0., source_mono_ns=row["mono_ns"])
    row["lateral_observation"]["provenance"]["pid_kinematic"]["pose_event_mono_ns"] = row["mono_ns"]
    prelude.append(row)
  rows = prelude + rows
  source, target = root / "source", root / "snapshot"
  source.mkdir()
  target.mkdir()
  store = pid_burst.PidBurstStore(source)
  with patch("os.fsync"):
    references = [store.append(event) for event in captured]
  history = {"records": [{"record": {"type": "pid_burst_chunk", "reference": reference}} for reference in references]}
  analysis_range = {"start_ns": 90_000_000_000, "end_ns": 280_000_000_000}
  inventory = _pin_pid_chunks(source, target, history, copy_files=True, analysis_range=analysis_range)
  chosen = select_pid_references(references, analysis_range)
  blocks = pid_burst.assemble_blocks([{"event": pid_burst.load_chunk(target, reference), "reference": reference} for reference in chosen])
  epoch = {"id": "multi-native-pid", **{"start_ns": analysis_range["start_ns"], "end_ns": analysis_range["end_ns"]},
    "rows": rows, "route": "generated-route", "settings_raw": generated["epoch"]["settings_raw"], "dependencies": {},
    "completeness": "observed", "boot_id": BOOT, "vehicle_identity": identity, "code_hashes": hashes, "consumer_evidence": []}
  extracted = {"source_receipts": [{"id": "native-source", "sha256": "a" * 64, "bytes": 100, "finalized_through_ns": rows[-1]["mono_ns"]}],
    "initial_snapshots": [{"recorded_car_params": decoded}], "mask_version": config["analysis_profile"]["profile_id"], "source_fields": {}, "limitations": []}
  return epoch, extracted, {"state": "linked", "pid_blocks": blocks}, profile, config, inventory, source, target, history, analysis_range


def native_twelve_fixture(directory):
  started = time.perf_counter()
  root = Path(directory)
  generated, _ = pid_event_fixture()
  raw_cp = cp_bytes(steerControlType="torque")
  identity, decoded = describe_car_params(raw_cp), decode_recorded_car_params(raw_cp)
  repo = Path.cwd()
  package = repo / "openpilot/selfdrive/carrot/control_advisor"
  profile = json.loads((package / "defaults/lateral_torque_kf_v1_profile.json").read_text())
  config = json.loads((package / "defaults/lateral_torque_kf_v1_objectives.json").read_text())
  hashes = {path: hash_file(repo / path, 4 * 1024 * 1024)[0] for path in pid_burst.SOURCE_PATHS}
  buffer, captured, rows = pid_burst.PidBurstBuffer(), [], []
  def drain():
    while (body := buffer.drain()) is not None:
      event = wire(body)
      event.update(source_hashes=hashes, vehicle_identity=identity)
      event["dependency_digest"] = pid_burst.dependency_digest(event["configuration"], identity, hashes)
      captured.append(pid_burst.validate_chunk(event))
  mode = ("linear_standard", 1, False, False, True, 1., -1., "stored", "stored")
  template = generated["blocks"][0]["samples"][0]["signals"]
  pole = math.exp(-.01 / .16)
  for burst in range(12):
    rng, y, pose, pose_stamp = random.Random(800 + burst), 0., 0., 0
    for frame in range(1000):
      stamp = (100 + burst * 30) * 1_000_000_000 + frame * 10_000_000
      position = frame - 50
      amplitude = 2.7 if burst < 4 else .6
      target = (-1 if burst % 2 else 1) * amplitude * math.sin(math.pi * position / 150) ** 2 if 0 < position < 150 else 0.
      measured = y + rng.gauss(0., .0003)
      if frame % 5 == 0:
        pose, pose_stamp = measured, stamp
      command = target / 3. * .8
      sent = round(command * 1000) / 1000
      values = {name: item["value"] for name, item in template.items()}
      values.update(desired_lateral_accel=target, actual_lateral_accel=measured, setpoint=target,
        measurement=measured, pid_error=(target-measured)/3., feedforward=target/3.,
        f_after=command, output_torque=command, pid_frame=burst*1000+frame,
        sent_torque_raw=sent, lateral_accel_kinematic=pose)
      signals = {name: {**item, "value": values[name], "source_mono_ns": pose_stamp if name == "lateral_accel_kinematic" else stamp}
        for name, item in template.items()}
      if buffer.capture(burst * 1000 + frame, stamp, tuple(values[name] for name in pid_burst.COLUMNS), mode):
        drain()
      output, response = signals["sent_torque_raw"], signals["lateral_accel_kinematic"]
      rows.append({"mono_ns": stamp, "source_id": "native-source", "route_id": "generated-route",
        "signals": {"sent_torque_raw": {**output, "source": "controller_output"},
          "sent_torque_can_raw": {**output, "value": round(sent*1000), "source": "controller_output"},
          "lateral_accel_kinematic": {**response, "source": "independent_measurement"}},
        "lateral_observation": {"provenance": {"config_sha256": identity["config_sha256"],
          "pid_output_source_id": "native-source", "pid_kinematic": {"source_id": "native-source", "calibration_valid": True,
            "gravity_corrected": False, "pose_event_mono_ns": pose_stamp}}},
        "excluded_reasons": {"lateral": [], "longitudinal": []}})
      y = pole*y + 3.*(1-pole)*sent
  drain()
  for tick in range(3600):
    stamp = 90_000_000_000 + tick * 100_000_000
    if 100_000_000_000 <= stamp < 440_000_000_000 and (stamp-100_000_000_000) % 30_000_000_000 < 10_000_000_000:
      continue
    row = deepcopy(rows[0])
    row["mono_ns"] = stamp
    for signal in row["signals"].values():
      signal.update(value=0., source_mono_ns=stamp)
    row["lateral_observation"]["provenance"]["pid_kinematic"]["pose_event_mono_ns"] = stamp
    rows.append(row)
  rows.sort(key=lambda row: row["mono_ns"])
  source, target = root / "source", root / "snapshot"
  source.mkdir()
  target.mkdir()
  store = pid_burst.PidBurstStore(source)
  captured_at = time.perf_counter()
  with patch("os.fsync"):
    references = [store.append(event) for event in captured]
  history = {"records": [{"record": {"type": "pid_burst_chunk", "reference": ref}} for ref in references]}
  stored_at = time.perf_counter()
  span = {"start_ns": 90_000_000_000, "end_ns": 450_000_000_000}
  inventory = _pin_pid_chunks(source, target, history, copy_files=True, analysis_range=span)
  chosen = select_pid_references(references, span)
  blocks = pid_burst.assemble_blocks([{"event": pid_burst.load_chunk(target, ref), "reference": ref} for ref in chosen])
  epoch = {"id": "twelve-native-pid", **span, "rows": rows, "route": "generated-route",
    "settings_raw": generated["epoch"]["settings_raw"], "dependencies": {}, "completeness": "observed",
    "boot_id": BOOT, "vehicle_identity": identity, "code_hashes": hashes, "consumer_evidence": []}
  extracted = {"source_receipts": [{"id": "native-source", "sha256": "a"*64, "bytes": 100,
    "finalized_through_ns": rows[-1]["mono_ns"]}], "initial_snapshots": [{"recorded_car_params": decoded}],
    "mask_version": config["analysis_profile"]["profile_id"], "source_fields": {}, "limitations": []}
  stages = {"fixture_capture_seconds": captured_at-started, "fixture_store_seconds": stored_at-captured_at,
    "snapshot_load_seconds": time.perf_counter()-stored_at}
  return epoch, extracted, {"state": "linked", "pid_blocks": blocks}, profile, config, inventory, len(references), stages, source, target, history, span


class NativeMultiBurstTests(unittest.TestCase):
  def test_actual_producer_pin_pack_supplies_complete_events_without_raising_budgets(self):
    with tempfile.TemporaryDirectory() as directory:
      epoch, extracted, history, profile, config, inventory, source, target, journal, span = native_multi_fixture(directory)
      before = json.dumps(history, sort_keys=True)
      self.assertEqual(6, len(inventory["burst_ids"]))
      self.assertEqual([2, 2, 2], [len(item["selected_burst_ids"]) for item in inventory["split_selection"]])
      (target / "snapshot.json").write_text(json.dumps({"pid_burst_inventory": inventory}), encoding="utf8")
      self.assertEqual(inventory, _pin_pid_chunks(source, target, journal, analysis_range=span))
      pack, receipt = _pack_pid(epoch, extracted, history, config, profile, [])
      self.assertEqual(6, len(pack["pid_trace_blocks"]), receipt["excluded_counts"])
      self.assertEqual(6000, sum(len(trace["rows"]) for trace in pack["pid_trace_blocks"]))
      self.assertLessEqual(len(_encoded(pack)), 4 * 1024 * 1024)
      report = candidates.evaluate_candidates(pack, profile)
      row = next(row for row in report["items"] if row["key"] == "LateralTorqueKf")
      counts = row["provenance"]["inference"]["event_counts"]
      self.assertTrue(all(count >= 4 for count in counts.values()), row["reason_codes"])
      self.assertNotIn("independent_events_insufficient", row["reason_codes"])
      self.assertEqual(before, json.dumps(history, sort_keys=True))
      root = Path(directory)
      (root / "input.json").write_bytes(_encoded(pack))
      (root / "profile.json").write_bytes(_encoded(profile))
      calculate_bundle(root / "input.json", root / "profile.json", root / "bundle")
      self.assertEqual(report, load_candidate_bundle(root / "bundle")["report"])
      damaged = deepcopy(history)
      damaged["pid_blocks"] = damaged["pid_blocks"][:1]
      partial, receipt = _pack_pid(epoch, extracted, damaged, config, profile, [])
      self.assertIn("pid_split_burst_insufficient", receipt["excluded_counts"])
      limited = {**profile, "max_rows": 3000}
      bounded, receipt = _pack_pid(epoch, extracted, history, config, limited, [])
      self.assertLessEqual(sum(len(trace["rows"]) for trace in bounded["pid_trace_blocks"]), 3000)
      self.assertIn("pid_split_row_budget", receipt["excluded_counts"])


if __name__ == "__main__":
  unittest.main()
