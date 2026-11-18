# AI Vision 平台开发索引

Django 后端与 React / Vite 前端组成的平台，负责账号、空间、Agent 接入、遥测查询及诊断交互。开始修改前读取 [AGENTS.md](AGENTS.md)；整个 monorepo 的任务入口见[仓库知识索引](../docs/README.md)。

| 任务 | 仓库入口 |
|---|---|
| 了解平台边界与数据流 | [架构说明](docs/architecture-overview.md)、[工程约定与关键路径](AGENTS.md) |
| 后端开发与本地检查 | [后端 README](aivision-py-be/README.md)、[后端约定](aivision-py-be/AGENTS.md)、[Core 约定](aivision-py-be/apps/core/AGENTS.md) |
| 现役前端开发 | [VFE 目录约定](vfe/AGENTS.md)、[V2 设计索引](docs/v2/README.md) |
| Agent 采集接入实现 | [插件目录](agent-plugins/README.md)、[插件约定](agent-plugins/AGENTS.md) |
| 查询接口、身份与空间 | [查询 API](docs/openapi/query/README.md)、[路由索引](docs/traces/query-routing/README.md)、[空间设计](docs/spaces/README.md) |
| 诊断平台联调 | [联调清单](docs/diagnose/integration-checklist.md)、[AgentLens 约定](../agentlens/AGENTS.md) |
| 镜像构建与运行脚本 | [scripts/README.md](scripts/README.md)、[部署维护索引](../docs/deployment/README.md) |

完整用户操作与配图见 [AI Vision 文档站](https://aivision.mintlify.site/zh-CN/introduction)。本目录的设计和契约随实现维护；核对当前源码和测试后再使用历史设计中的步骤。
