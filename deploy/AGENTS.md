# Deployment Agent Rules

Read the root [AGENTS.md](../AGENTS.md) first. These rules cover changes under
`deploy/` and the callers that package or launch its assets.

## Product distribution

- Publish product images and Release assets only from `antgroup/ai-vision`
  through the [product release workflow](../.github/workflows/release-images.yml).
  Follow the [product release guide](../docs/deployment/en/dev-image-release.md)
  for channel, qualification, and promotion rules.
- Use five product packages under `ghcr.io/antgroup/ai-vision/`: `frontend`,
  `backend`, `ingest-gateway`, `diagnosis`, and `data-backend`. Keep the
  data-backend mirror pinned to its approved upstream SigNoz digest. The
  [release asset builder](../scripts/ci/release_assets.py) pins OceanBase CE
  and seekdb to upstream references independently of the product version.
- Keep user-facing Skills in the separate `ob-labs/ai-vision-skills` catalog.
  Preserve dated delivery records as evidence for their own snapshots.
- Keep deployment assets authoritative here. Follow [README.md](README.md) for
  CLI lifecycle, ownership, generated assets, and platform boundaries.
- OpenShell is an optional third-party sandbox installed separately on the
  deployment host and is outside five-service product qualification.

## Optional native packaging

- Read the [native packaging and qualification contract](native-packaging/release-contract.md).
  Use the [central artifact producer](native-packaging/build-artifacts.md), then the
  [artifact-only image assembler and layer auditor](native-packaging/build-images.md).
  Fix repeatable steps in their owning scripts.
- Compile project-owned Python implementations, migrations, installers, and
  executable hook helpers. Retain individually approved integration example
  hashes and reasons in the ownership inventory. CPython and reviewed upstream
  dependencies may retain source.
- Audit runtime inventories, every distributed image layer and archive,
  the CLI executable and attribution members, and deployed helper mounts
  or ConfigMaps. Keep compiler output, generated C, raw logs, credentials,
  and private settings outside tracked source and distribution contexts.
- Build a restricted CLI with the `native_distribution` profile. Select
  the intended version and target explicitly and inspect its version and VCS
  metadata. The ordinary CLI remains source-compatible.
- The [native candidate workflow](../.github/workflows/native-candidate.yml)
  builds and audits a Linux amd64 candidate. Qualify actual image and CLI
  combinations separately for each claimed architecture and database.
- Exercise candidate migrations, retained-data behavior, SQL/authentication,
  OTLP/query readback, diagnosis, Agent installation/hooks, readiness, and
  PID 1 shutdown using the actual packaged artifacts. Exercise changes to
  lifecycle hooks through recast with the exact pinned datastore image and
  its bind-mounted helpers. Preserve existing deployments and record the exact
  platform, database and digest selection. Record fixture, emulated and native
  results separately.
- For requested offline scan exports, bind the inventory and checksums to the
  selected artifact identities and document which images were included and
  which scans actually ran.
