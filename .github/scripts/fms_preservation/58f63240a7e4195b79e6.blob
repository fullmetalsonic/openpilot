import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.archive import SegmentArchive
from openpilot.selfdrive.carrot.control_advisor.consumer_observer import CONSUMER_PATHS, OBSERVER_PATH
from openpilot.selfdrive.carrot.control_advisor.inspection import inspect_session
from openpilot.selfdrive.carrot.control_advisor.log_history import load_log_history
from openpilot.selfdrive.carrot.control_advisor.registry import load_registry
from openpilot.selfdrive.carrot.control_advisor.rlog import _schema
from openpilot.selfdrive.carrot.control_advisor.settings_history import SettingsRecorder, load_history


ROUTE = "00000000--0123456789"
BOOT = "00000000-0000-0000-0000-000000000001"


class NativeSessionFixture:
  """Actual pinned settings, wire event, closed Event log and durable copy receipt."""
  def __init__(self, root, *, change=False, gap=False, fractional=False, consumer_hash=None,
               extra_polls=0, later_cancelled=False, omit_consumer=False, duplicate_segment=False, late_foreign_event=False, recorder_options=None):
    self.root, self.time = root, 2_000_000_000
    self.settings, self.logs, self.session = [root / name for name in ("settings", "logs", "session")]
    for path in (self.settings, self.logs, self.session):
      path.mkdir()
    for item in load_registry()["items"]:
      (self.settings / item["key"]).write_text("0")
    if fractional:
      (self.settings / "PathOffset").write_text("1.0")
    (self.settings / "CurrentRoute").write_text(ROUTE)
    self.recorder = SettingsRecorder(self.settings, self.session, "native-fixture", clock_ns=self.clock, **(recorder_options or {}))
    self.first = self.recorder.sample()
    if change:
      (self.settings / "PathOffset").write_text("20")
    self.time = 5_000_000_000 if gap else 3_000_000_000
    self.second = self.recorder.sample()
    self.polls = [self.first, self.second]
    for index in range(extra_polls):
      self.time = (4 + index) * 1_000_000_000
      self.polls.append(self.recorder.sample())
    self.metadata = self.session / "recording.json"
    self.metadata.write_text(json.dumps({"session_id": "native-fixture", "source_mode": "live",
                                         "clock": {"domain": "native_monotonic", "scale": 1}}))
    repo = Path(__file__).resolve().parents[5]
    paths = (OBSERVER_PATH, CONSUMER_PATHS["controls.torque"])
    hashes = {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in paths}
    if consumer_hash:
      hashes[paths[1]] = consumer_hash
    self.event = {"schema_version": 1, "consumer_id": "controls.torque", "boot_id": BOOT, "pid": 42,
                  "process_instance_id": "a" * 32, "consumer_instance_id": "b" * 32, "sequence": 1,
                  "produced_mono_ns": 2_000_000_050, "cycle_id": 1, "phase": "sampled_use",
                  "fields": {"LateralTorqueAccelFactor": {"read_value": 0, "effective_value": 0, "state": "used",
                    "source": "stored", "read_mono_ns": 2_000_000_040}}, "context": {"active": True},
                  "dropped_events": 0, "source_hashes": hashes}
    if not omit_consumer:
      self.recorder.append_consumer_observation(self.event)
    if late_foreign_event:
      foreign = {**self.event, "boot_id": "00000000-0000-0000-0000-000000000002", "pid": 43,
                 "produced_mono_ns": 10_000_000_000, "source_hashes": {path: "f" * 64 for path in paths}}
      self.recorder.append_consumer_observation(foreign)
    log_end = max(4_000_000_000, self.time + 1000)
    diagnostic = {"revision": 1, "window_start_ns": 2_000_001_000, "window_end_ns": log_end,
                  "profile_id": "native-fixture", "invalidated": False, "partial_window": False,
                  "observed_settings_revision": 1, "route_observation": self.recorder.revision_metadata(1)["route_observation"]}
    data = json.dumps(diagnostic, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(data).hexdigest()
    (self.session / "diagnostics").mkdir()
    (self.session / "diagnostics" / f"1-{digest[:16]}.json").write_bytes(data)
    record = {key: value for key, value in diagnostic.items() if key not in {"observed_settings_revision", "route_observation"}}
    reference = self.recorder.append_diagnostic({**record, "sha256": digest}, settings_revision=1)["journal"]
    _, log = _schema()
    data = []
    for stamp, service, values in ((1_000_000_000, "sentinel", {"type": "startOfRoute"}),
                                    (2_000_001_000, "carState", {"aEgo": .1}),
                                    (log_end, "carState", {"aEgo": .2}),
                                    (log_end + 1_000_000_000, "sentinel", {"type": "endOfRoute"})):
      event = log.Event.new_message(logMonoTime=stamp, valid=True)
      payload = event.init(service)
      for key, value in values.items():
        setattr(payload, key, value)
      data.append(event.to_bytes())
    segment = self.logs / f"{ROUTE}--0"
    segment.mkdir()
    original = segment / "rlog"
    original.write_bytes(b"".join(data))
    archive = SegmentArchive(self.logs, self.session / "archive", session_id="native-fixture", source_mode="live",
                              byte_limit=1024 * 1024, max_files=16, free_reserve_bytes=0)
    try:
      self.saved = archive.preserve(original, window_ns=(diagnostic["window_start_ns"], diagnostic["window_end_ns"]),
                                    diagnostic_revision=1, settings_revision=1, journal_ref=reference)
      self.duplicate = None
      if duplicate_segment:
        duplicate = self.logs / f"{ROUTE}--1"
        duplicate.mkdir()
        (duplicate / "rlog").write_bytes(original.read_bytes())
        self.duplicate = archive.preserve(duplicate / "rlog", window_ns=(diagnostic["window_start_ns"], diagnostic["window_end_ns"]),
                                          diagnostic_revision=1, settings_revision=1, journal_ref=reference)
    finally:
      archive.close()
    self.source = {"sha256": self.saved["sha256"], "bytes": self.saved["bytes"], "route": ROUTE, "segment": 0}
    self.recorder.append_archive({k: v for k, v in self.saved.items() if k not in {"source_relative", "decompressed_bytes"}})
    if self.duplicate:
      self.recorder.append_archive({k: v for k, v in self.duplicate.items() if k not in {"source_relative", "decompressed_bytes"}})
    self.recorder.append_job_status({
      "diagnostic_revision": 1, "settings_revision": 1, "state": "complete",
      "window_start_ns": diagnostic["window_start_ns"], "window_end_ns": diagnostic["window_end_ns"],
      "route_observation": diagnostic["route_observation"], "settings_history_reference": reference,
      "actual_boundary_gaps": [], "gap_tolerance_ns": 250_000_000, "missing_segments": [], "missing_segments_count": 0,
      "endpoint_start_covered": True, "endpoint_end_covered": True, "completion_scope": "archive_work_only_host_profile",
      "coverage_complete": False, "interpolation_applied": False})
    if later_cancelled:
      later = {**diagnostic, "revision": 2, "window_start_ns": log_end + 1, "window_end_ns": log_end + 1_000_000_000}
      data = json.dumps(later, sort_keys=True, separators=(",", ":")).encode()
      digest = hashlib.sha256(data).hexdigest()
      (self.session / "diagnostics" / f"2-{digest[:16]}.json").write_bytes(data)
      record = {key: value for key, value in later.items() if key not in {"observed_settings_revision", "route_observation"}}
      reference = self.recorder.append_diagnostic({**record, "sha256": digest}, settings_revision=1)["journal"]
      self.recorder.append_job_status({
        "diagnostic_revision": 2, "settings_revision": 1, "state": "cancelled",
        "window_start_ns": later["window_start_ns"], "window_end_ns": later["window_end_ns"],
        "route_observation": later["route_observation"], "settings_history_reference": reference,
        "actual_boundary_gaps": [], "gap_tolerance_ns": 250_000_000, "missing_segments": [], "missing_segments_count": 0,
        "endpoint_start_covered": False, "endpoint_end_covered": False,
        "completion_scope": "archive_work_only_host_profile", "coverage_complete": False, "interpolation_applied": False})
    self.recorder.close()

  def clock(self):
    self.time += 1
    return self.time


class LogHistoryTests(unittest.TestCase):
  def fixture(self, **kwargs):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    return NativeSessionFixture(Path(temporary.name), **kwargs)

  def test_actual_native_closed_log_settings_and_consumer_link_without_ack(self):
    fixture = self.fixture()
    before = {path: path.read_bytes() for path in fixture.root.rglob("*") if path.is_file()}
    result = load_log_history(fixture.session, [fixture.source])
    self.assertEqual("linked", result["state"], result["reason_codes"])
    self.assertEqual("witnessed_coorigin", result["clock_binding"])
    self.assertFalse(result["consumer_applied"])
    self.assertEqual("not_recorded", result["helper_source_provenance"])
    self.assertTrue(result["raw_sources_verified"])
    epoch, = result["epochs"]
    self.assertEqual(max(fixture.first["observation"]["end_ns"], fixture.saved["start_mono_ns"]), epoch["start_ns"])
    self.assertEqual(fixture.second["observation"]["begin_ns"], epoch["end_ns"])
    self.assertEqual(fixture.event, epoch["consumer_evidence"][0])
    self.assertLess(fixture.event["produced_mono_ns"], epoch["start_ns"])
    self.assertEqual(47, len(epoch["settings_raw"]))
    self.assertNotIn("CurrentRoute", epoch["settings_raw"])
    self.assertFalse(epoch["provenance"]["continuous_settings_proven"])
    self.assertEqual(before, {path: path.read_bytes() for path in before})

  def test_history_optional_records_preserve_verified_bytes_and_default_api(self):
    fixture = self.fixture()
    path = fixture.session / "settings.jsonl"
    plain = load_history(path)
    snapshot = load_history(path, include_records=True)
    self.assertNotIn("records", plain)
    self.assertEqual(plain, {key: value for key, value in snapshot.items() if key not in {"records", "verified_bytes_sha256"}})
    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), snapshot["verified_bytes_sha256"])
    self.assertEqual(plain["verified_events"], len(snapshot["records"]))

  def test_changed_or_gap_poll_never_creates_a_stable_bracket(self):
    for kwargs in ({"change": True}, {"gap": True}):
      with self.subTest(kwargs=kwargs):
        fixture = self.fixture(**kwargs)
        result = load_log_history(fixture.session, [fixture.source])
        self.assertEqual("unlinked", result["state"])
        self.assertEqual([], result["epochs"])

  def test_fractional_raw_setting_is_not_coerced_to_integer(self):
    fixture = self.fixture(fractional=True)
    result = load_log_history(fixture.session, [fixture.source])
    self.assertEqual("linked", result["state"], result["reason_codes"])
    self.assertIsNone(result["epochs"][0]["settings_raw"]["PathOffset"])

  def test_routine_polls_coalesce_but_acquisition_holes_remain_ineligible(self):
    fixture = self.fixture(extra_polls=40)
    result = load_log_history(fixture.session, [fixture.source])
    self.assertEqual("linked", result["state"], result["reason_codes"])
    epoch, = result["epochs"]
    self.assertEqual(41, len(epoch["eligible_intervals"]))
    scan = fixture.second["observation"]
    self.assertTrue(all(not (interval["start_ns"] <= scan["begin_ns"] and interval["end_ns"] >= scan["end_ns"])
                        for interval in epoch["eligible_intervals"]))
    self.assertEqual(fixture.polls[-1]["observation"]["begin_ns"], epoch["end_ns"])

  def test_later_cancelled_job_does_not_discard_an_earlier_verified_source(self):
    fixture = self.fixture(later_cancelled=True)
    self.assertEqual("partial", inspect_session(fixture.session)["state"])
    result = load_log_history(fixture.session, [fixture.source])
    self.assertEqual("linked", result["state"], result["reason_codes"])
    self.assertIn("session_has_incomplete_evidence", result["reason_codes"])

  def test_later_unrelated_boot_and_code_do_not_rewrite_the_selected_bracket(self):
    fixture = self.fixture(late_foreign_event=True)
    result = load_log_history(fixture.session, [fixture.source])
    self.assertEqual("linked", result["state"], result["reason_codes"])
    self.assertEqual(BOOT, result["epochs"][0]["boot_id"])
    self.assertEqual([fixture.event], result["epochs"][0]["consumer_evidence"])

  def test_opaque_copy_name_uses_unique_verified_bytes_identity(self):
    fixture = self.fixture()
    source = {**fixture.source, "id": "opaque-copy", "route": None, "segment": None}
    result = load_log_history(fixture.session, [source])
    self.assertEqual("linked", result["state"], result["reason_codes"])
    self.assertEqual(("opaque-copy", ROUTE, 0), tuple(result["source_receipts"][0][key] for key in ("source_id", "route", "segment")))
    self.assertEqual(["session_archive_source_unlinked"], load_log_history(
      fixture.session, [{**source, "bytes": source["bytes"] + 1}])["reason_codes"])
    fixture = self.fixture(duplicate_segment=True)
    source = {**fixture.source, "route": None, "segment": None}
    self.assertEqual(["session_archive_source_ambiguous"], load_log_history(fixture.session, [source])["reason_codes"])
    self.assertEqual("linked", load_log_history(fixture.session, [fixture.source])["state"])

  def test_missing_consumer_identity_or_cross_session_metadata_is_not_fabricated(self):
    fixture = self.fixture(omit_consumer=True)
    self.assertEqual(["session_consumer_boot_unlinked"], load_log_history(fixture.session, [fixture.source])["reason_codes"])
    fixture = self.fixture()
    metadata = json.loads(fixture.metadata.read_text())
    metadata["session_id"] = "another-session"
    fixture.metadata.write_text(json.dumps(metadata))
    self.assertEqual(["session_history_incomplete_or_mismatched"], load_log_history(fixture.session, [fixture.source])["reason_codes"])

  def test_source_hash_route_segment_and_consumer_code_mismatch_are_blocked(self):
    fixture = self.fixture()
    for key, value in (("sha256", "f" * 64), ("route", "00000000--9876543210"), ("segment", 1)):
      result = load_log_history(fixture.session, [{**fixture.source, key: value}])
      self.assertEqual(["session_archive_source_unlinked"], result["reason_codes"])
    fixture = self.fixture(consumer_hash="f" * 64)
    self.assertEqual(["session_consumer_code_mismatch"], load_log_history(fixture.session, [fixture.source])["reason_codes"])

  def test_synthetic_or_missing_clock_cannot_be_relabelled_native(self):
    fixture = self.fixture()
    for metadata in ({"session_id": "native-fixture", "source_mode": "live"},
                     {"session_id": "native-fixture", "source_mode": "synthetic", "clock": {"domain": "native_monotonic", "scale": 1}}):
      fixture.metadata.write_text(json.dumps(metadata))
      self.assertEqual(["session_native_clock_unlinked"], load_log_history(fixture.session, [fixture.source])["reason_codes"])

  def test_truncated_and_unclosed_history_are_blocked(self):
    fixture = self.fixture()
    path = fixture.session / "settings.jsonl"
    data = path.read_bytes()
    for changed in (data[:-3], b"\n".join(data.splitlines()[:-1]) + b"\n"):
      path.write_bytes(changed)
      result = load_log_history(fixture.session, [fixture.source])
      self.assertEqual("unlinked", result["state"])
      self.assertEqual([], result["epochs"])

  def test_change_between_snapshot_and_inspector_is_rejected(self):
    fixture = self.fixture()
    def inspect_then_change(*args, **kwargs):
      result = inspect_session(*args, **kwargs)
      fixture.metadata.write_text(fixture.metadata.read_text() + " ")
      return result
    with patch("openpilot.selfdrive.carrot.control_advisor.log_history.inspect_session", side_effect=inspect_then_change):
      result = load_log_history(fixture.session, [fixture.source])
    self.assertEqual(["session_evidence_changed"], result["reason_codes"])


if __name__ == "__main__":
  unittest.main()
