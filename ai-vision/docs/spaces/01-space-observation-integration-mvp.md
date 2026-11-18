# 01. 空间级 Observation 接入一期设计

> **状态：一期设计文档（历史记录）。文中引用的部分文件路径为编写时结构，后续已重构——前端目录从 `aivision-fe/` 迁移为 `vfe/`，后端 `apps/core/views.py` 单文件已拆分为 `apps/core/views/` 包。具体现状以代码为准。**

## 目录

- [文档范围](#文档范围)
- [1. 背景](#1-背景)
- [2. 目标](#2-目标)
- [3. 设计原则](#3-设计原则)
- [3.1 一期范围内的权限口径](#31-一期范围内的权限口径)
- [4. 现状分析](#4-现状分析)
- [5. 总体架构](#5-总体架构)
- [6. 核心领域模型概览](#6-核心领域模型概览)
- [7. 为什么不复用现有 ApiKey](#7-为什么不复用现有-apikey)
- [7.1 是否复用现有表](#71-是否复用现有表)
- [7.2 个人空间是否需要迁移](#72-个人空间是否需要迁移)
- [8. 空间与 Langfuse 的绑定策略](#8-空间与-langfuse-的绑定策略)
- [9. 空间创建与初始化流程](#9-空间创建与初始化流程)
- [9.4 计费归属](#94-计费归属)
- [10. 与现有 DataSource / Adapter 的关系](#10-与现有-datasource--adapter-的关系)
- [11. 迁移策略](#11-迁移策略)
- [12. 时序示意](#12-时序示意)
- [13. 第一阶段 MVP 范围](#13-第一阶段-mvp-范围)
- [14. 跨空间数据关联](#14-跨空间数据关联)
- [15. 空间生命周期](#15-空间生命周期)
- [16. 前端空间选择器兼容性](#16-前端空间选择器兼容性)
- [17. 第二阶段扩展](#17-第二阶段扩展)
- [18. 结论](#18-结论)

## 文档范围

本文聚焦空间级 Observation 接入的一期总体方案，主要说明为什么要改、一期要做到什么程度、核心链路如何设计、以及新老链路如何迁移共存。

本文不展开接口字段细节、鉴权流程细节、SQL 落库细节和看板配置细节，这些内容分别放在 02、03、04 文档中。

## 1. 背景

当前 AI Vision 后端已经具备业务空间、空间成员、Trace 查询等基础能力，但现有外部观测数据接入方式仍偏向静态配置和内部控制台查询，无法满足以下新的产品要求：

1. 当前兼容链路仍对接 Langfuse Public API 语义，但最终形态不会暴露 `Langfuse` 作为内部核心组件；平台需要抽象统一的“观测项目 / 存储网关”模型。
2. 大多数业务空间共用一个默认观测网关 endpoint，少数高保业务空间会走独立 endpoint。
3. 业务空间创建时需要生成一份上报 Key（WRITE scope）和一份读取 Key（READ scope），且需要持久化。只有读取 Key 允许查询数据，上报 Key 不允许读。每个空间固定只有一份上报 Key 和一份读取 Key，不允许创建额外的。
4. AIVision 用户进入业务空间后，需要能够按 agent 名称筛选轨迹数据。
5. 对外提供兼容 Langfuse Public API 的轨迹列表、轨迹明细、span 明细接口，并在网关层完成 AIVision 自身的鉴权。

当前代码存在以下限制：

- `ApiKey` 是用户级模型，不适合承载空间级共享的读写分离凭证。
- 观测配置当前通过 `agent_product -> DataSource` 查找，不是 `space -> observation project`。
- Trace 查询默认绑定当前登录用户 `work_id`，不是空间级通用查询模型。
- 对外接口目前统一包装为 `{code, message, data}`，不适合做 Langfuse 协议兼容层。

## 2. 目标

本方案目标是将当前接入模型升级为：

- 以 `Space` 为主实体绑定统一的 observation project。
- 由平台统一管理默认 endpoint 和独立 endpoint。
- 每个空间创建时自动生成一份上报 Key（WRITE）和一份读取 Key（READ）。
- 内部控制台接口支持在业务空间内按 agent 名称筛选轨迹。
- 对外提供兼容 Langfuse Public API 的查询接口，并由 AIVision 完成自身的 Basic Auth。

## 3. 设计原则

1. 空间是 observation project 归属的核心实体。
2. AIVision 对外签发的是网关凭证，不直接暴露上游真实存储网关凭证。
3. 上游存储网关管理凭证和下游业务方使用的 PK/SK 必须隔离。
4. 控制台接口和对外兼容接口分层设计，分别面向人和系统。
5. 读写权限在服务端严格区分，不能依赖 UI 隐藏。
6. 默认 endpoint 统一管理，独立 endpoint 作为扩展能力接入。
7. 所有密钥都要支持持久化、重新生成和审计。
8. 新模型字段风格遵循现有项目约束：状态字段优先使用 `SmallIntegerField`，时间字段沿用 `gmt_create/gmt_modified/gmt_deleted`，ForeignKey 保持 `db_constraint=False`。

## 3.1 一期范围内的权限口径

为避免实现歧义，一期明确采用以下权限边界：

1. AIVision 用户进入业务空间后，traces 列表、session 列表按 agent 名称筛选，不按当前登录用户 `username` 过滤。
2. 对外兼容接口 `POST /api/public/traces/{trace_id}`、`GET /api/public/observations/{observation_id}` 允许外部应用在通过 Basic Auth 后直接按 ID 查询，不叠加 agent 或 username 过滤。
3. 一期外部兼容接口仅做 Basic Auth 校验，不增加额外的请求签名或 URI 校验。
4. 如果后续需要增强安全性，可再和调用方协商增加签名算法；该能力不属于一期范围。

## 4. 现状分析

现有系统中可复用的基础对象：

- `Space` / `SpaceMember` 已有业务空间与成员模型。
- 当前已有基于 Langfuse 的 traces/session/detail 查询逻辑。
- 已存在 IAM/JWT 鉴权链路。

现有系统中的关键问题：

1. `ApiKey` 为 `user + space` 维度，并非空间共享凭证，也没有读写 scope。
2. `get_langfuse_config(space, agent_product)` 当前依赖 `DataSource` 和 `agent_product`，不适合空间级 project 管理。
3. `TraceListView` 当前把过滤逻辑绑定到当前登录用户 `work_id`，无法表达业务空间按 agent 名称筛选的产品需求。
4. 当前对 Langfuse 的调用是“内部查询接口”，不是“对外兼容 API 网关”。

## 5. 总体架构

整体分为四层：

1. 配置层
   - 管理默认 Observation Gateway endpoint 和高保独立 endpoint。
   - 管理 organization 绑定信息。

2. 绑定层
   - 管理 `Space -> Observation Project` 一对一关系。

3. 凭证层
   - 管理空间级读取 Key（READ）/ 上报 Key（WRITE）。
   - 负责 Basic Auth 校验、重新生成和审计。

4. 代理层
   - 负责将外部兼容请求转发到上游 Observation 网关。
   - 负责过滤器合并、协议透传和错误映射。

请求入口分为两类：

- 内部控制台接口：`/api/v1/spaces/...`
- 外部兼容接口：`/api/public/...`

## 6. 核心领域模型概览

### 6.1 ObservationEndpointConfig

表示一个可用的观测存储网关接入点。

### 6.2 SpaceObservationProject

表示一个业务空间与 observation project 的绑定关系。

建议约束：

- `unique_together = ["space"]`

### 6.3 SpaceObservationCredential

表示业务空间的上报 Key / 读取 Key。当前阶段 PK/SK 从 Langfuse 获取，后续去 Langfuse 化时替换凭证来源，模型不变。

建议约束：

- `public_key` 全局唯一
- 同一空间项目下固定一条 `READ`（读取 Key）和一条 `WRITE`（上报 Key），不允许新增额外凭证
- 支持重新生成（旧 Key 立即失效，历史数据不受影响）

### 6.4 SpaceObservationCredentialAudit（可选）

用于记录敏感操作和认证行为。

## 7. 为什么不复用现有 ApiKey

现有 `ApiKey` 不适合直接承担新的 Langfuse 网关职责，原因如下：

1. 绑定维度偏向用户，不是空间共享。
2. 没有 `scope` 字段，无法表达读写权限。
3. 只存 `hash/prefix`，不具备密文回显和运维恢复能力。
4. 已混入历史 `langfuse_project_id` 等语义，继续扩展会进一步耦合。

结论：

- 现有 `ApiKey` 保留原用途。
- Observation 空间集成单独建模。

## 7.1 是否复用现有表

这里建议采用“新增表 + 兼容迁移”的方案，不建议直接复用 `ApiKey` / `DataSource`。

### 方案 A：直接复用 `ApiKey` / `DataSource`

优点：

- 表面上 migration 更少。
- Django Admin 初期可少配几张表。

缺点：

1. `ApiKey` 当前是 `user + space` 维度，且字段语义已带有历史包袱，不适合表示空间共享的 `READ/WRITE` 网关凭证。
2. `DataSource` 当前按 `agent_type + datasource_type` 建模，天然是 agent 维度，不适合作为空间主配置。
3. 如果往旧表里继续塞 `scope`、`endpoint`、`project`、`ciphertext` 等语义，会让旧链路和新链路长期耦合。
4. 后续要做轮转、审计、后台重试、禁用时，旧表语义会越来越混乱。

### 方案 B：新增 `ObservationEndpointConfig` / `SpaceObservationProject` / `SpaceObservationCredential`

优点：

1. 和当前目标模型一致，领域边界清晰。
2. 可以保留老接口和老表，按空间逐步迁移，风险更低。
3. Django Admin 里可独立配置 endpoint、project 绑定、credential 状态，运维更直接。
4. 后续做外部兼容 API、轮转、审计时不需要继续污染旧表。

结论：

- 一期推荐采用方案 B。
- `ApiKey` 与 `DataSource` 保留原功能。
- Observation 空间级集成使用新表承接。
- 查询链路采用“新表优先，旧逻辑兜底”的迁移模式。

## 7.2 个人空间是否需要迁移

建议：一期不强制迁移个人空间，只迁移业务空间。

原因：

1. 当前方案的核心诉求是“空间共享 project、共享写凭证、成员协作、外部应用查询”，这些都首先服务于业务空间。
2. 个人空间当前没有明显的共享凭证和成员协作诉求，优先级较低。
3. 先迁业务空间可以控制风险，避免一次性把所有空间都切到新链路。

建议策略：

- `Space.type = 2` 的业务空间：一期迁移到新表。
- `Space.type = 1` 的个人空间：一期维持旧逻辑。
- 如果后续个人空间也需要外部兼容 API 或共享凭证，再补迁到新表。

## 8. 空间与 Langfuse 的绑定策略

### 8.1 默认业务空间

- 使用默认 `ObservationEndpointConfig`
- 使用统一的默认 `tenant_name`
- 每个空间在该 organization 下创建独立 project

### 8.2 高保业务空间

- 空间元数据显式指定独立 `endpoint_config`
- 可有独立的 `base_url`
- 可有独立的 organization
- 下游调用逻辑与普通空间一致，仅上游路由不同

### 8.3 路由原则

所有空间级查询和后续写入，都通过：

- `space -> SpaceObservationProject -> ObservationEndpointConfig`

来决定上游地址，不再通过：

- `agent_product -> DataSource`

决定。

## 9. 空间创建与初始化流程

### 9.1 创建方式分期

- **一期（P0）**：通过 Django Admin 后台手动创建业务空间。建议实现 `@admin.action`，在 Admin 界面选择空间后一键完成 Observation Project 初始化和上报 Key / 读取 Key 生成。
- **二期（P1）**：接入内部审批流程平台，实现用户自助创建 → 超管审批 → 自动初始化的产品化流程。

### 9.2 目标流程

1. 创建业务空间
2. 创建空间管理员成员关系
3. 解析该空间应使用的 endpoint
4. 创建 `SpaceObservationProject(provision_status=PENDING)`
5. 立即返回空间创建成功
6. 由同步逻辑或后台补偿逻辑调用上游观测网关创建 project
7. 保存 `upstream_project_id/name`
8. 生成一份读取 Key（READ）和一份上报 Key（WRITE）
9. 持久化 `SpaceObservationCredential`
10. 将 `provision_status` 更新为 `ACTIVE` 或 `FAILED`

说明：

- 每个空间固定生成一份读取 Key 和一份上报 Key，不允许新增额外凭证
- 支持重新生成 Key（旧 Key 立即失效，历史数据不受影响）

### 9.4 计费归属

AI Vision 本身不计费，底层使用 AntMonitor 云原生监控进行数据采集和存储，该部分需要计费。

#### 计费粒度与归属

计费按数据上报时使用的上报 Key 归属结算。核心关系为：

```
1 业务空间 = 1 上报 Key = 1 AntMonitor 云原生监控数据源 = 1 计费单位
```

这三者是 1:1:1 的关系，上报 Key 是最小计费单位。所有通过该上报 Key 上报的数据，计费都归到该业务空间对应的 AntMonitor 数据源上。

在此基础上，需要解决"这笔费用结算给谁"的问题，即计费归属（`billing_owner`），记录费用应结算到哪个 BU / 成本中心。

示例：

| 空间 | 上报 Key | AntMonitor 数据源 | 计费归属 |
|------|---------|------------------|---------|
| HomiClaw 业务空间 | `pk-aiv-w-homi-xxx` | `homiclaw-observation` | HomiClaw 所属 BU |
| 个人空间 | 个人上报 Key | 统一默认数据源 | 挂靠「AntMonitor」产品，暂不分摊 |

#### 计费规则

- **业务空间**：计费挂载到云原生监控数据源（1 个业务空间 = 1 个同名数据源），计费归属由申请人创建空间时选择，费用结算到业务方
- **个人空间**：统一挂靠到「AntMonitor」产品下，暂不分摊
- **跨空间场景**：内置 Agent（如 HomiClaw）通过业务空间上报 Key 服务端统一上报，计费在业务空间。个人空间虽可查看该 Agent 的数据切片，但数据归属和计费均在业务空间，个人空间用户无需为此付费

#### 一期设计

一期通过 Django Admin 后台创建业务空间，计费归属在 `Space` 表中记录：

- `Space` 表新增 `billing_owner` 字段（`VARCHAR(256)`，可选），用于记录计费归属信息（如 BU / 成本中心标识）
- 一期由运维在 Admin 界面手动填写，不影响空间创建流程
- 计费归属信息随空间一起展示在空间设置-基础信息页面

#### 二期扩展

二期产品化创建表单中，计费归属作为必填字段（下拉选择框），审批通过后：

1. 系统自动传参（数据源名称、描述、计费归属、管理员）调用云原生监控接口创建同名数据源
2. 创建失败不阻塞空间创建，记录错误并支持后台重试

#### 与 AntMonitor 数据源的关系

- 一期：AntMonitor 数据源由运维手动创建，`billing_owner` 仅作为 AIVision 侧的记录字段
- 二期：AIVision 自动调用 AntMonitor API 创建数据源，`billing_owner` 作为传参依据

### 9.3 错误处理策略

不建议把“上游 project 创建成功”作为业务空间创建事务的硬前置条件。

建议处理方式：

- 空间先创建成功
- Observation provisioning 失败时记录为 `FAILED`
- `last_error` 保留失败原因
- 后台支持重试或补偿修复
- 前端设置页基于 `provision_status` 展示“初始化中 / 已成功 / 初始化失败”

原因：

- 外部依赖失败不应导致空间实体完全不可用
- 补偿式模型更适合处理第三方系统波动

## 10. 与现有 DataSource / Adapter 的关系

现有 `DataSource` 与 `TracesQueryAdapter` 更适合做 dashboard 查询适配，不适合做空间级 observation project 主配置。

保留策略：

- `DataSource` 继续服务于 dashboard / query adapter
- 新增 `SpaceObservationProject` 承担空间级 observation 配置
- 内部 trace/session/detail 接口改为优先读取空间 project
- 后续若 dashboard 也需要空间级 project，可在 adapter 层补充 space-aware 逻辑

## 11. 迁移策略

### 11.1 第一阶段

- 引入新表结构
- 后台录入默认 endpoint
- 不修改老的个人空间现有逻辑

### 11.2 第二阶段

- 新建业务空间走新链路
- 内部 trace 查询优先读取 `SpaceObservationProject`
- 若老空间尚未补数，可临时回退旧逻辑

### 11.3 第三阶段

- 为老的业务空间补建 `SpaceObservationProject`
- 完成后移除旧的 `get_langfuse_config(space, agent_product)` 主路径
- `DataSource` 不再承担空间级 observation host/pk/sk 职责

### 11.4 推荐迁移实现方式

查询链路建议按以下优先级实现：

1. 优先读取 `SpaceObservationProject`
2. 若空间未迁移，回退到当前 `get_langfuse_config(space, agent_product)` 逻辑
3. 在全部业务空间完成迁移后，再删除旧逻辑

这样可以保证：

- 前端参数 `agent_product` 暂时还能保留，不会立刻打断现有调用方
- 新老空间可以并存迁移
- Django Admin 可以先录基础配置，再逐步切流

## 12. 时序示意

### 12.1 创建业务空间

```text
用户 -> AIVision: 创建空间
AIVision -> DB: 创建 Space / SpaceMember
AIVision -> EndpointResolver: 解析默认或独立 endpoint
AIVision -> DB: 创建 SpaceObservationProject(PENDING)
AIVision -> Upstream Gateway: 创建 project
Upstream Gateway -> AIVision: 返回 project_id / project_name
AIVision -> CredentialService: 生成读取 Key(READ) / 上报 Key(WRITE)
AIVision -> DB: 保存 project 与 credential
AIVision -> 用户: 返回空间信息
```

### 12.2 外部系统查询 traces

```text
外部系统 -> AIVision: GET /api/public/traces + Basic Auth
AIVision -> CredentialStore: 校验 public_key / secret_hash / scope
AIVision -> ProjectBinding: 解析 space project 与 endpoint
AIVision -> ProxyLayer: 组装 filter 与 query
ProxyLayer -> Upstream Gateway: GET /api/public/traces
Upstream Gateway -> ProxyLayer: 返回 traces 结果
ProxyLayer -> 外部系统: 透传响应
```

## 13. 第一阶段 MVP 范围

建议第一阶段只做以下闭环：

1. 通过 Django Admin `@admin.action` 一键创建业务空间并绑定 observation project
2. 支持默认 endpoint 和少量高保独立 endpoint
3. 自动生成一份读取 Key（READ）和一份上报 Key（WRITE）并持久化
4. 空间设置页展示 project 状态、上报 Key 和读取 Key（默认脱敏展示）
5. 内部控制台 trace 查询支持按 `agentName` 筛选
6. 对外开放三类只读兼容接口：
   - `/api/public/traces`
   - `/api/public/traces/{trace_id}`
   - `/api/public/observations/{observation_id}`
7. 外部兼容接口完成 Basic Auth 和 scope 校验，且只允许读取 Key 查询数据
8. 明细接口一期允许按 ID 直接访问，不做 URL 签名校验
9. Key 支持重新生成（旧 Key 立即失效）

## 14. 跨空间数据关联

### 14.1 背景

共享接入的 Agent 通过业务空间的上报 Key 进行服务端统一上报，数据归属于业务空间。但用户进入个人空间时，需要能看到这些 Agent 中属于自己的数据切片。

### 14.2 关联原则

1. **数据只存一份**：同一条数据不在多个空间重复存储，个人空间通过关联查询展示业务空间的数据切片
2. **按用户标识关联**：内置 Agent 上报数据时携带用户标识（`metadata.attributes.ant.username`），个人空间按用户标识从业务空间关联展示
3. **自动建立，无需手动操作**：系统根据用户标识自动匹配，用户无感知
4. **业界官方 Agent 不涉及**：业界官方 Agent 数据完全在个人空间内

### 14.3 关联关系发现机制

个人空间需要知道"该查哪些业务空间的数据"。建议通过以下方式自动建立关联：

1. **基于 Agent 接入关系自动推导**：用户在个人空间启用共享接入的 Agent 后，系统已知该 Agent 同时存在于某个业务空间。个人空间查询时，自动关联该 Agent 对应的业务空间
2. **通过 Agent 表关联**：`aivision_agent` 表中内置 Agent 的 `agent_product` 保存稳定的产品标识，可用于查找关联的业务空间
3. **不需要额外的映射表**：一期无需新增 `space_agent_enrollment` 之类的关联表，利用现有 Agent 表中 `agent_product` + `space.type` 即可推导

#### 不关联的场景

- 业界官方 Agent（Claude Code、Codex）：数据完全在个人空间内，不涉及跨空间

### 14.4 查询设计方向

个人空间查询 traces 时：

1. 先查个人空间本地数据（业界官方 Agent、自定义接入）
2. 通过 Agent 表查找当前用户已启用的共享 Agent 对应的业务空间列表
3. 对每个关联业务空间，使用该业务空间的 `SpaceObservationProject` 配置，按 `metadata.attributes.ant.username = 当前用户标识` 过滤查询
4. 合并返回，每条 trace 标注数据来源空间（`source_space_id` / `source_space_name`）

#### 性能考量

- 关联业务空间数量通常很少（一个用户启用的内部 Agent 一般 1-5 个），不会产生大量跨空间请求
- 可对关联查询结果设置独立的 `limit`，避免单个业务空间返回过多数据
- 查询可并行发起，合并后按时间排序
- 后续如果关联空间增多，可引入缓存层（如 Redis 缓存用户的关联空间列表，TTL 5 分钟）

#### 前端数据来源标注

- 来自个人空间的数据正常展示
- 来自业务空间的数据在列表中增加来源标签（如"来自 HomiClaw 业务空间"）
- 点击来源标签可跳转到对应业务空间（需校验用户是否有该业务空间的成员权限）

### 14.5 实现优先级

一期不实现跨空间关联查询，优先保证业务空间和个人空间各自独立查询能力。二期补充此能力。

### 14.6 一期预留

虽然一期不实现跨空间查询，但建议在以下方面预留扩展性：

1. Trace 查询响应结构预留 `source_space_id` 字段（一期可为空）
2. 个人空间 trace 查询接口的实现中，保留后续注入跨空间查询逻辑的扩展点
3. 内部 Agent 的 `agent_product` 枚举值在业务空间和个人空间中保持一致，确保后续可通过 `agent_product` 做跨空间关联

## 15. 空间生命周期

### 15.1 空间删除

1. 仅管理员可发起删除申请，需超管审批
2. 删除后数据保留 30 天（可恢复），到期后彻底清除
3. 删除期间空间状态标记为 `DELETING`，所有查询和写入接口返回不可用
4. 恢复操作由超管在后台执行

### 15.2 成员退出与账号停用

空间管理员通过成员管理移除成员；账号停用由平台管理员处理。历史数据归属保持不变，访问权限按当前账号状态和空间成员关系校验。成员管理规则见 [空间成员管理](12-space-member-management-design.md)。

### 15.3 实现边界

空间删除和恢复以实际管理接口为准；本文的早期生命周期设想不代表已上线能力。

## 16. 前端空间选择器兼容性

### 16.1 现有代码支持情况

经代码检查，当前空间列表接口和前端组件已基本支持业务空间：

- **后端 `SpaceListView`** (`apps/core/views.py:355`)：查询当前用户作为成员的所有空间，不过滤 `type`，已能返回业务空间
- **`SpaceSerializer`** (`apps/core/serializers.py:25`)：响应中包含 `type` 字段
- **前端 Sidebar 空间选择器** (`src/components/layout/Sidebar.tsx:21`)：展示 API 返回的所有空间，不过滤 type

### 16.2 需要调整的点

- **`SpaceContext.tsx:29`**：初始化时仅查找 `type=1` 的个人空间作为默认空间。需要调整为：默认选中个人空间，但用户手动切换到业务空间后记住选择（如 localStorage）
- **空间选择器 UI**：建议个人空间置顶，业务空间按名称排列在下方，两组之间可加分隔线以区分

### 16.3 一期改动范围

一期只需确保：

1. `SpaceContext` 初始化逻辑兼容业务空间切换
2. 空间选择器能正确展示业务空间（已支持）
3. 切换到业务空间后，所有页面数据上下文跟随变化（已由 `space_id` URL 参数机制保证）

## 17. 第二阶段扩展

第二阶段可以继续做：

- 完善团队空间自助管理
- 跨空间数据关联查询
- 空间删除与恢复产品化
- 账号停用与成员权限联动
- `/api/public/otel` 写入代理
- 页面内嵌能力（iframe 嵌入 Agent 轨迹列表页/详情页，鉴权由平台侧完成）
- ODPS 表导出（每个空间对应一张 ODPS 表，支持导出指标、事件、轨迹）
- 凭证审计页面
- 限流与 IP 白名单
- 老空间数据补齐任务

## 18. 结论

本方案的核心变化是把当前“按 agent_product 配置 Langfuse”的模式，升级为“按空间绑定 observation project，并由 AIVision 统一签发读写网关凭证”的模式。

这样可以统一解决：

- 默认 organization 共享
- 高保业务独立 endpoint
- 读写分离（上报 Key / 读取 Key）
- 凭证持久化与重新生成
- 上报 Key 不可读、读取 Key 专用于查数
- 按 agent 名称筛选查询
- 外部应用按 ID 直接查详情
- 对外兼容 Langfuse Public API
- 平台侧权限校验和后续可运营能力

这也是后续继续扩展写入代理、页面内嵌、ODPS 导出、跨空间关联、审计和限流的正确基础。
