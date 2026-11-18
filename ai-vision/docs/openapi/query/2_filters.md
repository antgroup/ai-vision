# OpenAPI 过滤条件说明

本文档说明 AI Vision OpenAPI Trace 查询接口当前支持的过滤能力、字段写法、运算符以及调用示例。

示例统一使用：

```bash
# 本地示例；其他部署请替换为实际地址
export BASE_URL="http://localhost:5174"
export OPENAPI_PREFIX="/openapi/v1"
export API_KEY="sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export SPACE_ID="961005a6-6cf4-41e2-ab6b-58006b2e378b"
export AGENT_PRODUCT="ClaudeCode"
export FROM_TIME="2026-06-29T00:00:00+08:00"
export TO_TIME="2026-06-30T23:59:59+08:00"
```

认证 Header：

```bash
-H "Authorization: Bearer ${API_KEY}"
```

## 1. 支持过滤的接口

当前通用字段过滤主要支持在列表接口中使用：

| 接口 | 是否支持快捷维度 | 是否支持 `filters` 数组 | 说明 |
| --- | --- | --- | --- |
| `POST /spaces/{space_id}/agents/{agent_product}/traces/list` | 是 | 是 | Trace 列表查询，支持通用字段过滤 |

以下接口主要按 ID 查询，不作为通用字段过滤入口：

- `GET /trace/{trace_id}/detail`
- `POST /trace/{trace_id}/spans`
- `GET /trace/{trace_id}/span/{span_id}`

这些接口会复用必要的时间范围、空间权限、Agent 产品隔离等条件，但不建议依赖它们做任意字段过滤。

## 2. 过滤方式总览

列表接口有两类过滤方式：

1. 快捷维度参数：`env`、`scene`、`username`、`tags`
2. 通用 `filters` 数组：直接指定 SigNoz 表字段名、操作符和值

同时，后端会自动追加系统级过滤：

- 时间范围：`from` / `to` 或 `fromTimestamp` / `toTimestamp`
- 空间访问权限过滤
- 个人空间下的 username 隔离过滤
- Agent 产品隔离过滤：来自 DataSource 配置 `agent_product_filter`，不是请求里的 `agent_product`

## 3. 快捷维度参数

快捷参数会在 view 层转换成底层字段过滤。

| 快捷参数 | 底层字段 | 示例 |
| --- | --- | --- |
| `env` | `resource.attributes.ant.agent.env` | `"env": "Default"` |
| `scene` | `resource.attributes.ant.agent.scene` | `"scene": "coding"` |
| `username` | `attributes.ant.username` | `"username": "100001"` |
| `tags` | `attributes.langfuse.trace.tags` | `"tags": ["tag-a", "tag-b"]` |

### 示例：按 env 过滤 Trace

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "env": "Default",
  "page": 1,
  "limit": 20
}
JSON
```

## 4. 通用 `filters` 数组

如果要过滤快捷参数以外的字段，使用 `filters` 数组。

格式：

```json
{
  "filters": [
    {
      "field": "resource.attributes.ant.agent.env",
      "op": "=",
      "value": "Default"
    }
  ]
}
```

### 4.1 字段规则

- `field` 必须是 SigNoz 表中真实存在的字段名。
- 如果字段不存在，当前实现会忽略该过滤条件。
- 字段名使用支持的事件字段名，例如：
  - `trace_id`
  - `span_id`
  - `name`
  - `attributes.ant.username`
  - `attributes.gen_ai.conversation.id`
  - `attributes.gen_ai.span.kind`
  - `attributes.gen_ai.request.model`
  - `attributes.gen_ai.response.model`
  - `resource.attributes.ant.agent.env`
  - `resource.attributes.ant.agent.scene`

### 4.2 支持的操作符

| op | 说明 | value 示例 |
| --- | --- | --- |
| `=` | 等于 | `"Default"` |
| `IN` | 包含在列表中 | `["Default", "Pre"]` |
| `NOT IN` | 不包含在列表中 | `["Default", "Pre"]` |
| `>=` | 大于等于 | `"2026-06-30T00:00:00+08:00"` |
| `<=` | 小于等于 | `"2026-06-30T23:59:59+08:00"` |
| `LIKE` | 模糊匹配 | `"%GLM%"` |
| `IS NULL` | 为空 | 可省略 value |
| `IS NOT NULL` | 不为空 | 可省略 value |

`IN` / `NOT IN` 的 `value` 必须是数组；`=` / `>=` / `<=` 的
`value` 必须是标量；`LIKE` 的 `value` 必须是字符串。

> 建议时间范围优先使用顶层 `from` / `to`，不要自行用 `filters` 改写系统时间过滤。

### 示例：用 filters 按 env 过滤 Trace

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "filters": [
    {
      "field": "resource.attributes.ant.agent.env",
      "op": "=",
      "value": "Default"
    }
  ],
  "page": 1,
  "limit": 20
}
JSON
```

### 示例：按用户名过滤 Trace

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "filters": [
    {
      "field": "attributes.ant.username",
      "op": "=",
      "value": "100001"
    }
  ],
  "page": 1,
  "limit": 20
}
JSON
```

### 示例：按模型名称模糊匹配

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "filters": [
    {
      "field": "attributes.gen_ai.response.model",
      "op": "LIKE",
      "value": "%GLM%"
    }
  ],
  "page": 1,
  "limit": 20
}
JSON
```

### 示例：按多个环境过滤

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "filters": [
    {
      "field": "resource.attributes.ant.agent.env",
      "op": "IN",
      "value": ["Default", "Pre"]
    }
  ],
  "page": 1,
  "limit": 20
}
JSON
```

### 示例：只看 LLM span 相关 Trace

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "filters": [
    {
      "field": "attributes.gen_ai.span.kind",
      "op": "=",
      "value": "LLM"
    }
  ],
  "page": 1,
  "limit": 20
}
JSON
```

## 5. OpenAPI 友好的 filters map 写法

除数组形式外，OpenAPI 也接受一个简化 map：

```json
{
  "filters": {
    "resource.attributes.ant.agent.env": "Default",
    "attributes.ant.username": "100001"
  }
}
```

后端会转换成：

```json
[
  {
    "field": "resource.attributes.ant.agent.env",
    "op": "=",
    "value": "Default"
  },
  {
    "field": "attributes.ant.username",
    "op": "=",
    "value": "100001"
  }
]
```

示例：

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "filters": {
    "resource.attributes.ant.agent.env": "Default",
    "attributes.ant.username": "100001"
  },
  "page": 1,
  "limit": 20
}
JSON
```

## 6. tags 过滤

`tags` 是独立逻辑，使用 OR 语义：命中任意一个 tag 即返回。

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "tags": ["mcp", "tool"],
  "page": 1,
  "limit": 20
}
JSON
```

## 7. 与 `io=true` / `usage=true` 一起使用

过滤条件先作用于列表查询，随后 view 层再对结果做 I/O 或 usage 组装。

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list?io=true&usage=true" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "env": "Default",
  "page": 1,
  "limit": 20
}
JSON
```

## 8. 注意事项

1. `agent_product` 路径参数只用于选择 DataSource；底层查询的产品隔离值来自 DataSource 配置 `agent_product_filter`。
2. `env` / `scene` / `username` 是便捷参数，不代表所有字段都可以作为顶层参数传入。
3. 其他字段请使用 `filters` 数组或 filters map。
4. `filters.field` 必须是底层表里存在的字段；不存在时该条件会被忽略。
5. 个人空间会自动追加当前用户的 username 过滤，避免越权查看其他用户数据。
6. 业务空间可查询空间范围内数据，仍会受到 SpaceKey 权限和 DataSource 产品隔离限制。
7. Trace detail 和 Span 接口主要按 ID 查询；通用过滤请先在 Trace list 接口完成，再取 ID 调用后续接口。
