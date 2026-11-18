# 诊断模块 · 技术设计

> AI Vision「诊断」模块端到端设计。后端 AgentLens（独立 FastAPI，独立部署），前端经 Django 代理转发。
> 用户操作见[快速入门](quick-start.md)，后端契约以 [AgentLens API](../../../agentlens/docs/api.md) 和[诊断器开发指南](../../../agentlens/docs/diagnoser/README.md)为准；运维排查见[诊断联调](integration-checklist.md)。

## 1. 定位

让用户查看 agent 质量看板、下钻 BadCase、管理诊断器 / Job / Dataset。

内置 Rule、Core、专项检查和 CUSTOM 使用同一套批次数据：Runner 将终态 TaskStats 通过 Antcollector 写入
SigNoz，overview 和趋势只读 TaskStats；BadCase 下钻统一读取 DiagnosisRecord 列表。Task API
继续读取 OB 中的执行期统计，两者允许短暂最终一致。

诊断 Metric 使用 `good` / `bad` / `failed`；不适用或缺材料只跳过当前 Trace 的当前诊断器（SKIP），
不为该检查生成 Metric/Record，也不归一为 GOOD；其他检查仍可产出结果和记录。
`failed` 不进入 GOOD/BAD 通过比例，但通过 `failed_n` 单独展示；质量检查存在失败、跳过或覆盖不完整时，当前批次的质量分数不可用。

## 2. 质量分与运行信号

当前 TaskStats v3 保存本次执行固定的诊断器名称、版本、指标、权重和 `score_role`，质量看板按这些快照解释结果，不用当前定义改写旧批次。

- **质量检查（`quality`）**：参与质量分数。当前默认 Rule 中只有「修改后验证」属于质量检查；Core 检查也属于质量检查。
- **运行信号（`signal`）**：展示工具失败、耗时、重复调用或风险线索，不因信号异常扣除质量分数。其余七项默认 Rule 属于运行信号。
- **分类**：`outcome`（结果质量）、`process`（过程质量）、`safety`（安全合规）、`system`（系统性能）。分类用于组织检查，是否计分由检查的 `score_role` 决定。
- **分数**：有效指标按 `good / (good + bad)` 计算通过比例；诊断器内汇总指标，分类内按固定权重汇总质量检查（权重总和为零时取平均），全局取可评分分类的平均，再换算为 0–100。

只有取数、投影、样本选择与质量检查覆盖满足当前统计合同，才显示完整质量分数。0 是有效结果；质量检查缺材料、SKIP 或 FAILED，或没有质量检查、取数覆盖不完整时返回 `null`，界面显示不可用，不能转为 0。运行信号的 SKIP 不会单独使完整质量检查的分数不可用。取数完整不代表应用所有动作都被采集，也不证明任务完成。

前端 `overview/diagnosisStats.ts` 从 `/stats` 返回的 `categories` 和 `tasks[]` 生成概览与趋势。历史非快照统计保留查看，但不作为当前质量分数；具体判断范围见[快速入门](quick-start.md)和[通用 Core](../../../agentlens/docs/diagnoser/llm/general-core.md)。

## 3. 架构

```
浏览器(vfe) ──会话 Cookie + space_id（写请求校验 CSRF）──▶ Django 代理(agentlens_proxy.py)
                                  │ 鉴权(SpaceMember) + owner_id 可信覆盖 + 审计
                                  ▼ x-antmonitor-token(env 注入,前端不可见)
                              AgentLens(FastAPI, OB 存储, master/worker 调度)
                                  │ DiagnosisRecord 写 Antcollector(OTLP) → SigNoz(只增)
```

代理层职责：藏 token、补用户/space 鉴权（AgentLens 只有「调用方」概念）、统一错误码。

## 4. 鉴权

诊断查询要求空间成员具有 `diagnosis.read`；诊断器管理、结果反馈和试运行继续使用
`diagnosis.manage` / `diagnosis.execute`。运维 system/cluster 端点限 Admin。

具有 `diagnosis.manage` 的个人空间所有者、团队空间管理员和开发者可在有权限的 Agent 下新建和编辑
自定义 Rule 或 LLM 诊断器。复制预置规则时，原规则和副本必须属于当前空间和 Agent。
诊断器详情接口在收到 `agent_uuid` 时校验完整 Scope。个人空间旧客户端编辑或删除时未传该参数的，
服务端会根据已保存诊断器的 Agent 校验采集主体归属。

Job / Dataset 的增删改、Job 手动触发、Task 取消和删除要求 `diagnosis.job.manage`。
团队空间的管理员、开发者具有此权限，普通成员只读；个人空间不授予此权限。
限制由 Django 强制执行，前端根据空间返回的 `permissions` 禁用对应操作。

## 5. API（前端均经 `/api/v1/spaces/{spaceId}/diagnose/...` 代理）

质量统计通过 `job_uuid` 定位，以 `start_time/end_time` 选择批次时间范围，可用 `task_uuid` 精确锚定批次。当前前端在需个人归属过滤的场景携带 `owner_id`；空间、Agent 和用户权限由服务端核验。

| 函数 | 路由 | 说明 |
|---|---|---|
| `fetchDiagnosisStats` | `GET .../stats` | 固定批次定义、分类、质量分数和覆盖；前端据此生成概览与趋势 |
| `getDiagnosisRunOptions` / `createDiagnosisRun` | `GET .../runs/options`、`POST .../runs` | 对 1–10 条显式 Trace 运行一个已保存诊断器或专项检查；旧 `quick-runs` 接口保留兼容 |
| `listRecords` / `getRecord` / `submitFeedback` | `.../records[/{uuid}][/feedback]` | BadCase 记录 |
| `listDiagnosers` / `createDiagnoser` / `updateDiagnoser` / `deleteDiagnoser` | `.../diagnosers[/{uuid}]` | 诊断器；仍被同 Scope Job 引用时不能删除 |
| `copyBuiltinDiagnoser` | `POST .../diagnosers/{uuid}/copy?agent_uuid={agent_uuid}` | 接收副本名称 `name`，复制当前 Scope 的预置 Rule |
| `listJobs` / `createJob` / `updateJob` / `deleteJob` / `triggerJob` | `.../jobs[/{uuid}][/trigger]` | Job |
| `listDatasets` / `createDataset` / `updateDataset` / `deleteDataset` | `.../datasets[/{uuid}]` | Dataset |
| `listTasks` / `getTask` / `cancelTask` | `.../tasks[/{uuid}][/cancel]` | Task |
| — | `POST .../model-connection/{uuid}/test` | 使用空间级模型连接执行连通性测试 |
| — | `POST .../diagnosers/llm/trial` | 自定义 LLM 多指标试运行 |
| — | `POST .../diagnosers/rule/trial` | 自定义 Rule 多指标试运行 |
| — | `GET .../system/{health,cluster/*}` | Admin-only 运维端点 |

> AgentLens 不再提供 metric/category 专用 traces 路由；分类和指标下钻统一组合
> `listRecords` 的 `job_uuid/task_uuid/category/diagnoser_uuid/verdict/owner_id` 条件。

### 个人空间内置诊断与资源 Scope

个人空间的 Job 列表使用当前 `(space_uuid, agent_uuid)`，不再依赖全局共享 Job。
当部署者启用 `AIVISION_PERSONAL_DIAGNOSIS_ENABLED` 时，支持内置诊断的 Claude Code 兼容通道、Codex 和 DeepSeek Harness（DSH）接入完成后
自动配置一条「内置诊断」。此开关默认关闭。启用后使用以下配置。

- 每小时运行（`0 * * * *`），每批最多 20000 条，取数范围 1 小时，数据延迟 3 小时。
  相邻批次连续覆盖：18:00 取 `[14:00,15:00)`，19:00 取 `[15:00,16:00)`。
- 选择反复编辑线索、重复调用线索、工具执行结果、运行耗时、风险操作提醒、凭据暴露线索。
- Dataset 复用观测数据源和个人采集身份过滤，不把其他空间或用户的数据纳入诊断。
- Job 和 Dataset 的 `spec.managed_by=aivision.personal_diagnosis` 标识平台管理；页面展示
  「平台内置」和创建人 AI Vision。个人用户可查看详情、执行历史、质量看板和结果。

初始化通过 AgentLens HTTP API 创建同一 Scope 的规则、Dataset、Job，使用现有调度器，
不新增数据表或独立调度服务。Agent 安装完成后初始化，移除后停用，加回并完成接入后恢复。
列表读取会补齐遗漏的任务；初始化失败显示错误及重试入口。重复调用复用同一任务，
中途失败后复用已创建的 Dataset。历史自建任务及其执行记录保留，以只读方式展示。

升级时在后端运行 `python manage.py sync_personal_diagnosis`，为已有个人 Agent 补齐任务。
命令可重复执行；同名用户资源不会被覆盖，冲突或数据源缺失会报出对应 Agent。
列表读取和该命令都会把已有内置小时任务的旧 `2m` 窗口升级为 `1h`，保留空间、用户过滤、
其他取数参数和历史任务。仅修改带平台管理标识且归属相同的 Dataset；已遗漏的历史数据需要补跑。
AgentLens 配置 `AIVISION_INGEST_TOKEN_URL` 后，通过服务密钥调用
`POST /api/internal/agentlens/ingest-tokens`，请求体为 `space_uuid` 和 `agent_uuid`。
后端校验服务身份、Agent 归属与采集身份，签发最长 5 分钟的空间凭证。AgentLens 自动获取和续期，
按任务的 Space、Agent 分批向网关回写诊断记录与统计，个人和团队均无需手工配置 SpaceKey。
质量看板继续通过 AgentLens 查询遥测存储；AI Vision 不代转诊断正文。部署验收见
[诊断联调](integration-checklist.md)。

Diagnoser 列表仍兼容 `include_user_shared=true`；共享诊断器仅供只读发现，Job 只能引用
与自己同属 `(space_uuid, agent_uuid)` 的 Diagnoser 和 Dataset。团队空间仍按成员权限自建任务。

## 数据集来源

诊断数据集由后端解析为 `type="signoz"`，具体字段见 [AgentLens API](../../../agentlens/docs/api.md)。空间、用户和 Agent 过滤由服务端绑定；浏览器不能注入回放数据源。Langfuse 保持观测能力，不作为诊断数据源。

## 6. 取数模型（R1）

- **时间窗**：复用全局 `TimeFilterControl`（preset + 自定义 + 自动刷新），转 `YYYY-MM-DD HH:MM:SS` 传后端。概览取窗口内最新 task，趋势列窗口内所有 task 点。
- **Job 选择**：`JobSelector` 选择当前 Agent 的 Job；统计请求明确携带已解析的 `job_uuid`。从轨迹运行诊断会创建独立的 Dataset、Job 和 Task，无需用户先手工配置。
- **批次锚定**：点 L0 趋势图某次点 → `anchorTaskUuid` → 概览/列表按那次 task 取。无锚定 = 窗口内最新。
- 全局刷新（`refreshTick`）串进所有 queryKey，右上角刷新/自动刷新触发重取。

## 7. 自定义诊断器

从空白新建诊断器时，客户端提交 `type=custom` 与 `mode=llm|rule`。AgentLens 按
`type + mode` 固定路由到 `llm_custom` 或 `rule_custom`。

所有诊断器必须声明非空 `metrics[]`。每个指标包含稳定 `name` 和展示说明 `desc`；LLM 和 Rule 的创建、编辑与试运行均按完整多指标数组读写，不能只保留首个指标。不存在通用的 `threshold` 或 `passed` 字段合同。

自定义 LLM spec 使用 `prompt`、`metrics[]`、`model_connection_uuid`、`runtime_type`、场景、分类和权重。模型由当前空间的 ModelConnection 提供，API Key 不通过诊断器 API 返回；CUSTOM 试运行和执行要求连接为 `available`。Prompt 支持 `{{var}}`，后端按 runtime 对变量进行校验。

自定义 Rule spec 使用 `code` 与 `metrics[]`。源码定义唯一顶层入口 `evaluate(trace)`，读取输入中未改写的 `spans`，返回与声明指标严格一一对应的 `metrics[]`；每项使用 `name/verdict/reason/evidence`，用户 verdict 只能为 `good` 或 `bad`。底层使用 Python 执行，创建、编辑和试运行采用相同的 AST 和结果校验。

试运行响应包含 `verdict`、`summary`、逐指标 `metrics[]`、`execution` 和 `skipped/skip_reason` 等状态。外层失败和未判定必须保留；不能将无指标或未知状态显示为通过。正式运行的结果和记录由 Runner 统一构造。

LLM 试运行的代理等待预算按最多三次模型请求计算：`3 × request_timeout_sec + 3 + 30`
秒，包含重试等待与转发余量。单次请求默认 60 秒，可配置为大于 0、不超过 300 的有限非布尔
数值；非法值在转发前返回 422。已保存的专项配置由 AgentLens 解析，专项 Trial 采用最大
933 秒代理预算。其他管理接口和 Rule Trial 仍使用 15 秒。
这控制 I/O 等待，不是整个执行过程的绝对时间上限。代理等待超时返回 HTTP 504，后台模型请求
可能仍在执行；Django 不自动重试，也不承诺取消。Trial 不创建可供查询的 Task 或 Record，
请避免立即重复试运行。模型服务自身返回的错误仍保留原诊断失败结果。

内置定义由代码拥有名称、版本、Prompt 和指标。八项默认 Rule 中，「工具执行结果」可以调整
工具成功率下限，「运行耗时」可以调整耗时预算。PATCH 将对应的 `tool_success_min_rate` 或
`latency_budget_ms` 设为 `null` 时恢复平台默认判定。专项检查使用各自的有限政策，见
[专项指南](../../../agentlens/docs/diagnoser/specialists.md)。

「复制为自定义」生成同 Space 和 Agent 下的新诊断器。副本初始保留原规则版本、判定逻辑、配置阈值、
指标、跳过条件和证据。副本可从 Trace 手动运行，也可转为用户维护的 Python 规则，使用自定义 Rule
的输入和结果合同。现有 Job 继续引用原诊断器。合同细节见[开发指南](../../../agentlens/docs/diagnoser/README.md)
和[当前 Rule 指南](../../../agentlens/docs/diagnoser/rule/README.md)。

## 8. 前端结构

```
vfe/src/components/diagnose/
├── diagnoseApi.ts            # 稳定聚合入口，重导出按领域拆分的 API
├── recordApi.ts              # 诊断记录与反馈
├── qualityApi.ts             # 质量概览、趋势与指标轨迹
├── diagnoserApi.ts           # 诊断器与可用变量
├── jobApi.ts                 # Job、Task 与任务专属 Dataset
├── traceProjectionApi.ts     # Trace Projection 查询与 DTO
├── diagnosisRunApi.ts        # 已保存诊断器选项与运行请求
├── TraceDiagnosisDialog.tsx  # 轨迹列表与详情共用的运行入口
├── TraceDiagnosisPanel.tsx   # 已选 Trace、诊断器选择与请求恢复
├── QuickDiagnosisResult.tsx  # 批次覆盖、结果和复测
├── diagnoseAdapters.ts       # DTO→UI 转换
├── types.ts                  # DTO(snake_case) + UI 类型(camelCase)
├── badcase/
│   ├── QualityOverviewView.tsx   # 批次概览、分类与趋势
│   ├── BadCaseListView.tsx       # BadCase 列表
│   └── overview/                 # diagnosisStats / qualityDisplay / CategoryViews
│       / DiagnoserScoreSummary / CategoryTraceTable / L0TrendChart / overviewFormatters
├── badcase-detail/           # 详情编排、可复用详情区、反馈与 Graph/步骤回放
├── diagnoser/                # 卡片、LLM/Rule 多指标编辑、试运行、模型与有限政策配置
├── job/                      # JobDrawer、展示区、表单映射与任务专属 Dataset 提交
├── common/                   # JobSelector
└── trace-integration/        # BadCaseTag / DiagnosisBanner / ReportBadCaseModal（接入 TraceDetailPanel）
vfe/src/pages/diagnose/      # BadCasePage / DiagnoserPage / JobPage
```

DTO→UI 转换集中在 `diagnoseAdapters.ts`（`toRecord`/`toDiagnoser`/`toTaskRecord`）。
`Diagnoser` 只描述诊断器本身，不派生单一 Job 运行状态；运行与启停状态属于 Job/Task。

## 9. 响应与错误码

成功 `{code:0, message:"success", data:{...}}`。失败 agentlens 4xx/5xx 透传为 `{code, message, data:{agentlens_code, http_status}}`：not_found→4040、conflict→4090、forbidden→4030、validation→4220、external_unavailable→5030、其它→5001。

## 10. 审计

logger `diagnose.audit`（WARNING，落 ELK/Loki）。action：`diagnoser.create` / `job.{create,update,trigger,delete}` / `dataset.{create,update,delete}` / `system.*.read`。

## 11. 已知限制

- 小样本诊断只描述所选检查的观测结果；分数与真实准确率不同，Core/专项检查的人工校准仍未完成。
- 人工上报 BadCase（ReportBadCaseModal）UI 在、后端未实现。
- 诊断粒度为 Trace，session/project 级诊断未实现。历史记录保留，但不以现役定义重新解释旧结果。
