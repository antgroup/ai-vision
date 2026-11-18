# AgentLens

AgentLens 是 AI Vision 的诊断后端，负责诊断器、数据集和任务的管理与调度，读取 Trace 执行诊断，
并保存诊断记录和终态统计。

AI Vision 通过 AgentLens HTTP API 使用诊断能力，不直接访问 AgentLens 所依赖的 OceanBase、
SigNoz 遥测库或 Antcollector。

## 架构概览

```text
AI Vision
    |
    | HTTP API
    v
AgentLens instances
    |-- web：资源管理、诊断记录、Metrics、内部执行接口
    |-- kernel：心跳、选主、调度、派单、救援和 Runner
    |-- diagnoser：Executor 注册和诊断执行
    |-- service：Trace 读取、Projection、记录和指标编排
    `-- infra：OceanBase、SigNoz、Antcollector、LLM Judge

Trace read:  SigNoz OceanBase SQL
Record write: AgentLens -> Antcollector OTLP -> SigNoz
```

实例采用对等部署，通过 OceanBase lease 选出唯一 Master，其余实例继续承担 Worker 和 HTTP 服务。
当前架构、数据流和分层边界见 [`docs/architecture.md`](docs/architecture.md)。

## 快速开始

本节面向第一次参与 AgentLens 开发的后端开发者。完成标准是：服务健康检查正常，单元测试通过。

本节用于单独开发 AgentLens。部署整套 AI Vision 时，按[部署指南](../docs/deployment/README.md)使用 `aiv`。

### 前置条件

- macOS 或 Linux；当前不支持 Windows；
- Python 3.11 或 3.12；
- Docker 与 Docker Compose v2；
- 可访问公共 Python 依赖源和容器仓库；
- 已安装 Git。

### 1. 创建虚拟环境

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

虚拟环境创建后，本机项目 Python 命令统一使用 `.venv/bin/*`。

### 2. 启动本地 MySQL

```bash
docker compose -f docker/docker-compose.standalone.yml up -d ob
docker compose -f docker/docker-compose.standalone.yml ps ob
```

Compose 使用内部 MySQL 镜像，默认创建：

```text
host: 127.0.0.1
port: 3306
database: agentlens
username: root
password: rootpw
timezone: Asia/Shanghai
```

### 3. 初始化数据库

`AIVISION_ENV=dev` 会加载 `env/env.dev`，其中本地开发默认直连上一步启动的 MySQL。

```bash
AIVISION_ENV=dev \
  .venv/bin/alembic -c obschema/alembic.ini upgrade head
```

### 4. 启动 AgentLens

```bash
AIVISION_ENV=dev AGENTLENS_IP=127.0.0.1 \
  .venv/bin/uvicorn --factory agentlens.main:build_app \
  --host 0.0.0.0 --port 8080
```

在另一个终端验证：

```bash
curl http://localhost:8080/internal/healthz
```

预期响应：

```json
{"status":"ok"}
```

除健康检查外，API 请求需要携带本地 token：

```bash
curl -H "x-antmonitor-token: dev-token" \
  http://localhost:8080/api/v1/cluster/instances
```

### 5. 运行单元测试

```bash
.venv/bin/pytest tests/unit/ -q
```

未配置隔离数据库时，只运行不依赖数据库的测试，数据库用例会明确标记 skipped，不会自动启动容器。
数据库回归只使用本轮创建的空 OceanBase / seekdb 数据库；连接文件不进入仓库：

```bash
AGENTLENS_TEST_DB_BACKEND=oceanbase \
AGENTLENS_TEST_DB_CONFIG_FILE=/private/tmp/agentlens-test-connection.json \
  .venv/bin/pytest tests/unit/ -q
```

seekdb 使用 `AGENTLENS_TEST_DB_BACKEND=seekdb`。私密 JSON 文件只包含
`host/port/user/password/database`；host 必须为 loopback，database 必须使用
`aiv_diag_unit_` 前缀且初始无任何表。fixture 只创建并清理自己在该空库中创建的 AgentLens 表。
不要把开发库或真实用户库配置用于测试。

### 6. 停止本地 MySQL

```bash
docker compose -f docker/docker-compose.standalone.yml down
```

需要删除本地数据库卷时使用 `down -v`。该命令会清除本地 Compose 数据。

## 常用开发命令

```bash
# 格式与静态检查
.venv/bin/ruff check src/agentlens tests
.venv/bin/python -m compileall -q src/agentlens

# 指定测试
.venv/bin/pytest tests/unit/web/routes/test_job_routes.py -q

# 检查 diff 空白错误
git diff --check
```

修改 Python 文件时，使用 `.venv/bin/ruff format <path>` 格式化改动路径，再以 `--check` 复核。

项目完整工程约定见 [`AGENTS.md`](AGENTS.md)。重大设计变更应先阅读相关 ADR，并按
[`docs/adr/README.md`](docs/adr/README.md) 完成设计评审。

## 可选开发环境

### 数据库连接

使用 `AGENTLENS_OB_URL` 或 `DB_*` 配置业务库连接，迁移与排错见
[`docs/ob-connection.md`](docs/ob-connection.md)。

### 完整容器和集群

standalone 全容器模式、三节点 Compose、基础镜像和发布容器说明见
[`docs/deployment.md`](docs/deployment.md)。

## 项目结构

```text
src/agentlens/
  main.py               FastAPI app factory 和 lifespan
  config.py             pydantic-settings 配置
  logging.py            structlog 初始化与日志路由
  domain/               纯领域模型、枚举和错误
  infra/                OceanBase、SigNoz、Antcollector、LLM Judge
  service/              Trace 数据源、Projection、记录存储和指标聚合
  diagnoser/            Executor 注册、内置定义和诊断实现
  kernel/               心跳、选主、调度、派单、救援和 Runner
  web/                  FastAPI 路由、鉴权、DI 和响应模型
  utils/                通用轻量工具

obschema/               ORM 对应的 Alembic migration 和生产 DDL
docker/                 镜像与本地 Compose
cd/                     发布启动脚本和 Supervisor 配置
tests/                  单元测试和集成测试
docs/                   当前架构、ADR 和专项文档
```

## 文档

| 文档 | 内容 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 工程规范和 Agent 工作约定 |
| [`docs/architecture.md`](docs/architecture.md) | 当前系统架构和核心数据流 |
| [`docs/adr/README.md`](docs/adr/README.md) | ADR 对象、主题和使用方式 |
| [`docs/api.md`](docs/api.md) | HTTP API 契约 |
| [`docs/configuration.md`](docs/configuration.md) | 配置参数 |
| [`docs/projection/`](docs/projection/README.md) | Trace、Projection 与 Projector 文档 |
| [`docs/diagnoser/`](docs/diagnoser/README.md) | 诊断器开发指南与专项文档 |
| [`docs/scheduling.md`](docs/scheduling.md) | 选主与任务调度 |
| [`docs/logging.md`](docs/logging.md) | 日志规范 |
| [`docs/ob-connection.md`](docs/ob-connection.md) | OceanBase 连接方式 |
| [`docs/deployment.md`](docs/deployment.md) | 镜像、单机和集群部署 |
| [`docs/diagnoser/specialists.md`](docs/diagnoser/specialists.md) | 配置专项诊断并试跑一条Trace |


Optional Custom Rule sandbox execution is available through a separately installed OpenShell
plugin. The base AgentLens dependency set remains provider-free. See
[the sandbox contract](docs/diagnoser/sandbox.md) and
[operator installation](../docs/deployment/en/sandbox-plugins.md).
