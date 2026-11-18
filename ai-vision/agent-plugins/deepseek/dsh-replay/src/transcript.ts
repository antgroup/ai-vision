/**
 * Read the dsh canonical session JSONL transcript (plain or zstd).
 *
 * The transcript is host-maintained by dsh-session-persistence-jsonl; the
 * replay tool only reads it. First line is `{"type":"session",...}` (the
 * durable SessionHeader), following lines are SessionEvent records.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { releasedV0SessionFormatCodec, releasedV1SessionFormatCodec } from '@deepseek-ai/dsh-session-format-v0-to-v1';
import { releasedV2SessionFormatCodec } from '@deepseek-ai/dsh-session-format-v1-to-v2';
import { releasedV3SessionFormatCodec } from '@deepseek-ai/dsh-session-format-v2-to-v3';
import { releasedV4SessionFormatCodec } from '@deepseek-ai/dsh-session-format-v3-to-v4';
import { basename, join, resolve } from 'node:path';
import { decompress } from 'fzstd';
import type { DshSessionEvent, DshSessionHeader, DshSessionLike } from 'observ-dsh/core';

export interface TranscriptBundle {
  header: DshSessionHeader;
  events: DshSessionEvent[];
  sourceFile: string;
}

function isSessionFile(path: string): boolean {
  const name = basename(path);
  return name === 'session.jsonl' || name === 'session.jsonl.zstd';
}

function decodeBytes(buffer: Buffer, path: string): string {
  if (path.endsWith('.zstd')) {
    const plain = decompress(new Uint8Array(buffer));
    return Buffer.from(plain).toString('utf8');
  }
  return buffer.toString('utf8');
}

function parseHeaderLine(line: string, sourceFile: string): DshSessionHeader {
  const parsed = JSON.parse(line) as Record<string, unknown>;
  if (parsed.type !== 'session') {
    throw new Error(`${sourceFile}: first line must be a session header (type:"session")`);
  }
  return {
    version: typeof parsed.version === 'number' ? parsed.version : 0,
    id: String(parsed.id ?? ''),
    createdAt: typeof parsed.createdAt === 'number' ? parsed.createdAt : 0,
    ...(typeof parsed.cwd === 'string' ? { cwd: parsed.cwd } : {}),
    ...(typeof parsed.parentSession === 'string' ? { parentSession: parsed.parentSession } : {}),
    ...(typeof parsed.seedLength === 'number' ? { seedLength: parsed.seedLength } : {}),
    ...(parsed.origin === 'subagent' ? { origin: 'subagent' as const } : {}),
    ...(typeof parsed.delegationDepth === 'number' ? { delegationDepth: parsed.delegationDepth } : {}),
    ...(typeof parsed.agentPreset === 'string' ? { agentPreset: parsed.agentPreset } : {}),
  };
}

export function decodeTranscriptFile(path: string): TranscriptBundle {
  const absolute = resolve(path);
  const buffer = readFileSync(absolute);
  const text = decodeBytes(buffer, absolute);
  const lines = text.split('\n').filter(line => line.trim() !== '');
  if (lines.length === 0) throw new Error(`${absolute}: empty transcript`);
  const physicalHeader = JSON.parse(lines[0]!) as Record<string, unknown>;
  const header = parseHeaderLine(lines[0]!, absolute);
  const codec = [releasedV0SessionFormatCodec, releasedV1SessionFormatCodec,
    releasedV2SessionFormatCodec, releasedV3SessionFormatCodec, releasedV4SessionFormatCodec]
    .find(candidate => candidate.version === header.version);
  if (!codec) throw new Error(`${absolute}: unsupported session format v${header.version}`);
  const decoder = codec.createDecoder(physicalHeader, 'strict');
  const events: DshSessionEvent[] = [];
  const sink = {
    emitEvent: (event: unknown) => { events.push(event as DshSessionEvent); },
    emitRun: (run: { expand(): Iterable<unknown> }) => {
      for (const event of run.expand()) events.push(event as DshSessionEvent);
    },
  };
  for (let index = 1; index < lines.length; index++) {
    // Released physical codecs expand packed legacy chunks and retain the
    // embedded assistant streams of v2/v4 without mutating host artifacts.
    decoder.decodeRow(JSON.parse(lines[index]!), sink);
  }
  const inheritedEventCount = decoder.finish(sink);
  if (physicalHeader.isSeeded === true) header.seedLength = inheritedEventCount;
  return { header, events, sourceFile: absolute };
}

function collectSessionFiles(input: string): string[] {
  const absolute = resolve(input);
  const stat = statSync(absolute);
  if (stat.isFile()) {
    if (!isSessionFile(absolute)) throw new Error(`${absolute}: expected session.jsonl or session.jsonl.zstd`);
    return [absolute];
  }
  if (!stat.isDirectory()) throw new Error(`${absolute}: not a file or directory`);
  const found: string[] = [];
  const walk = (dir: string, depth: number): void => {
    if (depth > 6) return;
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'session.jsonl' || entry.name === 'session.jsonl.zstd') continue;
        walk(path, depth + 1);
      } else if (isSessionFile(path)) {
        found.push(path);
      }
    }
  };
  walk(absolute, 0);
  found.sort();
  return found;
}

export function loadTranscript(input: string): TranscriptBundle {
  const files = collectSessionFiles(input);
  if (files.length === 0) throw new Error(`${input}: no session.jsonl(.zstd) transcript found`);
  if (files.length > 1) throw new Error(`${input}: multiple session transcripts found; point to one session directory`);
  return decodeTranscriptFile(files[0]!);
}

/** Build the structural session object the shared semantic engine consumes. */
export function toSessionLike(bundle: TranscriptBundle): DshSessionLike & { readonly events: readonly DshSessionEvent[] } {
  return {
    id: bundle.header.id,
    header: bundle.header,
    events: bundle.events,
    firstLiveSeq: 0,
  };
}

export function sessionContext(session: DshSessionLike): string {
  const cwd = session.header.cwd ?? 'no-cwd';
  return `${cwd}--${session.id}`.replace(/[^a-zA-Z0-9._-]+/g, '-').slice(0, 220);
}

export function sessionHash(session: DshSessionLike): string {
  // Stable identity hash for replay artifacts.
  return createHash('sha256').update(session.id).digest('hex').slice(0, 12);
}
