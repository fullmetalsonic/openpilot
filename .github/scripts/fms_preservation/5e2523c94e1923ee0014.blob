"""Fixed host analysis goals. Selecting a goal never changes vehicle settings."""
from copy import deepcopy
from pathlib import Path

from .runtime_config import BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH


PROFILE_ENV = "CARROT_CONTROL_ADVISOR_AUTO_PROFILE"
OBJECTIVES_ENV = "CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES"
FMS_PRESET_RAW = {"CruiseMaxVals0": 160, "CruiseMaxVals1": 200, "CruiseMaxVals2": 160,
                  "CruiseMaxVals3": 130, "CruiseMaxVals4": 110, "CruiseMaxVals5": 95, "CruiseMaxVals6": 80}
FMS_PRESET_SOURCE_SHA256 = "d3b8b78a73366eda592d1d8b58bce9b35b7b7ccf9aca5b2e7d0682ed4ac8a788"
_DEFAULTS = Path(__file__).resolve().parent / "defaults"
_GOALS = {
  "tracking": {"id": "tracking", "label": "추적·횡방향 보정", "version": 1,
    "description": "기록된 독립 응답으로 기존 횡방향 보정 목표를 분석합니다.", "settings_raw": {}, "source_sha256": None},
  "fms_accel_defaults_v1": {"id": "fms_accel_defaults_v1", "label": "FMS 기본 가속 기준", "version": 1,
    "description": "현재 차속과 실제 가속 모드를 사용해 고정 FMS 가속 기본값과의 정책 일치를 분석합니다. 최적 가속이나 실차 개선을 증명하지 않습니다.",
    "settings_raw": FMS_PRESET_RAW, "source_sha256": FMS_PRESET_SOURCE_SHA256},
  "torque_pid_experiment_v1": {"id": "torque_pid_experiment_v1", "label": "토크 PID 모델 실험", "version": 1,
    "description": "연속 PID 기록과 독립 센서 응답을 검증해 모델이 지지하는 수동 실험 후보를 비교합니다. 인과적 개선이나 실차 최적값을 뜻하지 않습니다.",
    "settings_raw": {}, "source_sha256": None},
}
_GOAL_FILES = {
  "fms_accel_defaults_v1": ("native_acceleration_profile.json", "native_acceleration_objectives.json"),
  "torque_pid_experiment_v1": ("native_pid_profile.json", "native_pid_objectives.json"),
  "fms_policy_defaults_v1": ("native_policy_profile.json", "native_policy_objectives.json"),
}


def _goals():
  from .policy_observations import POLICY_DEFAULTS, SOURCE_SHA256
  return {**_GOALS, "fms_policy_defaults_v1": {
    "id": "fms_policy_defaults_v1", "label": "FMS 기본 정책 비교", "version": 1,
    "description": "같은 실제 초기 상태와 입력에서 FMS 기본 정책의 한 cycle 출력과 비교합니다. 지속 궤적이나 차량 개선 예측이 아닙니다.",
    "settings_raw": dict(POLICY_DEFAULTS), "source_sha256": SOURCE_SHA256}}


def preset_reference():
  return {"type": "fms_policy_preset", "preset_id": "fms_accel_defaults", "version": 1,
          "settings_raw": dict(FMS_PRESET_RAW), "source_sha256": FMS_PRESET_SOURCE_SHA256}


def validate_preset_reference(reference):
  if (type(reference) is not dict or set(reference) != set(preset_reference())
      or type(reference.get("version")) is not int or type(reference.get("settings_raw")) is not dict
      or set(reference["settings_raw"]) != set(FMS_PRESET_RAW)
      or any(type(value) is not int for value in reference["settings_raw"].values())
      or reference != preset_reference()):
    raise ValueError("invalid_fms_acceleration_preset")
  return deepcopy(reference)


def goal_options(environment):
  """A custom startup pair remains authoritative and offers no silent override."""
  if (type(environment) is not dict or environment.get(PROFILE_ENV) != BUILTIN_PROFILE_PATH
      or environment.get(OBJECTIVES_ENV) != BUILTIN_OBJECTIVES_PATH):
    return []
  return deepcopy(list(_goals().values()))


def goal_configuration(goal_id, environment):
  goals = _goals()
  if type(goal_id) is not str or goal_id not in goals:
    raise ValueError("invalid_analysis_goal")
  if not goal_options(environment):
    raise ValueError("analysis_goal_requires_builtin_configuration")
  if goal_id == "tracking":
    profile, objectives = BUILTIN_PROFILE_PATH, BUILTIN_OBJECTIVES_PATH
  else:
    profile, objectives = (str(_DEFAULTS / name) for name in _GOAL_FILES[goal_id])
  return {"profile_path": profile, "objectives_path": objectives, "selection": deepcopy(goals[goal_id])}
