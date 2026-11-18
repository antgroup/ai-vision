# OpenAPI Metadata 接口

Metadata 按数据类型拆分：事件表字段用 `metadata/events`，轨迹表字段用
`metadata/traces`。

通用 Header：

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

## Agent 实例选择

`metadata`、`metadata/events`、`metadata/traces` 均支持 `?agent_uuid=<Agent UUID>`。
同一 Space / Agent Product 下存在多个 Normal 或 Pending 实例时必须提供该参数；
仅有一个实例时可以省略。UUID 必须属于 URL 指定的 Space 和产品；Disabled、已删除
或其它 Space 的实例会被拒绝。Pending 允许读取元数据以验证接入。

CLI 对应命令为 `aiv query metadata events --param agent_uuid=<Agent UUID>`。

## Events 字段类型

```text
GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/metadata/events
```

返回当前 Space + Agent Product 配置的事件/日志数据源 schema 中发现的所有字段名和
字段类型。

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
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
}
```

## Traces 字段元数据

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

选中的 Agent 没有对应数据源时，接口返回成功，`source` 为 `null`，`fields` 为空数组。
Agent 不可访问或选择存在歧义时返回 400，不以空 schema 掩盖路由错误。


## 安全边界

Metadata 字段接口只返回经过脱敏的 schema 信息，不返回：

- SigNoz endpoint / access id / access token
- `namespace` / `table_name`
- datasource id
- query templates
- SpaceKey capabilities 或其他 key 级别权限配置

`space_id` 仍来自 URL，并通过 `Authorization: Bearer sk-aiv-...` 做 Space 级隔离；
调用方不能通过请求体或 query 参数覆盖 Space / Agent Product 数据源边界。
