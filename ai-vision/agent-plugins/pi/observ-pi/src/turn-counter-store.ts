import { createHash, randomUUID } from 'node:crypto';
import {
  chmodSync,
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';

const STATE_SCHEMA_VERSION = 2;
const LEGACY_STATE_SCHEMA_VERSION = 1;
const LOCK_STALE_MS = 30_000;

export interface EventSequenceReservation {
  startExclusive: number;
  endInclusive: number;
}

export interface TurnCounterStore {
  load(sessionId: string): number;
  save(sessionId: string, turnCounter: number): void;
  reserveEventSequence(
    sessionId: string,
    minimumExclusive: number,
    reservationSize: number,
  ): EventSequenceReservation | undefined;
}

interface PersistedSessionState {
  schemaVersion: typeof STATE_SCHEMA_VERSION;
  sessionId: string;
  turnCounter: number;
  eventSequenceHighWater: number;
  updatedAt: string;
}

interface RawPersistedSessionState {
  schemaVersion?: unknown;
  sessionId?: unknown;
  turnCounter?: unknown;
  eventSequenceHighWater?: unknown;
  updatedAt?: unknown;
}

export interface TurnStateDirectoryContext {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
}

export type TurnStateDiagnostic = (message: string) => void;

export function resolveTurnStateDirectory(
  context: TurnStateDirectoryContext = {},
): string {
  const env = context.env ?? process.env;
  const home = context.homeDir ?? homedir();
  const configured = env.OBSERV_PI_STATE_DIR?.trim();
  if (configured) return isAbsolute(configured) ? configured : join(home, configured);

  const aivisionHome = env.AIVISION_HOME?.trim();
  const root = aivisionHome
    ? (isAbsolute(aivisionHome) ? aivisionHome : join(home, aivisionHome))
    : join(home, '.aivision');
  return join(root, 'pi', 'state');
}

function stateFileName(sessionId: string): string {
  const digest = createHash('sha256').update(sessionId).digest('hex');
  return `session_${digest}.json`;
}

function validCounter(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function parseSessionState(raw: string, sessionId: string): PersistedSessionState | undefined {
  const state = JSON.parse(raw) as RawPersistedSessionState;
  if (state.sessionId !== sessionId
    || !validCounter(state.turnCounter)) {
    return undefined;
  }
  if (state.schemaVersion === LEGACY_STATE_SCHEMA_VERSION) {
    return {
      schemaVersion: STATE_SCHEMA_VERSION,
      sessionId,
      turnCounter: state.turnCounter,
      eventSequenceHighWater: 0,
      updatedAt: typeof state.updatedAt === 'string' ? state.updatedAt : new Date(0).toISOString(),
    };
  }
  if (state.schemaVersion !== STATE_SCHEMA_VERSION || !validCounter(state.eventSequenceHighWater)) {
    return undefined;
  }
  return {
    schemaVersion: STATE_SCHEMA_VERSION,
    sessionId,
    turnCounter: state.turnCounter,
    eventSequenceHighWater: state.eventSequenceHighWater,
    updatedAt: typeof state.updatedAt === 'string' ? state.updatedAt : new Date(0).toISOString(),
  };
}

export function createFileTurnCounterStore(
  directory: string = resolveTurnStateDirectory(),
  diagnostic: TurnStateDiagnostic = () => {},
): TurnCounterStore {
  const stateDirectory = isAbsolute(directory) ? directory : join(process.cwd(), directory);
  let directoryReady = false;

  const report = (message: string): void => {
    try { diagnostic(message); } catch { /* diagnostics must never break telemetry */ }
  };

  const ensureDirectory = (): void => {
    if (directoryReady) return;
    const created = mkdirSync(stateDirectory, { recursive: true, mode: 0o700 });
    if (created !== undefined) chmodSync(stateDirectory, 0o700);
    directoryReady = true;
  };
  const statePath = (sessionId: string): string => join(stateDirectory, stateFileName(sessionId));
  const lockPath = (sessionId: string): string => `${statePath(sessionId)}.lock`;

  const processIsAlive = (pid: number): boolean => {
    try {
      process.kill(pid, 0);
      return true;
    } catch (error) {
      return (error as NodeJS.ErrnoException).code === 'EPERM';
    }
  };

  const removeStaleLock = (sessionId: string): boolean => {
    const file = lockPath(sessionId);
    try {
      const lock = JSON.parse(readFileSync(file, 'utf8')) as { pid?: unknown; createdAt?: unknown };
      if (!Number.isSafeInteger(lock.pid) || typeof lock.createdAt !== 'string') return false;
      const pid = Number(lock.pid);
      if (processIsAlive(pid)) return false;
      rmSync(file, { force: true });
      return true;
    } catch {
      try {
        if (Date.now() - statSync(file).mtimeMs < LOCK_STALE_MS) return false;
        rmSync(file, { force: true });
        return true;
      } catch {
        return false;
      }
    }
  };

  const withSessionLock = <T>(sessionId: string, operation: () => T): T | undefined => {
    try {
      ensureDirectory();
      const file = lockPath(sessionId);
      const owner = randomUUID();
      let descriptor: number | undefined;
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          descriptor = openSync(file, 'wx', 0o600);
          writeFileSync(descriptor, JSON.stringify({ owner, pid: process.pid, createdAt: new Date().toISOString() }));
          break;
        } catch (error) {
          if (descriptor !== undefined) {
            try { closeSync(descriptor); } catch { /* cleanup is best-effort */ }
            descriptor = undefined;
            try { rmSync(file, { force: true }); } catch { /* cleanup is best-effort */ }
          }
          if ((error as NodeJS.ErrnoException).code !== 'EEXIST'
            || attempt > 0
            || !removeStaleLock(sessionId)) {
            const detail = error instanceof Error ? error.message : String(error);
            report(`failed to acquire session state lock: ${detail}`);
            return undefined;
          }
        }
      }
      if (descriptor === undefined) return undefined;
      try {
        return operation();
      } finally {
        try { closeSync(descriptor); } catch { /* cleanup is best-effort */ }
        try {
          const lock = JSON.parse(readFileSync(file, 'utf8')) as { owner?: unknown };
          if (lock.owner === owner) rmSync(file, { force: true });
        } catch (error) {
          const detail = error instanceof Error ? error.message : String(error);
          report(`failed to clean up session state lock: ${detail}`);
        }
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      report(`session state update failed: ${detail}`);
      return undefined;
    }
  };

  const readState = (sessionId: string): PersistedSessionState | undefined => {
    const file = statePath(sessionId);
    if (!existsSync(file)) return undefined;
    try {
      const state = parseSessionState(readFileSync(file, 'utf8'), sessionId);
      if (state) return state;
      report('ignoring invalid persisted turn state');
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      report(`failed to read persisted turn state: ${detail}`);
    }
    return undefined;
  };

  const persist = (state: PersistedSessionState): boolean => {
    let temporaryPath: string | undefined;
    try {
      ensureDirectory();
      temporaryPath = join(stateDirectory, `.${stateFileName(state.sessionId)}.${process.pid}.${randomUUID()}.tmp`);
      writeFileSync(temporaryPath, `${JSON.stringify(state)}\n`, {
        encoding: 'utf8',
        mode: 0o600,
        flag: 'wx',
      });
      renameSync(temporaryPath, statePath(state.sessionId));
      temporaryPath = undefined;
      try {
        chmodSync(statePath(state.sessionId), 0o600);
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        report(`failed to enforce session state permissions after persistence: ${detail}`);
      }
      return true;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      report(`failed to persist session state: ${detail}`);
      return false;
    } finally {
      if (temporaryPath) {
        try { rmSync(temporaryPath, { force: true }); }
        catch (error) {
          const detail = error instanceof Error ? error.message : String(error);
          report(`failed to clean up temporary session state: ${detail}`);
        }
      }
    }
  };

  const load = (sessionId: string): number => readState(sessionId)?.turnCounter ?? 0;

  const save = (sessionId: string, nextTurnCounter: number): void => {
    if (!validCounter(nextTurnCounter)) {
      report('ignoring invalid turn counter');
      return;
    }
    try {
      withSessionLock(sessionId, () => {
        const previous = readState(sessionId);
        persist({
          schemaVersion: STATE_SCHEMA_VERSION,
          sessionId,
          turnCounter: Math.max(previous?.turnCounter ?? 0, nextTurnCounter),
          eventSequenceHighWater: previous?.eventSequenceHighWater ?? 0,
          updatedAt: new Date().toISOString(),
        });
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      report(`failed to persist turn counter: ${detail}`);
    }
  };

  const reserveEventSequence = (
    sessionId: string,
    minimumExclusive: number,
    reservationSize: number,
  ): EventSequenceReservation | undefined => {
    if (!validCounter(minimumExclusive)
      || !Number.isSafeInteger(reservationSize)
      || reservationSize <= 0) {
      report('ignoring invalid event sequence reservation');
      return undefined;
    }
    try {
      return withSessionLock(sessionId, () => {
        const previous = readState(sessionId);
        const startExclusive = Math.max(previous?.eventSequenceHighWater ?? 0, minimumExclusive);
        const endInclusive = startExclusive + reservationSize;
        if (!Number.isSafeInteger(endInclusive)) {
          report('event sequence reservation exceeds the safe integer range');
          return undefined;
        }
        const saved = persist({
          schemaVersion: STATE_SCHEMA_VERSION,
          sessionId,
          turnCounter: previous?.turnCounter ?? 0,
          eventSequenceHighWater: endInclusive,
          updatedAt: new Date().toISOString(),
        });
        return saved ? { startExclusive, endInclusive } : undefined;
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      report(`failed to reserve event sequence: ${detail}`);
      return undefined;
    }
  };

  return { load, save, reserveEventSequence };
}
