# Agent 宿主版本与兼容边界

核对日期：2026-10-05。这里记录插件依赖的宿主接口和升级决策，帮助使用者选择版本、帮助贡献者判断上游变更的影响。版本号相同不代表所有观测能力已完成认证；逐项能力仍以各 Agent 的 `CAPABILITIES.md` 为准。

## 市场与源码接入

| Agent | 本轮核对的官方版本 | 适配与升级决策 |
|---|---|---|
| Claude Code | [2.1.289](https://github.com/anthropics/claude-code/releases/tag/v2.1.289) | 原生 hooks 与 transcript 的已用字段保持兼容，生产采集器保持。 |
| Codex | [0.160.0](https://github.com/openai/codex/releases/tag/rust-v0.160.0) | 修复客户端 `tool_search_call` / `tool_search_output` 分离的协议：按 `call_id` 关联结果与结束时间；保留旧版单记录和服务端搜索解析。`cdxhooks` 升至 0.3.18。 |
| Kimi | Python CLI [1.52.0](https://github.com/MoonshotAI/kimi-cli/releases/tag/1.52.0) 与 Kimi Code [2.1.1](https://github.com/MoonshotAI/kimi-code/releases/tag/%40moonshot-ai/kimi-code%402.1.1) | `kimihooks` 0.4.1 双协议适配配置/会话目录、Wire 与 `Interrupt`，保留旧版行为。2.1.1 原生进程与假模型纳入八个 E2E 场景；旧版保留固定 synthetic wire 契约回归。历史日志修复见 [AG-004](../../KNOWN_ISSUES.md#ag-004kimi-日志的关联与语义字段不完整)，SDK 用量限制见 [AG-005](../../KNOWN_ISSUES.md#ag-005kimi-code-sdk-丢失零用量报告状态和部分-token-明细)，高级能力边界见 [Kimi 说明](kimi/README.md#版本与验证边界2026-10-05)。 |
| OpenCode | [1.18.34](https://github.com/anomalyco/opencode/releases/tag/v1.18.34) | 已有 native E2E 使用此版本；现有 `^1.3.4` SDK 范围覆盖它，保留依赖范围和生产实现。另检查 1.3.4 SDK 类型兼容。 |
| OpenClaw | [2026.9.8](https://github.com/openclaw/openclaw/releases/tag/v2026.9.8) | 生产 LLM/model-call 接口保持兼容。开发者 replay 补齐新增的 5 个 typed hook，并保留旧 hook；不将新增事件等同于新增观测能力。2026.7.1 与 2026.9.8 均保留契约验证。 |
| Pi | [1.0.2](https://www.npmjs.com/package/@earendil-works/pi-coding-agent/v/1.0.2) | 开发依赖从 0.80.6 更新到 1.0.2；保留 0.80.6/0.80.10 的官方 skill 契约样本。0.80.6 与 1.0.2 均验证 production/replay；完整 usage 报告状态需要 1.0.2 的 raw provider-stream hook。 |
| DeepSeek Harness | [0.1.7-rc.2 / 0.2.0-rc.2](https://github.com/deepseek-ai/deepseek-harness/releases) | 保持已选定的两条版本线和 `observ-dsh` 0.2.0。npm `latest`/`next` 仍为 0.2.0-rc.2；新出现的 0.2.1-alpha.1 不自动进入支持范围。宿主 usage 损失见 [AG-002](../../KNOWN_ISSUES.md)。 |
| Hermes（官方 Relay 接入） | [0.22.5](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5) | 当前市场仅提供 Hermes 内置的官方 NeMo Relay 接入，不需要 AI Vision 源码或插件安装命令。已验证 NeMo Relay `0.10.4`，目录要求 `>=0.10,<0.11`；2026-11-02 的真实接入、自动压缩和 API 回读范围及覆盖限制见[官方 Relay 验收记录](hermes/docs/RELAY_INTEGRATION.md)。采集实现与语义验证参考见 [Hermes README](hermes/README.md#compatibility)。 |

## 保留在源码中的其他采集器

| 采集器 | 当前边界 |
|---|---|

## Framework 接入

Langfuse 和 NeMo Relay 是 framework 接入链路，不属于 Agent 应用插件。Langfuse 当前稳定 Python SDK 为 4.16.0，现有 3.15.0/4.16.0 兼容覆盖保持。NeMo Relay 从 0.9.3 新增了 [0.9.4 补丁](https://github.com/NVIDIA/NeMo-Relay/releases/tag/0.9.4)；已核对的变更未改变本项目使用的 framework/OTLP 接口，因此保持现有 0.9.3 基线。未运行 0.9.4 完整 E2E，不据此关闭已有第三方问题。

## 升级原则

- 对已确认的字段、事件或安装方式变化补充兼容分支；旧协议仍有明确来源时保留旧路径，不通过猜测填充模型、usage 或父子关系。
- SDK 范围已覆盖当前宿主且使用的接口没有变化时，保留原范围；开发依赖和 native E2E 的精确版本用于复现，不是对所有中间版本的支持承诺。
- 新增 hook 的 capture、旧新协议回归、真实进程 E2E、平台 API 回读是不同证据。截图只辅助查看，不能代替源数据对账。
- 生产包、replay 包分别升级；不因调整 SDK 测试基线而改动无关生产依赖。验证记录、下载缓存、私人配置和凭证不进入仓库。
