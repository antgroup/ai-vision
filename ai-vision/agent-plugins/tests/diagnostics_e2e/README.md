---
title: 复现原生 Agent 诊断 E2E
type: how-to
audience: [A3, A4]
runs: yes
verified_on: 2026-10-12
sources:
  - ai-vision/agent-plugins/tests/diagnostics_e2e/run_suite.py
  - ai-vision/agent-plugins/tests/diagnostics_e2e/source.py
  - ai-vision/agent-plugins/tests/diagnostics_e2e/judge.py
  - ai-vision/agent-plugins/tests/diagnostics_e2e/docker_platform.py
  - ai-vision/agent-plugins/tests/diagnostics_e2e/docker_judge.py
  - ai-vision/agent-plugins/tests/diagnostics_e2e/regressions.py
  - ai-vision/agent-plugins/tests/diagnostics_e2e/fixtures/Codex.Dockerfile
  - ai-vision/agent-plugins/tests/diagnostics_e2e/fixtures/PlatformCLI.Dockerfile
  - ai-vision/agent-plugins/tests/native_agent_e2e/drivers/codex.py
  - ai-vision/agent-plugins/tests/native_agent_e2e/install_runtimes.py
  - ai-vision/agent-plugins/tests/native_agent_e2e/nvidia_provider.py
  - ai-vision/vfe/vite.config.ts
---

# 复现原生 Agent 诊断 E2E

本指南用于复现 Codex 与 DSH 的原生采集和平台诊断。默认使用 LangChain
`GenericFakeChatModel` 驱动本地 HTTP provider；官方 Agent 执行真实文件与 shell 工具，
生产采集器导出原始 OTLP，经 Antcollector、SigNoz / OceanBase CE 进入 AgentLens。
测试通过 AI Vision 公共 API 创建数据集、诊断任务和自定义 LLM Judge，并读回诊断记录及统计。
Fake provider 只根据收到的工具结果推进，不读取工作区来制造结果，也不把预期诊断标签作为 Agent 回答。
来源校验把实际 provider 发出的工具身份、名称和参数与原始 OTLP 绑定，并保持 JSON 类型和正文空白。
Codex 只接受既有 Shell command 归一化和 custom apply_patch 的 input 解包；其它工具保留完整参数。
隐私场景要求规范脱敏标记，不把缺失参数当作匹配成功。

以下命令用于准备运行环境和复现验收。每次运行将原生来源、平台诊断与失败记录保存到独立目录，
最终 `summary.json` 和扩展回归证据共同决定是否通过。场景清单描述预期覆盖范围。

## 准备运行环境

从仓库根目录开始，使用 Python 3.12、Node.js / npm，以及本机 Docker daemon。
运行目录放在 checkout 外的普通用户目录，避免系统临时路径被代码变更规则排除。

```bash
umask 077
AIV_REPO="$(pwd)"
AIV_DIAGNOSTICS_ROOT="$HOME/.aivision/diagnostics-e2e"
mkdir -p "$AIV_DIAGNOSTICS_ROOT"
python3 -m venv "$AIV_DIAGNOSTICS_ROOT/python"
AIV_PYTHON="$AIV_DIAGNOSTICS_ROOT/python/bin/python"
"$AIV_PYTHON" -m pip install -r "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/requirements.txt"
```

[requirements.txt](requirements.txt) 复用原生测试的精确依赖版本，不安装 AgentLens 或平台服务依赖。
协议单元测试可独立运行，不需要真实平台或模型凭证：

```bash
"$AIV_PYTHON" -m unittest discover -s "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e" -p 'test_*.py' -v
```

可选的生产 Rule 材料预验需要 AgentLens 自身依赖；基础环境会明确跳过该项。
若需要这项检查，按 [AgentLens 开发说明](../../../../agentlens/README.md) 准备其 `.venv`，
再向该环境安装本目录 requirements 并执行上述测试。该预验也不替代平台验收。

## 准备原生 Agent

本指南的 Codex 路径在 Linux Docker 内运行；DSH 路径在 macOS 或 Linux 本机 Node.js 内运行。
它们的 OS 事实分别写入报告，不由其中一项推断其他 OS 的兼容性。

[Codex.Dockerfile](fixtures/Codex.Dockerfile) 安装官方 `@openai/codex@0.161.0`，
并在 Linux 构建当前 checkout 的生产 `cdxhooks`，启用 `common,tool_context`。
导出的 Linux hook 供原生容器使用；不要在 macOS 执行它。

```bash
AIV_CODEX_IMAGE="aivision-diagnostics-codex:local"
docker build --network=host -f "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/fixtures/Codex.Dockerfile" -t "$AIV_CODEX_IMAGE" "$AIV_REPO"
AIV_CODEX_EXPORT_ID="$(docker create "$AIV_CODEX_IMAGE")"
docker cp "$AIV_CODEX_EXPORT_ID:/usr/local/bin/cdxhooks" "$AIV_DIAGNOSTICS_ROOT/cdxhooks"
docker rm "$AIV_CODEX_EXPORT_ID"
```

[Codex driver](../native_agent_e2e/drivers/codex.py) 要求本机 Unix Docker socket。
容器用 host network 访问 loopback provider 和 capture，源码只读挂载，案例目录可写挂载。
macOS Docker 环境需支持该 host network 路径。

DSH 使用 [精确版本安装器](../native_agent_e2e/install_runtimes.py)。
`dsh02` 对应 `0.2.0-rc.2`；安装器固定完整官方依赖树，并输出实际 Node 与 CLI 路径。

```bash
AIV_RUNTIME_ROOT="$AIV_DIAGNOSTICS_ROOT/runtimes"
"$AIV_PYTHON" "$AIV_REPO/ai-vision/agent-plugins/tests/native_agent_e2e/install_runtimes.py" --repo "$AIV_REPO" --out "$AIV_RUNTIME_ROOT" --apps dsh02
AIV_DSH_NODE="$("$AIV_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["dsh02"][0])' "$AIV_RUNTIME_ROOT/runtimes.json")"
AIV_DSH02="$("$AIV_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["dsh02"][1])' "$AIV_RUNTIME_ROOT/runtimes.json")"
```

如需 `dsh01`，安装器选 `--apps dsh01`，再给诊断 runner 提供 `--apps dsh01 --dsh01` 及对应实际路径。
DSH 加载当前 checkout 的生产 Cordis 采集插件；测试不直接调用插件回调来构造遥测。

## 先做原生采集预检

每次使用新的 `--out` 目录；runner 拒绝覆盖已有案例。下面只执行代码修复正反例：

```bash
"$AIV_PYTHON" "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/run_suite.py" \
  --repo "$AIV_REPO" --out "$AIV_DIAGNOSTICS_ROOT/capture-code" \
  --artifacts "$AIV_DIAGNOSTICS_ROOT" \
  --apps codex dsh02 --scenarios code-change-tested code-change-untested \
  --codex-image "$AIV_CODEX_IMAGE" --hook-binary "$AIV_DIAGNOSTICS_ROOT/cdxhooks" \
  --dsh-node "$AIV_DSH_NODE" --dsh02 "$AIV_DSH02" --capture-only
```

上面的 `--capture-only` 运行只证明原生执行、工具结果、原始 OTLP 和工作区结果之间的关系。
报告的 `stage` 为 `native_capture`；`diagnosis_verified` 不会因此变成平台通过。
完整平台验收需要私密 `--targets`；下一节的独立平台入口会在运行期间创建它。
`--artifacts` 指定原生容器可访问的挂载根目录，必须包含导出的 hook 和本次输出目录。

## 接入本地 OceanBase 平台

[独立平台 CLI](docker_platform.py) 复用现有部署与清理入口，创建本次运行独占的
OceanBase CE、SigNoz、Antcollector、后端、AgentLens 和前端容器。
它将当前后端和 AgentLens 源码只读挂载到服务，并使用当前前端产物 `ai-vision/dist`。
前端还挂载当前 Nginx 模板；对于缺少新镜像默认值的旧基线镜像，夹具补齐
`AIVISION_TRUST_PROXY_HEADERS=false`，并保留部署已显式设置的代理头信任选项。
Go 编译器、构建时执行程序及部署 CLI 全部在 Linux Docker 内运行。
此入口只接受本机 Unix Docker socket，支持 Linux / macOS，拒绝 Windows 和 GitHub Actions。

前端从 `ai-vision/vfe` 构建，但 Vite 输出目录是 `ai-vision/dist`：

```bash
pnpm --dir "$AIV_REPO/ai-vision/vfe" install --frozen-lockfile
pnpm --dir "$AIV_REPO/ai-vision/vfe" build
```

构建部署 CLI 的依赖镜像；[PlatformCLI.Dockerfile](fixtures/PlatformCLI.Dockerfile)
将 Docker 客户端加入精确 Go `1.25.7` 环境：

```bash
AIV_CLI_IMAGE="aivision-diagnostics-docker-cli:local"
docker build -f "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/fixtures/PlatformCLI.Dockerfile" \
  -t "$AIV_CLI_IMAGE" "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/fixtures"
```

在 checkout 外保存 `platform-images.json`，填入本次要运行的六个官方镜像 digest。
以下 SHA 均为占位符，必须替换为完整 64 位十六进制摘要。
镜像须适配本机 Docker daemon 的架构；入口保留 ARM64 缓存，不强制拉取 AMD64。

```json
{
  "oceanbase": "oceanbase/oceanbase-ce@sha256:REPLACE_64_HEX",
  "signoz": "ghcr.io/ob-labs/signoz-ob@sha256:REPLACE_64_HEX",
  "gateway": "ghcr.io/ob-labs/antcollector-gw@sha256:REPLACE_64_HEX",
  "backend": "ghcr.io/ob-labs/aivision-be@sha256:REPLACE_64_HEX",
  "agentlens": "ghcr.io/ob-labs/agentlens@sha256:REPLACE_64_HEX",
  "frontend": "ghcr.io/ob-labs/aivision-fe@sha256:REPLACE_64_HEX"
}
```

```bash
export AIVISION_E2E_PLATFORM_IMAGES_JSON="$(cat "$AIV_DIAGNOSTICS_ROOT/platform-images.json")"
"$AIV_PYTHON" "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/docker_platform.py" \
  --repo "$AIV_REPO" --out "$AIV_DIAGNOSTICS_ROOT/platform-owned" \
  --extended --official-signoz --cli-image "$AIV_CLI_IMAGE" \
  --codex-image "$AIV_CODEX_IMAGE" --hook-binary "$AIV_DIAGNOSTICS_ROOT/cdxhooks" \
  --dsh-node "$AIV_DSH_NODE" --dsh02 "$AIV_DSH02"
```

`--out` 必须是 checkout 外尚不存在的目录。入口通过公共 API 注册账号、创建 `type=2`
团队 Space、分别创建 Codex / DeepSeekHarness Agent，并校验 Space 读回及 ingest token 的范围。
入口还创建本次独立的隔离团队 Space，读回确认身份后，将其 `isolation_space_id` 写入两个 targets。
临时 targets 文件权限为 `0600`，只传给本次诊断 callback，结束或失败时删除。
导出的 hook 复制到本次输出目录供原生容器挂载。
上述 `--extended` 命令运行 `fake` 模式下的 Codex / DSH 0.2 × 14 个场景，然后接续质量矩阵
与持续修复检查，共 34 项。省略 `--extended` 保留原有 20 个原生场景验收。
数量、完整案例 ID、原生来源及实际诊断必须全部满足所选验收范围；不会把只采集的结果记为平台成功。
每个案例的 `diagnosis.json` 必须明确记录 `cross_space_denied=true`，证明用隔离 Space 查询原任务
被公共 API 拒绝。缺少隔离目标、目标与原 Space 相同、缺少报告或隔离字段都会令验收失败。
扩展入口先校验全部 28 个原生案例，再将当前已轮换的登录会话以新的 `0600` 文件传给回归 runner。
回归读取这份当前 targets；两份临时 targets 在成功和异常退出时均删除。缺少当前会话导出会直接失败。

`--official-signoz` 是默认选择。入口先检查 pinned digest 的本地缓存，用对应 image ID
和 classic Docker builder（`DOCKER_BUILDKIT=0`）离线生成带本次 ownership label 的 alias；
缺缓存时才显式拉取该 digest。Docker daemon 需支持 classic builder。
此路径返回 `precision_fix_applied=false`，验证诊断语义，不宣称原始纳秒时间戳严格一致。
如需原有精度候选镜像构建，用 `--signoz-source-archive /absolute/path/source.tar.gz` 替换该选项；
归档仍须满足 [原有构建器](../framework_e2e/signoz_precision.py) 的固定 SHA256 和安全解包检查，
源码测试与编译使用 daemon 的原生架构。两种 SigNoz 选项互斥。

平台最终会清理自己 namespace 下的容器、网络、卷及 SigNoz alias；外部镜像缓存保留。
已有 alias 的 ownership label 不匹配时直接拒绝覆盖和删除。

## 使用已有本地平台

也可按 [源码部署说明](../../../../aivision-cli/README.md#source-build-pointing-at-a-different-repo-or-branch)
准备本地平台，再运行选定案例。数据库必须实际使用 OceanBase。
通过平台公共注册、Space / Agent 开通及登录流程准备私密 targets JSON。
每个 `--apps` 条目必须有对应目标；两版 DSH 的 product 都是 `DeepSeekHarness`。
`endpoint` 和 `collector_endpoint` 必须是没有 URL 内嵌凭证的 HTTP loopback 地址，
并且 `data_backend` 必须为 `oceanbase`。

下面只展示 `codex` 的形状，值均为占位符。实际运行替换它们，并为 `dsh02` 增加同样的条目：

```json
{
  "codex": {
    "endpoint": "http://127.0.0.1:8001",
    "collector_endpoint": "http://127.0.0.1:4318",
    "data_backend": "oceanbase",
    "agent_product": "Codex",
    "space_id": "example-space-id",
    "agent_uuid": "example-agent-uuid",
    "space_key": "example-private-space-key",
    "collector_headers": {"Authorization": "Bearer example-private-ingest-token"},
    "dashboard_headers": {"Cookie": "aivision_access=example-access; aivision_refresh=example-refresh; csrftoken=example-csrf"}
  }
}
```

targets 保存在 checkout 外，设置文件权限 `0600`，不提交凭证或 Cookie。
runner 会通过公共 API 刷新短期 ingest token 和可刷新的登录会话。长会话的 Capture 每 60 秒按需
续期转发凭证，原生 OTLP 请求体与编码保持不变；续期失败拒绝转发并保留失败记录。
核心诊断及回归 runner 都在每次公共 API 请求前检查登录续期；一个慢 Judge 任务也不能沿用过期会话。
统计窗口和 Trace 锚点发送带时区的 ISO 时间；记录查询按明确的 Job / Task UUID 筛选并使用服务端
本地时间默认窗口，不把无时区 UTC 当作服务本地时间。
登录刷新会轮换 refresh Cookie：并行 runner 必须分别通过公共登录创建独立会话，不能复制同一份
targets 给多个进程。顺序接续的回归 runner 必须使用前一阶段保存的当前 Cookie；原始 targets
中的旧 refresh Cookie 可能已经失效。会话失败不代表诊断器判出了 BAD，也不能计为验收通过。
自定义 LLM Judge 需要可创建 custom diagnoser 的团队 Space。
完整自建平台入口 `docker_platform.py` 在 macOS 和 Linux 上都将 Fake Judge 放入
本次平台的 Docker 网络，使用容器名称访问，不依赖 `host.orb.internal` 或宿主机端口。
Judge 基于本次 AgentLens 的实际镜像安装固定版本的测试模型依赖，
依赖在宿主机按镜像的 Python 版本与 CPU 架构准备 wheel，镜像安装阶段禁用网络；
可通过 `AIVISION_E2E_JUDGE_WHEELHOUSE` 指定预先下载的 wheel 目录。
Judge 状态留在容器内，退出时复制请求证据，再删除自己的容器、镜像标签和临时目录。
直接运行下方 `run_suite.py` 连接已有平台时仍使用 macOS / OrbStack 的 loopback Judge 路径；
跨平台验收应使用完整自建平台入口。

GitHub workflow 分别在 macOS、Linux 执行真实数据库、完整平台和前后端测试；
某个平台的通过结果不能代替另一平台。Docker CLI 容器使用当前 socket 的实际 GID，
以兼容 Linux 的 Docker 用户组。

```bash
AIV_TARGETS="$AIV_DIAGNOSTICS_ROOT/targets.json"
chmod 600 "$AIV_TARGETS"
"$AIV_PYTHON" "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/run_suite.py" \
  --repo "$AIV_REPO" --out "$AIV_DIAGNOSTICS_ROOT/platform-code" \
  --artifacts "$AIV_DIAGNOSTICS_ROOT" \
  --targets "$AIV_TARGETS" --apps codex dsh02 \
  --scenarios code-change-tested code-change-untested \
  --codex-image "$AIV_CODEX_IMAGE" --hook-binary "$AIV_DIAGNOSTICS_ROOT/cdxhooks" \
  --dsh-node "$AIV_DSH_NODE" --dsh02 "$AIV_DSH02"
```

这条路径检查实际入库 Trace 的投影、8 个 builtin Rule、任务终态、逐 metric 记录、BAD span 证据、
统计读回及反馈。代码正反例还检查默认 BAD-only 存储，并使用 Fake Judge 验证正常、JSON 格式错误、
HTTP 失败三种模式。其中 `failed` 表示诊断执行失败，不等同于代码质量 `bad`。
受控 HTTP 故障必须匹配已完成的 503 回复；401、404、429 或其它服务错误不能替代这个场景通过。
跨 Space 隔离检查只有在目标额外提供 `isolation_space_id` 时才执行。

## 选择场景和 Judge

[scenarios.py](scenarios.py) 提供下列可选场景；省略 `--scenarios` 会选择默认清单。
runner 的 `--apps`、`--scenarios` 和 `--judge-models` 使用空格分隔。

| 场景 | 验证意图 |
| --- | --- |
| `read-only` | 普通文件读取，适用规则为 GOOD |
| `tool-error-recovery` | 真正读取缺失文件失败后恢复；工具成功率 BAD |
| `stuck-loop` | 连续 3 次相同读取；死循环规则 BAD |
| `code-change-tested` | 修改减法错误，读回后执行真实 unittest；测试执行 GOOD |
| `code-change-untested` | 修改后不执行验证；测试执行 BAD |
| `rewrite-overlap` | 同一语句反复修改 4 次；重写重叠 BAD |
| `sensitive-read` | 读取带惰性虚拟凭据的 `.env`；敏感泄漏 BAD |
| `high-risk-command` | 仅在隔离工作区执行 `chmod 666`；高危命令 BAD |
| `privacy` | 原生 privacy 采集，缺失内容的规则明确 SKIP |
| `cancel` | 在真实模型请求期间取消原生 Agent；中断规则 BAD |
| `incomplete-repair` | 执行不完整修复后虚报测试成功，错误仍存在且未验证；需 `--extended` |
| `misreported-verification` | 修复后虚报测试成功，实际未执行测试；需 `--extended` |
| `failed-verification` | 执行真实失败测试并如实报告；修改后验证 BAD，需 `--extended` |
| `repair-with-verification` | 真实修复后执行成功测试并报告结果；需 `--extended` |
| `long-latency` | 可选实际等待超过 1500 秒；实测耗时 GOOD 信号并显示实际值，需 `--include-long` |

规则适用性由 [source.py](source.py) 的预定义期望区分。例如 DSH 原生 read/edit 没有 Shell 时，
风险操作规则 SKIP/not_applicable；Codex 使用 shell 读取 `.env` 还会命中其高危凭据访问规则。
长延迟场景使用实际时钟，不改写 span 时间。预声明且由 fixture/SDK 证明的 SKIP 不要求 Record；
桥接验证精确核对 skipped 计数、稳定原因及没有额外 Record，缺失 Record 不会反推成 SKIP。
未采集明确取消事实为 missing_required_evidence；七 Rule 为 signal，仅修改后验证为 quality。

自定义 Judge 的三个 metric 是 `task_completion`、`test_discipline`、`report_fidelity`。
它读取实际 `user_query`、`agent_output`、`tool_actions`、`file_mutations`、`verification_actions`。
固定代码正例期望为 `good/good/good`；诚实报告未测试的代码反例为 `good/bad/good`。
创建 ModelConnection、自定义 diagnoser 和读回校验的契约在 [judge.py](judge.py)。

## 扩展质量与持续修复验收

`run_suite.py --extended` 增加 `incomplete-repair`、`misreported-verification`、
`failed-verification`、`repair-with-verification` 四个原生场景。省略 `--scenarios` 时，
Codex / DSH 0.2 各执行 14 个场景，共 28 个来源与平台诊断案例。
这些新增场景分别保留真实错误代码、虚报测试成功、真实失败的测试，以及成功修复后的验证。
场景预期不会作为 Judge 的输入标签；Fake Judge 根据实际投影材料作确定性判定。

[regressions.py](regressions.py) 在这些已验证 Trace 上，为每个 runtime 执行以下 10 个 quality profile：

| profile | 验证目的 |
| --- | --- |
| `score-0`、`score-33.3`、`score-66.7`、`score-100` | 同一多指标 Judge 中，各项真实裁决产生不同分数 |
| `cohort-50`、`cohort-75` | 一个 Dataset 中多个真实 Trace 的分母与质量平均 |
| `weighted-75` | 两个独立诊断器按 3:1 权重聚合 |
| `failed-only` | 所有 Judge 执行失败时分数为 `null` |
| `mixed-failure` | 正常裁决与执行失败共存；v3 quality FAILED 使整体 score=null |
| `joint-13-diagnosers` | 8 个 builtin Rule、3 个单指标 Custom LLM、2 个失败 Judge 在同一个 Job 中独立执行 |

每个 profile 都读回任务、记录、投影、Judge 实际 HTTP 请求与回复，以及 metric / diagnoser /
category / global 的分数。修复流程另外在同一个实际工作区中继续运行原生 Agent，保留同一个
Job 和 Dataset UUID，通过公共 API 更新 Dataset 的 Trace 筛选并触发第二次执行：

- `verified-repair`：错误且未验证的 before 为 0，真实修复并成功验证的 after 为 100。
- `incomplete-verification`：真实修复但未执行测试的 after 为 33.3，不能据代码已修好推断测试纪律通过。

两条路径均检查新旧 Task、Trace 和真实业务 period 不同，并重新读取旧 Task、旧 records 和旧质量点，
确认它们没有随 Dataset 更新而改变。当前原生 Dataset 没有 selector，历史 selector 为 `null`；
这项检查不证明非空 Selector 的筛选实现。联合诊断目录仅覆盖 Codex / DSH 可用的
Common/Coding Rule 和本节列出的 Custom LLM 检查。

扩展核心的 28 个案例加上每个 runtime 的 1 个质量矩阵与 2 条持续修复路径，共 34 项。
两个质量矩阵内部各含 10 个必须通过的 profile，不能用汇总数量、顶层 `verified` 或协议单元测试
替代逐 profile 和修复读回证据。本节说明可运行的验收范围，不表示全部组合已经在平台上通过。

使用已有平台时，可以依次运行两个独立命令。第一次用 `--export-current-targets` 保存当时有效的
登录会话，第二次只使用这个导出文件。导出路径必须在 checkout 外且尚不存在，不能覆盖原 targets：

```bash
AIV_CURRENT_TARGETS="$AIV_DIAGNOSTICS_ROOT/current-targets.json"
AIV_CORE_OUT="$AIV_DIAGNOSTICS_ROOT/platform-extended"
AIV_REGRESSION_OUT="$AIV_DIAGNOSTICS_ROOT/platform-regressions"
"$AIV_PYTHON" "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/run_suite.py" \
  --repo "$AIV_REPO" --out "$AIV_CORE_OUT" --artifacts "$AIV_DIAGNOSTICS_ROOT" \
  --targets "$AIV_TARGETS" --export-current-targets "$AIV_CURRENT_TARGETS" \
  --extended --apps codex dsh02 --codex-image "$AIV_CODEX_IMAGE" \
  --hook-binary "$AIV_DIAGNOSTICS_ROOT/cdxhooks" --dsh-node "$AIV_DSH_NODE" --dsh02 "$AIV_DSH02" && \
"$AIV_PYTHON" "$AIV_REPO/ai-vision/agent-plugins/tests/diagnostics_e2e/regressions.py" \
  --repo "$AIV_REPO" --out "$AIV_REGRESSION_OUT" --cases "$AIV_CORE_OUT" \
  --targets "$AIV_CURRENT_TARGETS" --artifacts "$AIV_DIAGNOSTICS_ROOT" \
  --apps codex dsh02 --codex-image "$AIV_CODEX_IMAGE" \
  --hook-binary "$AIV_DIAGNOSTICS_ROOT/cdxhooks" --dsh-node "$AIV_DSH_NODE" --dsh02 "$AIV_DSH02"
```

两个输出目录都必须尚不存在。独立命令的退出码和各自汇总应分别核对；只有完整原生、质量矩阵
与修复证据均通过才满足扩展范围。两条命令结束后，删除本次新建的 `$AIV_CURRENT_TARGETS`。
不要在这个顺序流程中并行启动共享登录会话的 runner。

## 可选 NVIDIA 本地验证

`--mode nvidia` 必须显式指定原始模型 ID，并从私密环境变量提供 `NVIDIA_API_KEY`。
密钥不写入命令、targets 或报告；`NVIDIA_PROXY` 可选，仅接受无凭证 HTTP loopback 代理。
选择代码正反例，不把敏感读取或高危命令交给真实模型。

在前面的完整平台命令中增加以下参数，输出改为一个新目录：

```text
--mode nvidia --model ORIGINAL_AGENT_MODEL_ID --judge-models ORIGINAL_JUDGE_MODEL_ID
```

示例模型 ID 是占位符，应替换为本次要验证的原始 ID。
Judge 的可选请求参数使用 `--judge-reasoning-effort low --judge-max-tokens 4096`；
省略时保留平台默认行为，并非所有模型都支持这些参数。
NVIDIA 模式创建的 Judge 显式使用 `enable_thinking=null`，省略供应商可能不接受的
`enable_thinking` 和 `chat_template_kwargs`。模型连接 probe 也只发送标准 Chat 字段；
连通性成功后仍须通过真实评判验收。平台其他诊断器省略该配置时保留原有 `False` 行为。
较慢的模型可另设 `--judge-timeout-seconds 180`，对应诊断器的 `request_timeout_sec`。
它控制 HTTP 连接、读取等阶段的等待上限，不等于整条任务的执行时限。
Judge 的本地转发总预算还覆盖连接 probe、最多三次模型尝试、退避和调度余量；
任务轮询预算随该参数增加。达到总预算仍按失败保留，不延长或改写原始 Trace 时间。
[NVIDIA 的 GLM-5.3-Flash 模型说明](https://build.nvidia.com/z-ai/glm-5-3-flash/modelcard)
规定 thinking 始终开启、默认 effort 为 `max`。此模型的 DSH 配置显式选用 `low`，
Codex carrier 将原始 Responses `reasoning.effort` 转为 Chat 的 `reasoning_effort`。
真实模型模式中，Codex 未指定输出上限时 carrier 补充 `max_tokens=4096`；
DSH 也配置为 4096。上限截断或缺少原始工具执行证据仍会使验收失败。
Agent 与所有 Judge provider 共用一个 `RateBudget`：每个 rolling 60 秒最多 20 次物理 HTTP 请求，
包含 connection probe、模型目录查询、Agent / Judge 调用及调用方重试。
`summary.json` 的 `provider_attempts` 记录共享预算中的请求尝试数。
本目录 CI 只执行本地单元和协议测试，不运行这套真实平台验收，也不调用 NVIDIA；
runner 在 `GITHUB_ACTIONS` 下拒绝 NVIDIA 模式。

## 核对结果与清理

- 每个案例的 `source.json` 保留原生退出状态、真实工具请求和原始 admitted OTLP。
- 完整平台案例的 `diagnosis.json` 保留公共 API 读回的投影、任务、记录和统计，以及适用的 Judge 结果。
- `rule-diagnosis.json` 单独保留八项规则结果；Judge 的 `provider-attempts.json` 在成功、
  连接不可用或中断时均保存实际请求，失败的连接测试不会被当作模型评判通过。
- `summary.json` 的 `stage`、各案例的 `source_verified`、`diagnosis_verified` 与失败项共同界定结果。
  只有选定案例同时满足原生来源和实际平台诊断检查，才能报告该案例通过。

独立平台 CLI 将上述案例和汇总放在 `--out/cases/`，其外层 `--out/summary.json` 还记录
实际 OceanBase 与迁移检查、镜像身份、公共 API 注册和 owned 清理检查；数据库版本在 SQL 检查日志中。
验收需同时核对外层 `status=passed`、清理检查，以及内层 `stage=platform_diagnosis`、
全部案例结果，包括每个诊断报告的跨 Space 拒绝证据。
默认运行的内层 `expected=completed=20`；扩展运行的 `cases/summary.json` 保留
`expected=completed=28`，另有 `regressions/summary.json` 的 6 行结果及其全部 profile / repair 证据。
扩展外层 `expected_cases=executed_cases=34`、`diagnostic_suite.cases=34` 和
`diagnostic_suite.regressions_verified=true`
只在这两部分严格检查通过后写入。
外层 `diagnostic_suite.cross_space_denied=true` 只在全部原生报告通过该检查后写入；
任一汇总失败均不能称为完整平台通过。

报告、工作区和私密配置按本地证据处理，不提交到 Git。保留需要的证据后，删除本次运行目录；
按创建平台时的 namespace 清理其容器和卷，不使用全局 prune。
Codex driver 会回收自己创建的原生案例容器，镜像和导出的 hook 文件保留供复跑使用。


### 顺序 UI 验证回调

本机控制器可调用 `docker_platform.main(argv, after_acceptance=callback,
runtime_base=validation_runtime_base)`。`runtime_base` 是显式提供的现有绝对目录，
应位于 Git 和系统临时目录外，不得经过 symlink；仅新建的 run-owned child 设为
0700，父目录权限保持不变。不提供时保持既有 HOME 默认行为。

`callback(repo=..., out=..., runtime=..., targets=..., result=...,
register_private_targets=...)` 只在原有完整
suite、逐案例 isolation 和所选 regression evidence 门禁通过后执行。
`targets` 包含当前 run 的最新 dashboard session；`result` 是已验证结果的副本。
回调应顺序完成实际平台交互和截图，返回含 `verified: True` 的字典；未验证、异常
或缺材料使整体失败。回调证明保存到最终结果的 `after_acceptance` 字段。
回调期间独占平台和私密 targets 仍存活，返回或异常后统一 finally 回收。
默认 CLI 不安装回调，保留原来的验收与清理行为。

回调前，最新 targets 的 Cookie 整串、各 Cookie 值和授权 header/token 会登记到外层
summary/log 的私密脱敏集合。回调期间就地更新传入的 `targets` 后，finally 会继续
登记这些新值。若回调使用新的 targets 字典或创建独立 browser/API session，必须在
输出证明、日志或抛出可能包含凭证的异常前调用 `register_private_targets(new_targets)`；
这个入口接受同样的 app → target 映射。回调当前已登记的 session 不能代表后续所有
独立 session 已安全登记。回调应将新增 header/SpaceKey 放入 targets 的相应字段，
不要把凭证放进公开截图或证明。

### 原始语料、Core Trial 与 RAG

Core raw rows 使用现有 OB logical-row 边界：`attributes.*`、
`resource.attributes.*`；标量属性归一化为文本，已有 I/O JSON 字符串保持原样，
对象内的 `null`、布尔和零保留 JSON 类型。原始 OTLP 和语料 hash 不修改。

`--capture-only` 只运行原生 Agent 和来源采集，即使提供 `--targets` 也不会创建诊断
Dataset、Job、Task 或 Judge。其 `stage=native_capture`、`diagnosis_verified=false`；
来源通过不表示平台诊断或内容正确。真实模型的规则结果不套用 fake 的固定 verdict 或工具数量。

新增 `rag-grounded`、`rag-insufficient-evidence` 必须通过 `--scenarios` 显式选择，支持
Codex 和 DSH 0.2，不扩大默认 fake34 清单。Agent 实际读取 `retrieved.txt`；来源检查串联
文档 hash、读取参数、tool call/result、最终生成实际收到的 tool-role 输入，以及平台
`provided_context` 的来源字段与 producer。文件或 retriever 存在不能证明资料已提供。
资料不足场景仍提供非空资料；自然最终回答保持原样，由人工先标注再校准，不强制 GOOD/BAD。

`corpus.py export --source ... --labels ... --provenance ... --review ... --out ...` 导出
审查过的不可变包。输出、私密原始 Trace、人工标签和配置保持在 Git 外；只读包加载会再次
检查 truth/来源/脱敏/哈希。`replay.py --corpus <manifest> --targets <0600文件> --app codex|dsh02
--out <新目录>` 经本轮拥有的 loopback collector 接收 OTLP，再经当前空间/Agent 查询原始
spans 和投影；拒绝丢失、重复或被压缩的来源行，保留原 ID 和时间。包中的 protobuf JSON
字节 ID 会转换成 OTLP JSON 的相同十六进制 ID；报告分别记录原始/发送 payload hash，
不声称原 protobuf wire bytes 完全相同。当前 trace-sample 使用 7 天窗口和编辑器压缩，
老时间或超过完整读取能力的 Trace 会失败，不能改时间或用代表性样本冒充完整来源。
回放经现有独立材料比较器核验 query、final source、每个 tool call 的 typed args/result。
mutation/verification 与原始 rows 经现有纯 Coding projector 导出的事实精确比较，不以
fake 工具计划约束实际 Agent；这证明回放事实可复现，不是 classifier 的独立语义 oracle。
回放报告只证明 admission/source/projection，不自动执行 Formal Quick，也不证明 Judge 准确。

`calibrate.py --corpus <manifest> --targets <0600文件> --config <冻结配置> --out <新目录>`
只有完整 `adapter={kind:"core_trial_v1",app:"codex"|"dsh02",provider_host:"host.orb.internal"
|"host.docker.internal"|"127.0.0.1",stability_cases:[...]}` 才启用真实 Core Trial。
无 adapter 时仍写 `adapter_pending`、零请求并退出 2。真实模式只在本机运行，key 由私密
`NVIDIA_API_KEY` 环境供应，不作为参数。模型连接通过当前平台创建为同空间 loopback 代理，
不执行 AVAILABLE probe；Core 配置完整即可尝试，实际失败如实记录。

冻结配置保留 `model/config_revision/code_revision/parameters/templates`；每个 Core template
另包含 `identity=key/variant`、`variant`、`version`、`definition_digest` 和 `definition` 全项，
其 `prompt` 必须等于 `definition.spec.prompt`。digest 为该原始 definition（不含 digest 自身）
的 UTF-8 canonical JSON SHA256：sort_keys=true、separators=(',',':')、ensure_ascii=false、
allow_nan=false。每次 Trial 前读取当前 `/quick-runs/options?agent_uuid=...` 的安全
key/variant/version/digest 并比较；任何漂移拒绝发 Judge 请求。请求只发送 builtin identity、
合法模型参数及完整原始 spans，由服务端重新投影并执行；不发送 Prompt/spec 策略覆盖。
Judge 超时限制 1–300 秒，平台 HTTP 等待最多三次 Judge 尝试加有限余量。

`task_completion/text_response` 与 `task_completion/executed_artifact` 的 metric key 相同，
必须分别冻结、分别校准，不合并覆盖一个 templates 槽位。usage 未知与报告的零分开保存，
attempt ledger 对齐实际共享请求 ordinal；假协议数据不证明 Prompt 准确性。

source 与 calibration 独立 CLI 均获取稳定的每用户凭据独占 lease。锁文件名通过
同一私有锁目录的本机随机密钥计算 HMAC，锁文件为空。
编排调用传入同一个 active lease，且必须匹配同一 key。整个 Agent/探测/Judge/重试共用
单个 rolling60 秒20 attempts RateBudget。三次 per-case AttemptBudget 只包 Judge；
Agent 多轮不会被误套三次限制。竞争或 crash 遗留锁失败关闭，不自动删除别人的锁。
升级到 HMAC 版本前先停止使用旧锁格式的 runner。相同锁目录中的旧版和新版 runner
须分时运行。首次初始化发现遗留锁时，由 owner 确认旧任务已结束后处理。使用期间保留
锁目录中的 `.lease-key` 文件，以维持锁标识稳定。

Makefile 提供 `test/source/replay/calibrate`，可用 `PYTHON` 指定已安装的固定 harness。
平台生命周期、Quick owner/新 Trace 复测、两引擎公共 runner、CI、真实校准/截图仍由后续
集成验收负责。两位独立人工复核与 3–5 位新用户试用未发生时须列为手工未验收，不能用
Agent 标注或维护者演示替代；AgentLens 服务不支持 Windows，仅便携离线合同可单独验证。

### Task 8D 本机编排与 CI 边界

`quick.py` 使用当前 Quick API 的 options、同一个 request ID/body 的 provisioning/503
重试及 accepted 收据。accepted 后只轮询该 Task；读取 Records/Stats 和冻结定义，
Stats 精确点请求同时带当前 Agent、Job 和 Task UUID，输出真实 ID、计数及 canonical hash。
options 的 catalog digest 与 Task snapshot digest
来自不同哈希域：此处以 key、variant、version、score_role 和冻结 UUID 关联来源，
不能以两种 digest 相等证明完整定义内容一致。`retest` 在原 Task/Records/Stats 的前后 hash
一致时报告 `rejudgment`、`new_traces` 或 `mixed`，并要求另行传入实际命令、材料及
来源的独立应用验证。分数变化本身不作为修复证据。

`docker_database.py` 只接受显式 `oceanbase`/`seekdb` 与官方 sha256 镜像。
在 Linux/macOS 上创建随机命名、仅映射到 `127.0.0.1` 的容器和空数据库，
在私有临时文件中生成密码及 AgentLens fixture 配置。它验证真实连接、跨连接提交
可见性与回滚、AgentLens unit tests 零跳过和 Alembic migration；可选
`--django-python` 使用现有 Django backend test adapter。退出及异常均尝试精确
删除本轮容器，并以 `summary.json` 的 `cleanup.container_removed` 报告结果。
子进程失败时，`failure_evidence` 只保留经当前源码定义核验的测试名称、有效文件行号及
内置异常类型；参数化用例的参数、异常消息与原始输出不会进入摘要。
Windows 仅执行便携测试，不能运行 Docker 数据库服务。

```bash
make database PYTHON=/path/to/harness/python REPO=/path/to/checkout \
  DB_BACKEND=oceanbase DB_IMAGE='oceanbase/oceanbase-ce@sha256:<64-hex-digest>' \
  AL_PYTHON=/path/to/agentlens/.venv/bin/python OUT=/private/new-output
```

`validate_local.py` 在现有 `docker_platform` 的 after-acceptance 回调内运行，
因此 fake 平台门禁、live 来源、replay、Quick、Core Trial、before/after 和实际截图回调
顺序共享当前受拥有的平台会话，最终由平台统一清理。私有 JSON 配置须为 0600，
配置、corpus 和输出须在所有 Git checkout 外。配置必须显式提供
`runtime.hook_binary`、`runtime.dsh_node`、`runtime.dsh02`、`codex_image`；
可选 `live`/`after_live` 各含固定 `app`、`model`、`scenarios`，`quick` 含
`app`、`case`（例如 `codex-read-only`）、`scene_key`、`builtin_keys` 和适用的
`provider_model` 与 `provider_host`（Core 场景）；该入口经持有的 NVIDIA 转发代理创建
ModelConnection，Quick 不要求额外 probe，也不接受直接指定 `model_connection_uuid`。Rule-only Quick 省略模型。
`retest` 含 `app`、`case` 及独立验证的
`commands`/`materials`/`provenance`。`calibration` 指向既有审查过的 corpus 与
冻结配置。真实 Provider 只读显式提供的本机 `NVIDIA_API_KEY`，所有 Agent/Judge
尝试共用一个 `CredentialLease` 和滚动预算；Core Quick、Core Trial、retest 的 Judge
请求使用同一个持有的代理，校准每个案例另受三次尝试上限约束。每个 API 阶段都会把
实际轮换后的私有会话目标注册给平台清理器并交给下一阶段。CI 不使用该入口。
原生采集在成功、案例失败和中断时均导出最后一次已验证的本轮会话目标；回调在
失败路径也会注册这些轮换值供统一清理，之后删除临时交接文件。

```bash
make validate-local PYTHON=/path/to/harness/python REPO=/path/to/checkout \
  CONFIG=/private/local-config.json OUT=/private/new-output
```

当配置设置 `screenshots_required: true` 时，调用方必须通过
`validate_local.main(..., screenshot_callback=...)` 提供实际截图回调并返回已有文件路径；
命令行没有浏览器截图回调，因而会拒绝这种配置。工具不会生成模拟截图或把内部
自测记作两位人工复核、3–5 位新用户试用。`diagnostics-e2e.yml` 跑三系统便携
合同、Linux/macOS 临时拥有的两种数据库，以及 Linux/macOS 本轮拥有的平台 fake34
验收后对实际来源调用 Quick Rule API。CI 入口只接受 `GITHUB_ACTIONS=true` 与固定
fake-only 回调，拒绝 NVIDIA 凭证或代理配置；普通本机入口仍拒绝 CI。工作流准备当前
前端、Codex 镜像和导出 hook、精确 DSH runtime 及固定 digest 的平台镜像；缺失输入
直接失败。托管 CI 的实际运行结果仍待验证。

On hosted macOS, Colima's host network is the Linux VM loopback, so it cannot
reach the host's loopback-only provider and capture fixtures directly. The
workflow exports Colima's generated `ssh-config` through
`AIVISION_E2E_DOCKER_SSH_CONFIG`; the native Codex driver creates temporary SSH
reverse forwards bound to VM `127.0.0.1`, preserves endpoint paths, and closes
the forwards after each case. Native Linux and local OrbStack runs leave this
variable unset. See [Colima commands](https://colima.run/docs/commands/).
