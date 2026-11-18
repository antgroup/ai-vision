# Claude Code Trace 保留字段

## 外部传入的 Resource 保留字段

下列字段由外部上报链路传入，不由 Claude Code trace 字段清理流程判定其生命周期。即使字段只出现在底层表、未出现在当前 ODPS 有效字段清单中，也必须保留，不得放入 deprecated 清单：

| 字段 | 来源 | 处理规则 |
|---|---|---|
| `resource.attributes.agentic.collection.mode` | 外部传入 | 保留，不得 deprecated |
| `resource.attributes.ant.agent.env` | 外部传入 | 保留，不得 deprecated |
| `resource.attributes.ant.agent.instance` | 外部传入 | 保留，不得 deprecated |
| `resource.attributes.ant.agent.product` | 外部传入 | 保留，不得 deprecated |
| `resource.attributes.ant.agent.scene` | 外部传入 | 保留，不得 deprecated |
| `resource.attributes.ant.agent.type` | 外部传入 | 保留，不得 deprecated |
| `resource.attributes.user.id` | 外部传入 | 保留，不得 deprecated |
| `resource.attributes.ant.username` | 外部传入 | 保留，不得 deprecated |

本规则适用于 `docs/data-use/claudecode/` 下所有 trace 表的 deprecated 字段报告。字段是否有值、值由哪个外部系统提供，不影响其保留状态。
