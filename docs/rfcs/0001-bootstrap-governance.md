---
rfc: "0001"
title: "Bootstrap Governance and Contributor Responsibilities"
kind: bootstrap
status: draft
implementation_status: in_progress
created: "2026-10-16"
decision_record: null
---

# Bootstrap Governance and Contributor Responsibilities

This proposal is awaiting review. See [GOVERNANCE.md](../../GOVERNANCE.md) for the bootstrap working agreement and how it takes effect. This RFC describes future maintenance responsibilities and governance evolution; merging the working agreement does not accept this entire RFC.

## Background and goals

The initial engineering effort comes mainly from the Ant Group and OceanBase core teams. External participants primarily use the project, report problems, and open issues. Governance should help people find owners, understand decisions, and gradually take on maintenance responsibilities if they wish to contribute consistently.

Core maintainers lead the bootstrap phase. External code contribution volume, a full role hierarchy, or a PMC is not a prerequisite for `0.0.1`, `0.1.0`, or `1.0.0`.

## Proposal

Routine contributions, review by someone other than the author, cross-component coordination, releases, and feedback follow `GOVERNANCE.md`. Shared evidence requirements belong in the [contribution guide](../../CONTRIBUTING.md) rather than being duplicated across documents.

As responsibilities become established, record each maintainer's agreed scope, public contact route, and necessary handoff arrangements in a roster. During bootstrap, ownership and assignment in issues and PRs are sufficient. Do not invent people or delegates merely to fill a table. Configure path ownership and platform permissions as needed; a document's existence does not grant permissions.

Contributors may move from reproductions, documentation, tests, reviews, and implementations into component maintenance. Nominations or applications should show sustained responsibility and domain understanding. Core maintainers explain the scope accepted and why. Start with limited responsibilities and adjust them as needed. Organization, job title, and PR count do not automatically qualify someone for a role.

When responsibilities change, record the handoff and update actual permissions. Handle significant disagreements, permission disputes, and conflicts of interest under the governance agreement. Preserve the rationale and reconsideration route without inventing examples of events that have not occurred.

## Evolution toward a PMC

A new governance proposal may establish a PMC when a broader group of maintainers sustains responsibility, open participation works in practice, and review and release practices are stable. That proposal should define membership, responsibilities, decision making, and the handoff from existing core maintainers.

`1.0.0` does not automatically establish a PMC. This proposal sets no predetermined seats, quarterly assessment, or external membership count. The current model may continue while it supports maintenance; governance should evolve with actual responsibilities.

## Implementation and acceptance

Bootstrap governance, the contribution guide, the PR template, and engineering instruction entry points have been prepared, so implementation is `in_progress`. Whether the working agreement is in effect depends on actual approval by a maintainer other than the author and merge of the governance PR. This RFC remains `draft`.

Add a named roster, path ownership, or handoff records only when responsibilities and permissions actually change. When accepting this RFC, record reviewers, the exact content version accepted, effective scope, and open questions. Do not assume anyone has committed to a responsibility.

Version dates and release targets are defined in [RFC 0002](0002-versioning-and-releases.md). This proposal adds no separate release checklist or schedule.

## Questions for review

- Should maintenance scope grow gradually based on actual responsibility?
- When should issue and PR ownership be supplemented with a separate maintainer roster?
- Which practical changes would justify a PMC proposal?
