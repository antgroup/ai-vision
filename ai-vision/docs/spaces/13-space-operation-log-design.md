# 13. 空间操作审计日志设计

本文档基于代码实际实现描述空间操作审计日志的数据模型、写入机制、审计点清单与查询接口。

> 代码基线：`aivision-py-be/apps/core/`

---

## 1. 数据模型：SpaceOperationLog

**文件**: `models/operation_log.py:1-49`

```python
class SpaceOperationLog(models.Model):
    """空间操作审计日志（仅记录成功操作，写入后不可更新/删除）"""
```

### 1.1 Category 定义

| 常量名 | 值 | 中文标签 |
|--------|-----|----------|
| `CATEGORY_MEMBER_CHANGE` | `"member_change"` | 成员变更 |
| `CATEGORY_SPACE_INFO_CHANGE` | `"space_info_change"` | 空间信息变更 |
| `CATEGORY_KEY_OPERATION` | `"key_operation"` | Key 操作 |
| `CATEGORY_TRACE_DESENSITIZE_VIEW` | `"trace_desensitize_view"` | 轨迹脱敏查看 |
| `CATEGORY_AGENT_ACCESS` | `"agent_access"` | Agent 接入 |

### 1.2 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | AutoField (PK) | 主键 |
| `space` | ForeignKey → Space | 所属空间（`db_constraint=False`，`related_name="operation_logs"`） |
| `operator` | ForeignKey → User | 操作人（`db_constraint=False`，`related_name="operation_logs"`） |
| `category` | CharField(32) | 操作类目（见上表，`choices=CATEGORY_CHOICES`） |
| `action` | CharField(64) | 具体操作动作 |
| `target_display` | CharField(256) | 操作对象显示名（如成员姓名+用户标识），默认空串 |
| `detail` | CharField(512) | 操作详情描述，默认空串 |
| `extra` | JSONField (nullable) | 扩展 JSON 数据 |
| `gmt_create` | DateTimeField | 创建时间（`auto_now_add`） |

### 1.3 Meta 约束

- **表名**: `aivision_space_operation_log`（`Meta.db_table`）
- **排序**: `["-gmt_create"]`
- **设计约束**: 仅记录成功操作，写入后不可更新/删除（docstring 级声明，无 `gmt_modified`/`gmt_deleted` 字段）

---

## 2. 写入机制

**文件**: `services/operation_log_service.py:1-156`

### 2.1 底层写入函数 `log_operation`

**签名**（`services/operation_log_service.py:28-37`）:

```python
def log_operation(
    *,
    space,
    operator,
    category: str,
    action: str,
    target_display: str = "",
    detail: str = "",
    extra: dict | None = None,
) -> SpaceOperationLog | None:
```

**行为**:
- 若 `space` 为 None 或 `operator` 未认证 → 跳过写入，返回 `None`
- 调用 `SpaceOperationLog.objects.create(...)` 落库
- **写入失败不抛异常**：捕获所有 Exception，仅 `logger.warning` 记录，返回 `None`

### 2.2 View 方法装饰器 `@log_op`

**签名**（`services/operation_log_service.py:97-101`）:

```python
def log_op(
    *,
    category: str,
    action: str,
    space_id_kwarg: str = "space_id",
):
```

**行为**（`services/operation_log_service.py:112-155`）:

1. 执行被装饰的 view 方法，获得 `response`
2. 判断是否为成功响应（`_is_success_response`）：
   - HTTP status < 400 **且** `response.data["code"] == 0`
   - 若不成功 → 直接返回 response，**不写入日志**
3. 从 URL kwargs 中取 `space_id`，查询对应 Space
4. 从 `request.op_log` 属性读取动态字段，支持三种形式：
   - `dict`：写入一条日志
   - `list[dict]`：每个元素写入一条日志（用于批量场景）
   - 未设置 / `None`：仅以 category+action 写入一条空内容日志
5. 对每个 payload 调用 `log_operation` 写入
6. **装饰器失败不影响主流程**：外层 try-except 捕获所有异常，仅 `logger.warning`
7. 始终返回原始 response（不修改）

**关键设计点**:
- 只在**业务成功**时才记录审计日志（失败操作不记录）
- 日志写入是**旁路逻辑**，不阻塞主业务
- 未认证用户或无法解析空间时静默跳过

---

## 3. 当前所有审计点清单

| # | category | action | 触发位置 | 说明 |
|---|----------|--------|----------|------|
| 1 | `agent_access` | `create_agent` | `views/agent.py:268-270` | 新增 Agent 接入 |
| 2 | `key_operation` | `reveal_key` | `views/credential.py:27-29` | 查看/复制明文 Key |
| 3 | `key_operation` | `regenerate_key` | `views/credential.py:72-74` | 重新生成 Key |
| 4 | `space_info_change` | `update_space_name` | `views/space.py:280-283` | 修改空间名称 |
| 5 | `member_change` | `add_member` | `views/space.py:342-344` | 批量添加成员 |
| 6 | `member_change` | `change_role` | `views/space.py:572-574` | 修改成员角色 |
| 7 | `member_change` | `remove_member` | `views/space.py:653-655` | 移除成员 |
| 8 | `member_change` | `member_exit` | `views/space.py:717-719` | 成员主动退出 |

### 各审计点 op_log 详情

| action | target_display | detail 示例 |
|--------|---------------|-------------|
| `create_agent` | Agent display_name（如 "Claude Code"） | `"新增 Agent 接入"` |
| `reveal_key` | Key scope 标签（`"上报 Key"` / `"读取 Key"`） | `"复制 Key"` 或 `"查看明文"` |
| `regenerate_key` | Key scope 标签 | `"重新生成 Key"` |
| `update_space_name` | 新空间名 | `"将空间名称从「旧名」修改为「新名」"` |
| `add_member` | `"张三（123456）"` | `"添加为「开发者」"`（批量，每成功一人一条） |
| `change_role` | `"张三（123456）"` | `"将角色从「管理员」调整为「开发者」"` |
| `remove_member` | `"张三（123456）"` | `"移除成员"` |
| `member_exit` | `"张三（123456）"` | `"主动退出空间"` |

> **注意（未实现）**: `trace_desensitize_view`（轨迹脱敏查看）这个 category 在模型中定义（`models/operation_log.py:13,20`），并在查询接口中被列入 `HIDDEN_CATEGORIES` 隐藏（`views/operation_log.py:21`），但**当前代码中没有任何写入点**——全仓库 `apps/` 范围内既无 `@log_op` 装饰器、也无 `log_operation()` 调用写入该 category。即该审计类目已预留但尚未启用，目前不会产生任何此类日志。

---

## 4. 查询接口

**文件**: `views/operation_log.py:1-108`

| 属性 | 值 |
|------|----|
| **方法** | `GET` |
| **路径** | `/api/v1/spaces/{space_id}/operation-logs` |
| **View** | `SpaceOperationLogListView`（`views/operation_log.py:47`） |
| **URL name** | `space_operation_logs`（`urls.py:462`） |

### 4.1 权限

`check_space_access_or_error`（`views/operation_log.py:56`）— 需为该空间的活跃成员，任意角色均可查看。

### 4.2 查询参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `category` | string | 筛选类目。传 `"all"` 或不传则不过滤。必须在合法类目集合内（不含隐藏类目） |
| `keyword` | string | 模糊搜索，匹配 `target_display` 或 `detail`（`icontains`），截断至 100 字符 |
| `from` | ISO datetime string | 起始时间过滤（`gmt_create >= from`） |
| `to` | ISO datetime string | 结束时间过滤（`gmt_create <= to`） |
| `page` | int | 页码（标准分页） |
| `pageSize` | int | 每页条数，默认 20，最大 100 |

### 4.3 HIDDEN_CATEGORIES（隐藏类目）

**文件**: `views/operation_log.py:21`

```python
HIDDEN_CATEGORIES = {SpaceOperationLog.CATEGORY_TRACE_DESENSITIZE_VIEW}
```

`trace_desensitize_view` 这个 category 会被记录到数据库中，但在列表查询接口中：
1. 不出现在返回的 `categories` 选项列表中（`views/operation_log.py:23-27`）
2. 不允许作为 `category` 过滤参数传入（不在 `_VALID_CATEGORIES` 中，`views/operation_log.py:28-30`）
3. 当不传 category 或传 `"all"` 时，**并未**从查询结果中排除（即隐藏类目的日志仍会出现在全量列表中）

> 这意味着 `trace_desensitize_view` 日志对前端用户来说：可以在全量列表中看到，但不能按此类目单独筛选，前端也不会在类目下拉中展示此选项。

### 4.4 响应结构

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": 1,
        "time": "2024-12-01 14:30:00",
        "operator_name": "张三",
        "operator_work_id": "123456",
        "operator_avatar": "https://...",
        "category": "member_change",
        "category_display": "成员变更",
        "action": "add_member",
        "target_display": "李四（654321）",
        "detail": "添加为「开发者」"
      }
    ],
    "categories": [
      {"value": "member_change", "label": "成员变更"},
      {"value": "space_info_change", "label": "空间信息变更"},
      {"value": "key_operation", "label": "Key 操作"},
      {"value": "agent_access", "label": "Agent 接入"}
    ],
    "pagination": {
      "page": 1,
      "pageSize": 20,
      "total": 42,
      "totalPages": 3
    }
  }
}
```

- `items`：日志列表，按 `gmt_create` 降序 + `id` 降序（`views/operation_log.py:63`）
- `categories`：前端可选的类目选项（不含隐藏类目）
- `pagination`：标准分页信息
- `time`：本地时间格式 `YYYY-MM-DD HH:MM:SS`（`serializers.py:206-209`）

### 4.5 序列化器

**文件**: `serializers.py:180-209`

```python
class SpaceOperationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpaceOperationLog
        fields = [
            "id", "time", "operator_name", "operator_work_id",
            "operator_avatar", "category", "category_display",
            "action", "target_display", "detail",
        ]
```

额外字段：
- `operator_name`：来自 `operator.name`
- `operator_work_id`：来自 `operator.work_id`
- `operator_avatar`：来自 `operator.avatar_url`
- `category_display`：Django 的 `get_category_display()` 方法输出中文标签
- `time`：`gmt_create` 转本地时间后格式化为字符串

### 4.6 错误码

| code | 含义 |
|------|------|
| 3001 | 空间不存在（Space not found） |
| 3002 | 无权访问该空间 |
| 4002 | 非法 category 值 |

---

## 5. 设计要点总结

1. **仅记录成功操作**: `@log_op` 装饰器在 `_is_success_response` 判定为真时才写入（HTTP < 400 且 code == 0）
2. **写入后不可变**: 模型无 `gmt_modified`/`gmt_deleted` 字段，代码中不存在 update/delete 操作
3. **旁路不阻塞**: 写入失败仅 warning 日志，不影响业务响应
4. **隐藏类目机制**: `trace_desensitize_view` 被记录但在查询接口的类目选项中隐藏，前端无法按此类目筛选
5. **批量写入支持**: `request.op_log` 可传 list，装饰器逐条调用 `log_operation`（如批量添加成员场景）
6. **动态字段传递**: View 方法通过设置 `request.op_log = {...}` 传递 `target_display`/`detail`/`extra`，装饰器在方法返回后读取
