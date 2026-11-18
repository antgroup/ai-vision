import assert from 'node:assert/strict';
import test from 'node:test';
import type { Logger } from '@opentelemetry/api-logs';
import {
  emitEventAgentApiRequestEvent,
  emitEventAgentToolCallEvent,
} from '../src/semconv.js';

test('event body uses event.name and never emits legacy event_name', () => {
  let emitted: Record<string, unknown> | undefined;
  const logger = {
    emit(record: Record<string, unknown>) {
      emitted = record;
    },
  } as unknown as Logger;

  emitEventAgentApiRequestEvent(logger, {
    eventName: 'api_request',
    attributes: { 'event.name': 'api_request' },
    body: { event_name: 'legacy-value', detail: 'kept' },
  });

  assert.deepEqual(emitted?.body, {
    detail: 'kept',
    'event.name': 'api_request',
  });
  assert.equal('event_name' in (emitted?.body as Record<string, unknown>), false);
});

test('tool_call emitter uses the canonical event body', () => {
  let emitted: Record<string, unknown> | undefined;
  const logger = {
    emit(record: Record<string, unknown>) {
      emitted = record;
    },
  } as unknown as Logger;

  emitEventAgentToolCallEvent(logger, {
    eventName: 'tool_call',
    attributes: { contract: 'tool-start' },
  });

  assert.deepEqual(emitted?.body, { 'event.name': 'tool_call' });
  assert.deepEqual(emitted?.attributes, {
    'event.name': 'tool_call',
    contract: 'tool-start',
  });
});
