import assert from "node:assert/strict";
import test from "node:test";
import { createPcLogApi, createPhoneRelay, pcLogProgress, pcLogQueueStatus, pcLogSelection, validatePcState } from "../src/features/logs/pc_transfer.js";

test("selection is explicit and empty selection means the complete latest route", () => {
  assert.deepEqual(pcLogSelection(), { scope: "latest_route" });
  assert.deepEqual(pcLogSelection(["route--2", "route--1", "route--2", null]), { segments: ["route--2", "route--1"] });
});

test("HTTP queue or finished download is not reported as PC storage complete", () => {
  for (const status of ["queued", "transferring", "verifying", "failed", "canceled"])
    assert.equal(pcLogProgress({ status, files: [{ status: "acknowledged" }] }).completed, false);
  assert.equal(pcLogProgress({ status: "completed", files: [{ status: "pending" }] }).completed, false);
  assert.equal(pcLogProgress({ status: "completed", files: [] }).completed, false);
  assert.deepEqual(pcLogProgress({ status: "completed", files: [{ status: "acknowledged" }, { status: "acknowledged" }] }),
    { completed: true, acknowledged: 2, total: 2 });
});

test("header follows completion without hiding other active requests", () => {
  const complete = { id: "done", status: "completed", files: [{ status: "acknowledged" }] };
  assert.deepEqual(pcLogQueueStatus([complete], "done"), { status: "completed", active: 0 });
  assert.deepEqual(pcLogQueueStatus([complete, { id: "waiting", status: "queued" }], "done"), { status: "queued", active: 1 });
  assert.deepEqual(pcLogQueueStatus([complete, { id: "sending", status: "transferring" }]), { status: "transferring", active: 1 });
  assert.deepEqual(pcLogQueueStatus([{ id: "canceled", status: "canceled" }, complete]), { status: "canceled", active: 0 });
  assert.deepEqual(pcLogQueueStatus([{ status: "completed", files: [{ status: "pending" }] }]), { status: "verifying", active: 0 });
  assert.deepEqual(pcLogQueueStatus(), { status: null, active: 0 });
});

test("preview only contacts the local device; explicit confirmation is sent with the exact preview", async () => {
  const requests = [];
  const api = createPcLogApi(async (url, options) => {
    requests.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ ok: true, jobs: [], csrfNonce: "synthetic-nonce" }) };
  });
  await api.jobs();
  await api.preview(pcLogSelection(["route--1"]));
  assert.equal(requests.length, 2);
  assert.ok(requests.every(({url}) => !url.endsWith("/jobs")));
  await api.enqueue(pcLogSelection(["route--1"]), "c".repeat(64));
  await api.claim("a".repeat(64),"b".repeat(32));
  await api.transport({job:{id:"a".repeat(64)},owner:"b".repeat(32),epoch:1},"yield");
  assert.equal(requests.length, 5);
  for (const { url, options } of requests) {
    assert.ok(url.startsWith("/api/dashcam/pc-transfer/"));
    assert.ok(!url.includes("https://") && !url.includes("?"));
    assert.equal(options.headers.Authorization, undefined);
    if (options.method === "POST") assert.equal(options.headers["X-Carrot-CSRF"], "synthetic-nonce");
    assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store");
  }
  assert.deepEqual(JSON.parse(requests[1].options.body), { segments: ["route--1"] });
  assert.deepEqual(JSON.parse(requests[2].options.body), {
    selection: { segments: ["route--1"] }, previewId: "c".repeat(64), confirmed: true,
  });
  assert.throws(() => api.claim("../foreign","b".repeat(32)), /invalid_job_id/);
});

test("authentication and source failure reach the caller without a success result", async () => {
  for (const [status, error] of [[401, "unauthorized"], [409, "source_changed"]]) {
    const api = createPcLogApi(async () => ({ ok: false, status, json: async () => ({ ok: false, error }) }));
    await assert.rejects(api.jobs(), (failure) => failure.status === status && failure.message === error);
  }
});

test("PC receipt must match device, snapshot, complete digest and full byte count", () => {
  const file={id:"f",size:10,sha256:"a".repeat(64)};
  const manifest={id:"job",deviceId:"device",files:[file]};
  const receipt={...file,status:"acknowledged",receivedBytes:10,receiptId:"durable-receipt"};
  const value=(files=[receipt],extra={})=>({job:{id:"job",deviceId:"device",status:"completed",files,...extra}});
  assert.equal(validatePcState(value(),manifest).get("f").receiptId,"durable-receipt");
  for (const invalid of [value([receipt],{deviceId:"other"}),value([receipt],{id:"other"}),value([{...receipt,receivedBytes:9}]),
    value([{...receipt,sha256:"b".repeat(64)}]),value([{...receipt,receiptId:null}]),value([{...receipt,status:"pending"}])])
    assert.throws(()=>validatePcState(invalid,manifest),/invalid_receiver_receipt/);
});

test("a second job queued before first registration binds the already known sender ID", async () => {
  const deviceId="d".repeat(32),file={id:"f",size:10,sha256:"a".repeat(64)};
  const job={id:"job",deviceId:null,status:"queued",files:[file]};
  const context={endpoint:"http://minipc-amd5500u.tail8d9376.ts.net",key:"synthetic-memory-key",deviceId,
    epoch:1,owner:"a".repeat(32),job,manifest:{...job}};
  const actions=[];
  const api={jobs:async()=>({jobs:[job]}),claim:async()=>context,transport:async(_context,action,fields)=>{
    actions.push(action);
    if(action==="bind") {assert.equal(fields.deviceId,deviceId);return {job:{...job,deviceId}};}
    return {job};
  }};
  const relay=createPhoneRelay(api,{owner:"a".repeat(32),fetcher:async(url,options)=>{
    assert.ok(url.endsWith("/jobs"));
    const manifest=JSON.parse(options.body).job;
    assert.equal(manifest.deviceId,deviceId);
    return {ok:true,json:async()=>({ok:true,job:{id:"job",deviceId,status:"completed",files:[{...file,status:"acknowledged",receivedBytes:10,receiptId:"saved"}]}})};
  }});
  await relay.runOnce();relay.stop();
  assert.deepEqual(actions,["bind","ack","yield"]);
});
