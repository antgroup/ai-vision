# `aiv` — the unified AI Vision CLI

`aiv` is a **three-in-one** command-line tool for the AI Vision
observability stack:

1. **Deploy** — Docker through the Go CLI is the default; Compose and Kubernetes are optional. Foundry-style deployment. The authoritative **blueprint**
   ([`../deploy/blueprints/aivision.yaml`](../deploy/blueprints/aivision.yaml)) describes every component (image, run, health,
   datastore bootstrap). `aiv cast --vars-file` uses the selected release's
   verified image references to pull the images and drive the
   whole stack locally — the only prerequisite is docker.
   `aiv cast --target kubernetes` deploys the full stack using embedded fixed YAML
   and `kubectl`; see [Kubernetes deployment](https://aivision.mintlify.site/en/deployment/kubernetes). `aiv generate`
   renders product Compose or generic conversion materials; the generic
   Kubernetes conversion is deprecated as a product installation path.
   Helm is a committed future packaging direction, with implementation deferred.
   No Helm chart or CLI target is available yet; use the fixed YAML installation.
   A second [source blueprint](../deploy/blueprints/aivision-source.yaml)
   builds components from source and retains host-process development behavior.
2. **Query** — LangSmith-style access to your traces, spans, sessions, logs and
   metrics over the **AI Vision OpenAPI**, authenticated with a space-scoped
   **SpaceKey**.
3. **Auth** — Account registration and login, Space and Agent onboarding, and
   SpaceKey management for query access.

The CLI is built from the Go standard library plus a vendored `yaml.v3`, so it
**builds fully offline** (`make build`, using Go, Make and Python 3 for
deployment-asset freshness checks). Licensed
Apache-2.0.

The CLI version follows the AI Vision project version in
the repository root [`VERSION`](../VERSION) file. `make build` reads it
on each build and stamps it into the binary, so `aiv --version` works without
the source checkout. The file contains one version string; a missing or invalid
version stops the build instead of falling back to package metadata. Use three
numeric parts without leading zeros, optionally followed by prerelease segments
separated by dots or hyphens (for example, `0.1.0-rc.1`); `+build` is not accepted.
Unstamped developer builds report `dev`.

## Deployment sources, roles and compatibility

Agent onboarding downloads its installer from the selected deployment. Native
installers include their own runtime; a working Go `aiv` binary does not prove
that the downloaded installer supports the host's libc. If its loader reports
missing `GLIBC_*` versions, request a compatible native installer from the
deployment maintainer. Installing host Python or changing `--python` cannot
repair that bundled runtime. Source-development installers instead use the host
Python interpreter. These diagnostics do not lower the native bundle's minimum
libc requirement.

Root [`deploy/`](../deploy/README.md) owns blueprints, fixed Kubernetes templates,
bootstrap scripts, configuration examples and acceptance entry points. Existing
`blueprints/` and `deploy/kubernetes/` files are generated compatibility copies
used by Go embedding and the unchanged release archive layout. Edit canonical
sources, then run `make assets`; `make assets-check`, `make build` and `make test`
fail when the bundle or product Compose output is stale.

Current source output and selectors use `frontend`, `backend`, `diagnosis`,
`ingest-gateway`, `data-backend` and `database`. The
[canonical mapping](../deploy/components.json) preserves old `fe`, `be`,
`agentlens`, `antcollector-gw`, `signoz` / `signoz-ob` and `oceanbase` aliases.
Existing container/workload/Service names, selectors, DNS, namespace/state and
volume bindings remain stable. Upstream image repositories, API identifiers and
database schemas retain their established names.

Role configuration aliases such as `DATA_BACKEND_IMAGE` and `BACKEND_PORT` are
accepted alongside legacy keys. Within one map layer the role key wins; the
existing defaults < saved < vars-file < environment < flags precedence remains.
Repeated CLI flags use the last value regardless of old/new spelling.
These are source capabilities until present in a released binary; do not infer
them from a rolling tag or assume an older client accepts new selectors.

The image blueprint (`metadata.product: aivision`) stops existing applications
in reverse dependency order before replacing the database. Backend, gateway and
data backend receive TERM with a 60-second budget; diagnosis has 300 seconds.
Frontend receives QUIT with 30 seconds. CLI replacement and destruction verify
exit code 0 and no OOM before removing each container. A stop failure or timeout
halts the operation and prevents data purge; configured budgets do not by
themselves prove that every accepted request was drained.

Private `state/lifecycle-stops/<role>.json` records bind an application stop to
its exact container ID. Managed databases share the private
`state/lifecycle-stops/database-id` gate between container replacement and seekdb
offline initialization. Repeating `cast` or `destroy` cannot bypass a failed stop.
Inspect that container's logs and state, then restart it and stop it successfully,
or explicitly remove the failed container after diagnosing the failure and retry.
Do not delete the pending record to bypass the check. An unrelated startup
failure with no pending stop remains retryable with corrected configuration.
Source and custom blueprints retain their existing application lifecycle.

Managed databases use embedded scripts materialized privately under the same
state directory. OceanBase CE preserves the upstream bootstrap and stops the
standard `obcluster` through OBD; SLIM/demo layouts are unsupported. New seekdb
containers forward TERM to the engine's supported SIGUSR1 shutdown. When
upgrading an older CLI container that runs seekdb directly as PID 1, the CLI
sends SIGUSR1 and waits up to 120 seconds for exit 0 before offline credential
verification. It does not accept a forced exit as a successful stop. Existing
database volumes, credentials and state locations are preserved; verify retained
SQL data and traces after an upgrade.

### Native private distribution build

Use `make native-build` for restricted binary delivery. It builds with
`-tags native_distribution`: only the two datastore shell helpers and native
Kubernetes projections enter the executable. Source bootstrap Python, the
source installer interpreter probe and source HTTP health programs are excluded
at compile time. The CLI selects `NATIVE_PYTHON_RUNTIME=true` by default and
rejects explicit source mode, source build/process blueprints and source Agent
installers before deployment mutations or writing an installer. Standalone
`--compose-file` workflows are unavailable in this restricted profile; use
CLI-managed `--target compose` with native release variables. Normal builds
retain standalone Compose support.

`make build` retains the source-development and legacy-image behavior. A normal
binary is not accepted by the private Skills delivery gate, even if a native
runtime variable would prevent it from writing the embedded source files.

For a versioned native snapshot, run the same clean-checkout build with the
planned product version and actual target architecture (do not alter `VERSION`):

```sh
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -mod=vendor \
  -tags native_distribution -buildvcs=true \
  -ldflags "-s -w -X github.com/antgroup/ai-vision/aivision-cli/internal/cli.Version=$AIV_PRODUCT_VERSION" \
  -o /absolute/output/aiv-linux-x64 .
```

Select `GOARCH=arm64` for Linux ARM64. Keep the target binary outside the
checkout. Do not add `-trimpath` to the qualified Go 1.25 command: the private
metadata gate requires the inspected linker-version flags. The private archive
gate verifies the real executable's native build tag, version, target, exact
clean VCS identity and absence of the inventoried first-party Python programs.
Source filenames or command names alone are not treated as implementation code.

Run `make native-test` for native-profile contract regressions and the ordinary
`make test` for source compatibility. The actual linked-ELF regression is
`scripts/ci/test_native_cli_archive.py` from the repository root; set
`AIV_TEST_GO` only when the host Go executable is not on PATH. A dirty checkout's
ELF can pass the content checks but must fail the final clean-source archive
gate. This packaging check does not detect arbitrary obfuscated programs and is
separate from offline vulnerability scanning.

### Direct product Compose

Compose is optional and uses the same Go CLI to control native Docker Compose.
The [product Compose directory](../deploy/docker/README.md) provides managed and
external OceanBase CE / seekdb materials. Use Compose 2.27+, Docker and matching
images with backend/diagnosis SQL readiness, organization-file/bootstrap,
native gateway/data-backend readiness and bounded-shutdown support. Existing
published binaries/images must be checked explicitly; source support is not a
release claim.

`aiv generate --target compose` exports resolved private YAML and bootstrap
material without a host Python controller. Keep exports private. Source
blueprints with host processes retain their default Docker development workflow.
Static checks, same-image recast and actual upgrades remain separate evidence.

### Optional Compose through Go and native Docker Compose

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

Native inspection uses the exact original project name and absolute file. Replace
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


---

## Optional Sandbox provider installation

Current source adds deployment-host bootstrap with `aiv plugin setup openshell`.
Prepare the one-time command in AI Vision, then run it on the matching Docker
host. A deployment-admin catalog must provide the qualified plugin, native Rule
worker, and OpenShell 0.1.6 gateway/runtime/supervisor images as exact OCI digests
for the daemon platform. Missing catalogs or artifacts remain unavailable;
source support does not establish published-image or platform qualification.
The normal path chooses its own private configuration, credentials, ports,
Docker socket group, owned gateway, and internal service route, then saves and
tests a Space connection. Setting it as the Space default remains explicit.

New automatic connections use 1 CPU, 1 GiB and a 30-second execution limit.
Enhanced Python rules use the separately pinned `python-eval-v1` worker with
pytest, NumPy, pandas, Pydantic, jsonschema and openpyxl. The plugin verifies its
actual image and runtime manifest before advertising that capability. These
libraries stay outside the default application images. Existing connections and
legacy rules retain their configuration; enhanced execution requires a qualified
sandbox and never falls back locally. AgentLens's configured enhanced limit may
lower the connection's execution budget; the editor shows the effective limit.

Setup refuses remote Docker contexts, mismatched backend identity, ambiguous
diagnosis/network routes, unknown resources, changed plans, and unsafe private
files. It retains generated credentials, state, TLS, and journal for recovery;
retry with a newly prepared session and the same deployment. The dedicated
gateway uses mTLS with a loopback-only host port and trusted host-networked
supervisors. Actual Docker host-networking support must be qualified; setup
never widens gateway exposure to work around it. Plugin removal still drains
leases and preserves the independently owned gateway and journal.

Advanced native Docker management for an explicitly selected optional
OpenShell plugin remains available: `aiv plugin install/status/logs/remove openshell`. Start with
`aiv plugin install openshell --help` and the
[operator guide](../docs/deployment/en/sandbox-plugins.md)
([简体中文](../docs/deployment/zh-CN/sandbox-plugins.md)). It requires an existing
OpenShell gateway, explicit plugin/workload images, private token and policy
files, the actual gateway Docker socket and a persistent journal directory.
The plugin is outside the default five-service release contract; no published
image/tag is assumed. The browser manages connections and bindings separately.
Installation reports six phases on stderr: operator-file checks, Docker checks,
plugin image acquisition, saved state, container startup and authenticated
readiness. Long phases report elapsed waiting time; image downloads show only
recognized Docker layer progress. The default installation budget is 15 minutes;
use `--timeout 30m` for a slower registry. A started container with `Readiness:
false` is installed but not ready; use the printed status/logs commands with the
original `--state-dir` before configuring a connection. The CLI does not download
the workload image or install the gateway.

The installation drawer can prepare a host command with paired optional
`--progress-url URL --progress-token TOKEN` flags on `install` or `status`. Run
that command on the Docker host; the browser never starts host commands. Both
flags are required together. The explicit HTTP/HTTPS URL cannot contain URL
credentials, a query, or a fragment; redirects are refused. The short-lived
reporting token is sent only in the HTTP Authorization header and is never
saved in installation state or passed to Docker. Treat the generated command
as private.

Reporting sends only typed phases, elapsed times, and allowlisted Docker layer
updates. Bounded asynchronous updates preserve phase completions and terminal
results while coalescing waiting updates; retries reuse the same sequence
numbers. Each HTTP request times out after two seconds and final flushing is
limited to four seconds, including canceled installs. A reporting failure prints
one fixed warning and does not fail or stop the host operation. Without these
flags the CLI output and behavior stay unchanged. A reporting `status` command
checks the original installation once: it reports ready, not ready, or not
installed without repeating installation or creating a Space connection.

The container keeps the token file owner's numeric UID/GID. Its supplementary
socket group defaults to the host socket's GID. If a Docker VM maps the mounted
socket to a different group (for example, on macOS), determine that
**container-side** GID and pass `--docker-socket-gid N` (`0..2147483647`). Group
`0` is allowed and does not change the process UID to root. The CLI does not
probe another image to infer the group or broaden socket permissions. Use only
a trusted plugin image: Docker socket access already grants daemon control,
even when mounted read-only. See the operator guide for inspection and retained
installation-state requirements.

Removal fences admission and refuses active or unverifiable leases, preserving
the journal and operator files. Verify the command in the actual installed CLI;
source capability is not a release or platform-qualification claim.

## Register, connect and query entirely from the CLI

Download `install-aiv.sh` from the versioned GitHub Release used by your
deployment, then install and verify the client in the same terminal. Public
product Releases are listed at [antgroup/ai-vision](https://github.com/antgroup/ai-vision/releases).

```bash
sh install-aiv.sh --version YOUR_RESOLVED_VERSIONED_TAG
export PATH="$HOME/.local/bin:$PATH"
command -v aiv
aiv --version
```

Replace `YOUR_RESOLVED_VERSIONED_TAG` with the published version used by your
deployment. `--version vX.Y.Z` pins a stable release; a versioned Daily tag is
`vX.Y.Z-daily.YYYYMMDD[.REVISION]`: the first accepted snapshot has no revision
suffix, and later snapshots on that date use `.2`, `.3`, and so on. Resolve the
`daily` Release's versioned-asset link first: the moving `daily` Release itself
has no CLI assets. Omitting
`--version` selects the latest stable CLI, regardless of where the installer
was downloaded. `--version dev` remains a legacy rolling download path.
Release installers verify SHA-256 and default to `~/.local/bin`; for
`--install-dir`, use that stable directory and its printed export command.
Current source installers also configure Bash and zsh startup files by default;
`--no-modify-path` opts out. These changes apply only to installers containing
the feature, not already published immutable assets. Older installers may only
print PATH guidance. Complete the shell setup below in either case, including
after a manual archive/source installation. A script cannot change its parent
terminal's environment, so the one-time export above is still needed there.
See the [CLI installation guide](https://aivision.mintlify.site/en/guides/install-cli)
for platform downloads. Developers can also build with `make build`.

### Make `aiv` available in new terminals

Keep the binary at `~/.local/bin/aiv` or the chosen permanent installation path.
For the default path, ensure the following line appears once in the startup
files used by your shell, preserving their existing contents:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

- **zsh:** `${ZDOTDIR:-$HOME}/.zshrc`.
- **Bash:** `~/.bashrc` and the first existing login file in this order:
  `~/.bash_profile`, `~/.bash_login`, `~/.profile`. Create `~/.bash_profile` when
  none exists. Updating `.profile` alone does not help when `.bash_profile` exists.
- **Other shells:** configure their actual startup file using that shell's PATH
  syntax; automatic setup is not promised.

For a custom installation directory, replace `$HOME/.local/bin` in the
quoted PATH entry with the actual directory. Verify the installed
path/version in the current terminal, then open a new terminal and repeat
`command -v aiv` and `aiv --version`. For Bash/zsh, also test startup without
inheriting this task's PATH (use the actual supported shell executable):

```sh
env PATH=/usr/bin:/bin:/usr/sbin:/sbin "$SHELL" -ic 'command -v aiv && aiv --version'
env PATH=/usr/bin:/bin:/usr/sbin:/sbin "$SHELL" -lic 'command -v aiv && aiv --version'
```

Both checks must select the intended permanent binary. Resolve startup files
that reset PATH or select another `aiv` before declaring installation complete.

With an existing AI Vision deployment:

```bash
aiv auth register --endpoint https://vision.example.com --email you@example.com
# Existing account: aiv auth login --endpoint https://vision.example.com --email you@example.com
aiv space list
aiv connect codex --dry-run
aiv connect codex
# Restart Codex, trust the hooks via /hooks, and perform a real task.
aiv key create --name cli
aiv agent status codex
aiv query trace list --since 1h --limit 5
```

No browser-generated command is needed. Registration prompts for name/password;
non-interactive use supports `--name` and `--password-stdin`. SpaceKey creation
requires the existing server permission; `aiv key import --key-stdin` imports an
authorized existing key. Hook installation does not require a query key.
See [the complete CLI onboarding guide](docs/auth.md) for profiles, prerequisites,
reachable platform/gateway URLs, status semantics, and legacy compatibility.

## Choose a collection mode

```bash
aiv connect codex                              # First install: Full; reinstall: preserve saved mode
aiv connect codex --collection-mode privacy    # Filter original content locally
aiv connect codex --collection-mode full       # Explicitly restore Full
aiv agent status codex
```

Restart the host and start a **new session** after changing mode. Privacy keeps
trace structure, status, timings and usage, and filters prompts, responses,
thinking, tool arguments/results and free-form error content before telemetry
export or plugin-owned persistence. It does not remove historical telemetry or
change transcripts maintained by the host. Configuration is local to each host
config directory; it is independent of account, query key and ingest credential.
See [collection modes](docs/privacy.md) for environment overrides, verification
by trace ID and deployment requirements. The web installation entry remains
available; mode selection is a CLI option.

## The credential model

Account sessions, Agent reporting credentials and query keys are separate:

| Plane | Credential | Who holds it |
|---|---|---|
| **Management** (accounts/spaces/Agents) | Cookie session + CSRF | private CLI profile, automatic refresh |
| **Data ingest** (agents → ingest gateway → data backend) | Agent credential + short-lived **ingest token** | installer/runtime in `<host-config>/aivision/<agent>/` |
| **Query** (you → AI Vision OpenAPI) | **SpaceKey** (`sk-aiv-…`, space-scoped) | CLI profile per space, via `aiv key create/import` |

The **org id is discovered, never hardcoded**. The data backend starts
unregistered; the backend bootstrap command
(`manage.py bootstrap_signoz_integration`) registers the root user, which
produces the org, mints an *internal* data-backend API key and persists both. `cast`
then discovers the org id with `signoz_integration_status --print-org` and
injects it into the components that need it (e.g. AgentLens). The internal
internal API key remains in the backend database. The CLI forwards only the
organization ID; Compose publishes that ID through its private runtime volume.

So when you query, all you need is a **SpaceKey** and a **space id** — the org
and the internal API key do not need to be supplied by the operator.

---

## Quick start

```bash
# 1. Build the CLI (offline — dependencies are vendored)
cd aivision-cli
make build

# Install the command in a permanent location
install -d "$HOME/.local/bin"
install -m 755 ./aiv "$HOME/.local/bin/aiv"
export PATH="$HOME/.local/bin:$PATH"
command -v aiv
aiv --version
# Complete "Make aiv available in new terminals" above and verify a fresh shell.

# 2. Check prerequisites (docker daemon)
aiv gauge

# 2b. Only if the selected images require authentication (PAT: read:packages)
docker login ghcr.io -u YOUR_GITHUB_USERNAME

# 3. Deploy the whole stack from the built-in blueprint (pulls the images)
aiv cast

# 4. Verify
aiv status
aiv logs -n 50 backend

# 5. Register, install the hook, and configure querying
aiv auth register --endpoint http://127.0.0.1:5174 --email you@example.com
aiv connect codex
aiv key create --name cli
# Restart the host Agent, trust hooks if required, and run a real task.
aiv agent status codex
aiv query trace list --limit 5

# 6. Tear everything down (add --purge to also wipe data + state)
aiv destroy
```

`cast` pulls each component's image (nothing is cloned or compiled on this
host), starts the components in dependency order, waits for each to become
healthy, runs the datastore bootstrap and the backend's data-backend integration
bootstrap, then discovers the org id for downstream components. It is
idempotent: re-running it replaces containers but **preserves data** (the
datastore named volumes survive; `--purge` is what deletes them), and the
bootstrap does not create a second org.

### Choose the Docker datastore

Both Docker blueprints accept `OB_BACKEND=oceanbase|seekdb`; the default remains
`oceanbase`. OceanBase CE is recommended for team deployments and growing
observability workloads. OceanBase seekdb is the lightweight option for
individual use and local development. Select it with the CLI built from this source:

```bash
aiv cast --var OB_BACKEND=seekdb
```

Managed seekdb uses Docker `aiv cast` or the current source product Compose
lifecycle. Older generic Compose exports omit initialization and are not a
supported installation. Generic Kubernetes/systemd generation still rejects
managed seekdb, and fixed Kubernetes does not support it.

For manual setup and fixed trace-history configuration, start with the
[Docker guide](https://aivision.mintlify.site/en/deployment/docker), then apply the
backend selector and limits in this section. This is the source-version reference
for seekdb support; the published guide may track an earlier CLI release. Until a release includes
this feature, run `make build` in `aivision-cli/` and use that binary; an older
published CLI may not recognize the selector. `aiv gen --vars` exposes the
configuration, which can also be saved as `OB_BACKEND: "seekdb"` in a vars file.

seekdb uses `root` without an OceanBase tenant suffix. Set its password with
`OB_TENANT_PASSWORD`, using only letters, digits, `_`, and `-`. A managed empty
volume is initialized in a dedicated internal network without published ports,
using that password before normal service starts; an existing volume keeps its
password. Changing the variable is
not password rotation. `OB_IMAGE` overrides the seekdb image. The default seekdb
settings are 2 CPU, 2G memory, a 2G datafile, and 2G log disk, configurable through
`SEEKDB_CPU_COUNT`, `SEEKDB_MEMORY_LIMIT`, `SEEKDB_DATAFILE_SIZE`, and
`SEEKDB_LOG_DISK_SIZE`. These are datastore settings, not full-stack requirements.
The pinned default image is Linux/amd64; ARM hosts need suitable Docker emulation
or an independently validated image supplied with `OB_IMAGE`. Native ARM support
has not been verified for this image.

Managed seekdb persists at `/var/lib/seekdb` in
`aivforge-seekdb-data-${NAMESPACE}`, separate from OceanBase CE volumes. The CLI
rejects changing backend within an existing namespace. Use a new deployment and
migrate data explicitly; changing backend is not a data migration. The fixed
Kubernetes path uses the OceanBase Kubernetes Operator and does not support seekdb.

<a id="existing-oceanbase"></a>

Already running an OceanBase? Skip the bundled container and connect to yours:

```bash
export OB_PW='…'
aiv cast --ob-host 10.0.0.5 --ob-tenant aivision --ob-password-env OB_PW
```

`gauge` probes it from inside the docker network (reachability, MySQL-mode
tenant, `CREATE DATABASE IF NOT EXISTS` ×3) before any service starts, and
`destroy --purge` never touches it. The address and port must be reachable from
containers: for an OceanBase on the deploy host use its reachable IP or the
actual Docker bridge gateway. `127.0.0.1` inside a container refers to that
container itself.

Use a MySQL-mode user tenant. Its `root@<tenant>` account must be able to create
and migrate the `aivision`, `signoz` and `agentlens` databases. The current
connection templates do not escape passwords; use only letters, digits, `_`
or `-` in the tenant password.

For file-based configuration, follow the
[editable database configuration example](../deploy/config/aivision.vars.example.yaml).
`--vars-file` only loads values; `OB_MANAGED: "false"` selects external-database
mode. For an existing seekdb, add the backend selector and omit the tenant:

```bash
aiv cast --ob-host 10.0.0.5 --var OB_BACKEND=seekdb --ob-password-env OB_PW
```

The equivalent vars file contains `OB_BACKEND: "seekdb"`, `OB_MANAGED: "false"`,
`OB_HOST`, `OB_HOST_PORT`, and `OB_TENANT_PASSWORD`. seekdb defaults to `root`;
override `OB_USER` for an existing custom account with permission to create and
migrate the three application databases. The same container reachability and
password restrictions apply. The external probe uses the selected backend's
account rules and does not create an OceanBase tenant for seekdb.

The backend image ships the Agent-market plugins **pre-built for every
supported platform** (linux/darwin × x64/arm64), so the `aiv connect` command
shown on a market card works from any machine that can reach the deployment —
the deployment host's OS no longer matters.

The command is named `aiv`. Existing credentials in `~/.aivision`, deployment
state and Docker volume names remain compatible. Backend migrations also update
saved Agent-market installation commands to use `aiv connect`.

---

## Commands

Built-in help is available without logging in or contacting a deployment:

```bash
aiv help                  # Capabilities and the register/connect/query quick start
aiv auth help             # Account login, query-only login and logout behavior
aiv space help            # List, select and create spaces
aiv key help              # Create, import, list and revoke query keys
aiv connect --help        # Hook installation, prerequisites and dry-run behavior
aiv agent status --help   # Installation, credential and real-trace checks
aiv query help            # Query types, saved context and credential precedence
aiv query trace list --help  # Flags for a specific operation
```

### Deploy

| Command | What it does |
|---|---|
| `gauge` | Validate prerequisites: tools, docker daemon, images, source repos. |
| `forge` | Clone/update each repo at its declared branch and build artifacts (source blueprint only; a no-op for the image blueprint). |
| `generate` | Export private product Compose (`--target compose`) and bootstrap material, without a host controller script, or generic materials; `--target kubernetes` is deprecated as a product installation. |
| `cast` | Full pipeline: `gauge` + `forge` + deploy (pull images, start the whole stack). |
| `status` | Per-component state + health table, with endpoints. |
| `logs <component>` | Tail a component's logs (`-n LINES`, `-f`, `-F` or `--follow` to follow); flags can go before or after the component. |
| `redeploy` | `destroy` (keep data) then `cast` — rebuild a running stack. |
| `destroy` | Stop processes, remove containers. `--purge` also wipes data + state. |
| `gen` | Write the built-in blueprint to a file so you can edit it. |
| `version` | Print the `aiv` version. |

### Query (see [`docs/query.md`](docs/query.md))

| Command | What it does |
|---|---|
| `query trace list\|get\|export\|metrics` | List, fetch or export traces; aggregate the full time window with coverage. |
| `query span list\|get` | List a trace's spans, or fetch one span. |
| `query session list\|get\|io\|usage` | List/inspect agent sessions, their I/O and token usage. |
| `query log list\|query\|export` | List, query (`--mode fieldsJson\|rawSql`) or export events. |
| `query metric query` | Run a timeseries query (`--sql`, raw SQL). |
| `query metadata [events\|traces]` | Space/agent metadata and available fields. |

### Auth (see [`docs/auth.md`](docs/auth.md))

| Command | What it does |
|---|---|
| `auth register` | Register, log in and select the personal space. |
| `auth login` | Account login; keep accessible selected space. Legacy `--space-key` import remains supported. |
| `auth status` | Check the server session and show masked query context. |
| `auth print-env` | Print `export` lines for `eval "$(aiv auth print-env)"`. |
| `auth logout` | Revoke the account session and clear its local profile; leave installed Agent credentials valid. |
| `space list/use/create` | List, select or create a space. |
| `key create/import/list/revoke` | Manage query SpaceKeys under existing server permissions. |

### Connect and Agent status

| Command | What it does |
|---|---|
| `connect <plugin>` | Register/reuse the Agent, request installation authorization and run the deployment installer. |
| `agent status <plugin>` | Independently check local registration, reporting credentials and recent traces. |

`aiv connect codex` uses the logged-in account's current space and downloads the
deployment's installer for the local platform. Native deployments provide a
checksum-verified standalone bundle that runs without host Python. Source
deployments retain the Python 3.11+ installer. Registration, rollback and
credential renewal stay in the installer. `--dry-run` performs preflight checks
without issuing grants or registering Agents. Complete commands generated by
the web UI still work:

```bash
# Legacy explicit installation command (optional).
aiv connect codex \
  --base-url http://127.0.0.1:8001 \
  --space-id <space-uuid> \
  --agent-uuid <agent-uuid> \
  --installation-token <one-use-token>
```

Plugins: `claude-code`, `codex`, `kimi`, `opencode`, `openclaw`, `pi`, `dsh`
(aliases `cc cdx km ocd ocw ds deepseek`). Flags: `--config-dir`, `--profiles`,
`--dry-run`, `--python`, `--profile`. Run `aiv connect help` for details.
`--python` selects the interpreter for a source deployment's installer; native
bundles run directly. The currently qualified native targets are Linux x64 and
ARM64 with glibc >= 2.35, tested on Debian Bookworm. Other targets require a
matching installer from the deployment; a missing native target does not fall
back to downloading project source. See the [native build workflow](../deploy/native-packaging/README.md).

**Global flags** (blueprint-driven commands):

| Flag | Meaning |
|---|---|
| `-f, --file PATH` | Blueprint file (default: `./blueprint.yaml`, `./blueprints/aivision.yaml`, or built-in). |
| `-v, --verbose` | Stream build/deploy step output. |
| `--force` | Re-fetch repos, rebuild artifacts, re-run `once` bootstrap steps. |
| `--namespace NS` | Override `metadata.namespace` → isolated state/containers/volume. |
| `--source-root DIR` | Override where repos are cloned/found (source blueprint). |
| `--workdir DIR` | Override the state/logs root. |
| `--var KEY=VALUE` | Override any `config.vars` entry (repeatable). |
| `--vars-file PATH` | Flat YAML `KEY: VALUE` overrides; template via `aiv gen --vars`. |
| `--target docker\|compose\|kubernetes` | Default Docker, optional Go-controlled native Compose, or fixed Kubernetes. Keep the selected target on every operation. |
| `--compose-file PATH` | With `--target compose`, manage existing YAML through the same Go guards; cannot mix namespace/workdir/vars/blueprint overrides. |
| `--compose-env-file PATH` | Existing-file mode env selection (default `.env`); resolved exports use `/dev/null`. |
| `--reset-vars` | Ignore the overrides persisted by the previous `cast` in this namespace. |
| `--ob-host HOST` (`cast`/`redeploy`) | Use an existing OceanBase or seekdb instead of the bundled container; select seekdb with `--var OB_BACKEND=seekdb`. Supports `--ob-port`, `--ob-tenant` (OceanBase only), and `--ob-password-env NAME` / `--ob-password-file PATH`. |

For `logs`, use `-f`, `-F` or `--follow` to stream new entries. Select a
blueprint with `--file`, for example: `aiv logs backend -f --file blueprint.yaml`.

Precedence, low to high: blueprint `config.vars` < overrides persisted by the
last `cast` in the namespace < `--vars-file` < `AIV_VAR_<KEY>` environment
< `--var` / `--ob-*`. A successful `cast` persists the effective overrides
(`state/vars.json`, mode 0600), so `status`, `destroy` and `redeploy` need no
configuration overrides afterwards; `aiv status` lists them with their source.
The target and identity selectors are separate: retain the original `--target`,
`--namespace`, `--workdir` and `--file` on every applicable command, or the exact
`--compose-file` / `--compose-env-file` pair in existing-file Compose mode.
After an incomplete first cast, diagnostics may still need the original private
vars file and overrides because they have not been saved yet.

Query commands also accept `--format json|table` (default `table`), `-o FILE`,
`--limit/-n`, `--since`/`--last-n-minutes`, `--space`, `--agent-product` and
`--param k=v` (pass-through for filters the flags do not cover).

---

## Two blueprints: images (default) vs. source build

| Blueprint | What `cast` does | Host needs | Use it when |
|---|---|---|---|
| `../deploy/blueprints/aivision.yaml` (embedded default) | pulls the five application images and runs 6 containers including the managed database | docker | deploying a release, evaluating, any non-development host |
| `../deploy/blueprints/aivision-source.yaml` | clones the data-backend upstream + this monorepo, compiles in docker / with host `uv`, runs backend/frontend from source and diagnosis as a host process | docker, git, uv (+ cargo/npm for host-platform plugins) | hacking on a branch, unreleased changes |

The old `blueprints/` files are generated copies, retained for existing commands
and explicit `--file` paths. See the [component mapping](../deploy/components.json)
for the CLI role, legacy configuration keys and physical resource names.

### Choosing images and upgrading

Use **Release** (latest published stable) by default, or **Daily** when the
operator requests the latest tested preview. Honor an explicit version. Resolve
Release through the [latest stable Release](https://github.com/antgroup/ai-vision/releases/latest)
and Daily through the versioned-asset link in the
[`daily` Release](https://github.com/antgroup/ai-vision/releases/tag/daily).
Download the CLI, `release-manifest.json`, `SHA256SUMS` and the corresponding
`release-images.vars.yaml` (CE) or `release-seekdb.vars.yaml` from the **same
versioned Release**. Use an authenticated GitHub session when the selected
Release requires access. Before replacing the CLI, check each selected asset's
checksum and match the CLI version to the manifest. A missing channel is not
permission to substitute Dev.

Select one manifest for the whole stack. The public service packages are
`frontend`, `backend`, `ingest-gateway`, `diagnosis`, and `data-backend`
under `ghcr.io/antgroup/ai-vision/`. All five receive product-version and rolling
tags on publication. The data backend is a digest-pinned copy of the approved upstream
SigNoz index. Reused owned components retain their historical build identity
under the new product tag.

On a first public install, copy the verified vars file for your chosen database
to `aivision.vars.yaml`, configure it for your host, and run
`aiv cast --vars-file ./aivision.vars.yaml`. This supplies all five public image
digests and the release metadata. A bare cast on a fresh host uses the public
blueprint's rolling `latest` tag. Existing custom registry prefixes keep
their historical image names. Set each service's `*_IMAGE` for mirrors using
different names or to pin an exact digest.

For Docker, merge all five verified image entries and
`AIVISION_RELEASE_MANIFEST_BASE64` into the existing private deployment vars
file, updating keys without duplicates. Retain its datastore, credentials,
fixed history start, addresses and ports; the CLI takes only one `--vars-file`.
Update any existing role aliases in the file because they take precedence over
legacy keys in the same layer. Environment and CLI overrides have higher
precedence than the vars file.
External databases retain their existing connection and image settings. Run:

```bash
aiv cast --vars-file ./aivision.vars.yaml --var IMAGE_PULL=always
```

Preserve `--namespace`, `--workdir` and `--file` selectors on subsequent commands
when used. A plain `aiv cast` uses saved overrides or blueprint defaults; it does
not resolve a channel or download a release manifest. `IMAGE_TAG` is an advanced
fallback: saved per-component image references take precedence, and tags alone
do not supply the installed release record shown in About. Image aliases such as
`latest` and `daily` are mutable. Kubernetes accepts the five service-image
overrides and release metadata. See the
[Kubernetes configuration reference](../deploy/kubernetes/README.md#configuration)
for its image and datastore settings.

`IMAGE_PULL` defaults to `missing`; `always` checks the selected reference again.
Data is preserved across upgrades (datastore volumes and data-backend metastore are
kept; backend and AgentLens run migrations on boot). For backups and rollback
after schema changes, see
[database maintenance](../ai-vision/database-design.md#升级与回滚).

After login, open **Help → About** or `/about` to see the installed product
version and compare each actual frontend/backend build with its own expected
identity from the selected manifest. Different historical component commits are
valid when their expected identities match. Missing release metadata leaves the
installed product version unknown while build details remain available. Daily
builds show their full derived version; the update reference and release notes concern the latest stable
Release. Unknown identity or a failed update check is not proof of currency.

The [release workflow](../.github/workflows/release-images.yml) runs source checks,
builds standard source-compatible candidate images and CLI/plugin assets, and
deploys the actual Linux amd64 artifacts with both database backends before
promotion. Native compilation is an optional separate candidate variant. Stable/RC
tags must match root VERSION and require reviewed bilingual notes and protected
release approval. SigNoz is an external dependency released by
[`ob-labs/signoz`](https://github.com/ob-labs/signoz). The product manifest pins
the public mirror by the same digest as the upstream index. SigNoz maintains its
upstream release independently.

Manual `dev`/`daily` candidates use a derived product version. Candidate runs do
not publish a Release or move rolling aliases by default. The separate Daily
promotion workflow reuses the exact assets from one successful candidate run
and attempt. Scheduled Daily execution remains disabled. Keep the matching CLI and
selected vars file together to deploy the tested image digests. Use
`aiv cast --vars-file release-images.vars.yaml` for OceanBase CE or select
`release-seekdb.vars.yaml` for seekdb. `IMAGE_TAG` alone does not replace
saved image overrides or provide release metadata. Other branches use unique trial aliases and produce
Actions assets without a GitHub Release or rolling alias changes. See the
[release guide](../docs/deployment/en/dev-image-release.md) for setup, pinned
variables, reruns, and the limits of this validation.
Legacy private images require authenticated GHCR access. Public candidate
qualification checks anonymous access to the five pinned public references.

### Source build: pointing at a different repo or branch

The source blueprint accepts the same `OB_BACKEND=seekdb` selector for bundled or
existing seekdb. For external databases configured through a vars file, also set
`OB_HOST_LOCAL` / `OB_PORT_LOCAL` to an address reachable by the host AgentLens
process; `--ob-host` / `--ob-port` sets both address pairs automatically.

Every component of the source blueprint declares its own source. First build
and permanently install `aiv` using the [quick start](#quick-start), including
fresh-shell PATH verification. Run from the `aivision-cli/` directory. Edit
`config.vars` or override per invocation:

```bash
aiv cast -f ../deploy/blueprints/aivision-source.yaml \
  --var AIVISION_REPO=https://github.com/myorg/ai-vision.git \
  --var AIVISION_BRANCH=feature/my-change \
  --var SIGNOZ_REPO=https://github.com/myorg/signoz.git \
  --var SIGNOZ_BRANCH=main
```

Defaults use HTTPS and `main` in `antgroup/ai-vision` and `ob-labs/signoz`.
The product now clones into the `ai-vision` directory under the source root.
Existing checkouts and deployment state are left in place. Set `AIVISION_REPO`
and `SIGNOZ_REPO` directly for custom source deployments. To reuse an explicit
checkout path with `--force`, set its declared URL to match its Git origin,
or select a new checkout directory.

The stack is backed by **two repositories**: `signoz` (the SigNoz query
plane) and `ai-vision` (a monorepo holding `antcollector/`, `ai-vision/`
backend + frontend, and `agentlens/`). The monorepo is cloned **once** and each
component builds/runs from its own `repo.subdir`. See
the [source blueprint](../deploy/blueprints/aivision-source.yaml) for component settings
and the [blueprint schema](internal/blueprint/blueprint.go) for supported fields.

### Native health probes

`health.execArgv` accepts an executable and its arguments as a YAML array. For
container components, `cast` and `status` run it through `docker exec` in the
resolved container; Compose uses `CMD` and generated Kubernetes manifests use
`exec.command`. Process components run the same argv directly on the host.
Arguments support `${VAR}` substitution and are never interpreted by a shell.
Do not combine `execArgv` with `exec`, `http` or `tcp` in one probe.

The product image blueprint uses `["/app/collector", "healthcheck", "--ready"]`
for the gateway. It checks local pprof HTTP 200, rejection of an unauthenticated
OTLP request with HTTP 401, and downstream SQL readiness in sequence, each with a
two-second timeout. CLI and product Compose give it an eight-second budget;
the fixed Kubernetes readiness probe uses ten seconds. Running
`/app/collector healthcheck` without `--ready` retains the two-second local pprof
liveness check from main and does not check downstream dependencies.

The data backend uses `["/app/signoz", "healthcheck", "--ready"]` for its in-container API
on port 8080. It requires HTTP 200 from `/api/v1/ready` within two seconds;
the endpoint performs a telemetry-store `SELECT 1` with a one-second deadline.
The default `healthcheck` still uses local `/api/v1/health` for liveness;
changing the published `SIGNOZ_PORT` does not change this internal endpoint.
Compose runs this as a continuous healthcheck and retains the separate
`data-backend-ready` initialization gate. These checks do not prove authenticated
OTLP ingestion or durable trace readback.

Both native probes run in scratch images without a shell or curl. Use matching
gateway and data-backend images: source support in main does not prove that a
published or locally cached image contains it. The product readiness lifecycle
rejects older images that lack the SQL readiness endpoint; do not weaken its
probes to make an incompatible image pass. Existing `health.exec` keeps its
host-side shell behavior in `cast`/`status` and container-side `CMD-SHELL`
behavior in generated Compose files. When that command starts with `docker exec`
and the exact service container name (bare or single-quoted), Compose omits
that prefix because the probe already runs inside the container. The remaining
shell command is preserved. HTTP and TCP probes are unchanged.
The source blueprint retains its gateway and SigNoz TCP probes, so it can
still build branches whose binaries do not include the new subcommands.

---

## Running an isolated second stack (same host)

`--namespace` plus a few `--var` overrides gives you a fully isolated stack that
coexists with a running one — separate containers, ports, volume and state, no
collisions:

```bash
aiv cast --namespace aivtest \
  --var DATABASE_CONTAINER=aivtest-database \
  --var DATA_BACKEND_CONTAINER=aivtest-data-backend \
  --var INGEST_GATEWAY_CONTAINER=aivtest-ingest-gateway \
  --var BACKEND_CONTAINER=aivtest-backend --var FRONTEND_CONTAINER=aivtest-frontend \
  --var DIAGNOSIS_CONTAINER=aivtest-diagnosis \
  --var OB_PORT=2891 --var SIGNOZ_PORT=18092 \
  --var GATEWAY_GRPC_PORT=4327 --var GATEWAY_HTTP_PORT=4328 --var GATEWAY_HEALTH_PORT=13143 \
  --var BE_PORT=8011 --var AGENTLENS_PORT=8091 --var FE_PORT=5184

aiv status  --namespace aivtest           # the overrides were persisted with the namespace
aiv destroy --purge --namespace aivtest   # clean up, leaves the first stack alone
```

Each isolated stack bootstraps its **own** org, so there is no shared tenant id
to keep straight.

---

## Prerequisites

`aiv gauge` checks all of these and tells you exactly what is missing.

**Default (image) blueprint**

- **docker** (daemon running). That is all: the datastore bootstrap runs
  a SQL client inside the selected datastore container, and every AI Vision component is a
  released image.
- **Or your own OceanBase** (`--ob-host …`): a MySQL-mode user tenant whose
  `root` can `CREATE DATABASE` (or with `aivision`/`signoz`/`agentlens`
  pre-created), reachable from the docker network. The bundled datastore, its
  resource needs and its volumes then do not apply.
- **Or your own seekdb** (`--ob-host … --var OB_BACKEND=seekdb`): defaults to
  `root`, with no tenant; `OB_USER` can select an existing account with the same
  database creation/migration permissions. It must be reachable from containers.
- Resources for OceanBase CE in `mini` mode: ~6 GB RAM for the datastore
  container and ~10 GB free on the filesystem that holds docker volumes (5 GB
  datafile + 5 GB log disk are preallocated at first start; `OB_DATAFILE_SIZE`
  / `OB_LOG_DISK_SIZE` shrink them).
- Bundled seekdb defaults to 2 CPU, 2G memory, a 2G datafile, and 2G log disk.
  Other platform services need additional resources. The default pinned image is
  Linux/amd64; ARM requires emulation or a separately validated `OB_IMAGE`.
- Network access to Docker Hub (`oceanbase/oceanbase-ce` or `oceanbase/seekdb`) and `${IMAGE_REGISTRY}`
  on the first cast; `docker login ghcr.io` while the packages are private.
- To serve users on **other machines**, publish the two user-facing ports and
  tell Django/the Agent market the reachable address:
  `aiv cast --var BIND_ADDR=0.0.0.0 --var PUBLIC_HOST=<host-ip-or-dns>` (and
  override the default `*_SECRET` / `*_PASSWORD` vars first).
- On machines that run coding agents: a matching native installer needs no host
  Python. Source deployments' installers require Python 3.11+. Hook binaries are
  pre-built; client compiler tools are unnecessary.

**Source blueprint** (`-f ../deploy/blueprints/aivision-source.yaml`, from `aivision-cli/`)

- **docker** and **git**
- **uv** (the AgentLens host process Python env). The backend and frontend build
  and run **in containers**, so host `node`/`pnpm` are optional.
- **cargo** / **npm** if you want host-platform Agent-market plugins built at
  cast time (best-effort; missing toolchains skip that agent).
- Images: the selected OceanBase CE or seekdb image, `golang:1.25`,
  `ghcr.io/astral-sh/uv:python3.11-bookworm`, `node:22-bookworm` (pulled on first
  cast).
- Network for the **first** SigNoz community build (it has no vendor dir); the
  gateway build is vendored and fully offline.

> **Offline build of the CLI itself:** dependencies are vendored, so
> `make build` works with no module proxy.

---

## Project layout

```
aivision-cli/
├── main.go                    # entrypoint; embeds the built-in blueprint
├── internal/
│   ├── cli/                   # command dispatch + per-command flag parsing
│   ├── blueprint/             # blueprint schema, validation, ${var} expansion, topo order
│   ├── deploy/
│   │   ├── runtime/           # cast/forge/gauge/destroy/status engine
│   │   ├── generate/          # `generate` command: target dispatch
│   │   ├── compose/           # Compose renderer
│   │   ├── kubernetes/        # kustomize renderer
│   │   └── systemd/           # systemd unit renderer
│   ├── query/                 # AI Vision OpenAPI client + table/JSON rendering
│   ├── account/               # Cookie/CSRF account API client + serialized refresh
│   ├── auth/                  # legacy SpaceKey import/verify and terminal prompts
│   ├── config/                # ~/.aivision/config.json profiles + env.sh + shell block
│   ├── runner/                # logging, shell exec, docker helpers, health probes
│   ├── state/                 # pidfiles, once-markers, deploy metadata (per namespace)
│   ├── flags/                 # shared flag helpers
│   ├── pathenv/               # PATH augmentation for minimal launch contexts
│   └── proc/                  # process-tree termination
├── blueprints/
│   ├── aivision.yaml          # default recipe: released images from ghcr.io (embedded)
│   └── aivision-source.yaml   # developer recipe: clone + build from source
├── docs/                      # query.md, auth.md, privacy.md
└── vendor/                    # vendored yaml.v3 (offline build)
```

---

## Documentation

- **[Docker deployment](https://aivision.mintlify.site/en/deployment/docker)** — install `aiv`, deploy
  released images with OceanBase CE or seekdb, bundled or external, using a vars file.
- **[Kubernetes deployment](https://aivision.mintlify.site/en/deployment/kubernetes)** — prepare a
  cluster and deploy the stack with `aiv cast --target kubernetes`.
- **[docs/query.md](docs/query.md)** — getting a SpaceKey, the OpenAPI endpoint
  map, authentication, table/JSON examples and JSONL export for building
  datasets.
- **[docs/auth.md](docs/auth.md)** — pure CLI registration, spaces, keys, hook
  installation, status, profiles and non-interactive use.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
