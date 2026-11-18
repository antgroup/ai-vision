#!/usr/bin/env node
/**
 * Replay-owned local Report CLI — sends transcript-derived OTLP only to the
 * developer collector at http://127.0.0.1:4319.
 *
 * Usage:
 *   npx tsx src/report.ts <transcript.jsonl> [--shift] [--debug]
 *
 * 功能:
 *   - 读取 transcript JSONL 文件
 *   - 复用 createHooks() 转换为 OTLP spans
 *   - HTTP 只发送到固定本地 OTLP collector，不读取生产 endpoint 或凭据
 *   - --shift: 将 span 时间戳对齐到当前时间（走 replay → shift OTLP → HTTP POST 管道）
 *   - --debug: 启用调试日志
 *
 * --shift 模式工作原理:
 *   不侵入 hooks 代码，走 replay → shift → POST 管道：
 *   1. 先通过 replay 逻辑生成临时 OTLP JSONL（复用 initReplayTelemetry）
 *   2. 读取 JSONL，找到最早的 startTimeUnixNano
 *   3. 计算 offset = now - minStartTime，偏移所有时间戳
 *   4. 通过 HTTP POST 发送到 http://127.0.0.1:4319/v1/traces
 */

import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { initLogger, log, logError } from './logger.js';
import {
  createHooks,
  type CollectionMode,
} from 'observ-opencode/core';
import {
  decodeTranscriptFile,
  dispatchTranscriptEntry,
  transcriptCollectionMode,
  TRANSCRIPT_ONLY_HOOKS,
  type DecodedTranscriptEntry,
} from './decoder.js';
import { initReportTelemetry, initReplayTelemetry, replayOwnedConfig, replayReportConfig } from './telemetry.js';
import { assertCoreApiCompatible } from './compatibility.js';
import { replayOtlpUrl } from './local-collector.js';

interface ReportArgs {
  transcriptPath: string;
  shift: boolean;
  debug: boolean;
}

type TranscriptEntry = DecodedTranscriptEntry;

// ─── OTLP JSON types ─────────────────────────────────────────────────────

interface OtlpSpan {
  spanId: string;
  parentSpanId?: string;
  startTimeUnixNano: number;
  endTimeUnixNano: number;
  attributes?: Array<{ key: string; value: Record<string, unknown> }>;
  [key: string]: unknown;
}

interface OtlpScopeSpans {
  spans: OtlpSpan[];
  [key: string]: unknown;
}

interface OtlpResourceSpans {
  resource: unknown;
  scopeSpans: OtlpScopeSpans[];
  [key: string]: unknown;
}

interface OtlpExportRequest {
  resourceSpans: OtlpResourceSpans[];
}

// ─── CLI argument parsing ──────────────────────────────────────────────────

function parseArgs(): ReportArgs | null {
  const args = process.argv.slice(2);
  let transcriptPath: string | null = null;
  let shift = false;
  let debug = false;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--shift') {
      shift = true;
    } else if (arg === '--debug') {
      debug = true;
    } else if (arg === '--config' || arg === '--profile') {
      console.error(`Error: ${arg} was removed; replay HTTP is fixed to http://127.0.0.1:4319 and accepts no remote profile or credentials.`);
      return null;
    } else if (arg.startsWith('-')) {
      console.error(`Error: unsupported option ${arg}`);
      return null;
    } else if (!arg.startsWith('-')) {
      transcriptPath = arg;
    }
  }

  if (!transcriptPath) {
    console.error('Usage: report <transcript.jsonl> [--shift] [--debug]');
    console.error('');
    console.error('Options:');
    console.error('  --shift           Shift span timestamps to current time (preserving intervals)');
    console.error('  --debug           Print debug info to stderr');
    console.error('  HTTP destination  http://127.0.0.1:4319 (fixed, no override)');
    return null;
  }

  return { transcriptPath, shift, debug };
}

// ─── Replay to temp file ──────────────────────────────────────────────────

async function processTranscriptEntries(
  lines: TranscriptEntry[],
  hooks: Record<string, unknown>,
  debug: boolean,
): Promise<{ processed: number; skipped: number }> {
  let processed = 0;
  let skipped = 0;

  for (const entry of lines) {
    if (TRANSCRIPT_ONLY_HOOKS.has(entry.hook)) {
      skipped++;
      continue;
    }
    if (debug) console.error(`[report] Processing hook '${entry.hook}' at line ${entry.line}`);
    await dispatchTranscriptEntry(hooks, entry);
    processed++;
  }

  return { processed, skipped };
}

/**
 * Replay transcript to a temporary OTLP JSONL file.
 * Returns the temp file path.
 */
async function replayToTempFile(
  transcriptPath: string,
  debug: boolean,
  collectionMode: CollectionMode,
): Promise<{ tmpDir: string; tmpFile: string; lines: TranscriptEntry[]; processed: number }> {
  const lines = decodeTranscriptFile(transcriptPath);
  if (lines.length === 0) {
    throw new Error(`Empty transcript file '${transcriptPath}'`);
  }

  const resolvedCollectionMode = transcriptCollectionMode(lines, collectionMode);
  const tmpDir = mkdtempSync(join(tmpdir(), 'opencode-report-'));
  const tmpFile = join(tmpDir, 'spans.jsonl');

  if (debug) {
    console.error(`[report:shift] Replaying transcript to temp file: ${tmpFile}`);
  }

  let replayRuntime: ReturnType<typeof initReplayTelemetry> | undefined;
  let processed = 0;
  let failure: unknown;
  try {
    replayRuntime = initReplayTelemetry({ spans: tmpFile }, resolvedCollectionMode);
    const replayConfig = replayOwnedConfig('replay', resolvedCollectionMode, { traces: true, metrics: false, events: false });
    const hooks = createHooks(replayRuntime, replayConfig, {});
    ({ processed } = await processTranscriptEntries(lines, hooks, debug));
    await replayRuntime.forceFlush();
  } catch (error) {
    failure = error;
  }
  if (replayRuntime) {
    try {
      await replayRuntime.shutdown();
    } catch (shutdownError) {
      failure = failure
        ? new AggregateError([failure, shutdownError], 'Report replay and runtime shutdown failed')
        : shutdownError;
    }
  }
  if (failure) {
    rmSync(tmpDir, { recursive: true, force: true });
    throw failure;
  }

  if (debug) {
    console.error(`[report:shift] Replay complete: ${processed} spans written to ${tmpFile}`);
  }

  return { tmpDir, tmpFile, lines, processed };
}

// ─── OTLP JSONL timestamp shifting ───────────────────────────────────────

/**
 * Find the earliest startTimeUnixNano across all spans in the JSONL.
 * Uses BigInt for nanosecond precision (values ~1.7e18 exceed Number.MAX_SAFE_INTEGER).
 */
function findReplayTimeRange(jsonLines: string[]): { min: bigint; max: bigint } {
  let min: bigint | null = null;
  let max: bigint | null = null;
  for (const line of jsonLines) {
    if (!line.trim()) continue;
    const data = JSON.parse(line) as OtlpExportRequest;
    for (const rs of data.resourceSpans) {
      for (const ss of rs.scopeSpans) {
        for (const span of ss.spans) {
          const start = BigInt(span.startTimeUnixNano);
          const end = BigInt(span.endTimeUnixNano);
          if (min === null || start < min) min = start;
          if (max === null || end > max) max = end;
        }
      }
    }
  }
  return { min: min ?? 0n, max: max ?? 0n };
}

/**
 * Stretch and shift span timestamps.
 *
 * The replay generates spans with compressed wallclock timestamps (~ms apart).
 * This function stretches them proportionally to match the original transcript
 * duration, then shifts so the session ends at "now".
 *
 * Formula: new_T = targetStart + (T - replayMin) * targetDuration / replayDuration
 *
 * Preserves parent-child timing invariants (linear scaling preserves ordering).
 */
function stretchAndShiftTimestamps(
  data: OtlpExportRequest,
  replayMin: bigint,
  replayDuration: bigint,
  targetStartNs: bigint,
  targetDurationNs: bigint,
): void {
  for (const rs of data.resourceSpans) {
    for (const ss of rs.scopeSpans) {
      for (const span of ss.spans) {
        const oldStart = BigInt(span.startTimeUnixNano);
        const oldEnd = BigInt(span.endTimeUnixNano);
        if (replayDuration > 0n) {
          span.startTimeUnixNano = String(targetStartNs + (oldStart - replayMin) * targetDurationNs / replayDuration) as any;
          span.endTimeUnixNano = String(targetStartNs + (oldEnd - replayMin) * targetDurationNs / replayDuration) as any;
        } else {
          span.startTimeUnixNano = String(targetStartNs) as any;
          span.endTimeUnixNano = String(targetStartNs + targetDurationNs) as any;
        }
      }
    }
  }
}

// ─── Span timing adjustment (post stretch+shift) ────────────────────────

/**
 * Helper: extract an integer attribute value from an OTLP span.
 */
function getSpanAttrInt(span: OtlpSpan, key: string): number | null {
  const attrs = (span as Record<string, unknown>).attributes as
    Array<{ key: string; value: Record<string, unknown> }> | undefined;
  if (!attrs) return null;
  for (const attr of attrs) {
    if (attr.key === key) {
      if (attr.value.intValue !== undefined) return Number(attr.value.intValue);
      if (attr.value.doubleValue !== undefined) return Number(attr.value.doubleValue);
    }
  }
  return null;
}

/**
 * After stretch+shift, adjust span durations and positions using real timing
 * attributes recorded during the original session.
 *
 * During synchronous replay, two timestamp sources coexist:
 *   - LLM spans use real timestamps (assistantMsg.time.created/completed)
 *   - Turn/Tool/Skill spans use wallclock Date.now()
 * This creates a bimodal distribution: tools cluster seconds after LLM spans.
 * Linear stretch preserves this skew, pushing tools to the timeline end.
 *
 * This function:
 *   1. Fixes span durations using agentic.llm.duration_ms / agentic.tool.duration_ms
 *   2. Repositions child spans that fall outside their parent's time range
 *   3. Expands parent spans to contain all repositioned children
 */
function adjustSpanTimings(data: OtlpExportRequest): void {
  const allSpans: OtlpSpan[] = [];
  for (const rs of data.resourceSpans) {
    for (const ss of rs.scopeSpans) {
      for (const span of ss.spans) allSpans.push(span);
    }
  }

  // Build lookup: spanId → span, parentId → children
  const spanById = new Map<string, OtlpSpan>();
  const childrenOf = new Map<string, OtlpSpan[]>();
  for (const span of allSpans) {
    spanById.set(span.spanId, span);
    const parentId = span.parentSpanId as string;
    if (parentId) {
      let list = childrenOf.get(parentId);
      if (!list) { list = []; childrenOf.set(parentId, list); }
      list.push(span);
    }
  }

  // Step 1: Fix durations from real-measurement attributes
  for (const span of allSpans) {
    const durationMs =
      getSpanAttrInt(span, 'agentic.llm.duration_ms') ??
      getSpanAttrInt(span, 'gen_ai.response.duration_ms') ??  // backward compat
      getSpanAttrInt(span, 'agentic.tool.duration_ms');
    if (durationMs !== null && durationMs > 0) {
      const startNs = BigInt(span.startTimeUnixNano);
      span.endTimeUnixNano = String(startNs + BigInt(durationMs) * 1_000_000n) as any;
    }
  }

  // Step 2: Reposition children that fall outside their parent's time range
  for (const [parentId, children] of childrenOf) {
    const parent = spanById.get(parentId);
    if (!parent) continue;

    const pStart = BigInt(parent.startTimeUnixNano);
    const pEnd = BigInt(parent.endTimeUnixNano);

    const outside = children.filter(c => {
      const cs = BigInt(c.startTimeUnixNano);
      return cs < pStart || cs >= pEnd;
    });
    if (outside.length === 0) continue;

    // Sort by original start time to preserve relative ordering
    outside.sort((a, b) =>
      Number(BigInt(a.startTimeUnixNano) - BigInt(b.startTimeUnixNano)));

    // Distribute evenly within [pStart + 10%, pEnd)
    const pDuration = pEnd - pStart;
    const margin = pDuration > 10n ? pDuration / 10n : 0n;
    const slotRange = pDuration - margin;

    for (let i = 0; i < outside.length; i++) {
      const child = outside[i];
      const childDur = BigInt(child.endTimeUnixNano) - BigInt(child.startTimeUnixNano);
      const newStart = pStart + margin +
        (slotRange * BigInt(i)) / BigInt(Math.max(outside.length, 1));
      child.startTimeUnixNano = String(newStart) as any;
      child.endTimeUnixNano = String(newStart + (childDur > 0n ? childDur : 1_000_000n)) as any;
    }
  }

  // Step 3: Expand parents to contain all children (bottom-up via recursion)
  const expanded = new Set<string>();
  const expandParent = (id: string): void => {
    if (expanded.has(id)) return;
    expanded.add(id);
    const kids = childrenOf.get(id);
    if (!kids) return;
    // Recurse into grandchildren first
    for (const k of kids) expandParent(k.spanId as string);

    const span = spanById.get(id);
    if (!span) return;
    let maxEnd = BigInt(span.endTimeUnixNano);
    for (const k of kids) {
      const ke = BigInt(k.endTimeUnixNano);
      if (ke > maxEnd) maxEnd = ke;
    }
    if (maxEnd > BigInt(span.endTimeUnixNano)) {
      span.endTimeUnixNano = String(maxEnd + 1_000_000n) as any; // +1ms padding
    }
  };
  for (const span of allSpans) {
    if (!span.parentSpanId) expandParent(span.spanId as string);
  }
}

// ─── HTTP POST OTLP traces ───────────────────────────────────────────────

async function postOtlpTraces(
  url: string,
  headers: Record<string, string>,
  body: OtlpExportRequest,
  debug: boolean,
): Promise<void> {
  const payload = JSON.stringify(body);
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
    body: payload,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`OTLP POST failed: ${response.status} ${response.statusText} — ${text.slice(0, 200)}`);
  }

  if (debug) {
    console.error(`[report:shift] POST ${url} → ${response.status} (${payload.length} bytes)`);
  }
}

// ─── Shift mode: replay → shift → POST ───────────────────────────────────

async function runShiftMode(
  parsed: ReportArgs,
  otlpUrl: string,
  otlpHeaders: Record<string, string>,
  collectionMode: CollectionMode,
): Promise<void> {
  const { transcriptPath, debug } = parsed;

  // Step 1: Replay to temp OTLP JSONL
  console.log(`[report:shift] Step 1/3: Replaying transcript...`);
  const { tmpDir, tmpFile, lines, processed } = await replayToTempFile(transcriptPath, debug, collectionMode);

  try {
    // Step 2: Read temp file, stretch + shift timestamps
    console.log(`[report:shift] Step 2/3: Stretching & shifting timestamps...`);
    const rawContent = readFileSync(tmpFile, 'utf-8');
    const jsonLines = rawContent.split('\n').filter(l => l.trim());

    if (jsonLines.length === 0) {
      console.error('[report:shift] No spans generated from replay');
      return;
    }

    // Get original session duration from transcript entry timestamps
    const transcriptTimestamps = lines
      .filter((entry): entry is TranscriptEntry & { timestamp: string } => typeof entry.timestamp === 'string')
      .map(e => new Date(e.timestamp).getTime())
      .filter(t => !isNaN(t));
    const transcriptMinMs = transcriptTimestamps.length ? Math.min(...transcriptTimestamps) : 0;
    const transcriptMaxMs = transcriptTimestamps.length ? Math.max(...transcriptTimestamps) : 1000;
    const transcriptDurationNs = BigInt(Math.max(transcriptMaxMs - transcriptMinMs, 1000)) * 1_000_000n;

    // Get replay time range (compressed to ~ms from wallclock)
    const { min: replayMin, max: replayMax } = findReplayTimeRange(jsonLines);
    const replayDuration = replayMax - replayMin;

    // Target: session ends at "now", starts at now - transcriptDuration
    const nowNs = BigInt(Date.now()) * 1_000_000n;
    const targetStartNs = nowNs - transcriptDurationNs;
    const durationSec = Number(transcriptDurationNs / 1_000_000_000n);

    if (debug) {
      console.error(`[report:shift] Transcript duration: ${durationSec.toFixed(1)}s`);
      console.error(`[report:shift] Replay duration:     ${Number(replayDuration / 1_000_000n).toFixed(1)}ms`);
      console.error(`[report:shift] Stretch factor:      ${replayDuration > 0n ? Number(transcriptDurationNs / replayDuration).toFixed(0) : 'N/A'}x`);
    }

    console.log(`[report:shift] Stretching ${jsonLines.length} spans across ${(durationSec / 60).toFixed(1)}min session`);

    // Step 3: Stretch + shift and POST each line
    console.log(`[report:shift] Step 3/3: Posting to ${otlpUrl}...`);

    const BATCH_SIZE = 50;
    let posted = 0;

    for (let i = 0; i < jsonLines.length; i += BATCH_SIZE) {
      const batch = jsonLines.slice(i, i + BATCH_SIZE);
      const mergedResourceSpans: OtlpResourceSpans[] = [];

      for (const line of batch) {
        const data = JSON.parse(line) as OtlpExportRequest;
        stretchAndShiftTimestamps(data, replayMin, replayDuration, targetStartNs, transcriptDurationNs);
        adjustSpanTimings(data);
        mergedResourceSpans.push(...data.resourceSpans);
      }

      const batchBody: OtlpExportRequest = { resourceSpans: mergedResourceSpans };
      await postOtlpTraces(otlpUrl, otlpHeaders, batchBody, debug);
      posted += batch.length;

      if (debug) {
        console.error(`[report:shift] Progress: ${posted}/${jsonLines.length} spans`);
      }
    }

    console.log(`[report:shift] Complete: ${posted} spans sent (session: ${(durationSec / 60).toFixed(1)}min)`);
  } finally {
    // Cleanup the isolated report workspace.
    try {
      rmSync(tmpDir, { recursive: true, force: true });
      if (debug) console.error(`[report:shift] Cleaned up temp directory: ${tmpDir}`);
    } catch (error) {
      logError(`Failed to remove report temp directory ${tmpDir}`, error);
    }
  }
}

// ─── Normal mode: transcript → hooks → HTTP export ────────────────────────

async function runNormalMode(
  parsed: ReportArgs,
): Promise<void> {
  const { transcriptPath, debug } = parsed;

  const lines = decodeTranscriptFile(transcriptPath);
  if (lines.length === 0) {
    console.error(`Error: Empty transcript file '${transcriptPath}'`);
    process.exit(1);
  }

  const sessionId = lines[0]?.session || 'report';

  const collectionMode = transcriptCollectionMode(lines);
  const effectiveConfig = replayReportConfig(collectionMode);
  const runtime = initReportTelemetry(collectionMode);
  let processed = 0;
  let skipped = 0;
  let failure: unknown;
  try {
    const hooks = createHooks(runtime, effectiveConfig, {});
    console.log(`Reporting ${lines.length} events from '${transcriptPath}' (session=${sessionId})`);
    ({ processed, skipped } = await processTranscriptEntries(lines, hooks, debug));
    if (debug) console.error(`[report] Processed ${processed} events, skipped ${skipped}`);
    console.log(`[report] Flushing ${processed} events...`);
    await runtime.forceFlush();
  } catch (error) {
    failure = error;
  }
  try {
    await runtime.shutdown();
  } catch (shutdownError) {
    failure = failure
      ? new AggregateError([failure, shutdownError], 'Report and runtime shutdown failed')
      : shutdownError;
  }
  if (failure) throw failure;

  console.log(`Report complete: ${processed} events sent`);
}

// ─── Main ──────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  assertCoreApiCompatible();
  const parsed = parseArgs();
  if (!parsed) process.exit(1);

  const { shift, debug } = parsed;
  initLogger(debug || undefined);

  if (debug) {
    console.error(`[report] Reading transcript from '${parsed.transcriptPath}'`);
    console.error(`[report] Mode: ${shift ? 'shift (replay → shift → POST)' : 'normal (hooks → HTTP export)'}`);
  }

  if (shift) {
    await runShiftMode(parsed, replayOtlpUrl('traces'), {}, 'full');
  } else {
    await runNormalMode(parsed);
  }
}

main().catch(err => {
  console.error('Report failed:', err);
  process.exit(1);
});
