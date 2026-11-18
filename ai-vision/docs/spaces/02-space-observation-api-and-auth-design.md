# 02. 空间级 Observation 接口与鉴权设计

## 目录

- [文档范围](#文档范围)
- [1. 凭证设计](#1-凭证设计)
- [2. 内部控制台接口设计](#2-内部控制台接口设计)
- [3. 对外兼容接口设计](#3-对外兼容接口设计)
- [4. 鉴权与权限模型](#4-鉴权与权限模型)
- [5. 脱敏策略](#5-脱敏策略)
- [6. 过滤器合并设计](#6-过滤器合并设计)
- [7. 安全设计](#7-安全设计)
- [8. 接口草案](#8-接口草案)
- [9. 兼容边界说明](#9-兼容边界说明)
- [10. 权限矩阵](#10-权限矩阵)

## 文档范围

本文聚焦接口、鉴权、权限和过滤器设计，覆盖内部控制台接口与对外兼容 API。

本文不定义底层表结构、migration 步骤和 dashboard 建模，这些内容分别放在 03、04 文档中。

## 1. 凭证设计

### 1.1 上报 Key 与读取 Key

每个业务空间创建时固定生成一份上报 Key 和一份读取 Key，不允许创建额外凭证：

1. **读取 Key**（READ scope）
   - 用于 traces / trace detail / observation detail 查询
   - 是唯一允许调用只读查询接口的数据凭证

2. **上报 Key**（WRITE scope）
   - 用于 SDK 接入和数据上报
   - 不允许调用读取数据的查询接口

### 1.2 凭证来源与演进

#### 当前态：凭证来自 Langfuse

当前阶段，AIVision 仍依赖 Langfuse 作为底层观测存储，因此上报 Key 和读取 Key 的 PK/SK 实际从 Langfuse 获取：

1. 创建空间时，在 Langfuse 对应 project 下创建两组 API Key
2. 将获取到的 PK/SK 存入 `SpaceObservationCredential`
3. 存储格式与 Langfuse Basic Auth 一致：`base64(public_key:secret_key)`
4. AIVision 网关层在此基础上做 scope（READ/WRITE）校验，Langfuse 原生不区分读写

#### 最终态：去 Langfuse 化

后续将 Langfuse 替换为自研或其他观测组件时，只需替换凭证生成的来源，上层接口和存储模型不变。

#### 凭证提供者抽象

为支持平滑过渡，凭证生成逻辑应抽象为 `CredentialProvider` 接口：

```python
class CredentialProvider(ABC):
    """凭证提供者抽象，屏蔽底层观测组件差异"""

    @abstractmethod
    def create_credentials(self, project_config: dict) -> tuple[CredentialPair, CredentialPair]:
        """创建一组读取凭证和一组上报凭证，返回 (read_pair, write_pair)"""
        ...

    @abstractmethod
    def revoke_credential(self, public_key: str) -> bool:
        """吊销指定凭证"""
        ...

    @abstractmethod
    def regenerate_credential(self, old_public_key: str) -> CredentialPair:
        """重新生成凭证，旧凭证立即失效"""
        ...

@dataclass
class CredentialPair:
    public_key: str
    secret_key: str
```

一期实现 `LangfuseCredentialProvider`，后续新增其他实现即可切换：

```python
class LangfuseCredentialProvider(CredentialProvider):
    """从 Langfuse 获取实际 PK/SK"""

    def create_credentials(self, project_config):
        # 调用 Langfuse API 创建两组 API Key
        # 返回 (read_pair, write_pair)
        ...
```

#### Basic Auth 编码格式

AIVision 对外暴露的 Basic Auth 格式与 Langfuse 完全一致：

```
Authorization: Basic base64({public_key}:{secret_key})
```

这保证了：

1. 业务方 SDK 可以直接使用 Langfuse SDK 的 Basic Auth 格式接入
2. 后续替换底层组件时，业务方调用方式不变
3. 存储层 `secret_hash = sha256(secret_key)` 用于鉴权比对，`secret_ciphertext` 用于受控回显

#### Scope 校验层

虽然底层凭证来自 Langfuse，但读写权限的区分由 AIVision 网关层负责：

- Langfuse 原生 API Key 不区分读写 scope
- AIVision 在 `SpaceObservationCredential` 中标记 `scope = READ / WRITE`
- 网关层根据 scope 决定是否放行请求（READ Key 不允许写入，WRITE Key 不允许读取）
- 这一层逻辑独立于底层观测组件，不随组件替换而变化

### 1.3 展示策略

- 上报 Key 和读取 Key **均默认脱敏展示**
- **管理员**可取消脱敏查看完整 Key，取消脱敏后显示复制 icon
- **成员**仅查看脱敏 Key，不可取消脱敏

### 1.4 重新生成

- 上报 Key 和读取 Key 各自支持**重新生成**
- 仅管理员可操作
- 重新生成后旧 Key **立即失效**，历史数据不受影响
- 重新生成操作应记录审计日志

## 2. 内部控制台接口设计

内部控制台接口继续沿用 `/api/v1/spaces/{space_id}/...` 风格，但语义调整为“按空间 project 查询”。

### 2.1 Trace 列表

`GET /api/v1/spaces/{space_id}/traces`

请求参数建议：

- `page`
- `limit`
- `fromTimestamp`
- `toTimestamp`
- `orderBy`
- `name`
- `agentName` 可选
- `sessionId` 可选

行为：

1. 校验当前登录用户是否为该空间成员
2. 通过 `space_id` 找到 `SpaceObservationProject`
3. 构建上游兼容请求
4. 一期支持按 `agentName` 筛选业务空间内的 traces
5. 不再按当前登录用户 `work_id` 注入 `metadata.attributes.ant.username` 条件

### 2.2 Trace 详情

`GET /api/v1/spaces/{space_id}/traces/{trace_id}`

行为：

1. 校验空间成员身份
2. 按空间 project 请求上游兼容接口
3. 一期不叠加额外过滤条件，允许按 `trace_id` 直接查询
4. 后续若需要更严格控制，可增加 URL 签名校验能力

### 2.3 Observation / Span 详情

`GET /api/v1/spaces/{space_id}/observations/{observation_id}`

行为：

1. 校验空间成员身份
2. 按空间 project 请求上游兼容接口
3. 一期不叠加额外过滤条件，允许按 `observation_id` 直接查询
4. 后续若要求和明细链路统一收紧，可增加 URL 签名或 trace 归属校验

说明：

- Langfuse 概念里是 `observation`，前端可以在展示层映射为 span
- 后端接口建议继续使用 Langfuse 术语，降低协议偏差

### 2.4 空间设置接口

`GET /api/v1/spaces/{space_id}/settings`

建议新增字段：

- `observation.endpoint_name`
- `observation.tenant_name`
- `observation.project_id`
- `observation.project_name`
- `observation.provision_status`
- `observation.last_error`
- `write_key.public_key`（上报 Key，默认脱敏展示）
- `read_key.public_key`（读取 Key，默认脱敏展示）

### 2.5 Key 取消脱敏接口

`POST /api/v1/spaces/{space_id}/credentials/{credential_id}/reveal`

行为：

1. 校验当前登录用户是否为该空间管理员
2. 成员角色不允许调用此接口
3. 解密 `secret_ciphertext`，返回完整 Key
4. 记录审计日志

### 2.6 Key 重新生成接口

`POST /api/v1/spaces/{space_id}/credentials/{credential_id}/regenerate`

行为：

1. 校验当前登录用户是否为该空间管理员
2. 成员角色不允许调用此接口
3. 生成新的 `public_key` / `secret_key`
4. 更新 `secret_hash`、`secret_ciphertext`、`rotated_at`
5. 旧 Key 立即失效，历史数据不受影响
6. 返回新生成的完整 Key（仅本次返回）
7. 记录审计日志

## 3. 对外兼容接口设计

### 3.1 第一阶段开放范围

第一期建议开放：

- `GET /api/public/traces`
- `POST /api/public/traces/{trace_id}`
- `GET /api/public/observations/{observation_id}`

说明：

- 外部应用的核心诉求是“拿到 ID 后直接查详情”，因此一期明细接口不做额外过滤。

### 3.2 鉴权方式

使用 Basic Auth：

`Authorization: Basic base64(public_key:secret_key)`

认证通过后，可解析到：

- 当前 credential
- 对应空间 project
- 对应 Langfuse endpoint

补充说明：

- 明细查询接口仅依赖 Basic Auth 鉴权

### 3.3 协议要求

外部兼容接口应尽量保持与 Langfuse Public API 协议一致：

- 参数名保持一致
- 成功响应结构尽量透传
- HTTP 状态码尽量透传
- 不包装 `{code, message, data}`

### 3.4 为什么要单独一组 `/api/public/*`

原因：

1. 与 Langfuse 现有 Public API 习惯一致
2. 便于 SDK 或外部系统直接替换接入地址
3. 避免内部控制台协议和外部集成协议相互影响

## 4. 鉴权与权限模型

### 4.1 内部控制台接口

继续走现有 IAM/JWT 认证链。

权限规则建议：

- 空间成员均可查看 traces / trace detail / observation detail
- Key 取消脱敏、重新生成仅限管理员
- 成员仅可查看脱敏 Key

### 4.2 外部兼容接口

新增 `ObservationGatewayAuthentication`

核心步骤：

1. 读取 `Authorization`
2. 解析 Basic Auth
3. 取出 `public_key` 与 `secret_key`
4. 查询 `SpaceObservationCredential`
5. 校验凭证状态
6. 比对 `secret_hash`
7. 校验 scope
8. 更新 `last_used_at`
9. 将上下文写入 request

### 4.3 Scope 策略

建议第一阶段：

- `GET /api/public/traces`：仅允许读取 Key（READ scope）
- `POST /api/public/traces/{trace_id}`：仅允许读取 Key（READ scope）
- `GET /api/public/observations/{observation_id}`：仅允许读取 Key（READ scope）

未来若增加写入接口：

- `POST /api/public/otel`：仅允许上报 Key（WRITE scope）

### 4.4 错误语义

- `401 Unauthorized`：缺失或无效的 Basic Auth
- `403 Forbidden`：凭证存在但 scope 不足
- `404 Not Found`：空间项目绑定不存在
- `502/504`：上游 Langfuse 异常

补充说明：

- 一期外部明细查询不存在“因过滤条件不匹配而拒绝访问”的错误分支。

## 5. 脱敏策略

### 5.1 轨迹数据脱敏

脱敏范围：轨迹列表中的 Input/Output 文本内容。

| 空间类型 | 默认脱敏 | 管理员 | 成员 |
|---------|---------|--------|------|
| 业务空间 | 是 | 可取消脱敏查看原文 | 不可取消脱敏 |
| 个人空间 | 否 | N/A（仅 owner） | N/A |

实现建议：

1. 在 trace/session 查询响应中，根据空间类型和当前用户角色决定是否对 input/output 字段进行脱敏
2. 脱敏方式：将完整内容替换为截断摘要或掩码，如 `"内容已脱敏，管理员可查看原文"`
3. 管理员取消脱敏通过请求参数 `?unmask=true` 控制，后端校验角色后决定是否返回原文

### 5.2 Key 脱敏

上报 Key 和读取 Key 均默认脱敏展示：

| 角色 | 查看 Key | 取消脱敏 | 复制 | 重新生成 |
|------|---------|---------|------|---------|
| 管理员 | 脱敏展示 | 可取消脱敏，取消后显示复制 icon | 可复制 | 可重新生成 |
| 成员 | 脱敏展示 | 不可取消脱敏 | 不可复制 | 不可重新生成 |

## 6. 过滤器合并设计

### 6.1 目标

同时支持：

- Langfuse 原生 `filter`
- 业务参数 `agentName`

### 6.2 合并规则

1. 只传 `agentName`
   - 网关生成：
   - 按约定字段注入 agent 名称过滤条件

2. 只传 `filter`
   - 原样透传

3. 同时传 `agentName` 和 `filter`
   - 用 AND 逻辑合并

4. 内部控制台不传 `agentName`
   - 默认查询该业务空间 project 下的全量 traces

### 6.3 实现建议

需要从当前固定拼接逻辑升级为通用 builder：

- 能解析现有 `filter`
- 能注入 `agentName` 对应条件
- 能输出合法 JSON

建议抽象一个统一方法：

- `build_langfuse_trace_filter(agent_name=None, session_id=None, raw_filter=None)`

一期落地建议：

- 内部控制台调用该方法时，按 `agentName` 是否传入决定是否注入 agent 过滤
- 外部兼容查询接口调用该方法时，默认不注入 agent 过滤条件

## 7. 安全设计

### 7.1 日志控制

- 禁止打印完整 secret
- 仅保留 `public_key` 或 prefix
- 禁止把 Basic Auth 明文写入日志

### 7.2 审计

建议审计以下动作：

- Key 初始生成
- Key 取消脱敏（reveal）
- Key 重新生成（regenerate）
- 鉴权失败
- 鉴权成功

### 7.3 限流

建议对 `/api/public/*` 做基于 `public_key` 的限流。

### 7.4 白名单

高保空间可扩展：

- credential 级 IP 白名单
- endpoint 级网络访问限制

## 8. 接口草案

### 8.1 内部控制台 Trace 列表

请求：

```http
GET /api/v1/spaces/{space_id}/traces?page=1&limit=20&agentName=my-agent
Authorization: Bearer <jwt>
```

响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "data": [],
    "meta": {
      "page": 1,
      "limit": 20,
      "totalItems": 0,
      "totalPages": 1
    }
  }
}
```

### 8.2 外部兼容 Trace 列表

请求：

```http
GET /api/public/traces?page=1&limit=20&name=demo-trace
Authorization: Basic <base64(public_key:secret_key)>
```

响应：

```json
{
  "data": [],
  "meta": {
    "page": 1,
    "limit": 20,
    "totalItems": 0,
    "totalPages": 1
  }
}
```

### 8.3 外部兼容 Trace 详情

请求：

```http
POST /api/public/traces/trc_xxx
Authorization: Basic <base64(public_key:secret_key)>
Content-Type: application/json

{"fromTimestamp":"2026-06-25T00:00:00+08:00","toTimestamp":"2026-06-25T06:45:00+08:00"}
```

响应：

```json
{
  "id": "trc_xxx",
  "name": "demo trace"
}
```

### 8.4 外部兼容 Observation 详情

请求：

```http
GET /api/public/observations/obs_xxx
Authorization: Basic <base64(public_key:secret_key)>
```

响应：

```json
{
  "id": "obs_xxx",
  "traceId": "trc_xxx",
  "type": "SPAN"
}
```

## 9. 兼容边界说明

一期仅兼容当前业务需要的 Langfuse Public API 子集，不承诺完整镜像上游全部公共接口、全部查询参数和全部响应头。

设计原则：

- 优先兼容当前前端和外部调用方已使用到的 traces / observation 相关查询能力
- 成功响应尽量透传
- AIVision 自身的认证失败、权限不足、空间绑定缺失等错误，由网关自行返回，不强制伪装为上游错误

## 10. 权限矩阵

以下权限矩阵与产品文档 3.3 节对齐：

| 能力 | 管理员 | 成员 | 外部读取 Key | 外部上报 Key |
|---|---|---|---|---|
| 数据看板 | 可查看 | 可查看 | N | N |
| Agent 轨迹（Input/Output） | 可查看，可取消脱敏 | 可查看，不可取消脱敏 | N | N |
| 设置-基础信息 | 可查看 + 可编辑 | 可查看 | N | N |
| 设置-上报 Key | 可取消脱敏，可复制，可重新生成 | 仅查看脱敏 Key | N | N |
| 设置-读取 Key | 可取消脱敏，可复制，可重新生成 | 仅查看脱敏 Key | N | N |
| 设置-权限管理 | 可查看 + 可编辑 | 可查看，不可编辑 | N | N |
| Agent 接入 | 可操作 | 不可操作 | N | N |
| 调用 `/api/public/traces*` | N | N | Y | N |
| 调用 `/api/public/observations*` | N | N | Y | N |
| 调用未来写入接口 | N | N | N | Y |

补充说明：

1. 管理员可以添加或移除成员，可以设置其他人为管理员
2. 管理员可以移除其他管理员，空间至少保留 1 名管理员
3. 成员拥有空间内所有数据的查看权限，但不能修改空间配置或接入新 Agent
