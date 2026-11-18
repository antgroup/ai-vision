# Dynamic Upload Specification

Dynamic 模块在两个时机上传数据，通过 `session_id` 关联。

## 1. SessionStart

代码位置：`dynamic_handler.rs` → `handle_session_start()`，`init_session()` 调用之后。

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `event` | string | 固定值 `"session_start"` | 事件类型 |
| `session_id` | string | payload.session_id | 会话 ID |
| `started_at` | string | `now_iso8601_pub()` | ISO8601 会话开始时间 |
| `cwd` | string | payload.cwd | 工作目录 |
| `transcript_path` | string | payload.transcript_path | transcript 文件路径 |
| `git_remote_url` | string | `git_remote_url(cwd)` | git 远程仓库地址 |
| `git_branch` | string | `git_branch(cwd)` | 当前分支名 |
| `git_base_commit` | string | `get_base_commit(cwd)` | 基准 commit hash |

## 2. Stop（每轮一次）

代码位置：`dynamic_handler.rs` → `handle_stop()`，有两个上传点：

- **正常 turn**：`update_turn_post_state()` 之后，通过 `read_turn(session_id, current_turn)` 读取
- **合成 turn**（Plan Mode，`current_turn == 0`）：`create_synthetic_turn()` 之后，通过 `read_turn(session_id, 1)` 读取

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `event` | string | 固定值 `"stop"` | 事件类型 |
| `session_id` | string | payload.session_id | 会话 ID |
| `turn_id` | i64 | turn.turn_id | 轮次编号（从 1 开始） |
| `started_at` | string | turn.started_at | turn 开始时间 |
| `ended_at` | string | turn.ended_at | turn 结束时间 |
| `query` | string | turn.query | 用户提问内容 |
| `pre_state` | string\|null | turn.pre_state | turn 开始时的 git diff（相对 base_commit） |
| `post_state` | string\|null | turn.post_state | turn 结束时的 git diff（相对 base_commit） |
| `skip_reason` | string\|null | turn.skip_reason | pre/post 采集失败原因 |
| `synthetic` | bool\|null | turn.synthetic | Plan Mode 合成 turn 标记 |

## 注意事项

- 所有字段必须平铺，不允许嵌套对象
- pre_state / post_state 可能较大（默认上限 1MB，可通过 `CCHOOKS_DYNAMIC_MAX_STATE_SIZE` 环境变量覆盖），上传前经 zstd + base64 压缩后作为 OTel attribute 字符串发送，注意 HTTP body 大小限制
- Stop hook 有超时限制，上传需控制耗时（建议复用 ureq，已有全局 timeout）
- 未经 coordinator 授权的 `stop_hook_active == true` 不触发上传（防无限循环）；匹配 structured-output intent 的最终 Stop 可执行一次上传
- 上传失败不应阻塞 hook 返回，错误记日志即可
- `export_session()` 和本地 `export.json` 已移除，后端可从 session_start + stop 数据自行计算 diff 和 acceptance_rate
