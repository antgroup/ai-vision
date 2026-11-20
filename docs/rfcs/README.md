# Bootstrap RFCs and Project Decisions

Core maintainers from Ant Group and OceanBase lead AI Vision's initial open-source phase. External participants can begin with usage, feedback, and issues, then gradually take on maintenance responsibilities. See [GOVERNANCE.md](../../GOVERNANCE.md) for the bootstrap working agreement and [CONTRIBUTING.md](../../CONTRIBUTING.md) for contribution and validation requirements.

## Proposal index

| Number | Proposal | Decision status | Implementation status |
|---|---|---|---|
| RFC 0001 | [Bootstrap Governance and Contributor Responsibilities](0001-bootstrap-governance.md) | `draft` | `in_progress` |
| RFC 0002 | [Versioning, Branches, and the Initial Release Plan](0002-versioning-and-releases.md) | `draft` | `in_progress` |
| RFC 0003 | [RFC and Architecture Decision Record Process](0003-rfc-and-adr-process.md) | `draft` | `in_progress` |

Each RFC still requires its own acceptance decision. Approval and merge of the governance PR can put the working agreement in `GOVERNANCE.md` into effect without automatically approving these proposals. Partial implementation of RFC 0001 refers to the contribution guide, PR template, and bootstrap governance document. RFC 0002 provides unified versioning, the version page, test tiers, Daily/candidate workflows, and Gitflow naming checks. The migration to protected `main` and `develop`, the change to the Daily source branch, and stable release setup remain separate. RFC 0003 has authoring aids and a scoped ADR CI check; its process remains a proposal awaiting adoption.

## How to use these records

Routine fixes, tests, and documentation improvements go through issues and PRs directly. Propose an RFC first for cross-component protocols, public compatibility commitments, major architecture changes, or governance changes. Core maintainers invite affected maintainers to review, record the decision, rationale, and effective scope in the PR or discussion, then update the RFC status.

Existing core maintainers review the initial RFCs. No PMC or minimum volume of external code contributions is required. Silence is not agreement. Later major changes need an explicit public feedback window; see [RFC 0003](0003-rfc-and-adr-process.md).

Use the [RFC template](TEMPLATE.md) to draft a proposal and the [decision comment example](decision-comment.md) to record an actual decision. These aids do not assign RFC numbers, accept proposals, or grant permissions. Link existing implementation PRs or issues when available; a separate tracking issue is not required.

The first release target is `0.0.1`. The target dates for `0.1.0` and `1.0.0` are **2026-10-31** and **2026-11-30**, respectively, in Beijing time. These are release plans; see [RFC 0002](0002-versioning-and-releases.md) for acceptance scope and postponement decisions.

RFCs document proposals requiring a shared decision; ADRs preserve lasting technical decisions; source code, tests, and current behavior documentation describe implemented capabilities. Existing [AgentLens ADRs](../../agentlens/docs/adr/README.md) retain their object, topic, and record structure. Do not create a duplicate ADR for every RFC.
