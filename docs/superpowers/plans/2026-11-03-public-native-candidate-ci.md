# Portable Native Candidate CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reviewable, manual native candidate build/audit workflow that is independent of temporary private Skills delivery.

**Architecture:** A stdlib candidate driver validates source/producer identities and invokes existing native production and local OCI audits. The workflow reuses a Linux-only option in the plugin builder and uploads an allowlisted candidate directory. Existing basic CI and all-platform plugin callers remain compatible.

**Tech Stack:** GitHub Actions, Python stdlib, Docker, Go 1.25, existing native compiler tools.

**Spec:** `docs/superpowers/specs/2026-11-03-public-native-candidate-ci.md`

## Global Constraints

- Manual only; Linux amd64; one compiler job; no Daily/Release dispatch or publication.
- No private repository, catalog, Package, secret or publisher requirement.
- Source CI and producer identities are separate; source CI must be successful for the exact source.
- Ordinary source-capable and restricted native CLI variants both remain available.
- Runtime base is pristine digest-pinned official CPython 3.11.14/Bookworm; compiler work is never uploaded.
- Local/fake command checks do not establish a real native build or deployment pass.

## Review Focus

- Wrong SHA, workflow path, repository, attempt, cancelled/failed source CI must reject before build.
- User-controlled image/input strings must remain typed data/argv, never shell programs.
- Partial plugin output, unexpected examples and missing/tampered producer evidence must fail closed.
- Source mutation, output/source overlap and raw-log inclusion must reject before acceptance.
- Single-platform opt-in must not silently reduce default plugin/release callers.

### Task 1: Candidate driver and bounded toolchain

**Files:** `scripts/ci/native_candidate.py`, `scripts/ci/test_native_candidate.py`, `deploy/native-packaging/Dockerfile.ci-builder`.

**Interfaces:** `plan --source-root PATH --source-ci-json PATH --source-ci-jobs-json PATH --producer-json PATH --python-image REF --output PATH` emits a neutral candidate plan. The jobs input is the array of attempt-job API pages from `gh api --paginate --slurp`; it must prove the real `ci-gate` succeeded. Only push/manual source runs are accepted. `build --source-root PATH --plan PATH --plugin-dist TAR_GZ --output PATH --work PATH` produces an upload directory and final `candidate-receipt.json`, invoking only local builds/audits. Plan contains `source_sha`, `repository`, root `product_version`, `source_ci`, `producer`, `python_image`, `platform`, and distinct CLI profiles. The plugin tarball comes from the same-attempt `plugin-release` artifact.

- [ ] Write and run negative identity/image/path tests before implementation.
- [ ] Implement plan validation and separate provenance identities using real Git checkout metadata.
- [ ] Implement argv-based native producer, probe/dual CLI builds, explicit provider inputs, artifact-only contexts and local saved-image audits; keep work separate from output.
- [ ] Test orchestration with controlled command fixtures, missing/tampered evidence and unsafe output rejection; label this as controlled coverage.
- [ ] Review actual command contracts against owning scripts; run focused tests.

### Task 2: Opt-in Linux x64 plugin build

**Files:** `.github/workflows/release-plugins.yml`, `scripts/ci/test_plugin_build_scope.py`.

**Interfaces:** typed `linux_amd64_only: false` in manual/reusable inputs. True selects exactly the current Linux x64 Rust matrix row and passes `--platform linux-gnu/x64` to merge verification. Node plugins and default all-platform callers retain their behavior.

- [ ] Write failing scope/caller contract tests.
- [ ] Implement opt-in subset and credential-free checkouts.
- [ ] Run focused scope tests and actionlint; review default and opt-in matrices.

### Task 3: Workflow, legacy trigger and documentation

**Files:** `.github/workflows/native-candidate.yml`, `.github/workflows/release-images.yml`, `scripts/ci/test_candidate_workflow.py`, `scripts/ci/README.md`, `deploy/native-packaging/README.md`.

**Interfaces:** manual inputs `source_ci_run_id`, `source_ci_attempt`, `python_image`; plan job fetches only same-repository run metadata with read-only token, validates plan and uploads it. Plugin reusable job selects Linux x64. Build job consumes same-attempt artifacts, invokes the candidate driver and uploads only successful output. No automatic candidate trigger or registry login/write job.

- [ ] Add meaningful workflow contract tests for manual-only/read-only/no-private-dependency behavior and explicit unrun scope.
- [ ] Remove only the old nightly schedule; retain manual/tag publication boundaries.
- [ ] Implement bounded manual workflow and document portable versus private downstream roles.
- [ ] Run CI-policy/native packaging unit suites, relevant CLI archive checks, actionlint, documentation paths and diff checks.
- [ ] Obtain independent fixed-patch review and fix demonstrated blockers.
- [ ] Record local evidence separately from any later hosted basic checks; do not dispatch the candidate workflow.
