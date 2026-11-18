#!/usr/bin/env node
import { chmodSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve, dirname, basename, join } from 'node:path';
import {
  CanonicalNormalizer,
  parseConfig,
  SemanticEngine,
  createTelemetry,
  canonicalChecksum,
  type CanonicalEvent,
  type CollectionIntegrity,
  type Correlation,
  type PiHook,
} from '@observ/pi';
import { decodePiReplayRecord, PI_RAW_REPLAY_GENESIS, type PiRawCallbackEnvelopeV2 } from './raw-envelope.js';

function replayConfig(collectionMode: 'full' | 'privacy') {
  const config = parseConfig({
    enableTelemetry: true,
    serviceName: 'pireplay',
    collectionMode,
    otelExporter: {
      endpoint: 'http://127.0.0.1:4319',
      headers: {},
      attributes: { 'ant.username': 'replay' },
      resourceAttributes: { 'ant.agent.instance': 'replay' },
    },
    tracesConfig: { enabled: false, headers: {} },
    metricsConfig: { enabled: false, headers: {} },
    eventsConfig: { enabled: false, headers: {} },
  }, { env: {} });
  config.otelExporter.headers = {};
  config.tracesConfig.headers = {};
  config.metricsConfig.headers = {};
  config.eventsConfig.headers = {};
  return config;
}

function normalizedContext(envelope: PiRawCallbackEnvelopeV2): unknown {
  const context = envelope.context && typeof envelope.context === 'object'
    ? envelope.context as Record<string, unknown>
    : {};
  const sessionId = envelope.correlation.sessionId;
  return sessionId
    ? { ...context, sessionManager: { getSessionId: () => sessionId } }
    : context;
}

function normalizeRaw(envelopes: PiRawCallbackEnvelopeV2[]): CanonicalEvent[] {
  const mode = envelopes.some((envelope) => envelope.collectionMode === 'privacy') ? 'privacy' : 'full';
  const normalizer = new CanonicalNormalizer(mode);
  return envelopes.map((envelope) => {
    const correlation: Correlation = {
      user_turn: envelope.correlation.userTurn,
      pi_turn_index: envelope.correlation.piTurnIndex,
      ...(envelope.correlation.toolCallId ? { tool_call_id: envelope.correlation.toolCallId } : {}),
    };
    return normalizer.normalize(
      envelope.hook as PiHook,
      envelope.input,
      normalizedContext(envelope),
      correlation,
      envelope.timestamp,
    );
  });
}

export async function replay(input: string, output: string): Promise<void> {
  const spans = resolve(output);
  const suffix = basename(spans).replace(/^spans_/, '').replace(/\.jsonl$/, '');
  const files = { spans, events: join(dirname(spans), `events_${suffix}.jsonl`), metrics: join(dirname(spans), `metrics_${suffix}.jsonl`) };
  const outputDirectory = dirname(spans);
  const createdDirectory = mkdirSync(outputDirectory, { recursive: true, mode: 0o700 });
  if (createdDirectory) {
    // mkdir's mode is affected by umask; apply the private mode only to a directory we created.
    chmodSync(outputDirectory, 0o700);
  }
  for (const target of [files.spans, files.events, files.metrics]) {
    writeFileSync(target, '', { mode: 0o600 });
  }

  const content = readFileSync(input, 'utf8');
  if (content && !content.endsWith('\n')) throw new Error('partial final transcript line');
  const decoded = content.split('\n').filter(Boolean).map((line) => decodePiReplayRecord(JSON.parse(line)));
  const formats = new Set(decoded.map((record) => record.format));
  if (formats.size > 1) throw new Error('mixed raw-v2 and canonical-v1 transcript formats are not supported');

  let previous = PI_RAW_REPLAY_GENESIS;
  let sequence = 0;
  let collectionMode: 'full' | 'privacy' = 'full';
  let events: CanonicalEvent[];
  if (formats.has('raw-v2')) {
    const envelopes = decoded.map((record) => {
      if (record.format !== 'raw-v2') throw new Error('mixed transcript formats');
      const envelope = record.envelope;
      if (envelope.sequence !== sequence + 1 || envelope.previousChecksum !== previous) {
        throw new Error(`transcript continuity failure at ${envelope.sequence}`);
      }
      sequence = envelope.sequence;
      previous = envelope.checksum;
      return envelope;
    });
    collectionMode = envelopes.some((envelope) => envelope.collectionMode === 'privacy') ? 'privacy' : 'full';
    events = normalizeRaw(envelopes);
  } else {
    events = decoded.map((record) => {
      if (record.format !== 'canonical-v1') throw new Error('mixed transcript formats');
      const event = record.event as unknown as CanonicalEvent;
      if (event.sequence !== sequence + 1 || event.previous_checksum !== previous) {
        throw new Error(`transcript continuity failure at ${event.sequence}`);
      }
      const { checksum, ...base } = event;
      if (checksum !== canonicalChecksum(base)) throw new Error(`transcript checksum failure at ${event.sequence}`);
      sequence = event.sequence;
      previous = event.checksum;
      return event;
    });
  }

  let integrity: CollectionIntegrity;
  try { integrity = JSON.parse(readFileSync(`${input}.integrity.json`, 'utf8')) as CollectionIntegrity; }
  catch { throw new Error('transcript integrity manifest is missing or invalid'); }
  if (!integrity.complete || integrity.lastSequence !== sequence || integrity.lastChecksum !== previous || integrity.expectedSequence !== sequence || integrity.expectedChecksum !== previous) {
    throw new Error('transcript integrity manifest does not match transcript');
  }

  const config = replayConfig(collectionMode);
  const telemetry = createTelemetry(config, files, 'file-only');
  const engine = new SemanticEngine(config, observation => telemetry.consume(observation));
  for (const event of events) engine.apply(event);
  await telemetry.shutdown();
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [input, flag, output] = process.argv.slice(2);
  if (!input || flag !== '--otel' || !output) { console.error('Usage: pireplay-replay <transcript.jsonl> --otel <spans.jsonl>'); process.exit(2); }
  replay(input, output).catch(error => { console.error(error); process.exit(1); });
}
