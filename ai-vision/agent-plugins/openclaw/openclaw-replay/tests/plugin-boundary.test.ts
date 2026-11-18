import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import replayPlugin from '../index.ts';
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry';

function api(pluginConfig: Record<string, unknown>) {
  const typed: string[] = [];
  const internal: string[] = [];
  const services: string[] = [];
  const value = {
    pluginConfig,
    on(name: string) { typed.push(name); },
    registerHook(name: string | string[]) { internal.push(...(Array.isArray(name) ? name : [name])); },
    registerService(service: { id: string }) { services.push(service.id); },
    logger: { debug() {}, info() {}, warn() {} },
  } as unknown as OpenClawPluginApi;
  return { value, typed, internal, services };
}

test('disabled replay is inert even when production telemetry-like settings are present', () => {
  const fixture = api({ enableTelemetry: true, debug: true, otelExporter: { endpoint: 'https://production.example' } });
  replayPlugin.register(fixture.value);
  assert.deepEqual(fixture.typed, []);
  assert.deepEqual(fixture.internal, []);
  assert.deepEqual(fixture.services, []);
});

test('explicit capture registers only recorder hooks and one replay service', () => {
  const fixture = api({ captureEnabled: true, stdout: true });
  replayPlugin.register(fixture.value);
  assert.ok(fixture.typed.length > 0);
  assert.ok(fixture.internal.length > 0);
  assert.deepEqual(fixture.services, ['openclaw-replay']);
});

test('live replay entry has no production telemetry processor/exporter coupling', () => {
  const source = readFileSync(new URL('../index.ts', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /registerTelemetryProcessorContribution|SpanProcessor|FileSpanExporter|@opentelemetry/u);
});
