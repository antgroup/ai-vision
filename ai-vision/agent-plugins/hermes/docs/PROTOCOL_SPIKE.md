# Hermes OTLP transport truthfulness spike

Date: 2026-08-10

The approved config contract exposes both `http/json` and `http/protobuf`. The
implementation must therefore change the bytes on the wire rather than only retain
the configured protocol string.

## Baseline inventory

- `observ_hermes/pyproject.toml` already declares the OpenTelemetry HTTP/protobuf
  exporter; no additional production dependency is required for protobuf.
- `observ_hermes/telemetry.py` initializes the SDK trace, metric, and log protobuf
  exporters, but previously ignored `otelExporter.protocol`.
- `observ_hermes/exporters/semantic_file.py` already constructed valid OTLP/JSON
  request objects, but coupled that encoding to file persistence.
- The installed Hermes 0.20.0 virtual environment does not currently contain the
  plugin's declared OpenTelemetry packages. Verification therefore uses an isolated
  `uv` environment and does not mutate the Hermes runtime.

## Spike result

The JSON encoding is now transport-neutral in `exporters/otlp_json.py`. A bounded
standard-library transport in `exporters/http_json.py` posts those request objects as
`application/json`. The existing SDK path posts protobuf as
`application/x-protobuf`.

`tests/test_protocol_spike.py` proves for traces, metrics, and logs:

- the exact `/v1/{traces,metrics,logs}` path;
- the expected content type;
- a parseable protocol-specific body with the correct top-level OTLP signal shape;
- isolated HTTP failures with no request-header values in error state;
- idempotent shutdown and no exports after shutdown.

Reproduction:

```sh
cd hermes
uv run --python 3.14 \
  --with 'opentelemetry-api>=1.20.0' \
  --with 'opentelemetry-sdk>=1.20.0' \
  --with 'opentelemetry-exporter-otlp-proto-http>=1.20.0' \
  python -m unittest -v tests.test_protocol_spike tests.test_telemetry
```

Conclusion: both schema protocols are implementable truthfully with the existing
dependency set. The dependency-decision stop condition is not triggered. Production
protocol selection and config validation remain downstream implementation work.
