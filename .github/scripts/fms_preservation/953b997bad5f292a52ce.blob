"""Pure, side-effect-free runtime configuration resolver for FMS Control Advisor.

Derives runtime identities, canonical storage paths, readiness capabilities,
and component blockers without filesystem mutations, process leases, or socket binding.
"""

import os
from pathlib import Path
import platform
import posixpath
from typing import Any, Dict, List, Optional, Set, Tuple

NATIVE_DEFAULTS = {
    "evidence": "/data/media/0/fms_control_advisor/evidence",
    "candidates": "/data/media/0/fms_control_advisor/candidates",
    "requests": "/data/media/0/fms_control_advisor/requests",
    "exports": "/data/media/0/fms_control_advisor/exports",
    "consumer_socket": "/tmp/fms_control_advisor/consumer.sock",
}

BUILTIN_PROFILE_PATH = str(Path(__file__).resolve().parent / "defaults" / "native_profile.json")
BUILTIN_OBJECTIVES_PATH = str(Path(__file__).resolve().parent / "defaults" / "native_objectives.json")


def _is_link(path: Path) -> bool:
  try:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
  except FileNotFoundError:
    return False


def _is_absolute(path_str: str) -> bool:
  if not isinstance(path_str, str) or not path_str.strip():
    return False
  clean = path_str.strip()
  if clean.startswith("/"):
    return True
  return Path(clean).is_absolute()


def _canonical_path_str(path_str: str) -> str:
  clean = path_str.strip()
  if clean.startswith("/"):
    return posixpath.normpath(clean)
  return os.path.normpath(clean)


def _probe_params(params: Any) -> Tuple[bool, Optional[str]]:
  if params is None:
    return False, None
  try:
    path = params.get_param_path()
    if isinstance(path, (str, Path)) and str(path).strip():
      return True, str(path).strip()
  except TypeError:
    try:
      path = params.get_param_path("")
      if isinstance(path, (str, Path)) and str(path).strip():
        return True, str(path).strip()
    except Exception:
      pass
  except Exception:
    pass
  return False, None


def _resolve_lazy_hw_log_root() -> Optional[str]:
  try:
    from openpilot.system.hardware.hw import Paths
    return Paths.log_root()
  except Exception:
    return None


def _get_env_override(env: Any, *keys: str) -> Optional[str]:
  for key in keys:
    val = env.get(key)
    if isinstance(val, str) and val.strip():
      return val.strip()
    if val is not None and not isinstance(val, str):
      return val
  return None


def _validate_directory(
    component_name: str,
    path_str: Optional[str],
    *, allow_final_link: bool = False
) -> Tuple[Optional[str], Optional[Path], List[str]]:
  if path_str is None:
    return None, None, []

  if not _is_absolute(path_str):
    return None, None, [f"{component_name}_not_absolute"]

  p = Path(path_str)
  try:
    for parent in p.parents:
      if _is_link(parent):
        return None, None, [f"{component_name}_link"]
    if not allow_final_link and _is_link(p):
      return None, None, [f"{component_name}_link"]
    resolved = p.resolve()
    if _is_link(resolved) or any(_is_link(part) for part in resolved.parents):
      return None, None, [f"{component_name}_link"]
    if p.exists() and not p.is_dir():
      return None, None, [f"{component_name}_not_directory"]
  except Exception:
    return None, None, [f"{component_name}_invalid"]

  return _canonical_path_str(path_str), resolved, []


def _validate_socket(path_str: Optional[str]) -> Tuple[Optional[str], Optional[Path], List[str]]:
  if path_str is None:
    return None, None, []

  if not _is_absolute(path_str):
    return None, None, ["consumer_socket_not_absolute"]

  p = Path(path_str)
  try:
    if _is_link(p) or any(_is_link(parent) for parent in p.parents):
      return None, None, ["consumer_socket_link"]
    resolved = p.resolve()
    if _is_link(resolved) or any(_is_link(part) for part in resolved.parents):
      return None, None, ["consumer_socket_link"]
    if p.exists() and not p.is_socket():
      return None, None, ["consumer_socket_not_socket"]
  except Exception:
    return None, None, ["consumer_socket_invalid"]

  return _canonical_path_str(path_str), resolved, []


def _validate_file(component_name: str, path_str: Optional[str]) -> Tuple[Optional[str], List[str]]:
  if path_str is None:
    return None, [f"{component_name}_missing"]

  if not _is_absolute(path_str):
    return None, [f"{component_name}_not_absolute"]

  p = Path(path_str)
  try:
    if _is_link(p) or any(_is_link(part) for part in p.parents):
      return None, [f"{component_name}_link"]
    resolved = p.resolve()
    if _is_link(resolved) or any(_is_link(part) for part in resolved.parents):
      return None, [f"{component_name}_link"]
    if not p.exists():
      return None, [f"{component_name}_not_found"]
    if not p.is_file():
      return None, [f"{component_name}_not_regular_file"]
  except Exception:
    return None, [f"{component_name}_invalid"]

  return _canonical_path_str(path_str), []


def _dedup(items: List[str]) -> List[str]:
  seen: Set[str] = set()
  out: List[str] = []
  for item in items:
    if item not in seen:
      seen.add(item)
      out.append(item)
  return out


def resolve_runtime_config(
    *,
    environment: Optional[Any] = None,
    params: Optional[Any] = None,
    native_comma: Optional[bool] = None,
    log_root: Optional[Any] = None,
) -> Dict[str, Any]:
  env = os.environ if environment is None else environment

  if native_comma is not None:
    is_native = bool(native_comma)
  else:
    is_native = platform.system() == "Linux" and os.path.exists("/TICI")

  runtime_kind = "native_comma" if is_native else "pc"
  params_available, params_dir = _probe_params(params)

  raw_settings = _get_env_override(env, "CARROT_CONTROL_ADVISOR_SETTINGS_DIR")
  if raw_settings is None and params_available:
    raw_settings = params_dir

  raw_logs = _get_env_override(env, "CARROT_CONTROL_ADVISOR_LOG_ROOT")
  logs_unavailable = False
  if raw_logs is None and log_root is not None and str(log_root).strip():
    raw_logs = str(log_root).strip()
  if raw_logs is None:
    raw_logs = _get_env_override(env, "LOG_ROOT")
  if raw_logs is None and is_native:
    raw_logs = _resolve_lazy_hw_log_root()
    logs_unavailable = raw_logs is None

  raw_evidence = _get_env_override(env, "CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT")
  if raw_evidence is None and is_native:
    raw_evidence = NATIVE_DEFAULTS["evidence"]

  raw_candidates = _get_env_override(
      env,
      "CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT",
  )
  if raw_candidates is None and is_native:
    raw_candidates = NATIVE_DEFAULTS["candidates"]

  raw_requests = _get_env_override(env, "CARROT_CONTROL_ADVISOR_REQUEST_ROOT")
  if raw_requests is None and is_native:
    raw_requests = NATIVE_DEFAULTS["requests"]

  raw_exports = _get_env_override(env, "CARROT_CONTROL_ADVISOR_EXPORT_ROOT")
  if raw_exports is None and is_native:
    raw_exports = NATIVE_DEFAULTS["exports"]

  raw_socket = _get_env_override(env, "CARROT_CONTROL_ADVISOR_CONSUMER_SOCKET")
  if raw_socket is None and is_native:
    raw_socket = NATIVE_DEFAULTS["consumer_socket"]

  has_profile_env = "CARROT_CONTROL_ADVISOR_AUTO_PROFILE" in env
  has_objectives_env = "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES" in env

  if is_native and not has_profile_env and not has_objectives_env:
    raw_profile = BUILTIN_PROFILE_PATH
    raw_objectives = BUILTIN_OBJECTIVES_PATH
  else:
    raw_profile = _get_env_override(
        env,
        "CARROT_CONTROL_ADVISOR_AUTO_PROFILE",
    )
    raw_objectives = _get_env_override(
        env,
        "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES",
    )

  blockers_collection: List[str] = []
  blockers_analysis: List[str] = []
  blockers_requests: List[str] = []
  blockers_exports: List[str] = []
  paths_valid = True

  if not is_native:
    blockers_collection.append("native_runtime_required")
  if not params_available:
    blockers_collection.append("params_unavailable")

  can_settings, res_settings, err_settings = _validate_directory(
      "settings_dir", raw_settings, allow_final_link=True
  )
  if err_settings:
    paths_valid = False
    blockers_collection.extend(err_settings)
  elif can_settings is None:
    blockers_collection.append("settings_dir_missing")

  can_logs, res_logs, err_logs = _validate_directory("log_root", raw_logs, allow_final_link=False)
  if err_logs:
    paths_valid = False
    blockers_collection.extend(err_logs)
  elif can_logs is None:
    blockers_collection.append("log_root_unavailable" if logs_unavailable else "log_root_missing")

  can_evidence, res_evidence, err_evidence = _validate_directory(
      "evidence_root", raw_evidence, allow_final_link=False
  )
  if err_evidence:
    paths_valid = False
    blockers_collection.extend(err_evidence)
  elif can_evidence is None:
    blockers_collection.append("evidence_root_missing")

  can_candidates, res_candidates, err_candidates = _validate_directory(
      "candidates_root", raw_candidates, allow_final_link=False
  )
  if err_candidates:
    paths_valid = False
    blockers_analysis.extend(err_candidates)
  elif can_candidates is None:
    blockers_analysis.append("candidates_root_missing")

  can_requests, res_requests, err_requests = _validate_directory(
      "request_root", raw_requests, allow_final_link=False
  )
  if err_requests:
    paths_valid = False
    blockers_requests.extend(err_requests)
  elif can_requests is None:
    blockers_requests.append("request_root_missing")

  can_exports, res_exports, err_exports = _validate_directory(
      "export_root", raw_exports, allow_final_link=False
  )
  if err_exports:
    paths_valid = False
    blockers_exports.extend(err_exports)
  elif can_exports is None:
    blockers_exports.append("export_root_missing")

  can_socket, res_socket, err_socket = _validate_socket(raw_socket)
  if err_socket:
    paths_valid = False
    blockers_collection.extend(err_socket)
  elif can_socket is None:
    blockers_collection.append("consumer_socket_missing")

  can_profile, err_profile = _validate_file("profile", raw_profile)
  if err_profile:
    if raw_profile is not None:
      paths_valid = False
    blockers_analysis.extend(err_profile)

  can_objectives, err_objectives = _validate_file("objectives", raw_objectives)
  if err_objectives:
    if raw_objectives is not None:
      paths_valid = False
    blockers_analysis.extend(err_objectives)

  dir_entries = [
      ("settings", "settings_dir", can_settings, res_settings, blockers_collection),
      ("logs", "log_root", can_logs, res_logs, blockers_collection),
      ("evidence", "evidence_root", can_evidence, res_evidence, blockers_collection),
      ("candidates", "candidates_root", can_candidates, res_candidates, blockers_analysis),
      ("requests", "request_root", can_requests, res_requests, blockers_requests),
      ("exports", "export_root", can_exports, res_exports, blockers_exports),
  ]

  resolved_dirs = [entry for entry in dir_entries if entry[3] is not None]
  overlapping_keys: Set[str] = set()

  for i, (key_a, name_a, _, res_a, block_a) in enumerate(resolved_dirs):
    for key_b, name_b, _, res_b, block_b in resolved_dirs[i + 1:]:
      if res_a == res_b or res_a in res_b.parents or res_b in res_a.parents:
        paths_valid = False
        overlapping_keys.add(key_a)
        overlapping_keys.add(key_b)
        block_a.append(f"{name_a}_overlap")
        block_b.append(f"{name_b}_overlap")

  if res_socket is not None:
    for key, name, _, res_d, _ in resolved_dirs:
      if res_socket == res_d or res_d in res_socket.parents or res_socket in res_d.parents:
        paths_valid = False
        can_socket = None
        blockers_collection.append("consumer_socket_overlap")
        break

  paths = {
      "settings": None if "settings" in overlapping_keys else can_settings,
      "logs": None if "logs" in overlapping_keys else can_logs,
      "evidence": None if "evidence" in overlapping_keys else can_evidence,
      "candidates": None if "candidates" in overlapping_keys else can_candidates,
      "requests": None if "requests" in overlapping_keys else can_requests,
      "exports": None if "exports" in overlapping_keys else can_exports,
      "consumer_socket": can_socket,
      "profile": can_profile,
      "objectives": can_objectives,
  }

  dedup_collection = _dedup(blockers_collection)
  dedup_analysis = _dedup(blockers_analysis)
  dedup_requests = _dedup(blockers_requests)
  dedup_exports = _dedup(blockers_exports)

  capabilities = {
      "native_runtime_detected": is_native,
      "params_available": params_available,
      "paths_valid": paths_valid,
      "collection_ready": is_native and params_available and len(dedup_collection) == 0,
      "analysis_configured": len(dedup_analysis) == 0,
      "apply_enabled": False,
      "recommendations_enabled": False,
      "device_qualified": False,
  }

  blockers = {
      "collection": dedup_collection,
      "analysis": dedup_analysis,
      "requests": dedup_requests,
      "exports": dedup_exports,
  }

  return {
      "runtime_kind": runtime_kind,
      "paths": paths,
      "capabilities": capabilities,
      "blockers": blockers,
  }
