# 04. 空间内自定义 Agent 看板配置设计

> **状态：设计提案，截至当前（2026-06）尚未实现，代码中无对应表/模型（`aivision_space_agent_datasource`、`aivision_space_agent_dashboard_config` 均未建表）。**

## 目录

- [文档范围](#文档范围)
- [1. 看板配置策略](#1-看板配置策略)
- [2. 推荐建模方式](#2-推荐建模方式)
- [3. 查询优先级建议](#3-查询优先级建议)
- [4. 自定义 Agent 在 Agent 表中的标识](#4-自定义-agent-在-agent-表中的标识)
- [5. 自定义接入 agent 的 trace 字段约定](#5-自定义接入-agent-的-trace-字段约定)
- [6. 自定义 Agent 看板配置建表 SQL](#6-自定义-agent-看板配置建表-sql)
- [7. 自定义 Agent 看板初始化 SQL](#7-自定义-agent-看板初始化-sql)
- [8. 看板兼容策略落地建议](#8-看板兼容策略落地建议)
- [9. 与 Observation Project 的边界](#9-与-observation-project-的边界)

## 文档范围

本文只讨论空间内自定义 Agent 的看板配置建模，以及与现有 `agent_product` 级配置的兼容策略。

本文不负责定义 observation project、网关凭证、对外兼容 API 和外部鉴权。

## 1. 看板配置策略

看板配置需要同时兼容“个人空间的 agent 市场场景”和“业务空间的自定义接入场景”，一期建议明确区分如下：

1. 个人空间接入 agent 市场内置 agent
   - 继续按 `agent_product` 维度配置和渲染数据看板
   - 复用现有 `AgentTypeConfig + DataSource + dashboard panels` 模式

2. 业务空间
   - 一期只允许接入自定义 agent
   - 不走 agent 市场内置 agent 的看板配置方式

3. 个人空间 / 业务空间接入自定义 agent
   - 一律按 `space + agent` 粒度配置数据看板
   - 同一个自定义 agent 类型，在不同空间下可以有不同的数据源和面板配置

结论：

- 内置 agent 看板：按 `agent_product`
- 自定义 agent 看板：按 `space + agent`
- 业务空间一期不开放 agent 市场内置 agent 接入

## 2. 推荐建模方式

为避免把“空间级 observation project”与“看板配置”硬耦合，一期建议：

1. 保留现有 `AgentTypeConfig`、`DataSource`、dashboard panel 配置作为“内置 agent 看板配置层”
2. 对自定义 agent 增加空间粒度的看板配置模型，建议新增：
   - `SpaceAgentDashboardConfig`
   - `SpaceAgentDataSource`

建议字段方向：

- `space`
- `agent`
- `panel_config`
- `datasource_type`
- `source_system`
- `config`
- `status`
- `gmt_create/gmt_modified/gmt_deleted`

这样可以把“空间观测项目绑定”和“空间内某个自定义 agent 的看板配置”拆开管理。

## 3. 查询优先级建议

看板查询建议按以下优先级解析：

1. 若当前 agent 是内置 agent，且空间为个人空间，优先走 `agent_product` 级配置
2. 若当前 agent 是自定义 agent，优先走 `space + agent` 级配置
3. 若业务空间请求了内置 agent 看板，一期直接返回不支持或不展示

这样可以保证：

- 个人空间的 agent 市场场景不受本次改造影响
- 业务空间的数据看板和 observation project 解耦
- 自定义 agent 的看板配置可以做到真正的空间隔离

## 4. 自定义 Agent 在 Agent 表中的标识

自定义接入的 agent 在 `aivision_agent` 表中的 `agent_product` 字段值约定：

- 内置 agent（agent 市场）：使用预定义的枚举值，如 `claude_code`、`homiclaw` 等
- 自定义 agent：统一使用 `custom` 作为 `agent_product` 值
- 自定义 agent 的实际名称通过 `custom_name` 字段存储
- 同一空间下可接入多个自定义 agent，通过 `custom_name` 区分

查询时的识别逻辑：

1. `agent_product = 'custom'` 表示自定义 agent，走 `space + agent` 粒度看板配置
2. `agent_product != 'custom'` 表示内置 agent，走 `agent_product` 级看板配置

## 5. 自定义接入 agent 的 trace 字段约定

自定义接入的 agent trace 信息，一期明确约定读取以下 metadata 字段：

- `attributes.gen_ai.agent.name`
- `attributes.gen_ai.agent.id`

建议用途：

1. `attributes.gen_ai.agent.id`
   - 作为自定义 agent 的稳定标识
   - 优先用于 trace 与空间内 agent 实例的归属匹配

2. `attributes.gen_ai.agent.name`
   - 作为展示名或缺省名称来源
   - 用于看板、trace 列表、详情页展示

建议映射规则：

- 若同时存在 `id` 和 `name`，以 `id` 做识别、以 `name` 做展示
- 若只有 `name` 没有 `id`，可临时按 `name` 展示，但不建议作为长期唯一键
- 业务空间下的自定义 agent 面板配置，推荐最终绑定到平台内的 `agent.id`，而不是直接绑定 trace 中的 `name`

## 6. 自定义 Agent 看板配置建表 SQL

一期如果要把“自定义 agent 看板”从通用 `DataSource` 中拆出来，建议至少新增两张表：

- `aivision_space_agent_datasource`
- `aivision_space_agent_dashboard_config`

设计目标：

- 只服务“自定义 agent”
- 配置粒度固定为 `space + agent`
- 与 `aivision_space_observation_project` 解耦

### 6.1 aivision_space_agent_datasource

```sql
CREATE TABLE IF NOT EXISTS aivision_space_agent_datasource (
  id BIGINT NOT NULL AUTO_INCREMENT,
  space_id BIGINT NOT NULL,
  agent_id BIGINT NOT NULL,
  datasource_type VARCHAR(32) NOT NULL,
  source_system VARCHAR(32) NOT NULL,
  config JSON DEFAULT NULL,
  status SMALLINT NOT NULL DEFAULT 1,
  gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  gmt_deleted DATETIME DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_space_agent_datasource (space_id, agent_id, datasource_type),
  KEY idx_space_agent_datasource_agent (agent_id),
  KEY idx_space_agent_datasource_status (status)
);
```

字段说明：

- `space_id`：空间主键
- `agent_id`：平台内 `Agent` 表主键
- `datasource_type`：建议与现有保持一致，如 `metrics` / `traces` / `logs`
- `source_system`：建议保留，如 `langfuse` / `observation_gateway` / `custom`
- `config`：该空间下该 agent 的数据源明细配置

### 6.2 aivision_space_agent_dashboard_config

```sql
CREATE TABLE IF NOT EXISTS aivision_space_agent_dashboard_config (
  id BIGINT NOT NULL AUTO_INCREMENT,
  space_id BIGINT NOT NULL,
  agent_id BIGINT NOT NULL,
  panel_key VARCHAR(128) NOT NULL,
  panel_name VARCHAR(128) NOT NULL,
  panel_type VARCHAR(64) NOT NULL,
  panel_config JSON DEFAULT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  status SMALLINT NOT NULL DEFAULT 1,
  gmt_create DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  gmt_deleted DATETIME DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_space_agent_dashboard_panel (space_id, agent_id, panel_key),
  KEY idx_space_agent_dashboard_agent (agent_id),
  KEY idx_space_agent_dashboard_status (status, sort_order)
);
```

字段说明：

- `panel_key`：面板唯一标识，如 `trace_overview`、`token_trend`
- `panel_name`：展示名
- `panel_type`：面板类型，如 `line` / `table` / `stat`
- `panel_config`：面板查询、过滤条件、字段映射、样式配置

## 7. 自定义 Agent 看板初始化 SQL

### 7.1 查看某个空间下的自定义 Agent

```sql
SELECT id, space_id, agent_product, name, custom_name, status
FROM aivision_agent
WHERE space_id = 12345
  AND status = 1
  AND gmt_deleted IS NULL;
```

### 7.2 给某个自定义 Agent 插入 traces 数据源配置

```sql
INSERT INTO aivision_space_agent_datasource
  (space_id, agent_id, datasource_type, source_system, config, status)
VALUES
  (
    12345,
    67890,
    'traces',
    'observation_gateway',
    JSON_OBJECT(
      'agent_id_field', 'attributes.gen_ai.agent.id',
      'agent_name_field', 'attributes.gen_ai.agent.name',
      'default_filters', JSON_ARRAY()
    ),
    1
  );
```

### 7.3 给某个自定义 Agent 插入默认面板

```sql
INSERT INTO aivision_space_agent_dashboard_config
  (space_id, agent_id, panel_key, panel_name, panel_type, panel_config, sort_order, status)
VALUES
  (
    12345,
    67890,
    'trace_overview',
    'Trace 概览',
    'stat',
    JSON_OBJECT(
      'datasource_type', 'traces',
      'metric', 'trace_count'
    ),
    10,
    1
  ),
  (
    12345,
    67890,
    'token_trend',
    'Token 趋势',
    'line',
    JSON_OBJECT(
      'datasource_type', 'traces',
      'metric', 'total_tokens',
      'group_by', 'day'
    ),
    20,
    1
  );
```

### 7.4 查询某个空间下某个 Agent 的完整看板配置

```sql
SELECT
  a.id AS agent_id,
  a.space_id,
  a.agent_product,
  a.name,
  a.custom_name,
  d.datasource_type,
  d.source_system,
  d.config AS datasource_config,
  p.panel_key,
  p.panel_name,
  p.panel_type,
  p.panel_config,
  p.sort_order
FROM aivision_agent a
LEFT JOIN aivision_space_agent_datasource d
  ON d.space_id = a.space_id
 AND d.agent_id = a.id
 AND d.gmt_deleted IS NULL
LEFT JOIN aivision_space_agent_dashboard_config p
  ON p.space_id = a.space_id
 AND p.agent_id = a.id
 AND p.gmt_deleted IS NULL
WHERE a.space_id = 12345
  AND a.id = 67890
  AND a.status = 1
  AND a.gmt_deleted IS NULL
ORDER BY p.sort_order, p.id;
```

## 8. 看板兼容策略落地建议

落地时建议这样分流：

1. 个人空间 + 内置 agent
   - 继续查现有 `AgentTypeConfig` / `DataSource` / panels

2. 个人空间 + 自定义 agent
   - 优先查 `aivision_space_agent_datasource` / `aivision_space_agent_dashboard_config`

3. 业务空间 + 自定义 agent
   - 只查 `aivision_space_agent_datasource` / `aivision_space_agent_dashboard_config`

4. 业务空间 + 内置 agent
   - 一期直接不支持

## 9. 与 Observation Project 的边界

本文件关注的是“空间内 agent 看板配置”，不负责定义以下能力：

- 空间级 observation project 绑定
- 对外兼容查询 API
- 上报 Key / 读取 Key
- 外部鉴权与网关代理

边界原则：

- `SpaceObservationProject` 负责“这个空间的观测数据从哪里来”
- `SpaceAgentDataSource` / `SpaceAgentDashboardConfig` 负责“这个空间里的某个 agent 如何展示和消费这些数据”

两者应保持解耦，避免把 observation routing 和 dashboard rendering 混在同一层模型中。

