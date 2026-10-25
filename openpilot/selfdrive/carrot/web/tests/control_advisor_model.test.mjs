import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { advisorControlState, buildApplyRequestBody, buildReviewExportBody, buildReviewRegistrationBody, createReviewHistoryModel, createCandidateReportModel, createConsumerObservationsModel, createControlAdvisorModel, createRecordingModel, formatAdvisorBytes, formatAdvisorNumber, formatConsumerValue } from "../src/features/control_advisor/model.js";

test("local review registration rejects drafts, nonobjects and total UTF8 envelope over budget", () => {
  assert.throws(() => buildReviewRegistrationBody("id", "[]"), /invalid/);
  assert.throws(() => buildReviewRegistrationBody("id", '{"document_state":"draft"}'), /not_final/);
  assert.throws(() => buildReviewRegistrationBody("id", JSON.stringify({ document_state: "final", text: "한".repeat(45000) })), /too_large/);
  assert.equal(JSON.parse(buildReviewRegistrationBody("retry-id", '\uFEFF{"document_state":"final"}')).request_id, "retry-id");
});

test("review history remains exact historical self-declared text and never enables application", () => {
  const review = { document_state: "final", review_revision: 2, summary_revision: 7, verdict: "reviewed_recommendation",
    declared_reviewer: { tool: "Codex", model: "declared-model", reviewed_at: "2026-10-25T00:00:00Z" },
    findings: [{ statement: "<img onerror=alert(1)>" }], uncertainties: ["자료 한계"], unreviewed_scopes: [{ reason: "확인 안 함" }], follow_up: ["추가 관측"] };
  const wrapper = { review_hash: "b".repeat(64), review, identity_verified: false, historical_only: true, apply_enabled: false, device_qualified: false,
    path: "C:/private", token: "secret" };
  const model = createReviewHistoryModel({ current: false, binding_state: "unavailable", reviews: [wrapper], review_versions: { [wrapper.review_hash]: { code: "declared_only" } } });
  assert.equal(model.current, false); assert.equal(model.bindingVerified, false);
  assert.equal(model.items[0].summaryRevision, 7); assert.equal(model.items[0].revision, 2);
  assert.equal(model.items[0].findings[0], "<img onerror=alert(1)>");
  assert.match(model.items[0].verdictLabel, /적용 자격 아님/);
  assert.doesNotMatch(JSON.stringify(model), /private|secret/);
  assert.equal(createReviewHistoryModel({ reviews: [{ ...wrapper, identity_verified: true }] }).items.length, 0);
  const exports = createControlAdvisorModel({ review_exports: [{ job_id: "a".repeat(32), state: "ready", summary_id: "s", summary_revision: 7, review_count: 2, latest_review_revision: 2 }] });
  assert.equal(exports.reviewExports.jobs[0].summaryRevision, 7); assert.equal(exports.reviewExports.jobs[0].reviewCount, 2);
  assert.equal(exports.applyEnabled, false);
});

function epoch(values = {}) {
  return { sample_count: 10, eligible_count: 5, excluded_count: 3, provisional_count: 2, eligible_duration_ns: 1_500_000_000,
    raw_metrics: { rmse: 2 }, eligible_metrics: { rmse: 1, bias: -.2, p95_absolute_error: 3 }, ...values };
}

test("missing state is unknown data, never fabricated zero or an active session", () => {
  const model = createControlAdvisorModel();
  assert.equal(model.state, "idle");
  assert.equal(model.active, false);
  assert.equal(model.receivedEvents, null);
  assert.equal(model.eligibleCount, null);
  assert.equal(model.diagnosticAvailable, false);
  assert.equal(formatAdvisorNumber(null), "—");
  assert.equal(formatAdvisorNumber(NaN), "—");
  assert.equal(formatAdvisorNumber("0"), "—");
});

test("synthetic mode remains prominent after completion and never promotes apply", () => {
  const model = createControlAdvisorModel({ session: { source_mode: "synthetic", state: "completed", recommendations_enabled: true,
    apply_enabled: true }, parameters: [{ key: "Gain", axis: "lateral", candidate: 9, apply_enabled: true }] });
  assert.match(model.sourceLabel, /합성 데모.*실제 주행 아님/);
  assert.equal(model.applyEnabled, false);
  assert.equal(model.recommendationsEnabled, false);
  assert.equal(model.parameters[0].candidate, null);
  assert.equal(model.parameters[0].applyEnabled, false);
  assert.equal(model.parameters[0].currentRaw, null);
});

test("live unsupported and failed state never falls back to synthetic", () => {
  const model = createControlAdvisorModel({ session: { source_mode: "live", state: "unavailable", reason: "native_unavailable" } });
  assert.equal(model.sourceMode, "live");
  assert.equal(model.active, false);
  assert.match(model.stateLabel, /사용 불가/);
  assert.match(model.reason, /받을 수 없습니다/);
  assert.doesNotMatch(model.sourceLabel, /합성/);
});

test("axes and settings epochs retain individual error metrics and usage counts", () => {
  const diagnostic = { axes: { lateral: { epochs: { A: epoch(), B: epoch({ eligible_count: 4 }) } },
    longitudinal: { epochs: { A: epoch() } } } };
  const model = createControlAdvisorModel({ session: { diagnostic } });
  assert.equal(model.eligibleCount, 14);
  assert.equal(model.excludedCount, 9);
  assert.equal(model.provisionalCount, 6);
  assert.equal(model.summaries[0].eligibleSeconds, 3);
  assert.equal(model.summaries[0].epochs.length, 2);
  assert.deepEqual(model.summaries[0].epochs.map((value) => value.eligibleRmse), [1, 1]);
  assert.equal(Object.hasOwn(model.summaries[0], "combinedRmse"), false);
});

test("a missing axis or incomplete counters are not silently treated as zero", () => {
  const model = createControlAdvisorModel({ session: { diagnostic: { axes: { lateral: { epochs: { A: epoch() } } } } } });
  assert.equal(model.summaries[0].eligibleCount, 5);
  assert.equal(model.summaries[1].eligibleCount, null);
  assert.equal(model.eligibleCount, null);
});

test("invalidated past results never remain current usage or error evidence", () => {
  for (const signals of [{ diagnostic_invalidated: true }, {}]) {
    const model = createControlAdvisorModel({ session: { state: "degraded", ...signals,
      diagnostic: { invalidated: true, axes: { lateral: { epochs: { A: epoch() } }, longitudinal: { epochs: { A: epoch() } } } } } });
    assert.equal(model.diagnosticInvalidated, true);
    assert.equal(model.diagnosticAvailable, false);
    assert.equal(model.eligibleCount, null);
    assert.equal(model.summaries[0].eligibleSeconds, null);
    assert.deepEqual(model.summaries[0].epochs, []);
  }
});

test("partial window and accelerated synthetic time remain explicit statuses", () => {
  const model = createControlAdvisorModel({ session: { source_mode: "synthetic", clock_mode: "accelerated_synthetic",
    diagnostic: { partial_window: true, axes: { lateral: { epochs: { A: epoch() } } } } } });
  assert.equal(model.acceleratedSynthetic, true);
  assert.equal(model.partialWindow, true);
});

test("session actions require explicit capability, a key, and a matching lifecycle", () => {
  const base = { capabilities: { control_configured: true, live_enabled: true, synthetic_enabled: true },
    session: { state: "idle" } };
  const model = createControlAdvisorModel(base);
  assert.equal(advisorControlState(model).canStartLive, false);
  assert.equal(advisorControlState(model, { token: "key" }).canStartLive, true);
  assert.equal(advisorControlState(model, { token: "key", pending: true }).canStartSynthetic, false);
  const running = createControlAdvisorModel({ ...base, session: { state: "running", session_id: "session-A" } });
  assert.equal(advisorControlState(running, { token: "key" }).canStartLive, false);
  assert.equal(advisorControlState(running, { token: "key" }).canStop, true);
  assert.equal(advisorControlState(running, { token: "key" }).intervalDisabled, true);
  const stopping = createControlAdvisorModel({ ...base, session: { state: "stopping", session_id: "session-A" } });
  assert.equal(advisorControlState(stopping, { token: "key" }).canStop, false);
});

test("all 43 numeric parameters and 4 context settings have honest unavailable candidates", async () => {
  const registry = JSON.parse(await readFile(new URL("../../control_advisor/parameters.json", import.meta.url), "utf8"));
  const model = createControlAdvisorModel({ parameters: registry.items });
  assert.equal(model.parameters.length, 47);
  assert.equal(model.parameters.filter((item) => item.contextOnly).length, 4);
  assert.equal(model.parameters.filter((item) => !item.contextOnly).length, 43);
  assert.ok(model.parameters.every((item) => item.candidate === null && item.applyEnabled === false));
  assert.equal(model.parameters.find((item) => item.key === "LateralTorqueKd").reason, "현재 소비 경로에서 효과 없음");
});

function recordingFixture() {
  return {
    state: "recording",
    settings: { state: "observed", revision: 5, durable_revision: 5, poll_sequence: 10, changed_keys: ["LateralTorqueKf"],
      values: { LateralTorqueKf: 100 }, observation: { begin_ns: 100_000_000, end_ns: 105_000_000, previous_end_ns: 84_000_000,
        gap: false, change_interval: { after_ns: 84_000_000, before_ns: 105_000_000 } },
      journal: { path: "settings.jsonl", sequence: 10, sha256: "a".repeat(64), bytes: 1024, durable: true },
      history_complete: false, consumer_applied: false, atomic_snapshot: false, completeness: "polled_partial" },
    preservation: { state: "verified", verified_segments: 3, verified_bytes: 1024 * 1024,
      failed_segments: 0, pending_jobs: 0, uncovered_windows: 2, journal_committed: true, latest_diagnostic_revision: 8 },
  };
}

test("unconfigured old payload has unknown recording counts rather than fabricated preservation", () => {
  const model = createControlAdvisorModel();
  assert.equal(model.recording.configured, false);
  assert.equal(model.recording.settings.revision, null);
  assert.equal(model.recording.settings.observed, false);
  assert.equal(model.recording.preservation.verifiedSegments, null);
  assert.equal(model.recording.preservation.fullCoverageConfirmed, false);
});

test("saved observations and durable revisions never imply consumer application or complete history", () => {
  const fixture = recordingFixture();
  fixture.settings.consumer_applied = true;
  fixture.settings.history_complete = true;
  fixture.settings.atomic_snapshot = true;
  const model = createControlAdvisorModel({ session: { recording: fixture }, parameters: [
    { key: "LateralTorqueKf", axis: "lateral", current_raw: 100, current_physical: 1, apply_enabled: true },
  ] });
  const settings = model.recording.settings;
  assert.equal(settings.revision, 5);
  assert.equal(settings.durableRevision, 5);
  assert.equal(settings.journalCurrent, true);
  assert.equal(settings.observationWidthMs, 5);
  assert.equal(settings.changeUncertaintyMs, 21);
  assert.match(settings.historyLabel, /전체 설정 이력 미확인/);
  assert.match(settings.consumerLabel, /실제 제어기 반영 미확인/);
  assert.equal(model.parameters[0].currentRaw, 100);
  assert.equal(model.parameters[0].applyEnabled, false);
});

test("latest observed revision beyond durable revision remains explicitly pending", () => {
  const fixture = recordingFixture();
  fixture.settings.durable_revision = 4;
  const settings = createRecordingModel(fixture).settings;
  assert.equal(settings.observed, true);
  assert.equal(settings.journalCurrent, false);
  assert.equal(settings.journalStateLabel, "최신 관측 기록 대기");
});

test("partial settings and a polling gap retain uncertain change time without zero filling", () => {
  const fixture = recordingFixture();
  fixture.settings.state = "partial";
  fixture.settings.observation.gap = true;
  fixture.settings.observation.change_interval.after_ns = null;
  fixture.settings.journal.durable = false;
  const settings = createRecordingModel(fixture).settings;
  assert.equal(settings.stateLabel, "저장값 일부 관측");
  assert.equal(settings.gap, true);
  assert.equal(settings.gapKnown, true);
  assert.equal(settings.changeObserved, true);
  assert.equal(settings.changeUncertaintyMs, null);
  assert.equal(settings.journalCurrent, false);
});

test("verified individual segments never imply full latest-window coverage", () => {
  const raw = createRecordingModel(recordingFixture()).preservation;
  assert.equal(raw.verifiedSegments, 3);
  assert.equal(raw.verifiedBytes, 1024 * 1024);
  assert.equal(raw.latestDiagnosticRevision, 8);
  assert.equal(raw.uncoveredWindows, 2);
  assert.match(raw.stateLabel, /미포함 구간 있음/);
  assert.equal(raw.fullCoverageConfirmed, false);
  assert.match(raw.coverageLabel, /최신·미완료 구간.*미확인/);
});

test("uncommitted preservation history and failed copies retain honest partial status", () => {
  const fixture = recordingFixture();
  fixture.preservation.uncovered_windows = 0;
  fixture.preservation.journal_committed = false;
  let raw = createRecordingModel(fixture).preservation;
  assert.match(raw.stateLabel, /보존 이력 미확정/);
  assert.equal(raw.fullCoverageConfirmed, false);
  fixture.preservation.state = "failed";
  fixture.preservation.failed_segments = 1;
  raw = createRecordingModel(fixture).preservation;
  assert.equal(raw.stateLabel, "원본 보존 오류");
  assert.equal(raw.verifiedSegments, 3);
  assert.equal(raw.failedSegments, 1);
});

test("all preservation work states are visible without treating pending work as verified", () => {
  for (const state of ["disabled", "waiting", "pending", "copying", "verified", "partial", "failed", "stopped"]) {
    const fixture = recordingFixture();
    fixture.preservation.state = state;
    const raw = createRecordingModel(fixture).preservation;
    assert.equal(raw.state, state);
    assert.notEqual(raw.stateLabel, "원본 보존 미연결");
    assert.equal(raw.fullCoverageConfirmed, false);
  }
});

test("recording presentation drops private paths and values and rejects malformed counts", () => {
  const fixture = recordingFixture();
  fixture.reason = "C:/private/vehicle/data";
  fixture.settings.reason = "C:/private/params";
  fixture.settings.journal.path = "C:/private/sidecar.jsonl";
  fixture.settings.values.secret = "private-token";
  fixture.preservation.reason = "C:/private/raw";
  fixture.preservation.pending_jobs = true;
  fixture.preservation.failed_segments = -1;
  fixture.preservation.verified_bytes = "1048576";
  const model = createRecordingModel(fixture);
  assert.doesNotMatch(JSON.stringify(model), /C:\/private|private-token/);
  assert.equal(model.preservation.pendingJobs, null);
  assert.equal(model.preservation.failedSegments, null);
  assert.equal(model.preservation.verifiedBytes, null);
  assert.equal(formatAdvisorBytes(null), "—");
  assert.equal(formatAdvisorBytes(-1), "—");
  assert.equal(formatAdvisorBytes(0), "0 B");
  assert.equal(formatAdvisorBytes(1024), "1 KiB");
  assert.equal(formatAdvisorBytes(1024 * 1024), "1 MiB");
});

test("unrecognized session error text cannot expose a recorder's local file path", () => {
  const model = createControlAdvisorModel({ session: { state: "failed", reason: "write failed: C:/private/vehicle/settings.jsonl" } });
  assert.equal(model.technicalReason, null);
  assert.doesNotMatch(JSON.stringify(model), /C:\/private/);
  assert.match(model.reason, /상태를 확인/);
});

function consumerFixture(overrides = {}) {
  const field = { read_value: 2.8, effective_value: 2.9, state: "used", source: "stored", read_mono_ns: 1_000_000_000 };
  const wire = { consumer_id: "controls.torque", boot_id: "private-boot-identity", pid: 12345, process_instance_id: "private-process",
    consumer_instance_id: "private-object", sequence: 5, produced_mono_ns: 1_100_000_000, cycle_id: 55, phase: "update",
    fields: { LateralTorqueAccelFactor: field }, context: { private_path: "/data/private/vehicle" }, dropped_events: 0 };
  return { state: "observed", source_mode: "synthetic", received_events: 5, rejected_events: 0, dropped_events: 0, restarts: 0,
    consumer_application_ack: false, device_qualified: false,
    consumers: [{ consumer_id: "controls.torque", state: "observed", fresh: true,
      read_freshness: { LateralTorqueAccelFactor: true }, latest_event: wire }],
    persistence: { state: "recorded", recorded_events: 5, dropped_events: 0 }, ...overrides };
}

test("consumer values stay in physical consumer units and never replace stored raw parameters", () => {
  const observations = consumerFixture({ consumer_application_ack: true, device_qualified: true });
  const model = createControlAdvisorModel({ session: { consumer_observations: observations }, parameters: [
    { key: "LateralTorqueAccelFactor", axis: "lateral", current_raw: 2800, current_physical: 2.8 },
  ] });
  const item = model.parameters[0];
  assert.equal(item.currentRaw, 2800);
  assert.equal(item.consumerObservations[0].readValue, 2.8);
  assert.equal(item.consumerObservations[0].effectiveValue, 2.9);
  assert.equal(item.consumerObservations[0].effectiveLabel, "관측된 사용값");
  assert.equal(item.consumerObservations[0].readFresh, true);
  assert.equal(model.consumerObservations.consumerApplicationAck, false);
  assert.equal(model.consumerObservations.deviceQualified, false);
  assert.match(model.consumerObservations.sourceLabel, /합성.*실제 제어기 아님/);
  assert.equal(item.applyEnabled, false);
});

test("platform-fixed override preserves actual zero and explains the bypassed setting", () => {
  const fixture = consumerFixture();
  fixture.consumers[0].latest_event.fields = { LongTuningKiV: { read_value: .02, effective_value: 0, state: "bypassed", source: "platform_fixed", read_mono_ns: null } };
  fixture.consumers[0].read_freshness = { LongTuningKiV: false };
  const model = createConsumerObservationsModel(fixture, ["LongTuningKiV"]);
  const field = model.entries[0].fields.LongTuningKiV;
  assert.equal(field.effectiveValue, 0);
  assert.equal(formatConsumerValue(field.effectiveValue), "0");
  assert.equal(field.stateLabel, "현재 경로에서 우회");
  assert.equal(field.sourceLabel, "차량 플랫폼 고정값");
  assert.equal(field.effectiveLabel, "선택 경로 내부값");
  assert.equal(field.readFresh, false);
});

test("constructor-latched current object values remain visible while their old read is explicit", () => {
  const fixture = consumerFixture();
  fixture.consumers[0].latest_event.fields.LateralTorqueAccelFactor.state = "latched";
  fixture.consumers[0].read_freshness.LateralTorqueAccelFactor = false;
  const field = createConsumerObservationsModel(fixture, ["LateralTorqueAccelFactor"]).entries[0].fields.LateralTorqueAccelFactor;
  assert.equal(field.effectiveValue, 2.9);
  assert.equal(field.readValue, 2.8);
  assert.equal(field.readFresh, false);
  assert.equal(field.readLabel, "이전 읽기 관측값");
  assert.match(field.readStatusLabel, /오래됨·미확인/);
  assert.equal(field.effectiveLabel, "생성자 고정 내부값");
});

test("actual stale events and unavailable sources cannot expose past values as current", () => {
  for (const change of ["stale", "unavailable", "identity_mismatch"]) {
    const fixture = consumerFixture();
    if (change === "stale") { fixture.consumers[0].fresh = false; fixture.consumers[0].state = "stale"; }
    if (change === "unavailable") fixture.state = "unavailable";
    if (change === "identity_mismatch") fixture.consumers[0].latest_event.consumer_id = "modeld.action";
    const field = createConsumerObservationsModel(fixture, ["LateralTorqueAccelFactor"]).entries[0].fields.LateralTorqueAccelFactor;
    assert.equal(field.readValue, null);
    assert.equal(field.effectiveValue, null);
    assert.equal(field.readFresh, false);
    assert.match(field.stateLabel, /현재값 미확인/);
  }
});

test("multiple consumers keep separate observations, gaps, and restart information", () => {
  const fixture = consumerFixture({ state: "partial", dropped_events: 1, restarts: 1 });
  const second = structuredClone(fixture.consumers[0]);
  second.consumer_id = "modeld.action";
  second.latest_event.consumer_id = "modeld.action";
  second.latest_event.fields.LateralTorqueAccelFactor.effective_value = 3.1;
  second.state = "restarted";
  fixture.consumers[0].state = "gap";
  fixture.consumers.push(second);
  const item = createControlAdvisorModel({ session: { consumer_observations: fixture }, parameters: [{ key: "LateralTorqueAccelFactor", axis: "lateral" }] }).parameters[0];
  assert.equal(item.consumerObservations.length, 2);
  assert.deepEqual(item.consumerObservations.map((value) => value.effectiveValue), [2.9, 3.1]);
  assert.equal(item.consumerObservations[0].gap, true);
  assert.equal(item.consumerObservations[1].restarted, true);
});

test("consumer value formatting supports booleans and bounded numeric arrays without accepting strings", () => {
  assert.equal(formatConsumerValue(0), "0");
  assert.equal(formatConsumerValue(false), "false");
  assert.equal(formatConsumerValue([0, 1.25]), "[0, 1.25]");
  for (const value of [null, NaN, Infinity, "private-token", [0, "private-token"], [], Array(17).fill(0)]) {
    assert.equal(formatConsumerValue(value), "—");
  }
});

test("missing or unsupported consumer fields remain unknown, without fabricated values", () => {
  const fixture = consumerFixture();
  const field = fixture.consumers[0].latest_event.fields.LateralTorqueAccelFactor;
  field.state = "unsupported";
  field.effective_value = 0;
  const model = createConsumerObservationsModel(fixture, ["LateralTorqueAccelFactor", "LateralTorqueFriction"]);
  assert.equal(model.entries[0].fields.LateralTorqueAccelFactor.effectiveValue, null);
  assert.equal(Object.hasOwn(model.entries[0].fields, "LateralTorqueFriction"), false);
  assert.equal(model.entries[0].fields.LateralTorqueAccelFactor.stateLabel, "현재 경로 미지원");
  const old = createConsumerObservationsModel();
  assert.equal(old.state, "disabled");
  assert.equal(old.receivedEvents, null);
  assert.equal(old.entries.length, 0);
});

test("consumer identities, paths, arbitrary fields, and errors stay out of the presentation model", () => {
  const fixture = consumerFixture();
  fixture.reason = "failed at /data/private/vehicle";
  fixture.consumers[0].latest_event.fields.SECRET_TOKEN = { read_value: "secret", effective_value: "secret", state: "used", source: "stored" };
  fixture.consumers[0].latest_event.fields.LateralTorqueAccelFactor.read_value = "/data/private/vehicle";
  fixture.consumers.push({ consumer_id: "/data/private/process", state: "observed", fresh: true, latest_event: {} });
  const model = createConsumerObservationsModel(fixture, ["LateralTorqueAccelFactor"]);
  assert.equal(model.entries.length, 1);
  assert.equal(model.entries[0].fields.LateralTorqueAccelFactor.readValue, null);
  assert.doesNotMatch(JSON.stringify(model), /private|12345|SECRET_TOKEN|consumer_instance_id|boot_id/);
});

async function candidateFixture() {
  const parameters = JSON.parse(await readFile(new URL("../../control_advisor/parameters.json", import.meta.url), "utf8")).items;
  const metric = { rows: 40, blocks: 4, rmse: .1, max_absolute_error: .2, max_absolute_prediction: .4, trace_digest: "b".repeat(64) };
  const report = { schema_version: 1, profile_id: "private-profile", evidence_id: "private-route", profile_version: 1,
    source_kind: "synthetic", scope: "host_unqualified", device_qualified: false, recommendations_enabled: false, apply_enabled: false,
    items: parameters.map((parameter) => ({ key: parameter.key, status: parameter.classification === "context_only" ? "unsupported" : "blocked",
      current_raw: 0, current_physical: 0, candidate_raw: null, candidate_physical: null, raw_estimate: null, physical_estimate: null,
      prediction_kind: "static_formula", metrics: { baseline: { select: metric, holdout: metric }, selection: metric, holdout: metric },
      validation: { numerical_checks_pass: false, split_independent: false, holdout_objective_pass: false },
      provenance: { path: "/data/private/route" }, reason_codes: ["missing_inputs"] })) };
  return { parameters, envelope: { state: "ready", reason: null, report, artifact_sha256: "a".repeat(64), integrity_verified: true,
    device_qualified: false, apply_enabled: false, raw_sources_verified: false } };
}

test("offline reports remain separate from the current live recommendation and expose all 47 statuses", async () => {
  const { parameters, envelope } = await candidateFixture();
  envelope.report.items[0] = { ...envelope.report.items[0], status: "search_candidate", candidate_raw: 0, candidate_physical: 0,
    validation: { numerical_checks_pass: true, split_independent: true, holdout_objective_pass: true } };
  const model = createControlAdvisorModel({ parameters, candidate_report: envelope, session: { source_mode: "live" } });
  assert.equal(model.candidateReport.available, true);
  assert.equal(model.candidateReport.items.length, 47);
  assert.match(model.candidateReport.sourceLabel, /합성.*실제 주행.*아님/);
  assert.equal(model.candidateReport.items[0].candidateRaw, 0);
  assert.ok(model.parameters.every((item) => item.candidate === null && !item.applyEnabled));
  assert.equal(model.candidateReport.applyEnabled, false);
  assert.ok(model.candidateReport.items.every((item) => !item.applyEnabled && !item.deviceQualified));
  envelope.report.source_kind = "recorded";
  assert.match(createCandidateReportModel(envelope, parameters).sourceLabel, /기록 자료.*오프라인.*실제 주행 검증 전/);
});

test("raw estimates, current retention, blocked inputs, and context-only rows never borrow candidate values", async () => {
  const { parameters, envelope } = await candidateFixture();
  Object.assign(envelope.report.items[0], { status: "raw_estimate", estimate_kind: "parameter_calibration", raw_estimate: 120, physical_estimate: 1.2, candidate_raw: 999,
    validation: { numerical_checks_pass: true, split_independent: false, holdout_objective_pass: false } });
  Object.assign(envelope.report.items[1], { status: "keep_current", current_raw: 0, candidate_raw: 999 });
  const result = createCandidateReportModel(envelope, parameters);
  assert.equal(result.items[0].rawEstimate, 120);
  assert.equal(result.items[0].candidateRaw, null);
  assert.equal(result.items[1].currentRaw, 0);
  assert.equal(result.items[1].candidateRaw, null);
  assert.equal(result.items[2].status, "blocked");
  assert.equal(result.items.filter((item) => item.contextOnly).length, 4);
  assert.match(result.items.find((item) => item.contextOnly).statusLabel, /숫자 후보 대상 아님/);
});

test("unverified, malformed, elevated, and incomplete offline reports expose no past result", async () => {
  const { parameters, envelope } = await candidateFixture();
  const variants = [
    { ...envelope, state: "invalid" }, { ...envelope, state: "loading" }, { ...envelope, integrity_verified: false },
    { ...envelope, artifact_sha256: "/private/path" }, { ...envelope, apply_enabled: true },
    { ...envelope, report: { ...envelope.report, device_qualified: true } },
    { ...envelope, report: { ...envelope.report, items: envelope.report.items.slice(1) } },
    { ...envelope, report: { ...envelope.report, items: [envelope.report.items[0], ...envelope.report.items.slice(0, 46)] } },
  ];
  for (const value of variants) {
    const result = createCandidateReportModel(value, parameters);
    assert.equal(result.available, false);
    assert.deepEqual(result.items, []);
  }
  assert.equal(createCandidateReportModel().available, false);
});

test("offline score displays reject nonfinite input and exclude identifiers, paths, digests and arbitrary error strings", async () => {
  const { parameters, envelope } = await candidateFixture();
  Object.assign(envelope.report.items[0], { current_raw: Infinity, current_physical: "/data/private" });
  envelope.report.items[0].metrics.selection = { rows: 4, blocks: 1, rmse: NaN, max_absolute_error: 0, max_absolute_prediction: 0 };
  envelope.reason = "<img src=x onerror=private>";
  envelope.report.items[0].method_id = "<script>private</script>";
  const result = createCandidateReportModel(envelope, parameters);
  assert.equal(result.items[0].currentRaw, null);
  assert.equal(result.items[0].currentPhysical, null);
  assert.equal(result.items[0].selection, null);
  assert.doesNotMatch(JSON.stringify(result), /private|onerror|<script>|trace_digest|profile_id|evidence_id/);
});

test("offline candidates with failed guards or missing holdout never expose candidate values", async () => {
  const { parameters, envelope } = await candidateFixture();
  const base = { ...envelope.report.items[0], status: "search_candidate", candidate_raw: 10, candidate_physical: .1,
    validation: { numerical_checks_pass: true, split_independent: true, holdout_objective_pass: true } };
  for (const patch of [{ validation: { ...base.validation, numerical_checks_pass: false } },
    { validation: { ...base.validation, split_independent: false } },
    { validation: { ...base.validation, holdout_objective_pass: false } },
    { metrics: { ...base.metrics, holdout: null } }, { candidate_raw: Infinity }]) {
    envelope.report.items[0] = { ...base, ...patch };
    const result = createCandidateReportModel(envelope, parameters);
    assert.equal(result.state, "invalid");
    assert.deepEqual(result.items, []);
  }
});

test("calculation capability metadata updates availability copy without enabling live recommendations", () => {
  const model = createControlAdvisorModel({ parameters: [
    { key: "Gain", candidate_calculation: { method_id: "fit", backend: "host", reason_codes: [] } },
    { key: "Other", candidate_calculation: { method_id: "search", backend: "missing", reason_codes: ["backend_unavailable"] } },
    { key: "Fixed", structural_blocker: "unused", candidate_calculation: { method_id: "fit", backend: "host" } },
  ] });
  assert.match(model.parameters[0].reason, /오프라인 계산 경로 제공.*근거 미연결/);
  assert.match(model.parameters[1].reason, /백엔드 사용 불가/);
  assert.match(model.parameters[2].reason, /효과 없음/);
  assert.ok(model.parameters.every((item) => item.candidate === null && !item.applyEnabled));
});

test("special stored values are marked and cannot become a new numeric candidate", async () => {
  const { parameters, envelope } = await candidateFixture();
  const parameter = parameters.find((item) => item.key === "SteerActuatorDelay");
  assert.ok(parameter);
  const row = envelope.report.items.find((item) => item.key === parameter.key);
  const sentinel = Number(Object.keys(parameter.special_values)[0]);
  row.current_raw = sentinel;
  assert.equal(createCandidateReportModel(envelope, parameters).items.find((item) => item.key === parameter.key).currentSpecial, true);
  Object.assign(row, { status: "search_candidate", current_raw: sentinel + 1, candidate_raw: sentinel, candidate_physical: 0,
    validation: { numerical_checks_pass: true, split_independent: true, holdout_objective_pass: true } });
  assert.equal(createCandidateReportModel(envelope, parameters).state, "invalid");
  Object.assign(row, { status: "blocked", candidate_raw: null, candidate_physical: null });
  const boost = envelope.report.items.find((item) => item.key === "TFollowDecelBoost");
  Object.assign(boost, { status: "search_candidate", current_raw: 0, candidate_raw: 10, candidate_physical: .1,
    validation: { numerical_checks_pass: true, split_independent: true, holdout_objective_pass: true } });
  assert.equal(createCandidateReportModel(envelope, parameters).available, true);
});

test("diagnostic proxy estimates never become parameter calibration or search candidates", async () => {
  const { parameters, envelope } = await candidateFixture();
  const row = envelope.report.items[0];
  Object.assign(row, { status: "raw_estimate", estimate_kind: "diagnostic_proxy", raw_estimate: 12, physical_estimate: .12,
    validation: { numerical_checks_pass: true, split_independent: false, holdout_objective_pass: false } });
  const result = createCandidateReportModel(envelope, parameters);
  assert.equal(result.available, true);
  assert.equal(result.items[0].estimateKind, "diagnostic_proxy");
  assert.match(result.items[0].statusLabel, /대리 지표.*식별값 아님/);
  assert.equal(result.items[0].candidateRaw, null);
  Object.assign(row, { status: "search_candidate", candidate_raw: 12, candidate_physical: .12,
    validation: { numerical_checks_pass: true, split_independent: true, holdout_objective_pass: true } });
  assert.equal(createCandidateReportModel(envelope, parameters).state, "invalid");
});

test("CustomSR allows validated manual calibration while rejecting automatic or threshold transitions", async () => {
  const { parameters, envelope } = await candidateFixture();
  const row = envelope.report.items.find((item) => item.key === "CustomSR");
  Object.assign(row, { status: "search_candidate", current_raw: 150, current_physical: 15, candidate_raw: 160, candidate_physical: 16,
    estimate_kind: "parameter_calibration", validation: { numerical_checks_pass: true, split_independent: true, holdout_objective_pass: true } });
  const model = createControlAdvisorModel({ parameters, candidate_report: envelope });
  const custom = model.candidateReport.items.find((item) => item.key === "CustomSR");
  assert.equal(model.candidateReport.available, true);
  assert.equal(custom.contextOnly, false);
  assert.equal(custom.candidateRaw, 160);
  assert.equal(custom.applyEnabled, false);
  assert.equal(model.parameters.find((item) => item.key === "CustomSR").contextOnly, false);
  assert.equal(model.candidateReport.items.filter((item) => item.contextOnly).length, 4);
  for (const [current, candidate] of [[0, 160], [150, 0], [10, 160], [150, 10]]) {
    row.current_raw = current;
    row.candidate_raw = candidate;
    assert.equal(createCandidateReportModel(envelope, parameters).state, "invalid");
  }
});

function logLinkFixture() {
  return { source_bytes_verified_at_generation: true, schema_decoded: true, session_link: "verified_archive_receipt",
    semantic_mapping: "partial", originals_preservation: "verified_session_archive", originals_current_availability: "not_checked",
    source_count: 2, epoch_count: 3, excluded_counts: { driver_intervention: 7, recovery: 0 },
    reasons: ["consumer_evidence_missing", "recording_schema_match_unverified"] };
}

test("recorded extraction links separate generation checks, archive preservation, and current access", async () => {
  const { parameters, envelope } = await candidateFixture();
  envelope.report.source_kind = "recorded";
  envelope.log_link = logLinkFixture();
  const result = createCandidateReportModel(envelope, parameters);
  assert.equal(result.available, true);
  assert.equal(result.logLink.available, true);
  assert.match(result.logLink.stateLabel, /생성 당시.*해시.*추출/);
  assert.match(result.logLink.sessionLabel, /보존 세션 영수증/);
  assert.match(result.logLink.preservationLabel, /기존 세션 보존 사본/);
  assert.match(result.logLink.availabilityLabel, /현재.*재확인하지 않음/);
  assert.match(result.logLink.semanticLabel, /일부만 확인/);
  assert.deepEqual(result.logLink.excludedCounts, [{ label: "운전자 개입", count: 7 }, { label: "개입 후 회복", count: 0 }]);
  assert.equal(result.deviceQualified, false);
  assert.equal(result.applyEnabled, false);
  Object.assign(envelope.log_link, { session_link: "unlinked", originals_preservation: "reference_only", semantic_mapping: "per_signal_verified" });
  const oldLog = createCandidateReportModel(envelope, parameters);
  assert.match(oldLog.logLink.sessionLabel, /미연결.*과거 로그/);
  assert.match(oldLog.logLink.preservationLabel, /참조만.*보존 확인 아님/);
  assert.match(oldLog.logLink.semanticLabel, /신호별.*주행 검증 아님/);
});

test("missing or forged extraction metadata never upgrades original verification or hides a valid report", async () => {
  const { parameters, envelope } = await candidateFixture();
  const original = createCandidateReportModel(envelope, parameters);
  assert.equal(original.logLink, null);
  assert.match(original.integrityLabel, /원본 로그 연계 미검증/);
  envelope.report.source_kind = "recorded";
  const variations = [{ source_bytes_verified_at_generation: false }, { schema_decoded: false }, { source_count: -1 },
    { source_count: 33 }, { epoch_count: Infinity }, { epoch_count: 33 }, { originals_current_availability: "verified_now" },
    { session_link: "fake" }, { originals_preservation: "all_saved" }, { semantic_mapping: "everything_verified" },
    { excluded_counts: { driver_intervention: 1e9 + 1 } }, { excluded_counts: { driver_intervention: "7" } }, { reasons: [null] }];
  for (const patch of variations) {
    envelope.log_link = { ...logLinkFixture(), ...patch };
    const result = createCandidateReportModel(envelope, parameters);
    assert.equal(result.available, true);
    assert.equal(result.items.length, 47);
    assert.equal(result.logLink.available, false);
    assert.match(result.integrityLabel, /원본 로그 연계 미검증/);
  }
  envelope.report.source_kind = "synthetic";
  envelope.log_link = logLinkFixture();
  assert.equal(createCandidateReportModel(envelope, parameters).logLink.available, false);
});

test("extraction provenance drops raw paths, hashes, source identities, objectives and unknown code text", async () => {
  const { parameters, envelope } = await candidateFixture();
  envelope.report.source_kind = "recorded";
  envelope.log_link = { ...logLinkFixture(), path: "/data/private/raw", hash: "privatehash", source_id: "private-id", objective: "private-objective",
    reasons: ["session_unlinked", "/data/private/reason", "<img onerror=private>"], excluded_counts: { "/private/path": 8, driver_intervention: 2 } };
  const result = createCandidateReportModel(envelope, parameters);
  assert.equal(result.logLink.available, true);
  assert.deepEqual(result.logLink.reasons, ["세션 미연결"]);
  assert.deepEqual(result.logLink.excludedCounts, [{ label: "운전자 개입", count: 2 }]);
  assert.doesNotMatch(JSON.stringify(result), /private|onerror|objective|source_id|hash/);
});

test("automatic analysis phases and stale snapshots hide previous candidates while keeping safe version metadata", async () => {
  const { parameters, envelope } = await candidateFixture();
  Object.assign(envelope, { attempt: { state: "running", job_id: "/private/job", source_start_ns: 10e9, source_end_ns: 12e9,
    cutoff_mono_ns: 13e9, source_count: 2, processed_windows: 2, blocked_windows: 1,
    windows: [{ source_start_ns: 10e9, source_end_ns: 11e9, state: "ready" }, { source_start_ns: 11e9, source_end_ns: 12e9, state: "blocked" }] },
    interval_seconds: 180, result_revision: 8, last_success: { job_id: "/private/old-job", source_end_ns: 9e9, result_revision: 7 }, stale: false });
  for (const state of ["waiting", "running", "partial", "failed", "stopped", "invalid"]) {
    const result = createCandidateReportModel({ ...envelope, state }, parameters);
    assert.equal(result.available, false);
    assert.deepEqual(result.items, []);
    assert.equal(result.periodic.sourceEndSeconds, 12);
    assert.equal(result.periodic.sourceStartSeconds, 10);
    assert.equal(result.periodic.cutoffSeconds, 13);
    assert.equal(result.periodic.intervalSeconds, 180);
    assert.equal(result.periodic.publicationRevision, 8);
    assert.equal(result.periodic.latestWindowStartSeconds, 11);
    assert.equal(result.periodic.readyWindows, 1);
    assert.equal(result.periodic.blockedWindows, 1);
    assert.equal(result.periodic.resultRevision, 7);
    assert.doesNotMatch(JSON.stringify(result), /private|job_id/);
  }
  const stale = createCandidateReportModel({ ...envelope, stale: true }, parameters);
  assert.equal(stale.state, "stale");
  assert.equal(stale.available, false);
  assert.match(stale.stateLabel, /최신 분석 미확인/);
  const complete = createCandidateReportModel(envelope, parameters);
  assert.equal(complete.available, true);
  assert.equal(complete.periodic.resultSourceEndSeconds, 9);
  assert.equal(complete.applyEnabled, false);
  envelope.attempt.windows[1].state = "failed";
  assert.equal(createCandidateReportModel(envelope, parameters).available, false);
});

test("static reports have no automatic phase and malformed version or time values remain unknown", async () => {
  const { parameters, envelope } = await candidateFixture();
  assert.equal(createCandidateReportModel(envelope, parameters).periodic, null);
  Object.assign(envelope, { attempt: { source_end_ns: Infinity }, last_success: { source_end_ns: -1, result_revision: "/private/revision" } });
  const result = createCandidateReportModel(envelope, parameters);
  assert.equal(result.periodic.sourceEndSeconds, null);
  assert.equal(result.periodic.resultSourceEndSeconds, null);
  assert.equal(result.periodic.resultRevision, null);
  assert.doesNotMatch(JSON.stringify(result), /private/);
});

test("unknown failed window ranges retain coverage counts without inventing zero or weakening candidate checks", async () => {
  const { parameters, envelope } = await candidateFixture();
  Object.assign(envelope, { state: "partial", reason: "periodic_segment_missing", stale: false,
    attempt: { processed_windows: 2, blocked_windows: 0, windows: [
      { source_start_ns: 1e9, source_end_ns: 2e9, state: "ready" }, { source_start_ns: null, source_end_ns: null, state: "failed" },
    ] } });
  const result = createCandidateReportModel(envelope, parameters);
  assert.equal(result.available, false);
  assert.equal(result.periodic.readyWindows, 1);
  assert.equal(result.periodic.failedWindows, 1);
  assert.equal(result.periodic.latestWindowStartSeconds, null);
  assert.equal(result.periodic.latestWindowEndSeconds, null);
  assert.match(result.periodic.reasonLabel, /세그먼트.*누락/);
  envelope.state = "ready";
  assert.equal(createCandidateReportModel(envelope, parameters).available, false);
  envelope.attempt.windows[1].state = "blocked";
  assert.equal(createCandidateReportModel(envelope, parameters).periodic.failedWindows, 0);
  assert.equal(createCandidateReportModel(envelope, parameters).available, false);
  envelope.attempt.windows[1].state = "ready";
  assert.equal(createCandidateReportModel(envelope, parameters).available, false);
});

async function requestFixture() {
  const { parameters, envelope } = await candidateFixture();
  const candidate = envelope.report.items.find((item) => item.key === "PathOffset");
  Object.assign(candidate, { status: "search_candidate", candidate_raw: 20, candidate_physical: .2,
    validation: { numerical_checks_pass: true, split_independent: true, holdout_objective_pass: true } });
  return { parameters, candidate_report: envelope, capabilities: { control_configured: true, request_registration_enabled: true, pc_simulation_enabled: true },
    request_state: { state: "ready", reason: null },
    session: { state: "idle", session_id: null }, current_request_context: { target_id: "pc-fixture", scope: "pc_simulation", session_id: "pc-session",
      artifact_sha256: "a".repeat(64), result_revision: 1, settings_revision: 0, dependency_digest: "b".repeat(64),
      items: parameters.map((item) => ({ item_key: item.key, eligible: item.key === "PathOffset", reason: item.key === "PathOffset" ? null : "request_candidate_not_available", expected_current_raw: 0 })) } };
}

test("request registration is separate from apply and serializes only the nine bound request fields", async () => {
  const payload = await requestFixture();
  const model = createControlAdvisorModel(payload);
  assert.equal(model.candidateReport.items.filter((item) => item.request.eligible).length, 1);
  assert.equal(model.parameters.filter((item) => item.request.eligible).length, 1);
  assert.ok(model.candidateReport.items.every((item) => !item.applyEnabled && typeof item.request.reason === "string"));
  const body = buildApplyRequestBody(payload, "PathOffset", "ui-request-one");
  assert.deepEqual(Object.keys(body).sort(), ["request_id", "target_id", "session_id", "artifact_sha256", "result_revision", "item_key",
    "expected_current_raw", "expected_settings_revision", "dependency_digest"].sort());
  assert.equal(body.expected_current_raw, 0);
  assert.equal(body.expected_settings_revision, 0);
  assert.equal(Object.hasOwn(body, "candidate_raw"), false);
  assert.equal(Object.hasOwn(body, "adapter"), false);
  assert.equal(model.applyEnabled, false);
  assert.equal(model.recommendationsEnabled, false);
});

test("live cards expose only the verified log candidate whose request binding still matches", async () => {
  const payload = await requestFixture();
  payload.candidate_report.report.source_kind = "recorded";
  const row = payload.candidate_report.report.items.find((item) => item.key === "CruiseMaxVals0");
  Object.assign(row, { status: "search_candidate", current_raw: 100, current_physical: 1, candidate_raw: 130, candidate_physical: 1.3,
    validation: { numerical_checks_pass: true, split_independent: true, holdout_objective_pass: true } });
  for (const entry of payload.current_request_context.items) {
    entry.eligible = entry.item_key === "CruiseMaxVals0";
    entry.expected_current_raw = entry.eligible ? 100 : 0;
  }
  const model = createControlAdvisorModel(payload);
  const card = model.parameters.find((item) => item.key === "CruiseMaxVals0");
  assert.equal(card.requestCandidate.currentRaw, 100);
  assert.equal(card.requestCandidate.candidateRaw, 130);
  assert.equal(card.requestCandidate.currentPhysical, 1);
  assert.equal(card.requestCandidate.candidatePhysical, 1.3);
  assert.match(card.requestCandidate.sourceLabel, /기록 자료.*실제 주행 검증 전/);
  assert.equal(card.candidate, null);
  assert.equal(card.applyEnabled, false);
  assert.equal(model.parameters.find((item) => item.key === "PathOffset").requestCandidate, null);
  payload.current_request_context.artifact_sha256 = "f".repeat(64);
  assert.equal(createControlAdvisorModel(payload).parameters.find((item) => item.key === "CruiseMaxVals0").requestCandidate, null);
});

test("stale candidates, changed bindings, wrong scope, and missing capabilities cannot register", async () => {
  const original = await requestFixture();
  for (const mutate of [
    (p) => { p.candidate_report.stale = true; },
    (p) => { p.current_request_context.artifact_sha256 = "f".repeat(64); },
    (p) => { p.current_request_context.items.find((item) => item.item_key === "PathOffset").expected_current_raw = 1; },
    (p) => { p.capabilities.pc_simulation_enabled = false; },
    (p) => { p.capabilities.request_registration_enabled = false; },
    (p) => { p.current_request_context.scope = "vehicle"; },
    (p) => { p.current_request_context.dependency_digest = "/private/dependency"; },
  ]) {
    const payload = structuredClone(original);
    mutate(payload);
    assert.throws(() => buildApplyRequestBody(payload, "PathOffset", "ui-request-one"), /request_evidence_unavailable/);
  }
  assert.throws(() => buildApplyRequestBody(original, "PathOffset", "../private"));
  assert.throws(() => buildApplyRequestBody(original, "LateralTorqueKd", "ui-request-one"));
  const vehicle = structuredClone(original);
  vehicle.current_request_context.scope = "vehicle";
  vehicle.session = { state: "running", session_id: "pc-session", source_mode: "live" };
  const item = createControlAdvisorModel(vehicle).candidateReport.items.find((row) => row.key === "PathOffset");
  assert.equal(item.request.eligible, true);
  assert.match(item.request.reason, /차량 쓰기는 보류/);
});

test("a PC store receipt never becomes a separate consumer observation or comparison", async () => {
  const payload = await requestFixture();
  payload.apply_requests = [{ request_id: "ui-request-one", item_key: "PathOffset", state: "stored", scope: "pc_simulation",
    store_receipt: { value: 20, revision: 1 }, consumer_observed: true, comparison_ready: true, comparison: null,
    reason: "/private/root", apply_enabled: true, device_qualified: true }];
  const model = createControlAdvisorModel(payload);
  assert.equal(model.applyRequests.items[0].storedValue, 20);
  assert.equal(model.applyRequests.items[0].consumerObserved, false);
  assert.equal(model.applyRequests.items[0].comparisonReady, false);
  assert.equal(model.applyRequests.items[0].canCancel, false);
  assert.equal(model.applyRequests.items[0].applyEnabled, false);
  assert.doesNotMatch(JSON.stringify(model), /private/);
  payload.apply_requests[0].state = "registered";
  assert.equal(createControlAdvisorModel(payload).applyRequests.items[0].canCancel, true);
  payload.apply_requests[0].state = "intent";
  assert.equal(createControlAdvisorModel(payload).applyRequests.items[0].canCancel, false);
});

test("native cookie authorization expires independently of manual keys and configuration qualification", () => {
  const payload = { capabilities: { control_configured: true, live_enabled: true }, auth: { authorized: true, method: "native_cookie", native_available: true } };
  assert.equal(advisorControlState(createControlAdvisorModel(payload)).canStartLive, true);
  payload.auth.authorized = false;
  assert.equal(advisorControlState(createControlAdvisorModel(payload)).canStartLive, false);
  assert.equal(advisorControlState(createControlAdvisorModel(payload), { token: "manual" }).canStartLive, true);
  payload.auth = { authorized: true, method: "untrusted" };
  assert.equal(advisorControlState(createControlAdvisorModel(payload)).canStartLive, false);
});

test("actual vehicle storage receipts remain visible without claiming consumer confirmation and restore is request-linked", async () => {
  const payload = await requestFixture();
  payload.capabilities.vehicle_write_available = true;
  payload.current_request_context.scope = "vehicle";
  payload.session = { state: "running", session_id: "pc-session", source_mode: "live" };
  payload.native_runtime = { state: "ready", writer_available: true, vehicle_confirmed: true, vehicle: { state: "observed", car_fingerprint: "CAR", physical_group_id: "a".repeat(32), vin_known: false },
    groups: [{ group_id: "a".repeat(32), car_fingerprint: "CAR", path: "/private" }], analysis_blockers: ["profile_missing"], private: "/private", reason: "vehicle_confirmation_required" };
  payload.apply_requests = [{ request_id: "native-one", item_key: "PathOffset", state: "stored", scope: "vehicle", store_receipt: { value: 20, revision: 1 },
    consumer_observed: false, comparison_ready: false, apply_enabled: false, device_qualified: false }];
  const model = createControlAdvisorModel(payload);
  assert.equal(model.applyRequests.items[0].storedValue, 20);
  assert.equal(model.applyRequests.items[0].canRestore, true);
  assert.equal(model.applyRequests.items[0].stateLabel, "차량 설정 저장됨 · 소비 확인 대기");
  assert.doesNotMatch(model.applyRequests.items[0].recordedStateLabel, /PC 모의/);
  assert.equal(model.applyRequests.items[0].consumerObserved, false);
  const candidate = model.candidateReport.items.find((item) => item.key === "PathOffset");
  assert.match(candidate.request.reason, /조건을 다시 확인/);
  assert.equal(candidate.request.writesVehicle, true);
  assert.match(model.parameters.find((item) => item.key === "PathOffset").reason, /실제 소비값/);
  payload.capabilities.vehicle_write_available = false;
  assert.equal(createControlAdvisorModel(payload).candidateReport.items.find((item) => item.key === "PathOffset").request.writesVehicle, false);
  payload.capabilities.vehicle_write_available = true;
  assert.equal(model.nativeRuntime.writerAvailable, true);
  assert.doesNotMatch(JSON.stringify(model), /private/);
  payload.request_state.state = "failed";
  assert.equal(createControlAdvisorModel(payload).applyRequests.items[0].canRestore, false);
  payload.request_state.state = "ready";
  payload.native_runtime.writer_available = false;
  payload.native_runtime.offroad_restore_available = true;
  const offroad = createControlAdvisorModel(payload).applyRequests.items[0];
  assert.equal(offroad.canRestore, true);
  assert.equal(offroad.restoreMode, "offroad");
  assert.equal(offroad.consumerObserved, false);
  assert.equal(createControlAdvisorModel(payload).candidateReport.items.find((item) => item.key === "PathOffset").request.writesVehicle, false);
});

test("separately validated comparison stays observational and drops private or nonfinite metadata", async () => {
  const payload = await requestFixture();
  const metric = { count: 10, rmse: .3, bias: .1, p95_absolute_error: .5 };
  const summary = { sample_count: 15, eligible_count: 10, excluded_count: 4, provisional_count: 1, eligible_duration_ns: 2e9,
    raw_metrics: metric, eligible_metrics: metric };
  const comparison = { schema_version: 1, state: "comparison_ready", axis: "lateral", scope: "observational_generated", target_unit: "m/s^2",
    before: summary, after: summary, matched: { common_bin_count: 2, before: metric, after: { ...metric, rmse: .1 },
      after_minus_before: { rmse: -.2, bias: 0, p95_absolute_error: -.1 } }, boundary: { path: "/private/raw" },
    reason_codes: ["/private/reason"], consumer_application_ack: false, causal_improvement_proven: false, apply_enabled: false, device_qualified: false };
  payload.apply_requests = [{ request_id: "ui-request-one", item_key: "PathOffset", scope: "pc_simulation", state: "comparison_ready",
    consumer_observed: true, comparison_ready: true, store_receipt: { value: 20, revision: 1 }, comparison }];
  const item = createControlAdvisorModel(payload).applyRequests.items[0];
  assert.equal(item.consumerObserved, true);
  assert.equal(item.comparisonReady, true);
  assert.equal(item.comparison.matched.deltaRmse, -.2);
  assert.equal(item.comparison.before.eligibleSeconds, 2);
  assert.equal(item.comparison.causalImprovementProven, false);
  assert.match(item.comparison.scopeLabel, /생성 자료.*실제 주행 아님/);
  assert.doesNotMatch(JSON.stringify(item), /private|boundary/);
  for (const state of ["failed", "waiting"]) {
    payload.request_state = { state, reason: "request_status_unavailable" };
    const historicalModel = createControlAdvisorModel(payload);
    const historical = historicalModel.applyRequests.items[0];
    assert.equal(historical.current, false);
    assert.match(historical.stateLabel, /이전 결과.*현재 상태 미확인/);
    assert.equal(historical.consumerObserved, false);
    assert.equal(historical.comparisonReady, false);
    assert.equal(historical.storedValue, 20);
    assert.equal(historical.storedRevision, 1);
    assert.deepEqual(historical.comparison, item.comparison);
    assert.equal(historicalModel.parameters.find((entry) => entry.key === "PathOffset").request.eligible, false);
    payload.apply_requests[0].state = "registered";
    assert.equal(createControlAdvisorModel(payload).applyRequests.items[0].canCancel, false);
    payload.apply_requests[0].state = "comparison_ready";
  }
  payload.request_state = { state: "ready", reason: null };
  assert.equal(createControlAdvisorModel(payload).applyRequests.items[0].comparisonReady, true);
  comparison.causal_improvement_proven = true;
  assert.equal(createControlAdvisorModel(payload).applyRequests.items[0].comparisonReady, false);
  comparison.causal_improvement_proven = false;
  comparison.matched.after.rmse = Infinity;
  assert.equal(createControlAdvisorModel(payload).applyRequests.items[0].comparison.matched.after.rmse, null);
  comparison.state = "held";
  Object.assign(payload.apply_requests[0], { state: "stored", reason: "comparison_held", consumer_observed: false, comparison_ready: false });
  const held = createControlAdvisorModel(payload).applyRequests.items[0];
  assert.equal(held.consumerObserved, false);
  assert.equal(held.comparisonReady, false);
  assert.equal(held.comparison.state, "held");
  assert.match(held.reason, /현재 비교 보류/);
});

function reviewSummaryFixture() {
  return { capabilities: { control_configured: true, review_export_enabled: true }, session: { state: "stopped", parking_confirmed: false },
    parking_summary: { state: "ready", summary: { schema_version: 1, state: "ready", summary_id: "summary-fixture", revision: 2, session_id: null,
      source_kind: "generated_native", session_state: "running", historical_only: true,
      diagnostics: { invalidated: false, partial_window: true, finalized_range: { start_ns: 0, end_ns: 5e9 }, raw_tail: { start_ns: 5e9, end_ns: 6e9 },
        axes: { lateral: { sample_count: 10, eligible_count: 6, excluded_count: 3, provisional_count: 1, eligible_duration_ns: 2e9,
          epochs: [{ raw_metrics: { count: 10, rmse: .5 }, eligible_metrics: { count: 6, rmse: .2 } }] } } },
      requests: { inventory_scope: "ledger_snapshot", stored_count: 1, consumer_observed_count: 0, comparison_ready_count: 0 }, candidate: { candidate_count: 2 },
      source_quality: { collection_end_confirmation: "not_signaled", omissions: ["latest_diagnostic_partial", "/private/source"] },
      apply_enabled: false, recommendations_enabled: false, device_qualified: false } } };
}

test("parking, offroad and historical observations never substitute for one another", () => {
  const parking = { profile_id: "parking-host-unvalidated-v1", source_kind: "native_event", state: "park_observed", offroad_state: "onroad_observed" };
  const native = createControlAdvisorModel({ session: { parking, parking_confirmed: true } });
  assert.equal(native.parkingConfirmed, true);
  assert.match(native.parking.offroadLabel, /onroad/);
  const synthetic = createControlAdvisorModel({ session: { parking: { ...parking, source_kind: "synthetic_event" }, parking_confirmed: true } });
  assert.equal(synthetic.parkingConfirmed, false);
  assert.equal(synthetic.parking.synthetic, true);
  const stopped = createControlAdvisorModel({ session: { state: "stopped", parking: { ...parking, state: "unknown", offroad_state: "offroad_observed" },
    parking_confirmed: false, last_park_observation: parking } });
  assert.equal(stopped.parkingConfirmed, false);
  assert.equal(stopped.lastParking.state, "park_observed");
  assert.equal(stopped.lastParking.confirmed, false);
  assert.equal(createControlAdvisorModel({ session: { parking_confirmed: true } }).parkingConfirmed, false);
});

test("immutable summaries retain provisional ranges, omissions and distinct request evidence without reviving recommendations", () => {
  const payload = reviewSummaryFixture();
  const model = createControlAdvisorModel(payload);
  assert.equal(model.parkingSummary.available, true);
  assert.match(model.parkingSummary.stateLabel, /부분 구간/);
  assert.match(model.parkingSummary.sourceLabel, /생성.*실제 주행 아님/);
  assert.match(model.parkingSummary.endLabel, /종료 신호 미확인/);
  assert.equal(model.parkingSummary.axes[0].eligibleSeconds, 2);
  assert.equal(model.parkingSummary.storedCount, 1);
  assert.equal(model.parkingSummary.observedCount, 0);
  assert.equal(model.parkingSummary.comparisonCount, 0);
  assert.equal(model.parkingConfirmed, false);
  assert.equal(model.recommendationsEnabled, false);
  assert.doesNotMatch(JSON.stringify(model.parkingSummary), /private/);
  payload.parking_summary.state = "failed";
  assert.equal(createControlAdvisorModel(payload).parkingSummary.current, false);
  payload.parking_summary.summary.diagnostics.invalidated = true;
  assert.deepEqual(createControlAdvisorModel(payload).parkingSummary.axes, []);
  payload.parking_summary.summary.source_quality.collection_end_confirmation = "signal_only";
  assert.match(createControlAdvisorModel(payload).parkingSummary.endLabel, /완료 보장 아님/);
});

test("export bodies have only immutable summary identity and explicit raw opt-in", () => {
  const payload = reviewSummaryFixture();
  assert.deepEqual(buildReviewExportBody(payload, "export-request"), { request_id: "export-request", summary_id: "summary-fixture", summary_revision: 2, include_raw: false });
  assert.equal(buildReviewExportBody(payload, "export-request", true).include_raw, true);
  assert.throws(() => buildReviewExportBody(payload, "export-request", "true"));
  assert.throws(() => buildReviewExportBody(payload, "../private"));
  payload.parking_summary.summary.apply_enabled = true;
  assert.throws(() => buildReviewExportBody(payload, "export-request"));
});

test("export status is bounded, hides paths and offers download only for verified ready jobs", () => {
  const payload = reviewSummaryFixture();
  payload.review_exports = [{ job_id: "a".repeat(32), state: "ready", download_ready: true, include_raw: true, files: 3, bytes: 100, zip_bytes: 50, download_path: "/private/review.zip" },
    { job_id: "b".repeat(32), state: "interrupted", download_ready: true, zip_bytes: Infinity }, { job_id: "../private", state: "ready", download_ready: true }];
  const jobs = createControlAdvisorModel(payload).reviewExports.jobs;
  assert.equal(jobs.length, 2);
  assert.equal(jobs[0].canDownload, true);
  assert.equal(jobs[1].canDownload, false);
  assert.equal(jobs[1].zipBytes, null);
  assert.doesNotMatch(JSON.stringify(jobs), /private|download_path/);
});
