# 空间成员管理

成员使用账号 UUID 关联空间，列表展示姓名、邮箱和角色。

## 角色与权限

空间 owner 负责所有权；admin 管理配置和成员；developer、viewer 按各自授权访问。服务端以 [空间权限定义](../../aivision-py-be/apps/core/permissions/constants.py) 为准。

## 邀请成员

团队空间管理者通过邮箱创建邀请，可撤销或重新发送。用户接受邀请后加入对应空间。邮箱会统一大小写后匹配账号；新增成员不依赖外部人员目录，也不自动创建可登录账号。

邀请请求使用 `emails` 数组与 `role`，实现见 [invitations.py](../../aivision-py-be/apps/core/views/invitations.py)。邀请链接的有效期、重复接受和权限校验由服务端处理。

## 已有成员

成员列表、角色变更、移除和退出空间见 [space.py](../../aivision-py-be/apps/core/views/space.py)。变更接口用 `user_uuid` 定位成员，校验调用者权限以及 owner 保护；移除成员保留既有业务数据。

审计见 [空间操作日志](13-space-operation-log-design.md)。个人空间由其所属账号访问，不使用团队邀请流程。
