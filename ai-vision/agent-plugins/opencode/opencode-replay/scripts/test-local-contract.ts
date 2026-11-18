import assert from 'node:assert/strict';
import { REPLAY_OTLP_HTTP_BASE, replayOtlpUrl } from '../src/local-collector.js';
import { replayReportConfig } from '../src/telemetry.js';

const previousEndpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT;
const previousHeaders = process.env.OTEL_EXPORTER_OTLP_HEADERS;
process.env.OTEL_EXPORTER_OTLP_ENDPOINT = 'https://production.example';
process.env.OTEL_EXPORTER_OTLP_HEADERS = 'Authorization=secret';
try {
  const config = replayReportConfig('full');
  assert.equal(REPLAY_OTLP_HTTP_BASE, 'http://127.0.0.1:4319');
  assert.equal(config.tracesConfig.endpoint, REPLAY_OTLP_HTTP_BASE);
  assert.equal(config.otelExporter.endpoint, REPLAY_OTLP_HTTP_BASE);
  assert.deepEqual(config.tracesConfig.headers, {});
  assert.equal(replayOtlpUrl('traces'), 'http://127.0.0.1:4319/v1/traces');
  console.log('local-only replay configuration contract passed');
} finally {
  if (previousEndpoint === undefined) delete process.env.OTEL_EXPORTER_OTLP_ENDPOINT;
  else process.env.OTEL_EXPORTER_OTLP_ENDPOINT = previousEndpoint;
  if (previousHeaders === undefined) delete process.env.OTEL_EXPORTER_OTLP_HEADERS;
  else process.env.OTEL_EXPORTER_OTLP_HEADERS = previousHeaders;
}
