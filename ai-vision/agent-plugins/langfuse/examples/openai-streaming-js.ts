// Install the dependencies listed in openai-js.yaml. This example uses the raw
// OpenAI client: do not also wrap the same request with observeOpenAI.
import "dotenv/config";
import OpenAI from "openai";
import type { ChatCompletionChunk, ChatCompletionCreateParamsStreaming } from "openai/resources/chat/completions";
import { startActiveObservation, getActiveTraceId, propagateAttributes } from "@langfuse/tracing";
import { LangfuseSpanProcessor } from "@langfuse/otel";
import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { CompressionAlgorithm } from "@opentelemetry/otlp-exporter-base";
import { pathToFileURL } from "node:url";

/** Consume one text Chat Completions stream inside its generation lifetime.
 * Return false from onChunk to stop consuming. Partial output and first text
 * time are measured by this application wrapper. Usage is set only when the
 * provider sends its final usage chunk; cancellation never creates zero usage.
 */
export async function traceOpenAIStream(
  client: OpenAI,
  request: ChatCompletionCreateParamsStreaming,
  options: { signal?: AbortSignal; onChunk?: (chunk: ChatCompletionChunk) => boolean | void | Promise<boolean | void> } = {},
) {
  const cancellation = new AbortController();
  const signal = options.signal ? AbortSignal.any([options.signal, cancellation.signal]) : cancellation.signal;
  return startActiveObservation("openai-text-stream", async (generation) => {
    let output = "";
    let firstText = false;
    generation.update({ model: request.model, input: request.messages,
      metadata: { outputSource: "application-stream-consumer", firstTokenSource: "first-text-chunk",
        usageSource: "provider-final-usage-chunk" } });
    try {
      signal.throwIfAborted();
      const stream = await client.chat.completions.create(request, { signal });
      for await (const chunk of stream) {
        const text = chunk.choices[0]?.delta.content;
        if (text) {
          if (!firstText) {
            generation.update({ completionStartTime: new Date() });
            firstText = true;
          }
          output += text;
          generation.update({ output });
        }
        if (chunk.usage) {
          // OpenAI's detail counts are subsets of prompt/completion totals.
          const cached = chunk.usage.prompt_tokens_details?.cached_tokens;
          const reasoning = chunk.usage.completion_tokens_details?.reasoning_tokens;
          generation.update({ usageDetails: {
            input: chunk.usage.prompt_tokens - (cached ?? 0),
            output: chunk.usage.completion_tokens - (reasoning ?? 0),
            ...(cached == null ? {} : { input_cached_tokens: cached }),
            ...(reasoning == null ? {} : { output_reasoning_tokens: reasoning }),
            total: chunk.usage.total_tokens,
          } });
        }
        if (await options.onChunk?.(chunk) === false) {
          cancellation.abort(new Error("Stream consumer stopped early"));
          signal.throwIfAborted();
        }
        signal.throwIfAborted();
      }
      // OpenAI may finish iteration without throwing after AbortController.abort.
      signal.throwIfAborted();
      return output;
    } catch (error) {
      generation.update({ level: "ERROR", statusMessage: error instanceof Error ? error.message : String(error) });
      throw error;
    } finally {
      // Request-local cleanup only. startActiveObservation ends this generation;
      // the application owns the shared processor and flushes after all requests.
      cancellation.abort();
    }
  }, { asType: "generation" });
}

async function main() {
  const exporter = new OTLPTraceExporter({
    url: `${process.env.LANGFUSE_BASE_URL?.replace(/\/$/, "")}/api/public/otel/v1/traces`,
    headers: { Authorization: `Basic ${Buffer.from(`${process.env.LANGFUSE_PUBLIC_KEY}:${process.env.LANGFUSE_SECRET_KEY}`).toString("base64")}` },
    compression: CompressionAlgorithm.GZIP,
  });
  const sdk = new NodeSDK({ spanProcessors: [new LangfuseSpanProcessor({ exporter })] });
  sdk.start();
  try {
    await propagateAttributes({ sessionId: "replace-with-your-stable-session-id" }, () =>
      startActiveObservation("openai-stream-request", async (root) => {
        console.log("AI Vision trace_id:", getActiveTraceId());
        const messages = [{ role: "user" as const, content: "Say hello briefly" }];
        root.update({ input: messages });
        const output = await traceOpenAIStream(new OpenAI({ baseURL: process.env.OPENAI_BASE_URL }), {
          model: process.env.OPENAI_MODEL ?? "gpt-4o", messages, stream: true, stream_options: { include_usage: true },
        }, { onChunk: (chunk) => { process.stdout.write(chunk.choices[0]?.delta.content ?? ""); } });
        root.update({ output });
      }));
  } finally {
    await sdk.shutdown();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
