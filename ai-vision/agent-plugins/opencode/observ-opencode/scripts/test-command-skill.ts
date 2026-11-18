#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { metrics } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { parseConfig } from '../src/config.js';
import { createHooks, resetHookState } from '../src/core.js';
import type { TelemetryRuntime } from '../src/telemetry.js';

function toMilliseconds(time: [number, number]): number {
  return time[0] * 1_000 + time[1] / 1_000_000;
}

const exporter = new InMemorySpanExporter();
const provider = new NodeTracerProvider({
  spanProcessors: [new SimpleSpanProcessor(exporter)],
});
const runtime: TelemetryRuntime = {
  tracer: provider.getTracer('command-skill-test'),
  meter: metrics.getMeter('command-skill-test'),
  logger: logs.getLogger('command-skill-test'),
  tracesEnabled: true,
  metricsEnabled: false,
  eventsEnabled: false,
  resolvedUsername: 'command-skill-test',
  resolvedAgentScene: 'test',
  forceFlush: () => provider.forceFlush(),
  shutdown: () => provider.shutdown(),
};
const config = parseConfig({
  enableTelemetry: true,
  collectionMode: 'full',
  tracesConfig: { enabled: true },
  metricsConfig: { enabled: false },
  eventsConfig: { enabled: false },
  otelExporter: { attributes: { 'ant.username': 'command-skill-test' } },
});
const hooks = createHooks(runtime, config, {});
const event = hooks.event as (input: { event: unknown }) => Promise<void>;
const commandBefore = hooks['command.execute.before'] as (input: {
  command: string;
  sessionID: string;
  arguments: string;
  __observedAt?: string;
}) => Promise<void>;

const sessionID = 'session-command-skill';
const command = 'weekly-progress-summarizer';
const argumentsText = '帮我看看周报';
const commandStartedAt = 1_784_097_467_751;
const turnStartedAt = commandStartedAt + 1;
const turnEndedAt = commandStartedAt + 346_851;

await event({ event: { type: 'session.created', properties: { info: {
  id: sessionID, projectID: 'test', directory: '.', title: 'command skill', version: 'test',
} } } });
await commandBefore({
  command,
  sessionID,
  arguments: argumentsText,
  __observedAt: new Date(commandStartedAt).toISOString(),
});
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'command-user', role: 'user', sessionID, time: { created: turnStartedAt },
} } } });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'command-assistant', role: 'assistant', sessionID,
  modelID: 'GLM-5.1', providerID: 'theta', tokens: { input: 1, output: 1 }, cost: 0,
  time: { created: turnStartedAt + 1, completed: turnEndedAt }, finish: 'stop',
} } } });
await event({ event: { type: 'session.idle', properties: { sessionID } } });
await event({ event: { type: 'command.executed', properties: {
  name: command,
  sessionID,
  arguments: argumentsText,
  messageID: 'command-assistant',
  __observedAt: new Date(turnEndedAt + 18).toISOString(),
} } });
// Duplicate completion callbacks must not create a second command Skill span.
await event({ event: { type: 'command.executed', properties: {
  name: command,
  sessionID,
  arguments: argumentsText,
  messageID: 'command-assistant',
  __observedAt: new Date(turnEndedAt + 18).toISOString(),
} } });
await runtime.forceFlush();

const spans = exporter.getFinishedSpans();
const turn = spans.find(span => span.name === 'Turn 1');
const skills = spans.filter(span => span.name === `skill ${command}`);
assert.ok(turn, 'command Skill must remain under its Turn');
assert.equal(skills.length, 1, 'one before/executed pair must produce one Skill span');
const skill = skills[0]!;
assert.equal(skill.parentSpanContext?.spanId, turn.spanContext().spanId);
assert.equal(skill.attributes['agentic.tool.type'], 'skill');
assert.equal(skill.attributes['agentic.tool.name'], command);
assert.equal(skill.attributes['agentic.tool.duration_ms'], turnEndedAt - turnStartedAt - 1);
assert.equal(Object.hasOwn(skill.attributes, 'agentic.tool.derived'), false);
assert.equal(toMilliseconds(skill.startTime), turnStartedAt + 1,
  'command Skill must start at least one millisecond after its Turn');
assert.ok(toMilliseconds(skill.startTime) > toMilliseconds(turn.startTime),
  'command Skill start must be strictly later than its parent Turn start');
assert.equal(toMilliseconds(skill.endTime), turnEndedAt,
  'post-idle command.executed latency must not extend the Skill beyond its Turn');
assert.deepEqual(turn.attributes['langfuse.trace.tags'], [`skill:${command}`]);

await runtime.shutdown();
resetHookState();
console.log('command Skill timing regression checks passed');
