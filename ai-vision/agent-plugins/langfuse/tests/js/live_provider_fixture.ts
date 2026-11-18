import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { CompressionAlgorithm } from "@opentelemetry/otlp-exporter-base";
import { LangfuseSpanProcessor } from "@langfuse/otel";
import { getActiveTraceId, propagateAttributes, startActiveObservation } from "@langfuse/tracing";
import OpenAI from "openai";
import type { ChatCompletionCreateParamsStreaming } from "openai/resources/chat/completions";
import { traceOpenAIStream } from "./openai_streaming.js";

const sdk = new NodeSDK({ spanProcessors: [new LangfuseSpanProcessor({
  exporter: new OTLPTraceExporter({ url: `${process.env.LANGFUSE_BASE_URL}/api/public/otel/v1/traces`,
    headers: { Authorization: `Basic ${Buffer.from("pk-local:sk-local").toString("base64")}` },
    compression: CompressionAlgorithm.GZIP }),
})] });
sdk.start();
const scenario = process.env.JS_LIVE_SCENARIO!;
try {
  await propagateAttributes({ sessionId: process.env.JS_RUN_ID!, userId: "sdk-live-regression" }, () =>
    startActiveObservation(scenario, async (root) => {
      console.log("LIVE_TRACE:", JSON.stringify({ traceId: getActiveTraceId(), scenario }));
      const messages = [{ role: "user" as const, content: "Reply with OK only." }];
      root.update({ input: messages });
      const request: ChatCompletionCreateParamsStreaming & { chat_template_kwargs?: { enable_thinking: boolean } } = {
        model: scenario === "provider-error" ? `${process.env.OPENAI_MODEL}-aivision-invalid` : process.env.OPENAI_MODEL!,
        messages, max_tokens: 32, stream: true, stream_options: { include_usage: true },
        ...(process.env.JS_DISABLE_THINKING === "true" ? { chat_template_kwargs: { enable_thinking: false } } : {}),
      };
      const output = await traceOpenAIStream(new OpenAI({ baseURL: process.env.OPENAI_BASE_URL, maxRetries: 0 }), request);
      root.update({ output });
      console.log("LIVE_RESULT:", JSON.stringify({ scenario, nonemptyOutput: output.length > 0 }));
    }));
} catch (error) {
  // Never print provider error bodies, headers, or credential-bearing clients.
  console.log("LIVE_ERROR:", JSON.stringify({ scenario, status: error instanceof OpenAI.APIError ? error.status : null }));
  if (scenario !== "provider-error") process.exitCode = 1;
} finally {
  await sdk.shutdown();
}
