# Trace/Observation Link 第三方接入指南

## 背景

AI Vision 提供两类免登录签名链接，供第三方系统跳转或 iframe 内嵌：

| 场景 | 页面 URL | 后端接口 |
|------|----------|----------|
| 打开单个 Trace 详情 | `/trace-link?payload=...&sig=...` | `/api/v1/trace-link/detail` |
| 打开单个 Observation 详情 | `/observation-link?payload=...&sig=...` | `/api/v1/observation-link/detail` |

两类链接都依赖 `payload + sig` 校验，不要求访问者登录 AI Vision。

## 推荐接入方式

第三方系统应在服务端生成签名链接，前端只负责跳转或 iframe 内嵌完整 URL。

不要在浏览器端生成签名，因为签名需要使用 AI Vision 空间的 Langfuse READ secret key。

## URL 形式

Trace 完整页面：

```text
https://aivision.example.com/trace-link?payload=...&sig=...
```

Trace 内嵌页面：

```text
https://aivision.example.com/trace-link?payload=...&sig=...&embed=1&theme=light
```

Observation 完整页面：

```text
https://aivision.example.com/observation-link?payload=...&sig=...
```

Observation 内嵌页面：

```text
https://aivision.example.com/observation-link?payload=...&sig=...&embed=1&theme=light
```

`embed` 和 `theme` 是前端展示参数，不参与签名。第三方可以在已生成的签名 URL 后追加或修改这两个参数。

`theme` 支持：

| 参数值 | 说明 |
|--------|------|
| `light` | 强制浅色主题 |
| `dark` | 强制深色主题 |
| `system` | 跟随浏览器系统主题 |

不传 `theme` 或传入非法值时，使用 AI Vision 当前默认主题逻辑。

当 URL 指定了合法 `theme` 时，页面主题由 URL 参数控制，`D` 快捷键切换主题不会生效。

## Payload 字段

`payload` 是 base64url 编码后的 JSON。编码时去掉 `=` padding。

Trace Link 必填字段：

```json
{
  "space_id": "AI Vision space UUID",
  "trace_id": "trace id",
  "iat": 1700000000,
  "exp": 1700000600,
  "v": 1
}
```

Observation Link 需要额外包含 `observation_id`：

```json
{
  "space_id": "AI Vision space UUID",
  "trace_id": "trace id",
  "observation_id": "observation id",
  "iat": 1700000000,
  "exp": 1700000600,
  "v": 1
}
```

可选字段：

```json
{
  "agent_product": "codex"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `space_id` | 是 | AI Vision 空间 UUID，不是空间数字 ID |
| `trace_id` | 是 | 目标 Trace ID |
| `observation_id` | Observation Link 必填 | 目标 Observation ID |
| `iat` | 是 | 签发时间，Unix 秒级时间戳 |
| `exp` | 是 | 过期时间，Unix 秒级时间戳 |
| `v` | 是 | 当前固定为 `1` |
| `agent_product` | 否 | 多 Agent 产品数据源场景下用于路由查询 |

后端会校验：

- `payload` JSON 格式合法
- 必填字段存在
- `v === 1`
- `exp > iat`
- 链接未过期
- TTL 不超过后端允许的最大窗口
- `space_id` 存在且启用
- 使用该空间 READ secret key 计算出的签名和 `sig` 一致
- Observation Link 的 `observation_id` 属于 `trace_id` 对应 Trace

## 签名算法

1. 将 payload JSON 用紧凑格式序列化。
2. 对 JSON bytes 做 base64url 编码，并去掉 `=` padding，得到 `payload`。
3. 使用 AI Vision 空间 Langfuse READ secret key 计算：

```text
HMAC-SHA256(secret_key, payload)
```

4. 将 digest 做 base64url 编码，并去掉 `=` padding，得到 `sig`。

Python 示例：

```python
import base64
import hashlib
import hmac
import json
import time
import urllib.parse


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def build_link(
    *,
    host: str,
    secret_key: str,
    space_id: str,
    trace_id: str,
    observation_id: str | None = None,
    agent_product: str | None = None,
    ttl_seconds: int = 300,
    embed: bool = True,
    theme: str | None = None,
) -> str:
    now = int(time.time())
    payload_obj = {
        "space_id": space_id,
        "trace_id": trace_id,
        "iat": now,
        "exp": now + ttl_seconds,
        "v": 1,
    }
    if observation_id:
        payload_obj["observation_id"] = observation_id
    if agent_product:
        payload_obj["agent_product"] = agent_product

    payload_json = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False)
    payload = b64url(payload_json.encode("utf-8"))
    sig = b64url(
        hmac.new(
            secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )

    path = "observation-link" if observation_id else "trace-link"
    url = f"{host.rstrip('/')}/{path}?payload={urllib.parse.quote(payload)}&sig={urllib.parse.quote(sig)}"
    if embed:
        url += "&embed=1"
    if theme in {"light", "dark", "system"}:
        url += f"&theme={theme}"
    return url
```

## 第三方前端内嵌

第三方前端拿到服务端生成的完整链接后，直接放入 iframe。

HTML 示例：

```html
<iframe
  src="https://aivision.example.com/observation-link?payload=xxx&sig=yyy&embed=1&theme=light"
  style="width: 100%; height: 720px; border: 0;"
  allow="clipboard-read; clipboard-write"
></iframe>
```

React 示例：

```tsx
export function AIVisionEmbed({ url }: { url: string }) {
  return (
    <iframe
      src={url}
      title="AI Vision Detail"
      style={{ width: "100%", height: 720, border: 0 }}
      allow="clipboard-read; clipboard-write"
    />
  )
}
```

如果第三方拿到的是不带 `embed=1` 或 `theme` 的完整链接，可以只追加 `embed=1&theme=light|dark|system`。不要解析、修改、重签 `payload`。

## AI Vision 前端行为

`embed=1` 时：

- 不展示左侧导航栏和品牌区
- 不展示返回首页入口
- Trace Link 只展示 Trace 详情内容
- Observation Link 只展示 Observation 详情内容
- 页面容器使用 `height: 100vh; width: 100%; overflow: hidden`
- 如果 URL 包含 `theme=light|dark|system`，按该参数设置主题并禁用 `D` 快捷切换

不带 `embed=1` 时：

- 展示 AI Vision 侧边栏
- 侧边栏宽度和 AI Vision 首页一致，支持自适应和折叠
- 点击品牌区可回到 AI Vision 首页

## Trace Link 内部 Observation 明细

从 `/trace-link` 页面内部点击 Observation 明细时，前端会调用：

```text
/api/v1/trace-link/observations/:observation_id?payload=...&sig=...
```

这是 Trace 页面内部使用的接口，用于保证被打开的 Observation 属于当前签名 Trace。

第三方如果要直接打开某个 Observation，应生成 `/observation-link`，并在 payload 中签入 `observation_id`，不要把 `/api/v1/trace-link/observations/:id` 当成第三方页面入口。

## 本地生成验证链接

可以使用仓库内工具生成签名链接：

```bash
python3 tools/trace-link/gen_trace_link.py
```

生成普通 Trace 页面链接：

```bash
python3 tools/trace-link/gen_trace_link.py
```

生成内嵌 Trace 页面链接：

```bash
python3 tools/trace-link/gen_trace_link.py --embed
```

生成普通 Observation 页面链接：

```bash
python3 tools/trace-link/gen_trace_link.py --kind observation --observation-id <observation-id>
```

生成内嵌 Observation 页面链接：

```bash
python3 tools/trace-link/gen_trace_link.py --kind observation --observation-id <observation-id> --embed
```

工具脚本中的常量需要按环境调整：

| 常量 | 说明 |
|------|------|
| `SPACE_ID` | AI Vision 空间 UUID |
| `TRACE_ID` | 目标 Trace ID |
| `OBSERVATION_ID` | 目标 Observation ID，也可以通过 `--observation-id` 传入 |
| `BASIC_AUTH_B64` | READ 凭证的 `public_key:secret_key` base64，脚本会取其中的 secret key 签名 |
| `AIVISION_HOST` | 前端访问地址，如 `http://127.0.0.1:8080` |
| `TTL_SECONDS` | 链接有效期 |

示例输出：

```text
url = http://127.0.0.1:8080/observation-link?payload=...&sig=...&embed=1
```

## 第三方注意事项

- 签名必须由第三方服务端生成，不能把 secret key 下发到浏览器。
- 不要解析或改写 `payload` 内容，直接透传完整链接。
- 只追加 `embed=1` 和 `theme=light|dark|system`，不要重新签名。
- iframe 容器高度建议不少于 `600px`。
- 链接可能过期，过期后 iframe 内会展示链接无效或已过期。

## 网关和安全要求

如果使用 iframe 内嵌，AI Vision 页面响应不能被浏览器拦截。

需要确认不要返回：

```http
X-Frame-Options: DENY
X-Frame-Options: SAMEORIGIN
```

如果配置 CSP，需允许第三方域名作为 frame ancestor：

```http
Content-Security-Policy: frame-ancestors 'self' https://third-party.example.com;
```

生产环境应使用第三方产品的真实域名白名单，不建议使用通配 `*`。

## 安全边界

- `payload + sig` 是访问授权凭证，第三方不应记录或二次分发。
- `payload` 必须包含过期时间。
- 外部产品不需要 AI Vision 登录态。
- 外部产品不应获得空间 API Key 或 Langfuse WRITE secret key。
