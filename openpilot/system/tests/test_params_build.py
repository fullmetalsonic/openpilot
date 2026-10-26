import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
  sys.platform.startswith('win') or shutil.which('bash') is None or shutil.which('flock') is None or shutil.which('sha256sum') is None,
  reason="POSIX shell environment with bash, flock, and sha256sum required"
)

ROOT = Path(__file__).resolve().parents[3]

PARAMS_INITIAL_PY = """
class Params:
  def __init__(self, path=None):
    if not path:
      raise ValueError("Production default constructor not allowed")
    self.path = path

  def all_keys(self):
    return [b"TestKey"]
"""

PARAMS_VALID_PY = """
class Params:
  def __init__(self, path=None):
    if not path:
      raise ValueError("Production default constructor not allowed")
    self.path = path

  def all_keys(self):
    return [b"TestKey"]

  def get_raw(self, key):
    raise AssertionError("raw API get_raw must not be called")

  def get_many_raw(self, keys):
    raise AssertionError("raw API get_many_raw must not be called")

  def compare_and_set_raw(self, key, value, expected):
    raise AssertionError("raw API compare_and_set_raw must not be called")
"""


def test_ensure_params_build_lifecycle(tmp_path):
  repo = tmp_path / 'repo'
  scripts_dir = repo / 'scripts'
  scripts_dir.mkdir(parents=True)
  common_dir = repo / 'openpilot' / 'common'
  common_dir.mkdir(parents=True)
  manager_dir = repo / 'openpilot' / 'system' / 'manager'
  manager_dir.mkdir(parents=True)

  (repo / 'openpilot' / '__init__.py').touch()
  (repo / 'openpilot' / 'common' / '__init__.py').touch()
  (repo / 'openpilot' / 'system' / '__init__.py').touch()
  (repo / 'openpilot' / 'system' / 'manager' / '__init__.py').touch()

  helper_source = (ROOT / 'scripts/ensure_params_build.sh').read_text(encoding='utf-8')
  helper_path = scripts_dir / 'ensure_params_build.sh'
  helper_path.write_text(helper_source, encoding='utf-8')
  helper_path.chmod(0o755)

  check_source = (ROOT / 'openpilot/system/manager/params_check.py').read_text(encoding='utf-8')
  (manager_dir / 'params_check.py').write_text(check_source, encoding='utf-8')

  pyx_path = common_dir / 'params_pyx.pyx'
  pyx_path.write_text('# pyx source\n', encoding='utf-8')
  cc_path = common_dir / 'params.cc'
  cc_path.write_text('// cc source\n', encoding='utf-8')
  h_path = common_dir / 'params.h'
  h_path.write_text('// h source\n', encoding='utf-8')
  keys_h_path = common_dir / 'params_keys.h'
  keys_h_path.write_text('inline static std::unordered_map<std::string, ParamKeyAttributes> keys = {\n  {"TestKey", {PERSISTENT, BOOL}},\n};\n', encoding='utf-8')

  so_path = common_dir / 'params_pyx.so'
  so_path.write_text('placeholder .so\n', encoding='utf-8')

  params_py_path = common_dir / 'params.py'
  params_py_path.write_text(PARAMS_INITIAL_PY, encoding='utf-8')

  bin_dir = tmp_path / 'bin'
  bin_dir.mkdir()
  build_log = tmp_path / 'build.log'
  scons_mode = tmp_path / 'scons_mode'
  scons_mode.write_text('success', encoding='utf-8')

  fake_scons = bin_dir / 'scons'
  fake_scons_code = f"""#!/usr/bin/env python3
import os, sys
from pathlib import Path

lock_fd = os.environ.get("SCONS_CACHE_LOCK_FD")
if not lock_fd:
  sys.stderr.write("SCONS_CACHE_LOCK_FD missing\\n")
  sys.exit(2)
try:
  os.fstat(int(lock_fd))
except Exception as err:
  sys.stderr.write(f"Invalid lock fd: {{err}}\\n")
  sys.exit(3)

log_path = Path(os.environ["BUILD_LOG"])
with open(log_path, "a") as f:
  f.write("build\\n")

mode = Path(os.environ["SCONS_MODE"]).read_text().strip()
if mode == "fail":
  sys.exit(1)

repo = Path(os.environ["REPO_ROOT"])
params_target = repo / "openpilot/common/params.py"
if mode == "bad_postcheck":
  params_target.write_text({repr(PARAMS_INITIAL_PY)}, encoding="utf-8")
else:
  params_target.write_text({repr(PARAMS_VALID_PY)}, encoding="utf-8")
(repo / "openpilot/common/params_pyx.so").write_text("updated .so\\n", encoding="utf-8")
"""
  fake_scons.write_text(fake_scons_code, encoding='utf-8')
  fake_scons.chmod(0o755)

  cache_dir = tmp_path / 'cache'
  cache_dir.mkdir()
  stamp_path = cache_dir / 'carrot_params_keys.sha256'

  test_env = dict(
    os.environ,
    PATH=f"{bin_dir}:{os.environ['PATH']}",
    SCONS_CACHE_DIR=str(cache_dir),
    REPO_ROOT=str(repo),
    BUILD_LOG=str(build_log),
    SCONS_MODE=str(scons_mode),
    PYTHONDONTWRITEBYTECODE='1',
  )

  def run_helper():
    return subprocess.run(
      ['bash', str(helper_path)],
      cwd=str(repo),
      env=test_env,
      capture_output=True,
      text=True,
      timeout=15,
    )

  res = run_helper()
  assert res.returncode == 0, res.stderr
  assert build_log.read_text().splitlines() == ['build']
  assert stamp_path.is_file()
  stamp_val = stamp_path.read_text().strip()
  assert len(stamp_val) == 64
  assert all(c in '0123456789abcdef' for c in stamp_val)

  res = run_helper()
  assert res.returncode == 0, res.stderr
  assert len(build_log.read_text().splitlines()) == 1
  assert stamp_path.read_text().strip() == stamp_val

  for input_path, extra_content in [
    (cc_path, '// cc edit\n'),
    (pyx_path, '# pyx edit\n'),
    (h_path, '// h edit\n'),
    (keys_h_path, '/* keys comment */\n'),
  ]:
    input_path.write_text(input_path.read_text() + extra_content, encoding='utf-8')
    prev_build_count = len(build_log.read_text().splitlines())
    res = run_helper()
    assert res.returncode == 0, res.stderr
    assert len(build_log.read_text().splitlines()) == prev_build_count + 1
    new_stamp = stamp_path.read_text().strip()
    assert len(new_stamp) == 64
    assert new_stamp != stamp_val
    stamp_val = new_stamp

  params_py_path.write_text(PARAMS_INITIAL_PY, encoding='utf-8')
  prev_build_count = len(build_log.read_text().splitlines())
  res = run_helper()
  assert res.returncode == 0, res.stderr
  assert len(build_log.read_text().splitlines()) == prev_build_count + 1
  assert stamp_path.read_text().strip() == stamp_val

  last_stamp = stamp_path.read_text()
  last_so = so_path.read_text()
  scons_mode.write_text('fail', encoding='utf-8')
  cc_path.write_text(cc_path.read_text() + '// another edit\n', encoding='utf-8')
  res = run_helper()
  assert res.returncode != 0
  assert stamp_path.read_text() == last_stamp
  assert so_path.read_text() == last_so

  scons_mode.write_text('bad_postcheck', encoding='utf-8')
  h_path.write_text(h_path.read_text() + '// trigger postcheck fail\n', encoding='utf-8')
  last_count = len(build_log.read_text().splitlines())
  res = run_helper()
  assert res.returncode != 0
  assert stamp_path.read_text() == last_stamp
  assert len(build_log.read_text().splitlines()) == last_count + 1
  assert 'Native Params validation failed:' in res.stdout

  # A missing hash input must fail before SCons, never hash a partial file set.
  last_count = len(build_log.read_text().splitlines())
  cc_path.unlink()
  res = run_helper()
  assert res.returncode != 0
  assert stamp_path.read_text() == last_stamp
  assert len(build_log.read_text().splitlines()) == last_count
  assert so_path.read_text() == last_so
