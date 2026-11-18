# 事件查询 Fields 过滤

本文档说明事件查询接口中 `fields` 和 `filters` 的用法。

同一 Space / 产品有多个 Agent 时，在 JSON body 中提供 `agent_uuid`；元数据
发现使用同名 query 参数。也可以给 typed events 接口传 query 参数，但 query 与
body 同时指定时必须相同，否则返回 `400 / 3027`。UUID 不能跨 Space、产品或指向
Disabled 实例。CLI 使用 `--param agent_uuid=<Agent UUID>`，日志查询的能力检查
和实际请求会选择同一实例；此规则不扩展 raw SQL 的允许字段。

## 适用接口

| 接口 | 说明 |
|------|------|
| `POST .../events/list` | 事件列表查询 |
| `POST .../events/query` (`mode: fieldsJson`) | 双模式查询的安全字段模式 |

## 1. 字段发现

`metadata/events` 返回事件数据源表的所有字段名和类型，可用于确认支持的事件字段：

```bash
curl -H "Authorization: Bearer ${API_KEY}" \
  "${BASE_URL}/openapi/v1/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/metadata/events"
```

响应中 `data.fields` 包含底层字段及类型：

```json
{
  "fields": [
    {"name": "period", "type": "timestamp"},
    {"name": "trace_id", "type": "string"},
    {"name": "attributes.gen_ai.conversation.id", "type": "string"},
    {"name": "attributes.ant.username", "type": "string"}
  ],
  "totalFields": 4
}
```

`fieldsJson` 查询仍使用服务端支持的 canonical 字段 key（如 `time`、
`event_name`、`trace_id`、`session_id`、`username`、`env`、`model`、
`duration_ms`、`status`），服务端会映射到支持的事件字段。

## 2. fields — 选择返回字段

`fields` 指定返回哪些列。不传时使用 DataSource 配置中的 `default_fields`。

```json
{
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "fields": ["time", "event_name", "trace_id", "username"],
  "limit": 50
}
```

- 只允许使用服务端支持的 canonical field key
- 服务端会将 canonical key 映射到实际的 SigNoz 列名（通过 `field_mappings` 配置）
- 未被 canonical 映射支持的字段名会返回校验错误

## 3. filters — 条件过滤

`filters` 支持两种格式：

### 数组格式（推荐）

```json
{
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "filters": [
    {"field": "event_name", "op": "=", "value": "api_request"},
    {"field": "latency", "op": ">=", "value": 1000},
    {"field": "username", "op": "IN", "value": ["user1", "user2"]}
  ]
}
```

### 字典格式（简写）

字典格式默认使用 `=` 运算符：

```json
{
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "filters": {"event_name": "api_request", "username": "user1"}
}
```

### 支持的运算符

| 运算符 | 说明 | 值类型 |
|--------|------|--------|
| `=` | 精确匹配 | string / number |
| `IN` | 多值匹配 | array |
| `>=` | 大于等于 | number / datetime |
| `<=` | 小于等于 | number / datetime |
| `LIKE` | 模糊匹配 | string（支持 `%` 通配符） |
| `IS_NULL` | 空值判断 | 无需 value |
| `IS_NOT_NULL` | 非空判断 | 无需 value |

## 4. 排序

```json
{
  "orderBy": "-time"
}
```

- 前缀 `-` 表示降序（默认）
- 只允许 `sortable: true` 的字段

## 5. 分页

```json
{
  "page": 1,
  "limit": 50
}
```

- `page` 从 1 开始
- `limit` 不能超过 `maxPageSize`（默认 200）

## 6. 时间范围（必填）

所有事件查询必须传 `from` 和 `to`：

```json
{
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00"
}
```

- 支持 ISO 8601 格式
- 也支持 `fromTimestamp` / `toTimestamp`（Unix 毫秒时间戳）
- 最大查询跨度受 `maxTimeRangeDays` 限制

## 7. 完整请求示例

```bash
curl -X POST \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  "${BASE_URL}/openapi/v1/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/events/list" \
  -d '{
    "from": "2026-07-01T00:00:00+08:00",
    "to": "2026-07-01T01:00:00+08:00",
    "fields": ["time", "event_name", "trace_id", "username", "latency"],
    "filters": [
      {"field": "event_name", "op": "=", "value": "llm_call"},
      {"field": "latency", "op": ">=", "value": 500}
    ],
    "orderBy": "-latency",
    "page": 1,
    "limit": 20
  }'
```

## 8. 安全边界

- `agent_product` 由 URL 路径决定，不可通过 body 覆盖
- 服务端自动注入 DataSource 级别的 `agent_product_filter` 隔离条件
- `fields` 和 `filters` 中只能引用服务端支持的 canonical 字段
- 底层列名（如 `attributes.xxx`）可通过 `metadata/events` 查看，但不能直接作为 `fieldsJson` 字段，除非服务端 canonical 映射支持
