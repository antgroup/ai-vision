# AI Vision 查询 OpenAPI 使用文档

本文档面向外部系统调用 AI Vision OpenAPI 查询 Agent Trace、Span、I/O 与 Token usage 数据。本期对外仅开放 Trace 相关接口。

## 1. 接入信息

| 项目 | 值 |
|---|---|
| Base URL | 本地：`http://localhost:5174`；其他部署使用实际地址 |
| OpenAPI 前缀 | `/openapi/v1` |
| 认证方式 | `Authorization: Bearer <API_KEY>` |
| 示例 Agent Product | `ClaudeCode` |
| 请求格式 | JSON |
| 响应格式 | `{code, message, data}` |

通用 Header：

```http
Authorization: Bearer sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
```

通用响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

## 2. 文档目录

| 文档 | 内容 |
|---|---|
| [0. 安装与使用](./0_setup.md) | API Key 获取入口、curl 查询与插件市场接入 |
| [1. 认证与权限](./1_authentication.md) | API Key Header、Space 权限边界、个人/业务空间隔离、错误码、限流 |
| [2. 过滤条件](./2_filters.md) | `env`、`username`、`tags`、`filters` 数组、操作符与示例 |
| [3. Trace 接口](./3_traces.md) | Trace 列表、Trace 详情、Span 批量/详情、I/O 与 usage 扩展 |
| [4. Trace Metadata](./4_metadata.md) | Trace 数据源字段类型元数据 |
| [5. curl 示例](./5_api_curl.md) | 可复制执行的 Trace curl 调用示例 |

## 3. 快速开始

```bash
# 本地示例；其他部署请替换为实际地址
export BASE_URL="http://localhost:5174"
export OPENAPI_PREFIX="/openapi/v1"
export API_KEY="sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export SPACE_ID="your-space-id"
export AGENT_PRODUCT="ClaudeCode"
export FROM_TIME="2026-06-29T00:00:00+08:00"
export TO_TIME="2026-06-30T23:59:59+08:00"

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

## 4. 接口清单

### Trace

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/openapi/v1/spaces/{space_id}/agents/{agent_product}/traces/list` | 查询 Trace 列表 |
| GET | `/openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/detail` | 查询 Trace 详情 |
| POST | `/openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/spans` | 批量查询 Span |
| GET | `/openapi/v1/spaces/{space_id}/agents/{agent_product}/trace/{trace_id}/span/{span_id}` | 查询单个 Span 详情 |

### Trace Metadata

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/openapi/v1/spaces/{space_id}/agents/{agent_product}/metadata/traces` | 查询 Trace 数据源字段元数据 |

## 5. 关键规则

1. `space_id` 必须与 API Key 所属 Space 一致。
2. `agent_product` 用于选择 Agent 数据源配置；底层产品隔离值以 DataSource 配置 `agent_product_filter` 为准。
3. 时间范围同时支持 ISO 8601 字符串和 Unix 毫秒时间戳。
4. Trace 列表接口支持 `env`、`username`、`tags`、`filters` 等过滤条件。
5. 列表接口可通过 query 参数 `io=true` 额外内联 I/O 内容。
6. Trace 列表和外部 Langfuse Session 列表可通过 query 参数 `usage=true` 内联 provider 支持的 usage。SigNoz/native Session 列表始终返回规范化的 usage、coverage 和 token 总数，未知值为 `null`，明确上报的零值为 `0`。
7. 个人空间会在服务端自动附加用户身份过滤，调用方不需要手动补充权限过滤。
8. SigNoz/native Session 列表在完整验证的 Space/product 窗口内本地筛选 Agent UUID、计算元数据、facets 和分页；上限为 UUID 筛选前 10,000 条物理 traces，空页也需要完整验证。默认每页 50 个 Session、最多 200 个，按 `last_activity` 降序排列。
