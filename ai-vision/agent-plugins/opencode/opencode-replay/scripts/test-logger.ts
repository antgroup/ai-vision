import assert from 'node:assert/strict';
import { initLogger, log } from '../src/logger.js';

const original = console.error;
const messages: unknown[][] = [];
console.error = (...args: unknown[]) => { messages.push(args); };

try {
  process.env.OPENCODE_DEBUG = 'true';
  delete process.env.OPENCODE_REPLAY_DEBUG;
  initLogger();
  log('production debug must not affect replay');
  assert.equal(messages.length, 0);

  initLogger(true);
  log('replay debug enabled');
  assert.equal(messages.length, 1);
  assert.match(String(messages[0]?.[1]), /replay debug enabled/);
} finally {
  delete process.env.OPENCODE_DEBUG;
  console.error = original;
  initLogger(false);
}

console.log('replay logger isolation tests passed');
