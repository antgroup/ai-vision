# Agent framework E2E

这套测试验证 LangChain / Deep Agents 的真实运行数据能通过官方 Langfuse SDK
或 NVIDIA NeMo Relay SDK 进入 AI Vision，并由用户查询接口完整读回。
模型与 embedding 均在进程内运行，不需要 LLM 服务端点、模型账号或 API key。
默认 CI 在 runner 内启动接收器和临时 Docker 环境，不使用已有平台或外部模型端点。
另有显式启用的 NVIDIA 免费端点测试，执行次数与输出 token 上限固定，见下文。

## 用例与判定

业务场景是按租户查询保留策略，再调用计算工具得出存储量。
`GenericFakeChatModel` 读取真实工具消息；`DeterministicFakeEmbedding` 为文档和
查询生成实际向量，`InMemoryVectorStore` 在同租户候选文档间排序。
正常结果必须是 `14 days × 10 GiB = 140 GiB`，修改检索文档后必须变成
`21 days × 10 GiB = 210 GiB`。每次运行生成独立 receipt，检索、计算、模型输出
和平台读回必须保持一致。Deep Agents 必须实际调用 `task` / `researcher`。

| 场景 | 核心断言 |
| --- | --- |
| success | 检索→计算→回答；第二轮从 checkpoint 读取相同结果且不重复工具 |
| no-results | 实际 embedding / 检索后返回空结果，不调用计算 |
| retrieval-error | 真实工具错误转为框架的受控错误消息 |
| tool-exception | 未处理的工具异常传播，工具和请求记录 ERROR |
| retry | 实际失败消息之后重试，重新检索并得出正确结果 |
| changed-corpus | 回答随实际文档变化 |
| zero-usage / unknown-usage | SDK 明确报告的 0 与未报告用量保持区别 |
| stream | 实际工具流、文本 chunk 与完成用量，不重复累计 |
| cancel | 首个实际文本 chunk 后取消；任务停止、scope 闭合、父图完整 |
| concurrent | 独立会话、receipt、trace 和工具结果，不串数据 |

Langfuse 3.15.0 / 4.16.0 环境各运行 **2 frameworks × 2 SDKs × 11 scenarios = 44 cases**。
新增 Langfuse 5.0.2 环境使用 `--sdks langfuse`，运行 **2 frameworks × 11 scenarios = 22 cases**；
不将 Relay 扩展纳入 SDK5 适配。三个版本分别安装，其他框架版本见 requirements 文件。
每次完成的 fake chat 调用报告确定的 `13 / 5 / 18` token；这些数值是测试输入。
Embedding 提供真实向量但不报告计费用量，平台应显示未知，并单独提供已知用量小计。
本测试不验证模型供应商账单。Fake 模型不声明真实供应商，provider 分组的验收
覆盖未知归属；不能据此宣称各真实供应商都已兼容。

`execute_rag.py` 捕获官方 SDK 的原始 protobuf，转发原请求体，并记录 HTTP / OTLP
接收结果。`verify_rag.py` 用独立业务常量和实际框架账本验证：

- 全量 span IDs、名称、父子图、类型、状态、时间、原始业务属性和用量；
- 独立 metadata 类型账本，SDK5 JSON 字符串与旧 SDK 原始线路值分别核对；
- 原生 trace、Langfuse v1 trace / 分页 observations；
- session 首末输入输出及其来源、完整用量和未知状态；
- 按 model / provider 分组的 metric、调用数和已知用量覆盖；
- metric 的 `reportedUsage` 按 input / output / total 分别对账已上报小计和
  `reportedCalls`，`usageByType` 分开对账 chat（`GENERATION`）与 embedding；
- 错误 key、跨 space、跨 agent、错误时间窗均不能获得目标数据。

小计按 `(traceId, observationId)` 去重，真实报告的 0 保持为 0；某个字段完全未报告
时，其小计仍为 `null`、覆盖调用数为 0。分类只包含实际有调用的类型，按 chat、
embedding 顺序返回；分类的完整用量聚合保留全量父子上下文，嵌套 billable 调用仍保持
unknown。这些展示字段不改变原 `usage` 的完整窗口语义，也不补造 fake embedding 计量。

Collector 验证独立标注为 `source_verified`。只有真实平台 API 全部对账成功才能
标注 `platform_query_verified`；空矩阵、跳过用例或任意失败都不能通过。
NeMo Relay 0.9.3 取消生成的 partial output / usage / TTFT，以及 Deep Agents
native task 可能先于子图清理结束的 sibling scope 时间边界，仍按
[NR-002](../../../../KNOWN_ISSUES.md#nr-002框架-middleware-的-managed-streaming--ttft-边界)
记录。完整44场景中的请求 root、父图、工具、generation 和 embedding 仍严格对账。
独立生命周期回归只对提前停止消费 / 显式 `GeneratorExit` 的原生 task guard
记录晚于 root 的收尾；仍要求唯一开始和一次 `ERROR` 结束，保留实际异常、parent
和原始时间。适配器等待本请求实际模型任务及已进入原生调用的真实 SDK end，
模型须在 root 前停止且没有完成内容。
worker、orchestrator、所有 generation、embedding、业务工具，以及正常完成和实际
`CancelledError` 的请求边界不放宽。
单请求清理不调用会等待其它请求的全局 flush。
取消时保留原始 SDK 时间并报告清理尾差；已完成调用的时间边界继续严格对账。

## 本地运行

使用 Python 3.12，从仓库根目录执行：

```sh
python -m pip install -r ai-vision/agent-plugins/tests/framework_e2e/requirements-lf4.txt
python -m unittest discover -s ai-vision/agent-plugins/tests/framework_e2e
python ai-vision/agent-plugins/tests/framework_e2e/suite.py \
  --mode collector --repo . --out artifacts/collector
```

仅运行新版 SDK 时，在独立 Python 环境安装 `requirements-lf5.txt`，并给 `suite.py`
增加 `--sdks langfuse`。默认矩阵仍包含两个 SDK，不能用过滤后的 22 项替代旧环境 44 项。

如需完整平台链路，使用下述 `docker_platform.py` 启动临时环境并自动建立测试身份。
每次选择新输出目录。运行报告、日志、页面截图和凭证文件均不提交到 Git。
`browser_platform.py` 还会让当前前端源码访问本次平台，执行真实页面断言和脱敏截图。

## CI：runner 内的临时测试环境

[workflow](../../../../.github/workflows/framework-e2e.yml) 在 PR 和手动触发时运行。
默认 CI 不连接现有部署，不要求平台端点、SpaceKey 或模型供应商 secrets。
各后端与 SDK 矩阵独立运行，避免某个单测失败遮蔽其他环境的结果。
Docker 平台任务等待前端源码检查通过；整套 CI 通过仍要求全部任务成功。

`sdk_coverage=full` 是默认配置，正式 Release 使用完整矩阵。Daily 可以显式选择
`sdk_coverage=smoke`，在真实 OceanBase 平台上用 Langfuse 4.16.0 与 NeMo Relay
分别验证 LangChain 和 Deep Agents 的 `success`、`unknown-usage`、`stream`、
`cancel`、`concurrent`，共 20 例。启用冒烟集时，同一次运行还要求独立的真实
SDK／浏览器任务通过，其中 Langfuse 5.0.2 完整执行 22 例。两种 Docker 配置都按
精确用例 ID、固定 SDK 版本以及源码和平台对账结果验收；CI 不接收调试用的场景列表。

| Job | 环境 | 验证范围 |
| --- | --- | --- |
| frontend | Linux / macOS / Windows | 当前前端全量 lint、回归、类型检查和生产构建 |
| backend-contracts | Linux / macOS / Windows | 原生查询、会话、v1 与用量语义单测；不初始化数据库 |
| cli | Linux / macOS | Go 单元测试与 Unix 安装器测试；现有 CLI 发行平台不包含 Windows |
| contracts | 三系统 × Langfuse SDK3 / SDK4 / SDK5 | 业务、oracle、onboarding 与生命周期单测；SDK3/4 各44项、SDK5仅 Langfuse 22项实际导出到本地 OTLP 接收器 |
| js-contracts | 三系统 × JS 5.14 / 6.0，Node 22 | 严格 TypeScript、四入口双模式、protobuf/gzip、metadata、IO/usage、stream/cancel 与 flush/shutdown |
| docker-database | Linux / macOS Intel Colima × OceanBase / seekdb | 临时数据库启动、SQL 事务读回；当前 Django 全量单测使用同一数据库引擎 |
| docker-platform | Linux / macOS Intel Colima | 完整矩阵在临时六服务平台验证 SDK3／SDK4 各 44 例和 SDK5 22 例的采集与 API 对账。显式 Daily 冒烟集在 Linux 验证 SDK4／Relay 共 20 例 |
| browser-platform | Linux | 独立临时平台；Python5 fake 22 cases、JS5/6 本地 provider fixture 各33 traces；当前源码页面与实际 API 闭环 |
| browser-platform-nvidia | Linux，手动显式启用 | 独立临时平台；Python5 3请求、JS5/6 各2请求；NVIDIA 原始 usage、SDK、API 和实际页面对账 |

默认 Python 模型是 LangChain fake chat / embedding；向量检索使用 `InMemoryVectorStore`。
JS 的确定性 OpenAI 兼容 HTTP fixture 单独标注，不称为 LangChain Python fake model。
`source_verified` 只证明实际框架和官方 SDK 导出；临时 Docker 平台的
`platform_query_verified` 还要求 OTLP 接收、OceanBase 持久化以及当前后端 API 数据一致。
两种证明保留各自边界，不能用 collector 通过替代完整平台通过。

Windows 暂不运行 Docker。macOS Docker 使用 `macos-15-intel` 和 Colima，分配
3 CPU、10 GiB 内存、30 GiB 磁盘，使用本轮独立配置目录与 `nofile=65536`。
Linux 默认使用标准 `ubuntu-24.04`；可用
repository variable `AIVISION_E2E_DOCKER_LINUX_RUNNER` 选择已有 runner。
CI 记录可用资源，执行结果及容器 OOM 状态决定验收，不因预设预算直接跳过测试，
也不会自动创建收费 runner。Docker SDK 套件顺序执行，每次只运行一个用例 worker。

Docker 数据库只使用官方 OceanBase / seekdb 的固定 digest，六服务基础镜像也在
workflow 中固定 digest。需要更新时同时审查对应镜像仓库、支持架构和实际部署结果。
`docker_database.py --backend-tests . --python <backend-python>` 会把当前 Django
`apps` 全量测试放到本次临时数据库上；显式禁止 SQLite，并隔离本机 dotenv / 遥测配置。
临时数据库容器显式设置文件句柄上限。固定 seekdb 镜像的初始化问题按
[DB-001](../../../../KNOWN_ISSUES.md#db-001seekdb-1400-镜像的密码初始化未执行)
处理；SQL 和 Django 测试始终要求生成密码可用且空密码被拒绝。
任何单元测试、SQL 对账或清理失败都会令 job 失败。

临时 SigNoz 密码保留随机主体，并保证大小写字母、数字和特殊字符。
后端与 AgentLens 日志目录在容器启动前由 runner 创建，确保 Linux 上即使日志文件
由 root 写入，也能由 runner 清理；不依赖 sudo 或事后递归 chown。

完整 Docker job 从当前 CLI 源码构建，挂载当前后端应用代码，保留固定 backend 镜像
中的 Linux 环境。SDK 容器使用同一基础镜像、Python 3.12 和三份独立固定依赖，
因此 macOS Intel 不依赖 Relay 的原生 Intel wheel。前端源码单独经过三系统构建；
`docker-platform` 接口测试使用固定 FE 镜像；新增 browser jobs 用当前源码 Vite
代理到本次临时平台，保留渲染后的页面证据。该结果证明源码页面，不冒充发布镜像验收。
固定 AgentLens 源码基线镜像尚无 `/internal/readyz`；仅 source baseline 使用该镜像
真实公开的 `/internal/healthz` 存活检查，不宣称旧 AgentLens 通过了 SQL readiness。
诊断套件挂载当前 AgentLens 源码，仍使用 canonical `/internal/readyz` 数据库检查；
当前挂载后端的 readiness 和发布候选的原始探针保持不变。

此 workflow 固定的旧 SigNoz 基础镜像未包含
[AV-003](../../../../KNOWN_ISSUES.md#av-003存储查询中-telemetry-json-的大整数精度) 修复。
为严格验证 SDK 原始纳秒和属性，CI 从固定上游 commit
`1deab460854209ca0b18094d10128dcd0598aea9` 下载校验过的源码，应用
`fixtures/signoz-native-nanos.patch`，运行新增的3个模块回归，再用 Go 1.25.7 构建临时
Linux 候选 binary / 镜像。报告记录源码、补丁、binary 和镜像哈希；候选仅在本次
runner 内使用，不发布、不修改参考仓库。这套 CI 的候选构建不等于旧基础镜像已修复；使用新版已包含修复的固定镜像
进行本地验收时，应另记录其 digest / revision，且保持相同纳秒 oracle。
Go 缓存和构建输入使用本轮独立目录，候选镜像完成后即回收，再启动数据库，
避免占用部署磁盘；保留脱敏日志和哈希。镜像复制已设为可执行的二进制，兼容
BuildKit 与旧 Docker 构建器。

私有 GHCR 基础镜像使用 `packages: read` 的 job token；各 package 必须允许本仓库
的 Actions 读取。cast 前由 host Docker client 使用当前 registry 登录预拉取缺失的固定
digest；已缓存的本机架构镜像不会重拉。诊断 CLI 容器使用同一 daemon 的镜像缓存，
不挂载或接收 host registry 凭据。无需配置平台凭据。部署密码与测试 SpaceKey 由本次 job 临时生成，
原始部署配置保存在 HOME 下 `0700` 临时目录，以便 Colima 共享；退出时删除。
artifact 只上传脱敏后的账本、SDK spans、接口对账结果和失败日志。
部署失败摘要记录 SigNoz 候选镜像构建前与部署失败时可访问文件系统的可用字节数，不记录磁盘路径。
若 cast 失败且 OceanBase 已退出，额外限时 10 秒、限量 64 KiB 在内存中检查启动日志，
仅输出已知的固定阶段和错误分类；该检查不保存或上传日志原文，未知内容不进入摘要。
`insufficient_disk` 仅对应 OBD-2003 的完整磁盘预检消息，不保留其中的路径或容量。
固定 OceanBase 镜像的 OBD 3.6.0 在同一文件系统上额外预留 4 GiB slog 空间，
因此这里的 2 GiB datafile 与 4 GiB log 配置在启动预检时需要至少 10 GiB 可用空间；
构建缓存和所有固定服务镜像也会消耗部署磁盘，磁盘不足仍使测试失败。

本地完整运行示例（镜像 JSON 使用 workflow 中的固定配置）：

```sh
python ai-vision/agent-plugins/tests/framework_e2e/docker_database.py \
  --backend seekdb --out artifacts/docker-database \
  --backend-tests . --python ai-vision/aivision-py-be/.venv/bin/python
python ai-vision/agent-plugins/tests/framework_e2e/docker_platform.py \
  --repo . --out artifacts/docker-platform
```

数据库部署与 Docker 操作只作用于本次随机 namespace 下的资源。检查 `summary.json`
和每个 `verification.json` 的实际执行数、断言和已知限制，不只看 job 总状态。
NeMo Relay 的 NR-002 取消限制继续逐案报告。

该目录的 Makefile 也提供 `test`、`collector`、`platform`、`ci`、
`docker-database` 和 `docker-platform` 入口。数据库 target 默认使用 seekdb 和后端
venv；可通过 `BACKEND`、`BACKEND_PYTHON`、`PYTHON`、`REPO`、`OUT` 覆盖配置。

## SDK 到实际页面的可复用 E2E

安装 `requirements-lf5.txt`、JS `tests/js` 与 `tests/js6` 两份锁定依赖、前端锁定依赖
及 Playwright Chromium 后，从仓库根目录运行：

```sh
python ai-vision/agent-plugins/tests/framework_e2e/browser_platform.py \
  --repo . --out artifacts/browser-fake --source fake
```

默认入口复用上述六服务固定镜像配置与临时身份，独立部署后依次执行采集、API 对账、
公共 manifest 构造、当前源码页面验证。源码账本生成预期值，页面 API 响应不能反过来
充当预期值。完整 fake 矩阵产生 92 条轨迹；页面选取 Python5、JS5/6 的成功、错误、
用量为零与未知、stream/cancel、会话与并发隔离，以及三语言接入页面。
页面同时核对完整 IO 的显示与复制、原始 JSON 导出、metadata 类型和单次解码，
包括数值、布尔值、空字符串、显式 null 与缺失字段。IO 预期值来自 SDK 实际导出的
wire 值，按后端现有规则只解析一次；如果 SDK 已将原始数值与数字字符串编码成
相同 wire 值，测试不声称能够恢复上报前丢失的类型差异。
页面用例不得 mock API，不允许空矩阵、跳过、重试或失败后仅凭退出码宣称通过。
当前 fake 页面门禁至少24项，NVIDIA 页面门禁至少11项；除退出码外逐条检查
Playwright 报告，所有项目必须只有一次执行且状态为 passed。

已准备的本地隔离平台可显式复用。targets 为现有 `{"langfuse": {...}}` 格式，必须
声明 `data_backend: "oceanbase"`；origin 仅允许 HTTP loopback（含 SSH 隧道）。
认证使用 Playwright storageState，或私有 `{"email": "...", "password": "..."}` 登录文件：

```sh
python ai-vision/agent-plugins/tests/framework_e2e/browser_platform.py \
  --repo . --out /private/tmp/browser-fake-new --source fake \
  --targets /private/tmp/targets.json --auth-file /private/tmp/browser-auth.json
# 也可用 --login-file 替代 --auth-file；三个私有输入文件均须为0600，且在artifact目录之外。
```

登录放在采集、API 对账和 manifest 完成之后，避免15分钟 access cookie 在页面验证前过期。
fresh 部署和 `--login-file` 在此时通过公开登录接口取得新会话；`--auth-file` 通过公开
CSRF、refresh、me 接口续期并验证身份，保持原输入文件内容不变。新 storageState 在内存中
保存，并通过私有临时 Unix socket 传给 Playwright worker。refresh 会正常轮换服务端 token，
因此需要多次运行时优先使用 `--login-file`，
不要反复使用已被轮换的旧 storageState。续期失败则保留失败报告，不发送新的 SDK 或模型请求。

existing 模式仅管理自己启动的 SDK、Playwright 与 Vite 进程，不停止现有平台或删除数据。
每次要求新输出目录；`--python`、`--node` 可选择已安装的 Python3.12 / Node22。
公共输出包含源账本、API 验证、`browser-manifest.json`、`browser/results.json`、截图和
`browser-summary.json`。后者记录实际 SDK 矩阵、页面测试数、失败阶段、脱敏检查与请求预算。
manifest 要求 Python live 与 JS readback 中的公共 space/agent 身份与目标完全一致，且每条
轨迹的原始字段、父子关系、状态、时间、用量与 v1 检查齐全。历史缺少身份的 JS 报告必须使用
当前目标重新只读回查，不能给旧证据补造身份后直接接纳；无需为此再次采集或请求模型。
Python live 回查分别要求 native HTTP/应用 envelope 成功、`meta.complete=true`、每个 span 的
认证 space/agent/product/user 身份一致，并用保存的供应商账本核对用量。随后只读查询 v1 trace、
全部 observations 分页及 SDK 声明的 session；ID、类型、原始 IO、业务用户、session 成员与时间
来自 SDK exports，逐 observation 用量还须与供应商账本一致（未知用量保留未知语义，拒绝布尔计数）。
manifest 必须包含这些逐 case 证明；缺少 v1 或完整性证明的旧 Python live 报告需要真实只读补验。
逐 span 的 `input` / `output` 保留单层解码后的源预期，`inputWire` / `outputWire` 保留同一 SDK
字段的原始包络；页面分别核对 Enhanced 内容与 Original Tree / JSON 及剪贴板，不能用页面或
API 值改写源预期。Attributes 的零值与缺失断言先确认该节已展开且表格可见。
当前源码的 SigNoz native read API 共用两个前端请求槽位，匹配规范部署的后端查询并发预算。
排队请求取消后不发送；已发送请求只取消调用方等待，HTTP / JSON 仍读完才释放槽位，避免
客户端 abort 后后端查询仍占槽。认证与旧版 API 不排入此队列，HTTP 错误仍原样传出，调度层不新增重试。
页面用例在显式 reload、导航和退出前等待自身 native 请求完成，再检查全部真实 HTTP 错误；
这个隔离边界不宣称限制其他标签页或外部客户端的查询。
runner 为源码 Vite 设置 `VITE_DEV_API_PRESERVE_HOST=1`，使 `/api`、`/openapi`、
`/project/` 代理保留浏览器入口 Host；平台 Nginx 继续转发该 Host。这样 Django 收到的
Host 与原始 Origin 一致，仍执行正常 CSRF 校验。此开关只用于显式启用的测试，默认开发
代理行为不变；手工 page-only 验证也需设置它。[Vite 代理测试](../../../vfe/tests/viteProxy.test.mjs)使用临时 loopback
HTTP 服务分别验证默认与开启时三个路径的真实 Host/Origin，不依赖平台或供应商。
私有 targets、storageState、provider.env 位于独立临时目录，退出时清理。
浏览器不记录 trace.zip、HAR、video 或 HTML 报告；截图对接入凭据做遮挡。
文本输出先按本次密钥与 cookie 脱敏，上传前再检查允许的文件类型和秘密值；审计失败即失败，
保留已脱敏日志。新部署还须通过独立的资源清理检查，existing 模式不宣称完成部署清理。

NVIDIA 入口使用 `--source nvidia` 与环境变量 `NVIDIA_API_KEY`，固定
`https://integrate.api.nvidia.com/v1` 和
`nvidia/nemotron-3.5-lightning-30b-a3b`。此模型的官方页面提供免费端点；可用性与免费额度
仍以运行时平台状态为准，测试不会自动切换模型或付费服务。
每轮最多7次请求（4次正常生成尝试、3次 invalid-model），每次最多32个输出 tokens、
关闭 thinking 与自动重试；超时保留未知用量并令本轮失败，不把它计为零或自动补发。

GitHub Actions 的 `nvidia_browser` 输入默认 false。只有从默认分支手动选择后才启动
`browser-platform-nvidia`，使用 `nvidia-e2e` environment 的 `NVIDIA_API_KEY` secret；
显式启用但没有 key 会在部署之前失败。建议为该 environment 配置维护者批准及默认分支限制。
固定平台镜像需要仓库的 GHCR package 读取权限，因此完整 fake 页面 job 运行于仓库内 PR；
fork PR 仍执行不需要 secrets 的 collector 与 API-fixture 页面门禁，其通过不代表完整平台闭环。
新增 workflow 配置本身不等于托管 CI 已运行，须查看具体 job 及保留的报告。

### Real-model UI acceptance

Use the [frontend UI quality guide](../../../docs/frontend-ui-quality-guide.md)
for display semantics, localization, and browser evidence requirements. For a
real-model UI check, preserve the evidence chain below.

The built-in NVIDIA runner currently covers **Chat only**:
`browser_platform.py` selects `live_python.py` and
`../../langfuse/tests/js/run_live_provider.mjs`. Their live requests exercise
chat generation and provider errors. The RAG suite's deterministic embeddings
are not real-provider embedding coverage. Real-provider embedding UI acceptance
requires a separately recorded embedding run; it is not supplied by this
built-in matrix.

1. **Provider response.** Record each actual attempt, model, operation, HTTP
   result, response, and reported usage. A model appearing in a catalog does not
   guarantee that the account can call it. Retain failed attempts when testing
   another model. Missing usage stays unknown, including unreported embedding
   output usage. Keep calls with missing measurements in usage-coverage checks;
   do not report missing measurements as zero.
2. **Original SDK export.** Capture the SDK-generated spans and original
   protobuf before forwarding, using `sdk_transport.py`. Record whether the
   request used an automatic integration or explicit observation construction.
   Manually naming an observation `chat <model>` or `embeddings <model>` proves
   that display case only; it does not establish an auto-instrumentor's default
   name. Preserve custom names and the original payload.
3. **Real API readback.** Compare the same trace and span IDs, parents, names,
   timestamps, status, model/operation attributes, IO, and usage with the saved
   provider and SDK evidence. For embeddings, compare the returned vector as
   well. An accepted ingestion response is not proof that queries are complete.
   If readback is initially empty or incomplete, use bounded read-only polling
   for the original IDs and time window. Do not repeat inference or resend
   telemetry to make the readback pass; retain a timeout as a failed check.
4. **Same-ID browser check.** Open those trace IDs through the current frontend
   and real backend, without API route mocks. Verify operation titles, neutral
   model badges, original-name tooltips, search where available, and unknown
   versus reported usage. Record the actual frontend source revision and dirty
   diff, backend source/mounts and image digests, and available UI/data-source
   paths. A Langfuse SDK trace served by SigNoz verifies Native UI, not the
   legacy LF UI. Keep fixture regression results separate from this evidence.
