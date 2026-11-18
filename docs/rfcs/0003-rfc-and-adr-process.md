---
rfc: "0003"
title: "RFC and Architecture Decision Record Process"
kind: bootstrap
status: draft
implementation_status: in_progress
created: "2026-10-16"
decision_record: null
---

# RFC and Architecture Decision Record Process

This proposal is awaiting review. It defines a lightweight way to record proposals, decisions, and implementation. Current work follows [GOVERNANCE.md](../../GOVERNANCE.md); ordinary contributions need not wait for this RFC to be accepted.

## When to create a record

| Situation | Record |
|---|---|
| Routine fix, test, or documentation correction | Issue or PR describing the problem, change, and validation |
| Cross-component protocol, public compatibility commitment, major architecture change, or governance change | RFC describing goals, tradeoffs, affected parties, and implementation scope |
| Lasting technical contract within a component, or a difficult-to-reverse choice | ADR following that component's conventions |
| Implementation within an accepted decision | Implementation PR linking the original record and stating completed scope and remaining work |

RFCs discuss shared proposals, ADRs preserve lasting technical decisions, and source code, tests, and current behavior documentation describe actual capabilities. Do not duplicate every RFC in an ADR; the decision record for a governance RFC can serve directly as its authority.

## Accepting the initial RFCs

Existing core maintainers from Ant Group and OceanBase organize review of RFCs 0001–0003 and invite the maintainers actually affected. No PMC is required first. Silence from external participants is not agreement, and external contribution volume is not a condition for approval.

For each proposal, record the exact content version accepted, actual reviewers and decision maker, decision and rationale, effective scope and date, and outstanding work in its PR or discussion. The [decision comment example](decision-comment.md) provides a copyable format. The three may be discussed together, but each needs an explicit conclusion. Merging drafts does not automatically accept all proposals.

Update `status` and `decision_record` only after that record exists; do not invent anyone's approval. The bootstrap working agreement in `GOVERNANCE.md` has its own PR review and effective-date conditions and does not depend on acceptance of all three RFCs.

## Later proposals

Authors can use the [RFC template](TEMPLATE.md) to describe the problem, proposal, main alternatives, impact, and validation approach, then invite relevant maintainers to review. Maintainers confirm scope and the decision maker. For major changes, publish a feedback deadline that gives affected participants an opportunity to respond, without imposing two fixed waiting periods.

Before deciding, summarize substantive feedback and how it was addressed. Explain tradeoffs when disagreements remain, and continue discussion if needed. Silence is not automatic approval. An authorized maintainer records the decision under their name; implementation PRs then link back and report progress. Link existing tracking issues when useful; do not require a separate issue solely for this process.

Changing the substance of an accepted decision requires a new RFC linked to the record it supersedes. Spelling and link corrections that do not change the decision can use ordinary PRs. Necessary measures to handle a security incident or restore service may be taken first, with the rationale, scope, and review outcome recorded afterward. Permanent rule changes still require an acceptance decision.

## Numbering and status

Maintainers assign four-digit numbers in proposal issues or PRs to avoid concurrent duplicates. Published numbers are not reused. The body needs only background, proposal, tradeoffs, impact, validation, open questions, and related records.

Use the front matter fields shown here: `rfc`, `title`, `kind`, `status`, `implementation_status`, `created`, and `decision_record`. Keep unknown decisions `null`; do not invent facts to complete fields.

- Decision status: `draft`, `discussion`, `accepted`, `rejected`, `deferred`, or `superseded`. Acceptance requires an actual decision record; superseded records link to their successor.
- Implementation status: `not_started`, `in_progress`, `implemented`, or `retired`. Mark work complete only when delivery and supporting evidence are complete.

Track acceptance and implementation separately and update the [RFC index](README.md). Milestone targets, implementation code, or a single passing CI check cannot establish proposal acceptance or release acceptance.

## Component ADRs and engineering entry points

[AgentLens ADRs](../../agentlens/docs/adr/README.md) already have an object, topic, and record structure with validation tools. Retain their native identities and statuses rather than migrating or renumbering them. Reference a specific record by its `record_id` and native anchor, for example [`trace/runtime-input@20260823-0234`](../../agentlens/docs/adr/trace/runtime-input.md#trace-runtime-input20260823-0234).

Discuss a root ADR location only when a lasting cross-component contract has no clear owner. Do not create empty directories or parallel templates in advance. After checking the existing record baseline, the root CI now selects the ADR checker for ADR/AgentLens changes and full test tiers. It validates records, index freshness, and authoring compatibility; it does not require ordinary documentation contributors to create an ADR or adopt this proposed process.

Coding agents read the root and target directory `AGENTS.md`, the [repository knowledge index](../README.md), relevant accepted decisions, and current source and tests. [CONTRIBUTING.md](../../CONTRIBUTING.md) is the contribution process entry point. When documentation and implementation conflict, identify the differences for the relevant maintainers to resolve. Do not silently treat drafts or examples as current rules.

## Implementation boundary

This PR supplies the process proposal, an RFC template, and a decision comment example. `in_progress` records the availability of these documentation aids, not adoption of the process: this RFC remains `draft` with `decision_record: null`. Evaluate the process through review records from real proposals, then add tools as needed. Do not build a separate approval system for the status table.
