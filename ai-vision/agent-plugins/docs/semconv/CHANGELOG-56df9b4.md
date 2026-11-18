# Semconv 变更记录 (56df9b4)

## 一、属性级别变更

### `gen_ai.provider.name` required/recommended → opt_in

Agentic 场景下，运行时（如 Claude Code）作为网关不直接感知底层 LLM 供应商，强制要求无法满足。

| Span 定义 | 变更前 | 变更后 |
|-----------|--------|--------|
| `span.gen_ai.create_agent` | required | **opt_in** |
| `span.gen_ai.invoke_agent` | required | **opt_in** |
| `span.gen_ai.inference` | required | **opt_in** |
| `span.gen_ai.embeddings` | required | **opt_in** |
| `span.gen_ai.retrieval` | conditionally_required | **opt_in** |

### `gen_ai.system` recommended → opt_in

同理，agentic runtime 可能通过统一网关调用模型，无法确定实际 system 标识。

| Span 定义 | 变更前 | 变更后 |
|-----------|--------|--------|
| `span.ant.llm` | recommended | **opt_in** |

> 对应 Rego 策略同步调整：`llm_missing_system` 和 `llm_missing_provider_name` 从 `improvement` 降为 `information` 级别。

---

## 二、新增属性

### `attribute_groups.yaml`

| 属性 | 类型 | 说明 |
|------|------|------|
| `agentic.turn.number` | int | Turn 序号（1-based） |
| `agentic.turn.index` | int | Turn 索引（0-based） |
| `agentic.response.index` | int | 单 turn 内 LLM 响应索引（0-based） |
| `agentic.tool.count` | int | 本次 LLM 响应触发的工具调用数 |

### `gen_ai.tool.type` 枚举扩展

新增 `skill` 成员，用于区分技能调用与普通工具调用。

| 枚举值 | 说明 |
|--------|------|
| `function` | 已有 — 函数调用 |
| `extension` | 已有 — 外部扩展 |
| `datastore` | 已有 — 数据存储访问 |
| **`skill`** | **新增** — 预定义 prompt 驱动工作流 |

### `entities.yaml`

`ant.agent.type` 枚举新增 `codex` 成员。

---

## 三、Span 定义属性补充

### `span.ant.chat` (Turn span)

新增 recommended 属性：

- `agentic.runtime.name` — Agent 运行时标识
- `agentic.turn.number` — Turn 序号

### `span.ant.llm` (LLM span)

新增 recommended 属性：

- `agentic.turn.index` — Turn 索引
- `agentic.response.index` — 响应索引
- `agentic.tool.count` — 工具调用数

---

## 四、属性前缀白名单门禁

新增 Rego 规则 `disallowed_attr_prefix` / `disallowed_resource_attr_prefix`，对 span 和 resource 属性名做前缀校验。

**允许的前缀：**

| 前缀 | 用途 |
|------|------|
| `gen_ai.*` | OTel GenAI 语义规范 |
| `ant.*` | AI Vision业务扩展 |
| `agentic.*` | Agent 运行时元数据 |
| `service.*` | OTel 标准 service 资源属性 |
| `telemetry.*` | OTel SDK 元数据 |

不在白名单内的属性（如 `langfuse.*`、`llm.*`、裸属性名）会触发 **violation**。

---

## 五、策略目录重组

```
weaver/policies/
├── base/          # 通用 helpers、枚举校验、废弃属性拦截、属性前缀门禁
├── span/          # Turn/LLM/Tool span 校验、semconv 合规检测
├── metric/        # Metric 校验（预留）
├── event/         # Event 校验（预留）
```

- 旧版 v1 规则移至 `weaver/_legacy_policies/`
- `weaver-guard` 引擎新增递归目录加载（跳过 `_` 前缀目录）
- 验证命令变更为 `--policies-dir weaver/policies`
