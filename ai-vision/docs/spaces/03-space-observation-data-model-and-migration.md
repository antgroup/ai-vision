# 03. 空间级 Observation 数据模型与迁移设计

## 目录

- [文档范围](#文档范围)
- [1. 核心领域模型](#1-核心领域模型)
- [2. 凭证存储设计](#2-凭证存储设计)
- [3. 状态机设计](#3-状态机设计)
- [4. 迁移策略](#4-迁移策略)
- [5. 推荐代码组织](#5-推荐代码组织)
- [6. 表结构草案](#6-表结构草案)
- [7. 实现约束补充](#7-实现约束补充)

## 文档范围

本文聚焦数据模型、状态设计、代码落地组织、迁移步骤、Admin 初始化与 SQL 草案。

本文不展开 public API 协议兼容与 dashboard 展示策略，这些内容分别放在 02、04 文档中。

## 1. 核心领域模型

### 1.1 ObservationEndpointConfig

表示一个可用的观测存储网关接入点。

建议字段：

- `id`
- `name`
- `base_url`
- `tenant_name`
- `backend_type`（CharField，choices: `langfuse` / `antmonitor`，默认 `langfuse`；migration 0007 加入）
- `is_default`
- `auth_mode`
- `admin_public_key`
- `admin_secret_ciphertext`
- `config`
- `status`
- `gmt_create`
- `gmt_modified`
- `gmt_deleted`

说明：

- `is_default=1` 表示默认共享 endpoint。
- 普通业务空间默认走这张表中的默认记录。
- 高保业务空间可以绑定独立记录。
- `admin_*` 仅服务端使用，用于 project provisioning 或管理接口调用。

### 1.2 SpaceObservationProject

表示一个业务空间与 observation project 的绑定关系。

建议字段：

- `id`
- `space` FK
- `endpoint_config` FK
- `tenant_name`
- `upstream_project_id`
- `upstream_project_name`
- `upstream_project_slug`
- `status`
- `provision_status`
- `last_error`
- `gmt_create`
- `gmt_modified`
- `gmt_deleted`

建议约束：

- `unique_together = ["space"]`

说明：

- 一个业务空间仅绑定一个主用 observation project。
- 即使是默认 endpoint，也要在这里固化 `tenant_name`，避免后续租户迁移时逻辑不清。
- `provision_status` 建议枚举：`PENDING` / `ACTIVE` / `FAILED` / `DISABLED`。

### 1.3 SpaceObservationCredential

表示 AIVision 为业务空间签发的上报 Key / 读取 Key。

建议字段：

- `id`
- `space_observation_project` FK
- `scope`，枚举：`READ`（读取 Key）/ `WRITE`（上报 Key）
- `public_key`
- `secret_hash`
- `secret_ciphertext`
- `name`
- `status`
- `last_used_at`
- `last_used_ip`
- `rotated_at`
- `gmt_create`
- `gmt_modified`
- `gmt_deleted`

建议约束：

- `public_key` 全局唯一
- 同一空间项目下固定一条 `READ`（读取 Key）和一条 `WRITE`（上报 Key），不允许新增额外凭证
- 建议通过 `unique_together = ["space_observation_project", "scope"]` 约束

说明：

- 上报 Key 和读取 Key 均默认脱敏展示
- 管理员可取消脱敏查看完整 Key，成员不可
- `secret_hash` 用于鉴权
- `secret_ciphertext` 用于受控回显和运维恢复
- 支持重新生成（旧 Key 立即失效，历史数据不受影响）

### 1.4 SpaceObservationCredentialAudit（可选）

用于记录敏感操作和认证行为。

建议字段：

- `id`
- `credential` FK
- `action`
- `operator_user` FK
- `ip`
- `user_agent`
- `detail`
- `gmt_create`

建议动作枚举：

- `CREATED`：Key 初始生成
- `REVEALED`：管理员取消脱敏查看
- `REGENERATED`：Key 重新生成
- `AUTH_SUCCESS`：鉴权成功
- `AUTH_FAILED`：鉴权失败

## 2. 凭证存储设计

### 2.1 凭证来源

当前阶段，上报 Key 和读取 Key 的 PK/SK 从 Langfuse 获取（在 Langfuse project 下创建 API Key），而非 AIVision 自行生成随机串。后续去 Langfuse 化时，替换为新组件的凭证获取逻辑。

凭证生成逻辑应通过 `CredentialProvider` 抽象接口封装（详见 02 文档 1.2 节），一期实现 `LangfuseCredentialProvider`。

### 2.2 存储规则

从 Langfuse 获取到 PK/SK 后，按以下规则存储：

- `public_key`：直接存储 Langfuse 返回的 public key
- `secret_hash = sha256(secret_key)`：用于 Basic Auth 鉴权比对
- `secret_ciphertext`：对 secret_key 使用应用密钥或 KMS 加密后的密文，用于受控回显和运维恢复
- Basic Auth 编码格式：`base64(public_key:secret_key)`，与 Langfuse 原生 Basic Auth 一致

### 2.3 重新生成

重新生成 Key 时：

1. 通过 `CredentialProvider.regenerate_credential()` 在 Langfuse 创建新 API Key 并吊销旧 Key
2. 更新 `SpaceObservationCredential` 中的 `public_key`、`secret_hash`、`secret_ciphertext`、`rotated_at`
3. 旧 Key 在 Langfuse 侧和 AIVision 侧同时失效

## 3. 状态机设计

### 3.1 SpaceObservationProject

建议状态：

- `status` 使用 `SmallIntegerField`
- `provision_status` 使用 `CharField` 或 `SmallIntegerField` 均可，但建议优先统一为 `SmallIntegerField` + 常量枚举，减少和现有代码风格偏差

推荐枚举：

- `status`: `0=DISABLED`, `1=ACTIVE`
- `provision_status`: `0=PENDING`, `1=ACTIVE`, `2=FAILED`, `3=DISABLED`

### 3.2 SpaceObservationCredential

建议状态：

- `status` 建议使用 `SmallIntegerField`

推荐枚举：

- `0=DISABLED`
- `1=ACTIVE`

说明：每个空间固定一条 READ + 一条 WRITE，不存在多条凭证轮转的场景。重新生成时直接更新当前记录的 `public_key`、`secret_hash`、`secret_ciphertext` 和 `rotated_at`。

## 4. 迁移策略

### 4.1 第一阶段

- 引入新表结构
- 后台录入默认 endpoint
- 不修改老的个人空间现有逻辑

### 4.2 第二阶段

- 新建业务空间走新链路
- 内部 trace 查询优先读取 `SpaceObservationProject`
- 若老空间尚未补数，可临时回退旧逻辑

### 4.3 第三阶段

- 为老的业务空间补建 `SpaceObservationProject`
- 完成后移除旧的 `get_langfuse_config(space, agent_product)` 主路径
- `DataSource` 不再承担空间级 observation host/pk/sk 职责
- 自定义 agent 的空间级看板配置逐步从通用 `DataSource` 迁移到 `space + agent` 粒度模型

### 4.4 推荐迁移实现方式

查询链路建议按以下优先级实现：

1. 优先读取 `SpaceObservationProject`
2. 若空间未迁移，回退到当前 `get_langfuse_config(space, agent_product)` 逻辑
3. 在全部业务空间完成迁移后，再删除旧逻辑

这样可以保证：

- 前端参数 `agent_product` 暂时还能保留，不会立刻打断现有调用方
- 新老空间可以并存迁移
- Django Admin 可以先录基础配置，再逐步切流

### 4.5 完整迁移步骤

以下步骤按“需要你手动在 Django Admin 配置一部分数据”的前提设计。

#### 步骤 1：新增模型与后台管理

新增以下模型：

- `ObservationEndpointConfig`
- `SpaceObservationProject`
- `SpaceObservationCredential`
- `SpaceObservationCredentialAudit`（可选，一期可先不上）

要求：

- 所有 FK 保持 `db_constraint=False`
- `status` 优先用 `SmallIntegerField`
- 统一补上 `gmt_create/gmt_modified/gmt_deleted`

#### 步骤 2：生成并执行 migration

命令：

```bash
cd aivision-py-be
uv run python manage.py makemigrations
uv run python manage.py migrate
```

#### 步骤 3：注册 Django Admin

在 Django Admin 中至少注册：

- `ObservationEndpointConfig`
- `SpaceObservationProject`
- `SpaceObservationCredential`

推荐在 admin list 页面展示：

- endpoint：`name`, `base_url`, `tenant_name`, `is_default`, `status`
- project：`space`, `endpoint_config`, `upstream_project_id`, `provision_status`, `status`
- credential：`space_observation_project`, `scope`, `public_key`, `name`, `status`, `last_used_at`

#### 步骤 4：手动录入默认 endpoint

你需要在 Django Admin 里先创建一条默认 `ObservationEndpointConfig`：

- `name`: 默认 Observation Gateway
- `base_url`: 上游 Observation 网关地址
- `tenant_name`: 默认租户
- `is_default`: 1
- `status`: 1
- `admin_public_key/admin_secret_ciphertext`: 按实际上游管理凭证填写

若有高保空间，再额外为其录入独立 endpoint。

#### 步骤 5：为新空间接入新链路

一期通过 Django Admin `@admin.action` 实现一键初始化：

1. 在 Admin 界面选择业务空间
2. 执行 action，解析应绑定的 `ObservationEndpointConfig`
3. 创建一条 `SpaceObservationProject(status=1, provision_status=0)`
4. 尝试调用 provisioning 服务
5. 成功则回填 `upstream_project_id/name/slug`
6. 生成一份读取 Key（READ）和一份上报 Key（WRITE）的 `SpaceObservationCredential`
7. 失败则更新 `provision_status=FAILED` 和 `last_error`

这里不要求因为 provisioning 失败而回滚空间创建。二期接入内部审批平台后，改为自助创建 -> 审批 -> 自动初始化。

#### 步骤 6：改查询链路为“新表优先，旧逻辑兜底”

建议新增一个统一入口，例如：

- `get_space_observation_runtime_config(space, agent_product=None)`

查询逻辑：

1. 如果空间存在 `SpaceObservationProject` 且状态正常，直接返回其 endpoint/project 配置
2. 否则回退到旧的 `get_langfuse_config(space, agent_product)`

这样可以兼容当前前后端仍在传的 `agent_product` 参数。

#### 步骤 7：手动迁移老空间

你可以分批在 Django Admin 中为老的业务空间创建 `SpaceObservationProject`：

1. 选择一个空间
2. 绑定 endpoint
3. 填入或补建其 observation project 信息
4. 为该空间生成一份读取 Key（READ）和一份上报 Key（WRITE）
5. 将 `provision_status` 置为 `ACTIVE`

如果你希望批量处理，后续也可以补一个 management command，但一期并不强制。

#### 步骤 8：验证新老空间都能正常查询

至少验证：

- 未迁移老空间仍可按旧逻辑查 traces
- 新迁移空间可按新逻辑查 traces / trace detail / observation detail
- Django Admin 中能看到 project 与 credential 状态
- 外部 Basic Auth 可直接查询详情接口

#### 步骤 9：完成切流后清理旧路径

当所有业务空间都迁移完成后：

1. 移除 `get_langfuse_config(space, agent_product)` 主路径
2. 移除 `DataSource.config` 中承载空间级 host/pk/sk 的职责
3. 评估是否保留 `agent_product` 参数，仅作为前端兼容字段

## 5. 推荐代码组织

建议新增文件与职责：

- `apps/core/models/`（包结构，含 `observation.py`、`agent.py`、`space.py` 等）
  - `observation.py` 中包含 `ObservationEndpointConfig`、`SpaceObservationProject`、`SpaceObservationCredential`、`SpaceDatasourceBinding`
  - 可选新增 `SpaceObservationCredentialAudit`

- `apps/core/admin.py`
  - 注册上述新模型
  - 配置便于人工录入和排查的 list_display / search_fields / list_filter

- `apps/core/authentication.py`
  - 新增 `ObservationGatewayAuthentication`

- `apps/core/services/space_observation_provisioning.py`
  - 创建 project
  - 失败重试

- `apps/core/views/observation.py`
  - `/api/public/*` 接口
  - 观测数据代理逻辑（traces 列表代理、trace detail 代理、observation detail 代理、错误映射）内联在此文件中，未单独实现为 `services/observation_proxy.py`

- `apps/core/services/credential_provider.py`
  - 定义 `CredentialProvider` 抽象基类和 `CredentialPair` 数据类
  - 一期实现 `LangfuseCredentialProvider`（从 Langfuse 创建/吊销/重新生成 API Key）
  - 后续替换底层组件时新增其他 Provider 实现

- `apps/core/services/credential_service.py`
  - 调用 `CredentialProvider` 获取 PK/SK
  - hash 比对（Basic Auth 鉴权）
  - 加密解密（secret_ciphertext 受控回显）
  - 重新生成（协调 Provider 吊销旧 Key + 创建新 Key + 更新本地记录）

- `apps/core/serializers.py`
  - 空间设置与凭证展示 serializer

- `aivision_backend/urls.py`
  - 注册 `/api/public/*` 路由

## 6. 表结构草案

以下为示意字段，不是最终 migration 代码。

### 6.1 aivision_observation_endpoint_config

```sql
CREATE TABLE aivision_observation_endpoint_config (
  id BIGINT PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  base_url VARCHAR(512) NOT NULL,
  tenant_name VARCHAR(128) NOT NULL,
  is_default SMALLINT NOT NULL DEFAULT 0,
  auth_mode VARCHAR(32) NULL,
  admin_public_key VARCHAR(256) NULL,
  admin_secret_ciphertext TEXT NULL,
  config JSON NULL,
  status SMALLINT NOT NULL DEFAULT 1,
  gmt_create DATETIME NOT NULL,
  gmt_modified DATETIME NOT NULL,
  gmt_deleted DATETIME NULL
);
```

### 6.2 aivision_space_observation_project

```sql
CREATE TABLE aivision_space_observation_project (
  id BIGINT PRIMARY KEY,
  space_id BIGINT NOT NULL,
  endpoint_config_id BIGINT NOT NULL,
  tenant_name VARCHAR(128) NOT NULL,
  upstream_project_id VARCHAR(128) NULL,
  upstream_project_name VARCHAR(256) NULL,
  upstream_project_slug VARCHAR(128) NULL,
  status SMALLINT NOT NULL DEFAULT 1,
  provision_status SMALLINT NOT NULL DEFAULT 0,
  last_error TEXT NULL,
  gmt_create DATETIME NOT NULL,
  gmt_modified DATETIME NOT NULL,
  gmt_deleted DATETIME NULL
);
```

### 6.3 aivision_space_observation_credential

```sql
CREATE TABLE aivision_space_observation_credential (
  id BIGINT PRIMARY KEY,
  space_observation_project_id BIGINT NOT NULL,
  scope VARCHAR(16) NOT NULL,
  public_key VARCHAR(256) NOT NULL UNIQUE,
  secret_hash VARCHAR(128) NOT NULL,
  secret_ciphertext TEXT NOT NULL,
  name VARCHAR(128) NULL,
  status SMALLINT NOT NULL DEFAULT 1,
  last_used_at DATETIME NULL,
  last_used_ip VARCHAR(64) NULL,
  rotated_at DATETIME NULL,
  gmt_create DATETIME NOT NULL,
  gmt_modified DATETIME NOT NULL,
  gmt_deleted DATETIME NULL
);
```

### 6.4 可直接在 OB 执行的建表 SQL

以下 SQL 为一期推荐版本，按“只迁业务空间，不迁个人空间”的策略设计；如果你的 OB 版本对 `CURRENT_TIMESTAMP`、`AUTO_INCREMENT` 或字符集有特殊要求，再按实例规范微调。

```sql
CREATE TABLE IF NOT EXISTS aivision_observation_endpoint_config (
  id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  base_url VARCHAR(512) NOT NULL,
  tenant_name VARCHAR(128) NOT NULL,
  is_default SMALLINT NOT NULL DEFAULT 0,
  auth_mode VARCHAR(32) DEFAULT NULL,
  admin_public_key VARCHAR(256) DEFAULT NULL,
  admin_secret_ciphertext TEXT DEFAULT NULL,
  config JSON DEFAULT NULL,
  status SMALLINT NOT NULL DEFAULT 1,
  gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  gmt_deleted DATETIME DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_obs_endpoint_status (status),
  KEY idx_obs_endpoint_default (is_default, status)
);

CREATE TABLE IF NOT EXISTS aivision_space_observation_project (
  id BIGINT NOT NULL AUTO_INCREMENT,
  space_id BIGINT NOT NULL,
  endpoint_config_id BIGINT NOT NULL,
  tenant_name VARCHAR(128) NOT NULL,
  upstream_project_id VARCHAR(128) DEFAULT NULL,
  upstream_project_name VARCHAR(256) DEFAULT NULL,
  upstream_project_slug VARCHAR(128) DEFAULT NULL,
  status SMALLINT NOT NULL DEFAULT 1,
  provision_status SMALLINT NOT NULL DEFAULT 0,
  last_error TEXT DEFAULT NULL,
  gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  gmt_deleted DATETIME DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_space_observation_project_space (space_id),
  KEY idx_space_observation_project_endpoint (endpoint_config_id),
  KEY idx_space_observation_project_status (status, provision_status)
);

CREATE TABLE IF NOT EXISTS aivision_space_observation_credential (
  id BIGINT NOT NULL AUTO_INCREMENT,
  space_observation_project_id BIGINT NOT NULL,
  scope VARCHAR(16) NOT NULL,
  public_key VARCHAR(256) NOT NULL,
  secret_hash VARCHAR(128) NOT NULL,
  secret_ciphertext TEXT NOT NULL,
  name VARCHAR(128) DEFAULT NULL,
  status SMALLINT NOT NULL DEFAULT 1,
  last_used_at DATETIME DEFAULT NULL,
  last_used_ip VARCHAR(64) DEFAULT NULL,
  rotated_at DATETIME DEFAULT NULL,
  gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  gmt_deleted DATETIME DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_space_observation_credential_pk (public_key),
  UNIQUE KEY uk_space_observation_credential_scope (space_observation_project_id, scope),
  KEY idx_space_observation_credential_status (status)
);
```

### 6.5 Django Admin 初始化 SQL

默认 endpoint 可以先手工插一条：

```sql
INSERT INTO aivision_observation_endpoint_config
  (name, base_url, tenant_name, is_default, auth_mode, admin_public_key, admin_secret_ciphertext, status)
VALUES
  ('default-observation-endpoint', 'https://your-observation-gateway.example.com', 'default', 1, 'BASIC', 'your-admin-pk', 'your-admin-sk-ciphertext', 1);
```

如果后续需要高保独立 endpoint，再额外插入新记录即可。

### 6.6 业务空间初始化 project / credential 示例 SQL

以下 SQL 适用于“空间表里已经有业务空间记录，需要手工补 observation project 和 credential”的场景。

#### 1. 先查看待迁移的业务空间

```sql
SELECT id, uuid, name, type, status
FROM aivision_space
WHERE type = 2
  AND status = 1
  AND gmt_deleted IS NULL;
```

#### 2. 为某个业务空间插入 project 绑定

说明：

- `space_id` 替换为目标业务空间的 `id`
- `endpoint_config_id` 替换为你已创建好的默认或独立 endpoint 主键
- `tenant_name`、`upstream_project_*` 按实际上游信息填写

```sql
INSERT INTO aivision_space_observation_project
  (space_id, endpoint_config_id, tenant_name, upstream_project_id, upstream_project_name, upstream_project_slug, status, provision_status)
VALUES
  (12345, 1, 'default', 'proj_xxx', 'space-demo-project', 'space-demo-project', 1, 1);
```

#### 3. 为该业务空间生成 READ / WRITE 两条凭证

说明：

- `space_observation_project_id` 替换为上一步新插入 project 记录的主键
- `public_key` 填对外暴露的 key
- `secret_hash` 填原始 secret 的 sha256
- `secret_ciphertext` 填你们保存的密文

```sql
INSERT INTO aivision_space_observation_credential
  (space_observation_project_id, scope, public_key, secret_hash, secret_ciphertext, name, status)
VALUES
  (10001, 'READ',  'pk-aiv-r-xxxxx', 'sha256-of-read-secret',  'ciphertext-of-read-secret',  'default-read',  1),
  (10001, 'WRITE', 'pk-aiv-w-xxxxx', 'sha256-of-write-secret', 'ciphertext-of-write-secret', 'default-write', 1);
```

#### 4. 批量筛出“还没迁移 project”的业务空间

```sql
SELECT s.id, s.uuid, s.name
FROM aivision_space s
LEFT JOIN aivision_space_observation_project p
  ON p.space_id = s.id
 AND p.gmt_deleted IS NULL
WHERE s.type = 2
  AND s.status = 1
  AND s.gmt_deleted IS NULL
  AND p.id IS NULL;
```

#### 5. 校验某个业务空间是否已经补齐

```sql
SELECT
  s.id AS space_id,
  s.name AS space_name,
  p.id AS project_bind_id,
  p.upstream_project_id,
  p.provision_status,
  c.scope,
  c.public_key,
  c.status AS credential_status
FROM aivision_space s
LEFT JOIN aivision_space_observation_project p
  ON p.space_id = s.id
 AND p.gmt_deleted IS NULL
LEFT JOIN aivision_space_observation_credential c
  ON c.space_observation_project_id = p.id
 AND c.gmt_deleted IS NULL
WHERE s.id = 12345
ORDER BY c.scope;
```

## 7. 实现约束补充

### 7.1 新旧运行时配置解析

建议统一收敛到一个运行时配置解析入口，例如：

- `get_space_observation_runtime_config(space, agent_product=None)`

建议语义：

1. 优先从 `SpaceObservationProject` 解析 endpoint / project / upstream auth 信息
2. 当空间尚未迁移时，再回退到旧的 `get_langfuse_config(space, agent_product)`
3. 对调用方屏蔽“新表 / 旧表”差异，避免在多个 view 中重复分支判断

### 7.2 软删除与唯一约束

由于当前项目广泛使用 `gmt_deleted`，实现时需要明确以下约束：

- `SpaceObservationProject` 建议按“一个 space 只维护一条主绑定记录”的方式实现，优先更新状态，不建议通过软删后重建来规避唯一约束
- `SpaceObservationCredential.public_key` 保持全局唯一，避免历史凭证和新凭证冲突
- 若未来确实需要保留多版本历史 project 绑定，再单独评估唯一索引与归档策略

### 7.3 一期默认实现建议

一期通过 Django Admin `@admin.action` 一键初始化，采用”同步尝试 provisioning + 失败不回滚空间创建”的模式：

- 在 Admin 界面选择空间后执行初始化 action
- 初始化成功则直接回填 project 信息并生成一份读取 Key 和一份上报 Key
- 初始化失败则保留空间，记录 `provision_status=FAILED` 与 `last_error`
- 后续通过后台补偿或人工重试恢复

二期接入内部审批平台后，改为用户自助创建 -> 超管审批 -> 自动初始化的产品化流程。

