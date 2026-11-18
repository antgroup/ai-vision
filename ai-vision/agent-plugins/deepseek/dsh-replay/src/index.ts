/**
 * dsh-replay public entry: offline reconstruction of AIVision OTLP signals
 * from the host-maintained dsh canonical session JSONL transcript.
 */

export { runReplay, deriveOutputPaths } from './replay.js';
export { decodeTranscriptFile, loadTranscript, sessionContext, sessionHash, toSessionLike } from './transcript.js';
export type { TranscriptBundle } from './transcript.js';
export { initReplayTelemetry, replayOwnedConfig, fileRuntimeExtensions } from './telemetry.js';
export type { FileOutputSet } from './telemetry.js';
export { emitToLocalCollector, REPLAY_OTLP_HTTP_BASE, localCollectorUrl } from './local-collector.js';
