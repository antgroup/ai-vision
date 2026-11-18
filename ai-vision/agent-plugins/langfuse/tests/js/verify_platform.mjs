// Native reads + v1 regression only. This deliberately does not import the
// platform semantic parser or infer usage from the captured SDK attributes.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { pathToFileURL } from "node:url";
import { canonicalUsageKeys, generationUsageContract, rawUsageKey } from "./usage_contract.mjs";

export async function loadTarget(path, key = "langfuse") {
  const targets = JSON.parse(await readFile(path, "utf8"));
  const target = targets[key] ?? targets;
  for (const name of ["agent_uuid", "space_key", "space_id", "agent_product"]) assert.ok(target[name], `Target requires ${name}`);
  assert.ok(target.endpoint ?? target.base_url, "Target requires endpoint");
  return target;
}

export function exactTimestampNs(value) {
  if (typeof value === "string" && /^\d+$/.test(value)) return BigInt(value);
  if (typeof value === "number") {
    assert.ok(Number.isSafeInteger(value), "Unsafe JSON numeric nanoseconds; use the exact ISO timestamp");
    return BigInt(value);
  }
  const match = typeof value === "string" && value.match(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$/);
  assert.ok(match, "Expected ISO timestamp with up to nanosecond precision");
  const wholeSecondMs = Date.parse(match[1] + match[3]);
  assert.ok(Number.isSafeInteger(wholeSecondMs));
  return BigInt(wholeSecondMs) * 1_000_000n + BigInt((match[2] ?? "").padEnd(9, "0"));
}

function recordsFromLedgers(ledgers) {
  return ledgers.flatMap((rows) => rows.flatMap((row) => row.collectorOnly ? [] : row.expectedByTrace
    ? row.expectedByTrace.map((record) => ({ ...record, sdkVersion: row.sdkVersion, scenario: row.scenario }))
    : [{ ...row, usage: row.expectedUsage, scenario: row.variant ?? row.scenario }]));
}

export async function verifyPlatform(target, ledgers, reportPath) {
  const records = recordsFromLedgers(ledgers);
  assert.ok(records.length, "No platform traces in the ledger");
  const origin = (target.endpoint ?? target.base_url).replace(/\/$/, "");
  const prefix = `/openapi/v1/spaces/${target.space_id}/agents/${target.agent_product}`;
  const checks = [], failures = [];
  async function fetchJson(path, basic = false) {
    const response = await fetch(origin + path, { headers: {
      authorization: basic ? `Basic ${Buffer.from(`${target.agent_uuid}:${target.space_key}`).toString("base64")}` : `Bearer ${target.space_key}`,
    }, signal: AbortSignal.timeout(30_000) });
    return { status: response.status, body: await response.json() };
  }
  function check(name, condition) {
    checks.push({ name, passed: Boolean(condition) });
    if (!condition) failures.push(name);
  }
  function equal(left, right) { try { assert.deepEqual(left, right); return true; } catch { return false; } }
  function object(value) { try { return typeof value === "string" ? JSON.parse(value) : value; } catch { return undefined; } }
  for (const expected of records) {
    const source = expected.inventory ?? [];
    assert.ok(source.length, "Ledger needs SDK source span inventory");
    const start = Math.min(...source.map((span) => Number(BigInt(span.startTimeUnixNano) / 1_000_000n)));
    const end = Math.max(...source.map((span) => Number(BigInt(span.endTimeUnixNano) / 1_000_000n))) + 1;
    const params = new URLSearchParams({ from: String(start), to: String(end), detail: "1", agent_uuid: target.agent_uuid });
    const path = `${prefix}/trace/${expected.traceId}/detail?${params}`;
    const deadline = Date.now() + 60_000;
    let native;
    do {
      native = await fetchJson(path);
      if (native.status === 200 && native.body.data?.meta?.complete && native.body.data.spans?.length === source.length) break;
      if ([400, 401, 403].includes(native.status) || Date.now() >= deadline) break;
      await new Promise((done) => setTimeout(done, 1000));
    } while (true);
    const detail = native.body.data ?? {};
    const spans = detail.spans ?? [];
    const label = `${expected.scenario}:${expected.traceId}`;
    check(`${label}:native-http`, native.status === 200 && native.body.code === 0);
    check(`${label}:complete`, detail.meta?.complete === true);
    check(`${label}:inventory`, spans.length === source.length && new Set(spans.map((span) => span.span_id)).size === source.length
      && source.every((span) => spans.some((live) => live.span_id === span.id)));
    check(`${label}:parents`, source.every((span) => (spans.find((live) => live.span_id === span.id)?.parent_span_id || null) === span.parentId));
    const statusCode = (value) => typeof value === "object" ? statusCode(value?.code) :
      ({ OK: 1, ERROR: 2, UNSET: 0, STATUS_CODE_OK: 1, STATUS_CODE_ERROR: 2, STATUS_CODE_UNSET: 0 }[value] ?? Number(value ?? 0));
    check(`${label}:error-status`, source.every((span) => {
      const live = spans.find((item) => item.span_id === span.id);
      return live && statusCode(live.status) === statusCode(span.status)
        && (live.attributes?.["langfuse.observation.level"] ?? null) === (span.level ?? null);
    }));
    check(`${label}:raw-io`, source.every((span) => {
      const live = spans.find((item) => item.span_id === span.id);
      return live && ["input", "output"].every((field) => (live.attributes?.[`langfuse.observation.${field}`] ?? null) === (span[field] ?? null));
    }));
    check(`${label}:timestamps`, source.every((span) => {
      const live = spans.find((item) => item.span_id === span.id);
      // Native top-level ISO preserves nanoseconds. payload JSON integers may
      // exceed JS Number precision; never prefer them over the exact strings.
      return live && exactTimestampNs(live.start_time_unix_nano ?? live.payload?.start_time_unix_nano) === BigInt(span.startTimeUnixNano)
        && exactTimestampNs(live.end_time_unix_nano ?? live.payload?.end_time_unix_nano) === BigInt(span.endTimeUnixNano);
    }));
    const usage = detail.semantics?.usage ?? {};
    check(`${label}:independent-usage`, ["input", "output", "total"].every((field) => usage[field] === (expected.usage?.[field] ?? null)));
    const generations = source.filter((span) => span.type === "generation");
    const references = expected.expectedGenerations ?? [];
    const contracts = new Map();
    let validReferences = Array.isArray(expected.expectedGenerations) && references.length === generations.length
      && new Set(references.map((item) => item.id)).size === generations.length;
    for (const reference of references) {
      try { contracts.set(reference.id, generationUsageContract(reference.providerUsage, expected.sdkVersion)); }
      catch { validReferences = false; }
    }
    // Existing no-generation traces do not need a provider usage reference.
    if (!generations.length && !references.length) validReferences = true;
    const completeReferences = validReferences && generations.every((span) => contracts.has(span.id));
    check(`${label}:sdk-generation-usage`, completeReferences && generations.every((span) => {
      const contract = contracts.get(span.id), attrs = span.usageAttributes;
      if (!attrs || typeof attrs !== "object") return false;
      const wanted = contract.raw === null ? {} : { [rawUsageKey]: contract.raw };
      return equal(Object.fromEntries(Object.entries(attrs).map(([key, value]) => [key, key === rawUsageKey ? object(value) : value])), wanted)
        && Object.entries(attrs).every(([key, value]) => equal(spans.find((live) => live.span_id === span.id)?.attributes?.[key], value))
        && (contract.raw !== null || !Object.hasOwn(spans.find((live) => live.span_id === span.id)?.attributes ?? {}, rawUsageKey));
    }));
    check(`${label}:native-generation-usage`, completeReferences && generations.every((span) => {
      const attrs = spans.find((live) => live.span_id === span.id)?.attributes ?? {};
      const contract = contracts.get(span.id);
      return Object.entries(canonicalUsageKeys).every(([field, key]) => (attrs[key] ?? null) === contract.values[field])
        && equal(Object.hasOwn(attrs, rawUsageKey) ? object(attrs[rawUsageKey]) : null, contract.raw);
    }));
    check(`${label}:usage-provenance`, completeReferences && generations.every((span) => {
      const attrs = spans.find((live) => live.span_id === span.id)?.attributes ?? {};
      const sources = object(attrs["aivision.normalization.sources"]) ?? {};
      const actual = Object.fromEntries(Object.entries(sources).filter(([key]) => key.startsWith("gen_ai.usage.")));
      const wanted = Object.fromEntries(Object.entries(contracts.get(span.id).sources).map(([field, value]) => [canonicalUsageKeys[field], value]));
      return equal(actual, wanted);
    }));
    const aggregate = Object.fromEntries(Object.keys(canonicalUsageKeys).map((field) => {
      const values = generations.map((span) => contracts.get(span.id)?.values[field]);
      return [field, values.length && values.every((value) => value != null) ? values.reduce((sum, value) => sum + value, 0) : null];
    }));
    check(`${label}:native-usage-details`, completeReferences && Object.entries(aggregate).every(([field, value]) => usage[field] === value)
      && equal(usage.source, Object.fromEntries(Object.entries(aggregate).filter(([, value]) => value !== null).map(([field]) => [field, "aggregate_unique_calls"]))));
    if (expected.sessionId) check(`${label}:session`, detail.sessionId === expected.sessionId);
    if (expected.userId) check(`${label}:business-user`, detail.semantics?.userId === expected.userId);
    if (expected.expectedGenerationCount !== undefined) {
      check(`${label}:generations`, spans.filter((span) => span.attributes?.["langfuse.observation.type"] === "generation").length === expected.expectedGenerationCount);
    }
    const publicTrace = await fetchJson(`/api/public/traces/${expected.traceId}`, true);
    check(`${label}:v1-http`, publicTrace.status === 200 && publicTrace.body.id === expected.traceId);
    const observations = publicTrace.body.observations ?? [];
    check(`${label}:v1-inventory`, observations.length === source.length && source.every((span) => observations.some((live) => live.id === span.id)));
    check(`${label}:v1-generation-usage`, completeReferences && generations.every((span) => {
      const observation = observations.find((item) => item.id === span.id), contract = contracts.get(span.id);
      return observation && ["input", "output", "total"].every((field) => observation.usage?.[field] === contract.v1[field])
        && observation.usage?.unit === "TOKENS" && equal(observation.usageDetails, contract.raw ?? {});
    }));
    if (expected.metadata) {
      const root = observations.find((span) => !span.parentObservationId);
      check(`${label}:typed-metadata`, Boolean(root) && Object.entries(expected.metadata).every(([key, value]) => {
        try { assert.deepEqual(root.metadata[key], value); return true; } catch { return false; }
      }));
    }
  }
  const report = { passed: failures.length === 0, traces: records.length, checks: checks.length, failures, results: checks,
    identity: { spaceId: target.space_id, agentUuid: target.agent_uuid, agentProduct: target.agent_product } };
  if (reportPath) await writeFile(reportPath, JSON.stringify(report, null, 2) + "\n");
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { values } = parseArgs({ options: { targets: { type: "string" }, ledger: { type: "string", multiple: true }, out: { type: "string" } } });
  assert.ok(values.targets && values.ledger?.length, "Usage: --targets FILE --ledger FILE [--ledger FILE] --out FILE");
  const report = await verifyPlatform(await loadTarget(values.targets), await Promise.all(values.ledger.map(async (path) => JSON.parse(await readFile(path, "utf8")))), values.out);
  console.log(JSON.stringify({ passed: report.passed, traces: report.traces, checks: report.checks, failures: report.failures }));
  process.exitCode = report.passed ? 0 : 1;
}
