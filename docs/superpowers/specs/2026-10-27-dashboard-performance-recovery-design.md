# Dashboard performance recovery

Scope: remove primary cost widgets from dashboards and Trace/Session lists, and let numeric metrics omit complete pricing work. Retain canonical cost fields in compatible list API responses and complete cost semantics in details. Repair actual page filtering and oversized-source projection while preserving correctness fallbacks.

## Contracts

- Metrics callers can explicitly request `include_cost=false`. Default requests retain complete legacy cost semantics. Numeric-only responses identify omitted cost; unknown cost must not become zero.
- Eliminate only an exact equality Agent resource predicate matching the independently authorized `agent_uuid`; retain mismatched or custom filters and all authorization fences.
- `native-trace-source-pages-v1` transports complete sources in bounded fragments. Every page binds Space, physical scope, source revision and maintenance generation. Consumer rejects drift, duplicate/missing spans, malformed fragments or an excessive reconstructed source. Final publication retains existing compare-and-swap checks.
- Recent events use selected operation facts and bounded, explicitly marked previews rather than hydrating every selected Trace. Details remain the route to full content.
- Source v1 limits, raw emergency mode, readiness and capability fallbacks remain. No additional deployment switches.

## Validation

Failing regression tests precede each contract change. Real-service validation starts with raw-only ingestion, not restored projections: four historical framework captures; aggregate source >16 MiB; one Span >16 MiB; few huge plus many small Traces; unready projections; actual browser request bodies; concurrent first-screen calls. Record completeness, path, bytes, first/warm time and environment separately. Test OB and seekdb serially on the same Mac resource budget. Capture candidate UI. Do not label simulations as new model calls or local results as VM evidence.

## Delivery

Publish the retained query optimizations and their measured limits in review PRs. Experimental FULLTEXT candidates and empty-result pruning are excluded. The newer `inputEvidence` dashboard projection still requires original observations and has a separate unresolved performance gap; historical seven-field results do not establish its acceptance. See the [validation report](../../testing/native-query-performance-20261028.md). Merge and release remain separate operations.
