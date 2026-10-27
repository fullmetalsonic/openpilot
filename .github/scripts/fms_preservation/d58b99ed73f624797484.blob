"""Offline limits are distinct from capture; event onsets must be observed."""
import json
from pathlib import Path
import tempfile
import unittest

from openpilot.selfdrive.carrot.control_advisor import candidates, pid_burst
from openpilot.selfdrive.carrot.control_advisor.candidate_report import (
  MAX_INPUT_BYTES, MAX_PID_INPUT_BYTES, MAX_PID_ANALYSIS_ROWS, _read_candidate_input)


class OfflinePidBudgetTests(unittest.TestCase):
  def test_capture_budget_is_unchanged_and_offline_rows_are_separate(self):
    self.assertEqual(6000, pid_burst.MAX_ROUND_ROWS)
    self.assertEqual(8 * 1024 * 1024, pid_burst.MAX_ROUND_BYTES)
    self.assertEqual(1000, pid_burst.MAX_BLOCK_ROWS)
    self.assertEqual(12000, MAX_PID_ANALYSIS_ROWS)

  def test_only_recorded_pid_input_gets_larger_bounded_reader(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'input.json'
      payload = {'source_kind': 'recorded', 'pid_trace_blocks': [{}], 'padding': 'x' * MAX_INPUT_BYTES}
      path.write_text(json.dumps(payload), encoding='utf8')
      self.assertGreater(path.stat().st_size, MAX_INPUT_BYTES)
      self.assertEqual(payload, _read_candidate_input(path)[0])
      # Reader eligibility never grants evidence/model validity.
      with self.assertRaises(ValueError):
        candidates._evidence(payload, {})
      for source, traces in (('synthetic', [{}]), ('recorded', [])):
        payload.update(source_kind=source, pid_trace_blocks=traces)
        path.write_text(json.dumps(payload), encoding='utf8')
        with self.assertRaises(ValueError):
          _read_candidate_input(path)
      path.write_bytes(b' ' * (MAX_PID_INPUT_BYTES + 1))
      with self.assertRaisesRegex(ValueError, 'evidence_byte_limit'):
        _read_candidate_input(path)

  def event_count(self, prefix, recovery, gap=False):
    values = [0.] * prefix + [.4] * 50 + [0.] * recovery
    rows = [{'route_id': 'boundary', 'block_id': 'fit', 'mono_ns': 1_000_000_000 + index * 10_000_000,
      'reference': value, 'signals': {'desired_lateral_accel': {'value': value}}} for index, value in enumerate(values)]
    if gap:
      for row in rows[prefix:]:
        row['mono_ns'] += 3_000_000_000
    return candidates._event_records({'fit': rows, 'select': [], 'holdout': []}, 'LateralTorqueKf')[1]['event_counts']['fit']

  def test_observed_quiet_input_recovery_is_one_complete_event(self):
    self.assertEqual(1, self.event_count(50, 250))

  def test_left_and_right_censor_and_gap_do_not_become_complete(self):
    self.assertEqual(0, self.event_count(0, 250))
    self.assertEqual(0, self.event_count(1, 250))
    self.assertEqual(0, self.event_count(49, 250))
    self.assertEqual(0, self.event_count(10, 0))
    self.assertEqual(0, self.event_count(10, 250, gap=True))

  def test_exact_quiet_duration_boundary(self):
    for duration, expected in ((0, 0), (10_000_000, 0), (499_000_000, 0), (500_000_000, 1)):
      with self.subTest(duration=duration):
        # Dense 1ms input establishes the boundary without assuming a gap was quiet.
        rows = [{'route_id':'boundary', 'block_id':'fit', 'mono_ns': 1_000_000_000 + stamp,
          'reference': 0., 'signals': {'desired_lateral_accel': {'value':0.}}}
          for stamp in range(0, duration, 1_000_000)]
        for index in range(300):
          value = .4 if index < 50 else 0.
          rows.append({'route_id':'boundary', 'block_id':'fit', 'mono_ns':1_000_000_000 + duration + index*10_000_000,
            'reference':value, 'signals': {'desired_lateral_accel':{'value':value}}})
        summary = candidates._event_records({'fit':rows,'select':[],'holdout':[]}, 'LateralTorqueKf')[1]
        self.assertEqual(expected, summary['event_counts']['fit'])


if __name__ == '__main__':
  unittest.main()
