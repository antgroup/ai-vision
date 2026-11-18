# 通过 CLI 配置和验证 Privacy

`Full` 上报现有采集内容；`Privacy` 在本机过滤输入、输出、思考、工具参数/结果和自由文本错误，保留 Trace 结构、关联 ID、状态、耗时及 token/调用次数统计。适用于 Claude Code、Codex、Kimi、OpenCode、OpenClaw、Pi 和 DSH。

## 安装与切换

```bash
aiv connect codex                               # 首次 Full；重复安装保留已有模式
aiv connect codex --collection-mode privacy --dry-run
aiv connect codex --collection-mode privacy
aiv agent status codex

# 明确恢复完整采集。
aiv connect codex --collection-mode full
```

参数支持大小写归一化，非法值报错。自动接入及网页生成的完整 `connect` 命令均支持此参数。网页保留现有安装命令入口，不增加隐私开关或模式展示。

安装值按“显式参数 → 本机已有配置 → Full”确定。Node 插件兼容原生 `collectionMode`；二进制 hook 保存在 `<config-dir>/aivision/<agent>/ingest.json`。`--config-dir` 和原有宿主目录环境变量继续有效；不同 Agent、不同目录各自保存模式，不随账号、空间或凭据切换。安装失败回滚配置，重试不会把 Privacy 重置成 Full。

预检查要求部署的安装器和对应平台产物支持所选模式；缺少能力时直接失败，不降级。`--dry-run` 展示计划采用的模式、来源和目录，不创建 Agent、不签发授权、不保存模式、不修改原生配置。

## 运行时覆盖与会话

模式优先级为 `AIVISION_COLLECTION_MODE` → Agent 专属环境变量 → 本机配置 → Full。

| Agent | 兼容环境变量 |
|---|---|
| Claude Code | `CCHOOKS_COLLECTION_MODE` |
| Codex | `CDXHOOKS_COLLECTION_MODE` |
| Kimi | `KIMI_COLLECTION_MODE` |
| OpenCode | `OBSERV_OPENCODE_COLLECTION_MODE` |
| OpenClaw | `OBSERV_OPENCLAW_COLLECTION_MODE` |
| Pi | `OBSERV_PI_COLLECTION_MODE` |
| DSH | `DSH_OBSERV_COLLECTION_MODE` |

环境覆盖不改写保存值。无论是配置还是环境变化，都应重启宿主并开启新会话；已开始的会话、worker 和收尾逻辑沿用会话模式。二进制 launcher 在私有 `session-modes/` 中保存会话模式，避免重复 hook 或 worker 恢复时改变模式。恢复旧会话仍沿用其原模式。

非法运行时模式会关闭该采集组件的遥测并输出简短英文错误，不回退 Full，不阻止宿主任务。Privacy 优先于旧的内容采集开关；插件调试输出也不得写入原文。宿主自行保存的原始会话文件不属于插件持久化，不由本功能修改；切换模式不会清除历史上报数据或以前由 Full 生成的文件。

## 验证实际模式

`aiv agent status codex` 的 `Configured collection mode` 是本机保存值。`Current process environment override` 只描述本次检查进程；检查进程与宿主环境可以不同。该命令继续验证安装、上报凭据与最近一小时真实数据，JSON 将运行模式标为 `runtime_collection_mode: "unverified"`。

```bash
# 切换后重启宿主，执行一个带唯一标记的真实新任务。
aiv query trace list --since 1h --limit 10
aiv query trace get <trace-id> --format json
```

按新会话/Trace ID 检查 `agentic.collection.mode`、结构和计数，并确认 Privacy 的原文标记不存在于 traces、logs/events、metrics 属性及插件持久化文件中。恢复 Full 后再执行新任务，确认内容恢复。最近 trace 可能来自同空间同类 Agent 的其他机器；仅看到“有数据”或本机配置为 Privacy，不表示当前宿主已经生效。

## 分发与开发验收

需要配套发布 CLI、新安装器和七类插件产物，并更新承载它们的后端镜像。`dist/<agent>/latest.json` 的每个 download 使用 `collection_modes: ["full", "privacy"]` 声明该具体产物能力；下载 catalog 保留该字段，旧产物未声明时只视为支持 Full。仅升级本机 CLI 不能让旧镜像支持 Privacy。

```bash
# ai-vision/agent-plugins；Python 3.11+、Rust、Node 工具链需已安装。
make plugin-packages
make test-onboarding
make test-privacy-node
make test-privacy-hooks
make test-privacy-replay
python3 scripts/verify_plugin_dist.py --platform linux-gnu/x64 --require-privacy
```

发布工作流在 Linux/macOS × x64/arm64 原生 runner 构建 Rust hook，验证其生产 exporter，并检查平台、SHA-256 与模式能力。Node 包为通用产物。fixture、交叉编译、原生启动和真实宿主任务是不同证据，验收记录分别列出。

本轮具体结果与尚未执行的实机检查见 [Privacy 验收记录](validation-privacy.md)。

开发者 replay 与生产采集独立。Rust replay 新增 `--collection-mode full|privacy`，默认 Full，不继承终端的模式环境；Privacy 要求 OTLP 输出。OpenCode/OpenClaw/Pi replay 按历史 envelope 的模式重建，DSH replay 使用自身的模式设置（默认 Full），避免当前 shell 的 Privacy 改写历史 Full 的含义。
