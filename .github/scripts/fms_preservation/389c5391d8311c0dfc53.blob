export const ENCODING = "reason-indexed-v1";

const DECIMAL_RE = /^(0|[1-9][0-9]*)$/;
const REASON_RE = /^[a-z_]{1,128}$/;
const MAX_INT64 = 9223372036854775807n;
const MAX_SAFE_INT = Number.MAX_SAFE_INTEGER;
const MAX_SAFE_INT_BIG = BigInt(MAX_SAFE_INT);
const MAX_BOUND = 100000;

function checkLegacyKeys(item) {
  if (item === null || typeof item !== "object" || Array.isArray(item)) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  const keys = Object.keys(item);
  if (keys.length !== 4) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  if (!Object.hasOwn(item, "reason") ||
      !Object.hasOwn(item, "start_ns") ||
      !Object.hasOwn(item, "end_ns") ||
      !Object.hasOwn(item, "sample_count")) {
    throw new Error("invalid_public_comparison_exclusions");
  }
}

function formatNsString(val) {
  if (typeof val === "bigint") {
    if (val < 0n || val > MAX_INT64) {
      throw new Error("invalid_public_comparison_exclusions");
    }
    return val.toString(10);
  }
  if (typeof val === "number") {
    if (!Number.isSafeInteger(val) || val < 0 || Object.is(val, -0)) {
      throw new Error("invalid_public_comparison_exclusions");
    }
    return String(val);
  }
  throw new Error("invalid_public_comparison_exclusions");
}

function parseDecimalNs(s) {
  if (typeof s !== "string" || !DECIMAL_RE.test(s)) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  if (s.length > 19) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  const big = BigInt(s);
  if (big > MAX_INT64) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  if (big <= MAX_SAFE_INT_BIG) {
    return Number(big);
  }
  return big;
}

export function encodeExclusions(intervals) {
  if (!Array.isArray(intervals) || intervals.length > MAX_BOUND) {
    throw new Error("invalid_public_comparison_exclusions");
  }

  const reasons = [];
  const reasonMap = new Map();
  const rows = [];

  for (let i = 0; i < intervals.length; i++) {
    const item = intervals[i];
    checkLegacyKeys(item);

    const reason = item.reason;
    if (typeof reason !== "string" || !REASON_RE.test(reason)) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    const startStr = formatNsString(item.start_ns);
    const endStr = formatNsString(item.end_ns);
    const startBig = typeof item.start_ns === "bigint" ? item.start_ns : BigInt(item.start_ns);
    const endBig = typeof item.end_ns === "bigint" ? item.end_ns : BigInt(item.end_ns);
    if (startBig > endBig) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    const count = item.sample_count;
    if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0 || Object.is(count, -0)) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    let reasonIndex = reasonMap.get(reason);
    if (reasonIndex === undefined) {
      reasonIndex = reasons.length;
      if (reasonIndex >= MAX_BOUND) {
        throw new Error("invalid_public_comparison_exclusions");
      }
      reasonMap.set(reason, reasonIndex);
      reasons.push(reason);
    }

    rows.push([reasonIndex, startStr, endStr, count]);
  }

  return {
    encoding: ENCODING,
    reasons,
    rows,
  };
}

export function decodeExclusions(value, schemaVersion = 1) {
  if (typeof schemaVersion !== "number" || !Number.isInteger(schemaVersion) || (schemaVersion !== 1 && schemaVersion !== 2)) {
    throw new Error("invalid_public_comparison_exclusions");
  }

  if (schemaVersion === 1) {
    if (!Array.isArray(value) || value.length > MAX_BOUND) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    const decoded = [];
    for (let i = 0; i < value.length; i++) {
      const item = value[i];
      checkLegacyKeys(item);

      const reason = item.reason;
      if (typeof reason !== "string" || !REASON_RE.test(reason)) {
        throw new Error("invalid_public_comparison_exclusions");
      }

      if (typeof item.start_ns !== "number" || !Number.isSafeInteger(item.start_ns) || item.start_ns < 0 || Object.is(item.start_ns, -0)) {
        throw new Error("invalid_public_comparison_exclusions");
      }
      if (typeof item.end_ns !== "number" || !Number.isSafeInteger(item.end_ns) || item.end_ns < 0 || Object.is(item.end_ns, -0)) {
        throw new Error("invalid_public_comparison_exclusions");
      }
      if (item.start_ns > item.end_ns) {
        throw new Error("invalid_public_comparison_exclusions");
      }

      const count = item.sample_count;
      if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0 || Object.is(count, -0)) {
        throw new Error("invalid_public_comparison_exclusions");
      }

      decoded.push({
        reason,
        start_ns: item.start_ns,
        end_ns: item.end_ns,
        sample_count: count,
      });
    }

    return decoded;
  }

  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  const keys = Object.keys(value);
  if (keys.length !== 3 || !Object.hasOwn(value, "encoding") || !Object.hasOwn(value, "reasons") || !Object.hasOwn(value, "rows")) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  if (value.encoding !== ENCODING) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  if (!Array.isArray(value.reasons) || value.reasons.length > MAX_BOUND) {
    throw new Error("invalid_public_comparison_exclusions");
  }
  if (!Array.isArray(value.rows) || value.rows.length > MAX_BOUND) {
    throw new Error("invalid_public_comparison_exclusions");
  }

  const reasonSet = new Set();
  for (let i = 0; i < value.reasons.length; i++) {
    const r = value.reasons[i];
    if (typeof r !== "string" || !REASON_RE.test(r)) {
      throw new Error("invalid_public_comparison_exclusions");
    }
    if (reasonSet.has(r)) {
      throw new Error("invalid_public_comparison_exclusions");
    }
    reasonSet.add(r);
  }

  let nextExpectedIndex = 0;
  const decoded = [];

  for (let i = 0; i < value.rows.length; i++) {
    const row = value.rows[i];
    if (!Array.isArray(row) || row.length !== 4) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    const [idx, startStr, endStr, count] = row;
    if (typeof idx !== "number" || !Number.isInteger(idx) || idx < 0 || idx >= value.reasons.length || Object.is(idx, -0)) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    if (idx === nextExpectedIndex) {
      nextExpectedIndex++;
    } else if (idx > nextExpectedIndex) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    const startNs = parseDecimalNs(startStr);
    const endNs = parseDecimalNs(endStr);
    const startBig = typeof startNs === "bigint" ? startNs : BigInt(startNs);
    const endBig = typeof endNs === "bigint" ? endNs : BigInt(endNs);
    if (startBig > endBig) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0 || Object.is(count, -0)) {
      throw new Error("invalid_public_comparison_exclusions");
    }

    decoded.push({
      reason: value.reasons[idx],
      start_ns: startNs,
      end_ns: endNs,
      sample_count: count,
    });
  }

  if (nextExpectedIndex !== value.reasons.length) {
    throw new Error("invalid_public_comparison_exclusions");
  }

  return decoded;
}
