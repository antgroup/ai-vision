import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test from 'node:test';
import { parseConfig } from '../dist/config.js';
import { initTelemetry } from '../dist/telemetry.js';

// Catch a missing exporter preference by measuring actual HTTP exports across
// two collections. The inherited SDK preference deliberately contradicts config.
for (const [preference, mode, temporality, values, counts] of [
  ['delta', 'full', 1, [2, 3], [1, 1]],
  [' DELTA ', 'privacy', 1, [2, 3], [1, 1]],
  ['cumulative', 'full', 2, [2, 5], [1, 2]],
  ['lowmemory', 'full', 1, [2, 3], [1, 1]],
]) {
  test(`production metric exporter honors ${preference.trim()}/${mode} over SDK environment`, async () => {
    const received = [];
    const server = createServer((request, response) => {
      const chunks = [];
      request.on('data', chunk => chunks.push(Buffer.from(chunk)));
      request.on('end', () => {
        received.push(JSON.parse(Buffer.concat(chunks).toString('utf8')));
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end('{}');
      });
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const previous = process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE;
    let runtime;
    try {
      process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE = temporality === 1 ? 'cumulative' : 'delta';
      const config = parseConfig({
        enableTelemetry: true,
        collectionMode: mode,
        otelExporter: { metricsTemporalityPreference: preference },
        tracesConfig: { enabled: false },
        eventsConfig: { enabled: false },
        metricsConfig: { enabled: true, endpoint: `http://127.0.0.1:${server.address().port}/v1/metrics` },
        metricExportInterval: 60_000,
      });
      runtime = initTelemetry(config);
      const counter = runtime.meter.createCounter('test.temporality.count');
      const histogram = runtime.meter.createHistogram('test.temporality.duration');
      counter.add(2);
      histogram.record(2);
      await runtime.forceFlush();
      counter.add(3);
      histogram.record(3);
      await runtime.forceFlush();
      const exported = received.flatMap(payload => payload.resourceMetrics ?? [])
        .flatMap(resource => resource.scopeMetrics ?? []).flatMap(scope => scope.metrics ?? []);
      for (const [name, kind] of [['test.temporality.count', 'sum'], ['test.temporality.duration', 'histogram']]) {
        const data = exported.filter(metric => metric.name === name).map(metric => metric[kind]);
        assert.equal(data.length, 2, `${name} must export two collections`);
        assert.deepEqual(data.map(value => value.aggregationTemporality), [temporality, temporality]);
        assert.deepEqual(data.map(value => Number(value.dataPoints[0][kind === 'sum' ? 'asDouble' : 'sum'])), values);
        if (kind === 'histogram') {
          assert.deepEqual(data.map(value => Number(value.dataPoints[0].count)), counts);
        }
      }
    } finally {
      await runtime?.shutdown();
      if (previous === undefined) delete process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE;
      else process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE = previous;
      await new Promise(resolve => server.close(resolve));
    }
  });
}
