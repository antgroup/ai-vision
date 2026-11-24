# CI and PR validation

During project bootstrap, use ordinary Markdown to record validation and screenshots. The [PR template](../../.github/PULL_REQUEST_TEMPLATE.md) has four required level-two headings: `Problem and impact`, `Validation`, `Screenshots and interaction`, and `Compatibility and unverified scope`. Fill each section with actual information. Documentation-only changes can record link and diff checks; explain when there is no visual impact.

[pr-evidence.yml](../../.github/workflows/pr-evidence.yml) checks the [Gitflow branch and PR title convention](../../CONTRIBUTING.md#name-the-branch-and-pr) and checks that each of the four body sections appears once with actual content. Results appear in the `PR material completeness` job and Actions summary. No JSON declaration, run/job ID, or Git tree fingerprint is required. Maintainers assess the diff and verify that the evidence covers the tested SHA, environment, and behavior. Include failures, unrun checks, and environment limits in the PR.

## Security and initial setup

The workflow runs on `pull_request` with only `contents: read`. It checks out the event's `base.sha` and runs the checker already present in that target revision. It reads the title, branch and body as data from `GITHUB_EVENT_PATH`. It does not interpolate PR content into shell commands, execute body commands, or download attachments. Fork PRs follow GitHub's approval and read-only permission rules.

If the base does not yet contain a checker, the job displays a bootstrap notice and skips validation. A PR changing the checker runs the old policy in this job; exercise the new policy in the metadata CI suite or locally. After the policy reaches the target branch, verify with a compliant PR, a bad branch or title, a corrected title or body on the same commit, and a fork PR before making the check required on a protected branch.

A title or body edit triggers the metadata check without triggering the separate source CI workflow. Results apply to the event snapshot. Automated edits made with the repository's `GITHUB_TOKEN` may not trigger a new run, so review the current title, source branch, body, and checks before merging.

The workflow checks PR metadata. It does not restrict branch creation or pushes, enforce protections or approvals, verify branch ancestry, or configure merge methods. Ordinary PRs still use the default branch unless a maintainer directs them elsewhere. Special release, hotfix, and backmerge routes have repository and target checks; the migration to `develop` for ordinary PRs and Daily builds remains separate.

## Local validation

Use Python 3.12 and install PyYAML 6.0.3 in your validation environment; the workflow contract tests read the actual YAML workflows.

```bash
python3 -m pip install PyYAML==6.0.3
python3 -m unittest discover -s scripts/ci -p 'test_*.py' -v
python3 scripts/ci/pr_evidence.py --body-file /tmp/pr.md \
  --branch cicd/check-pr-names --title "ci: Check Gitflow branch names and PR titles"
python3 scripts/ci/pr_evidence.py --event /tmp/pr-event.json --summary /tmp/pr-summary.md
```

Use `--body-file` alone for a body-only preflight, or supply `--branch` and `--title` together to check an ordinary PR's names as well. Use `--event` for the full policy, including release and bot context. Use a GitHub PR event payload for that mode. Output contains fixed diagnostics and does not echo PR text or branch names.

## Test tiers

[ci.yml](../../.github/workflows/ci.yml) is the source-test entry point. PRs test the merge checkout; branch pushes, manual runs, and release callers test their event's exact SHA. [plan.py](plan.py) reads the complete Git diff, including deleted paths and both sides of renames. It first classifies affected checks, then applies the selected validation profile. Missing diffs and unclassified runtime paths select every check within that profile, never an empty success.

| Profile | Selection and platform scope | Purpose |
|---|---|---|
| `basic` (automatic PR/push and manual default) | Affected source checks on Linux; both isolated databases when database tests apply; native production unit contracts without the isolated Agent installer or fake49 execution | Required PR feedback without deploying a product stack |
| `integration` (explicit manual run) | Framework and diagnostic platforms, official native fake49 and Weaver, native platform fake49, and deployment smoke on Linux | Validate deployment and cross-service contracts separately |
| `full` (explicit caller) | All checks; `daily` uses Linux and `release` expands supported OS matrices | Existing complete-tier compatibility; separate from the private native delivery pipeline |

Basic and integration profiles require `tier=pr`. Ordinary PRs, including version and release/hotfix branch changes, remain Linux basic runs; broader qualification must be selected explicitly. Only `full` retains the version/branch promotion to the release tier. Existing reusable release callers default to `profile=full`; this preserves their interface without qualifying their deployment procedure.

Run basic checks manually with `gh workflow run ci.yml --ref <branch> -f tier=pr -f profile=basic`. The separate [Deployment and platform integration](../../.github/workflows/integration-ci.yml) entry point is manual-only and calls `profile=integration`; it has no PR, push, or scheduled trigger and does not publish. A newly added workflow becomes available by name after reaching the default branch; the existing `CI` entry point also accepts an explicit integration profile. Daily/Release qualification is outside this basic-CI acceptance and separately tests the actual source-compatible product artifacts. Native compilation and the private Skills distribution have distinct contracts; source profiles do not establish their acceptance.

The plan lists affected checks outside the selected profile as `deferred_checks`. Deferred integration is **not run**, not passing or inapplicable. A basic green check is not deployment approval: authors and maintainers must still select and record relevant integration before accepting changes that require it. `ci-gate` accepts only basic/full plans; integration-only runs use a distinct `Integration checks passed` check and cannot substitute for the basic required check.

Basic native production units are scoped by plugin: Codex, Claude, Pi/Pireplay,
OpenCode, DSH/replay, Hermes and Kimi. Each affected group also retains the shared
onboarding and harness contracts. Shared libraries, scripts, schemas, peer edition
configs, CI changes, unavailable diffs and unclassified runtime paths retain all
groups. Daily/Release and selected native source execution also retain all groups.
The plan carries `native_unit_groups` separately from the Boolean family selectors;
the required gate rejects missing, invalid or incomplete support scopes.

The reusable native suite accepts `unit_scope` as `all` (the default for existing
callers) or a nonempty JSON list of group names. A targeted manual unit rerun uses
`tier=pr`, `units=true`, `source=false`, `platform=false` and a scope such as
`["pi","onboarding","harness"]`. The scope is validated before Rust/Node setup;
unselected compilers and plugin commands do not run. Selected source execution or
a non-PR tier expands a valid scope to all groups. Empty, duplicate, unknown or
malformed scopes fail instead of creating an empty success. Targeted manual scope
is partial evidence; it does not replace an ordinary PR's generated basic plan.

The independent deployment asset workflow skips heavy setup, Go/package/schema
checks and Compose rendering only for existing regular, unpackaged `.md`/`.rst`
files under `docs/deployment/`. It reads the complete diff and canonical package
inventory, including source and destination paths. Packaged Kubernetes Markdown,
configuration, new/renamed or executable/symlinked prose, mixed changes, manual
runs and unknown/unavailable diffs retain full asset checks. A cheap scope summary
states when heavy checks were not run; scope failures still fail the job.

For ordinary PRs:

- Prose and Issue forms run the metadata/policy checks. ADR changes also validate the AgentLens records, generated indexes, and English/legacy authoring compatibility. Executable deployment configuration is not treated as prose.
- Metadata checks validate capability evidence and generated semantic-policy documentation on every PR. They run after scope selection even if planning fails; the final gate still rejects a failed planner. Go setup, compiler-backed build/CI regressions and actionlint run when the plan selects a source, deployment or ADR family. Documentation-only plans retain the lightweight validators.
- Frontend changes run lint, unit tests, type checking, and the production build once. The Linux job also builds the development-mode fixture bundle once and uploads it under an artifact name containing the workflow run ID and source SHA. Failed browser checks reuse that bundle on retry. Rerunning the producer replaces the bundle for the same source. Four disjoint Playwright shards download that exact bundle and run `pnpm exec playwright test --shard=N/4`, with at most two shards running concurrently; every shard must pass. The local `pnpm test:e2e` command still builds and serves its own fixture bundle through Vite preview. Each test case has a 30-second limit. Each shard gets eight minutes for tests and the runner job has a 20-minute limit including installation.
- Backend changes run API contracts and both database backends. Schema, authentication, ingestion, and trace-query changes also record the affected integration checks as deferred.
- AgentLens changes run diagnostics contracts and database tests; affected platform checks are deferred. Collector changes compile and test the current gateway and select source ingestion contracts.
- CLI changes run its unit and installer checks. Blueprints, deployment code, and deployment configuration record deployment smoke as deferred; they do not deploy during a basic PR run.
- Native integrations install their unit-test SDK/engine dependencies and run production TypeScript/Rust/Hermes and harness contracts, including locked common-edition Codex and Claude builds/tests and the production installer/onboarding suite. The isolated official Agent installer, fake49, resumed Hermes processes and Weaver remain source-integration checks. Framework SDK contracts still run locally with their fake providers. Workflow/policy changes select all Linux basic checks and record the full integration requirement.

Codex and Claude basic checks compile their common-edition binaries and run `cargo test --lib --bins`. Source integration selects `--all-targets`, including async process and latency contracts. Claude source integration uses release optimization to measure the shipped hook's execution path, with debug assertions and overflow checks enabled for the test build. These Rust tests use one test thread because their fixtures share process environment and local I/O state. The production onboarding tests run with the basic native checks.

Release matrices also collect the portable tests on Windows. POSIX shell execution for copied OpenShell commands runs on Linux and macOS; its command-building, UI and authorization contracts still run on Windows. The Codex diagnostic Docker driver fixture requires POSIX host hooks and Linux containers, so that fixture class is explicitly skipped on Windows. This does not skip the Codex Rust or onboarding checks. Test filesystem paths use decoded file URLs to support Windows drive letters and checkout paths containing spaces.

Onboarding fixtures preserve real child-process input, output and exit behavior on Windows by launching their Python programs through the current interpreter. POSIX permission-bit assertions remain on POSIX hosts. Managed native hook registration uses the command format emitted on each platform; these fixture checks do not qualify a Windows release artifact.

The owned diagnostics platform's fixed `codex-read-only` Quick smoke requires a complete, evaluated `tool_success` result and matching Records for every selected trace. It checks the expected successful-read evidence against the frozen Task and Record hashes. A completed Task with skipped, failed or empty rule results cannot pass this gate. General Quick lifecycle and retest fixtures still retain their legitimate skipped-result cases.

The framework, diagnostics, native-agent, and deployment workflows are reusable suites with manual entry points. They no longer independently trigger on the same PR. The duplicate frontend runs and native database matrix have been removed; the framework database suite owns the shared Django database tests, while diagnostics retains its distinct AgentLens transaction and migration tests.

When selected in the PR tier, the framework and diagnostic database jobs run
both OceanBase and seekdb on Linux x64 (`ubuntu-24.04`). Daily and formal release
require both Linux architectures, with ARM64 on `ubuntu-24.04-arm`. For a
targeted Linux run, choose `tier=daily` and `database=true` in the relevant suite
and turn off its other selectors. Each selected architecture must pass the suite's
existing gate.

The two database helpers accept `--platform linux/amd64|linux/arm64`, defaulting
to amd64 for existing callers. Workflows pass the matrix platform to both pull
and run. The helpers check the image platform selected for the running container
before executing SQL and record the requested and actual platforms in
`summary.json`.

These two jobs use the multi-platform official database index pins already
selected in `release_assets.py`. The seekdb index replaces the former amd64-only
child pin; other platform suites and their pins are unchanged. Each architecture
must be qualified with its own results; earlier runs of the old seekdb image do
not qualify the new pin. Matrix addition is not a claim of a successful hosted run.

Formal release retains portable macOS source and CLI checks. The separate manual
[Intel macOS Docker](../../.github/workflows/intel-macos-docker.yml) workflow runs
on `macos-15-intel`. It requires both database suites, the full framework SDK
Docker matrix, diagnostics fake34/Quick API, and native fake49 platform checks.
Every selected check must succeed. Formal release continues to require all Linux
database and platform checks, including the Linux SDK/browser suite.

To run the Intel gate, match the workflow ref to the candidate's source SHA and
pass the full verified `release-manifest.json` from that candidate. The workflow
checks the manifest's source SHA against the checkout and uses the same public
image projection and pinned SigNoz precision fixture as release source CI. Follow the
[dispatch recipe](../../docs/deployment/en/dev-image-release.md#qualify-docker-on-intel-macos).

The hosted OceanBase container on Intel macOS 15 reached Observer's first
connection but returned MySQL error 8001 at the 600-second SQL readiness deadline
under [VZ](https://github.com/antgroup/ai-vision/actions/runs/45219265697/job/137848768935)
and [QEMU](https://github.com/antgroup/ai-vision/actions/runs/45231711684/job/137892704669).
The [macOS 26 Intel QEMU control](https://github.com/antgroup/ai-vision/actions/runs/45236390465/job/137909170764)
reproduced that timeout. Report Intel macOS Docker as unverified for the
candidate until the separate gate passes. Docker acceptance on macOS ARM64
requires a physical or suitably configured self-hosted Mac because hosted Apple
Silicon runners lack nested virtualization. See the [GitHub runner limitations](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#limitations-for-arm64-macos-runners).

The Windows release source jobs record Codex and Claude PreToolUse p95 and
the parent latency for the Claude PostToolUse 1 MiB queue handoff. These
latencies are report-only on the Windows release runner. The actual handoff
must succeed. Windows hook functionality, Native runtime cases, semantic
checks, and x64 CLI artifact smoke remain mandatory.
The Codex prompt-seed reuse test requires the PreToolUse parent to return in
under 120 ms on Windows release runners and under 50 ms on Linux and macOS.
It checks the hook control response and worker state on all three platforms.
Run [Windows Native Hook Performance](../../.github/workflows/windows-native-performance.yml)
on the candidate ref for independent strict performance acceptance. It requires
Codex prompt-seed parent latency, Codex internal p95, and Claude internal and
process wall p95 below 50 ms. It also requires Claude's 1 MiB PostToolUse
parent latency below 100 ms.
Linux release source jobs enforce their original thresholds. On macOS release
runners, the Codex delayed-worker startup test requires the parent to return
in under 120 ms, ahead of the injected 150 ms delay, and verifies that the
queue drains. Claude PreToolUse requires internal and process wall p95 below
100 ms. Codex PreToolUse p95 remains strictly below 50 ms. Both Linux and
macOS release runners enforce the 1 MiB handoff parent latency below 100 ms.

The diagnostic database gate runs the real `/proc` descendant-cleanup test on
Linux. On macOS it explicitly deselects that one Linux-only test and records the
excluded node in `checks.platform_exclusions`. It checks the exact deselection
count and still rejects every skipped test, so unrelated omissions cannot pass.

For a targeted framework browser repair rerun, manually select `platform=true`, `browser_only=true`, and the required `tier`; set the other suite selectors to `false` to omit their independent checks. This still deploys a fresh platform and requires the SDK/browser acceptance, while the separate Docker SDK profile is unselected. It is partial coverage. `browser_only` defaults to `false` for both manual and reusable calls; the central CI caller leaves it unset, so ordinary PR/Daily/release plans still require both selected platform jobs. The independent NVIDIA opt-in and all selected-check failure/skip gates are unchanged.

For product Daily candidates, the release plan permits the Docker SDK smoke
only with a verified published baseline, the same upstream SigNoz digest, and
a complete diff limited to ordinary documentation or allowlisted frontend
presentation files. The plan records the selected coverage and its reason.
The Docker smoke checks 20 fixed Langfuse 4 and NeMo Relay cases against a
fresh OceanBase deployment. The same run must pass the SDK/browser job,
including all 22 Python 5 cases and the JavaScript matrix. Formal Release,
RC, manual default coverage, missing baselines, changed dependencies, and
unclassified paths retain the full 110-case Docker matrix. Any selected
platform job that is missing, skipped, or failed blocks qualification.

To reproduce the bounded browser matrix locally, run `pnpm test:e2e --shard=N/4` from `ai-vision/vfe/` for each `N` from 1 through 4. All four results are required; a single shard is partial coverage. The unsharded command shares the eight-minute Playwright ceiling. Use the four shards for complete validation.

Each profile gate requires the plan, metadata checks, and every selected family to succeed. Reusable families reject failure, cancellation, missing results, and unexpected skips. Private-image integration remains an explicit trusted-branch operation; a fork does not receive project secrets or an implied integration pass. A separate manual integration run cannot satisfy the ordinary PR's basic check. Basic database jobs use disposable hosted runners rather than the configurable persistent integration runner.

## Runtime and evidence budgets

The manual Product Compose lifecycle checks Docker capacity before checkout, dependency installation or compilation. Its existing 14 GiB minimum requires a suitably sized `AIVISION_E2E_DOCKER_LINUX_RUNNER` for private repositories; standard private Linux runners provide 8 GB. Configure the runner before dispatching. Each lifecycle case checks the actual Docker daemon before its build work.

Compose lifecycle qualification keeps all four database/managed-external cases,
with `max-parallel: 1` and a workflow-wide concurrency group. A new dispatch does
not cancel active qualification or its cleanup. Other integration workflows have
separate concurrency policies; this is not a global quota for a shared runner pool.

PR reruns cancel obsolete PR runs. Daily/release runs are not cancelled by a newer PR. Matrices bound parallelism; jobs have timeouts; locked dependencies and build caches are reused where supported. Broad platform checks are concentrated in release validation instead of every frontend or documentation change.

Product candidate builds serialize per Git ref. Only jobs that publish a
Release or move public package or Git aliases share the publication queue.
The frontend, ingest gateway and diagnosis image builds start after the plan
is ready. The backend waits for the plugin archive. Both groups use one
[image build workflow](../../.github/workflows/release-image-build.yml), and the
image indexes require both groups to succeed.

The diagnostic platform job loads its Codex and deployment CLI images from a randomly named, job-owned Buildx builder. It removes that builder and its cache before database startup and on build failure; loaded runtime images and the host's selected builder remain available. A cleanup failure fails the build step.

The five full-platform jobs (diagnostics fake34/Quick, framework SDK, both framework browser modes, and native fake49) and the manual native candidate builder also run [prepare_platform_disk.py](prepare_platform_disk.py) on a GitHub-hosted Linux X64 VM, after Python setup and before dependency installation or large builds. Both the workflow runner context and the helper's environment guard restrict this operation to the disposable VM. With at least 64 GiB free on the SDK filesystem, it leaves the SDK in place without scanning its contents. Below that reserve it removes only the unused `/usr/local/lib/android/sdk` directory defined by the [runner image's Android installer](https://github.com/actions/runner-images/blob/main/images/ubuntu/scripts/build/install-android-sdk.sh). Supplied Android SDK environment paths must match that constant; a symlinked SDK root or ancestor, unexpected filesystem, or failed removal fails the step. Output contains fixed statuses and numeric capacity facts, without command output or environment values. It does not prune Docker, global caches, repository dependencies, or .NET. Self-hosted and macOS runners receive no SDK cleanup; their operators must provide enough deployment disk. The unchanged OceanBase configuration requires 10 GiB free for its same-filesystem startup precheck after the pinned images are cached (2 GiB datafile, 4 GiB log, and 4 GiB slog reservation); build and service storage need additional capacity. Database sizing and acceptance remain unchanged.

Native semantic validation consumes OTLP evidence from the same workflow attempt. When a selected native source matrix has already succeeded but its downstream semantic job needs a retry, use **Re-run all jobs** so its attempt-scoped evidence is produced again. Release retries also require all jobs, as described in the [release guide](../../docs/deployment/en/dev-image-release.md#reuse-failures-and-recovery).

Initial feedback targets are about 5 minutes for documentation, 15–20 minutes for ordinary source changes, and 45–60 minutes when database or deployment checks apply. These are planning targets, not measured guarantees. The full platform suites have explicit 90–120 minute ceilings and run only for affected high-risk changes or full tiers. Inspect actual job durations and failure rates after hosted runs become available; reduce redundant work or improve fixtures before weakening a required check. Runner capacity, cache misses, and image downloads must remain visible in the evidence.

Source-mounted platform suites and fake-provider scenarios verify their stated contracts. They do not establish acceptance of a newly built image, a real model/provider, an upgrade, Kubernetes, or an unexecuted platform. Artifact smoke uses the built CLI and exact image digests separately; preserve each scope in the release record. Its Linux amd64 candidate gate checks every running container's measured platform and image config ID during initial deployment and recast; receipt generation independently rejects missing, incomplete or non-amd64 platform evidence. Check results and sanitized failure artifacts remain with the workflow run. Build identity and the job summary record the tested SHA; evidence from an earlier successful run cannot substitute for a changed candidate.

The public Daily run passes its verified candidate image digests directly to source CI. For a manually selected platform suite, set the repository variable `AIVISION_E2E_PLATFORM_IMAGES_JSON` to a JSON object with pinned image references for `oceanbase`, `signoz`, `gateway`, `backend`, `agentlens`, and `frontend`. Basic CI needs no image fixture variable.

The standalone seekdb workflow and the candidate smoke use the production blueprint, native executable probes and authenticated Langfuse v1 trace readback. For a standalone seekdb run, set `AIVISION_E2E_DEPLOY_IMAGES_JSON` to the five pinned service images built for this contract. The Daily workflow passes the exact candidate deployment images to seekdb and uses the independently pinned precision fixture for framework source checks.

## Daily and release handoff

The product release builds and qualifies the five core service packages. Optional OpenShell components are installed separately.

Repository CI must remain independently usable when this repository becomes open
source. Basic PR/push checks and the manual native candidate builder do not require
the Skills repository, a private catalog/Package, cross-repository credentials or
a publishing account. User-facing Skills live in the separate `ob-labs/ai-vision-skills` catalog.

The [Native candidate build and audit](../../.github/workflows/native-candidate.yml)
workflow is manual-only. It validates a successful `ci.yml` run/attempt for the
exact clean source SHA, records its own producer run separately, and requires a
reviewed public `python:3.11.14-slim-bookworm@sha256:<64-lowercase-hex>` base.
Only successful push/manual source runs with an actually successful `ci-gate`
are accepted. A PR run's branch head differs from its synthetic merge checkout;
an integration-only run also cannot replace the basic source gate.
The source workflow and candidate workflow must belong to the same repository,
including when running in a fork. No repository name is hard-coded in that gate.
The workflow becomes dispatchable by name after reaching the default branch.

The first candidate target is native Linux amd64. It builds backend, AgentLens
and x64 onboarding with the central producer, scopes Rust hooks to Linux x64
while retaining the portable Node plugins, then prepares and audits local OCI
images. Ordinary plugin callers retain their all-platform default. The ordinary
source-capable CLI and optional restricted `native_distribution` CLI are separate
versioned artifacts; compilation does not replace the source-development profile.
Candidate metadata uses root `VERSION`, a separate producer run/attempt and an
`rc` build channel; no RC tag or release is published.

Only successful, allowlisted candidate archives and typed provenance/audits are
uploaded. Compiler directories, generated C, raw runtime/compiler logs and private
configuration are excluded. The Docker builder has one compiler job and bounded
resources; the workflow has a 150-minute build ceiling and five-day artifact
retention. These are initial limits, not measured cold-build performance promises.
The automatic dependency-preparation path and this complete hosted candidate
pipeline have not yet been qualified. Local unit/controlled command checks are
recorded separately from actual compilation and OCI audit execution.

This is a native build/content/import/OCI gate, not a complete five-service product
or deployment qualification. Frontend/gateway assembly, external SigNoz, CE/seekdb,
upgrade/data retention, diagnosis, Agent installation/hooks, readiness, shutdown,
models and other platforms require later explicit checks. No registry login/push,
Release, tag or alias write is part of the candidate workflow.

The [native packaging and qualification contract](../../deploy/native-packaging/release-contract.md)
describes the optional compiled candidate, runtime checks, and archived delivery
evidence. Product publication runs through the standard release workflow.

The standard source-compatible product [release workflow](../../.github/workflows/release-images.yml)
defaults manual runs to `publish=false`. In `antgroup/ai-vision`, first run basic
CI on the selected source. The release workflow stages the four owned images,
the ordinary CLI and an exact copy of the selected upstream SigNoz index.
After anonymously verifying all five public image indexes, it runs the full
source checks with its staged service images and the pinned SigNoz precision
fixture. It also deploys the exact packaged Linux amd64 combination separately
against OceanBase CE and seekdb. Candidate-only runs can write candidate images
to GHCR. Product Releases and rolling tags require explicit publication. The nightly
schedule stays paused. Native compilation is optional for this standard channel.
When a package is created private, staging can complete and the anonymous check
will stop the run before deployment. Confirm the package's repository access and
public visibility, then run the full workflow again to obtain a new acceptance
for the exact attempt. See the [product release guide](../../docs/deployment/en/dev-image-release.md)
for repository setup.

Successful candidates upload `release-assets-<attempt>`, sanitized
`release-smoke-<attempt>-<backend>` receipts and
`release-acceptance-<attempt>/candidate-acceptance.json`. Acceptance binds the
source repository/SHA, original run/attempt, product version, exact asset hashes
and both passed database smokes. Raw runtime logs and deployment configuration
are excluded from these receipts.

[Promote qualified Daily or Dev](../../.github/workflows/promote-release.yml)
accepts `candidate_run_id` and `candidate_run_attempt`. It reuses those exact
assets without rebuilding or replacing the publisher's own GitHub run identity.
The helper verifies that the selected attempt is the latest successful official
candidate for the current default-branch head, checks both smoke jobs and frozen
acceptance, and rejects stale, foreign, failed, partial or changed inputs.
Promotion cannot publish stable/RC or a branch trial. The publisher downloads and
compares actual Release asset bytes before moving rolling aliases and records
candidate and publisher identities separately. The workflow uploads
`promotion-receipts-<publisher-run-id>-<attempt>`.

Daily versions use `VERSION-daily.YYYYMMDD[.REVISION]`. The selected snapshot date
is fixed across midnight and retries; if omitted, it comes from the original
workflow creation date in Asia/Shanghai. Revision `1` omits the suffix; another
accepted snapshot for that date uses `.2`. A rerun keeps the selected version,
while run ID/attempt distinguish diagnostic build identity. Accepted versions
are immutable. A branch trial or candidate-only pass is not a published Daily.

The public profile distributes `frontend`, `backend`, `ingest-gateway`,
`diagnosis` and `data-backend` under `ghcr.io/antgroup/ai-vision/`, with package
names that match the deployment roles. The stable component keys in the
manifest continue to identify their implementation. The public manifest binds
the deployed data backend to the exact upstream `ghcr.io/ob-labs/signoz-ob`
index through `data_backend_mirror`. SigNoz retains its own upstream build
identity and tags. Product version and rolling tags cover all five public
packages after acceptance. Database references stay pinned to OceanBase's upstream images.

The latest published versioned product Release supplies the default image reuse
baseline, or `reuse_release` selects one explicitly. Reuse requires verified
unchanged tracked build-context fingerprints and retains each component's actual
original build identity; `force_rebuild` bypasses it. Source checks and actual
CE/seekdb smoke are rerun for the new combination. The smoke qualifies Linux
amd64 only, not upgrade, other platforms, Kubernetes or model quality.

Explicit `publish=true` remains available for a qualified manual run. Stable/RC
publication continues through the tag workflow and additionally requires matching
`VERSION`, reviewed bilingual notes and the protected release environment with an
uninvolved reviewer. The separate promotion workflow cannot bypass those gates.
Owners, package access, the external SigNoz digest and hosted acceptance remain
setup items until verified. See the [release guide](../../docs/deployment/en/dev-image-release.md)
for inputs, downloads and recovery. Merging these files creates no Release,
changes no repository visibility and does not reactivate the schedule.

## Native packaging and historical delivery

The [native build and image guides](../../deploy/native-packaging/README.md)
cover local compilation, CLI and OCI content audits, platform qualification, and
offline evidence. The product workflow publishes to `antgroup/ai-vision`.
User-facing Skills live in the separate [Skills catalog](../../skills/README.md).
The [historical records](../../deploy/native-packaging/release-contract.md#historical-evidence)
preserve the temporary delivery's accepted artifacts, archive receipts, and
platform limits.

References: [GitHub PR events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request), [fork permissions and secrets](https://docs.github.com/en/actions/concepts/security/compromised-runners), and [checkout ref and credential options](https://github.com/actions/checkout#usage).
