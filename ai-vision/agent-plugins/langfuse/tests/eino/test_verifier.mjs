import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { formatReport, verifyPlatform } from "./verify_platform.mjs";

const traceId = "0123456789abcdef0123456789abcdef";
const rootId = "0123456789abcdef", callId = "0123456789abcde0";
const target = { endpoint: "http://127.0.0.1:18000", space_id: "test-space", agent_product: "langfuse",
  agent_uuid: "test-agent", space_key: "private-test-key" };

function fixture({ unknown = false, zero = false } = {}) {
  const input = zero ? 0 : 4, output = zero ? 0 : 5, total = zero ? 0 : 9;
  const ledger = { schema_version: 1, mode: "fixture", scenarios: [{ scenario: "basic", trace_id: traceId,
    from_ms: 1790000000000, to_ms: 1790000001001,
    expected: { span_count: 2, spans: [
      { key: "root", name: "root-chain", parent_key: null, kind: "chain", status: 0 },
      { key: "call", name: "chat-model", parent_key: "root", kind: "generation", status: 0,
        input_contains: "hello", output_contains: "world",
        usage: unknown ? null : { input_tokens: input, output_tokens: output, total_tokens: total } },
    ] }, observed_spans: [
      { key: "root", span_id: rootId, parent_span_id: null },
      { key: "call", span_id: callId, parent_span_id: rootId,
        input: '[{"role":"user","content":"hello"}]', output: '{"role":"assistant","content":"world"}' },
    ] }] };
  const body = { code: 0, data: { traceId, meta: { complete: true, expectedSpanCount: 2 }, spans: [
    { trace_id: traceId, span_id: rootId, parent_span_id: "", name: "root-chain", status: { code: "STATUS_CODE_UNSET" },
      attributes: { "langfuse.observation.type": "chain" } },
    { trace_id: traceId, span_id: callId, parent_span_id: rootId, name: "chat-model", status: { code: "STATUS_CODE_UNSET" },
      attributes: { "langfuse.observation.type": "generation", "langfuse.observation.input": '[{"role":"user","content":"hello"}]',
        "langfuse.observation.output": '{"role":"assistant","content":"world"}',
        ...(unknown ? {} : { "gen_ai.usage.input_tokens": input, "gen_ai.usage.output_tokens": output, "gen_ai.usage.total_tokens": total }) } },
  ], semantics: { missingParentIds: [], usage: { input: unknown ? null : input, output: unknown ? null : output, total: unknown ? null : total },
    reportedUsage: { input: { value: unknown ? null : input, reportedCalls: unknown ? 0 : 1 },
      output: { value: unknown ? null : output, reportedCalls: unknown ? 0 : 1 }, total: { value: unknown ? null : total, reportedCalls: unknown ? 0 : 1 } },
    coverage: { billableCalls: 1, knownUsageCalls: unknown ? 0 : 1, knownTokens: unknown ? 0 : total, knownCostCalls: 0 } } } };
  return { ledger, body };
}

async function run(value, options = {}) {
  return verifyPlatform(target, [value.ledger], { maxAttempts: 2, pollIntervalMs: 0,
    fetchFn: async () => Response.json(value.body), ...options });
}

test("readback verifies persisted IDs, topology, IO, usage and reporting coverage", async () => {
  const report = await run(fixture());
  assert.equal(report.passed, true);
  assert.equal(report.traces, 1);
  assert.ok(report.checks >= 15);
  const detail = report.traceDetails[0];
  assert.equal(detail.passed, true);
  assert.equal(detail.readback, "available");
  assert.deepEqual(detail.spanCounts, { expectedDistinct: 2, actualDistinct: 2, actualRows: 2, platformExpected: 2 });
  assert.deepEqual(detail.usage.expected, detail.usage.actual);
  assert.deepEqual(detail.parents.anomalies, []);
  assert.match(formatReport(report), /spans: expected 2 distinct; actual 2 distinct \(2 rows\)/);
  assert.match(formatReport(report), /usage: expected 1\/1 known\/billable calls, 9 known tokens; actual 1\/1 known\/billable calls, 9 known tokens/);
});

test("trace details distinguish missing spans, duplicate rows and platform inventory metadata", async () => {
  const missing = fixture(); missing.body.data.spans.pop();
  const missingReport = await run(missing);
  assert.deepEqual(missingReport.traceDetails[0].spanCounts,
    { expectedDistinct: 2, actualDistinct: 1, actualRows: 1, platformExpected: 2 });
  assert.equal(missingReport.traceDetails[0].parents.anomalies[0].actualSpanPresent, false);
  assert.match(formatReport(missingReport), /actual missing-span/);
  const duplicate = fixture(); duplicate.body.data.spans.push(structuredClone(duplicate.body.data.spans[1]));
  assert.deepEqual((await run(duplicate)).traceDetails[0].spanCounts,
    { expectedDistinct: 2, actualDistinct: 2, actualRows: 3, platformExpected: 2 });
  const metadata = fixture(); metadata.body.data.meta.expectedSpanCount = 3;
  assert.equal((await run(metadata)).traceDetails[0].spanCounts.platformExpected, 3);
});

test("parent anomalies retain independent expected, exported and persisted parent IDs", async () => {
  const value = fixture(), wrongParent = "f".repeat(16);
  value.ledger.scenarios[0].observed_spans[1].parent_span_id = null;
  value.body.data.spans[1].parent_span_id = wrongParent;
  value.body.data.semantics.missingParentIds = [wrongParent];
  const report = await run(value), detail = report.traceDetails[0];
  assert.deepEqual(detail.parents.anomalies, [{ spanId: callId, expectedParentId: rootId,
    observedParentId: null, actualParentId: wrongParent, actualSpanPresent: true,
    sourceMismatch: true, persistedMismatch: true }]);
  assert.deepEqual(detail.parents.missingParentIds, [wrongParent]);
  assert.match(formatReport(report), new RegExp(`span ${callId}: expected parent ${rootId}, source root, actual ${wrongParent}`));
});

test("usage details expose coverage and known subtotal mismatches without recomputing actual values", async () => {
  const value = fixture();
  value.body.data.semantics.coverage = { billableCalls: 3, knownUsageCalls: 2, knownTokens: 99 };
  value.body.data.semantics.reportedUsage.total = { value: 99, reportedCalls: 2 };
  const report = await run(value), { expected, actual } = report.traceDetails[0].usage;
  assert.equal(report.passed, false);
  assert.equal(expected.billableCalls, 1); assert.equal(actual.billableCalls, 3);
  assert.equal(expected.knownUsageCalls, 1); assert.equal(actual.knownUsageCalls, 2);
  assert.equal(expected.knownTokens, 9); assert.equal(actual.knownTokens, 99);
  assert.deepEqual(actual.reported.total, { value: 99, reportedCalls: 2 });
  assert.match(formatReport(report), /expected 1\/1 known\/billable calls, 9 known tokens; actual 2\/3 known\/billable calls, 99 known tokens/);
});

for (const [name, mutate] of [
  ["wrong trace identity", (f) => { f.body.data.traceId = "f".repeat(32); }],
  ["a foreign trace span", (f) => { f.body.data.spans[1].trace_id = "f".repeat(32); }],
  ["a missing persisted span", (f) => { f.body.data.spans.pop(); }],
  ["duplicate persisted IDs", (f) => { f.body.data.spans[1].span_id = rootId; }],
  ["wrong persisted parent", (f) => { f.body.data.spans[1].parent_span_id = "f".repeat(16); }],
  ["wrong source parent despite matching platform", (f) => { f.ledger.scenarios[0].observed_spans[1].parent_span_id = null; f.body.data.spans[1].parent_span_id = null; }],
  ["an incorrect expected count", (f) => { f.ledger.scenarios[0].expected.span_count = 1; }],
  ["a different expected topology", (f) => { f.ledger.scenarios[0].expected.spans[1].parent_key = null; }],
  ["a different expected provider usage", (f) => { f.ledger.scenarios[0].expected.spans[1].usage.total_tokens = 99; }],
  ["changed classification", (f) => { f.body.data.spans[1].attributes["langfuse.observation.type"] = "tool"; }],
  ["lost error status", (f) => { f.ledger.scenarios[0].expected.spans[1].status = 2; }],
  ["a missing status", (f) => { delete f.body.data.spans[1].status; }],
  ["changed raw IO despite containing business output", (f) => { f.body.data.spans[1].attributes["langfuse.observation.output"] = "world"; }],
  ["missing business input", (f) => { f.ledger.scenarios[0].expected.spans[1].input_contains = "required input"; }],
  ["corrupt per-call usage despite correct totals", (f) => { f.body.data.spans[1].attributes["gen_ai.usage.input_tokens"] = 99; }],
  ["corrupt aggregate usage", (f) => { f.body.data.semantics.usage.total = 99; }],
  ["corrupt known subtotal", (f) => { f.body.data.semantics.reportedUsage.total.value = 99; }],
  ["corrupt usage coverage", (f) => { f.body.data.semantics.coverage.knownUsageCalls = 0; }],
  ["unresolved parents", (f) => { f.body.data.semantics.missingParentIds = ["missing"]; }],
  ["a complete flag with a larger expected inventory", (f) => { f.body.data.meta.expectedSpanCount = 3; }],
]) {
  test(`readback rejects ${name}`, async () => {
    const value = fixture(); mutate(value);
    const report = await run(value);
    assert.equal(report.passed, false);
    assert.ok(report.failures.length > 0);
  });
}

test("unknown usage stays unknown while an explicitly reported zero remains zero", async () => {
  const unknown = await run(fixture({ unknown: true })), zero = await run(fixture({ zero: true }));
  assert.equal(unknown.passed, true);
  assert.equal(zero.passed, true);
  for (const side of ["expected", "actual"]) {
    assert.equal(unknown.traceDetails[0].usage[side].knownUsageCalls, 0);
    assert.equal(zero.traceDetails[0].usage[side].knownUsageCalls, 1);
    assert.equal(unknown.traceDetails[0].usage[side].knownTokens, 0);
    assert.equal(zero.traceDetails[0].usage[side].knownTokens, 0);
    assert.equal(unknown.traceDetails[0].usage[side].totals.total, null);
    assert.equal(zero.traceDetails[0].usage[side].totals.total, 0);
    assert.deepEqual(unknown.traceDetails[0].usage[side].reported.total, { value: null, reportedCalls: 0 });
    assert.deepEqual(zero.traceDetails[0].usage[side].reported.total, { value: 0, reportedCalls: 1 });
  }
  assert.match(formatReport(unknown), /0\/1 known\/billable calls, 0 known tokens/);
  assert.match(formatReport(zero), /1\/1 known\/billable calls, 0 known tokens/);
  for (const mutate of [
    (f) => { f.body.data.spans[1].attributes["gen_ai.usage.total_tokens"] = 0; },
    (f) => { f.body.data.semantics.usage.total = 0; },
    (f) => { f.body.data.semantics.reportedUsage.total.value = 0; },
  ]) {
    const value = fixture({ unknown: true }); mutate(value);
    assert.equal((await run(value)).passed, false);
  }
});

test("readback sends the exact ledger time window and Agent scope without persisting credentials", async () => {
  const value = fixture(); const requests = [];
  const dir = await mkdtemp(join(tmpdir(), "eino-verifier-"));
  const reportPath = join(dir, "report.json");
  try {
    const report = await run(value, { reportPath, fetchFn: async (url, options) => {
      requests.push({ url, options }); return Response.json(value.body);
    } });
    assert.equal(report.passed, true);
    const url = new URL(requests[0].url);
    assert.equal(url.pathname, `/openapi/v1/spaces/test-space/agents/langfuse/trace/${traceId}/detail`);
    assert.deepEqual(Object.fromEntries(url.searchParams), { from: "1790000000000", to: "1790000001001", agent_uuid: "test-agent" });
    assert.equal(requests[0].options.headers.authorization, "Bearer private-test-key");
    assert.equal(requests[0].options.redirect, "manual");
    const saved = await readFile(reportPath, "utf8");
    assert.doesNotMatch(saved, /private-test-key|authorization|hello|world|http:\/\//i);
    assert.deepEqual(JSON.parse(saved), report);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test("bounded polling waits for complete persisted inventory, never for ingestion HTTP success", async () => {
  const value = fixture(); let attempts = 0;
  const report = await run(value, { maxAttempts: 3, fetchFn: async () => {
    attempts++;
    if (attempts === 1) return Response.json({ code: 0, data: { meta: { complete: false }, spans: [] } });
    if (attempts === 2) return Response.json({ code: 0, data: { ...value.body.data, spans: [] } });
    return Response.json(value.body);
  } });
  assert.equal(report.passed, true);
  assert.equal(attempts, 3);
});

test("incomplete data and non-2xx responses fail after the polling cap", async () => {
  for (const response of [
    () => Response.json({ code: 0, data: { meta: { complete: false }, spans: [] } }),
    () => Response.json({ message: "temporarily unavailable" }, { status: 503 }),
    () => Response.json({ message: "not yet available" }, { status: 404 }),
  ]) {
    let attempts = 0;
    const report = await run(fixture(), { maxAttempts: 3, fetchFn: async () => { attempts++; return response(); } });
    assert.equal(report.passed, false);
    assert.equal(attempts, 3);
  }
});

test("authentication failures, redirects and invalid JSON fail without leaking response content", async () => {
  for (const response of [
    () => Response.json({ message: "private-test-key" }, { status: 401 }),
    () => new Response("private-test-key", { status: 307, headers: { location: "http://other.invalid/" } }),
    () => new Response("private-test-key", { status: 200 }),
    () => Response.json({ code: 9, message: "private-test-key" }),
  ]) {
    let attempts = 0;
    const report = await run(fixture(), { fetchFn: async () => { attempts++; return response(); } });
    assert.equal(report.passed, false);
    assert.equal(attempts, 1);
    assert.doesNotMatch(JSON.stringify(report), /private-test-key/);
    const detail = report.traceDetails[0];
    assert.equal(detail.readback, "unavailable");
    assert.equal(detail.spanCounts.actualDistinct, null);
    assert.equal(detail.spanCounts.actualRows, null);
    assert.equal(detail.usage.actual, null);
    assert.equal(detail.parents.persistedChecked, false);
    assert.deepEqual(detail.parents.anomalies, []);
    assert.match(formatReport(report), /actual unavailable distinct \(unavailable rows\)/);
    assert.match(formatReport(report), /actual unavailable/);
  }
});

test("invalid scenario ledgers fail without querying or inventing expected/actual measurements", async () => {
  for (const scenario of [null, { trace_id: "private-test-key" },
    { ...fixture().ledger.scenarios[0], expected: { span_count: 1, spans: [null] } },
    { ...fixture().ledger.scenarios[0], observed_spans: [null, {}] }]) {
    const value = fixture(); value.ledger.scenarios[0] = scenario;
    const report = await run(value, { fetchFn: async () => { assert.fail("invalid ledger must not query"); } });
    assert.deepEqual(report.failures, ["scenario-1:ledger"]);
    assert.deepEqual(report.attempts, []);
    const detail = report.traceDetails[0];
    assert.equal(detail.ledgerValid, false);
    assert.equal(detail.passed, false);
    assert.equal(detail.readback, "not-requested");
    assert.deepEqual(detail.spanCounts, { expectedDistinct: null, actualDistinct: null, actualRows: null, platformExpected: null });
    assert.deepEqual(detail.usage, { expected: null, actual: null });
    assert.doesNotMatch(JSON.stringify(report) + formatReport(report), /private-test-key/);
  }
});

test("unavailable readback keeps source-parent anomalies separate from unmeasured platform parents", async () => {
  const value = fixture();
  value.ledger.scenarios[0].observed_spans[1].parent_span_id = null;
  const report = await run(value, { fetchFn: async () => Response.json({ message: "unavailable" }, { status: 503 }) });
  assert.deepEqual(report.traceDetails[0].parents.anomalies, [{ spanId: callId, expectedParentId: rootId,
    observedParentId: null, actualParentId: null, actualSpanPresent: null,
    sourceMismatch: true, persistedMismatch: null }]);
  assert.match(formatReport(report), /source root, actual unavailable/);
});

test("malformed persisted span entries produce a failed report with the returned row count", async () => {
  const value = fixture(); value.body.data.spans[1] = null;
  const report = await run(value);
  assert.equal(report.passed, false);
  assert.ok(report.failures.includes("scenario-1:inventory"));
  assert.deepEqual(report.traceDetails[0].spanCounts,
    { expectedDistinct: 2, actualDistinct: 1, actualRows: 2, platformExpected: 2 });
});

test("JSON and CLI details allowlist diagnostic values without exposing source or response content", async () => {
  const value = fixture(), secret = "private-body-and-key";
  value.ledger.scenarios[0].scenario = secret;
  value.ledger.scenarios[0].expected.spans[1].name = secret;
  value.ledger.scenarios[0].observed_spans[1].input = secret;
  value.body.data.spans[1].name = secret;
  value.body.data.spans[1].attributes["langfuse.observation.input"] = secret;
  value.body.data.spans[1].attributes["langfuse.observation.output"] = secret;
  value.body.data.spans[1].parent_span_id = secret;
  value.body.data.meta.expectedSpanCount = secret;
  value.body.data.semantics.missingParentIds = [secret, { secret }];
  value.body.data.semantics.coverage.knownTokens = secret;
  value.body.data.semantics.reportedUsage.total.value = { secret };
  const dir = await mkdtemp(join(tmpdir(), "eino-report-safety-"));
  try {
    const reportPath = join(dir, "report.json"), report = await run(value, { reportPath });
    assert.equal(report.passed, false);
    assert.equal(report.traceDetails[0].parents.anomalies[0].actualParentId, "invalid");
    assert.equal(report.traceDetails[0].parents.invalidMissingParentIds, 2);
    assert.equal(report.traceDetails[0].usage.actual.knownTokens, null);
    assert.doesNotMatch(await readFile(reportPath, "utf8"), /private-body-and-key|private-test-key/);
    assert.doesNotMatch(formatReport(report), /private-body-and-key|private-test-key/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test("network errors are bounded and the raw exception cannot leak credentials", async () => {
  let attempts = 0;
  const report = await run(fixture(), { fetchFn: async () => { attempts++; throw new Error("private-test-key"); } });
  assert.equal(report.passed, false);
  assert.equal(attempts, 2);
  assert.doesNotMatch(JSON.stringify(report), /private-test-key/);
});

test("a repeated read of the same trace keeps an exact unique inventory", async () => {
  const value = fixture();
  assert.equal((await run(value)).passed, true);
  assert.equal((await run(value)).passed, true);
  value.body.data.spans.push(structuredClone(value.body.data.spans[1]));
  assert.equal((await run(value)).passed, false);
});

test("explicit null raw IO is preserved without being treated as an absent source field", async () => {
  const value = fixture();
  value.ledger.scenarios[0].observed_spans[0].attributes = { "langfuse.observation.output": null };
  value.body.data.spans[0].attributes["langfuse.observation.output"] = null;
  assert.equal((await run(value)).passed, true);
});

test("malformed persisted classification fails as a check instead of crashing the report", async () => {
  const value = fixture();
  value.body.data.spans[1].attributes["langfuse.observation.type"] = 42;
  assert.equal((await run(value)).passed, false);
});

test("Go empty parent keys and all-zero OTLP root parent IDs represent the root", async () => {
  const value = fixture();
  value.ledger.scenarios[0].expected.spans[0].parent_key = "";
  value.ledger.scenarios[0].observed_spans[0].parent_span_id = "0000000000000000";
  value.body.data.spans[0].parent_span_id = "0000000000000000";
  assert.equal((await run(value)).passed, true);
});

test("SDK usage evidence must survive persistence even when independent totals are correct", async () => {
  const value = fixture();
  value.ledger.scenarios[0].observed_spans[1].usage_attributes = { "gen_ai.usage.input_tokens": 99 };
  assert.equal((await run(value)).passed, false);
});
