import { advisorControlState, formatAdvisorBytes, formatAdvisorNumber, formatConsumerValue } from "./model.js";

const AXES = { lateral: "횡방향", longitudinal: "종방향" };
let viewSerial = 0;

function node(document, tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function pair(document, label, value) {
  const row = node(document, "div", "control-advisor__pair");
  row.append(node(document, "span", "control-advisor__muted", label), node(document, "strong", "", value));
  return row;
}

function metric(document, label, value, hook) {
  const box = node(document, "div", "control-advisor__metric");
  box.dataset.caMetric = hook;
  box.append(node(document, "span", "control-advisor__muted", label), node(document, "strong", "", value));
  return box;
}

function registrationButton(document, item, controls, location = "offline") {
  const button = node(document, "button", "btn", item.request?.writesVehicle ? "차량 설정 저장 요청" : item.request?.scope === "pc_simulation" ? "PC 모의 적용 요청" : "적용 요청 등록");
  button.type = "button";
  button.dataset.caRequestItem = item.key;
  button.dataset.caRequestLocation = location;
  button.disabled = !controls.authorized || !item.request?.eligible;
  button.addEventListener("click", () => controls.handlers.onApplyRequest(item.key));
  return button;
}

function parameterCard(document, item, requestControls) {
  const card = node(document, "article", "control-advisor__card");
  card.dataset.caParameter = item.key;
  const heading = node(document, "div", "control-advisor__card-heading");
  heading.append(node(document, "h3", "", item.key), node(document, "span", "control-advisor__tag", item.contextOnly ? "설정 맥락" : AXES[item.axis]));
  const values = node(document, "div", "control-advisor__values");
  const current = item.currentRaw === null ? "—" : formatAdvisorNumber(item.currentRaw, 4);
  values.append(pair(document, "관측 저장값", current), pair(document, "전체 반영 확인", "미확인"));
  if (!item.contextOnly) values.append(pair(document, item.requestCandidate ? "연결된 로그 후보값" : "실시간 추천값",
    item.requestCandidate ? formatAdvisorNumber(item.requestCandidate.candidateRaw, 6) : "—"));
  card.append(heading, values, node(document, "p", "control-advisor__reason", item.reason));
  card.append(pair(document, "요청 등록 조건", item.request?.reason || "현재 후보 연결 근거 미확인"));
  if (item.requestCandidate) {
    const candidate = item.requestCandidate;
    const linked = node(document, "div", "control-advisor__notice control-advisor__values");
    linked.dataset.caLinkedCandidate = item.key;
    linked.append(node(document, "strong", "control-advisor__reason", item.request?.writesVehicle
      ? "연결 확인된 로그 후보 · 명시적 설정 저장 요청 가능" : "연결 확인된 로그 후보 · 실제 차량 저장 조건 미충족"),
      node(document, "p", "control-advisor__muted", candidate.sourceLabel),
      pair(document, "보고서 기준값 → 후보 (저장 단위)", `${formatAdvisorNumber(candidate.currentRaw, 6)} → ${formatAdvisorNumber(candidate.candidateRaw, 6)}`));
    if (candidate.currentPhysical !== null && candidate.candidatePhysical !== null) linked.append(pair(document,
      `물리값 기준 → 후보${candidate.unit ? ` (${candidate.unit})` : ""}`, `${formatAdvisorNumber(candidate.currentPhysical, 6)} → ${formatAdvisorNumber(candidate.candidatePhysical, 6)}`));
    linked.append(registrationButton(document, item, requestControls, "live"));
    card.append(linked);
  }
  if (item.consumerObservations.length) {
    const details = node(document, "details", "control-advisor__details");
    details.dataset.caConsumerDetails = "";
    details.dataset.caDetailKind = "consumers";
    details.append(node(document, "summary", "", `소비 지점별 관측 (${item.consumerObservations.length})`));
    for (const observation of item.consumerObservations) {
      const entry = node(document, "div", "control-advisor__values");
      entry.dataset.caConsumerObservation = "";
      entry.append(node(document, "strong", "", observation.label),
        pair(document, "관측 상태", observation.fresh ? observation.consumerStateLabel : "오래됨·현재값 미확인"),
        pair(document, "값의 의미", observation.stateLabel),
        pair(document, observation.readLabel, formatConsumerValue(observation.readValue)),
        pair(document, observation.effectiveLabel, formatConsumerValue(observation.effectiveValue)),
        pair(document, "값의 출처", observation.sourceLabel),
        node(document, "p", "control-advisor__muted", observation.readStatusLabel));
      if (observation.gap) entry.append(node(document, "p", "control-advisor__reason", "이전 관측과의 공백이 있습니다."));
      if (observation.restarted) entry.append(node(document, "p", "control-advisor__reason", "소비 프로세스가 재시작된 관측입니다."));
      details.append(entry);
    }
    details.append(node(document, "p", "control-advisor__muted", "표시값은 해당 소비 지점의 관측이며 전체 반영 완료 판정이 아닙니다. 저장값과 소비 단위가 다를 수 있습니다."));
    card.append(details);
  } else {
    card.append(node(document, "p", "control-advisor__muted", "소비 지점 값 미관측"));
  }
  if (item.requirements.length || item.technicalReason) {
    const details = node(document, "details", "control-advisor__details");
    details.dataset.caDetailKind = "requirements";
    details.append(node(document, "summary", "", "항목별 근거·조건"));
    if (item.technicalReason) details.append(node(document, "p", "", item.technicalReason));
    const list = node(document, "ul");
    for (const requirement of item.requirements) list.append(node(document, "li", "", requirement));
    details.append(list);
    card.append(details);
  }
  if (!item.contextOnly && !item.request?.writesVehicle) {
    const apply = node(document, "button", "btn control-advisor__apply", "로그 후보·차량 저장 조건 확인 전");
    apply.type = "button";
    apply.disabled = true;
    apply.dataset.caApply = "disabled";
    card.append(apply);
  }
  return card;
}

function offlineCandidatePanel(document, report, connected, requestControls) {
  const section = node(document, "section", "control-advisor__panel control-advisor__notice");
  section.dataset.caCandidateReport = report.state;
  const nativeCandidateReady = report.items.some((item) => item.request?.writesVehicle);
  section.append(node(document, "h3", "", report.stateLabel), node(document, "p", "control-advisor__muted",
    nativeCandidateReady
      ? "로그에서 계산한 후보 보고서입니다. 현재 차량·설정에 연결된 항목은 명시적으로 설정 저장을 요청하고 실제 소비값을 별도로 확인합니다. 이후 관측 근거가 모이면 전후를 비교합니다."
      : "입력 묶음의 무결성을 확인한 로그 후보 보고서입니다. 실제 차량 저장 조건을 충족한 항목만 설정 저장을 요청할 수 있습니다. PC 모의 저장과 진단 추정은 차량 설정 저장이 아닙니다."));
  if (!connected) {
    section.append(node(document, "p", "control-advisor__notice is-error", "서버 연결이 확인되지 않아 결과 최신성을 확인할 수 없습니다. 후보 수치는 표시하지 않습니다."));
    return section;
  }
  if (report.periodic) {
    const periodic = node(document, "div", "control-advisor__values");
    periodic.dataset.caPeriodicStatus = "";
    periodic.append(pair(document, "분석 처리", report.periodic.stateLabel),
      pair(document, "실행 방식", "분석 버튼 또는 P 전환 · 이번 주행 전체"),
      pair(document, "현재 대상 구간 (기기 부팅 시계)", report.periodic.sourceStartSeconds === null || report.periodic.sourceEndSeconds === null ? "범위 미확인"
        : `${formatAdvisorNumber(report.periodic.sourceStartSeconds, 3)}–${formatAdvisorNumber(report.periodic.sourceEndSeconds, 3)}초`),
      pair(document, "분석 기준 시각 (단조 시계)", `${formatAdvisorNumber(report.periodic.cutoffSeconds, 3)}초`),
      pair(document, "현재 대상 원본 수", formatAdvisorNumber(report.periodic.sourceCount)),
      pair(document, "처리 창 / 계산 완료 / 보류 / 실패", `${formatAdvisorNumber(report.periodic.processedWindows)} / ${formatAdvisorNumber(report.periodic.readyWindows)} / ${formatAdvisorNumber(report.periodic.blockedWindows)} / ${formatAdvisorNumber(report.periodic.failedWindows)}`),
      pair(document, "최신 분석 창 (기기 부팅 시계)", report.periodic.latestWindowStartSeconds === null || report.periodic.latestWindowEndSeconds === null ? "범위 미확인"
        : `${formatAdvisorNumber(report.periodic.latestWindowStartSeconds, 3)}–${formatAdvisorNumber(report.periodic.latestWindowEndSeconds, 3)}초`),
      pair(document, "현재 게시 버전", formatAdvisorNumber(report.periodic.publicationRevision)),
      pair(document, "마지막 완료 결과 버전", formatAdvisorNumber(report.periodic.resultRevision)),
      pair(document, "마지막 완료 대상 종료 (기기 부팅 시계)", `${formatAdvisorNumber(report.periodic.resultSourceEndSeconds, 3)}초`),
      node(document, "p", "control-advisor__muted", report.periodic.continuousBatch
        ? "이번 주행 전체를 분석합니다. 설정 적용에 성공했다면 적용 이후부터 분석하며, 원본과 이전 비교 자료는 유지합니다. 시각은 기기 부팅 시계 기준입니다."
        : "대상 시각은 실제 날짜·시각이 아닙니다. 각 완료 세그먼트의 창을 별도로 계산하며 최신 창의 결과는 3분·5분 전체를 통합한 최적값이 아닙니다. 일부 실패·분석 중·최신성 미확인 상태에서는 과거 후보로 대체하지 않습니다."));
    if (report.periodic.epochDurationSeconds !== null) periodic.append(
      pair(document, "최신 설정 구간의 실제 분석 시간", `${formatAdvisorNumber(report.periodic.epochDurationSeconds, 1)}초`));
    if (report.periodic.stale) periodic.append(node(document, "p", "control-advisor__reason", "이전 완료 결과의 최신성을 확인할 수 없어 후보를 보류합니다."));
    if (report.periodic.reasonLabel) periodic.append(node(document, "p", "control-advisor__reason", report.periodic.reasonLabel));
    section.append(periodic);
  }
  if (!report.available) return section;
  section.append(node(document, "strong", "control-advisor__reason", report.sourceLabel),
    node(document, "p", "control-advisor__muted", report.integrityLabel),
    node(document, "p", "control-advisor__notice", nativeCandidateReady
      ? "설정 저장은 추종 성능 개선이나 전체 소비 확인을 뜻하지 않습니다. 관계식 추정·고정 입력 예측의 실제 차량 반응은 아직 검증되지 않았습니다."
      : "실제 차량 반응은 아직 검증되지 않았습니다. 관계식 추정·고정 입력 예측은 추종 성능 개선을 입증하지 않으며, 현재 차량 설정 저장 조건을 충족한 후보가 없습니다."));
  if (report.logLink) {
    const link = report.logLink;
    const provenance = node(document, "article", "control-advisor__card");
    provenance.dataset.caLogLink = link.available ? "available" : "invalid";
    provenance.append(node(document, "h3", "", link.stateLabel));
    if (link.available) provenance.append(pair(document, "세션 연결", link.sessionLabel),
      pair(document, "신호 의미 매핑", link.semanticLabel), pair(document, "원본 보존", link.preservationLabel),
      pair(document, "연결 원본 / 설정 구간", `${formatAdvisorNumber(link.sourceCount)} / ${formatAdvisorNumber(link.epochCount)}`),
      node(document, "p", "control-advisor__muted", link.availabilityLabel),
      node(document, "p", "control-advisor__reason", "해시·추출 연결 확인은 신호 의미 전체, 독립 검증 또는 주행 추천의 증명이 아닙니다."));
    if (link.available) {
      if (link.wholeTrip) provenance.append(pair(document, "전체 원본 확인 / 계산 표본",
        `${formatAdvisorNumber(link.wholeTrip.rawCount)} / ${formatAdvisorNumber(link.wholeTrip.selectedCount)}`),
        pair(document, "계산 자료 선정", link.wholeTrip.policyLabel),
        node(document, "p", "control-advisor__muted", "모든 원본의 제외 조건을 확인하고 주행 전반을 대표하는 자료로 후보를 계산합니다. 원본은 유지합니다."));
      else if (link.sampling) provenance.append(pair(document, "원래 주기 표본 / 후보 계산 표본",
        `${formatAdvisorNumber(link.sampling.rawCount)} / ${formatAdvisorNumber(link.sampling.selectedCount)}`),
        node(document, "p", "control-advisor__muted", "개입·회복 구간은 원래 주기로 구분한 뒤 후보 계산에 5Hz 표본을 사용합니다. 원본과 빠른 제어 변동 기록은 보존합니다."));
      for (const reason of link.reasons) provenance.append(node(document, "p", "control-advisor__muted", reason));
      for (const excluded of link.excludedCounts) provenance.append(pair(document, `제외 근거 · ${excluded.label}`, formatAdvisorNumber(excluded.count)));
    }
    section.append(provenance);
  }
  const details = node(document, "details", "control-advisor__details");
  details.dataset.caOfflineItems = "";
  details.append(node(document, "summary", "", `전체 ${report.items.length}개 항목의 계산 결과·근거 보기`));
  const cards = node(document, "div", "control-advisor__cards");
  for (const item of report.items) {
    const card = node(document, "article", "control-advisor__card");
    card.dataset.caOfflineParameter = item.key;
    card.append(node(document, "h3", "", item.key), node(document, "strong", "control-advisor__reason", item.statusLabel),
      pair(document, "보고서 기준 저장값", formatAdvisorNumber(item.currentRaw, 6)),
      pair(document, `보고서 기준 물리값${item.unit ? ` (${item.unit})` : ""}`, formatAdvisorNumber(item.currentPhysical, 6)));
    if (item.currentSpecial) card.append(node(document, "p", "control-advisor__muted", "특수 저장값입니다. 표시된 물리값이 실제 소비 지점의 사용값과 다를 수 있습니다."));
    if (item.status === "search_candidate" || item.status === "raw_estimate") {
      const estimate = item.status === "raw_estimate";
      const proxy = item.estimateKind === "diagnostic_proxy";
      card.append(pair(document, proxy ? "대리 지표 (변환 전)" : estimate ? "진단 추정값 (저장 단위)" : "후보값 (저장 단위)", formatAdvisorNumber(estimate ? item.rawEstimate : item.candidateRaw, 6)),
        pair(document, `${proxy ? "진단 대리 지표" : estimate ? "물리 추정값" : "물리 후보값"}${item.unit ? ` (${item.unit})` : ""}`,
          formatAdvisorNumber(estimate ? item.physicalEstimate : item.candidatePhysical, 6)));
    }
    card.append(pair(document, "계산 방식", item.predictionLabel));
    for (const reason of item.reasons) card.append(node(document, "p", "control-advisor__reason", reason));
    if (item.status === "raw_estimate") card.append(node(document, "p", "control-advisor__muted", "피팅의 수치 결과이며 독립 보류 검증을 통과한 추천값이 아닙니다. 미제공 점수는 확인되지 않은 근거입니다."));
    if (item.estimateKind === "diagnostic_proxy") card.append(node(document, "p", "control-advisor__reason", "잔차 폭·전체 경로 지연 등의 진단 대리 지표입니다. 이 숫자를 해당 설정값으로 적용할 근거는 없습니다."));
    for (const [label, score, baseline] of [["선택 분할", item.selection, item.baselineSelection], ["보류 검증 분할", item.holdout, item.baselineHoldout]]) {
      const evidence = node(document, "div", "control-advisor__values");
      evidence.append(node(document, "strong", "", label),
        pair(document, "근거 행 / 독립 블록", score ? `${formatAdvisorNumber(score.rows)} / ${formatAdvisorNumber(score.blocks)}` : "—"),
        pair(document, "기준값 / 계산값 RMSE", `${formatAdvisorNumber(baseline?.rmse, 6)} / ${formatAdvisorNumber(score?.rmse, 6)}`),
        pair(document, "최대 절대 오차", formatAdvisorNumber(score?.max_absolute_error, 6)),
        pair(document, "최대 절대 예측값", formatAdvisorNumber(score?.max_absolute_prediction, 6)));
      card.append(evidence);
    }
    card.append(pair(document, "피팅·계산 수치 검사", item.numericalChecksPass ? "통과 · 차량 검증 아님" : "미충족·미확인"),
      pair(document, "분할 독립성", item.splitIndependent ? "보고서 조건 통과" : "미충족·미확인"),
      pair(document, "보류 분할 목적함수", item.holdoutObjectivePass ? "보고서 조건 통과" : "미충족·미확인"),
      pair(document, "독립 주행 재현 / 실기기 검증", "미확인 / 미검증"));
    if (!item.contextOnly && !item.request?.writesVehicle) {
      const apply = node(document, "button", "btn control-advisor__apply", item.request?.scope === "pc_simulation" ? "PC 모의·진단 전용" : "차량 설정 저장 조건 미충족");
      apply.type = "button";
      apply.disabled = true;
      apply.dataset.caOfflineApply = "disabled";
      card.append(apply);
    }
    card.append(registrationButton(document, item, requestControls), node(document, "p", "control-advisor__muted", item.request?.reason || "요청 근거 미확인"));
    if (item.latestRequest) card.append(pair(document, "최근 요청", item.latestRequest.stateLabel));
    cards.append(card);
  }
  details.append(cards);
  section.append(details);
  return section;
}

function requestPanel(document, requests, controls) {
  const section = node(document, "section", "control-advisor__panel control-advisor__notice");
  section.dataset.caApplyRequests = "";
  section.append(node(document, "h3", "", "요청 등록 · 별도 관측 · 비교"),
    node(document, "p", "control-advisor__muted", requests.vehicleWriterAvailable
      ? "차량 설정 쓰기 경로가 준비됐습니다. 명시적인 요청마다 차량·후보·현재값·소비 관측 조건을 다시 확인하며 저장과 실제 사용 확인은 별도입니다."
      : "차량 쓰기 준비 조건이 미충족이면 요청을 보류합니다. PC 모의 요청은 격리 저장소에서만 처리합니다. 저장 영수증과 실제 소비 관측은 별도입니다."));
  if (requests.reason) section.append(node(document, "p", "control-advisor__reason", requests.reason));
  if (requests.items.length && !controls.connected) section.append(node(document, "p", "control-advisor__reason", "서버 연결이 확인되지 않아 아래 기록의 현재 상태를 확인할 수 없습니다."));
  if (!requests.items.length) {
    section.append(node(document, "p", "control-advisor__muted", "등록된 요청이 없습니다."));
    return section;
  }
  const latest = node(document, "p", "control-advisor__reason", `최근 요청: ${requests.items[0].itemKey} · ${requests.items[0].stateLabel}`);
  latest.setAttribute("role", "status");
  section.append(latest);
  const history = node(document, "details", "control-advisor__details");
  history.dataset.caRequestHistory = "";
  history.append(node(document, "summary", "", `최근 요청 ${requests.items.length}개 상태 보기`));
  const cards = node(document, "div", "control-advisor__cards");
  for (const item of requests.items) {
    const card = node(document, "article", "control-advisor__card");
    card.dataset.caRequestRow = item.requestId;
    card.dataset.caRequestCurrent = String(item.current);
    card.append(node(document, "h3", "", item.itemKey), node(document, "strong", "control-advisor__reason", item.scopeLabel),
      pair(document, "요청 상태", item.stateLabel), pair(document, "요청 등록 기록", "확인됨"),
      pair(document, item.scope === "vehicle" ? "차량 설정 저장 영수증" : "PC 모의 저장 영수증", item.storedRevision === null ? "미확인" : `버전 ${formatAdvisorNumber(item.storedRevision)} · 값 ${formatAdvisorNumber(item.storedValue, 4)}`),
      pair(document, "별도 소비 관측", item.consumerObserved ? "근거 확인 · 전체 반영 ACK 아님" : "미확인"),
      pair(document, "관측 비교", item.comparisonReady ? "준비됨 · 인과적 개선 미입증" : item.current ? "후속 근거 대기" : "현재 상태 미확인"));
    if (!item.current) card.append(node(document, "strong", "control-advisor__reason", "이전 결과 · 현재 상태 미확인"),
      pair(document, "마지막 기록 상태", item.recordedStateLabel));
    if (item.reason) card.append(node(document, "p", "control-advisor__reason", item.reason));
    if (item.canCancel) {
      const cancel = node(document, "button", "btn", "의도 기록 전 취소 요청");
      cancel.type = "button";
      cancel.dataset.caCancelRequest = item.requestId;
      cancel.disabled = !controls.authorized;
      cancel.addEventListener("click", () => controls.handlers.onCancelRequest(item.requestId));
      card.append(cancel);
    } else if (["intent", "indeterminate"].includes(item.state)) {
      card.append(node(document, "p", "control-advisor__muted", "저장 의도 기록 뒤에는 취소를 확정하거나 자동으로 되돌리지 않습니다. 상태와 별도 근거를 다시 확인합니다."));
    }
    if (item.canRestore) {
      const restore = node(document, "button", "btn", item.restoreMode === "offroad" ? "원복 저장 · 다음 시동 반영" : "이 요청 직전 값으로 원복 요청");
      restore.type = "button"; restore.dataset.caVehicleRestore = item.requestId; restore.disabled = !controls.authorized;
      restore.addEventListener("click", () => controls.handlers.onRestoreVehicle(item.requestId, item.restoreMode));
      card.append(restore, node(document, "p", "control-advisor__muted", "현재 값이 이 요청의 변경값과 같을 때만 직전 값으로 되돌립니다. 자동 원복하지 않습니다."));
    }
    cards.append(card);
  }
  history.append(cards);
  section.append(history);
  return section;
}

function comparisonCard(document, request) {
  const comparison = request.comparison;
  const section = node(document, "section", "control-advisor__panel");
  section.dataset.caRequestComparison = "";
  section.dataset.caRequestCurrent = String(request.current);
  section.append(node(document, "h3", "", `${request.itemKey} · ${!request.current ? "이전 관측 비교" : comparison.state === "held" ? "비교 근거 보류" : "요청 관측 비교"}`),
    node(document, "strong", "control-advisor__reason", request.scopeLabel),
    node(document, "strong", "control-advisor__reason", comparison.scopeLabel),
    node(document, "p", "control-advisor__notice", "같은 조건으로 맞춘 구간의 관측 차이입니다. 전체 반영 ACK, 인과적 주행 개선 또는 실기기 검증을 뜻하지 않습니다."));
  if (!request.current) section.append(node(document, "strong", "control-advisor__notice is-error", "이전 결과 · 현재 상태 미확인"),
    node(document, "p", "control-advisor__muted", "아래 지표는 마지막으로 받은 비교 기록입니다. 현재 소비 관측과 비교 준비 상태는 확인되지 않았습니다."));
  const summaries = node(document, "div", "control-advisor__phases");
  for (const [title, summary] of [["변경 전 근거", comparison.before], ["변경 후 근거", comparison.after]]) {
    const card = node(document, "article", "control-advisor__card");
    card.append(node(document, "h3", "", title), pair(document, "사용 / 제외 / 잠정", `${formatAdvisorNumber(summary?.eligibleCount)} / ${formatAdvisorNumber(summary?.excludedCount)} / ${formatAdvisorNumber(summary?.provisionalCount)}`),
      pair(document, "사용 시간", `${formatAdvisorNumber(summary?.eligibleSeconds, 3)}초`),
      pair(document, "원시 RMSE / 사용 RMSE", `${formatAdvisorNumber(summary?.raw?.rmse, 4)} / ${formatAdvisorNumber(summary?.eligible?.rmse, 4)}`),
      pair(document, "사용 편향 / p95 절대오차", `${formatAdvisorNumber(summary?.eligible?.bias, 4)} / ${formatAdvisorNumber(summary?.eligible?.p95, 4)}`));
    if (summary?.detailPages !== null && summary?.detailPages !== undefined) {
      card.append(pair(document, "제외 구간 상세", `${formatAdvisorNumber(summary.detailIntervals)}개 · ${formatAdvisorNumber(summary.detailPages)}개 파일`),
        node(document, "p", "control-advisor__muted", "전체 제외 구간은 로그·그래프 내보내기 묶음에 포함됩니다."));
    }
    summaries.append(card);
  }
  section.append(summaries, pair(document, "공통 조건 묶음", formatAdvisorNumber(comparison.matched?.commonBins)),
    pair(document, "맞춘 RMSE (전 / 후)", `${formatAdvisorNumber(comparison.matched?.before?.rmse, 4)} / ${formatAdvisorNumber(comparison.matched?.after?.rmse, 4)}`),
    pair(document, "후−전 RMSE 관측 차이 (m/s²)", formatAdvisorNumber(comparison.matched?.deltaRmse, 4)),
    pair(document, "후−전 편향 / p95 관측 차이 (m/s²)", `${formatAdvisorNumber(comparison.matched?.deltaBias, 4)} / ${formatAdvisorNumber(comparison.matched?.deltaP95, 4)}`));
  for (const reason of comparison.reasons) section.append(node(document, "p", "control-advisor__reason", reason));
  return section;
}

function nativeRuntimePanel(document, model, ui, handlers, authorized) {
  const value = model.nativeRuntime;
  const panel = node(document, "section", "control-advisor__panel");
  panel.dataset.caNativeRuntime = "";
  if (!value.available) { panel.hidden = true; return panel; }
  panel.append(node(document, "h3", "", ui.connected ? value.stateLabel : "이전 기기 상태 · 현재 연결 미확인"), pair(document, "현재 차량 구성", value.fingerprint),
    pair(document, "차량 연결 정보", value.vinLabel), pair(document, "물리 차량 기준", value.confirmed ? `확인됨 · ${(value.groupId || "").slice(0, 8)}` : "직접 확인 필요"),
    pair(document, "설정 저장 경로", !ui.connected ? "현재 준비 상태 미확인" : value.writerAvailable ? "준비됨 · 요청 시 조건 재확인" : "준비 조건 미충족"));
  if (value.reason) panel.append(node(document, "p", "control-advisor__reason", value.reason));
  for (const reason of value.analysisBlockers) panel.append(node(document, "p", "control-advisor__reason", reason));
  const label = node(document, "label", "control-advisor__field", "동일 물리 차량의 기준 선택");
  const select = node(document, "select"); select.dataset.caVehicleGroup = "";
  const initial = node(document, "option", "", "첫 연결 또는 현재 차량 기준 확인"); initial.value = ""; select.append(initial);
  const fresh = node(document, "option", "", "새 차량으로 등록"); fresh.value = "new"; select.append(fresh);
  for (const group of value.groups) { const option = node(document, "option", "", `${group.label} · 기준 ${group.id.slice(0, 8)}`); option.value = group.id; select.append(option); }
  select.value = ui.vehicleGroup || ""; select.disabled = ui.pending;
  select.addEventListener("change", () => handlers.onVehicleGroup(select.value)); label.append(select);
  const confirm = node(document, "button", "btn", "이 물리 차량의 기준으로 확인");
  confirm.type = "button"; confirm.dataset.caVehicleConfirm = ""; confirm.disabled = !authorized;
  confirm.addEventListener("click", () => handlers.onConfirmVehicle());
  const backup = node(document, "button", "btn", "차량 설정 기준 백업 다운로드");
  backup.type = "button"; backup.dataset.caVehicleBackup = ""; backup.disabled = !authorized || !value.writerAvailable;
  backup.addEventListener("click", () => handlers.onBackupVehicle());
  panel.append(label, confirm, backup, node(document, "p", "control-advisor__notice", "차량 구성이 같아도 다른 물리 차량일 수 있습니다. 동일 차량의 기존 기준만 선택하세요. 최초 설정 저장 전에 기준을 보존하며, 브랜치 변경은 설정 원복이 아닙니다. 백업에는 민감한 설정 정보가 포함될 수 있습니다."));
  if (ui.lastRestore) panel.append(node(document, "strong", "control-advisor__reason", ui.lastRestore.stored
    ? ui.lastRestore.mode === "offroad" ? "원복 저장 확인 · 다음 시동의 소비·반영 확인 필요" : "직전 값 원복 저장 확인 · 실제 소비·반영은 아직 미확인"
    : "원복 결과 미확인 · 자동 재쓰기 없음"));
  return panel;
}

function parkingReviewPanel(document, model, ui, handlers, authorized, renderDiagnostic) {
  const section = node(document, "section", "control-advisor__panel");
  section.dataset.caParkingReview = "";
  section.append(node(document, "h3", "", ui.connected ? model.parking.stateLabel : "연결 미확인 · 현재 주차 여부 미확인"),
    pair(document, "현재 기기 상태", ui.connected ? model.parking.offroadLabel : "현재 offroad 미확인"),
    node(document, "p", "control-advisor__muted", model.parking.synthetic ? "합성 주차 신호 · 실제 차량 주차 확인 아님" : "주차 신호와 offroad는 별도입니다. 주차 판정 프로파일은 실기기 검증 전입니다."));
  if (model.lastParking) section.append(node(document, "p", "control-advisor__muted", "이전 주차 신호 기록이 있습니다. 현재 주차 확인으로 대신하지 않습니다."));
  const summary = model.parkingSummary;
  if (summary.available) {
    const card = node(document, "article", "control-advisor__card");
    card.dataset.caFrozenSummary = "";
    card.append(node(document, "h3", "", summary.stateLabel), node(document, "strong", "control-advisor__reason", summary.sourceLabel),
      pair(document, "불변 요약 버전", formatAdvisorNumber(summary.revision)), pair(document, "수집 종료 확인 범위", summary.endLabel),
      pair(document, "진단 확정 범위 (단조 시계)", summary.finalizedRange), pair(document, "잠정 원시 꼬리 범위 (단조 시계)", summary.rawTailRange),
      pair(document, "요청 목록 범위", summary.requestInventoryLabel),
      pair(document, "저장 기록 / 별도 소비 관측 / 비교 근거", `${formatAdvisorNumber(summary.storedCount)} / ${formatAdvisorNumber(summary.observedCount)} / ${formatAdvisorNumber(summary.comparisonCount)}`),
      pair(document, "후보 계산 기록", `${formatAdvisorNumber(summary.candidateCount)}개 · 과거 기록이며 현재 추천 아님`),
      pair(document, "Codex 심층 검토", model.reviewExports.jobs.some((job) => job.summaryId === summary.summaryId
        && job.summaryRevision === summary.revision && job.reviewCount > 0) ? "이 요약에 연결된 검토 기록 있음 · 신원 미확인" : "연결된 검토 기록 미확인"));
    if (!summary.current || !ui.connected) card.append(node(document, "strong", "control-advisor__reason", "이전 불변 요약 · 현재 갱신 상태 미확인"));
    card.append(node(document, "p", "control-advisor__notice", "요약과 확정 범위는 전체 원본 보존·설정 반영·실차 검증 완료를 뜻하지 않습니다. 겹치는 과거 진단을 합산하지 않았습니다."));
    for (const omission of summary.omissions) card.append(node(document, "p", "control-advisor__reason", omission));
    const axes = node(document, "div", "control-advisor__phases");
    for (const axis of summary.axes) {
      const item = node(document, "article", "control-advisor__card");
      const epoch = axis.epochs.length === 1 ? axis.epochs[0] : null;
      item.append(node(document, "h3", "", `${AXES[axis.axis]} · 최근 진단만`),
        pair(document, "관측 / 사용 / 제외 / 잠정", `${formatAdvisorNumber(axis.sampleCount)} / ${formatAdvisorNumber(axis.eligibleCount)} / ${formatAdvisorNumber(axis.excludedCount)} / ${formatAdvisorNumber(axis.provisionalCount)}`),
        pair(document, "사용 시간", `${formatAdvisorNumber(axis.eligibleSeconds, 3)}초`),
        pair(document, "원시 / 사용 RMSE (m/s²)", `${formatAdvisorNumber(epoch?.raw?.rmse, 4)} / ${formatAdvisorNumber(epoch?.eligible?.rmse, 4)}`));
      if (axis.epochs.length > 1) item.append(node(document, "p", "control-advisor__muted", "여러 설정 구간의 오차를 합산하지 않았습니다."));
      axes.append(item);
    }
    card.append(axes);
    section.append(card);
  } else section.append(node(document, "p", "control-advisor__notice", `${summary.stateLabel} · 아래는 현재 받은 진단의 임시 통계입니다.`), renderDiagnostic(model));
  const exports = node(document, "article", "control-advisor__card");
  exports.dataset.caReviewExports = "";
  exports.append(node(document, "h3", "", "Codex 검토용 로그·분석 묶음"), node(document, "p", "control-advisor__muted", model.reviewExports.stateLabel),
    node(document, "p", "control-advisor__notice", "묶음을 기기에 저장하고 다운로드합니다. Codex나 외부 서비스로 자동 전송하지 않습니다. 원본을 제외해도 설정·차량·식별정보가 포함될 수 있으며 비식별화를 보장하지 않습니다."));
  const rawLabel = node(document, "label", "control-advisor__muted");
  const raw = node(document, "input");
  raw.type = "checkbox";
  raw.checked = ui.includeRaw === true;
  raw.disabled = ui.pending;
  raw.dataset.caExportRaw = "";
  raw.addEventListener("change", () => handlers.onExportRaw(raw.checked));
  rawLabel.append(raw, document.createTextNode(" 민감 원본 로그 포함 요청 (확보된 닫힌 사본만)"));
  const create = node(document, "button", "btn", "검토용 묶음 만들기");
  create.type = "button";
  create.dataset.caExportCreate = "";
  create.disabled = !authorized || !model.reviewExports.enabled || !summary.available;
  create.addEventListener("click", () => handlers.onCreateExport());
  exports.append(rawLabel, create, node(document, "p", "control-advisor__muted", "실패·취소·중단 때 부분 파일이 남을 수 있습니다. 원본과 부분 결과는 자동 삭제하지 않습니다."));
  for (const job of model.reviewExports.jobs) {
    const item = node(document, "article", "control-advisor__card");
    item.dataset.caExportJob = job.jobId;
    item.append(node(document, "strong", "", job.stateLabel), pair(document, "민감 원본 포함 선택", job.includeRaw ? "포함 요청함 · 실제 포함 범위는 묶음 목록 참조" : "포함 요청하지 않음"));
    if (job.reasonLabel) item.append(node(document, "p", "control-advisor__reason", job.reasonLabel));
    if (job.state === "ready") item.append(pair(document, "파일 수 / ZIP 크기", `${formatAdvisorNumber(job.files)} / ${formatAdvisorBytes(job.zipBytes)}`));
    if (job.canCancel) {
      const cancel = node(document, "button", "btn", job.state === "cancelling" ? "취소 확인 중" : "내보내기 취소 요청");
      cancel.type = "button";
      cancel.dataset.caExportCancel = job.jobId;
      cancel.disabled = !authorized || job.state === "cancelling";
      cancel.addEventListener("click", () => handlers.onCancelExport(job.jobId));
      item.append(cancel);
    }
    if (job.canDownload) {
      const download = node(document, "button", "btn", "ZIP 다운로드");
      download.type = "button";
      download.dataset.caExportDownload = job.jobId;
      download.disabled = !authorized || !model.reviewExports.enabled;
      download.addEventListener("click", () => handlers.onDownloadExport(job.jobId));
      item.append(download);
    }
    item.append(reviewResultsPanel(document, job, ui, handlers, authorized, model.reviewExports.enabled));
    exports.append(item);
  }
  section.append(exports);
  return section;
}

function reviewResultsPanel(document, job, ui, handlers, authorized, exportEnabled) {
  const section = node(document, "section", "control-advisor__panel");
  section.dataset.caCodexReviews = job.jobId;
  section.append(node(document, "h3", "", "Codex 검토 결과 연결"),
    pair(document, "대상 요약 버전", formatAdvisorNumber(job.summaryRevision)),
    pair(document, "등록 검토 수 / 최근 검토 버전", `${formatAdvisorNumber(job.reviewCount)} / ${formatAdvisorNumber(job.latestReviewRevision)}`),
    node(document, "p", "control-advisor__notice", "이 묶음을 검토한 final JSON 한 개만 선택해 등록합니다. 검토자의 모델·신원은 자기 진술이며 해시 일치는 신원이나 차량 적용 자격을 보증하지 않습니다. 자동 분석 호출·외부 전송·설정 변경은 없습니다."));
  if (job.reviewUnavailable) section.append(node(document, "strong", "control-advisor__reason", "검토 이력 상태 미확인"));
  const label = node(document, "label", "control-advisor__field", "검토 JSON 선택 (128KiB 이하) ");
  const file = node(document, "input");
  file.type = "file"; file.accept = ".json,application/json"; file.dataset.caReviewFile = job.jobId;
  file.disabled = ui.pending || !job.canDownload;
  file.addEventListener("change", () => handlers.onReviewFile(job.jobId, file.files?.[0]));
  label.append(file);
  const selection = ui.reviewFiles?.[job.jobId];
  const register = node(document, "button", "btn", "선택한 검토 JSON 등록");
  register.type = "button"; register.dataset.caReviewRegister = job.jobId;
  register.disabled = !authorized || !exportEnabled || !job.canDownload || selection?.ready !== true;
  register.addEventListener("click", () => handlers.onRegisterReview(job.jobId));
  const query = node(document, "button", "btn", "검토 이력 조회");
  query.type = "button"; query.dataset.caReviewQuery = job.jobId; query.disabled = !authorized;
  query.addEventListener("click", () => handlers.onQueryReviews(job.jobId));
  section.append(label, node(document, "p", "control-advisor__muted", selection ? `선택됨: ${selection.name} · 등록은 별도 버튼` : "선택한 검토 JSON 없음"), register, query);
  const history = job.history;
  if (!history.available) return section;
  if (!history.current || !ui.connected) section.append(node(document, "strong", "control-advisor__reason", "이전 조회 결과 · 현재 이력 상태 미확인"));
  section.append(node(document, "p", "control-advisor__reason", history.bindingVerified
    ? "이 완료 묶음과 검토의 해시 연결 확인 · 과거 검토 기록" : "기록된 과거 검토 · 현재 묶음 연결 재확인 불가"));
  if (!history.items.length) { section.append(node(document, "p", "", "등록된 검토 없음")); return section; }
  const selector = node(document, "select");
  selector.dataset.caReviewSelect = job.jobId; selector.setAttribute("aria-label", "표시할 검토 버전");
  for (const entry of history.items) {
    const option = node(document, "option", "", `검토 ${entry.revision} · ${entry.model} · ${entry.verdictLabel}`);
    option.value = entry.hash; selector.append(option);
  }
  const selected = history.items.find((entry) => entry.hash === ui.selectedReviews?.[job.jobId]) || history.items.at(-1);
  selector.value = selected.hash;
  selector.addEventListener("change", () => handlers.onSelectReview(job.jobId, selector.value));
  const selectLabel = node(document, "label", "control-advisor__field", "표시할 검토 버전");
  selectLabel.append(selector);
  section.append(selectLabel, pair(document, "요약 버전 / 검토 버전", `${selected.summaryRevision} / ${selected.revision}`),
    pair(document, "자기 진술 도구 / 모델", `${selected.tool} / ${selected.model}`), pair(document, "자기 진술 검토 시각", selected.reviewedAt),
    pair(document, "검토 의견", selected.verdictLabel));
  for (const version of selected.versions) section.append(pair(document, `${version.key} 버전 근거`, version.label));
  for (const [title, values] of [["검토 소견", selected.findings], ["미검토 범위", selected.unreviewed], ["불확실성", selected.uncertainties], ["추가 확인", selected.followUp]]) {
    section.append(node(document, "h4", "", title));
    for (const text of values) section.append(node(document, "p", "control-advisor__muted", text));
    if (!values.length) section.append(node(document, "p", "control-advisor__muted", "명시된 항목 없음"));
  }
  return section;
}

export function createControlAdvisorView(container, handlers) {
  const document = container.ownerDocument;
  const id = `controlAdvisorView${++viewSerial}`;
  const root = node(document, "section", "control-advisor");
  root.dataset.caRoot = "";
  const status = node(document, "div", "control-advisor__status");
  const state = node(document, "span", "control-advisor__tag");
  state.dataset.caState = "";
  const source = node(document, "strong", "control-advisor__source");
  source.dataset.caSource = "";
  status.append(state, source);
  const connection = node(document, "p", "control-advisor__notice");
  connection.dataset.caConnection = "";
  connection.setAttribute("role", "status");
  const controls = node(document, "section", "control-advisor__controls");
  const keyLabel = node(document, "label", "control-advisor__field", "세션 키");
  const token = node(document, "input");
  token.type = "password";
  token.autocomplete = "off";
  token.spellcheck = false;
  token.dataset.caToken = "";
  token.placeholder = "로컬에서 설정한 세션 키";
  keyLabel.append(token);
  const analysisStatus = node(document, "p", "control-advisor__muted");
  analysisStatus.dataset.caAnalysisStatus = "";
  analysisStatus.setAttribute("role", "status");
  const goalLabel = node(document, "label", "control-advisor__field control-advisor__goal", "분석 목표");
  const goal = node(document, "select");
  goal.dataset.caGoal = "";
  goalLabel.append(goal);
  const goalNote = node(document, "p", "control-advisor__muted");
  goalNote.dataset.caGoalNote = "";
  let goalOptionsSignature = null;
  const actions = node(document, "div", "control-advisor__actions");
  const startLive = node(document, "button", "btn btn--filled", "실제 수신 시작");
  const startSynthetic = node(document, "button", "btn", "합성 데모 시작");
  const stop = node(document, "button", "btn", "수집 종료");
  const analyze = node(document, "button", "btn btn--filled", "이번 주행 분석");
  for (const [button, action] of [[startLive, "live"], [startSynthetic, "synthetic"], [stop, "stop"], [analyze, "analyze"]]) {
    button.type = "button";
    button.dataset.caAction = action;
    actions.append(button);
  }
  controls.append(keyLabel, goalLabel, actions);
  const controlsNote = node(document, "p", "control-advisor__muted");
  const offline = node(document, "div");
  const tabs = node(document, "div", "control-advisor__tabs c-segmented-control c-segmented-control--equal");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "제어 분석 화면");
  const panel = node(document, "section", "control-advisor__panel");
  panel.id = `${id}Panel`;
  panel.setAttribute("role", "tabpanel");
  let selectedTab = "live";
  let panelSignature = null;
  const tabButtons = [];
  for (const [value, label] of [["live", "실시간 추천"], ["compare", "적용 전후 비교"], ["parking", "주차 요약"]]) {
    const button = node(document, "button", "c-segmented-control__item", label);
    button.type = "button";
    button.id = `${id}Tab${value}`;
    button.dataset.caTab = value;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", panel.id);
    button.addEventListener("click", () => handlers.onTab(value));
    tabButtons.push(button);
    tabs.append(button);
  }
  tabs.addEventListener("keydown", (event) => {
    const index = tabButtons.indexOf(document.activeElement);
    if (index < 0) return;
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabButtons.length;
    else if (event.key === "ArrowLeft") next = (index + tabButtons.length - 1) % tabButtons.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabButtons.length - 1;
    else return;
    event.preventDefault();
    tabButtons[next].focus();
    handlers.onTab(tabButtons[next].dataset.caTab);
  });
  const footer = node(document, "p", "control-advisor__muted", "화면을 닫아도 수집 세션은 유지됩니다. 수집 종료 버튼으로 종료해 주세요. 설정 자동 적용은 없습니다.");
  root.append(status, connection, controls, goalNote, analysisStatus, controlsNote, offline, tabs, panel, footer);
  container.replaceChildren(root);
  token.addEventListener("input", () => handlers.onToken(token.value));
  goal.addEventListener("change", () => handlers.onGoal(goal.value));
  startLive.addEventListener("click", () => handlers.onStart("live"));
  startSynthetic.addEventListener("click", () => handlers.onStart("synthetic"));
  stop.addEventListener("click", () => handlers.onStop());
  analyze.addEventListener("click", () => handlers.onAnalyze());

  function renderDiagnostic(model) {
    const metrics = node(document, "div", "control-advisor__metrics");
    metrics.dataset.caMetrics = "";
    for (const [label, value, hook] of [["수신 메시지", model.receivedEvents, "received"], ["사용 관측", model.eligibleCount, "eligible"],
      ["제외 관측", model.excludedCount, "excluded"], ["잠정 관측", model.provisionalCount, "provisional"],
      ["늦은 도착", model.lateEvents, "late"], ["공백 의심", model.gapEvents, "gap"], ["수신 한도 초과", model.overflowEvents, "overflow"],
      ["결과 버전", model.revision, "revision"]]) metrics.append(metric(document, label, formatAdvisorNumber(value), hook));
    return metrics;
  }

  function renderRecording(model) {
    const recording = model.recording;
    const section = node(document, "section", "control-advisor__panel");
    section.dataset.caRecording = "";
    section.append(node(document, "h3", "", "설정 관측 · 원본 보존"),
      node(document, "p", "control-advisor__muted", recording.stateLabel));
    const cards = node(document, "div", "control-advisor__phases");
    const settings = node(document, "article", "control-advisor__card");
    settings.dataset.caSettings = "";
    const setting = recording.settings;
    settings.append(node(document, "h3", "", setting.stateLabel),
      pair(document, "관측 버전", formatAdvisorNumber(setting.revision)),
      pair(document, "기록된 버전", formatAdvisorNumber(setting.durableRevision)),
      pair(document, "설정 관측 기록", setting.journalStateLabel),
      pair(document, "감시 공백", setting.gap ? "있음 · 이력 불확실" : setting.gapKnown ? "이번 관측에서 미검출" : "미확인"),
      pair(document, model.sourceMode === "synthetic" ? "합성 시간 기준 읽기 폭" : "관측 읽기 폭", `${formatAdvisorNumber(setting.observationWidthMs, 3)} ms`),
      pair(document, "변경 시점 불확실 폭", setting.changeObserved ? setting.changeUncertaintyMs === null ? "시작 시점 미확인"
        : `${formatAdvisorNumber(setting.changeUncertaintyMs, 3)} ms` : "변경 미관측"),
      node(document, "p", "control-advisor__muted", setting.historyLabel),
      node(document, "p", "control-advisor__muted", `${setting.consumerLabel}. 여러 키를 한 시점의 스냅샷으로 보장하지 않습니다.`));
    const preservation = node(document, "article", "control-advisor__card");
    preservation.dataset.caPreservation = "";
    const raw = recording.preservation;
    preservation.append(node(document, "h3", "", raw.stateLabel),
      pair(document, "검증된 닫힌 사본", `${formatAdvisorNumber(raw.verifiedSegments)}개`),
      pair(document, "검증된 사본 크기", formatAdvisorBytes(raw.verifiedBytes)),
      pair(document, "보존 대기 작업", formatAdvisorNumber(raw.pendingJobs)),
      pair(document, "보존 실패", formatAdvisorNumber(raw.failedSegments)),
      pair(document, "원본 미포함 진단 구간", formatAdvisorNumber(raw.uncoveredWindows)),
      pair(document, "보존 이력 기록", raw.journalCommitted ? "확인됨" : "미확정"),
      pair(document, "보존 검사 대상 진단 버전", formatAdvisorNumber(raw.latestDiagnosticRevision)),
      node(document, "p", "control-advisor__muted", raw.coverageLabel));
    cards.append(settings, preservation);
    section.append(cards);
    const consumers = model.consumerObservations;
    const consumerCard = node(document, "article", "control-advisor__card");
    consumerCard.dataset.caConsumerStatus = "";
    consumerCard.append(node(document, "h3", "", consumers.stateLabel),
      node(document, "strong", "", consumers.sourceLabel),
      pair(document, "신선한 관측 지점", formatAdvisorNumber(consumers.freshConsumers)),
      pair(document, "수신 관측", formatAdvisorNumber(consumers.receivedEvents)),
      pair(document, "거절된 관측", formatAdvisorNumber(consumers.rejectedEvents)),
      pair(document, "관측 누락", formatAdvisorNumber(consumers.droppedEvents)),
      pair(document, "재시작 감지", formatAdvisorNumber(consumers.restarts)),
      pair(document, "관측 이력", consumers.persistence.stateLabel),
      pair(document, "기록된 관측", formatAdvisorNumber(consumers.persistence.recordedEvents)),
      pair(document, "미기록 관측", formatAdvisorNumber(consumers.persistence.droppedEvents)),
      node(document, "p", "control-advisor__muted", "소비 지점별 읽기·사용값을 관측합니다. 생성자 고정·우회·오래된 관측을 구분하며 전체 반영 완료나 실기기 검증을 뜻하지 않습니다."));
    section.append(consumerCard);
    return section;
  }

  function update(model, ui) {
    state.textContent = model.stateLabel;
    root.dataset.caSourceMode = model.sourceMode || "none";
    source.textContent = model.sourceLabel;
    connection.textContent = ui.actionError || ui.error || (ui.connected
      ? model.nativeRuntime.writerAvailable ? "서버 연결됨 · 설정 저장 요청 가능" : "서버 연결됨 · 읽기 전용 진단"
      : "분석 상태를 확인하고 있습니다.");
    connection.classList.toggle("is-error", Boolean(ui.actionError || ui.error));
    const actionState = advisorControlState(model, { pending: ui.pending || !ui.connected || Boolean(ui.error), token: token.value });
    startLive.disabled = !actionState.canStartLive;
    startSynthetic.disabled = !actionState.canStartSynthetic;
    stop.disabled = !actionState.canStop;
    analyze.disabled = !actionState.canAnalyze;
    analysisStatus.textContent = [model.analysisControl.label, model.analysisControl.scopeLabel,
      model.analysisControl.progress, "분석 버튼 또는 P 전환 시 실행 · 설정은 수동 적용"].filter(Boolean).join(" · ");
    goalLabel.hidden = model.analysisGoals.options.length === 0;
    goalNote.hidden = goalLabel.hidden;
    const nextGoals = JSON.stringify(model.analysisGoals.options);
    if (goalOptionsSignature !== nextGoals) {
      goal.replaceChildren();
      for (const item of model.analysisGoals.options) {
        const option = node(document, "option", "", item.label);
        option.value = item.id; goal.append(option);
      }
      goalOptionsSignature = nextGoals;
    }
    goal.value = ui.goalId;
    goal.disabled = ui.pending;
    goalNote.textContent = model.analysisGoals.options.find((item) => item.id === goal.value)?.description || "";
    token.disabled = ui.pending;
    controlsNote.textContent = model.auth.authorized ? "이 브라우저의 기기 연결 권한이 확인됐습니다. 설정 저장·소비 확인은 항목별 상태를 확인해 주세요."
      : model.auth.nativeAvailable ? "기기 화면의 연결 QR로 이 브라우저를 연결해 주세요. 수동 세션 키도 사용할 수 있습니다."
      : !model.controlConfigured
      ? "세션 제어가 아직 허용되지 않았습니다. 현재 상태와 항목 목록을 볼 수 있습니다."
      : model.acceleratedSynthetic ? "합성 시계가 가속되어 진행됩니다. 실제 주행 자료가 아닙니다. 세션 키는 이 화면에서만 사용합니다."
        : "세션 키는 이 화면에서만 사용합니다. 합성 데모는 실제 주행 자료가 아닙니다.";
    selectedTab = ui.tab;
    for (const button of tabButtons) {
      const selected = button.dataset.caTab === selectedTab;
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected) panel.setAttribute("aria-labelledby", button.id);
    }
    const signature = JSON.stringify([ui.tab, ui.axis, ui.connected, ui.pending, ui.includeRaw, ui.reviewFiles, ui.selectedReviews, ui.lastRestore, token.value.trim().length > 0, model]);
    if (signature === panelSignature) return;
    panelSignature = signature;
    const offlineExpanded = offline.querySelector("[data-ca-offline-items]")?.open === true;
    const requestsExpanded = offline.querySelector("[data-ca-request-history]")?.open === true;
    const focusedOffline = document.activeElement;
    const offlineFocused = focusedOffline?.parentElement?.hasAttribute?.("data-ca-offline-items") && focusedOffline?.tagName === "SUMMARY";
    const requestsFocused = focusedOffline?.parentElement?.hasAttribute?.("data-ca-request-history") && focusedOffline?.tagName === "SUMMARY";
    const requestFocusKey = focusedOffline?.dataset?.caRequestLocation === "offline" ? focusedOffline.dataset.caRequestItem : null;
    const cancelFocusId = focusedOffline?.dataset?.caCancelRequest;
    const requestControls = { handlers, connected: ui.connected, authorized: ui.connected && !ui.pending && model.controlConfigured
      && model.applyRequests.registrationEnabled && (model.auth.authorized || token.value.trim().length > 0) };
    offline.replaceChildren(nativeRuntimePanel(document, model, ui, handlers, ui.connected && !ui.pending && model.controlConfigured
      && (model.auth.authorized || token.value.trim().length > 0)), requestPanel(document, model.applyRequests, requestControls),
      offlineCandidatePanel(document, model.candidateReport, ui.connected, requestControls));
    if (offlineExpanded) offline.querySelector("[data-ca-offline-items]")?.setAttribute("open", "");
    if (requestsExpanded) offline.querySelector("[data-ca-request-history]")?.setAttribute("open", "");
    if (offlineFocused) offline.querySelector("[data-ca-offline-items] summary")?.focus({ preventScroll: true });
    if (requestsFocused) offline.querySelector("[data-ca-request-history] summary")?.focus({ preventScroll: true });
    if (requestFocusKey) [...offline.querySelectorAll("[data-ca-request-item]")].find((button) => button.dataset.caRequestItem === requestFocusKey)?.focus({ preventScroll: true });
    if (cancelFocusId) [...offline.querySelectorAll("[data-ca-cancel-request]")].find((button) => button.dataset.caCancelRequest === cancelFocusId)?.focus({ preventScroll: true });
    const expanded = new Set(Array.from(panel.querySelectorAll("[data-ca-parameter] details[open]"), (details) => JSON.stringify([
      details.closest("[data-ca-parameter]").dataset.caParameter, details.dataset.caDetailKind])));
    const focused = document.activeElement;
    const focusExportRaw = focused?.hasAttribute?.("data-ca-export-raw");
    const liveRequestFocus = focused?.dataset?.caRequestLocation === "live" ? focused.dataset.caRequestItem : null;
    const focusAxis = focused?.hasAttribute?.("data-ca-axis");
    const focusParameter = focused?.tagName === "SUMMARY" ? focused.closest("[data-ca-parameter]")?.dataset.caParameter : null;
    const focusDetailKind = focused?.tagName === "SUMMARY" ? focused.parentElement?.dataset.caDetailKind : null;
    panel.replaceChildren();
    if (model.diagnosticInvalidated) {
      const invalidated = node(document, "p", "control-advisor__notice is-error", "이전 진단 결과가 무효화되었습니다. 사용 시간·오차를 현재 근거로 표시하지 않고 새 자료를 기다립니다.");
      invalidated.dataset.caInvalidated = "";
      panel.append(invalidated);
    } else if (model.partialWindow) {
      const partial = node(document, "p", "control-advisor__notice", "종료 시점의 부분 구간 진단입니다. 선택한 3분·5분 분석 구간을 채웠다는 뜻은 아닙니다.");
      partial.dataset.caPartial = "";
      panel.append(partial);
    }
    if (model.reason) {
      const reason = node(document, "div", "control-advisor__notice", model.reason);
      if (model.technicalReason) {
        const details = node(document, "details", "control-advisor__details");
        details.append(node(document, "summary", "", "상세 사유"), node(document, "p", "", model.technicalReason));
        reason.append(details);
      }
      panel.append(reason);
    }
    panel.append(renderRecording(model));
    if (selectedTab === "live") {
      panel.append(renderDiagnostic(model));
      const readyCandidates = model.parameters.filter((item) => item.request?.writesVehicle).length;
      const notice = node(document, "p", "control-advisor__notice", readyCandidates
        ? "로그 후보를 확인하고 차량 설정 저장을 요청할 수 있습니다. 저장 후 실제 소비값과 전후 관측을 따로 확인합니다. 실제 추종 성능 개선은 아직 입증되지 않았습니다."
        : "진단 통계를 수집합니다. 소비 지점별 값 관측과 전체 반영 확인은 구분하며, 현재 차량 설정 저장 조건을 충족한 로그 후보를 기다립니다.");
      panel.append(notice);
      const filter = node(document, "label", "control-advisor__field control-advisor__filter", "제어 방향");
      const select = node(document, "select");
      select.dataset.caAxis = "";
      for (const [value, label] of [["all", "전체 항목"], ["lateral", "횡방향"], ["longitudinal", "종방향"]]) {
        const option = node(document, "option", "", label);
        option.value = value;
        select.append(option);
      }
      select.value = ui.axis;
      select.addEventListener("change", () => handlers.onAxis(select.value));
      filter.append(select);
      const cards = node(document, "div", "control-advisor__cards");
      cards.dataset.caCards = "";
      const shown = model.parameters.filter((item) => ui.axis === "all" || item.axis === ui.axis);
      for (const item of shown) cards.append(parameterCard(document, item, requestControls));
      if (!shown.length) cards.append(node(document, "p", "control-advisor__empty", "항목 목록을 기다리고 있습니다."));
      panel.append(filter, node(document, "p", "control-advisor__muted", `${shown.length}개 항목 · 차량 설정 저장 요청 가능한 로그 후보 ${shown.filter((item) => item.request?.writesVehicle).length}개`), cards);
    } else if (selectedTab === "compare") {
      const comparisons = model.applyRequests.items.filter((request) => request.comparison);
      panel.append(node(document, "h3", "", comparisons.length ? "요청별 관측 비교" : "전후 비교 보류"), node(document, "p", "control-advisor__notice", "소비 지점별 관측만으로 전체 반영 경계를 확정하지 않습니다. 조건이 확인된 요청의 관측 차이만 아래에 표시합니다."));
      if (comparisons.length) {
        for (const request of comparisons) panel.append(comparisonCard(document, request));
      } else {
        const phases = node(document, "div", "control-advisor__phases");
        for (const [title, description] of [["변경 전", "설정·조건 근거 대기"], ["전환 구간", "반영 경계 확인 대기"], ["변경 후", "새 설정 관측 대기"]]) {
          const phase = node(document, "article", "control-advisor__card");
          phase.append(node(document, "h3", "", title), node(document, "p", "control-advisor__muted", description));
          phases.append(phase);
        }
        panel.append(phases, pair(document, "진단 결과 이력", `${model.historyCount}개 · 설정 적용 이력이 아님`));
      }
    } else {
      panel.append(parkingReviewPanel(document, model, ui, handlers, ui.connected && !ui.pending && model.controlConfigured
        && (model.auth.authorized || token.value.trim().length > 0), renderDiagnostic));
    }
    for (const card of panel.querySelectorAll("[data-ca-parameter]")) {
      for (const details of card.querySelectorAll("details")) {
        if (expanded.has(JSON.stringify([card.dataset.caParameter, details.dataset.caDetailKind]))) details.setAttribute("open", "");
        if (card.dataset.caParameter === focusParameter && details.dataset.caDetailKind === focusDetailKind) details.querySelector("summary")?.focus({ preventScroll: true });
      }
    }
    if (focusAxis) panel.querySelector("[data-ca-axis]")?.focus({ preventScroll: true });
    if (focusExportRaw) panel.querySelector("[data-ca-export-raw]")?.focus({ preventScroll: true });
    if (liveRequestFocus) [...panel.querySelectorAll("[data-ca-request-item]")].find((button) => button.dataset.caRequestItem === liveRequestFocus)?.focus({ preventScroll: true });
  }

  return { update, destroy() { token.value = ""; root.remove(); } };
}
