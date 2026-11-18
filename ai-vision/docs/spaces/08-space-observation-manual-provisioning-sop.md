# Space Observation Manual Provisioning SOP

## 1. Purpose

在 `Langfuse` 尚未提供内部 provisioning API 之前，AIVision 业务空间的 Observation 资源需要通过人工方式完成初始化。

本 SOP 用于规范管理员操作流程，确保：

- 业务空间创建路径统一
- Langfuse project 创建位置统一
- 两组 API key 命名和用途统一
- AIVision 绑定信息完整且可追溯

## 2. Scope

适用于以下场景：

- 新建业务空间，但 `Langfuse` 内部 provisioning API 尚未上线
- 历史业务空间尚未绑定 Observation Project，需要补齐

不适用于：

- 个人空间
- 已经完成自动 provisioning 的空间
- 已经绑定过 Observation Project 且需要迁移 endpoint 的空间

## 3. Roles

执行人建议为：

- AIVision Django Admin 管理员
- 有权限操作对应 `Langfuse organization` 的管理员

## 4. Preconditions

开始前请先确认：

1. 默认 `ObservationEndpointConfig` 已创建并可用
2. 已确认本次空间使用的 Langfuse organization
3. 已确认当前空间不是高保独立 endpoint 场景
4. 若是高保独立 endpoint 场景，需先由 Django Admin 选择对应 endpoint，再继续后续操作

## 5. Fixed Conventions

为避免人工操作不一致，先固定以下约定。

### 5.1 Project Placement

- 默认业务空间全部创建在默认 Langfuse organization 下
- 高保用户若需独立 organization，不在页面暴露配置，由 Django Admin 单独处理

### 5.2 Project Name

建议统一命名规则：

```text
aivision-{spaceId}-{normalizedSpaceName}
```

示例：

```text
aivision-12345-agent-ops
```

要求：

- 尽量带上 `spaceId`
- 不要只使用中文空间名
- 不要手工随意缩写

### 5.3 API Key Usage

每个 project 固定创建两组 key：

- `READ`
  - 用于 AIVision 内部读取 traces / observations
- `WRITE`
  - 用于业务空间写入或对外接入

### 5.4 API Key Note

建议在 Langfuse 中创建 key 时统一填写 note：

- `aivision-default-read`
- `aivision-default-write`

## 6. Manual Provisioning Flow

### Step 1. Create Business Space in AIVision Admin

在 AIVision Django Admin 中：

1. 进入用户列表
2. 选择目标用户
3. 执行“创建业务空间”相关 admin action
4. 填写业务空间名称
5. 确认创建成功

执行结果应包括：

- 新建一条 `Space`
- 创建者自动成为该空间管理员

### Step 2. Confirm Space Information

在 AIVision Admin 中记录以下信息：

- `spaceId`
- `spaceName`
- 当前绑定的 `ObservationEndpointConfig`
- 对应 Langfuse organization

建议在执行后立即整理如下记录：

```text
spaceId: 12345
spaceName: Agent Ops
endpoint: default-langfuse
organization: org_xxx
projectName: aivision-12345-agent-ops
```

### Step 3. Create Project in Langfuse

在 `Langfuse` 中：

1. 进入目标 organization
2. 创建 project
3. project 名称严格按约定填写

建议 project metadata 至少包含：

```json
{
  "source": "aivision",
  "spaceId": "12345",
  "spaceName": "Agent Ops"
}
```

如果当前 UI 不支持 metadata，可先忽略，但 project 名称必须按规范执行。

### Step 4. Create Two Project API Keys

在刚创建的 project 下创建两组 key：

1. 创建第一组 key
   - note: `aivision-default-read`
   - 记录 `publicKey`
   - 记录 `secretKey`
2. 创建第二组 key
   - note: `aivision-default-write`
   - 记录 `publicKey`
   - 记录 `secretKey`

注意：

- `secretKey` 只展示一次，必须在创建当下完整保存
- 不要把 READ / WRITE 两组 key 混用
- 不要只截图不落文本

建议临时记录模板：

```text
projectId: proj_xxx
projectName: aivision-12345-agent-ops

READ
publicKey: pk-lf-xxx
secretKey: sk-lf-xxx

WRITE
publicKey: pk-lf-yyy
secretKey: sk-lf-yyy
```

### Step 5. Bind Observation Project in AIVision Admin

回到 AIVision Django Admin：

1. 进入空间列表
2. 选择刚创建的业务空间
3. 执行 `bind_observation_project` admin action
4. 填写以下字段：
   - `upstream_project_id`
   - `upstream_project_name`
   - `read_public_key`
   - `read_secret_key`
   - `write_public_key`
   - `write_secret_key`
5. 提交绑定

执行成功后，AIVision 应生成：

- 一条 `SpaceObservationProject`
- 两条 `SpaceObservationCredential`

## 7. Verification Checklist

绑定完成后，需逐项核对：

1. `SpaceObservationProject` 已创建
2. `upstream_project_id` 正确
3. `upstream_project_name` 正确
4. `provision_status = ACTIVE`
5. 存在一条 `READ` credential
6. 存在一条 `WRITE` credential
7. 两条 credential 的 `public_key` 与 Langfuse 中一致
8. settings / traces 页面能正常读取空间观测信息

## 8. Operational Constraints

### 8.1 Do Not Rebind Existing Project Repeatedly

如果某业务空间已经绑定过 `SpaceObservationProject`：

- 不要再次重复绑定
- 不要手工覆盖 key
- 不要在没有迁移方案的前提下直接换 project

### 8.2 Do Not Migrate Provisioned Space Endpoint Directly

如果空间已经绑定过 Observation Project：

- 一期不建议直接切换 endpoint
- 一期不建议直接切换 organization
- 如确有需要，先走人工迁移评审

### 8.3 High-Security Space Handling

高保用户场景必须先确认：

- 是否需要独立 endpoint
- 是否需要独立 organization

处理原则：

- 不在前端页面暴露配置
- 由 Django Admin 先完成 endpoint 切换
- 确认后再创建 project 和绑定

## 9. Failure Handling

### Case 1. Space Created but Langfuse Project Not Created

处理方式：

- 保留业务空间
- 后续重新执行手工 provisioning
- 不需要删除空间

### Case 2. Project Created but API Key Not Fully Saved

处理方式：

- 废弃已生成但未完整保存的 key
- 重新创建新的 key
- 使用新 key 完成绑定

### Case 3. Project and Keys Created but AIVision Bind Failed

处理方式：

- 先不要删除 Langfuse project
- 检查是否为字段填写错误或 endpoint 选择错误
- 修正后重新执行 `bind_observation_project`

### Case 4. Wrong Project Bound to Space

处理方式：

- 一期不要直接在线修复
- 先记录错误空间、project、keys
- 由管理员评估是否需要人工清理和重新绑定

## 10. Recommended Operation Record

建议每次人工 provisioning 都保留一条操作记录，至少包括：

- 操作时间
- 操作人
- `spaceId`
- `spaceName`
- `endpoint`
- `organization`
- `projectId`
- `projectName`
- `read publicKey`
- `write publicKey`

不建议在长期文档中保存明文 `secretKey`。  
如确需短时间中转，建议在 AIVision 绑定完成后立即清理临时记录。

## 11. Transition Plan After Internal API Goes Live

待 `Langfuse` 内部 provisioning API 上线后：

1. 新建业务空间改为自动 provisioning
2. 当前 SOP 仅保留给异常补偿场景使用
3. `bind_observation_project` admin action 保留作为人工补偿入口
4. 历史手工创建的空间不需要重新初始化，只要绑定数据正确即可

## 12. Quick Checklist

管理员执行时可按以下最小清单操作：

1. 在 AIVision Admin 创建业务空间
2. 记录 `spaceId` 和规范化 `projectName`
3. 在目标 Langfuse organization 下创建 project
4. 创建两组 key：
   - `aivision-default-read`
   - `aivision-default-write`
5. 回 AIVision Admin 执行 `bind_observation_project`
6. 检查 project 和两条 credential 是否生成成功
