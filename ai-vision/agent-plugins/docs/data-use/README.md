# 数据使用

插件保留已有 Langfuse 上报能力；本部署的 OTLP traces、logs、metrics 经认证网关写入 SigNoz 的 OceanBase 遥测表。字段本身遵循 OTel 与 Agent 原生语义，不因存储切换改名。

- [Token 用量统计口径](token-usage.md)
- [Claude Code 事件字段](claudecode/events.md)
- [Codex 事件字段](codex/events.md)

`*-preserved.md` 保留原始属性清单。属性中的历史命名属于数据合同，不代表启用对应的旧存储后端。

`otel-*-fields.txt` 保存历史采集的原始属性参考，不是 SigNoz OceanBase 的建表定义。
