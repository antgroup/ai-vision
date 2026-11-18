---
object_key: trace-data-flow
title: Trace Data Flow Decision Archive
scope: Cross-component Trace admission, accepted storage, derived views, retrieval, presentation and resource-failure isolation
---

# Trace Data Flow Decision Archive

This object owns the capacity and failure-isolation contract from the collection boundary to the browser. Collector, native ingest/storage, platform queries and all Trace consumers implement coordinated parts of the same contract. AgentLens retains ownership of diagnosis-specific interpretation and task outcomes.

## Current decision index

| Topic | Target decision | Implementation |
|---|---|---|
| [layered-capacity-isolation](layered-capacity-isolation.md) | `proposed`: trace-data-flow/layered-capacity-isolation@20261107-2203 | `not_started`; earlier fixture fixes are a baseline, not delivery of this record |

## Related decisions and contracts

- [AgentLens Trace Retrieval archive](../../../agentlens/docs/adr/trace-retrieval/README.md): diagnostic candidate and exact-source responsibilities remain separate.
- [Implemented native query contract](../../../ai-vision/docs/traces/query-performance-contract.md).
- [Repository contribution and review requirements](../../../CONTRIBUTING.md).

## Design review

[OceanBase-integrated technical review](reviews/layered-capacity-isolation-oceanbase-20261107.md) binds the current proposal hash. The [research-integrated revision](layered-capacity-isolation-20261107-2148.md) and its [review](reviews/layered-capacity-isolation-research-20261107.md) remain historical. The [original complete proposal](layered-capacity-isolation-20261107-1540.md) and its [original review](reviews/layered-capacity-isolation-20261107.md) remain historical. Human decision acceptance and implementation validation remain separate.

[Current comparative gap and risk assessment](reviews/langfuse-langsmith-gap-20261107.md) distinguishes merged main, local capacity work and the revised delivery packages. The [original risk assessment](reviews/implementation-gap-and-risk-20261107.md) remains historical.

[Langfuse/LangSmith research](research/langfuse-langsmith-large-traces-20261107.md) records official sources, fixed snapshots and evidence limits; it does not establish equivalent runtime guarantees or validate the proposed numerical policy.

[OceanBase execution profile](research/oceanbase-execution-profile-20261107.md) selects transactional/indexed foundations and names optional, version-qualified storage, snapshot and resource-governance paths.
