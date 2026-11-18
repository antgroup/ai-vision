import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import { gunzipSync } from "node:zlib";
import { attributes, decodeTraceRequest, environment, forwardToPlatform, here,
  nodeModules, receivedSpans, sdkVersion, sourceInventory } from "./support.mjs";
import { generationExpectations } from "./usage_contract.mjs";

function run(args, env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, args, { env, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.on("data", (chunk) => output += chunk);
    child.stderr.on("data", (chunk) => output += chunk);
    const timeout = setTimeout(() => child.kill("SIGKILL"), 30_000);
    child.on("error", (error) => { clearTimeout(timeout); resolve({ code: -1, output: String(error) }); });
    child.on("close", (code) => { clearTimeout(timeout); resolve({ code, output }); });
  });
}

const metadata = { numericString: "123", booleanString: "false", jsonString: '{"x":1}',
  empty: "", text: "中文🌊", integer: 123, boolean: false,
  object: { x: 1 }, array: [1, "two", false], nullable: null };

test("live provider fixture sends an optional thinking flag in the actual request body", async () => {
  const directory = await mkdtemp(join(environment, ".live-wire-"));
  const fixture = join(directory, "live.ts");
  await writeFile(fixture, await readFile(join(here, "live_provider_fixture.ts")));
  await writeFile(join(directory, "openai_streaming.ts"), await readFile(join(here, "../../examples/openai-streaming-js.ts")));
  const requests = [], batches = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    if (request.url === "/api/public/otel/v1/traces") {
      batches.push(decodeTraceRequest(body, request.headers));
      response.setHeader("content-type", "application/x-protobuf"); response.end(); return;
    }
    requests.push(JSON.parse(body));
    response.setHeader("content-type", "text/event-stream");
    const event = { id: "local-live", object: "chat.completion.chunk", created: 1, model: "local-test" };
    response.end([
      { ...event, choices: [{ index: 0, delta: { content: "OK" }, finish_reason: null }] },
      { ...event, choices: [], usage: { prompt_tokens: 3, completion_tokens: 1, total_tokens: 4 } },
    ].map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join("") + "data: [DONE]\n\n");
  });
  try {
    await new Promise((listen) => server.listen(0, "127.0.0.1", listen));
    const base = `http://127.0.0.1:${server.address().port}`;
    const env = { PATH: process.env.PATH, HOME: directory, LANGFUSE_BASE_URL: base,
      LANGFUSE_PUBLIC_KEY: "pk-local", LANGFUSE_SECRET_KEY: "sk-local", OPENAI_API_KEY: "proxy-local-only",
      OPENAI_BASE_URL: `${base}/v1`, OPENAI_MODEL: "local-test", OTEL_TRACES_EXPORTER: "none",
      JS_RUN_ID: "local-live-wire", JS_LIVE_SCENARIO: "live-stream" };
    const compiled = await run([join(nodeModules, "typescript/bin/tsc"), "--noEmit", "--skipLibCheck", "--strict",
      "--target", "es2022", "--module", "nodenext", "--moduleResolution", "nodenext", fixture], env);
    assert.equal(compiled.code, 0, compiled.output);
    for (const disabled of [false, true]) {
      const result = await run([join(nodeModules, "tsx/dist/cli.mjs"), fixture], {
        ...env, ...(disabled ? { JS_DISABLE_THINKING: "true" } : {}),
      });
      assert.equal(result.code, 0, result.output);
      const body = requests.at(-1);
      assert.equal(body.max_tokens, 32);
      assert.equal(body.stream, true);
      assert.deepEqual(body.stream_options, { include_usage: true });
      assert.deepEqual(body.chat_template_kwargs, disabled ? { enable_thinking: false } : undefined);
    }
    assert.equal(requests.length, 2);
    assert.equal(receivedSpans(batches).filter((span) => attributes(span)["langfuse.observation.type"] === "generation").length, 2);
  } finally {
    await new Promise((close) => server.close(close));
    await rm(directory, { recursive: true, force: true });
  }
});

test("real SDK typed metadata, lifecycle and transport regression", async (t) => {
  const directory = await mkdtemp(join(environment, ".lifecycle-"));
  const fixture = join(directory, "collection.ts");
  await writeFile(fixture, await readFile(join(here, "collection_fixture.ts")));
  await writeFile(join(directory, "openai_streaming.ts"), await readFile(join(here, "../../examples/openai-streaming-js.ts")));
  const batches = [], requests = [], receiverErrors = [], ledger = [];
  let attempts = 0, completedStreams = 0, closedStreams = 0;
  let checkDefaultExporter = false;
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    if (request.url === "/api/public/otel/v1/traces") {
      try {
        assert.equal(request.headers.authorization, `Basic ${Buffer.from("pk-local:sk-local").toString("base64")}`);
        requests.push(request.headers);
        if (checkDefaultExporter) {
          // This documents the SDK default, not an AI Vision-supported ingest
          // transport. Never forward this JSON request to the platform.
          assert.equal(request.headers["content-type"], "application/json");
          batches.push(JSON.parse(request.headers["content-encoding"] === "gzip" ? gunzipSync(body) : body));
        } else {
          batches.push(decodeTraceRequest(body, request.headers));
          await forwardToPlatform(body, request.headers);
        }
        response.setHeader("content-type", checkDefaultExporter ? "application/json" : "application/x-protobuf");
        response.end();
      } catch (error) {
        receiverErrors.push(error);
        response.statusCode = 400; response.end();
      }
      return;
    }
    assert.equal(request.url, "/v1/chat/completions");
    const input = JSON.parse(body);
    attempts++;
    if (input.model === "local-error" || (input.model === "local-retry" && attempts === 1)) {
      response.statusCode = input.model === "local-error" ? 401 : 429;
      response.setHeader("content-type", "application/json");
      response.setHeader("retry-after-ms", "1");
      response.end(JSON.stringify({ error: { message: "Simulated provider rejection", type: "rate_limit_error" } }));
      return;
    }
    // Independent provider ledger: cached/reasoning are subsets, not extra totals.
    const usage = { prompt_tokens: 4, completion_tokens: 5, total_tokens: 9,
      prompt_tokens_details: { cached_tokens: 1 }, completion_tokens_details: { reasoning_tokens: 2 } };
    if (input.model === "local-no-details") {
      delete usage.prompt_tokens_details; delete usage.completion_tokens_details;
    } else if (input.model === "local-zero-details") {
      usage.prompt_tokens_details.cached_tokens = 0; usage.completion_tokens_details.reasoning_tokens = 0;
    }
    if (!input.stream) {
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({ id: "local", object: "chat.completion", created: 1, model: "local-test",
        choices: [{ index: 0, message: { role: "assistant", content: "Hello world" }, finish_reason: "stop" }], usage }));
      return;
    }
    response.setHeader("content-type", "text/event-stream");
    const pieces = [
      { choices: [{ index: 0, delta: { role: "assistant", content: "Hello" }, finish_reason: null }] },
      { choices: [{ index: 0, delta: { content: " world" }, finish_reason: null }] },
      { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] },
      { choices: [], usage },
    ];
    let index = 0;
    const timer = setInterval(() => {
      if (index === pieces.length) {
        clearInterval(timer); completedStreams++;
        response.end("data: [DONE]\n\n");
      } else {
        response.write(`data: ${JSON.stringify({ id: "local", object: "chat.completion.chunk", created: 1,
          model: "local-test", ...pieces[index++] })}\n\n`);
      }
    }, 20);
    response.on("close", () => { clearInterval(timer); closedStreams++; });
  });
  try {
    await new Promise((listen) => server.listen(0, "127.0.0.1", listen));
    const baseUrl = `http://127.0.0.1:${server.address().port}`;
    const baseEnv = { PATH: process.env.PATH, HOME: directory, LANGFUSE_BASE_URL: baseUrl,
      LANGFUSE_PUBLIC_KEY: "pk-local", LANGFUSE_SECRET_KEY: "sk-local", OPENAI_API_KEY: "sk-local",
      OPENAI_BASE_URL: `${baseUrl}/v1`, OTEL_TRACES_EXPORTER: "none", LANGFUSE_TRACING_ENVIRONMENT: "local-regression" };
    const compiled = await run([join(nodeModules, "typescript/bin/tsc"), "--noEmit", "--skipLibCheck", "--strict",
      "--target", "es2022", "--module", "nodenext", "--moduleResolution", "nodenext", fixture], baseEnv);
    assert.equal(compiled.code, 0, compiled.output);
    for (const scenario of ["metadata", "concurrent", "success", "stream", "provider-error", "retry",
      "cancel-before", "cancel-after", "early-break", "identity", "safe-stream", "safe-error",
      "safe-cancel-before", "safe-cancel-after", "safe-break", "safe-consumer-error", "safe-concurrent",
      "safe-no-details", "safe-zero-details", "sdk-default-transport"]) {
      await t.test(scenario, async () => {
        batches.length = 0; requests.length = 0; receiverErrors.length = 0;
        attempts = 0; completedStreams = 0; closedStreams = 0;
        checkDefaultExporter = scenario === "sdk-default-transport";
        const result = await run([join(nodeModules, "tsx/dist/cli.mjs"), fixture], { ...baseEnv,
          JS_SCENARIO: scenario === "identity" || checkDefaultExporter ? "metadata" : scenario,
          ...(checkDefaultExporter ? { JS_DEFAULT_EXPORTER: "1" } : {}),
          ...(scenario === "identity" ? { LANGFUSE_OTEL_COMPRESSION: "none" } : {}) });
        assert.equal(result.code, 0, result.output);
        assert.deepEqual(receiverErrors, []);
        const spans = receivedSpans(batches);
        assert.ok(spans.length > 0, result.output);
        assert.ok(requests.every((headers) => scenario === "identity" || (checkDefaultExporter && sdkVersion.startsWith("5."))
          ? !headers["content-encoding"] : headers["content-encoding"] === "gzip"));
        assert.equal(new Set(spans.map((span) => `${span.traceId}:${span.spanId}`)).size, spans.length,
          "forceFlush + shutdown must not duplicate spans");
        const roots = spans.filter((span) => !span.parentSpanId);
        assert.equal(roots.length, ["concurrent", "safe-concurrent"].includes(scenario) ? 4 : 1);
        for (const root of roots) {
          const related = spans.filter((span) => span.traceId === root.traceId);
          assert.ok(related.every((span) => attributes(span)["session.id"] === `session-${root.name}`));
          assert.ok(related.every((span) => attributes(span)["user.id"] === `user-${root.name}`));
          assert.ok(BigInt(root.endTimeUnixNano) >= BigInt(root.startTimeUnixNano));
        }
        for (const resource of batches.flatMap((batch) => batch.resourceSpans)) {
          assert.equal(attributes(resource.resource)["telemetry.sdk.language"], "nodejs");
          assert.ok(resource.scopeSpans.every((scope) => scope.scope.name === "langfuse-sdk" && scope.scope.version === sdkVersion));
        }
        if (["metadata", "identity"].includes(scenario)) {
          const attrs = attributes(roots[0]);
          for (const [key, value] of Object.entries(metadata)) {
            const actual = attrs[`langfuse.observation.metadata.${key}`];
            if (value === null || (value === "" && sdkVersion.startsWith("5."))) {
              assert.equal(actual, undefined, "SDK omits null metadata (and v5 empty strings)");
            } else if (sdkVersion.startsWith("6.")) {
              assert.deepEqual(JSON.parse(actual), value, `typed metadata ${key}`);
            } else {
              assert.equal(actual, typeof value === "string" ? value : JSON.stringify(value));
            }
          }
          const ioString = spans.find((span) => span.name === 'io-"123"');
          assert.equal(attributes(ioString)["langfuse.observation.input"], "123", "IO strings stay raw across SDK majors");
          assert.ok(spans.every((span) => !attributes(span)["langfuse.observation.usage_details"]));
        }
        const generations = spans.filter((span) => attributes(span)["langfuse.observation.type"] === "generation");
        if (["success", "stream", "retry"].includes(scenario)) {
          assert.equal(generations.length, 1);
          const attrs = attributes(generations[0]);
          const usage = JSON.parse(attrs["langfuse.observation.usage_details"]);
          assert.equal(usage.input, 3); // 4 total input minus the separately represented cached token.
          assert.equal(usage.output, 3); // 5 total output minus 2 separately represented reasoning tokens.
          assert.equal(usage.input_cached_tokens, 1);
          assert.equal(usage.output_reasoning_tokens, 2);
          assert.equal(usage.total, 9);
          if (scenario === "retry") assert.equal(attempts, 2);
          if (scenario === "stream") {
            assert.equal(completedStreams, 1);
            assert.equal(attrs["langfuse.observation.output"], "Hello world");
            assert.ok(attrs["langfuse.observation.completion_start_time"]);
          }
        }
        if (["provider-error", "cancel-before"].includes(scenario)) {
          assert.equal(generations.length, 1);
          assert.equal(attributes(generations[0])["langfuse.observation.level"], "ERROR");
          assert.equal(attributes(generations[0])["langfuse.observation.usage_details"], undefined);
          assert.equal(roots[0].status.code, 2);
          assert.equal(attempts, scenario === "cancel-before" ? 0 : 1);
        }
        if (["cancel-after", "early-break"].includes(scenario)) {
          assert.equal(roots[0].status.code, 2);
          assert.equal(completedStreams, 0);
          assert.equal(closedStreams, 1);
          // Frozen SDK 5.14/6.0 automatic OpenAI wrapper does not end its
          // generation when iteration exits early. Record this measured limit;
          // never fabricate a finished generation or zero usage in the adapter.
          if (scenario === "early-break") {
            assert.equal(generations.length, 0, "Recheck the documented SDK cancellation gap if this changes");
          } else {
            assert.equal(generations.length, 1);
            assert.equal(attributes(generations[0])["langfuse.observation.usage_details"], undefined);
            assert.notEqual(attributes(generations[0])["langfuse.observation.level"], "ERROR",
              "The automatic wrapper currently loses cancellation status");
          }
        }
        if (scenario === "safe-concurrent") {
          assert.equal(generations.length, 4);
          assert.equal(completedStreams, 3);
          assert.equal(generations.filter((span) => attributes(span)["langfuse.observation.level"] === "ERROR").length, 1);
          assert.equal(generations.filter((span) => attributes(span)["langfuse.observation.usage_details"]).length, 3);
          assert.equal(roots.filter((span) => span.status.code === 2).length, 1);
        } else if (scenario.startsWith("safe-")) {
          assert.equal(generations.length, 1, "One public-API generation must end in every terminal path");
          const attrs = attributes(generations[0]);
          if (["safe-stream", "safe-no-details", "safe-zero-details"].includes(scenario)) {
            assert.equal(completedStreams, 1);
            assert.equal(attrs["langfuse.observation.output"], "Hello world");
            assert.equal(JSON.parse(attrs["langfuse.observation.usage_details"]).total, 9);
            assert.ok(attrs["langfuse.observation.completion_start_time"]);
            if (scenario === "safe-no-details") {
              assert.deepEqual(JSON.parse(attrs["langfuse.observation.usage_details"]), { input: 4, output: 5, total: 9 });
            } else if (scenario === "safe-zero-details") {
              assert.deepEqual(JSON.parse(attrs["langfuse.observation.usage_details"]), {
                input: 4, output: 5, total: 9, input_cached_tokens: 0, output_reasoning_tokens: 0 });
            }
          } else {
            assert.equal(attrs["langfuse.observation.level"], "ERROR");
            assert.equal(roots[0].status.code, 2);
            assert.equal(attrs["langfuse.observation.usage_details"], undefined);
            assert.equal(completedStreams, 0);
            if (["safe-break", "safe-cancel-after", "safe-consumer-error"].includes(scenario)) {
              assert.equal(closedStreams, 1);
              assert.equal(attrs["langfuse.observation.output"], "Hello");
              assert.ok(attrs["langfuse.observation.completion_start_time"]);
            } else {
              assert.equal(attrs["langfuse.observation.output"], undefined);
              assert.equal(attrs["langfuse.observation.completion_start_time"], undefined);
            }
          }
        }
        const usageFor = (name) => ["success", "stream", "retry", "safe-stream", "safe-no-details", "safe-zero-details"].includes(scenario)
          || (scenario === "safe-concurrent" && name !== "cancelled") ? { input: 4, output: 5, total: 9 } : null;
        const providerUsageFor = (name) => usageFor(name) === null ? null : {
          prompt_tokens: 4, completion_tokens: 5, total_tokens: 9,
          ...(scenario === "safe-no-details" ? {} : { prompt_tokens_details: { cached_tokens: scenario === "safe-zero-details" ? 0 : 1 },
            completion_tokens_details: { reasoning_tokens: scenario === "safe-zero-details" ? 0 : 2 } }),
        };
        ledger.push({ scenario, sdkVersion, traces: roots.map(({ traceId }) => traceId),
          collectorOnly: checkDefaultExporter,
          expectedByTrace: roots.map(({ traceId, name }) => ({ traceId, name, sessionId: `session-${name}`,
            userId: `user-${name}`, usage: usageFor(name),
            inventory: sourceInventory(spans.filter((span) => span.traceId === traceId)),
            expectedGenerations: generationExpectations(sourceInventory(spans.filter((span) => span.traceId === traceId)), providerUsageFor(name)),
            metadata: Object.fromEntries(Object.entries(metadata).filter(([, value]) => value !== null
              && !(sdkVersion.startsWith("5.") && value === "")).map(([key, value]) => [key,
              sdkVersion.startsWith("5.") && typeof value !== "string" ? JSON.stringify(value) : value])),
            expectedGenerationCount: scenario === "early-break" || ["metadata", "identity", "concurrent", "sdk-default-transport"].includes(scenario) ? 0 : 1,
          })),
          expectedUsage: usageFor(roots[0].name),
          provider: ["metadata", "identity", "concurrent", "sdk-default-transport"].includes(scenario) ? "none" : "local-simulated-openai",
          attempts, exportedGenerations: generations.length });
        if (process.env.AIVISION_JS_CAPTURE && scenario === "metadata") {
          await writeFile(process.env.AIVISION_JS_CAPTURE, JSON.stringify(batches, null, 2) + "\n");
        }
      });
    }
  } finally {
    if (process.env.AIVISION_JS_LEDGER) {
      await writeFile(`${process.env.AIVISION_JS_LEDGER}.lifecycle.json`, JSON.stringify(ledger, null, 2) + "\n");
    }
    await new Promise((close) => server.close(close));
    await rm(directory, { recursive: true, force: true });
  }
});
