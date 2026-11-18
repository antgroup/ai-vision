# AI Vision OpenAPI Trace curl 示例

Base URL：本地示例 `http://localhost:5174`，其他部署请替换为实际地址
OpenAPI 前缀：`/openapi/v1`
认证 Header：`Authorization: Bearer <API_KEY>`
Agent Product 示例：`ClaudeCode`

> 文档中的 API Key、Space ID、Trace ID 和 Span ID 均为占位示例，请替换为实际值。

## 0. 通用变量

```bash
# 本地示例；其他部署请替换为实际地址
export BASE_URL="http://localhost:5174"
export OPENAPI_PREFIX="/openapi/v1"
export API_KEY="sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export SPACE_ID="your-space-id"
export AGENT_PRODUCT="ClaudeCode"
export TRACE_ID="your-trace-id"
export SPAN_ID="your-span-id"
export FROM_TIME="2026-06-29T00:00:00+08:00"
export TO_TIME="2026-06-30T23:59:59+08:00"
```

常见错误：

- `401 / code=4001`：API Key 无效、禁用或缺失。
- `403 / code=4003`：API Key 不属于请求的 Space。
- `429 / code=4029`：触发限流，默认每个外部 API 凭证每分钟 100 次请求。

## 1. 查询 Trace 列表

`POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/traces/list`

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
  "orderBy": "start_time_unix_nano desc",
  "filters": [
    {
      "field": "resource.attributes.ant.agent.env",
      "op": "=",
      "value": "Default"
    }
  ],
  "keyword": "",
  "entry_only": false
}
JSON
```

如需在列表响应中内联 I/O 与 usage，在路径后添加 `?io=true&usage=true`，请求体保持不变。

## 2. 查询 Trace 详情

`GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/detail`

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/detail" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

如需内联 I/O，增加 `--data-urlencode "io=true"`。

## 3. 批量查询 Trace 下的 Span

`POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/spans`

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/spans" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "spanIds": ["${SPAN_ID}"],
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}"
}
JSON
```

## 4. 查询单个 Span 详情

`GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/span/{span_id}`

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/span/${SPAN_ID}" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

## 5. 查询 Trace 字段元数据

`GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/metadata/traces`

```bash
curl -sS "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/metadata/traces" \
  -H "Authorization: Bearer ${API_KEY}"
```
