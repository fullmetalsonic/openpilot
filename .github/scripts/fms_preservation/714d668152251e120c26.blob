import { buildApplyRequestBody, buildReviewExportBody, buildReviewRegistrationBody, createControlAdvisorModel, nativeReasonMessage } from "./model.js";
import { createControlAdvisorView } from "./view.js";

const STATUS_PATH = "/api/control_advisor/status";
const REQUEST_PATH = "/api/control_advisor/apply-requests";
const EXPORT_PATH = "/api/control_advisor/export-jobs";
const ACTION_PATHS = Object.freeze({ start: "/api/control_advisor/start", stop: "/api/control_advisor/stop", register_request: REQUEST_PATH,
  create_export_job: EXPORT_PATH, redeem_access: "/api/control_advisor/access/redeem",
  confirm_vehicle: "/api/control_advisor/vehicle/confirm", backup_vehicle: "/api/control_advisor/vehicle/backup", restore_vehicle: "/api/control_advisor/vehicle/restore" });

export function advisorErrorMessage(error) {
  const code = error?.code || error?.message;
  if (typeof code === "string" && /^(native|vehicle)_/.test(code)) return nativeReasonMessage(code);
  const reviewErrors = { review_not_final: "초안은 등록할 수 없습니다. final 상태의 검토 JSON을 선택해 주세요.",
    review_json_invalid: "검토 JSON 형식이 올바르지 않습니다.", review_json_too_large: "검토 JSON 요청은 128KiB 이하여야 합니다.",
    review_binding_mismatch: "선택한 검토 결과가 이 내보내기 묶음·요약 버전과 일치하지 않습니다.",
    review_source_unavailable: "현재 완료 묶음을 검증할 수 없어 신규 등록을 보류했습니다. 기존 검토 이력은 조회할 수 있습니다.",
    review_request_conflict: "같은 요청 번호에 다른 검토 내용이 이미 있습니다.", review_revision_conflict: "검토 버전이 기존 이력과 충돌합니다.",
    review_previous_hash_mismatch: "이전 검토 연결이 일치하지 않습니다.", review_import_busy: "내보내기 작업 중입니다. 완료 후 다시 등록해 주세요.",
    review_store_limit: "검토 저장 개수·용량 한도를 넘었습니다.", review_store_corrupt: "검토 이력 무결성을 확인하지 못했습니다.",
    review_store_incomplete: "검토 저장이 완료되지 않아 이력을 확인할 수 없습니다." };
  if (Object.hasOwn(reviewErrors, code)) return reviewErrors[code];
  if (error?.message === "request_evidence_unavailable") return "현재 후보·세션·설정 연결 근거가 달라 요청을 보류했습니다. 최신 상태를 다시 확인합니다.";
  if (error?.message === "review_export_unavailable") return "내보낼 불변 요약과 내보내기 설정을 확인해 주세요.";
  if (error?.status === 401 || error?.status === 403) return "세션 제어 권한과 세션 키를 확인해 주세요.";
  if (error?.status === 409) return "다른 세션이 진행 중이거나 세션 상태가 바뀌었습니다. 현재 상태를 다시 확인합니다.";
  if (error?.status === 413) return "요청 크기가 허용 범위를 넘었습니다.";
  if (error?.name === "AbortError") return "서버 응답이 지연됐습니다. 마지막 상태를 표시하고 있습니다.";
  return "분석 서버에 연결하지 못했습니다. 마지막 상태를 표시하고 있습니다.";
}

export function createAdvisorClient({ fetchImpl = globalThis.fetch, timeoutMs = 5000 } = {}) {
  let active = null;
  async function request(action = "status", { token = "", body, requestId, jobId, rawJsonBody } = {}) {
    if (typeof fetchImpl !== "function") throw new Error("fetch_unavailable");
    let path = action === "status" ? STATUS_PATH : Object.hasOwn(ACTION_PATHS, action) ? ACTION_PATHS[action] : null;
    if (["request_status", "cancel_request"].includes(action)) {
      if (typeof requestId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(requestId)) throw new Error("invalid_request_id");
      path = `${REQUEST_PATH}/${encodeURIComponent(requestId)}${action === "cancel_request" ? "/cancel" : ""}`;
    }
    if (["cancel_export_job", "grant_export_download"].includes(action)) {
      if (typeof jobId !== "string" || !/^[a-f0-9]{32}$/.test(jobId)) throw new Error("invalid_export_job");
      path = `${EXPORT_PATH}/${jobId}/${action === "cancel_export_job" ? "cancel" : "download"}`;
    }
    if (["register_codex_review", "query_codex_reviews"].includes(action)) {
      if (typeof jobId !== "string" || !/^[a-f0-9]{32}$/.test(jobId)) throw new Error("invalid_export_job");
      path = `${EXPORT_PATH}/${jobId}/reviews${action === "query_codex_reviews" ? "/query" : ""}`;
    }
    if (!path) throw new Error("unknown_advisor_action");
    if (rawJsonBody !== undefined && (action !== "register_codex_review" || typeof rawJsonBody !== "string"
      || new TextEncoder().encode(rawJsonBody).length > 128 * 1024)) throw new Error("review_json_too_large");
    active?.abort();
    const controller = new AbortController();
    active = controller;
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    const readOnly = action === "status" || action === "request_status";
    const options = { method: readOnly ? "GET" : "POST", signal: controller.signal, credentials: "same-origin", cache: "no-store" };
    if (!readOnly) {
      options.headers = { "Content-Type": "application/json" };
      if (token) options.headers["X-Control-Advisor-Token"] = token;
      options.body = rawJsonBody === undefined ? JSON.stringify(body) : rawJsonBody;
    }
    try {
      const response = await fetchImpl(path, options);
      if (!response.ok) {
        const error = new Error("advisor_request_failed");
        error.status = response.status;
        try { const failure = await response.json(); if (/^(?:review|native|vehicle)_[a-z0-9_]{1,112}$/.test(failure?.error || "")) error.code = failure.error; } catch {}
        throw error;
      }
      const data = await response.json();
      if (!data || typeof data !== "object" || Array.isArray(data) || data.ok !== true) throw new Error("invalid_advisor_response");
      return data;
    } finally {
      clearTimeout(timeout);
      if (active === controller) active = null;
    }
  }
  return { request, cancel() { active?.abort(); active = null; } };
}

function requestId() {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    return Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, "0")).join("");
  }
  // This is an idempotence identifier, never a session credential.
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function consumeNativeGrant(location, history) {
  if (!location || typeof location.hash !== "string" || !location.hash.startsWith("#ca_grant=")) return null;
  const fragment = location.hash.slice("#ca_grant=".length);
  // Remove credentials before any fetch, DOM rendering, or route handling.
  history.replaceState(null, "", `${location.pathname}${location.search}`);
  if (!/^[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9_-]{16,256}$/.test(fragment)) return null;
  const dot = fragment.indexOf(".");
  return { grant_id: fragment.slice(0, dot), secret: fragment.slice(dot + 1) };
}

let nativeGrantRedemption = null;
let nativeGrantOpenPending = false;
function openNativeAdvisorOnce() {
  const entry = globalThis.document?.getElementById("controlAdvisorEntry");
  if (!nativeGrantOpenPending || !entry || typeof globalThis.openAppDialog !== "function") return;
  nativeGrantOpenPending = false;
  entry.click();
}
function redeemNativeGrant() {
  if (nativeGrantRedemption) return nativeGrantRedemption;
  const grant = consumeNativeGrant(globalThis.location, globalThis.history);
  if (!grant) return null;
  const client = createAdvisorClient();
  nativeGrantRedemption = client.request("redeem_access", { body: grant })
    .then(() => { nativeGrantOpenPending = true; openNativeAdvisorOnce(); return null; },
      () => "기기 연결 확인이 만료되었거나 거부됐습니다. 기기 화면에서 새 연결 QR을 열어 주세요.");
  return nativeGrantRedemption;
}

const controlAuthorized = (model, token) => model.auth.authorized || token.trim().length > 0;

export function mountControlAdvisor(container, { fetchImpl = globalThis.fetch, pollMs = 2000, timeoutMs = 5000 } = {}) {
  const client = createAdvisorClient({ fetchImpl, timeoutMs });
  const document = container.ownerDocument;
  let disposed = false;
  let timer = null;
  let sequence = 0;
  let polling = false;
  let payload = {};
  let token = "";
  const requestIds = new Map();
  const exportIds = new Map();
  const reviewFiles = new Map();
  const fileReads = new Map();
  const restoreIds = new Map();
  const ui = { tab: "live", axis: "all", interval: 300, includeRaw: false, vehicleGroup: "", lastRestore: null,
    reviewFiles: {}, selectedReviews: {}, pending: false, connected: false, error: null, actionError: null };

  function render() {
    if (!disposed) view.update(createControlAdvisorModel(payload), ui);
  }

  function accept(data) {
    payload = { ...payload, ...data, capabilities: data.capabilities || payload.capabilities,
      session: data.session || payload.session, parameters: data.parameters || payload.parameters,
      candidate_report: data.candidate_report || null, parking_summary: data.parking_summary || null,
      review_exports: Array.isArray(data.review_exports) ? data.review_exports : [], review_export_state: data.review_export_state || null };
    ui.connected = true;
    ui.error = null;
    render();
  }

  function schedule(delay = pollMs) {
    clearTimeout(timer);
    if (!disposed && !ui.pending && !document.hidden) timer = setTimeout(poll, delay);
  }

  async function poll() {
    if (disposed || ui.pending || polling || document.hidden) return;
    polling = true;
    const serial = ++sequence;
    try {
      const data = await client.request();
      if (!disposed && sequence === serial) accept(data);
    } catch (error) {
      if (!disposed && sequence === serial) {
        ui.error = advisorErrorMessage(error);
        ui.connected = false;
        render();
      }
    } finally {
      polling = false;
      schedule();
    }
  }

  async function action(kind, mode) {
    const model = createControlAdvisorModel(payload);
    if (disposed || ui.pending || !ui.connected || !controlAuthorized(model, token) || !model.controlConfigured) return;
    if (kind === "start" && (model.active || !(mode === "live" ? model.liveEnabled : model.syntheticEnabled))) return;
    if (kind === "stop" && (!model.active || !model.sessionId || model.state === "stopping")) return;
    ui.pending = true;
    ui.actionError = null;
    clearTimeout(timer);
    client.cancel();
    const serial = ++sequence;
    render();
    try {
      const body = kind === "start" ? { mode, interval_seconds: ui.interval, request_id: requestId() } : { session_id: model.sessionId };
      const data = await client.request(kind, { token: token.trim(), body });
      if (!disposed && sequence === serial) accept(data);
    } catch (error) {
      if (!disposed && sequence === serial) {
        ui.actionError = advisorErrorMessage(error);
        render();
      }
    } finally {
      ui.pending = false;
      render();
      schedule(0);
    }
  }

  async function applyRequestAction(kind, key) {
    const model = createControlAdvisorModel(payload);
    if (disposed || ui.pending || !ui.connected || !controlAuthorized(model, token) || !model.controlConfigured || !model.applyRequests.registrationEnabled) return;
    ui.pending = true;
    ui.actionError = null;
    clearTimeout(timer);
    client.cancel();
    const serial = ++sequence;
    render();
    try {
      let body, id;
      if (kind === "register_request") {
        const context = payload.current_request_context || {};
        const bindingKey = JSON.stringify([context.target_id, context.session_id, context.artifact_sha256, context.result_revision,
          context.settings_revision, context.dependency_digest, key]);
        const previous = requestIds.get(bindingKey);
        const summary = previous && payload.apply_requests?.find((item) => item.request_id === previous);
        id = previous && !["expired", "cancelled", "rejected"].includes(summary?.state) ? previous : requestId();
        if (!requestIds.has(bindingKey) && requestIds.size >= 128) requestIds.delete(requestIds.keys().next().value);
        requestIds.set(bindingKey, id);
        body = buildApplyRequestBody(payload, key, id);
      } else {
        if (!model.applyRequests.items.some((item) => item.requestId === key && item.canCancel)) throw new Error("request_evidence_unavailable");
        id = key;
        body = {};
      }
      const data = await client.request(kind, { token: token.trim(), body, requestId: id });
      if (!data.request || typeof data.request !== "object" || Array.isArray(data.request)) throw new Error("invalid_advisor_response");
      if (!disposed && sequence === serial) {
        payload.apply_requests = [data.request, ...(Array.isArray(payload.apply_requests) ? payload.apply_requests : [])
          .filter((item) => item.request_id !== data.request.request_id)].slice(0, 16);
        render();
      }
    } catch (error) {
      if (!disposed && sequence === serial) ui.actionError = advisorErrorMessage(error);
    } finally {
      ui.pending = false;
      render();
      schedule(0);
    }
  }

  async function reviewExportAction(kind, jobId) {
    const model = createControlAdvisorModel(payload);
    if (disposed || ui.pending || !ui.connected || !controlAuthorized(model, token) || !model.controlConfigured || !model.reviewExports.enabled) return;
    ui.pending = true;
    ui.actionError = null;
    clearTimeout(timer);
    client.cancel();
    const serial = ++sequence;
    render();
    try {
      let body = {};
      if (kind === "create_export_job") {
        const key = JSON.stringify([model.parkingSummary.summaryId, model.parkingSummary.revision, ui.includeRaw]);
        const previous = exportIds.get(key);
        const job = previous && payload.review_exports?.find((entry) => entry.request_id === previous);
        const id = previous && !["failed", "cancelled", "timed_out", "interrupted"].includes(job?.state) ? previous : requestId();
        if (!exportIds.has(key) && exportIds.size >= 32) exportIds.delete(exportIds.keys().next().value);
        exportIds.set(key, id);
        body = buildReviewExportBody(payload, id, ui.includeRaw);
      } else if (!model.reviewExports.jobs.some((job) => job.jobId === jobId && (kind === "grant_export_download" ? job.canDownload : job.canCancel))) {
        throw new Error("review_export_unavailable");
      }
      const data = await client.request(kind, { token: token.trim(), body, jobId });
      if (!disposed && sequence === serial) {
        if (kind === "grant_export_download") {
          const path = `${EXPORT_PATH}/${jobId}/download`;
          if (data.download_url !== path) throw new Error("invalid_advisor_response");
          const anchor = document.createElement("a");
          anchor.href = path;
          anchor.download = "";
          document.body.append(anchor);
          anchor.click();
          anchor.remove();
        } else {
          if (!data.job || typeof data.job !== "object" || Array.isArray(data.job)) throw new Error("invalid_advisor_response");
          payload.review_exports = [data.job, ...(Array.isArray(payload.review_exports) ? payload.review_exports : []).filter((job) => job.job_id !== data.job.job_id)].slice(0, 32);
        }
      }
    } catch (error) {
      if (!disposed && sequence === serial) ui.actionError = error?.status === 409
        ? "내보내기 작업이 진행 중이거나 요약·한도 조건이 바뀌었습니다. 현재 상태를 다시 확인합니다." : advisorErrorMessage(error);
    } finally {
      ui.pending = false;
      render();
      schedule(0);
    }
  }

  async function selectReviewFile(jobId, file) {
    if (disposed || ui.pending || !file) return;
    const generation = (fileReads.get(jobId) || 0) + 1;
    fileReads.set(jobId, generation);
    try {
      if (file.size > 128 * 1024 || !/\.json$/i.test(file.name)) throw new Error("review_json_too_large");
      const text = await file.text();
      if (disposed || fileReads.get(jobId) !== generation) return;
      const previous = reviewFiles.get(jobId);
      const id = previous?.text === text ? previous.id : requestId();
      buildReviewRegistrationBody(id, text);
      if (!reviewFiles.has(jobId) && reviewFiles.size >= 32) reviewFiles.delete(reviewFiles.keys().next().value);
      reviewFiles.set(jobId, { text, id });
      ui.reviewFiles[jobId] = { name: file.name.slice(0, 256), ready: true };
      ui.actionError = null;
    } catch (error) {
      if (disposed || fileReads.get(jobId) !== generation) return;
      reviewFiles.delete(jobId); delete ui.reviewFiles[jobId]; ui.actionError = advisorErrorMessage(error);
    }
    render();
  }

  async function codexReviewAction(kind, jobId) {
    const model = createControlAdvisorModel(payload);
    const job = model.reviewExports.jobs.find((item) => item.jobId === jobId);
    if (disposed || ui.pending || !ui.connected || !controlAuthorized(model, token) || !model.controlConfigured || !job) return;
    if (kind === "register_codex_review" && (!job.canDownload || !model.reviewExports.enabled || !reviewFiles.has(jobId))) return;
    ui.pending = true; ui.actionError = null; clearTimeout(timer); client.cancel();
    const serial = ++sequence;
    render();
    try {
      const file = reviewFiles.get(jobId);
      const data = await client.request(kind, { jobId, token: token.trim(), body: {},
        rawJsonBody: kind === "register_codex_review" ? buildReviewRegistrationBody(file.id, file.text) : undefined });
      if (!Array.isArray(data.reviews) || data.job_id !== jobId || data.summary_id !== job.summaryId
        || data.summary_revision !== job.summaryRevision) throw new Error("invalid_advisor_response");
      if (!disposed && serial === sequence) {
        payload.review_histories = { ...payload.review_histories, [jobId]: { ...data, current: true } };
        if (data.review?.review_hash) ui.selectedReviews[jobId] = data.review.review_hash;
      }
    } catch (error) {
      if (!disposed && serial === sequence) {
        if (payload.review_histories?.[jobId]) payload.review_histories[jobId].current = false;
        ui.actionError = advisorErrorMessage(error);
      }
    } finally { ui.pending = false; render(); schedule(0); }
  }

  async function vehicleAction(kind, originalId, restoreMode = "live") {
    const model = createControlAdvisorModel(payload);
    if (disposed || ui.pending || !ui.connected || !controlAuthorized(model, token) || !model.nativeRuntime.available) return;
    if (kind === "restore_vehicle" && !model.applyRequests.items.some((item) => item.requestId === originalId && item.canRestore && item.restoreMode === restoreMode)) return;
    if (kind === "restore_vehicle") ui.lastRestore = { requestId: originalId, stored: false };
    ui.pending = true; ui.actionError = null; clearTimeout(timer); client.cancel();
    const serial = ++sequence;
    render();
    try {
      let body = {};
      if (kind === "confirm_vehicle") body = { group_id: ui.vehicleGroup || null };
      if (kind === "restore_vehicle") {
        const restoreKey = `${originalId}:${restoreMode}`;
        if (!restoreIds.has(restoreKey)) {
          if (restoreIds.size >= 128) restoreIds.delete(restoreIds.keys().next().value);
          restoreIds.set(restoreKey, requestId());
        }
        body = { request_id: originalId, restore_id: restoreIds.get(restoreKey), mode: restoreMode };
      }
      const data = await client.request(kind, { body, token: token.trim() });
      if (disposed || serial !== sequence) return;
      if (data.native_runtime) payload.native_runtime = data.native_runtime;
      if (kind === "backup_vehicle") {
        if (!data.backup || typeof data.backup !== "object") throw new Error("invalid_advisor_response");
        const text = JSON.stringify(data.backup, null, 2);
        if (new TextEncoder().encode(text).length > 256 * 1024) throw new Error("invalid_advisor_response");
        const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
        const anchor = document.createElement("a");
        anchor.href = url; anchor.download = "control-advisor-vehicle-backup.json";
        document.body.append(anchor); anchor.click(); anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      if (kind === "restore_vehicle") ui.lastRestore = { requestId: originalId, stored: data.restore?.state === "stored", mode: restoreMode };
    } catch (error) { if (!disposed && serial === sequence) ui.actionError = advisorErrorMessage(error); }
    finally { ui.pending = false; render(); schedule(0); }
  }

  const view = createControlAdvisorView(container, {
    onTab(value) { ui.tab = value; render(); },
    onAxis(value) { ui.axis = value; render(); },
    onToken(value) { token = value; ui.actionError = null; render(); },
    onInterval(value) { if (!ui.pending && !createControlAdvisorModel(payload).active && [180, 300].includes(value)) ui.interval = value; },
    onStart(mode) { void action("start", mode); },
    onStop() { void action("stop"); },
    onApplyRequest(key) { void applyRequestAction("register_request", key); },
    onCancelRequest(id) { void applyRequestAction("cancel_request", id); },
    onExportRaw(value) { ui.includeRaw = value === true; render(); },
    onCreateExport() { void reviewExportAction("create_export_job"); },
    onCancelExport(id) { void reviewExportAction("cancel_export_job", id); },
    onDownloadExport(id) { void reviewExportAction("grant_export_download", id); },
    onReviewFile(id, file) { void selectReviewFile(id, file); },
    onRegisterReview(id) { void codexReviewAction("register_codex_review", id); },
    onQueryReviews(id) { void codexReviewAction("query_codex_reviews", id); },
    onSelectReview(id, hash) { ui.selectedReviews[id] = hash; render(); },
    onVehicleGroup(value) { ui.vehicleGroup = value; },
    onConfirmVehicle() { void vehicleAction("confirm_vehicle"); },
    onBackupVehicle() { void vehicleAction("backup_vehicle"); },
    onRestoreVehicle(id, mode) { void vehicleAction("restore_vehicle", id, mode); },
  });

  function visibilityChanged() {
    if (document.hidden) {
      clearTimeout(timer);
      if (!ui.pending) { ++sequence; client.cancel(); }
    } else schedule(0);
  }
  document.addEventListener("visibilitychange", visibilityChanged);
  render();
  const redemption = redeemNativeGrant();
  if (redemption) void redemption.then((error) => { if (!disposed) { ui.actionError = error; void poll(); } });
  else void poll();
  return () => {
    disposed = true;
    ++sequence;
    clearTimeout(timer);
    client.cancel();
    document.removeEventListener("visibilitychange", visibilityChanged);
    token = "";
    requestIds.clear();
    exportIds.clear();
    reviewFiles.clear();
    fileReads.clear();
    restoreIds.clear();
    payload.review_histories = {};
    view.destroy();
  };
}

let dialogSerial = 0;
let activeCleanup = null;

export function installControlAdvisorEntry() {
  const document = globalThis.document;
  const bar = document?.querySelector?.("#pageLogs .logs-tabbar");
  if (!bar || document.getElementById("controlAdvisorEntry")) { openNativeAdvisorOnce(); return; }
  const button = document.createElement("button");
  button.id = "controlAdvisorEntry";
  button.type = "button";
  button.className = "btn control-advisor-entry";
  button.textContent = "제어 분석";
  button.setAttribute("aria-haspopup", "dialog");
  button.addEventListener("click", () => {
    if (typeof openAppDialog !== "function") return;
    activeCleanup?.();
    const serial = ++dialogSerial;
    const mountId = `controlAdvisorMount${serial}`;
    const closed = openAppDialog({ mode: "alert", html: true, title: "제어 분석", messageHtml: `<div id="${mountId}"></div>`, confirmLabel: "닫기" });
    const mount = document.getElementById(mountId);
    activeCleanup = mount ? mountControlAdvisor(mount) : null;
    Promise.resolve(closed).finally(() => {
      if (dialogSerial === serial) { activeCleanup?.(); activeCleanup = null; }
    });
  });
  bar.insertBefore(button, bar.querySelector("#logsMenu"));
  openNativeAdvisorOnce();
}

if (typeof document !== "undefined") {
  redeemNativeGrant();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installControlAdvisorEntry, { once: true });
  else installControlAdvisorEntry();
  globalThis.addEventListener?.("carrot:pagechange", installControlAdvisorEntry);
}
