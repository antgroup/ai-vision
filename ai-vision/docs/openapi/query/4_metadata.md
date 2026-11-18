# OpenAPI Trace Metadata 接口

Trace Metadata 接口通过 `metadata/traces` 返回轨迹数据源字段信息。

多实例选择与状态规则见 [Metadata 接口](metadata.md#agent-实例选择)：使用
`?agent_uuid=<Agent UUID>` 选择当前 Space 的 Normal / Pending 实例。

通用 Header：

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

## Trace 字段元数据

```text
GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/metadata/traces
```

返回当前 Space + Agent Product 配置的轨迹表 schema 中发现的所有字段名、字段类型，
以及 SigNoz schema 返回的可选注释/nullable 信息。

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "agentProduct": "ClaudeCode",
    "kind": "traces",
    "source": {
      "sourceSystem": "signoz",
      "default": true,
      "protocol": "native",
      "responseFormat": "table"
    },
    "fields": [
      {"name": "period", "type": "timestamp"},
      {"name": "trace_id", "type": "string"},
      {"name": "span_id", "type": "string"},
      {"name": "start_time_unix_nano", "type": "bigint"}
    ],
    "totalFields": 4
  }
}
```

没有对应数据源时，接口仍返回成功，`source` 为 `null`，`fields` 为空数组。


## 安全边界

Metadata 字段接口只返回经过脱敏的 schema 信息，不返回：

- SigNoz endpoint / access id / access token
- `namespace` / `table_name`
- datasource id
- query templates
- SpaceKey capabilities 或其他 key 级别权限配置

`space_id` 仍来自 URL，并通过 `Authorization: Bearer sk-aiv-...` 做 Space 级隔离；
调用方不能通过请求体或 query 参数覆盖 Space / Agent Product 数据源边界。
