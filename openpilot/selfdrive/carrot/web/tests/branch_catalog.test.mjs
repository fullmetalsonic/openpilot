import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

test("branch picker displays partial remote error and keeps available items", async () => {
  const meta = { textContent: "" };
  const result = { current_branch: "installed", branch_items: [{kind:"local", name:"installed", ref:"installed"}], remote_errors: {origin:"timeout"} };
  const context = vm.createContext({
    appBranchPickerMeta: meta, appBranchPickerList: {innerHTML:""},
    appBranchPicker: null, appBranchPickerBackdrop:null, appBranchPickerClose:null, UI_STRINGS:{ko:{}}, LANG:"ko", BRANCHES:[], CURRENT_BRANCH_NAME:"",
    getUIText: (_, fallback) => fallback, bulkGet: async () => ({}), runTool: async () => result,
  });
  vm.runInContext(fs.readFileSync(new URL("../js/pages/branch.js", import.meta.url), "utf8"), context);
  vm.runInContext("openBranchPicker = () => true; renderBranchList = () => {};", context);
  await vm.runInContext("loadBranchesAndShow()", context);
  assert.equal(meta.textContent, "1 branches\norigin: timeout");
  assert.equal(context.BRANCHES[0].name, "installed");
});
