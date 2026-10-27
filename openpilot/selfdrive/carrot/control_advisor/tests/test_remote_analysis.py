"""Executor isolation and strict owner-operated transfer boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.execution import preference, validate_execution
from openpilot.selfdrive.carrot.control_advisor.periodic import PeriodicAnalyzer, _atomic, _hash
from openpilot.selfdrive.carrot.control_advisor.remote_analysis import _manifest, _verify, _ticket


class RemoteAnalysisTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)

  def test_native_export_preserves_only_referenced_diagnostics_and_rejects_changes(self):
    import hashlib
    from .test_native_calibration_flow import NativeCalibrationFixture
    from .test_log_candidates import BOOT, ROUTE
    from openpilot.selfdrive.carrot.control_advisor.remote_analysis import export_input
    from openpilot.selfdrive.carrot.control_advisor.runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
    fixture = NativeCalibrationFixture(self.root / 'input', keep_open=True, consumer_samples=60)
    self.addCleanup(fixture.close)
    analyzer = PeriodicAnalyzer(fixture.session, BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH, self.root / 'device')
    self.addCleanup(analyzer.close)
    analyzer.set_executor('remote_pc')
    intent = {'schema_version': 1, 'session_id': fixture.session_id, 'request_id': 'diagnostic-closure',
      'trigger': 'manual', 'range_start_ns': 2_000_000_000, 'requested_end_ns': 61_900_000_000,
      'cut_revision': 0, 'trip_binding': {'vehicle_id': None, 'boot_id': BOOT, 'route': ROUTE},
      'profile_sha256': hashlib.sha256(Path(BUILTIN_PROFILE_PATH).read_bytes()).hexdigest(),
      'objectives_sha256': hashlib.sha256(Path(BUILTIN_OBJECTIVES_PATH).read_bytes()).hexdigest()}
    analyzer.request_analysis(intent)
    analyzer.offer(fixture.checkpoint)
    with patch('openpilot.selfdrive.carrot.control_advisor.periodic.subprocess.Popen', side_effect=AssertionError('remote child')):
      self.assertEqual('running', analyzer.tick()['state'])
    source = next((fixture.session / 'diagnostics').glob('*.json'))
    before = source.read_bytes()
    (source.parent / 'unrelated.json').write_text('{}')
    target = self.root / 'transfer'
    export_input(analyzer._job, target)
    self.assertEqual(before, (target / 'session/diagnostics' / source.name).read_bytes())
    self.assertFalse((target / 'session/diagnostics/unrelated.json').exists())
    for label in ('changed', 'missing'):
      with self.subTest(label=label):
        try:
          if label == 'changed':
            source.write_bytes(before + b' ')
          else:
            source.unlink()
          with self.assertRaises((ValueError, OSError)):
            export_input(analyzer._job, self.root / label)
          self.assertFalse((self.root / label / 'remote-manifest.json').exists())
        finally:
          source.write_bytes(before)

  def test_remote_queue_never_spawns_and_cancels_on_explicit_mode_switch(self):
    analyzer = PeriodicAnalyzer(self.root / 'session', self.root / 'profile.json', self.root / 'objectives.json', self.root / 'output')
    analyzer.set_executor('remote_pc')
    identity = {'analysis_execution': {'executor': 'remote_pc', 'revision': analyzer.execution_snapshot()['revision']}}
    job_id = _hash(identity)
    analyzer._job = self.root / 'output/jobs' / (job_id + '-12345678')
    analyzer._job.mkdir(parents=True)
    _atomic(analyzer._job / 'request.json', {'job_id': job_id, 'identity': identity})
    with patch('openpilot.selfdrive.carrot.control_advisor.periodic.subprocess.Popen', side_effect=AssertionError('remote spawned locally')):
      analyzer._start_child()
      execution = validate_execution(analyzer.execution_snapshot())
      self.assertEqual(execution['pending_job'], analyzer._job.name)
      self.assertEqual(_ticket(analyzer._job)[0]['executor_revision'], execution['revision'])
      analyzer.set_executor('device')
    self.assertTrue((analyzer._job / 'remote-cancelled.json').exists())
    self.assertIsNone(analyzer.execution_snapshot()['pending_job'])
    with self.assertRaisesRegex(ValueError, 'ticket_unavailable'):
      _ticket(analyzer._job)

  def test_executor_preference_is_separate_from_recording_and_rejects_unknown_mode(self):
    self.assertEqual(preference(self.root), 'device')
    self.assertEqual(preference(self.root, 'remote_pc'), 'remote_pc')
    self.assertEqual(preference(self.root), 'remote_pc')
    before = (self.root / 'analysis-executor.json').read_bytes()
    with self.assertRaises(ValueError):
      preference(self.root, 'automatic_fallback')
    self.assertEqual(before, (self.root / 'analysis-executor.json').read_bytes())

  def test_transfer_rejects_modified_extra_and_escaping_files(self):
    data = self.root / 'input.json'
    data.write_text('{}')
    _manifest(self.root, 'remote_analysis_input', {})
    _verify(self.root, 'remote_analysis_input')
    data.write_text('{"changed":true}')
    with self.assertRaisesRegex(ValueError, 'member_changed'):
      _verify(self.root, 'remote_analysis_input')
    data.write_text('{}')
    extra = self.root / 'extra.json'
    extra.write_text('{}')
    with self.assertRaisesRegex(ValueError, 'inventory_changed'):
      _verify(self.root, 'remote_analysis_input')
    value = json.loads((self.root / 'remote-manifest.json').read_text())
    value['files'][0]['path'] = '../outside'
    (self.root / 'remote-manifest.json').write_text(json.dumps(value))
    with self.assertRaisesRegex(ValueError, 'member_invalid'):
      _verify(self.root, 'remote_analysis_input')

  def test_remote_review_keeps_existing_store_but_does_not_capture_or_decode_comparison(self):
    from .test_manual_calibration_flow import ManualCalibrationFlowTests
    from openpilot.selfdrive.carrot.control_advisor.apply_requests import ApplyRequestController
    from openpilot.selfdrive.carrot.control_advisor.native_runtime import comparison_profile
    from openpilot.selfdrive.carrot.control_advisor.request_evidence import make_comparison_provider
    fixture = ManualCalibrationFlowTests()
    fixture.setUp()
    self.addCleanup(fixture.doCleanups)
    store, resolver, body, _, _ = fixture.fixture()
    execution = [None]
    provider = make_comparison_provider(resolver, comparison_profile(), execution_provider=lambda: execution[0])
    body['dependency_digest'] = resolver.candidate_context('trial-pc')['dependency_digest']
    actor = ApplyRequestController(fixture.root / 'remote-requests', resolver, pc_store=store, enabled=True,
      clock_ns=lambda: 2_000_000_000, comparison_provider=provider)
    self.addCleanup(actor.close)
    actor.register(body)
    self.assertEqual(actor.execute(body['request_id'])['state'], 'stored')
    with patch.object(resolver, 'capture_evidence', side_effect=AssertionError('comparison captured on comma')):
      unconfirmed = actor.review(body['request_id'])
      self.assertIn('analysis_executor_unconfirmed', unconfirmed['comparison']['reason_codes'])
      execution[0] = 'remote_pc'
      reviewed = actor.review(body['request_id'])
    self.assertEqual(reviewed['state'], 'stored')
    self.assertIn('comparison_on_remote_pc', reviewed['comparison']['reason_codes'])

  def test_empty_directory_links_and_input_output_overlap_are_rejected(self):
    from openpilot.selfdrive.carrot.control_advisor.remote_analysis import _disjoint
    (self.root / 'input.json').write_text('{}')
    _manifest(self.root, 'remote_analysis_input', {})
    link = self.root / 'empty-link'
    if __import__('os').name == 'nt':
      import subprocess
      outside = self.root.parent / (self.root.name + '-outside')
      outside.mkdir()
      self.addCleanup(outside.rmdir)
      subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], check=True, capture_output=True)
      self.addCleanup(link.rmdir)
    else:
      link.symlink_to(self.root.parent, target_is_directory=True)
    with self.assertRaisesRegex(ValueError, 'remote_path_invalid'):
      _verify(self.root, 'remote_analysis_input')
    with self.assertRaisesRegex(ValueError, 'remote_output_overlap'):
      _disjoint(self.root, self.root / 'child')

  def test_first_archive_waiting_does_not_spawn_or_discard_the_request(self):
    from openpilot.selfdrive.carrot.control_advisor.remote_analysis import export_input
    analyzer = PeriodicAnalyzer(self.root / 'session', self.root / 'profile.json', self.root / 'objectives.json', self.root / 'output')
    analyzer.set_executor('remote_pc')
    checkpoint = {'sequence': 8, 'session_id': 'waiting-fixture', 'cutoff_mono_ns': 2}
    identity = {'analysis_execution': {'executor': 'remote_pc', 'revision': analyzer.execution_snapshot()['revision']}, 'checkpoint': checkpoint}
    job_id = _hash(identity)
    analyzer._job = self.root / 'output/jobs' / (job_id + '-12345678')
    analyzer._job.mkdir(parents=True)
    for name in ('profile.json', 'objectives.json'):
      (self.root / name).write_text('{}')
    from openpilot.selfdrive.carrot.control_advisor.evidence import hash_file
    intent = {'schema_version': 1, 'request_id': 'wait-request', 'trigger': 'manual', 'session_id': 'waiting-fixture',
      'range_start_ns': 1, 'requested_end_ns': 2, 'profile_sha256': hash_file(self.root / 'profile.json', 1024)[0],
      'objectives_sha256': hash_file(self.root / 'objectives.json', 1024)[0]}
    _atomic(analyzer._job / 'request.json', {'job_id': job_id, 'identity': identity, 'session_directory': str(self.root / 'session'),
      'analysis_intent': intent})
    analyzer._active_intent = intent
    analyzer._initialized = True
    analyzer._start_child()
    def no_archive(*args, **kwargs):
      (analyzer._job / 'snapshot').mkdir()
      raise ValueError('snapshot_no_committed_archive')
    with patch('openpilot.selfdrive.carrot.control_advisor.journal_snapshot.create_journal_snapshot', side_effect=no_archive):
      self.assertEqual(export_input(analyzer._job, self.root / 'transfer')['state'], 'waiting_for_closed_archive')
    with patch('openpilot.selfdrive.carrot.control_advisor.periodic.subprocess.Popen', side_effect=AssertionError('waiting spawned')):
      self.assertEqual(analyzer.tick()['state'], 'waiting')
    self.assertEqual(analyzer._intent, intent)
    self.assertEqual(analyzer._waiting_sequence, 8)
    self.assertIsNone(analyzer.execution_snapshot()['pending_job'])
    self.assertFalse((analyzer._job / 'snapshot').exists())
    self.assertTrue(list(analyzer._job.glob('snapshot-uncommitted-*')))
    old_job = analyzer._job
    analyzer._pending = (checkpoint, None)
    self.assertEqual(analyzer.tick()['state'], 'waiting')
    self.assertIsNone(analyzer.execution_snapshot()['pending_job'])
    analyzer._pending = ({**checkpoint, 'sequence': 9}, None)
    with patch('openpilot.selfdrive.carrot.control_advisor.periodic.subprocess.Popen', side_effect=AssertionError('new checkpoint spawned locally')):
      current = analyzer.tick()
      self.assertEqual(current['state'], 'running', current)
    self.assertNotEqual(analyzer._job, old_job)
    self.assertIsNotNone(analyzer.execution_snapshot()['pending_job'])

  def test_changed_bytes_during_result_copy_cannot_reach_publication(self):
    from openpilot.selfdrive.carrot.control_advisor.remote_analysis import _copy_packet
    source = self.root / 'incoming'
    source.mkdir()
    payload = source / 'job-result.json'
    payload.write_text('{}')
    manifest = _manifest(source, 'remote_analysis_result', {})
    payload.write_text('[]')
    with self.assertRaisesRegex(ValueError, 'remote_member_changed'):
      _copy_packet(source, self.root / 'staging', manifest)


if __name__ == '__main__':
  unittest.main()
