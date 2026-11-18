
我需要对整体的架构做一次重构

重构的原因是：未来会有更多 hook 需求复用当前仓库，不只是 Langfuse 上报。

后续 hook 也不会只覆盖 `Stop`，还会接入更多 Codex hook 事件。

1. 当前内置实现统一沉淀到 `hooks/langfuse` 等静态模块中
2. 保留 `hooks/dynamic` 目录，用于每个独立需求的运行时扩展
3. 在外层 `main` 中通过注册机制分发 hook 事件，由主逻辑按 handler 支持的事件进行调度
4. `otel`、`transcript`、`hook`、`logger` 等通用能力放到 `src/common`

这个设计思路后续可以继续扩展。

可以优先参考仓库上层的 `codex-hooks-overview.md` 查看 Codex hooks 的定义和输入输出；如需对照上游实现风格，可再参考目录下的 `claude-hooks-reference.md`。
