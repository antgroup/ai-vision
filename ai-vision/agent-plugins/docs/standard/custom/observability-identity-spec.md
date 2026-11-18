## AI Agent 可观测身份标识规范 v2.0

> 本规范定义了 AI Agent 可观测体系中**用户身份**和 **Agent 身份**的标准属性，覆盖 Langfuse Traces、OTel Metrics/Events 等所有可观测信号。规范明确了不同上报场景下各属性的必填要求，确保数据可归属、可聚合、可分析。

文中的用户、密钥和实例值均为合成示例，仅用于说明字段含义。

---

### 一、背景与目标

AI Agent 可观测数据的上报涉及两类身份识别需求：

- **用户身份**：这条数据是**谁**产生的？
- **Agent 身份**：这条数据是**哪个 Agent**、**哪个产品**、**哪个实例**产生的？

在实际场景中，用户身份的获取途径和上报通道各不相同，需要一套统一的属性定义来支撑跨场景的数据关联和分析。

#### 1.1 用户身份获取途径

| 途径 | 说明 | 适用场景 |
|------|------|---------|
| **Langfuse PK 关联** | 通过 Langfuse API 查询 PK 与 username 的绑定关系 | 个人/业务 Langfuse Trace 上报 |
| **AIVision PK 关联** | 通过 AIVision 后端 DB 查询 PK 与 username 的绑定关系 | 业务指标/事件上报（规划中） |
| **直接上报 username** | 在 Metrics/Events 的上报属性中直接指定 `username`；Langfuse 场景通过 `LANGFUSE_ATTR_USERNAME` 环境变量设置 | 个人指标/事件上报（antclaudecode、claudecode） |

#### 1.2 四种上报场景

| 场景 | 上报通道 | 数据类型 | PK 归属 | 用户身份来源 | 状态 |
|------|---------|---------|---------|-------------|------|
| **个人 + Langfuse** | Langfuse | Trace | 个人 PK | PK 关联 + userId 字段 | ✅ 已落地 |
| **业务 + Langfuse** | Langfuse | Trace | 业务项目 PK | userId 字段（服务端注入） | ✅ 已落地 |
| **个人 + 指标/事件** | OTel | Metrics / Events | 无 PK | 直接上报 `username` 属性 | ✅ 已落地 |
| **业务 + 指标/事件** | OTel | Metrics / Events | AIVision PK | PK 关联 + `username` 属性 | 📋 规划中 |

---

### 二、核心属性定义

#### 2.1 属性总览

| 属性 | OTel 字段名 | Langfuse 映射 | 类型 | 说明 | 示例 |
|------|------------|--------------|------|------|------|
| **用户身份** | `username` | `userId` | string | 用户标识 | `"100001"` |
| **接入密钥** | `public_key` | Langfuse PK（隐式） | string | 接入方的 PK，通过前缀区分平台 | `"pk-lf-xxx"`, `"pk-av-xxx"` |
| **Agent 类型** | `agent.type` | `metadata.agent_type` | string | Agent 底层引擎类型 | `"claudecode"`, `"opencode"`, `"openclaw"` |
| **产品类型** | `agent.product` | `metadata.agent_product` | string | 面向用户的产品名称 | `"antclaudecode"`, `"homeclaw"` |
| **实例名称** | `agent.instance` | `metadata.agent_instance` | string | 具体部署实例的唯一标识 | `"claudecode-100001"`, `"openclaw-prod-01"` |

#### 2.2 属性层次关系

```
public_key (接入密钥)
  │  PK 标识一个接入方（个人或业务项目）
  │  通过 PK 前缀区分平台：pk-lf-* = Langfuse, pk-av-* = AIVision
  │
  ├── username (用户身份)
  │     用户标识，所有数据归属的最终锚点
  │
  └── agent.product (产品类型)
        │  面向用户的产品名称
        │
        ├── agent.type (Agent 类型)
        │     底层引擎类型
        │
        └── agent.instance (实例名称)
              具体部署实例，PK 可直接关联到实例级别
```

#### 2.3 各属性详细定义

##### `username` — 用户身份

用户标识用于关联同一账号的遥测。网页接入由服务端根据当前账号 UUID 注入；Langfuse 可通过已有身份绑定关联外部用户标识。

| 项目 | 说明 |
|------|------|
| **格式** | 稳定的字符串，如账号 UUID 或 `"user-001"` |
| **唯一性** | 全局唯一，不可变 |
| **必填性** | 所有场景必填（缺失时降级为 `"unknown"`） |
| **获取方式** | 环境变量配置（Langfuse 场景使用 `LANGFUSE_ATTR_USERNAME`）/ PK 反查 / 服务端注入 / 上报属性直传 |

> **约定**：标识不要求纯数字或固定长度，不进行补零；展示名称与用于查询隔离的标识分开处理。

##### `public_key` — 接入密钥

标识数据的接入方，通过前缀区分所属平台。PK 可以关联到实例级别。

| 前缀 | 平台 | 用户身份获取方式 | 示例 |
|------|------|----------------|------|
| `pk-lf-` | Langfuse | 通过 Langfuse API 查询 PK → username 绑定 | `pk-lf-abc123` |
| `pk-av-` | AIVision | 通过 AIVision 后端 DB 查询 PK → username 绑定 | `pk-av-xyz789` |

**PK 与实例的关联**：

- **个人场景**：一个 PK 对应一个用户的一个 Agent 实例（如 `pk-lf-abc123` → `claudecode-100001`）
- **业务场景**：一个 PK 对应一个业务项目的 Agent 实例，PK 下包含多个用户的数据，用户归属仍由每条遥测的用户字段区分

##### `agent.type` — Agent 类型

Agent 的底层引擎类型，标识使用的核心技术。

| 值 | 说明 |
|----|------|
| `claudecode` | 基于 Claude Code 引擎 |
| `opencode` | 基于 OpenCode 引擎 |
| `openclaw` | 基于 OpenClaw 引擎 |

> 新增 Agent 类型时需在本规范中注册。

##### `agent.product` — 产品类型

面向用户的产品名称。同一个 `agent.type` 可能对应多个产品。

| 产品 | Agent 类型 | 说明 |
|------|-----------|------|
| `claudecode` | `claudecode` | 原版 Claude Code |
| `antclaudecode` | `claudecode` | 定制版 Claude Code |
| `openclaw` | `openclaw` | OpenClaw 标准版 |
| `homeclaw` | `openclaw` | OpenClaw 家庭版 / 定制版 |
| `opencode` | `opencode` | OpenCode 标准版 |

##### `agent.instance` — 实例名称

具体部署实例的唯一标识，格式为 `{product}-{identifier}`。

| 示例 | 说明 |
|------|------|
| `claudecode-100001` | 用户 100001 的 Claude Code 实例 |
| `antclaudecode-100001` | 用户 100001 的 AntClaudeCode 实例 |
| `opencode-example-01` | 合成示例中的 OpenCode 实例 |
| `homeclaw-333` | HomeClaw 333 号实例 |

---

### 三、各上报场景的属性要求

#### 3.1 场景一：个人 + Langfuse（Trace）

用户在本地配置个人 Langfuse PK，上报 Claude Code 等 Agent 的 Trace 数据。

| 属性 | 必填 | 来源 | 示例 |
|------|------|------|------|
| `username` | ✅ | 环境变量 `LANGFUSE_ATTR_USERNAME` | `"100001"` |
| `public_key` | ✅ | 环境变量 `CC_LANGFUSE_PUBLIC_KEY`（隐式，作为 Langfuse 认证） | `"pk-lf-abc123"` |
| `agent.type` | ✅ | Hook 脚本自动设置 | `"claudecode"` |
| `agent.product` | 可选 | Hook 脚本自动设置，未设置时默认 `"default"` | `"antclaudecode"` |
| `agent.instance` | 可选 | Hook 脚本自动生成，未设置时默认 `"default"` | `"antclaudecode-100001"` |
| `session.id` | 推荐 | 从 transcript 中提取 | `"uuid-xxx"` |

**Langfuse Trace 示例**：

```json
{
  "name": "Turn 1",
  "sessionId": "session-abc-123",
  "userId": "100001",
  "metadata": {
    "source": "claude-code",
    "agent_type": "claudecode",
    "agent_product": "antclaudecode",
    "agent_instance": "antclaudecode-100001",
    "turn_number": 1
  },
  "tags": ["antclaudecode", "claudecode"]
}
```

**用户身份解析链路**：

```
CC_LANGFUSE_PUBLIC_KEY (pk-lf-abc123)
    │
    ├─→ Langfuse API: 查询 PK 绑定的 username
    │   (平台侧用于审计和校验)
    │
    └─→ Trace.metadata.username = LANGFUSE_ATTR_USERNAME (100001)
        (上报侧通过环境变量注入到 metadata)
```

#### 3.2 场景二：业务 + Langfuse（Trace）

共享 Agent 服务使用项目级 Langfuse PK 上报 Trace，每条 Trace 分别标识调用者。

| 属性 | 必填 | 来源 | 示例 |
|------|------|------|------|
| `username` | ✅ | 服务端识别调用者后注入 | `"user-001"` |
| `public_key` | ✅ | 服务端配置（隐式，作为 Langfuse 认证） | `"pk-lf-example"` |
| `agent.type` | ✅ | Agent 的实际引擎类型 | `"opencode"` |
| `agent.product` | 可选 | 产品配置，未设置时默认 `"default"` | `"default"` |
| `agent.instance` | 可选 | 实例配置，未设置时默认 `"default"` | `"opencode-example-01"` |
| `session.id` | 推荐 | 当前会话标识 | `"example-session-01"` |

**用户身份注入要求**：

服务端**必须**将调用者的 `username` 注入每条 Trace 的 `userId` 字段。具体身份获取方式由接入服务决定；项目级 PK 只标识接入方，不能替代调用者身份。

> **降级策略**：无法获取时设为 `"unknown"`，在 metadata 中标记 `identity_missing: true`，并记录告警。

**最小合成示例**：

```json
{
  "name": "Example Trace",
  "sessionId": "example-session-01",
  "userId": "user-001",
  "metadata": {
    "agent_type": "opencode",
    "agent_product": "default",
    "agent_instance": "opencode-example-01"
  }
}
```

#### 3.3 场景三：个人 + 指标/事件（Metrics/Events）

用户在本地配置 `username`，通过 OTel 协议上报 Metrics 和 Events。

| 属性 | 必填 | 来源 | 示例 |
|------|------|------|------|
| `username` | ✅ | 环境变量直接配置（如 `AGENT_USERNAME`） | `"100001"` |
| `public_key` | 不适用 | 此场景无 PK，通过 `username` 直接标识 | — |
| `agent.type` | ✅ | 上报 SDK 自动设置 | `"claudecode"` |
| `agent.product` | 可选 | 上报 SDK 自动设置，未设置时默认 `"default"` | `"antclaudecode"` |
| `agent.instance` | 可选 | 上报 SDK 自动生成，未设置时默认 `"default"` | `"antclaudecode-100001"` |

**OTel Metric 示例**：

```
agent.token.usage{
  username="100001",
  agent.type="claudecode",
  agent.product="antclaudecode",
  agent.instance="antclaudecode-100001",
  type="input",
  model="claude-sonnet-4-6"
} 1500
```

**OTel Event 示例**：

```json
{
  "name": "agent.llm_request",
  "attributes": {
    "username": "100001",
    "agent.type": "claudecode",
    "agent.product": "antclaudecode",
    "agent.instance": "antclaudecode-100001",
    "model": "claude-sonnet-4-6",
    "input_tokens": 1500,
    "output_tokens": 800
  }
}
```

#### 3.4 场景四：业务 + 指标/事件（规划中）

业务方通过新建 AIVision Project，使用 AIVision PK 上报 Metrics 和 Events。

| 属性 | 必填 | 来源 | 示例 |
|------|------|------|------|
| `username` | ✅ | 服务端从请求上下文提取 | `"100001"` |
| `public_key` | ✅ | 服务端配置 | `"pk-av-xyz789"` |
| `agent.type` | ✅ | 服务端硬编码 | `"openclaw"` |
| `agent.product` | 可选 | 服务端硬编码，未设置时默认 `"default"` | `"openclaw"` |
| `agent.instance` | 可选 | 服务端配置，未设置时默认 `"default"` | `"openclaw-example-01"` |

项目级 PK 用于关联接入方与实例；每条 Metric/Event 的 `username` 用于区分用户。

---

### 四、属性必填矩阵

| 属性 | 个人+Langfuse | 业务+Langfuse | 个人+指标/事件 | 业务+指标/事件 |
|------|:------------:|:------------:|:-------------:|:-------------:|
| `username` | ✅ 必填 | ✅ 必填 | ✅ 必填 | ✅ 必填 |
| `public_key` | ✅ 隐式 | ✅ 隐式 | — 不适用 | ✅ 必填 |
| `agent.type` | ✅ 必填 | ✅ 必填 | ✅ 必填 | ✅ 必填 |
| `agent.product` | 可选 | 可选 | 可选 | 可选 |
| `agent.instance` | 可选 | 可选 | 可选 | 可选 |
| `session.id` | 推荐 | 推荐 | 推荐 | 推荐 |

> **说明**：
> **说明**：
> - **✅ 隐式**：Langfuse 场景下 PK 作为 SDK 认证参数传入，不需要显式写入 Trace 属性，但平台侧通过 PK 关联实例
> - **— 不适用**：个人 Metrics/Events 场景无 PK 概念，直接通过 `username` 标识
> - **可选**：`agent.product` 和 `agent.instance` 为可选字段，未设置时默认值为 `"default"`

---

### 五、PK 与实例的关联模型

PK 是连接**接入方**与**实例**的桥梁，平台侧维护 PK → 实例的映射关系。

| 关联维度 | 作用 |
|----------|------|
| PK → 实例 | 定位接入方及其部署实例 |
| 实例 → 产品 / 类型 | 识别产品和底层引擎 |
| 遥测 → 用户 | 区分共享实例中每条 Trace、Metric 或 Event 的用户归属 |

---

### 六、与现有规范的字段映射

#### 6.1 与 Traces 规范的映射

| 本规范属性 | Traces 规范字段 | 映射说明 |
|-----------|---------------|---------|
| `username` | Resource `user.id`（映射） | `username` 写入 Trace metadata，平台侧映射到 `user.id` |
| `agent.type` | `agentic.runtime.name` | 语义一致，`agent.type` 更简洁 |
| `agent.product` | `service.name` | 产品名作为服务名 |
| `agent.instance` | `service.instance.id` | OTel 标准实例标识 |
| `public_key` | — | 新增，Traces 规范中无对应字段 |

#### 6.2 与 Metrics/Events 规范的映射

| 本规范属性 | Metrics/Events 规范字段 | 映射说明 |
|-----------|----------------------|---------|
| `username` | 标准属性 `user.id`（映射） | `username` 作为上报属性，平台侧映射到 `user.id` |
| `agent.type` | — | 新增，补充 `agent_name` 的类型维度 |
| `agent.product` | 标准属性 `agent_name` | `agent.product` 替代 `agent_name` 作为产品标识 |
| `agent.instance` | — | 新增，支持实例级别的数据隔离 |
| `public_key` | — | 新增，业务指标/事件场景使用 |

> **兼容性说明**：现有 Metrics/Events 规范中的 `agent_name` 继续保留，新接入方推荐使用 `agent.product`。两者并存时以 `agent.product` 为准。

---

### 七、分析维度与查询示例

| 分析需求 | 查询条件 | 适用场景 |
|---------|---------|---------|
| 某用户的全量数据 | `username = "100001"` | 跨所有平台和产品 |
| 某用户在某产品的数据 | `username = "100001"` AND `agent.product = "antclaudecode"` | 单产品维度 |
| 某 Agent 类型的全量数据 | `agent.type = "openclaw"` | 引擎维度 |
| 某产品的全量数据 | `agent.product = "homeclaw"` | 产品维度 |
| 某实例的数据 | `agent.instance = "opencode-example-01"` | 实例维度 |
| 某 PK 下的全量数据 | `public_key = "pk-lf-example"` | 接入方维度 |
| 某 PK 下某用户的数据 | `public_key = "pk-lf-example"` AND `username = "user-001"` | 接入方 + 用户 |
| 无归属数据审计 | `username = "unknown"` | 数据质量监控 |

---

### 八、接入校验清单

#### 个人 + Langfuse（Trace）

- [ ] 已配置 `LANGFUSE_ATTR_USERNAME` 环境变量为个人用户标识
- [ ] Trace 的 metadata 中已包含 `username` 字段（通过 `LANGFUSE_ATTR_USERNAME` 自动注入）
- [ ] `metadata.agent_type` 和 `metadata.agent_product` 已正确设置
- [ ] PK 已在平台侧完成与 username 的绑定注册

#### 业务 + Langfuse（Trace）

- [ ] 服务端已实现从请求上下文提取 `username` 的逻辑
- [ ] 所有 Trace 的 `userId` 字段已强制注入调用者用户标识
- [ ] `username` 缺失时有降级策略（`"unknown"` + 告警）
- [ ] `metadata.agent_type`、`metadata.agent_product`、`metadata.agent_instance` 已正确设置
- [ ] PK 已在平台侧完成与实例的绑定注册

#### 个人 + 指标/事件

- [ ] 上报属性中已包含 `username`（用户标识）
- [ ] `agent.type` 和 `agent.product` 已正确设置

#### 业务 + 指标/事件（规划中）

- [ ] 已申请 AIVision PK（`pk-av-` 前缀）（`public_key`）
- [ ] 服务端已实现 `username` 注入逻辑
- [ ] `agent.type`、`agent.product`、`agent.instance` 已正确设置
- [ ] PK 已在 AIVision 后端完成与实例的绑定注册

---

### 九、FAQ

**Q: `agent.type` 和 `agent.product` 有什么区别？**

A: `agent.type` 是底层引擎类型（如 `claudecode`、`openclaw`），`agent.product` 是面向用户的产品名称（如 `antclaudecode`、`homeclaw`）。同一个引擎可以有多个产品形态。例如 `openclaw` 引擎同时支撑了 `openclaw` 和 `homeclaw` 两个产品。

**Q: 为什么需要 `agent.instance`？**

A: 同一个产品可能有多个部署实例（如生产环境、预发环境、不同团队的独立部署）。`agent.instance` 提供实例级别的数据隔离，PK 可以直接关联到实例。

**Q: 个人 Metrics/Events 场景为什么没有 PK？**

A: 个人 Metrics/Events 通过 OTel 协议直接上报到统一采集端，不经过 Langfuse 或 AIVision 平台，因此没有 PK 的概念。用户身份通过上报属性中的 `username` 直接标识。

**Q: PK 前缀的命名规则是什么？**

A: `pk-lf-` 表示 Langfuse 平台的 PK，`pk-av-` 表示 AIVision 平台的 PK。后续如有新平台接入，按 `pk-{platform}-` 的格式扩展。

**Q: 新增 Agent 类型或产品类型需要做什么？**

A: 需要在本规范的第二章「各属性详细定义」中注册新的 `agent.type` 或 `agent.product` 值，确保命名不冲突，并更新相关的接入文档。
