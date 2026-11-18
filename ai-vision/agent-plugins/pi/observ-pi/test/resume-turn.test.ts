import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent';
import observPi from '../src/index.js';
import type { SemanticObservation } from '../src/types.js';

test('a resumed session continues the persisted user Turn number', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-resume-turn-'));
  const agentDir = join(root, 'agent');
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, 'observ-pi.json'), JSON.stringify({
    enableTelemetry: true,
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  }));

  const sessionId = 'resumed-session';
  const counters = new Map<string, number>();
  const eventSequenceHighWaters = new Map<string, number>();
  const observations: SemanticObservation[] = [];
  const dependencies = {
    createTelemetry: () => ({
      consume(observation: SemanticObservation) { observations.push(observation); },
      async forceFlush() {},
      async shutdown() {},
    }),
    createTurnCounterStore: () => ({
      load(id: string) { return counters.get(id) ?? 0; },
      save(id: string, counter: number) {
        counters.set(id, Math.max(counters.get(id) ?? 0, counter));
      },
      reserveEventSequence(id: string, minimumExclusive: number, reservationSize: number) {
        const startExclusive = Math.max(eventSequenceHighWaters.get(id) ?? 0, minimumExclusive);
        const endInclusive = startExclusive + reservationSize;
        eventSequenceHighWaters.set(id, endInclusive);
        return { startExclusive, endInclusive };
      },
    }),
  };
  const ctx = {
    cwd: root,
    isProjectTrusted: () => false,
    sessionManager: { getSessionId: () => sessionId },
  } as unknown as ExtensionContext;

  const runOneProcess = async (prompt: string): Promise<SemanticObservation[]> => {
    const start = observations.length;
    const handlers = new Map<string, (raw: unknown, context: ExtensionContext) => Promise<void>>();
    const pi = {
      on(name: string, handler: (raw: unknown, context: ExtensionContext) => Promise<void>) {
        handlers.set(name, handler);
      },
    } as unknown as ExtensionAPI;
    observPi(pi, dependencies);
    await handlers.get('session_start')?.({}, ctx);
    await handlers.get('before_agent_start')?.({ prompt }, ctx);
    await handlers.get('agent_settled')?.({}, ctx);
    await handlers.get('session_shutdown')?.({}, ctx);
    return observations.slice(start);
  };

  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  let firstProcess: SemanticObservation[] = [];
  let secondProcess: SemanticObservation[] = [];
  try {
    firstProcess = await runOneProcess('first prompt');
    secondProcess = await runOneProcess('second prompt');
  } finally {
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
  }

  const turns = observations.filter(
    (observation): observation is Extract<SemanticObservation, { type: 'span.start' }> =>
      observation.type === 'span.start' && observation.kind === 'CHAT',
  );
  assert.deepEqual(turns.map(turn => turn.name), ['Turn 1', 'Turn 2']);
  assert.deepEqual(turns.map(turn => turn.attributes['agentic.turn.number']), [1, 2]);
  assert.equal(counters.get(sessionId), 2);
  const eventSequences = (items: SemanticObservation[]) => items
    .filter((item): item is Extract<SemanticObservation, { type: 'event' }> => item.type === 'event')
    .map(item => Number(item.attributes['event.sequence']));
  assert.ok(
    Math.min(...eventSequences(secondProcess)) > Math.max(...eventSequences(firstProcess)),
    'event.sequence must remain monotonic across plugin restart',
  );
});

test('turn state persistence failures do not suppress Turn telemetry', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-fail-open-'));
  const agentDir = join(root, 'agent');
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, 'observ-pi.json'), JSON.stringify({
    enableTelemetry: true,
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  }));
  const observations: SemanticObservation[] = [];
  const handlers = new Map<string, (raw: unknown, context: ExtensionContext) => Promise<void>>();
  const pi = {
    on(name: string, handler: (raw: unknown, context: ExtensionContext) => Promise<void>) {
      handlers.set(name, handler);
    },
  } as unknown as ExtensionAPI;
  observPi(pi, {
    createTelemetry: () => ({
      consume(observation: SemanticObservation) { observations.push(observation); },
      async forceFlush() {},
      async shutdown() {},
    }),
    createTurnCounterStore: () => ({
      load: () => 0,
      save: () => { throw new Error('disk full'); },
      reserveEventSequence: () => { throw new Error('disk full'); },
    }),
  });
  const ctx = {
    cwd: root,
    isProjectTrusted: () => false,
    sessionManager: { getSessionId: () => 'fail-open-session' },
  } as unknown as ExtensionContext;

  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  try {
    await handlers.get('before_agent_start')?.({ prompt: 'still emit' }, ctx);
  } finally {
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
  }

  assert.ok(observations.some(
    observation => observation.type === 'span.start'
      && observation.kind === 'CHAT'
      && observation.name === 'Turn 1',
  ));
  assert.ok(observations.some(
    observation => observation.type === 'event'
      && observation.name === 'user_prompt'
      && observation.attributes['agentic.turn.number'] === 1,
  ));
  assert.ok(observations
    .filter((observation): observation is Extract<SemanticObservation, { type: 'event' }> =>
      observation.type === 'event')
    .every(observation => !('event.sequence' in observation.attributes)),
  'failed durable reservations must not emit duplicate-prone local event.sequence values');
});

test('a session id that becomes available before the first Turn restores persisted state', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-late-session-'));
  const agentDir = join(root, 'agent');
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, 'observ-pi.json'), JSON.stringify({
    enableTelemetry: true,
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  }));
  const observations: SemanticObservation[] = [];
  const storeCalls: string[] = [];
  let sessionId = '';
  const handlers = new Map<string, (raw: unknown, context: ExtensionContext) => Promise<void>>();
  const pi = {
    on(name: string, handler: (raw: unknown, context: ExtensionContext) => Promise<void>) {
      handlers.set(name, handler);
    },
  } as unknown as ExtensionAPI;
  observPi(pi, {
    createTelemetry: () => ({
      consume(observation: SemanticObservation) { observations.push(observation); },
      async forceFlush() {},
      async shutdown() {},
    }),
    createTurnCounterStore: () => ({
      load(id: string) {
        storeCalls.push(`load:${id}`);
        return id === 'late-session' ? 7 : 0;
      },
      save(id: string, counter: number) { storeCalls.push(`save:${id}:${counter}`); },
      reserveEventSequence(id: string, minimumExclusive: number, reservationSize: number) {
        storeCalls.push(`reserve:${id}`);
        return { startExclusive: minimumExclusive, endInclusive: minimumExclusive + reservationSize };
      },
    }),
  });
  const ctx = {
    cwd: root,
    isProjectTrusted: () => false,
    sessionManager: { getSessionId: () => sessionId },
  } as unknown as ExtensionContext;

  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  try {
    await handlers.get('session_start')?.({}, ctx);
    sessionId = 'late-session';
    await handlers.get('before_agent_start')?.({ prompt: 'resume late' }, ctx);
  } finally {
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
  }

  const turn = observations.find(
    (observation): observation is Extract<SemanticObservation, { type: 'span.start' }> =>
      observation.type === 'span.start' && observation.kind === 'CHAT',
  );
  assert.equal(turn?.name, 'Turn 8');
  assert.equal(turn?.attributes['agentic.turn.number'], 8);
  assert.ok(storeCalls.indexOf('load:late-session') < storeCalls.indexOf('save:late-session:8'));
  assert.equal(storeCalls.some(call => call.includes('unknown')), false);
});
