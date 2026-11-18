#!/usr/bin/env node
import { chmodSync, mkdirSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { createDshSemanticEngine } from 'observ-dsh/core';
import { decodeTranscriptFile, loadTranscript, sessionContext, sessionHash, toSessionLike } from './transcript.js';
import { initReplayTelemetry } from './telemetry.js';
import { emitToLocalCollector } from './local-collector.js';
import type { TranscriptBundle } from './transcript.js';

export interface OutputPaths {
  spans: string;
  events?: string;
  metrics?: string;
}

export function deriveOutputPaths(spans: string): OutputPaths {
  const name = basename(spans);
  const directory = dirname(spans);
  const suffix = name.startsWith('spans_') ? name.slice('spans_'.length) : name;
  return {
    spans,
    events: join(directory, `events_${suffix}`),
    metrics: join(directory, `metrics_${suffix}`),
  };
}

function parseArgs(args: string[]): { inputs: string[]; output: string; emit: boolean } {
  const otelIndex = args.indexOf('--otel');
  const inputs = otelIndex > 0 ? args.slice(0, otelIndex) : [];
  const output = otelIndex >= 0 ? args[otelIndex + 1] : undefined;
  if (inputs.length === 0 || inputs.some(arg => arg.startsWith('-')) || !output) {
    throw new Error('Usage: dsh-replay <transcript.jsonl|session-dir> [more...] --otel <spans-output.jsonl> [--emit]');
  }
  return { inputs, output, emit: args.includes('--emit') };
}

function defaultOutputPath(bundle: TranscriptBundle): string {
  const home = process.env.DSH_REPLAY_OUTPUT_DIR
    || join(process.env.AIVISION_HOME || join(process.env.HOME ?? '.', '.aivision'), 'dsh', 'replays');
  return join(home, `spans_${sessionContext(toSessionLike(bundle))}_${sessionHash(toSessionLike(bundle))}.jsonl`);
}

function ensurePrivateParent(filePath: string): void {
  const dir = dirname(filePath);
  const created = mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (created !== undefined) chmodSync(dir, 0o700);
}

export async function runReplay(
  transcriptInput: string | string[],
  outputFile?: string,
  opts: { emit?: boolean; collectionMode?: 'full' | 'privacy' } = {},
): Promise<OutputPaths> {
  const inputs = Array.isArray(transcriptInput) ? transcriptInput : [transcriptInput];
  const bundles = inputs.map(input => (input.endsWith('.jsonl') || input.endsWith('.jsonl.zstd')
    ? decodeTranscriptFile(input)
    : loadTranscript(input)));
  if (bundles.length === 0) throw new Error('no transcript inputs');

  const collectionMode = opts.collectionMode ?? 'full';
  const first = bundles[0]!;
  const output = outputFile ?? defaultOutputPath(first);
  const outputs = deriveOutputPaths(output);

  ensurePrivateParent(outputs.spans);
  writeFileSync(outputs.spans, '');
  writeFileSync(outputs.events!, '');
  writeFileSync(outputs.metrics!, '');

  const runtime = initReplayTelemetry(
    { spans: outputs.spans, events: outputs.events, metrics: outputs.metrics },
    collectionMode,
    first.header.cwd,
  );
  try {
    const config = runtime.config;
    const engine = createDshSemanticEngine(runtime, config);
    for (const bundle of bundles) {
      const session = toSessionLike(bundle);
      engine.handleSessionCreated(session);
      for (const event of session.events) engine.handleSessionEvent(session, event);
      engine.handleSessionDisposed(session);
    }
    engine.forceCloseAll();
    await runtime.forceFlush();
    await runtime.shutdown();

    if (opts.emit) {
      await emitToLocalCollector(outputs.spans, 'traces');
      await emitToLocalCollector(outputs.events!, 'logs');
      await emitToLocalCollector(outputs.metrics!, 'metrics');
    }
    return outputs;
  } catch (error) {
    try { await runtime.shutdown(); } catch { /* cleanup best effort */ }
    throw error;
  }
}

async function main(): Promise<void> {
  const { inputs, output, emit } = parseArgs(process.argv.slice(2));
  const outputs = await runReplay(inputs, output, { emit });
  console.log(`dsh-replay complete: ${outputs.spans}`);
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main().catch(error => {
    console.error(`dsh-replay failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  });
}
