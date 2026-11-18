# 统一 Agent 提示词到数据库管理

## Context

当前 Agent 相关的描述文案（description、config_guide、cli_command、icon）分散在三处：
1. **后端 seed_data.py** — 硬编码 agent 描述、icon 和 cli_command
2. **前端 AgentOnboarding.tsx** — 硬编码接入引导文案、限制说明、功能标签
3. **前端 agentProducts.ts / CombinedAgents.tsx** — 硬编码 icon URL 映射、自定义接入说明

`AgentTypeConfig` 模型的 `config_guide` 和 `icon` 字段已存在但未充分使用。目标是将所有 agent 配置文案和 icon 统一到 DB，通过 API 下发给前端。

## 实现方案

### 1. `config_guide` JSON 结构

`config_guide` 字段（JSONField）存储如下结构的 JSON：

```json
{
  "setup_guide": {
    "title": "自动发现模式",
    "description": "此 Agent 支持自动发现，无需手动配置 API Key。启动后会自动接入平台。",
    "type": "auto | manual"
  },
  "limitations": ["示例：此接入仅采集命令行会话。"],
  "features": {
    "preset_dashboard": true,
    "trace_supported": true
  },
  "empty_state": {
    "no_trace": "自定义的轨迹为空提示（可选，不配则用默认 i18n）",
    "no_session": "自定义的 Session 为空提示（可选）"
  },
  "success_message": "使用 {name} 后数据将自动采集到平台",
  "custom_guide": {
    "title": "接入说明",
    "description": "当前版本支持为非内置 Agent 预留自定义接入入口...",
    "checklist": ["确认 Agent 名称与实例名规则。", "..."]
  }
}
```

### 2. `icon` 字段

`icon` 字段（CharField）可以保存 Agent 图标的 URL。

前端优先使用 DB 返回的 `icon`，fallback 到前端硬编码的 `AGENT_LOGO_MAP`，再 fallback 到文字首字母。

### 3. 后端修改

- **seed_data.py**: 为每个 agent type 添加 `config_guide` JSON、`setup_mode`、`icon` CDN URL；改 `get_or_create` 为 `update_or_create`
- **serializers.py**: `config_guide` 作为 `AgentTypeConfigSerializer`（ModelSerializer）的普通字段，JSONField 自动序列化为 JSON 对象
- **migrations/0003_populate_config_guide.py**: 数据迁移，为已部署环境填充数据

### 4. 前端修改

- **AgentOnboarding.tsx**: 引导文案、限制说明、功能标签从 `config_guide` 读取；icon 从 API `icon` 字段读取
- **CombinedAgents.tsx**: 自定义接入说明从 `config_guide.custom_guide` 读取
- **AgentList.tsx**: icon 优先用 API 返回的 `icon` 字段
- **Trace.tsx**: 空状态提示从 `config_guide.empty_state` 读取，fallback 到 i18n 默认值
- 所有硬编码中文 UI 标签迁移到 i18n 翻译文件

### 5. 各 Agent 数据配置

在 Django Admin 的 `AgentTypeConfig` 表中维护以下字段：

| agent_name | icon | setup_mode | config_guide |
|---|---|---|---|
| CLAUDE_CODE | CDN URL | APIKEY | setup_guide(manual) + features(trace) |
| CUSTOM | ⚙️ | OTHER | setup_guide(manual) + custom_guide(checklist) |

## 关键文件

| 文件 | 改动 |
|------|------|
| `apps/core/management/commands/seed_data.py` | config_guide、icon CDN URL、update_or_create |
| `apps/core/serializers.py` | config_guide 作为 ModelSerializer 普通字段（JSONField 自动序列化） |
| `apps/core/migrations/0003_populate_config_guide.py` | 数据迁移 |
| `vfe/src/pages/AgentOnboarding.tsx` | 用 API 数据替换硬编码文案和 icon |
| `vfe/src/pages/AgentList.tsx` | icon 优先用 API 数据 |
| `vfe/src/pages/CombinedAgents.tsx` | 用 API 数据替换硬编码文案 |
| `vfe/src/pages/Trace.tsx` | 空状态提示从 config_guide 读取 |
| `vfe/src/i18n/locales/*.json` | 新增 i18n key |

## 验证方式

1. 运行 `python manage.py migrate` 确认迁移成功
2. 运行 `python manage.py seed_data` 确认 config_guide 和 icon 被正确写入
3. 调用 API `GET /api/agent-types/` 确认 config_guide 返回为 JSON 对象、icon 为 CDN URL
4. 前端页面验证 Agent 卡片 icon 显示正确
5. 前端页面验证 Agent 接入弹窗显示正确的引导文案
6. 轨迹页面验证空状态提示语按 agent 配置展示
