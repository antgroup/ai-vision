# Layered Trace Capacity: Research Integration Review, 2026-11-07

- Record: [trace-data-flow/layered-capacity-isolation@20261107-2148](../layered-capacity-isolation-20261107-2148.md#trace-data-flow-layered-capacity-isolation20261107-2148).
- Exact reviewed ADR SHA-256: `e1f301bbf3886c39557a3290947284213f739076a3704973639b1369651fc343`.
- Previous complete proposal: [20261107-1540](../layered-capacity-isolation-20261107-1540.md), preserved byte-for-byte with SHA-256 `8093438bae3e04bc6be05cf09697719f939bbd704f994d487d74609ef2e0d62f`.
- Decision status: **proposed**. Implementation status: **not_started**.
- Scope: incorporation of the [Langfuse/LangSmith research](../research/langfuse-langsmith-large-traces-20261107.md), delivery-package boundaries and current implementation comparison. The user's instruction authorized this revision; it did not establish runtime qualification or accept all policy defaults.

## Review and resolutions

A fresh independent coding-agent builder review read the complete resulting ADR. Separate read/UI and ingest/projection audits checked actual main and local source boundaries for the [comparative gap assessment](langfuse-langsmith-gap-20261107.md).

| Review question | Result / resolution |
|---|---|
| Can R1 deliver a useful closed read flow without synchronous ACK, full chunk replication and catalog history? | R1 requires an independent catalog and fenced current index, metadata-first topology and bounded selected content/refusal. A1, J1 and C1 have explicit capability/cutover gates. R1 cannot claim their guarantees. |
| Does live pagination accidentally claim retained as-of stability? | R1 declares `live-keyset-v1`, late-update movement and unknown totals. J1 alone advertises retained catalog snapshots. Cursor profiles distinguish live positions, catalog epochs and Trace revisions. |
| Does deferring B1 leave one active pane able to grow without bound? | Required clarification closed: R1 itself has8 MiB retained/reserved accounting, reservation before fetch, old-range replacement/release or local refusal, current-page-only catalog retention, and repeated manual-page/switch/close validation. B1 extends the ledger to shared views/caches. |
| Are old ACK and original-content semantics weakened silently? | Existing asynchronous outcomes are not called durable commit. The proposed A1 commit profile and actual wire receipts remain separate. Accepted normalized values are not shortened into ingest placeholders; unsafe existing-body reads fail that reference locally. |
| Are short/empty pages and nested budgets unambiguous? | Continuation, not result count, determines completion. Empty work-limited pages advance their scan position; same-request retries retain the4/64 MiB scopes. Client retention/prefetch limits apply across requests. |
| Does the comparison attribute candidate code to main? | Corrected main worker deferral attribution; paired SigNoz transactions/tracking remain under the inspected local combination. Main includes #155/#152, not the broader unmerged capacity routes. Existing JSON folding and structured parsing are acknowledged. |

The builder re-read the exact resulting ADR hash above and reported no remaining material contract blocker. This is an agent technical review, not a maintainer approval, accepted ADR, implementation result or production capacity guarantee. The original proposal's architecture review remains bound only to its original hash.

## Verification boundary

Documentation checks cover relative links/anchors, record/index/review consistency, preserved historical hash and unchanged product-file fingerprints. No product code, dependency, schema, test fixture or running service was modified. No new load test, SDK runtime/ACK test, Linux operation or browser screenshot was performed. Original source/test evidence remains limited to its actual fixed candidate; the package-specific Validation section is still a planned implementation gate.
