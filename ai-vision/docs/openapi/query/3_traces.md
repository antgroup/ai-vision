# OpenAPI Trace 接口

Trace 接口用于查询 Agent 执行轨迹、轨迹详情、Span 列表与 Span 详情。

## 1. 通用路径参数

| 参数 | 说明 |
|---|---|
| `space_id` | Space UUID，必须与 API Key 所属 Space 一致 |
| `agent_product` | Agent 产品标识，例如 `ClaudeCode` |
| `trace_id` | Trace ID，支持接口返回的 trace id |
| `span_id` | Span ID |

通用 Header：

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

## 2. 查询 Trace 列表

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/traces/list
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
  "filters": [
    {"field": "resource.attributes.ant.agent.scene", "op": "=", "value": "chat"}
  ]
}
```

curl：

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
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

## 3. 查询 Trace 列表并内联 I/O / usage

列表接口可通过 query 参数额外加载 I/O 与 usage：

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/traces/list?io=true&usage=true
```

说明：

- `io=true`：服务端会额外查询并在 view 层组装输入/输出内容。
- `usage=true`：服务端会尝试额外查询并在 view 层组装 provider 支持的 Token usage；当前 provider 无可用 trace usage 数据时不会伪造 usage 字段。
- 过滤条件与普通 Trace 列表保持一致。

## 4. 查询 Trace 详情

```text
GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/detail
```

curl：

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/detail" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

如需在详情中内联 I/O：

```text
GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/detail?io=true
```

## 5. 批量查询 Trace 下的 Span

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/spans
```

body：

```json
{
  "spanIds": ["span-id-1", "span-id-2"],
  "from": "2026-06-29T00:00:00+08:00",
  "to": "2026-06-30T23:59:59+08:00"
}
```

说明：

- `spanIds` 必填，必须是非空数组。
- `spanIds` 最多 200 个。
- `spanIds` 缺失、不是数组、为空数组或超过 200 个时，返回 HTTP `400`，响应 `code=3026`。

## 6. 查询单个 Span 详情

```text
GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/span/{span_id}
```

curl：

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/span/${SPAN_ID}" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

## 7. 响应结构

所有接口响应使用统一 envelope：`{"code": 0, "message": "success", "data": <payload>}`。以下是各接口 `data` 字段的结构。

本期对外响应结构以本节定义为准。

### Trace 列表 (`TraceListResponse`)

```json
{
  "data": [
    {
      "trace_id": "abc123",
      "span_id": "span001",
      "parent_span_id": null,
      "name": "root-span",
      "kind": 2,
      "status_code": "OK",
      "start_time_unix_nano": "1719648000000000000",
      "end_time_unix_nano": "1719648001000000000",
      "span_count": 5,
      "input": "hello",
      "output": "world"
    }
  ],
  "meta": {
    "page": 1,
    "limit": 20,
    "totalItems": 100,
    "totalPages": 5
  }
}
```

- `input` / `output` 仅在 `?io=true` 时返回。
- `usage` 仅在 `?usage=true` 时返回。
- `data[]` 中的字段取决于请求中的 `fields` 参数，默认返回核心字段。

### Trace 详情 (`TraceDetailResponse`)

```json
{
  "traceId": "abc123",
  "sofaTraceId": null,
  "sessionId": "session-001",
  "spans": [
    {
      "trace_id": "abc123",
      "span_id": "span001",
      "parent_span_id": null,
      "name": "root-span",
      "kind": 2,
      "start_time_unix_nano": "1719648000000000000",
      "end_time_unix_nano": "1719648001000000000",
      "status": {"code": "OK", "message": ""},
      "attributes": {},
      "resource": {},
      "events": [],
      "links": []
    }
  ],
  "input": "hello",
  "output": "world"
}
```

- `input` / `output` 仅在 `?io=true` 时返回。

### Span 批量查询 (`SpanBatchResponse`)

```json
{
  "spans": [<Span>, ...]
}
```

### 单个 Span 详情 (`Span`)

直接返回单个 Span 对象作为 `data`。结构同上面 spans 数组中的元素。

## 8. 过滤说明

Trace 列表支持快捷过滤参数和通用 `filters` 数组。详见 [过滤条件](./2_filters.md)。
