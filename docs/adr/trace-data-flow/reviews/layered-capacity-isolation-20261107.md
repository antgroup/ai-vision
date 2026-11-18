# Layered Trace Capacity: Design Review, 2026-11-07

- Record: [trace-data-flow/layered-capacity-isolation@20261107-1540](../layered-capacity-isolation-20261107-1540.md#trace-data-flow-layered-capacity-isolation20261107-1540).
- Exact reviewed document SHA-256: `8093438bae3e04bc6be05cf09697719f939bbd704f994d487d74609ef2e0d62f`.
- Decision status: **proposed**. Implementation status: **not_started**.
- Review type: independent coding-agent architecture reviews plus a separate builder-readiness check. This is not maintainer acceptance, a production capacity qualification, or authorization to claim implementation.
- Scope: the complete resulting ingest/storage/projection/read/presentation contract, migration, consumer scope and planned evidence gates.

## Required findings and resolution

| Finding | Resulting contract clarification |
|---|---|
| Byte limit alone permits millions of tiny protobuf objects before pdata allocation | Published total wrapper/Span/KV/event/link/AnyValue/token limits and outcome for unusable envelopes; checked pre-materialization. |
| Same Span ID with conflicting versions can straddle export/write chunks | Check the entire bounded input before packing; coalesce identical persistence and reject all conflicting occurrences locally. |
| Stable IDs do not prove revision-stable replay after changed updates or quota reevaluation | Preserve last-successful-arrival semantics; unchanged-current accepted set is no-op. Explicitly test rejected growth becoming admissible after a later shrink. No invented causal-order contract. |
| Mixed-Trace batch cannot have one source revision | Per-reference Trace/Span/content proof; valid sibling results survive a legitimate local snapshot conflict. |
| 4/64 MiB read-budget lifetime was ambiguous | Both scopes belong to one HTTP request and charge together; retries do not reset. Explicit continuation is a new request; retained/prefetch bounds persist. |
| Renewed page tokens could outlive retained catalog history | Inherit original expiry; retain superseded versions through validTo+TTL+skew, and never expire current versions by creation time. |
| Optional count could time out the transaction holding a valid page | Separate same-epoch read-only count transaction with500 ms/remaining-time subdeadline and serialization reserve; count can be unknown without discarding the page. |
| Active view references could escape cache eviction accounting | Shared retained/reserved ledger includes pinned view data and in-flight reservations, with8 MiB per-Trace/24 MiB global caps and explicit release on view close/switch. |

The ingestion/storage reviewer and read/frontend reviewer re-read and matched the exact final hash above. Their proposed-contract blockers are closed. The builder check reported no important unanswered question for implementation planning after budget ownership was defined; final narrow replay/retention clarifications were independently checked.

## Review boundaries

No product implementation, database migration, new capacity/fault-injection test, browser memory run or Linux runtime qualification was performed for this design review. Earlier successful local/Linux fixture runs are baseline evidence only. The ADR's Validation section remains a planned gate, and all published defaults still need implementation-boundary and resource qualification.

Human review should confirm the chosen new-data rejection policy, storage quota, synchronous ACK/backpressure tradeoff and default layered budgets. Do not mark this ADR accepted or implemented solely because coding-agent reviews completed. Subsequent substantive edits require review against their new content hash.
