import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { metrics } from '@opentelemetry/api';
import { parseConfig } from '../src/config.js';
import { initObservabilityTelemetry } from '../src/http-runtime.js';

// Verify the production exporter's actual two collection intervals, including
// the privacy wrapper and explicit config overriding the SDK environment.
const received: any[] = [];
const server = createServer((request, response) => {
  const chunks: Buffer[] = [];
  request.on('data', chunk => chunks.push(Buffer.from(chunk)));
  request.on('end', () => {
    received.push(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end('{}');
  });
});
await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
const previous = process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE;
try {
  const address = server.address() as { port: number };
  for (const [preference, mode, expected] of [
    ['delta', 'full', 1], ['delta', 'privacy', 1],
    ['cumulative', 'full', 2], ['lowmemory', 'full', 1],
  ] as const) {
    received.length = 0;
    process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE = preference === 'cumulative' ? 'delta' : 'cumulative';
    const config = parseConfig({ enableTelemetry: true, collectionMode: mode,
      otelExporter: { metricsTemporalityPreference: preference },
      tracesConfig: { enabled: false }, eventsConfig: { enabled: false },
      metricsConfig: { enabled: true, endpoint: `http://127.0.0.1:${address.port}/v1/metrics` },
      metricExportInterval: 60_000,
    });
    const runtime = initObservabilityTelemetry(config);
    try {
      const counter = runtime.meter.createCounter('test.temporality.count');
      const histogram = runtime.meter.createHistogram('test.temporality.duration');
      counter.add(2); histogram.record(2);
      await runtime.forceFlush();
      counter.add(3); histogram.record(3);
      await runtime.forceFlush();
      const exported = received.flatMap(payload => payload.resourceMetrics ?? [])
        .flatMap(resource => resource.scopeMetrics ?? []).flatMap(scope => scope.metrics ?? []);
      for (const [name, kind] of [['test.temporality.count', 'sum'], ['test.temporality.duration', 'histogram']]) {
        const data = exported.filter(metric => metric.name === name).map(metric => metric[kind]);
        assert.equal(data.length, 2, `${preference}/${mode} must export two real intervals`);
        assert.deepEqual(data.map(value => value.aggregationTemporality), [expected, expected],
          `${preference}/${mode} must honor production config rather than inherited SDK env`);
        const values = data.map(value => Number(value.dataPoints[0][kind === 'sum' ? 'asDouble' : 'sum']));
        assert.deepEqual(values, expected === 1 ? [2, 3] : [2, 5],
          `${preference}/${mode} must preserve interval values`);
      }
    } finally {
      await runtime.shutdown();
      metrics.disable();
    }
  }
  console.log('Production metric temporality HTTP tests passed');
} finally {
  if (previous === undefined) delete process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE;
  else process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE = previous;
  await new Promise<void>(resolve => server.close(() => resolve()));
}
