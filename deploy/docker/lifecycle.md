# Compose 健康、启动与停机契约

此页说明当前源码生成的产品 Compose 行为。安装入口、镜像前提和私有配置见
[README](README.md)；服务定义来自
[产品渲染器](../../aivision-cli/internal/deploy/generate/product.go)，
控制与运行顺序由 [Go Compose controller](../../aivision-cli/internal/deploy/compose/) 实现。
不要直接修改生成的 `compose*.yaml`。这些定义面向单机、单实例部署，不提供高可用承诺。

## Go control and native inspection

Docker through Go remains the supported default. Optional Compose is selected
explicitly with `aiv ... --target compose`; fixed Kubernetes is separate. There
is no host Python control dependency or extra controller script. Bootstrap
Python runs inside applicable application images.

The Go CLI invokes native Docker Compose and retains storage identity/ownership,
the shared lock, isolated seekdb initialization, initialization-task reruns and
exit/OOM-before-deletion checks. Use native config --quiet, ps --all and logs for
inspection with the exact original project name/file/env. A resolved CLI bundle
uses /dev/null; a parameterized standalone template uses its original actual env.
Pin --project-name to avoid ambient COMPOSE_PROJECT_NAME changing the target.
Bare native up/down is not an established equivalent of the guarded lifecycle.

CLI-managed Compose bundles are private and immutable under
<namespace-state>/compose/bundles/<hash>/. Cast/status display the actual path.
Configuration/images change through original vars/flags and cast, not edits to
live files. Saved effective image references override aggregate tag changes.
Status/logs/destroy use the validated saved exact bundle, not new defaults or
ambient deployment variables. Direct standalone files use --compose-file and
--compose-env-file (default .env, /dev/null for resolved exports), deriving
identity/state from YAML. This mode cannot mix namespace/workdir/vars/blueprint
overrides and does not automatically import standalone values into CLI vars.

Default Docker ownership remains supported and is not implicitly converted.
Foreign resources, contradictory markers and corrupted saved projects fail
closed. Stop failures preserve exact container IDs across retries. Native logs
and status need no additional control dependency; log streaming must not hold
the mutation lock. Compose purge removes named volumes, retaining bind-mounted
metastore/identity/configuration, unlike default Docker purge's original managed
volumes plus namespace state. External databases remain external. Compose rejects
redeploy --purge and --purge-repos before changes. Its fixed UTC history bound is
required and is never generated automatically for retained data.

## 三种不同的就绪证据

| 检查 | 当前职责 | 不能证明什么 |
|---|---|---|
| 长期运行服务的 healthcheck | 数据库认证 SQL、后端/诊断/数据后端 HTTP + 只读 SQL、前端 HTTP 和网关认证/下游探针 | 完整遥测路径、历史数据可读、负载能力或业务诊断成功 |
| 一次性初始化任务 | 校验数据库与建库，等待数据后端，再配置后端集成并导出组织 ID | 服务持续健康、任务自动重新执行、跨版本迁移一定成功 |
| 部署验收 | 真实 Web 入口、新 trace 采集与鉴权回读、保留 trace 回读、身份与卷绑定比较 | 未执行组合、不同架构或真实 Kubernetes 集群的结果 |

`data-backend` runs `/app/signoz healthcheck --ready` continuously inside its scratch
image, without a shell, curl or Python. The separate `data-backend-ready` task
still runs its HTTP wait script in the backend image and must succeed before
dependents start. Both that task and the gateway require `/api/v1/ready`, which
executes a read-only `SELECT 1` through the configured telemetry store with a
one-second deadline. Database failures return HTTP 503; local `/api/v1/health`
and native `healthcheck` without `--ready` remain independent liveness checks.
Older data-backend images without this endpoint fail readiness instead of
falling back to HTTP-only health. None of these checks proves authenticated ingestion or durable query
readback. Native commands already in main do not establish that a published or
cached image contains the matching readiness and lifecycle capabilities.
外部数据库没有本地 `database` 容器；连接、认证、兼容模式和应用库由
`database-init` 检查，外部服务的持续监控由其运维方负责。

`depends_on` 的 `service_healthy` / `service_completed_successfully` 用于启动依赖门槛。
长期运行服务使用 `restart: unless-stopped`；一次性任务使用 `restart: "no"`。
Docker 的 `unhealthy` 状态本身不会触发该重启策略，依赖方也不会因上游后来失去健康而自动重跑初始化。
发现异常时读取目标服务日志和数据库状态，修复原因，再用原配置运行生命周期入口。
参考 [Compose 服务健康与重启规则](https://docs.docker.com/reference/compose-file/services/#restart)。

## 启动顺序与等待预算

每次 `up` 先校验部署身份与存储所有权，持有部署锁，再停止当前服务并移除旧的一次性任务容器。
托管 seekdb 额外在无公开端口的内部网络中执行 `database-initialize`：
空卷设置密码；已有卷只验证原密码，不重置凭证。初始化进程和服务进程共享卷锁。
随后才执行最终 Compose `up --detach --wait`：

1. 托管数据库就绪，或外部数据库可连接；`database-init` 验证 MySQL 模式并建库。
2. 数据后端启动，`data-backend-ready` 等待只读 SQL 就绪接口成功。
3. 网关和后端启动；`backend-bootstrap` 配置集成并原子写入组织 ID。
4. 诊断服务读取组织 ID 文件；前端等待后端与初始化任务就绪。

当前默认预算以源码为准，不能简单相加当作安装时长保证：

| 位置 | 默认行为 |
|---|---|
| 容器健康检查 | 每 10 秒检查，单次最多 8 秒，连续失败 3 次；启动宽限见下表，首次成功即可就绪 |
| seekdb 独立初始化 | 整个阶段最多1200s，超时后另留120s退出；单条SQL最多30s，忽略TERM时5s后强制结束客户端 |
| `database-init` | 最多 200 次连接尝试，连接、读、写各有30s超时，失败间隔3s；认证拒绝立即失败 |
| `data-backend-ready` | 最多 200 次 HTTP 尝试，单次超时 3 秒，失败间隔 3 秒 |
| `backend-bootstrap` | 三条管理命令共用900s预算；单次命令最多180s、最多12次，失败间隔最多5s；超时停止子进程，保留已写入的组织检查点供重试 |
| 最终 `up --wait` | Go 控制入口当前固定等待预算为 1200 秒 |

该预算仅用于最终 Compose `up`，CLI 当前不暴露等待参数；它不限制前置镜像操作、停止阶段或独立的 seekdb 初始化。
探针尝试次数包含网络请求耗时；`200 × 3 秒` 不是完整超时上限。
首次下载、数据库恢复和迁移可能更久。失败后保留原配置、历史下界和数据，
先定位失败任务，再重试；不要用删除卷或更换数据库类型绕过失败。

## 逐组件探针与停机预算

| 角色 | healthcheck 的判定与失败含义 | 启动宽限 | 停机宽限 / 信号 |
|---|---|---|---|
| `database` | CE `obclient SELECT 1` / seekdb 原生 MySQL 客户端；必须以配置密码成功认证，失败表示 SQL 未就绪 | 600s | 120s / TERM |
| `data-backend` | Native `/app/signoz healthcheck --ready`: `/api/v1/ready` must return HTTP 200 within 2s after a successful telemetry-store `SELECT 1` (1s server deadline); the separate initialization wait remains | 60s | 60s / TERM |
| `ingest-gateway` | `/app/collector healthcheck --ready`: local pprof=200, unauthenticated OTLP=401 and downstream SQL readiness=200; three sequential checks with 2s timeouts under the 8s Compose budget | 60s | 60s / TERM; Collector closes receivers and attempts to drain its queue |
| `backend` | `/api/v1/ready`：HTTP 可服务且短超时数据库 `SELECT 1`；迁移由镜像入口先完成。`/api/v1/health` 仅本地存活 | 600s | 60s / TERM；supervisor 向 Gunicorn 和投影维护进程转发信号，最多等待50s；Gunicorn 保留45s排空在途请求 |
| `diagnosis` | `/internal/readyz`：HTTP 生命周期已启动且短超时数据库 `SELECT 1`；不证明任务执行或模型依赖正常 | 180s | 300s / TERM；现有运行时停止派单并等待已接受任务 |
| `frontend` | 镜像内 `curl -fsS` 请求 Nginx `/`，要求 HTTP 200；仅证明静态入口可用，API 请求由后端提供 | 30s | 30s / QUIT，Nginx 优雅退出 |

镜像必须包含本页所述新 readiness 接口；仅有旧 liveness 端点的镜像不适用。
Compose 只有一个 healthcheck，将上述检查作为就绪判断；Kubernetes 分开使用 startup、readiness 与本地 liveness。
Both gateway and data-backend probes execute their native binaries inside the
service container; scratch images need no shell or curl. Gateway
`/app/collector healthcheck` without `--ready` retains the 2s local liveness
check. The stronger readiness command gets 8s in CLI/Compose and 10s in the
fixed Kubernetes path; downstream failures must not become liveness failures.

诊断执行可能调用长时间运行的同步线程。容器300s宽限是进程退出上界，不能证明所有在途任务自然完成。
超过宽限的任务按现有异常退出/startup rescue/attempt限制处理，可能从头执行或最终失败，不是断点续跑。
升级前应停止投递并等待长任务结束；单实例 Recreate/停止重建有服务中断。
验收必须区分空闲/短请求退出与长诊断任务；不要将前者推广成无损排空承诺。

## 停止、重试与持久化

`up` 的准备阶段调用 `stop`，与 `down` 一样使用各组件的 `stop_grace_period`；这是有维护窗口的替换流程。
入口随后检查本次停止的长期服务是否已退出、退出码是否为0以及是否发生OOM；
`docker compose stop` 返回0本身不算停机成功。失败时不继续初始化、删除容器或清理卷，
并在私有state目录记录 `.compose-stop-pending.json`，重复命令仍检查同一失败容器。
先查看其日志并修复原因；可明确重启后成功停止，或检查后仅移除失败容器再重试，保留数据卷。
具体进程能否完成请求、刷盘或队列排空仍取决于镜像实现与负载，不能从配置值推断已验证无损停机。
The data-backend image must keep the shared SQLStore pool open until shutdown
snapshots finish; OpenFGA must not close a pool borrowed from SQLStore.
A nonzero exit during this shutdown remains a stop-gate failure.

CE 使用包装入口保留上游启动流程。停止时先终止启动脚本的进程组（最多5s），
再执行 `obd cluster stop obcluster`（100s，超时后5s强制结束该命令），并确认 observer 已退出；
命令失败、超时或 observer 残留都返回非零，不伪装为成功。
此入口只支持当前 mini/standard 的 `obcluster` 布局，不支持 SLIM/demo。
全新单节点 CE 在尚无 OBD 配置且未显式覆盖 `OB_SERVER_IP` 时，以 `127.0.0.1`
作为数据库内部记录的地址，避免绑定 Docker 网络重建后的临时容器 IP；
应用仍通过数据库服务地址连接。已有持久化 OBD 配置绝不自动重写：
若记录的是旧容器 IP，须保留原网络和 IP，不能将本次新部署默认值视为旧库地址迁移方案。

seekdb 使用 shell 作为PID 1，将容器TERM转换为数据库子进程的USR1并等待；
当前原生引擎的TERM处理会触发SIGKILL，USR1才调用 `prepare_stop` / `set_stop`。
90s后仍未退出才强制结束并返回失败。日志保留数据库子进程退出码及是否强制终止，
只将子进程0作为成功，143或137不会被吞掉；初始化清理也使用同一机制。
Signal handling alone does not establish lossless shutdown for long transactions
or other seekdb versions.
信号语义对应[实测seekdb源码](https://github.com/oceanbase/seekdb/blob/13e493b33b6c3ec22133e5fdf8694e8a4e9bff38/src/observer/ob_signal_handle.cpp#L135-L146)。
seekdb健康SQL在3s后发TERM，另留1s结束客户端，早于Docker的8s探针超时。

`down` 保留数据库命名卷、运行时卷、bind-mounted 元数据和部署身份。
`aiv destroy --target compose --purge` 删除命名卷，但保留 bind-mounted 元数据与部署身份；它不清空外部数据库。
同名配置的重试或升级继续使用原密码、固定历史下界和卷绑定，不自动接管 CLI 管理的部署。

## 副本与资源边界

产品模板为每个长期运行服务指定固定容器名与宿主端口，适用于单实例。
不要用 `--scale` 推断其支持水平扩展；数据库、元数据与诊断调度的多副本协调需要单独设计和验证。
Compose 没有为应用服务设置通用 CPU/内存 reservation 或 limit。
`SEEKDB_MEMORY_LIMIT`、`SEEKDB_CPU_COUNT` 等是数据库自身参数，不是整套平台的资源额度，
也不等于容器 cgroup 限制。负载和磁盘增长应按实际数据量测量。

静态配置、单元测试、健康状态与单次安装不能证明容量或升级兼容性。
准出记录应分别列明实际镜像、数据库组合、新旧 trace、组织身份、数据库 canary、卷绑定及恢复结果；
真实 Kubernetes、长时间负载、故障恢复和未执行的平台架构单独列为未验证。
