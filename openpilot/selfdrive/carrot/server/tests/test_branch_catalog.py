import subprocess

import pytest
from openpilot.selfdrive.carrot.server.services import branch_catalog as catalog


def test_listing_only_remote_names_and_current_local(monkeypatch):
  calls = []
  def git(repo, *args, **kwargs):
    calls.append(args)
    if args == ('branch', '--show-current'):
      return 0, 'fms-carrot-wip'
    if args[:3] == ('remote', 'get-url', 'origin'):
      return 0, catalog.UPSTREAM_URL
    if args[:2] == ('ls-remote', '--heads'):
      return 0, 'a' * 40 + '\trefs/heads/topic/slash\nmalformed\n'
    raise AssertionError(args)
  monkeypatch.setattr(catalog, '_git', git)
  result = catalog.list_branches('repo')
  assert result['ok']
  assert [i['ref'] for i in result['branch_items']] == ['fms-carrot-wip', 'origin/topic/slash', 'fullmetalsonic/topic/slash']
  assert not any(c[0] in ('fetch', 'config') for c in calls)


def test_partial_failure_and_origin_fork(monkeypatch):
  def git(repo, *args, **kwargs):
    if args[0] == 'branch': return 0, 'fms-carrot'
    if args[0] == 'remote': return 0, catalog.FORK_URL
    if args[-1] == catalog.UPSTREAM_URL: return 1, 'timeout'
    return 0, 'b' * 40 + '\trefs/heads/fms-carrot'
  monkeypatch.setattr(catalog, '_git', git)
  result = catalog.list_branches('repo')
  assert result['ok'] and result['remote_errors'] == {'ajouatom': 'timeout'}
  assert result['remotes'] == ['ajouatom', 'fullmetalsonic']
  assert len(result['branch_items']) == 2


@pytest.mark.parametrize('body', [
  {'kind': 'remote', 'remote': '--upload-pack=bad', 'name': 'main'},
  {'kind': 'remote', 'remote': 'fullmetalsonic', 'name': '../bad'},
  {'kind': 'local', 'name': '-bad'},
  {'kind': 'unexpected', 'name': 'main'},
])
def test_invalid_selection_no_fetch(monkeypatch, body):
  calls = []
  def git(repo, *args, **kwargs):
    calls.append(args)
    if args[0] == 'remote': return 0, catalog.UPSTREAM_URL
    if args[0] == 'check-ref-format': return 1, 'invalid'
    raise AssertionError(args)
  monkeypatch.setattr(catalog, '_git', git)
  assert not catalog.checkout_branch('repo', body)['ok']
  assert not any(c[0] == 'fetch' for c in calls)


def test_local_switch_does_not_fetch(monkeypatch):
  calls = []
  def git(repo, *args, **kwargs):
    calls.append(args)
    return 0, catalog.UPSTREAM_URL if args[0] == 'remote' else ''
  monkeypatch.setattr(catalog, '_git', git)
  assert catalog.checkout_branch('repo', {'kind': 'local', 'name': 'installed'})['ok']
  assert ('switch', '--', 'installed') in calls
  assert not any(c[0] == 'fetch' for c in calls)


def test_remote_fetch_only_selected_branch(monkeypatch):
  calls = []
  def git(repo, *args, **kwargs):
    calls.append(args)
    if args[:3] == ('remote', 'get-url', 'origin'): return 0, catalog.UPSTREAM_URL
    if args[:3] == ('remote', 'get-url', 'fullmetalsonic'): return 0, catalog.FORK_URL
    if args[0] == 'show-ref': return 1, ''
    return 0, ''
  monkeypatch.setattr(catalog, '_git', git)
  assert catalog.checkout_branch('repo', {'kind': 'remote', 'remote': 'fullmetalsonic', 'name': 'fms-carrot-paddle'})['ok']
  fetch = [c for c in calls if c[0] == 'fetch']
  assert fetch == [('fetch', '--no-tags', '--no-recurse-submodules', 'fullmetalsonic', '+refs/heads/fms-carrot-paddle:refs/remotes/fullmetalsonic/fms-carrot-paddle')]


def test_existing_local_collision_rejected(monkeypatch):
  def git(repo, *args, **kwargs):
    if args[0] == 'remote': return 0, catalog.UPSTREAM_URL if args[-1] == 'origin' else catalog.FORK_URL
    if args[0] == 'for-each-ref': return 0, 'origin/shared'
    if args[0] == 'switch': raise AssertionError('must not switch wrong repository')
    return 0, ''
  monkeypatch.setattr(catalog, '_git', git)
  assert not catalog.checkout_branch('repo', {'kind': 'remote', 'remote': 'fullmetalsonic', 'name': 'shared'})['ok']


def test_sync_prunes_metadata_without_fetch(monkeypatch):
  calls = []
  def git(repo, *args, **kwargs):
    calls.append(args)
    if args == ('branch', '--show-current'): return 0, 'installed'
    if args[0] == 'for-each-ref': return 0, 'installed\nold'
    if args[:2] == ('remote', 'get-url'): return 0, catalog.UPSTREAM_URL if args[-1] == 'origin' else catalog.FORK_URL
    return 0, ''
  monkeypatch.setattr(catalog, '_git', git)
  assert catalog.sync_branches('repo')['ok']
  assert ('branch', '-D', '--', 'old') in calls
  assert ('remote', 'prune', 'origin') in calls
  assert not any(c[0] in ('fetch', 'reset', 'clean') for c in calls)


def test_git_timeout_is_a_result(monkeypatch):
  monkeypatch.setattr(subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired('git', 30)))
  assert catalog._git('repo', 'ls-remote')[0] == 1


@pytest.mark.parametrize("url", ["git@github.com:fullmetalsonic/openpilot.git", "ssh://git@github.com/fullmetalsonic/openpilot.git", catalog.FORK_URL])
def test_github_url_identity(url):
  assert catalog._same_url(url, catalog.FORK_URL)


def test_sync_detached_head_refuses_cleanup(monkeypatch):
  calls = []
  def git(repo, *args, **kwargs):
    calls.append(args)
    return 0, ''
  monkeypatch.setattr(catalog, '_git', git)
  assert not catalog.sync_branches('repo')['ok']
  assert calls == [('branch', '--show-current')]


def test_dispatcher_listing_bypasses_mutation_guard():
  import ast
  from pathlib import Path
  source = (Path(__file__).parents[1] / "features/tools/dispatcher.py").read_text(encoding="utf-8")
  tree = ast.parse(source)
  node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_needs_repo_lock")
  namespace = {}
  exec(compile(ast.Module(body=[node], type_ignores=[]), "guard", "exec"), namespace)
  guard = namespace['_needs_repo_lock']
  assert not guard('git_branch_list', {})
  assert guard('git_checkout', {})
  assert guard('git_sync', {})
  # Both routes use the shared service instead of alternate full-download flows.
  for service in ('list_branches', 'checkout_branch', 'sync_branches'):
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == service]
    assert len(calls) == 2


def test_real_git_catalog_and_selective_checkout(tmp_path, monkeypatch):
  def git(directory, *args, check=True):
    proc = subprocess.run(['git', *args], cwd=directory, text=True, capture_output=True)
    if check:
      assert proc.returncode == 0, proc.stderr
    return proc

  seed = tmp_path / 'seed'
  seed.mkdir()
  git(seed, 'init', '-b', 'installed')
  git(seed, 'config', 'user.email', 'fixture@example.invalid')
  git(seed, 'config', 'user.name', 'Fixture')
  (seed / 'content.txt').write_text('installed')
  git(seed, 'add', 'content.txt')
  git(seed, 'commit', '-m', 'installed')
  for branch in ('selected', 'unselected'):
    git(seed, 'switch', '-c', branch, 'installed')
    (seed / 'content.txt').write_text(branch)
    git(seed, 'commit', '-am', branch)
  unselected_blob = git(seed, 'rev-parse', 'unselected:content.txt').stdout.strip()
  fork = tmp_path / 'fork.git'
  upstream = tmp_path / 'upstream.git'
  git(tmp_path, 'clone', '--bare', str(seed), str(fork))
  git(tmp_path, 'clone', '--bare', str(seed), str(upstream))
  installed = tmp_path / 'device'
  git(tmp_path, 'clone', '--no-local', '--single-branch', '--branch', 'installed', str(fork), str(installed))
  monkeypatch.setattr(catalog, 'UPSTREAM_URL', str(upstream))
  monkeypatch.setattr(catalog, 'FORK_URL', str(fork))

  def snapshot():
    config = (installed / '.git/config').read_bytes()
    refs = git(installed, 'show-ref').stdout
    objects = git(installed, 'count-objects', '-v').stdout
    return config, refs, objects

  before = snapshot()
  result = catalog.list_branches(str(installed))
  assert result['ok'] and not result['remote_errors']
  assert snapshot() == before
  assert [item['name'] for item in result['branch_items'] if item['kind'] == 'local'] == ['installed']
  assert result['remotes'] == ['ajouatom', 'fullmetalsonic']
  assert git(installed, 'cat-file', '-e', unselected_blob, check=False).returncode != 0

  selected = catalog.checkout_branch(str(installed), {'kind': 'remote', 'remote': 'fullmetalsonic', 'name': 'selected'})
  assert selected['ok'], selected
  assert (installed / 'content.txt').read_text() == 'selected'
  assert git(installed, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}').stdout.strip() == 'fullmetalsonic/selected'
  assert git(installed, 'show-ref', '--verify', 'refs/remotes/fullmetalsonic/unselected', check=False).returncode != 0
  assert git(installed, 'cat-file', '-e', unselected_blob, check=False).returncode != 0
  # The already installed branch tracks origin, whose URL is the same fork.
  existing = catalog.checkout_branch(str(installed), {'kind': 'remote', 'remote': 'fullmetalsonic', 'name': 'installed'})
  assert existing['ok'], existing
  assert (installed / 'content.txt').read_text() == 'installed'
  assert git(installed, 'remote', 'get-url', 'origin').stdout.strip() == str(fork)
