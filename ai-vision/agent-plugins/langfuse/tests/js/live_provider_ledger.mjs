import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";

export function createProviderBudget(scenarios) {
  const allowed = new Set(scenarios), attempted = new Set();
  return {
    claim(scenario) {
      assert.ok(allowed.has(scenario) && !attempted.has(scenario), "Live provider request budget exhausted or unknown case");
      attempted.add(scenario);
    },
    get count() { return attempted.size; },
  };
}

export async function writeLiveEvidence(directory, { ledger, attempts, status }) {
  const failures = [];
  for (const [name, value] of [["live-ledger.json", ledger], ["provider-attempts.json", attempts], ["run-status.json", status]]) {
    try {
      await writeFile(join(directory, name), JSON.stringify(value, null, 2) + "\n");
    } catch (error) {
      status.passed = false;
      const failure = { stage: `write.${name}`, type: error?.constructor?.name ?? "WriteError" };
      (status.errors ??= []).push(failure);
      failures.push(failure);
    }
  }
  if (failures.length) throw new AggregateError(failures, "Live evidence write failed");
}

export async function finalizeLiveRun(directory, evidence, { closeServer, removeTemporary }) {
  for (const [stage, cleanup] of [["server.close", closeServer], ["runtime.remove", removeTemporary]]) {
    try { await cleanup(); }
    catch (error) {
      evidence.status.passed = false;
      (evidence.status.errors ??= []).push({ stage, type: error?.constructor?.name ?? "CleanupError" });
    }
  }
  try { await writeLiveEvidence(directory, evidence); }
  catch { evidence.status.passed = false; }
  return evidence.status.passed;
}

export function fetchProviderOnce(url, options) {
  // A 307/308 redirect would repeat the POST outside the per-scenario budget.
  return fetch(url, { ...options, redirect: "error" });
}

export async function forwardProviderRequest({ input, body, response, scenario, budget,
  attempts, providerRecords, baseUrl, providerKey }) {
  // Each HTTP handler owns its receipt, including requests rejected before
  // forwarding. A rejected duplicate must never mutate an in-flight request.
  const record = { scenario, httpStatus: null, model: input.model, request: null,
    usageEvents: [], usage: null, textChunks: 0, startedAt: new Date().toISOString(), upstreamAttempted: false };
  attempts.push(record);
  try {
    record.request = providerRequestParameters(input);
    budget.claim(scenario);
    record.upstreamAttempted = true;
    providerRecords.push(record);
    const upstream = await fetchProviderOnce(`${baseUrl.replace(/\/$/, "")}/chat/completions`, {
      method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${providerKey}` },
      body, signal: AbortSignal.timeout(60_000),
    });
    record.httpStatus = upstream.status;
    response.statusCode = upstream.status;
    response.setHeader("content-type", upstream.headers.get("content-type") ?? "application/json");
    let pending = "";
    const decoder = new TextDecoder();
    for await (const chunk of upstream.body) {
      response.write(Buffer.from(chunk));
      pending += decoder.decode(chunk, { stream: true });
      let boundary;
      while ((boundary = pending.indexOf("\n")) !== -1) {
        const line = pending.slice(0, boundary).trim(); pending = pending.slice(boundary + 1);
        if (!line.startsWith("data:") || line === "data: [DONE]") continue;
        const data = JSON.parse(line.slice(5).trim());
        if (data.usage) recordProviderUsage(record, data.usage);
        if (data.choices?.some((choice) => choice.delta?.content)) record.textChunks++;
      }
    }
    response.end();
    return record;
  } catch (error) {
    record.errorType = error?.constructor?.name ?? "ReceiverError";
    throw error;
  } finally { record.endedAt = new Date().toISOString(); }
}

export function providerRequestParameters(request) {
  assert.equal(request.max_tokens, 32, "Live request must retain the 32-token budget");
  assert.equal(request.stream, true, "Live request must stream");
  assert.equal(request.stream_options?.include_usage, true, "Live request must request provider usage");
  const thinking = request.chat_template_kwargs?.enable_thinking;
  if (thinking !== undefined) assert.equal(typeof thinking, "boolean", "Thinking flag must be boolean");
  return { max_tokens: request.max_tokens, stream: request.stream,
    stream_options: { include_usage: request.stream_options.include_usage },
    ...(thinking === undefined ? {} : { chat_template_kwargs: { enable_thinking: thinking } }) };
}

function numericUsageFields(value) {
  if (typeof value === "number") return value;
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return Object.fromEntries(Object.entries(value).flatMap(([key, child]) => {
    const safe = numericUsageFields(child);
    return safe === undefined ? [] : [[key, safe]];
  }));
}

export function recordProviderUsage(record, usage) {
  assert.ok(usage && typeof usage === "object" && !Array.isArray(usage), "Expected a provider usage object");
  record.usageEvents.push(numericUsageFields(usage));
  record.usage = { input: usage.prompt_tokens, output: usage.completion_tokens, total: usage.total_tokens };
}

export function assertBoundedProviderUsage(record) {
  providerRequestParameters(record.request);
  assert.ok(record.usage && ["input", "output", "total"].every((field) =>
    Number.isSafeInteger(record.usage[field]) && record.usage[field] >= 0),
  "Provider input/output/total must be reported nonnegative safe integers");
  assert.equal(record.usage.input + record.usage.output, record.usage.total);
  assert.ok(record.usage.output <= 32, "Provider output exceeded the 32-token budget");
}
