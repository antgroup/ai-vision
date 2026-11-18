# AgentLens Projection 文档

本目录统一维护 Trace 与 Projection 的当前行为文档，不按 runtime 拆分顶层目录。
公共合同与各 Projector 实现说明都从这里进入。

## 文档导航

| 文档 | 内容 |
|---|---|
| [Trace / Projection 契约](trace-contract.md) | Trace 运行时实体、Projection 公共模型、Projector 路由、变量与失败语义 |
| [Coding Projector](coding.md) | Coding turn 的 CHAT/LLM/TOOL、文件修改、验证和子 Agent 投影 |
| [Framework Projector](framework.md) | LangChain / LangGraph / Deep Agents 的 Langfuse / Relay SDK 来源与材料边界 |

当前内建实现为 `CodingProjector` 和 `FrameworkProjector`；产品或版本 runtime 通过
alias 路由到其中一个实现。没有专用 Projector 的 runtime 仍保留原始 `Trace.spans`，但
`Trace.projection` 为空。

## 文档组织

- 公共 Trace、Projection、变量和序列化合同维护在 `trace-contract.md`。
- 每个内建 Projector 的具体字段提取与优先级维护在本目录独立文件中。
- 缺少现役映射的 runtime 与历史记录读取边界维护在 `trace-contract.md`；迁移和回滚见
  [调度文档](../scheduling.md#诊断退役迁移与发布)。
- 表达式读取的实现说明仍由 [`docs/expression-engine.md`](../expression-engine.md) 维护。

Projection 的架构决策记录仍按对象保留在
[`docs/adr/projection/`](../adr/projection/README.md)，不与当前行为文档混放。ADR 的
`canonical_doc` 应指向本目录中的对应当前行为文档。
