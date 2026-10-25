"""Bounded offline candidate artifacts; never a live recommendation or writer."""
import hashlib
import json
from pathlib import Path
import re
import tempfile

from .evidence import create_bundle, hash_file, verify_bundle

MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_COLLECTION_BYTES = 32 * 1024 * 1024
MAX_LINK_BYTES = 256 * 1024
ENGINE_FILES = ('candidates.py', 'candidate_lateral.py', 'candidate_longitudinal.py',
                'candidate_report.py', 'parameters.json', 'torque_fit.py',
                'log_candidates.py', 'log_signals.py', 'log_history.py', 'rlog.py',
                'normalizer.py', 'analysis.py', 'settings_history.py',
                'journal_snapshot.py', 'journal_segments.py', 'inspection.py', 'periodic.py', 'vehicle_identity.py')
SOURCE_FILES = ('openpilot/selfdrive/carrot/t_follow.py',
                'openpilot/selfdrive/controls/lib/longitudinal_preview.py',
                'openpilot/selfdrive/controls/lib/steer_ratio.py',
                'openpilot/selfdrive/carrot_settings.json',
                'openpilot/selfdrive/carrot/traffic_stop.py',
                'openpilot/selfdrive/carrot/radar_motion/lane_change_gap.py')


def _code_hashes():
  from .consumer_observer import CONSUMER_PATHS
  package = Path(__file__).resolve().parent
  paths = {name: package / name for name in ENGINE_FILES}
  paths.update({name: package.parents[3] / name for name in {*SOURCE_FILES, *CONSUMER_PATHS.values()}})
  return {name: hash_file(path, MAX_INPUT_BYTES)[0] for name, path in sorted(paths.items())}


def _json(data):
  def reject(value):
    raise ValueError('invalid_candidate_json')
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError('duplicate_candidate_json_key')
      result[key] = value
    return result
  try:
    return json.loads(data, parse_constant=reject, object_pairs_hook=unique)
  except (UnicodeError, RecursionError) as exc:
    raise ValueError('invalid_candidate_json') from exc


def _read(path, limit):
  before = hash_file(path, limit)
  with Path(path).open('rb') as stream:
    value = stream.read(limit + 1)
  if len(value) > limit or hashlib.sha256(value).hexdigest() != before[0] or hash_file(path, limit) != before:
    raise ValueError('candidate_input_changed')
  return _json(value), before[0]


def _link_receipt(value):
  required = {'schema_version', 'epoch_id', 'source_bytes_verified_at_generation', 'schema_decoded',
              'session_link', 'semantic_mapping', 'originals_preservation', 'originals_current_availability',
              'sources', 'source_fields', 'objectives', 'objectives_sha256', 'mask_profile_sha256',
              'excluded_counts', 'reasons', 'input_sha256', 'profile_sha256'}
  if type(value) is not dict or set(value) != required or type(value['schema_version']) is not int or value['schema_version'] != 1:
    raise ValueError('invalid_log_link_receipt')
  if (value['source_bytes_verified_at_generation'] is not True or value['schema_decoded'] is not True
      or value['session_link'] not in ('verified_archive_receipt', 'unlinked')
      or value['semantic_mapping'] not in ('partial', 'per_signal_verified')
      or value['originals_preservation'] not in ('verified_session_archive', 'reference_only')
      or value['originals_current_availability'] != 'not_checked'
      or (value['originals_preservation'] == 'verified_session_archive' and value['session_link'] != 'verified_archive_receipt')):
    raise ValueError('invalid_log_link_claim')
  def identifier(item):
    return isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', item)
  def sha(item):
    return isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item)
  if not identifier(value['epoch_id']) or any(not sha(value[name]) for name in ('input_sha256', 'profile_sha256', 'objectives_sha256', 'mask_profile_sha256')):
    raise ValueError('invalid_log_link_identity')
  sources = value['sources']
  if type(sources) is not list or not 1 <= len(sources) <= 32:
    raise ValueError('log_link_source_limit')
  seen = set()
  for entry in sources:
    if (type(entry) is not dict or set(entry) != {'id', 'sha256', 'bytes'} or not identifier(entry['id'])
        or entry['id'] in seen or not sha(entry['sha256']) or type(entry['bytes']) is not int or not 1 <= entry['bytes'] <= 512 * 1024 * 1024):
      raise ValueError('invalid_log_link_source')
    seen.add(entry['id'])
  if sum(entry['bytes'] for entry in sources) > 512 * 1024 * 1024:
    raise ValueError('log_link_source_byte_limit')
  fields, excluded, reasons = value['source_fields'], value['excluded_counts'], value['reasons']
  if (type(fields) is not dict or len(fields) > 64 or any(not identifier(key) or not identifier(field) for key, field in fields.items())
      or type(excluded) is not dict or len(excluded) > 32 or any(not identifier(key) or type(count) is not int or not 0 <= count <= 10**9 for key, count in excluded.items())
      or type(reasons) is not list or len(reasons) > 32 or any(not identifier(reason) for reason in reasons)
      or type(value['objectives']) is not dict or len(json.dumps(value['objectives'], allow_nan=False).encode()) > 64 * 1024):
    raise ValueError('invalid_log_link_details')
  if len(json.dumps(value, allow_nan=False).encode()) > MAX_LINK_BYTES:
    raise ValueError('log_link_receipt_limit')
  return value


def calculate_bundle(input_path, profile_path, destination, *, log_link=None):
  from .candidates import evaluate_candidates, validate_candidate_report
  input_path, profile_path = Path(input_path), Path(profile_path)
  engine_hashes = _code_hashes()
  evidence, input_hash = _read(input_path, MAX_INPUT_BYTES)
  profile, profile_hash = _read(profile_path, MAX_INPUT_BYTES)
  report = validate_candidate_report(evaluate_candidates(evidence, profile))
  if engine_hashes != _code_hashes():
    raise ValueError('candidate_engine_changed')
  result = {'schema_version': 1, 'scope': 'offline_candidate_calculation', 'report': report,
            'input_sha256': input_hash, 'profile_sha256': profile_hash,
            'analyzer_sha256': engine_hashes, 'recommendations_enabled': False,
            'apply_enabled': False, 'device_qualified': False, 'raw_sources_verified': False}
  if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_RESULT_BYTES // 2:
    raise ValueError('candidate_result_limit')
  with tempfile.TemporaryDirectory(prefix='fms-candidate-link-') as temporary:
    sources, hashes = [input_path, profile_path], [input_hash, profile_hash]
    if log_link is not None:
      receipt = _link_receipt({**log_link, 'input_sha256': input_hash, 'profile_sha256': profile_hash})
      if receipt['epoch_id'] != report['provenance']['epoch_id']:
        raise ValueError('log_link_epoch_mismatch')
      path = Path(temporary) / 'log-extraction.json'
      path.write_text(json.dumps(receipt, ensure_ascii=False, allow_nan=False), encoding='utf-8')
      result['log_link_sha256'] = hash_file(path, MAX_LINK_BYTES)[0]
      sources.append(path)
      hashes.append(result['log_link_sha256'])
    create_bundle(Path(destination), sources, result, byte_limit=MAX_BUNDLE_BYTES, expected_sha256=hashes)
  loaded = load_candidate_bundle(destination)
  return {'state': 'complete', 'scope': result['scope'], 'output': str(destination),
          'parameter_count': len(loaded['report']['items']), 'apply_enabled': False, 'device_qualified': False}


def _load_single_bundle(directory, byte_limit=MAX_BUNDLE_BYTES):
  from .candidates import validate_candidate_report
  directory = Path(directory)
  manifest = verify_bundle(directory, byte_limit=min(byte_limit, MAX_BUNDLE_BYTES), max_files=3)
  if len(manifest['files']) not in (2, 3):
    raise ValueError('candidate_sources_required')
  result, digest = _read(directory / 'result.json', MAX_RESULT_BYTES)
  if (not isinstance(result, dict) or result.get('schema_version') != 1
      or result.get('scope') != 'offline_candidate_calculation'
      or any(result.get(key) is not False for key in ('apply_enabled', 'recommendations_enabled', 'device_qualified', 'raw_sources_verified'))
      or digest != manifest['result']['sha256']
      or [result.get('input_sha256'), result.get('profile_sha256')] != [entry['sha256'] for entry in manifest['files'][:2]]):
    raise ValueError('invalid_candidate_bundle')
  report = validate_candidate_report(result.get('report'))
  input_epoch = None
  for entry, field in zip(manifest['files'][:2], ('evidence_sha256', 'profile_sha256')):
    source, source_hash = _read(directory / entry['path'], MAX_INPUT_BYTES)
    if type(source) is not dict:
      raise ValueError('invalid_candidate_source_object')
    canonical = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if source_hash != entry['sha256'] or report['provenance'][field] != canonical:
      raise ValueError('candidate_report_source_binding_mismatch')
    if field == 'evidence_sha256':
      input_epoch = source.get('epoch')
  if (type(input_epoch) is not dict or input_epoch.get('id') != report['provenance']['epoch_id']
      or type(input_epoch.get('start_ns')) is not int or type(input_epoch.get('end_ns')) is not int
      or not 0 <= input_epoch['start_ns'] <= input_epoch['end_ns']):
    raise ValueError('invalid_candidate_source_epoch')
  envelope = {'state': 'ready', 'reason': None, 'report': report, 'artifact_sha256': digest,
              'integrity_verified': True, 'raw_sources_verified': False, 'device_qualified': False, 'apply_enabled': False}
  receipt = None
  if len(manifest['files']) == 3:
    entry = manifest['files'][2]
    receipt, link_hash = _read(directory / entry['path'], MAX_LINK_BYTES)
    _link_receipt(receipt)
    if (link_hash != entry['sha256'] or link_hash != result.get('log_link_sha256')
        or receipt['epoch_id'] != report['provenance']['epoch_id']
        or any(receipt[key] != result[key] for key in ('input_sha256', 'profile_sha256'))):
      raise ValueError('log_link_binding_mismatch')
    envelope['log_link'] = {key: receipt[key] for key in ('source_bytes_verified_at_generation', 'schema_decoded',
      'session_link', 'semantic_mapping', 'originals_preservation', 'originals_current_availability', 'excluded_counts', 'reasons')}
    envelope['log_link'].update(source_count=len(receipt['sources']), epoch_count=1)
  elif 'log_link_sha256' in result:
    raise ValueError('log_link_receipt_missing')
  total = manifest['result']['bytes'] + sum(entry['bytes'] for entry in manifest['files'])
  return envelope, input_epoch, receipt, total


def _load_reports(directory):
  directory = Path(directory)
  if not (directory / 'index.json').exists():
    return [_load_single_bundle(directory)]
  index, _ = _read(directory / 'index.json', MAX_LINK_BYTES)
  if (type(index) is not dict or set(index) != {'schema_version', 'scope', 'epochs', 'latest', 'apply_enabled', 'device_qualified'}
      or type(index['schema_version']) is not int or index['schema_version'] != 1 or index['scope'] != 'log_candidate_collection'
      or index['apply_enabled'] is not False or index['device_qualified'] is not False
      or type(index['epochs']) is not list or not 1 <= len(index['epochs']) <= 32):
    raise ValueError('invalid_log_candidate_index')
  loaded, names, epoch_ids, remaining, previous_end = [], set(), set(), MAX_COLLECTION_BYTES, -1
  for entry in index['epochs']:
    if (type(entry) is not dict or set(entry) != {'epoch_id', 'start_ns', 'end_ns', 'bundle', 'status'}
        or type(entry['bundle']) is not str or not re.fullmatch(r'epoch-[0-9]{4}', entry['bundle'])
        or entry['bundle'] in names or entry['status'] != 'complete'
        or type(entry['epoch_id']) is not str or entry['epoch_id'] in epoch_ids
        or type(entry['start_ns']) is not int or type(entry['end_ns']) is not int
        or not previous_end < entry['start_ns'] <= entry['end_ns']):
      raise ValueError('invalid_log_candidate_epoch')
    item = _load_single_bundle(directory / entry['bundle'], max(1, remaining))
    envelope, epoch, receipt, size = item
    if receipt is None or any(epoch.get(key) != entry[field] for key, field in (('id', 'epoch_id'), ('start_ns', 'start_ns'), ('end_ns', 'end_ns'))):
      raise ValueError('log_candidate_epoch_binding_mismatch')
    remaining -= size
    if remaining < 0:
      raise ValueError('log_candidate_collection_limit')
    loaded.append(item)
    names.add(entry['bundle'])
    epoch_ids.add(entry['epoch_id'])
    previous_end = entry['end_ns']
  if index['latest'] != index['epochs'][-1]['bundle']:
    raise ValueError('log_candidate_latest_mismatch')
  return loaded


def load_candidate_bundle(directory):
  loaded = _load_reports(directory)
  envelope = loaded[-1][0]
  if 'log_link' in envelope:
    envelope['log_link']['epoch_count'] = len(loaded)
  return envelope


def verify_log_sources(directory, paths):
  """Recheck explicitly supplied originals; never follow a receipt's file paths."""
  reports = _load_reports(directory)
  if not paths or len(paths) > 32 or any(item[2] is None for item in reports):
    raise ValueError('log_source_references_required')
  expected = {(source['sha256'], source['bytes']) for item in reports for source in item[2]['sources']}
  if sum(size for _, size in expected) > 512 * 1024 * 1024 or any(Path(path).stat().st_size not in {size for _, size in expected} for path in paths):
    raise ValueError('log_source_hash_mismatch')
  actual = [hash_file(Path(path), 512 * 1024 * 1024) for path in paths]
  if len(actual) != len(set(actual)) or set(actual) != expected:
    raise ValueError('log_source_hash_mismatch')
  return {'state': 'verified', 'source_count': len(actual), 'scope': 'original_bytes_only',
          'device_qualified': False, 'apply_enabled': False}
