# AI Vision Bootstrap Governance

During the initial open-source phase, core maintainers from Ant Group and OceanBase lead AI Vision development, review, and releases. Participation through usage, feedback, issues, reproductions, documentation, and code is welcome. Launch does not depend on the volume of external code contributions.

This agreement takes effect when the PR introducing it receives approval from at least one existing maintainer other than the author and is merged into `main`. The approval and merge records establish that decision. Until then, it remains a proposed working agreement. This decision accepts this document and the corresponding contribution process; it does not automatically accept every [bootstrap RFC](docs/rfcs/README.md) or establish that GitHub permissions and checks have been configured.

## Responsibilities

| Participant | Current responsibility |
|---|---|
| Users and contributors | Report problems, provide reproductions or improvements, and take responsibility for submitted content and validation claims |
| Relevant maintainers | Triage issues, review changes, confirm component contracts and validation scope, and follow up on regressions |
| Core maintainers | Coordinate direction across components, resolve disagreements, assign maintenance responsibilities, and plan releases |
| Release lead | Assemble the release scope, artifact validation, release notes, and open issues; carry out the approved release |

Existing repository permissions remain in place. This document grants no new permissions and does not assume that anyone has accepted additional responsibilities. The public entry points are this repository's [Issues](https://github.com/antgroup/ai-vision/issues) and [Pull requests](https://github.com/antgroup/ai-vision/pulls). Existing maintainers take ownership and coordinate reviews in those records; core maintainers assign work when ownership is unclear. Anyone unable to continue should record that fact and arrange a handoff.

Add a named maintainer roster and path ownership when responsibilities have been assigned and accepted. Do not invent names, delegates, or `CODEOWNERS` entries in advance.

## Pending owner and contact confirmations

The following details have not been confirmed for publication in this working agreement. They are open setup items, not appointments or evidence that existing repository permissions are absent. Existing maintainers continue to coordinate through issue and PR assignments.

| Responsibility | Details to confirm |
|---|---|
| Platform frontend and backend | Named maintainers, accepted review scope, and public review route |
| AgentLens diagnostics and scheduling | Named maintainers, accepted review scope, and public review route |
| Collection service and agent integrations | Named maintainers, accepted review scope, and public review route |
| CLI and deployment | Named maintainers, accepted review scope, and public review route |
| Governance and release coordination | Named coordinator; release lead, reviewer, and handoff for each release |
| Private security reports | Recipient and backup, tested private channel, supported release coverage, and response expectations |
| Private conduct reports and appeals | Primary handler, uninvolved alternate, and tested private contact route |

Record confirmations only after the people involved consent to the responsibility and public attribution. Authorized administrators verify any required access and contact routing. Then update this section and the linked reporting documents; add `CODEOWNERS` only for confirmed owners. Before treating reporting channels as available for public launch, confirm their recipients and verify delivery. Do not copy organization-wide email addresses, response deadlines, or reward promises without confirmation that they cover this project.

## Contributions and decisions

- Submit focused PRs for routine fixes, tests, and documentation. `main` is currently the baseline and default target. See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution process.
- Before merging, obtain review from at least one relevant maintainer other than the author, using [REVIEW.md](REVIEW.md). For changes spanning components, ask the relevant maintainers to confirm interfaces and impact. One person may review multiple components they understand. Existing maintainers arrange suitable reviewers.
- Authors provide validation, results, and unverified scope that correspond to the change. Visible UI changes need real screenshots; interaction changes need behavioral assertions or a recording. The contribution guide defines when each applies.
- Align on cross-component protocols, public compatibility commitments, major architecture changes, or governance changes through an issue or RFC before implementation. Record the decision maker, rationale, scope, and effective conditions in the PR or discussion. Routine changes do not require an RFC.
- Relevant maintainers first discuss disagreements. If they cannot resolve them, core maintainers record a named decision and its rationale. Anyone may request reconsideration in the original record, with review by a maintainer who did not participate in the original decision. People whose own permissions or direct interests are involved must recuse themselves from the final decision.

CI results are part of the review evidence. A complete PR description, passing checks, or a coding agent's conclusion cannot replace maintainer judgment about actual behavior and remaining risks. State which checks were not run; a missing environment is not a passing result.

## Releases and feedback

Each release PR or release record identifies its lead, scope, actual version and artifacts, validation results, and known limitations. Another relevant maintainer must confirm the release. If evidence is insufficient, reduce scope that has not yet been promised or postpone the release. Dates and version numbers do not replace acceptance checks.

Each product version has one GitHub Release body. Stable release notes use English and Simplified Chinese, following the [documentation language policy](CONTRIBUTING.md#documentation-language), and cover user-visible changes, affected components, upgrade considerations, known issues, and one shared contributor section. Follow the [credit guidelines](CONTRIBUTING.md#recognition-and-release-credits) for evidence-based recognition of code and non-code contributions. Independently released SDKs and plugins retain their own versions and changelogs. Future product versioning, Gitflow, and daily-build plans are in [RFC 0002](docs/rfcs/0002-versioning-and-releases.md), subject to separate acceptance and implementation.

Issue handling should state the next step and responsible scope. Link fixes to their PRs, validation, and the version containing them. When closing an issue, distinguish what maintainers have verified from environments still awaiting user confirmation. Explain why a request is declined or when it could be reopened.

Follow the [Code of Conduct](CODE_OF_CONDUCT.md) in project collaboration. Use the [security reporting guide](SECURITY.md) for vulnerabilities. Both follow this working agreement's acceptance conditions. Private report handlers and contact routes remain pending confirmation above; these documents do not activate a service or appoint recipients. Keep incident details, credentials, and exploit information out of public issues and PRs.

## Contributor growth and future governance

Maintenance responsibilities can grow from reproductions, documentation, tests, reviews, and focused implementations. Existing maintainers may nominate contributors, and contributors may apply. Core maintainers decide based on sustained responsibility, domain knowledge, review quality, and collaboration, recording the scope the person agrees to own and the permissions needed. Employer, job title, or PR count does not automatically confer permissions.

Authorized administrators carry out and record permission changes. Departing maintainers hand over unfinished work, while historical contribution credit is preserved. Permission decisions may be reconsidered through the process above.

As the project matures, a new governance proposal may establish a Project Management Committee (PMC). The basis is sustained maintenance, open participation, and a practical handoff, rather than release dates, fixed membership counts, or meeting frequency. `1.0.0` does not automatically establish a PMC. Existing core maintainers remain responsible until new rules are accepted and the handoff is complete.
