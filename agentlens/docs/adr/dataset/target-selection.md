---
object_key: dataset
topic: target-selection
title: Dataset 目标选择
---

# Dataset 目标选择

## dataset-target-selection@20260823-0234

**Dataset Selector 在诊断分母前选择共享目标**

```yaml
record_id: dataset/target-selection@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - trace-retrieval
  - executor
  - task-stats
  - diagnosis-record
canonical_doc: docs/architecture.md
```

### 背景

样本门禁决定本次 Task 的诊断对象集合，应由 Dataset 统一定义，而不是由每个 Diagnoser 各自筛样。

### 决策

- 在旧版 Trace 查询源中，可选 Selector 在第一阶段去重后、`source.total` 计数前运行。
- 选中目标是所有绑定 Diagnoser 的共享样本；排除、无效和未解析目标不产生诊断结果，也不进入分母。
- Selector 只接收身份字段和自身声明字段，可附加仅用于记录持久化的 target metadata。

### 保持不变

Selector 不产生 GOOD、BAD 或 FAILED，也不向 Executor、Projection 或 Prompt 注入配置和实验字段。

### 影响与风险

门禁直接改变 TaskStats 分母和可见明细集合；Selector 失败策略必须在 Dataset 主题内评审。

### 验证

由 Dataset 路由、Selector、Runner 两阶段查询和 TaskStats 测试验证共享样本及分母。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。

## dataset-target-selection@20260916-1130

**移除退役后端专属 Selector，保留共享选择合同**

```yaml
record_id: dataset/target-selection@20260916-1130
decision_status: superseded
implementation_status: retired
proposed_at: 2026-09-16T11:30+08:00
decided_at: 2026-09-16T11:30+08:00
implemented_at: 2026-09-16T11:30+08:00
supersedes:
  - dataset/target-selection@20260823-0234
affected_objects:
  - task-runner
canonical_doc: docs/architecture.md
```

### 背景

依赖退役外部数据源的选择器不能在当前部署中提供等价结果。

### 决策

退役选择器不再注册，也不提供创建入口。含旧 Selector 的数据集必须在升级前处理，迁移不静默丢弃筛选。
保留通用 Selector 接口：在 phase1 去重后、`source.total` 计数前选出共享目标；配置不进入 Executor 或 Prompt。

### 保持不变

预评估只影响当前诊断器；历史记录中的选择元数据按保存事实读取。

### 影响与风险

旧选择器配置不再可执行；未知类型由服务端拒绝。

### 验证

数据集配置验证、迁移阻塞测试、Runner 预评估及历史记录读取回归。

### 实施说明

已实施。当前行为以本记录及对应文档为准。

## dataset-target-selection@20261009-2200

**保留 Selector 合同并增加可信固定 Trace 选择**

```yaml
record_id: dataset/target-selection@20261009-2200
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T22:00+08:00
decided_at: 2026-10-09T22:00+08:00
implemented_at: 2026-10-09T22:00+08:00
supersedes:
  - dataset/target-selection@20260916-1130
related_records:
  - job/quick-request-receipt@20261009-2200
  - task/execution-snapshot@20261009-1945
affected_objects:
  - trace-retrieval
  - task-runner
canonical_doc: docs/api.md
```

### 背景

个人首跑必须处理用户授权的精确既有 Trace 集合；旧相对时间窗可能漏掉其中的样本或扫描未选择数据。

### 决策

退役 Selector 仍不注册、不提供入口。未知旧 Selector 配置仍拒绝而不静默丢弃。
通用 Selector 接口保留，legacy 在 phase1 去重后、source.total 计数前选出共享目标；配置不进 Prompt。

可信 quick Dataset 可有 `trace_selection={mode:explicit,trace_ids:[...]}`：1–10 个 distinct 合法 ID，必须
与 Task frozen execution policy.fixed_trace_ids 相等。查询绑定所有 ID 与服务端 Space/subject/product scope
值，严格校验表/列名；不依赖相对时间窗，读取该集合全部保留 spans。预检和 Runner 共用同一 exact query，
返回集合不相等则整单失败。固定集合不叠加可二次筛样的 Selector。无该字段的 legacy window 行为不变。

### 保持不变

预评估只影响当前诊断器；材料不足的 SKIP、读取/投影/Judge 失败的 FAILED、历史 model/exp_ver_id 读取
与 TaskStats 的质量/信号分离保持。固定 Trace 不代表内容适用或质量通过。

### 影响与风险

固定 ID 查询涵盖保留数据，样本数量有界但单 Trace spans 仍取完整；底层查询失败不能冒充缺材料。
复测只换授权选择/资源绑定，检查条件不变；原 Task 与结果不可修改。

### 验证

参数绑定与 scope 缺失拒绝、完整选择集合、legacy 路由兼容离线测试；真实 scope collision/两引擎查询由
控制器提供隔离数据验证，不以 in-memory stub 代替 SQL 证明。

### 实施说明

TraceStore 增加共享 exact reader，Runner 仅在有 explicit selection 时走该分支；快照保留相同选择。
