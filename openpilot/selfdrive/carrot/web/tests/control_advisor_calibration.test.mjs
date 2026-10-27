import assert from "node:assert/strict";
import test from "node:test";
import { buildTrialRequestBody, CALIBRATION_KEYS, createCalibrationReviewModel, createControlAdvisorModel, eventInferenceModel, comparisonAssessment } from "../src/features/control_advisor/model.js";

function fixture() {
  const columns = ["mono_ns", "target", "actual", "target_mono_ns", "actual_mono_ns", "independent_actual", "independent_mono_ns", "sent_request", "sent_mono_ns", "segment", "sent_raw", "stop_req", "jerk_upper", "jerk_lower"];
  const series = () => ({ unit: "m/s^2", actual_source: "controller_observation", independent_source: "sensor", sent_source: "sendcan_scc_control",
    columns, points: [["9007199254740993", .5, .4, "9007199254740993", "9007199254740993", .41, "9007199254740993", .6, "9007199254740993", 0, .6, 0, 2, 2]],
    raw_count: 2000, selected_count: 1 });
  const option = { id: "b".repeat(64), purpose: "manual_trial", parameter_key: "LongActuatorDelay", current_raw: 30, candidate_raw: 35, restore_raw: 30 };
  return { capabilities: { request_registration_enabled: true, pc_simulation_enabled: true }, request_state: { state: "ready" },
    candidate_report: { state: "ready", integrity_verified: true, artifact_sha256: "a".repeat(64), result_revision: 1,
      calibration_review: { schema_version: 1, parameter_key: "LongActuatorDelay", purpose: "manual_trial", state: "ready",
        baseline: { settings_raw: { LongActuatorDelay: 30, LateralTorqueKiV: 0 }, input_sha256: "c".repeat(64), profile_sha256: "d".repeat(64) },
        series: { lateral: series(), longitudinal: series() }, metrics: {}, exclusions: {}, reasons: [], trial_options: [option] } },
    current_request_context: { target_id: "pc", session_id: "session", scope: "pc_simulation", artifact_sha256: "a".repeat(64),
      result_revision: 1, settings_revision: 0, dependency_digest: "e".repeat(64),
      items: [{ item_key: "LongActuatorDelay", eligible: true, purpose: "manual_trial", expected_current_raw: 30, trial_ids: [option.id] }] } };
}

test("identified timing evidence exposes bounded experiments without presenting a software optimum", () => {
  const payload = fixture(), review = payload.candidate_report.calibration_review;
  review.schema_version = 2;
  review.diagnostics = { version: 1, method_id: "signal_stage_event_response_v1", causal_effect_proven: false, software_optimum_identified: false,
    stages: [{ id: "longitudinal_scc_to_acceleration", state: "identified_response", event_count: 8,
      lag_s: { estimate: .2, lower: .15, upper: .25 }, time_constant_s: { estimate: .4, lower: .3, upper: .5 } }],
    timing: { LongActuatorDelay: { state: "experiment_required", trial_plan: {
      max_candidates: 2, direction_effect: "unconfirmed", event_count_per_candidate: 8, max_observation_s: 900 } } } };
  const model = createCalibrationReviewModel(payload);
  assert.equal(model.available, true);
  assert.equal(model.diagnostics.stages[0].lag.upper, .25);
  assert.deepEqual(model.diagnostics.plan, { events: 8, seconds: 900 });
  assert.equal(model.trials[0].eligible, true);
  review.diagnostics.timing.LongActuatorDelay.trial_plan = null;
  assert.equal(createCalibrationReviewModel(payload).available, false);
  review.diagnostics.software_optimum_identified = true;
  assert.equal(createCalibrationReviewModel(payload).available, false);
});

test("event inference shows independent validation uncertainty and rejects reused holdout", () => {
  const source = { schema_version: 1, method: "event_bootstrap_v1", causal_improvement_proven: false,
    configuration: { confidence: .95, bootstrap_repetitions: 256 }, event_counts: { fit: 8, select: 6, holdout: 4 },
    holdout_evaluations: 1, minimum_effect: .03, selection: { effect: { lower: .04, upper: .08 } },
    holdout: { effect: { lower: -.01, upper: .09 } } };
  assert.equal(eventInferenceModel(source).holdout.lower, -.01);
  assert.equal(eventInferenceModel(source).counts.holdout, 4);
  assert.equal(eventInferenceModel({ ...source, holdout_evaluations: 2 }), null);
  assert.equal(eventInferenceModel({ ...source, causal_improvement_proven: true }), null);
});

test("comparison exposes a finite inconclusive result without enabling application", () => {
  const value = { schema_version: 1, method: "matched_event_bootstrap_v1", state: "inconclusive", ended: true,
    causal_improvement_proven: false, events_required: 8, observation_limit_s: 900, policy_sha256: "a".repeat(64),
    counts: { before_events: 8, after_events: 3, matched_pairs: 3 }, observed_after_s: 900, minimum_effect: .1,
    effect: null, unit: "s" };
  assert.equal(comparisonAssessment(value).ended, true);
  assert.equal(comparisonAssessment(value).state, "inconclusive");
  assert.equal(comparisonAssessment({ ...value, state: "improved", causal_improvement_proven: true }), null);
  assert.equal(comparisonAssessment({ ...value, state: "observing" }), null);
});

test("calibration preserves original int64 timestamps and sends only the opaque trial selector", () => {
  const payload = fixture(), model = createCalibrationReviewModel(payload);
  assert.equal(model.available, true);
  assert.equal(model.series[0].points[0][0], "9007199254740993");
  assert.equal(model.trials[0].eligible, true);
  const body = buildTrialRequestBody(payload, "LongActuatorDelay", "b".repeat(64), "request");
  assert.equal(body.purpose, "manual_trial");
  assert.equal(body.trial_id, "b".repeat(64));
  assert.equal(Object.hasOwn(body, "candidate_raw"), false);
  assert.doesNotThrow(() => JSON.stringify(model));
});

test("invalid timestamps, unbound trial IDs and nonzero Ki cannot enable a trial", () => {
  const payload = fixture();
  assert.throws(() => buildTrialRequestBody(payload, "LongActuatorDelay", "f".repeat(64), "request"));
  payload.current_request_context.items[0].trial_ids = [];
  assert.equal(createCalibrationReviewModel(payload).trials[0].eligible, false);
  const nonzero = fixture(); nonzero.candidate_report.calibration_review.baseline.settings_raw.LateralTorqueKiV = 20;
  assert.equal(createCalibrationReviewModel(nonzero).trials[0].eligible, false);
  const corrupted = fixture(); corrupted.candidate_report.calibration_review.series.lateral.points[0][0] = 9007199254740992;
  assert.equal(createCalibrationReviewModel(corrupted).available, false);
});

test("the current six-goal catalogue shows exactly the six agreed parameters", () => {
  const payload = fixture();
  payload.analysis_goals = { options: [{ id: "custom_sr_v1", label: "CustomSR", description: "CustomSR" }] };
  payload.parameters = [...CALIBRATION_KEYS, "LateralTorqueKiV", "CruiseMaxVals0"].map((key) => ({ key, axis: key === "LongActuatorDelay" ? "longitudinal" : "lateral" }));
  assert.deepEqual(new Set(createControlAdvisorModel(payload).parameters.map((item) => item.key)), new Set(CALIBRATION_KEYS));
  const goals = ["custom_sr_v1", "lateral_torque_kp_v1", "lateral_torque_kf_v1", "steer_actuator_delay_v1", "lat_smooth_sec_v1", "long_actuator_delay_v1"];
  payload.analysis_goals.options = goals.map((id) => ({ id, version: 1, label: id, description: id }));
  assert.deepEqual(createControlAdvisorModel(payload).analysisGoals.options.map((item) => item.id), goals);
});

test("analysis location is independent of recording source mode and old payloads stay on device", () => {
  const payload = fixture();
  payload.session = { state: "running", source_mode: "live", session_id: "same-recording" };
  assert.equal(createControlAdvisorModel(payload).analysisExecutor, "device");
  payload.analysis_execution = { executor: "remote_pc", revision: "a".repeat(32), pending_job: "b".repeat(64) + "-12345678" };
  const remote = createControlAdvisorModel(payload);
  assert.equal(remote.analysisExecutor, "remote_pc");
  assert.equal(remote.executorAvailable, true);
  assert.equal(remote.sessionId, "same-recording");
  assert.equal(remote.sourceMode, "live");
  assert.equal(remote.remoteJob, payload.analysis_execution.pending_job);
});
