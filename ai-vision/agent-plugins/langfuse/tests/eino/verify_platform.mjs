// Eino callback acceptance: compare independent workflow/provider expectations
// with a persisted native read. No Python/JavaScript Langfuse usage inference.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { isDeepStrictEqual, parseArgs } from "node:util";
import { pathToFileURL } from "node:url";

const usageFields = { input: "input_tokens", output: "output_tokens", total: "total_tokens" };
const billable = (span) => ["generation", "embedding"].includes(span.kind.toLowerCase());
const parentId = (id) => !id || /^0+$/.test(id) ? null : id;
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const safeCount = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
const safeId = (value, length) => typeof value === "string" && new RegExp(`^[a-f\\d]{${length}}$`).test(value) ? value : null;
const safeParentId = (value) => parentId(value) === null ? null : safeId(value, 16) ?? "invalid";

function targetFields(target) {
  for (const name of ["agent_uuid", "space_key", "space_id", "agent_product"])
    assert.ok(typeof target[name] === "string" && target[name], `Target requires ${name}`);
  const endpoint = new URL(target.endpoint ?? target.base_url);
  assert.ok(["http:", "https:"].includes(endpoint.protocol) && !endpoint.username && !endpoint.password,
    "Target requires an HTTP(S) endpoint without embedded credentials");
  assert.ok(!endpoint.search && !endpoint.hash, "Target endpoint cannot contain a query or fragment");
  return endpoint.toString().replace(/\/$/, "");
}

export async function loadTarget(path, key = "langfuse") {
  const targets = JSON.parse(await readFile(path, "utf8"));
  const target = targets[key] ?? targets;
  targetFields(target);
  return target;
}

function validScenario(record) {
  if (!isObject(record)) return false;
  const expected = record.expected?.spans, observed = record.observed_spans;
  if (!safeId(record.trace_id, 32) || !Number.isSafeInteger(record.from_ms)
      || !Number.isSafeInteger(record.to_ms) || record.from_ms >= record.to_ms
      || !Array.isArray(expected) || !expected.length || !Array.isArray(observed)
      || !expected.every(isObject) || !observed.every(isObject)
      || record.expected.span_count !== expected.length || observed.length !== expected.length) return false;
  const keys = new Set(expected.map((span) => span.key));
  if (keys.size !== expected.length || new Set(observed.map((span) => span.key)).size !== expected.length
      || new Set(observed.map((span) => span.span_id)).size !== expected.length) return false;
  return expected.every((span) => typeof span.key === "string" && span.key
      && typeof span.name === "string" && span.name && typeof span.kind === "string" && span.kind
      && [0, 1, 2].includes(span.status)
      && (span.parent_key === null || span.parent_key === "" || keys.has(span.parent_key) && span.parent_key !== span.key)
      && (!billable(span) || Object.hasOwn(span, "usage") && (span.usage === null
        || typeof span.usage === "object" && Object.values(span.usage).every((value) =>
          value === null || Number.isSafeInteger(value) && value >= 0))))
    && observed.every((span) => keys.has(span.key) && /^[a-f\d]{16}$/.test(span.span_id)
      && (parentId(span.parent_span_id) === null || /^[a-f\d]{16}$/.test(span.parent_span_id)));
}

function expectedUsage(spans) {
  const calls = spans.filter(billable), totals = {}, reported = {};
  for (const [field, sourceField] of Object.entries(usageFields)) {
    const known = calls.map((span) => span.usage?.[sourceField] ?? null).filter((value) => value !== null);
    const value = known.length ? known.reduce((sum, count) => sum + count, 0) : null;
    totals[field] = calls.length && known.length === calls.length ? value : null;
    reported[field] = { value, reportedCalls: known.length };
  }
  const known = calls.filter((span) => span.usage?.total_tokens != null);
  return { billableCalls: calls.length, knownUsageCalls: known.length,
    knownTokens: known.reduce((sum, span) => sum + span.usage.total_tokens, 0), totals, reported };
}

function actualUsage(semantics) {
  if (!isObject(semantics)) return null;
  return { billableCalls: safeCount(semantics.coverage?.billableCalls),
    knownUsageCalls: safeCount(semantics.coverage?.knownUsageCalls), knownTokens: safeCount(semantics.coverage?.knownTokens),
    totals: Object.fromEntries(Object.keys(usageFields).map((field) => [field, safeCount(semantics.usage?.[field])])),
    reported: Object.fromEntries(Object.keys(usageFields).map((field) => [field,
      { value: safeCount(semantics.reportedUsage?.[field]?.value),
        reportedCalls: safeCount(semantics.reportedUsage?.[field]?.reportedCalls) }])) };
}

// The report is an allowlist of IDs, counts and bounded status labels. Never copy
// names, scenario labels, attributes, provider bodies, endpoints or I/O into it.
function traceDetails(label, record, expected, response) {
  const entry = { scenario: label, traceId: safeId(record?.trace_id, 32), ledgerValid: Boolean(expected),
    readback: "not-requested", spanCounts: { expectedDistinct: expected?.length ?? null,
      actualDistinct: null, actualRows: null, platformExpected: null },
    parents: { persistedChecked: false, anomalies: [], missingParentIds: null, invalidMissingParentIds: null },
    usage: { expected: expected ? expectedUsage(expected) : null, actual: null } };
  if (!expected) return entry;
  const httpOK = response.status >= 200 && response.status < 300 && response.body?.code === 0;
  const detail = httpOK && isObject(response.body?.data) ? response.body.data : null;
  entry.readback = detail ? "available" : "unavailable";
  const spans = Array.isArray(detail?.spans) ? detail.spans : null;
  const persistedById = new Map(spans?.filter(isObject).map((span) => [span.span_id, span]) ?? []);
  if (spans) {
    entry.spanCounts.actualRows = spans.length;
    entry.spanCounts.actualDistinct = new Set(spans.map((span) => safeId(span?.span_id, 16)).filter(Boolean)).size;
    entry.parents.persistedChecked = true;
  }
  entry.spanCounts.platformExpected = safeCount(detail?.meta?.expectedSpanCount);
  const missing = detail?.semantics?.missingParentIds;
  if (Array.isArray(missing)) {
    entry.parents.missingParentIds = missing.map((id) => safeId(id, 16)).filter(Boolean);
    entry.parents.invalidMissingParentIds = missing.length - entry.parents.missingParentIds.length;
  }
  const observedByKey = new Map(record.observed_spans.map((span) => [span.key, span]));
  for (const want of expected) {
    const source = observedByKey.get(want.key), actual = persistedById.get(source.span_id);
    const wantedParent = want.parent_key ? observedByKey.get(want.parent_key).span_id : null;
    const sourceMismatch = parentId(source.parent_span_id) !== wantedParent;
    const persistedMismatch = spans ? !actual || parentId(actual.parent_span_id) !== wantedParent : null;
    if (sourceMismatch || persistedMismatch) entry.parents.anomalies.push({ spanId: source.span_id,
      expectedParentId: wantedParent, observedParentId: safeParentId(source.parent_span_id),
      actualParentId: actual ? safeParentId(actual.parent_span_id) : null,
      actualSpanPresent: spans ? Boolean(actual) : null, sourceMismatch, persistedMismatch });
  }
  entry.usage.actual = actualUsage(detail?.semantics);
  return entry;
}

export function formatReport(report) {
  const lines = [`${report.passed ? "PASS" : "FAIL"}: ${report.traces} Eino traces, ${report.checks} checks, ${report.failures.length} failures (${report.modes.join(", ")})`];
  const count = (value) => value === null ? "unavailable" : String(value);
  const usage = (value) => value ? `${count(value.knownUsageCalls)}/${count(value.billableCalls)} known/billable calls, ${count(value.knownTokens)} known tokens` : "unavailable";
  const parent = (id, present = true) => present === null ? "unavailable" : !present ? "missing-span" : id ?? "root";
  for (const trace of report.traceDetails) {
    lines.push(`${trace.scenario} ${trace.traceId ?? "invalid-trace-id"}: ${trace.passed ? "PASS" : "FAIL"}; ledger ${trace.ledgerValid ? "valid" : "invalid"}; readback ${trace.readback}`);
    lines.push(`  spans: expected ${count(trace.spanCounts.expectedDistinct)} distinct; actual ${count(trace.spanCounts.actualDistinct)} distinct (${count(trace.spanCounts.actualRows)} rows); platform expected ${count(trace.spanCounts.platformExpected)}`);
    lines.push(`  usage: expected ${usage(trace.usage.expected)}; actual ${usage(trace.usage.actual)}`);
    lines.push(`  parents: persisted ${trace.parents.persistedChecked ? "checked" : "unavailable"}; ${trace.parents.anomalies.length} anomalies; unresolved IDs ${trace.parents.missingParentIds === null ? "unavailable" : trace.parents.missingParentIds.length + trace.parents.invalidMissingParentIds}`);
    for (const anomaly of trace.parents.anomalies) lines.push(`    span ${anomaly.spanId}: expected parent ${parent(anomaly.expectedParentId)}, source ${parent(anomaly.observedParentId)}, actual ${parent(anomaly.actualParentId, anomaly.actualSpanPresent)}`);
    if (trace.parents.missingParentIds?.length) lines.push(`    unresolved parents: ${trace.parents.missingParentIds.join(", ")}`);
  }
  if (report.failures.length) lines.push(report.failures.join("\n"));
  return lines.join("\n");
}

function statusCode(status) {
  const value = typeof status === "object" && status !== null ? status.code : status;
  if ([0, 1, 2].includes(value)) return value;
  return { UNSET: 0, OK: 1, ERROR: 2, STATUS_CODE_UNSET: 0, STATUS_CODE_OK: 1, STATUS_CODE_ERROR: 2,
    "0": 0, "1": 1, "2": 2 }[value];
}

async function readDetail(url, target, expectedCount, options) {
  const { fetchFn, maxAttempts, pollIntervalMs, timeoutMs, requestTimeoutMs } = options;
  const deadline = Date.now() + timeoutMs;
  let result = { status: null, body: null, attempts: 0 };
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    result.attempts = attempt;
    try {
      const response = await fetchFn(url, { headers: { authorization: `Bearer ${target.space_key}` },
        redirect: "manual", signal: AbortSignal.timeout(Math.max(1, Math.min(requestTimeoutMs, deadline - Date.now()))) });
      result.status = response.status;
      result.body = null;
      result.error = null;
      if (response.ok) {
        try { result.body = await response.json(); }
        catch { result.error = "invalid-json"; break; }
        if (result.body?.code !== 0) { result.error = "api-error"; break; }
        const detail = result.body.data;
        if (detail?.meta?.complete === true && detail.spans?.length === expectedCount) break;
      } else if (![404, 408, 429].includes(response.status) && response.status < 500) {
        result.error = "http-error";
        break;
      }
    } catch {
      // Never persist a raw URL, response body or exception from credentialed I/O.
      result.status = null;
      result.body = null;
      result.error = "transport-error";
    }
    if (attempt === maxAttempts || Date.now() >= deadline) break;
    await new Promise((done) => setTimeout(done, Math.min(pollIntervalMs, deadline - Date.now())));
    if (Date.now() >= deadline) break;
  }
  return result;
}

export async function verifyPlatform(target, ledgers, options = {}) {
  const origin = targetFields(target);
  const settings = { fetchFn: fetch, maxAttempts: 30, pollIntervalMs: 1000, timeoutMs: 60_000,
    requestTimeoutMs: 10_000, ...options };
  for (const key of ["maxAttempts", "timeoutMs", "requestTimeoutMs"])
    assert.ok(Number.isSafeInteger(settings[key]) && settings[key] > 0, `${key} must be a positive integer`);
  assert.ok(Number.isSafeInteger(settings.pollIntervalMs) && settings.pollIntervalMs >= 0,
    "pollIntervalMs must be a nonnegative integer");
  assert.ok(Array.isArray(ledgers) && ledgers.length, "At least one ledger is required");
  assert.ok(ledgers.every((ledger) => ledger.schema_version === 1 && ["fixture", "live"].includes(ledger.mode)
    && Array.isArray(ledger.scenarios) && ledger.scenarios.length), "Expected Eino schema_version 1 ledgers with scenarios");
  const records = ledgers.flatMap((ledger) => ledger.scenarios);
  const results = [], attempts = [], details = [];
  const check = (name, passed) => results.push({ name, passed: Boolean(passed) });
  for (const [index, record] of records.entries()) {
    const label = `scenario-${index + 1}`;
    if (!validScenario(record)) { check(`${label}:ledger`, false); details.push(traceDetails(label, record)); continue; }
    const expected = record.expected.spans;
    const observedByKey = new Map(record.observed_spans.map((span) => [span.key, span]));
    const params = new URLSearchParams({ from: String(record.from_ms), to: String(record.to_ms), agent_uuid: target.agent_uuid });
    const path = `/openapi/v1/spaces/${encodeURIComponent(target.space_id)}/agents/${encodeURIComponent(target.agent_product)}`
      + `/trace/${record.trace_id}/detail?${params}`;
    const response = await readDetail(origin + path, target, expected.length, settings);
    details.push(traceDetails(label, record, expected, response));
    attempts.push({ traceId: record.trace_id, attempts: response.attempts, httpStatus: response.status,
      ...(response.error ? { error: response.error } : {}) });
    const detail = response.body?.data ?? {}, rawSpans = Array.isArray(detail.spans) ? detail.spans : [];
    const spans = rawSpans.filter(isObject);
    const persistedById = new Map(spans.map((span) => [span.span_id, span]));
    check(`${label}:http`, response.status >= 200 && response.status < 300 && response.body?.code === 0);
    check(`${label}:trace-id`, detail.traceId === record.trace_id && spans.every((span) => span.trace_id === record.trace_id));
    check(`${label}:complete`, detail.meta?.complete === true);
    check(`${label}:inventory`, spans.length === rawSpans.length && spans.length === expected.length && persistedById.size === expected.length
      && record.observed_spans.every((span) => persistedById.has(span.span_id))
      && (detail.meta?.expectedSpanCount === undefined || detail.meta.expectedSpanCount === expected.length));
    check(`${label}:resolved-parents`, Array.isArray(detail.semantics?.missingParentIds) && detail.semantics.missingParentIds.length === 0);
    for (const [spanIndex, want] of expected.entries()) {
      const prefix = `${label}:span-${spanIndex + 1}`;
      const source = observedByKey.get(want.key), actual = persistedById.get(source.span_id);
      const wantedParent = want.parent_key === null || want.parent_key === "" ? null : observedByKey.get(want.parent_key)?.span_id;
      check(`${prefix}:parents`, parentId(source.parent_span_id) === wantedParent && actual
        && parentId(actual.parent_span_id) === wantedParent);
      check(`${prefix}:name`, actual?.name === want.name);
      const actualKind = actual?.attributes?.["langfuse.observation.type"];
      check(`${prefix}:classification`, typeof actualKind === "string" && actualKind.toLowerCase() === want.kind.toLowerCase());
      check(`${prefix}:status`, actual && statusCode(actual.status) === want.status);
      for (const field of ["input", "output"]) {
        const key = `langfuse.observation.${field}`, raw = actual?.attributes?.[key];
        if (Object.hasOwn(source.attributes ?? {}, key) || Object.hasOwn(source, field))
          check(`${prefix}:raw-${field}`, isDeepStrictEqual(raw,
            Object.hasOwn(source.attributes ?? {}, key) ? source.attributes[key] : source[field]));
        if (want[`${field}_contains`] !== undefined) {
          const needles = [want[`${field}_contains`]].flat();
          const text = typeof raw === "string" ? raw : raw === undefined ? "" : JSON.stringify(raw);
          check(`${prefix}:business-${field}`, needles.length > 0 && needles.every((needle) => typeof needle === "string" && text.includes(needle)));
        }
      }
      if (billable(want)) {
        check(`${prefix}:usage`, Object.values(usageFields).every((field) =>
          (actual?.attributes?.[`gen_ai.usage.${field}`] ?? null) === (want.usage?.[field] ?? null)));
      }
      if (source.usage_attributes) check(`${prefix}:raw-usage`, Object.entries(source.usage_attributes).every(([key, value]) =>
        isDeepStrictEqual(actual?.attributes?.[key], value)));
    }
    const calls = expected.filter(billable);
    for (const [field, sourceField] of Object.entries(usageFields)) {
      const values = calls.map((span) => span.usage?.[sourceField] ?? null);
      const known = values.filter((value) => value !== null);
      const subtotal = known.length ? known.reduce((sum, value) => sum + value, 0) : null;
      check(`${label}:usage-${field}`, detail.semantics?.usage?.[field] === (values.length && known.length === values.length ? subtotal : null));
      check(`${label}:reported-${field}`, isDeepStrictEqual(detail.semantics?.reportedUsage?.[field], { value: subtotal, reportedCalls: known.length }));
    }
    const known = calls.filter((span) => span.usage?.total_tokens != null);
    check(`${label}:coverage`, detail.semantics?.coverage?.billableCalls === calls.length
      && detail.semantics?.coverage?.knownUsageCalls === known.length
      && detail.semantics?.coverage?.knownTokens === known.reduce((sum, span) => sum + span.usage.total_tokens, 0));
  }
  const failures = results.filter((result) => !result.passed).map((result) => result.name);
  for (const detail of details) detail.passed = !failures.some((name) => name.startsWith(`${detail.scenario}:`));
  const report = { passed: failures.length === 0, traces: records.length, checks: results.length,
    modes: [...new Set(ledgers.map((ledger) => ledger.mode))], failures, results, attempts, traceDetails: details,
    identity: { spaceId: target.space_id, agentUuid: target.agent_uuid, agentProduct: target.agent_product } };
  if (options.reportPath) await writeFile(options.reportPath, JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const { values } = parseArgs({ options: { targets: { type: "string" }, "target-key": { type: "string", default: "langfuse" },
      ledger: { type: "string", multiple: true }, out: { type: "string" } } });
    assert.ok(values.targets && values.ledger?.length && values.out,
      "Usage: --targets FILE --ledger FILE [--ledger FILE] --out FILE [--target-key langfuse]");
    const target = await loadTarget(values.targets, values["target-key"]);
    const ledgers = await Promise.all(values.ledger.map(async (path) => JSON.parse(await readFile(path, "utf8"))));
    const report = await verifyPlatform(target, ledgers, { reportPath: values.out });
    console.log(formatReport(report));
    process.exitCode = report.passed ? 0 : 1;
  } catch {
    console.error("Eino verification could not run. Check target/ledger JSON, schema and output path; no credentials or response bodies were logged.");
    process.exitCode = 1;
  }
}
