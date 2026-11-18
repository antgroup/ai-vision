import assert from 'node:assert/strict';
import test from 'node:test';
import { buildSessionEndAttributes } from '../src/hooks/session-lifecycle.js';

test('session_end emits the required common and lifecycle semantic fields', () => {
  const attributes = buildSessionEndAttributes({
    sessionKey: 'agent:main:test',
    sessionId: 'session-id',
    turnCount: 3,
    chatId: 'chat-id',
    username: 'test-user',
    eventSequence: 7,
  });

  assert.deepEqual(attributes, {
    'event.name': 'session_end',
    'event.sequence': 7,
    'gen_ai.conversation.id': 'session-id',
    'gen_ai.chat.id': 'chat-id',
    'gen_ai.operation.name': 'chat',
    'gen_ai.session.id': 'session-id',
    'ant.username': 'test-user',
    'agentic.runtime.name': 'openclaw',
    'agentic.turn.number': 3,
  });
  assert.equal('agentic.session.turn_count' in attributes, false);
});
