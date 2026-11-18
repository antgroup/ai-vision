# 第三方 Agent 接入

本目录由原 `observ-agents/` 合并而来，随 AI Vision 一起维护。`agents.json` 定义应用采集插件，以及自建 Agent 可使用的 SDK / 运行时接入目录；历史采集器、语义规范和 replay 工具保留在源码中，不因此出现在市场。

个人和团队空间均可在 Agent 市场生成安装命令，复制到 Agent 所在机器执行。网页自动授权，安装器登记原生 hooks、插件或扩展，无需手动创建 SpaceKey。安装完成后运行任务，在数据看板和 Agent 轨迹确认采集结果。

宿主升级前请查看[适配版本与兼容边界](COMPATIBILITY.md)，其中区分当前官方版本、旧版兼容路径和仍需确认的接口。

## 接入分类与扩展契约

Codex、Claude Code 等 Agent 应用推荐对应的 AI Vision 专用采集插件；Hermes 使用其内置的官方 NeMo Relay。可修改业务代码的自建 Agent 则选择兼容的观测平台 SDK 或合作伙伴运行时，再添加所用框架的采集代码。框架是业务实现信息，SDK / 运行时是采集方式，AI Vision 项目是数据归属；三者分别展示。

自建 Agent 的目录条目通过 `integration` 描述接入方式。`configure_third_party_agents` 将其原样写入 `config_guide.integration`；前端据此生成市场分组和项目身份，保留旧 Langfuse / Relay 目录的兼容回退。

| 字段 | 含义 |
|---|---|
| `kind` | `observability_sdk`（如 Langfuse SDK）或 `partner_runtime`（如 NeMo Relay） |
| `onboardingPath` | 已实现的站内 `/access/…` 接入路由 |
| `docsUrl` | 对应 SDK / 运行时的官方文档 |
| `verifiedVersions` | 有验证证据的 SDK 版本；不代表其所有框架、字段或平台 API 均已兼容 |

业务项目沿用 `agent_product` 标识采集方式，以 `metadata.framework` 保存框架 ID，以 `name` / `custom_name` 展示业务名称。现有项目不迁移、不改写历史数据；选择框架或读到旧 Trace 不会更新项目元数据或标记本次接入成功。验证使用当前空间、项目和指定 Trace ID 的 Span 读回，读回后仍需核对模型、工具和父子关系。

后续 LangSmith SDK 接入应复用这个目录契约。当前**尚未实现 LangSmith SDK 兼容**，因此不添加可点击入口。新增采集方式还需实现并验证认证与项目映射、实际传输协议、Span 语义转换、Trace 查询、SDK 生命周期及完整示例，再登记目录和路由。支持一个 SDK 的上报路径，不等于兼容该观测平台的全部管理、评测或查询 API。

用户教程维护于独立文档站的[自建 Agent 接入](https://aivision.mintlify.site/zh-CN/integrations/frameworks)。目录或示例变更后，更新部署中的接入目录；仅更新前端不会自动刷新数据库保存的 guide。

## 七类安装方式

| 市场 Agent / 安装脚本 key | 原生登记位置 | 本部署生成的生产制品 |
|---|---|---|
| Claude Code / `claude-code` | `CLAUDE_CONFIG_DIR`（默认 `~/.claude`）的 `settings.json` hooks | `cchooks` |
| Codex / `codex` | `CODEX_HOME`（默认 `~/.codex`）的 `config.toml` 和 `hooks.json` | `cdxhooks` |
| Kimi / `kimi` | CLI 1.x：`KIMI_SHARE_DIR`（默认 `~/.kimi`）；Code 2.x：`KIMI_CODE_HOME`（默认 `~/.kimi-code`）；均登记 `config.toml` hooks | `kimihooks` |
| OpenCode / `opencode` | `OPENCODE_CONFIG` 或配置目录中的 `opencode.json` 的 plugin 列表 | `observ-opencode` Node.js 包 |
| OpenClaw / `openclaw` | `OPENCLAW_CONFIG_PATH` 或状态目录中的 `openclaw.json` 的插件路径与配置 | `observ-openclaw` Node.js 包 |
| Pi / `pi` | 使用 `pi install` 登记本地扩展包，配置位于 `PI_CODING_AGENT_DIR`（默认 `~/.pi/agent`） | `observ-pi` Node.js 包 |
| DeepSeek Harness / `dsh` | 使用 `dsh plugin --profile … add` 登记，并配置 `tui,web` profiles | `observ-dsh` Node.js 包 |

安装不需要独立的 `aivision` 命令或新的 Go 客户端。Python 脚本只负责检查、下载、校验和修改配置。生产包不包含独立 replay 插件或 replay 可执行文件。

Hermes 仅提供[官方 Relay 接入说明](https://aivision.mintlify.site/zh-CN/integrations/hermes)，不需要 AI Vision 源码或上述 CLI 插件安装流程。

## 部署者准备

先按 [CLI 源码部署说明](../../aivision-cli/README.md#source-build-pointing-at-a-different-repo-or-branch) 启动 AI Vision、SigNoz 和采集网关。构建机需要 Python 3.11+、Rust stable/Cargo、C/C++ 构建工具、Node.js 和 npm。

在本目录执行：

```bash
make plugin-packages PLUGIN_PYTHON=../aivision-py-be/.venv/bin/python
```

首次构建 Node 插件需先安装各包依赖，也可通过以下参数完成：

```bash
make plugin-packages PLUGIN_PYTHON=../aivision-py-be/.venv/bin/python INSTALL_DEPS=1
```

产物在 `dist/<key>/<version>/`，每类 `latest.json` 包含版本、平台、架构和 SHA-256。默认只构建当前机器对应的 hook 二进制；Node 插件生成通用包。其他平台需在对应构建环境中构建，或准备 Rust target 与交叉链接器后传入 `TARGET=<triple>`，合并对应产物与清单。不能把 Linux 二进制当作 macOS/Windows 包。

后端配置：

| 配置 | 用途 |
|---|---|
| `AIVISION_AGENT_PLUGINS_DIR` | 本目录的绝对路径，默认从后端源码位置推导 |
| `AIVISION_PLUGIN_ARTIFACTS_DIR` | 产物根目录，默认本目录的 `dist/` |
| `AIVISION_PUBLIC_BASE_URL` | 客户端可访问的 AI Vision 地址，包含协议；不包含 `/api` |
| `AIVISION_OTLP_INGEST_ENDPOINT` | 客户端可访问的采集网关 HTTP 地址，可带 `/v1/traces` |

在已有后端运行环境中，使用已有数据库与加密配置执行；查询 key 从 signoz DataSource config 读取（部署时由 bootstrap 写入），也可经 `SIGNOZ_API_KEY` 环境变量临时提供：

```bash
cd ../aivision-py-be
.venv/bin/python manage.py configure_third_party_agents --endpoint http://signoz-query:8080
```

这个命令幂等配置目录中的应用插件、自建 Agent 采集方式及各自 metrics、traces、events 数据源，按目录设置显示范围，保留已有 Agent id 和历史数据。不重建数据库。升级已有部署时先运行 `manage.py migrate`（包括 `0022_agent_installation_authorization`），再执行此命令更新市场指引和显示范围。SigNoz 查询地址以实际部署为准，不能把示例地址直接用于客户端。

安装脚本通过 `GET /api/public/onboarding/catalog.json` 获取本部署已经构建的可下载文件；同一公开路径下提供脚本与插件下载，响应不携带 SpaceKey 或采集令牌。原下载页继续使用 `/api/v1/download/latest`，沿用现有登录要求。无需额外安装包分发服务，也不依赖上游 CDN。

客户端访问的平台地址必须同时转发 `/api` 与 `/openapi` 到 Django。Vite 开发代理的两个前缀均使用 `VITE_DEV_API_TARGET`。部署在另一台机器时，平台地址和 OTLP 网关地址都必须能从 Agent 所在机器访问。

## 客户端使用

1. 安装并配置好第三方 Agent，确认其本身能调用模型、执行工具。安装脚本需要 Python 3.11+；四类 Node 插件还需要 Node.js。
2. 进入个人或团队空间的 Agent 市场，打开接入说明，点击“生成安装命令”（需管理员或开发者权限）。选择操作系统，复制命令到 Agent 所在机器执行。
3. 安装器自动保存上报凭据并确认安装，关闭弹窗后刷新 Agent 状态。命令 15 分钟内有效；过期或安装到另一台机器时重新生成。已安装插件自动续期。
4. 重启 Agent。Codex 还需进入 `/hooks`，检查并信任 AI Vision hooks；未信任的 hook 不会执行，参见 [Codex 官方说明](https://learn.chatgpt.com/docs/hooks)。
5. 执行一次包含模型调用、工具调用的真实任务，在当前空间的数据看板和 Agent 轨迹确认结果。“已接入”仅表示完成安装登记。

使用自定义目录时追加 `--config-dir "目录路径"`，并确保 Agent 启动时也使用该目录。DSH 可通过 `--profiles tui` 只接入指定 profile。`--dry-run` 检查目标与可用产物，不写配置或交换凭据。

默认 `python3` 版本过低时，安装器会查找 PATH 和 uv 中已安装的 Python 3.11+，也可通过 `AIVISION_PYTHON` 指定。若未安装兼容版本，先执行 `uv python install 3.12` 或安装 Python 后重试。命令中的 Agent ID 由网页生成，不能保留 `{agentUuid}` 占位符。

原生配置修改会保留其他插件；无法解析的配置会停止安装，不覆盖原文件。成功修改前的文件保存在同目录 `*.aivision-backup`；安装失败会恢复被修改的配置并清理本次新增版本目录。重复安装替换本插件的登记，不追加重复 hook。

安装器用一次性授权兑换仅供当前 Agent 上报的长期凭据，保存到权限为 `600` 的 `aivision/<key>/ingest.json`；服务端只存哈希。安装失败时保留 `pending-installation.json` 供原命令重试，成功后删除。插件通过 `/openapi/v1/ingest-tokens` 自动续期；移除 Agent 会撤销其安装凭据，已签发的短期令牌在到期前仍有效。历史 `--space-key`、`--space-key-file` 和 `AIVISION_SPACE_KEY` 方式继续兼容。

Codex、Claude Code 的 Metrics 上报复用安装器提供的凭证；后台 worker 在处理回调时读取最新的 `ingest-token.json`。非托管配置使用各自的 `CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS`、`CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS`，运行时同名 header 优先于内置配置。`make test-metrics-auth` 检查这条读取链路；CI 的 `make test-privacy-hooks` 使用带鉴权的接收端验证生产制品，并检查凭证续期后的 Metrics 请求。

Installer requests and hook credential refreshes connect directly to explicit loopback targets (`localhost`, IPv4 loopback addresses, and `::1`), preserving proxy settings for remote deployments and rejecting redirects. Managed binary hooks add their loopback ingest host to the child process's `NO_PROXY` and `no_proxy` lists without changing the parent environment. Credential network failures retain fail-open behavior and print a brief diagnostic without exposing proxy URLs or credentials.

The release-only `privacy_hook_probe.py --mode` fixture fixes both collection-mode environment variables for the selected Agent in its child environment. Inherited Full, Privacy, or invalid overrides do not change the mode under test. Production runtime override precedence remains unchanged; fixture success does not establish real host/model acceptance.

## 验证范围

2026-09-14 在 Linux GNU x64、Node.js 24.14.0、Python 3.12.11 上验证：

| Agent | 宿主版本 | 插件版本 | 原生安装/加载 | 真实任务到 AI Vision |
|---|---|---|---|---|
| Claude Code | 2.1.270（官方） | 0.7.22 | 已通过 | 已通过：模型与工具调用、4 个 spans |
| Codex | 0.153.4 | 0.3.17 | 已通过：服务器默认 `~/.codex` 安装，5 个 hooks 持久信任；重复安装无重复 hooks | 已通过：两个新进程的真实任务共 9 个 spans，平台 token 用量与 Codex 一致；缓存过期自动续期，浏览器可查看模型与工具调用 |
| Kimi CLI | 1.50.0 | 0.3.0 | 已通过，宿主解析 Stop/StopFailure hooks | 待可用模型环境 |
| OpenCode | 1.18.30 | 0.3.4 | 已通过，宿主解析包含生产插件的配置 | 待可用模型环境 |
| OpenClaw | 2026.3.13 | 0.5.3 | 已通过，原生插件状态 loaded | 本机模型请求超时，待完整任务验收 |
| Pi | 0.80.6 | 0.2.1 | 已通过，原生包管理器登记并列出扩展 | 待可用模型环境 |
| DeepSeek Harness | 0.1.5-rc.1 | 0.1.1 | 已通过，tui/web 配置均包含生产插件 | 待可用模型环境 |

已运行后端下载、初始化、采集认证和数据查询检查、安装配置与回滚检查、Node 插件配置/加载检查、运行中凭据更新检查，以及前端类型检查和构建。OpenClaw 的本地 CLI 首次事件初始化、任务结束刷新已通过三类信号和认证检查，原有 99 项回归检查通过。接入页、Codex 看板和轨迹页已通过浏览器验证。原有团队洞察接口在本地部署仍返回 502，不影响这条采集与查询链路。

本机验收记录位于部署状态目录，不保存真实凭据到仓库。Codex 默认目录的验收使用正常启动和已持久化的 hooks 信任，未使用临时 `CODEX_HOME` 或跳过 hooks 信任的参数；链路为 Codex → 采集网关 → OceanBase → SigNoz → AI Vision。网关三类 OTLP 入口均检查了无凭据返回 401、有效凭据返回 200；真实任务的输入、输出、模型与工具 spans 和 token 用量另行逐项核对。

构建成功、原生登记成功、真实任务上报是不同验收项。当前 hook 制品只包含 Linux GNU x64；macOS、Windows、ARM64 尚未构建并验收。Node 插件虽为通用包，也不能据此声明已完成其他操作系统的宿主验收。安装脚本会检查当前系统是否有对应制品。

统一升级/卸载命令、远程启动 Agent、平台托管 Agent 的接入改造不在本轮范围。各插件原有开发入口、语义约定和 replay 工具见对应子目录及 [AGENTS.md](AGENTS.md)。
