import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  ENCODING,
  decodeExclusions,
  encodeExclusions,
} from "../src/features/control_advisor/comparison_intervals.js";

describe("control_advisor comparison exclusion intervals codec", () => {
  it("encodes and decodes empty exclusion lists losslessly across schemas", () => {
    const encoded = encodeExclusions([]);
    assert.deepEqual(encoded, { encoding: ENCODING, reasons: [], rows: [] });
    assert.deepEqual(decodeExclusions([], 1), []);
    assert.deepEqual(decodeExclusions(encoded, 2), []);
  });

  it("decodes schema1 legacy payload with deep-copy isolation", () => {
    const legacy = [
      { reason: "driver_intervention", start_ns: 1000, end_ns: 2000, sample_count: 5 },
      { reason: "recovery", start_ns: 2500, end_ns: 3000, sample_count: 2 },
    ];
    const decoded = decodeExclusions(legacy, 1);
    assert.deepEqual(decoded, legacy);
    assert.notEqual(decoded, legacy);
    decoded[0].start_ns = 9999;
    assert.equal(legacy[0].start_ns, 1000);
  });

  it("preserves exact row order, gaps, overlaps, zero durations, and reasons in schema2", () => {
    const intervals = [
      { reason: "settings_hole", start_ns: 1000, end_ns: 2000, sample_count: 10 },
      { reason: "driver_mask", start_ns: 1500, end_ns: 2500, sample_count: 8 },
      { reason: "point_event", start_ns: 3000, end_ns: 3000, sample_count: 1 },
      { reason: "settings_hole", start_ns: 5000, end_ns: 6000, sample_count: 12 },
      { reason: "driver_mask", start_ns: 7000, end_ns: 7500, sample_count: 3 },
    ];

    const encoded = encodeExclusions(intervals);
    assert.equal(encoded.encoding, ENCODING);
    assert.deepEqual(encoded.reasons, ["settings_hole", "driver_mask", "point_event"]);
    assert.deepEqual(encoded.rows, [
      [0, "1000", "2000", 10],
      [1, "1500", "2500", 8],
      [2, "3000", "3000", 1],
      [0, "5000", "6000", 12],
      [1, "7000", "7500", 3],
    ]);

    const decoded = decodeExclusions(encoded, 2);
    assert.deepEqual(decoded, intervals);
    assert.notEqual(decoded, intervals);
    decoded.forEach((item, idx) => assert.notEqual(item, intervals[idx]));

    const reencoded = encodeExclusions(decoded);
    assert.deepEqual(reencoded, encoded);
  });

  it("handles int64 boundaries and exact BigInt representation", () => {
    const maxInt64 = 9223372036854775807n;
    const maxSafeInt = Number.MAX_SAFE_INTEGER;
    const overSafeInt = BigInt(maxSafeInt) + 1n;

    const mixed = [
      { reason: "zero_point", start_ns: 0, end_ns: 0, sample_count: 0 },
      { reason: "safe_max", start_ns: maxSafeInt, end_ns: maxSafeInt, sample_count: 1 },
      { reason: "unsafe_big", start_ns: overSafeInt, end_ns: maxInt64, sample_count: maxSafeInt },
    ];

    const encoded = encodeExclusions(mixed);
    assert.deepEqual(encoded.rows, [
      [0, "0", "0", 0],
      [1, String(maxSafeInt), String(maxSafeInt), 1],
      [2, overSafeInt.toString(10), maxInt64.toString(10), maxSafeInt],
    ]);

    const decoded = decodeExclusions(encoded, 2);
    assert.equal(typeof decoded[0].start_ns, "number");
    assert.equal(decoded[0].start_ns, 0);
    assert.equal(typeof decoded[1].start_ns, "number");
    assert.equal(decoded[1].start_ns, maxSafeInt);
    assert.equal(typeof decoded[2].start_ns, "bigint");
    assert.equal(decoded[2].start_ns, overSafeInt);
    assert.equal(typeof decoded[2].end_ns, "bigint");
    assert.equal(decoded[2].end_ns, maxInt64);

    const reencoded = encodeExclusions(decoded);
    assert.deepEqual(reencoded, encoded);
  });

  it("strictly rejects duplicate, unreferenced, or non-first-use dictionary entries", () => {
    const unreferenced = {
      encoding: ENCODING,
      reasons: ["used_reason", "unreferenced_reason"],
      rows: [[0, "10", "20", 1]],
    };
    assert.throws(() => decodeExclusions(unreferenced, 2), /invalid_public_comparison_exclusions/);

    const duplicateReasons = {
      encoding: ENCODING,
      reasons: ["reason_a", "reason_a"],
      rows: [[0, "10", "20", 1]],
    };
    assert.throws(() => decodeExclusions(duplicateReasons, 2), /invalid_public_comparison_exclusions/);

    const nonFirstUse = {
      encoding: ENCODING,
      reasons: ["reason_zero", "reason_one"],
      rows: [[1, "10", "20", 1], [0, "30", "40", 1]],
    };
    assert.throws(() => decodeExclusions(nonFirstUse, 2), /invalid_public_comparison_exclusions/);

    const badIndex = {
      encoding: ENCODING,
      reasons: ["reason_zero"],
      rows: [[5, "10", "20", 1]],
    };
    assert.throws(() => decodeExclusions(badIndex, 2), /invalid_public_comparison_exclusions/);
  });

  it("strictly fails on malformed structures, types, signs, and bounds", () => {
    const invalidEncoderCases = [
      { reason: "ok", start_ns: 0, end_ns: 10, sample_count: 1, extra: 1 },
      { reason: "ok", start_ns: 0, end_ns: 10 },
      { reason: "ok", start_ns: true, end_ns: 10, sample_count: 1 },
      { reason: "ok", start_ns: 0, end_ns: false, sample_count: 1 },
      { reason: "ok", start_ns: 0, end_ns: 10, sample_count: true },
      { reason: "ok", start_ns: 1.5, end_ns: 10, sample_count: 1 },
      { reason: "ok", start_ns: 0, end_ns: 1.5, sample_count: 1 },
      { reason: "ok", start_ns: 0, end_ns: 10, sample_count: 1.5 },
      { reason: "ok", start_ns: -1, end_ns: 10, sample_count: 1 },
      { reason: "ok", start_ns: -1n, end_ns: 10n, sample_count: 1 },
      { reason: "ok", start_ns: 10, end_ns: 5, sample_count: 1 },
      { reason: "ok", start_ns: 9223372036854775808n, end_ns: 9223372036854775809n, sample_count: 1 },
      { reason: "ok", start_ns: Number.MAX_SAFE_INTEGER + 1, end_ns: Number.MAX_SAFE_INTEGER + 2, sample_count: 1 },
      { reason: "ok", start_ns: 0, end_ns: 10, sample_count: Number.MAX_SAFE_INTEGER + 1 },
      { reason: "Invalid_Upper", start_ns: 0, end_ns: 10, sample_count: 1 },
      { reason: "invalid-hyphen", start_ns: 0, end_ns: 10, sample_count: 1 },
      { reason: "", start_ns: 0, end_ns: 10, sample_count: 1 },
      { reason: "a".repeat(129), start_ns: 0, end_ns: 10, sample_count: 1 },
    ];

    for (const bad of invalidEncoderCases) {
      assert.throws(() => encodeExclusions([bad]), /invalid_public_comparison_exclusions/);
      assert.throws(() => decodeExclusions([bad], 1), /invalid_public_comparison_exclusions/);
    }

    const invalidDecoderCases = [
      { encoding: "reason-indexed-v2", reasons: [], rows: [] },
      { encoding: ENCODING, reasons: [], rows: [], extra: 1 },
      { encoding: ENCODING, reasons: ["ok"], rows: [[0, "01", "10", 1]] },
      { encoding: ENCODING, reasons: ["ok"], rows: [[0, "+1", "10", 1]] },
      { encoding: ENCODING, reasons: ["ok"], rows: [[0, "-1", "10", 1]] },
      { encoding: ENCODING, reasons: ["ok"], rows: [[0, "10", "5", 1]] },
      { encoding: ENCODING, reasons: ["ok"], rows: [[0, "10", "20", -1]] },
      { encoding: ENCODING, reasons: ["ok"], rows: [[0, "10", "20", 1.5]] },
      { encoding: ENCODING, reasons: ["ok"], rows: [[0, "10", "20"]] },
      { encoding: ENCODING, reasons: ["ok"], rows: [[0, "10", "20", 1, 2]] },
      { encoding: ENCODING, reasons: ["ok"], rows: "not_array" },
    ];

    for (const bad of invalidDecoderCases) {
      assert.throws(() => decodeExclusions(bad, 2), /invalid_public_comparison_exclusions/);
    }

    assert.throws(() => decodeExclusions([], 0), /invalid_public_comparison_exclusions/);
    assert.throws(() => decodeExclusions([], 3), /invalid_public_comparison_exclusions/);
    assert.throws(() => decodeExclusions([], 2), /invalid_public_comparison_exclusions/);
    assert.throws(() => decodeExclusions({ encoding: ENCODING, reasons: [], rows: [] }, 1), /invalid_public_comparison_exclusions/);
  });

  it("enforces 100,000 bounds check", () => {
    const sample = { reason: "gap", start_ns: 0, end_ns: 1, sample_count: 1 };
    const oversized = new Array(100001).fill(sample);
    assert.throws(() => encodeExclusions(oversized), /invalid_public_comparison_exclusions/);
    assert.throws(() => decodeExclusions(oversized, 1), /invalid_public_comparison_exclusions/);
  });

  it("compresses realistic 180s and 300s repeated scan-hole comparisons to fit <= 32 KiB", () => {
    const overhead = {
      schema_version: 1,
      state: "comparison_ready",
      axis: "longitudinal",
      scope: "observational_vehicle_acceleration",
      target_unit: "m/s^2",
      consumer_application_ack: false,
      causal_improvement_proven: false,
      apply_enabled: false,
      device_qualified: false,
      reason_codes: [],
      boundary: {
        setting_uncertainty_start_ns: 29000000000,
        setting_uncertainty_end_ns: 31000000000,
        consumer_observed_ns: 32000000000,
        after_eligible_from_ns: 35000000000,
        exact_consumer_apply_time_known: false,
      },
      matched: {
        weighting: "common_stratum_minimum_count",
        common_bin_count: 1,
        bins: [{ speed_bin: 0, target_bin: 0, before_count: 100, after_count: 100, weight: 1.0 }],
        before: { count: 100, bias: 0.0, rmse: 0.1, p95_absolute_error: 0.15 },
        after: { count: 100, bias: 0.0, rmse: 0.08, p95_absolute_error: 0.12 },
        before_duration_ns: 10000000000,
        after_duration_ns: 10000000000,
        after_minus_before: { bias: 0.0, rmse: -0.02, p95_absolute_error: -0.03 },
      },
    };

    const sideSummary = (count, exclusions) => ({
      window_start_ns: 0,
      window_end_ns: count * 1e9,
      raw_metrics: { count: count * 10, bias: 0.0, rmse: 0.2, p95_absolute_error: 0.3 },
      eligible_metrics: { count: count * 9, bias: 0.0, rmse: 0.1, p95_absolute_error: 0.15 },
      sample_count: count * 10,
      eligible_count: count * 9,
      excluded_count: count,
      provisional_count: 0,
      observed_duration_ns: count * 1e9,
      eligible_duration_ns: count * 9e8,
      output_limit_observation: {
        fully_known_count: count * 10,
        limited_count: 0,
        output_limited_known_count: 0,
        saturated_known_count: 0,
        unknown_count: 0,
      },
      scope_limitations: [],
      exclusion_counts: { comparison_settings_source_uncertain: count },
      exclusions,
    });

    const makeScanHoles = (count, baseStamp) => Array.from({ length: count }, (_, sec) => ({
      reason: "comparison_settings_source_uncertain",
      start_ns: baseStamp + sec * 1000000000,
      end_ns: baseStamp + sec * 1000000000 + 100000000,
      sample_count: 1,
    }));

    for (const duration of [180, 300]) {
      const v1Before = makeScanHoles(duration, 1700000000000000);
      const v1After = makeScanHoles(duration, 1700300000000000);
      const v1Comparison = {
        ...overhead,
        schema_version: 1,
        before: sideSummary(duration, v1Before),
        after: sideSummary(duration, v1After),
      };

      const v1Bytes = new TextEncoder().encode(JSON.stringify(v1Comparison)).length;
      assert.ok(v1Bytes > 32 * 1024, `v1 size ${v1Bytes} should exceed 32 KiB`);
      if (duration === 180) {
        assert.ok(v1Bytes >= 42000, `180s v1 size ${v1Bytes} expected ~42 KiB`);
      } else {
        assert.ok(v1Bytes >= 69000, `300s v1 size ${v1Bytes} expected ~69 KiB`);
      }

      const v2Before = encodeExclusions(v1Before);
      const v2After = encodeExclusions(v1After);
      assert.equal(v2Before.rows.length, duration);
      assert.equal(v2After.rows.length, duration);

      const v2Comparison = {
        ...overhead,
        schema_version: 2,
        before: sideSummary(duration, v2Before),
        after: sideSummary(duration, v2After),
      };

      const v2Bytes = new TextEncoder().encode(JSON.stringify(v2Comparison)).length;
      assert.ok(v2Bytes <= 32 * 1024, `v2 size ${v2Bytes} must fit <= 32 KiB`);

      const decodedBefore = decodeExclusions(v2Before, 2);
      const decodedAfter = decodeExclusions(v2After, 2);
      assert.deepEqual(decodedBefore, v1Before);
      assert.deepEqual(decodedAfter, v1After);
    }
  });
});
