# Docker development and operational notes

[Deployment entry points](../README.md) · [简体中文](../zh-CN/docker.md) | English

Installation, configuration, manual deployment, verification and shutdown are maintained in the [Docker deployment documentation](https://aivision.mintlify.site/en/deployment/docker). This page keeps notes tied to the source and deployment implementation. See the [aiv CLI reference](../../../aivision-cli/README.md#commands) for parameters.

Install `aiv` in a permanent directory and [verify a fresh terminal](../../../aivision-cli/README.md#make-aiv-available-in-new-terminals). Routine deployment, upgrades, status, logs, rebuild and data-preserving stop use `aiv cast`, `aiv status`, `aiv logs`, `aiv redeploy` and `aiv destroy`. Retain the original target, namespace, workdir and blueprint selectors; existing-file Compose retains its exact file/env pair. Component development commands and explicit operator repair procedures below serve their narrower purposes.

<a id="install-aiv"></a>

For downloads, verification and PATH configuration, see [Install the aiv CLI](https://aivision.mintlify.site/en/guides/install-cli).

<a id="reuse-existing-ob"></a>

For existing OceanBase or seekdb account, permission and connection requirements, see the [CLI datastore reference](../../../aivision-cli/README.md#existing-oceanbase). The authoritative [vars-file example](../../../deploy/config/aivision.vars.example.yaml) lives in `deploy/config/`. Keep actual configuration containing credentials outside version control.

## Product assets and optional Compose

Default Docker remains the supported Go CLI runtime. Optional Compose uses the
same Go CLI with --target compose to call native Docker Compose; it requires
Compose 2.27+ and Docker, without host Python or an additional control script.
Container bootstrap Python remains inside application images. See the
[Go/native Compose contract](../../../deploy/docker/README.md) for CLI-managed
private immutable bundles and existing --compose-file/--compose-env-file mode.

Inspect with native config --quiet, ps --all and logs --tail100 frontend, using
the original project name, absolute file and env. CLI resolved bundles use
/dev/null; parameterized templates use their actual original private env.
Mutations retain Go identity/ownership, lock, isolated seekdb initialization,
init-task reruns and exit/OOM gates. Bare up/down is not an established equivalent.
Default Docker is not converted automatically; standalone projects are not
imported into CLI vars. Compose purge retains bind-mounted metastore/identity,
while default Docker purge keeps its original managed-volume/state removal.

Root deploy owns canonical templates/bootstrap and component mapping. Generated
assets must be refreshed with make assets and checked with make assets-check.
Use matching images with SQL readiness, organization-file/bootstrap and bounded
shutdown support. Source merges, dev tags and prior controller evidence do not
qualify the new Go path or prove a published CLI includes the target.

<a id="use-seekdb"></a>

## Datastore selection and source version

Docker defaults to OceanBase CE. **OceanBase seekdb** is an option for lightweight personal deployments. Check that the selected CLI supports `OB_BACKEND=seekdb`. If a released CLI lacks this option, follow the [CLI source-build instructions](../../../aivision-cli/README.md#source-build-pointing-at-a-different-repo-or-branch). For the parameters and deployment commands supported by this checkout, see the [CLI datastore reference](../../../aivision-cli/README.md#choose-the-docker-datastore).

Check these maintenance boundaries; the linked implementation defines exact defaults and behavior:

- The 2 CPU and 2G memory defaults configure the datastore; other services need additional resources. The pinned image is Linux/amd64 and does not establish native ARM verification.
- Managed seekdb uses Docker `aiv cast` or the current source product Compose lifecycle. Older generic Compose exports omit initialization and are not installation entry points. Generic Kubernetes/systemd generation and the fixed Kubernetes path still do not support seekdb.
- seekdb uses `root` without a tenant suffix. Default Docker first-start credentials/retries are defined by [datastore.go](../../../aivision-cli/internal/deploy/runtime/datastore.go) and its [tests](../../../aivision-cli/internal/deploy/runtime/datastore_test.go); optional Compose uses the [Go/native contract](../../../deploy/docker/README.md). Changing a variable does not rotate an existing volume's password.
- seekdb and OceanBase CE use separate volumes; a namespace cannot switch backend in place. A backend change requires a new deployment and explicit data migration.

## Image read access

When images require authorization, follow the published Docker guide and run `docker login ghcr.io -u YOUR_GITHUB_USERNAME`. Enter the token only at the terminal's hidden prompt.

<a id="create-pat"></a>

Enter your personal access token (PAT) at the `Password:` prompt. If you do not have one, open **[Create a PAT (classic)](https://github.com/settings/tokens/new)** and follow these steps:

1. **Note**: enter `AI Vision GHCR pull`.
2. **Expiration**: choose a validity period, such as `30 days`.
3. **Select scopes**: select only **`read:packages`** (the first option in the third group) to download images.
4. Click **Generate token** at the bottom of the page.
5. Copy the generated token, paste it at the terminal's `Password:` prompt, and press Enter.

## Native v1 history scanning limits

For default Docker Go, `AIVISION_NATIVE_TRACE_HISTORY_START` is optional for the
rest of the platform. The optional guarded Compose lifecycle requires a fixed
nonempty UTC history bound before installation; it never substitutes today’s date.
When it is empty, deployment prints a warning and native v1 SDK reads return an
explicit capability error (HTTP 503, code 3038), rather than
claiming a recent window covers all history. It is a query boundary, not a data
retention or deletion setting. A start later than retained data excludes that
earlier data, so operators must keep it accurate.

Native reads split the configured history into windows of at most 31 days and
merge and deduplicate them before returning a result. The upper bound is the
current time plus `AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS` (default `300`).
`AIVISION_NATIVE_TRACE_MAX_WINDOWS` (default `120`) limits work; exceeding it
fails explicitly instead of returning partial history. A 93-day recent window
does not establish coverage of all retained traces. These settings require a backend image that implements native v1 history scanning. The fixed Kubernetes CLI does not accept the history-start setting; see its [current limits](kubernetes.md#updates-data-and-current-limits).

## Custom Docker networks

To choose a Docker network, add `--var DOCKER_NETWORK=aivision-custom` to either
`cast` command. The CLI creates the effective network if needed before the
external OB probe or service startup, and both use that same network. A separate
`docker network create` is not required. Existing shared networks are reused;
`destroy` does not remove networks.

## Logs with a custom blueprint

Select the blueprint with `--file`, for example `aiv logs backend -f --file blueprint.yaml`. Use Ctrl+C to stop following logs with `-f`.

## External reverse proxies

If you add an external reverse proxy, configure an upstream read timeout of at least 963 seconds only for `/api/v1/spaces/<space_id>/diagnose/diagnosers/llm/trial` and `/api/v1/spaces/<space_id>/diagnose/specialists/trial` to cover the finite diagnosis Trial budget; keep other API timeout settings unchanged.

For OTLP requests to `/api/public/otel/v1/traces`, the bundled Nginx frontend allows a 64 MiB HTTP body, matching the default Django and SigNoz ingress limits. Other frontend routes retain the 32 MiB body limit. Set the same route-specific allowance at any outer proxy; size batches by encoded request bytes, not by the 16 MiB decoded limit for each attachment. The ingress allowance does not change backend decompression, per-file or space-quota checks.

If an outer proxy overrides static asset MIME types, serve the PDF.js `.mjs` worker under `/assets/` as JavaScript. To verify the built frontend through Nginx, run `AIVISION_E2E_BASE_URL=http://127.0.0.1:5174 npx playwright test traceMediaPdf.spec.ts` from `ai-vision/vfe/` with the frontend running at that address. This check uses a synthetic Trace API fixture but loads the emitted worker from Nginx without overriding its response.

### Trace query memory budgets

For the pinned seekdb 1.4 image, `SEEKDB_MEMORY_BUDGET` configures `memory_budget` (logical cache/buffer budget). It defaults to the legacy `SEEKDB_MEMORY_LIMIT` value, then `2G`; the engine's deprecated `memory_limit` is ignored and is no longer passed. `SEEKDB_CONTAINER_MEMORY=4G` independently limits the managed database and its initializer. Backend and SigNoz containers default to `BE_CONTAINER_MEMORY=2G` and `SIGNOZ_CONTAINER_MEMORY=2G`. Budget headroom is required: the logical budget is not a process RSS ceiling. Existing containers must be recreated through the supported CLI lifecycle to receive new limits. Source host processes are not constrained by Docker. Kubernetes already has separate resource limits and does not support managed seekdb.

The Python backend streams native responses with a 32 MiB decoded-response limit, a 64 MiB cumulative request limit, and two concurrent query requests per worker. Configure `AIVISION_TRACE_RESPONSE_MAX_BYTES`, `AIVISION_TRACE_REQUEST_MAX_BYTES`, and `AIVISION_TRACE_QUERY_CONCURRENCY` in the deployment vars. Slots remain held through response rendering/close; batch reads share the request budget. Limits return explicit resource/capacity errors (3036/3037), never successful truncated data. Worker/replica counts multiply the admission budget; size the datastore for the aggregate. These are conservative starting defaults, not a capacity guarantee for arbitrary telemetry. Measure RSS, cgroup peaks, idle retention and query latency before raising them.

The matching SigNoz change adds stored `page_dimensions_v1` and `page_source_bytes_v1` columns to the raw Span table. This additive migration backfills existing data and requires ALTER permission, disk space and a maintenance window on large tables. Pagination verifies the generated definitions and fails explicitly if migration is incomplete; ingestion remains available. Scope, metadata size and the encoded page estimate are checked in the same repeatable-read transaction before raw page JSON aggregation. Full-window filtering reads compact metadata; raw payloads are loaded only for selected page spans. Upgrade both repositories together; product source changes alone do not replace a published SigNoz image.

Background projection also uses a cumulative source-batch budget. Over-budget batches are released and split before retrying; an individually oversized Trace remains dirty and is deferred while later Traces continue. Publication buffers are limited to 16 MiB. SigNoz source-page recovery shares native query admission and checks raw source sizes before JSON construction (128 MiB per Trace, 32 MiB per Span); the existing escaped-source limit still applies. Complete-window response recovery is capped at 24 MiB. Kubernetes accepts the three `AIVISION_TRACE_*` overrides and backend/data-backend container memory overrides through its supported CLI path, using binary units for Docker-style `G`/`M` values.
