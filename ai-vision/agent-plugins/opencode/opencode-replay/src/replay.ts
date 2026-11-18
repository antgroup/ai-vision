#!/usr/bin/env node
import { chmodSync, mkdirSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import {
  createHooks,
  resetHookState,
} from 'observ-opencode/core';
import { assertCoreApiCompatible } from './compatibility.js';
import { decodeTranscriptFile, dispatchTranscriptEntry, transcriptCollectionMode } from './decoder.js';
import { initLogger, log } from './logger.js';
import { initReplayTelemetry, replayOwnedConfig } from './telemetry.js';
import { OutputTransaction, type OutputPaths } from './transaction.js';

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

function parseArgs(args: string[]): { transcripts: string[]; output: string } {
  const otelIndex = args.indexOf('--otel');
  const transcripts = otelIndex > 0 ? args.slice(0, otelIndex) : [];
  const output = otelIndex >= 0 ? args[otelIndex + 1] : undefined;
  if (transcripts.length === 0 || transcripts.some(arg => arg.startsWith('-')) || !output) {
    throw new Error('Usage: opencode-replay <transcript.jsonl> [more-transcripts.jsonl ...] --otel <spans-output.jsonl>');
  }
  return { transcripts, output };
}

export async function runReplay(
  transcriptInput: string | string[],
  outputFile: string,
): Promise<OutputPaths> {
  assertCoreApiCompatible();

  // Decode and validate the entire file before exporter/provider initialization.
  const transcriptFiles = Array.isArray(transcriptInput) ? transcriptInput : [transcriptInput];
  const sourcedEntries = transcriptFiles.flatMap((file, fileIndex) => (
    decodeTranscriptFile(file).map((entry, entryIndex) => ({ entry, file, fileIndex, entryIndex }))
  ));
  if (sourcedEntries.every(({ entry }) => entry.timestamp)) {
    sourcedEntries.sort((left, right) => (
      left.entry.timestamp!.localeCompare(right.entry.timestamp!)
      || left.fileIndex - right.fileIndex
      || left.entryIndex - right.entryIndex
    ));
  }
  const entries = sourcedEntries.map(({ entry }) => entry);
  const collectionMode = transcriptCollectionMode(entries);
  const outputs = deriveOutputPaths(outputFile);
  const transaction = new OutputTransaction(outputs);
  let runtime: ReturnType<typeof initReplayTelemetry> | undefined;

  try {
    const createdDirectory = mkdirSync(dirname(outputFile), { recursive: true, mode: 0o700 });
    if (createdDirectory) chmodSync(dirname(outputFile), 0o700);
    for (const path of Object.values(transaction.staged)) writeFileSync(path, '');
    resetHookState();
    runtime = initReplayTelemetry(transaction.staged, collectionMode);
    const config = replayOwnedConfig('replay', collectionMode, { traces: true, metrics: true, events: true });
    const hooks = createHooks(runtime, config, {});
    for (const { entry, file } of sourcedEntries) await dispatchTranscriptEntry(hooks, entry, file);
    await runtime.forceFlush();
    await runtime.shutdown();
    runtime = undefined;
    transaction.commit();
    return outputs;
  } catch (error) {
    const failures: unknown[] = [error];
    if (runtime) {
      try { await runtime.shutdown(); } catch (shutdownError) { failures.push(shutdownError); }
    }
    try { transaction.cleanup(); } catch (cleanupError) { failures.push(cleanupError); }
    if (failures.length > 1) {
      throw new AggregateError(failures, 'Replay failed and cleanup was incomplete');
    }
    throw error;
  } finally {
    resetHookState();
  }
}

async function main(): Promise<void> {
  initLogger();
  const { transcripts, output } = parseArgs(process.argv.slice(2));
  log(`Replay: ${transcripts.join(', ')} -> ${output}`);
  const outputs = await runReplay(transcripts, output);
  console.log(`Replay complete: ${outputs.spans}`);
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main().catch(error => {
    console.error(`Replay failed: ${error instanceof Error ? error.message : error}`);
    process.exitCode = 1;
  });
}
