# Session/Trace 临时分享链接 — 技术设计

## 1. 背景

AI Vision 当前仅上线了个人空间能力，个人空间数据只有本人可见。用户在排查 AI Agent 调用链问题时，需要把某条 session 或 trace 分享给同事协作排查，但目前不存在任何途径将个人空间的 trace/session 分享给他人查看。

核心痛点：截图信息丢失、无法交互、发页面链接对方打不开。

## 2. 方案选型

采用「临时分享链接」方案，不采用快照。

| 维度 | 快照（数据副本） | 临时分享链接（授权访问） |
|------|----------------|----------------------|
| 原理 | 序列化当前数据存到 OSS | 生成带签名的链接，临时授权访问 |
| 数据新鲜度 | 冻结的 | 实时的 |
| 存储成本 | 每次分享存一份副本 | **无存储**，签名自包含 |
| 开发成本 | 序列化逻辑 + 独立快照渲染页 | 复用现有详情页 + 签名验证 |
| 可撤销 | N/A | 不支持（7 天自动失效） |

## 3. 技术方案

### 3.1 Token 设计：无状态 HMAC 签名

与现有 trace-link 完全同构：`payload` (base64url JSON) + `sig` (HMAC-SHA256)。

**Claims 结构：**

```json
{
  "t": "trace",            // resource_type: "trace" | "session"
  "id": "trace-abc123",    // resource_id
  "s": 42,                 // space_id
  "a": "claude-code",      // agent_product
  "p": "lf",               // protocol: "lf" | "signoz"
  "iat": 1748969600,       // issued_at (unix timestamp)
  "exp": 1749574400,       // expires_at (iat + TTL)
  "u": "花名",             // sharer_name
  "v": 1                   // version
}
```

**签名密钥：** `SHARE_LINK_SIGNING_KEY` 环境变量，不依赖 per-space 观测凭证。

**有效期：** 默认 7 天，可通过 Django admin 的 `PlatformConfig(platform_code="share_link")` 动态配置 `config.ttl_seconds`。

**URL 格式：** `https://{domain}/share/{resource_type}?payload={base64url}&sig={hmac_hex}`

- Trace 分享：`https://{domain}/share/trace?payload=...&sig=...`
- Session 分享：`https://{domain}/share/session?payload=...&sig=...`

### 3.2 审计日志

不存 DB，通过 Python logger 结构化输出：

```python
logger.info("share_link_%s", action, extra={
    "action": action,             # "create" | "view"
    "resource_type": claims["t"],
    "resource_id": claims["id"],
    "space_id": claims["s"],
    "agent_product": claims["a"],
    "operator_id": user.uuid,
    "operator_name": user.name,
    "expires_at": claims["exp"],
})
```

日志随 OTel logging exporter 进入现有日志采集链路。

### 3.3 后端 API

#### 管理端（需登录 + 空间成员校验）

**POST `/api/v1/spaces/{space_id}/share-links/generate`**

- 构建 claims，HMAC 签名，拼接完整 URL
- 记录 create 审计日志
- 返回 `{ url, expires_at }`

#### 分享页端（免登录，签名自鉴权）

**GET `/api/v1/share/data?payload=...&sig=...`**

- `authentication_classes = []`, `permission_classes = [AllowAny]`（同 trace-link 模式）
- 验签 + 校验过期
- 根据 `resource_type` 调用 `TraceQueryService.get_trace_detail()` 或 `.get_session_detail()`
- 根据 claims 中 `protocol` 字段设置 `source_system_override`（`signoz` → signoz）
- 记录 view 审计日志
- 返回 `{ resource_type, trace/session, share_info, agent_product, space_id, protocol }`

**GET `/api/v1/share/observations/{observation_id}?payload=...&sig=...`**

- `authentication_classes = []`, `permission_classes = [AllowAny]`
- 验签后获取 observation 详情
- 用于 trace 分享页内点击 span 查看详情

**GET `/api/v1/share/trace/{trace_id}?payload=...&sig=...`**

- `authentication_classes = []`, `permission_classes = [AllowAny]`（ShareTraceDetailView）
- 验签后获取 trace 详情
- 用于 session 分享页内钻取到 trace 详情

#### 认证豁免配置

与 trace-link 保持一致，以下路径在三层认证中豁免：

| 层 | 配置位置 | 豁免路径 |
|---|---------|---------|
| DRF 认证边界 | 匿名只读 + 签名 Payload | `/api/v1/share/`, `/share/trace`, `/share/session` |
| DRF 认证 | `authentication.py` `IAM_EXEMPT_PATHS` | `/api/v1/share/` |
| View 级别 | `share.py` 视图类 | `authentication_classes = []` |

### 3.4 前端方案

#### 分享按钮

在 4 个详情面板（TraceDetailPanel、SessionDetailPanel、NativeTraceDetail、NativeSessionDetailPanel）的 header icon 区增加 `Share2` 图标按钮。

#### ShareDialog

点击分享按钮弹出 Dialog：
- 调用 generate API 获取分享链接
- 只读 Input 展示链接（点击全选）
- 复制链接按钮 + toast 提示
- 灰字有效期说明

#### ShareDialog

标题区分资源类型：「分享链接 - Trace」或「分享链接 - Session」。

如果后端返回的 URL 为 path-only（`/share/trace?...`），前端自动用 `window.location.origin` 补全完整域名。

#### SharePage

路由 `/share/trace` 和 `/share/session`（AppShell 外），参照 `TraceLinkPage` 模式：
- 从 URL 读 `payload` + `sig` query params
- 调用 share/data API 获取数据（`skipAuthRedirect: true`）
- **根据 `protocol` 字段分支渲染**：
  - `protocol === "lf"`：Langfuse 组件（TraceDetailPanel / SessionDetailPanel），传入 `traceData` / `sessionData` prop 跳过自行 fetch
  - `protocol === "signoz"`：Native 组件（NativeTraceDetail / NativeSessionDetailPanel），传入 spaceId、traceId/sessionId 由组件自行 fetch
- `showHeaderActions={false}` 隐藏操作按钮
- 顶部渲染分享 Banner（分享者、时间、有效期）
- 错误态：过期、无效、数据不存在

#### SessionDetailPanel 改造

新增 `sessionData` prop（类似 TraceDetailPanel 的 `traceData`），有值时跳过 useQuery fetch。
新增 `showHeaderActions` prop，控制 header 操作按钮显示。

### 3.5 与现有 trace-link 的关系

| 维度 | trace-link | share-link |
|------|-----------|------------|
| 用途 | 嵌入外部系统 | 用户间协作排查 |
| 签名密钥 | per-space READ credential | 全局 `SHARE_LINK_SIGNING_KEY` |
| TTL | 30 分钟 | 7 天 |
| 认证 | 完全免登录 | 完全免登录（签名自鉴权） |
| 支持资源 | trace + observation | trace + session |
| 数据源 | 仅 Langfuse | Langfuse + SigNoz |

两套共存，互不影响。

## 4. 产品交互

### 4.1 入口

Session/Trace 详情页右上角操作栏增加「分享」按钮（`Share2` icon，无文字）。

### 4.2 分享面板

- 临时链接：只读输入框
- 「复制链接」按钮 → 复制到剪贴板 → toast「已复制」
- 有效期说明：灰色小字「链接 7 天内有效，届时将自动失效」

### 4.3 分享查看页

- 无需登录即可访问（签名自鉴权）
- 页面顶部分享标识栏：「由 {花名} 分享 · {时间} · 7 天内有效」
- 页面布局与原始详情页一致，但隐藏：分享按钮、关联 session/trace 跳转
- 链接过期：展示过期提示
- 原始数据过期：展示数据不可用提示

### 4.4 边界与约束

- 分享页不可再分享（隐藏分享按钮）
- 分享页只读（不可编辑、不可触发写操作）
- 关联数据不可跳转（隐藏跳转入口）
- 不支持撤销（无状态设计，7 天自动失效）
