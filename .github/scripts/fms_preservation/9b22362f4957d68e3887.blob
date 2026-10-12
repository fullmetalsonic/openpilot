import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";

const catalogue = JSON.parse(readFileSync(new URL("../../../carrot_settings.json", import.meta.url), "utf8"));
const paddle = catalogue.params.find((p) => p.name === "PaddleMode");
const source = readFileSync(new URL("../js/pages/setting.js", import.meta.url), "utf8");
const start = source.indexOf("const SETTING_CONTROL_KINDS =");
const end = source.indexOf("const SETTING_DISPLAY_UNIT_TYPES =", start);
assert.ok(start >= 0 && end > start);
const config = runInNewContext(`${source.slice(start, end)}; getSettingControlConfig(paddle)`, { paddle });

test("mode 4 follows the shared five-choice selector without an override", () => {
  assert.equal(paddle.control, undefined);
  assert.equal(config.kind, "select");
  assert.deepEqual([config.min, config.max, config.optionCount], [0, 4, 5]);
  assert.equal(paddle.default, 1);
});

test("mode-switch restart and in-drive gap selection are distinguished", () => {
  assert.match(paddle.descr, /재시작/);
  assert.match(paddle.descr, /주행 중/);
  assert.match(paddle.descr, /오른쪽.*감소.*왼쪽.*증가/);
});
