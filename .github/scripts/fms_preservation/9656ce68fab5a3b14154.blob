"""Native compressed fixtures through the real decoder and shared driver masks."""
import bz2
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.analysis import AnalysisProfile
from openpilot.selfdrive.carrot.control_advisor import log_signals
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema, iter_events


PROFILE = AnalysisProfile("native-log-mask-test", 10_000_000, 20_000_000, 200_000_000, 0, 1000)
BUDGET = {"max_events": 10000, "max_samples": 1000, "max_decompressed_bytes": 8 * 1024 * 1024,
          "max_input_bytes": 8 * 1024 * 1024, "max_event_bytes": 1024 * 1024, "max_age_ns": 200_000_000}


class LogSignalTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.parser, cls.log = _schema()
    cls.zstd = importlib.import_module("zstandard")

  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)

  def event(self, kind, mono, values=None, valid=True):
    event = self.log.Event.new_message(logMonoTime=mono, valid=valid)
    value = event.init(kind)
    for key, setting in (values or {}).items():
      setattr(value, key, setting)
    return event

  def events(self, *, mode=3, future=False, pedal=True):
    init = self.event("initData", 10, {"gitCommit": "b" * 40, "dirty": False})
    init.initData.params = {"entries": [{"key": "CruiseMaxVals2", "value": b"100"},
                                        {"key": "ApiToken", "value": b"PRIVATE-SECRET-VALUE"},
                                        {"key": "VEgoStopping", "value": b"not-a-number"}]}
    values = [init, self.event("sentinel", 1_900_000_000, {"type": "startOfSegment"}),
              self.event("carControl", 2_000_000_000, {"enabled": True, "latActive": True, "longActive": True})]
    controls = self.event("controlsState", 2_000_000_000)
    torque = controls.controlsState.lateralControlState.init("torqueState")
    torque.active, torque.actualLateralAccel, torque.desiredLateralAccel = True, .3, .4
    values.extend([controls, self.event("longitudinalPlan", 3_000_000_000 if future else 2_000_000_000,
                                      {"aTarget": .7, "myDrivingMode": mode, "tFollow": 1.5, "trafficStopModelLeadOffset": 0.}),
                   self.event("selfdriveState", 2_000_000_000, {"personality": "standard", "enabled": True})])
    output = self.event("carOutput", 2_000_000_000)
    output.carOutput.actuatorsOutput.torque = -.2
    values.append(output)
    for index in range(10):
      values.append(self.event("carState", 2_010_000_000 + index * 10_000_000,
                              {"canValid": True, "gearShifter": "drive", "vEgo": 40/3.6, "aEgo": .6,
                               "gasPressed": pedal and index == 2, "steeringPressed": pedal and index == 2}))
    values.append(self.event("sentinel", 3_100_000_000, {"type": "endOfSegment"}))
    return values

  def write(self, events, suffix=".zst", *, name=None):
    blobs = []
    for event in events:
      blobs.append(event.to_bytes())
      event.clear_write_flag()
    payload = b"".join(blobs)
    if suffix == ".zst":
      payload = self.zstd.ZstdCompressor(write_checksum=True).compress(payload)
    elif suffix == ".bz2":
      payload = bz2.compress(payload)
    path = self.root / (name or f"00000001--abcdefghij--0.rlog{suffix}")
    path.write_bytes(payload)
    return path

  def extract(self, path, **changes):
    budget = {**BUDGET, **changes}
    return log_signals.extract_log_signals([path], analysis_profile=PROFILE, budget=budget)

  def test_native_three_formats_signal_units_hashes_modes_and_private_projection(self):
    for suffix in ("", ".bz2", ".zst"):
      with self.subTest(suffix=suffix):
        path = self.write(self.events(), suffix)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result = self.extract(path)
        self.assertEqual(len(result["rows"]), 10)
        receipt = result["source_receipts"][0]
        self.assertTrue(receipt["closed"])
        self.assertEqual(receipt["sha256"], digest)
        self.assertEqual(receipt["route"], "00000001--abcdefghij")
        self.assertEqual(receipt["segment"], 0)
        self.assertEqual(receipt["mono_min_ns"], 2_010_000_000)
        self.assertEqual(receipt["decoded_bytes_charged"], receipt["decompressed_bytes"] * 2)
        signal = result["rows"][0]["signals"]
        self.assertAlmostEqual(signal["v_ego"]["value"], 40/3.6, places=5)
        self.assertEqual(signal["v_ego"]["unit"], "m/s")
        self.assertEqual(signal["accel_mode_factor"]["value"], 1.)
        self.assertEqual(signal["accel_mode_factor"]["source_mono_ns"], 2_000_000_000)
        self.assertAlmostEqual(signal["sent_torque"]["value"], .2, places=6)
        self.assertEqual(signal["personality"]["value"], 1.)
        self.assertNotIn("lateral_accel_independent", signal)
        snapshot = result["initial_snapshots"][0]
        self.assertTrue(snapshot["advisory_only"])
        self.assertEqual(snapshot["settings_raw"], {"CruiseMaxVals2": 100})
        self.assertEqual(snapshot["invalid_setting_keys"], ["VEgoStopping"])
        serialized = json.dumps(result)
        self.assertNotIn("PRIVATE-SECRET-VALUE", serialized)
        self.assertNotIn("ApiToken", serialized)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

  def test_shared_masks_keep_short_override_pre_margin_recovery_and_finalized_tail(self):
    result = self.extract(self.write(self.events()))
    rows = result["rows"]
    for axis in ("lateral", "longitudinal"):
      self.assertIn("driver_intervention", rows[2]["excluded_reasons"][axis])
      self.assertIn("intervention_pre_margin", rows[1]["excluded_reasons"][axis])
      self.assertIn("recovery", rows[3]["excluded_reasons"][axis])
      self.assertEqual(rows[5]["excluded_reasons"][axis], [])
      self.assertIn("unfinalized_tail", rows[-1]["excluded_reasons"][axis])

  def test_future_or_unknown_plan_mode_never_becomes_normal(self):
    future = self.extract(self.write(self.events(future=True)))
    self.assertNotIn("accel_mode_factor", future["rows"][0]["signals"])
    self.assertIn("invalid_signal", future["rows"][0]["excluded_reasons"]["longitudinal"])
    unknown = self.extract(self.write(self.events(mode=0)))
    self.assertNotIn("accel_mode_factor", unknown["rows"][0]["signals"])
    for mode, factor in ((1, .9), (2, .8), (3, 1.), (4, 1.2)):
      result = self.extract(self.write(self.events(mode=mode)))
      self.assertEqual(result["rows"][0]["signals"]["accel_mode_factor"]["value"], factor)

  def test_closure_truncation_cancellation_and_budgets_are_failures_not_partial_success(self):
    for records in (self.events()[:-1], self.events()[1:]):
      # No initData is allowed; no start sentinel is not.
      if records[0].which() == "sentinel":
        records = records[1:]
      path = self.write(records)
      with self.assertRaisesRegex(ValueError, "sentinel"):
        self.extract(path)
    path = self.write(self.events())
    for field, limit in (("max_samples", 9), ("max_events", 3), ("max_input_bytes", 1), ("max_decompressed_bytes", 100)):
      with self.subTest(field=field), self.assertRaises(ValueError):
        self.extract(path, **{field: limit})
    with self.assertRaises(InterruptedError):
      log_signals.extract_log_signals([path], analysis_profile=PROFILE, budget=BUDGET, cancelled=lambda: True)
    path.write_bytes(path.read_bytes()[:-1])
    with self.assertRaises(ValueError):
      self.extract(path)

  def test_selected_iterator_whitelist_and_per_service_regressions(self):
    events = self.events()
    events.insert(-1, self.event("selfdriveState", 1_990_000_000, {"personality": "aggressive"}))
    path = self.write(events)
    selected = list(iter_events(path, services=("selfdriveState", "initData")))
    self.assertEqual([row["service"] for row in selected], ["initData", "selfdriveState", "selfdriveState"])
    self.assertFalse(selected[-1]["service_time_ordered"])
    self.assertNotIn("PRIVATE-SECRET-VALUE", json.dumps(selected))
    with self.assertRaisesRegex(ValueError, "service selection"):
      list(iter_events(path, services=("gpsLocationExternal",)))

  def test_mutation_after_extract_fails_before_receipt_is_returned(self):
    path = self.write(self.events())
    original = log_signals.iter_events
    def altered(*args, **kwargs):
      yield from original(*args, **kwargs)
      with path.open("ab") as stream:
        stream.write(b"changed-after-decode")
    with patch.object(log_signals, "iter_events", side_effect=altered):
      with self.assertRaisesRegex(ValueError, "source changed"):
        self.extract(path)

  def test_input_totals_and_duplicate_content_not_independent_routes(self):
    first = self.write(self.events(), name="00000001--abcdefghij--0.rlog.zst")
    second = self.root / "00000002--abcdefghij--0.rlog.zst"
    second.write_bytes(first.read_bytes())
    with self.assertRaisesRegex(ValueError, "duplicate log content"):
      log_signals.extract_log_signals([first, second], analysis_profile=PROFILE, budget=BUDGET)
    with self.assertRaisesRegex(ValueError, "duplicate log input"):
      log_signals.extract_log_signals([first, first], analysis_profile=PROFILE, budget=BUDGET)
    single = self.extract(first)
    used = single["budget_usage"]["decoded_bytes_charged"]
    self.assertTrue(self.extract(first, max_decompressed_bytes=used))
    with self.assertRaisesRegex(ValueError, "pass budget"):
      self.extract(first, max_decompressed_bytes=used-1)


if __name__ == "__main__":
  unittest.main()
