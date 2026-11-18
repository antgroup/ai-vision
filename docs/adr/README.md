# Cross-component Architecture Decisions

These records preserve long-term contracts that span the platform, collection gateway, external data plane and their consumers. They complement component ADRs; they do not replace the [AgentLens decision archive](../../agentlens/docs/adr/README.md).

Use one object/topic record for one resulting contract. Decision status and implementation status are independent. A proposed record describes a review target, not a released capability. Reviewers record the exact document revision, rationale and scope before acceptance; implementation and actual evidence are linked separately. Later substantive changes supersede the complete record, preserving history.

| Object | Archive | Scope |
|---|---|---|
| `trace-data-flow` | [Trace Data Flow](trace-data-flow/README.md) | Cross-component Trace admission, accepted storage, derived views, retrieval, presentation and resource-failure isolation |

The user requested this cross-component archive because no existing component owns the entire data-flow capacity contract. Current code and tests remain authoritative for implemented behavior. The [RFC/ADR process proposal](../rfcs/0003-rfc-and-adr-process.md) remains a separate proposal; using a record here does not adopt that RFC or grant merge authority.
