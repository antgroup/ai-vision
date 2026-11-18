# Explicit deployment targets with Docker as the default

## User requirement

Keep Docker as the default deployment, with Compose and Kubernetes as optional targets. Withdraw the attempted mandatory Compose dependency. Prefer native Docker Compose operations while retaining necessary lifecycle protections. Update the product contracts and three-language documentation, and verify actual affected deployments.

## Target boundary

- Default `aiv cast` and explicit `--target docker` use the existing Go Docker runtime. They must not invoke or require host Python or Compose. Existing Docker installations remain fully supported, including cast/redeploy.
- Explicit `--target compose` supports the reviewed product image blueprint. Source/process and non-product custom blueprints retain their current Docker/development runtime and are rejected by the Compose adapter.
- Kubernetes retains its fixed manifest implementation and supported commands.
- A target never implicitly adopts another target's state, containers or storage. A mismatched or conflicting owner produces an actionable error before engine execution.

## Native Compose and protected operations

Use native `docker compose config`, `ps` and `logs` for configuration inspection and observation. The explicit Compose CLI verifies its saved project and then invokes the selected Docker executable directly for status/logs, without Python. Clear ambient `COMPOSE_*` selection so saved projects cannot be retargeted.

Implement lifecycle guards inside the Go CLI for installation, upgrade and removal, following the Kubernetes target's control boundary. Invoke Docker Compose and retain product-specific checks: deployment/storage ownership, concurrency locks, isolated seekdb password initialization, rerunning initialization jobs, and exit/OOM verification before container removal. Remove the host Python controller from source, generated assets and exports. The upgrade sequence deliberately stops the single-node stack before schema/credential work. Bare `up` and `down` are not claimed equivalent to that sequence.

Use Compose-native healthchecks, successful one-shot dependencies, stop signals and grace periods where already supported. Do not duplicate those mechanisms in a second orchestration engine. Compose 2.27+ applies only to the optional Compose target. Deployment control does not require host Python. Container-internal bootstrap scripts and repository verification tools may continue using Python in their own environments.

## Optional CLI project persistence

The Compose adapter publishes private immutable bundles at `<state>/compose/bundles/<content-hash>/`. Never rewrite scripts mounted by running containers. A validated `.cli-vars.json` sidecar preserves raw derived configuration expressions, literal credentials and selected image references. Go accepts the sidecar under the lifecycle lock, persisting private retry variables and a pending/ready `.compose-cli.json` pointer after ownership checks.

Compose observations and removal use the verified saved bundle. Invalid pointers, escaped paths, symlinks, modified content or unknown ownership fail closed. Standalone Compose exports use explicit `--compose-file` and optional `--compose-env-file` (default `.env`) with the same Go lifecycle guards. Their namespace/state/configuration come from that exact YAML/environment, so blueprint/variable/namespace/workdir selectors cannot be mixed with file mode. Standalone projects are not automatically imported into CLI variables.

Default Docker variable precedence, persistence and purge behavior remain unchanged. Compose purge removes named volumes while retaining bind-mounted metastore, identity and private configuration; Compose redeploy purge and source-repository purge remain unsupported. Keep these differences explicit in help and documentation.

## Verification and delivery

Cover default Docker without Compose/Python, explicit targets, target mismatch, native observations, aliases, private bundle integrity, partial retry, rejected-update preservation, literal credentials, prerequisite failures and purge boundaries. Run CLI/deployment suites and generated-asset checks.

Run fresh native Linux Docker regression checks and explicit Compose managed/external seekdb/OceanBase acceptance, including real authenticated telemetry readback, retained SQL/Trace/org identity, image replacement, CLI/native observation and controlled stop/removal. Do not relabel earlier acceptance as this patch's evidence. Kubernetes gets relevant source regressions because its runtime/manifests are unchanged.

Update the three peer deployment tutorials, architecture/installation guidance, deployment Skill, navigation and MCP evidence. Preserve stable URLs/anchors and distinguish current source support from published artifacts. Do not commit, push or publish without applicable user authorization.
