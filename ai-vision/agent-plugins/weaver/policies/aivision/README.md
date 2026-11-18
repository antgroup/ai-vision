# AI Vision Policy Group

`aivision/` is the built-in Weaver Guard policy group for AI Vision agent telemetry. It appears as a policy-group tab in the dashboard and can be enabled/disabled independently from future policy groups. Its child signal directories (`traces`, `metrics`, `events`) appear as second-level tabs and can also be enabled/disabled independently.

Layout:

- `traces/` — OTLP trace/span semantic checks; `traces/base/` contains trace/span-local helpers and compatibility rules.
- `metrics/` — OTLP metric and data-point checks; `metrics/base/` contains metric-local helpers and compatibility rules.
- `events/` — OTLP log-record checks mapped to semantic events; `events/base/` contains event/log-local helpers and compatibility rules.

There is intentionally no `aivision/base/*.rego` layer. Each signal keeps an independent `base/` so helper functions, compatibility checks, and rule evolution cannot leak across signal types.

All rules in this group use package `live_check_advice` and emit findings via `make_advice(id, level, context, message)`.
