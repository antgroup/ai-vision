---
object_key: projection
topic: projector-architecture
title: Projection Projector 架构
---

# Projection Projector 架构

## projection-projector-architecture@20260827-1718

**以显式清单和运行时归属组织 Projector**

```yaml
record_id: projection/projector-architecture@20260827-1718
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-27T17:18+08:00
decided_at: 2026-08-27T17:18+08:00
implemented_at: 2026-08-27T23:06+08:00
supersedes: []
affected_objects:
  - executor
  - trace
canonical_doc: docs/projection/trace-contract.md
```

### 背景

依赖装饰器和 import 副作用的注册方式，不便于从目录与清单审查运行时路由和实现归属。

### 决策

- Registry 使用不可变 `ProjectorDefinition` 和显式 `BUILTIN_PROJECTORS` 清单，不通过 import 副作用注册。
  定义包含名称、实现类型、canonical runtime、runtime aliases 和 builtin scenarios；alias 不重复实现。
- 初始化校验 canonical runtime 与 alias 唯一路由、定义和实现一致、实现类型有效、变量合法，以及单个
  Projector 的普通变量与图片变量不重名；不同 Projector 可以声明同名变量。
- 共享层负责合同、模型、Registry、服务、序列化和表达式解析；各 runtime 的实现按所属 Projector 组织。
  内部采用上下文、语义事实提取和 Projection 组装，不建立跨 Projector 的通用猜测框架。
- 可选字段缺失保持空值；未预期异常、内部不变量破坏或无有效运行时上下文使 Projection 不可用。
- 公开 Projection 门面保持兼容；仓库内深层导入随实现布局迁移，不额外保留旧深层转发模块。

### 保持不变

原始 spans、公开字段、Prompt 变量和序列化合同保持；Projector 不负责诊断结论或质量统计。

### 影响与风险

显式清单使路由可审查；仓库外直接依赖旧深层模块可能导入失败，受支持的公开门面不受影响。

### 验证

验证 Registry 冲突、runtime alias 路由、类型与序列化、变量范围和旧深层路径清理。

### 实施说明

显式清单与初始化校验、运行时实现归属、共享合同和内部导入迁移已落地。

## projection-projector-architecture@20261009-1345

**Projection Projector 架构：完整材料与独立跳过**

```yaml
record_id: projection/projector-architecture@20261009-1345
decision_status: superseded
implementation_status: implemented
proposed_at: 2026-10-09T13:45+08:00
decided_at: 2026-10-09T13:45+08:00
implemented_at: 2026-10-09T13:45+08:00
supersedes:
  - projection/projector-architecture@20260827-1718
related_records:
  - projection/runtime-facts@20261009-1345
affected_objects:
  - executor
  - trace
canonical_doc: docs/projection/trace-contract.md
```

### 背景

已接受的轻量诊断设计要求材料不足、不适用与执行故障分别呈现，并保留冻结任务和历史事实。

### 决策

- 显式 BUILTIN_PROJECTORS 清单拥有 family/runtime aliases/scenarios，并校验路由唯一、实现一致、变量合法与文本/图片根不重叠。
- Coding 与 Framework 的映射分别维护；Framework canonical langchain，aliases langgraph/deepagents/deep-agents，仅声明 common 场景。未知 runtime 不使用其它 family 的 fallback。
- 共享层保留 contracts/models/registry/service/serialization/expression；SDK 消息解析只封装已知 GenAI、Langfuse、LangChain 与 Relay shape，不构造通用推测 FieldExtractor。
- Coding 的一 Trace 一 CHAT 合同保留；多个 CHAT 不静默拼接。Framework 要求唯一原始 request root，并避免将工具下子 Agent generation 作为父 Agent 最终输出。
- Coding/Framework 即使缺文本也保留合法投影，由诊断器完整材料门禁决定适用性；解析错误或结构歧义仍使投影不可用。
- Projector 内部使用副本适配 SDK 字段，Trace.spans 不改写；源字段映射与限制维护在各 family 文档。

### 保持不变

原始 Trace spans 不改写；Metric verdict 仍只有 good/bad/failed，历史记录不重算。

### 影响与风险

新执行语义使用 Executor revision 1.1.0；旧 revision 的排队任务需兼容 Worker 或先排空，当前恢复入口明确拒绝。已知 schema 的安全历史定义仍可只读显示。

### 验证

离线来源正例、缺失与错误反例、Formal/Trial、多诊断器局部及全部跳过、隐私遮蔽与可见异常回归；不以离线协议测试代表真实 Judge 准确性。

### 实施说明

共享门禁、来源模型、SDK 投影和 Trial 外层合同已实现；具体边界与验证见 canonical_doc。

## projection-projector-architecture@20261010-0350

**保留现役通用投影与原始 CUSTOM 路径**

```yaml
record_id: projection/projector-architecture@20261010-0350
decision_status: superseded
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes:
  - projection/projector-architecture@20261009-1345
related_records:
  - projection/runtime-facts@20261010-0350
  - projection/prompt-variables@20261010-0350
  - projection/image-attachments@20261010-0350
  - task/execution-snapshot@20261010-0350
affected_objects:
  - diagnoser
  - executor
  - trace-retrieval
  - diagnosis-record
canonical_doc: docs/projection/trace-contract.md
```

### 背景

退役映射需从注册和消费者退出；未知 runtime 不能套用其它 family 的 fallback。

### 决策

- 显式清单与 family、aliases、scenarios、公开变量校验保留；活动 family 仅 Coding/Framework。
  Framework 的已声明 SDK aliases 仅使用 common 场景；Common 是 scene，不是 runtime fallback。
- Coding 一 Trace 一个确认 CHAT anchor，多 anchor 或结构歧义不静默选择；Framework 要求唯一原始请求 root，
  工具下子 Agent generation 不提升为父 final。缺文本可保留结构投影，由每个 Diagnoser 判断材料；结构故障不冒充材料不足。
- 共享合同、Registry、服务、序列化与表达式解析以及已知 SDK 映射保留，不猜任意字段。适配使用副本，
  不修改原始 spans；投影不承载裁决，序列化单向有界。
- 退役 Projector 不再注册或用于新投影；对应与未知 runtime 保留原 runtime 字符串、原始 spans 和 CUSTOM raw 表达式。
  需要 typed facts 的内置或专项显示 mapping unavailable，不套用替代 family。
- 旧记录按已保存 JSON 读取，不重投影，不要求退役类型；历史展示保留。共享 factory 使用现役通用模型。

### 保持不变

原始 Trace 身份、Coding/Framework 来源事实、scope replay、usage 的零值/未知值和默认 Core 集合保持。

### 影响与风险

依赖退役变量的 CUSTOM 需人工检查；原始读取不承诺旧语义变量可复现。物理发布受完整 worker-pool 门禁约束。

### 验证

验证注册与导出退出、未知 runtime raw CUSTOM、SDK aliases、旧 JSON、usage、取消、恢复和序列化。

### 实施说明

目标合同由后续显式 SDK 路由记录和当前 Trace 契约维护。

## projection-projector-architecture@20261010-1409

**显式接通 Langfuse SDK runtime 与既有 Framework 事实映射**

```yaml
record_id: projection/projector-architecture@20261010-1409
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T14:09+08:00
decided_at: 2026-10-10T14:12+08:00
implemented_at: null
supersedes:
  - projection/projector-architecture@20261010-0350
related_records:
  - projection/runtime-facts@20261010-0350
  - projection/prompt-variables@20261010-0350
  - projection/image-attachments@20261010-0350
  - task/execution-snapshot@20261010-0350
  - diagnoser/specialist-material-policy@20261010-0350
affected_objects:
  - diagnoser
  - executor
  - trace-retrieval
  - diagnosis-record
canonical_doc: docs/projection/trace-contract.md
```

### 背景

平台生产接入目录把官方 Langfuse SDK 集成定义为 product=langfuse、runtime=langfuse-sdk。
Django 的可信 Dataset builder 从 AgentTypeConfig 读取这个 runtime，并原样送往 AgentLens catalog 与
Trial。Stage C 的实际平台验证已观察到该接入可以上报并读回原始 SDK spans，但专项 catalog 的五项
可实现检查全部显示 runtime_mapping_unavailable。

当前 Framework Projector 已拥有已知 Langfuse trace/observation、GenAI、LangChain 与 Relay 消息载体的
事实映射，显式 registry 却只有 langchain、langgraph、deepagents 与 deep-agents。因此缺口是生产 SDK
runtime 尚未声明到既有 family 的路由，不能通过伪改 Dataset runtime、宽松布尔门禁或未知 runtime
fallback 解决。该记录完整取代本主题 0350 目标；本轮新增目标仅是 langfuse-sdk 的显式 alias。

### 决策

- 继续以不可变 ProjectorDefinition 与显式 BUILTIN_PROJECTORS 清单拥有 family、canonical runtime、
  runtime aliases、builtin scenarios 与 public variables。初始化校验路由唯一、定义与实现一致、实现
  类型有效、变量合法，以及单 family 文本/图片根不重叠；不使用装饰器或 import 副作用注册。
- 活动 family 仍仅 Coding 与 Framework。Coding canonical cc 及其既有 aliases、common/coding 场景
  不变。Framework canonical langchain，aliases 明确为 langgraph、deepagents、deep-agents、
  langfuse-sdk，且仅声明 common 场景。Common 是 scene，不是 runtime fallback。langfuse-sdk 使用
  同一个 FrameworkProjector 实例与既有事实/变量合同，不建立重复实现，不改变 canonical runtime。
- langfuse-sdk 是生产接入目录已有的确切 SDK runtime，不是“任意使用 Langfuse 存储的 Agent”。
  不依据 product 名称、span 内容、框架 tags 或成功状态猜测 runtime；Django 继续按 AgentTypeConfig
  构造可信 Dataset，catalog、Trial、Job freeze/restore 与 Runner 均通过统一 registry 解析该 alias。
  保留外层 Trace.runtime_type=langfuse-sdk，不写成 langchain，不改已保存资源或原始 spans。
- Coding 一 Trace 一个确认 CHAT anchor；多 anchor/结构歧义不拆分、不静默取第一。Framework 要求
  唯一原始请求 root，保留明确 application 请求/输出入口、已知 SDK 消息角色及父 generation 来源
  边界；tool 下子 Agent generation 不提升为父 final。缺文本仍可形成结构投影，每 Diagnoser 独立
  判断材料；结构或解码 fault 不冒充材料不足。
- 共享 contracts/models/registry/service/serialization/expression 与已知 GenAI、Langfuse、LangChain、
  Relay shape 映射保留，不建立任意 FieldExtractor 或字段猜测。SDK adapter 使用副本，不修改 raw
  spans；SourceReference 继续指向原始唯一 span/实际 leaf 或 ingest 已记录的原字段 alias，Trace ID、
  Span ID、parent、clock、call identity 与未知字段原样保留。投影不承载裁决，serialization 单向有界。
- 新 alias 只声明既有映射可供选择，不证明每条 Trace 采集完整或适合每项检查。保持 runtime-facts 与
  specialist-material-policy 的当前请求/final、真实同容器历史、必要非文本、遮蔽、尺寸、政策、模型与
  引用门禁；完整查询不等于采集完整。合法缺失/遮蔽/不支持/超界零 Judge SKIP，query/source/structure/
  decode/projection/config/Judge/protocol fault FAILED；不会通过 runtime alias 提升未知材料为 GOOD。
- 退役 Projector 不再注册或用于新投影。对应与未知 runtime 保留原 runtime 字符串、raw spans 和 CUSTOM
  raw 表达式；需要 typed facts 的 BUILTIN/Specialist 显示 mapping unavailable，不套 replacement family。
  signoz 与 generic unknown 均不加入 Framework aliases；其它接入缺口另行记录与评审。
- 旧 Record JSON 按已保存事实读取，不重投影，不需要退役类型；前端历史展示保持。共享 factory 使用
  现役 Projection/Coding/Framework；usage、cancel、sort 与 rescue 覆盖保留。添加 alias 不改旧资源或历史。

### 保持不变

既有 Coding/Framework aliases 的来源事实、场景、公开变量、缺失/未知边界及 raw Trace 身份不变；scope
replay、usage known zero/null、default Core 集合、专项独立身份/预算/政策/CAS、CUSTOM raw、历史读取和
所有最终用户授权合同不变。四项缺义务/主体/必须消费/可得性事实映射的专项仍不可选择，不因本 alias
变成可用。未知 runtime、退役 runtime 和 signoz 不获得新的 typed fallback。

### 影响与风险

langfuse-sdk 从无专用 mapper 变为既有 Framework mapper，目录及变量发现会显示该 family 已支持的
能力；只具有 raw SDK 载体但缺必要语义材料的 Trace 仍可能 SKIP 或 FAILED。不能把五项可选择、SDK
上报成功或 FakeJudge 结果当作完整材料、Judge 准确率或所有框架语义等价的证明。

修复属于本轮 Stage C ABI1.4 目标的接入补全。正式激活前必须刷新实际 source/build 摘要、执行所需
验证与既有完整 worker-pool gate；新增 alias 不是绕过旧 Task revision 校验、全 pool 排空或旧 build
回滚就绪的理由。若在已激活环境变更执行语义，应按 execution-snapshot 的兼容/排空合同处理。

### 备选方案

- 只让 supports_specialist_runtime 对 langfuse-sdk 返回 true：拒绝。真正 project_trace 仍无 mapper，
  会出现目录可选、执行无事实的分裂，freeze/restore 与变量发现也不一致。
- Django 将 langfuse-sdk 改写为 langchain：拒绝。改变生产接入身份，catalog 之外的 formal/legacy
  路径仍有缺口，也掩盖实际原始 runtime。
- 把 signoz、未知 runtime 或所有 Langfuse product 套 Framework：拒绝。超出本次明确 SDK alias，
  没有已接受的统一材料合同，可能让未知应用进入不适用的语义映射。

### 验证

- Registry 验证 langfuse-sdk（含既有 trim/lower 归一）唯一指向同一个 FrameworkProjector，scenarios
  仅 common，公开变量相同；保留碰撞/变量校验、旧 alias、退役 runtime、unknown、signoz 反例。
- 使用受控的原生官方 SDK 行走实际 project_trace 与 specialist material gate，不预改 runtime 或伪造
  canonical fields。断言 request/final/history 的实际原字段 path、ID、clock 与 raw spans 不变；覆盖单 root、
  多 root、缺 final、最新 reasoning/tool-only assistant、缺同容器历史、遮蔽与 malformed JSON。
- AL catalog 对该 runtime 保持九 key：五可实现项 selectable、四缺事实映射项仍不可选择。saved/draft
  Trial 与 formal freeze/restore/legacy Runner 使用相同 alias；Rule 零 Judge，合法 SKIP 零 Judge，LLM
  请求/引用/AVAILABLE 门禁不变。验证不得只断言 catalog 布尔值。
- Django 以真实 AgentTypeConfig runtime=langfuse-sdk、product=langfuse 的受控接入确认服务器 Dataset/
  catalog/Trial 原样转发 runtime，并保持真实 datasource product、Space 和本人 subject 限制。跨本人/
  Space、disabled Agent、浏览器 authoritative 材料与缺 SigNoz 诊断 source 仍拒绝。
- Root 重开真实平台 SDK 接入/Trace 读取/catalog/单条 Trial，记录新增代码的 exact source/build 与结果；
  先前源码版本的绿测试不得称为新增 alias 的证明。真实 Provider 校准独立，不据这些测试承诺准确率。

### 实施说明

已接受。显式 alias、相关回归测试与当前行为文档已落地，ADR index/check 已通过。真实平台已读取
官方 SDK 原始来源，并完成专项目录、试跑及正式任务执行；页面证据和本阶段资源清理仍在收尾。
