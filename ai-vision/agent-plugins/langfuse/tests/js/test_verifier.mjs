import assert from "node:assert/strict";
import test from "node:test";
import { mkdir, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { exactTimestampNs, verifyPlatform } from "./verify_platform.mjs";
import { providerRequestParameters, recordProviderUsage, assertBoundedProviderUsage } from "./live_provider_ledger.mjs";
import * as liveLedger from "./live_provider_ledger.mjs";

const usageAttribute = "langfuse.observation.usage_details";
const usageFields = { input: "gen_ai.usage.input_tokens", output: "gen_ai.usage.output_tokens",
  total: "gen_ai.usage.total_tokens", cacheReadInput: "gen_ai.usage.cache_read.input_tokens",
  cacheWriteInput: "gen_ai.usage.cache_creation.input_tokens", reasoning: "gen_ai.usage.reasoning.output_tokens" };

function usageFixture({ details = "reported", unknown = false, zero = false } = {}) {
  const providerUsage = unknown ? null : { prompt_tokens: zero ? 0 : 4, completion_tokens: zero ? 0 : 5,
    total_tokens: zero ? 0 : 9, ...(details === "absent" ? {} : {
      prompt_tokens_details: { cached_tokens: details === "zero" || zero ? 0 : 1 },
      completion_tokens_details: { reasoning_tokens: details === "zero" || zero ? 0 : 2 } }) };
  const raw = unknown ? null : { input: zero ? 0 : details === "reported" ? 3 : 4,
    output: zero ? 0 : details === "reported" ? 3 : 5, total: zero ? 0 : 9,
    ...(details === "absent" ? {} : { input_cached_tokens: details === "zero" || zero ? 0 : 1,
      output_reasoning_tokens: details === "zero" || zero ? 0 : 2 }) };
  const values = unknown ? Object.fromEntries(Object.keys(usageFields).map((key) => [key, null])) : {
    input: providerUsage.prompt_tokens, output: providerUsage.completion_tokens, total: providerUsage.total_tokens,
    cacheReadInput: raw.input_cached_tokens ?? 0, cacheWriteInput: 0, reasoning: raw.output_reasoning_tokens ?? 0 };
  const sources = unknown ? {} : { input: "langfuse.exclusive_input_plus_cache",
    output: "langfuse.exclusive_output_plus_reasoning", total: `${usageAttribute}.total`,
    cacheReadInput: details === "absent" ? "langfuse.complete_usage_absent_bucket" : `${usageAttribute}.input_cached_tokens`,
    cacheWriteInput: "langfuse.complete_usage_absent_bucket",
    reasoning: details === "absent" ? "langfuse.complete_usage_absent_bucket" : `${usageAttribute}.output_reasoning_tokens` };
  const usageAttributes = raw === null ? {} : { [usageAttribute]: JSON.stringify(raw) };
  const source = { id: "generation", parentId: null, name: "one-call", type: "generation", status: 0,
    startTimeUnixNano: "1790000000000000000", endTimeUnixNano: "1790000000001000000", usageAttributes };
  const attributes = { "langfuse.observation.type": "generation", ...usageAttributes,
    ...Object.fromEntries(Object.entries(values).filter(([, value]) => value !== null).map(([key, value]) => [usageFields[key], value])),
    "aivision.normalization.sources": JSON.stringify(Object.fromEntries(Object.entries(sources).map(([key, value]) => [usageFields[key], value]))) };
  const aggregate = { ...values, source: Object.fromEntries(Object.entries(values).filter(([, value]) => value !== null).map(([key]) => [key, "aggregate_unique_calls"])) };
  const native = { code: 0, data: { meta: { complete: true }, spans: [{ span_id: source.id, parent_span_id: null,
    name: source.name, status: 0, attributes, start_time_unix_nano: source.startTimeUnixNano,
    end_time_unix_nano: source.endTimeUnixNano }], semantics: { usage: aggregate } } };
  const v1 = { id: "trace", observations: [{ id: source.id, usage: {
    input: values.input ?? 0, output: values.output ?? 0, total: values.total ?? 0, unit: "TOKENS" }, usageDetails: raw ?? {} }] };
  const record = { scenario: "source", sdkVersion: "6.0.0", traceId: "trace", expectedUsage: unknown ? null : {
    input: values.input, output: values.output, total: values.total }, inventory: [source],
    expectedGenerations: [{ id: source.id, providerUsage }] };
  return { native, v1, record };
}

async function verifyUsageFixture(fixture) {
  const original = globalThis.fetch;
  globalThis.fetch = async (url) => new Response(JSON.stringify(String(url).includes("/openapi/") ? fixture.native : fixture.v1),
    { status: 200, headers: { "content-type": "application/json" } });
  try { return await verifyPlatform({ endpoint: "http://127.0.0.1:12345", space_id: "space", agent_uuid: "agent",
    agent_product: "langfuse", space_key: "synthetic" }, [[fixture.record]]); }
  finally { globalThis.fetch = original; }
}

for (const [name, mutate] of [
  ["corrupted native detail buckets", (f) => { f.native.data.semantics.usage.cacheReadInput = 999; f.native.data.semantics.usage.reasoning = 999; }],
  ["corrupted generation detail bucket", (f) => { f.native.data.spans[0].attributes[usageFields.cacheReadInput] = 999; }],
  ["missing generation usage provenance", (f) => { f.native.data.spans[0].attributes["aivision.normalization.sources"] = "{}"; }],
  ["incorrect explicit-zero provenance", (f) => { const attrs = f.native.data.spans[0].attributes; const sources = JSON.parse(attrs["aivision.normalization.sources"]); sources[usageFields.cacheReadInput] = "langfuse.complete_usage_absent_bucket"; attrs["aivision.normalization.sources"] = JSON.stringify(sources); }],
  ["missing trace usage source", (f) => { f.native.data.semantics.usage.source = {}; }],
  ["changed raw SDK usage", (f) => { f.native.data.spans[0].attributes[usageAttribute] = JSON.stringify({ input: 999, output: 999, total: 1998 }); }],
  ["corrupted SDK capture despite matching platform", (f) => { const raw = JSON.stringify({ input: 999, output: 999, total: 1998 }); f.record.inventory[0].usageAttributes[usageAttribute] = raw; f.native.data.spans[0].attributes[usageAttribute] = raw; }],
  ["incorrect v1 totals", (f) => { f.v1.observations[0].usage = { input: 999, output: 999, total: 1998, unit: "TOKENS" }; }],
  ["missing v1 explicit zero buckets", (f) => { delete f.v1.observations[0].usageDetails.input_cached_tokens; }],
  ["missing independent generation reference", (f) => { delete f.record.expectedGenerations; }],
  ["missing original SDK usage evidence", (f) => { delete f.record.inventory[0].usageAttributes; }],
]) {
  test(`platform usage gate rejects ${name}`, async () => {
    const fixture = usageFixture({ details: "zero" });
    mutate(fixture);
    assert.equal((await verifyUsageFixture(fixture)).passed, false);
  });
}

for (const options of [{}, { details: "absent" }, { details: "zero" }, { zero: true }, { unknown: true }]) {
  test(`platform usage gate accepts independently reported ${JSON.stringify(options)}`, async () => {
    assert.equal((await verifyUsageFixture(usageFixture(options))).passed, true);
  });
}

test("original generation usage attributes are retained in the source inventory", async () => {
  const { sourceInventory } = await import("./support.mjs");
  const [span] = sourceInventory([{ spanId: "generation", attributes: [
    { key: "langfuse.observation.type", value: { stringValue: "generation" } },
    { key: usageAttribute, value: { stringValue: '{"input":0,"output":0,"total":0}' } },
  ] }]);
  assert.deepEqual(span.usageAttributes, { [usageAttribute]: '{"input":0,"output":0,"total":0}' });
});

test("per-generation usage cannot hide behind correct aggregate totals", async () => {
  const fixture = usageFixture();
  const source = structuredClone(fixture.record.inventory[0]); source.id = "generation-two";
  fixture.record.inventory.push(source);
  fixture.record.expectedGenerations.push({ ...fixture.record.expectedGenerations[0], id: source.id });
  const span = structuredClone(fixture.native.data.spans[0]); span.span_id = source.id;
  fixture.native.data.spans.push(span);
  const observation = structuredClone(fixture.v1.observations[0]); observation.id = source.id;
  fixture.v1.observations.push(observation);
  for (const field of Object.keys(usageFields)) fixture.native.data.semantics.usage[field] *= 2;
  fixture.record.expectedUsage = { input: 8, output: 10, total: 18 };
  assert.equal((await verifyUsageFixture(fixture)).passed, true);
  fixture.v1.observations[0].usage.input--;
  fixture.v1.observations[1].usage.input++;
  assert.equal((await verifyUsageFixture(fixture)).passed, false);
});

test("SDK 5 and 6 lifecycle records inherit their version without changing zero provenance", async () => {
  for (const sdkVersion of ["5.14.0", "6.0.0"]) {
    const fixture = usageFixture({ details: "absent" });
    const record = fixture.record;
    fixture.record = { sdkVersion, scenario: "safe-no-details", expectedByTrace: [{ ...record, usage: record.expectedUsage }] };
    assert.equal((await verifyUsageFixture(fixture)).passed, true);
  }
});

test("unknown SDK usage cannot become explicit zero at either API boundary", async () => {
  for (const mutate of [
    (f) => { f.native.data.spans[0].attributes[usageFields.input] = 0; },
    (f) => { f.v1.observations[0].usageDetails = { input: 0, output: 0, total: 0 }; },
  ]) {
    const fixture = usageFixture({ unknown: true });
    mutate(fixture);
    assert.equal((await verifyUsageFixture(fixture)).passed, false);
  }
});

const listen = (server) => new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${server.address().port}`)));
const close = (server) => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));

test("platform readback binds evidence to exactly three public target fields", async () => {
  const target = { endpoint: "http://127.0.0.1:12345", space_id: "source-space", agent_uuid: "source-agent",
    agent_product: "langfuse", space_key: "synthetic-secret-never-persist", private_config: "never-copy" };
  const source = { id: "root", parentId: null, name: "test-root", status: 0,
    startTimeUnixNano: "1790000000000000000", endTimeUnixNano: "1790000000001000000" };
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    const body = String(url).includes("/openapi/") ? { code: 0, data: { meta: { complete: true },
      spans: [{ span_id: source.id, parent_span_id: null, name: source.name, status: 0, attributes: {},
        start_time_unix_nano: source.startTimeUnixNano, end_time_unix_nano: source.endTimeUnixNano }],
      semantics: { usage: { input: null, output: null, total: null, cacheReadInput: null, cacheWriteInput: null, reasoning: null, source: {} } } } }
      : { id: "trace", observations: [{ id: source.id }] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  };
  try {
    const report = await verifyPlatform(target, [[{ scenario: "source", traceId: "trace",
      expectedUsage: null, inventory: [source] }]]);
    assert.equal(report.passed, true);
    assert.deepEqual(report.identity, { spaceId: "source-space", agentUuid: "source-agent", agentProduct: "langfuse" });
    assert.ok(requests[0].includes("/spaces/source-space/agents/langfuse/trace/trace/detail"));
    assert.ok(requests[0].includes("agent_uuid=source-agent"));
    assert.doesNotMatch(JSON.stringify(report), /synthetic-secret|never-copy/);
    assert.equal(report.checks, 15);
  } finally { globalThis.fetch = original; }
});

test("live provider fetch cannot follow a redirect into a second physical POST", async () => {
  assert.equal(typeof liveLedger.fetchProviderOnce, "function");
  const paths = [];
  const server = createServer((request, response) => {
    paths.push(request.url);
    if (request.url === "/first") { response.writeHead(307, { location: "/repeated" }); response.end(); }
    else { response.end("second physical request"); }
  });
  const origin = await listen(server);
  try {
    await assert.rejects(liveLedger.fetchProviderOnce(`${origin}/first`, { method: "POST", body: "fixed-test" }));
    assert.deepEqual(paths, ["/first"]);
  } finally { await close(server); }
});

test("rejected duplicate request does not end or attach its error to the in-flight provider attempt", async () => {
  assert.equal(typeof liveLedger.forwardProviderRequest, "function");
  let signalStarted, release;
  const started = new Promise((resolve) => { signalStarted = resolve; });
  const released = new Promise((resolve) => { release = resolve; });
  let physicalRequests = 0;
  const upstream = createServer(async (_request, response) => {
    physicalRequests++; signalStarted(); await released;
    response.setHeader("content-type", "text/event-stream");
    response.end('data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n');
  });
  const baseUrl = await listen(upstream);
  const budget = liveLedger.createProviderBudget(["live-stream"]), attempts = [], providerRecords = [];
  const receiver = createServer(async (request, response) => {
    try {
      const chunks = []; for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks), input = JSON.parse(body);
      await liveLedger.forwardProviderRequest({ input, body, response, scenario: "live-stream", budget,
        attempts, providerRecords, baseUrl, providerKey: "synthetic-only" });
    } catch { response.statusCode = 502; response.end(); }
  });
  const receiverUrl = await listen(receiver);
  const body = JSON.stringify({ model: "fixture", max_tokens: 32, stream: true, stream_options: { include_usage: true } });
  let first;
  try {
    first = fetch(receiverUrl, { method: "POST", body }).then((response) => response.text());
    await started;
    const rejected = await fetch(receiverUrl, { method: "POST", body }); await rejected.text();
    assert.equal(rejected.status, 502);
    assert.equal(attempts.length, 2);
    assert.equal(attempts[0].upstreamAttempted, true);
    assert.equal(attempts[0].errorType, undefined);
    assert.equal(attempts[0].endedAt, undefined);
    assert.equal(attempts[1].upstreamAttempted, false);
    assert.equal(attempts[1].errorType, "AssertionError");
    assert.ok(attempts[1].endedAt);
    release(); await first;
    assert.ok(attempts[0].endedAt);
    assert.equal(attempts[0].textChunks, 1);
    assert.equal(providerRecords.length, 1);
    assert.equal(physicalRequests, 1); assert.equal(budget.count, 1);
  } finally { release(); await first; await close(receiver); await close(upstream); }
});

test("an unwritable ledger does not prevent attempt and failed-status receipts", async () => {
  const directory = await mkdtemp(join(tmpdir(), "aiv-live-write-failure-"));
  try {
    await mkdir(join(directory, "live-ledger.json"));
    const status = { passed: true, physicalRequests: 1 };
    await assert.rejects(liveLedger.writeLiveEvidence(directory, {
      ledger: [], attempts: [{ usage: null }], status,
    }));
    assert.deepEqual(JSON.parse(await readFile(join(directory, "provider-attempts.json"), "utf8")), [{ usage: null }]);
    const saved = JSON.parse(await readFile(join(directory, "run-status.json"), "utf8"));
    assert.equal(saved.passed, false);
    assert.deepEqual(saved.errors, [{ stage: "write.live-ledger.json", type: "Error" }]);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("cleanup failure still removes temporary runtime and saves failure evidence", async () => {
  assert.equal(typeof liveLedger.finalizeLiveRun, "function");
  const directory = await mkdtemp(join(tmpdir(), "aiv-live-finalize-"));
  const runtime = join(directory, "temporary-runtime");
  await mkdir(runtime);
  try {
    const status = { passed: true, physicalRequests: 1 };
    const passed = await liveLedger.finalizeLiveRun(directory, { ledger: [], attempts: [{ usage: null }], status }, {
      closeServer: async () => { throw new Error("sensitive detail must not be recorded"); },
      removeTemporary: () => rm(runtime, { recursive: true }),
    });
    assert.equal(passed, false);
    await assert.rejects(stat(runtime), { code: "ENOENT" });
    const saved = JSON.parse(await readFile(join(directory, "run-status.json"), "utf8"));
    assert.equal(saved.passed, false);
    assert.deepEqual(saved.errors, [{ stage: "server.close", type: "Error" }]);
    assert.doesNotMatch(JSON.stringify(saved), /sensitive detail/);
    assert.deepEqual(JSON.parse(await readFile(join(directory, "provider-attempts.json"), "utf8")), [{ usage: null }]);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("live provider budget rejects repeated or unknown cases before another physical request", () => {
  assert.equal(typeof liveLedger.createProviderBudget, "function");
  const budget = liveLedger.createProviderBudget(["live-stream", "provider-error"]);
  budget.claim("live-stream");
  assert.throws(() => budget.claim("live-stream"), /budget/i);
  assert.throws(() => budget.claim("unexpected"), /budget/i);
  budget.claim("provider-error");
  assert.throws(() => budget.claim("provider-error"), /budget/i);
  assert.equal(budget.count, 2);
});

test("live failure receipt preserves partial attempts without turning unknown usage into zero", async () => {
  assert.equal(typeof liveLedger.writeLiveEvidence, "function");
  const directory = await mkdtemp(join(tmpdir(), "aiv-live-receipt-"));
  try {
    await liveLedger.writeLiveEvidence(directory, { ledger: [], attempts: [{ scenario: "live-stream",
      httpStatus: 200, usage: null, usageEvents: [], errorType: "TimeoutError" }],
      status: { passed: false, errorType: "AssertionError", physicalRequests: 1 } });
    assert.deepEqual(JSON.parse(await readFile(join(directory, "live-ledger.json"), "utf8")), []);
    const attempts = JSON.parse(await readFile(join(directory, "provider-attempts.json"), "utf8"));
    assert.equal(attempts[0].usage, null);
    assert.equal(attempts[0].errorType, "TimeoutError");
    assert.deepEqual(JSON.parse(await readFile(join(directory, "run-status.json"), "utf8")),
      { passed: false, errorType: "AssertionError", physicalRequests: 1 });
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("platform timestamp comparison keeps nanoseconds without unsafe JSON numbers", () => {
  const expected = 1792381234609384375n;
  assert.equal(exactTimestampNs("2026-10-19T03:40:34.609384375Z"), expected);
  assert.equal(exactTimestampNs("2026-10-19T11:40:34.609384375+08:00"), expected);
  assert.equal(exactTimestampNs("1792381234609384375"), expected);
  assert.notEqual(exactTimestampNs("2026-10-19T03:40:34.609384374Z"), expected);
  assert.equal(exactTimestampNs("2026-10-19T03:40:34.607Z"), 1792381234607000000n);
  assert.throws(() => exactTimestampNs(Number(expected)), /Unsafe JSON numeric nanoseconds/);
});

test("live request ledger excludes prompts and rejects requests outside the streaming budget", () => {
  const input = { max_tokens: 32, stream: true, stream_options: { include_usage: true, private: "secret" },
    messages: [{ role: "user", content: "private prompt" }], api_key: "secret" };
  assert.deepEqual(providerRequestParameters(input), {
    max_tokens: 32, stream: true, stream_options: { include_usage: true },
  });
  for (const mutation of [{ max_tokens: 33 }, { max_tokens: "32" }, { stream: false }, { stream_options: {} }]) {
    assert.throws(() => providerRequestParameters({ ...input, ...mutation }));
  }
  assert.deepEqual(providerRequestParameters({ ...input,
    chat_template_kwargs: { enable_thinking: false, private: "secret" } }), {
    max_tokens: 32, stream: true, stream_options: { include_usage: true },
    chat_template_kwargs: { enable_thinking: false },
  });
  assert.throws(() => providerRequestParameters({ ...input, chat_template_kwargs: { enable_thinking: "false" } }));
});

test("live ledger keeps every raw usage event without provider text or fabricated detail counts", () => {
  const record = { usageEvents: [], usage: null };
  const first = { prompt_tokens: 3, completion_tokens: 0, total_tokens: 3,
    prompt_tokens_details: { cached_tokens: 0 }, provider_message: "private response" };
  recordProviderUsage(record, first);
  recordProviderUsage(record, { prompt_tokens: 3, completion_tokens: 1, total_tokens: 4,
    completion_tokens_details: { reasoning_tokens: 0, private: "secret" } });
  first.prompt_tokens_details.cached_tokens = 99;
  assert.deepEqual(record.usageEvents, [
    { prompt_tokens: 3, completion_tokens: 0, total_tokens: 3, prompt_tokens_details: { cached_tokens: 0 } },
    { prompt_tokens: 3, completion_tokens: 1, total_tokens: 4, completion_tokens_details: { reasoning_tokens: 0 } },
  ]);
  assert.deepEqual(record.usage, { input: 3, output: 1, total: 4 });
});

test("live usage rejects missing invalid inconsistent or over-budget provider counts", () => {
  const record = { request: { max_tokens: 32, stream: true, stream_options: { include_usage: true } },
    usage: { input: 3, output: 32, total: 35 } };
  assert.doesNotThrow(() => assertBoundedProviderUsage(record));
  assert.doesNotThrow(() => assertBoundedProviderUsage({ ...record, usage: { input: 0, output: 0, total: 0 } }));
  for (const usage of [null, { input: 3, output: 33, total: 36 }, { input: -1, output: 2, total: 1 },
    { input: 1.5, output: 1, total: 2.5 }, { input: "3", output: 1, total: 4 },
    { output: 1, total: 4 }, { input: 3, output: 1, total: 5 },
    { input: Number.MAX_SAFE_INTEGER + 1, output: 0, total: Number.MAX_SAFE_INTEGER + 1 }]) {
    assert.throws(() => assertBoundedProviderUsage({ ...record, usage }));
  }
  assert.throws(() => assertBoundedProviderUsage({ ...record, request: { ...record.request, max_tokens: 64 } }));
});
