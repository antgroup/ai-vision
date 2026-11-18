# `8_api_curl.md` Grafana rawSql 示例备份

该内容从面向用户的 OpenAPI curl 文档中移除。本期对外只开放 Trace 接口，以下示例仅作为内部历史备份，不对外发布。

## rawSql / Grafana 注意事项

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
