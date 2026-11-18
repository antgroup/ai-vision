import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import test from "node:test";
import { attributes, decodeTraceRequest, environment, forwardToPlatform, here,
  nodeModules, parse, receivedSpans as allSpans, sdkVersion, sourceInventory } from "./support.mjs";
import { generationExpectations } from "./usage_contract.mjs";

const examples =
  process.env.LANGFUSE_EXAMPLES_DIR ?? resolve(here, "../../examples");
const ids = ["langchain-js", "langgraph-js", "openai-js", "manual-js"];

function run(command, args, options = {}) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(command, args, {
      ...options,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.on("data", (data) => {
      output += data;
    });
    child.stderr.on("data", (data) => {
      output += data;
    });
    const timer = setTimeout(() => child.kill("SIGKILL"), 30_000);
    child.on("error", reject);
    child.on("close", (code) => {
      clearTimeout(timer);
      resolveRun({ code, output });
    });
  });
}

const existingSetup = {
  "langchain-js": `
import { ChatPromptTemplate } from "@langchain/core/prompts";
import { ChatOpenAI } from "@langchain/openai";
const model = new ChatOpenAI({ model: "local-test", configuration: { baseURL: process.env.OPENAI_BASE_URL } });
const existing_chain = ChatPromptTemplate.fromTemplate("Answer: {question}").pipe(model);
const existing_input = { question: "Hello" };
const existing_config = { callbacks: [{ handleChainStart() { console.log("EXISTING_CALLBACK"); } }] };
`,
  "langgraph-js": `
import { ChatOpenAI } from "@langchain/openai";
import { StateGraph, MessagesAnnotation, MemorySaver, START, END } from "@langchain/langgraph";
import type { RunnableConfig } from "@langchain/core/runnables";
const model = new ChatOpenAI({ model: "local-test", configuration: { baseURL: process.env.OPENAI_BASE_URL } });
const existing_graph = new StateGraph(MessagesAnnotation)
  .addNode("answer", async (state: typeof MessagesAnnotation.State, config: RunnableConfig) => ({ messages: [await model.invoke(state.messages, config)] }))
  .addEdge(START, "answer").addEdge("answer", END).compile({ checkpointer: new MemorySaver() });
const existing_input = { messages: [{ role: "user", content: "Hello" }] };
const existing_config = { configurable: { thread_id: "existing-thread" }, callbacks: [{ handleChainStart() { console.log("EXISTING_CALLBACK"); } }] };
`,
  "openai-js": `
import OpenAI from "openai";
const existing_openai = new OpenAI({ baseURL: process.env.OPENAI_BASE_URL });
const existing_input = { model: "local-test", messages: [{ role: "user" as const, content: "Hello" }] };
`,
  "manual-js": `
const existing_input = { text: "Hello" };
const existing_workflow = async (input: typeof existing_input) => startActiveObservation("existing-tool", async (step) => {
  const output = input.text.toLowerCase();
  step.update({ input, output });
  return output;
}, { asType: "tool" });
`,
};

function existingProgram(guide) {
  const marker = "// Add the following to your async request handler.\n";
  const [startup, request] = guide.existingExample.split(marker);
  assert.ok(
    startup && request,
    "Existing snippet must separate service startup from request handling",
  );
  return `${startup}\n${existingSetup[guide.id]}\nconst session_id = "existing-session";
async function main() {
  try {
${request}
    console.log("BUSINESS_RESULT:", JSON.stringify(result));
  } finally { await sdk.shutdown(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });`;
}

test("published Node examples typecheck and export connected observations", async (t) => {
  const directory = await mkdtemp(join(environment, ".onboarding-smoke-"));
  const batches = [];
  const requests = [];
  const ledger = [];
  const receiverErrors = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    response.setHeader("content-type", "application/json");
    if (request.url === "/api/public/otel/v1/traces") {
      requests.push(request.headers);
      try {
      assert.equal(
        request.headers.authorization,
        `Basic ${Buffer.from("pk-local:sk-local").toString("base64")}`,
      );
      batches.push(decodeTraceRequest(body, request.headers));
      await forwardToPlatform(body, request.headers);
      response.setHeader("content-type", "application/x-protobuf");
      response.end();
      } catch (error) {
        receiverErrors.push(error);
        response.statusCode = 400;
        response.end();
      }
    } else if (request.url === "/v1/chat/completions") {
      if (JSON.parse(body).model === "local-error") {
        response.statusCode = 401;
        response.end(
          JSON.stringify({
            error: {
              message: "Simulated provider rejection",
              type: "authentication_error",
            },
          }),
        );
        return;
      }
      response.end(
        JSON.stringify({
          id: "chatcmpl-local",
          object: "chat.completion",
          created: 1,
          model: "local-test",
          choices: [
            {
              index: 0,
              message: {
                role: "assistant",
                content: "A locally simulated response.",
              },
              finish_reason: "stop",
            },
          ],
          usage: { prompt_tokens: 4, completion_tokens: 5, total_tokens: 9 },
        }),
      );
    } else {
      response.statusCode = 404;
      response.end(
        JSON.stringify({ error: `Unexpected path: ${request.url}` }),
      );
    }
  });
  try {
    await new Promise((listen) => server.listen(0, "127.0.0.1", listen));
    const baseUrl = `http://127.0.0.1:${server.address().port}`;
    const guides = await Promise.all(
      ids.map(async (id) =>
        parse(await readFile(join(examples, `${id}.yaml`), "utf8")),
      ),
    );
    const files = [];
    for (const guide of guides) {
      for (const variant of ["full", "existing"]) {
        const filename = join(directory, `${guide.id}-${variant}.ts`);
        await writeFile(
          filename,
          variant === "full" ? guide.npmExample : existingProgram(guide),
        );
        files.push(filename);
      }
    }
    const compile = await run(process.execPath, [
      join(nodeModules, "typescript/bin/tsc"),
      "--noEmit",
      "--skipLibCheck",
      "--strict",
      "--target",
      "es2022",
      "--module",
      "nodenext",
      "--moduleResolution",
      "nodenext",
      ...files,
    ]);
    assert.equal(compile.code, 0, compile.output);
    // Explicit local credentials and URLs: never inherit real exporter/provider settings.
    const env = {
      PATH: process.env.PATH,
      HOME: directory,
      LANGFUSE_BASE_URL: baseUrl,
      LANGFUSE_PUBLIC_KEY: "pk-local",
      LANGFUSE_SECRET_KEY: "sk-local",
      OPENAI_BASE_URL: `${baseUrl}/v1`,
      OPENAI_API_KEY: "sk-local",
      OPENAI_MODEL: "local-test",
      OTEL_SDK_DISABLED: "false",
      OTEL_TRACES_EXPORTER: "none",
      LANGFUSE_TRACING_ENVIRONMENT: "local-smoke",
    };
    const receivedSpans = () => allSpans(batches);
    for (const file of files) {
      await t.test(file.split("/").at(-1), async () => {
        batches.length = 0;
        requests.length = 0;
        const result = await run(
          process.execPath,
          [join(nodeModules, "tsx/dist/cli.mjs"), file],
          {
            cwd: directory,
            env,
          },
        );
        assert.equal(result.code, 0, result.output);
        assert.deepEqual(receiverErrors, []);
        assert.ok(requests.every((headers) => headers["content-type"] === "application/x-protobuf"),
          "AI Vision accepts protobuf OTLP; JSON-only collector proof is insufficient");
        const match = result.output.match(/AI Vision trace_id: ([a-f0-9]{32})/);
        assert.ok(match, result.output);
        const inventory = sourceInventory(receivedSpans());
        ledger.push({ sdkVersion, variant: file.split("/").at(-1), traceId: match[1],
          expectedUsage: file.includes("manual-js") ? null : { input: 4, output: 5, total: 9 },
          inventory, expectedGenerations: generationExpectations(inventory, file.includes("manual-js") ? null : {
            prompt_tokens: 4, completion_tokens: 5, total_tokens: 9 }),
          provider: file.includes("manual-js") ? "none" : "local-simulated-openai" });
        const spans = receivedSpans();
        assert.ok(
          spans.length >= 2,
          "Root and instrumented child observations must reach the HTTP receiver",
        );
        assert.ok(
          spans.every((span) => span.traceId === match[1]),
          "Every child must share the printed trace ID",
        );
        const roots = spans.filter((span) => !span.parentSpanId);
        assert.equal(
          roots.length,
          1,
          "Each invocation must produce one connected trace",
        );
        const ids = new Set(spans.map((span) => span.spanId));
        assert.ok(
          spans.every(
            (span) => !span.parentSpanId || ids.has(span.parentSpanId),
          ),
          "No child may reference a missing parent",
        );
        const rootAttributes = attributes(roots[0]);
        assert.ok(
          rootAttributes["session.id"],
          "Root must receive session context",
        );
        assert.ok(
          spans.every(
            (span) =>
              attributes(span)["session.id"] === rootAttributes["session.id"],
          ),
          "Session context must propagate to every child",
        );
        if (!file.includes("manual-js")) {
          const generation = spans.find(
            (span) =>
              attributes(span)["langfuse.observation.type"] === "generation",
          );
          assert.ok(
            generation,
            "Actual SDK integration must emit a generation",
          );
          assert.ok(
            attributes(generation)["langfuse.observation.usage_details"],
            "Provider usage must be captured",
          );
          const usage = JSON.parse(attributes(generation)["langfuse.observation.usage_details"]);
          assert.equal(usage.input, 4);
          assert.equal(usage.output, 5);
          assert.equal(usage.total ?? usage.input + usage.output, 9);
        } else {
          assert.ok(spans.every((span) => attributes(span)["langfuse.observation.type"] !== "generation"));
        }
        if (file.includes("existing") && /langchain|langgraph/.test(file)) {
          assert.match(
            result.output,
            /EXISTING_CALLBACK/,
            "Existing callbacks must survive integration",
          );
        }
        if (file.includes("existing")) {
          const businessLine = result.output
            .split("\n")
            .find((line) => line.startsWith("BUSINESS_RESULT: "));
          assert.ok(
            businessLine,
            "The wrapped request must return its business result",
          );
          const businessResult = JSON.parse(
            businessLine.slice("BUSINESS_RESULT: ".length),
          );
          if (file.includes("manual-js")) {
            assert.equal(businessResult, "hello");
          } else if (file.includes("openai-js")) {
            assert.equal(
              businessResult.choices[0].message.content,
              "A locally simulated response.",
            );
          } else if (file.includes("langgraph-js")) {
            // BaseMessage serializes through LangChain's constructor representation.
            assert.equal(
              businessResult.messages.at(-1).kwargs.content,
              "A locally simulated response.",
            );
          } else {
            assert.equal(
              businessResult.kwargs.content,
              "A locally simulated response.",
            );
          }
        }
      });
    }
    for (const file of files.filter(
      (file) => file.endsWith("-full.ts") && !file.includes("manual-js"),
    )) {
      await t.test(
        `${file.split("/").at(-1)} exports on provider error`,
        async () => {
          batches.length = 0;
          const result = await run(
            process.execPath,
            [join(nodeModules, "tsx/dist/cli.mjs"), file],
            { cwd: directory, env: { ...env, OPENAI_MODEL: "local-error" } },
          );
          assert.equal(result.code, 1, result.output);
          const spans = receivedSpans();
          const root = spans.find((span) => !span.parentSpanId);
          assert.ok(
            root,
            "finally must export the ended root even when the provider fails",
          );
          assert.equal(
            root.status.code,
            2,
            "The root must retain the provider failure",
          );
          const generation = spans.find(
            (span) =>
              attributes(span)["langfuse.observation.type"] === "generation",
          );
          assert.ok(
            generation,
            "The failed model observation must be exported",
          );
          assert.equal(
            attributes(generation)["langfuse.observation.level"],
            "ERROR",
          );
        },
      );
    }
  } finally {
    if (process.env.AIVISION_JS_LEDGER) {
      await writeFile(process.env.AIVISION_JS_LEDGER, JSON.stringify(ledger, null, 2) + "\n");
    }
    await new Promise((close) => server.close(close));
    await rm(directory, { recursive: true, force: true });
  }
});
