# RFC Authoring Template

This is an authoring aid, not a numbered RFC or an accepted decision. Copy the block below into a new proposal file and follow [RFC 0003](0003-rfc-and-adr-process.md). Maintainers assign the published four-digit number; do not reserve or reuse one yourself.

Replace prompts with the actual proposal. Keep unknown facts `null` or `pending`; set `created` to the actual proposal date and `kind` to its actual category (`bootstrap` describes the initial RFCs). Acceptance and implementation have separate statuses. A new proposal starts as `draft` / `not_started`, with `decision_record: null`.

```markdown
---
rfc: null
title: null
kind: null
status: draft
implementation_status: not_started
created: null
decision_record: null
---

# Pending proposal title

## Problem and goals

Pending: explain the problem, affected users or components, intended outcome, and non-goals.

## Proposal

Pending: describe the proposed behavior or contract, with examples where useful. Distinguish existing behavior from proposed changes.

## Alternatives and tradeoffs

Pending: explain the main alternatives, including keeping the current behavior, and why this proposal is preferable.

## Impact and compatibility

Pending: identify affected callers, providers, data, deployment, and compatibility commitments. State migration or rollback needs where applicable.

## Validation and implementation scope

Pending: describe how the outcome will be verified, what can be delivered first, and what remains outside this proposal. A planned check is not an executed result.

## Open questions and related records

Pending: list unresolved decisions and link relevant issues, RFCs, or specific component ADR records. Use existing implementation links when available; no separate tracking issue is required.
```

After a real decision, use the [decision comment example](decision-comment.md) to record it and link that record from `decision_record`. Do not create a duplicate ADR for this RFC.
