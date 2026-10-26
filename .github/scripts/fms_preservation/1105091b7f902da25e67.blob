from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.comparison import _side, _profile, _consumers, _matched, evaluate_comparison
from openpilot.selfdrive.carrot.control_advisor.comparison_stream import (InputWriter, InputReader, streaming_side,
  streaming_matched, read_interval_page, write_interval_pages, validate_interval_reference)
from openpilot.selfdrive.carrot.control_advisor.comparison_intervals import decode_exclusions
from .test_comparison import comparison_fixture


class StreamingComparisonTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)

  def disk_side(self, side, axis, profile, mask, timelines, expected, key, floor, *, chunk_rows=20):
    writer = InputWriter(self.root / "raw")
    try:
      for row in side["observations"]:
        writer.append(row)
      reference = writer.finish()
    finally:
      writer.close()
    result = streaming_side({**side, "observations": reference, "comparison_output_root": str(self.root)}, axis,
      profile, mask, timelines, expected, key, floor, _side, chunk_rows=chunk_rows)
    self.addCleanup(result[1].owner.close)
    return result

  def intervals(self, reference):
    result = []
    for index in range(reference["page_count"]):
      result.extend(decode_exclusions(read_interval_page(self.root, reference, index), 2))
    self.assertEqual(len(result), reference["interval_count"])
    return result

  def test_existing_thread_policy_failure_blocks_before_any_source_work(self):
    from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver
    from openpilot.selfdrive.carrot.control_advisor.apply_requests import RequestConflict
    resolver = CandidateEvidenceResolver({}, lambda _: self.fail("current context queried before execution policy"))
    with patch("openpilot.selfdrive.carrot.control_advisor.worker_runtime.configure_worker_runtime",
               return_value={"state": "degraded", "reason": "native_worker_policy_incomplete"}) as configure:
      with self.assertRaisesRegex(RequestConflict, "comparison_worker_policy_incomplete"):
        resolver.comparison_cohort({}, {}, {})
      configure.assert_called_once_with("live")
    target = {"target_id": "unconfigured", "session_id": "unit", "session_generation": 1}
    capture = {"target_id": "unconfigured", "session_id": "unit", "session_generation": 1}
    for policy in ({"state": "applied", "reason": None}, {"state": "not_applicable", "reason": "non_native_runtime"}):
      with self.subTest(policy=policy), patch("openpilot.selfdrive.carrot.control_advisor.worker_runtime.configure_worker_runtime", return_value=policy):
        with self.assertRaises(RequestConflict) as held:
          resolver.comparison_cohort({"target": target, "source_candidate": {"evidence_reference": {}, "bundle_sha256": "a" * 64}}, capture, {})
        self.assertNotEqual(str(held.exception), "comparison_worker_policy_incomplete")

  def test_chunk_guard_masks_metrics_counts_and_duration_equal_original(self):
    for variant in ("clean", "held", "driver_gap_invalid", "settings_holes", "epochs_output"):
      with self.subTest(variant=variant):
        application, cohort, profile = comparison_fixture(duration_seconds=40)
        profile["analysis_profile"].update(pre_margin_ns=100_000_000, recovery_ns=2_000_000_000,
          dependency_ns=100_000_000, max_gap_ns=250_000_000)
        mask = _profile(profile)
        timelines, _, old, _ = _consumers(application, cohort, {"key": "CruiseMaxVals0", "raw_scale": .01})
        side = deepcopy(cohort["before"])
        if variant in {"held", "driver_gap_invalid"}:
          for row in side["observations"]:
            row["context"]["actual_mono_ns"] = max(0, row["mono_ns"] - 100_000_000)
        if variant == "driver_gap_invalid":
          side["observations"] = [row for index, row in enumerate(side["observations"]) if not 81 <= index < 88]
          for index, row in enumerate(side["observations"]):
            row["valid"] = not 121 <= index < 125
            row["driver_override"] = True if index == 181 else None if index == 211 else False
        if variant == "settings_holes":
          side["eligible_intervals"] = [{"start_ns": n * 1_000_000_000 + 100, "end_ns": (n + 1) * 1_000_000_000} for n in range(40)]
        if variant == "epochs_output":
          for index, row in enumerate(side["observations"]):
            if index >= 170:
              row["epoch"] = "second-observed-epoch"
            row["context"]["saturated"] = index % 63 == 0
            row["context"]["output_limited"] = None if index % 7 == 0 else False
        reference = _side(side, "longitudinal", profile, mask, timelines, old, "CruiseMaxVals0", 0)
        actual = self.disk_side(side, "longitudinal", profile, mask, timelines, old, "CruiseMaxVals0", 0)
        decoded = {**actual[0], "exclusions": self.intervals(actual[0]["exclusions"])}
        self.assertEqual(reference[0], decoded)
        self.assertEqual([row.mono_ns for row in reference[1]], [row.mono_ns for row in actual[1]])

  def test_exact_common_stratum_weighted_p95_rmse_and_selected_duration(self):
    application, cohort, profile = comparison_fixture(duration_seconds=40)
    mask = _profile(profile)
    timelines, observed, old, new = _consumers(application, cohort, {"key": "CruiseMaxVals0", "raw_scale": .01})
    plain, disk = [], []
    for name, expected in (("before", old), ("after", new)):
      side = deepcopy(cohort[name])
      for index, row in enumerate(side["observations"]):
        row["actual"] += (index % 13) * .003
        row["target"] = .2 if index % 7 < 3 else 1.
        row["context"]["v_ego"] = 6. if index % 5 == 0 else 10.
      plain.append(_side(side, "longitudinal", profile, mask, timelines, expected, "CruiseMaxVals0", side["eligible_intervals"][0]["start_ns"]))
      disk.append(self.disk_side(side, "longitudinal", profile, mask, timelines, expected, "CruiseMaxVals0", side["eligible_intervals"][0]["start_ns"]))
    expected = _matched(plain[0][1], plain[1][1], (plain[0][2], plain[1][2]), profile, mask)
    actual = streaming_matched(disk[0][1], disk[1][1], profile, mask)
    self.assertEqual(expected, actual)

  def test_paged_intervals_exact_roundtrip_and_tampering_is_rejected(self):
    connection = sqlite3.connect(":memory:")
    self.addCleanup(connection.close)
    connection.execute("CREATE TABLE intervals(reason TEXT,start INTEGER,end INTEGER,count INTEGER)")
    intervals = [{"reason": "reason_" + chr(97 + index % 9), "start_ns": 2**53 + index * 1000,
      "end_ns": 2**53 + index * 1000 + 900, "sample_count": index % 31 + 1} for index in range(5000)]
    connection.executemany("INSERT INTO intervals VALUES(:reason,:start_ns,:end_ns,:sample_count)", intervals)
    reference = write_interval_pages(connection, self.root)
    self.assertEqual(intervals, self.intervals(reference))
    self.assertLess(len(json.dumps(reference).encode()), 512)
    self.assertGreater(reference["page_count"], 1)
    with self.assertRaises(ValueError):
      validate_interval_reference({**reference, "path": "../outside/manifest.json"})
    page = self.root / reference["path"]
    payload = page.parent / "000000.json"
    payload.write_bytes(payload.read_bytes() + b" ")
    with self.assertRaises(ValueError):
      read_interval_page(self.root, reference, 0)

  def test_full_evaluator_disk_protocol_matches_legacy_public_values(self):
    application, cohort, profile = comparison_fixture(duration_seconds=40)
    expected = evaluate_comparison(application, cohort, profile)
    actual_cohort = deepcopy(cohort)
    for name in ("before", "after"):
      writer = InputWriter(self.root / "raw")
      for row in actual_cohort[name]["observations"]:
        writer.append(row)
      actual_cohort[name]["observations"] = writer.finish()
      actual_cohort[name]["comparison_output_root"] = str(self.root)
    writer = InputWriter(self.root / "raw", kind="events")
    for event in actual_cohort["consumer_events"]:
      writer.append(event)
    actual_cohort["consumer_events"] = writer.finish()
    actual = evaluate_comparison(application, actual_cohort, profile)
    self.assertEqual(actual["state"], "comparison_ready", actual)
    self.assertEqual(actual["comparison"]["schema_version"], 3)
    decoded = deepcopy(actual["comparison"])
    decoded["schema_version"] = expected["comparison"]["schema_version"]
    for name in ("before", "after"):
      decoded[name]["exclusions"] = self.intervals(decoded[name]["exclusions"])
    self.assertEqual(expected["comparison"], decoded)
    self.assertEqual(list((self.root / "raw").glob("stats-*.sqlite3")), [])

  def test_one_hour_each_side_exceeds_legacy_counts_without_raising_budgets(self):
    application, cohort, profile = comparison_fixture()
    application["stored_mono_ns"] = 3610_000_000_000
    cohort["setting_change_interval"] = {"after_ns": 3609_000_000_000, "before_ns": 3611_000_000_000}
    for name, start, end in (("before", 0, 3600), ("after", 3616, 7216)):
      template = deepcopy(cohort[name]["observations"][0])
      writer = InputWriter(self.root / "raw")
      for tick in range(start * 10, end * 10 + 1):
        row = deepcopy(template)
        row["mono_ns"] = tick * 100_000_000
        for field in ("target_mono_ns", "actual_mono_ns", "car_control_mono_ns"):
          row["context"][field] = row["mono_ns"]
        writer.append(row)
      cohort[name].update(observations=writer.finish(), comparison_output_root=str(self.root),
        eligible_intervals=[{"start_ns": start * 1_000_000_000, "end_ns": end * 1_000_000_000}],
        window={"start_ns": (start + 2) * 1_000_000_000, "end_ns": (end - 3) * 1_000_000_000,
                "finalized_through_ns": (end - 3) * 1_000_000_000})
    templates = cohort["consumer_events"][:2]
    writer = InputWriter(self.root / "raw", kind="events")
    for second in range(7222):
      for index, template in enumerate(templates):
        event = deepcopy(template)
        event.update(sequence=second + 1, cycle_id=second, produced_mono_ns=second * 1_000_000_000)
        field = event["fields"]["CruiseMaxVals0"]
        field.update(read_value=1.3 if second >= 3611 + index else 1.,
                     effective_value=1.3 if second >= 3611 + index else 1., read_mono_ns=second * 1_000_000_000)
        writer.append(event)
    cohort["consumer_events"] = writer.finish()
    self.assertEqual(profile["analysis_profile"]["max_samples"], 10000)
    result = evaluate_comparison(application, cohort, profile)
    self.assertEqual(result["state"], "comparison_ready", result)
    public = result["comparison"]
    self.assertEqual(public["schema_version"], 3)
    self.assertLessEqual(len(json.dumps(public).encode()), 32 * 1024)
    for name in ("before", "after"):
      self.assertEqual(public[name]["sample_count"], 35951)
      self.assertEqual(public[name]["eligible_count"], 35951)
      self.assertEqual(public[name]["eligible_duration_ns"], 3595_000_000_000)
      self.assertEqual(public["matched"][name]["count"], 35951)
    self.assertEqual(public["matched"]["before"]["p95_absolute_error"], abs(.7 - 1.))
    self.assertEqual(public["matched"]["after"]["p95_absolute_error"], abs(.9 - 1.))


if __name__ == "__main__":
  unittest.main()
