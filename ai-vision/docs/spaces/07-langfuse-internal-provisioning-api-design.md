# Langfuse Provisioning API Design

## 1. Context

AIVision 需要在创建业务空间后，自动在 `Langfuse` 中完成以下动作：

1. 在指定 `organization` 下创建 `project`
2. 在该 `project` 下创建两组 API Key
   - 一组供 AIVision 内部读取观测数据使用
   - 一组供业务空间写入观测数据使用
3. 将返回的 `project` 信息和两组 `pk/sk` 回填到 AIVision 自身库表

当前不适合直接复用 `Langfuse` 现有 `/api/public/*` 路由，原因如下：

- `/api/public/*` 主要面向 Langfuse API Key 调用，不认浏览器登录态 cookie
- organization / project 管理类 public API 依赖 organization-scoped API key
- 现有实例未确认具备稳定可用的 organization-scoped API key 获取路径
- AIVision 只需要“空间资源编排”能力，不需要 Langfuse 用户态能力

因此建议在 `Langfuse` 内新增一组只给 AIVision 使用的内部接口。

## 2. Goals

接口需要满足：

- 支持 AIVision 自动创建业务空间对应的 Langfuse `project`
- 支持自动生成两组 project API keys
- 支持幂等，避免 AIVision 重试导致重复创建
- 支持未来按不同 `organization` / `endpoint` 隔离高保用户
- 不处理 organization member / project member
- 不依赖用户名登录态

## 3. Non-Goals

以下内容不在本期范围内：

- Langfuse 用户自动创建
- organization membership / project membership 管理
- 通过 `/api/public/*` 复用现有 public API
- 前端页面暴露 endpoint / organization 配置
- 多租户动态授权模型设计

## 4. Authentication

### 4.1 Recommended

建议新增专用内部鉴权：

```http
Authorization: Bearer <AIVISION_INTERNAL_API_KEY>
x-aivision-request-id: <uuid>
```

### 4.2 Alternative

如果不想新增环境变量，也可直接复用现有 `ADMIN_API_KEY` 机制。

### 4.3 Auth Rules

- 仅服务端调用
- 不接受浏览器 cookie / session
- 不接受用户名 / IAM 登录态
- 不接受 Langfuse public API key

## 5. Base Path

建议新增内部路径前缀：

```http
/api/internal/aivision
```

## 6. API List

### 6.1 Provision Space Observation Project

`POST /api/internal/aivision/spaces/provision`

用途：

- 在指定 organization 下创建 project
- 创建两组 project API key
- 返回完整 provisioning 结果给 AIVision

#### Request Body

```json
{
  "spaceId": "12345",
  "spaceName": "AI Vision 测试空间",
  "organizationId": "org_xxx",
  "projectName": "aivision-ai-vision-test",
  "idempotencyKey": "space-12345-endpoint-1",
  "metadata": {
    "source": "aivision",
    "spaceId": "12345",
    "spaceName": "AI Vision 测试空间"
  }
}
```

#### Fields

- `spaceId`: AIVision 空间 ID，必填，字符串
- `spaceName`: AIVision 空间名称，必填，字符串
- `organizationId`: 目标 Langfuse organization ID，必填
- `projectName`: 目标 project 名称，必填
- `idempotencyKey`: 幂等键，必填
- `metadata`: 可选，project metadata 扩展字段

#### Success Response

```json
{
  "project": {
    "id": "proj_xxx",
    "name": "aivision-ai-vision-test",
    "organizationId": "org_xxx"
  },
  "credentials": {
    "read": {
      "id": "key_read_xxx",
      "publicKey": "pk-lf-read-xxx",
      "secretKey": "sk-lf-read-xxx"
    },
    "write": {
      "id": "key_write_xxx",
      "publicKey": "pk-lf-write-xxx",
      "secretKey": "sk-lf-write-xxx"
    }
  },
  "idempotent": false
}
```

#### Idempotent Retry Response

```json
{
  "project": {
    "id": "proj_xxx",
    "name": "aivision-ai-vision-test",
    "organizationId": "org_xxx"
  },
  "credentials": {
    "read": {
      "id": "key_read_xxx",
      "publicKey": "pk-lf-read-xxx",
      "secretKey": "sk-lf-read-xxx"
    },
    "write": {
      "id": "key_write_xxx",
      "publicKey": "pk-lf-write-xxx",
      "secretKey": "sk-lf-write-xxx"
    }
  },
  "idempotent": true
}
```

#### Error Codes

- `400`: 参数非法
- `401`: 鉴权失败
- `403`: 无权限
- `404`: organization 不存在
- `409`: `projectName` 冲突或 `idempotencyKey` 冲突
- `500`: 内部错误

#### Server Behavior

1. 校验内部鉴权
2. 校验 organization 是否存在
3. 根据 `idempotencyKey` 查历史 provisioning 结果
4. 若存在则直接返回历史结果
5. 否则创建 project
6. 创建两组 project api key
7. 记录 provisioning 结果
8. 返回完整结果

### 6.2 Query Provision Result

`GET /api/internal/aivision/spaces/provision?idempotencyKey=<value>`

用途：

- AIVision 在请求超时或状态不明确时，主动查询 provisioning 结果

#### Success Response

```json
{
  "found": true,
  "project": {
    "id": "proj_xxx",
    "name": "aivision-ai-vision-test",
    "organizationId": "org_xxx"
  },
  "credentials": {
    "read": {
      "id": "key_read_xxx",
      "publicKey": "pk-lf-read-xxx",
      "secretKey": "sk-lf-read-xxx"
    },
    "write": {
      "id": "key_write_xxx",
      "publicKey": "pk-lf-write-xxx",
      "secretKey": "sk-lf-write-xxx"
    }
  }
}
```

#### Not Found

```json
{
  "found": false
}
```

#### Notes

如果服务端不希望长期保存 `secretKey` 明文，则可以调整为：

- `POST provision` 返回完整 `secretKey`
- `GET provision` 仅返回 `project` 和 `publicKey`
- 不再返回 `secretKey`

### 6.3 Revoke Project API Key

`POST /api/internal/aivision/spaces/revoke-key`

用途：

- AIVision 在 key 轮转或空间下线时，吊销旧的 project api key

#### Request Body

```json
{
  "projectId": "proj_xxx",
  "apiKeyId": "key_old_xxx"
}
```

#### Success Response

```json
{
  "success": true
}
```

#### Error Codes

- `400`: 参数非法
- `401`: 鉴权失败
- `403`: 无权限
- `404`: project 或 api key 不存在
- `500`: 内部错误

### 6.4 Rotate Project API Key (Optional)

`POST /api/internal/aivision/spaces/rotate-key`

用途：

- 为现有 project 创建新的 READ 或 WRITE key，并返回给 AIVision

#### Request Body

```json
{
  "projectId": "proj_xxx",
  "scope": "READ"
}
```

#### Success Response

```json
{
  "credential": {
    "id": "key_new_xxx",
    "publicKey": "pk-lf-new-xxx",
    "secretKey": "sk-lf-new-xxx",
    "scope": "READ"
  }
}
```

#### Notes

一期可以不单独实现该接口。  
AIVision 可先通过“create new key + revoke old key”组合完成。

## 7. Metadata Convention

建议所有由 AIVision 创建的 project 统一写入 metadata：

```json
{
  "source": "aivision",
  "spaceId": "12345",
  "spaceName": "AI Vision 测试空间",
  "idempotencyKey": "space-12345-endpoint-1"
}
```

用途：

- 便于溯源
- 支持幂等
- 支持未来按空间反查 project
- 支持 endpoint / organization 切换

## 8. Idempotency Design

### 8.1 Recommended Key

建议 AIVision 使用：

```text
space-{spaceId}-endpoint-{endpointConfigId}
```

### 8.2 Rules

同一个 `idempotencyKey`：

- 若第一次已成功，后续重试返回同一结果
- 若参数不一致，应返回 `409`
- 不允许同一 key 对应不同 `organizationId` 或 `projectName`

### 8.3 Storage

建议在 `Langfuse` 侧增加一张 provisioning 结果表，或等价持久化记录。

建议字段：

- `id`
- `source_system`，固定为 `aivision`
- `idempotency_key`
- `organization_id`
- `project_id`
- `project_name`
- `read_api_key_id`
- `read_public_key`
- `read_secret_key_ciphertext` 或临时明文存储策略
- `write_api_key_id`
- `write_public_key`
- `write_secret_key_ciphertext`
- `status`
- `created_at`
- `updated_at`

如果不想新增表，也可以把 `idempotencyKey` 放入 `project.metadata`，但不利于保存完整 key 返回结果。

## 9. Route Placement

建议新增文件：

- `web/src/pages/api/internal/aivision/spaces/provision.ts`
- `web/src/pages/api/internal/aivision/spaces/revoke-key.ts`
- `web/src/pages/api/internal/aivision/spaces/rotate-key.ts`

建议新增服务层：

- `web/src/features/aivision/server/aivisionInternalAuth.ts`
- `web/src/features/aivision/server/spaceProvisioningService.ts`

## 10. Implementation Notes

### 10.1 Organization Selection

- AIVision 不在前端页面暴露 endpoint / organization 选择
- 默认情况下，所有业务空间走同一个默认 `ObservationEndpointConfig`
- 高保用户如需独立 organization，由 Django Admin action 切换 endpoint 后，再调用相同 provisioning 接口

### 10.2 No Membership Sync

本方案明确不做：

- organization membership 创建
- project membership 创建
- Langfuse 用户创建
- IAM 用户绑定

### 10.3 Why Not Use Public API

现有 `/api/public/*` 路由：

- 要求 public api key
- 不认浏览器 cookie
- 不认用户名登录态
- 组织级 / 项目级管理路由还依赖 `admin-api` entitlement

因此不适合作为 AIVision 的内部空间编排接口。

## 11. Minimal Scope for Phase 1

一期建议只实现以下两个接口：

1. `POST /api/internal/aivision/spaces/provision`
2. `POST /api/internal/aivision/spaces/revoke-key`

这已经足够支撑：

- AIVision 创建业务空间
- 自动创建 observation project
- 自动生成两组 key
- 后续轮转旧 key 的回收

## 12. Example Flow

AIVision 创建业务空间后的调用流程：

1. AIVision 创建 `Space`
2. 读取绑定的 `ObservationEndpointConfig`
3. 调 `POST /api/internal/aivision/spaces/provision`
4. 获取：
   - `project.id`
   - `project.name`
   - `read pk/sk`
   - `write pk/sk`
5. 落库到：
   - `SpaceObservationProject`
   - `SpaceObservationCredential`
6. 若后续重试，则使用相同 `idempotencyKey`

## 13. Open Questions

以下实现前需确认：

1. 内部鉴权是否新增 `AIVISION_INTERNAL_API_KEY`，还是直接复用 `ADMIN_API_KEY`
2. provisioning 结果是否允许服务端持久化 `secretKey` 明文
3. 是否需要在 `project.metadata` 中强制写入 AIVision 空间标识
4. 是否需要提供按 `spaceId` 反查 project 的辅助接口

## 14. AIVision Backend Integration

AIVision 在“创建业务空间”后，不再自己拼 Langfuse 的 project/api key 创建逻辑，而是统一调用 `Langfuse` 提供的内部 provisioning 接口。AIVision 只负责：

- 选择 `ObservationEndpointConfig`
- 调内部接口
- 将返回结果落库到本地 observation 相关表
- 在失败时记录 `last_error` 和 `provision_status`

### 14.1 Invocation Timing

建议放在业务空间创建流程里，顺序如下：

1. 创建 `Space`
2. 创建 `SpaceMember`
3. 选定 `ObservationEndpointConfig`
4. 调 `Langfuse` 内部接口 `POST /api/internal/aivision/spaces/provision`
5. 成功后创建：
   - `SpaceObservationProject`
   - `SpaceObservationCredential(scope=READ)`
   - `SpaceObservationCredential(scope=WRITE)`
6. 若失败：
   - `SpaceObservationProject.provision_status = FAILED`
   - `SpaceObservationProject.last_error = xxx`

如果仍然保留 admin action，也建议 admin action 最终调用同一套 provisioning service，不要两套逻辑并存。

### 14.2 Request Mapping

接口请求：

```json
{
  "spaceId": "12345",
  "spaceName": "AI Vision 测试空间",
  "organizationId": "org_xxx",
  "projectName": "aivision-ai-vision-test",
  "idempotencyKey": "space-12345-endpoint-7",
  "metadata": {
    "source": "aivision",
    "spaceId": "12345",
    "spaceName": "AI Vision 测试空间"
  }
}
```

字段来源：

- `spaceId`
  - 来源：`Space.id`
- `spaceName`
  - 来源：`Space.name`
- `organizationId`
  - 来源：`ObservationEndpointConfig.config.organization_id`
- `projectName`
  - 建议规则：`aivision-{space.id}-{slugified_space_name}`
- `idempotencyKey`
  - 建议规则：`space-{space.id}-endpoint-{endpoint_config.id}`
- `metadata`
  - 固定包含 `source=aivision`
  - 建议再带上 `spaceId`、`spaceName`

### 14.3 ObservationEndpointConfig Convention

AIVision 建议在 `ObservationEndpointConfig.config` 中至少约定这些字段：

```json
{
  "organization_id": "org_xxx",
  "internal_api_base_url": "https://aivision.example.com",
  "auth_mode": "internal_api"
}
```

说明：

- `organization_id`
  - 目标 Langfuse organization
- `internal_api_base_url`
  - `Langfuse` 内部接口地址
- `auth_mode`
  - 标识这条 endpoint 走内部 provisioning 模式

如果内部接口使用独立 `AIVISION_INTERNAL_API_KEY`，建议放在 Django 环境变量中，不建议存进该表。

### 14.4 Persistence Mapping

`POST /api/internal/aivision/spaces/provision` 成功后，返回：

```json
{
  "project": {
    "id": "proj_xxx",
    "name": "aivision-ai-vision-test",
    "organizationId": "org_xxx"
  },
  "credentials": {
    "read": {
      "id": "key_read_xxx",
      "publicKey": "pk-lf-read-xxx",
      "secretKey": "sk-lf-read-xxx"
    },
    "write": {
      "id": "key_write_xxx",
      "publicKey": "pk-lf-write-xxx",
      "secretKey": "sk-lf-write-xxx"
    }
  },
  "idempotent": false
}
```

AIVision 本地落库建议：

`SpaceObservationProject`

- `space` = 当前空间
- `endpoint_config` = 当前 endpoint
- `tenant_name` = `endpoint.tenant_name`
- `upstream_project_id` = `project.id`
- `upstream_project_name` = `project.name`
- `status` = 1
- `provision_status` = 1
- `last_error` = null

`SpaceObservationCredential(scope=READ)`

- `space_observation_project` = 上面创建的 project
- `scope` = `"READ"`
- `public_key` = `credentials.read.publicKey`
- `secret_hash` = `hash_secret(credentials.read.secretKey)`
- `secret_ciphertext` = `encrypt_secret(credentials.read.secretKey)`
- `name` = `"default-read"`
- `status` = 1

`SpaceObservationCredential(scope=WRITE)`

- `space_observation_project` = 上面创建的 project
- `scope` = `"WRITE"`
- `public_key` = `credentials.write.publicKey`
- `secret_hash` = `hash_secret(credentials.write.secretKey)`
- `secret_ciphertext` = `encrypt_secret(credentials.write.secretKey)`
- `name` = `"default-write"`
- `status` = 1

### 14.5 Suggested AIVision Service

建议新增统一服务：

- `apps/core/services/space_observation_provisioning.py`

职责：

- 选择 endpoint
- 组装请求
- 调内部接口
- 落库 project 和 credential
- 处理幂等和异常

建议接口：

```python
def provision_space_observation_project(space: Space, endpoint: ObservationEndpointConfig) -> SpaceObservationProject:
    ...
```

内部步骤：

1. 检查 `space.type == 2`
2. 检查空间是否已存在 `SpaceObservationProject`
3. 创建一条本地 `SpaceObservationProject(provision_status=PENDING)`
4. 调 `Langfuse` 内部接口
5. 更新本地 project 记录
6. 创建两条 credential
7. 返回 project

失败时：

- 更新 `provision_status=FAILED`
- 写 `last_error`
- 抛出异常或返回失败结果

### 14.6 Retry Strategy

AIVision 调用 provisioning 接口时必须带固定 `idempotencyKey`。

推荐生成规则：

```text
space-{space.id}-endpoint-{endpoint.id}
```

这样可以保证：

- 同一个空间首次重试不会重复建 project
- 高保用户切换到新的 endpoint 后，会使用新的幂等键
- 不同 organization / endpoint 的资源天然隔离

AIVision 重试策略建议：

- 首次失败后允许重试 1 到 2 次
- 若 HTTP 超时，可调用 `GET /api/internal/aivision/spaces/provision?idempotencyKey=...`
- 如果查到结果则直接落库，不再重复创建

### 14.7 Error Handling

AIVision 对不同错误码处理建议：

- `400`
  - 配置错误或请求体不合法
  - 标记 `FAILED`
  - 需要人工处理
- `401`
  - AIVision 调内部接口鉴权失败
  - 标记 `FAILED`
  - 需要人工处理
- `403`
  - `Langfuse` 侧未开放内部权限
  - 标记 `FAILED`
- `404`
  - organization 不存在
  - 标记 `FAILED`
- `409`
  - projectName 冲突或幂等参数冲突
  - 优先尝试查询已有 provisioning 结果
- `500`
  - 上游内部错误
  - 可短暂重试

`last_error` 建议直接记录：

- HTTP status code
- 响应 body
- request id

例如：

```text
Provision failed: status=409, body={"error":"project name already exists"}, request_id=xxx
```

### 14.8 High-Security Endpoint Switching

高保用户的 endpoint 切换不在页面暴露，由 Django Admin action 控制。

AIVision 侧处理建议：

1. Admin action 选择新的 `ObservationEndpointConfig`
2. 若该空间尚未 provision，则直接按新 endpoint 调 provisioning
3. 若该空间已 provision 且要迁移：
   - 一期建议不要自动迁移历史 project
   - 一期建议仅对新空间生效，老空间切换 endpoint 先走人工迁移流程

这点需要与当前 `SpaceObservationProject` 的 `space` 唯一约束保持一致。

### 14.9 Recommended AIVision Refactor Points

建议优先收敛这些位置：

- `apps/core/admin.py` 中的业务空间创建逻辑
- `apps/core/admin.py` 中的 observation project 初始化逻辑
- `apps/core/services/credential_provider.py`

目标改造方向：

1. 保留业务空间创建 action
2. 在创建空间成功后，直接调用新的 provisioning service
3. 将现有 observation 初始化 action 改为历史补偿入口
4. 不再由 AIVision 直接拼装 Langfuse public API project/key 创建逻辑
5. 改为调用 `Langfuse` 内部接口

### 14.10 Phase 1 Closed Loop

一期推荐最小闭环如下：

`Langfuse` 实现：

- `POST /api/internal/aivision/spaces/provision`
- `POST /api/internal/aivision/spaces/revoke-key`

`AIVision` 实现：

- 创建业务空间后自动调用 `provision`
- 成功后写入本地 observation project + credentials
- 失败后记录 `FAILED + last_error`
- 保留 admin action 作为补偿入口

### 14.11 Suggested Fixed Conventions

建议在 AIVision 侧固定以下约定：

- `ObservationEndpointConfig.config.organization_id`
- `ObservationEndpointConfig.config.internal_api_base_url`
- `idempotencyKey = space-{spaceId}-endpoint-{endpointConfigId}`
- `projectName = aivision-{spaceId}-{normalizedSpaceName}`
