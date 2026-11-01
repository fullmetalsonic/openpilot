import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const api = await readFile(new URL("../js/shared/api.js", import.meta.url), "utf8");
const runtime = await readFile(new URL("../js/realtime/app_realtime.js", import.meta.url), "utf8");
const catalog = JSON.parse(await readFile(new URL("../../../carrot_settings.json", import.meta.url), "utf8"));

test("legacy monitoring modes remain normal settings writes", async () => {
  const writes = [];
  const context = vm.createContext({
    window: { CarrotParamCommit: { create: () => ({ commit: async (...args) => writes.push(args) }) } },
  });
  vm.runInContext(api, context);
  for (const value of [0, 1, 2]) await context.setParam("DisableDM", value);
  assert.deepEqual(writes, [["DisableDM", 0, undefined], ["DisableDM", 1, undefined], ["DisableDM", 2, undefined]]);
  const dm = catalog.params.find((entry) => entry.name === "DisableDM");
  assert.deepEqual([dm.min, dm.max, dm.default], [0, 2, 0]);
  for (const name of ["DriverMonitoringEnabled", "DriverMonitoringMode", "CarrotVisionEnabled"])
    assert.equal(catalog.params.some((entry) => entry.name === name), false);
});

test("web runtime preserves all three legacy monitoring modes", () => {
  const start = runtime.indexOf("function normalizeRuntimeBool(");
  const end = runtime.indexOf("let _carrotVisionEnvironmentSignature", start);
  assert.ok(start >= 0 && end > start);
  const context = vm.createContext({ CARROT_DEVICE_RUNTIME_STATE: {}, window: {} });
  vm.runInContext(runtime.slice(start, end), context);
  for (const mode of [0, 1, 2]) {
    context.updateCarrotDeviceRuntimeState({ disableDm: String(mode) });
    assert.equal(context.CARROT_DEVICE_RUNTIME_STATE.disableDm, mode);
  }
});
