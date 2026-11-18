# Dashboard performance recovery plan

1. Freeze released baseline and prepare isolated candidate branches. DONE.
2. Add regression tests for numeric-only metrics and exact duplicate Agent filters; implement backend normalization and frontend request/display changes.
3. Add bounded, fenced source-page transport and consumer; validate oversized Trace and Span without truncation.
4. Publish bounded operation facts/previews and consume selected events without whole-source hydration.
5. Run focused backend/Go/frontend checks, then fresh OB and seekdb scenario replay. Compare source/projection completeness and real page payloads; benchmark first/warm/concurrent requests.
6. Capture primary UI/detail behavior, independently review cross-component changes, and update an evidence-backed report with unresolved limits.

Delivery includes review PRs for the retained product and data-plane changes, with validation evidence and unresolved limits. This plan is not a claim that every performance target passed; see the [validation report](../../testing/native-query-performance-20261028.md). FULLTEXT and empty-result-pruning experiments are not part of the delivered implementation.
