# 业务空间所有者

Owner 是 `Space.owner` 表示的唯一身份，成员角色仍为 Admin、Developer、Member。创建者默认成为 Owner + Admin；`Space.creator` 永远保留创建历史。

| 操作 | Owner | 普通 Admin |
|---|---|---|
| 邀请、授予 Admin | 可以 | 不可以 |
| 修改、移除其他 Admin | 可以 | 不可以 |
| 管理 Developer / Member | 可以 | 可以 |
| 转让所有权 | 可以 | 不可以 |

Owner 不能被降级、移除或直接退出。通过成员管理中的「转让所有权」选择现有有效成员，新 Owner 自动成为 Admin，原 Owner 保留 Admin。个人空间不开放该功能。

后端同时检查邀请创建、接受、重发和撤销。邀请人失去权限后，旧邀请不能继续授予角色；转让前的 Owner 签发的 Admin 邀请需由新 Owner 撤销后重新邀请。接受旧邀请不会覆盖已有效加入成员的现有角色。角色修改和所有权转让使用同一空间事务锁。

## 升级已有环境

使用部署环境执行 `python manage.py migrate --noinput`。迁移 `0026_space_owner` 仅自动回填创建者仍是有效 Admin 的业务空间。其他有效空间保持待确认状态，成员权限写入返回 `SPACE_OWNER_UNRESOLVED`，读取不受影响。

在 `ai-vision/aivision-py-be/` 下使用同一数据库配置：

```bash
# 只读列出未确认所有者的有效业务空间
.venv/bin/python manage.py assign_space_owner

# 维护者核实归属后显式指定现有有效 Admin，不会重新加入或提升成员
.venv/bin/python manage.py assign_space_owner --space '<space_uuid>' --user '<user_uuid>'
```

归属不明确时不要随机选管理员。命令不能覆盖已存在的其他 Owner；后续交接使用页面或 `POST /api/v1/spaces/{space_id}/owner/transfer`，body 为 `{"target_user_uuid":"..."}`。该维护命令不是新增的平台管理员角色。

部署完成前确认没有待处理空间。回滚旧代码会恢复旧成员权限行为，不能当作无权限影响的回滚；保留 owner 数据并显式评估权限变化。
