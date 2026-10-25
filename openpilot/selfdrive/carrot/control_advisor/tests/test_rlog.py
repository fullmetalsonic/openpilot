"""Synthetic native Cap'n Proto input; no device or vehicle claims."""
import bz2
import hashlib
import importlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor.rlog import _schema, iter_observations, validate_closed_log


class RlogTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.parser, cls.log = _schema()
    cls.zstandard = importlib.import_module("zstandard")

  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.directory_path = Path(self.directory.name)

  def event(self, kind, time, values=None, valid=True):
    message = self.log.Event.new_message(logMonoTime=time, valid=valid)
    value = message.init(kind)
    for key, entry in (values or {}).items():
      setattr(value, key, entry)
    return message

  def car(self, time, **values):
    return self.event("carState", time, {"canValid": True, "gearShifter": "drive", "aEgo": .6, "vEgo": 12., **values})

  def controls(self, time, *, target=.4, actual=.3, valid=True):
    message = self.event("controlsState", time, valid=valid)
    torque = message.controlsState.lateralControlState.init("torqueState")
    torque.active = True
    torque.desiredLateralAccel, torque.actualLateralAccel = target, actual
    return message

  def records(self, *, stop=False):
    return [
      self.event("carControl", 2_000_000_000, {"enabled": True, "latActive": True, "longActive": True}),
      self.controls(2_000_000_000),
      self.event("longitudinalPlan", 2_000_000_000, {"aTarget": .7, "accels": [-9.], "shouldStop": stop}),
      self.car(2_010_000_000),
    ]

  def write(self, records, suffix="", *, payload=None):
    if payload is None:
      blobs = []
      for record in records:
        blobs.append(record.to_bytes())
        record.clear_write_flag()
      payload = b"".join(blobs)
    if suffix == ".zst":
      payload = self.zstandard.ZstdCompressor(write_checksum=True).compress(payload)
    elif suffix == ".bz2":
      payload = bz2.compress(payload)
    path = self.directory_path / ("rlog" + suffix)
    path.write_bytes(payload)
    return path

  def read(self, path, **options):
    return list(iter_observations(path, epoch="synthetic-config-0", max_age_ns=100_000_000, **options))

  def test_native_three_formats_equal_and_original_unchanged(self):
    expected = None
    for suffix in ("", ".zst", ".bz2"):
      with self.subTest(suffix=suffix):
        path = self.write(self.records(), suffix)
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        output = self.read(path)
        self.assertEqual(original, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(2, len(output))
        lateral, longitudinal = output
        self.assertAlmostEqual(.7, longitudinal["target"], places=6)
        self.assertAlmostEqual(.6, longitudinal["actual"], places=6)
        self.assertAlmostEqual(.4, lateral["target"], places=6)
        self.assertEqual("steering_model_derived", lateral["context"]["response_kind"])
        self.assertEqual(2_000_000_000, lateral["context"]["actual_mono_ns"])
        self.assertEqual(2_010_000_000, longitudinal["context"]["actual_mono_ns"])
        self.assertEqual(10_000_000, lateral["context"]["target_age_ns"])
        self.assertTrue(all(entry["valid"] and entry["active"] for entry in output))
        if expected is None:
          expected = output
        self.assertEqual(expected, output)

  def test_brief_pedal_and_steering_override_between_planner_frames_are_kept(self):
    records = self.records() + [self.car(2_020_000_000, gasPressed=True, steeringPressed=True), self.car(2_030_000_000)]
    output = self.read(self.write(records))
    self.assertEqual([False, True, False], [row["driver_override"] for row in output if row["axis"] == "longitudinal"])
    self.assertEqual([False, True, False], [row["driver_override"] for row in output if row["axis"] == "lateral"])
    self.assertFalse(output[2]["recovery_ready"])
    self.assertFalse(output[3]["recovery_ready"])

  def test_stale_missing_future_and_invalid_state_never_become_zero(self):
    rows = self.read(self.write([self.car(2_010_000_000)]))
    self.assertIsNone(rows[0]["target"])
    self.assertIsNone(rows[0]["actual"])
    self.assertFalse(rows[0]["valid"])
    records = self.records()[:-1] + [self.car(2_200_000_000)]
    self.assertTrue(all(not row["valid"] for row in self.read(self.write(records))))
    records = self.records()[:-1] + [self.event("carControl", 3_000_000_000, {"enabled": True, "latActive": True, "longActive": True}), self.car(2_010_000_000)]
    self.assertTrue(all(not row["valid"] for row in self.read(self.write(records))))
    records = self.records()[:-1] + [self.car(2_010_000_000, canValid=False)]
    rows = self.read(self.write(records))
    self.assertTrue(all(not row["valid"] and row["driver_override"] is None for row in rows))

  def test_older_target_does_not_replace_latest(self):
    records = self.records()[:-1] + [self.controls(1_900_000_000, target=9), self.car(2_010_000_000)]
    self.assertAlmostEqual(.4, self.read(self.write(records))[0]["target"], places=6)

  def test_hold_fault_cancel_and_non_drive_are_recorded_but_ineligible(self):
    for changes in ({"softHoldActive": 2}, {"brakeHoldActive": True}, {"gearShifter": "park"}, {"activateCruise": -1}, {"vehicleSensorsInvalid": True}):
      with self.subTest(changes=changes):
        records = self.records()[:-1] + [self.car(2_010_000_000, **changes)]
        rows = self.read(self.write(records))
        self.assertEqual(2, len(rows))
        self.assertTrue(all(not row["valid"] and not row["recovery_ready"] for row in rows))

  def test_normal_stop_context_is_retained(self):
    records = self.records(stop=True)[:-1] + [self.car(2_010_000_000, standstill=True, vEgo=0)]
    rows = self.read(self.write(records))
    self.assertTrue(rows[1]["valid"])
    self.assertTrue(rows[1]["context"]["should_stop"])
    self.assertTrue(rows[1]["context"]["standstill"])

  def test_nonfinite_target_and_actual_are_unknown(self):
    records = self.records()[:-1] + [self.controls(2_005_000_000, target=float("nan")), self.car(2_010_000_000, aEgo=float("inf"))]
    rows = self.read(self.write(records))
    self.assertIsNone(rows[0]["target"])
    self.assertIsNone(rows[1]["actual"])
    self.assertTrue(all(not row["valid"] for row in rows))

  def test_anchor_duplicates_and_backwards_times_fail(self):
    for time in (2_010_000_000, 2_000_000_000):
      with self.subTest(time=time):
        with self.assertRaisesRegex(ValueError, "strictly increase"):
          self.read(self.write(self.records() + [self.car(time)]))

  def test_raw_truncation_and_invalid_header_fail(self):
    blob = b"".join(row.to_bytes() for row in self.records())
    for payload in (blob[:-1], b"\x00", struct.pack("<I", 512)):
      with self.subTest(size=len(payload)):
        with self.assertRaises(ValueError):
          self.read(self.write([], payload=payload))

  def test_invalid_native_pointer_has_normalized_error(self):
    payload = struct.pack("<IIQ", 0, 1, (1 << 32) | 4)
    with self.assertRaisesRegex(ValueError, "invalid Cap'n Proto event payload"):
      self.read(self.write([], payload=payload))

  def test_truncated_or_trailing_compressed_frames_fail(self):
    for suffix in (".zst", ".bz2"):
      complete = self.write(self.records(), suffix).read_bytes()
      for payload in (complete[:-1], complete + b"garbage"):
        with self.subTest(suffix=suffix, trailing=payload[-7:] == b"garbage"):
          path = self.directory_path / ("broken" + suffix)
          path.write_bytes(payload)
          with self.assertRaises(ValueError):
            self.read(path)

  def test_concatenated_compressed_streams_supported(self):
    records = self.records()
    records.append(self.car(2_030_000_000))
    first = b"".join(row.to_bytes() for row in records[:3])
    last = b"".join(row.to_bytes() for row in records[3:])
    for suffix, compress in ((".zst", self.zstandard.ZstdCompressor().compress), (".bz2", bz2.compress)):
      path = self.directory_path / ("concat" + suffix)
      path.write_bytes(compress(first) + compress(last))
      self.assertEqual(4, len(self.read(path)))

  def test_exact_total_budget_and_cancellation_during_decode(self):
    for suffix in ("", ".zst", ".bz2"):
      with self.subTest(suffix=suffix):
        records = self.records()
        path = self.write(records, suffix)
        size = len(b"".join(row.to_bytes() for row in self.records()))
        self.assertEqual(2, len(self.read(path, max_decompressed_bytes=size)))
        calls = 0

        def cancel_after_reads():
          nonlocal calls
          calls += 1
          return calls >= 8

        with self.assertRaises(InterruptedError):
          self.read(path, cancelled=cancel_after_reads)

  def test_complete_native_payload_accepts_small_unused_budget(self):
    size = len(b"".join(row.to_bytes() for row in self.records()))
    for suffix in ("", ".zst", ".bz2"):
      path = self.write(self.records(), suffix)
      expected = self.read(path)
      for slack in range(5):
        with self.subTest(suffix=suffix, slack=slack):
          self.assertEqual(expected, self.read(path, max_decompressed_bytes=size + slack))
      with self.subTest(suffix=suffix, below_size=True):
        with self.assertRaisesRegex(ValueError, "limit exceeded"):
          self.read(path, max_decompressed_bytes=size - 1)

  def test_closed_log_requires_final_valid_sentinel_and_strict_compression_eof(self):
    for suffix in ("", ".zst", ".bz2"):
      with self.subTest(suffix=suffix):
        records = [self.event("sentinel", 1_900_000_000, {"type": "startOfSegment"})] + self.records() + [self.event("sentinel", 2_100_000_000, {"type": "endOfSegment"})]
        path = self.write(records, suffix)
        result = validate_closed_log(path)
        self.assertEqual("endOfSegment", result["end_sentinel"])
        self.assertEqual(6, result["event_count"])
        self.assertEqual(1, result["services"]["carState"]["count"])
        self.assertEqual(2_100_000_000, result["mono_max_ns"])
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
          validate_closed_log(path)
    prefix = [self.event("sentinel", 1_900_000_000, {"type": "startOfSegment"})]
    for records, reason in ((prefix + self.records(), "missing_end"),
                            (self.records() + [self.event("sentinel", 2_100_000_000, {"type": "endOfRoute"})], "missing_start"),
                            (prefix + self.records() + [self.event("sentinel", 2_100_000_000, {"type": "endOfRoute"}, valid=False)], "invalid_end"),
                            (prefix + self.records() + [self.event("sentinel", 2_100_000_000, {"type": "endOfRoute"}), self.car(2_200_000_000)], "after_end")):
      with self.subTest(reason=reason):
        with self.assertRaisesRegex(ValueError, reason):
          validate_closed_log(self.write(records))

  def test_partial_header_is_truncated_or_over_budget(self):
    payload = b"".join(row.to_bytes() for row in self.records())
    for suffix in ("", ".zst", ".bz2"):
      path = self.write([], suffix, payload=payload + b"\x00")
      for slack in range(1, 5):
        with self.subTest(suffix=suffix, slack=slack):
          with self.assertRaisesRegex(ValueError, "truncated"):
            self.read(path, max_decompressed_bytes=len(payload) + slack)
      path = self.write([], suffix, payload=payload + b"\x00\x00")
      with self.subTest(suffix=suffix, over_budget=True):
        with self.assertRaisesRegex(ValueError, "limit exceeded"):
          self.read(path, max_decompressed_bytes=len(payload) + 1)

  def test_zstd_payload_checksum_is_validated_by_decoder(self):
    path = self.write(self.records(), ".zst")
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)
    with self.assertRaisesRegex(ValueError, "zstd payload"):
      self.read(path)

  def test_zstd_skippable_frames_are_bounded(self):
    path = self.write(self.records(), ".zst")
    compressed = path.read_bytes()
    skip = struct.pack("<II", 0x184D2A50, 0)
    path.write_bytes(skip + compressed)
    self.assertEqual(2, len(self.read(path)))
    path.write_bytes(skip * 5 + compressed)
    with self.assertRaisesRegex(ValueError, "zstd frame count limit exceeded"):
      self.read(path, max_events=4)

  def test_cli_analyzes_native_zstd_and_verifies_preserved_bundle(self):
    records = []
    for index in range(51):
      time = 2_000_000_000 + index * 20_000_000
      records.extend([
        self.event("carControl", time, {"enabled": True, "latActive": True, "longActive": True}),
        self.controls(time),
        self.event("longitudinalPlan", time, {"aTarget": .7, "accels": [-9.]}),
        self.car(time + 1_000_000),
      ])
    source = self.write(records, ".zst")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = self.directory_path / "new-evidence-bundle"
    root = Path(__file__).resolve().parents[5]
    profile = root / "openpilot/selfdrive/carrot/control_advisor/examples/host_profile.json"
    command = [sys.executable, "-m", "openpilot.selfdrive.carrot.control_advisor"]
    completed = subprocess.run(
      [*command, "analyze-rlog", str(source), "--log-kind", "rlog", "--synthetic", "--epoch", "synthetic-0",
       "--profile", str(profile), "--output", str(output)],
      cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    self.assertEqual(0, completed.returncode, completed.stderr)
    self.assertEqual("complete", json.loads(completed.stdout)["state"])
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    self.assertEqual("synthetic", result["source"]["source_kind"])
    self.assertFalse(result["recommendations_enabled"])
    self.assertFalse(result["apply_enabled"])
    self.assertFalse(result["runtime_connected"])
    self.assertEqual(digest, result["input_sha256"])
    self.assertEqual(digest, hashlib.sha256(source.read_bytes()).hexdigest())
    epoch = result["diagnostic"]["axes"]["longitudinal"]["epochs"]["synthetic-0"]
    self.assertGreater(epoch["eligible_count"], 0)
    self.assertAlmostEqual(-.1, epoch["eligible_metrics"]["bias"], places=6)
    self.assertAlmostEqual(.1, epoch["eligible_metrics"]["rmse"], places=6)
    verified = subprocess.run([*command, "verify", str(output)], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", timeout=30, check=False)
    self.assertEqual(0, verified.returncode, verified.stderr)
    manifest = json.loads(verified.stdout)
    self.assertEqual("complete", manifest["state"])
    self.assertEqual(digest, manifest["files"][0]["sha256"])
    self.assertEqual(digest, hashlib.sha256((output / manifest["files"][0]["path"]).read_bytes()).hexdigest())

  def test_budget_and_cancellation_fail_without_source_changes(self):
    path = self.write(self.records())
    for options in ({"max_event_bytes": 64}, {"max_decompressed_bytes": 128}, {"max_events": 3}):
      with self.subTest(options=options):
        with self.assertRaisesRegex(ValueError, "limit exceeded"):
          self.read(path, **options)
    with self.assertRaises(InterruptedError):
      self.read(path, cancelled=lambda: True)
    for options in ({"max_age_ns": True}, {"max_events": 0}, {"max_event_bytes": -1}, {"epoch": ""}):
      defaults = {"epoch": "synthetic-config-0", "max_age_ns": 100_000_000, **options}
      with self.subTest(options=options):
        with self.assertRaises(ValueError):
          list(iter_observations(path, **defaults))


if __name__ == "__main__":
  unittest.main()
