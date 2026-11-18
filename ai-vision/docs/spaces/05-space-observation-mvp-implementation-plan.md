# Space Observation Integration MVP - Implementation Plan

> **状态：一期实现计划快照（历史记录）。本文记录的文件路径为编写时规划，后续代码已发生重构——前端目录已从 `aivision-fe/` 迁移为 `vfe/`，后端 `apps/core/views.py` 单文件已拆分为 `apps/core/views/` 包（`langfuse.py`/`share.py`/`observation.py`/`agent.py`/`space.py`/`query.py` 等）。具体现状以代码为准。**

## Context

AI Vision 当前只有个人空间的 Langfuse 观测能力，需要新增业务空间支持。核心变化：把"按 agent_product 配置 Langfuse"升级为"按空间绑定 observation project + AIVision 统一管理凭证"。凭证当前从 Langfuse 获取，后续去 Langfuse 化时替换 Provider 即可。

## Phase 1: 数据模型 + Migration

**新建/修改文件：**
- `aivision-py-be/apps/core/models.py` — 新增 3 个模型 + Space 加 billing_owner 字段

**具体内容：**

1. `ObservationEndpointConfig` — 观测网关接入点
   - 字段：name, base_url, tenant_name, is_default, auth_mode, admin_public_key, admin_secret_ciphertext, config(JSON), status
   - 表名：`aivision_observation_endpoint_config`

2. `SpaceObservationProject` — 空间与 observation project 绑定
   - 字段：space(FK), endpoint_config(FK), tenant_name, upstream_project_id/name/slug, status, provision_status(0=PENDING/1=ACTIVE/2=FAILED/3=DISABLED), last_error
   - 约束：`unique_together = ["space"]`
   - 表名：`aivision_space_observation_project`

3. `SpaceObservationCredential` — 空间级上报/读取 Key
   - 字段：space_observation_project(FK), scope(READ/WRITE), public_key(unique), secret_hash, secret_ciphertext, name, status, last_used_at, last_used_ip, rotated_at
   - 约束：`unique_together = ["space_observation_project", "scope"]`
   - 表名：`aivision_space_observation_credential`

4. `Space` 模型新增 `billing_owner` (CharField 256, nullable)

**验证：** `makemigrations` + `migrate` 无报错

---

## Phase 2: 凭证服务 + Django Admin

**新建文件：**
- `apps/core/services/credential_provider.py` — CredentialProvider 抽象 + LangfuseCredentialProvider
- `apps/core/services/credential_service.py` — hash/加密/解密/重新生成

**修改文件：**
- `apps/core/admin.py` — 注册 3 个新模型 + SpaceAdmin 加 `@admin.action` 一键初始化

**credential_provider.py：**
- `CredentialPair` dataclass (public_key, secret_key)
- `CredentialProvider` ABC: create_credentials(), revoke_credential(), regenerate_credential()
- `LangfuseCredentialProvider`: 调用 Langfuse API 创建/吊销 API Key

**credential_service.py：**
- `hash_secret(secret_key)` → sha256
- `encrypt_secret(secret_key)` → Fernet 加密（用 settings.SECRET_KEY 派生）
- `decrypt_secret(ciphertext)` → 解密
- `verify_secret(secret_key, secret_hash)` → 常量时间比对
- `create_credential_records(project, read_pair, write_pair)` → 写入 DB
- `reveal_credential(credential)` → 解密返回完整 key
- `regenerate_credential(credential, provider)` → 协调 Provider + 更新 DB

**admin.py action：**
- 仅处理 type=2 业务空间
- 查默认 endpoint → 创建 SpaceObservationProject(PENDING) → 调 Provider 创建凭证 → 成功更新 ACTIVE / 失败记录 FAILED + last_error

**验证：** Admin 界面手动创建 endpoint → 选择业务空间执行 action → 确认 project + 2 条 credential 记录生成

---

## Phase 3: 查询链路迁移

**修改文件：**
- `apps/core/views.py` — 新增 `get_space_observation_runtime_config()` + `build_langfuse_trace_filter()`

**get_space_observation_runtime_config(space, agent_product=None)：**
1. 查 SpaceObservationProject (status=1, provision_status=1) → 有则返回 endpoint 配置
2. 否则回退到现有 `get_langfuse_config(space, agent_product)`
3. 返回 dict 统一结构，带 `source` 标识新旧链路

**build_langfuse_trace_filter(agent_name=None, session_id=None, work_id=None)：**
- 替代现有 `build_langfuse_user_trace_filter()`
- 按参数组合构建 Langfuse filter JSON

**验证：** 有 project 的业务空间走新链路，无 project 的个人空间走旧链路

---

## Phase 4: 内部接口重构 + 空间设置增强

**修改文件：**
- `apps/core/views.py` — 重构 5 个 View + 新增 2 个 View
- `aivision_backend/urls.py` — 新增 credential 路由

**Trace/Session View 重构：**
- `TraceListView`：用 `get_space_observation_runtime_config()` 替换 `get_langfuse_config()`；业务空间(type=2)不注入 work_id 过滤，改为支持 `agentName` 参数；个人空间保持现有 work_id 过滤
- `TraceDetailView`、`ObservationDetailView`、`SessionListView`、`SessionDetailView`：同样替换 config 获取方式

**SpaceSettingsView 增强：**
- 响应新增 observation 块（endpoint_name, project_id, provision_status）
- 新增 write_key / read_key 块（public_key 脱敏展示）
- 新增 billing_owner

**新增 View：**
- `CredentialRevealView` — POST /api/v1/spaces/{space_id}/credentials/{credential_id}/reveal（仅管理员）
- `CredentialRegenerateView` — POST /api/v1/spaces/{space_id}/credentials/{credential_id}/regenerate（仅管理员）

**验证：**
- 业务空间 traces 不再按 work_id 过滤，agentName 过滤生效
- 个人空间行为不变
- settings 返回 observation + key 信息
- reveal/regenerate 接口管理员可用，成员 403

---

## Phase 5: 对外兼容 API

**新建文件：**
- `apps/core/observation_views.py` — 3 个 Public View

**修改文件：**
- `apps/core/authentication.py` — 新增 ObservationGatewayAuthentication
- `aivision_backend/urls.py` — 新增 /api/public/* 路由

**ObservationGatewayAuthentication：**
- 解析 Basic Auth header → base64 解码 → 拆分 pk:sk
- 查 SpaceObservationCredential(public_key, status=1)
- verify_secret 比对 → 校验 scope(一期仅 READ)
- 更新 last_used_at/last_used_ip
- 将 credential/project/endpoint 附加到 request

**Public Views（不包装 {code,message,data}，透传 Langfuse 响应）：**
- `PublicTraceListView` — GET /api/public/traces
- `PublicTraceDetailView` — POST /api/public/traces/{trace_id}
- `PublicObservationDetailView` — GET /api/public/observations/{observation_id}

**错误语义：** 401 无效 auth, 403 scope 不足, 502/504 上游异常

**验证：** curl -u pk:sk 测试 3 个接口；WRITE key 返回 403；无效 key 返回 401

---

## Phase 6: 前端适配

**修改文件：**
- `aivision-fe/src/context/SpaceContext.tsx` — 支持业务空间切换 + localStorage 持久化
- `aivision-fe/src/components/layout/Sidebar.tsx` — 空间列表分组（个人置顶 + 业务空间）
- `aivision-fe/src/types/index.ts` — 新增 observation/credential 类型
- `aivision-fe/src/pages/Settings.tsx` — 展示 observation 信息 + key 管理

**SpaceContext 改动：**
- 读 localStorage `selectedSpaceId`（兼容回退 `personalSpaceId`）
- 切换空间时存储 selectedSpaceId
- 暴露 spaceType 供下游组件判断

**Sidebar 改动：**
- 个人空间置顶，业务空间分组展示
- 点击空间调用 setSpaceId 切换

**Settings 改动：**
- 展示 observation project 状态
- 展示脱敏 key + 管理员 Reveal/Regenerate 按钮
- 展示 billing_owner

**验证：** 切换业务空间 → 刷新保持 → Settings 展示正确 → Reveal/Regenerate 功能正常

---

## 关键复用点

| 已有代码 | 位置 | 复用方式 |
|---------|------|---------|
| `get_langfuse_config()` | views.py:948 | 作为新函数的 fallback |
| `get_langfuse_auth_header()` | views.py:989 | 代理请求时复用 |
| `check_space_access_or_error()` | views.py:66 | 所有新 View 复用 |
| `success_response/error_response` | views.py | 内部接口复用 |
| `ApiKey.generate_key()` | models.py:156 | 参考 hash 生成模式 |
| `IAMOrJWTAuthentication` | authentication.py | 参考 auth 类结构 |

## 风险点

1. **Langfuse API 调用失败**：admin action 容错处理，记录 FAILED + last_error
2. **加密密钥管理**：用 Fernet + SECRET_KEY 派生，或新增 CREDENTIAL_ENCRYPTION_KEY 环境变量
3. **个人空间兼容**：通过 space.type 分支 + fallback 机制保证不影响现有行为
4. **前端 localStorage 迁移**：新旧 key 兼容读取
