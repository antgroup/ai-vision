import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";

export const here = dirname(fileURLToPath(import.meta.url));
export const environment = process.env.LANGFUSE_JS_ENV
  ? resolve(process.env.LANGFUSE_JS_ENV) : here;
export const requireDependency = createRequire(join(environment, "package.json"));
export const nodeModules = join(environment, "node_modules");
export const sdkVersion = JSON.parse(readFileSync(join(nodeModules, "@langfuse/tracing/package.json"), "utf8")).version;
export const { parse } = requireDependency("yaml");

const protobuf = requireDependency("protobufjs");
const protoRoot = resolve(here, "../../../../proto");
const root = new protobuf.Root();
root.resolvePath = (_, target) => target.startsWith("opentelemetry/")
  ? join(protoRoot, target) : target;
await root.load(join(protoRoot, "opentelemetry/proto/trace/v1/trace.proto"));
// TracesData and ExportTraceServiceRequest have the same resource_spans wire field.
const traceMessage = root.lookupType("opentelemetry.proto.trace.v1.TracesData");

export function decodeTraceRequest(body, headers) {
  assert.equal(headers["content-type"], "application/x-protobuf");
  const data = headers["content-encoding"] === "gzip" ? gunzipSync(body) : body;
  const result = traceMessage.toObject(traceMessage.decode(data), { longs: String, bytes: Array });
  for (const resource of result.resourceSpans ?? []) {
    for (const scope of resource.scopeSpans ?? []) {
      for (const span of scope.spans ?? []) {
        for (const key of ["traceId", "spanId", "parentSpanId"]) {
          if (span[key]) span[key] = Buffer.from(span[key]).toString("hex");
        }
      }
    }
  }
  return result;
}

export function attributes(span) {
  function value(item) {
    if ("stringValue" in item) return item.stringValue;
    if ("intValue" in item) return Number(item.intValue);
    if ("doubleValue" in item) return item.doubleValue;
    if ("boolValue" in item) return item.boolValue;
    if (item.arrayValue) return (item.arrayValue.values ?? []).map(value);
    return undefined;
  }
  return Object.fromEntries((span.attributes ?? []).map(({ key, value: item }) => [key, value(item)]));
}

export function receivedSpans(batches) {
  return batches.flatMap((batch) => (batch.resourceSpans ?? []).flatMap((resource) =>
    (resource.scopeSpans ?? []).flatMap((scope) => scope.spans ?? [])));
}

export function sourceInventory(spans) {
  return spans.map((span) => ({ id: span.spanId, parentId: span.parentSpanId ?? null,
    name: span.name, type: attributes(span)["langfuse.observation.type"],
    status: span.status?.code ?? 0, level: attributes(span)["langfuse.observation.level"],
    startTimeUnixNano: span.startTimeUnixNano, endTimeUnixNano: span.endTimeUnixNano,
    input: attributes(span)["langfuse.observation.input"], output: attributes(span)["langfuse.observation.output"],
    usageAttributes: Object.fromEntries(Object.entries(attributes(span)).filter(([key]) =>
      key === "langfuse.observation.usage_details" || key.startsWith("gen_ai.usage.") || key.startsWith("llm.token_count."))),
  }));
}

// Explicit opt-in forwarding is for isolated platform acceptance. Never inherit
// ambient production LANGFUSE_* credentials or print the target credentials.
export async function forwardToPlatform(body, headers) {
  if (!process.env.AIVISION_OTLP_BASE_URL) return;
  assert.ok(process.env.AIVISION_AGENT_UUID && process.env.AIVISION_SPACE_KEY,
    "Platform forwarding requires dedicated Agent UUID and Space Key");
  const response = await fetch(`${process.env.AIVISION_OTLP_BASE_URL.replace(/\/$/, "")}/api/public/otel/v1/traces`, {
    method: "POST",
    headers: {
      "content-type": "application/x-protobuf",
      ...(headers["content-encoding"] ? { "content-encoding": headers["content-encoding"] } : {}),
      authorization: `Basic ${Buffer.from(`${process.env.AIVISION_AGENT_UUID}:${process.env.AIVISION_SPACE_KEY}`).toString("base64")}`,
    },
    body,
    signal: AbortSignal.timeout(30_000),
  });
  assert.ok(response.ok, `Isolated platform rejected OTLP: HTTP ${response.status}`);
}
