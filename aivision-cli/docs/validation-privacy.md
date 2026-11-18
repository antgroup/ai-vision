# CLI Privacy 验收记录

日期：2026-09-18。对象：本次工作区中的 CLI、安装器、七类生产采集插件、下载 catalog 与发布工作流。使用方法见 [Privacy 文档](privacy.md)。网页没有增加模式配置或展示。

## 真实 Codex 端到端

在 Linux x64 上使用新账号、独立 OceanBase 数据库、隔离 CLI/Codex 目录和更新后的后端镜像验收；复用现有采集网关与 SigNoz。注册、创建查询 Key、接入、模式切换、状态和查询均由 `aiv` 完成，没有通过网页生成命令，没有发送模拟遥测。

运行镜像：`sha256:c65b5e4b81ffac640b62047dc5181aca47d7c3340f6464300b0434a53a27ff52`。常规 Dockerfile 构建在依赖下载阶段取消；验收镜像复用现有依赖层，使用 `uv sync --frozen --dry-run --offline` 核对 46 个依赖无需变更，再打入当前后端、安装器和七类新产物。实际 HTTP 下载的七个产物均与工作区 SHA-256 一致，均声明 Full/Privacy 能力；下载的安装器及安装到客户端的 launcher 也已核对。

Codex 版本 `0.154.0`。每次切换后启动新的 `codex exec` 会话，通过真实 shell 读取文件中的 `19` 和 `23`，计算 `42`，输出该任务的唯一标记。自动验收对隔离目录中已审查的 hook 使用 `--dangerously-bypass-hook-trust`；本轮没有重新验证交互式 `/hooks` 信任流程。输入文件前后摘要一致。

| 新会话模式 | Trace ID | Span 数 | 输入 / 输出 token | 查询中的任务原文标记 |
|---|---|---|---|---|
| Full | `7b73aea4008817660e1cbec77b2d61fc` | 4 | 31120 / 164 | 存在 |
| Privacy | `57ecd537bd500ecfa37e45ff79d696f6` | 4 | 31147 / 154 | 不存在 |
| 切回 Full | `1f8556d300e39a37c15e4505beb01d5d` | 4 | 31107 / 153 | 恢复 |

三条 Trace 的所有 span 均具有对应的 `agentic.collection.mode`，错误数为 0，耗时非负；查询 token 数逐项等于 Codex `turn.completed` 报告。Privacy 标记也不存在于插件管理目录中的任何文件；宿主自己维护的会话文件不在删除或过滤范围内。

本轮端到端发现并修复了旧 Rust 属性编码的兼容问题：部分 token 数和耗时存储为 `stringValue`，最初被当作自由文本过滤。现在仅对符合统计字段语义的有限数值字符串保留原值，并增加 Rust、Node、生产 exporter 和 replay 回归检查。表格只记录修复后的最终镜像结果。

补充实际镜像检查：

- `connect --collection-mode Privacy --dry-run` 后，服务端 Agent/安装授权数量、本机宿主目录中的所有文件摘要均未改变。
- 配置保存为 Full 时，在检查进程设置 `AIVISION_COLLECTION_MODE=privacy`，状态分别显示保存值 Full、进程覆盖 Privacy、运行模式 `unverified`，没有把进程环境当成宿主运行证据。
- 安装、原生 hook 登记、上报凭据检查通过；真实查询为 `found`，仍提示数据可能来自同空间其他机器。模式验收使用上述明确的 Trace ID。

## 自动验证

| 范围 | 已执行结果 |
|---|---|
| CLI | `go test -race -mod=vendor ./...` 通过；覆盖参数归一化/非法值、旧安装器、能力缺失、自动/显式安装、默认 Full、Privacy 保留、切回 Full、失败回滚、授权失效与 dry-run |
| Python 安装器 | 25 项 onboarding 测试、10 项 launcher 恢复测试通过；包括原生配置兼容、自定义目录、Agent 隔离、环境优先级、会话固定、私有文件权限 |
| 后端 | 45 项认证/安装/下载相关测试通过，使用 SQLite；真实接入使用 OceanBase；catalog 兼容旧产物仅声明 Full |
| Rust 共享层 | 167 项单元测试通过；另有 Codex Privacy 与 Claude Code turn-seed 定向回归通过，三个生产二进制和 replay 均编译通过 |
| Node 生产包 | 四个包构建/typecheck 通过；配置定向回归通过 |
| 七类生产 exporter | Full/Privacy 共 14 组 fixture，通过真实 exporter 向本地 HTTP 接收器发送信号，检查原文标记、模式、用量、错误和插件持久化边界 |
| Rust replay | Claude Code、Codex、Kimi 的 Full/Privacy 均通过 weaver-guard；两种模式的结构、状态、用量统计一致；默认 replay Full 不继承 shell Privacy |
| Node replay | Pi 21 项、OpenClaw 18 项、DSH 5 项定向测试及 OpenCode Privacy 回归通过；DSH、OpenCode 的 Privacy replay 另经 weaver-guard 检查，0 violations |
| 分发 | 本机七类产物的格式、平台、SHA-256、模式能力验证通过；镜像 catalog 和真实下载校验通过 |
| 工作流 | 三份发布 YAML 语法检查通过；新增生产 exporter、replay 及 Full/Privacy 能力校验；未触发远端 Actions 或发布 Release |

生产 exporter fixture 使用实际插件/二进制与合成宿主事件，不等于七种宿主的真实任务验收。当前通用 Claude Code 构建没有独立 logs exporter，因此检查 traces/events 与 metrics；其余六类检查 traces、logs/events 和 metrics。Node fixture 也检查 Pi 文件 exporter；Rust fixture 检查异步队列及插件目录，验证 Kimi 旧内容开关不能绕过 Privacy。

复现入口位于 `ai-vision/agent-plugins/`：

```bash
make test-onboarding
make test-privacy-node
make test-privacy-hooks
make test-privacy-replay
# 先构建 weaver-guard，再验证 replay 的语义约定。
make -C weaver build
python3 scripts/privacy_replay_probe.py --validate
python3 scripts/verify_plugin_dist.py --platform linux-gnu/x64 --require-privacy
```

## 平台范围与未完成实机验证

| 平台 | CLI 构建 / 包校验 | CLI 启动 | 本地对应 hook | 真实宿主任务 |
|---|---|---|---|---|
| Linux x64 | 通过 | 通过 | 三个二进制与四个通用 Node 包通过 | Codex 三次模式切换通过 |
| Linux arm64 | 交叉编译、压缩包及 SHA-256 通过 | 未运行 | 待四平台 CI 构建 | 未运行 |
| macOS x64 | 交叉编译、压缩包及 SHA-256 通过 | 未运行 | 待四平台 CI 构建 | 未运行 |
| macOS arm64 | 交叉编译、压缩包及 SHA-256 通过 | 未运行 | 待四平台 CI 构建 | 未运行 |

Claude Code、Kimi、OpenCode、OpenClaw、Pi、DSH 的真实宿主任务未执行；它们通过的是生产 exporter fixture。四平台原生 runner 与产物完整性门禁已配置，本轮没有把 CI 配置或交叉编译计作其他平台的实机证据。

本地四平台 CLI 压缩包和 `SHA256SUMS` 位于 `aivision-cli/dist/privacy-release/`，插件产物位于 `ai-vision/agent-plugins/dist/`，均为 Git 忽略目录。正式交付需要配套发布 CLI、hook 产物及承载它们的后端镜像；未替换用户当前运行的部署。

非敏感 Trace ID、统计及产物摘要保存在 `aivision-cli/dist/e2e/2026-09-18/privacy.json`（Git 忽略目录）。验收账号的查询 Key、会话及本次创建的 SigNoz Key 已撤销，临时后端容器、数据库、镜像、隔离客户端凭据及测试文件已删除。
