import { existsSync, renameSync, rmSync } from 'node:fs';

export interface OutputPaths {
  spans: string;
  events: string;
  metrics: string;
}

export interface TransactionFs {
  exists(path: string): boolean;
  rename(from: string, to: string): void;
  remove(path: string): void;
}

const defaultFs: TransactionFs = {
  exists: existsSync,
  rename: renameSync,
  remove: path => rmSync(path, { force: true }),
};

export class OutputTransaction {
  readonly staged: OutputPaths;
  private readonly backups: OutputPaths;
  private readonly fs: TransactionFs;

  constructor(
    readonly final: OutputPaths,
    options: { nonce?: string; fs?: TransactionFs } = {},
  ) {
    const nonce = options.nonce ?? `${process.pid}-${Date.now()}`;
    this.fs = options.fs ?? defaultFs;
    this.staged = {
      spans: `${final.spans}.tmp-${nonce}`,
      events: `${final.events}.tmp-${nonce}`,
      metrics: `${final.metrics}.tmp-${nonce}`,
    };
    this.backups = {
      spans: `${final.spans}.bak-${nonce}`,
      events: `${final.events}.bak-${nonce}`,
      metrics: `${final.metrics}.bak-${nonce}`,
    };
  }

  cleanup(): void {
    for (const path of [...Object.values(this.staged), ...Object.values(this.backups)]) {
      this.fs.remove(path);
    }
  }

  commit(): void {
    const keys = ['spans', 'events', 'metrics'] as const;
    const backedUp: typeof keys[number][] = [];
    const promoted: typeof keys[number][] = [];

    try {
      for (const key of keys) {
        if (this.fs.exists(this.final[key])) {
          this.fs.rename(this.final[key], this.backups[key]);
          backedUp.push(key);
        }
      }
      for (const key of keys) {
        if (!this.fs.exists(this.staged[key])) continue;
        this.fs.rename(this.staged[key], this.final[key]);
        promoted.push(key);
      }
      for (const key of backedUp) this.fs.remove(this.backups[key]);
    } catch (error) {
      const rollbackErrors: unknown[] = [];
      for (const key of [...promoted].reverse()) {
        try { this.fs.remove(this.final[key]); } catch (rollbackError) { rollbackErrors.push(rollbackError); }
      }
      for (const key of [...backedUp].reverse()) {
        try {
          if (this.fs.exists(this.backups[key])) this.fs.rename(this.backups[key], this.final[key]);
        } catch (rollbackError) {
          rollbackErrors.push(rollbackError);
        }
      }
      this.cleanup();
      if (rollbackErrors.length > 0) {
        throw new AggregateError([error, ...rollbackErrors], 'Replay output commit and rollback failed');
      }
      throw error;
    } finally {
      this.cleanup();
    }
  }
}
