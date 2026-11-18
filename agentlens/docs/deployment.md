# AgentLens 部署指南

本文记录镜像构建、Docker Compose 单机/集群运行和发布容器约定。本地开发的主路径见根目录
`README.md`。

以下命令用于单独开发 AgentLens 和进行集群测试。部署整套 AI Vision 时，按[部署指南](../../docs/deployment/README.md)使用 `aiv`，维护时沿用部署指定的目标、命名空间、状态目录和配置参数。

Integrated releases use `docker/Dockerfile.selfhost`. Pass `AIVISION_BUILD_INFO_BASE64` at build time to embed the root product version and build identity in `/etc/aivision/build-info.json`; omitted metadata remains unknown. This file does not mean the platform has discovered a running instance. See the [product version guide](../../docs/version-info.md) for sources and limitations.

## 前置条件

- Docker 与 Docker Compose v2；
- 可访问 Docker Hub 和 Python 包源；

镜像使用公共 `python:3.12-bookworm` 基础镜像，Compose 数据库保持 `mysql:5.7` 版本。无需预先构建专用基础镜像。

```bash
docker build -f docker/Dockerfile -t agentlens:local .
```

## 单机 Compose

standalone Compose 会启动 MySQL、执行 Alembic migration，并构建、启动一个 AgentLens 实例：

```bash
docker compose -f docker/docker-compose.standalone.yml up -d
```

默认端口：

| 服务 | 地址 |
|---|---|
| AgentLens | `http://localhost:8080` |
| MySQL | `127.0.0.1:3306` |

验证：

```bash
curl http://localhost:8080/internal/healthz
curl -H "x-antmonitor-token: dev-token" \
  http://localhost:8080/api/v1/cluster/instances
```

查看日志：

```bash
docker compose -f docker/docker-compose.standalone.yml logs -f agentlens
```

Standalone 和 Cluster Compose 的 AgentLens 服务均使用 Docker `json-file` 日志驱动，单文件达到
50 MB 时滚动并保留 5 个文件，避免 `AGENTLENS_LOG_STDOUT=true` 时容器 stdout 无限增长。修改
Compose 日志配置后需要重新创建容器才会生效。

停止：

```bash
docker compose -f docker/docker-compose.standalone.yml down
```

需要同时删除本地数据库卷时使用 `down -v`。该操作会删除本地 Compose 数据，只应在确认不再需要后
执行。

## 三节点 Compose

集群 Compose 使用一个 MySQL、一个 migration job 和三个对等 AgentLens 实例：

```bash
docker compose -f docker/docker-compose.cluster.yml up -d
```

| 实例 | 宿主机端口 |
|---|---|
| `agentlens-1` | `8080` |
| `agentlens-2` | `8081` |
| `agentlens-3` | `8082` |

容器内端口均为 `8080`。实例通过 OceanBase lease 选出 Master，其余实例作为 Worker。Master 派单时
根据存活实例当前 RUNNING Task 数选择负载较低者；完整语义见 `docs/scheduling.md`。

验证 Master：

```bash
curl -H "x-antmonitor-token: dev-token" \
  http://localhost:8080/api/v1/cluster/leader
```

扩容 Compose 示例时，复制一个 AgentLens service，并修改 service 名、hostname 和宿主机端口。生产
扩容应由部署平台管理，不通过手工复制 Compose 文件实现。

停止集群：

```bash
docker compose -f docker/docker-compose.cluster.yml down
```

## Schema 管理

直连 MySQL 和 Compose 环境使用 Alembic：

```bash
.venv/bin/alembic -c obschema/alembic.ini upgrade head
```

部署账号没有 DDL 权限时，由数据库管理员执行迁移或审核后的 `obschema/ddl.sql`，不要在应用启动时自动改表。migration 和 `ddl.sql` 必须表达同一最终 schema。

Task 自动清理默认启用。按照
[task/retention@20260823-0234](adr/task/retention.md#task-retention20260823-0234)
的约束，启用该能力前必须通过迁移或管理员创建
`idx_task_status_finished_id (status, finished_at, id)`，确认索引生效后再发布应用；禁止在该索引缺失
时以 `TASK_CLEANUP_ENABLED=true` 启动新版本。

## 发布镜像

整套 AI Vision 使用 `docker/Dockerfile.selfhost`，默认以 `AIVISION_ENV=dev` 选择单节点配置。
其入口先读取可选的
`SIGNOZ_ORG_ID_FILE`，再执行现有 Alembic 迁移与 Uvicorn 启动。该文件由平台 bootstrap
原子写入并只读挂载，只保存组织 ID，不含内部 API key；显式 `SIGNOZ_ORG_ID` 优先。
配置了文件但文件缺失、为空或含多个值时，入口失败退出，部署编排须等待 bootstrap 完成后启动。

`docker/Dockerfile` 基于公共 Python 3.12 镜像安装当前 AgentLens 包，并复制：

- `src/`
- `env/`
- `obschema/`
- `cd/` 发布启动脚本和 Supervisor 配置

镜像预先创建以下目录并授权给 `admin`：

```text
/home/admin/agentlens
/home/admin/logs/agentlens
/home/admin/conf
```

运行时日志卷可能覆盖镜像内目录，因此 `cd/start.sh` 会在启动 supervisord 前重新创建并授权日志目录，
避免 `stdout_logfile` 的父目录检查阻断进程启动。

发布平台通过 postStart hook 调用 `cd/start.sh`。login shell 和 Supervisor 不会自动继承容器入口环境，
脚本会从 PID 1 环境读取 `AIVISION_ENV`，写入
`/home/admin/agentlens/.supervisor.env`；`cd/app.sh` 加载该文件后启动 Uvicorn。

`election.log`、`scheduler.log`、`executor.log` 和 `ob.log` 由应用按系统本地时间每日午夜滚动，默认
保留 7 天，可通过 `AGENTLENS_LOG_RETENTION_DAYS` 调整。Uvicorn 的 `stdout.log` 由 Supervisor 按
50 MB 滚动并保留 5 个备份。日志采集应持续读取无日期后缀的当前文件。

## ABI升级与回滚

发布ABI1.4构建前，排空全部受控旧worker pool及pending/dispatched/running/rescue/legacyNone活动，
包括Core和CUSTOM。保存source/target/catalog/schema/build摘要，暂停Job并核对零activity census；
旧workers退出后再激活新构建，不能混revision派单。详见[迁移与发布](scheduling.md#诊断退役迁移与发布)。

回滚旧绑定后恢复 enabled，仍须实际可运行的 source 1.3 worker/build 和对应依赖；归档源码不等于可运行镜像。
缺少恢复证据时保留 disabled，历史读取不受影响。

## 配置

配置优先级和全部参数见 `docs/configuration.md`。部署时重点确认：

- OceanBase 连接方式和数据源；
- `AGENTLENS_TOKEN`；
- SigNoz 查询凭证；
- Antcollector 上报地址和凭证；
- 心跳、lease、派单和救援周期；
- 日志目录和 stdout 策略。

数据库连接和迁移步骤见 `docs/ob-connection.md`。

## 健康与退出

`GET /internal/healthz` 不需要 token，用于存活探针。它只说明 HTTP 进程可响应，不代替 OceanBase、
SigNoz 遥测库或 Antcollector 的完整链路验证。
`GET /internal/readyz` 同样无需 token，沿用应用数据库配置，以独立短超时连接执行只读 `SELECT 1`；
MySQL 连接、读取、写入 socket 超时分别为 1 秒。应用数据库可用返回 200，
不可用返回 503，不输出异常详情。镜像入口在迁移成功后才启动 HTTP 服务。

应用退出时先停止 Master 循环并等待已接收的 Runner 收尾，再关闭 Antcollector Client，尽量刷新本地
OTLP 队列。上报进入本地队列不等于 SigNoz 已可查询，部署和排障时需要考虑异步写入延迟。
整套产品部署给 diagnosis 300 秒容器停止宽限。该宽限是部署平台强制结束进程的上界，
并不使应用内 Runner 排空变成有界操作。升级前应暂停任务提交并确认无长任务在途；
超过宽限的执行可能被中断，重启后由既有 startup recovery / RescueLoop 按 attempt 上限从头恢复，
可能达到上限后 FAILED，不提供断点续跑或无损退出保证。单纯取消等待协程无法停止 Runner 的同步执行线程。

## 相关文档

- `README.md`：本地开发快速开始
- `docs/configuration.md`：环境变量和启动配置
- `docs/ob-connection.md`：OceanBase / MySQL 连接与迁移
- `docs/logging.md`：日志目录和发布容器日志
- `docs/scheduling.md`：集群选主与任务调度
