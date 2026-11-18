#!/usr/bin/env node
/**
 * Report CLI — transcript 上报 / replay / shift 三合一工具。
 *
 * Usage:
 *   npx tsx src/report.ts <transcript.jsonl> [options]
 *
 * Modes:
 *   (default)     Replay transcript → HTTP export to OTLP collector
 *   --shift       Stretch+shift timestamps to current time → HTTP POST
 *   --replay      Replay transcript → local OTLP JSONL file (no HTTP)
 *
 * Options:
 *   --otel <path>     Output OTLP JSONL path (required for --replay)
 *   --events <path>   Output events JSONL path (--replay only, auto-derived if omitted)
 *   --debug           Print debug info to stderr
 *
 * --shift 模式工作原理:
 *   不侵入 hooks 代码，走 replay → stretch+shift → POST 管道：
 *   1. 调用 observ-openclaw.createHooks() 生成临时 OTLP JSONL
 *   2. 读取 JSONL，找到 replay 时间范围
 *   3. 从 transcript timestamps 计算原始 session 时长
 *   4. 等比拉伸 + 平移，session 末尾对齐 now
 *   5. 通过 HTTP POST 发送到固定的本地 OTLP endpoint
 */

// __PLUGIN_VERSION__ is normally injected by esbuild at build time.
// When running via tsx (unbundled), define it as a global fallback.
(globalThis as any).__PLUGIN_VERSION__ = '0.0.0-report';

import { chmodSync, readFileSync, unlinkSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import {
  createHooks,
  type ApiLogger,
  type OpenClawApi,
} from '@aivision/observ-openclaw';
import { replayOtlpUrl } from './local-collector.js';
import { migrateOpenClawReplayRecord } from './raw-envelope.js';
import { initReportTelemetry, initReplayTelemetry, replaySemanticConfig } from './telemetry.js';

interface ReportArgs {
  transcriptPath: string;
  shift: boolean;
  replay: boolean;
  otelOutput?: string;
  eventsOutput?: string;
  debug: boolean;
}

interface TranscriptEntry {
  timestamp: string;
  hook: string;
  event: unknown;
  ctx: unknown;
  collectionMode: 'full' | 'privacy';
}

// ─── TOML config structures (matching opencode/codex) ──────────────────────

// ─── OTLP JSON types ─────────────────────────────────────────────────────

interface OtlpSpan {
  startTimeUnixNano: number;
  endTimeUnixNano: number;
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
  let otelOutput: string | undefined;
  let eventsOutput: string | undefined;
  let shift = false;
  let replay = false;
  let debug = false;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--shift') {
      shift = true;
    } else if (arg === '--replay') {
      replay = true;
    } else if (arg === '--debug') {
      debug = true;
    } else if (arg === '--config' || arg === '--profile') {
      console.error(`Error: ${arg} is not supported; replay egress is fixed to http://127.0.0.1:4319`);
      return null;
    } else if (arg === '--otel' && i + 1 < args.length) {
      otelOutput = args[++i];
    } else if (arg === '--events' && i + 1 < args.length) {
      eventsOutput = args[++i];
    } else if (!arg.startsWith('-')) {
      transcriptPath = arg;
    }
  }

  if (!transcriptPath) {
    console.error('Usage: report <transcript.jsonl> [options]');
    console.error('');
    console.error('Modes:');
    console.error('  (default)         Replay transcript → HTTP export to http://127.0.0.1:4319');
    console.error('  --shift           Stretch+shift timestamps to current time → HTTP POST');
    console.error('  --replay          Replay transcript → local OTLP JSONL file (no HTTP)');
    console.error('');
    console.error('Options:');
    console.error('  --otel <path>     Output OTLP JSONL path (required for --replay)');
    console.error('  --events <path>   Output events JSONL path (--replay only, auto-derived if omitted)');
    console.error('  --debug           Print debug info to stderr');
    return null;
  }

  if (shift && replay) {
    console.error('Error: --shift and --replay are mutually exclusive');
    return null;
  }

  if (replay && !otelOutput) {
    console.error('Error: --replay requires --otel <output.jsonl>');
    return null;
  }

  return { transcriptPath, shift, replay, otelOutput, eventsOutput, debug };
}

// ─── TOML config loading ───────────────────────────────────────────────────

// ─── OTLP endpoint resolution ─────────────────────────────────────────────

// ─── Replay to temp file ──────────────────────────────────────────────────

function buildMockApi(debug: boolean): { mockApi: OpenClawApi; handlers: Map<string, (event: unknown, ctx: unknown) => unknown> } {
  const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
  const logger: ApiLogger = {
    info: debug ? (msg: string) => console.error(`[report] ${msg}`) : undefined,
    debug: debug ? (msg: string) => console.error(`[report:debug] ${msg}`) : undefined,
    warn: (msg: string) => console.error(`[report:warn] ${msg}`),
  };

  const mockApi: OpenClawApi = {
    on(event: string, handler: (event: unknown, ctx: unknown) => unknown) {
      handlers.set(event, handler);
    },
    registerHook() {
      // Command hooks are not replayed
    },
    logger,
  };

  return { mockApi, handlers };
}

function readTranscript(path: string): TranscriptEntry[] {
  const files = statSync(path).isDirectory()
    ? readdirSync(path).filter((name) => name.endsWith('.jsonl')).sort().map((name) => join(path, name))
    : [path];
  const entries = files.flatMap((file) => readFileSync(file, 'utf-8')
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const envelope = migrateOpenClawReplayRecord(JSON.parse(line));
      return {
        timestamp: envelope.timestamp,
        hook: envelope.hook,
        event: envelope.input,
        ctx: envelope.context,
        collectionMode: envelope.collectionMode,
      } satisfies TranscriptEntry;
    }));

  // before_model_resolve is commonly emitted before OpenClaw attaches ctx.trace.
  // Enrich only the in-memory replay copy from a later event in the same run/session;
  // the source transcript remains immutable.
  const tracesByRun = new Map<string, unknown>();
  for (const entry of entries) {
    const event = entry.event && typeof entry.event === 'object' ? entry.event as Record<string, unknown> : {};
    const ctx = entry.ctx && typeof entry.ctx === 'object' ? entry.ctx as Record<string, unknown> : {};
    const key = String(ctx.runId || event.runId || ctx.sessionKey || event.sessionKey || '');
    if (key && ctx.trace) tracesByRun.set(key, ctx.trace);
  }
  for (const entry of entries) {
    const event = entry.event && typeof entry.event === 'object' ? entry.event as Record<string, unknown> : {};
    const ctx = entry.ctx && typeof entry.ctx === 'object' ? entry.ctx as Record<string, unknown> : {};
    const key = String(ctx.runId || event.runId || ctx.sessionKey || event.sessionKey || '');
    if (key && !ctx.trace && tracesByRun.has(key)) entry.ctx = { ...ctx, trace: tracesByRun.get(key) };
  }

  return entries.sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
}

const SKIP_HOOKS = new Set(['plugin:init', 'gateway_start', 'before_message_write', 'message_received']);

function processTranscriptEntries(
  lines: TranscriptEntry[],
  handlers: Map<string, (event: unknown, ctx: unknown) => unknown>,
  debug: boolean,
): { processed: number; skipped: number } {
  let processed = 0;
  let skipped = 0;

  for (const entry of lines) {
    if (SKIP_HOOKS.has(entry.hook)) {
      skipped++;
      continue;
    }
    try {
      const handler = handlers.get(entry.hook);
      if (handler) {
        handler(entry.event, entry.ctx);
        processed++;
      } else {
        skipped++;
      }
    } catch (err) {
      if (debug) {
        console.error(`[report] Error processing hook '${entry.hook}': ${err instanceof Error ? err.message : String(err)}`);
      }
      skipped++;
    }
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
): Promise<{ tmpFile: string; lines: TranscriptEntry[]; processed: number }> {
  const lines = readTranscript(transcriptPath);
  if (lines.length === 0) {
    throw new Error(`Empty transcript file '${transcriptPath}'`);
  }

  const tmpDir = join(tmpdir(), 'openclaw-report');
  const createdTmpDir = mkdirSync(tmpDir, { recursive: true, mode: 0o700 });
  if (createdTmpDir) chmodSync(tmpDir, 0o700);
  const tmpFile = join(tmpDir, `spans_${Date.now()}.jsonl`);

  if (debug) {
    console.error(`[report:shift] Replaying transcript to temp file: ${tmpFile}`);
  }

  const collectionMode = lines.some((line) => line.collectionMode === 'privacy') ? 'privacy' : 'full';
  const replayConfig = replaySemanticConfig(collectionMode);
  const replayRuntime = initReplayTelemetry(tmpFile, undefined, collectionMode);
  const { mockApi, handlers } = buildMockApi(debug);
  createHooks(mockApi, replayRuntime, replayConfig);

  const { processed } = processTranscriptEntries(lines, handlers, debug);

  await replayRuntime.forceFlush();
  await replayRuntime.shutdown();

  if (debug) {
    console.error(`[report:shift] Replay complete: ${processed} spans written to ${tmpFile}`);
  }

  return { tmpFile, lines, processed };
}

// ─── OTLP JSONL timestamp shifting ───────────────────────────────────────

/**
 * Find the min start and max end timestamps across all spans in the JSONL.
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

// ─── HTTP POST OTLP traces ───────────────────────────────────────────────

async function postOtlpTraces(
  url: string,
  body: OtlpExportRequest,
  debug: boolean,
): Promise<void> {
  const payload = JSON.stringify(body);
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
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
): Promise<void> {
  const { transcriptPath, debug } = parsed;

  // Step 1: Replay to temp OTLP JSONL
  console.log(`[report:shift] Step 1/3: Replaying transcript...`);
  const { tmpFile, lines, processed } = await replayToTempFile(transcriptPath, debug);

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
      .map(e => new Date(e.timestamp).getTime())
      .filter(t => !isNaN(t));
    const transcriptMinMs = Math.min(...transcriptTimestamps);
    const transcriptMaxMs = Math.max(...transcriptTimestamps);
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
    console.log(`[report:shift] Step 3/3: Posting to ${replayOtlpUrl('traces')}...`);

    const BATCH_SIZE = 50;
    let posted = 0;

    for (let i = 0; i < jsonLines.length; i += BATCH_SIZE) {
      const batch = jsonLines.slice(i, i + BATCH_SIZE);
      const mergedResourceSpans: OtlpResourceSpans[] = [];

      for (const line of batch) {
        const data = JSON.parse(line) as OtlpExportRequest;
        stretchAndShiftTimestamps(data, replayMin, replayDuration, targetStartNs, transcriptDurationNs);
        mergedResourceSpans.push(...data.resourceSpans);
      }

      const batchBody: OtlpExportRequest = { resourceSpans: mergedResourceSpans };
      await postOtlpTraces(replayOtlpUrl('traces'), batchBody, debug);
      posted += batch.length;

      if (debug) {
        console.error(`[report:shift] Progress: ${posted}/${jsonLines.length} spans`);
      }
    }

    console.log(`[report:shift] Complete: ${posted} spans sent (${(durationSec / 60).toFixed(1)}min session)`);
  } finally {
    // Cleanup temp file
    try {
      unlinkSync(tmpFile);
      if (debug) console.error(`[report:shift] Cleaned up temp file: ${tmpFile}`);
    } catch {
      // ignore cleanup errors
    }
  }
}

// ─── Replay mode: transcript → local OTLP JSONL ─────────────────────

async function runReplayMode(parsed: ReportArgs): Promise<void> {
  const { transcriptPath, otelOutput, eventsOutput, debug } = parsed;

  if (!otelOutput) {
    console.error('Error: --replay requires --otel <output.jsonl>');
    process.exit(1);
  }

  const lines = readTranscript(transcriptPath);
  if (lines.length === 0) {
    console.error(`Error: Empty transcript file '${transcriptPath}'`);
    process.exit(1);
  }

  // Derive events file: spans_xxx.jsonl → events_xxx.jsonl (if not specified)
  const eventsFile = eventsOutput ?? (() => {
    const base = otelOutput.replace(/spans_/, 'events_');
    return base === otelOutput ? join(dirname(otelOutput), `events_${Date.now()}.jsonl`) : base;
  })();

  // Ensure output directory exists
  const outputDirectory = dirname(otelOutput);
  const createdOutputDirectory = mkdirSync(outputDirectory, { recursive: true, mode: 0o700 });
  if (createdOutputDirectory) chmodSync(outputDirectory, 0o700);

  console.log(`[report:replay] ${transcriptPath} → spans=${otelOutput}, events=${eventsFile}`);

  const collectionMode = lines.some((line) => line.collectionMode === 'privacy') ? 'privacy' : 'full';
  const replayConfig = replaySemanticConfig(collectionMode);
  const runtime = initReplayTelemetry(otelOutput, eventsFile, collectionMode);
  const { mockApi, handlers } = buildMockApi(debug);
  createHooks(mockApi, runtime, replayConfig);

  const { processed, skipped } = processTranscriptEntries(lines, handlers, debug);

  console.log(`[report:replay] Processed ${processed} events, skipped ${skipped}`);

  await runtime.forceFlush();
  await runtime.shutdown();
  console.log(`[report:replay] Done → ${otelOutput}`);
}

// ─── Normal mode: transcript → hooks → HTTP export ────────────────────────

async function runNormalMode(
  parsed: ReportArgs,
): Promise<void> {
  const { transcriptPath, debug } = parsed;

  const lines = readTranscript(transcriptPath);
  if (lines.length === 0) {
    console.error(`Error: Empty transcript file '${transcriptPath}'`);
    process.exit(1);
  }

  const collectionMode = lines.some((line) => line.collectionMode === 'privacy') ? 'privacy' : 'full';
  const config = replaySemanticConfig(collectionMode);
  const runtime = initReportTelemetry(collectionMode);
  const { mockApi, handlers } = buildMockApi(debug);
  createHooks(mockApi, runtime, config);

  console.log(`Reporting ${lines.length} events from '${transcriptPath}'`);

  const { processed, skipped } = processTranscriptEntries(lines, handlers, debug);

  if (debug) {
    console.error(`[report] Processed ${processed} events, skipped ${skipped}`);
  }

  console.log(`[report] Flushing ${processed} events...`);
  await runtime.forceFlush();
  await runtime.shutdown();

  console.log(`Report complete: ${processed} events sent`);
}

// ─── Main ──────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const parsed = parseArgs();
  if (!parsed) process.exit(1);

  const { shift, replay, debug } = parsed;

  const modeName = replay ? 'replay (→ local JSONL)' : shift ? 'shift (replay → stretch → POST)' : 'normal (hooks → HTTP export)';
  if (debug) {
    console.error(`[report] Reading transcript from '${parsed.transcriptPath}'`);
    console.error(`[report] Mode: ${modeName}`);
  }

  // Replay mode: no config needed, write to local file
  if (replay) {
    await runReplayMode(parsed);
    return;
  }

  if (shift) {
    await runShiftMode(parsed);
  } else {
    await runNormalMode(parsed);
  }
}

main().catch(err => {
  console.error('Report failed:', err);
  process.exit(1);
});
