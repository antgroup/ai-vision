# AI Vision 产品部署资产

`deploy/` 是产品部署定义、初始化材料和验收入口的权威目录。安装前请在[部署概览](https://aivision.mintlify.site/zh-CN/deployment/overview)选择适合的方式。当前源码能力、版本差异和运维补充见[部署维护索引](../docs/deployment/README.md)。

日常产品操作统一使用固定安装的 `aiv`，并[验证新终端可直接执行](../aivision-cli/README.md#make-aiv-available-in-new-terminals)。部署、升级、状态、日志、重建和保留数据停止使用所选 target 支持的 CLI 命令；每次保留原 namespace、workdir、blueprint、私密配置或 Compose file/env，不另建 `manage` 包装脚本。Kubernetes 当前没有 `destroy`／`redeploy`；单组件开发、原生只读排障和明确的运维修复保留各自入口。

| 目录 | 所有权 |
|---|---|
| `blueprints/` | CLI 镜像部署和源码开发蓝图 |
| `docker/` | 从产品蓝图生成的四种 Compose 配置与 `.env` 示例；日常通过 Go CLI 操作，原生 Compose 用于明确的只读排障 |
| `kubernetes/base/` | 固定 Kubernetes 产品资源模板；由 CLI 注入参数并按生命周期应用 |
| `kubernetes/dependencies/` | 原样保留的上游资源、许可证和校验和 |
| `bootstrap/` | 容器内数据库初始化、后端集成初始化与就绪等待材料；Compose 控制由 Go CLI 承担 |
| `config/` | CLI 私有配置的可编辑示例 |
| [`native-packaging/`](native-packaging/README.md) | Python 原生编译统一入口、构建步骤、兼容经验与分架构验收记录；镜像组装和分发验收仍需单独完成 |
| `tests/` | 部署与生命周期验收；不取代各组件自身测试 |
| `components.json` | 产品角色、旧标识、配置别名与实现的统一映射 |

Coding agents：先读 [部署代理规则](AGENTS.md)。产品镜像和 Release 统一从
`antgroup/ai-vision` 发布，用户技能保留在独立的 Skills 目录仓库。
可选的原生编译按 [原生打包和验收契约](native-packaging/release-contract.md)
执行构建、镜像和 CLI 审计，分架构、分数据库记录运行验收和离线制品证据。
历史验收记录以各自对应的快照为准。

组件 Dockerfile 和单组件开发环境仍由各组件目录维护。此目录不合并服务、不切换已有数据库，
也不提供 CLI 与 Compose 现有部署之间的自动接管。

可选 OpenShell Sandbox 插件优先从 **集成 → 插件 → 沙箱 → OpenShell** 准备
`aiv plugin setup openshell` 命令，再在实际 AI Vision 部署主机上执行。匹配的 CLI 和
不可变安装清单负责准备镜像、专用网关、私有配置及空间连接，无需普通用户填写技术参数。
空间默认绑定仍需在连接保存后明确点击，不能用服务就绪代替连接注册或默认绑定。

已有网关的 `aiv plugin install/status/logs/remove openshell` 保留为高级运维入口。
OpenShell 可选组件独立于产品的五服务制品和验收。源码和本地验证清单记录对应版本和架构的实测范围。入口、清单与清理边界见
[中文指南](../docs/deployment/zh-CN/sandbox-plugins.md) / [English guide](../docs/deployment/en/sandbox-plugins.md)。

## 组件命名与兼容

产品选择器采用小写 kebab-case，`data-backend` 表示遥测存储与查询服务，`database` 表示数据库引擎。
实现名、数据库 schema、镜像仓库、上游协议名有独立兼容要求；它们不随显示名称替换。

| 产品角色 / CLI 与 Compose 选择器 | 职责 | 保留的旧 CLI 标识 | 实现及旧物理资源 |
|---|---|---|---|
| `frontend` | Web 入口 | `fe`、`aivision-fe` | React / Nginx；`aivision-fe` |
| `backend` | 平台 API | `be`、`aivision-be` | Django；`aivision-be` |
| `diagnosis` | 诊断 | `agentlens` | AgentLens；`agentlens` |
| `ingest-gateway` | 遥测接入 | `gateway`、`antcollector-gw` | AntCollector；`antcollector-gw` |
| `data-backend` | 遥测存储与查询 | `signoz`、`signoz-ob` | 外部 SigNoz OceanBase 分发；`signoz-ob` |
| `database` | 数据库引擎 | `oceanbase`、`seekdb` | OceanBase CE / seekdb；Kubernetes 使用 OB Operator |

CLI 接受 `DATA_BACKEND_IMAGE`、`BACKEND_PORT` 等角色配置别名，完整映射以
[components.json](components.json) 为准。旧 `SIGNOZ_*`、`BE_*` 等配置继续有效：
同一配置层同时出现时，角色别名优先；配置层顺序仍为默认值 < 已保存配置 < vars 文件 <
环境变量 < 命令行。重复命令行参数按出现顺序取最后一个，不区分新旧拼写。
Compose `.env` 按 [自身示例](docker/.env.example) 的键填写，不自动套用 CLI 的 vars 优先级。

既有 CLI 容器、Kubernetes workload/Service/Job、selector、DNS、namespace/state 路径及卷绑定继续保留。
新增角色标签提供一致的查找入口；不要通过改旧资源名来迁移现有数据。新 Compose 安装使用角色服务名，
遇到另一种部署工具拥有的容器会拒绝接管；更换入口需要单独的备份与迁移方案。

## 选择部署入口

默认并推荐使用 OceanBase CE，面向团队部署及持续积累的观测数据。OceanBase seekdb 是个人、本地开发和轻量使用的选项；选择它不会改变现有部署的数据库引擎或自动迁移数据。

| 入口 | 数据库组合 | 生命周期与边界 |
|---|---|---|
| Docker（默认）：`aiv cast` | 托管 / 外部 OceanBase CE、托管 / 外部 seekdb | 继续使用 CLI state、所有权与配置优先级；源码开发蓝图的 host process 保留 |
| [产品 Compose](docker/README.md) | `compose.yaml`、`compose.seekdb.yaml`、`compose.external-oceanbase.yaml`、`compose.external-seekdb.yaml` | 当前源码以 `aiv ... --target compose` 调用原生 Compose；已有文件使用 `--compose-file`／`--compose-env-file`，不另加宿主 Python 控制层 |
| `aiv cast --target kubernetes` | 仅 OB Operator 托管 OceanBase CE | [固定资源说明](kubernetes/README.md)；不支持外部数据库、seekdb 或原生 v1 history-start 配置 |
| `aiv generate --target kubernetes` | 通用材料转换 | 已弃用为产品安装入口；不具备固定路径的初始化和生命周期能力 |

Helm 是已确定的后续部署方向，当前没有可用的产品 Chart 或 Helm 安装入口。
本次只明确[未来配置、生命周期和资源归属边界](kubernetes/helm-roadmap.md)，
Chart 的实现、发布与运行验收留在后续迭代；现有 Kubernetes 仍使用固定资源入口。

Compose 需要匹配的 Go CLI、Docker daemon 和 Docker Compose 2.27+，运行平台为 Linux/macOS；不需要宿主 Python 或额外控制程序。默认 Docker 保持原 Go 运行时，Kubernetes 仍是独立可选方式。
Use compatible templates and images: the gateway needs `/app/collector healthcheck --ready`,
diagnosis needs organization-file support and `/internal/readyz`, and the backend needs
`/api/v1/ready` plus bootstrap/status support. Docker/Compose also require the data backend's
native `/app/signoz healthcheck --ready`. Matching bounded-shutdown behavior is required.
Native liveness and slim runtimes in main do not establish a release or prove that an existing
`dev` image includes the additional readiness and lifecycle capabilities.
先验证镜像版本与能力；构建说明见 [开发镜像发布](../docs/deployment/zh-CN/dev-image-release.md)。

Use the [Go/native Compose contract](docker/README.md) for exact commands.
Native config --quiet, ps --all and logs use the original project name, absolute
file and env; CLI resolved bundles use /dev/null while parameterized templates
use the original actual private env. Mutating operations use aiv --target compose
and retain identity/ownership, lock, isolated initialization and exit/OOM gates.
Bare up/down is not an established equivalent. Compose purge removes named
volumes but retains bind-mounted metastore/identity/configuration; default Docker
purge keeps its original managed-volume plus state removal. Neither clears an
external database. Do not automatically migrate or mix ownership.

探针、初始化依赖、等待预算、停机与副本资源边界见
[Compose 生命周期说明](docker/lifecycle.md)；
Kubernetes 另见 [资源与生命周期契约](kubernetes/lifecycle.md)。

## 打包与检查

Go 无法直接嵌入模块目录外的文件，因此旧 CLI 路径是自动生成的兼容副本：
`aivision-cli/blueprints/`、`aivision-cli/deploy/kubernetes/`、
`aivision-cli/internal/deploy/assets/`，以及旧的 `docs/deployment/aivision.vars.example.yaml`。
修改权威源后，在 `aivision-cli/` 执行：

```sh
make assets        # 同步嵌入副本，并重建产品 Compose 文件
make assets-check  # 只检查；缺失、过期、多余资产均失败
make test
```

[package_assets.py](scripts/package_assets.py) 仅使用 Python 标准库，
生成稳定的 [来源与 SHA-256 清单](../aivision-cli/deploy/assets-manifest.json)，不修改上游依赖字节。
`make build`、`make test` 和发布 CI 先检查生成材料。旧发布归档成员路径和安装器校验格式继续保留。
旧 seekdb 冒烟入口是转发脚本，实际实现位于 [tests/seekdb_deploy_smoke.py](tests/seekdb_deploy_smoke.py)。

## 验收证据边界

Runtime qualification remains outstanding for rebuilt main-based application images,
the default pinned seekdb digest, and representative sustained/peak load,
queue saturation/backpressure and long-running shutdown under configured limits.
Source support and static checks do not establish released-image compatibility.

[资产 CI](../.github/workflows/deployment-assets.yml) 检查新鲜度、归档安装器、Compose 配置、
Kubernetes 渲染及标准/上游 CRD schema。它不等于实际部署成功，不执行 admission webhook 或完整集群生命周期。
现有 seekdb 冒烟验证 CLI 首装、凭证、OTLP 与鉴权回读、同镜像 recast 持久化；
recast 不能替代不同镜像版本之间的升级验收，external `gauge` 也不能替代完整外部数据库安装。

四种 Compose 组合及真实镜像升级要分别记录实际运行证据。每次准出检查新旧 trace 的鉴权回读、
数据库 canary、数据后端组织身份/元数据、卷绑定、固定历史下界及更新后的镜像 ID；
未运行的组合应标为待验证。Kubernetes 的真实集群结果单独报告，不从 Docker 结果推断。

## 原生查询维护与应急配置

对应源码默认自动迁移派生结构，并由 `backend` 的同一 supervisor 管理 Gunicorn 与投影维护进程。初始化 Job 的显式命令不会启动服务器或维护循环；后台历史投影尚未完成不等于 API 进程失败，仍需核对完整查询结果。

| 部署配置 | 默认 | 实际应用环境变量 |
|---|---|---|
| `BACKEND_QUERY_MODE` | `auto` | `AIVISION_QUERY_MODE` |
| `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED` | `true` | `SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED` |

角色名和旧名是同两个控制的别名，沿用上文配置优先级。CLI 修改原私有 vars 文件；Compose 修改原私有 `.env`，再走对应生命周期入口。维护关闭是持久的共享状态，改回 `true` 并重启不会清除，修复后需明确恢复。不要把内部采集密钥加入普通 backend 环境；仅在获授权的恢复进程中继承它。详见[查询运行与恢复](../docs/deployment/zh-CN/trace-read-model.md)。
