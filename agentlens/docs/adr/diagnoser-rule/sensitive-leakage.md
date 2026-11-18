---
object_key: diagnoser-rule
topic: sensitive-leakage
title: Sensitive Leakage Rule 诊断器
---

# Sensitive Leakage Rule 诊断器

## diagnoser-rule-sensitive-leakage@20260827-1603

**在模型可见文本中按受控预算检测可信敏感信息泄漏**

```yaml
record_id: diagnoser-rule/sensitive-leakage@20260827-1603
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-27T16:03+08:00
decided_at: 2026-08-27T16:03+08:00
implemented_at: 2026-08-27T16:03+08:00
supersedes: []
affected_objects:
  - metric-result
  - projection
  - task-runner
canonical_doc: docs/diagnoser/README.md
```

### 背景

敏感信息规则既要覆盖模型实际读取的长文本和超长单行，又要避免扫描命令字面量、测试源码、fixture
或规则自测输出形成误报，并以确定的常见路径成本处理可能很大的工具输出。

### 决策

- 规则扫描 Tool output 和用户查询等模型可见文本。Tool input 只用于判断测试、源码审计、敏感文件
  读取或网络传输上下文，不作为普通泄漏文本扫描。
- 直接读取敏感文件和网络外传信号优先于测试语境豁免；确认测试、fixture、文档或规则自测上下文时
  可跳过对应 Tool output。
- 匹配使用可泛化规则，不建立 trace、用户、仓库或固定样例白名单。
- 每个字段先对总计 64 KiB 的 head + tail 窗口和短行执行可信模式匹配；超长单行整行执行。快扫未
  命中的超长字段继续扫描原字段，因此预算是优化边界而非检测上限。
- 单字段发现首个可信 finding 后停止该字段继续扫描，但在整条 Trace 的 32 条 finding 上限内仍处理
  其他 Tool output 和用户查询字段；达到上限后不再追加 finding。
- finding 在来源 span 可用时通过 `bad_spans` 关联真实位置；来源缺失时不伪造 span，但仍可按检测
  结果判定。
- 扫描预算以代码常量实现，不通过 Diagnoser Spec 暴露可漂移配置。

### 保持不变

高危 Shell 命令归属 `high-risk-command` 主题；无法回指的疑似证据不会因优化自动改判 GOOD；规则
业务语义不由离线评测批次决定。

### 影响与风险

全量兜底消除窗口漏检但最坏情况仍需线性扫描超长字段；上下文豁免和模型可见范围依赖 Projection
正确标识，需要保持保守并由真实 Trace 回放验证。

### 验证

由 Tool input/output、user query、测试语境、敏感文件、64 KiB 边界、超长单行、多字段提前停止、
规则单测和真实 Trace 冻结回放验证。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## diagnoser-rule-sensitive-leakage@20261009-1515

**按渠道保存遮蔽的凭据暴露线索**

```yaml
record_id: diagnoser-rule/sensitive-leakage@20261009-1515
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T15:15+08:00
decided_at: 2026-10-09T15:15+08:00
implemented_at: 2026-10-09T15:15+08:00
supersedes:
  - diagnoser-rule/sensitive-leakage@20260827-1603
related_records:
  - executor/applicability@20261009-1345
affected_objects:
  - projection
  - metric-result
canonical_doc: docs/diagnoser/rule/README.md
```

### 背景

轻量诊断需区分质量裁决和事实线索，避免成功数量、缺失材料或定位信息制造假通过。当前用户已批准本记录的窄范围实施。

### 决策

- 扫描 current_request、非测试工具输出和可见 final_output；T2 当前消息边界、字面文本与来源字段合同保持不变。Tool input 仅用于已有测试/敏感文件上下文判别。
- 用户当前请求中的凭据归因 user_provided；工具输出与最终输出分别归因观察渠道，不判断 Agent 是否违规泄露。
- 保留既有强信号、上下文豁免、64 KiB 快扫/全量兜底和 32 finding 上限。excerpt 仅保存遮蔽值。finding 独立于 bad_spans；无 Span 仍 BAD 并标记 location_status=unavailable。
- 可见命中不被另一隐藏渠道覆盖；未采集正文或只有遮蔽而无命中时外层 SKIP，不声称安全通过。

### 保持不变

稳定 Executor 和 metric key、原始 Trace/ToolAction 身份、共享外层 SKIP/FAILED 状态及未涉及规则合同保持不变。历史接受记录不改写；旧 Task 的名称、版本和评分角色不重解释。

### 影响与风险

Common/Coding 八项 Rule 定义版本为 `3.0.0`，执行 ABI 为 `1.2.0`。发布需显式同步受管定义，并排空旧 ABI Task 或保留兼容 Worker；观测缺失和不支持的命令子集仍可能使检查 SKIP，不能解释为安全或修复成功。

### 验证

以独立失败反例、实际 unittest 失败后真实修复通过、正常/未知/遮蔽/无定位控制和受影响回归验证。同步持久化另用显式隔离 OceanBase/seekdb fixture；真实平台/API/GUI 验收由主控单独完成，本文不宣称其已完成。

### 实施说明

已按本记录实现生产规则、版本边界和窄验证派生；准确命令、计数与待补数据库节点保留在本次交付报告。
