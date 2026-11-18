# AI Agent Hooks - Weaver Semantic Conventions

本目录提供 AiVision 本地 OTLP 语义验证器相关内容：语义约定、Rego 规则、回放验证、HTTP 接收服务和本地观测后端转发。

## 目录结构

```text
weaver/
├── policies/                   # Rego 策略根目录
│   ├── README.md               # 策略布局说明
│   └── aivision/               # 内置 AiVision 规则组
│       ├── README.md
│       ├── traces/             # trace/span 规则；含 signal-local base/
│       ├── events/             # OTLP logs 映射出的 semantic events 规则
│       └── metrics/            # metric / data point 规则
├── weaver-guard/               # 本地验证器 CLI / HTTP 服务
├── scripts/                    # 辅助脚本
├── live/                       # serve 运行时 diagnostics / replay 输出
└── README.md
```

旧的顶层 `base/`、`span/`、`event/`、`metric/`、`traces/`、`events/`、`metrics/` 规则目录已迁移到 `policies/aivision/**`。内置规则不再使用全局 base 层；每个信号在自己的 `base/` 中维护 helper 和兼容性规则。

## 快速开始

### 构建

```bash
make -C weaver build
```

构建 `weaver-guard` 裸二进制 release：

```bash
make -C weaver release-wg
# 兼容写法：
make -C weaver release wg
```

默认输出到：

```text
releases/weaver-guard/{version}/
├── weaver-guard-{version}-darwin-arm64
├── weaver-guard-{version}-darwin-x64
├── weaver-guard-{version}-linux-musl-arm64
├── weaver-guard-{version}-linux-musl-x64
├── weaver-guard-{version}-linux-gnu-arm64
├── weaver-guard-{version}-linux-gnu-x64
└── runtime-assets.tar.gz
```

### 安装 Weaver Guard

公开命令和发布产物名为 `weaver-guard`。当前一键安装脚本只支持 macOS arm64，并且只从 OSS 下载主可执行文件：

```bash
export WEAVER_GUARD_OSS_PREFIX="https://assets.example.com/weaver-guard/"
sh scripts/install-weaver-guard.sh
```

默认安装到：

```text
${AIVISION_HOME:-$HOME/.aivision}/bin/weaver-guard
```

使用前将 `WEAVER_GUARD_OSS_PREFIX` 配置为自己已发布制品的地址。也可指定本地运行资源包 `WEAVER_GUARD_RUNTIME_ASSET_FILE`。例如：

```bash
WEAVER_GUARD_OSS_PREFIX=https://example.invalid/aivision/signed/weaver-guard/ \
  sh weaver/scripts/install-weaver-guard.sh
```

安装流程：

1. 读取 `${OSS_PREFIX}/latest.txt`。
2. 下载 `${OSS_PREFIX}/{version}/weaver-guard`。
3. 写入临时文件、`chmod 0755`，再原子替换到安装路径。

### Standalone 发布布局

发布包由 `make -C weaver package-macos-arm64` 生成，默认输出到 `weaver/dist/weaver-guard/macos-arm64/`：

```text
dist/weaver-guard/macos-arm64/
├── install-weaver-guard.sh
├── latest.txt              # 当前发布版本
└── {version}/
    ├── weaver-guard            # macOS arm64 主可执行文件
    └── runtime-assets.tar.gz   # 策略、semconv、后端 bootstrap 脚本和示例配置
```

OSS 下载约定为：

```text
{OSS_PREFIX}/latest.txt
{OSS_PREFIX}/install-weaver-guard.sh
{OSS_PREFIX}/{version}/weaver-guard
{OSS_PREFIX}/{version}/runtime-assets.tar.gz
{OSS_PREFIX}/backend-assets.json
{OSS_PREFIX}/backends/{platform}-{arch}/{backend}/{backend_version}/{archive}.tar.gz
```

`{version}/runtime-assets.tar.gz` 解压后包含：

```text
runtime-manifest.json
policies/
semconv/
scripts/
config.example.yaml
```

一键 installer 不安装 `runtime-assets.tar.gz`；首次 `weaver-guard serve` 会按当前二进制版本从 OSS 下载该包并安装到 `${AIVISION_HOME:-$HOME/.aivision}/guard/`。如需离线或本地测试，可通过 `WEAVER_GUARD_RUNTIME_ASSET_FILE=/path/to/runtime-assets.tar.gz` 指定本地 runtime 包。

后端二进制目录由 `backend-assets.json` 维护。当前三个 backend 共同可用且已同步的主流目标为 `macos-amd64`、`macos-arm64`、`linux-amd64`、`linux-arm64`；`s390x` 不纳入默认同步集。

macOS arm64 当前引用：

```text
backends/macos-arm64/jaeger/v1.76.0/jaeger-all-in-one-1.76.0-darwin-arm64.tar.gz
backends/macos-arm64/vmetrics/v1.147.0/victoria-metrics-darwin-arm64-v1.147.0.tar.gz
backends/macos-arm64/vlogs/v1.51.0/victoria-logs-darwin-arm64-v1.51.0.tar.gz
```

`runtime-manifest.json` 会嵌入这些 URL；首次 `serve` 下载 runtime 包后，后端脚本统一从同一个 OSS 前缀下载 Jaeger / VictoriaMetrics / VictoriaLogs。下载缓存默认在 `${AIVISION_HOME:-$HOME/.aivision}/guard/downloads/{jaeger,victoria}/`，解压后的 backend home 默认在 `${AIVISION_HOME:-$HOME/.aivision}/guard/backends/{jaeger,victoria}/`。

构建和发布：

```bash
make -C weaver backend-assets-macos-arm64
make -C weaver package-macos-arm64
make -C weaver publish-macos-arm64
```

`publish-macos-arm64` 会使用 `../../aivision-setup/crates/aivision-oss`，并自动 source `../../aivision-setup/.env` 中的 OSS 配置。

同步最新版 backend 包并上传到 OSS：

```bash
make -C weaver backend-sync
make -C weaver backend-sync-upload
```

`backend-sync` 会查询 GitHub release，选择 Jaeger 最新 v1.x `jaeger-all-in-one`、VictoriaMetrics 最新 community release、VictoriaLogs 最新 community release；Jaeger 会重新打成只含 `jaeger-all-in-one` 的小 tar.gz。

### 默认资源路径

`serve` 的 `--policies-dir`、`--semconv-dir`、后端脚本、后端 home、诊断日志和 replay 目录都支持显式传参；显式传参优先于默认路径。

debug 构建用于本仓库开发。通过 `cargo run -- serve` 启动时，如果当前目录或上级目录能定位到 `weaver/`，默认加载当前项目目录：

```text
weaver/policies/
../docs/semconv/
weaver/scripts/
weaver/.victoria/
weaver/.jaeger/
weaver/.downloads/
weaver/live/
```

release 构建用于单个二进制分发。直接启动 `weaver-guard serve` 时，默认从 managed runtime 目录加载和写入：

```text
${AIVISION_HOME:-$HOME/.aivision}/guard/
├── policies/
├── semconv/
├── scripts/
├── backends/
├── downloads/
└── live/
```

### 首次运行行为

安装后可直接查看 CLI：

```bash
"$HOME/.aivision/bin/weaver-guard" --help
```

首次启动本地 OTLP 服务不需要手动传入规则、semconv 或后端脚本路径：

```bash
weaver-guard serve
```

release 二进制首次 `serve` 会创建以下目录并校验 runtime 包 manifest：

```text
${AIVISION_HOME:-$HOME/.aivision}/guard/
├── policies/
├── semconv/
├── scripts/
├── backends/
├── downloads/
└── live/
```

后端 Victoria / Jaeger 仍由 runtime 包内脚本按需准备。诊断日志默认写到 `guard/live/diagnostics.jsonl`，replay 默认写到 `guard/live/replays/`。如果只运行 `validate`，仍需显式传入已安装的规则和 semconv 目录：

```bash
weaver-guard validate path/to/replays \
  --policies-dir "$HOME/.aivision/guard/policies" \
  --semconv-dir "$HOME/.aivision/guard/semconv"
```

### First-cut 非目标

- 不支持 Linux、Windows 或 macOS Intel 安装包。
- Installer 不安装 runtime assets，不安装 Victoria / Jaeger 后端，也不修改 shell profile 或 `PATH`；runtime assets 由 `serve` 首次运行时准备。
- 不提供 Homebrew、npm、pkg 或系统级服务集成。
- 不做配置迁移或远端策略热更新。

### 启动本地 AiVision OTLP 服务

```bash
make -C weaver serve
```

默认监听：

- OTLP HTTP traces: `http://127.0.0.1:4319/v1/traces`
- OTLP HTTP logs/events: `http://127.0.0.1:4319/v1/logs`
- OTLP HTTP metrics: `http://127.0.0.1:4319/v1/metrics`
- Dashboard: `http://127.0.0.1:4319/`

重启：

```bash
make -C weaver serve-restart
# 或
make -C weaver restart
```

停止：

```bash
make -C weaver serve-stop
```

### 验证 replay 文件

```bash
make -C weaver validate AGENT=opencode FORMAT=json
make -C weaver validate-all
```

### 回放到本地 HTTP 服务

从仓库根目录执行：

```bash
make report-check opencode
make report-check-all
```

这些命令会把 replay JSONL 按文件名推送到 `http://127.0.0.1:4319/v1/{traces,logs,metrics}`，用于 Dashboard 实时诊断。

## 规则布局

当前内置规则组是 `policies/aivision`：

```text
policies/aivision/
  traces/
    base/
    resource_attributes/
    span_attributes/
    turn/
    llm/
    tool/
    compaction/
  events/
    base/
    resource_attributes/
    common_attributes/
    session_start/
    session_end/
    user_prompt/
    api_request/
    tool_result/
    compaction_result/
  metrics/
    base/
    common/
    llm/
    tool/
```

规则文件使用扁平命名：

- `violations_*.rego`：强制违规
- `improvements_*.rego`：推荐补齐
- `information_*.rego`：诊断信息

详见 `weaver/policies/README.md` 和 `weaver/policies/aivision/README.md`。
