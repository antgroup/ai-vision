# OB 配置类数据本地缓存方案

## 背景

当前所有 OceanBase 查询每次请求都直接查库。AgentTypeConfig、DataSource、AgentDashboardPanel 等配置类数据变化频率很低，但每次请求都查库造成不必要的 DB 压力。

## 方案：LocMemCache + Signal + DB Version 轮询

采用 Django 内置 LocMemCache（进程内存缓存），三层失效机制确保数据一致性：

### 1. Signal 主动失效（当前进程，即时）

监听模型 `post_save` / `post_delete` signal，自动清除关联缓存前缀，下次请求重新加载。

适用场景：Admin 保存配置后，处理该请求的 worker 进程立即看到最新数据。

### 2. DB Version 轮询（跨进程，30 秒内）

解决多进程 / 多服务器部署下 LocMemCache 各自独立、signal 无法跨进程的问题。

工作原理：
- `PlatformConfig(platform_code='system').config` 中维护一个 `cache_version` 整数
- Signal handler 在清本地缓存的同时，原子递增 DB 中的 `cache_version`
- 每个进程在 `cached_query()` 被调用时，每 30 秒检查一次 DB version（单行 raw SQL，极轻量）
- 发现 version 变化 → 清空本地全部业务缓存 → 下次查询自动从 DB 重新加载

时序：
```
Admin 改数据
  ├─ 当前 worker: signal → 立即清缓存 + bump cache_version
  └─ 其他 worker: ≤30s 后发现 version 变了 → 清缓存 → 下次请求重新加载
```

### 3. TTL 兜底（全局，可配置）

即使以上机制都未触发，缓存也会在 TTL 到期后自然过期。

TTL 配置位置：`PlatformConfig(platform_code='system').config.cache_ttl`，单位秒。
TTL 配置本身缓存 5 分钟（`_CACHE_TTL_SELF_TIMEOUT`）。

## 缓存范围

| 模型 | 缓存 key 示例 | 失效触发 |
|------|---------------|---------|
| AgentTypeConfig | `agent_types:active`, `agent_type:name:{name}`, `agent_type:product:{product}` | AgentTypeConfig save/delete |
| DataSource | `datasources:agent_type:{id}` | DataSource save/delete |
| AgentDashboardPanel | `panels:agent_type:{id}:page:{page}` | AgentDashboardPanel save/delete |
| UserRemoteMapping | `user_mapping:{user_id}:{agent_type}` | UserRemoteMapping save/delete |

## 不缓存的查询

- Dashboard 聚合查询（Session count/sum/metrics）
- Langfuse 链路/轨迹查询
- Space/Agent 的 CRUD 操作
- 用户认证相关查询

## 配置项

| 配置 | 位置 | 默认值 | 说明 |
|------|------|--------|------|
| `cache_ttl` | PlatformConfig system.config | 86400 (24h) | 缓存 TTL 兜底时间（秒） |
| `cache_version` | PlatformConfig system.config | 0 | 自动维护，signal 触发时 +1 |
| `_VERSION_CHECK_INTERVAL` | cache.py 常量 | 30 | 跨进程 version 轮询间隔（秒） |
| `_CACHE_TTL_SELF_TIMEOUT` | cache.py 常量 | 300 | TTL 配置本身的缓存时间（秒） |

## 生效时间总结

| 场景 | 生效延迟 |
|------|---------|
| Admin 改数据 → 当前进程 | 即时（signal） |
| Admin 改数据 → 其他进程 | ≤30 秒（version 轮询） |
| DB 被直接修改（跳过 ORM） | ≤TTL（需手动 bump cache_version） |

## 涉及文件

- `aivision_backend/settings.py` — CACHES 配置
- `apps/core/cache.py` — cached_query、signal handler、version 轮询
- `apps/core/apps.py` — ready() 中注册 signal + 初始化 version
- `apps/core/views/agent.py` — AgentTypeListView、SpaceAgentsView 加缓存
- `apps/core/views/query.py` — DataQueryView、resolve_username、find_datasource_for_item、Panel 查询加缓存（V2 查询缓存见 `apps/core/views/v2_query.py`）

## 后续扩展

如需分布式缓存，只需将 settings.py 的 BACKEND 换为 `django_redis.cache.RedisCache`，业务代码无需改动。届时 signal 失效自动跨进程生效，version 轮询可移除。
