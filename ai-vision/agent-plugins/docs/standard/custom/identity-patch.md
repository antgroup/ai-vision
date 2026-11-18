## 身份标识字段补丁（Identity Patch）

> 基于 [observability-identity-spec.md](observability-identity-spec.md) 提炼，列出需要在现有 Traces / Metrics / Events 规范基础上**额外添加**的身份标识字段。

---

### 一、新增字段总览

| 字段名 | 类型 | 说明 | 枚举值 / 格式 | 示例 |
|--------|------|------|--------------|------|
| `username` | string | 用户标识，数据归属的最终锚点 | 稳定的用户标识字符串 | `"100001"` |
| `public_key` | string | 接入方密钥，通过前缀区分平台 | `pk-lf-*`（Langfuse）, `pk-av-*`（AIVision） | `"pk-lf-abc123"` |
| `agent.type` | string | Agent 底层引擎类型 | `claudecode`, `opencode`, `openclaw` | `"claudecode"` |
| `agent.product` | string | 面向用户的产品名称，未设置时默认 `"default"` | `claudecode`, `antclaudecode`, `openclaw`, `homeclaw`, `opencode` | `"antclaudecode"` |
| `agent.instance` | string | 具体部署实例的唯一标识，未设置时默认 `"default"` | 格式：`{product}-{identifier}` | `"opencode-example-01"` |

---

### 二、Traces 需要添加的字段

适用于 Langfuse Trace 上报场景。字段写入 Trace 的 `metadata` 中。

| 字段名 | 类型 | Langfuse 位置 | 个人场景 | 业务场景 | 说明 |
|--------|------|--------------|:--------:|:--------:|------|
| `username` | string | `metadata.username` | ✅ 必填 | ✅ 必填 | 通过 `LANGFUSE_ATTR_USERNAME` 环境变量设置 |
| `agent.type` | string | `metadata.agent_type` | ✅ 必填 | ✅ 必填 | Agent 引擎类型 |
| `agent.product` | string | `metadata.agent_product` | 可选 | 可选 | 产品名称，未设置时默认 `"default"` |
| `agent.instance` | string | `metadata.agent_instance` | 可选 | 可选 | 实例标识，未设置时默认 `"default"` |
| `public_key` | string | 隐式（SDK 认证参数） | ✅ 隐式 | ✅ 隐式 | 不写入 metadata，作为 Langfuse PK 传入 |

> **注意**：`public_key` 在 Traces 场景中不需要显式写入属性，它作为 Langfuse SDK 的认证参数隐式存在，平台侧通过 PK 关联实例。

---

### 三、Metrics 需要添加的字段

适用于 OTel Metrics 上报场景。字段作为 Metric 的 attribute label。

| 字段名 | 类型 | OTel 属性名 | 个人场景 | 业务场景 | 说明 |
|--------|------|------------|:--------:|:--------:|------|
| `username` | string | `username` | ✅ 必填 | ✅ 必填 | 用户标识 |
| `agent.type` | string | `agent.type` | ✅ 必填 | ✅ 必填 | Agent 引擎类型 |
| `agent.product` | string | `agent.product` | 可选 | 可选 | 产品名称（替代原 `agent_name`），默认 `"default"` |
| `agent.instance` | string | `agent.instance` | 可选 | 可选 | 实例标识，默认 `"default"` |
| `public_key` | string | `public_key` | — 不适用 | ✅ 必填 | 仅业务场景需要 |

> **基数控制提醒**：`username` 和 `agent.instance` 会增加 Metric 基数。`agent.product` 和 `agent.instance` 未设置时默认为 `"default"`，可根据后端存储能力决定是否显式设置。

---

### 四、Events 需要添加的字段

适用于 OTel Events/Logs 上报场景。字段作为 Event 的 attribute。

| 字段名 | 类型 | OTel 属性名 | 个人场景 | 业务场景 | 说明 |
|--------|------|------------|:--------:|:--------:|------|
| `username` | string | `username` | ✅ 必填 | ✅ 必填 | 用户标识 |
| `agent.type` | string | `agent.type` | ✅ 必填 | ✅ 必填 | Agent 引擎类型 |
| `agent.product` | string | `agent.product` | 可选 | 可选 | 产品名称（替代原 `agent_name`），默认 `"default"` |
| `agent.instance` | string | `agent.instance` | 可选 | 可选 | 实例标识，默认 `"default"` |
| `public_key` | string | `public_key` | — 不适用 | ✅ 必填 | 仅业务场景需要 |

> Events 与 Metrics 的字段要求一致。`agent.product` 和 `agent.instance` 未设置时默认为 `"default"`，推荐尽量填写完整。

---

### 五、必填矩阵速查

| 字段 | Traces（个人） | Traces（业务） | Metrics（个人） | Metrics（业务） | Events（个人） | Events（业务） |
|------|:-------------:|:-------------:|:--------------:|:--------------:|:-------------:|:-------------:|
| `username` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `public_key` | 隐式 | 隐式 | — | ✅ | — | ✅ |
| `agent.type` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `agent.product` | 可选 | 可选 | 可选 | 可选 | 可选 | 可选 |
| `agent.instance` | 可选 | 可选 | 可选 | 可选 | 可选 | 可选 |

---

### 六、与现有规范字段的关系

| 新增字段 | 替代/补充的现有字段 | 关系 |
|---------|-------------------|------|
| `username` | `user.id`（Traces）/ `user.id`（Metrics/Events） | **映射**：`username` 写入上报数据，平台侧映射到 `user.id` |
| `agent.product` | `agent_name`（Metrics/Events） | **替代**：新接入方使用 `agent.product`，`agent_name` 保留兼容 |
| `agent.type` | `agentic.runtime.name`（Traces） | **补充**：语义一致，`agent.type` 更简洁，跨信号统一 |
| `agent.instance` | `service.instance.id`（Traces） | **补充**：对应 OTel 标准实例标识，Metrics/Events 中为新增 |
| `public_key` | — | **新增**：现有规范中无对应字段 |

---

### 七、环境变量配置参考

| 环境变量 | 对应字段 | 适用场景 | 说明 |
|---------|---------|---------|------|
| `LANGFUSE_ATTR_USERNAME` | `username` | Langfuse Trace | 通过 `LANGFUSE_ATTR_*` 机制自动注入到 Trace metadata |
| `AGENT_USERNAME` | `username` | OTel Metrics/Events | 直接配置到上报属性 |
| `CC_LANGFUSE_PUBLIC_KEY` | `public_key` | Langfuse Trace | 作为 Langfuse SDK 认证参数 |
