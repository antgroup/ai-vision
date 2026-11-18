# 表达式引擎使用文档

## 背景

AgentLens现役为Coding和Framework两类Projector，它们将 `Trace.spans` 投影为结构化的
`Projection`，提供 `user_query`、`agent_output`、`steps` 等预定义字段，适用于已知结构的 Agent。

通用业务 Agent 的 Trace 结构可能不同，预定义语义字段无法覆盖所有场景。

**表达式引擎**解决的问题：让诊断 prompt 的编写者可以用 `{{ }}` 路径表达式，从 OTel trace 的原始 span 数据中**按需提取任意字段**，无需修改代码。

例如 `{{spans[name:example-generation].attributes.gen_ai.output.messages}}` 会读取匹配 span 的已采集模型输出。
以下字段、标识和内容均为合成示例。

## 数据格式

表达式引擎操作的是自定义诊断器输入中的原始 OTel `spans`；它们是从数据源按 trace 分组后未经
Projector 重建的 source rows。引擎保留数据源实际返回的扁平或嵌套结构，例如扁平 source row：

```json
{
  "spans": [
    {
      "span_id": "1111111111111111",
      "parent_span_id": "",
      "trace_id": "33333333333333333333333333333333",
      "name": "example-generation",
      "kind": "SPAN_KIND_CLIENT",
      "attributes.gen_ai.span.kind": "LLM",
      "attributes.gen_ai.input.messages": "[{\"role\":\"system\",\"content\":\"Example instructions\"},{\"role\":\"user\",\"content\":\"Example request\"}]",
      "attributes.gen_ai.output.messages": "[{\"role\":\"assistant\",\"content\":\"Example response\"}]",
      "attributes.gen_ai.request.model": "example-model",
      "attributes.gen_ai.usage.input_tokens": "100",
      "attributes.__meta__.spanName": "example-generation",
      "resource.service.name": "example-agent",
      "status.code": "OK",
      "status.message": ""
    },
    {
      "span_id": "2222222222222222",
      "parent_span_id": "1111111111111111",
      "trace_id": "33333333333333333333333333333333",
      "name": "example-tool",
      "attributes.gen_ai.span.kind": "TOOL",
      "attributes.gen_ai.tool.name": "example-search",
      "attributes.gen_ai.tool.call.arguments": "{\"query\":\"Example request\"}",
      "attributes.gen_ai.tool.call.result": "{\"text\":\"Example result\"}"
    },
    {
      "span_id": "4444444444444444",
      "parent_span_id": "1111111111111111",
      "trace_id": "33333333333333333333333333333333",
      "name": "example-final",
      "attributes.gen_ai.span.kind": "LLM",
      "attributes.__meta__.spanName": "example-final",
      "attributes.gen_ai.output.messages": "[{\"role\":\"assistant\",\"content\":\"Example final response\"}]"
    }
  ]
}
```

关键特性：
- OTel attribute key 带点号时，引擎在当前真实字典层级执行**最长真实键匹配**，不合成父对象
- `gen_ai.input.messages` 等值是 JSON 字符串，引擎会**自动解析**为对象/数组

---

## 表达式语法

所有表达式写在 `{{ }}` 内，由键名和操作符组成链式路径。

### 1. 键名访问

```
{{spans}}                      → 获取所有 spans 数组
{{spans[0].name}}              → 第一个 span 的 name
```

### 2. 点号键名（最长真实键匹配）

OTel 的 attribute key 包含点号（如 `gen_ai.span.kind`），引擎自动处理：

```
{{spans[0].attributes.gen_ai.span.kind}}     → "LLM"
{{spans[0].attributes.gen_ai.request.model}} → "example-model"
```

引擎在当前真实字典层级将连续路径从最长到最短组合，选择实际存在的最长键。对于扁平 source span，
`attributes.error.type` 直接读取同名完整键；对于真实嵌套对象，则命中当前层的真实父键后继续向下读取。

引擎不会根据点号合成 source span 中不存在的父对象。若 span 只有 `attributes.error.type`，则
`attributes.error.type` 可读取，而 `attributes` 返回空。同时存在 `a.b` 与 `a.b.c` 时，两条表达式
分别读取各自的真实键，互不覆盖。

### 3. 通配符 `[*]`

遍历数组中所有元素：

```
{{spans[*].name}}
```
输出（字符串自动换行拼接）：
```
example-generation
example-tool
example-final
```

### 4. 索引 `[n]`

支持正向和负向索引：

```
{{spans[0].name}}      → "example-generation"（第一个 span）
{{spans[-1].name}}     → "example-final"（最后一个 span）
{{spans[2].name}}      → "example-final"（第三个 span）
```

### 5. 过滤 `[key:value]`

按属性值筛选 span，支持嵌套属性路径：

```
{{spans[name:example-generation]}}
```
筛选 `name` 等于 `"example-generation"` 的 span。

嵌套属性过滤：
```
{{spans[attributes.gen_ai.span.kind:LLM]}}
```
筛选所有 `attributes.gen_ai.span.kind == "LLM"` 的 span。

使用 `__meta__.spanName` 过滤：
```
{{spans[attributes.__meta__.spanName:example-final]}}
```

### 6. 过滤 + 链式操作

过滤后继续提取字段：

```
{{spans[attributes.gen_ai.span.kind:LLM].name}}
```
→ 所有 LLM 类型 span 的名称列表

过滤后取索引：
```
{{spans[attributes.gen_ai.span.kind:LLM][0].name}}
```
→ 第一个 LLM span 的名称

过滤后取最后一个：
```
{{spans[attributes.gen_ai.span.kind:LLM][-1].name}}
```
→ 最后一个 LLM span 的名称

### 7. 切片与范围

Python 风格切片（左闭右开）：
```
{{spans[0:3].name}}    → 前三个 span 的名称
{{spans[-2:].name}}    → 最后两个 span 的名称
{{spans[:5].name}}     → 前五个 span 的名称
```

Datadog 风格范围（两端包含）：
```
{{spans[1,3].name}}    → 第 2~4 个 span 的名称（索引 1、2、3）
```

### 8. 隐式展开（Implicit Fan-out）

当路径中间遇到数组时，自动对每个元素展开：

```
{{spans[name:example-generation].attributes.gen_ai.input.messages.role}}
```
`gen_ai.input.messages` 解析后是 `[{role: "system", ...}, {role: "user", ...}]`，引擎自动遍历每条 message 提取 `role`：
→ `["system", "user"]`

### 9. 全量输出 `{{*}}`

输出整个数据对象的 JSON：
```
{{*}}
```

---

## 合成示例

以下示例只演示原始字段提取，不代表特定业务或诊断器的评分政策。

| 表达式 | 用途 |
|---|---|
| `{{spans[attributes.gen_ai.span.kind:LLM].attributes.gen_ai.request.model}}` | 读取模型调用声明的模型名 |
| `{{spans[attributes.gen_ai.span.kind:TOOL].attributes.gen_ai.tool.call.arguments}}` | 读取工具参数 |
| `{{spans[attributes.gen_ai.span.kind:TOOL].attributes.gen_ai.tool.call.result}}` | 读取工具结果 |
| `{{spans[attributes.gen_ai.span.kind:LLM].attributes.gen_ai.usage.total_tokens}}` | 读取已采集的 token 用量 |

Prompt 可以组合这些表达式；所引用的字段必须存在于该数据源实际返回的 spans 中。缺失字段不应被
解释为应用已正确完成任务，具体适用性和输出协议遵循对应诊断器的合同。

---

## 在系统中配置

### 1. 创建诊断任务

为任意 runtime 创建自定义 LLM 或 Python 诊断器即可使用 `spans`；未知 runtime 不会构造通用 Projector，也不需要额外的 `generic` 配置。

### 2. 编写 Prompt

在 prompt 中使用 `{{ }}` 表达式引用 trace 数据。表达式会在运行时被 `resolve_var()` 解析并替换。

### 3. 变量解析流程

```
prompt 模板
  │
  ▼
提取 {{ }} 中的变量名
  │
  ├─ 包含 . 或 [ → 识别为表达式路径
  │     │
  │     ▼
  │   expressions.query() → spans根读原始rows；语义根读公开有界Projection
  │     │
  │     ▼
  │   expressions.serialize() → 序列化为字符串
  │
  └─ 纯变量名 → 外层公共根或当前Projector.available_vars
  │
  ▼
替换到 prompt 中，发送给 LLM
```

---

## 表达式语法速查表

| 语法 | 含义 | 示例 |
|------|------|------|
| `key` | 访问属性 | `spans` |
| `[n]` | 索引（支持负数） | `spans[0]`, `spans[-1]` |
| `[*]` | 通配，展开所有元素 | `spans[*].name` |
| `[key:value]` | 按属性值过滤 | `spans[name:example-generation]` |
| `[start:end]` | Python 切片（左闭右开） | `spans[0:3]`, `spans[-2:]` |
| `[start,end]` | 范围（两端包含） | `spans[1,3]` |
| `a.b.c` | 按原始结构做最长真实键匹配 | `attributes.gen_ai.span.kind` |
| `{{*}}` | 输出全量 JSON | — |

### 序列化规则

| 值类型 | 输出格式 |
|--------|---------|
| 字符串 | 原样输出 |
| 字符串数组 | 换行拼接 |
| 对象/混合数组 | JSON（indent=2） |
| null / 空数组 | 空字符串 |

---

## 与现有 Projector 的关系

| 项目 | 有专用Projector（Coding / Framework） | 无专用 Projector |
|------|-------------------------------|------------------|
| 语义字段 | `Projection` 的稳定字段 | 不产生伪语义字段 |
| 原始变量 | 同样可用 `{{spans[...].attributes...}}` | 同样可用 `{{spans[...].attributes...}}` |
| 适用场景 | 已知运行时的内置和自定义诊断器 | 仅自定义 LLM/Python 诊断器 |
| Prompt 编写 | 语义变量与原始 spans 可组合 | 只用原始 spans 表达式 |

CUSTOM 保留显式 runtime 与原 raw Prompt/code；依赖退役语义根的配置需人工检查。
上述raw语法与专项SourceReference的有限leaf定位不同，不能用通配表达式作为专项来源引用。
