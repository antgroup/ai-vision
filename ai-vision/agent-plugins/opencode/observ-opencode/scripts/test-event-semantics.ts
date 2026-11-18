#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { context, trace } from '@opentelemetry/api';
import { parseConfig } from '../src/config.js';
import { createHooks, resetHookState } from '../src/core.js';
import { handleSessionIdle } from '../src/hooks/session.js';
import { sessionMap, turnMap } from '../src/hooks/state.js';
import type { HookContext, SessionState } from '../src/hooks/types.js';
import {
  emitEventAgentApiRequestEvent,
  emitEventAgentCompactionResultEvent,
  emitEventAgentSessionEndEvent,
  emitEventAgentSessionStartEvent,
  emitEventAgentToolCallEvent,
  emitEventAgentToolResultEvent,
  emitEventAgentUserPromptEvent,
  classifyTool,
  toolDisplayName,
} from '../src/semconv.js';
import type { TelemetryRuntime } from '../src/telemetry.js';

const records: Array<Record<string, any>> = [];
const runtime = {
  tracer: trace.getTracer('event-semantics-test'),
  meter: {},
  logger: {
    emit(record: Record<string, unknown>) {
      records.push(record);
    },
  },
  tracesEnabled: false,
  metricsEnabled: false,
  eventsEnabled: true,
  resolvedUsername: 'event-test-user',
  resolvedAgentScene: 'default',
  forceFlush: async () => {},
  shutdown: async () => {},
} as unknown as TelemetryRuntime;

const config = parseConfig({
  enableTelemetry: true,
  otelExporter: {
    attributes: { 'ant.username': 'event-test-user' },
  },
});

const semanticEventEmitters = [
  ['user_prompt', emitEventAgentUserPromptEvent],
  ['api_request', emitEventAgentApiRequestEvent],
  ['tool_call', emitEventAgentToolCallEvent],
  ['tool_result', emitEventAgentToolResultEvent],
  ['session_start', emitEventAgentSessionStartEvent],
  ['session_end', emitEventAgentSessionEndEvent],
  ['compaction_result', emitEventAgentCompactionResultEvent],
] as const;
const legacyCachedTokensAttribute = ['agentic.usage', 'cached_tokens'].join('.');

for (const [eventName, emit] of semanticEventEmitters) {
  emit(runtime.logger, { eventName, attributes: { contract: 'event-body' } });
}

assert.equal(records.length, semanticEventEmitters.length);
for (const [index, [eventName]] of semanticEventEmitters.entries()) {
  assert.deepEqual(records[index]?.body, { 'event.name': eventName });
  assert.equal(records[index]?.attributes?.['event.name'], eventName);
  assert.equal(records[index]?.attributes?.contract, 'event-body');
  assert.equal(Object.hasOwn(records[index]?.body ?? {}, 'event_name'), false);
}
records.length = 0;

assert.equal(toolDisplayName(classifyTool('read')), 'tool read');
assert.equal(toolDisplayName(classifyTool('github_search')), 'mcp github_search');
assert.equal(toolDisplayName(classifyTool('task')), 'agent SyncAgent');
assert.equal(classifyTool('read')['agentic.tool.raw_name'], 'read');
assert.equal(classifyTool('mcp__github__search')['agentic.tool.raw_name'], 'mcp__github__search');

const session: SessionState = {
  sessionId: 'session-event-contract',
  activeTurnId: 'turn-active-fallback',
  turnCounter: 1,
  createdAt: Date.now(),
  lastUserMessageId: 'message-1',
  lastTurnContext: context.active(),
  lastTurnId: '',
  lastTurnEndTime: null,
  eventSequence: 0,
  commandSequence: 0,
  pendingCommands: [],
  completedCommandCallIds: new Set(),
  sessionStartEmitted: true,
  subagentInvocation: null,
  compactionSpan: null,
  compactionStartTime: null,
  compactionSequence: 0,
};

sessionMap.clear();
turnMap.clear();
sessionMap.set(session.sessionId, session);

const hookContext = {
  runtime,
  config,
  resolveUsername: () => 'event-test-user',
  finishTurn: () => {},
} as unknown as HookContext;

await handleSessionIdle(hookContext, {
  type: 'session.idle',
  properties: { sessionID: session.sessionId },
});

assert.equal(records.length, 1);
assert.deepEqual(records[0]?.body, { 'event.name': 'session_end' });
assert.equal(records[0]?.attributes?.['event.name'], 'session_end');
assert.equal(records[0]?.attributes?.['gen_ai.chat.id'], 'turn-active-fallback');
assert.equal(Object.hasOwn(records[0]?.body ?? {}, 'event_name'), false);

sessionMap.clear();
turnMap.clear();

records.length = 0;
resetHookState();

const fullConfig = parseConfig({
  enableTelemetry: true,
  collectionMode: 'full',
  tracesConfig: { enabled: false },
  metricsConfig: { enabled: false },
  eventsConfig: { enabled: true },
  otelExporter: { attributes: { 'ant.username': 'event-test-user' } },
});
const fullHooks = createHooks(runtime, fullConfig, {});
const fullEvent = fullHooks.event as (input: { event: unknown }) => Promise<void>;
const fullSessionId = 'session-delayed-user-prompt-full';
const fullMessageId = 'message-delayed-user-prompt-full';
const userPromptRecords = () => records.filter(record => record.body?.['event.name'] === 'user_prompt');

await fullEvent({ event: { type: 'session.created', properties: { info: {
  id: fullSessionId, projectID: 'test', directory: '.', title: 'full', version: 'test',
} } } });
await fullEvent({ event: { type: 'message.updated', properties: { info: {
  id: fullMessageId, role: 'user', sessionID: fullSessionId, time: { created: Date.now() },
} } } });
assert.equal(userPromptRecords().length, 0,
  'user_prompt must wait for message text instead of treating missing text as redacted');

await fullEvent({ event: { type: 'message.part.updated', properties: {
  sessionID: fullSessionId,
  part: { id: 'part-full', sessionID: fullSessionId, messageID: fullMessageId, type: 'text', text: 'hello full mode' },
} } });
assert.equal(userPromptRecords().length, 1);
assert.equal(
  userPromptRecords()[0]?.attributes?.['gen_ai.input.messages'],
  JSON.stringify([{ role: 'user', content: 'hello full mode' }]),
);

await fullEvent({ event: { type: 'message.updated', properties: { info: {
  id: fullMessageId, role: 'user', sessionID: fullSessionId, time: { created: Date.now() },
} } } });
await fullEvent({ event: { type: 'message.part.updated', properties: {
  sessionID: fullSessionId,
  part: { id: 'part-full-2', sessionID: fullSessionId, messageID: fullMessageId, type: 'text', text: ' again' },
} } });
assert.equal(userPromptRecords().length, 1, 'user_prompt must be emitted once per user message');

const emptySessionId = 'session-delayed-user-prompt-empty';
const emptyMessageId = 'message-delayed-user-prompt-empty';
await fullEvent({ event: { type: 'session.created', properties: { info: {
  id: emptySessionId, projectID: 'test', directory: '.', title: 'empty', version: 'test',
} } } });
await fullEvent({ event: { type: 'message.updated', properties: { info: {
  id: emptyMessageId, role: 'user', sessionID: emptySessionId, time: { created: Date.now() },
} } } });
await fullEvent({ event: { type: 'message.part.updated', properties: {
  sessionID: emptySessionId,
  part: { id: 'part-empty', sessionID: emptySessionId, messageID: emptyMessageId, type: 'text', text: '' },
} } });
assert.equal(userPromptRecords().length, 2);
assert.equal(
  userPromptRecords()[1]?.attributes?.['gen_ai.input.messages'],
  JSON.stringify([{ role: 'user', content: '' }]),
  'full mode must preserve an explicitly empty prompt instead of redacting it',
);

records.length = 0;
resetHookState();

const resumedCounters = new Map<string, { turnCounter: number; lastUserMessageId: string }>();
const resumedTurnCounterStore = {
  load: (sessionId: string) => resumedCounters.get(sessionId) ?? { turnCounter: 0, lastUserMessageId: '' },
  save: (sessionId: string, state: { turnCounter: number; lastUserMessageId: string }) => {
    resumedCounters.set(sessionId, state);
  },
  remove: (sessionId: string) => resumedCounters.delete(sessionId),
};
const firstProcessHooks = createHooks(runtime, fullConfig, { turnCounterStore: resumedTurnCounterStore });
const firstProcessEvent = firstProcessHooks.event as (input: { event: unknown }) => Promise<void>;
const resumedSessionId = 'session-resumed-without-created-event';
await firstProcessEvent({ event: { type: 'session.created', properties: { info: {
  id: resumedSessionId, projectID: 'test', directory: '.', title: 'resume', version: 'test',
} } } });
await firstProcessEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'message-before-restart', role: 'user', sessionID: resumedSessionId, time: { created: Date.now() },
} } } });
assert.equal(resumedCounters.get(resumedSessionId)?.turnCounter, 1,
  'the latest turn counter must be persisted before a plugin restart');

resetHookState();
const resumedProcessHooks = createHooks(runtime, fullConfig, { turnCounterStore: resumedTurnCounterStore });
const resumedProcessEvent = resumedProcessHooks.event as (input: { event: unknown }) => Promise<void>;
await resumedProcessEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'message-before-restart', role: 'user', sessionID: resumedSessionId, time: { created: Date.now() },
} } } });
assert.equal(sessionMap.get(resumedSessionId)?.turnCounter, 1,
  'a repeated pre-restart user message must not consume another turn index');
const resumedMessageId = 'message-after-restart';
await resumedProcessEvent({ event: { type: 'message.updated', properties: { info: {
  id: resumedMessageId, role: 'user', sessionID: resumedSessionId, time: { created: Date.now() },
} } } });
await resumedProcessEvent({ event: { type: 'message.part.updated', properties: {
  sessionID: resumedSessionId,
  part: {
    id: 'part-resumed-without-created-event', sessionID: resumedSessionId,
    messageID: resumedMessageId, type: 'text', text: 'continue resumed session',
  },
} } });
assert.ok(sessionMap.has(resumedSessionId),
  'a resumed session must be restored when OpenCode omits session.created after plugin restart');
assert.equal(sessionMap.get(resumedSessionId)?.turnCounter, 2,
  'a resumed session must continue the persisted turn index');
assert.equal(userPromptRecords().length, 1,
  'events from a resumed session must continue through the telemetry pipeline');
await resumedProcessEvent({ event: { type: 'session.deleted', properties: { info: {
  id: resumedSessionId, projectID: 'test', directory: '.', title: 'resume', version: 'test',
} } } });
assert.equal(resumedCounters.has(resumedSessionId), false,
  'deleting an OpenCode session must remove its persisted turn counter');

records.length = 0;
resetHookState();

const privacyConfig = parseConfig({
  enableTelemetry: true,
  collectionMode: 'privacy',
  tracesConfig: { enabled: false },
  metricsConfig: { enabled: false },
  eventsConfig: { enabled: true },
  otelExporter: { attributes: { 'ant.username': 'event-test-user' } },
});
const privacyHooks = createHooks(runtime, privacyConfig, {});
const privacyEvent = privacyHooks.event as (input: { event: unknown }) => Promise<void>;
const privacySessionId = 'session-delayed-user-prompt-privacy';
const privacyMessageId = 'message-delayed-user-prompt-privacy';

await privacyEvent({ event: { type: 'session.created', properties: { info: {
  id: privacySessionId, projectID: 'test', directory: '.', title: 'privacy', version: 'test',
} } } });
await privacyEvent({ event: { type: 'message.updated', properties: { info: {
  id: privacyMessageId, role: 'user', sessionID: privacySessionId, time: { created: Date.now() },
} } } });
assert.equal(userPromptRecords().length, 0);
await privacyEvent({ event: { type: 'message.part.updated', properties: {
  sessionID: privacySessionId,
  part: { id: 'part-privacy', sessionID: privacySessionId, messageID: privacyMessageId, type: 'text', text: 'secret' },
} } });
assert.equal(userPromptRecords().length, 1);
assert.equal(userPromptRecords()[0]?.attributes?.['gen_ai.input.messages'], '[REDACTED]');

resetHookState();

records.length = 0;
const turnConfig = parseConfig({
  enableTelemetry: true,
  collectionMode: 'full',
  tracesConfig: { enabled: false },
  metricsConfig: { enabled: false },
  eventsConfig: { enabled: true },
  otelExporter: { attributes: { 'ant.username': 'event-test-user' } },
});
const turnHooks = createHooks(runtime, turnConfig, {});
const turnEvent = turnHooks.event as (input: { event: unknown }) => Promise<void>;
const turnSessionId = 'session-frozen-turn-number';
const turnUserId = 'user-frozen-turn-number';
await turnEvent({ event: { type: 'session.created', properties: { info: {
  id: turnSessionId, projectID: 'test', directory: '.', title: 'turn', version: 'test',
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: turnUserId, role: 'user', sessionID: turnSessionId, time: { created: Date.now() },
} } } });
const frozenSession = sessionMap.get(turnSessionId);
assert.ok(frozenSession);
frozenSession.turnCounter = 99;
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'assistant-frozen-turn-number', parentID: turnUserId, role: 'assistant',
  sessionID: turnSessionId, modelID: 'test-model', providerID: 'test-provider',
  tokens: { input: 3, output: 2 }, cost: 0,
  time: { created: Date.now(), completed: Date.now() + 1 }, finish: 'stop',
} } } });
const frozenApiRequest = records.find(record => record.attributes?.['event.name'] === 'api_request');
assert.equal(frozenApiRequest?.attributes?.['agentic.turn.number'], 1);

resetHookState();
records.length = 0;
const cacheReadSessionId = 'session-cache-read-event';
const cacheReadUserId = 'user-cache-read-event';
await turnEvent({ event: { type: 'session.created', properties: { info: {
  id: cacheReadSessionId, projectID: 'test', directory: '.', title: 'cache-read', version: 'test',
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: cacheReadUserId, role: 'user', sessionID: cacheReadSessionId, time: { created: Date.now() },
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'assistant-cache-read-event', parentID: cacheReadUserId, role: 'assistant',
  sessionID: cacheReadSessionId, modelID: 'test-model', providerID: 'test-provider',
  tokens: { input: 3, output: 2, cache: { read: 0, write: 0 } }, cost: 0,
  time: { created: Date.now(), completed: Date.now() + 1 }, finish: 'stop',
} } } });
const cacheReadApiRequest = records.find(record => record.attributes?.['event.name'] === 'api_request');
assert.equal(cacheReadApiRequest?.attributes?.['agentic.usage.cache_read_tokens'], 0);
assert.equal(
  Object.hasOwn(cacheReadApiRequest?.attributes ?? {}, legacyCachedTokensAttribute),
  false,
  'api_request must not emit the legacy cached_tokens event attribute',
);

resetHookState();
records.length = 0;
const errorSessionId = 'session-error-cache-read-event';
const errorUserId = 'user-error-cache-read-event';
await turnEvent({ event: { type: 'session.created', properties: { info: {
  id: errorSessionId, projectID: 'test', directory: '.', title: 'cache-read-error', version: 'test',
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: errorUserId, role: 'user', sessionID: errorSessionId, time: { created: Date.now() },
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'assistant-error-cache-read-event', parentID: errorUserId, role: 'assistant',
  sessionID: errorSessionId, modelID: 'test-model', providerID: 'test-provider',
  tokens: { input: 0, output: 0 }, cost: 0,
  time: { created: Date.now(), completed: Date.now() + 1 }, finish: 'other',
} } } });
const errorApiRequest = records.find(record => record.attributes?.['event.name'] === 'api_request');
assert.equal(
  Object.hasOwn(errorApiRequest?.attributes ?? {}, 'agentic.usage.cache_read_tokens'),
  false,
  'api_request error events must omit unknown cache-read usage instead of emitting zero',
);
assert.equal(
  Object.hasOwn(errorApiRequest?.attributes ?? {}, legacyCachedTokensAttribute),
  false,
  'api_request error events must not emit the legacy cached_tokens event attribute',
);

resetHookState();
records.length = 0;
const staleSessionId = 'session-stale-assistant-owner';
await turnEvent({ event: { type: 'session.created', properties: { info: {
  id: staleSessionId, projectID: 'test', directory: '.', title: 'stale', version: 'test',
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'user-turn-1', role: 'user', sessionID: staleSessionId, time: { created: Date.now() },
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'assistant-turn-1', parentID: 'user-turn-1', role: 'assistant', sessionID: staleSessionId,
  modelID: 'test-model', providerID: 'test-provider', tokens: { input: 0, output: 0 },
  cost: 0, time: { created: Date.now() },
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'user-turn-2', role: 'user', sessionID: staleSessionId, time: { created: Date.now() + 2 },
} } } });
await turnEvent({ event: { type: 'message.updated', properties: { info: {
  id: 'assistant-turn-1', parentID: 'user-turn-1', role: 'assistant', sessionID: staleSessionId,
  modelID: 'test-model', providerID: 'test-provider', tokens: { input: 4, output: 1 },
  cost: 0, time: { created: Date.now(), completed: Date.now() + 1 }, finish: 'stop',
} } } });
assert.equal(
  records.some(record => record.attributes?.['event.name'] === 'api_request'),
  false,
  'a late assistant callback from a finished turn must not attach to the new active turn',
);

resetHookState();

console.log('event semantic regression checks passed');
