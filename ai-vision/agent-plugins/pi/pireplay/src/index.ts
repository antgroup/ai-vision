import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent';
import type { PiHook } from '@observ/pi';
import { loadPireplayConfig } from './config.js';
import { createPiRawEnvelope, PI_RAW_REPLAY_GENESIS } from './raw-envelope.js';
import { TranscriptWriter } from './transcript-writer.js';
import { chmodSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

/**
 * pireplay — a developer-only pi extension that records raw callback envelopes and
 * provides offline replay.
 *
 * **Responsibility: transcript recording + replay only.** This extension
 * subscribes to the same lifecycle anchors that `piagent` consumes for
 * telemetry and writes checksum-chained raw callback envelopes
 * (one `session_*.jsonl` + `.integrity.json` seal per session), and exposes
 * a `pireplay-replay` CLI that converts a transcript back into AiVision OTLP
 * replay files. It does NOT emit real-time OTLP — load `piagent` alongside it
 * for live telemetry.
 *
 * When both piagent and pireplay are loaded, you get real-time OTLP (piagent)
 * AND a durable transcript (pireplay) from the same session, which is exactly
 * what the golden pipeline needs for realtime/replay parity.
 *
 * Env:
 *   PIREPLAY_CAPTURE_ENABLED=true  Explicitly enable developer capture
 *   PIREPLAY_SESSION_DIR=<dir>     Raw transcript output directory
 *                                  (default: ~/.aivision/pi/transcripts)
 *   PIREPLAY_SESSION_NAME=<label>  Transcript artifact label
 *   PIREPLAY_COLLECTION_MODE       full | privacy
 *   PIREPLAY_CAPABILITY_PROBE=<path>  Write tool/command capability probe on session_start
 */

const HOOKS: PiHook[] = [
  'session_start', 'session_shutdown', 'before_agent_start', 'agent_settled', 'turn_start',
  'before_provider_request', 'after_provider_response', 'provider_stream_event', 'message_update', 'message_end',
  'tool_execution_start', 'tool_execution_update', 'tool_execution_end',
  'session_before_compact', 'session_compact', 'model_select'
];

export default function pireplay(pi: ExtensionAPI): void {
  const config = loadPireplayConfig();
  if (!config.captureEnabled) return;
  const writer = new TranscriptWriter(config.sessionDir, config.sessionName);
  let turn = 0;
  let piTurn = 0;
  let sequence = 0;
  let previousChecksum = PI_RAW_REPLAY_GENESIS;
  let closed = false;

  for (const hook of HOOKS) {
    pi.on(hook as never, async (raw: unknown, _ctx: ExtensionContext) => {
      if (closed) return;
      try {
        // Capability probe: record tools and Pi-native slash commands once so
        // the golden superloop can classify optional subagent/mcp/skill scenarios.
        if (hook === 'session_start' && config.capabilityProbe) {
          const tools = pi.getAllTools().map(tool => ({ name: tool.name }));
          const commands = pi.getCommands().map(command => ({ name: command.name, source: command.source }));
          const probeDirectory = dirname(config.capabilityProbe);
          const createdProbeDirectory = mkdirSync(probeDirectory, { recursive: true, mode: 0o700 });
          if (createdProbeDirectory) chmodSync(probeDirectory, 0o700);
          writeFileSync(config.capabilityProbe, `${JSON.stringify({ tools, commands }, null, 2)}\n`, { mode: 0o600 });
        }
        if (hook === 'before_agent_start') { turn++; piTurn = 0; }
        if (hook === 'turn_start') piTurn = Number((raw as { turnIndex?: number }).turnIndex ?? piTurn);
        const rawObject = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
        const sessionManager = (_ctx as unknown as { sessionManager?: { getSessionId?: () => string } }).sessionManager;
        const envelope = createPiRawEnvelope({
          hook,
          collectionMode: config.collectionMode,
          sequence: ++sequence,
          previousChecksum,
          correlation: {
            ...(sessionManager?.getSessionId ? { sessionId: sessionManager.getSessionId() } : {}),
            userTurn: turn,
            piTurnIndex: piTurn,
            ...(Object.hasOwn(rawObject, 'toolCallId') ? { toolCallId: String(rawObject.toolCallId ?? '') } : {}),
          },
          input: raw,
          context: _ctx,
        });
        previousChecksum = envelope.checksum;
        writer.write(envelope);
      } catch (error) {
        console.warn(`[pireplay] ${hook} failed: ${error instanceof Error ? error.message : String(error)}`);
      }
      if (hook === 'session_shutdown') {
        closed = true;
        await writer.close();
      }
    });
  }
}

export { TranscriptWriter } from './transcript-writer.js';
export { loadPireplayConfig } from './config.js';
export type { PireplayConfig } from './config.js';
export { GOLDEN_SCENARIOS } from './golden/scenarios.js';
