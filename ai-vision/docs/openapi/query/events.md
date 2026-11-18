# OpenAPI Events 接口

Events 接口用于查询事件/日志类数据。默认使用安全的 `fieldsJson` 模式；`rawSql` 仅供 Grafana/service key 使用。

## 1. 事件列表

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/events/list
```

请求示例：

```json
{
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "page": 1,
  "limit": 50,
  "fields": ["time", "event_name", "trace_id", "session_id", "username"],
  "filters": {"env": "pre"},
  "orderBy": "-time"
}
```

响应保持统一 envelope：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "data": [{"time": "2026-07-01 00:01:02", "event_name": "api_request"}],
    "meta": {"page": 1, "limit": 50, "totalItems": 1, "totalPages": 1}
  }
}
```

## 2. 事件 facets

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/events/facets
```

请求示例：

```json
{
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "dimensions": ["event_name", "env", "username"],
  "filters": {"env": "pre"},
  "limit": 20
}
```

## 3. 双模式 query endpoint

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/events/query
```

### fieldsJson（默认）

`fieldsJson` 等价于受控字段查询，调用方不能提交 SQL：

```json
{
  "mode": "fieldsJson",
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "fields": ["time", "event_name", "username"],
  "filters": {"env": "pre"},
  "limit": 100
}
```

### rawSql（受限 SQL / Grafana）

当前规划不引入 SpaceKey 级别的 rawSql 权限字段/能力模型。`rawSql` 对有效 SpaceKey 开放，但必须通过服务端宏注入表、时间、查询范围和 Agent 产品隔离条件：

```json
{
  "mode": "rawSql",
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "rawSql": "SELECT `period` AS time, count(*) AS value FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000",
  "format": "table",
  "refId": "A"
}
```

安全限制：

- 仅接受有效 SpaceKey，且路径中的 `space_id` 必须与 SpaceKey 所属 Space 一致。
- body 不能覆盖 `space_id`、`agent_product`、datasource、table、endpoint、access id/token 或 scope filter。
- URL 中的 `space_id` 用于鉴权校验，不会被渲染成 SQL；`{{scope_filter}}` 在业务空间通常为 `1=1`，在个人空间会按用户身份追加 username 过滤。
- 只允许单条只读 `SELECT`。
- 默认拒绝 DDL/DML、多语句、`JOIN`、`UNION` 和额外表引用。
- 必须包含 `{{table}}`、`{{time_filter}}`、`{{scope_filter}}`、`{{agent_product_filter}}`。
- 必须包含显式 `LIMIT`，并受服务端 `max_rows` 限制。
- 服务端不记录完整渲染 SQL，只记录 hash 和审计元数据。

## 4. Grafana alias

```text
POST /openapi/v1/grafana/spaces/{space_id}/agents/{agent_product}/query
```

该 endpoint 可接收单个 `rawSql` payload，也可接收 `targets[]`。单 payload 形态可省略
`mode`，服务端会按 `rawSql` 处理。标准 Grafana 查询体中的
`maxDataPoints`、`intervalMs`、`range`、`scopedVars`、`panelId` 等传输元数据会被忽略；
如果未显式传 `from` / `to`，服务端会使用 `range.from` / `range.to` 作为时间范围：

```json
{
  "range": {
    "from": "2026-07-01T00:00:00+08:00",
    "to": "2026-07-01T01:00:00+08:00"
  },
  "maxDataPoints": 500,
  "intervalMs": 60000,
  "refId": "A",
  "rawSql": "SELECT `period` AS time, count(*) AS value FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000"
}
```

Grafana 多 target 形态：

```json
{
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "targets": [
    {
      "refId": "A",
      "mode": "rawSql",
      "rawSql": "SELECT `period` AS time, count(*) AS value FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000"
    }
  ]
}
```

## 响应结构

所有接口响应使用统一 envelope：`{"code": 0, "message": "success", "data": <payload>}`。以下是各接口 `data` 字段的结构。

完整 Schema 定义见 [`openapi-external.yaml`](./openapi-external.yaml)。

### 事件列表 (`EventListResponse`)

```json
{
  "data": [
    {"time": "2026-07-01 00:01:02", "event_name": "api_request", "trace_id": "abc123"}
  ],
  "meta": {
    "page": 1,
    "limit": 50,
    "totalItems": 1,
    "totalPages": 1
  }
}
```

- `data[]` 中的字段取决于请求中的 `fields` 参数。

### 事件 Facets (`EventFacetsResponse`)

```json
{
  "data": {
    "event_name": [{"value": "api_request", "count": 42}],
    "username": [{"value": "100001", "count": 10, "label": "张三"}]
  },
  "meta": {"limit": 100}
}
```

### rawSql 查询 (`RawSqlResponse`)

```json
{
  "data": [
    {"time": "2026-07-01 00:00:00", "value": 1}
  ],
  "meta": {
    "mode": "rawSql",
    "refId": "A",
    "format": "table",
    "rowCount": 1,
    "sqlHash": "a1b2c3d4..."
  }
}
```

### Grafana 多目标查询 (`GrafanaMultiResponse`)

```json
{
  "results": [
    {"refId": "A", "data": [...], "meta": {"mode": "rawSql", "refId": "A", ...}},
    {"refId": "B", "data": [...], "meta": {"mode": "rawSql", "refId": "B", ...}}
  ]
}
```

### Events Metadata (`TableMetadataResponse`)

```json
{
  "agentProduct": "ClaudeCode",
  "kind": "events",
  "source": {
    "sourceSystem": "signoz",
    "default": true,
    "protocol": "native",
    "responseFormat": "table"
  },
  "fields": [
    {"name": "period", "type": "timestamp"},
    {"name": "trace_id", "type": "string"},
    {"name": "attributes.gen_ai.conversation.id", "type": "string"}
  ],
  "totalFields": 3
}
```
