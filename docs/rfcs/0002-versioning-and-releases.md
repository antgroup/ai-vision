---
rfc: "0002"
title: "Versioning, Branches, and the Initial Release Plan"
kind: bootstrap
status: draft
implementation_status: in_progress
created: "2026-10-16"
decision_record: null
---

# Versioning, Branches, and the Initial Release Plan

This proposal is awaiting review. The [contribution guide](../../CONTRIBUTING.md#branches-and-prs) defines the Gitflow naming convention. Ordinary contributions continue to target the repository's default branch until the branch migration. See the [version guide](../version-info.md) for the shared product version and version page and the [CI guide](../../scripts/ci/README.md) for implemented checks and candidate workflows. The naming check does not change protected branches or adopt this proposal in full.

## Goals and scope

Use lightweight Gitflow, Pride Versioning, and one product release number to support continued development by core maintainers, user trials, and issue feedback. The first release target is `0.0.1`, with **`0.1.0` planned for 2026-10-31** and **`1.0.0` for 2026-11-30**. Dates use Beijing time.

Dates are targets subject to acceptance checks. Initially, maintain only one stable release line. A PMC, external contribution volume, and maturity of every integration are not release prerequisites.

## Lightweight Gitflow

| Branch or flow | Responsibility |
|---|---|
| `main` | Default branch and stable release baseline; stable tags identify exact release commits |
| `develop` | Integration for the next version; becomes the ordinary contribution target and daily-build source after migration |
| Working branch → `develop` | Focused change reviewed through a PR; squash merging is allowed |
| `develop` → `release/<version>` → `main` | Freeze scope, finish fixes and release notes, then merge into the release baseline |
| Published tag → `hotfix/<version>` → `main` | Fix the current stable version and complete affected validation |
| `main` → `develop` | Merge back through a PR after each release or hotfix; propagate necessary fixes to an active release branch |

Release, hotfix, and backmerge operations preserve commit history so released fixes are not lost. Normally, keep only one active release branch. Follow the [branch and PR naming rules](../../CONTRIBUTING.md#name-the-branch-and-pr). Protection rules govern source, review, and checks; a matching branch name does not grant release permissions or prove source ancestry.

The naming convention is the first step. The migration establishes `main` and `develop`, configures their protections and history-preserving merges for release flows, adjusts Daily build and promotion inputs, and updates the contribution guide. Until that migration, ordinary PRs continue to target the default branch unless directed by maintainers. Never overwrite stable version tags or move the recommended release entry point to a failed candidate.

## Pride Versioning and compatibility

| Part | Project meaning |
|---|---|
| Proud | A complete capability milestone worth announcing, such as `1.0.0`; it does not automatically imply incompatibility |
| Regular | Routine feature, performance, and usability improvements, such as `1.1.0` |
| Fix | Defect, security, and reliability fixes, such as `1.1.1` |

The original Pride terminology is `PROUD.DEFAULT.SHAME`; this project uses the neutral terms Proud, Regular, and Fix. Maintainers choose the version in the release PR rather than deriving it automatically from commit titles.

Version numbers do not replace compatibility notes. Each release states its supported scope and changes to APIs, telemetry semantics, configuration, data, installation, and upgrades. Announce migration steps before breaking changes. `1.0.0` commits only to capabilities explicitly supported and validated; it does not imply equal maturity across all integrations.

The root `VERSION` file is the source for the product release number, updated by release PRs. The frontend, backend, AgentLens, collector, and companion CLI follow the product release number. Independent SDKs, plugins, and external dependencies retain their native versions. Do not downgrade an already published independent package to align with the initial product release.

Release records preserve the actual source SHA, artifact checksums or image digests, and validated combinations. Desired configuration, floating tags, and health status do not independently establish the running version. Record unknown or mixed versions accurately.

## Daily builds and stable releases

Build Daily from a fixed, full `develop` SHA. The [product candidate workflow](../../.github/workflows/release-images.yml) schedules Daily at 02:23 Asia/Shanghai on the default `develop` branch and uses the event SHA. Manual runs use the selected branch's event SHA and reserve the rolling Daily and dev aliases for the default branch. Newly built components use that source. Unchanged components may reuse an earlier verified digest and preserve their real historical build identity. All five bundled service images receive the same immutable product-version tag. A component can therefore carry multiple release tags without being rebuilt.

Daily versions identify a dated candidate snapshot, for example `0.0.1-daily.20261021`. The first snapshot for a date has no revision suffix; another accepted snapshot for that date uses `.2`, then `.3`. A manual run can fix the snapshot date explicitly, while a scheduled run uses its original creation date in Asia/Shanghai. Keep the selected date and revision across midnight and candidate retries. Workflow reruns do not change the product version; cumulative workflow run numbers and full run ID/attempt belong in operational build metadata. Published versions cannot be overwritten. The numeric base comes from `VERSION`, which is not modified during the build. Manual development builds remain separate from Daily snapshots.

Update the daily download entry point only after source checks, candidate builds, and the actual artifact smoke succeed. Failure in these stages leaves the previous successful entry point unchanged. Daily builds are for trials; they do not automatically become stable releases or the default upgrade target. Linux source validation runs daily; release validation includes the broader supported-platform matrices. Candidate build and artifact-smoke records are retained for 14 days; source-suite evidence is retained for 7 days. Published versioned snapshots and registry retention require an explicit maintenance policy. Registry aliases are not a transaction across images; the emitted digest manifest and pinned deployment variables identify the verified combination.

PRs use impact-based test selection rather than every cross-platform matrix. Prose changes run lightweight checks; component changes run their source tests; schema, authorization, ingestion, and deployment changes add the relevant database and platform checks. Version changes and release/hotfix flows require the release tier. Selected checks cannot pass by being skipped or cancelled. Feedback targets and job timeouts are budgets to measure and improve, not reasons to omit required validation. The [CI guide](../../scripts/ci/README.md#test-tiers) is the implementation reference.

For a stable release, the release lead prepares the scope, candidate artifacts, and bilingual draft notes. Another relevant maintainer confirms validation results before publication. The pipeline requires an actually protected `release` environment with required reviewers, self-review prevention, and administrator bypass disabled; an unconfigured environment does not authorize publication. Validation must correspond to the commits and artifacts actually released; rebuilding or changing content requires rerunning affected checks. External users must be able to obtain artifacts and complete the stated installation path. Do not overwrite stable tags or replace artifacts under the same version.

The automated candidate smoke uses the packaged CLI and exact image digests on Linux amd64 with both OceanBase CE and seekdb. It checks deployment, authentication, telemetry read-back, recast persistence, and cleanup without source mounts. Native image metadata checks and cross-platform source tests are additional evidence, not proof of deployment on every architecture. Upgrade, recovery, Kubernetes, and other platform commitments need their own applicable evidence and maintainer review. SigNoz retains its independent upstream version. The bundled `ob-labs/signoz-ob` image receives the same product-version alias as the other four service images; its actual digest is recorded in the manifest and pinned deployment variables. Upstream database images are not retagged.

Choose required checks based on the change. Validate installation, integration, and queries for the first release; validate upgrades, data retention, and failure recovery when later changes affect upgrades or storage. Report each platform separately. A local test or a test using mounted source cannot replace validation of every release combination. Fix blockers or postpone the release.

## Release notes and the version page

Each product version has one user-facing GitHub Release body. Stable releases provide English and Simplified Chinese summaries, plus one shared contributor section. PRs describe user-visible changes; releases consolidate new capabilities, fixes, affected components, compatibility, upgrade considerations, and known issues. The [release-note template](../../.github/RELEASE_TEMPLATE.md) and [credit guidelines](../../CONTRIBUTING.md#recognition-and-release-credits) keep the two language sections and contributor acknowledgments consistent. No extra product changelog fragment is required. Independent packages retain their own changelogs, linked from product notes as needed.

The version page answers only three questions: **Which version is running? Is an update available? What changed?** It reads the installed product release from the deployment manifest, checks the latest stable release, and reuses its notes. Original component builds remain in diagnostics. Different component SHAs are valid when each matches its own manifest entry; they do not by themselves mean a mixed installation. Users can retry the check; the page does not perform upgrades. Frontend and backend build differences appear in diagnostics collapsed by default, without a component audit dashboard.

Clearly distinguish unknown versions, development builds, no stable release, offline operation, and failed checks. None may be labeled "up to date." Update checks must be optional and require no user-provided GitHub credentials. See the [version guide](../version-info.md) for the current implementation and validation entry points.

## Initial release targets

| Version | Target date | Focus |
|---|---|---|
| `0.0.1` | At the open-source launch; the release review sets the exact date | Public availability, installation, integration, and queries, with feedback channels and known limitations |
| `0.1.0` | 2026-10-31 | Working Gitflow, basic PR checks, and daily builds; a clear upgrade or data migration path from the first release |
| `1.0.0` | 2026-11-30 | Explicit supported scope, with actual validation of critical integrations, queries, diagnostics, upgrades, and recovery |

These priorities do not require all automation to be built at once. During bootstrap, maintainers may execute checks manually and preserve results, then automate frequent or error-prone steps. Make promised paths work before expanding the support matrix.

The release lead reviews scope and blockers during preparation and again before publication. If targets are unmet, core maintainers record whether to continue with candidates, reduce scope not yet promised, or postpone, explaining the reason and revised plan. An approaching date cannot justify overlooking data loss, unauthorized access, or unrecoverable migrations.

## Incremental implementation

1. Bootstrap governance and contribution entry points: use a concise PR template with the default branch workflow. The body check validates section completeness, and maintainers review actual results.
2. Product versioning and the version page: implement a shared version source, current version, update checks, and release notes, with relevant code checks.
3. Test tiers and candidate automation: implement path selection, Linux daily checks, broader release checks, fixed artifact identities, and publication gates. Validate hosted behavior and failure handling before relying on them.
4. Gitflow naming: use purpose-based branches and typed PR titles. Check them in the existing PR job, along with body completeness.
5. Gitflow routing and remote protection: establish the stable and integration branches, configure confirmed owners, required checks, protected release approval and release backmerges, and update Daily build and promotion inputs.

Implementation remains `in_progress`. The naming check is independent of branch protection and release permissions. Source code or workflow files alone do not establish release acceptance.

## References

- [Pride Versioning](https://pridever.org/): the source of the versioning narrative; this project defines its own compatibility responsibilities.
- [A successful Git branching model](https://nvie.com/posts/a-successful-git-branching-model/): a reference for branch responsibilities and release backmerges.
