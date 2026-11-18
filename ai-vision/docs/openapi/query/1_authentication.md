# OpenAPI 认证说明

本文说明 AI Vision OpenAPI 的对外认证方式。当前对外接入统一使用 **Space API Key**，通过标准 `Authorization` Header 传递。

- Base URL：本地示例 `http://localhost:5174`，其他部署请替换为实际地址
- OpenAPI 前缀：`/openapi/v1`
- 认证方式：`Authorization: Bearer <API_KEY>`
- API Key 格式：`sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

> 本文仅覆盖对外 OpenAPI 调用所需的 Bearer API Key 认证。

---

## 1. 请求 Header

所有 OpenAPI 请求都需要携带以下 Header：

```http
Authorization: Bearer sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
```

curl 示例：

```bash
# 本地示例；其他部署请替换为实际地址
export BASE_URL="http://localhost:5174"
export OPENAPI_PREFIX="/openapi/v1"
export API_KEY="sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export SPACE_ID="your-space-id"
export AGENT_PRODUCT="ClaudeCode"

curl -sS -X POST "${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/traces/list" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data '{
    "page": 1,
    "limit": 20
  }'
```

---

## 2. API Key 的权限边界

API Key 是 **Space 级别** 的凭证：

1. 一个 API Key 只属于一个 Space。
2. 请求路径中的 `{space_id}` 必须与 API Key 所属 Space 一致。
3. API Key 不是 Agent 级别凭证；`{agent_product}` 用于选择该 Space 下对应 Agent 的数据源配置。
4. API Key 被禁用或删除后会立即失效。

路径示例：

```text
/openapi/v1/spaces/{space_id}/agents/{agent_product}/traces/list
```

其中：

| 参数 | 含义 |
|---|---|
| `space_id` | API Key 所属 Space 的 UUID |
| `agent_product` | Agent 产品标识，例如 `ClaudeCode` |

如果 API Key 不属于请求中的 `space_id`，接口会返回 `403`。

---

## 3. 个人空间与业务空间

OpenAPI 会根据 Space 类型自动应用权限边界：

| Space 类型 | 查询范围 |
|---|---|
| 个人空间 | 自动按 API Key 创建者身份做用户维度过滤 |
| 业务空间 | 查询该 Space 授权范围内的数据；可通过请求参数继续按 `username`、`env`、`tags` 等过滤 |

个人空间下不需要调用方手动补充 `username` 来保证隔离；服务端会自动加上用户身份约束。

业务空间下如果需要缩小到某个用户，可以在业务过滤条件中添加 `username` 或 `filters`。过滤写法见 [`2_filters.md`](./2_filters.md)。

---

## 4. API Key 获取与管理

API Key 由 Space 管理入口创建和管理：

- 创建：需要 Space 的 Admin 或 Developer 权限。
- 数量限制：单个 Space 最多保留 10 个 API Key。
- 展示规则：完整 Key 只在创建时返回一次；后续列表只展示前缀。
- 禁用：禁用后请求会返回认证失败。
- 删除：删除为软删除；删除后请求会返回认证失败。
- 使用记录：每次成功认证会更新 `last_used_at`。

建议为不同调用方、环境或系统分别创建独立 API Key，便于审计和轮换。

---

## 5. 常见错误

OpenAPI 通常返回统一结构：

```json
{
  "code": 4001,
  "message": "Authentication required",
  "data": null
}
```

常见认证相关错误：

| HTTP 状态码 | code | 含义 | 常见原因 |
|---|---:|---|---|
| `401` | `4001` | 未认证或 API Key 无效 | Header 缺失、Key 格式错误、Key 被禁用/删除 |
| `403` | `4003` | 无权访问该 Space | API Key 所属 Space 与路径中的 `space_id` 不一致 |
| `429` | `4029` | 请求触发限流 | 同一外部凭证请求过快 |

示例：API Key 与 Space 不匹配

```json
{
  "code": 4003,
  "message": "Space Key does not match the requested space",
  "data": null
}
```

示例：API Key 无效或已禁用

```json
{
  "code": 4001,
  "message": "Invalid or disabled Space Key",
  "data": null
}
```

---

## 6. 限流

OpenAPI 对外部凭证启用限流，当前默认值为：

```text
1000 requests / minute / API Key
```

触发限流时返回：

```json
{
  "code": 4029,
  "message": "Request throttled. Retry after 12 seconds.",
  "data": null
}
```

建议客户端在收到 `429` 后按 `message` 中的等待时间进行退避重试。

---

## 7. 安全建议

1. 不要把 API Key 提交到 Git、文档截图、日志或前端代码中。
2. 本地调试建议通过环境变量传入：

   ```bash
   export API_KEY="sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   ```

3. 生产环境必须使用 HTTPS。
4. 不同系统、不同环境使用不同 API Key。
5. 发现泄漏后立即禁用或删除旧 Key，并创建新 Key。
6. 定期轮换长期使用的 API Key。
7. 只把 API Key 分发给实际需要访问该 Space 数据的系统。

---

## 8. 调试检查清单

请求认证失败时，按以下顺序检查：

1. Header 是否为 `Authorization: Bearer <API_KEY>`。
2. API Key 是否完整，是否包含 `sk-aiv-` 前缀。
3. 请求路径中的 `space_id` 是否是该 Key 所属 Space。
4. API Key 是否被禁用或删除。
5. 是否触发了限流。
6. `agent_product` 是否拼写正确，例如 `ClaudeCode`。
7. 如果返回为空数据，再检查时间范围、过滤条件和数据源配置。

完整调用示例见 [`5_api_curl.md`](./5_api_curl.md)。
