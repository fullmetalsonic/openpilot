"""Native-generated closed logs through sidecars, automatic splits and bundles."""
import bz2
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.archive import SegmentArchive
from openpilot.selfdrive.carrot.control_advisor.candidate_report import load_candidate_bundle
from openpilot.selfdrive.carrot.control_advisor.consumer_observer import CONSUMER_PATHS, OBSERVER_PATH
from openpilot.selfdrive.carrot.control_advisor.log_candidates import calculate_log_candidates
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder
from openpilot.selfdrive.carrot.control_advisor.tests.test_candidates import candidate_fixture


ROUTE = "00000000--0123456789"
BOOT = "00000000-0000-0000-0000-000000000001"


def log_candidate_configs(root):
  _, profile = candidate_fixture()
  profile.update(status="not_validated", allowed_source_kinds=["recorded"], max_lag_ns=2_000_000_000,
                 dependency_margin_ns=100_000_000, per_key_change_limits={"CruiseMaxVals0": 50},
                 guard_tolerances={"CruiseMaxVals0": {"max_absolute_error": 1., "max_absolute_prediction": 3.}})
  objectives = {"schema_version": 1,
    "analysis_profile": {"profile_id": "explicit-native-fixture-mask", "pre_margin_ns": 100_000_000,
                         "recovery_ns": 200_000_000, "max_gap_ns": 250_000_000, "dependency_ns": 100_000_000, "max_samples": 2000},
    "objectives": {"CruiseMaxVals0": {"id": "explicit-user-acceleration-policy", "kind": "policy_reference",
      "reference": {"type": "policy_curve", "input_signal": "v_ego", "input_unit": "m/s", "unit": "m/s^2", "knots": [[0., 1.3], [50., 1.3]]}}}}
  profile_path, objectives_path = root / "host-profile.json", root / "objectives.json"
  profile_path.write_text(json.dumps(profile))
  objectives_path.write_text(json.dumps(objectives))
  return profile_path, objectives_path


class NativeLogCandidateFixture:
  def __init__(self, root, *, intervention=False, missing_consumer=False, changed=False, gap=False, mode=3, held_plan=False):
    self.root, self.now = root, 2_000_000_000
    self.settings, self.logs, self.session = (root / name for name in ("settings", "logs", "session"))
    for path in (self.settings, self.logs, self.session):
      path.mkdir()
    for item in load_registry()["items"]:
      (self.settings / item["key"]).write_text("100" if item["key"].startswith("CruiseMaxVals") else "0")
    (self.settings / "CurrentRoute").write_text(ROUTE)
    recorder = SettingsRecorder(self.settings, self.session, "native-log-candidates", clock_ns=self.clock)
    recorder.sample()
    repo = Path(__file__).resolve().parents[5]
    for index in range(10):
      stamp = 2_000_000_050 + index * 1_000_000_000
      for consumer in ("planner.carrot", "planner.longitudinal"):
        if missing_consumer and consumer == "planner.carrot":
          continue
        fields = {f"CruiseMaxVals{i}": {"read_value": 1., "effective_value": 1., "state": "loaded" if consumer == "planner.carrot" else "used",
                                        "source": "stored", "read_mono_ns": 2_000_000_000} for i in range(7)}
        paths = (OBSERVER_PATH, CONSUMER_PATHS[consumer])
        event = {"schema_version": 1, "consumer_id": consumer, "boot_id": BOOT, "pid": 42,
                 "process_instance_id": "a" * 32, "consumer_instance_id": ("b" if consumer == "planner.carrot" else "c") * 32,
                 "sequence": index + 1, "produced_mono_ns": stamp, "cycle_id": index, "phase": "sampled_use", "fields": fields,
                 "context": {"mode": "acc"}, "dropped_events": 0,
                 "source_hashes": {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in paths}}
        recorder.append_consumer_observation(event)
      self.now = (index + 3) * 1_000_000_000
      if changed and index == 4:
        (self.settings / "CruiseMaxVals0").write_text("140")
      recorder.sample()
    (self.session / "recording.json").write_text(json.dumps({"session_id": "native-log-candidates", "source_mode": "live",
      "clock": {"domain": "native_monotonic", "scale": 1}}))
    _, log = _schema()
    def event(kind, stamp, data):
      message = log.Event.new_message(logMonoTime=stamp, valid=True)
      message.init(kind).from_dict(data)
      return message.to_bytes()
    chunks = [event("initData", 10, {"gitCommit": "b" * 40, "params": {"entries": [{"key": "CruiseMaxVals0", "value": b"250"}]}}),
              event("sentinel", 2_000_000_000, {"type": "startOfRoute"})]
    stamps = []
    for index in range(197):
      stamp = 2_100_000_000 + index * 50_000_000
      if gap and 8_000_000_000 <= stamp <= 8_750_000_000:
        continue
      stamps.append(stamp)
      pressed = intervention and 5_000_000_000 <= stamp <= 6_000_000_000
      chunks.extend([event("carControl", stamp, {"enabled": True, "latActive": True, "longActive": True}),
        event("controlsState", stamp, {"lateralControlState": {"torqueState": {"active": True, "actualLateralAccel": .3, "desiredLateralAccel": .4, "saturated": False}}})])
      if not held_plan or not 3_000_000_000 <= stamp <= 3_050_000_000:
        chunks.append(event("longitudinalPlan", stamp, {"aTarget": .7, "myDrivingMode": mode}))
      chunks.append(event("carState", stamp, {"canValid": True, "gearShifter": "drive", "vEgo": 0., "aEgo": .6,
                                             "gasPressed": pressed, "steeringPressed": False, "brakePressed": False}))
    chunks.append(event("sentinel", 12_000_000_000, {"type": "endOfRoute"}))
    directory = self.logs / f"{ROUTE}--0"
    directory.mkdir()
    self.source = directory / "rlog.bz2"
    self.source.write_bytes(bz2.compress(b"".join(chunks)))
    start, end = stamps[0], stamps[-1]
    diagnostic = {"revision": 1, "window_start_ns": start, "window_end_ns": end, "profile_id": "native-fixture",
                  "invalidated": False, "partial_window": False, "observed_settings_revision": 1,
                  "route_observation": recorder.revision_metadata(1)["route_observation"]}
    data = json.dumps(diagnostic, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(data).hexdigest()
    (self.session / "diagnostics").mkdir()
    (self.session / "diagnostics" / f"1-{digest[:16]}.json").write_bytes(data)
    record = {key: value for key, value in diagnostic.items() if key not in {"observed_settings_revision", "route_observation"}}
    reference = recorder.append_diagnostic({**record, "sha256": digest}, settings_revision=1)["journal"]
    archive = SegmentArchive(self.logs, self.session / "archive", session_id="native-log-candidates", source_mode="live",
                             byte_limit=4 * 1024 * 1024, max_files=16, free_reserve_bytes=0)
    try:
      saved = archive.preserve(self.source, window_ns=(start, end), diagnostic_revision=1, settings_revision=1, journal_ref=reference)
    finally:
      archive.close()
    if saved["state"] != "complete":
      raise AssertionError(saved)
    self.archive_copy = self.session / "archive" / saved["archive_id"] / self.source.name
    recorder.append_archive({key: value for key, value in saved.items() if key not in {"source_relative", "decompressed_bytes"}})
    recorder.append_job_status({"diagnostic_revision": 1, "settings_revision": 1, "state": "complete", "window_start_ns": start,
      "window_end_ns": end, "route_observation": diagnostic["route_observation"], "settings_history_reference": reference,
      "actual_boundary_gaps": [], "gap_tolerance_ns": 250_000_000, "missing_segments": [], "missing_segments_count": 0,
      "endpoint_start_covered": True, "endpoint_end_covered": True, "completion_scope": "archive_work_only_host_profile",
      "coverage_complete": False, "interpolation_applied": False})
    recorder.close()

  def clock(self):
    self.now += 1
    return self.now


class LogCandidateTests(unittest.TestCase):
  def fixture(self, **options):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    fixture = NativeLogCandidateFixture(root, **options)
    fixture.profile, fixture.objectives = log_candidate_configs(root)
    return fixture

  def calculate(self, fixture, name="result", **options):
    destination = fixture.root / name
    summary = calculate_log_candidates([options.pop("source", fixture.source)], fixture.profile, fixture.objectives, destination,
                                       session_directory=options.pop("session", fixture.session), **options)
    return summary, load_candidate_bundle(destination), destination

  def row(self, loaded):
    return next(row for row in loaded["report"]["items"] if row["key"] == "CruiseMaxVals0")

  def test_native_closed_compressed_log_to_real_candidate_bundle_without_source_writes(self):
    fixture = self.fixture()
    before = {path: path.read_bytes() for path in fixture.root.rglob("*") if path.is_file()}
    summary, loaded, output = self.calculate(fixture)
    row = self.row(loaded)
    self.assertEqual("complete", summary["state"])
    self.assertEqual(1, summary["epoch_count"])
    self.assertEqual(("search_candidate", 130), (row["status"], row["candidate_raw"]), row)
    self.assertTrue(row["validation"]["split_independent"])
    self.assertEqual(before, {path: path.read_bytes() for path in before})
    self.assertFalse(loaded["apply_enabled"])
    self.assertFalse(loaded["raw_sources_verified"])
    index = json.loads((output / "index.json").read_text())
    self.assertEqual("epoch-0000", index["latest"])

  def test_preserved_neutral_filename_uses_verified_receipt_not_filename_guess(self):
    fixture = self.fixture()
    _, loaded, _ = self.calculate(fixture, source=fixture.archive_copy)
    self.assertEqual(("search_candidate", 130), (self.row(loaded)["status"], self.row(loaded)["candidate_raw"]))

  def test_identical_policy_references_share_storage_without_merging_objectives(self):
    fixture = self.fixture()
    config = json.loads(fixture.objectives.read_text())
    original = config["objectives"]["CruiseMaxVals0"]
    for key in ("CruiseMaxVals1", "CruiseMaxVals2", "TFollowGap1"):
      spec = copy.deepcopy(original)
      spec["id"] = f"independent-policy-{key}"
      if key == "CruiseMaxVals2":
        spec["reference"]["knots"] = [[0., 1.4], [50., 1.4]]
      elif key == "TFollowGap1":
        spec["reference"]["unit"] = "s"
      config["objectives"][key] = spec
    fixture.objectives.write_text(json.dumps(config))
    _, loaded, output = self.calculate(fixture)
    pack = json.loads(next(output.glob("generated-*.json")).read_text())
    names = {key: value["reference_signal"] for key, value in pack["objectives"].items()}
    self.assertEqual(names["CruiseMaxVals0"], names["CruiseMaxVals1"])
    self.assertNotEqual(pack["objectives"]["CruiseMaxVals0"]["id"], pack["objectives"]["CruiseMaxVals1"]["id"])
    self.assertEqual(3, len(set(names.values())))
    for block in pack["blocks"]:
      for sample in block["samples"]:
        self.assertEqual(3, len(set(sample["signals"]) & set(names.values())))
        self.assertEqual("m/s^2", sample["signals"][names["CruiseMaxVals2"]]["unit"])
        self.assertEqual("s", sample["signals"][names["TFollowGap1"]]["unit"])
    self.assertEqual(130, self.row(loaded)["candidate_raw"])

  def test_driver_intervention_recovery_and_split_gaps_are_excluded(self):
    fixture = self.fixture(intervention=True, gap=True)
    _, loaded, output = self.calculate(fixture)
    packs = [json.loads(path.read_text()) for path in output.glob("generated-*.json")]
    excluded = [reason for pack in packs for block in pack["blocks"] for sample in block["samples"] for reason in sample["excluded_reasons"]]
    self.assertTrue(any("driver" in reason or "intervention" in reason for reason in excluded), excluded)
    self.assertTrue(any("recovery" in reason or "gap" in reason for reason in excluded), excluded)
    for pack in packs:
      ranges = sorted((block["start_ns"], block["end_ns"]) for block in pack["blocks"])
      self.assertTrue(all(a[1] + 100_000_000 < b[0] for a, b in zip(ranges, ranges[1:])))
    self.assertFalse(loaded["apply_enabled"])

  def test_missing_consumer_and_old_log_never_use_init_settings_as_current(self):
    fixture = self.fixture(missing_consumer=True)
    _, loaded, _ = self.calculate(fixture)
    self.assertIsNone(self.row(loaded)["candidate_raw"])
    self.assertIn("required_consumer_observation_missing", self.row(loaded)["reason_codes"])
    _, old, _ = self.calculate(fixture, name="old", session=None)
    self.assertIsNone(self.row(old)["current_raw"])
    self.assertTrue(all(row["candidate_raw"] is None for row in old["report"]["items"]))

  def test_held_input_cannot_cross_a_poll_hole_without_an_anchor_in_hole(self):
    fixture = self.fixture(held_plan=True)
    profile = json.loads(fixture.profile.read_text())
    profile["dependency_margin_ns"] = 0
    fixture.profile.write_text(json.dumps(profile))
    config = json.loads(fixture.objectives.read_text())
    config["analysis_profile"]["dependency_ns"] = 0
    fixture.objectives.write_text(json.dumps(config))
    _, _, output = self.calculate(fixture)
    pack = json.loads(next(output.glob("generated-*.json")).read_text())
    sample = next(sample for block in pack["blocks"] for sample in block["samples"] if sample["mono_ns"] == 3_050_000_000)
    self.assertEqual(2_950_000_000, sample["signals"]["accel_mode_factor"]["source_mono_ns"])
    self.assertIn("settings_dependency_uncertain", sample["excluded_reasons"])
    self.assertFalse(any(3_000_000_001 <= sample["mono_ns"] <= 3_000_000_098 for block in pack["blocks"] for sample in block["samples"]))

  def test_real_cli_and_latest_unknown_epoch_does_not_select_best_older_score(self):
    fixture = self.fixture(changed=True)
    destination = fixture.root / "cli"
    command = [sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor", "evaluate-logs", str(fixture.source),
               "--profile", str(fixture.profile), "--objectives", str(fixture.objectives), "--session", str(fixture.session), "--output", str(destination)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    self.assertEqual(0, completed.returncode, completed.stderr)
    summary = json.loads(completed.stdout)
    self.assertEqual("complete", summary["state"])
    index = json.loads((destination / "index.json").read_text())
    chosen = max(index["epochs"], key=lambda entry: (entry["end_ns"], entry["start_ns"], entry["bundle"]))
    self.assertEqual(chosen["bundle"], index["latest"])
    loaded = load_candidate_bundle(destination)
    self.assertTrue(all(row["candidate_raw"] is None for row in loaded["report"]["items"]))
    earlier = [load_candidate_bundle(destination / entry["bundle"]) for entry in index["epochs"] if entry["bundle"] != index["latest"]]
    self.assertTrue(any(self.row(item)["candidate_raw"] == 130 for item in earlier))

  def test_real_cli_native_positive_from_preserved_copy(self):
    fixture = self.fixture()
    destination = fixture.root / "cli-positive"
    completed = subprocess.run([sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor", "evaluate-logs", str(fixture.archive_copy),
      "--profile", str(fixture.profile), "--objectives", str(fixture.objectives), "--session", str(fixture.session), "--output", str(destination)],
      capture_output=True, text=True, timeout=30)
    self.assertEqual(0, completed.returncode, completed.stderr)
    self.assertEqual("complete", json.loads(completed.stdout)["state"])
    loaded = load_candidate_bundle(destination)
    self.assertEqual(("search_candidate", 130), (self.row(loaded)["status"], self.row(loaded)["candidate_raw"]))
    self.assertFalse(loaded["device_qualified"])

  def test_budgets_no_overwrite_and_invalid_objective_fail_explicitly(self):
    fixture = self.fixture()
    for limits in ({"max_samples": 2}, {"max_events": 2}, {"max_decompressed_bytes": 64}, {"max_output_bytes": 1024}):
      with self.subTest(limits=limits), self.assertRaises(ValueError):
        self.calculate(fixture, name=f"limit-{next(iter(limits))}", **limits)
    output = fixture.root / "exists"
    output.mkdir()
    (output / "keep").write_text("preserve")
    with self.assertRaisesRegex(ValueError, "destination_exists"):
      self.calculate(fixture, name="exists")
    self.assertEqual("preserve", (output / "keep").read_text())
    config = json.loads(fixture.objectives.read_text())
    config["objectives"]["CruiseMaxVals0"]["reference"]["knots"] = [[0., 1.], [0., 2.]]
    fixture.objectives.write_text(json.dumps(config))
    with self.assertRaisesRegex(ValueError, "nonincreasing"):
      self.calculate(fixture, name="bad-objective")

  def test_different_or_unknown_routes_are_not_ranked_by_uptime(self):
    fixture = self.fixture()
    _, log = _schema()
    for number, stamp in enumerate((100_000_000_000, 3_000_000_000)):
      chunks = []
      for service, time, values in (("sentinel", stamp - 10, {"type": "startOfRoute"}),
                                    ("carState", stamp, {"canValid": True, "vEgo": 2.}),
                                    ("sentinel", stamp + 10, {"type": "endOfRoute"})):
        message = log.Event.new_message(logMonoTime=time, valid=True)
        message.init(service).from_dict(values)
        chunks.append(message.to_bytes())
      directory = fixture.logs / f"00000001--abcdef0123--{number}"
      directory.mkdir()
      other = directory / "rlog.bz2"
      other.write_bytes(bz2.compress(b"".join(chunks)))
      output = fixture.root / f"multi-{number}"
      with self.assertRaisesRegex(ValueError, "multi_route_clock_unlinked"):
        calculate_log_candidates([fixture.source, other], fixture.profile, fixture.objectives, output)
      self.assertFalse((output / "index.json").exists())
      opaque1, opaque2 = fixture.root / f"opaque-a-{number}.bz2", fixture.root / f"opaque-b-{number}.bz2"
      opaque1.write_bytes(fixture.source.read_bytes())
      opaque2.write_bytes(other.read_bytes())
      with self.assertRaisesRegex(ValueError, "multi_route_clock_unlinked"):
        calculate_log_candidates([opaque1, opaque2], fixture.profile, fixture.objectives, fixture.root / f"opaque-{number}")


if __name__ == "__main__":
  unittest.main()
