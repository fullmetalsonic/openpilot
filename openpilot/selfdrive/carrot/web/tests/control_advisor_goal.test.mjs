import assert from "node:assert/strict";
import test from "node:test";
import { createControlAdvisorModel, createCandidateReportModel, advisorControlState } from "../src/features/control_advisor/model.js";

test("manual analysis uses the whole trip and exposes an applied cutoff without rounding nanoseconds", () => {
  const model = createControlAdvisorModel({ auth: { authorized: true, method: "native_cookie" }, capabilities: { control_configured: true },
    session: { session_id: "trip", state: "running" }, analysis_control: { schema_version: 1, mode: "manual_or_park", scope: "trip_since_apply",
      state: "running", trigger: "manual", range_start_ns: "9007199254740993", range_end_ns: "9007202254740993",
      apply_cut_ns: "9007199254740993", progress: { completed_sources: 2, total_sources: 9 }, can_analyze: true } });
  assert.equal(model.analysisControl.durationSeconds, 3);
  assert.match(model.analysisControl.scopeLabel, /적용 성공 이후/);
  assert.equal(model.analysisControl.progress, "2 / 9개 로그");
  assert.equal(advisorControlState(model).canAnalyze, true);
  assert.equal(advisorControlState(model, { pending: true }).canAnalyze, false);
  assert.equal(advisorControlState(createControlAdvisorModel()).canAnalyze, false);
  assert.doesNotThrow(() => JSON.stringify(model));
});

test("goal catalogue exposes fixed choices and drops internal metadata", () => {
  const goal = { id: "fms_accel_defaults_v1", label: "FMS 기본 가속 기준", version: 1, description: "선택한 가속 정책과 비교",
    profile_path: "C:/private", source_sha256: "a".repeat(64), settings_raw: { CruiseMaxVals0: 160 } };
  const model = createControlAdvisorModel({ analysis_goals: { options: [goal] }, session: { analysis_goal: goal } });
  assert.equal(model.analysisGoals.options[0].id, goal.id);
  assert.equal(model.analysisGoals.selectedId, goal.id);
  assert.doesNotMatch(JSON.stringify(model.analysisGoals), /private|settings_raw|source_sha256/);
  assert.deepEqual(createControlAdvisorModel({ analysis_goals: { options: [{ ...goal, id: "../../other" }] } }).analysisGoals.options, []);
  assert.deepEqual(createControlAdvisorModel().analysisGoals.options, []);
});

test("batch result shows the observed settings epoch rather than implying a whole-window epoch", () => {
  const model = createCandidateReportModel({ state: "waiting", attempt: null, last_success: null, stale: false,
    analysis_mode: "continuous_batch", latest_epoch: { start_ns: 280e9, end_ns: 300e9 } });
  assert.equal(model.periodic.continuousBatch, true);
  assert.equal(model.periodic.epochDurationSeconds, 20);
  const invalid = createCandidateReportModel({ state: "waiting", attempt: null, stale: false,
    latest_epoch: { start_ns: 300e9, end_ns: 280e9 } });
  assert.equal(invalid.periodic.epochDurationSeconds, null);
  assert.equal(invalid.periodic.continuousBatch, false);
});
