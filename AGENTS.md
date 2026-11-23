# AI Vision Development Guidelines

These guidelines apply to the entire monorepo and define project boundaries and shared working practices. Before editing a subdirectory, read its `AGENTS.md` and those of its parent directories. More specific rules apply only within their own directory. The user's current explicit instructions take precedence over repository conventions.

## Communication and Execution

- Start from the problem, its goal, and the current implementation. Respond in the user's language; use English for repository engineering docs, agent instructions, RFCs, and PR text unless a file is explicitly localized. Stable product release notes and their template use English and Simplified Chinese in one GitHub Release body.
- Lead with the outcome and impact, then provide the necessary evidence. Be concise and specific.
- Continue authorized work until it is complete. Do not overwrite the user's existing changes or refactor unrelated code.
- If missing information affects implementation, first complete the work that does not depend on it, then ask a specific question.

## Project Boundaries and Entry Points

| Directory | Responsibility | Read before editing |
|---|---|---|
| `ai-vision/` | Platform Django backend and React / Vite frontend | [Project guidelines](ai-vision/AGENTS.md) and the backend or frontend `AGENTS.md` |
| `agentlens/` | Diagnosis engine, task management, and scheduling | [Project guidelines](agentlens/AGENTS.md), then the relevant architecture, ADRs, and topic documentation |
| `antcollector/` | OpenTelemetry-based telemetry collection service | [Project README](antcollector/README.md) and the target component's documentation |
| `ai-vision/agent-plugins/` | AI Agent telemetry integrations, semantic conventions, and verification tools | [Project guidelines](ai-vision/agent-plugins/AGENTS.md) and the target Agent's documentation |
| `aivision-cli/` | Three-in-one CLI (`aiv`): deployment / SpaceKey queries / auth | [Project README](aivision-cli/README.md) and the relevant blueprint |
| `deploy/` | Canonical deployment assets, native packaging, and private delivery contracts | [Deployment agent rules](deploy/AGENTS.md), [deployment index](deploy/README.md), and the relevant build or release guide |

The root [README.md](README.md) provides project navigation. [GOVERNANCE.md](GOVERNANCE.md) defines bootstrap governance, [CONTRIBUTING.md](CONTRIBUTING.md) defines contribution, validation, and PR evidence requirements, and [docs/README.md](docs/README.md) helps agents and developers find source code, contracts, configuration, and verification entry points. This repository keeps technical documentation, ADRs, and component-specific development Skills alongside the implementation. Maintained user-facing AI Vision Skills are owned by the separate `ai-vision-skills` catalog. See the [Skills guide](skills/README.md) for access requirements. Complete user tutorials and illustrations are maintained in the separate `ai-vision-docs` repository and published on the [AI Vision documentation site](https://aivision.mintlify.site/en/introduction).

The local [docs/deployment/](docs/deployment/README.md) directory contains the deployment index, version differences, maintenance notes, configuration examples, and development image publishing instructions. Chinese and English supplements live in `zh-CN/` and `en/`; update both when their content changes. For source deployments, start with the [CLI source-build instructions](aivision-cli/README.md#source-build-pointing-at-a-different-repo-or-branch). SigNoz in the deployment documentation is an external dependency, not part of this repository.

For deployment, release, or native packaging work, read the [deployment agent rules](deploy/AGENTS.md), [product release guide](docs/deployment/en/dev-image-release.md), and relevant [native packaging contract](deploy/native-packaging/release-contract.md), including when the implementation is in `scripts/ci/`, CLI code, or component Dockerfiles. Product releases and images are owned by `antgroup/ai-vision`. The separate `ob-labs/ai-vision-skills` repository owns user-facing Skills. Dated delivery records document their own snapshots.

## Working in the Monorepo

- The root Git repository manages all projects. Do not initialize separate repositories in subdirectories.
- Each project maintains its own dependencies, lockfiles, build commands, and runtime entry points. Enter the appropriate directory before running commands; do not assume there is a root build command.
- Keep changes in the project responsible for the capability. When interfaces, configuration, or data formats cross project boundaries, check both callers and providers and update the relevant documentation.
- The active AI Vision frontend is `ai-vision/vfe/`. Do not add features to the legacy `aivision-fe/` frontend.
- Do not update dependencies in bulk or regenerate unrelated lockfiles. Follow each project's rules for maintaining `vendor/`.
- Check ignore rules before adding files. Keep local credentials, runtime logs, build outputs, and crash dumps out of Git. Example configuration must not contain real credentials.

## Verification and Delivery

- Run the relevant project's checks for the scope of the change. For cross-project changes, verify the affected interfaces and call chains.
- Do not add tests for reversible, low-impact changes or tests that merely repeat the implementation. For documentation changes, check links, command paths, and the diff; running the whole service stack is unnecessary.
- Once checks pass, expand or repeat verification only when new changes, failures, or unresolved questions justify it.
- Before finishing, inspect `git status` and the current diff, run `git diff --check`, and remove temporary files created for this task that are no longer needed.
- Report the changes, verification results, and unfinished work. Never claim to have run a check that was not executed.
- Do not run `git commit` or `git push` unless the user explicitly requests it. Review all content to be committed before committing, and confirm the remote and target branch before pushing. Do not rewrite remote history by default.

## PR Validation and Governance

- Follow [REVIEW.md](REVIEW.md) for review evidence and required fixes versus non-blocking suggestions. Follow the [Code of Conduct](CODE_OF_CONDUCT.md) and [security reporting guide](SECURITY.md); pending contacts must not be invented or treated as active services.
- Follow the [branch and PR naming rules](CONTRIBUTING.md#name-the-branch-and-pr) before creating a branch or PR, including when using a coding agent. Select verification scope using the [contribution guide](CONTRIBUTING.md). Use the [PR template](.github/PULL_REQUEST_TEMPLATE.md) to record **Problem and impact**, **Validation**, **Screenshots and interaction**, and **Compatibility and unverified scope**.
- Evidence must correspond to the actual code tested. Distinguish manual runs from CI, real services from fixtures, and passed checks from checks that were not run. Provide real screenshots for visible UI changes and behavioral assertions or recordings for interaction changes; explain when these do not apply.
- For cross-component changes, check providers and callers and record the actual combination and unverified platforms. Ordinary PRs must not bump versions on their own; independently released packages retain their native versions.
- See the [governance guidelines](GOVERNANCE.md) for bootstrap responsibilities and how they take effect. The [bootstrap RFCs](docs/rfcs/README.md) still require separate review. [CI tiers](scripts/ci/README.md#test-tiers) select PR, daily, and release validation; source checks and candidate artifact smoke are separate evidence. Protecting `main` and `develop` and moving Daily builds to `develop` are separate migration steps. See the [version guide](docs/version-info.md) for the shared version source and version page.
- The PR check validates branch and title format and body section completeness. Maintainers verify evidence and behavior. See the [PR check documentation](scripts/ci/README.md) for the boundaries between automated checks, validation, and maintainer review.
