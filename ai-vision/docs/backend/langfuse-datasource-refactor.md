# 改造 Trace 查询：从环境变量改为 DataSource 数据库配置

## Context
当前 trace 查询（TraceListView、TraceDetailView、ObservationDetailView）和 TracesQueryAdapter 都硬编码从 `os.environ` 读取单组 Langfuse 凭证。不同 agent 的 trace 存在不同 Langfuse 实例/project，需要按 agent_type 从 `aivision_datasource` 表读取对应的凭证。

## 方案

### 1. 新增辅助函数获取 Langfuse 凭证
在 `apps/core/views/langfuse.py:23` 中新增 `get_langfuse_config(space)` 函数，从 Space 关联的 Agent 自动推断 agent_product，再从 DataSource 表查询凭证：

```python
def get_langfuse_config(space) -> dict | None:
    agent_products = list(
        Agent.objects.filter(space=space, status=1, gmt_deleted__isnull=True)
        .values_list("agent_product", flat=True)
        .distinct()
    )
    if not agent_products:
        return None
    ds = DataSource.objects.select_related("agent_type").filter(
        agent_type__agent_product__in=agent_products,
        datasource_type="traces",
        source_system="langfuse",
        enabled=1,
    ).first()
    if not ds:
        return None
    config = ds.config or {}
    host = config.get("langfuse_host")
    pk = config.get("langfuse_public_key")
    sk = config.get("langfuse_secret_key")
    if all([host, pk, sk]):
        return {"host": host, "public_key": pk, "secret_key": sk}
    return None
```

### 2. 改造 3 个 View
- **TraceListView.get** — 调用 `get_langfuse_config(space)` 获取凭证，替换 `os.environ` 读取
- **TraceDetailView.get** — 同上
- **ObservationDetailView.get** — 同上

前端无需传额外参数，后端自动从 space 的 agents 推断数据源。

### 3. 改造 TracesQueryAdapter
`TracesQueryAdapter` 的类方法 `_get_langfuse_config` 也从 `os.environ` 读凭证，改为从 `context.agent_product` 查 DataSource 表获取。

### 修改文件清单
1. **`apps/core/views/`** 包（含 `langfuse.py`、`share.py`、`observation.py` 等多文件） — 新增 `get_langfuse_config()`（位于 `langfuse.py:23`），改造 TraceListView、TraceDetailView、ObservationDetailView
2. **`apps/core/adapters/traces.py`** — 改造类方法 `_get_langfuse_config()` 从 DataSource 获取凭证

### DataSource 数据格式
`aivision_datasource` 表的 `config` JSON 字段结构：

```json
{
  "langfuse_host": "https://xxx",
  "langfuse_public_key": "pk-xxx",
  "langfuse_secret_key": "sk-xxx",
  "response_format": "langfuse",
  "queries": {
    "fetch_traces": {
      "mode": "simple",
      "value_type": "table",
      "sdk_params": {
        "method": "fetch_traces",
        "param_mapping": {}
      }
    }
  }
}
```

### 验证
- 手动插入一条 DataSource 记录（`datasource_type="traces"`, `source_system="langfuse"`, config 包含凭证）
- 确保 Space 下有对应的 Agent 记录（`agent_product` 与 `AgentTypeConfig.agent_product` 匹配）
- 访问 `/spaces/{space_id}/traces` 验证查询正常
