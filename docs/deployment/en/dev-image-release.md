# Development builds and release validation

[Deployment guides](../README.md) · [简体中文](../zh-CN/dev-image-release.md) | English

[`Release images`](../../../.github/workflows/release-images.yml) builds and qualifies a fixed product candidate. Manual runs default to **`publish=false`**. They retain reviewable assets and acceptance without creating a product Release or moving product aliases. The standard public channel uses source-compatible service images and the ordinary CLI. Its GitHub repository is `antgroup/ai-vision`. The current product target is `0.6.0`; check the Releases page for available downloads.

Native compilation is available through an [optional candidate workflow](../../../.github/workflows/native-candidate.yml). The [temporary private Skills delivery](../../../deploy/native-packaging/release-contract.md) retains its own source-exclusion and access gates. User-facing Skills remain in `ob-labs/ai-vision-skills`. The nightly schedule remains paused.

## Channels and test scope

| Run | Source and version | Result after validation |
| --- | --- | --- |
| Manual `daily`, default `publish=false` | Current default branch; `VERSION-daily.YYYYMMDD[.N]` | Frozen candidate assets and acceptance; no Release or product aliases |
| Manual `dev`, default `publish=false` | Current default branch; `VERSION-dev.YYYYMMDD.RUN_NUMBER[.ATTEMPT]` | Frozen development candidate; no Release or product aliases |
| Manual trial alias | Selected pushed branch; unique non-version alias and selected candidate channel | Candidate assets only; publication is rejected before version-tag writes |
| Qualified Daily/Dev promotion | Exact successful candidate run/attempt, still at the current default-branch head | Versioned prerelease and rolling entry using the original frozen assets |
| Manual `release_preview=true` | Selected branch and exact `VERSION` | Full formal-release matrix and frozen artifacts with publication disabled |
| Push of a `v*` tag | Tag matches `VERSION` and is reachable from the default branch | Reviewed stable Release or RC prerelease after protected approval |

An explicit `publish=true` manual Daily/Dev run still publishes after the same source/artifact gates. The separate promotion path is useful when a candidate must be reviewed before publication; it does not rebuild.

Daily uses a fixed snapshot date. Set `snapshot_date` as `YYYYMMDD` and retain it through retries or work crossing midnight. Revision `1` omits the suffix; the next accepted snapshot for the same date uses `.2`. The version format is `0.6.0-daily.YYYYMMDD`, or `0.6.0-daily.YYYYMMDD.2` for revision 2. A rerun keeps the selected version; run ID/attempt only distinguish build identity. Changed accepted content requires a fresh Daily revision.

A formal RC uses `0.6.0-rc.1` in VERSION and the matching Git tag `v0.6.0-rc.1`. Pushing that tag starts the reviewed RC workflow. Use a manual Daily/Dev branch trial when validating CI setup. Its GHCR staging tags identify the exact run and attempt.

If `snapshot_date` is blank, CI uses the original workflow run's `created_at` in Asia/Shanghai, including reruns. `daily_revision` is a positive integer string, default `1`. Manual Dev versions keep their run-number format. Stable/RC versions continue to come from `VERSION`. Selecting the Daily channel for a branch trial does not bypass default-branch publication gates.

PRs select Linux basic checks by changed paths, including version/release PRs. Integration is explicit; full Daily uses Linux source coverage, while full release callers expand supported OS matrices. See [CI policy](../../../scripts/ci/README.md). Source checks and arm64 image compilation do not establish artifact deployment acceptance on those platforms.

## Candidate validation and publication

1. Resolve the exact source SHA and approve an immutable upstream SigNoz index. Stable and RC builds use the index recorded in [the dependency lock](../../../deploy/config/release-dependencies.json) for the numeric product version. For public candidates, stage and verify the same index as `ghcr.io/antgroup/ai-vision/data-backend`.
2. Confirm basic CI passed on the selected source, then build plugins and the matching ordinary CLI.
3. Build changed product-owned images on Linux amd64/arm64 builders, or verify both architectures reused from a published Release. Record their actual build identities and digests under unique candidate tags.
4. Assemble checksummed CLI/assets, `release-manifest.json` and both pinned vars files. The manifest binds `source_repository`, `release_tag`, `product_version`, source SHA and original build ID.
5. Verify anonymous access to all five public candidate image references. Run the full source checks with the staged owned images and the pinned SigNoz precision fixture. Deploy the packaged Linux amd64 CLI and exact release images separately against OceanBase CE and seekdb. Require health, authentication, OTLP ingestion and API readback (including native memory operations), recast persistence, image identity and cleanup checks.
6. Freeze the exact asset sizes/hashes and both sanitized passed smoke receipts in `candidate-acceptance.json`. Candidate-only runs stop here.
7. On explicit publication, validate candidate provenance and current remote state, tag the five public packages, publish the versioned Release, then download and compare its actual asset bytes before moving rolling aliases. Stable/RC additionally requires reviewed notes and protected approval.

The public manifest pins five service images under `ghcr.io/antgroup/ai-vision/`. The `frontend`, `backend`, `ingest-gateway`, `diagnosis` and `data-backend` packages receive product-version and rolling tags after qualification and explicit publication. The `data-backend` package carries a digest-pinned copy of an approved `ghcr.io/ob-labs/signoz-ob` index. `data_backend_mirror` records both references with the same digest. SigNoz owns its upstream tags and build evidence. The OceanBase CE and seekdb references remain pinned to their upstream images.

Artifact deployment acceptance covers **Linux amd64** with both databases. It does not cover upgrades, Kubernetes, every real model/provider or unexecuted platforms. Native compilation and image/CLI platform availability have separate evidence.

On disposable GitHub-hosted Linux x64 runners with less than 64 GiB free on the SDK filesystem, artifact smoke reclaims disk space by removing only the unused preinstalled Android SDK. Public smoke uses a fresh Docker configuration when it reaches the same daemon, and otherwise keeps the runner's configured Docker context. The separate registry job verifies anonymous access to the candidate digests in either case.

## Repository setup and candidate triggering

Confirm these prerequisites before expensive builds or publication:

- For Daily and Dev, set `AIVISION_SIGNOZ_IMAGE` or the manual `signoz_image` input to an approved `ghcr.io/ob-labs/signoz-ob@sha256:...` index containing both Linux architectures. For a formal version, review the image digest, upstream source commit, and build receipt in the dependency lock. The lock applies to the corresponding RC and Stable versions.
- Confirm that the product repository is public and the intended actor has package and Release access. Candidate runs stage bytes in GHCR even when `publish=false`. The first authorized staging run can create private packages and stop before deployment smoke. Configure repository association, workflow access and public visibility for all five packages in GHCR. Then rerun the candidate so the registry gate can verify anonymous access.
- For stable/RC, configure the `release` environment with required reviewers, prevention of self-review and administrator bypass disabled. Prepare an asset-free draft for the exact tag with reviewed English and Chinese notes using the [release template](../../../.github/RELEASE_TEMPLATE.md). The protected publishing job resolves that draft and verifies its uploaded assets before publication.
- An optional `AIVISION_E2E_DOCKER_LINUX_RUNNER` must identify a trusted Linux Docker runner with enough resources for the full stack.

The release workflow qualifies the five core services. Optional OpenShell installation is documented separately in the [sandbox plugin guide](sandbox-plugins.md).

In **Actions → Release images → Run workflow**, select the default branch, `tag=daily`, the fixed snapshot/revision and **`publish=false`**. `candidate_channel` accepts `auto`, `daily` or `dev`. For a pushed branch trial, select that branch, set a unique `tag` and an explicit candidate channel, and keep `publish=false`. `latest`, version-like aliases and generated `candidate-*` names are reserved. Candidate builds queue per Git ref. Publication jobs share one queue for package aliases and Releases.

```bash
# Select the actual snapshot date/revision before running.
SNAPSHOT_DATE=YYYYMMDD
gh workflow run release-images.yml -R antgroup/ai-vision \
  --ref main -f tag=daily -f publish=false \
  -f snapshot_date="$SNAPSHOT_DATE" -f daily_revision=1
gh run list -R antgroup/ai-vision --workflow release-images.yml --limit 5
```

This assumes the external dependency variable is configured. Otherwise also pass `-f signoz_image=IMAGE@sha256:DIGEST` with the approved reference. A newly added workflow is dispatchable by name after reaching the default branch.

The release plan records the SDK Docker coverage and selection reason.
A Daily with a verified published baseline, an unchanged SigNoz digest and
only allowlisted documentation or presentation changes uses the fixed SDK
smoke. The same run still needs the complete SDK/browser job and the usual
artifact deployment acceptance. The [CI guide](../../../scripts/ci/README.md)
describes the full-coverage conditions.

## Rehearse a formal release before tagging

Run the image workflow on the proposed release branch with `release_preview=true`
and `publish=false`. Leave the Daily date, revision, and channel settings at
their defaults. The run uses the exact `VERSION`, pinned SigNoz dependency and
full Release matrix. Review the frozen acceptance before merging. After merging,
push the matching version tag at the accepted `main` commit. The tag run
qualifies its own build and then waits for approval in the `release` environment.

## Qualify the All-in-One Personal Edition

Stable and RC builds include the Linux amd64 and arm64 single-container edition
with embedded seekdb. On a Daily run, select `personal_edition=true` and
`publish=false` to qualify those same platforms. Formal previews also qualify the
Windows x64 CLI alongside the Personal Edition and distributed artifacts. The native
jobs consume the frozen distributed candidate after both database smokes pass.
Both run actual installs from the candidate files and record packaged image and
CLI identities and persistent first-use results. Each architecture also checks
native memory operation readback on the packaged data backend. The final `release-assets`
and `release-acceptance` artifacts contain the original distributed assets,
the Personal Edition installers, manifests, archives, and verified receipts.
Use the final acceptance for promotion and the versioned Release for downloads.

## Review and promote the frozen candidate

A successful candidate uploads `release-assets-<attempt>`, `release-smoke-<attempt>-oceanbase`, `release-smoke-<attempt>-seekdb` and `release-acceptance-<attempt>`. The last artifact contains `candidate-acceptance.json`; smoke artifacts contain only sanitized typed receipts, without raw logs or private runtime configuration.

After reviewing the successful candidate and authorizing publication, run:

```bash
CANDIDATE_RUN_ID=...
CANDIDATE_ATTEMPT=...
gh workflow run promote-release.yml -R antgroup/ai-vision \
  --ref main -f candidate_run_id="$CANDIDATE_RUN_ID" \
  -f candidate_run_attempt="$CANDIDATE_ATTEMPT"
```

Promotion requires the latest successful attempt of the official `release-images.yml` workflow from the **current default-branch head**, both successful database smoke jobs and exact frozen assets. A newer rerun supersedes old acceptance. Stale/foreign/failed attempts, changed assets, branch trials and stable/RC candidates are rejected. The original candidate build ID remains in the assets; the publisher records its own distinct run/attempt and uploads `promotion-receipts-<publisher-run-id>-<attempt>`. No GitHub run identity is substituted.

## Download and deploy the tested combination

For a first public install, use the [stable Release](https://github.com/antgroup/ai-vision/releases/latest), or follow the [Daily pointer](https://github.com/antgroup/ai-vision/releases/tag/daily) to a specific versioned preview when explicitly selected. Download the CLI, `SHA256SUMS`, `release-manifest.json` and one vars file from that same versioned Release. Choose `release-images.vars.yaml` for OceanBase CE or `release-seekdb.vars.yaml` for seekdb. Verify the checksums, copy the selected vars file to `deployment.vars.yaml`, configure it for your host, and run `aiv cast --vars-file ./deployment.vars.yaml`. The vars file selects the public image digests. A bare `aiv cast` on a fresh installation selects the rolling `latest` tag for the five public service packages.

For an authorized candidate trial, use the matching Actions assets instead. Replace the placeholders with the actual successful run ID and attempt:

```bash
RUN_ID=...
ATTEMPT=...
gh run watch "$RUN_ID" -R antgroup/ai-vision --exit-status
gh run download "$RUN_ID" -R antgroup/ai-vision \
  -n "release-assets-$ATTEMPT" -D release-assets
cd release-assets
sha256sum -c SHA256SUMS
tar -xzf aiv-linux-x64.tar.gz aiv
install -d "$HOME/.local/bin"
install -m 755 aiv "$HOME/.local/bin/aiv"
export PATH="$HOME/.local/bin:$PATH"
command -v aiv
aiv --version
cp release-images.vars.yaml deployment.vars.yaml
chmod 600 deployment.vars.yaml
```

These commands target Linux amd64. Complete [durable PATH setup and fresh-shell verification](../../../aivision-cli/README.md#make-aiv-available-in-new-terminals), then add deployment-specific secrets/settings using the [Docker guide](docker.md), preserving digests. Keep the configuration and manifest in a durable private directory. A trial needs an isolated host or [separate names, ports and state](../../../aivision-cli/README.md#running-an-isolated-second-stack-same-host).

```bash
aiv cast --namespace aivision --vars-file deployment.vars.yaml
aiv status --namespace aivision
aiv logs --namespace aivision -n 80 backend
```

Retain the actual target, namespace, workdir and blueprint on subsequent operations. Repeat the same `cast` after configuration changes; an authorized rebuild uses `aiv redeploy --namespace aivision`, and a data-preserving stop uses `aiv destroy --namespace aivision`.

When upgrading an existing deployment, update all five image overrides and the release metadata in the original private vars file. Preserve the original database type and configuration, credentials, namespace and storage. If the file uses role aliases such as `DATA_BACKEND_IMAGE`, update that spelling since it takes precedence over `SIGNOZ_IMAGE` in the same file. Check environment and CLI overrides before casting. About checks each observed component against its own manifest entry, including reused builds that retain their original SHA and version.


For an existing OpenShell deployment with unkeyed enrollment fingerprints, first
deploy a backend that writes keyed fingerprints and can still verify old retries
([introduced in #8](https://github.com/antgroup/ai-vision/pull/8)). Wait at least
60 minutes after the last backend instance that writes unkeyed fingerprints
stops serving before deploying the backend that requires keyed fingerprints.
If you skip the intermediate backend, pause issuance of new OpenShell setup
commands at least 60 minutes before upgrading and keep it paused until the
upgrade finishes.
Keep saved connections and their owned host state. If an earlier command can no
longer confirm its registration, generate a new command from the
[OpenShell setup drawer](sandbox-plugins.md#recommended-install-on-the-ai-vision-deployment-host).

## Reuse, failures and recovery

The latest published versioned product Release is the default reuse baseline; `reuse_release` selects one explicitly. CI verifies its manifest, checksum and source tag. Unchanged tracked build contexts may retain their image digest and actual original identity; missing metadata or changed inputs trigger a new build. `force_rebuild=true` refreshes builds/base images. Source and deployment test success are never reused.

Source checks run independently of release preparation. Missing SigNoz configuration or registry access blocks artifact production, while source regressions still run. Source-fixture SigNoz v1 readback is not the candidate's native Langfuse v1 readback.

After a candidate failure, use **Re-run all jobs** to keep CLI, images and checks within one attempt. Preserve the Daily snapshot date/revision; changed accepted content requires the next revision. Unique candidate image tags can remain after failure, but candidate-only runs never move user aliases.

Advancing `daily` requires both a newer workflow attempt and a product version that does not move backward. The publisher verifies the current version through its published versioned Release, authenticated manifest/checksum and live tags, then compares the numeric base version, snapshot date and revision. A later run cannot replace a newer date or revision; unknown or inconsistent prior provenance stops publication.

For an interrupted Dev alias upload, retry promotion with the original candidate run and attempt. If a direct `publish=true` candidate run failed only in the development publication job, set `recover_dev_publication=true`; the verifier requires both smokes and frozen acceptance to have passed in that exact attempt and rejects every other failure or cancellation. Recovery verifies the frozen acceptance, exact published versioned assets, original Dev build/source pointer and live tag before restoring the same bytes. It preserves the original candidate notes and records the recovery publisher separately. Newer, changed or unprovable alias state requires inspection. A fresh Dev publication verifies uploaded alias assets before committing its completion notes.

If publication is interrupted, preserve receipts and inspect exact assets, digests and current remote state before recovery. Do not rebuild different bytes under an accepted version, overwrite newer/unknown aliases or manually bypass a rejected gate. Publication spans several GitHub/registry writes and is not atomic. Billing/access failures, unavailable runners and skipped required checks remain failures or missing evidence.

The separate manual Product Compose lifecycle requires at least 14 GiB of Docker memory. Configure `AIVISION_E2E_DOCKER_LINUX_RUNNER` with sufficient Linux x64 capacity if the default runner falls short. Capacity is checked before checkout and build work.
