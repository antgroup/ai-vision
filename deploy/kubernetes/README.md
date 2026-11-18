# Fixed Kubernetes deployment resources

`deploy/kubernetes/base/` is the authoritative fixed product template source;
`deploy/kubernetes/dependencies/` owns pinned upstream distributions. The
packaging script copies these into `aivision-cli/deploy/kubernetes/`, where
`assets.go` embeds them into the CLI. Edit the canonical sources and run
`make assets` in `aivision-cli/`; generated copies must not be edited directly.
These resources are not generated from a Docker blueprint. `internal/deploy/kubernetes`
only substitutes the documented scalar parameters and applies these resources
in dependency order. Generated secrets are sent over stdin to kubectl and never
written into the deployment state or printed.

User instructions: [Kubernetes deployment](https://aivision.mintlify.site/en/deployment/kubernetes).

Install with `aiv cast --target kubernetes`. The generic
`aiv generate --target kubernetes` converter is deprecated as a product
installation path and omits this lifecycle. Only OB Operator-managed OceanBase
CE is supported here; external databases, seekdb and
`AIVISION_NATIVE_TRACE_HISTORY_START` are not supported by the fixed CLI.

Helm support is a committed follow-up and is currently unavailable: there is no
product Chart or Helm installation target. The [future Helm contract](helm-roadmap.md)
maps current configuration, bootstrap and ownership behavior to that delivery.
Issue #88 records those boundaries; Chart implementation and runtime validation
remain deferred.

Role selectors are `frontend`, `backend`, `diagnosis`, `ingest-gateway`,
`data-backend` and `database`, mapped by `deploy/components.json`.
Legacy selectors and configuration keys remain aliases. Role metadata and
`app.kubernetes.io/component` Pod labels do not rename physical resources,
existing selectors, DNS, Secrets, state identity or PVC bindings. In particular,
the upstream `signoz-ob` resource/image names remain deliberate implementation
compatibility names, while operational output calls this service `data-backend`.
The database engine is the separate `database` component.

CI checks rendered standard Kubernetes and pinned CRD schemas, plus simulated
lifecycle contracts. Those do not establish live admission behavior or upgrade
persistence. Report real-cluster evidence separately from Docker acceptance.

## Dependencies and provenance

The files under `dependencies/` are unmodified upstream distributions:

| Dependency | Version | Source |
| --- | --- | --- |
| cert-manager | 1.21.2 | [cert-manager.yaml](https://github.com/cert-manager/cert-manager/releases/download/v1.21.2/cert-manager.yaml) |
| ob-operator | 2.3.4 | [operator.yaml](https://raw.githubusercontent.com/oceanbase/ob-operator/ob-operator-2.3.4/deploy/operator.yaml) |
| OceanBase image | 4.4.2.1-101000022026050611 | `oceanbase/oceanbase-cloud-native:4.4.2.1-101000022026050611` |
| Managed Ingress Controller image | 3.7.13 | `traefik:v3.7.13`; [release](https://github.com/traefik/traefik/releases/tag/v3.7.13) |

`dependencies/SHA256SUMS` records the checked-in manifest hashes; the adjacent
license files cover the upstream material. Runtime application adds an aiv
ownership label and uses server-side apply without forcing field conflicts.
Compatible existing cert-manager 1.21.x (patch >= 2) and ob-operator 2.3.4
controllers are reused without modifying their resources, including compatible
Helm installations in other namespaces. Partial unmanaged installations and
other versions cause an explicit error. Kubernetes 1.33–1.36 follows
[cert-manager's support matrix](https://cert-manager.io/docs/releases/).

`database.yaml` uses the Operator's Service mode and independent PVC lifecycle.
Database topology, tenant name and storage requests are deliberately fixed.
The data backend uses a single replica with a Recreate strategy so its SQLite metastore
never has two writers. No host paths or provider-specific commands are used.
All five applications use a single-replica Recreate strategy; upgrades can
interrupt service.
The [lifecycle and resource contract](https://github.com/antgroup/ai-vision/blob/main/deploy/kubernetes/lifecycle.md) documents each probe,
startup and termination budget, scaling boundary, and the remaining live-cluster
validation. Readiness endpoints require application images built from the matching
source revision; older backend, diagnosis, gateway and data-backend images are incompatible.
The data backend must provide `/api/v1/ready`, backed by a read-only telemetry-store
`SELECT 1` with a one-second deadline; plain `/api/v1/health` is liveness only.

The managed Traefik manifest is maintained here rather than vendored as an
upstream distribution. It uses standard Kubernetes Ingress resources without
Traefik CRDs, watches only the application namespace, and runs one Controller
replica. Existing cluster Controllers can be reused without modifying them.

## Configuration

Precedence: defaults → previous cast for this cluster/namespace → `--vars-file`
→ `AIV_VAR_*` environment → `--var`. `--reset-vars` ignores local saved values;
it never resets data or Secrets. State lives at
`~/.aivision/deploy/kubernetes/<cluster-UID-hash>/<namespace>/config.json`.

| Variable | Default / meaning |
| --- | --- |
| `IMAGE_REGISTRY`, `IMAGE_TAG` | `ghcr.io/antgroup/ai-vision`, `latest`. A different registry prefix keeps historical image basenames. Set complete role image refs for a mirror with a different layout. |
| `IMAGE_PULL` | `Always`; also `IfNotPresent` / `Never`; `always` / `missing` accepted |
| `IMAGE_AUTH` | `auto`, which allows anonymous pulls for the five official public service packages. Use `anonymous` for other public registries. |
| `IMAGE_PULL_SECRET` | Existing `kubernetes.io/dockerconfigjson` Secret in the deployment namespace; otherwise create/reuse `aivision-registry` |
| `STORAGE_CLASS` | Cluster default; explicitly required if absent or ambiguous |
| `PUBLIC_HOST` | `127.0.0.1`; an externally reachable IP/hostname |
| `PUBLIC_PORT`, `PUBLIC_SCHEME` | `5174`, `http`; the shared public origin for Web and OTLP HTTP |
| `INGRESS_CONTROLLER` | `managed` installs pinned Traefik in the application namespace; `existing` uses an installed Controller |
| `INGRESS_CLASS` | `aivision`; the IngressClass to create or reuse |
| `INGRESS_HTTP_NODE_PORT`, `INGRESS_HTTPS_NODE_PORT` | `30080`, `30443`; managed Controller Service NodePorts (30000–32767) |
| `INGRESS_TLS_SECRET` | Same-namespace `kubernetes.io/tls` Secret, created beforehand; required with `PUBLIC_SCHEME=https` |
| `INGRESS_IMAGE` | `traefik:v3.7.13`; optional mirrored controller image, using `IMAGE_PULL_SECRET` when configured |
| `TIMEOUT` | `30m` per deployment stage; each application has its own deadline; saved explicit values remain authoritative |
| `SIGNOZ_IMAGE`, `GATEWAY_IMAGE`, `BE_IMAGE`, `AGENTLENS_IMAGE`, `FE_IMAGE` | Optional full image references instead of the registry/tag defaults |
| `AIVISION_RELEASE_MANIFEST_BASE64` | Verified product release record from the selected Release vars; forwarded to About separately from each image's historical build identity |
| `OB_IMAGE` | Pinned cloud-native image above; changing an existing database image is rejected |

App image tags never change the database or dependency images. Changing
`STORAGE_CLASS` for existing volumes is rejected. Cluster capacity checks use
node allocatable resources minus other Pods' requests; they cannot prove a
dynamic provisioner's free physical disk capacity. Insufficient backing storage
surfaces via PVC/Pod events and a failed deployment wait.

Frontend and gateway use internal ClusterIP Services; their containers still
listen on 5174 and 4318. Ingress sends `/v1/traces`, `/v1/logs`, and
`/v1/metrics` to the gateway and `/` to frontend Nginx, which continues proxying
`/api/` and `/openapi/` to the backend. Web and OTLP HTTP advertise one origin,
`PUBLIC_SCHEME://PUBLIC_HOST:PUBLIC_PORT`. OTLP gRPC remains internal. Internal
Service addresses, ingest tokens, service credentials, and database access do
not change.

With `INGRESS_CONTROLLER=managed`, the Deployment, Service, and ServiceAccount
are named `aivision-ingress` in the application namespace. The Controller
Service exposes 80 → 8000 and 443 → 8443 through the configured NodePorts. Its
IngressClass uses `traefik.io/ingress-controller`. Class ownership prevents a
second managed installation in another namespace from taking it over. A
managed Controller watches only its application namespace. To reuse a
Controller with `INGRESS_CONTROLLER=existing`, it must already watch the
target namespace; an aiv-managed class scoped to another namespace is rejected.
Existing mode creates application routes without changing the installed
Controller's workloads, RBAC, or exposure configuration. For a Traefik class,
HTTPS routes explicitly enable TLS without assuming an entry point name;
other controller types receive only standard Ingress TLS configuration.

For a new kind cluster, map host port 5174 to node port 30080. An optional
HTTPS mapping uses host port 443 and node port 30443. Without port mapping or
a load balancer, set `PUBLIC_PORT` to the reachable Controller NodePort.
HTTP can use an IP address, in which case the Ingress rule omits `host`.
HTTPS requires a DNS `PUBLIC_HOST` and the pre-created `INGRESS_TLS_SECRET`;
the managed route binds to the HTTPS entry point. It does not configure an
automatic HTTP-to-HTTPS redirect. Certificate issuance is not automated by
these resources, even though cert-manager is installed for ob-operator.

Kubernetes enables `AIVISION_TRUST_PROXY_HEADERS=true` in frontend and backend
so TLS termination preserves the original scheme through Nginx to Django.
The managed Controller overwrites client-supplied forwarded headers. An
existing Controller must do the same and preserve the original Host. Direct
Docker deployments keep this opt-in disabled by default.

All application Pods inherit image pull credentials from the `aivision` Service
Account and disable automatic Kubernetes token mounting in their Pod specs.
OceanBase mounts the ServiceAccount token because its official entrypoint
initializes an in-cluster Kubernetes client. Bootstrap Jobs have no Kubernetes
API credentials; the CLI reads only the non-sensitive organization ID and
creates `aivision-runtime`. The internal data-backend API key stays in the backend DB.
The Ingress Controller uses its own ServiceAccount and Kubernetes API token to
watch Services, endpoints, TLS Secrets, and routing configuration.

`Always` assigns a new Pod template annotation each cast, including for an
unchanged rolling tag. App config changes also change a template checksum. The
database and dependency workloads are never restarted solely for a new app tag.
Disposable initialization Jobs are deleted with foreground propagation before
recreation; they use idempotent database and backend operations.

## Native query recovery controls

The fixed CLI accepts `BACKEND_QUERY_MODE` (`auto` or `raw`) and `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED` (`true` or `false`) in the original vars file or `--var`; legacy application spellings remain aliases. Defaults are `auto` and `true`. Edit the authoritative `deploy/kubernetes/base/` resources only, then regenerate compatibility assets with `make assets`; do not patch embedded copies or live Pods to maintain configuration.

Reapply with the same context, namespace and private configuration. Normal backend startup supervises Gunicorn and the projector together, while the bootstrap Job's explicit command does not start either. Shared maintenance suspension survives a default-ON restart; privileged recovery follows the [operator reference](https://github.com/antgroup/ai-vision/blob/main/docs/deployment/en/trace-read-model.md). Only the recovery process receives the existing internal ingest key. These controls do not change replicas, database support, PVC ownership or the distinction between rendered and live-cluster validation.
