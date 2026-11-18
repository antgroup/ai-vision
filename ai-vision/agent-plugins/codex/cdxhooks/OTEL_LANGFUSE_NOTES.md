## OTel 上报 Langfuse 注意事项

本文档记录了通过 OTLP (OpenTelemetry Protocol) HTTP/JSON 格式向 Langfuse 上报 trace 数据时遇到的各种坑和注意事项。

---

### 1. OTLP JSON 属性值格式

**问题**：Langfuse OTel worker 对属性值格式非常严格，不支持 `intValue` 和 `arrayValue`。

**正确做法**：
- ✅ 所有属性值统一使用 `stringValue`，包括数字类型
- ❌ 不要使用 `intValue`（即使 OTel 规范允许）
- ❌ 不要使用 `arrayValue`（Langfuse 不支持）

```json
// ✅ 正确
{"key": "gen_ai.usage.input_tokens", "value": {"stringValue": "63"}}

// ❌ 错误 — Langfuse worker 会崩溃
{"key": "gen_ai.usage.input_tokens", "value": {"intValue": 63}}

// ❌ 错误 — Langfuse 不支持 arrayValue
{"key": "some.list", "value": {"arrayValue": {"values": [...]}}}
```

**根因**：Langfuse 的 OTel ingestion worker 在解析属性时，遇到非 `stringValue` 的类型会抛异常，导致整个 span 处理失败。observations 页面可能显示数据（因为 span 被创建了），但 Input/Output 列为空（因为属性解析失败）。

---

### 2. Trace 与 Root Span 的关系

**问题**：Langfuse Traces 页面查不到数据，但 Observations 页面有数据。

**关键规则**：
- Langfuse 从 **root span**（`parentSpanId` 为空字符串 `""` 或不存在）自动创建 trace
- Root span 的 `parentSpanId` **必须是空字符串 `""`**，不能是 `null`、不能省略
- 如果所有 span 都有 `parentSpanId`，Langfuse 不会创建 trace

```json
// ✅ 正确 — root span
{
  "traceId": "abcd1234...",
  "spanId": "ef567890",
  "parentSpanId": "",
  "name": "Turn 1",
  "kind": 2
}

// ❌ 错误 — 不会被识别为 root span
{
  "traceId": "abcd1234...",
  "spanId": "ef567890",
  "parentSpanId": null,
  "name": "Turn 1"
}
```

---

### 3. Langfuse OTel 属性映射（Property Mapping）

Langfuse 通过特定的 span attribute key 来填充 trace/observation 的字段：

#### Trace 级别属性（放在 root span 上）

| Attribute Key | 映射到 | 说明 |
|---|---|---|
| `langfuse.trace.name` | Trace Name | 显示在 Traces 列表中 |
| `langfuse.trace.input` | Trace Input | 用户输入 |
| `langfuse.trace.output` | Trace Output | 最终输出 |
| `langfuse.session.id` | Session ID | 会话关联 |
| `langfuse.user.id` | User ID | 用户标识 |

#### Observation 级别属性（放在子 span 上）

| Attribute Key | 映射到 | 说明 |
|---|---|---|
| `langfuse.observation.input` | Observation Input | 该步骤的输入 |
| `langfuse.observation.output` | Observation Output | 该步骤的输出 |
| `gen_ai.system` | Provider | 如 `anthropic` |
| `gen_ai.request.model` | Model | 如 `claude-sonnet-4-20250514` |
| `gen_ai.prompt` | Input (generation) | LLM 的 prompt |
| `gen_ai.completion` | Output (generation) | LLM 的回复 |
| `gen_ai.usage.input_tokens` | Input Tokens | 输入 token 数 |
| `gen_ai.usage.output_tokens` | Output Tokens | 输出 token 数 |

**注意**：
- `langfuse.trace.*` 属性只在 root span 上生效
- `gen_ai.*` 属性会让 Langfuse 将该 span 识别为 **generation** 类型
- 不要在属性 key 前加 `metadata.` 前缀（如 `metadata.langfuse.trace.name`），Langfuse 不识别

---

### 4. OTLP JSON ID 格式

| 字段 | 格式 | 长度 | 示例 |
|---|---|---|---|
| `traceId` | 小写 hex 字符串 | 32 字符（16 字节） | `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6` |
| `spanId` | 小写 hex 字符串 | 16 字符（8 字节） | `1a2b3c4d5e6f7a8b` |
| `parentSpanId` | 小写 hex 字符串或空字符串 | 16 字符或 `""` | `""` (root) 或 `1a2b3c4d5e6f7a8b` |

**注意**：不要使用 UUID 格式（带 `-` 的），必须是纯 hex 字符串。

---

### 5. 时间戳格式

OTLP JSON 中的时间戳使用 **纳秒级 Unix 时间戳字符串**：

```json
{
  "startTimeUnixNano": "1711234567000000000",
  "endTimeUnixNano": "1711234568000000000"
}
```

**注意**：
- 必须是字符串类型，不是数字
- 单位是纳秒（10^9），不是毫秒或微秒
- 从 ISO 8601 转换时注意精度

---

### 6. Span Kind 值

| Kind | 含义 | 在 Langfuse 中的表现 |
|---|---|---|
| `1` | INTERNAL | 普通 span/observation |
| `2` | SERVER | 通常用于 root span，映射为 trace |
| `3` | CLIENT | 客户端调用 |

**建议**：Root span 使用 `kind: 2`（SERVER），子 span 使用 `kind: 1`（INTERNAL）。

---

### 7. Span 层级结构设计

#### 推荐的扁平结构

```
Root Span (kind=2, parentSpanId="")  →  Langfuse Trace + 同名 Observation
  ├── Tool: Glob (kind=1)            →  Tool call span
  ├── Tool: Read (kind=1)            →  Tool call span
  └── Claude Response (kind=1)       →  Generation (因为有 gen_ai.* 属性)
```

**注意事项**：
- Langfuse 会自动从 root span 创建一个 trace **和** 一个同名的 observation span
- 所以不需要额外创建中间层的 "turn span"，否则会出现多余的嵌套层
- Tool spans 和 Generation span 直接挂在 root span 下即可

#### 避免的错误结构

```
❌ 过深嵌套 — 会在 Langfuse 中产生多余层级
Root Span
  └── Turn Span
        └── Generation Span
              ├── Tool Span 1
              └── Tool Span 2
```

---

### 8. HTTP 请求配置

#### Endpoint

```
POST {LANGFUSE_HOST}/api/public/otel/v1/traces
```

#### Headers

```
Authorization: Basic base64({PUBLIC_KEY}:{SECRET_KEY})
Content-Type: application/json
```

**注意**：不需要 `x-langfuse-ingestion-version` header（那是 ingestion 协议用的），但加了也不会报错。

---

### 9. 多 Turn 会话处理

**问题**：如果每次 hook 触发时都发送所有 turns 的数据，会导致：
- Turn 2 的 trace 包含 Turn 1 的重复数据
- Langfuse 中出现重复的 observations

**正确做法**：
- 每次只处理**最新的 turn**（`turns.last()`）
- 使用 `prev_turn_count` 跟踪已处理的 turn 数量
- 每个 turn 生成独立的 `traceId`

---

### 10. 常见调试方法

#### Debug Dump

在发送前将 OTLP JSON body dump 到文件：

```rust
if let Ok(log_dir) = std::env::var("HOME").map(|h| format!("{}/.claude/logs", h)) {
    let dump_path = format!("{}/otel_body_debug.json", log_dir);
    let pretty = serde_json::to_string_pretty(body).unwrap_or_default();
    let _ = std::fs::write(&dump_path, &pretty);
}
```

#### 快速检查 Spans

```bash
python3 -c "
import json
d=json.load(open('\$HOME/.claude/logs/otel_body_debug.json'))
spans=d['resourceSpans'][0]['scopeSpans'][0]['spans']
print('span_count:', len(spans))
for s in spans:
    print(s['name'], s.get('parentSpanId','')[:8])
"
```

#### 检查特定 Span 的属性

```bash
python3 -c "
import json
d=json.load(open('\$HOME/.claude/logs/otel_body_debug.json'))
spans=d['resourceSpans'][0]['scopeSpans'][0]['spans']
for s in spans:
    if 'SPAN_NAME' in s['name']:
        for a in s['attributes']:
            print(a['key'], '=', list(a['value'].values())[0])
"
```

---

### 11. Langfuse OTel vs Ingestion 协议对比

| 维度 | OTel 协议 | Ingestion 协议 |
|---|---|---|
| **Endpoint** | `/api/public/otel/v1/traces` | `/api/public/ingestion` |
| **格式** | OTLP JSON（resourceSpans） | Langfuse 自定义 JSON（batch events） |
| **Trace 创建** | 自动从 root span 创建 | 显式 `trace-create` event |
| **Observation 类型** | 由属性自动推断（有 `gen_ai.*` = generation） | 显式指定 `span-create` / `generation-create` |
| **Input/Output** | 通过 `langfuse.observation.input/output` 属性 | 直接在 body 中指定 `input` / `output` 字段 |
| **属性值类型** | 仅支持 `stringValue` | 原生 JSON 类型 |
| **灵活性** | 较低（受 OTel 规范约束） | 较高（Langfuse 原生格式） |
| **标准化** | 符合 OpenTelemetry 标准 | Langfuse 私有协议 |

---

### 12. 已知的 Langfuse OTel 限制

1. **不支持 `intValue`**：所有数值必须转为 `stringValue`
2. **不支持 `arrayValue`**：列表类型数据需要序列化为 JSON 字符串
3. **Root span 自动创建 observation**：无法避免，设计层级时需考虑
4. **属性 key 大小写敏感**：`gen_ai.system` ≠ `Gen_AI.System`
5. **tool_use 信息可能丢失**：Claude Code transcript 中 assistant message 的 content 可能不包含 `type: "tool_use"` 的项，需要从 `tool_results` 反向构建 tool spans 作为 fallback
