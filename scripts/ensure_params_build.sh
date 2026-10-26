#!/usr/bin/env bash
set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="$ROOT/openpilot/common/params_pyx.so"
CACHE_DIR="${SCONS_CACHE_DIR:-/tmp/scons_cache}"
if [ -z "${SCONS_CACHE_DIR:-}" ] && [ -f /TICI ]; then
  CACHE_DIR="/data/scons_cache"
fi

# Hold the cache guard across fingerprinting, SCons, validation and stamp writes.
# SConstruct validates and reuses this inherited descriptor instead of relocking.
# Read-only, so a guard created by another user still works; never removed.
function lock_params_cache {
  local guard="${CACHE_DIR}.build.lock"
  if [ -L "$guard" ]; then
    echo "SCons cache guard must not be a symlink" >&2
    return 1
  fi
  [ -e "$guard" ] || : >> "$guard" || return 1
  if [ ! -f "$guard" ]; then
    echo "SCons cache guard must be a regular file" >&2
    return 1
  fi
  exec 8<"$guard" || return 1
  if ! flock -n 8; then
    echo "Waiting for another SCons build to release $CACHE_DIR (up to 300s)" >&2
    if ! flock -w 300 8; then
      echo "SCons cache is in use: $CACHE_DIR; Params build deferred" >&2
      return 1
    fi
  fi
  export SCONS_CACHE_LOCK_FD=8
}
if ! mkdir -p "$CACHE_DIR" 2>/dev/null; then
  CACHE_DIR="/tmp/scons_cache"
  mkdir -p "$CACHE_DIR"
fi
export SCONS_CACHE_DIR="$CACHE_DIR"
lock_params_cache || exit 1

STAMP="$CACHE_DIR/carrot_params_keys.sha256"
SOURCE_INPUTS=(
  "openpilot/common/params_pyx.pyx"
  "openpilot/common/params.cc"
  "openpilot/common/params.h"
  "openpilot/common/params_keys.h"
)
FINGERPRINT="$(cd "$ROOT" && sha256sum "${SOURCE_INPUTS[@]}" | sha256sum | awk '{print $1}')"
BUILT_HASH="$(cat "$STAMP" 2>/dev/null || true)"

if [ -n "$FINGERPRINT" ] && [ "$FINGERPRINT" = "$BUILT_HASH" ] && [ -f "$MODULE" ]; then
  if PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 "$ROOT/openpilot/system/manager/params_check.py"; then
    exit 0
  fi
fi

echo "Params registry changed; rebuilding params_pyx.so."

cd "$ROOT"
scons -u -j4 openpilot/common/params_pyx.so
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 "$ROOT/openpilot/system/manager/params_check.py"
printf '%s\n' "$FINGERPRINT" > "$STAMP.tmp"
mv -f "$STAMP.tmp" "$STAMP"
