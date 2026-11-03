"use strict";

const PREFIX = "/api/dashcam/pc-transfer";
let activePanel = null;
let opening = false;
const text = (key, fallback, values) => typeof getUIText === "function" ? getUIText(key, fallback, values) : fallback;

export function pcLogSelection(segments = []) {
  const selected = [...new Set(segments.filter((item) => typeof item === "string" && item))];
  return selected.length ? { segments: selected } : { scope: "latest_route" };
}

export function pcLogProgress(job) {
  const files = Array.isArray(job?.files) ? job.files : [];
  return {
    completed: job?.status === "completed" && files.length > 0 && files.every((item) => item.status === "acknowledged"),
    acknowledged: files.filter((item) => item.status === "acknowledged").length,
    total: files.length,
  };
}

export function pcLogQueueStatus(jobs = [], preferredId = null) {
  const active = jobs.filter((job) => !["completed", "failed", "canceled"].includes(job.status));
  if (active.length) {
    const status = ["transferring", "verifying", "queued"].find((value) => active.some((job) => job.status === value)) || "queued";
    return { status, active: active.length };
  }
  const job = jobs.find((item) => item.id === preferredId) || jobs[0];
  return { status: job?.status === "completed" && !pcLogProgress(job).completed ? "verifying" : job?.status || null, active: 0 };
}

export function createPcLogApi(fetcher = globalThis.fetch) {
  let csrfNonce = "";
  const request = async (method, path, body, binary = false, options = {}) => {
    const response = await fetcher(`${PREFIX}/${path}`, {
      method, redirect: "error", cache: "no-store", credentials: "same-origin",
      headers: { ...(body ? { "Content-Type": "application/json", "X-Carrot-CSRF": csrfNonce } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
      ...options,
    });
    if (binary && response.ok) return { data: await response.arrayBuffer(), range: response.headers.get("Content-Range"), sha256: response.headers.get("X-Content-SHA256") };
    const result = await response.json();
    if (!response.ok || !result.ok) {
      const error = new Error(result.error || "pc_transfer_failed");
      error.status = response.status;
      throw error;
    }
    if (typeof result.csrfNonce === "string") csrfNonce = result.csrfNonce;
    return result;
  };
  const action = (id, operation, body = {}, binary = false, options = {}) => {
    if (!/^[0-9a-f]{64}$/.test(id)) throw new Error("invalid_job_id");
    return request("POST", `jobs/${id}/${operation}`, body, binary, options);
  };
  return {
    jobs: () => request("GET", "status"),
    preview: (selection) => request("POST", "preview", selection),
    enqueue: (selection, previewId) => request("POST", "jobs", { selection, previewId, confirmed: true }),
    claim: (id, owner) => action(id, "claim", { owner }),
    transport: (context, operation, fields = {}, options = {}) => action(context.job.id, operation,
      { owner: context.owner, epoch: context.epoch, ...fields }, operation === "chunk", options),
  };
}

const PC_ENDPOINT = "http://minipc-amd5500u.tail8d9376.ts.net";
const FILE_CHUNK = 1024 * 1024;

export function validatePcState(value, manifest) {
  const job = value?.job;
  if (!job || job.id !== manifest.id || job.deviceId !== manifest.deviceId || !Array.isArray(job.files) || job.files.length !== manifest.files.length)
    throw new Error("invalid_receiver_receipt");
  const expected = new Map(manifest.files.map((file) => [file.id, file]));
  const found = new Map();
  for (const file of job.files) {
    const source = expected.get(file.id);
    if (!source || found.has(file.id) || file.size !== source.size || file.sha256 !== source.sha256
      || !Number.isSafeInteger(file.receivedBytes) || file.receivedBytes < 0 || file.receivedBytes > source.size
      || !["pending", "acknowledged"].includes(file.status)
      || (file.status === "acknowledged" && (file.receivedBytes !== source.size || !/^[A-Za-z0-9_-]{1,128}$/.test(file.receiptId || ""))))
      throw new Error("invalid_receiver_receipt");
    found.set(file.id, file);
  }
  if (job.status === "completed" && [...found.values()].some((file) => file.status !== "acknowledged")) throw new Error("invalid_receiver_receipt");
  return found;
}

export function createPhoneRelay(api, { fetcher = globalThis.fetch, allowTestHttp = false, owner = null } = {}) {
  const bytes = new Uint8Array(16);
  if (!owner) globalThis.crypto.getRandomValues(bytes);
  const transportOwner = owner || [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  let active = false, running = false, timer = null, current = null, controller = null, generation = 0;
  const endpointAllowed = (endpoint) => {
    if (endpoint === PC_ENDPOINT) return;
    const url = new URL(endpoint);
    if (!(allowTestHttp && url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
      && url.origin === endpoint)) throw new Error("receiver_redirect_rejected");
  };
  const remote = async (context, method, path, body = null, extra = {}) => {
    endpointAllowed(context.endpoint);
    const headers = { Authorization: `Bearer ${context.key}`, ...extra };
    if (path !== "/senders") {
      headers["X-Carrot-Device-Id"] = context.deviceId;
      headers["X-Carrot-Transport-Epoch"] = String(context.epoch);
      headers["X-Carrot-Transport-Id"] = context.owner;
    }
    const binary = body instanceof ArrayBuffer;
    if (body && !binary) headers["Content-Type"] = "application/json";
    const timeout = setTimeout(() => controller?.abort(), 30000);
    try {
      const response = await fetcher(context.endpoint + "/api/carrot-pc/v1" + path, { method, headers,
        body: body ? binary ? body : JSON.stringify(body) : undefined, redirect: "error", credentials: "omit", cache: "no-store", signal: controller?.signal });
      if (!response.ok) {
        const error = new Error(({401:"receiver_auth_failed",409:"receiver_conflict",413:"receiver_limit",429:"receiver_busy",502:"receiver_unavailable",503:"receiver_unavailable",504:"receiver_unavailable",507:"receiver_disk_full"})[response.status] || "receiver_rejected");
        const value = response.headers.get("Retry-After");
        const seconds = value && /^\d+$/.test(value) ? Number(value) : (Date.parse(value || "") - Date.now()) / 1000;
        error.retryAfter = Number.isFinite(seconds) ? Math.min(3600, Math.max(0, seconds)) : 0;
        throw error;
      }
      const value = await response.json();
      if (!value || value.ok !== true) throw new Error("invalid_receiver_response");
      return value;
    } catch (error) {
      if (error instanceof TypeError || error.name === "AbortError") throw new Error("receiver_unavailable");
      if (error instanceof SyntaxError) throw new Error("invalid_receiver_response");
      throw error;
    } finally { clearTimeout(timeout); }
  };
  const transmit = async (context) => {
    if (!context.deviceId) {
      const identity = await remote(context, "POST", "/senders", { version: 1 });
      if (Object.keys(identity).sort().join(",") !== "deviceId,endpoint,ok,version" || identity.version !== 1
        || identity.endpoint !== context.endpoint || !/^[0-9a-f]{32}$/.test(identity.deviceId || "")) throw new Error("invalid_receiver_response");
      context.deviceId = identity.deviceId;
    }
    if (context.manifest.deviceId !== context.deviceId) {
      const bound = await api.transport(context, "bind", { deviceId: context.deviceId });
      context.manifest.deviceId = context.deviceId; context.job = bound.job;
    }
    const post = () => remote(context, "POST", "/jobs", { version: 1, job: context.manifest });
    let state = validatePcState(await post(), context.manifest);
    if (context.job.releasePending) {
      const result = await remote(context, "POST", `/jobs/${context.job.id}/release`, { version: 1 });
      if (result.released !== true) throw new Error("invalid_receiver_response");
      await api.transport(context, "released");
      return;
    }
    for (const file of context.manifest.files) {
      let receipt = state.get(file.id), reacquired = false;
      while (receipt.status !== "acknowledged") {
        if (!active) return;
        const local = await api.transport(context, "heartbeat");
        if (["failed", "canceled"].includes(local.job.status)) return;
        const offset = receipt.receivedBytes, length = Math.min(FILE_CHUNK, file.size - offset);
        if (length <= 0) throw new Error("invalid_receiver_receipt");
        const chunk = await api.transport(context, "chunk", { fileId: file.id, offset, length });
        if (chunk.data.byteLength !== length || chunk.sha256 !== file.sha256 || chunk.range !== `bytes ${offset}-${offset+length-1}/${file.size}`)
          throw new Error("invalid_receiver_response");
        let result;
        try {
          result = await remote(context, "PUT", `/jobs/${context.job.id}/files/${file.id}`, chunk.data,
            { "Content-Type":"application/octet-stream", "Content-Range":`bytes ${offset}-${offset+length-1}/${file.size}`, "X-Content-SHA256":file.sha256 });
        } catch (error) {
          if (error.message !== "receiver_conflict") throw error;
          result = await remote(context, "GET", `/jobs/${context.job.id}`);
          const fresh = validatePcState(result, context.manifest).get(file.id);
          if (fresh.receivedBytes === offset && fresh.status !== "acknowledged") {
            if (reacquired) throw error;
            result = await post(); reacquired = true;
          }
        }
        state = validatePcState(result, context.manifest); receipt = state.get(file.id);
        if (receipt.receivedBytes > offset) reacquired = false;
      }
      await api.transport(context, "ack", { fileId:file.id, size:file.size, sha256:file.sha256, receiptId:receipt.receiptId });
    }
  };
  const runOnce = async () => {
    if (running) return;
    running = true;
    try {
      const status = await api.jobs();
      const now = Date.now() / 1000;
      const eligible = status.jobs.filter((job) => job.releasePending ? (job.releaseRetryAt || 0) <= now
        : !["completed","failed","canceled"].includes(job.status) && (job.retryAt || 0) <= now);
      for (const job of [...eligible.filter((item) => item.releasePending).slice(0,4), ...eligible.filter((item) => !item.releasePending)]) {
        if (!active) break;
        try {
          current = await api.claim(job.id, transportOwner); controller = new AbortController();
          await transmit(current);
        } catch (error) {
          if (active && current && !["transport_stale","transport_busy","job_not_transferable"].includes(error.message)) {
            try { await api.transport(current, "defer", { error: ["receiver_unavailable","receiver_busy","receiver_disk_full","receiver_auth_failed","receiver_conflict","invalid_receiver_receipt","invalid_receiver_response","receiver_redirect_rejected","receiver_rejected","receiver_limit"].includes(error.message) ? error.message : "receiver_unavailable", retryAfter:error.retryAfter || 0 }); } catch (_) {}
          }
        } finally {
          if (current) { try { await api.transport(current, "yield"); } catch (_) {} }
          current = null; controller = null;
        }
      }
    } finally { running = false; }
  };
  const tick = async (token) => { try { await runOnce(); } catch (_) {} if (active && token === generation) timer = setTimeout(() => tick(token), 1500); };
  const control = async (id, action) => {
    const context = current?.job.id === id ? current : await api.claim(id, transportOwner);
    try { return await api.transport(context, action); }
    finally { if (context !== current) { try { await api.transport(context,"yield"); } catch (_) {} } }
  };
  return {
    start() { if (!active) { active = true; tick(++generation); } },
    stop() { active = false; generation++; clearTimeout(timer); controller?.abort(); if (current) api.transport(current,"yield",{}, {keepalive:true}).catch(()=>{}); },
    runOnce: async () => { active = true; await runOnce(); },
    cancel: (id) => control(id,"cancel"), retry: (id) => control(id,"retry"),
  };
}

let phoneRelay = null;
function getPhoneRelay() {
  if (!phoneRelay) phoneRelay = createPhoneRelay(createPcLogApi(), { allowTestHttp: ["127.0.0.1","localhost"].includes(globalThis.location?.hostname) });
  return phoneRelay;
}
if (typeof window !== "undefined") {
  const start = () => getPhoneRelay().start();
  if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", start, {once:true}); else start();
  window.addEventListener("pagehide", () => phoneRelay?.stop());
  document.addEventListener("visibilitychange", () => document.hidden ? phoneRelay?.stop() : start());
}

function statusLabel(status) {
  const labels = {
    queued: ["pc_logs_queued", "PC 연결 대기"], transferring: ["pc_logs_transferring", "PC로 받는 중"],
    verifying: ["pc_logs_verifying", "PC 저장 확인 중"], completed: ["pc_logs_completed", "PC 저장 완료"],
    failed: ["pc_logs_failed", "전송 실패"], canceled: ["pc_logs_canceled", "취소됨"],
  };
  return text(...(labels[status] || ["pc_logs_wait", "확인 중"]));
}

function errorLabel(code) {
  const labels = {
    receiver_unavailable: ["pc_logs_offline", "수신 PC가 꺼져 있거나 수신할 수 없어 대기 중입니다. 준비되면 자동으로 다시 시도합니다."],
    receiver_disk_full: ["pc_logs_disk_full", "PC 저장 공간이 부족합니다. 공간을 확보하면 다시 시도합니다."],
    source_missing: ["pc_logs_source_missing", "기기의 원본 로그가 없어 전송할 수 없습니다."],
    source_unavailable: ["pc_logs_source_missing", "기기의 원본 로그를 읽을 수 없습니다."],
    source_modified: ["pc_logs_source_changed", "원본 로그가 바뀌었습니다. 기록 완료 후 새로 요청하세요."],
    source_locked: ["pc_logs_source_locked", "아직 기록 중인 로그입니다. 기록 완료 후 다시 시도하세요."],
    receiver_busy: ["pc_logs_busy", "수신 한도로 대기 중입니다. 잠시 후 자동으로 다시 시도합니다."],
    receiver_auth_failed: ["pc_logs_auth_failed", "수신 PC에서 기기 인증을 확인하지 못했습니다. 요청은 실패 상태로 보존됩니다."],
    legacy_configuration: ["pc_logs_legacy", "이전 시험용 PC 등록 정보가 있습니다. 다른 수신처로 자동 변경하지 않았습니다."],
    selection_changed: ["pc_logs_changed", "확인한 로그 목록이 바뀌었습니다. 창을 다시 열어 전송 내용을 확인하세요."],
    transport_busy: ["pc_logs_other_page", "다른 페이지에서 전송 중입니다. 그 페이지를 닫고 잠시 기다리면 이어받습니다."],
    transport_stale: ["pc_logs_stale_page", "다른 페이지로 전송 권한이 넘어갔습니다. 이 페이지를 다시 열어 확인하세요."],
  };
  return labels[code] ? text(...labels[code]) : text("pc_logs_error", "처리하지 못했습니다. 연결과 기록 상태를 확인한 뒤 다시 시도하세요.");
}

export async function openPcLogTransfer({ segments = [] } = {}) {
  if (activePanel) {
    activePanel.focus();
    return;
  }
  if (opening) return;
  opening = true;
  const api = createPcLogApi();
  const selection = pcLogSelection(segments);
  let preview;
  try {
    const initial = await api.jobs();
    if (initial.error) throw new Error(initial.error);
    preview = await api.preview(selection);
  } catch (error) { throw new Error(errorLabel(error.message)); }
  finally { opening = false; }
  const dialog = document.createElement("dialog");
  dialog.className = "pc-log-dialog";
  dialog.setAttribute("aria-labelledby", "pcLogDialogTitle");
  const heading = document.createElement("h2");
  heading.id = "pcLogDialogTitle";
  heading.textContent = text("pc_logs_send", "fullmetalsonic에게 로그 보내기");
  const scope = document.createElement("p");
  scope.textContent = selection.segments
    ? text("pc_logs_selected", "선택한 {count}개 구간의 원본 로그", { count: selection.segments.length })
    : text("pc_logs_latest", "가장 최근 주행의 기록 완료 구간 전체 · 원본 로그만");
  const help = document.createElement("p");
  help.className = "pc-log-help";
  help.textContent = text("pc_logs_recipient", "수신처: fullmetalsonic 개인 PC");
  const conditions = document.createElement("p");
  conditions.className = "pc-log-help";
  conditions.textContent = text("pc_logs_phone_help", "폰의 Tailscale과 PC 수신기를 켜고 이 웹페이지를 열어 두세요. 페이지를 닫거나 폰이 절전하면 멈추며, 다시 열면 이어 보냅니다.");
  const manifest = document.createElement("p");
  manifest.className = "pc-log-help";
  manifest.textContent = text("pc_logs_manifest", "{count}개 파일 · 총 {bytes}바이트 · 주행 시작일: {dates}", {
    count: preview.fileCount, bytes: Number(preview.totalBytes).toLocaleString(),
    dates: preview.driveDates.map((date) => date === "날짜미확인" ? text("pc_logs_unknown_date", "날짜미확인") : date).join(", "),
  });
  const disclosure = document.createElement("p");
  disclosure.className = "pc-log-help";
  disclosure.textContent = text("pc_logs_disclosure", "원본 주행 로그에는 위치·차량 정보가 포함될 수 있습니다. 영상·qlog는 제외합니다. 아래 전송 버튼을 누르면 이 수신처에 로그를 보내는 데 동의합니다. PC가 꺼져 있으면 요청을 저장하고 다시 연결되면 전송합니다.");
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  const list = document.createElement("div");
  list.className = "pc-log-job-list";
  const actions = document.createElement("div");
  actions.className = "pc-log-dialog-actions";
  const send = document.createElement("button");
  send.type = "button"; send.className = "smallBtn btn--filled";
  send.textContent = text("pc_logs_queue", "확인하고 전송");
  const close = document.createElement("button");
  close.type = "button"; close.className = "smallBtn";
  close.textContent = text("close", "닫기");
  actions.append(send, close);
  dialog.append(heading, scope, help, manifest, disclosure, conditions, status, list, actions);
  document.body.appendChild(dialog);
  activePanel = dialog;
  let stopped = false;
  let busy = false;
  let timer = null;
  let requestedJobId = null;
  let actionError = null;
  const opener = document.activeElement;
  const shutdown = () => {
    if (stopped) return;
    stopped = true;
    clearTimeout(timer);
    dialog.close(); dialog.remove(); activePanel = null;
    opener?.focus?.();
  };
  close.addEventListener("click", shutdown);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); shutdown(); });

  const perform = async (button, operation) => {
    button.disabled = true;
    actionError = null;
    try { await operation(); await refresh(); }
    catch (error) { actionError = errorLabel(error.message); status.textContent = actionError; }
    finally { button.disabled = false; }
  };
  const refresh = async () => {
    if (stopped) return;
    const result = await api.jobs();
    if (stopped) return;
    list.replaceChildren();
    for (const job of result.jobs.slice(0, 8)) {
      const row = document.createElement("section");
      row.className = "pc-log-job";
      const progress = pcLogProgress(job);
      const title = document.createElement("strong");
      title.textContent = `${statusLabel(job.status === "completed" && !progress.completed ? "verifying" : job.status)} · ${progress.acknowledged}/${progress.total}`;
      const dates = Object.values(job.routes || {}).map((date) => date.dateStatus === "known"
        ? date.driveDate : text("pc_logs_unknown_date", "날짜미확인"));
      const detail = document.createElement("p");
      detail.textContent = [...new Set(dates)].join(", ");
      if (job.error) detail.textContent += ` · ${errorLabel(job.error)}`;
      if (job.releasePending) detail.textContent += ` · ${text("pc_logs_cleanup", "수신 자원 정리 대기")}`;
      if (job.retryRequested) detail.textContent += ` · ${text("pc_logs_retry_after_cleanup", "정리 후 자동 재시도")}`;
      row.append(title, detail);
      if (["failed", "canceled"].includes(job.status)) {
        const retry = document.createElement("button");
        retry.type = "button"; retry.className = "smallBtn";
        retry.textContent = text("pc_logs_retry", "다시 시도");
        retry.addEventListener("click", () => perform(retry, () => getPhoneRelay().retry(job.id)));
        row.append(retry);
      }
      if (!["completed", "canceled"].includes(job.status)) {
        const cancel = document.createElement("button");
        cancel.type = "button"; cancel.className = "smallBtn";
        cancel.textContent = text("cancel", "취소");
        cancel.addEventListener("click", () => perform(cancel, () => getPhoneRelay().cancel(job.id)));
        row.append(cancel);
      }
      list.appendChild(row);
    }
    const summary = pcLogQueueStatus(result.jobs, requestedJobId);
    status.textContent = actionError || (summary.status ? statusLabel(summary.status) : text("pc_logs_no_jobs", "아직 보낸 요청이 없습니다."));
    if (!actionError && summary.active) status.textContent += ` · ${text("pc_logs_active_requests", "남은 요청 {count}개", { count: summary.active })}`;
  };
  send.addEventListener("click", async () => {
    if (busy) return;
    busy = true; send.disabled = true;
    actionError = null;
    status.textContent = text("pc_logs_preparing", "기록 완료 구간을 확인하고 있습니다…");
    try {
      const result = await api.enqueue(selection, preview.previewId);
      requestedJobId = result.job.id;
      getPhoneRelay().start();
      const progress = pcLogProgress(result.job);
      status.textContent = progress.completed ? text("pc_logs_completed", "PC 저장 완료")
        : text("pc_logs_waiting_pc", "요청을 저장했습니다. PC 수신기가 연결되면 전송합니다.");
      await refresh();
    } catch (error) { actionError = errorLabel(error.message); status.textContent = actionError; }
    finally { busy = false; send.disabled = false; }
  });
  const poll = async () => {
    try { await refresh(); }
    catch (error) {
      if (!stopped) status.textContent = errorLabel(error.message);
    }
    if (!stopped) timer = setTimeout(poll, 1500);
  };
  dialog.showModal();
  await poll();
}
