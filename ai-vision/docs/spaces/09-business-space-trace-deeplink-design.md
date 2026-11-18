# 09. 业务空间 Trace Deeplink 设计

## 目录

- [文档范围](#文档范围)
- [1. 背景与问题定义](#1-背景与问题定义)
- [2. 目标与非目标](#2-目标与非目标)
- [3. 权限模型](#3-权限模型)
- [4. 总体方案](#4-总体方案)
- [5. Deeplink 协议设计](#5-deeplink-协议设计)
- [6. AIVision 校验链路](#6-aivision-校验链路)
- [7. 前后端改造建议](#7-前后端改造建议)
- [8. 上游接入方式](#8-上游接入方式)
- [9. 安全设计](#9-安全设计)
- [10. 风险与后续演进](#10-风险与后续演进)

## 文档范围

本文聚焦“业务平台用户点击上游平台中的链接，跳转到 AIVision 查看某一条 trace 详情”的 deeplink 设计。

本文覆盖：

- 跳转授权模型
- HMAC 协议设计
- AIVision 校验链路
- 前后端落地边界
- 上游接入方式

本文不覆盖：

- 业务最终用户身份体系接入
- 业务用户在 AIVision 内的账号模型
- 单次消费、吊销中心、审计中心等二期增强能力

## 1. 背景与问题定义

当前业务空间已经具备：

1. 空间级一读一写 key
2. 基于 Langfuse Public API 兼容层的 trace / observation 查询能力
3. AIVision 控制台中的 trace 列表和 trace 详情页

但现状仍有一个缺口：

- 业务 owner 希望在自己的业务平台中，为某条 trace 提供“跳转查看 AIVision 详情”的入口
- 点击后用户应直接看到该 trace 详情，而不是先进入 AIVision 的完整空间视图再自行搜索
- 同时不能允许用户通过篡改 URL 参数查看同一空间下的其他 trace

已有实现不适合直接复用：

1. 浏览器页面跳转不会自动携带上游平台调用 API 时使用的 `Authorization` header
2. 当前内部控制台接口 `/api/v1/spaces/{space_id}/traces/{trace_id}` 是“空间成员态”权限模型，只校验当前登录用户是否为该空间成员，不适合直接承接“链接持有者授权”语义
3. 业务场景不要求 AIVision 识别最终点击人的业务身份，是否展示跳转入口由上游平台自行决定

因此本次需要新增一套“空间级受限 deeplink”能力。

## 2. 目标与非目标

### 2.1 目标

1. 上游平台可为某条 trace 生成一个可直接打开的 AIVision 链接
2. 链接具备防篡改能力，不能通过修改 `trace_id`、`space_id` 越权查看其他数据
3. 链接具备时效性，过期后自动失效
4. 最终打开链接时不依赖浏览器额外携带 `Authorization` header
5. 不要求为最终点击人创建 AIVision 空间成员身份

### 2.2 非目标

1. 不解决“链接被转发后仍然只能原始点击人可见”的问题
2. 不解决跨空间聚合跳转
3. 不在一期引入新的上游注册中心或审批流程
4. 不在一期引入 observation 级单独分享链接

## 3. 权限模型

本方案采用的不是“最终用户身份授权”，而是“链接持有者授权”。

语义如下：

1. 上游平台决定是否向某个业务用户展示“查看 trace”入口
2. 一旦上游生成合法 deeplink，持有该链接的人即可查看这条 trace
3. AIVision 负责保证该链接只能访问其绑定的那一条 trace，不能通过改参数查看其他 trace

该模型的边界是：

- 能防止参数篡改带来的越权
- 不能防止链接被转发后的再次使用

如果后续需要“单次消费”“可撤销”“绑定用户态”的更强语义，应在本方案之上继续叠加能力。

## 4. 总体方案

### 4.1 方案概述

采用“空间 READ Key + HMAC 签名”的方式生成 deeplink。

流程：

1. 上游后端持有该业务空间的 READ Key
2. 上游后端为某条 trace 构造 payload
3. 上游后端使用 READ Key 的 `secret_key` 对 payload 做 HMAC-SHA256 签名
4. 上游前端将生成后的 URL 作为普通跳转链接展示给用户
5. 用户点击链接后，AIVision 根据 payload 中的 `space_id` 找到空间对应的 READ credential
6. AIVision 使用该 credential 的 secret 校验签名与有效期
7. 校验通过后，仅返回该 payload 对应的 trace 详情

### 4.2 为什么使用 HMAC 而不是纯加密

本场景真正需要解决的是：

1. 参数不可篡改
2. 链接具备时效
3. AIVision 能验证该链接确实由该空间的持钥方生成

本场景并不强依赖“隐藏 `space_id` 和 `trace_id` 的明文值”，因此：

- 使用 HMAC 可直接满足防篡改需求
- 协议更简单，跨语言实现更稳定
- 不需要额外处理对称加密后的完整性问题

因此一期不建议引入 AES 等对称加密，只做 HMAC 签名。

## 5. Deeplink 协议设计

### 5.1 URL 形态

建议前端页面入口为：

```text
/trace-link?payload=<base64url_payload>&sig=<base64url_signature>
```

说明：

- `payload` 为原始 JSON 的 base64url 编码结果
- `sig` 为对 `payload` 原始字符串做 HMAC-SHA256 后的 base64url 编码结果

### 5.2 Payload 字段

建议 payload JSON 结构如下：

```json
{
  "space_id": "0b5c8d48-37f4-4e18-9e76-9b6fe36b9fa5",
  "trace_id": "2dfdc141a59a2c68b34b1bb4135960d1",
  "agent_product": "codex",
  "iat": 1776769200,
  "exp": 1776769800,
  "v": 1
}
```

字段说明：

- `space_id`
  - 必填
  - AIVision 业务空间 UUID
- `trace_id`
  - 必填
  - 允许查看的唯一 trace
- `agent_product`
  - 可选但建议带上
  - 用于选择该空间下对应 agent 的观测配置，也便于前端回显上下文
- `iat`
  - 必填
  - 签发时间，Unix timestamp，单位秒
- `exp`
  - 必填
  - 过期时间，Unix timestamp，单位秒
- `v`
  - 必填
  - 协议版本，便于后续扩展

### 5.3 签名算法

签名输入：

```text
payload_raw = "<query 中的 payload 原始字符串>"
signature = HMAC_SHA256(read_secret_key, payload_raw)
sig = base64url(signature)
```

说明：

1. 签名对象是 `payload` 的原始字符串，而不是 parse JSON 后重新排序的结果
2. 上下游只要对同一 `payload` 原始值签名即可，不必额外约定 JSON key 排序
3. 为避免 URL 特殊字符问题，`payload` 和 `sig` 都使用 base64url

### 5.4 TTL 约定

建议上游在生成链接时使用短期有效期：

- 推荐：5 分钟到 10 分钟
- 一期上限：30 分钟

AIVision 校验时除了判断 `now < exp`，还应额外限制：

```text
exp - iat <= 1800
```

防止上游签出过长有效期的链接。

## 6. AIVision 校验链路

### 6.1 校验步骤

AIVision 在接收到 `/trace-link` 请求后，校验顺序建议如下：

1. 读取 query 中的 `payload` 和 `sig`
2. base64url 解码 `payload`
3. parse payload JSON
4. 校验必填字段是否齐全
5. 校验 `v` 是否为支持的协议版本
6. 校验 `iat`、`exp` 的格式与时间范围
7. 校验 `exp > now`
8. 校验 `exp - iat <= 1800`
9. 使用 `space_id` 查询 `Space`
10. 查询该空间下状态为有效的 READ credential
11. 解密 `secret_ciphertext` 拿到 `secret_key`
12. 以 `payload` 原始字符串重新计算 HMAC
13. 使用常量时间比较校验 `sig`
14. 获取该空间对应的 Langfuse runtime config
15. 按 `trace_id` 请求上游 trace detail
16. 校验返回数据中的 `trace.id == payload.trace_id`
17. 返回 trace detail 给前端 deeplink 页面

### 6.2 为什么要根据 `space_id` 查 READ credential

浏览器最终打开 deeplink 时，无法像后端调用那样可靠携带：

```text
Authorization: Basic base64(public_key:secret_key)
```

因此 deeplink 最终打开阶段不依赖 header，而是：

1. 从 payload 中取得 `space_id`
2. 由 AIVision 在服务端查到该空间的 READ credential
3. 用该 credential 的 secret 完成验签

这保证了：

- 浏览器只需要普通 URL 跳转即可
- 上游前端不暴露 READ Key
- AIVision 仍然能够确认该链接是否由持有该空间 READ Key 的服务端生成

### 6.3 Observation 详情约束

如果 deeplink 页面后续仍需按 observation 单独请求详情，则必须增加额外校验：

1. 先验证 deeplink payload 和签名
2. 获取主 trace detail
3. 只允许查看属于该 `trace_id` 的 observation

不能开放“只要带上合法 deeplink 就能查询任意 `observation_id`”。

## 7. 前后端改造建议

### 7.1 后端

建议新增一组独立于“空间成员态”的 deeplink 接口，不直接复用：

```text
GET /api/v1/trace-link/detail?payload=...&sig=...
```

后端 helper 建议：

- `parse_trace_link_payload(payload_raw: str) -> dict`
- `validate_trace_link_claims(claims: dict) -> None`
- `get_space_read_credential(space: Space) -> SpaceObservationCredential`
- `verify_trace_link_signature(secret_key: str, payload_raw: str, sig: str) -> bool`

建议不要直接复用当前 `/api/v1/spaces/{space_id}/traces/{trace_id}`：

1. 该接口当前基于空间成员身份校验
2. deeplink 场景是另一套权限模型
3. 混用容易导致权限判断分支继续膨胀

### 7.2 前端

建议新增独立页面：

```text
/trace-link
```

页面职责：

1. 从 URL 读取 `payload` 和 `sig`
2. 请求 `/api/v1/trace-link/detail`
3. 只展示该条 trace 的详情

建议前端不要直接复用当前完整 `Trace` 页作为 deeplink 落点，原因：

1. 当前 `Trace` 页包含空间切换、列表、搜索、Session 等完整控制台能力
2. deeplink 场景只需要“单条 trace 只读详情”
3. 直接落到完整 `Trace` 页会放大误用和越权检查成本

推荐做法：

- 复用现有 `TraceDetailPanel` 中的详情展示组件能力
- 移除列表、空间切换、搜索、其它 trace 跳转
- 保持只读

## 8. 上游接入方式

### 8.1 适用对象

适用于：

- 已经接入 AIVision 业务空间 READ Key 的上游服务端
- 需要在业务平台页面中，为某条 trace 提供“查看 AIVision 详情”入口的业务系统

### 8.2 接入前提

上游需要准备：

1. `space_id`
2. 该空间的 READ Key
   - `public_key`
   - `secret_key`
3. 待跳转的 `trace_id`
4. AIVision 的 deeplink 页面地址

注意：

- 上游前端页面不应保存 `secret_key`
- HMAC 签名必须在上游服务端生成

### 8.3 接入步骤

#### 步骤 1：上游后端组装 payload

示例：

```json
{
  "space_id": "0b5c8d48-37f4-4e18-9e76-9b6fe36b9fa5",
  "trace_id": "2dfdc141a59a2c68b34b1bb4135960d1",
  "agent_product": "codex",
  "iat": 1776769200,
  "exp": 1776769800,
  "v": 1
}
```

#### 步骤 2：将 payload JSON 做 base64url 编码

得到：

```text
payload=<base64url(json)>
```

#### 步骤 3：使用 READ Key 的 `secret_key` 计算 HMAC-SHA256

签名对象为第 2 步中的 `payload` 原始字符串：

```text
sig = base64url(HMAC_SHA256(secret_key, payload))
```

#### 步骤 4：生成完整跳转 URL

```text
https://{aivision-host}/trace-link?payload=...&sig=...
```

#### 步骤 5：上游前端将该 URL 渲染为普通跳转链接

例如：

- 按钮点击后 `window.open(url, '_blank')`
- 或 `<a href="{url}" target="_blank">查看详情</a>`

浏览器最终只需要普通页面跳转，不要求携带 `Authorization` header。

### 8.4 参考伪代码

Python 伪代码：

```python
import base64
import hashlib
import hmac
import json
import time


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def sign_trace_link(space_id: str, trace_id: str, agent_product: str, secret_key: str) -> str:
    now = int(time.time())
    payload_obj = {
        "space_id": space_id,
        "trace_id": trace_id,
        "agent_product": agent_product,
        "iat": now,
        "exp": now + 600,
        "v": 1,
    }
    payload_json = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False)
    payload = b64url(payload_json.encode("utf-8"))
    sig = b64url(hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest())
    return f"https://{{aivision-host}}/trace-link?payload={payload}&sig={sig}"
```

JavaScript 伪代码：

```ts
import crypto from 'node:crypto';

function b64url(input: Buffer | string) {
  return Buffer.from(input)
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

export function signTraceLink(spaceId: string, traceId: string, agentProduct: string, secretKey: string) {
  const now = Math.floor(Date.now() / 1000);
  const payloadObj = {
    space_id: spaceId,
    trace_id: traceId,
    agent_product: agentProduct,
    iat: now,
    exp: now + 600,
    v: 1,
  };
  const payload = b64url(JSON.stringify(payloadObj));
  const sig = b64url(crypto.createHmac('sha256', secretKey).update(payload).digest());
  return `https://{aivision-host}/trace-link?payload=${payload}&sig=${sig}`;
}
```

### 8.5 脱敏接入样例

下面给出一个可直接给上游参考的脱敏 Python 样例。该样例展示了如何从空间 READ Key 的 Basic Auth 值中解析 `secret_key`，并生成最终 deeplink URL。

注意：

1. 样例中的空间、trace、key 都是占位值
2. 真实 `secret_key` 只能保存在上游服务端，不能下发到前端
3. 上游前端只消费最终生成的 URL

```python
import base64
import hashlib
import hmac
import json
import time
import urllib.parse


SPACE_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
TRACE_ID = "trace_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
BASIC_AUTH_B64 = "base64(public_key:secret_key)"
AIVISION_HOST = "https://aivision.example.com"
TTL_SECONDS = 600


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def sign_trace_link() -> str:
    decoded = base64.b64decode(BASIC_AUTH_B64).decode("utf-8")
    _, secret_key = decoded.split(":", 1)

    now = int(time.time())
    payload_obj = {
        "space_id": SPACE_ID,
        "trace_id": TRACE_ID,
        "iat": now,
        "exp": now + TTL_SECONDS,
        "v": 1,
    }

    payload_json = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False)
    payload = b64url(payload_json.encode("utf-8"))
    sig = b64url(
        hmac.new(
            secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )

    return f"{AIVISION_HOST}/trace-link?payload={urllib.parse.quote(payload)}&sig={urllib.parse.quote(sig)}"


if __name__ == "__main__":
    print(sign_trace_link())
```

### 8.6 失败处理建议

上游接入时建议按以下错误语义处理：

- 链接过期：提示“链接已过期，请返回业务平台重新打开”
- 签名错误：提示“链接无效”
- trace 不存在：提示“目标 trace 不存在或已不可访问”
- AIVision 上游异常：提示“详情暂时不可用，请稍后重试”

## 9. 安全设计

### 9.1 一期必须满足

1. 使用 `READ` key 的 `secret_key` 做 HMAC-SHA256
2. 只允许短期有效链接
3. 后端使用常量时间比较签名
4. 只允许访问 payload 中声明的单条 trace
5. deeplink 页面不暴露空间切换、列表浏览、全空间搜索能力

### 9.2 明确不保证的事项

一期不保证：

1. 链接不被转发
2. 同一链接不能重复打开
3. AIVision 识别“最终点击人是谁”

这些能力需要后续引入：

- `jti`
- 单次消费落库
- 审计日志
- 用户态绑定

### 9.3 凭证使用边界

虽然上游持有 READ Key 的 `secret_key`，但该 key 本身已经是该业务空间的既有读取凭证，因此：

- 上游能签发该空间内 trace 的 deeplink，符合该空间 owner 的授权边界
- AIVision 不应再引入“全局共享总密钥”来签 deeplink
- 这样可以天然保持“一个空间一把密钥”的隔离边界

## 10. 风险与后续演进

### 10.1 一期风险

1. 若上游将链接长期缓存，可能与 AIVision 的 TTL 校验冲突
2. 若上游误把 `secret_key` 暴露到前端，会导致该空间 query 与 deeplink 能力同时泄露
3. deeplink 页面若继续复用 observation 二次请求，需要补齐 trace 归属校验

### 10.2 二期演进方向

1. 增加 `jti`，支持单次消费和撤销
2. 增加访问审计日志，记录 `space_id`、`trace_id`、来源 IP、UA
3. 支持业务方服务端换取一次性页面 token，进一步缩短暴露面
4. 若业务要求“不可转发”，可在 deeplink 基础上叠加业务用户态绑定

## 结论

业务空间 trace deeplink 一期建议采用：

1. 上游服务端使用该空间 READ Key 的 `secret_key` 对 `payload` 做 HMAC-SHA256
2. 浏览器只打开带 `payload + sig` 的普通 URL
3. AIVision 按 `space_id` 查空间 READ credential 完成验签
4. 只提供单条 trace 的只读详情页，不复用完整空间控制台页

该方案实现成本低、与现有“一空间一读一写 key”模型一致，且能满足“业务平台发起跳转、AIVision 验签后展示单条 trace 详情”的一期目标。
