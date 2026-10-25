"""Real numerical CLI -> immutable artifacts -> HTTP, without vehicle imports."""
import builtins
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from openpilot.selfdrive.carrot.control_advisor.candidate_report import calculate_bundle, load_candidate_bundle, verify_log_sources, _read
from openpilot.selfdrive.carrot.control_advisor.evidence import create_bundle, hash_file
from openpilot.selfdrive.carrot.control_advisor.tests.test_candidates import candidate_fixture


ROOT = Path(__file__).resolve().parents[5]


def fixture_files(root):
  evidence, profile = candidate_fixture()
  inputs = root / 'input.json', root / 'profile.json'
  for path, value in zip(inputs, (evidence, profile)):
    path.write_text(json.dumps(value), encoding='utf-8')
  return inputs


class CandidateArtifactTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.root = Path(self.directory.name)
    self.inputs = fixture_files(self.root)
    self.destination = self.root / 'result'

  def test_actual_cli_computes_quantized_candidate_and_preserves_inputs(self):
    before = [path.read_bytes() for path in self.inputs]
    command = [sys.executable, '-m', 'openpilot.selfdrive.carrot.control_advisor', 'evaluate-candidates',
               str(self.inputs[0]), '--profile', str(self.inputs[1]), '--output', str(self.destination)]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)
    self.assertEqual(0, done.returncode, done.stderr)
    self.assertEqual(47, json.loads(done.stdout)['parameter_count'])
    report = load_candidate_bundle(self.destination)
    item = next(row for row in report['report']['items'] if row['key'] == 'PathOffset')
    self.assertEqual('search_candidate', item['status'])
    self.assertEqual((20, .2), (item['candidate_raw'], item['candidate_physical']))
    self.assertTrue(item['validation']['holdout_objective_pass'])
    self.assertFalse(report['device_qualified'])
    self.assertFalse(report['raw_sources_verified'])
    self.assertFalse(report['apply_enabled'])
    recorded = json.loads((self.destination / 'result.json').read_text())['analyzer_sha256']
    for dependency in ('openpilot/selfdrive/carrot/t_follow.py',
                       'openpilot/selfdrive/controls/lib/longitudinal_preview.py',
                       'openpilot/selfdrive/carrot/traffic_stop.py',
                       'openpilot/selfdrive/carrot/radar_motion/lane_change_gap.py'):
      self.assertEqual(hash_file(ROOT / dependency, 4 * 1024 * 1024)[0], recorded[dependency])
    self.assertEqual(before, [path.read_bytes() for path in self.inputs])
    saved = (self.destination / 'result.json').read_bytes()
    again = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)
    self.assertNotEqual(0, again.returncode)
    self.assertEqual(saved, (self.destination / 'result.json').read_bytes())

  def test_no_params_or_native_control_import_and_tampered_bundle_rejected(self):
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
      if name in ('openpilot.common.params', 'openpilot.system.hardware', 'openpilot.cereal.messaging'):
        raise AssertionError('vehicle import: ' + name)
      return original_import(name, *args, **kwargs)
    with patch('builtins.__import__', side_effect=guarded):
      calculate_bundle(*self.inputs, self.destination)
    result = self.destination / 'result.json'
    body = result.read_bytes()
    result.write_bytes(body + b' ')
    with self.assertRaisesRegex(ValueError, 'evidence_hash_mismatch|evidence_byte_limit'):
      load_candidate_bundle(self.destination)

  def test_parsed_bytes_must_match_the_bound_hash_even_if_file_changed_back(self):
    original = self.inputs[0].read_bytes()
    expected = (hashlib.sha256(original).hexdigest(), len(original))
    self.inputs[0].write_text('{}')
    with patch('openpilot.selfdrive.carrot.control_advisor.candidate_report.hash_file', return_value=expected):
      with self.assertRaisesRegex(ValueError, 'candidate_input_changed'):
        _read(self.inputs[0], 1024 * 1024)

  def test_individually_valid_files_cannot_mix_a_report_with_another_profile(self):
    calculate_bundle(*self.inputs, self.destination)
    result = json.loads((self.destination / 'result.json').read_text())
    profile = json.loads(self.inputs[1].read_text())
    profile['profile_id'] = 'different-profile'
    self.inputs[1].write_text(json.dumps(profile))
    result['profile_sha256'] = hash_file(self.inputs[1], 1024 * 1024)[0]
    mixed = self.root / 'mixed'
    create_bundle(mixed, list(self.inputs), result, byte_limit=8 * 1024 * 1024)
    with self.assertRaisesRegex(ValueError, 'candidate_report_source_binding_mismatch'):
      load_candidate_bundle(mixed)

  def test_extraction_receipt_binds_inputs_without_exposing_private_references(self):
    evidence = json.loads(self.inputs[0].read_text())
    original = self.root / 'private-original.bin'
    original.write_bytes(b'byte identity test only, not a decodable rlog')
    sha, size = hash_file(original, 1024)
    link = {'schema_version': 1, 'epoch_id': evidence['epoch']['id'],
            'source_bytes_verified_at_generation': True, 'schema_decoded': True,
            'session_link': 'unlinked', 'semantic_mapping': 'partial',
            'originals_preservation': 'reference_only', 'originals_current_availability': 'not_checked',
            'sources': [{'id': 'input-0', 'sha256': sha, 'bytes': size}], 'source_fields': {'v_ego': 'carState.vEgo'},
            'objectives': {'note': 'receipt binding fixture'}, 'objectives_sha256': 'a' * 64,
            'mask_profile_sha256': 'b' * 64, 'excluded_counts': {'driver_intervention': 2}, 'reasons': ['session_unlinked']}
    calculate_bundle(*self.inputs, self.destination, log_link=link)
    loaded = load_candidate_bundle(self.destination)
    self.assertTrue(loaded['log_link']['source_bytes_verified_at_generation'])
    self.assertFalse(loaded['raw_sources_verified'])
    self.assertEqual(1, loaded['log_link']['source_count'])
    self.assertNotIn(sha, json.dumps(loaded['log_link']))
    self.assertNotIn('objectives', loaded['log_link'])
    self.assertEqual('verified', verify_log_sources(self.destination, [original])['state'])
    original.write_bytes(b'changed')
    with self.assertRaisesRegex(ValueError, 'log_source_hash_mismatch'):
      verify_log_sources(self.destination, [original])
    manifest = json.loads((self.destination / 'bundle.json').read_text())
    (self.destination / manifest['files'][2]['path']).write_text('{}')
    with self.assertRaisesRegex(ValueError, 'evidence_hash_mismatch'):
      load_candidate_bundle(self.destination)

  def test_collection_selects_latest_blocked_epoch_and_rejects_index_relabeling(self):
    base = json.loads(self.inputs[0].read_text())
    self.destination.mkdir()
    entries = []
    for index in range(2):
      evidence = copy.deepcopy(base)
      offset = index * 10000
      evidence['epoch']['id'] = f'epoch-{index}'
      for key in ('start_ns', 'end_ns'):
        evidence['epoch'][key] += offset
      for block in evidence['blocks']:
        for key in ('start_ns', 'end_ns', 'finalized_through_ns'):
          block[key] += offset
        for row in block['samples']:
          row['mono_ns'] += offset
          for signal in row['signals'].values():
            signal['source_mono_ns'] += offset
      if index:
        evidence['objectives'] = {}
      self.inputs[0].write_text(json.dumps(evidence))
      link = {'schema_version': 1, 'epoch_id': evidence['epoch']['id'],
              'source_bytes_verified_at_generation': True, 'schema_decoded': True,
              'session_link': 'unlinked', 'semantic_mapping': 'partial', 'originals_preservation': 'reference_only',
              'originals_current_availability': 'not_checked', 'sources': [{'id': 'source-0', 'sha256': 'a' * 64, 'bytes': 1}],
              'source_fields': {}, 'objectives': {}, 'objectives_sha256': 'b' * 64, 'mask_profile_sha256': 'c' * 64,
              'excluded_counts': {}, 'reasons': ['session_unlinked']}
      name = f'epoch-{index:04d}'
      calculate_bundle(*self.inputs, self.destination / name, log_link=link)
      entries.append({'epoch_id': evidence['epoch']['id'], 'start_ns': evidence['epoch']['start_ns'],
                      'end_ns': evidence['epoch']['end_ns'], 'bundle': name, 'status': 'complete'})
    index = {'schema_version': 1, 'scope': 'log_candidate_collection', 'epochs': entries,
             'latest': entries[-1]['bundle'], 'apply_enabled': False, 'device_qualified': False}
    path = self.destination / 'index.json'
    path.write_text(json.dumps(index))
    report = load_candidate_bundle(self.destination)
    self.assertEqual('epoch-1', report['report']['provenance']['epoch_id'])
    self.assertTrue(all(row['candidate_raw'] is None for row in report['report']['items']))
    self.assertEqual(2, report['log_link']['epoch_count'])
    index['latest'] = entries[0]['bundle']
    path.write_text(json.dumps(index))
    with self.assertRaisesRegex(ValueError, 'log_candidate_latest_mismatch'):
      load_candidate_bundle(self.destination)
    index['latest'] = entries[-1]['bundle']
    entries[-1]['end_ns'] += 1
    path.write_text(json.dumps(index))
    with self.assertRaisesRegex(ValueError, 'log_candidate_epoch_binding_mismatch'):
      load_candidate_bundle(self.destination)

  def test_malformed_bound_source_is_a_report_error_not_an_uncaught_server_exception(self):
    calculate_bundle(*self.inputs, self.destination)
    result = json.loads((self.destination / 'result.json').read_text())
    self.inputs[0].write_text('[]')
    result['input_sha256'] = hash_file(self.inputs[0], 1024)[0]
    result['report']['provenance']['evidence_sha256'] = hashlib.sha256(b'[]').hexdigest()
    mixed = self.root / 'malformed'
    create_bundle(mixed, list(self.inputs), result, byte_limit=8 * 1024 * 1024)
    with self.assertRaisesRegex(ValueError, 'invalid_candidate_source_object'):
      load_candidate_bundle(mixed)


class CandidateHttpTests(unittest.IsolatedAsyncioTestCase):
  async def test_verified_offline_report_is_separate_from_live_session_and_invalid_file_is_isolated(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    root = Path(directory.name)
    inputs = fixture_files(root)
    calculate_bundle(*inputs, root / 'report')
    path = ROOT / 'openpilot/selfdrive/carrot/server/features/control_advisor.py'
    spec = importlib.util.spec_from_file_location('candidate_report_http', path)
    feature = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(feature)
    for selected, state in ((root / 'report', 'ready'), (root / 'missing', 'invalid')):
      app = web.Application()
      feature.register(app, report_directory=str(selected), token='', live_enabled=False, synthetic_enabled=False)
      client = TestClient(TestServer(app))
      await client.start_server()
      try:
        response = await client.get('/api/control_advisor/status')
        self.assertEqual(200, response.status)
        body = await response.json()
        self.assertEqual(state, body['candidate_report']['state'])
        self.assertEqual('idle', body['session']['state'])
        self.assertFalse(body['apply_enabled'])
        self.assertFalse(body['recommendations_enabled'])
        self.assertEqual(47, len(body['parameters']))
        self.assertTrue(all('candidate_calculation' in item for item in body['parameters']))
        if state == 'ready':
          report = body['candidate_report']['report']
          self.assertEqual('synthetic', report['source_kind'])
          self.assertEqual(20, next(item for item in report['items'] if item['key'] == 'PathOffset')['candidate_raw'])
          self.assertTrue(all(item['current_raw'] is None for item in body['parameters']))
        else:
          self.assertIsNone(body['candidate_report']['report'])
          self.assertNotIn(str(root), json.dumps(body['candidate_report']))
      finally:
        await client.close()


if __name__ == '__main__':
  unittest.main()
