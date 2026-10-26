import { decodeExclusions } from "./comparison_intervals.js";

const ACTIVE_STATES = new Set(["starting", "running", "collecting", "evaluating", "waiting", "waiting_for_data", "degraded", "stopping"]);

const STATE_LABELS = Object.freeze({
  idle: "수집 전", starting: "수집 준비 중", running: "수집 중", waiting: "메시지 대기 중",
  collecting: "자료 수집 중", evaluating: "진단 계산 중",
  waiting_for_data: "메시지 대기 중", degraded: "자료 품질 저하", stopping: "수집 종료 중",
  stopped: "수집 종료", completed: "수집 완료", ended: "수집 종료", failed: "수집 실패",
  unavailable: "실제 수신 사용 불가",
});

const REASON_LABELS = Object.freeze({
  native_unavailable: "이 환경에서는 실제 메시지를 받을 수 없습니다.",
  messaging_unavailable: "이 환경에서는 실제 메시지를 받을 수 없습니다.",
  waiting_for_data: "메시지를 기다리고 있습니다.",
  late_event: "늦게 도착한 메시지가 있어 해당 분석 근거를 다시 확인해야 합니다.",
  queue_overflow: "수신 한도를 넘어 해당 구간 분석을 보류했습니다.",
  heartbeat_timeout: "수집 작업의 응답이 끊겼습니다.",
  worker_exited: "수집 작업이 종료됐습니다.",
  input_stale: "입력 메시지가 끊겨 이전 진단 결과를 무효화했습니다.",
  collecting_unvalidated_diagnostics: "검증 전 진단 자료를 수집하고 있습니다.",
  unvalidated_diagnostic_ready: "검증 전 진단 통계를 확인할 수 있습니다.",
  synthetic_logical_600_seconds_complete: "가속된 합성 시계의 600초 재생을 마쳤습니다. 실제 주행이나 실제 경과 시간이 아닙니다.",
});

export function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function formatAdvisorNumber(value, digits = 0) {
  const number = finiteNumber(value);
  if (number === null) return "—";
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits }).format(number);
}

export function formatAdvisorBytes(value) {
  const number = finiteNumber(value);
  if (number === null || number < 0) return "—";
  if (number >= 1024 * 1024) return `${formatAdvisorNumber(number / (1024 * 1024), 1)} MiB`;
  if (number >= 1024) return `${formatAdvisorNumber(number / 1024, 1)} KiB`;
  return `${formatAdvisorNumber(number)} B`;
}

export function formatConsumerValue(value) {
  if (typeof value === "boolean") return String(value);
  if (finiteNumber(value) !== null) return formatAdvisorNumber(value, 6);
  if (Array.isArray(value) && value.length > 0 && value.length <= 16 && value.every((number) => finiteNumber(number) !== null)) {
    return `[${value.map((number) => formatAdvisorNumber(number, 6)).join(", ")}]`;
  }
  return "—";
}

const CONSUMER_LABELS = Object.freeze({
  "controls.vehicle_model": "차량 모델", "controls.lateral_target": "횡방향 목표", "controls.torque": "토크 제어",
  "controls.longitudinal": "종방향 제어", "modeld.action": "주행 모델 출력", "planner.lateral": "횡방향 계획",
  "planner.lane_path": "차선 경로", "planner.longitudinal": "종방향 계획", "planner.carrot": "Carrot 주행 정책",
  "planner.long_mpc": "종방향 MPC",
});
const CONSUMER_STATUS_LABELS = Object.freeze({
  disabled: "소비값 관측 비활성", waiting: "소비값 관측 대기", observed: "소비값 관측됨",
  partial: "소비값 일부 관측", unavailable: "소비값 관측 사용 불가", stopped: "소비값 관측 종료",
});
const CONSUMER_ENTRY_LABELS = Object.freeze({
  observed: "관측됨", stale: "오래된 관측 · 현재값 미확인", gap: "관측 공백 있음",
  restarted: "소비 프로세스 재시작 관측", unknown: "관측 상태 미확인",
});
const FIELD_STATE_LABELS = Object.freeze({
  loaded: "읽기 지점 관측", used: "사용 경로 관측", latched: "생성자에서 고정", bypassed: "현재 경로에서 우회",
  unsupported: "현재 경로 미지원", unknown: "소비값 미확인",
});
const FIELD_SOURCE_LABELS = Object.freeze({
  stored: "저장소에서 읽은 값", learned: "자동 학습값", platform_fixed: "차량 플랫폼 고정값", inherited: "상속값",
  derived: "계산된 값", retained_previous: "이전 내부값 유지", unknown: "출처 미확인",
});
const PERSISTENCE_LABELS = Object.freeze({
  disabled: "관측 이력 기록 비활성", waiting: "관측 이력 기록 대기", recorded: "관측 이력 기록됨",
  partial: "관측 이력 일부 기록", failed: "관측 이력 기록 오류",
});

function knownString(value, labels, fallback) {
  return typeof value === "string" && Object.hasOwn(labels, value) ? value : fallback;
}

function safeConsumerValue(value) {
  if (typeof value === "boolean" || finiteNumber(value) !== null) return value;
  if (Array.isArray(value) && value.length > 0 && value.length <= 16 && value.every((number) => finiteNumber(number) !== null)) return [...value];
  return null;
}

export function createConsumerObservationsModel(value, parameterKeys = []) {
  const input = value && typeof value === "object" ? value : {};
  const state = knownString(input.state, CONSUMER_STATUS_LABELS, "disabled");
  const allowedKeys = new Set(parameterKeys);
  const available = state === "observed" || state === "partial";
  const entries = [];
  for (const consumer of Array.isArray(input.consumers) ? input.consumers : []) {
    const id = knownString(consumer?.consumer_id, CONSUMER_LABELS, null);
    if (!id || entries.some((entry) => entry.id === id)) continue;
    const entryState = knownString(consumer.state, CONSUMER_ENTRY_LABELS, "unknown");
    const event = consumer.latest_event && typeof consumer.latest_event === "object" ? consumer.latest_event : {};
    const fresh = available && consumer.fresh === true && ["observed", "gap", "restarted"].includes(entryState) && event.consumer_id === id;
    const gap = entryState === "gap" || count(event.dropped_events) > 0;
    const fields = {};
    for (const [key, field] of Object.entries(event.fields && typeof event.fields === "object" ? event.fields : {})) {
      if (!allowedKeys.has(key) || !field || typeof field !== "object") continue;
      const fieldState = knownString(field.state, FIELD_STATE_LABELS, "unknown");
      const source = knownString(field.source, FIELD_SOURCE_LABELS, "unknown");
      const readable = fresh && fieldState !== "unknown" && fieldState !== "unsupported";
      fields[key] = {
        state: fieldState, stateLabel: fresh ? FIELD_STATE_LABELS[fieldState] : CONSUMER_ENTRY_LABELS.stale,
        sourceLabel: FIELD_SOURCE_LABELS[source],
        readFresh: fresh && consumer.read_freshness?.[key] === true,
        readLabel: fresh && consumer.read_freshness?.[key] === true ? "읽힌 값 (소비 단위)" : "이전 읽기 관측값",
        readStatusLabel: fresh && consumer.read_freshness?.[key] === true ? "읽기 시각 신선도 확인" : "설정 읽기 시각 오래됨·미확인",
        readValue: readable ? safeConsumerValue(field.read_value) : null,
        effectiveValue: readable ? safeConsumerValue(field.effective_value) : null,
        effectiveLabel: fieldState === "used" ? "관측된 사용값" : fieldState === "latched" ? "생성자 고정 내부값"
          : fieldState === "bypassed" ? "선택 경로 내부값" : "읽기 후 내부값",
      };
    }
    entries.push({ id, label: CONSUMER_LABELS[id], stateLabel: CONSUMER_ENTRY_LABELS[entryState], fresh,
      gap, restarted: entryState === "restarted", fields });
  }
  const persistence = input.persistence || {};
  const persistenceState = knownString(persistence.state, PERSISTENCE_LABELS, null);
  return {
    state, stateLabel: CONSUMER_STATUS_LABELS[state],
    sourceLabel: input.source_mode === "synthetic" ? "합성 소비 관측 · 실제 제어기 아님"
      : input.source_mode === "live" ? "기기 소비 지점 관측 · 실기기 검증 전" : "소비 관측 출처 미확인",
    receivedEvents: count(input.received_events), rejectedEvents: count(input.rejected_events),
    droppedEvents: count(input.dropped_events), restarts: count(input.restarts),
    freshConsumers: entries.filter((entry) => entry.fresh).length, entries,
    persistence: { stateLabel: persistenceState ? PERSISTENCE_LABELS[persistenceState] : "관측 이력 기록 미연결",
      recordedEvents: count(persistence.recorded_events), droppedEvents: count(persistence.dropped_events) },
    consumerApplicationAck: false, deviceQualified: false,
  };
}

function count(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function durationMs(begin, end) {
  if (finiteNumber(begin) === null || finiteNumber(end) === null || begin < 0 || end < begin) return null;
  return (end - begin) / 1e6;
}

const RECORDING_LABELS = Object.freeze({
  disabled: "기록 비활성", starting: "기록 준비 중", recording: "관측·보존 진행 중",
  partial: "일부 기록 · 누락 확인 필요", failed: "기록 오류", stopped: "기록 종료",
});
const SETTINGS_LABELS = Object.freeze({
  unavailable: "저장값 관측 불가", observed: "저장값 관측됨", partial: "저장값 일부 관측",
  failed: "저장값 관측 오류", closed: "관측 종료",
});
const PRESERVATION_LABELS = Object.freeze({
  disabled: "원본 보존 비활성", waiting: "닫힌 세그먼트 대기", pending: "원본 보존 대기",
  copying: "원본 사본 보존 중", verified: "개별 세그먼트 검증됨", partial: "원본 일부 보존",
  failed: "원본 보존 오류", stopped: "보존 작업 종료",
});

export function createRecordingModel(value) {
  const recording = value && typeof value === "object" ? value : {};
  const settings = recording.settings || {};
  const observation = settings.observation || {};
  const journal = settings.journal || {};
  const preservation = recording.preservation || {};
  const revision = count(settings.revision);
  const durableRevision = count(settings.durable_revision);
  const settingsObserved = revision !== null && ["observed", "partial", "closed"].includes(settings.state);
  const settingsJournalCurrent = settingsObserved && journal.durable === true && durableRevision !== null && durableRevision >= revision;
  const pendingRevision = revision !== null && durableRevision !== null && revision > durableRevision;
  const uncoveredWindows = count(preservation.uncovered_windows);
  const failedSegments = count(preservation.failed_segments);
  const pendingJobs = count(preservation.pending_jobs);
  let preservationLabel = PRESERVATION_LABELS[preservation.state] || "원본 보존 미연결";
  if (preservation.state === "verified" && (uncoveredWindows > 0 || failedSegments > 0 || pendingJobs > 0)) {
    preservationLabel = "개별 사본 검증 · 미포함 구간 있음";
  } else if (preservation.state === "verified" && preservation.journal_committed !== true) {
    preservationLabel = "개별 사본 검증 · 보존 이력 미확정";
  }
  return {
    configured: typeof recording.state === "string" && recording.state !== "disabled",
    state: typeof recording.state === "string" ? recording.state : "disabled",
    stateLabel: RECORDING_LABELS[recording.state] || "기록 미연결",
    settings: {
      stateLabel: SETTINGS_LABELS[settings.state] || "저장값 관측 미연결", observed: settingsObserved,
      revision, durableRevision, pollSequence: count(settings.poll_sequence),
      changedCount: Array.isArray(settings.changed_keys) ? settings.changed_keys.length : null,
      gap: observation.gap === true,
      gapKnown: typeof observation.gap === "boolean",
      observationWidthMs: durationMs(observation.begin_ns, observation.end_ns),
      changeObserved: Boolean(observation.change_interval && typeof observation.change_interval === "object"),
      changeUncertaintyMs: durationMs(observation.change_interval?.after_ns, observation.change_interval?.before_ns),
      journalCurrent: settingsJournalCurrent,
      journalStateLabel: settingsJournalCurrent ? "현재 관측 기록 확인" : pendingRevision ? "최신 관측 기록 대기"
        : journal.durable === false ? "관측 기록 미확정" : "관측 기록 미연결",
      journalSequence: count(journal.sequence), journalBytes: count(journal.bytes),
      historyLabel: "관측 사이 변경 누락 가능 · 전체 설정 이력 미확인",
      consumerLabel: "실제 제어기 반영 미확인",
    },
    preservation: {
      state: typeof preservation.state === "string" ? preservation.state : "disabled",
      stateLabel: preservationLabel,
      verifiedSegments: count(preservation.verified_segments), verifiedBytes: count(preservation.verified_bytes),
      failedSegments, pendingJobs, uncoveredWindows,
      journalCommitted: preservation.journal_committed === true,
      latestDiagnosticRevision: count(preservation.latest_diagnostic_revision),
      fullCoverageConfirmed: false,
      coverageLabel: "최신·미완료 구간을 포함한 전체 원본 보존은 미확인",
    },
  };
}

function sumKnown(rows, key) {
  if (!rows.length || rows.some((row) => finiteNumber(row?.[key]) === null)) return null;
  return rows.reduce((sum, row) => sum + row[key], 0);
}

function axisSummary(diagnostic, axis) {
  const epochs = Object.values(diagnostic?.axes?.[axis]?.epochs || {});
  return {
    axis,
    sampleCount: sumKnown(epochs, "sample_count"),
    eligibleCount: sumKnown(epochs, "eligible_count"),
    excludedCount: sumKnown(epochs, "excluded_count"),
    provisionalCount: sumKnown(epochs, "provisional_count"),
    eligibleSeconds: sumKnown(epochs, "eligible_duration_ns") === null ? null : sumKnown(epochs, "eligible_duration_ns") / 1e9,
    epochs: epochs.map((epoch) => ({
      rawRmse: finiteNumber(epoch?.raw_metrics?.rmse),
      eligibleRmse: finiteNumber(epoch?.eligible_metrics?.rmse),
      bias: finiteNumber(epoch?.eligible_metrics?.bias),
      p95: finiteNumber(epoch?.eligible_metrics?.p95_absolute_error),
    })),
  };
}

const CANDIDATE_STATES = Object.freeze({ disabled: "오프라인 후보 보고서 미연결", loading: "오프라인 후보 보고서 확인 중",
  waiting: "완료 로그 분석 결과 대기", running: "완료 로그 분석 중", stale: "이전 결과 · 최신 분석 미확인",
  partial: "분석 창 일부 보류·실패 · 후보 표시 보류", failed: "완료 로그 분석 실패", stopped: "자동 분석 종료",
  ready: "오프라인 후보 계산 결과", invalid: "오프라인 후보 보고서 확인 실패" });
const CANDIDATE_STATUSES = Object.freeze({ raw_estimate: "진단용 관계식 추정 · 추천값 아님", search_candidate: "탐색 후보 · 미검증",
  keep_current: "현재값 유지", blocked: "계산 조건 미충족", unsupported: "분석 경로 미지원" });
const PREDICTION_LABELS = Object.freeze({ static_formula: "정적 관계식", frozen_input_replay: "고정 입력 재생",
  closed_loop_model: "폐루프 모델" });
const CANDIDATE_REASONS = Object.freeze({
  independent_objective_missing: "독립 목적함수 근거 없음", objective_unit_mismatch: "목적함수 단위 불일치",
  split_dependency_overlap: "분할 사이 의존 구간 겹침", insufficient_independent_split_exposure: "독립 분할의 사용 근거 부족",
  single_change_limit_missing: "단일 변경 한도 근거 없음", objective_guard_missing: "목적함수 보호 조건 없음",
  manual_experiment_change_limit_unset: "수동 실험 후보 · 자동 변경 한도 미지정",
  model_uncertainty_no_candidate: "모델 불확실성 안에서 변경 효과를 구분하지 못해 현재값 유지",
  pid_command_transport_unverified: "실제 조향 출력의 변환 관계 미확인",
  pid_command_transport_ambiguous: "조향 출력 지연을 하나로 구분하지 못함",
  pid_trace_contract_required: "정밀 PID 기록의 기준 정보 부족",
  pid_effective_use_unlinked: "저장한 PID 값의 연속 사용 근거 부족",
  torque_mapping_adapter_unavailable: "실제 토크 함수의 표준 관계식 미확인",
  response_time_alignment_required: "토크 출력과 차량 반응의 지연 정렬 필요",
  torque_callback_zero_offset_required: "표준 토크 함수의 오프셋 근거 불일치",
  current_value_retained: "현재값 유지 · 변경 근거 미충족", holdout_or_objective_guard_failed: "보류 검증 또는 보호 조건 미충족",
  host_objective_only_not_vehicle_validation: "호스트 목적함수만 확인 · 차량 검증 아님", backend_unavailable: "계산 백엔드 사용 불가",
  context_only_no_automatic_mode_switch: "설정 맥락 항목 · 자동 모드 변경 대상 아님", derivative_path_has_no_effect: "현재 미분 제어 경로에서 효과 없음",
  platform_fixed_longitudinal_pid: "차량 플랫폼 고정 PID 경로", current_raw_unknown: "기준 저장값 미확인",
  profile_source_kind_not_allowed: "프로파일이 이 자료 종류를 허용하지 않음", missing_stale_or_mismatched_signal_units: "필수 신호 누락·오래됨 또는 단위 불일치",
  consumer_identity_or_epoch_unknown: "소비 지점 또는 설정 구간 미확인", consumer_evidence_not_time_aligned: "소비 관측과 분석 구간의 시각 불일치",
  synthetic_epoch_not_fixture: "합성 설정 구간 근거 불충족", engine_contract_or_budget_failed: "계산 계약 또는 자원 한도 조건 미충족",
  engine_error: "계산 오류로 보류", independent_plant_required: "독립 차량 응답 모델 필요", trajectory_replay_required: "궤적 재생 검증 경로 필요",
  diagnostic_proxy_only: "진단 대리 지표만 계산 · 원래 파라미터 식별값 아님",
});

function candidateMetric(value) {
  if (!value || typeof value !== "object") return null;
  const result = { rows: count(value.rows), blocks: count(value.blocks) };
  for (const key of ["rmse", "max_absolute_error", "max_absolute_prediction"]) {
    result[key] = finiteNumber(value[key]);
    if (result[key] === null || result[key] < 0) return null;
  }
  return result.rows !== null && result.blocks !== null ? result : null;
}

function contextOnlyParameter(item) {
  return item?.classification === "context_only" && item?.key !== "CustomSR";
}

function parameterReason(item) {
  if (contextOnlyParameter(item)) return "설정 맥락 항목 · 숫자 후보 대상 아님";
  if (item?.structural_blocker) return "현재 소비 경로에서 효과 없음";
  const calculation = item?.candidate_calculation;
  if (calculation?.backend === "unavailable" || (Array.isArray(calculation?.reason_codes)
      && calculation.reason_codes.includes("backend_unavailable"))) return "계산 백엔드 사용 불가 · 실시간 추천 보류";
  if (calculation?.estimate_kind === "diagnostic_proxy") return "진단 대리 지표 계산 경로 · 파라미터 추천 아님";
  if (Array.isArray(calculation?.reason_codes) && calculation.reason_codes.length) return "오프라인 계산 조건 미충족 · 실시간 추천 보류";
  if (typeof calculation?.method_id === "string" && calculation.method_id.length > 0
      && typeof calculation.backend === "string" && calculation.backend.length > 0) return "오프라인 계산 경로 제공 · 실시간 추천 근거 미연결";
  return "추천 계산 미구현 · 검증 미완료";
}

function changesSpecialMode(key, current, candidate) {
  if (key === "CustomSR") return current <= 10 || candidate <= 10;
  const sentinel = key === "SteerActuatorDelay" ? 0 : /^LeadAccelResponseTF[1-4]$/.test(key) ? -1 : null;
  return sentinel !== null && (current === sentinel) !== (candidate === sentinel);
}

const LOG_LINK_REASONS = Object.freeze({
  control_inactive: "제어 비활성", invalid_signal: "신호 유효성 미충족", recovery_not_ready: "회복 근거 미충족",
  output_limited: "출력 제한", intervention_unknown: "운전자 개입 여부 미확인", driver_intervention: "운전자 개입",
  recovery: "개입 후 회복", intervention_pre_margin: "개입 직전 여유 구간", signal_gap: "신호 공백",
  dependency_unobserved: "의존 신호 미관측", dependency_window: "의존 신호 확인 구간", settings_unknown: "설정 미확인",
  settings_transition: "설정 전환 구간", consumer_evidence_missing: "소비 관측 근거 누락", session_unlinked: "세션 미연결",
  helper_source_provenance_not_recorded: "보조 소스 출처 이력 미기록", clock_binding_witnessed_coorigin: "동일 출처 시계 연결 근거 확인",
  recording_schema_match_unverified: "기록 스키마 일치 미검증",
  settings_observation_uncertain: "설정 읽기 구간 불확실", settings_dependency_uncertain: "설정 관측 공백을 통과한 의존 신호",
  split_dependency_support: "분할 경계의 의존 여백", settings_epoch_unlinked: "설정 구간 미연결",
  recorded_response_duplicate: "같은 센서 응답의 중복 표본",
  recorded_response_unavailable: "독립 센서 응답 시각 미확인",
  recorded_reference_unlinked: "기록된 차량 구성·독립 반응 연결 미충족",
  recorded_dependency_unavailable: "해당 항목의 시간 지원 구간 부족",
  factor_mapping_unverified: "해당 구간의 표준 토크 관계식 미확인",
});

function candidateLogLink(value, sourceKind) {
  if (value === undefined) return null;
  const invalid = { available: false, stateLabel: "원본 연결 정보 확인 불가" };
  const trip = value?.whole_trip;
  const wholeTrip = trip && ["thirty_time_strata_centred_ten_seconds_native_5hz_v1", "complete_burst_each_frozen_time_third_v1"].includes(trip.selection_policy)
    && count(trip.raw_anchor_count) !== null && trip.raw_anchor_count <= 1e9
    && count(trip.selected_anchor_count) !== null && trip.selected_anchor_count > 0 && trip.selected_anchor_count <= trip.raw_anchor_count
    ? { rawCount: trip.raw_anchor_count, selectedCount: trip.selected_anchor_count,
      policyLabel: trip.selection_policy === "complete_burst_each_frozen_time_third_v1" ? "시간 구간별 완전 PID 기록" : "주행 시간대별 균등 선정" } : null;
  if (sourceKind !== "recorded" || !value || typeof value !== "object" || Array.isArray(value)
      || value.source_bytes_verified_at_generation !== true || value.schema_decoded !== true
      || !["verified_archive_receipt", "unlinked"].includes(value.session_link)
      || !["partial", "per_signal_verified"].includes(value.semantic_mapping)
      || !["verified_session_archive", "reference_only"].includes(value.originals_preservation)
      || value.originals_current_availability !== "not_checked"
      || count(value.source_count) === null || value.source_count > (wholeTrip ? 128 : 32)
      || count(value.epoch_count) === null || value.epoch_count > 32
      || !value.excluded_counts || typeof value.excluded_counts !== "object" || Array.isArray(value.excluded_counts)
      || Object.keys(value.excluded_counts).length > 32
      || Object.values(value.excluded_counts).some((number) => count(number) === null || number > 1e9)
      || !Array.isArray(value.reasons) || value.reasons.length > 32
      || value.reasons.some((reason) => typeof reason !== "string" || reason.length > 96)) return invalid;
  const audit = value.sampling_audit;
  const rawCount = count(audit?.raw_anchor_count), selectedCount = count(audit?.selected_count);
  const uniqueCount = count(audit?.unique_response_count), fallbackCount = count(audit?.anchor_fallback_count ?? 0);
  const sampling = audit?.version === "native_static_response_5hz_v1" && rawCount !== null && rawCount <= 1e9
    && selectedCount !== null && uniqueCount !== null && fallbackCount !== null
    && selectedCount <= uniqueCount + fallbackCount && uniqueCount + fallbackCount <= rawCount
    && ["response_time", "anchor_time", "response_or_anchor"].includes(audit.clock_basis || "response_time")
    ? { rawCount, selectedCount, uniqueCount, fallbackCount,
      basis: audit.clock_basis || "response_time" } : null;
  return { available: true, stateLabel: "생성 당시 원본 파일 해시·추출 연결 확인", sampling, wholeTrip,
    sessionLabel: value.session_link === "verified_archive_receipt" ? "검증된 보존 세션 영수증과 연결" : "세션 미연결 · 과거 로그일 수 있음",
    semanticLabel: value.semantic_mapping === "per_signal_verified" ? "신호별 매핑 확인 · 주행 검증 아님" : "의미 매핑 일부만 확인",
    preservationLabel: value.originals_preservation === "verified_session_archive" ? "기존 세션 보존 사본 확인" : "원본 참조만 연결 · 보존 확인 아님",
    availabilityLabel: "현재 원본 접근 가능 여부는 재확인하지 않음",
    sourceCount: value.source_count, epochCount: value.epoch_count,
    excludedCounts: Object.entries(value.excluded_counts).filter(([reason]) => Object.hasOwn(LOG_LINK_REASONS, reason))
      .map(([reason, number]) => ({ label: LOG_LINK_REASONS[reason], count: number })),
    reasons: [...new Set(value.reasons.filter((reason) => Object.hasOwn(LOG_LINK_REASONS, reason)).map((reason) => LOG_LINK_REASONS[reason]))] };
}

function periodicReportStatus(value) {
  if (!value || (!Object.hasOwn(value, "attempt") && !Object.hasOwn(value, "last_success") && !Object.hasOwn(value, "stale"))) return null;
  const attempt = value.attempt && typeof value.attempt === "object" ? value.attempt : {};
  const success = value.last_success && typeof value.last_success === "object" ? value.last_success : {};
  const windows = Array.isArray(attempt.windows) && attempt.windows.length <= 8 && attempt.windows.every((window) => window
    && ["ready", "blocked", "failed"].includes(window.state) && ((count(window.source_start_ns) !== null
      && count(window.source_end_ns) !== null && window.source_end_ns >= window.source_start_ns)
      || (window.state !== "ready" && window.source_start_ns === null && window.source_end_ns === null))) ? attempt.windows : null;
  const latestWindow = windows?.at(-1);
  const epoch = value.latest_epoch;
  const epochValid = count(epoch?.start_ns) !== null && count(epoch?.end_ns) !== null && epoch.end_ns >= epoch.start_ns;
  const reasonLabels = { periodic_publisher_unavailable: "자동 분석 작업자의 응답이 없어 후보를 보류합니다.",
    periodic_result_waiting: "자동 분석 결과 게시를 기다립니다.", periodic_result_invalid: "게시 결과 검증에 실패했습니다.",
    candidate_report_configuration_conflict: "정적 보고서와 자동 결과 경로가 함께 설정돼 결과를 보류합니다.",
    periodic_result_requires_absolute_directory: "자동 결과 경로의 절대 디렉터리 설정을 확인해야 합니다.",
    periodic_cache_quota: "자동 분석 결과 저장 한도에 도달했습니다.", periodic_job_timeout: "자동 분석이 제한 시간 안에 끝나지 않았습니다.",
    automatic_output_root_overlap: "자동 결과 경로가 다른 작업 경로와 겹쳐 분석을 보류합니다.",
    waiting_for_new_closed_archive: "새 완료 원본 사본을 기다립니다.", periodic_source_gap: "분석 대상 원본에 공백이 있습니다.",
    periodic_archive_unavailable: "보존 원본을 사용할 수 없습니다.", periodic_segment_missing: "필수 원본 세그먼트가 누락됐습니다.",
    periodic_root_session_overlap: "자동 결과 경로와 기록 세션 경로가 겹칩니다.", periodic_root_unowned: "자동 결과 경로의 소유 조건이 충족되지 않았습니다.",
    periodic_shutdown_incomplete: "자동 분석 종료를 확인하지 못했습니다.", periodic_windows_partial: "일부 분석 창이 보류되거나 실패했습니다." };
  return { stateLabel: CANDIDATE_STATES[value.state] || "자동 분석 상태 미확인",
    continuousBatch: value.analysis_mode === "continuous_batch",
    epochStartSeconds: epochValid ? epoch.start_ns / 1e9 : null,
    epochEndSeconds: epochValid ? epoch.end_ns / 1e9 : null,
    epochDurationSeconds: epochValid ? (epoch.end_ns - epoch.start_ns) / 1e9 : null,
    reasonLabel: Object.hasOwn(reasonLabels, value.reason) ? reasonLabels[value.reason] : null,
    sourceStartSeconds: count(attempt.source_start_ns) === null ? null : attempt.source_start_ns / 1e9,
    sourceEndSeconds: count(attempt.source_end_ns) === null ? null : attempt.source_end_ns / 1e9,
    cutoffSeconds: count(attempt.cutoff_mono_ns) === null ? null : attempt.cutoff_mono_ns / 1e9,
    sourceCount: count(attempt.source_count),
    intervalSeconds: [180, 300].includes(value.interval_seconds) ? value.interval_seconds : null,
    processedWindows: count(attempt.processed_windows), blockedWindows: count(attempt.blocked_windows),
    readyWindows: windows ? windows.filter((window) => window.state === "ready").length : null,
    failedWindows: windows ? windows.filter((window) => window.state === "failed").length : null,
    latestWindowState: latestWindow?.state || null,
    latestWindowStartSeconds: count(latestWindow?.source_start_ns) === null ? null : latestWindow.source_start_ns / 1e9,
    latestWindowEndSeconds: count(latestWindow?.source_end_ns) === null ? null : latestWindow.source_end_ns / 1e9,
    resultSourceEndSeconds: count(success.source_end_ns) === null ? null : success.source_end_ns / 1e9,
    publicationRevision: count(value.result_revision),
    resultRevision: count(success.result_revision), stale: value.stale === true };
}

export function createCandidateReportModel(value, parameters = []) {
  const state = value?.stale === true && value?.state === "ready" ? "stale" : knownString(value?.state, CANDIDATE_STATES, "disabled");
  const empty = { state, stateLabel: CANDIDATE_STATES[state], available: false, items: [],
    periodic: periodicReportStatus(value),
    deviceQualified: false, applyEnabled: false };
  if (state !== "ready") return empty;
  const report = value?.report;
  const allowed = new Map(parameters.map((item) => [item?.key, item]));
  const invalid = () => ({ ...empty, state: "invalid", stateLabel: CANDIDATE_STATES.invalid });
  if (empty.periodic && (!empty.periodic.latestWindowState || empty.periodic.failedWindows > 0
      || empty.periodic.latestWindowStartSeconds === null || empty.periodic.latestWindowEndSeconds === null)) return invalid();
  if (value.integrity_verified !== true || !/^[a-f0-9]{64}$/.test(value.artifact_sha256 || "")
      || value.device_qualified !== false || value.apply_enabled !== false || value.raw_sources_verified !== false
      || report?.schema_version !== 1 || report.scope !== "host_unqualified"
      || !["synthetic", "recorded"].includes(report.source_kind)
      || report.device_qualified !== false || report.apply_enabled !== false || report.recommendations_enabled !== false
      || allowed.size !== 47 || !Array.isArray(report.items) || report.items.length !== 47) return invalid();
  const seen = new Set();
  const items = [];
  for (const row of report.items) {
    const parameter = allowed.get(row?.key);
    if (!parameter || seen.has(row.key) || !Object.hasOwn(CANDIDATE_STATUSES, row.status)) return invalid();
    seen.add(row.key);
    const metrics = row.metrics || {};
    const contextOnly = contextOnlyParameter(parameter);
    const validation = row.validation || {};
    const selection = candidateMetric(metrics.selection);
    const holdout = candidateMetric(metrics.holdout);
    if ([row.device_qualified, row.recommendations_enabled, row.apply_enabled].some((flag) => flag === true)
        || (row.status === "raw_estimate" && (contextOnly || !["parameter_calibration", "diagnostic_proxy"].includes(row.estimate_kind)
          || validation.numerical_checks_pass !== true
          || finiteNumber(row.raw_estimate) === null || finiteNumber(row.physical_estimate) === null))
        || (row.status === "search_candidate" && (contextOnly || row.estimate_kind === "diagnostic_proxy" || validation.numerical_checks_pass !== true
          || validation.split_independent !== true || validation.holdout_objective_pass !== true || !selection || !holdout
          || finiteNumber(row.candidate_raw) === null || finiteNumber(row.candidate_physical) === null
          || changesSpecialMode(row.key, row.current_raw, row.candidate_raw)))) return invalid();
    items.push({ key: row.key, axis: parameter.axis === "longitudinal" ? "longitudinal" : "lateral", contextOnly,
      status: row.status, statusLabel: contextOnly ? "설정 맥락 · 숫자 후보 대상 아님"
        : row.status === "raw_estimate" && row.estimate_kind === "diagnostic_proxy" ? "진단 대리 지표 · 파라미터 식별값 아님" : CANDIDATE_STATUSES[row.status],
      estimateKind: ["parameter_calibration", "diagnostic_proxy"].includes(row.estimate_kind) ? row.estimate_kind : null,
      unit: typeof parameter.physical_unit === "string" ? parameter.physical_unit : "",
      currentRaw: finiteNumber(row.current_raw), currentPhysical: finiteNumber(row.current_physical),
      currentSpecial: finiteNumber(row.current_raw) !== null && Object.hasOwn(parameter.special_values || {}, String(row.current_raw)),
      candidateRaw: row.status === "search_candidate" ? finiteNumber(row.candidate_raw) : null,
      candidatePhysical: row.status === "search_candidate" ? finiteNumber(row.candidate_physical) : null,
      rawEstimate: row.status === "raw_estimate" ? finiteNumber(row.raw_estimate) : null,
      physicalEstimate: row.status === "raw_estimate" ? finiteNumber(row.physical_estimate) : null,
      predictionLabel: row.provenance?.candidate_semantics === "manual_experiment"
        && row.provenance?.evaluation_scope === "observational_pid_model" ? "관측 기반 PID 모델 · 수동 실험"
        : PREDICTION_LABELS[row.prediction_kind] || "예측 방식 미확인",
      reasons: [...new Set((Array.isArray(row.reason_codes) ? row.reason_codes : []).slice(0, 24)
        .filter((reason) => Object.hasOwn(CANDIDATE_REASONS, reason)).map((reason) => CANDIDATE_REASONS[reason]))],
      baselineSelection: candidateMetric(metrics.baseline?.select), baselineHoldout: candidateMetric(metrics.baseline?.holdout),
      selection, holdout,
      numericalChecksPass: validation.numerical_checks_pass === true, splitIndependent: validation.split_independent === true,
      holdoutObjectivePass: validation.holdout_objective_pass === true,
      deviceQualified: false, applyEnabled: false });
  }
  const logLink = candidateLogLink(value.log_link, report.source_kind);
  return { ...empty, available: true, sourceKind: report.source_kind,
    integrityLabel: logLink?.available ? "입력 묶음 무결성 확인 · 로그 추출 연결 정보 있음" : "입력 묶음 무결성 확인 · 원본 로그 연계 미검증",
    logLink,
    sourceLabel: report.source_kind === "synthetic" ? "합성 자료 · 실제 주행 결과 아님" : "기록 자료의 오프라인 계산 · 실제 주행 검증 전",
    items };
}

const REQUEST_STATES = Object.freeze({ registered: "요청 등록됨", held: "요청 보류", intent: "저장 의도 기록됨 · 결과 재확인 중",
  stored: "PC 모의 저장 영수증 확인", consumer_observed: "별도 소비 관측 근거 확인", comparison_ready: "관측 비교 준비됨",
  cancelled: "의도 기록 전 취소됨", expired: "요청 만료", rejected: "조건 불일치로 거절", indeterminate: "결과 불확정 · 재확인 필요" });
const REQUEST_REASONS = Object.freeze({ request_registration_disabled: "요청 등록 기능 미설정", request_configuration_invalid: "요청 경로·근거 설정 조건 미충족",
  request_evidence_unavailable: "현재 세션·설정·후보 연결 근거 미충족", request_status_unavailable: "요청 상태 조회 실패", request_status_waiting: "요청 상태 확인 중",
  request_candidate_not_available: "등록할 수 있는 검증 후보 없음", vehicle_writer_unavailable: "차량 쓰기 경로 미연결 · 등록 기록만 보존",
  pc_simulation_unavailable: "PC 모의 저장소 미연결", candidate_binding_changed: "후보·세션·설정 조건이 달라져 보류",
  compare_and_swap_conflict: "기대 저장값 또는 버전이 달라 저장하지 않음", request_expired: "요청 유효기간 지남", operator_cancelled: "저장 의도 기록 전에 취소함",
  crash_after_intent_uncertain: "의도 기록 후 중단 · 저장 결과 재확인 필요", pc_readback_unconfirmed: "PC 모의 저장 후 읽기 확인 미충족",
  pc_write_unconfirmed: "PC 모의 저장 결과 미확인", recovered_pc_receipt: "복구된 PC 모의 저장 영수증 확인",
  comparison_held: "후속 비교 근거 미충족 · 현재 비교 보류", restart_reconfirmation_required: "재시작으로 이전 요청 종료 · 최신 근거로 재등록 필요",
  request_ledger_write_uncertain: "요청 기록 저장 결과 불확정 · 새 요청 보류", request_ledger_uncertain: "요청 기록 무결성 미확인 · 새 요청 보류" });
const validRequestId = (value) => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const validDigest = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const requestReason = (value) => Object.hasOwn(REQUEST_REASONS, value) ? REQUEST_REASONS[value]
  : typeof value === "string" && /^(native|vehicle)_/.test(value) ? nativeReasonMessage(value) : null;

function requestBindingEligible(payload, itemKey, currentRaw) {
  const context = payload.current_request_context;
  const entry = context?.items?.find((item) => item?.item_key === itemKey);
  return ["vehicle", "pc_simulation"].includes(context?.scope)
    && (context.scope !== "pc_simulation" || payload.capabilities?.pc_simulation_enabled === true)
    && validRequestId(context?.target_id) && validRequestId(context?.session_id) && validDigest(context?.artifact_sha256)
    && validDigest(context?.dependency_digest) && count(context?.result_revision) !== null && context.result_revision > 0
    && count(context?.settings_revision) !== null && context.artifact_sha256 === payload.candidate_report?.artifact_sha256
    && (payload.candidate_report?.result_revision === undefined || payload.candidate_report.result_revision === context.result_revision)
    && (context.scope !== "vehicle" || (context.session_id === payload.session?.session_id && payload.session?.source_mode === "live"
      && ACTIVE_STATES.has(payload.session?.state) && !["starting", "stopping"].includes(payload.session.state)))
    && entry?.eligible === true && Number.isSafeInteger(entry.expected_current_raw) && entry.expected_current_raw === currentRaw;
}

function requestEligibility(payload, itemKey, report) {
  const context = payload?.current_request_context;
  const scope = ["vehicle", "pc_simulation"].includes(context?.scope) ? context.scope : null;
  const entry = Array.isArray(context?.items) ? context.items.find((item) => item?.item_key === itemKey) : null;
  const candidate = report.items.find((item) => item.key === itemKey);
  let reason = "현재 세션·설정·후보 연결 근거 미충족";
  const configured = payload?.capabilities?.request_registration_enabled === true && payload?.request_state?.state === "ready";
  if (!configured) reason = "요청 등록 기능 미설정";
  else if (!report.available || candidate?.status !== "search_candidate") reason = "등록할 수 있는 검증 후보 없음";
  const eligible = configured && report.available && candidate?.status === "search_candidate"
    && scope !== null && requestBindingEligible(payload, itemKey, candidate.currentRaw);
  return { eligible: Boolean(eligible), scope, writesVehicle: Boolean(eligible && scope === "vehicle"
    && payload?.capabilities?.vehicle_write_available === true && payload?.native_runtime?.writer_available === true), reason: eligible ? scope === "vehicle"
    ? payload?.capabilities?.vehicle_write_available === true ? "명시적으로 요청하면 차량 설정 저장 조건을 다시 확인합니다. 저장과 실제 소비는 별도입니다." : "차량 쓰기는 보류됩니다. 현재 쓰기 경로 준비 조건을 충족하지 않았습니다."
    : "명시적인 PC 모의 저장소에만 요청합니다."
    : requestReason(entry?.reason) || reason };
}

export function buildApplyRequestBody(payload, itemKey, requestId) {
  const report = createCandidateReportModel(payload?.candidate_report, Array.isArray(payload?.parameters) ? payload.parameters : []);
  if (!validRequestId(requestId) || !requestEligibility(payload, itemKey, report).eligible) throw new Error("request_evidence_unavailable");
  const context = payload.current_request_context;
  const item = context.items.find((entry) => entry.item_key === itemKey);
  return { request_id: requestId, target_id: context.target_id, session_id: context.session_id,
    artifact_sha256: context.artifact_sha256, result_revision: context.result_revision, item_key: itemKey,
    expected_current_raw: item.expected_current_raw, expected_settings_revision: context.settings_revision,
    dependency_digest: context.dependency_digest };
}

export const CALIBRATION_KEYS = Object.freeze(["SteerActuatorDelay", "CustomSR", "LateralTorqueKpV", "LateralTorqueKf", "LatSmoothSec", "LongActuatorDelay"]);
const TRIAL_KEYS = new Set(["SteerActuatorDelay", "LatSmoothSec", "LongActuatorDelay"]);
const TRACE_COLUMNS = ["mono_ns", "target", "actual", "target_mono_ns", "actual_mono_ns", "independent_actual", "independent_mono_ns", "sent_request", "sent_mono_ns", "segment", "sent_raw", "stop_req", "jerk_upper", "jerk_lower"];

export function createCalibrationReviewModel(payload = {}) {
  const raw = payload.candidate_report?.calibration_review;
  const empty = { available: false, invalid: Boolean(raw), parameterKey: null, state: "held", purpose: null, series: [], trials: [], reasons: [] };
  if (!raw) return empty;
  const stamp = (value) => typeof value === "string" && /^(0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) <= 9223372036854775807n;
  const number = (value) => value === null || typeof value === "number" && Number.isFinite(value);
  try {
    if (raw.schema_version !== 1 || !CALIBRATION_KEYS.includes(raw.parameter_key) || !["ready", "held"].includes(raw.state)
      || !["numeric_calibration", "manual_trial"].includes(raw.purpose) || payload.candidate_report?.state !== "ready"
      || payload.candidate_report?.stale === true || payload.candidate_report?.integrity_verified !== true
      || new TextEncoder().encode(JSON.stringify(raw)).length > 128 * 1024
      || !Array.isArray(raw.reasons) || raw.reasons.some((reason) => typeof reason !== "string" || !/^[a-z0-9_]{1,128}$/.test(reason))
      || !raw.baseline || !raw.baseline.settings_raw || !validDigest(raw.baseline.input_sha256) || !validDigest(raw.baseline.profile_sha256)) return empty;
    const series = [];
    for (const axis of ["lateral", "longitudinal"]) {
      const source = raw.series?.[axis];
      if (!source || JSON.stringify(source.columns) !== JSON.stringify(TRACE_COLUMNS) || source.unit !== "m/s^2"
        || !Array.isArray(source.points) || source.points.length > 600 || count(source.raw_count) === null || count(source.selected_count) === null) return empty;
      let previous = null;
      for (const point of source.points) {
        if (!Array.isArray(point) || point.length !== 14 || !stamp(point[0]) || previous !== null && BigInt(point[0]) <= previous
          || ![3, 4, 6, 8].every((index) => point[index] === null || stamp(point[index]))
          || ![1, 2, 5, 7, 10, 11, 12, 13].every((index) => number(point[index]))
          || point[9] !== null && (!Number.isSafeInteger(point[9]) || point[9] < 0)) return empty;
        previous = BigInt(point[0]);
      }
      series.push({ axis, unit: source.unit, actualSource: source.actual_source, independentSource: source.independent_source,
        sentSource: source.sent_source, points: source.points, rawCount: source.raw_count, selectedCount: source.selected_count,
        metrics: raw.metrics?.[axis] || {}, exclusions: raw.exclusions?.[axis] || {} });
    }
    if (!Array.isArray(raw.trial_options) || raw.trial_options.length > 2 || raw.purpose === "numeric_calibration" && raw.trial_options.length) return empty;
    const currentRaw = raw.baseline.settings_raw[raw.parameter_key];
    const contextEntry = payload.current_request_context?.items?.find((item) => item.item_key === raw.parameter_key);
    const trials = raw.trial_options.map((option) => {
      if (raw.purpose !== "manual_trial" || !TRIAL_KEYS.has(raw.parameter_key) || option.purpose !== "manual_trial"
        || option.parameter_key !== raw.parameter_key || !validDigest(option.id) || !Number.isSafeInteger(option.candidate_raw)
        || !Number.isSafeInteger(option.current_raw) || option.current_raw !== currentRaw || option.restore_raw !== currentRaw
        || option.candidate_raw === currentRaw) throw new Error("invalid_trial_option");
      const configured = payload.capabilities?.request_registration_enabled === true && payload.request_state?.state === "ready";
      return { id: option.id, currentRaw, candidateRaw: option.candidate_raw, direction: option.candidate_raw > currentRaw ? "증가" : "감소",
        eligible: Boolean(configured && raw.state === "ready" && raw.baseline.settings_raw.LateralTorqueKiV === 0 && contextEntry?.purpose === "manual_trial"
          && contextEntry.trial_ids?.includes(option.id) && requestBindingEligible(payload, raw.parameter_key, currentRaw)) };
    });
    return { available: true, invalid: false, parameterKey: raw.parameter_key, state: raw.state, purpose: raw.purpose,
      currentRaw, series, trials, reasons: raw.reasons, kiRaw: raw.baseline.settings_raw.LateralTorqueKiV };
  } catch { return empty; }
}

export function buildTrialRequestBody(payload, itemKey, trialId, requestId) {
  const review = createCalibrationReviewModel(payload);
  if (!validRequestId(requestId) || review.parameterKey !== itemKey || !review.trials.some((item) => item.id === trialId && item.eligible)) {
    throw new Error("manual_trial_evidence_unavailable");
  }
  const context = payload.current_request_context;
  return { request_id: requestId, target_id: context.target_id, session_id: context.session_id,
    artifact_sha256: context.artifact_sha256, result_revision: context.result_revision, item_key: itemKey,
    expected_current_raw: review.currentRaw, expected_settings_revision: context.settings_revision,
    dependency_digest: context.dependency_digest, purpose: "manual_trial", trial_id: trialId };
}

function comparisonMetric(value) {
  if (!value || typeof value !== "object") return null;
  const rmse = finiteNumber(value.rmse), p95 = finiteNumber(value.p95_absolute_error);
  return { count: count(value.count), rmse: rmse !== null && rmse >= 0 ? rmse : null, bias: finiteNumber(value.bias), p95: p95 !== null && p95 >= 0 ? p95 : null };
}

function comparisonSummary(value) {
  if (!value || typeof value !== "object") return null;
  return { sampleCount: count(value.sample_count), eligibleCount: count(value.eligible_count), excludedCount: count(value.excluded_count),
    provisionalCount: count(value.provisional_count), eligibleSeconds: count(value.eligible_duration_ns) === null ? null : value.eligible_duration_ns / 1e9,
    raw: comparisonMetric(value.raw_metrics), eligible: comparisonMetric(value.eligible_metrics),
    detailPages: value.exclusions?.encoding === "reason-indexed-pages-v1" ? count(value.exclusions.page_count) : null,
    detailIntervals: value.exclusions?.encoding === "reason-indexed-pages-v1" ? count(value.exclusions.interval_count) : null };
}

function requestComparison(value) {
  if (!value || ![1, 2, 3].includes(value.schema_version) || !["held", "consumer_observed", "comparison_ready"].includes(value.state)
      || !["observational_generated", "observational_model_derived", "observational_vehicle_acceleration"].includes(value.scope)
      || value.target_unit !== "m/s^2" || ["consumer_application_ack", "causal_improvement_proven", "apply_enabled", "device_qualified"].some((key) => value[key] !== false)) return null;
  if (value.schema_version === 2) {
    try {
      for (const summary of [value.before, value.after]) {
        if (summary !== null) decodeExclusions(summary?.exclusions, 2);
      }
    } catch { return null; }
  }
  if (value.schema_version === 3) {
    for (const summary of [value.before, value.after]) {
      if (summary === null) continue;
      const ref = summary?.exclusions;
      if (!ref || Object.keys(ref).sort().join(",") !== "encoding,interval_count,page_count,path,sha256"
          || ref.encoding !== "reason-indexed-pages-v1" || !validDigest(ref.sha256)
          || ref.path !== `comparison-details/${ref.sha256}/manifest.json`
          || !Number.isSafeInteger(ref.interval_count) || ref.interval_count < 0
          || !Number.isSafeInteger(ref.page_count) || ref.page_count < 0 || ref.page_count > 128
          || (ref.interval_count === 0) !== (ref.page_count === 0)) return null;
    }
  }
  const labels = { required_consumer_observation_missing: "별도 소비 관측 근거 부족", consumer_sample_not_time_aligned: "소비 관측 시각 불일치",
    comparison_eligible_exposure_insufficient: "사용 가능한 비교 구간 부족", comparison_matched_exposure_insufficient: "같은 조건의 비교 구간 부족",
    comparison_dependencies_changed_or_unknown: "의존 설정 변경 또는 미확인", comparison_not_a_single_parameter_change: "단일 항목 변경 조건 미충족",
    comparison_windows_overlap_transition_or_settling: "전환·회복 구간과 비교 창 겹침", comparison_evidence_invalid: "비교 근거 검증 미충족" };
  return { state: value.state, before: comparisonSummary(value.before), after: comparisonSummary(value.after),
    scopeLabel: value.scope === "observational_generated" ? "생성 자료의 관측 비교 · 실제 주행 아님"
      : value.scope === "observational_model_derived" ? "모델 유래 반응의 관측 비교" : "차량 가속 반응의 관측 비교",
    matched: value.matched && typeof value.matched === "object" ? { commonBins: count(value.matched.common_bin_count),
      before: comparisonMetric(value.matched.before), after: comparisonMetric(value.matched.after),
      deltaRmse: finiteNumber(value.matched.after_minus_before?.rmse), deltaBias: finiteNumber(value.matched.after_minus_before?.bias),
      deltaP95: finiteNumber(value.matched.after_minus_before?.p95_absolute_error) } : null,
    reasons: (Array.isArray(value.reason_codes) ? value.reason_codes : []).slice(0, 32)
      .filter((key) => Object.hasOwn(labels, key)).map((key) => labels[key]), causalImprovementProven: false, deviceQualified: false };
}

function applyRequestsModel(payload, keys) {
  const allowed = new Set(keys);
  const current = payload?.request_state?.state === "ready";
  const items = [];
  for (const item of Array.isArray(payload?.apply_requests) ? payload.apply_requests.slice(0, 16) : []) {
    if (!validRequestId(item?.request_id) || !allowed.has(item.item_key) || !Object.hasOwn(REQUEST_STATES, item.state)
        || !["vehicle", "pc_simulation"].includes(item.scope)) continue;
    const comparison = requestComparison(item.comparison);
    const stored = ["stored", "consumer_observed", "comparison_ready"].includes(item.state)
      && count(item.store_receipt?.revision) !== null && finiteNumber(item.store_receipt?.value) !== null;
    if (item.scope === "vehicle" && ["stored", "consumer_observed", "comparison_ready"].includes(item.state) && !stored) continue;
    const stateLabel = item.scope === "vehicle" && item.state === "stored" ? "차량 설정 저장됨 · 소비 확인 대기" : REQUEST_STATES[item.state];
    items.push({ requestId: item.request_id, itemKey: item.item_key, state: item.state, current,
      stateLabel: current ? stateLabel : "이전 결과 · 현재 상태 미확인", recordedStateLabel: stateLabel,
      scope: item.scope, scopeLabel: item.scope === "pc_simulation" ? "PC 모의 요청 · 차량 쓰기 아님" : "실제 차량 설정 요청 · 저장·소비 확인 별도",
      reason: requestReason(item.reason), canCancel: current && item.state === "registered",
      storedValue: stored ? finiteNumber(item.store_receipt?.value) : null, storedRevision: stored ? count(item.store_receipt?.revision) : null,
      consumerObserved: current && item.consumer_observed === true && ["consumer_observed", "comparison_ready"].includes(item.state)
        && ["consumer_observed", "comparison_ready"].includes(comparison?.state),
      comparisonReady: current && item.comparison_ready === true && item.state === "comparison_ready" && comparison?.state === "comparison_ready",
      canRestore: current && stored && item.scope === "vehicle" && (payload?.native_runtime?.writer_available === true || payload?.native_runtime?.offroad_restore_available === true),
      restoreMode: payload?.native_runtime?.offroad_restore_available === true ? "offroad" : "live",
      comparison, applyEnabled: false, deviceQualified: false });
  }
  return { items, current, registrationEnabled: payload?.capabilities?.request_registration_enabled === true,
    vehicleWriterAvailable: payload?.capabilities?.vehicle_write_available === true,
    pcSimulationEnabled: payload?.capabilities?.pc_simulation_enabled === true,
    reason: requestReason(payload?.request_state?.reason) };
}

function linkedRequestCandidate(report, key) {
  const item = report.items.find((entry) => entry.key === key && entry.request?.eligible);
  return item ? { currentRaw: item.currentRaw, candidateRaw: item.candidateRaw, currentPhysical: item.currentPhysical,
    candidatePhysical: item.candidatePhysical, unit: item.unit, sourceLabel: report.sourceLabel } : null;
}

const PARKING_LABELS = Object.freeze({ unknown: "주차 여부 미확인", moving: "이동 신호 관측", park_candidate: "주차 조건 유지 확인 중", park_observed: "주차 조건 신호 관측" });
const OFFROAD_LABELS = Object.freeze({ unknown: "기기 offroad 미확인", onroad_observed: "기기 onroad 신호 관측", offroad_observed: "기기 offroad 신호 관측" });
function parkingModel(value, confirmed = false) {
  const valid = value?.profile_id === "parking-host-unvalidated-v1" && ["native_event", "synthetic_event"].includes(value.source_kind);
  const state = valid ? knownString(value.state, PARKING_LABELS, "unknown") : "unknown";
  const offroad = valid ? knownString(value.offroad_state, OFFROAD_LABELS, "unknown") : "unknown";
  return { state, stateLabel: PARKING_LABELS[state], offroadLabel: OFFROAD_LABELS[offroad],
    confirmed: valid && confirmed === true && value.source_kind === "native_event" && state === "park_observed",
    synthetic: valid && value.source_kind === "synthetic_event" };
}

const SUMMARY_OMISSIONS = Object.freeze({ diagnostic_unavailable: "진단 자료 없음", latest_diagnostic_invalidated: "최근 진단 무효화됨",
  latest_diagnostic_partial: "최근 진단은 부분 구간", finalized_range_unknown: "확정 분석 범위 미확인", diagnostic_duration_unknown: "관측 시간 미확인",
  request_inventory_cached_only: "요청 목록은 화면 캐시 일부", request_ledger_unavailable: "요청 원장 근거 미확보", source_roots_unconfigured: "원본 자료 경로 미설정",
  candidate_reference_unavailable: "후보 결과 연결 근거 미확보", candidate_previous_session: "이전 세션 후보를 현재 요약에 귀속하지 않음",
  comparison_after_source_reference_unavailable: "비교 후 자료 연결 근거 미확보", settings_checkpoint_unavailable: "설정 기록 체크포인트 미확보", raw_archives_unavailable: "완료 원본 사본 미확보" });
function summaryRange(value) {
  return count(value?.start_ns) !== null && count(value?.end_ns) !== null && value.end_ns >= value.start_ns
    ? `${formatAdvisorNumber(value.start_ns / 1e9, 3)}–${formatAdvisorNumber(value.end_ns / 1e9, 3)}초` : "범위 미확인";
}
function parkingSummaryModel(envelope) {
  const value = envelope?.summary;
  const available = value?.schema_version === 1 && value.state === "ready" && validRequestId(value.summary_id) && count(value.revision) > 0
    && value.historical_only === true && ["recommendations_enabled", "apply_enabled", "device_qualified"].every((key) => value[key] === false);
  if (!available) return { available: false, current: false, stateLabel: "불변 요약 자료 대기", axes: [], omissions: [] };
  const diagnostics = value.diagnostics || {}, quality = value.source_quality || {}, requests = value.requests || {};
  const axes = [];
  if (!diagnostics.invalidated) for (const axis of ["lateral", "longitudinal"]) {
    const source = diagnostics.axes?.[axis];
    if (!source) continue;
    axes.push({ axis, sampleCount: count(source.sample_count), eligibleCount: count(source.eligible_count), excludedCount: count(source.excluded_count),
      provisionalCount: count(source.provisional_count), eligibleSeconds: count(source.eligible_duration_ns) === null ? null : source.eligible_duration_ns / 1e9,
      epochs: (Array.isArray(source.epochs) ? source.epochs : []).slice(0, 32).map((epoch) => ({ eligible: comparisonMetric(epoch.eligible_metrics), raw: comparisonMetric(epoch.raw_metrics) })) });
  }
  return { available: true, current: envelope.state === "ready", summaryId: value.summary_id, revision: value.revision,
    stateLabel: diagnostics.invalidated ? "무효화된 진단 포함 · 통계 보류" : diagnostics.partial_window ? "부분 구간의 불변 요약"
      : ACTIVE_STATES.has(value.session_state) ? "임시 요약 · 수집 종료 미확인" : "불변 검토용 요약",
    sourceLabel: ["synthetic", "generated_native"].includes(value.source_kind) ? "생성·합성 시험 자료 · 실제 주행 아님"
      : ["live", "recorded"].includes(value.source_kind) ? "수신·기록 자료의 검토용 요약 · 실차 검증 전" : "자료 출처 미확인 · 검토용 요약",
    endLabel: quality.collection_end_confirmation === "signal_only" ? "수집 종료 신호 있음 · 로그·작업 완료 보장 아님" : "수집 종료 신호 미확인",
    finalizedRange: summaryRange(diagnostics.finalized_range), rawTailRange: summaryRange(diagnostics.raw_tail),
    axes, storedCount: count(requests.stored_count), observedCount: count(requests.consumer_observed_count), comparisonCount: count(requests.comparison_ready_count),
    requestInventoryLabel: requests.inventory_scope === "ledger_snapshot" ? "요청 원장 스냅샷" : "화면에 받은 요청 일부",
    omissions: (Array.isArray(quality.omissions) ? quality.omissions : []).slice(0, 32).filter((key) => Object.hasOwn(SUMMARY_OMISSIONS, key)).map((key) => SUMMARY_OMISSIONS[key]),
    candidateCount: count(value.candidate?.candidate_count), applyEnabled: false, deviceQualified: false };
}

const EXPORT_LABELS = Object.freeze({ queued: "내보내기 대기", running: "묶음 작성·검증 중", cancelling: "취소 확인 중", ready: "검토 묶음 준비됨",
  cancelled: "내보내기 취소됨", timed_out: "내보내기 시간 한도 초과", interrupted: "서버 중단으로 미완료", failed: "내보내기 실패" });
const EXPORT_REASONS = Object.freeze({ review_export_limit: "파일 수·자료량 한도 미충족", review_export_source_changed: "원본·체크포인트가 변경되거나 누락되어 검증 실패",
  review_export_failed: "자료 묶음 작성·검증 미완료", review_integrity_failed: "완료 묶음의 무결성 검증 실패", export_server_restarted: "재시작 전에 완료되지 않은 작업",
  export_server_stopped: "서버 종료로 작업 중단", review_cancelled: "작업 중단 확인 · 부분 파일은 남을 수 있음", review_timeout: "작업 시간 한도 초과", export_storage_unconfirmed: "저장 결과 미확인" });
export function buildReviewRegistrationBody(requestId, text) {
  if (!validRequestId(requestId) || typeof text !== "string") throw new Error("review_json_invalid");
  const source = text.replace(/^\uFEFF/, "");
  let value;
  try { value = JSON.parse(source); } catch { throw new Error("review_json_invalid"); }
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("review_json_invalid");
  if (value.document_state !== "final") throw new Error("review_not_final");
  // Preserve the original JSON: duplicate keys must reach the server's strict parser.
  const body = `{"request_id":${JSON.stringify(requestId)},"review":${source}}`;
  if (new TextEncoder().encode(body).length > 128 * 1024) throw new Error("review_json_too_large");
  return body;
}

const REVIEW_VERDICTS = Object.freeze({ retain: "현재 값 유지 의견", candidate_revision_requested: "후보 수정 요청 의견",
  reviewed_recommendation: "검토자 추천 의견 · 적용 자격 아님", needs_more_data: "추가 자료 필요" });
const VERSION_LABELS = Object.freeze({ matched_export_metadata: "내보내기 메타데이터 일치", declared_only: "검토자 진술만 있음", unknown: "미확인" });
const reviewText = (value, limit = 2048) => typeof value === "string" ? value.slice(0, limit) : "";
export function createReviewHistoryModel(value) {
  const available = value && typeof value === "object" && Array.isArray(value.reviews);
  return { available: !!available, current: available && value.current !== false, bindingVerified: available && value.binding_state === "verified_export",
    items: !available ? [] : value.reviews.slice(0, 32).filter((entry) => /^[a-f0-9]{64}$/.test(entry?.review_hash || "")
      && entry.identity_verified === false && entry.historical_only === true && entry.apply_enabled === false
      && entry.device_qualified === false && entry.review?.document_state === "final"
      && Object.hasOwn(REVIEW_VERDICTS, entry.review?.verdict)).map((entry) => {
      const review = entry.review, versions = value.review_versions?.[entry.review_hash] || {};
      return { hash: entry.review_hash, revision: count(review.review_revision), summaryRevision: count(review.summary_revision),
        tool: reviewText(review.declared_reviewer?.tool, 256), model: reviewText(review.declared_reviewer?.model, 256),
        reviewedAt: reviewText(review.declared_reviewer?.reviewed_at, 256), verdictLabel: REVIEW_VERDICTS[review.verdict],
        versions: ["code", "dbc", "analyzer"].map((key) => ({ key, label: VERSION_LABELS[versions[key]] || VERSION_LABELS.unknown })),
        findings: (Array.isArray(review.findings) ? review.findings : []).slice(0, 64).map((finding) => reviewText(finding?.statement)),
        unreviewed: (Array.isArray(review.unreviewed_scopes) ? review.unreviewed_scopes : []).slice(0, 128).map((item) => reviewText(item?.reason)),
        uncertainties: (Array.isArray(review.uncertainties) ? review.uncertainties : []).slice(0, 128).map((item) => reviewText(item)),
        followUp: (Array.isArray(review.follow_up) ? review.follow_up : []).slice(0, 128).map((item) => reviewText(item)) };
    }) };
}

function reviewExportsModel(payload) {
  return { enabled: payload?.capabilities?.review_export_enabled === true,
    stateLabel: payload?.review_export_state?.state === "ready" ? "명시적 내보내기 사용 가능" : "내보내기 미설정·사용 불가",
    jobs: (Array.isArray(payload?.review_exports) ? payload.review_exports : []).slice(0, 32).filter((job) => /^[a-f0-9]{32}$/.test(job?.job_id || "")
      && Object.hasOwn(EXPORT_LABELS, job.state)).map((job) => ({ jobId: job.job_id, state: job.state, stateLabel: EXPORT_LABELS[job.state],
        reasonLabel: Object.hasOwn(EXPORT_REASONS, job.reason) ? EXPORT_REASONS[job.reason] : null,
        includeRaw: job.include_raw === true, files: count(job.files), bytes: count(job.bytes), zipBytes: count(job.zip_bytes),
        summaryId: reviewText(job.summary_id, 128), summaryRevision: count(job.summary_revision),
        reviewCount: count(job.review_count), latestReviewRevision: count(job.latest_review_revision),
        reviewUnavailable: job.review_state === "unavailable", history: createReviewHistoryModel(payload.review_histories?.[job.job_id]),
        canCancel: ["queued", "running", "cancelling"].includes(job.state), canDownload: job.state === "ready" && job.download_ready === true })) };
}

export function buildReviewExportBody(payload, requestId, includeRaw = false) {
  const summary = parkingSummaryModel(payload?.parking_summary);
  if (!summary.available || !validRequestId(requestId) || typeof includeRaw !== "boolean" || payload?.capabilities?.review_export_enabled !== true) throw new Error("review_export_unavailable");
  return { request_id: requestId, summary_id: summary.summaryId, summary_revision: summary.revision, include_raw: includeRaw };
}

const NATIVE_REASONS = Object.freeze({ native_runtime_required: "콤마 기기 실행 환경이 아님", native_runtime_unavailable: "기기 기능 준비 실패 · 기존 FMS 기능은 계속 사용 가능",
  native_status_unavailable: "현재 기기·차량 상태를 확인할 수 없음", vehicle_confirmation_required: "이 물리 차량의 튜닝 기준을 선택·확인해 주세요.",
  native_params_raw_api_required: "실행 중 Params 빌드에 원시값 읽기·조건부 저장 API가 없음",
  vehicle_group_selection_required: "기존 동일 차량 기준을 선택하거나 새 차량 등록을 명시해 주세요.", vehicle_already_bound: "현재 차량은 이미 기준에 연결되어 있습니다.",
  vehicle_identity_unavailable: "현재 CarParams 차량 식별 정보 미확인", native_live_observation_required: "실시간 수집과 신선한 설정 관측 필요",
  native_baseline_not_created: "최초 설정 저장 전 · 기준 백업 아직 없음", native_restore_current_value_changed: "현재 값이 해당 요청의 변경값과 달라 원복하지 않음",
  native_vehicle_identity_changed: "차량·설정 식별 정보가 달라 요청 보류", native_candidate_boot_changed: "후보 생성 이후 기기가 재시작되어 보류",
  native_session_inactive: "현재 수집 세션이 활성 상태가 아님", native_consumer_source_unavailable: "실제 소비 관측 출처 미확인",
  native_consumer_missing: "필요한 소비 관측 누락", native_consumer_boot_or_age_changed: "소비 관측의 기기·신선도 조건 미충족",
  native_consumer_configuration_changed: "소비 관측의 차량 구성 불일치", native_consumer_value_not_effective: "현재 저장값의 실제 사용 근거 미충족" });
export function nativeReasonMessage(code) { return Object.hasOwn(NATIVE_REASONS, code) ? NATIVE_REASONS[code] : "기기·차량·설정 준비 조건을 다시 확인해 주세요."; }
export function createNativeRuntimeModel(value) {
  const active = value && value.state !== "disabled" && ["ready", "partial", "unavailable"].includes(value.state);
  const vehicle = value?.vehicle || {};
  return { available: !!active, writerAvailable: active && value.writer_available === true, offroadRestoreAvailable: active && value.offroad_restore_available === true,
    confirmed: active && value.vehicle_confirmed === true,
    stateLabel: value?.state === "ready" ? "기기 제어 분석 준비 상태" : value?.state === "partial" ? "기기 일부 준비 조건 미충족" : "기기 상태 확인 필요",
    reason: value?.reason ? nativeReasonMessage(value.reason) : null,
    fingerprint: vehicle.state === "observed" && typeof vehicle.car_fingerprint === "string" ? vehicle.car_fingerprint.slice(0, 256) : "차량 구성 미확인",
    groupId: /^[a-f0-9]{32}$/.test(vehicle.physical_group_id || "") ? vehicle.physical_group_id : null,
    vinLabel: vehicle.vin_known === true ? "VIN 기반 연결 정보 있음" : "VIN 미확인 · 동일 물리 차량인지 직접 확인 필요",
    groups: !active ? [] : (Array.isArray(value.groups) ? value.groups : []).slice(0, 64).filter((item) => /^[a-f0-9]{32}$/.test(item?.group_id || ""))
      .map((item) => ({ id: item.group_id, label: typeof item.car_fingerprint === "string" ? item.car_fingerprint.slice(0, 256) : "저장된 차량 기준" })),
    analysisBlockers: (Array.isArray(value?.analysis_blockers) ? value.analysis_blockers : value?.blockers?.analysis || []).slice(0, 32)
      .map((code) => ({ profile_missing: "분석 프로파일 미설정", objectives_missing: "독립 목표 자료 미설정", profile_not_found: "분석 프로파일 파일 없음",
        objectives_not_found: "목표 자료 파일 없음" })[code] || "분석 입력 준비 조건 미충족") };
}

function analysisGoalsModel(value, session) {
  const ids = new Set(["tracking", "fms_accel_defaults_v1", "custom_sr_v1", "lateral_torque_kp_v1", "lateral_torque_kf_v1",
    "steer_actuator_delay_v1", "lat_smooth_sec_v1", "long_actuator_delay_v1"]);
  const valid = (item) => item && ids.has(item.id) && item.version === 1 && typeof item.label === "string"
    && item.label.length <= 80 && typeof item.description === "string" && item.description.length <= 400;
  const options = Array.isArray(value?.options) && value.options.length <= 6
    ? value.options.filter(valid).map(({ id, label, description }) => ({ id, label, description })) : [];
  const selected = valid(session.analysis_goal) ? session.analysis_goal : null;
  return { options, selectedId: selected?.id || null, selectedLabel: selected?.label || null };
}

export function createAnalysisControlModel(value) {
  const labels = { recording: "주행 자료 기록 중", queued: "전체 주행 분석 대기", running: "전체 주행 분석 중",
    waiting_for_tail: "마지막 로그 저장 대기", ready: "분석 완료", failed: "분석 실패", incomplete: "분석 자료 일부 부족" };
  const valid = value?.schema_version === 1 && value.mode === "manual_or_park" && value.scope === "trip_since_apply";
  const state = valid && Object.hasOwn(labels, value.state) ? value.state : null;
  const timestamp = (raw) => {
    if (typeof raw === "string" && /^(0|[1-9]\d{0,18})$/.test(raw)) return BigInt(raw);
    return Number.isSafeInteger(raw) && raw >= 0 ? BigInt(raw) : null;
  };
  const start = timestamp(value?.range_start_ns), end = timestamp(value?.range_end_ns);
  const completed = finiteNumber(value?.progress?.completed_sources), total = finiteNumber(value?.progress?.total_sources);
  return { state, label: state ? labels[state] : "분석 실행 상태 대기", canAnalyze: valid && value.can_analyze === true,
    scopeLabel: timestamp(value?.apply_cut_ns) !== null ? "마지막 적용 성공 이후부터 이번 주행 전체" : "이번 주행 전체",
    durationSeconds: start !== null && end !== null && end >= start ? Number((end - start) / 1000000000n) : null,
    progress: completed !== null && total !== null && completed >= 0 && total >= completed ? `${completed} / ${total}개 로그` : null,
    triggerLabel: value?.trigger === "park" ? "P 전환" : value?.trigger === "manual" ? "분석 버튼" : null };
}

export function createControlAdvisorModel(payload = {}) {
  const session = payload?.session && typeof payload.session === "object" ? payload.session : {};
  const capabilities = payload?.capabilities || {};
  const state = typeof session.state === "string" ? session.state : "idle";
  const sourceMode = session.source_mode === "synthetic" ? "synthetic" : session.source_mode === "live" ? "live" : null;
  const parameters = Array.isArray(payload?.parameters) ? payload.parameters : [];
  const parking = parkingModel(session.parking, session.parking_confirmed);
  const candidateReport = createCandidateReportModel(payload.candidate_report, parameters);
  const applyRequests = applyRequestsModel(payload, parameters.map((item) => item?.key));
  candidateReport.items = candidateReport.items.map((item) => ({ ...item, request: requestEligibility(payload, item.key, candidateReport),
    latestRequest: applyRequests.items.find((request) => request.itemKey === item.key) || null }));
  const consumerObservations = createConsumerObservationsModel(session.consumer_observations, parameters.map((item) => item?.key).filter((key) => typeof key === "string"));
  const diagnosticInvalidated = session.diagnostic_invalidated === true || session.diagnostic?.invalidated === true;
  const currentDiagnostic = diagnosticInvalidated ? null : session.diagnostic;
  const summaries = [axisSummary(currentDiagnostic, "lateral"), axisSummary(currentDiagnostic, "longitudinal")];
  const epochCounts = summaries.map((axis) => axis.sampleCount);
  return {
    auth: { authorized: payload?.auth?.authorized === true && payload?.auth?.method === "native_cookie",
      nativeAvailable: payload?.auth?.native_available === true, manualAvailable: payload?.auth?.manual_available === true },
    nativeRuntime: createNativeRuntimeModel(payload.native_runtime),
    sessionId: typeof session.session_id === "string" ? session.session_id : null,
    state, stateLabel: STATE_LABELS[state] || "상태 확인 필요", active: ACTIVE_STATES.has(state),
    sourceMode,
    acceleratedSynthetic: sourceMode === "synthetic" && session.clock_mode === "accelerated_synthetic",
    diagnosticInvalidated,
    partialWindow: !diagnosticInvalidated && session.diagnostic?.partial_window === true,
    recording: createRecordingModel(session.recording),
    consumerObservations,
    candidateReport, applyRequests, calibrationReview: createCalibrationReviewModel(payload), analysisGoals: analysisGoalsModel(payload.analysis_goals, session),
    analysisControl: createAnalysisControlModel(payload.analysis_control || session.analysis_control),
    sourceLabel: sourceMode === "synthetic" ? "합성 데모 · 실제 주행 아님"
      : sourceMode === "live" ? "실제 메시지 경로 · 실기기 검증 전" : "수집 세션 없음",
    controlConfigured: capabilities.control_configured === true,
    liveEnabled: capabilities.live_enabled === true,
    syntheticEnabled: capabilities.synthetic_enabled === true,
    receivedEvents: finiteNumber(session.received_events),
    observations: finiteNumber(session.observations),
    revision: finiteNumber(session.diagnostic_revision),
    lateEvents: finiteNumber(session.late_events), overflowEvents: finiteNumber(session.overflow_events),
    gapEvents: finiteNumber(session.gap_events),
    eligibleCount: sumKnown(summaries, "eligibleCount"),
    excludedCount: sumKnown(summaries, "excludedCount"),
    provisionalCount: sumKnown(summaries, "provisionalCount"),
    diagnosticAvailable: epochCounts.some((count) => count !== null), summaries,
    parkingConfirmed: parking.confirmed, parking,
    lastParking: session.last_park_observation ? parkingModel(session.last_park_observation) : null,
    parkingSummary: parkingSummaryModel(payload.parking_summary), reviewExports: reviewExportsModel(payload),
    historyCount: Array.isArray(session.history) ? session.history.length : 0,
    reason: typeof session.reason === "string" && session.reason
      ? REASON_LABELS[session.reason] || "수집 상태를 확인해 주세요. 진단 자료는 보류될 수 있습니다." : null,
    technicalReason: typeof session.reason === "string" && Object.hasOwn(REASON_LABELS, session.reason) ? session.reason : null,
    parameters: parameters.filter((item) => !payload.analysis_goals?.options?.some((goal) => goal.id === "custom_sr_v1") || CALIBRATION_KEYS.includes(item?.key)).map((item) => {
      const request = requestEligibility(payload, item?.key, candidateReport);
      return {
      key: typeof item?.key === "string" ? item.key : "알 수 없는 항목",
      axis: item?.axis === "longitudinal" ? "longitudinal" : "lateral",
      family: typeof item?.family === "string" ? item.family : "",
      contextOnly: contextOnlyParameter(item),
      currentRaw: finiteNumber(item?.current_raw), currentPhysical: finiteNumber(item?.current_physical),
      unit: typeof item?.physical_unit === "string" ? item.physical_unit : "",
      reason: request.writesVehicle ? "로그에서 계산한 후보가 현재 차량·설정과 연결됐습니다. 저장 요청 뒤 실제 소비값을 확인합니다." : parameterReason(item),
      technicalReason: typeof item?.structural_blocker === "string" ? item.structural_blocker : null,
      requirements: Array.isArray(item?.path_requirements) ? item.path_requirements.filter((value) => typeof value === "string") : [],
      consumerObservations: consumerObservations.entries.filter((entry) => Object.hasOwn(entry.fields, item?.key)).map((entry) => ({
        label: entry.label, fresh: entry.fresh, gap: entry.gap, restarted: entry.restarted,
        consumerStateLabel: entry.stateLabel, ...entry.fields[item?.key],
      })),
      candidate: null, applyEnabled: false,
      request,
      requestCandidate: linkedRequestCandidate(candidateReport, item?.key),
    }; }),
    recommendationsEnabled: false, applyEnabled: false,
  };
}

export function advisorControlState(model, { pending = false, token = "" } = {}) {
  const authorized = model.controlConfigured && (model.auth?.authorized === true || typeof token === "string" && token.trim().length > 0);
  return {
    canStartLive: authorized && model.liveEnabled && !model.active && !pending,
    canStartSynthetic: authorized && model.syntheticEnabled && !model.active && !pending,
    canStop: authorized && model.active && Boolean(model.sessionId) && model.state !== "stopping" && !pending,
    canAnalyze: authorized && Boolean(model.sessionId) && model.analysisControl?.canAnalyze === true && !pending,
    intervalDisabled: model.active || pending,
  };
}
