---
title: Native packaging and qualification contract
type: reference
audience: [A3, A4]
runs: no
sources:
  - deploy/native-packaging/build.py
  - deploy/native-packaging/images.py
  - scripts/ci/native_candidate.py
  - scripts/ci/native_cli_archive.py
  - scripts/ci/release_assets.py
  - .github/workflows/native-candidate.yml
  - .github/workflows/release-images.yml
---

# Native packaging and qualification contract

`antgroup/ai-vision` owns the standard source-compatible AI Vision product
release. Its [release workflow](../../.github/workflows/release-images.yml)
produces CLI archives and publishes the five functional image packages under
`ghcr.io/antgroup/ai-vision/` after candidate acceptance and authorization.
The `data-backend` mirror retains the exact approved upstream SigNoz index.
OceanBase CE and seekdb are pinned to their upstream images. The separate
`ob-labs/ai-vision-skills` repository is the user-facing Skills catalog.

Native compilation remains available through the [native candidate workflow](../../.github/workflows/native-candidate.yml)
and local build entry points. The hosted candidate produces scoped Linux amd64
build, content, import and OCI audit evidence in Actions artifacts. Its output
requires separate deployment qualification for any runtime claims. The dated
Skills delivery records preserve prior results and their exact release scope.

## Build and content checks

| Artifact | Build or audit |
| --- | --- |
| Compiled Python | Use the [central producer](build-artifacts.md) for separate target-specific source, compiler work, runtime output, inventory and receipts. |
| Backend and diagnosis images | Use [artifact-only image assembly](build-images.md), auditing every distributed layer and keeping compiler output out of the context. |
| Restricted CLI | Build with the `native_distribution` profile. Select the intended product version and platform, keep the output outside the checkout, and inspect the resulting module, version and VCS identity. |
| CLI archive | Use an executable and exactly four trusted attribution files. The [archive auditor](../../scripts/ci/native_cli_archive.py) checks these bytes and raw tar/gzip structure. Its executable scan checks known first-party Python payloads. |
| Native CLI deployment helpers | Check actual exported Compose mounts, Kubernetes ConfigMaps and execution paths for owned Python fallback source. |
| Reviewed exceptions | Retain exact content hashes and approval reasons for public integration examples. CPython and reviewed open-source dependencies may retain source. |

For a restricted native image, include compiled owned implementations,
migrations, installers and hooks. Preserve required framework data.
Audit image layers and metadata, nested archives and the executable, including
source removed by later layers. Keep compiler directories, generated C,
credentials, private logs, configuration and traces outside distribution.

## Qualification evidence

- Freeze source commit/tree, product version, per-component identity, actual
  build ID or Actions run/attempt, image index and selected child digests, CLI
  and image-platform choices, and artifact and receipt hashes. Keep the original
  build identity when reusing bytes.
- Exercise the actual packaged CLI and matching images for each claimed
  platform/database combination. Record fresh and retained-data migrations,
  SQL and authentication, OTLP/query readback, persisted diagnosis, Agent hooks,
  configured readiness and normal shutdown. Use the exact pinned upstream
  datastore child on the actual native host when claiming native qualification.
- Record emulated, fixture, model, native and unrun checks separately. Respect
  the distinct compatibility of container architectures, CLI binaries and
  Agent installers.
- Keep new evidence immutable for the accepted combination. When an input
  changes, rerun affected checks and record updated evidence.
- If offline scan media is requested, bind its image/platform/config/layer
  inventory, manifest and checksums to the selected bytes. Record package
  readback and vulnerability scan results separately.

## Historical evidence

| Record | Scope |
| --- | --- |
| [Initial service qualification](qualification-2026-10-28.md) | Earlier component/native-platform results with explicitly unrun image/full-stack cases. |
| [Withdrawn first delivery](delivery-2026-10-28-amd64.md) | Historical first amd64 delivery, superseded by revision 2. |
| [Corrected amd64 revision 2](delivery-2026-10-28-amd64-revision2.md) | Packaged native x86 acceptance, later metadata correction, Daily readback and scan archive with the correction's rerun scope. |

The revision 2 record includes the one-time authorized correction and withdrawal.
Keep earlier receipts and describe any retired endpoint in the original scope.
