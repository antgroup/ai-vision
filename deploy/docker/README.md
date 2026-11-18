# Optional product Docker Compose

Docker through the Go CLI is the supported default. Compose and fixed Kubernetes
are optional deployment methods. The generated Compose definitions share product
images, ports, configuration and database volumes with the authoritative
[image blueprint](../blueprints/aivision.yaml); existing deployment ownership is
not automatically mixed or converted.

## Go CLI and native Compose interfaces

Docker through the Go runtime remains the supported default: `aiv cast` and
`--target docker` do not require Docker Compose. Compose and fixed Kubernetes
are optional methods. Current source adds explicit `--target compose` for the
product image blueprint. Use a matching CLI confirmed to include this target
and matching images; a source merge, OB_BACKEND variable or dev tag does not
establish published support. Source/process and unsupported custom blueprints
cannot explicitly select Compose.

Compose needs Docker Compose 2.27+ and Docker on Linux/macOS. The Go CLI invokes
native Compose and retains storage identity/ownership, the shared lock, isolated
seekdb initialization, initialization-task reruns and exit/OOM checks before
deletion. There is no host Python dependency or additional control script.
Bootstrap Python remains inside the applicable application images. Native
inspection does not establish bare up/down as an equivalent guarded lifecycle.

For a new CLI-managed Compose project, prepare private vars and a fixed UTC
history start using the [Docker tutorial](https://aivision.mintlify.site/en/deployment/docker),
then keep the explicit target on every operation:

```sh
aiv cast --target compose --namespace aivision --vars-file ./aivision.vars.yaml
aiv status --target compose --namespace aivision
aiv logs --target compose --namespace aivision -n 100 frontend
```

Retain the original namespace and customized workdir/blueprint selectors too.
Omitting the target selects default Docker. Existing Docker installations remain
supported through their original Go workflow and are not converted automatically.
Do not mix ownership or remove markers/labels to adopt resources.

Only CLI-managed Compose creates immutable private bundles under
`<namespace-state>/compose/bundles/<hash>/`. Cast/status display the actual path.
Keep the complete bundle and CLI state private. Change configuration/images
through original vars/flags and cast, not generated-file edits. Status/logs/destroy
reuse the validated saved bundle instead of regenerating from new defaults or
ambient deployment variables. Saved image references take precedence over
aggregate tag/registry changes.

For optional low-level read-only diagnostics, native inspection uses the exact
original project name and absolute file. Replace
the placeholder path and namespace below with the actual deployment values:

```sh
AIV_COMPOSE_BUNDLE=/absolute/path/reported/by/aiv
AIV_COMPOSE_PROJECT=aivision
docker compose --project-name "$AIV_COMPOSE_PROJECT" --file "$AIV_COMPOSE_BUNDLE/compose.yaml" \
  --env-file /dev/null config --quiet
docker compose --project-name "$AIV_COMPOSE_PROJECT" --file "$AIV_COMPOSE_BUNDLE/compose.yaml" \
  --env-file /dev/null ps --all
docker compose --project-name "$AIV_COMPOSE_PROJECT" --file "$AIV_COMPOSE_BUNDLE/compose.yaml" \
  --env-file /dev/null logs --tail 100 frontend
```

`config --quiet` avoids dumping resolved private values. The explicit project name
prevents ambient COMPOSE_PROJECT_NAME from changing the target. Resolved CLI
bundles use `--env-file /dev/null`; parameterized standalone templates use their
original actual private env file instead.

Existing standalone files use the same Go guards with explicit file/env selection:

```sh
aiv cast --target compose --compose-file /absolute/path/compose.yaml \
  --compose-env-file /absolute/private/compose.env
aiv status --target compose --compose-file /absolute/path/compose.yaml \
  --compose-env-file /absolute/private/compose.env
aiv logs --target compose --compose-file /absolute/path/compose.yaml \
  --compose-env-file /absolute/private/compose.env -n 100 frontend
```

`--compose-env-file` defaults to `.env`; resolved exports use `/dev/null` instead.
This mode derives identity/state from resolved YAML and cannot mix namespace,
workdir, vars-file, var or blueprint overrides. Edit the actual original env for
parameterized templates; regenerate resolved exports from original private vars.
It does not automatically import standalone configuration into CLI vars or adopt
foreign default Docker resources.

Default Docker `destroy --purge` keeps its existing removal of managed datastore
volumes and namespace state, including the default metastore. Compose
`destroy --target compose --purge` removes named volumes only and retains
bind-mounted metastore, identity and configuration; it is not a complete reset.
Neither clears external databases. Compose rejects redeploy --purge and
--purge-repos before changes. Keep target-specific purge semantics.

## Select a database

| File | Database | Initialization |
|---|---|---|
| `compose.yaml` | Managed OceanBase CE | Initialize the tenant and three application databases. |
| `compose.seekdb.yaml` | Managed seekdb | Isolated credential initialization on empty storage; verify original credentials on retained data. |
| `compose.external-oceanbase.yaml` | Existing OceanBase CE | Container-reachable MySQL-mode tenant account authorized to create/migrate application databases. |
| `compose.external-seekdb.yaml` | Existing seekdb | Reachable authorized account; no tenant creation. |

The file selects the database mode. Changing OB_BACKEND or filenames is not a
data migration. Preserve original credentials, namespace/state, volume bindings,
metastore, organization identity and fixed history start. The pinned seekdb
image is Linux/amd64; other architectures need independent validation.

## Prepare a standalone private env

Use the complete selected-version deployment directory and matching images.
From this directory, copy `.env.example` outside source control only for a new
installation; reuse an existing private env instead of overwriting it.

```sh
install -d -m 700 "$HOME/aivision-compose"
(umask 077; set -C
  cat .env.example > "$HOME/aivision-compose/compose.env"
)
```

Fill credentials and absolute AIV_STATE_DIR/AIV_LOG_DIR, NAMESPACE and
DOCKER_NETWORK. Fix AIVISION_NATIVE_TRACE_HISTORY_START to a UTC timestamp
covering all retained traces. For confirmed empty storage, select the first
deployment day's UTC start once; never recalculate it on retries or updates.
Use role image keys (FRONTEND_IMAGE, BACKEND_IMAGE, DIAGNOSIS_IMAGE,
INGEST_GATEWAY_IMAGE, DATA_BACKEND_IMAGE); they win over old spellings in the
same config layer. Changing IMAGE_TAG alone does not replace explicit images.
Leave IMAGE_REGISTRY unset in a standalone Compose environment to select the
official product packages. A set IMAGE_REGISTRY retains the historical image
basenames for mirror compatibility. Set complete role image references for a
mirror that uses role names.
Use matching verified release metadata; source candidates need not invent it.

External databases need a container-reachable host, authorized account and the
original password. OB_USER/OB_USER_URL must agree, such as root@aivision and
root%40aivision for CE. Database passwords use letters, digits, underscore and
hyphen. Single-quote dotenv secrets containing dollar signs. Keep env and resolved
YAML private. Configure BIND_ADDR/PUBLIC_HOST before remote onboarding.

## Install, retry and upgrade standalone files

From the repository root, using managed CE as the example:

```sh
aiv cast --target compose --compose-file "$(pwd)/deploy/docker/compose.yaml" \
  --compose-env-file "$HOME/aivision-compose/compose.env"
aiv status --target compose --compose-file "$(pwd)/deploy/docker/compose.yaml" \
  --compose-env-file "$HOME/aivision-compose/compose.env"
aiv logs --target compose --compose-file "$(pwd)/deploy/docker/compose.yaml" \
  --compose-env-file "$HOME/aivision-compose/compose.env" -n 100 backend
```

For another database, use its original file from the table. Use these same
target/file/env selectors for routine CLI operations. Native Compose inspection
is an optional low-level diagnostic using the same absolute YAML, actual
original env and project name. Keep `aiv` permanently installed and available
in a new terminal; no `manage` wrapper or task-local binary is required.

Cast and non-purge redeploy run the guarded native Compose installation sequence:
validate identity/ownership, acquire the deployment lock, stop consumers and
check exit/OOM, remove completed initialization tasks, initialize managed seekdb
on an isolated network when needed, then native `up --detach --wait`. The
initializer has no application-network connection or published ports and shares
the database volume lock. Wrong credentials on nonempty storage fail without
changing them. Backend migrations and bootstrap precede diagnosis, which reads
only the organization ID; the integration API key remains backend-owned.

Fix failures and repeat the original complete cast. Parameterized templates read
the original private env; resolved exports are regenerated from original private
vars. Keep the same identity and storage. Updates have a maintenance window.
Failed stops retain exact container IDs in .compose-stop-pending.json and block
removal/purge until resolved. Inspect logs, fix the cause and stop successfully;
do not delete stop records or volumes to bypass protection.

## Existing installations and data retention

Default Docker remains supported. It does not automatically convert to Compose.
The Go guard rejects foreign containers sharing names, database volumes or
metadata paths, including stopped containers. A naming/label change is not a
migration. Existing standalone Compose files retain their own identity/state
and are not automatically imported into CLI-managed vars.

For an authorized rebuild or stop, use the original file and env:

```sh
aiv redeploy --target compose --compose-file "$(pwd)/deploy/docker/compose.yaml" \
  --compose-env-file "$HOME/aivision-compose/compose.env"
aiv destroy --target compose --compose-file "$(pwd)/deploy/docker/compose.yaml" \
  --compose-env-file "$HOME/aivision-compose/compose.env"
```

Destroy retains data. Compose purge removes named volumes only, retaining
bind-mounted metastore, identity and configuration. Do not pair that retained
metastore with an empty database and call it a fresh install. A complete reset
requires separate backups and an explicit plan for retained state. External
databases are never cleared. Default Docker's purge semantics remain distinct.

## Verification

Healthy containers and Web HTTP 200 establish startup, not telemetry correctness.
Complete authorized onboarding, send a non-sensitive Trace and read it back with
authentication. On retries/upgrades also read retained telemetry and compare
actual image IDs, organization identity, database canaries and storage mounts.
Same-image recast is not cross-release upgrade evidence. Read the
[lifecycle contract](lifecycle.md) for probes, dependencies and shutdown limits.

The earlier Python-controlled source-candidate matrix is historical evidence;
it does not qualify the new Go Compose controller or native/CLI round trips.
Record the actual new combinations and images separately. The
[Compose acceptance entry](../tests/compose_cli_smoke.py) and
[manually dispatched workflow](../../.github/workflows/compose-deploy-smoke.yml)
require explicit matching candidates. Static rendering is not runtime acceptance.

The manually dispatched [Compose lifecycle CI](../../.github/workflows/compose-deploy-smoke.yml)
runs all four database combinations against explicit candidate and replacement
image manifests. It requires images that implement these source interfaces and
records sanitized evidence; it does not assume published `dev` images have them.

Refresh generated files from the CLI directory with `make assets`; verify with
`make assets-check`. Do not edit generated Compose YAML directly.

## Native query maintenance and recovery

Matching source/images run Gunicorn and the managed projector under one backend supervisor. The normal settings are `BACKEND_QUERY_MODE=auto` and `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED=true` in the original private `.env`. The corresponding application variables remain `AIVISION_QUERY_MODE` and `SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED`; role and legacy spellings represent two controls, with the precedence described above.

For emergency query recovery, use `raw` to bypass derived summaries while preserving filters. `false` for maintenance durably suspends derived maintenance and serving across instances. Apply configuration changes with the same `aiv cast --target compose --compose-file ... --compose-env-file ...` lifecycle; it has a maintenance window and retains data. Returning the maintenance setting to `true` does not clear shared suspension. Follow the [explicit recovery sequence](../../docs/deployment/en/trace-read-model.md) after repair, using `status` to locate the actual backend container. Supply the existing internal ingest key only to that operator command, never to normal backend service configuration or command arguments.

The bootstrap task's explicit entrypoint remains separate from API/projector serving. API readiness does not establish complete historical projection coverage. Verify the fixed queries and Token-zero semantics after recovery. Candidate source checks do not establish that an older released image contains these features.
