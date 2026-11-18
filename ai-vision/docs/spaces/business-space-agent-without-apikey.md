# Business Space Agent Without ApiKey

## Background

业务空间已经具备独立的 Observation project，以及 READ / WRITE credentials。

在这个前提下，业务空间设置页继续展示 `API Key 与映射`，并且在“接入 agent”时继续自动创建 `ApiKey`，会带来两个问题：

- 语义重复，用户不知道应该使用空间 `ApiKey` 还是 Observation credentials
- 历史兼容模型会持续把无实际价值的 `ApiKey` 写入业务空间

## Scope

本次只处理以下范围：

- `业务空间` 设置页不再展示旧的 `API Key 与映射`
- `业务空间 + 接入 agent` 时不再自动创建 `ApiKey`

本次明确不处理：

- 历史业务空间中已经存在的 `ApiKey` 清理
- 个人空间现有 `ApiKey` 逻辑
- 其他依赖 `ApiKey` 的旧功能全面下线

## Design

### 1. Settings API

`GET /api/v1/spaces/{space_id}/settings`

行为调整：

- 若是个人空间，保持原有返回：
  - `api_key`
  - `langfuse`
- 若是业务空间，不再返回上述旧字段，只返回：
  - `observation`
  - `write_key`
  - `read_key`
  - `permissions`

目的：

- 避免业务空间页面继续强调旧 `ApiKey`
- 让前端直接以 Observation 为唯一接入语义

### 2. Settings Page

前端设置页按空间类型分流：

- 个人空间：保留 `API Key 与映射`
- 业务空间：只展示 Observation 配置与读写 key

### 3. Agent Create

`POST /api/v1/spaces/{space_id}/agents`

行为调整：

- 若是个人空间：
  - 继续复用/自动创建 `ApiKey`
  - 保持原有兼容行为
- 若是业务空间：
  - 不再自动创建 `ApiKey`
  - `Agent` 直接以 `api_key = null` 创建

### 4. Data Model

为了允许业务空间 agent 不依赖 `ApiKey`，需要把：

- `Agent.api_key`

从必填外键改为可空外键。

## Compatibility

- 历史 `Agent` 仍可继续关联已有 `ApiKey`
- 历史业务空间中已有的 `ApiKey` 不做删除
- 新创建的业务空间 agent 不再写入新的 `ApiKey`
- 个人空间不受影响

## Current Stage Decision

当前阶段接受以下兼容状态：

- 业务空间的主接入能力来自 Observation project 的 `READ / WRITE` credentials
- `Agent.api_key` 在业务空间下允许为空
- 这不是遗漏，而是有意的阶段性设计

前提：

- 当前业务空间页面已经展示独立的读写 key
- 当前主链路不依赖业务空间 `Agent.api_key`
- `业务空间 + 自定义 agent 接入` 的更细粒度绑定逻辑，后续再单独设计

这意味着当前实现目标不是“立即让业务空间 agent 绑定一条新的 credential 外键”，而是先停止继续写入无实际价值的 `ApiKey`，同时保持现有主链路可用。

## Rationale

这是一次有意收敛范围的改造。

原因：

- 目标是先解决“业务空间语义错误”和“业务空间继续写入无意义 ApiKey”两个问题
- 不在这次改造里全面拆除旧 `ApiKey` 模型，可以避免影响过大
- 后续如果要继续推进，可以再单独做 `Agent` 与旧 `ApiKey` 模型的彻底解耦
