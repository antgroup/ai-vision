import { TRANSCRIPT_ONLY_HOOKS } from './decoder.js';
import { log } from './logger.js';
import type { TranscriptRecorder } from './transcript.js';

type Hook = (...args: unknown[]) => unknown;

const CANONICAL_RUNTIME_HOOKS = [
  'event',
  'tool.execute.before',
  'tool.execute.after',
  'command.execute.before',
  'experimental.chat.system.transform',
  'experimental.session.compacting',
] as const;

function recordableSession(hookName: string, input: unknown, output: unknown): string | undefined {
  const inputRecord = input && typeof input === 'object' ? input as Record<string, unknown> : {};
  if (hookName === 'event') {
    const event = inputRecord.event && typeof inputRecord.event === 'object'
      ? inputRecord.event as Record<string, unknown> : {};
    const properties = event.properties && typeof event.properties === 'object'
      ? event.properties as Record<string, unknown> : {};
    const info = properties.info && typeof properties.info === 'object'
      ? properties.info as Record<string, unknown> : {};
    return [properties.sessionID, info.sessionID, info.id].find(value => typeof value === 'string') as string | undefined;
  }
  if (hookName === 'experimental.chat.messages.transform') {
    const outputRecord = output && typeof output === 'object' ? output as Record<string, unknown> : {};
    const messages = Array.isArray(outputRecord.messages) ? outputRecord.messages : [];
    const first = messages[0] && typeof messages[0] === 'object' ? messages[0] as Record<string, unknown> : {};
    const info = first.info && typeof first.info === 'object' ? first.info as Record<string, unknown> : {};
    return typeof info.sessionID === 'string' ? info.sessionID : undefined;
  }
  return typeof inputRecord.sessionID === 'string' ? inputRecord.sessionID : undefined;
}

function transcriptHookName(hookName: string, input: unknown): string {
  if (hookName !== 'event') return hookName;
  const inputRecord = input && typeof input === 'object' ? input as Record<string, unknown> : {};
  const event = inputRecord.event && typeof inputRecord.event === 'object'
    ? inputRecord.event as Record<string, unknown> : {};
  return `event.${String(event.type ?? 'unknown')}`;
}

function record(
  recorder: TranscriptRecorder,
  hookName: string,
  input: unknown,
  output: unknown,
): void {
  const session = recordableSession(hookName, input, output);
  if (!session) {
    log(`Transcript not persisted for sessionless hook ${hookName}`);
    return;
  }
  const transcriptInput = hookName === 'event' && input && typeof input === 'object'
    ? (input as Record<string, unknown>).event
    : input;
  recorder(session, transcriptHookName(hookName, input), transcriptInput, output);
}

export function decorateHooksWithTranscript(
  canonicalHooks: Record<string, unknown>,
  recorder: TranscriptRecorder | undefined,
): Record<string, unknown> {
  if (!recorder) return canonicalHooks;

  const decorated: Record<string, unknown> = {};
  for (const [hookName, candidate] of Object.entries(canonicalHooks)) {
    if (typeof candidate !== 'function') {
      decorated[hookName] = candidate;
      continue;
    }
    const canonical = candidate as Hook;
    decorated[hookName] = async (...args: unknown[]) => {
      record(recorder, hookName, args[0], args[1]);
      return canonical(...args);
    };
  }

  for (const hookName of TRANSCRIPT_ONLY_HOOKS) {
    decorated[hookName] = async (input: unknown, output: unknown) => {
      record(recorder, hookName, input, output);
    };
  }
  return decorated;
}

/**
 * Build a transcript-only OpenCode hook surface for coexistence with the
 * production observ-opencode plugin. No semantic engine or OTel provider is
 * created here; offline replay feeds these records back through the shared
 * observ-opencode/core implementation.
 */
export function createTranscriptHooks(
  recorder: TranscriptRecorder | undefined,
): Record<string, unknown> {
  if (!recorder) return {};

  const hookNames = new Set<string>([
    ...CANONICAL_RUNTIME_HOOKS,
    ...[...TRANSCRIPT_ONLY_HOOKS].filter(name => !name.startsWith('event.')),
  ]);
  return Object.fromEntries([...hookNames].map(hookName => [
    hookName,
    async (input: unknown, output: unknown) => {
      record(recorder, hookName, input, output);
    },
  ]));
}
