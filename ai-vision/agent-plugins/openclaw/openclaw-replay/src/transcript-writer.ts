import { appendFileSync, chmodSync, mkdirSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { createOpenClawRawEnvelope, type OpenClawRawCallbackEnvelopeV2 } from './raw-envelope.js';

export type TranscriptRecord = OpenClawRawCallbackEnvelopeV2;

interface WriterOptions {
  sessionDir?: string;
  sessionName?: string;
  collectionMode?: 'full' | 'privacy';
  stdout: boolean;
  warn: (message: string) => void;
  now?: () => Date;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(...values: unknown[]): string | undefined {
  return values.find((value): value is string => typeof value === 'string' && value.length > 0);
}

function safePart(value: string): string {
  const part = value.replace(/[^a-zA-Z0-9._-]+/g, '_');
  let start = 0;
  let end = part.length;
  while (part[start] === '_') start++;
  while (end > start && part[end - 1] === '_') end--;
  return part.slice(start, Math.min(end, start + 120)) || 'unknown';
}

export class SessionTranscriptWriter {
  private readonly childToRoot = new Map<string, string>();
  private readonly filesByRoot = new Map<string, { path: string; token: string; sessionId?: string }>();
  private directoryReady = false;

  constructor(private readonly options: WriterOptions) {}

  record(hook: string, event: unknown, ctx: unknown, instanceId?: string): void {
    try {
      const e = objectValue(event);
      const c = objectValue(ctx);
      const parentSessionKey = stringValue(c.requesterSessionKey, e.requesterSessionKey);
      const childSessionKey = stringValue(c.childSessionKey, e.childSessionKey, e.targetSessionKey);
      const sessionId = stringValue(c.sessionId, e.sessionId);
      const directSessionKey = stringValue(c.sessionKey, e.sessionKey, sessionId);
      const runId = stringValue(c.runId, e.runId);

      if (parentSessionKey && childSessionKey) this.childToRoot.set(childSessionKey, this.resolveRoot(parentSessionKey));
      const rootSessionKey = this.resolveRoot(parentSessionKey || directSessionKey || childSessionKey || 'unknown');
      const record = createOpenClawRawEnvelope({
        hook,
        collectionMode: this.options.collectionMode,
        correlation: {
          sessionKey: rootSessionKey,
          ...(parentSessionKey ? { parentSessionKey } : {}),
          ...(childSessionKey ? { childSessionKey } : {}),
          ...(sessionId ? { sessionId } : {}),
          ...(runId ? { runId } : {}),
          ...(instanceId ? { instanceId } : {}),
        },
        input: event,
        context: ctx,
        timestamp: (this.options.now ?? (() => new Date()))().toISOString(),
      });
      const line = JSON.stringify(record) + '\n';
      if (this.options.stdout) process.stdout.write(line);
      if (!this.options.sessionDir) return;

      this.ensureDirectory();
      const file = this.resolveFile(rootSessionKey, sessionId);
      appendFileSync(file, line, { mode: 0o600 });
    } catch (error) {
      this.options.warn('[openclaw-replay] transcript write failed for ' + hook + ': '
        + (error instanceof Error ? error.message : String(error)));
    }
  }

  private resolveRoot(sessionKey: string): string {
    let current = sessionKey;
    const visited = new Set<string>();
    while (this.childToRoot.has(current) && !visited.has(current)) {
      visited.add(current);
      current = this.childToRoot.get(current)!;
    }
    return current;
  }

  private ensureDirectory(): void {
    if (this.directoryReady || !this.options.sessionDir) return;
    const created = mkdirSync(this.options.sessionDir, { recursive: true, mode: 0o700 });
    if (created !== undefined) chmodSync(this.options.sessionDir, 0o700);
    this.directoryReady = true;
  }

  private resolveFile(rootSessionKey: string, sessionId?: string): string {
    const fixedName = this.options.sessionName?.trim();
    if (fixedName) {
      const base = fixedName.toLowerCase().endsWith('.jsonl') ? fixedName.slice(0, -6) : fixedName;
      return join(this.options.sessionDir!, fixedName.toLowerCase().endsWith('.jsonl')
        ? safePart(base) + '.jsonl'
        : 'session_' + safePart(base) + '.jsonl');
    }
    const existing = this.filesByRoot.get(rootSessionKey);
    if (existing) {
      if (sessionId && !existing.sessionId) {
        const nextPath = join(this.options.sessionDir!, 'session_' + safePart(sessionId) + '_' + existing.token + '.jsonl');
        renameSync(existing.path, nextPath);
        existing.path = nextPath;
        existing.sessionId = sessionId;
      }
      return existing.path;
    }
    const token = Date.now().toString(36);
    const file = join(this.options.sessionDir!, 'session_' + safePart(sessionId || rootSessionKey) + '_' + token + '.jsonl');
    this.filesByRoot.set(rootSessionKey, { path: file, token, ...(sessionId ? { sessionId } : {}) });
    return file;
  }
}
