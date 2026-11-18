# Contributing to AI Vision

Contributions through usage feedback, issues, reproductions, documentation, tests, reviews, and code are welcome. Core maintainers from Ant Group and OceanBase lead the bootstrap phase; responsibilities and decisions are described in [GOVERNANCE.md](GOVERNANCE.md). Reporting a problem does not require contributing code first.

## Start with the problem

- Usage questions or defects: check existing [issues](https://github.com/antgroup/ai-vision/issues), then use the [bug report form](https://github.com/antgroup/ai-vision/issues/new?template=1-bug-report.yml) to describe the scenario, minimal reproduction, expected and actual results, environment, and actual version. If the version cannot be determined, write `unknown` and explain why.
- Feature requests: use the [feature request form](https://github.com/antgroup/ai-vision/issues/new?template=2-feature-request.yml) to explain the use case and desired outcome. Blank issues remain available for questions and other feedback; reporting does not commit you to implementing a fix.
- Focused fixes, tests, and documentation corrections: submit a focused PR and link any existing issue. An RFC is not required first.
- Cross-component protocols, public contracts, major architecture changes, or governance changes: align on scope and tradeoffs in an issue or RFC first. See the [RFC index](docs/rfcs/README.md) for process proposals. Existing [AgentLens ADRs](agentlens/docs/adr/README.md) retain their structure.

Follow the [Code of Conduct](CODE_OF_CONDUCT.md). Redact public materials: do not include credentials, personal information, private traces, or complete business prompts. Use the [security reporting guide](SECURITY.md) for vulnerabilities and the conduct guide for private incident reports. Recipients and contact routes are [pending confirmation](GOVERNANCE.md#pending-owner-and-contact-confirmations); do not post incident or exploit details in public issues.

## Documentation language

Write engineering documentation, `AGENTS.md`, RFCs and ADRs, PR titles and descriptions, and independent-package changelog entries in English, except in explicitly localized files. Stable product release notes are bilingual: English and Simplified Chinese in one GitHub Release body, using the [release-note template](.github/RELEASE_TEMPLATE.md). Keep both language sections consistent; names and links in shared contributor credits need not be duplicated. Historical ADR records and entries in independently versioned plugin changelogs may retain their original language. Respond to users in their preferred language.

Write organization names in full: **OceanBase** and **Ant Group**. Use the official localized name in localized documents. Preserve exact product names, repository paths, URLs, and configuration identifiers.

Optimize README images before submitting: prefer WebP for large illustrations, preserve readable text, and update all affected language links. The [README asset guide](docs/assets/readme/README.md#image-size-and-automatic-optimization) covers local compression and the optional same-repository PR bot. Review any bot changes and confirm checks cover its latest commit before merging; fork contributors compress images locally.

## Prepare the development environment

Read the [root AGENTS.md](AGENTS.md), then the instructions in the target directory and its parents. Use the [repository knowledge index](docs/README.md) to find source code, contracts, configuration, tests, and ADRs. Each component manages its own dependencies and validation entry points; there is no single root installation or test command.

| Change scope | Environment and validation entry points |
|---|---|
| Platform and frontend/backend call paths | [Platform instructions](ai-vision/AGENTS.md), [architecture overview](ai-vision/docs/architecture-overview.md) |
| React frontend | [VFE instructions](ai-vision/vfe/AGENTS.md), [package.json](ai-vision/vfe/package.json) |
| Django backend | [Backend README](ai-vision/aivision-py-be/README.md), [backend instructions](ai-vision/aivision-py-be/AGENTS.md) |
| Diagnostics, tasks, and scheduling | [AgentLens instructions](agentlens/AGENTS.md), [README](agentlens/README.md), and relevant ADRs |
| Collection plugins and integrations | [Plugin instructions](ai-vision/agent-plugins/AGENTS.md), [framework E2E](ai-vision/agent-plugins/tests/framework_e2e/README.md) |
| Collection service | [Antcollector README](antcollector/README.md) |
| CLI and deployment | [CLI README](aivision-cli/README.md), [deployment index](docs/deployment/README.md) |

User tutorials are published on the [AI Vision documentation site](https://aivision.mintlify.site/en/introduction) from the separate `ai-vision-docs` repository. This repository keeps engineering contracts, configuration, validation, and decision records. If a change affects user workflows, identify the documentation updates needed and their status in the PR.

## Branches and PRs

Ordinary contributions currently use `main` as their baseline and PR target. For specific fixes, use the target designated by maintainers. Gitflow remains a proposed migration in [RFC 0002](docs/rfcs/0002-versioning-and-releases.md); update this section when it is enabled. Branch names should describe the change; names generated by tools may be retained.

Keep each PR focused on one clear problem. Do not include unrelated refactoring, dependencies, or lockfile updates. Use the four [PR template](.github/PULL_REQUEST_TEMPLATE.md) sections: **Problem and impact**, **Validation**, **Screenshots and interaction**, and **Compatibility and unverified scope**. Larger changes may start as draft PRs. Merging requires review by a relevant maintainer other than the author; changes spanning components need confirmation from the relevant maintainers.

Reviewers follow [REVIEW.md](REVIEW.md), separating required fixes from non-blocking suggestions. For an RFC, start with the [proposal template](docs/rfcs/TEMPLATE.md); authorized decision makers can use the [decision-comment example](docs/rfcs/decision-comment.md). An implementation PR links its RFC or specific ADR record and states the scope delivered and remaining work. A separate tracking issue is optional.

## Validation and screenshots

Choose checks based on behavioral impact, prioritizing reproduction, critical paths, and relevant failure cases. Documentation changes do not require running the full stack, and small changes do not need new tests that merely restate the implementation.

| Change type | Validation to provide |
|---|---|
| Documentation or comments | Links, paths, command sources, and diff review; configuration examples also need checks against the actual parsing rules |
| Backend, CLI, collection, or internal logic | Inputs, outputs, affected contracts, and regression results; cover errors, permissions, and boundaries as needed |
| Visible UI | Comparable before/after screenshots from the running application, identifying the page and state; explain when a new page has no before screenshot |
| User interaction | Behavioral assertions or a reviewable recording of actions, state changes, and results; screenshots alone do not prove interaction behavior |
| Cross-component changes, deployment, or migration | Affected call paths and actual component combinations; check installation, upgrade, data retention, and failure recovery as needed |

For each set of results, state the environment, commands and working directories or interaction steps, expected and actual results, and relevant logs, responses, screenshots, or CI run links. Record the full SHA actually tested. When testing a dirty worktree before committing, identify the baseline SHA and change scope, including new files. After creating a commit or updating a PR, confirm that the evidence still corresponds to the code and rerun affected checks when content changes. Do not relabel old results as validation of a new commit.

Distinguish manual execution from CI, passing from failing, and unexecuted from inapplicable checks. Missing environments, permissions, or dependencies mean "unverified"; explain why. Explain why an inapplicable check is unaffected. UI evidence should identify relevant browser, viewport, and data conditions. Separate fixtures and mocks from real services, and never present design mockups as runtime screenshots. Local success does not prove untested platforms or deployment combinations work.

Attachments and CI artifacts must be accessible to reviewers and redacted before upload. Do not commit temporary logs, screenshots, or recordings by default. Archive evidence or rerun checks as needed when links expire.

The PR description check only confirms that the four sections exist and contain content. It does not authenticate test results, inspect screenshot content, or establish that evidence is sufficient. Workflows and their current runs determine which automated checks actually ran. Maintainers judge the materials and reproduce behavior when needed. See [PR check documentation](scripts/ci/README.md) for activation and limitations.

## Compatibility and versions

Ordinary feature or fix PRs do not bump versions independently. For changes affecting APIs, configuration, data formats, installation, upgrades, or independent packages, explain compatibility changes, migration steps, and unverified scope. Record actual tested combinations for cross-component changes; floating tags do not establish version consistency.

Describe user-visible changes and affected components in the PR. At release time, consolidate them into one bilingual GitHub Release body covering compatibility, upgrade considerations, known issues, and contributor credits. No additional product changelog fragment is required. Independently released packages retain their native versions and existing changelogs. The root [VERSION](VERSION) is the product build version source, shared by the core images and CLI. See the [version guide](docs/version-info.md) for the current version, update checks, and release notes. [CI profiles](scripts/ci/README.md#test-tiers) select validation by impact: ordinary PRs run relevant Linux basic checks and report affected deployment/platform integration separately as deferred. A basic pass does not qualify deployment; maintainers must request the relevant integration and broader version/release qualification explicitly. Selected failed, cancelled or unexecuted checks never count as passing. Daily/Release qualification is separate: the standard product channel uses the source-compatible CLI/images and pinned external SigNoz digest, then qualifies the actual packaged combination. Native compilation is optional; the temporary private Skills distribution retains its own stricter contract. Source-fixture checks do not establish candidate acceptance. Gitflow and release policy remain subject to separate review under [RFC 0002](docs/rfcs/0002-versioning-and-releases.md).

## Recognition and release credits

The README editions link to the [GitHub contributors graph](https://github.com/antgroup/ai-vision/graphs/contributors), which reflects GitHub-attributed code contributions. Release credits also recognize issue reports, reproductions, testing, reviews, documentation, and community help. Maintenance rights follow the [governance guide](GOVERNANCE.md).

For each stable release, the release lead prepares one shared **Contributors / 贡献者** section:

- Start with GitHub's **Generate release notes** for the intended tag and previous stable release, or the initial release range. The [release configuration](.github/release.yml) groups PRs by existing labels; generated PR titles are source material, not a translated release summary.
- Verify credits against PRs and issues included in that release. Link contributor profiles and the relevant work, including co-authors, issue reporters, testers, reviewers, and documentation contributors where supported by public records. Keep the generated PR author links and full changelog link.
- List each person once in the shared credits. Identify first-time contributors only when confirmed. Preserve automation-authored changes in the changelog, but do not count bots or coding tools as human contributors. Do not infer identities from private email addresses or invent acknowledgments.
- Copy the [release-note template](.github/RELEASE_TEMPLATE.md) into the draft Release and replace its guidance with actual content. Review English and Chinese summaries together, remove empty sections, and preserve upgrade requirements and known limitations in both languages. The template and native GitHub generator do not publish a release or translate prose automatically.

To request a missing or corrected credit, link the public contribution in an issue or PR. Respect a contributor's preferred public attribution. Credit for non-code work does not depend on appearing in the avatar wall.

## Coding agents and final checks

Coding agents and human contributions follow the same quality standards. Human submitters must understand changes, confirm that source material may be used, verify actual behavior, and respond to reviews and regressions. Briefly disclose substantive AI assistance in the PR; original prompts, full conversations, and reasoning transcripts are not required.

Agents first read the applicable `AGENTS.md`, contracts, and accepted decisions. Draft RFCs describe proposals only; test summaries cannot replace execution records. Do not run `git commit` or `git push` without an explicit user request.

Before submitting, review the diff and new files, run `git diff --check`, and confirm that no credentials, temporary artifacts, or unrelated changes are included. Update affected contracts and operating instructions. Preserve executed checks, actual results, and unfinished work in the PR.
