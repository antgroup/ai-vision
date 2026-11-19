import { promises as fs } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import type { CanonicalEvent, CollectionIntegrity } from '@observ/pi';
import type { PiRawCallbackEnvelopeV2 } from './raw-envelope.js';

type TranscriptRecord = CanonicalEvent | PiRawCallbackEnvelopeV2;

function safeArtifactPart(value: string): string {
  const part = value.replace(/[^A-Za-z0-9._-]+/g, '_');
  let start = 0;
  let end = part.length;
  while (part[start] === '_' || part[start] === '.') start++;
  while (end > start && (part[end - 1] === '_' || part[end - 1] === '.')) end--;
  return part.slice(start, Math.min(end, start + 80)) || 'session';
}

function sequenceOf(record: TranscriptRecord): number { return record.sequence; }
function checksumOf(record: TranscriptRecord): string { return record.checksum; }
function previousChecksumOf(record: TranscriptRecord): string {
  return 'previousChecksum' in record ? record.previousChecksum : record.previous_checksum;
}
function sessionIdOf(record: TranscriptRecord): string {
  return 'schemaVersion' in record ? record.correlation.sessionId || 'unknown' : record.session_id;
}

export class TranscriptWriter {
  readonly integrity: CollectionIntegrity = { complete: true, lastSequence: 0, lastChecksum: '0'.repeat(64), expectedSequence: 0, expectedChecksum: '0'.repeat(64), errors: [] };
  private file?: string;
  private pending = Promise.resolve();
  private queued = 0;
  private acceptedSequence = 0;
  private acceptedChecksum = '0'.repeat(64);
  private directoryReady = false;

  constructor(private readonly dir?: string, private readonly name?: string, private readonly maxQueue = 4096) {}

  write(record: TranscriptRecord): void {
    if (!this.dir) return;
    const sequence = sequenceOf(record);
    const checksum = checksumOf(record);
    this.integrity.expectedSequence = sequence;
    this.integrity.expectedChecksum = checksum;
    if (!this.file) {
      const sessionId = sessionIdOf(record);
      const context = safeArtifactPart(this.name || sessionId);
      const hash = createHash('sha256').update(sessionId).digest('hex').slice(0, 12);
      this.file = join(this.dir, `session_${context}_${hash}.jsonl`);
    }
    if (sequence !== this.acceptedSequence + 1 || previousChecksumOf(record) !== this.acceptedChecksum) {
      this.fail(`continuity gap at sequence ${sequence}`);
      return;
    }
    if (this.queued >= this.maxQueue) {
      this.fail(`transcript queue exceeded ${this.maxQueue} records at sequence ${sequence}`);
      return;
    }
    this.acceptedSequence = sequence;
    this.acceptedChecksum = checksum;
    this.queued++;
    const file = this.file;
    this.pending = this.pending.then(async () => {
      if (!this.directoryReady) {
        const created = await fs.mkdir(this.dir!, { recursive: true, mode: 0o700 });
        if (created) await fs.chmod(this.dir!, 0o700);
        this.directoryReady = true;
      }
      await fs.appendFile(file, `${JSON.stringify(record)}\n`, { encoding: 'utf8', mode: 0o600 });
      this.integrity.lastSequence = sequence;
      this.integrity.lastChecksum = checksum;
    }).catch(error => this.fail(error instanceof Error ? error.message : String(error))).finally(() => { this.queued--; });
  }

  async close(): Promise<CollectionIntegrity> {
    await this.pending;
    if (this.integrity.lastSequence !== this.integrity.expectedSequence || this.integrity.lastChecksum !== this.integrity.expectedChecksum) {
      this.fail(`persisted transcript ended at ${this.integrity.lastSequence}, expected ${this.integrity.expectedSequence}`);
    }
    if (this.file) {
      const manifest = this.integrityPath()!;
      const temporary = `${manifest}.tmp-${process.pid}`;
      try {
        await fs.writeFile(temporary, `${JSON.stringify(this.integrity)}\n`, { encoding: 'utf8', mode: 0o600 });
        await fs.rename(temporary, manifest);
      } catch (error) {
        this.fail(`integrity manifest: ${error instanceof Error ? error.message : String(error)}`);
        await fs.rm(temporary, { force: true }).catch(() => undefined);
      }
    }
    return this.integrity;
  }

  path(): string | undefined { return this.file; }
  integrityPath(): string | undefined { return this.file ? `${this.file}.integrity.json` : undefined; }
  private fail(message: string): void { this.integrity.complete = false; this.integrity.errors.push(message); }
}
