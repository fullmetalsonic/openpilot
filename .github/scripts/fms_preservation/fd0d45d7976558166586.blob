import assert from "node:assert/strict";
import test from "node:test";

import { advisorErrorMessage, consumeNativeGrant, createAdvisorClient } from "../src/features/control_advisor/index.js";
import { buildReviewRegistrationBody } from "../src/features/control_advisor/model.js";

test("manual analysis posts the session and idempotency ID, with no timer or range in the browser", async () => {
  const calls = [];
  const client = createAdvisorClient({ fetchImpl: async (...args) => { calls.push(args); return { ok: true, json: async () => ({ ok: true }) }; } });
  const body = { session_id: "trip", request_id: "manual-A" };
  await client.request("analyze", { body });
  assert.equal(calls[0][0], "/api/control_advisor/analyze");
  assert.equal(calls[0][1].method, "POST");
  assert.deepEqual(JSON.parse(calls[0][1].body), body);
});

test("review registration preserves strict JSON bytes and history uses authenticated POST without paths or URL credentials", async () => {
  const calls = [];
  const client = createAdvisorClient({ fetchImpl: async (...args) => { calls.push(args); return { ok: true, json: async () => ({ ok: true }) }; } });
  const rawJsonBody = buildReviewRegistrationBody("retry-id", '{"document_state":"final","same":1,"same":2}');
  const jobId = "a".repeat(32);
  await client.request("register_codex_review", { jobId, token: "private-key", rawJsonBody });
  await client.request("query_codex_reviews", { jobId, token: "private-key", body: {} });
  assert.equal(calls[0][0], `/api/control_advisor/export-jobs/${jobId}/reviews`);
  assert.equal(calls[0][1].body, rawJsonBody);
  assert.match(calls[0][1].body, /"same":1,"same":2/);
  assert.equal(calls[1][0], `/api/control_advisor/export-jobs/${jobId}/reviews/query`);
  assert.ok(calls.every(([url, options]) => options.method === "POST" && options.headers["X-Control-Advisor-Token"] === "private-key" && !url.includes("private-key")));
  await assert.rejects(client.request("query_codex_reviews", { jobId: "../outside", body: {} }));
  await assert.rejects(client.request("query_codex_reviews", { jobId, rawJsonBody }));
});

test("structured review conflicts have clear safe messages while arbitrary server text stays hidden", async () => {
  const client = createAdvisorClient({ fetchImpl: async () => ({ ok: false, status: 409, json: async () => ({ error: "review_binding_mismatch" }) }) });
  await assert.rejects(client.request("query_codex_reviews", { jobId: "a".repeat(32), body: {} }), (error) => /묶음·요약 버전/.test(advisorErrorMessage(error)));
  assert.doesNotMatch(advisorErrorMessage({ code: "C:/private/key", status: 400 }), /private/);
});

test("status reads never carry a key or a request body", async () => {
  const calls = [];
  const client = createAdvisorClient({ fetchImpl: async (...args) => {
    calls.push(args);
    return { ok: true, json: async () => ({ ok: true, session: { state: "idle" } }) };
  } });
  await client.request("status", { token: "private-key" });
  const [url, options] = calls[0];
  assert.equal(url, "/api/control_advisor/status");
  assert.equal(options.method, "GET");
  assert.equal(options.headers, undefined);
  assert.equal(options.body, undefined);
  assert.equal(options.cache, "no-store");
});

test("native QR credentials are removed from the fragment before use and ordinary routes stay untouched", () => {
  const calls = [];
  const history = { replaceState: (...args) => calls.push(args) };
  const grant = consumeNativeGrant({ hash: "#ca_grant=grant-id." + "s".repeat(32), pathname: "/", search: "?page=logs" }, history);
  assert.deepEqual(grant, { grant_id: "grant-id", secret: "s".repeat(32) });
  assert.deepEqual(calls, [[null, "", "/?page=logs"]]);
  assert.equal(consumeNativeGrant({ hash: "#logs" }, history), null);
  assert.equal(calls.length, 1);
  assert.equal(consumeNativeGrant({ hash: "#ca_grant=invalid", pathname: "/", search: "" }, history), null);
  assert.equal(calls.length, 2);
});

test("native cookie actions need no empty bearer header and never offer a grant issuer", async () => {
  const calls = [];
  const client = createAdvisorClient({ fetchImpl: async (...args) => { calls.push(args); return { ok: true, json: async () => ({ ok: true }) }; } });
  for (const [action, body] of [["redeem_access", { grant_id: "id", secret: "s".repeat(32) }], ["confirm_vehicle", { group_id: null }],
    ["backup_vehicle", {}], ["restore_vehicle", { request_id: "original", restore_id: "restore", mode: "offroad" }]]) {
    await client.request(action, { body });
  }
  assert.ok(calls.every(([url, options]) => options.credentials === "same-origin" && options.method === "POST"
    && !Object.hasOwn(options.headers, "X-Control-Advisor-Token") && !url.includes("secret")));
  assert.deepEqual(calls.map(([url]) => url), ["/api/control_advisor/access/redeem", "/api/control_advisor/vehicle/confirm", "/api/control_advisor/vehicle/backup", "/api/control_advisor/vehicle/restore"]);
  assert.deepEqual(JSON.parse(calls[3][1].body), { request_id: "original", restore_id: "restore", mode: "offroad" });
  await assert.rejects(client.request("issue_access", { body: {} }));
});

test("start and stop bind the selected session action without exposing the key in URLs", async () => {
  const calls = [];
  const client = createAdvisorClient({ fetchImpl: async (...args) => {
    calls.push(args);
    return { ok: true, json: async () => ({ ok: true }) };
  } });
  const body = { mode: "synthetic", interval_seconds: 300, request_id: "request-A" };
  await client.request("start", { token: "private-key", body });
  await client.request("stop", { token: "private-key", body: { session_id: "session-A" } });
  assert.equal(calls[0][0], "/api/control_advisor/start");
  assert.deepEqual(JSON.parse(calls[0][1].body), body);
  assert.equal(calls[0][1].headers["X-Control-Advisor-Token"], "private-key");
  assert.equal(calls[1][0], "/api/control_advisor/stop");
  assert.deepEqual(JSON.parse(calls[1][1].body), { session_id: "session-A" });
  assert.ok(calls.every(([url]) => !url.includes("private-key")));
});

test("a new request cancels the earlier status fetch and cleanup cancels its own request", async () => {
  const calls = [];
  const client = createAdvisorClient({ fetchImpl: async (_url, options) => {
    calls.push(options);
    return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true }));
  } });
  const first = client.request();
  const firstRejected = assert.rejects(first, { name: "AbortError" });
  const second = client.request();
  const secondRejected = assert.rejects(second, { name: "AbortError" });
  assert.equal(calls[0].signal.aborted, true);
  client.cancel();
  assert.equal(calls[1].signal.aborted, true);
  await Promise.all([firstRejected, secondRejected]);
});

test("timeout aborts a stalled request", async () => {
  const client = createAdvisorClient({ timeoutMs: 5, fetchImpl: async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  }) });
  await assert.rejects(client.request(), { name: "AbortError" });
});

test("failed or malformed responses are errors and do not become empty success", async () => {
  for (const response of [{ ok: false, status: 403 }, { ok: true, json: async () => ({ ok: false }) },
    { ok: true, json: async () => [] }, { ok: true, json: async () => null }]) {
    const client = createAdvisorClient({ fetchImpl: async () => response });
    await assert.rejects(client.request());
  }
  assert.match(advisorErrorMessage({ status: 403 }), /세션 키/);
  assert.match(advisorErrorMessage({ status: 409 }), /세션 상태/);
  assert.doesNotMatch(advisorErrorMessage(new Error("private-key")), /private-key/);
});

test("unknown actions never trigger a network request", async () => {
  let called = false;
  const client = createAdvisorClient({ fetchImpl: async () => { called = true; } });
  await assert.rejects(client.request("apply"));
  assert.equal(called, false);
});

test("request endpoints keep the bound body separate from the server-owned target and use no token for reads", async () => {
  const calls = [];
  const client = createAdvisorClient({ fetchImpl: async (...args) => { calls.push(args); return { ok: true, json: async () => ({ ok: true }) }; } });
  const body = { request_id: "request-1", item_key: "PathOffset" };
  await client.request("register_request", { body, token: "test-token" });
  await client.request("request_status", { requestId: "request-1", token: "test-token" });
  await client.request("cancel_request", { requestId: "request-1", body: {}, token: "test-token" });
  assert.equal(calls[0][0], "/api/control_advisor/apply-requests");
  assert.deepEqual(JSON.parse(calls[0][1].body), body);
  assert.equal(calls[1][0], "/api/control_advisor/apply-requests/request-1");
  assert.equal(calls[1][1].method, "GET");
  assert.equal(calls[1][1].headers, undefined);
  assert.equal(calls[2][0], "/api/control_advisor/apply-requests/request-1/cancel");
  assert.deepEqual(JSON.parse(calls[2][1].body), {});
});

test("request identifiers cannot choose a path and inherited action names are rejected", async () => {
  let calls = 0;
  const client = createAdvisorClient({ fetchImpl: async () => { calls++; } });
  for (const requestId of ["../private", "https://other.invalid", "a/b", ""]) await assert.rejects(client.request("request_status", { requestId }));
  await assert.rejects(client.request("constructor"));
  assert.equal(calls, 0);
});

test("export creation, cancellation and native download grant use fixed authenticated POST paths", async () => {
  const calls = [];
  const client = createAdvisorClient({ fetchImpl: async (...args) => { calls.push(args); return { ok: true, json: async () => ({ ok: true }) }; } });
  const jobId = "a".repeat(32);
  await client.request("create_export_job", { token: "private-key", body: { request_id: "export-one", summary_id: "summary", summary_revision: 1, include_raw: false } });
  await client.request("cancel_export_job", { jobId, token: "private-key", body: {} });
  await client.request("grant_export_download", { jobId, token: "private-key", body: {} });
  assert.equal(calls[0][0], "/api/control_advisor/export-jobs");
  assert.equal(calls[1][0], `/api/control_advisor/export-jobs/${jobId}/cancel`);
  assert.equal(calls[2][0], `/api/control_advisor/export-jobs/${jobId}/download`);
  assert.ok(calls.every(([url, options]) => !url.includes("private-key") && options.method === "POST" && options.credentials === "same-origin"));
  await assert.rejects(client.request("grant_export_download", { jobId: "../private" }));
  assert.equal(calls.length, 3);
});
