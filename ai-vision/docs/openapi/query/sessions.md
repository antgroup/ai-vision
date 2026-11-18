# OpenAPI Session 接口

Session 接口用于查询 Agent 会话列表、会话详情、会话 I/O 与会话 Token usage。

## 1. 通用路径参数

| 参数 | 说明 |
|---|---|
| `space_id` | Space UUID，必须与 API Key 所属 Space 一致 |
| `agent_product` | Agent 产品标识，例如 `ClaudeCode` |
| `session_id` | Session ID |

通用 Header：

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

## 2. 查询 Session 列表

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/list
```

常用 body：

```json
{
  "page": 1,
  "limit": 20,
  "from": "2026-06-29T00:00:00+08:00",
  "to": "2026-06-30T23:59:59+08:00",
  "env": "Default",
  "username": "100001",
  "sessionId": "session-id",
  "detail": "1"
}
```

curl：

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/sessions/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "page": 1,
  "limit": 20,
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "env": "Default"
}
JSON
```

- `detail` 可选；设置为 `"1"` 时，请求服务端返回 Provider 支持的会话列表明细字段。
- SigNoz/native 的 `limit` 默认 `50`、最大 `200`，默认按 `last_activity` 降序排列。分页、Session 元数据和 facets 在服务端本地计算。
- 原生列表先完整验证授权 Space/product 时间窗口，最多扫描 10,000 条物理 traces，再筛选 Agent UUID；空页也需要完成验证。数据不完整或超过上限时请求失败。
- 原生 Session ID 与规范化 usage 查询采用相同优先级：`gen_ai.conversation.id`、`langfuse.session.id`、`session.id`，最后才回退到存储列 `session_id`。原始 span 中的值仍保留。
- 指定 Agent UUID 时，每条物理 trace 的全部 spans 必须携带同一个非空 UUID；缺失或混合身份会使扫描失败，其他 Agent 的完整 traces 在本地排除。
- 原生列表过滤条件必须同时命中同一原始 span，决定 Session 是否命中及其列表元数据。`trace_count` / `span_count` 描述匹配子集；用量仍包含选中 Session 在该时间窗口、Agent 范围内的全部关联 traces，不会被列表过滤条件截断。
- 原生 Session 时间字段使用 RFC3339Nano 文本，包括历史命名的 `start_time_unix_nano` / `end_time_unix_nano`。未上报结束时间时，`last_activity` 和 `end_time_unix_nano` 为 `null`。
- 原生 facets 对 Session 去重计数，计算每个维度时排除该维度自身的过滤条件。`fields` 校验支持的字段，但仍返回固定的完整行，保留 `usage` 和 `coverage`。

## 3. 查询 Session 列表并内联 I/O / usage

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/list?io=true&usage=true
```

说明：

- `io=true`：服务端额外查询并组装每个 Session 的输入/输出。
- SigNoz/native 列表始终返回规范化的 `usage`、`coverage` 和 `input_tokens`、`output_tokens`、`total_tokens`，无需 `usage=true`。未知值为 `null`，明确上报的零值为 `0`。
- `usage=true`：为外部 Langfuse 来源启用 provider 支持的 Session usage enrichment；无可用数据时不会伪造用量。
- 过滤条件与普通 Session 列表保持一致。

## 4. 批量查询 Session I/O

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/io
```

body：

```json
{
  "sessionIds": ["session-id-1", "session-id-2"],
  "from": "2026-06-29T00:00:00+08:00",
  "to": "2026-06-30T23:59:59+08:00"
}
```

说明：

- `sessionIds` 必填，必须是非空数组。
- `sessionIds` 最多 100 个。
- `sessionIds` 缺失、不是数组、为空数组或超过 100 个时，返回 HTTP `400`，响应 `code=3026`。
- 适合先通过 Session 列表拿到 ID 后，再批量补充 I/O。

## 5. 批量查询 Session usage

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/usage
```

body：

```json
{
  "sessionIds": ["session-id-1", "session-id-2"],
  "from": "2026-06-29T00:00:00+08:00",
  "to": "2026-06-30T23:59:59+08:00"
}
```

说明：

- `sessionIds` 必填，必须是非空数组。
- `sessionIds` 最多 100 个。
- `sessionIds` 缺失、不是数组、为空数组或超过 100 个时，返回 HTTP `400`，响应 `code=3026`。
- 该接口只在 Provider 支持会话级 usage 查询时返回 usage 数据；不支持时可能返回空数组或空 usage 字段。

返回中的 usage 字段通常包含：

```json
{
  "id": "session-id-1",
  "inputTokens": 100,
  "outputTokens": 20,
  "totalTokens": 120,
  "usage": {
    "input_tokens": 100,
    "output_tokens": 20,
    "total_tokens": 120
  }
}
```

## 6. 查询 Session 详情

```text
GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/session/{session_id}/detail
```

curl：

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/session/${SESSION_ID}/detail" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

如需内联 I/O：

```text
GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/session/{session_id}/detail?io=true
```

如需在 Provider 支持时同时返回关联 span：

```text
GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/session/{session_id}/detail?include_spans=true
```

## 7. 查询单个 Session I/O

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/session/{session_id}/io
```

body：

```json
{
  "from": "2026-06-29T00:00:00+08:00",
  "to": "2026-06-30T23:59:59+08:00"
}
```

## 8. 响应结构

所有接口响应使用统一 envelope：`{"code": 0, "message": "success", "data": <payload>}`。以下是各接口 `data` 字段的结构。

完整 Schema 定义见 [`openapi-external.yaml`](./openapi-external.yaml)。

### Session 列表 (`SessionListResponse`)

以下为 SigNoz/native 响应示例，其中 I/O 字段通过 `?io=true` 加载：

```json
{
  "data": [
    {
      "session_id": "session-001",
      "created_at": "2026-07-01T00:00:00Z",
      "last_activity": "2026-07-01T01:00:00Z",
      "start_time_unix_nano": "2026-07-01T00:00:00Z",
      "end_time_unix_nano": "2026-07-01T01:00:00Z",
      "trace_count": 3,
      "environment": "Default",
      "username": "100001",
      "input": "hello",
      "output": "world",
      "input_tokens": 80,
      "output_tokens": 40,
      "total_tokens": 120,
      "usage": {"input": 80, "output": 40, "total": 120, "unknownReason": null},
      "coverage": {"billableCalls": 3, "knownUsageCalls": 3, "knownTokens": 120, "knownCostCalls": 0}
    }
  ],
  "meta": {
    "page": 1,
    "limit": 20,
    "totalItems": 50,
    "totalPages": 3,
    "complete": true
  },
  "facets": {
    "env": [{"value": "Default", "count": 40}],
    "username": [{"value": "100001", "count": 10}]
  }
}
```

- `input` / `output` 仅在 `?io=true` 时返回。
- SigNoz/native 的 `usage`、`coverage` 和三个 token 总数字段始终返回；外部 Langfuse 的 `usage` 由 `?usage=true` 和 provider 支持情况决定。
- `coverage.knownTokens` 是已知单次调用用量的小计，不能替代完整用量 `usage.total`。即使全部调用都有用量，`usage.unknownReason` 为 `ambiguous_nested_generations` 时，总数仍为 `null`。
- `facets` 可选，由 Provider 决定是否返回。

### Session 详情 (`SessionDetailResponse`)

```json
{
  "sessionId": "session-001",
  "traceCount": 3,
  "traceIds": ["trace-001", "trace-002", "trace-003"],
  "timeRange": {
    "from": "2026-07-01T00:00:00Z",
    "to": "2026-07-01T01:00:00Z"
  },
  "spans": [<Span>, ...],
  "input": "hello",
  "output": "world"
}
```

- `spans` 仅在 `?include_spans=true` 时返回。
- `input` / `output` 仅在 `?io=true` 时返回。

### Session IO 批量 (`IOBatchResponse`)

```json
{
  "data": [
    {"id": "session-001", "input": "hello", "output": "world"},
    {"id": "session-002", "input": null, "output": null}
  ]
}
```

### Session Usage 批量 (`UsageBatchResponse`)

```json
{
  "data": [
    {
      "id": "session-001",
      "inputTokens": 100,
      "outputTokens": 20,
      "totalTokens": 120,
      "usage": {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120
      }
    }
  ]
}
```

### 单个 Session IO (`IOBatchItem`)

```json
{"id": "session-001", "input": "hello", "output": "world"}
```

## 9. 过滤说明

Session 列表支持快捷过滤参数和通用 `filters` 数组。详见 [过滤条件](./2_filters.md)。
