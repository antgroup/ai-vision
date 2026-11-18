import assert from 'node:assert/strict';
import test from 'node:test';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import pireplay from '../src/index.js';

function withEnvironment(values: Record<string, string | undefined>, run: () => void): void {
  const previous = Object.fromEntries(Object.keys(values).map((key) => [key, process.env[key]]));
  try {
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    run();
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
}

function api(hooks: string[]): ExtensionAPI {
  return {
    on(name: string) { hooks.push(name); },
    getAllTools: () => [],
    getCommands: () => [],
  } as unknown as ExtensionAPI;
}

test('pireplay is inert by default and production observability flags cannot enable it', () => {
  const hooks: string[] = [];
  withEnvironment({
    PIREPLAY_CAPTURE_ENABLED: undefined,
    OBSERV_PI_ENABLE_TELEMETRY: 'true',
    OBSERV_PI_OTLP_ENDPOINT: 'https://production.example',
  }, () => pireplay(api(hooks)));
  assert.deepEqual(hooks, []);
});

test('explicit replay capture registers raw recorder callbacks without live normalization', () => {
  const hooks: string[] = [];
  withEnvironment({ PIREPLAY_CAPTURE_ENABLED: 'true' }, () => pireplay(api(hooks)));
  assert.equal(hooks.length, 16);
  assert.ok(hooks.includes('provider_stream_event'));
  assert.doesNotMatch(pireplay.toString(), /CanonicalNormalizer|createTelemetry|SemanticEngine/);
});
