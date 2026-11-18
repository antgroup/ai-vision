# Cross-Repository Public Daily Implementation Plan

> Historical implementation record for November 2026. For current release procedures and repository ownership, see the [release guide](../../deployment/en/dev-image-release.md).

**Goal:** Correct the four-repository source/install/CI/publication boundaries and prepare a frozen source-compatible 0.1.0 Daily for end-to-end acceptance.

**Architecture:** Each repository keeps independent source validation. Product release metadata binds an immutable product tag to its own images and external SigNoz digest; Skills/docs consume the same published manifest. Candidate production and actual CE/seekdb smoke precede separately authorized promotion and remote asset readback.

**Tech Stack:** Existing GitHub Actions, Python stdlib tests, Go/vendor, Docker/OCI, Mintlify and standalone shell Skills recipes.

**Spec:** `docs/superpowers/specs/2026-11-03-cross-repo-public-daily.md`

## Global Constraints

- Standard source-compatible delivery; native compilation optional; no repository visibility changes.
- Preserve historical immutable assets and existing user deployments/worktrees.
- At this checkpoint, the former development repository owned product, CLI, and release work. Skills remained external.
- Only four owned product images receive product tags; SigNoz is a pinned external dependency.
- Exact source SHA, workflow run/attempt and frozen asset hashes identify acceptance; no simulated evidence presented as hosted/runtime success.
- Production Release/rolling promotion follows a reviewable qualified candidate and final user approval.

## Review Focus

- Public source deployment must not silently clone internal GitLab/master or the wrong SigNoz repository.
- Same-repo fork PRs must run the intended Go checks; external label approval must not expose organization credentials or mutable head/cache state.
- Public product installation must work without mandatory auth, with explicit private fallback and immutable tag/checksum/manifest consistency.
- Candidate promotion must reject failed/stale/foreign/partial attempts and changed assets before writes; stable approval is separate.
- SigNoz namespace/aliases and historical private delivery must not be accidentally rewritten by product publication.

### Task 1: Product source and release preparation

**Files:** `deploy/blueprints/aivision-source.yaml`, generated CLI asset copies/manifest, `aivision-cli/README.md`, root `VERSION`, relevant deploy/CLI tests and deployment/CI docs.

- [x] Reproduce default-coordinate mismatches; set canonical GitHub coordinates and main branch, retaining explicit source overrides.
- [x] Regenerate canonical embedded assets and verify freshness/CLI source resolution.
- [x] Set VERSION 0.1.0 for the requested release; update only owned release documentation.

### Task 2: Product candidate and publication contract

**Files:** `scripts/ci/release_assets.py`, `release_publish.py`, existing release tests, candidate acceptance/promotion helper and tests as required; `.github/workflows/release-images.yml` and separate explicit promotion workflow.

- [x] TDD manifest source_repository/release_tag and four-image-only publisher behavior.
- [x] Implement downloaded byte-for-byte Release asset readback before rolling writes.
- [x] Make manual candidate generation non-promoting by default, retain source/artifact gates, and allow explicit reuse of the same successful frozen Daily candidate without forging current run identity.
- [x] Reject mismatched/failed candidate provenance, asset mutations and stable approval bypass; record source/SigNoz identities separately.
- [x] Run relevant full CI-policy tests and independent fixed-patch review.

### Task 3: Skills product installation cutover

**Repository:** `/tmp/aiv-open-20261103-skills`, base c97cef5a51b31a4622614a37e6ddea69e45dfb7c.

- [x] Change all four standalone recipes to product ownership and explicit immutable published tags.
- [x] Validate anonymous public transport, authenticated private fallback, manifest identity/checksums/vars and safe CLI replacement with controlled fixtures.
- [x] Retain historical private tag guards explicitly; update references and run independent Skill installation checks.

### Task 4: SigNoz downstream CI and image provenance

**Repository:** `/tmp/aiv-open-20261103-signoz`, base 1e6f290e57764f8a1eb31a73bd116a406909c522.

- [x] TDD repository-identity trust predicates and read-only immutable PR head checks without inherited Primus credentials.
- [x] Constrain credential-bearing upstream-only jobs without claiming inherited base-checkout suites validate PR heads.
- [x] Inject exact source/version/time into image metadata and emit a typed actual digest/platform receipt after a real successful build.
- [x] Run local policy tests/actionlint; retain actual Go/image execution as separate hosted acceptance.

### Task 5: Documentation and coordinated acceptance

**Repository:** `/tmp/aiv-open-20261103-docs`, base 8832f5eee60a3daef9133cfeadbc2d41f8ec9bb5. Original dirty docs checkout is excluded.

- [x] Repair nine localized Skill links, MCP handoff and ownership instructions.
- [x] Document qualified immutable product Daily selection without claiming an unpublished tag is available; align manifest fields across languages.
- [x] Run docs/MCP/build/link checks; independently review all external patches.
- [ ] Commit/push scoped branches and prepare reviewable PRs with true local/hosted evidence; do not broaden published access.
- [ ] Freeze successful code/candidate identities, run actual product acceptance, and present the concrete production publication for final approval.

## Implementation checkpoint — 2026-11-03

- Main implementation: 236 CI-policy tests passed with Go enabled, source coordinates/embedded assets checked, full ordinary CLI tests passed after replacing a fixture-only 400 ms deadline with an observable failure event. Two deliberate wrong-signal overlays failed as expected. Production lifecycle code is unchanged.
- Main candidate/publisher fixed patch `32a0f5cc8c0809d4841f798ea73eb996e2dd69ee4628ca1b21e46d30837b4aab` passed independent review, 31 targeted Python checks, source-coordinate Go regression and actionlint. Final documentation/fixture additions receive a separate review.
- Skills `ob-labs/ai-vision-skills#3`, head `80481b77f481c3914313a9fce89a530b46ce42ea`: 29 catalog/download tests, five Quickstart fixtures and four independent Skill installs passed locally; hosted catalog passed.
- Documentation `ob-labs/ai-vision-docs#29`, head `e77d79b0308260456890387e8f219bde2dcb9743`: local docs/MCP/Mintlify/link checks and hosted validation passed. Site publication remains pending.
- SigNoz [PR #19](https://github.com/ob-labs/signoz/pull/19), head `ba40d613e1617bcb11429cbae2e94144eb0ec7d7`: 13 local policy/provenance tests and four actionlint checks passed. No hosted checks have been created yet; automatic trigger investigation remains open. No source image was built or receipt issued.
- All external patches passed independent fixed-patch review. Two nonblocking documentation clarifications were applied before committing.
- No candidate artifact build, real CE/seekdb smoke, production Release, rolling alias, repository visibility change or existing deployment mutation has occurred. Actual SigNoz digest/source receipt and full product qualification remain release acceptance work.
