---
title: Assemble and inspect native service images
type: how-to
audience: [A4]
runs: yes
verified_on: 2026-10-28
sources:
  - deploy/native-packaging/images.py
  - deploy/native-packaging/test_images.py
  - deploy/native-packaging/Dockerfile.native-backend
  - deploy/native-packaging/Dockerfile.native-agentlens
  - deploy/native-packaging/native-entrypoint-backend.sh
  - deploy/native-packaging/native-entrypoint-agentlens.sh
  - ai-vision/aivision-py-be/apps/core/services/plugin_artifacts.py
---

# Assemble and inspect native service images

`images.py` prepares an allowlisted context from successful central native
producer outputs. It does not compile services, create build identities, push
images, or qualify a deployment. Source-mode Dockerfiles remain separate.

## Required inputs

- A frozen repository snapshot and its real source revision. The helper compares
  every compilation input and the complete selected input file set with the
  native producer receipt. A receipt with `source_revision: null` stays null in
  the assembly evidence; matching input hashes bind its native bytes to the
  frozen source. For a Git checkout the supplied revision must equal `HEAD`.
  An exported snapshot needs `--source-revision` and independently retained
  evidence of the export's commit identity.
- A successful `build.py` stage for the selected component and target, including
  its original receipt, inventory and content audit. Runtime bytes and all ELF
  machines are checked again. Private compiler output is never copied.
- Frozen component build-info JSON generated from that identified clean source.
  The component, source SHA and clean identity must match. Metadata is copied as
  data; no inline interpreter program generates it in the image.
- A pristine, reviewed CPython 3.11.14/Bookworm runtime base for the target, its
  full local image config ID from `docker image inspect`, and a retained
  single-image `docker image save` archive. Prefer a digest-pinned slim reference.
  The Dockerfile performs no dependency resolution or package installation.
- A compiled native HTTP probe for the target. Its command contract is
  `aiv-http-probe HOST PORT PATH TIMEOUT_SECONDS`; success means HTTP 200. The
  context installs it at `/usr/local/bin/aiv-http-probe`. Docker healthchecks
  expand `BE_PORT` or `AGENTLENS_PORT` at execution time, following the same
  configured listener port and default as the service entrypoint. Their shell
  command executes only this native probe; host Python is unnecessary.
- A fresh output directory outside all source and prepared input directories.

The helper records the declared base reference and checks the full config ID and
all base layer hashes during image audit. A mutable local tag alone is not
provenance. Retain the base acquisition evidence with the private build ledger.
Never use a previous source-copying product image as a runtime base.

Build the probe from the same frozen source with Go 1.25 or newer. The amd64
command below produces a static Linux executable outside the source tree:

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' \
  -o /artifacts/amd64/aiv-http-probe deploy/native-packaging/probe/main.go
```

## Backend provider inputs

The backend additionally requires the complete reviewed plugin distribution and
its SHA-256, a source-input proof file, exact public-example approvals, and one or
more explicitly selected native installer targets. Image platforms and installer
client platforms are independent. An amd64-only release may serve only the
`linux-gnu-x64` native installer; this makes no ARM installer promise.

`--plugin-inputs` names a JSON object mapping repository-relative production
source paths to their SHA-256. It must come from the reviewed source-equivalence
check for a reused distribution. The helper verifies those bytes against the
frozen source, records the declaration, and verifies the distribution catalog,
artifact hashes, native formats, package identities and privacy metadata. It
does not reconstruct how historical plugin binaries were compiled. Retain that
separate review evidence.

`--public-examples` names a JSON object mapping each exact provider-relative
example to its approved hash and reason:

```json
{
  "langfuse/examples/langchain.yaml": {
    "sha256": "<actual sha256>",
    "reason": "Approved public SDK integration example served by onboarding"
  }
}
```

This is a format excerpt, not a complete approval file. The approval must cover
all current top-level YAML guides in `langfuse/examples/` and
`nvidia-nemo-relay/examples/`, and the exact
`nvidia-nemo-relay/relay_tracing.py` integration example. Extra, missing or changed
approved examples fail. Approval applies only to those exact bytes; a directory
is never exempted. The provider also contains `agents.json`, the three catalog
support JavaScript helpers and the complete plugin distribution. Raw installer
and persisted hook helper Python source is excluded.

Use a separate `--onboarding-stage TARGET=DIRECTORY` for every selected
`--installer-target`. Each archive must match its successful central receipt.
Complete bundles are copied to
`/agent-plugins/native-onboarding/<target>/connect.tar.gz`, which selects native
onboarding and disables the source installer endpoints.

## Prepare a context

The following commands are operator templates, not evidence of an executed image
build. Obtain actual identities and prepared inputs first.

```bash
python3 deploy/native-packaging/images.py prepare \
  --component agentlens --target linux/amd64 \
  --source-root /frozen/repository --source-revision "$SOURCE_SHA" \
  --stage-dir /artifacts/amd64-agentlens \
  --build-info /identity/agentlens.build-info.json \
  --base-image "$PINNED_PYTHON_BASE" --base-image-id "$PYTHON_BASE_CONFIG_ID" \
  --health-probe /artifacts/amd64/aiv-http-probe \
  --output-dir /image-staging/amd64-agentlens
```

For the backend, select `--component backend`, its own stage and build-info, and
add these arguments:

```bash
  --plugin-dist /reviewed/plugin-dist.tar.gz \
  --plugin-dist-sha256 "$PLUGIN_DIST_SHA256" \
  --plugin-inputs /reviewed/plugin-source-inputs.json \
  --public-examples /reviewed/public-examples.json \
  --installer-target linux-gnu-x64 \
  --onboarding-stage linux-gnu-x64=/artifacts/amd64-onboarding
```

The output contains `context/` and a private `receipt.json` outside that context.
The context contains only the selected runtime, frozen metadata, native probe,
backend provider when applicable, Dockerfile and restrictive `.dockerignore`.
Upstream dependencies may retain Python. Owned Python source, bytecode, Cython
inputs, C/C++ source, generated compiler source, source archives and common
embedded Python are rejected. Exact approved integration examples remain marked
as owned public examples in the receipt.

Both runtimes live at `/app`. Backend executables include `/app/manage`,
`/app/backend-bootstrap` and `/app/wait-http`; the AgentLens database job uses
`/app/database-init`. Reviewed native shell entrypoints preserve migration and
server startup and contain no source fallback branch. AgentLens starts Uvicorn
through `python -m uvicorn` with `/app/src` on its module path. This loads compiled
owned modules with the matching interpreter and upstream dependencies.
During bootstrap, entrypoints forward TERM/INT to the active startup process
group and wait for the child before exiting. After bootstrap they execute the
server directly, preserving the container's PID 1 lifecycle.

Failed preparation leaves partial output without a successful receipt. Diagnose
it and use a new output directory. Existing output is never replaced. Do not
send the source repository, receipts, compiler caches, logs or review files to
the Docker builder.

## Build and inspect every distributed layer

Use the generated context as the complete Docker build context. Do not override
its base argument with another image. Retain one-image save archives for the
exact runtime base and the resulting image.

```bash
docker build --platform linux/amd64 \
  -t native-agentlens:acceptance /image-staging/amd64-agentlens/context
docker image save "$PINNED_PYTHON_BASE" -o /evidence/python-base.tar
docker image save native-agentlens:acceptance -o /evidence/agentlens.tar
python3 deploy/native-packaging/images.py audit-image \
  --image-save /evidence/agentlens.tar --base-save /evidence/python-base.tar \
  --receipt /image-staging/amd64-agentlens/receipt.json \
  --output /evidence/agentlens-image-audit.json
```

Require exit code zero and `ok: true`. The audit hashes every base layer and
requires the exact expected layer and history prefixes and config ID. It then inspects every
product layer, including files hidden by later changes, against the context's
per-file hashes and ownership. Unexpected files, product links, whiteouts,
missing payloads, modified files, unsupported archive contents, platform
mismatches, and inline Python in decoded product image history/config fail. Tar
PAX headers permit only structural paths, sizes, ownership and timestamps;
nonstandard metadata and ZIP comments/extra fields in owned archives fail. These
checks include nested archives and product-layer directory metadata. Adding source
and deleting it later cannot pass. Base layers are identified as reviewed
upstream; the audit does not relabel their Python standard library as product
source.

Supply uncompressed `docker image save` TAR files. The audit validates the full
raw save envelope and product-layer headers, including overridden names, unused
fields, padding and trailing data. It also checks every image-config field and
product-added save metadata; duplicate JSON keys fail. Nested owned archives
require complete, bounded compression streams and matching ZIP local/central
records, so ignored headers or compression trailers cannot carry source.

The archive auditor has explicit size and nesting bounds and fails closed when
it cannot inspect owned content. It does not detect arbitrary encoded source or
reverse-engineer strings inside native executables. Base acquisition, dependency
ownership and exact public-example approval remain review responsibilities.

Only actual candidate-image acceptance establishes migrations, telemetry,
persisted diagnosis, readiness and PID 1 shutdown behavior. The context and layer
audits do not qualify those behaviors, registry privacy, an image-platform index,
performance or a platform not exercised. The focused regression command is:

```bash
python3 -m unittest discover -s deploy/native-packaging -p 'test_*.py' -v
```
