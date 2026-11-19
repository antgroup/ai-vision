// Bounded opt-in: two requests, one <=32-token stream and one invalid-model
// request. The reference usage comes from raw provider SSE, not SDK attributes.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs, parseEnv } from "node:util";
import { randomUUID } from "node:crypto";
import { loadTarget, verifyPlatform } from "./verify_platform.mjs";
import { generationExpectations } from "./usage_contract.mjs";
import { assertBoundedProviderUsage, createProviderBudget, forwardProviderRequest,
  finalizeLiveRun } from "./live_provider_ledger.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const { values } = parseArgs({ options: { targets: { type: "string" }, environment: { type: "string" }, out: { type: "string" },
  "provider-env": { type: "string" }, "key-name": { type: "string" }, "provider-key-env": { type: "string" },
  "base-url": { type: "string" }, model: { type: "string" },
  "disable-thinking": { type: "boolean" },
  help: { type: "boolean", short: "h" } } });
if (values.help) {
  console.log(`Usage: node run_live_provider.mjs --targets FILE --environment DIRECTORY --out DIRECTORY
  --provider-env FILE --key-name ENV_NAME --base-url HTTPS_URL --model MODEL

Runs one real provider stream (at most 32 output tokens) and one invalid-model request.
Uses the named credential from --provider-env only for the selected provider.
Alternatively, --provider-key-env ENV_NAME reads it from the child environment.
Set AIVISION_E2E_TARGETS_JSON instead of --targets to read an in-memory target.
Use a dedicated platform target and either tests/js or tests/js6 as the environment.
--disable-thinking  Send chat_template_kwargs.enable_thinking=false (default: omitted).
--help, -h  Show this help without reading credentials or contacting any service.`);
  process.exit(0);
}
for (const key of ["environment", "out", "base-url", "model"]) assert.ok(values[key], `Missing --${key}`);
const providerKeyEnv = values["provider-key-env"];
const providerFile = values["provider-env"];
const providerFileKey = values["key-name"];
assert.ok(providerKeyEnv ? !providerFile && !providerFileKey : providerFile && providerFileKey,
  "Pass either --provider-key-env or --provider-env with --key-name");
process.env.LANGFUSE_JS_ENV = resolve(values.environment);
const { attributes, decodeTraceRequest, nodeModules, receivedSpans, sdkVersion, sourceInventory } = await import("./support.mjs");
const providerKey = providerKeyEnv ? process.env[providerKeyEnv]
  : parseEnv(await readFile(providerFile, "utf8"))[providerFileKey];
if (providerKeyEnv) delete process.env[providerKeyEnv];
assert.ok(providerKey, "Selected provider key is absent");
const providerOrigin = new URL(values["base-url"]);
assert.equal(providerOrigin.protocol, "https:", "Real provider requires HTTPS");
const target = await loadTarget(values.targets);
delete process.env.AIVISION_E2E_TARGETS_JSON;
const output = resolve(values.out);
await mkdir(output, { recursive: true, mode: 0o700 });
const directory = await mkdtemp(join(resolve(values.environment), ".live-provider-"));
await writeFile(join(directory, "live.ts"), await readFile(join(here, "live_provider_fixture.ts")));
await writeFile(join(directory, "openai_streaming.ts"), await readFile(join(here, "../../examples/openai-streaming-js.ts")));
let batches = [], providerRecords = [], receiverErrors = [];
const ledger = [];
const attempts = [], budget = createProviderBudget(["live-stream", "provider-error"]);
let currentScenario;
const runStatus = { passed: false, physicalRequests: 0 };
const server = createServer(async (request, response) => {
  try {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    if (request.url === "/api/public/otel/v1/traces") {
      batches.push(decodeTraceRequest(body, request.headers));
      const upstream = await fetch(`${(target.endpoint ?? target.base_url).replace(/\/$/, "")}/api/public/otel/v1/traces`, {
        method: "POST", headers: { "content-type": "application/x-protobuf", "content-encoding": "gzip",
          authorization: `Basic ${Buffer.from(`${target.agent_uuid}:${target.space_key}`).toString("base64")}` },
        body, signal: AbortSignal.timeout(30_000),
      });
      assert.ok(upstream.ok, `Platform ingestion HTTP ${upstream.status}`);
      response.setHeader("content-type", "application/x-protobuf"); response.end(); return;
    }
    assert.equal(request.url, "/v1/chat/completions");
    const input = JSON.parse(body);
    await forwardProviderRequest({ input, body, response, scenario: currentScenario, budget,
      attempts, providerRecords, baseUrl: values["base-url"], providerKey });
  } catch (error) {
    receiverErrors.push(error instanceof Error ? error.message.replaceAll(providerKey, "<redacted>") : "Receiver failure");
    response.statusCode = 502; response.end();
  } finally {
    runStatus.physicalRequests = budget.count;
  }
});

function run(args, env) {
  return new Promise((done) => {
    const child = spawn(process.execPath, args, { env, cwd: directory, stdio: ["ignore", "pipe", "pipe"] });
    let text = "";
    child.stdout.on("data", (chunk) => text += chunk);
    child.stderr.on("data", (chunk) => text += chunk);
    const timeout = setTimeout(() => child.kill("SIGKILL"), 90_000);
    child.on("error", () => { clearTimeout(timeout); done({ code: 1, text: "Child spawn failed" }); });
    child.on("close", (code) => { clearTimeout(timeout); done({ code, text }); });
  });
}

try {
  await new Promise((listen) => server.listen(0, "127.0.0.1", listen));
  const base = `http://127.0.0.1:${server.address().port}`;
  const runId = `sdk-live-${sdkVersion}-${randomUUID()}`;
  const env = { PATH: process.env.PATH, HOME: directory, LANGFUSE_BASE_URL: base, LANGFUSE_PUBLIC_KEY: "pk-local", LANGFUSE_SECRET_KEY: "sk-local",
    OPENAI_BASE_URL: `${base}/v1`, OPENAI_API_KEY: "proxy-local-only", OPENAI_MODEL: values.model, OTEL_TRACES_EXPORTER: "none", JS_RUN_ID: runId,
    ...(values["disable-thinking"] ? { JS_DISABLE_THINKING: "true" } : {}) };
  const compile = await run([join(nodeModules, "typescript/bin/tsc"), "--noEmit", "--skipLibCheck", "--strict", "--target", "es2022",
    "--module", "nodenext", "--moduleResolution", "nodenext", join(directory, "live.ts")], env);
  assert.equal(compile.code, 0, "Live fixture TypeScript compile failed");
  for (const scenario of ["live-stream", "provider-error"]) {
    batches = []; providerRecords = []; receiverErrors = [];
    currentScenario = scenario;
    const result = await run([join(nodeModules, "tsx/dist/cli.mjs"), join(directory, "live.ts")], { ...env, JS_LIVE_SCENARIO: scenario });
    assert.equal(result.code, 0, `Live SDK child failed (${scenario}); response bodies omitted`);
    assert.deepEqual(receiverErrors, []);
    assert.equal(providerRecords.length, 1, "No hidden retries in the bounded provider proof");
    const reference = providerRecords[0];
    const match = result.text.match(/LIVE_TRACE: (.+)/);
    assert.ok(match, "Live fixture omitted trace ID");
    const { traceId } = JSON.parse(match[1]);
    const spans = receivedSpans(batches);
    const generations = spans.filter((span) => attributes(span)["langfuse.observation.type"] === "generation");
    assert.equal(generations.length, 1);
    if (scenario === "live-stream") {
      assert.equal(reference.httpStatus, 200);
      assert.ok(reference.textChunks > 0 && Number.isInteger(reference.usage?.total));
      assertBoundedProviderUsage(reference);
      assert.ok(attributes(generations[0])["langfuse.observation.completion_start_time"]);
    } else {
      assert.ok([400, 404, 422].includes(reference.httpStatus), "Expected actual provider invalid-model rejection");
      assert.equal(reference.usage, null);
      assert.equal(attributes(generations[0])["langfuse.observation.level"], "ERROR");
    }
    const inventory = sourceInventory(spans);
    ledger.push({ scenario, sdkVersion, provider: providerOrigin.host, providerReference: reference, traceId,
      expectedUsage: reference.usage, inventory,
      expectedGenerations: generationExpectations(inventory, reference.usageEvents.at(-1) ?? null),
      sessionId: runId, userId: "sdk-live-regression", expectedGenerationCount: 1 });
    console.log(JSON.stringify({ scenario, sdkVersion, providerStatus: reference.httpStatus, traceId, providerUsage: reference.usage }));
  }
  await writeFile(join(output, "live-ledger.json"), JSON.stringify(ledger, null, 2) + "\n");
  const report = await verifyPlatform(target, [ledger], join(output, "live-readback.json"));
  console.log(JSON.stringify({ passed: report.passed, traces: report.traces, checks: report.checks, failures: report.failures }));
  runStatus.passed = report.passed;
  process.exitCode = report.passed ? 0 : 1;
} catch (error) {
  runStatus.errorType = error?.constructor?.name ?? "RunnerError";
  console.error(JSON.stringify({ passed: false, errorType: runStatus.errorType,
    scenario: currentScenario, physicalRequests: budget.count }));
  process.exitCode = 1;
} finally {
  const passed = await finalizeLiveRun(output, { ledger, attempts, status: runStatus }, {
    closeServer: () => new Promise((resolveClose, reject) => server.close((error) => error ? reject(error) : resolveClose())),
    removeTemporary: () => rm(directory, { recursive: true, force: true }),
  });
  if (!passed) {
    process.exitCode = 1;
    console.error(JSON.stringify({ passed: false, errors: runStatus.errors ?? [], physicalRequests: budget.count }));
  }
}
