import { createHash, randomUUID } from 'node:crypto';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { logWarn } from './logger.js';

const STATE_SCHEMA_VERSION = 1;

export interface PersistedTurnState {
  turnCounter: number;
  lastUserMessageId: string;
}

export interface TurnCounterStore {
  load(sessionId: string): PersistedTurnState;
  save(sessionId: string, state: PersistedTurnState): void;
  remove(sessionId: string): void;
}

interface PersistedTurnCounter {
  schemaVersion: typeof STATE_SCHEMA_VERSION;
  sessionId: string;
  turnCounter: number;
  lastUserMessageId: string;
  updatedAt: string;
}

export interface TurnStateDirectoryContext {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
}

export function resolveTurnStateDirectory(
  context: TurnStateDirectoryContext = {},
): string {
  const env = context.env ?? process.env;
  const home = context.homeDir ?? homedir();
  const configured = env.OBSERV_OPENCODE_STATE_DIR?.trim();
  if (configured) return isAbsolute(configured) ? configured : join(home, configured);

  const aivisionHome = env.AIVISION_HOME?.trim();
  const root = aivisionHome
    ? (isAbsolute(aivisionHome) ? aivisionHome : join(home, aivisionHome))
    : join(home, '.aivision');
  return join(root, 'ocd', 'state');
}

function stateFileName(sessionId: string): string {
  const digest = createHash('sha256').update(sessionId).digest('hex');
  return `session_${digest}.json`;
}

const EMPTY_TURN_STATE: PersistedTurnState = {
  turnCounter: 0,
  lastUserMessageId: '',
};

function parsePersistedTurnCounter(raw: string, sessionId: string): PersistedTurnState | undefined {
  const state = JSON.parse(raw) as Partial<PersistedTurnCounter>;
  const turnCounter = state.turnCounter;
  if (state.schemaVersion !== STATE_SCHEMA_VERSION
    || state.sessionId !== sessionId
    || !Number.isSafeInteger(turnCounter)
    || turnCounter === undefined
    || turnCounter < 0
    || typeof state.lastUserMessageId !== 'string') {
    return undefined;
  }
  return {
    turnCounter,
    lastUserMessageId: state.lastUserMessageId,
  };
}

export function createFileTurnCounterStore(
  directory: string = resolveTurnStateDirectory(),
): TurnCounterStore {
  const stateDirectory = isAbsolute(directory) ? directory : join(process.cwd(), directory);
  let directoryReady = false;

  const ensureDirectory = (): void => {
    if (directoryReady) return;
    mkdirSync(stateDirectory, { recursive: true, mode: 0o700 });
    chmodSync(stateDirectory, 0o700);
    directoryReady = true;
  };

  const statePath = (sessionId: string): string => join(stateDirectory, stateFileName(sessionId));

  const load = (sessionId: string): PersistedTurnState => {
    const file = statePath(sessionId);
    if (!existsSync(file)) return { ...EMPTY_TURN_STATE };
    try {
      const state = parsePersistedTurnCounter(readFileSync(file, 'utf8'), sessionId);
      if (state !== undefined) return state;
      logWarn(`Ignoring invalid persisted turn state for session ${sessionId}`);
    } catch (error) {
      logWarn(`Failed to read persisted turn state for session ${sessionId}`, error);
    }
    return { ...EMPTY_TURN_STATE };
  };

  const save = (sessionId: string, nextState: PersistedTurnState): void => {
    if (!Number.isSafeInteger(nextState.turnCounter) || nextState.turnCounter < 0
      || typeof nextState.lastUserMessageId !== 'string') {
      logWarn(`Ignoring invalid turn state for session ${sessionId}`);
      return;
    }

    let temporaryPath: string | undefined;
    try {
      ensureDirectory();
      const existing = load(sessionId);
      const highWaterState = existing.turnCounter >= nextState.turnCounter ? existing : nextState;
      const state: PersistedTurnCounter = {
        schemaVersion: STATE_SCHEMA_VERSION,
        sessionId,
        turnCounter: highWaterState.turnCounter,
        lastUserMessageId: highWaterState.lastUserMessageId,
        updatedAt: new Date().toISOString(),
      };
      temporaryPath = join(stateDirectory, `.${stateFileName(sessionId)}.${process.pid}.${randomUUID()}.tmp`);
      writeFileSync(temporaryPath, `${JSON.stringify(state)}\n`, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
      renameSync(temporaryPath, statePath(sessionId));
      temporaryPath = undefined;
      chmodSync(statePath(sessionId), 0o600);
    } catch (error) {
      logWarn(`Failed to persist turn counter for session ${sessionId}`, error);
    } finally {
      if (temporaryPath) rmSync(temporaryPath, { force: true });
    }
  };

  const remove = (sessionId: string): void => {
    try {
      rmSync(statePath(sessionId), { force: true });
    } catch (error) {
      logWarn(`Failed to remove persisted turn state for session ${sessionId}`, error);
    }
  };

  return { load, save, remove };
}
