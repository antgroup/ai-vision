# Kubernetes lifecycle and resource contract

This is the fixed product deployment's **single-node evaluation baseline**,
not a production sizing or high-availability claim. Use matching application
images built from the same revision: backend SQL readiness, diagnosis SQL
readiness, bounded application shutdown, and the gateway's native
`/app/collector healthcheck --ready` are required capabilities. Older images may run but
will not become Ready. The database remains the pinned OceanBase CE Operator
deployment; managed/external seekdb and external OceanBase are not supported by
this Kubernetes path.

Future Helm support must preserve this lifecycle and resource contract.
Its [configuration and ownership boundaries](helm-roadmap.md) are documented;
the Chart is planned and currently unavailable.

## Health and initialization

Each application gets an independent CLI deployment deadline, default `TIMEOUT=30m`.
Previously saved or explicit values still take precedence; choose a value exceeding
the longest startup plus rollout margin. A failed CLI wait retains resources and
does not roll back. Kubernetes continues reconciling after the CLI exits.

Application startup probes run every 5 seconds with a 5-second timeout.
Readiness failures remove the Pod from Service endpoints; they do not restart
it. Liveness failures restart the container after three consecutive failures,
with a 30-second period and 5-second timeout. Startup success enables the other
probes. Deployments require five continuous ready seconds before availability,
and their progress deadline is startup allowance plus 120 seconds.
These are conservative configured allowances, not measured startup percentiles.

| Component | Startup allowance / evidence | Readiness / traffic gate | Liveness / failure meaning |
| --- | --- | --- | --- |
| frontend | 300s; HTTP `/` on 5174 | Same local static response, 5s period / 3 failures; backend availability is independently gated by its Service | Local HTTP `/`; Nginx cannot serve static content |
| backend | 1200s; local `/api/v1/health` after entrypoint migrations, platform config and seed data | `/api/v1/ready`: successful application-account `SELECT 1`; 5s period / 3 failures | `/api/v1/health` does not query downstream services; process cannot answer |
| diagnosis | 600s; local `/internal/healthz` after schema migration and runtime startup | `/internal/readyz`: successful control-database `SELECT 1`; 5s period / 3 failures | `/internal/healthz` checks the process, not database or data-backend availability |
| ingest-gateway | 300s; native `/app/collector healthcheck` checks local pprof on 13133 | `/app/collector healthcheck --ready`: local pprof success, OTLP HTTP rejects unauthenticated writes, data-backend SQL readiness succeeds; 10s period / timeout, 3 failures | Native `/app/collector healthcheck` checks local pprof only; no shell, credentials or downstream request |
| data-backend | 600s; `/api/v1/ready` on 8080 | Telemetry-store `SELECT 1` with a 1s server deadline; HTTP 503 on failure, 5s period / 3 failures; does not replace authenticated trace acceptance | TCP on 8080 only; closed listener triggers restart. A transient database outage does not restart the SQLite writer |
| managed Ingress | 300s; Traefik `/ping` | `/ping`, 5s period / timeout, 3 failures | `/ping`, 10s period / 5s timeout, 3 failures |
| database | Operator owns the Pod; CLI waits for both OBCluster and OBTenant `running`, then a successful SQL initialization Job | Operator 2.3.4 uses TCP 2881 readiness, not tenant authentication; the SQL Job and application readiness provide the account-level checks | No product liveness probe is added to an Operator-owned Pod; crash recovery remains the Operator's responsibility |

The gateway probe establishes that listeners, authentication rejection and the
downstream SQL readiness route are available. The latter uses the configured
telemetry store rather than the legacy ClickHouse connection, so it works with
OceanBase CE and seekdb. It does not prove valid-token
ingestion or durable storage. `/app/collector healthcheck` retains main's
two-second local liveness check; startup/liveness have five-second probe budgets.
The `--ready` variant runs three sequential checks with two-second timeouts;
the Kubernetes readiness probe allows ten seconds. Native liveness in main does
not imply that an existing image contains readiness support or has been published.
The data-backend image must implement `/api/v1/ready`; an older image returning
404 is not ready. Plain `/api/v1/health` stays local and cannot replace this check.
Readiness does not claim bootstrap completion:
the CLI waits for the separate bootstrap Job, then obtains the organization ID,
then deploys diagnosis and frontend. A backend process can be Ready before that
Job completes. Product acceptance still requires authenticated OTLP write and
readback through the public origin.

The two Jobs have `parallelism=1`, `completions=1`, `restartPolicy=Never`,
`backoffLimit=2`, a 900-second total deadline and 30-second termination grace.
`podReplacementPolicy=Failed` prevents a replacement while a previous Pod is
still terminating. Repeating `cast` deletes each old Job with foreground
propagation and waits before recreating it. Do not run concurrent deployment
commands against the same namespace.

| Job | Prerequisite and completion |
| --- | --- |
| database-init | Tenant running, then up to 90 SQL connection attempts with 5s connect timeout and 3s delay; creates the three databases idempotently. Its 900s deadline includes connection attempts and 30s SQL I/O timeouts. A nonzero exit never opens the next deployment stage |
| backend-bootstrap | Backend migrations and SQL readiness succeeded; configures the agent catalog and integration, verifies the internal key, and requires integration status. Only organization identity is published to diagnosis; the private integration key remains in the backend |

## Termination and recovery

| Component | Pod grace | Current behavior and limit |
| --- | --- | --- |
| frontend | 60s | Official Nginx image stop signal performs graceful shutdown. Requests that outlive the Pod grace can be interrupted, including long synchronous LLM trials |
| backend | 60s | The backend supervisor is PID 1 after initialization and forwards TERM/INT to Gunicorn and the projector. It allows up to 50s before force-killing/reaping survivors, retaining Gunicorn’s 45s graceful timeout within the 60s Pod budget. Either child failing stops its sibling for container restart; long requests may still be interrupted |
| diagnosis | 300s | Uvicorn stops accepting connections and application shutdown stops scheduling before draining accepted work and closing clients. The matching image must provide a bounded drain; interrupted tasks depend on existing lease/fencing/rescue behavior, not zero-loss completion guarantees |
| ingest-gateway | 60s | Collector receives its termination signal and attempts exporter flush. The in-memory queue is not durable; a downstream outage extending beyond shutdown can lose accepted telemetry |
| data-backend | 60s | Give the upstream server time to close its HTTP server and SQLite metadata store; upstream bounded drain is not established by these manifests |
| managed Ingress | 60s | Traefik uses its image shutdown behavior; graceful forwarding within this budget still needs live validation |
| database / shared controllers | Operator/upstream defaults | Operator 2.3.4 does not expose observer Pod grace/probe overrides through this CR. Its generated observer Pod uses Kubernetes' 30s default grace; its manager manifest declares 10s. No direct Pod patch is applied that the Operator would overwrite |

Kubernetes removes terminating endpoints and sends the image's stop signal, then
forces termination when the grace expires. A grace value provides a hard outer
bound; it does not prove that every accepted task is drained. See the
[Kubernetes Pod lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/).
Do not forcibly delete the data-backend or database Pod to speed an update.

A temporary SQL outage makes backend and diagnosis unready, while their local
liveness remains successful. A data-backend outage makes the gateway unready
without restarting its collector. Returning dependencies should restore Ready
automatically. The data-backend uses TCP liveness to avoid a dependency-driven
restart loop, at the cost of not detecting an HTTP deadlock with an open socket.
In that case diagnose the Pod before a deliberate restart.

## Replicas, state and update boundaries

| Component | Default / supported replicas | Workload / update strategy | Constraint |
| --- | --- | --- | --- |
| database | 1 / 1 | OBCluster, one zone and observer; Operator-managed updates | Single tenant unit and fixed storage/topology; no HA claim, database image changes rejected for an existing installation |
| data-backend | 1 / 1 | Deployment / Recreate | One SQLite metastore writer, fixed ReadWriteOnce PVC; extra replicas or RollingUpdate can corrupt state or fail attachment |
| backend | 1 / 1 | Deployment / Recreate | Entrypoint migrations run before serving; multi-replica migration locking and bootstrap concurrency are not established |
| diagnosis | 1 / 1 | Deployment / Recreate | Database leases and fencing exist, but fixed deployment and startup migrations have not been validated for concurrent replicas or rolling leader handoff |
| ingest-gateway | 1 / 1 | Deployment / Recreate | Stateless routing with local volatile batches; scale-out, rate capacity and loss behavior need separate validation |
| frontend | 1 / 1 | Deployment / Recreate | Local static assets; one replica keeps this evaluated product topology consistent |
| managed Ingress | 1 / 1 steady state, 2 during update | Deployment / RollingUpdate; maxSurge 1, maxUnavailable 0 | Two stateless controller Pods may overlap; include the extra request in scheduling capacity |
| initialization Jobs | 1 / 1 active attempt | Job / serial replacement | Idempotence does not authorize concurrent schema/bootstrap mutations |
| ob-operator and cert-manager | Pinned upstream defaults; one of each controller | Existing compatible controllers are reused | Shared installation ownership and leader election remain upstream; the CLI does not resize another installation |

Application Recreate updates have a maintenance window and no application Pod
surge. Physical workload names, Service DNS, immutable selectors, PVC names,
database/schema names and saved state remain compatible with prior installations.
Role labels do not change those identities. No HPA or PodDisruptionBudget is
installed: a one-replica PDB would block voluntary maintenance without providing
another serving replica.

## Resource baseline

Requests reserve scheduling capacity; limits cap runtime use. This evaluation
profile deliberately leaves burst room above requests. CPU throttling can delay
probes and migrations; memory-limit termination can interrupt work. These values
are a starting point for measurement, not demonstrated capacity for a given
ingestion rate. See [Kubernetes resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).

| Component | CPU request / limit | Memory request / limit | Reason |
| --- | --- | --- | --- |
| frontend | 50m / 500m | 32Mi / 256Mi | Static Nginx with bounded proxy workload |
| backend | 250m / 2 | 512Mi / 2Gi | One Python web worker with bounded threads; startup migrations need burst CPU |
| diagnosis | 250m / 2 | 512Mi / 2Gi | Python runtime, execution threads and rule subprocesses; default execution concurrency is not a 2Gi capacity guarantee |
| ingest-gateway | 100m / 1 | 128Mi / 768Mi | Collector limiter at 512Mi; `GOMEMLIMIT=576MiB` leaves runtime/native headroom below the cgroup cap |
| data-backend | 250m / 2 | 512Mi / 2Gi | Telemetry query/write service plus SQLite metastore; upstream heap and concurrency still require load sizing |
| managed Ingress | 100m / 500m | 64Mi / 256Mi | Namespace-scoped router; allow one additional Pod during rollout |
| database-init | 100m / 1 | 128Mi / 512Mi | One Python SQL initializer |
| backend-bootstrap | 100m / 1 | 256Mi / 1Gi | One Django management-command process at a time |
| database observer | 2 / 2 | 10Gi / 10Gi | Fixed Operator CR budget; system memory 1G, tenant 2Gi/1 CPU is inside this observer budget, not an extra Pod |
| ob-operator manager + RBAC sidecar | 15m / 1.5 | 128Mi / 1152Mi | Exact pinned upstream manifest (10m/64Mi manager + 5m/64Mi sidecar requests) |
| cert-manager controller, cainjector, webhook | Not declared by pinned upstream | Not declared by pinned upstream | Intentional dependency exception: preserve checksum-verified upstream distribution and existing shared installations. Reserve and measure extra node capacity; namespace policies may inject resources and must be accounted for |

The gateway's 512Mi memory limiter applies backpressure before its 768Mi hard
cap. The exporter has four consumers, a 10,000-batch in-memory queue and retries
for up to five minutes. That queue is bounded by item count, not bytes; its
configured maximum is not safe payload capacity under all traffic. Measure
queue occupancy, refused/dropped data and RSS before selecting production
limits. `GOMEMLIMIT` is a soft Go runtime target, not a hard process memory cap.
Diagnosis has a bounded execution concurrency but can also use subprocesses;
the whole process tree counts against its container memory limit.

Declared steady-state requests, including managed Ingress, database and the
Operator sidecar, total **3.015 CPU / 12,128Mi (11.84375Gi)**. A conservative
upper scheduling envelope adds both Jobs and an Ingress surge Pod:
**3.315 CPU / 12,576Mi (12.28125Gi)**. The CLI actually runs Jobs serially; the
larger envelope allows job cleanup overlap. There are no product application
sidecars or init containers in this profile.

The matching declared limits are **11.5 CPU / 18.375Gi** steady state and
**14 CPU / 20.125Gi** with both Jobs and the Ingress surge. They exclude
cert-manager, the Kubernetes control plane, DNS/CNI/storage agents, Pod
overhead, Operator lifecycle helper Pods and other workloads. Shared-controller
upgrades need their own rollout budget. These exclusions prevent interpreting
the totals as whole-node maximum consumption.

`gauge` requires at least 3.4 CPU / 14Gi free by requests, including one node
with 2 CPU / 10Gi for the observer. A nominal 4 CPU / 16Gi cluster is only an
evaluation scheduling floor: it cannot let all memory limits burst at once.
Measure node memory and leave capacity for the excluded components; do not
overcommit their peak demand. PVC requests remain 130Gi total (observer
50+50+20Gi and metastore 10Gi), with at least 140Gi backing disk recommended.
Changing capacity does not rename or recreate existing volumes.

## Verification boundary

Automated tests render and schema-validate manifests, enforce the probe split,
serial Jobs, replica/update constraints and complete resource budgets. The
fake-cluster lifecycle tests verify ordering and retained identities. These
checks do not establish production health or resource sizing.

For each real cluster/image combination, record fresh and repeated `cast`,
credential/organization/PVC preservation, authenticated OTLP ingestion and
retained-trace readback, then record the following before claiming the issue's
full lifecycle/resource acceptance:

1. Cold database start, backend/diagnosis migrations, Job retries and restart
   after partial initialization; include duration and deadline margin.
2. Temporarily make SQL and data-backend unavailable: dependency consumers lose
   Ready without a cascade of restarts, then recover automatically.
3. Terminate each workload with active HTTP/diagnosis/ingest work; record request
   completion or interruption, task recovery, exporter loss and exit duration
   against the configured grace. Observe database recovery after a normal stop.
4. Run representative sustained ingestion and diagnosis load. Report requested
   throughput, CPU throttling, peak RSS/process-tree memory, OOM/restarts,
   probe failures, queue/backpressure/drop signals and retained-data correctness.
5. Upgrade to a different application image revision, verifying Recreate does
   not overlap metastore writers, and compare namespace, volume bindings,
   organization identity, metadata and pre-upgrade trace readback.

No measured cold-start/load/throttling/OOM/drain result is asserted by this
contract. Attach actual cluster evidence separately. The observer lifecycle
and resource inheritance above are reviewed against the pinned
[ob-operator 2.3.4 Pod construction](https://github.com/oceanbase/ob-operator/blob/2.3.4/internal/resource/observer/utils.go).
