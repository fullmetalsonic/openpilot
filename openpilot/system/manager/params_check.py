"""Check the native Params registry before trusting a prebuilt checkout."""
import re
import tempfile
from pathlib import Path


PARAMS_HEADER = Path(__file__).resolve().parents[2] / 'common' / 'params_keys.h'
REQUIRED_METHODS = ('get_raw', 'get_many_raw', 'compare_and_set_raw')


def check_keys(source: str, compiled_keys) -> None:
  # Ignore disabled declarations, including multiline block comments.
  source = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.DOTALL)
  expected = set(re.findall(r'^\s*\{\s*"([^"]+)"\s*,\s*\{', source, flags=re.MULTILINE))
  if not expected:
    raise ValueError('No Params keys found in source header')
  actual = {key.decode('utf-8') if isinstance(key, bytes) else key for key in compiled_keys}
  if actual != expected:
    raise ValueError(f'Params registry mismatch: missing={sorted(expected - actual)}, obsolete={sorted(actual - expected)}')


def main() -> int:
  try:
    # Import in this fresh process: the old extension must not stay loaded after
    # SCons rebuilds it. Missing/unloadable bindings also require a rebuild.
    from openpilot.common.params import Params
    for method in REQUIRED_METHODS:
      if not callable(getattr(Params, method, None)):
        raise AttributeError(f'Params missing callable {method}')
    with tempfile.TemporaryDirectory() as tmp_dir:
      params = Params(tmp_dir)
      compiled_keys = params.all_keys()
    check_keys(PARAMS_HEADER.read_text(encoding='utf-8'), compiled_keys)
  except Exception as exc:
    print(f'Native Params validation failed: {exc}', flush=True)
    return 1
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
