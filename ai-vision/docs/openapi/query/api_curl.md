# AI Vision OpenAPI curl 示例

Base URL: 本地示例 `http://localhost:5174`，其他部署请替换为实际地址
OpenAPI 前缀: `/openapi/v1`
认证 Header: `Authorization: Bearer <API_KEY>`
Agent Product: `ClaudeCode`

> 注意：文档中的 API Key 为占位示例；实际调用请替换为对应 Space 的 API Key。

## 0. 通用变量

先在 shell 中设置变量，后续 curl 可以直接复制执行。以下使用本地部署地址，其他环境请设置实际的 Base URL。

```bash
# 本地示例；其他部署请替换为实际地址
export BASE_URL="http://localhost:5174"
export OPENAPI_PREFIX="/openapi/v1"
export API_KEY="sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export SPACE_ID="961005a6-6cf4-41e2-ab6b-58006b2e378b"
export AGENT_PRODUCT="ClaudeCode"
export TRACE_ID="6f3c089b27fc65f0fe0743fedf5623b2"
export SPAN_ID="e78938ad5d401b8a"
export SESSION_ID="fb5ba3c2-d7ad-46d8-a479-a0d1038acf82"
# 可选：单独指定 session IO/batch IO 示例使用的 session；不设置时脚本会从 08 的结果自动选择一个有 IO 的 session。
export IO_SESSION_ID="${SESSION_ID}"

# 可按需调整时间范围；接口同时接受 ISO 8601 或 Unix 毫秒时间戳。
export FROM_TIME="2026-06-29T00:00:00+08:00"
export TO_TIME="2026-06-30T23:59:59+08:00"
```

也可以直接运行脚本自动请求全部接口：

```bash
# 从仓库根目录执行
./docs/openapi/query/api_curl.sh

# 或进入 OpenAPI 文档目录执行
cd docs/openapi/query
./api_curl.sh

# 也可以覆盖变量后执行
SPACE_ID="961005a6-6cf4-41e2-ab6b-58006b2e378b" ./docs/openapi/query/api_curl.sh
```

脚本输出说明：

- 每个请求都会生成响应文件：`01_xxx.json`
- 每个请求都会生成可复现命令脚本：`01_xxx.sh`
- 只有 stderr 非空时才保留：`01_xxx.err`
- 汇总文件：`summary.tsv`
- `SESSION_ID` 用于 session detail 示例；`09_sessions_io_batch` 和 `13_session_io` 默认使用同一个 `SESSION_ID`。如需单独指定 I/O 示例的 session，可覆盖 `IO_SESSION_ID`。

统一响应格式：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

常见错误：

- `401 / code=4001`：API Key 无效、禁用或缺失。
- `403 / code=4003`：API Key 不属于请求的 Space。
- `429 / code=4029`：触发限流，默认每个外部 API 凭证每分钟 100 次请求。

---

## 1. Trace 接口

### 1.1 查询 Trace 列表

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

### 1.2 查询 Trace 列表，并内联 I/O 与 usage

`io=true` 会在 view 层额外加载 I/O 内容并组装到每条 trace；`usage=true` 会额外加载用量信息。请求体建议与普通列表查询保持一致，只通过 query 参数打开 enrichment。

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list?io=true&usage=true" \
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

### 1.3 查询 Trace 详情

`GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/detail`

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/detail" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

### 1.4 查询 Trace 详情，并内联 I/O

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/detail" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "io=true" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

### 1.5 批量查询 Trace 下的 Span

`POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/spans`

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/spans" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "spanIds": [
    "${SPAN_ID}"
  ],
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}"
}
JSON
```

### 1.6 查询单个 Span 详情

`GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/span/{span_id}`

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/trace/${TRACE_ID}/span/${SPAN_ID}" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

---

## 2. Session 接口

### 2.1 查询 Session 列表

`POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/list`

以下列表示例使用 SigNoz/native 排序字段。原生列表默认每页 50 个 Session，最多 200 个，按 `last_activity` 降序排列。Session 元数据、facets 和分页在完整验证的 Space/product 时间窗口内本地计算。扫描上限为 10,000 条物理 traces，在 Agent UUID 筛选前生效；空页也需要完整验证。所有过滤条件必须同时命中同一原始 span，用量仍包含该 Session 在选定时间窗口、Agent 范围内的全部关联 traces。

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
  "orderBy": "last_activity.desc",
  "filters": [
    {
      "field": "resource.attributes.ant.agent.env",
      "op": "=",
      "value": "Default"
    }
  ],
  "keyword": ""
}
JSON
```

### 2.2 查询 Session 列表，并内联 I/O 与 usage

`io=true` 会在 view 层额外加载每个 session 的 I/O。SigNoz/native 列表始终返回规范化的 `usage`、`coverage` 和三个 token 总数字段，无需 `usage=true`；未知值为 `null`，明确上报的零值为 `0`。外部 Langfuse 来源可通过 `usage=true` 加载 provider 支持的 session usage。请求体与普通列表查询保持一致。

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/sessions/list?io=true&usage=true" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "page": 1,
  "limit": 20,
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "orderBy": "last_activity.desc",
  "filters": [
    {
      "field": "resource.attributes.ant.agent.env",
      "op": "=",
      "value": "Default"
    }
  ],
  "keyword": ""
}
JSON
```

### 2.3 批量查询 Session I/O

`POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/io`

```bash
SESSION_IO_ID="${IO_SESSION_ID:-${SESSION_ID}}"

curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/sessions/io" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "sessionIds": [
    "${SESSION_IO_ID}"
  ],
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}"
}
JSON
```

### 2.4 批量查询 Session usage

`POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/sessions/usage`

```bash
SESSION_IO_ID="${IO_SESSION_ID:-${SESSION_ID}}"

curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/sessions/usage" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "sessionIds": [
    "${SESSION_IO_ID}"
  ],
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}"
}
JSON
```

### 2.5 查询 Session 详情

`GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/session/{session_id}/detail`

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/session/${SESSION_ID}/detail" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}" \
  --data-urlencode "include_spans=false"
```

### 2.6 查询 Session 详情，并内联 I/O

```bash
curl -sS -G "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/session/${SESSION_ID}/detail" \
  -H "Authorization: Bearer ${API_KEY}" \
  --data-urlencode "io=true" \
  --data-urlencode "include_spans=true" \
  --data-urlencode "from=${FROM_TIME}" \
  --data-urlencode "to=${TO_TIME}"
```

### 2.7 查询单个 Session I/O

`POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/session/{session_id}/io`

```bash
SESSION_IO_ID="${IO_SESSION_ID:-${SESSION_ID}}"

curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/session/${SESSION_IO_ID}/io" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}"
}
JSON
```

---

## Events / Metadata 示例

### 查询 events 字段 metadata

```bash
curl -sS -X GET "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/metadata/events" \
  -H "Authorization: Bearer ${API_KEY}"
```

### 查询 traces 字段 metadata

```bash
curl -sS -X GET "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/metadata/traces" \
  -H "Authorization: Bearer ${API_KEY}"
```

### fieldsJson 事件列表

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/events/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "fields": ["time", "event_name", "trace_id", "session_id", "username"],
  "filters": {"env": "${ENV_VALUE}"},
  "orderBy": "-time",
  "limit": 50
}
JSON
```

### fieldsJson 双模式 query

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/events/query" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "mode": "fieldsJson",
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "fields": ["time", "event_name", "username"],
  "filters": {"env": "${ENV_VALUE}"},
  "limit": 100
}
JSON
```

### rawSql / Grafana 注意事项

`rawSql` 使用同一类 Space API Key 认证，并依赖 SpaceKey 所属 Space、个人空间用户范围、
DataSource 的 Agent Product 隔离、服务端宏和 body allowlist 共同限制查询边界。示例使用
占位 SQL，不包含任何真实 token、表名或 endpoint：

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/events/query" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<'JSON'
{
  "mode": "rawSql",
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "rawSql": "SELECT `period` AS time, count(*) AS value FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000",
  "format": "table",
  "refId": "A"
}
JSON
```

Grafana datasource alias 可以省略 `mode`，单 payload 默认按 `rawSql` 执行：

```bash
curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/grafana/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/query" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @- <<'JSON'
{
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "rawSql": "SELECT `period` AS time, count(*) AS value FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000",
  "format": "table",
  "refId": "A"
}
JSON
```
