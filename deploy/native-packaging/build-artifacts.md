---
title: Build native artifact stages
type: how-to
audience: [A4]
runs: yes
verified_on: 2026-10-28
sources:
  - deploy/native-packaging/build.py
  - deploy/native-packaging/requirements-build.txt
  - deploy/native-packaging/test_build.py
  - ai-vision/aivision-py-be/scripts/native_build.py
  - agentlens/native/build.py
  - scripts/build/build_native_onboarding.py
  - scripts/build/native_artifacts.py
---

# Build native artifact stages

Use the central producer to prepare backend, AgentLens, and Agent-installer
artifacts for a later image build. Run it once per target architecture. Product
images and deployment changes are outside this procedure.

## Prerequisites

- A Linux builder running the target architecture, natively or through configured
  emulation. Select `linux/arm64` or `linux/amd64` explicitly.
- CPython 3.11.14 on Debian Bookworm, its development headers, GCC, `readelf`
  (binutils), and the native tools required by the pinned compiler environment,
  including `patchelf`.
- The complete repository snapshot at `/repo`, including both component lockfiles
  and the canonical `deploy/bootstrap/` inputs.
- Separate, fresh work and output locations outside the source tree. This guide
  uses children of `/build-private` and `/artifacts`; all paths are builder-local
  examples.
- Network access for default tool/dependency preparation, or all prepared inputs
  and compiler tools described in step 3.

## Steps

### 1. Select the source snapshot and target

Freeze the repository inputs for the duration of the build. Keep source, work,
output, prepared tool environments, and prepared dependency environments disjoint.
Keep the work/output directories private and exclude them from Git and runtime
image contexts.

Use a fresh output directory for each architecture and each retry. A failed run
can leave partial artifacts and logs. Retain them for diagnosis or discard only
the generated directories owned by that run; do not reuse them as accepted output.
Choose step 2 or step 3 for preparation, then continue at step 4.

### 2. Build with automatic tool and dependency preparation

Run the central script inside the target Linux builder. The default path prepares
an isolated pinned tool environment and frozen runtime dependencies, then builds,
smokes, inventories, and audits the selected components.

```bash title="not executed in this run"
python3.11 /repo/deploy/native-packaging/build.py \
  --source-root /repo \
  --target linux/arm64 \
  --component all \
  --work-dir /build-private/arm64 \
  --output-dir /artifacts/arm64 \
  --jobs 1
```

For amd64, run the same entry point in the amd64 builder with
`--target linux/amd64` and fresh work/output directories. For a component-only stage, select
`backend`, `agentlens`, or `onboarding` instead of `all`.

### 3. Supply prepared inputs when installation is unavailable

Prepare target-compatible tool and dependency environments. The supplied
dependency lockfiles must match the repository's corresponding `uv.lock` bytes
exactly.

| Prepared input | Required contents |
| --- | --- |
| Tool interpreter | CPython 3.11.14 with versions matching [requirements-build.txt](requirements-build.txt) and the system compiler tools |
| Optional separate onboarding tool interpreter | The pinned standalone installer compiler environment; the same tool interpreter may serve both paths when complete |
| Backend dependency environment and lock | Frozen non-development dependencies resolved from `ai-vision/aivision-py-be/uv.lock`; project source is not installed as a dependency |
| AgentLens dependency environment and lock | Frozen non-development dependencies resolved from `agentlens/uv.lock`; matching target Python and architecture |

Supply every input required by the selected components to skip the orchestrator's
tool installation and dependency resolution. Keep prepared inputs outside the
source, work, and output trees. The underlying Nuitka producer can still request
compiler-tool downloads: there is no strict offline switch. For a network-isolated
builder, preload its system/compiler tools and verify the prepared build under
that isolation.

The prepared-input pattern below passed for all three components in one arm64
invocation. The amd64 build exercised each component separately. Paths here are
generic builder locations; the recorded runs used private staging paths.

```bash
python3.11 /repo/deploy/native-packaging/build.py \
  --source-root /repo \
  --target linux/arm64 \
  --component all \
  --work-dir /build-private/arm64-prepared \
  --output-dir /artifacts/arm64-prepared \
  --tool-python /prepared/tools/bin/python \
  --backend-deps-venv /prepared/backend/venv \
  --backend-deps-lock /prepared/backend/uv.lock \
  --agentlens-deps-venv /prepared/agentlens/venv \
  --agentlens-deps-lock /prepared/agentlens/uv.lock \
  --jobs 1
```

If a separate tool environment supplies the standalone compiler, select it with
`--onboarding-tool-python`; otherwise the shared tool environment supplies both
service and installer compilers.

### 4. Accept the output contract

Require a successful process exit and the final `receipt.json`. Failed or
interrupted runs do not qualify output. Inspect the recorded input identities,
target, toolchain, component smoke results, and content-audit results before
passing any stage to an image builder.

Record the source baseline outside the builder as well. A mounted source snapshot
can lack usable Git metadata, leaving receipt `source_revision` null; the recorded
input hashes still identify its selected compilation inputs.

| Component | Central smoke scope |
| --- | --- |
| Backend | Django system check and compiled backend/bootstrap readiness-helper imports; no database initialization |
| AgentLens | Native migration-head discovery, isolated-worker flags, configuration/application imports |
| Onboarding | Native Agent listing and persisted hook-runtime help dispatch |

Database initialization belongs to the AgentLens dependency environment, which
contains its locked SQL driver. A compiled `database-init` helper also exists in
the backend output, but its presence does not qualify it with the backend's
dependency lock. The central backend smoke does not execute it.

| Output below the selected output directory | Consumer |
| --- | --- |
| `backend/runtime/` | Backend application/native bootstrap stage, including its locked dependency environment |
| `agentlens/runtime/` | AgentLens application/native migration/worker stage, including its locked dependency environment |
| `onboarding/linux-gnu-arm64/connect.tar.gz` or `onboarding/linux-gnu-x64/connect.tar.gz` | Backend native installer provider; retain the complete archive/bundle |
| Component manifests, audit reports, and top-level `receipt.json` | Private build verification and provenance; retain outside runtime image payloads |
| Work directory | Private compiler outputs, generated source, caches, and logs; exclude from distribution |

Copy only the selected runtime trees and installer archives into a later
allowlisted image context. Supply a matching CPython 3.11.14/Bookworm runtime base
for the services; the producer does not stage a complete base interpreter or
operating system. Preserve approved configuration/resource data and the complete
installer bundle. Keep credentials and deployment configuration external.

Backend plugin-provider assembly remains a later image step. Combine the
requested architectures' installer archives with an explicit allowlist of catalog,
approved public examples, required non-Python support data, and reviewed plugin
distribution files. This producer does not assemble that complete provider tree;
do not copy the raw installer/helper script directory into it.

### 5. Qualify the actual image and caller combination

Run the affected native regression suites, real fresh/upgrade migrations,
onboarding and authenticated telemetry readback, persisted diagnosis, readiness,
and actual PID 1 shutdown checks against the candidate combination. Inspect every
distributed image layer/archive and remove source-bearing bootstrap mounts or
ConfigMaps from the native deployment path.

The producer's database-free smoke and content checks do not replace those
acceptance results. Existing Dockerfiles still copy source and must not be treated
as native-only image recipes.

## Verification recorded for this guide

The refreshed source baseline is `historical-source-revision-redacted`
plus the native-packaging changes. Prepared-input builds passed for backend,
AgentLens, and onboarding on both targets. Each completed its content audit and
database-free smoke checks; every staged ELF file matched its requested target.
The rebuilt installer archives matched the earlier accepted hashes byte for byte.

The central boundary suite passed 14 tests, and `--help` executed successfully.
Fresh compiled regression suites passed 351 backend cases and 1,580 AgentLens
cases per target; AgentLens retained 12 explicit database-fixture skips. External
test scenarios remained outside the production artifacts.

Default preparation created a fresh tool environment with all eight exact
package pins. The supplementary cold-cache dependency download was stopped
before completion because of limited network throughput; automatic frozen
dependency preparation remains unqualified. The completed builds used prepared
environments whose locks matched the source and whose bytes were recorded. The
complete default-preparation pipeline in step 2 remains unexecuted as a single
build; its command block retains that label.

The [qualification reference](qualification-2026-10-28.md#central-workflow-verification-on-refreshed-main)
records builder and receipt hashes for these new builds. Its separate historical
section retains the real SQL, live-model, persisted-readback, and PID 1 lifecycle
acceptance from `4502a881` plus its native changes. Those service checks have not
been rerun on the refreshed source; current build/content/import success does not
extend their qualification.

## Troubleshooting

| Failure | Action |
| --- | --- |
| Target, Python, or compiler version mismatch | Use the target Linux builder and the pinned tool environment; do not relabel a host-architecture output |
| Lock mismatch | Recreate the prepared dependency environment from the exact component lock and retain that lock beside it |
| Overlapping paths or existing output | Select fresh disjoint work/output directories; retain source and prepared inputs |
| Input changed during compilation | Freeze the source/tool inputs, then rebuild into a fresh output directory |
| Missing source ownership or failed content audit | Correct the producer inventory or payload; do not exempt a runtime directory as an example or upstream dependency |
| Build/smoke failure without a final receipt | Keep the output unaccepted; inspect private logs and retry with a fresh stage |

For compiler choices and framework compatibility, see the [design explanation](README.md).
