# Grafana rawSql 查询

本文档说明如何通过 AI Vision OpenAPI 的 Grafana 兼容端点执行 rawSql 查询，适用于在 Grafana 中配置 JSON API 数据源。

## 端点

```text
POST /openapi/v1/grafana/spaces/{space_id}/agents/{agent_product}/query
```

也可使用通用 events/query 端点：

```text
POST /openapi/v1/spaces/{space_id}/agents/{agent_product}/events/query
```

## 认证

```http
Authorization: Bearer sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

当前规划不引入 SpaceKey 级别的 rawSql 权限字段/能力模型。只要是有效 SpaceKey，且请求路径中的
`space_id` 与该 Key 所属 Space 一致，即可调用本端点；查询仍会受到个人空间用户范围、
DataSource 的 Agent Product 隔离、服务端宏和 body allowlist 约束。

## 必需宏（Macros）

rawSql 中 **必须** 包含以下服务端宏，否则请求会被拒绝：

| 宏 | 作用 | 渲染示例 |
|----|------|----------|
| `{{table}}` | 注入数据源表名（含 namespace） | `` `my_namespace`.`my_table` `` |
| `{{time_filter}}` | 注入时间范围过滤 | `` `period` >= '2026-07-01' AND `period` <= '2026-07-02' `` |
| `{{scope_filter}}` | 注入查询范围约束。URL 中的 `space_id` 只用于鉴权校验，不会被渲染成 SQL；业务空间通常渲染为 `1=1`，个人空间会按用户身份过滤 | `` `attributes.ant.username` = '100001' `` |
| `{{agent_product_filter}}` | 注入 Agent 产品过滤 | `` `resource.attributes.ant.agent.product` = 'ClaudeCode' `` |

完整 SQL 模板示例：

```sql
SELECT
  `period` AS time,
  count(*) AS value
FROM {{table}}
WHERE {{time_filter}}
  AND {{scope_filter}}
  AND {{agent_product_filter}}
GROUP BY `period`
ORDER BY `period` ASC
LIMIT 1000
```

## 安全限制

| 规则 | 说明 |
|------|------|
| 只允许 SELECT | DDL（CREATE/DROP/ALTER）和 DML（INSERT/UPDATE/DELETE）被拒绝 |
| 单条语句 | 不允许分号分隔的多条 SQL |
| 禁止 JOIN/UNION | 不允许跨表查询 |
| 必须显式 LIMIT | 每条 SQL 必须包含 `LIMIT`，受 DataSource 配置的 `max_rows` 上限限制 |
| 只读 body 字段 | rawSql 核心字段只允许 `rawSql`、`refId`、`format`、`from`、`to`、`fromTimestamp`、`toTimestamp`、`mode`；Grafana alias 会额外接受并剥离 `range`、`targets`、`maxDataPoints`、`intervalMs`、`scopedVars` 等标准传输元数据；不允许覆盖服务端控制的字段 |
| 宏不可伪造 | `{{table}}`、`{{time_filter}}` 等由服务端渲染，客户端无法注入自定义值 |

## 请求格式

### 单 target（Grafana 简单面板）

```bash
curl -X POST \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  "${BASE_URL}/openapi/v1/grafana/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/query" \
  -d '{
    "from": "2026-07-01T00:00:00+08:00",
    "to": "2026-07-01T01:00:00+08:00",
    "rawSql": "SELECT `period` AS time, count(*) AS value FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000",
    "refId": "A",
    "format": "table"
  }'
```

### 多 targets（Grafana 混合面板）

```bash
curl -X POST \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  "${BASE_URL}/openapi/v1/grafana/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/query" \
  -d '{
    "from": "2026-07-01T00:00:00+08:00",
    "to": "2026-07-01T01:00:00+08:00",
    "targets": [
      {
        "refId": "A",
        "rawSql": "SELECT `period` AS time, count(*) AS total FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000"
      },
      {
        "refId": "B",
        "rawSql": "SELECT `period` AS time, sum(CAST(`attributes.gen_ai.usage.total_tokens` AS BIGINT)) AS tokens FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000"
      }
    ]
  }'
```

### 通过 events/query 端点

```json
{
  "mode": "rawSql",
  "from": "2026-07-01T00:00:00+08:00",
  "to": "2026-07-01T01:00:00+08:00",
  "rawSql": "SELECT ... FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} LIMIT 100",
  "refId": "A",
  "format": "table"
}
```

## 响应格式

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "columns": [
      {"key": "time"},
      {"key": "value"}
    ],
    "rows": [
      {"time": "2026-07-01 00:00:00", "value": 42},
      {"time": "2026-07-01 00:01:00", "value": 38}
    ],
    "meta": {
      "mode": "rawSql",
      "refId": "A",
      "rowCount": 2
    }
  }
}
```

多 targets 响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "results": [
      {"refId": "A", "columns": [...], "rows": [...]},
      {"refId": "B", "columns": [...], "rows": [...]}
    ]
  }
}
```

## Grafana JSON API 数据源配置

在 Grafana 中配置 JSON API 数据源连接 AI Vision：

1. **URL**: `http://localhost:5174/openapi/v1/grafana/spaces/{SPACE_ID}/agents/{AGENT_PRODUCT}/query`
2. **Auth**: Custom Header → `Authorization: Bearer sk-aiv-xxx`
3. **Query Type**: 选择 Table 或 Time series
4. **Body**: 填入包含 `{{table}}`、`{{time_filter}}`、`{{scope_filter}}`、`{{agent_product_filter}}` 和 `LIMIT` 的 SQL 模板

## 常见错误

| 错误码 | 消息 | 原因 |
|--------|------|------|
| 3027 | `rawSql must be a non-empty string` | rawSql 为空或非字符串 |
| 3027 | `rawSql missing required macro: xxx` | SQL 未包含必需的 `{{xxx}}` 宏 |
| 3027 | `rawSql must include an explicit LIMIT` | SQL 中没有 LIMIT 子句 |
| 3027 | `rawSql contains unsafe construct: xxx` | SQL 包含禁止的 DDL/DML/JOIN/UNION |
| 4003 | `rawSql body contains unsupported or server-controlled fields: xxx` | body 中包含不允许的字段 |
| 3027 | `Event query requires valid from and to timestamps` | 缺少时间范围参数 |
