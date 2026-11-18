# Space Create With Endpoint Selection

## Background

当前业务空间创建会自动联动 Observation project provisioning。

现状问题：

- 业务空间创建默认依赖唯一的 `ObservationEndpointConfig`
- `organization_id` 配置在 endpoint 的 `config` 中
- 当不同 organization 对应不同 endpoint 时，默认 endpoint 模式无法表达“创建空间时选择目标 endpoint”

## Goal

把“创建业务空间时自动用默认 endpoint”改成“创建业务空间时显式选择 `ObservationEndpointConfig`”。

要求：

- 仅业务空间需要选择 endpoint
- 个人空间不需要 endpoint
- 业务空间、SpaceMember、Observation project、READ/WRITE credentials 仍保持强一致
- Langfuse 的 provision 协议和调用逻辑不改

## Design

### API

`POST /api/v1/spaces/create` 在 `type=2` 时新增必填字段：

```json
{
  "name": "业务空间A",
  "type": 2,
  "description": "optional",
  "observation_endpoint_id": 3
}
```

行为：

- 若 `type=2` 且未传 `observation_endpoint_id`，返回 400
- 若 endpoint 不存在、已禁用或已删除，返回 400
- 若 provision 失败，整个空间创建回滚

### Admin

`UserAdmin.create_business_space` 表单新增 endpoint 下拉框。

行为：

- 提交时必须选择一个可用 endpoint
- 批量为多个用户创建空间时，每个用户单独事务
- 某个用户的 provision 失败时，仅该用户的空间不创建，其他用户不受影响

### Provisioning Service

不改 Langfuse 调用协议，仅复用已有能力：

- `provision_space_observation_project(space, endpoint=selected_endpoint)`

当前服务本来就支持显式传入 endpoint，因此无需适配上游协议。

## Why endpoint, not organization_id

不直接让创建接口传 `organization_id`，原因是：

- 不同 organization 的 endpoint 本身不同
- `base_url`、`tenant_name`、`organization_id` 是同一条 endpoint 配置的一部分
- 只传 `organization_id` 无法确定要访问哪个 endpoint

因此“创建业务空间时选择 endpoint”是更完整、更稳定的建模。

## Scope

本次实现范围：

- `SpaceCreateView` 支持 `observation_endpoint_id`
- admin 创建业务空间支持选择 endpoint
- 相关测试补齐

本次不改：

- 手动绑定 Observation Project
- 初始化 Observation Project 动作
- 前端业务空间创建 UI

