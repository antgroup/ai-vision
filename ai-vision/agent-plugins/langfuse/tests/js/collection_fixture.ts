import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { CompressionAlgorithm } from "@opentelemetry/otlp-exporter-base";
import { LangfuseSpanProcessor } from "@langfuse/otel";
import { getActiveTraceId, propagateAttributes, startActiveObservation } from "@langfuse/tracing";
import { observeOpenAI } from "@langfuse/openai";
import OpenAI from "openai";
import { traceOpenAIStream } from "./openai_streaming.js";

const processor = process.env.JS_DEFAULT_EXPORTER === "1" ? new LangfuseSpanProcessor() : new LangfuseSpanProcessor({
  exporter: new OTLPTraceExporter({
    url: `${process.env.LANGFUSE_BASE_URL}/api/public/otel/v1/traces`,
    headers: { Authorization: `Basic ${Buffer.from(`${process.env.LANGFUSE_PUBLIC_KEY}:${process.env.LANGFUSE_SECRET_KEY}`).toString("base64")}` },
    compression: process.env.LANGFUSE_OTEL_COMPRESSION === "none" ? CompressionAlgorithm.NONE : CompressionAlgorithm.GZIP,
  }),
});
const sdk = new NodeSDK({ spanProcessors: [processor] });
sdk.start();
const client = observeOpenAI(new OpenAI({ baseURL: process.env.OPENAI_BASE_URL, maxRetries: 1 }));
const scenario = process.env.JS_SCENARIO ?? "metadata";
const metadata = {
  numericString: "123", booleanString: "false", jsonString: '{"x":1}',
  empty: "", text: "中文🌊", integer: 123, boolean: false,
  object: { x: 1 }, array: [1, "two", false], nullable: null,
};

async function request(name: string) {
  return propagateAttributes({ sessionId: `session-${name}`, userId: `user-${name}`, tags: [`case:${name}`] }, () =>
    startActiveObservation(name, async (root) => {
      console.log("TRACE:", JSON.stringify({ name, traceId: getActiveTraceId() }));
      root.update({ input: { case: name }, metadata });
      if (scenario === "metadata") {
        for (const input of ["123", "false", '{"x":1}', "", "中文🌊", 123, false, [1, "two"], { x: 1 }, null]) {
          await startActiveObservation(`io-${JSON.stringify(input)}`, async (tool) => {
            tool.update({ input, output: input, metadata });
          }, { asType: "tool" });
        }
        root.update({ output: "done" });
        return;
      }
      if (scenario === "concurrent") {
        await startActiveObservation(`tool-${name}`, async (tool) => {
          await new Promise((done) => setImmediate(done));
          tool.update({ input: name, output: name, metadata: { request: name } });
        }, { asType: "tool" });
        root.update({ output: name });
        return;
      }
      const controller = new AbortController();
      if (scenario.startsWith("safe-")) {
        if (scenario === "safe-cancel-before") controller.abort();
        const output = await traceOpenAIStream(new OpenAI({ baseURL: process.env.OPENAI_BASE_URL, maxRetries: 0 }), {
          model: scenario === "safe-error" ? "local-error" : scenario === "safe-no-details"
            ? "local-no-details" : scenario === "safe-zero-details" ? "local-zero-details" : "local-test",
          messages: [{ role: "user", content: name }], stream: true, stream_options: { include_usage: true },
        }, { signal: controller.signal, onChunk: (chunk) => {
          if ((scenario === "safe-cancel-after" || (scenario === "safe-concurrent" && name === "cancelled")) && chunk.choices[0]?.delta.content) controller.abort();
          if (scenario === "safe-break" && chunk.choices[0]?.delta.content) return false;
          if (scenario === "safe-consumer-error") throw new Error("Consumer failed");
        } });
        root.update({ output });
        return;
      }
      if (scenario === "cancel-before") controller.abort();
      const model = scenario === "provider-error" ? "local-error" : scenario === "retry" ? "local-retry" : "local-test";
      if (["success", "provider-error", "retry"].includes(scenario)) {
        const result = await client.chat.completions.create({ model, messages: [{ role: "user", content: name }] });
        root.update({ output: result.choices[0]?.message.content });
      } else {
        const stream = await client.chat.completions.create({
          model, messages: [{ role: "user", content: name }], stream: true,
          stream_options: { include_usage: true },
        }, { signal: controller.signal });
        let output = "";
        for await (const chunk of stream) {
          output += chunk.choices[0]?.delta.content ?? "";
          root.update({ output });
          if (scenario === "cancel-after") controller.abort();
          if (scenario === "early-break") break;
        }
        controller.signal.throwIfAborted();
        if (scenario === "early-break") {
          // Business cancellation is explicit and retained on the root. The
          // SDK's automatic generation cleanup is checked independently.
          throw new Error("Business consumer stopped the stream early");
        }
      }
    }, { asType: "agent" }));
}

try {
  if (scenario === "safe-concurrent") {
    await Promise.allSettled(["ok-a", "cancelled", "ok-b"].map((name) => request(name)));
    await processor.forceFlush();
    await request("after-flush");
  } else if (scenario === "concurrent") {
    await Promise.all(["a", "b", "c"].map((name) => request(name)));
    await processor.forceFlush();
    // A flush must not close shared instrumentation for subsequent requests.
    await request("after-flush");
  } else {
    await request(scenario);
  }
} catch (error) {
  console.log("EXPECTED_ERROR:", error instanceof Error ? error.name : "unknown");
} finally {
  await processor.forceFlush();
  await sdk.shutdown();
}
