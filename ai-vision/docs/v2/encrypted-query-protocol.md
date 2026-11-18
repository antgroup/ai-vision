# 加密查询协议

> 版本：draft-v1
> 日期：2026-05-11

## 1. 目标

加密查询协议用于减少查询载荷以明文形式暴露。

它解决的问题：

1. 让查询请求体在浏览器网络面板中以加密结构呈现。
2. 减少请求日志中意外记录明文查询的概率。
3. 让前后端围绕一个稳定查询执行接口协作。

## 2. 选定方案：AES-GCM 共享密钥

使用 AES-GCM 和前后端共享对称密钥。协议加密查询请求体，授权由服务端校验。

浏览器从 `query-config` 响应中取得共享密钥，查询结果仍以 JSON 返回。通过普通 HTTP 访问时，网络路径上的第三方可以读取或篡改页面脚本、认证信息、密钥和查询结果。部署需使用 HTTPS/TLS 保护这些传输。

## 3. 请求开关

查询接口必须支持 query 参数：

```text
e=1
e=0
```

行为：

1. `e=1` 表示 request body 是加密协议结构。
2. `e=0` 表示 request body 是明文查询 payload。
3. 生产客户端缺省应该使用 `e=1`。
4. 后端可以只在开发环境或显式开启的内部环境中允许 `e=0`。

## 4. 加密请求体

```json
{
  "payloadVersion": "v1",
  "kid": "dashboard-query-v1",
  "iv": "base64url-encoded-iv",
  "encryptedPayload": "base64url-encoded-ciphertext-and-tag"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `payloadVersion` | Payload 版本。初始值：`v1`。 |
| `kid` | Key id，用于后续密钥轮换。 |
| `iv` | 12 字节 AES-GCM nonce，使用无填充的 base64url 编码。 |
| `encryptedPayload` | AES-GCM 密文拼接 16 字节认证标签，使用无填充的 base64url 编码。 |

## 5. 解密后的载荷

```json
{
  "scope": "personal",
  "queryKey": "claude_code.tokens.summary",
  "timeRange": {
    "type": "absolute",
    "from": 1710000000000,
    "to": 1710086400000
  },
  "params": {}
}
```

## 6. 前端实现

### 加密与密钥加载

`agentQueryCrypto.ts` 的 `encryptAgentQueryPayload()` 支持单查询和批量查询载荷。当前 Dashboard 通过 `dashboardQueryAdapter.ts` 发起单查询。生产构建设置 `VITE_AGENT_QUERY_ENCRYPTION_ENABLED=true` 后，适配器在加密前通过以下接口加载密钥。

```text
GET /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/query-config
```

后端校验登录和 Space 访问权限后，在响应中返回 `kid` 和 `keyB64`。推荐使用 256 位 AES 密钥。

### 编码规则

1. 使用 `JSON.stringify` 序列化 payload。
2. 编码成 UTF-8 字节。
3. 每次加密都调用浏览器的 `crypto.getRandomValues(new Uint8Array(12))` 生成新的 IV。
4. 使用 AES-GCM 加密，附加认证数据为空，认证标签为 16 字节。
5. 对 IV 和拼接了认证标签的密文分别做无填充的 base64url 编码。

浏览器优先使用原生 `SubtleCrypto`。普通 HTTP 等场景中若 `SubtleCrypto` 不可用，按需加载锁定版本的 `@noble/ciphers` 2.4.0 执行 AES-GCM。该版本处于上游支持范围，项目的 1.0.0 版本曾由 Cure53 独立审计。 [版本说明](https://github.com/paulmillr/noble-ciphers/releases/tag/2.4.0) · [支持政策](https://github.com/paulmillr/noble-ciphers/blob/main/SECURITY.md) · [审计报告](https://cure53.de/audit-report_noble-crypto-libs.pdf)

两种实现使用相同的 `v1` 请求结构和 `e=1`。随机数生成、密钥加载或加密失败时，客户端终止查询。明文模式只能通过显式配置启用。

### 共享查询客户端

业务组件不应该直接调用加密函数。

V2 API helper 通过以下路径发起 SigNoz 加密查询。

```text
POST /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/signoz/query?e=1
```

Dashboard、scope、section 和 widget 不应该直接拼接 `/api/v2` 请求。

在 `vfe` 中，推荐把这层封装放到：

```text
vfe/src/agents/lib/dashboardQueryAdapter.ts
```

它内部调用：

```text
vfe/src/agents/lib/observabilityV2Api.ts
vfe/src/agents/lib/agentQueryCrypto.ts
```

然后由各个 section 通过 `useSectionQuery` 或等价 hook 调用 `dashboardQueryAdapter`，再把转换后的数据传给 V2 widgets。

## 7. 后端实现

### 环境变量

推荐后端环境变量：

```text
AGENT_QUERY_ENCRYPTION_ENABLED=true
AGENT_QUERY_ALLOW_PLAIN=false
AGENT_QUERY_ENCRYPTION_KEYS={"dashboard-query-v1":"base64url-encoded-256-bit-key"}
```

后端应该支持多个 key，便于密钥轮换。

### 后端伪代码

```python
def parse_agent_query_request(request):
    encrypted = request.query_params.get("e", "1").lower() in {"1", "true", "yes"}

    if encrypted:
        body = decrypt_agent_query_payload(request.data)
    else:
        if not settings.AGENT_QUERY_ALLOW_PLAIN:
            raise PermissionDenied("Plain agent query is disabled")
        body = request.data

    validate_agent_query_payload(body)
    return body
```

### 解密要求

1. 校验 `payloadVersion`。
2. 校验 `kid` 是否存在。
3. base64url decode `iv`。
4. base64url decode `encryptedPayload`。
5. 使用 AES-GCM 解密。
6. 按 UTF-8 JSON 解析。
7. 拒绝结构异常的 payload。

## 8. 解密后的后端校验

解密之后，后端仍然必须：

1. 检查当前用户是否有 `space_id` 访问权限。
2. 检查 `agent_product` 是否在当前 space 中可用。
3. 检查 `scope` 是否支持。
4. 只从后端配置渲染 `{{table}}`。
5. 渲染 `{{time_filter}}` 时包含开始和结束边界。
6. 根据当前 user 和 space 渲染 `{{scope_filter}}`。
7. 拒绝未知模板变量。
8. 只使用后端配置的 endpoint 执行查询。

## 9. 密钥轮换

使用 `kid` 支持密钥轮换。

推荐流程：

1. 向后端 `AGENT_QUERY_ENCRYPTION_KEYS` 增加新 key，将其排在首位供 `query-config` 返回。
2. 部署后端。已打开的页面可能继续使用已加载的旧 key。
3. 后端保留旧 key 一段宽限期。
4. 稍后移除旧 key。

## 10. 日志策略

默认日志可以包含：

1. `space_id`
2. `agent_product`
3. `queryKey`
4. `scope`
5. encrypted 标记
6. 执行耗时
7. row count

默认日志不应该包含：

1. encrypted payload
2. 解密后的 template
3. 最终渲染后的 UCS
4. 加密 key

如果需要排查问题，最终渲染 UCS 只能在显式临时 debug flag 后记录。

## 11. 失败行为

推荐失败类型：

1. 解密失败：HTTP 400，`AGENT_QUERY_DECRYPT_FAILED`。
2. 明文查询被禁用：HTTP 403，`AGENT_QUERY_PLAIN_DISABLED`。
3. 未知 key id：HTTP 400，`AGENT_QUERY_UNKNOWN_KEY_ID`。
4. payload 结构非法：HTTP 400，`AGENT_QUERY_INVALID_PAYLOAD`。

不要在加密模式失败时自动降级到明文模式。
