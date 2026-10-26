"""Analysis location is independent of recording and vehicle control."""
from copy import deepcopy
import re
from pathlib import Path

EXECUTORS = {"device", "remote_pc"}


def validate_executor(value):
  if type(value) is not str or value not in EXECUTORS:
    raise ValueError("invalid_analysis_executor")
  return value


def preference(root, executor=None):
  from .periodic import _atomic
  from .candidate_report import _read
  path = Path(root) / "analysis-executor.json"
  if not path.is_absolute() or any(part.is_symlink() or getattr(part, "is_junction", lambda: False)() for part in (path, *path.parents)):
    raise ValueError("analysis_executor_path_invalid")
  if executor is None:
    if not path.exists():
      return "device"
    value, _ = _read(path, 1024)
    if type(value) is not dict or set(value) != {"schema_version", "executor"} or value["schema_version"] != 1:
      raise ValueError("analysis_executor_preference_invalid")
    return validate_executor(value["executor"])
  validate_executor(executor)
  path.parent.mkdir(parents=True, exist_ok=True)
  _atomic(path, {"schema_version": 1, "executor": executor}, durable=True)
  return executor


def validate_execution(value):
  if (type(value) is not dict or set(value) != {"executor", "revision", "pending_job"}
      or type(value["revision"]) is not str or re.fullmatch(r"[a-f0-9]{32}", value["revision"]) is None
      or value["pending_job"] is not None and (type(value["pending_job"]) is not str
          or re.fullmatch(r"[a-f0-9]{64}-[a-f0-9]{8}", value["pending_job"]) is None)):
    raise ValueError("invalid_analysis_execution")
  validate_executor(value["executor"])
  return deepcopy(value)
