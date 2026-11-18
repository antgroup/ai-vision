---
object_key: diagnoser-rule
topic: test-execution
title: Test Execution Rule 诊断器
---

# Test Execution Rule 诊断器

## diagnoser-rule-test-execution@20260827-1603

**以有效变更后的结构化验证动作判定测试执行率**

```yaml
record_id: diagnoser-rule/test-execution@20260827-1603
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-27T16:03+08:00
decided_at: 2026-08-27T16:03+08:00
implemented_at: 2026-08-27T16:03+08:00
supersedes: []
affected_objects:
  - projection
  - metric-result
canonical_doc: docs/diagnoser/README.md
```

### 背景

文档、测试自身或无效编辑不应触发“代码修改后未验证”，验证也必须发生在相关变更之后并以结构化
执行事实为准，否则会系统性制造 BAD 或把历史测试文本误认为有效验证。

### 决策

- 有效变更从 Projection 的 canonical ToolAction 及共享动作分类结果中识别；FileMutation 是从同一
  ToolAction 派生的公开视图，不是规则的第二份输入事实。
- ToolAction 被归类为代码或配置写入、执行状态成功且具有可确认的内容变化时，属于待验证变更。
- 失败写入、内容相同的 no-op、仅有文件路径而缺少实际变化证据、系统临时目录产物和未被代码路径
  分类覆盖的文档修改不触发验证要求；JS/TS 还有保守的纯空白 no-op 判定。
- 多次变更只保留最后一次有效变更作为后续验证边界；变更事实必须带真实来源 span，缺失时不伪造
  步骤索引。
- 共享动作分类将测试、构建、类型或静态检查等命令归一为 ToolAction 的 `test` operation；最后有效
  变更后的任一此类 ToolAction 即可满足要求。VerificationAction 是从同一 ToolAction 派生的公开视图。
- 验证结果进入 evidence；本指标衡量验证是否执行，验证失败也算已经执行。验证发生在变更前，或仅在
  普通输出文本中提到测试，不满足要求。
- 缺失验证的 BAD finding 锚定最后有效变更 span，类型为 `missing_verification`。

### 保持不变

GOOD、免检和已执行验证的结果不生成缺失验证 finding；本主题不评价验证是否通过。

### 影响与风险

变更范围依赖代码路径和 ToolAction 分类，可能漏掉未分类的新文件或验证类型，也会把测试源码等代码
文件视为待验证变更，需要用真实 Trace 持续回归。

### 验证

由 ToolAction 投影、变更与验证分类、连续变更、时序、失败或拒绝命令以及 BAD evidence 测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## diagnoser-rule-test-execution@20261008-2311

**从原始 Shell 命令证明同一动作内最后修改后的验证顺序**

```yaml
record_id: diagnoser-rule/test-execution@20261008-2311
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-08T23:11+08:00
decided_at: 2026-10-08T23:11+08:00
implemented_at: 2026-10-08T23:13+08:00
supersedes:
  - diagnoser-rule/test-execution@20260827-1603
affected_objects:
  - projection
  - metric-result
canonical_doc: docs/diagnoser/README.md
```

### 背景

真实 Codex Trace 将 `apply_patch` heredoc、修改后回读和 `python test_app.py` 放在同一条 Bash
ToolAction 中。实际补丁成功，退出码为 0，测试输出为 2 tests / OK；公开 FileMutation 和
VerificationAction 也来自同一原始动作。仅按步骤索引严格递增会错误地判为修改后未验证。
将索引比较改为 `>=` 又无法区分先测试再修改、测试后继续修改或仅在字符串中出现测试词。

### 决策

- 有效变更从 Projection 的 canonical ToolAction 及共享动作分类结果中识别；FileMutation 是从同一
  ToolAction 派生的公开视图，不是规则的第二份输入事实。
- ToolAction 被归类为代码或配置写入、执行状态成功且具有可确认的内容变化时，属于待验证变更。
- 失败写入、内容相同的 no-op、仅有路径而缺少实际变化证据、系统临时目录产物和未被代码路径分类
  覆盖的文档修改不触发验证要求；JS/TS 继续采用保守的纯空白 no-op 判定。证据不足的写入明确免检。
- 多次变更只保留最后一次有效变更作为验证边界。跨 ToolAction 的验证仍由共享分类的 `test`
  operation 识别，且步骤索引必须严格大于最后变更索引。
- 最后一次有效变更本身是复合 Shell 命令时，可从它的原始 command 证明同一动作内的顺序。只接受
  可解析的直线命令序列：最后一条可识别修改命令之后存在真正的验证可执行命令才满足要求。识别
  修改时复用共享修改分类；识别验证时复用共享验证命令分类，同时确认可执行文件或解释器实际目标。
- 命令分段尊重引号、转义、注释及 heredoc 的准确结束边界；heredoc 正文作为数据，不参与验证匹配。
  解释器的 `-c`/`--eval` 文本、普通参数里的测试词以及 help/version、仅收集或列出测试信息的选项
  均不能证明已执行验证。完整的受支持 Shell 包装可递归解析，不据此执行命令。
- 不推测条件分支、管道、后台任务、命令替换、未知命令、未闭合引号或 heredoc 的执行顺序。可能
  跳过测试且被后续命令掩盖结果的条件链也不能证明验证。无法证明同一动作顺序时，仍可由真实后续
  ToolAction 满足验证要求；不会放宽为同索引即可通过。
- 验证在最后修改前，或测试后又出现修改且没有再次验证，均不满足要求。同一动作顺序判断仅用于
  规则，不拆分或重写 canonical ToolAction、FileMutation、VerificationAction 或 Trace 原始事实。
- 验证结果进入 evidence，并保留完整原始命令。指标衡量验证是否执行，验证失败仍算已经执行，
  其 result 与是否通过分别表达；不能从普通输出文字中推断执行事实。
- 缺失验证的 BAD finding 锚定最后有效变更的真实来源 span，类型为 `missing_verification`。
  缺少真实 span 时不伪造步骤索引或占位定位。

### 保持不变

GOOD、免检和已执行验证的结果不生成缺失验证 finding。本主题不评价验证是否通过，不改变公开
Projection 形状、工具动作身份或其他内置 Rule 的输入和判定。

### 影响与风险

同一动作内的顺序证明有意限制为可识别的 Shell 子集；复杂 Shell 即使实际完成验证，也可能无法
证明顺序并判为缺失验证。未分类的修改或验证工具仍依赖共享分类的后续扩展。允许顺序证明不能
代替原始写入成功和实际内容变化证据，也不能用仅列出的测试名称冒充执行。

### 验证

以真实原命令验证补丁、回读、测试在同一动作内通过；覆盖测试先执行、测试后再次修改、字符串、
注释、quoted executable、heredoc 边界、命令替换、条件结果掩盖和未知命令反例。help/version、
仅收集和列出测试信息使用实际形式的 usage/version 输出及退出码 0 作为负控。
信息查询边界进一步以真实 unittest subprocess 验证短选项簇及帮助长选项缩写，并用消费参数值的
`-khelp`、discover `-phelp*.py` 等正例区分标志和参数内容；不改变本记录已接受的执行与信息查询边界。
原始 public CodingProjection 离线复算保留两个 ToolAction 和真实来源 span，只用于生产规则回归，
不替代平台公共 HTTP 诊断验收。

### 实施说明

顺序证明在 Coding ToolAction 的共享命令工具中实现，由 `TestExecutionExecutor` 只对最后确认变更
调用；跨动作索引合同保持不变。实现、focused 回归及当前行为文档对应本记录。

## diagnoser-rule-test-execution@20261009-1515

**最后修改后的真实验证结果**

```yaml
record_id: diagnoser-rule/test-execution@20261009-1515
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T15:15+08:00
decided_at: 2026-10-09T15:15+08:00
implemented_at: 2026-10-09T15:15+08:00
supersedes:
  - diagnoser-rule/test-execution@20261008-2311
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

- 最后确认或不确定代码/配置修改决定验证边界；file-only 或隐藏明确编辑不能沿用早先验证。专用失败编辑、no-op、临时审计产物和文档修改保持原忽略合同；复合 Shell 失败不能证明未发生部分写入。
- 修改后缺少验证为 BAD/missing；未解决的相关验证失败为 BAD/failed。匹配同一命令的成功重跑可解决对应失败；更早成功或之后无关 lint 不能解决失败测试。
- 明确采集的 `cwd`/`workdir` 不同表示不同验证目标，不能仅因命令文字相同而解决失败。缺失目录不推断为主机当前目录，也不承诺验证完整覆盖。
- 具名结构化验证结果与普通工具传输成功分开。未知结果外层 SKIP/missing_required_evidence。单个最后执行检查或直接 && 检查链可用退出结果；测试后 true、semicolon 检查链等掩盖结果不据整体 exit 0 判通过。
- 同动作修改后检查继续使用受支持的有序 Shell 子集，不拆分 canonical ToolAction。help/version/collect/setup-only 等信息模式、字符串和 heredoc 正文不算执行验证。
- 缺失 finding 关联最后修改，失败 finding 关联实际检查；无来源保留事实但不伪造 Span。该项为八 Rule 中唯一 quality。

### 保持不变

稳定 Executor 和 metric key、原始 Trace/ToolAction 身份、共享外层 SKIP/FAILED 状态及未涉及规则合同保持不变。历史接受记录不改写；旧 Task 的名称、版本和评分角色不重解释。

### 影响与风险

Common/Coding 八项 Rule 定义版本为 `3.0.0`，执行 ABI 为 `1.2.0`。发布需显式同步受管定义，并排空旧 ABI Task 或保留兼容 Worker；观测缺失和不支持的命令子集仍可能使检查 SKIP，不能解释为安全或修复成功。

### 验证

以独立失败反例、实际 unittest 失败后真实修复通过、正常/未知/遮蔽/无定位控制和受影响回归验证。同步持久化另用显式隔离 OceanBase/seekdb fixture；真实平台/API/GUI 验收由主控单独完成，本文不宣称其已完成。

### 实施说明

已按本记录实现生产规则、版本边界和窄验证派生；准确命令、计数与待补数据库节点保留在本次交付报告。
