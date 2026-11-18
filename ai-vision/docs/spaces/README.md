# Space Observation Design Docs

## 文档分工

本目录按“总体方案 -> 接口与鉴权 -> 数据模型与迁移 -> 看板配置 -> 实施计划 -> 上游接口协同”拆分，避免单篇文档同时承载过多主题。

本目录集中存放“空间级 Observation 接入”相关设计文档。

## 阅读顺序

### 1. `01-space-observation-integration-mvp.md`
一期总体设计文档，适合先读。

内容包括：
- 背景与问题定义
- 一期目标与范围
- 总体架构
- 核心模型概览
- 空间创建与初始化流程（一期 Django Admin action，二期审批平台）
- 与现有 `ApiKey` / `DataSource` 的关系
- 迁移策略总览
- 跨空间数据关联设计
- 空间生命周期设计

### 2. `02-space-observation-api-and-auth-design.md`
接口与鉴权设计文档。

内容包括：
- 上报 Key / 读取 Key 凭证设计
- 内部控制台接口设计
- 对外兼容 API 设计
- Basic Auth 鉴权链路
- 脱敏策略（轨迹数据脱敏 + Key 脱敏）
- Key 重新生成设计
- 过滤器合并规则
- 权限矩阵（与产品文档 3.3 对齐）
- 安全与错误语义

### 3. `03-space-observation-data-model-and-migration.md`
数据模型与落地迁移文档。

内容包括：
- 新模型字段设计
- 状态机设计
- 凭证生成与存储
- Django 落地建议
- migration 步骤
- Admin 初始化方案
- SQL 草案与示例 SQL

### 4. `04-space-agent-dashboard-config-design.md`
空间内自定义 Agent 看板配置设计文档。

内容包括：
- 空间内自定义 agent 的看板建模
- 自定义 Agent 在 Agent 表中的 `agent_product` 标识约定
- `space + agent` 粒度数据源与面板配置
- 查询优先级
- trace metadata 字段约定
- 建表 SQL 与初始化 SQL

### 5. `05-space-observation-mvp-implementation-plan.md`
一期 MVP 实施计划文档。

内容包括：
- 分阶段实施计划
- 后端模型与 migration 落地步骤
- Django Admin 初始化与绑定方案
- 查询链路迁移范围
- 对外兼容 API 改造范围
- 前端适配范围
- 风险点与验证建议

### 6. `06-public-api-reference.md`
空间观测相关 public API 参考文档。

内容包括：
- 对外查询接口示例
- Langfuse 兼容接口使用方式
- SDK / curl 调用参考

### 7. `07-langfuse-internal-provisioning-api-design.md`
Langfuse 扩展 provisioning 接口协同设计文档（需对应服务实现该扩展）。

内容包括：
- 为什么不直接复用 `/api/public/*`
- `Langfuse` 内部接口设计
- 鉴权方式建议
- project 与 key 的 provisioning 流程
- 幂等、错误语义与 metadata 约定
- AIVision 后端字段映射与落库约定
- 高保用户切换 endpoint 的处理建议

### 8. `08-space-observation-manual-provisioning-sop.md`
内部接口上线前的人工 provisioning 操作手册。

内容包括：
- 业务空间人工初始化的适用范围
- project / key 的命名和用途约定
- AI Vision Admin 与 Langfuse 的手工操作顺序
- 绑定后的核对清单
- 常见失败场景处理建议
- 内部接口上线后的切换方案

### 9. `09-business-space-trace-deeplink-design.md`
业务空间 trace deeplink 设计文档。

内容包括：
- 业务平台跳转到 AIVision trace 详情的授权模型
- 基于空间 READ Key 的 HMAC 协议设计
- AIVision deeplink 验签与 trace 校验链路
- 前后端改造建议
- 上游业务系统接入方式

### 12. `12-space-member-management-design.md`
空间成员管理设计文档。

内容包括：
- SpaceMember 数据模型（字段、角色 choices、软删除）
- 通过邮箱邀请加入团队空间，成员按账号 UUID 管理
- 每个端点的方法、路径、请求体、响应结构、权限校验、错误码
- 角色权限矩阵
- 与操作审计日志的关系（成员变更写 member_change 类日志）

### 13. `13-space-operation-log-design.md`
空间操作审计日志设计文档。

内容包括：
- SpaceOperationLog 数据模型（全字段、5 个 category、不可变约束）
- 写入机制：log_operation 底层函数与 @log_op 装饰器行为
- 当前所有 8 个审计写入点清单表
- 查询接口：分页、category 过滤、keyword 模糊搜索、时间范围过滤
- HIDDEN_CATEGORIES 隐藏类目（trace_desensitize_view）设计
- 响应结构（items/categories/pagination）

### 14. `14-authentication-chain-design.md`
认证与鉴权链路设计文档。

内容包括：
- 邮箱注册、密码登录、HttpOnly Cookie 和 CSRF
- 空间成员角色检查
- SpaceKey、Langfuse Basic 凭证和 Agent 安装授权
- 部署环境及浏览器来源配置

### 15. `15-external-business-space-api-design.md`
外部平台创建业务空间和接入 Agent Product 的 API 设计文档。

内容包括：
- `/api/v2/external/business-spaces` 外部服务间 API 命名空间
- `Authorization: Bearer` 固定 token 一期鉴权方案
- 基于业务空间名前缀（如 `Piston`）的资源范围约束
- 业务空间创建、精确/模糊查询接口
- 空间下 Agent Product 创建、精确/模糊查询接口
- 不创建 `SpaceObservationProject` / observation credentials / 旧 `ApiKey` 的范围约束
- 后续多平台凭证表演进方向

## 历史文档来源

本目录文档由历史文档 `langfuse-space-integration-design.md` 拆分整理而来，原始归档文件已删除。
