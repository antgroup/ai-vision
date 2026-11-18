# Changelog

本文件记录 observ-opencode 的重要版本变更。

## [0.3.4] - 2026-08-11

### Added

- 仅由 OpenCode 拥有的真实 Turn span 写入字符串属性
  `_internal.meta.root="1"`；上游 OTel parent 不会抑制该逻辑根标记，借用的
  SubAgent AGENT span 及 LLM、Tool、Compaction 等子 span 保持不标记。

### Security

- `otelExporter.attributes` 与 `OBSERV_OPENCODE_OTEL_ATTRIBUTES` 中的保留键
  `_internal.meta.root` 会在公共信号属性合并后被精确过滤，其他
  `_internal.*` 自定义属性继续保留。

## [0.3.3] - 2026-07-22

### Fixed

- `/resume` 在插件重启且 OpenCode 不重发 `session.created` 时会惰性恢复 session 状态，不再丢弃后续遥测。
- 生产插件使用自有的最小状态文件持久化每个 session 的 `turnCounter` 高水位和最后一条 user message ID，使恢复后的 Turn 序号连续且重发事件不会重复计数。
- ESM 产物现在自带模块内 `createRequire` 兼容绑定，OpenCode Desktop 可直接加载包，不再依赖外部 loader 注入全局 `require`。
- Desktop 同一进程内的重复或并发工作区加载会复用同一份 hooks 和 OTel runtime，不再触发 runtime profile 重复占用错误。

### Security

- 状态文件名使用 session ID 的 SHA-256 摘要，目录权限为 `0700`、文件权限为 `0600`，并通过同目录临时文件原子替换。
- 状态恢复失败或文件损坏时安全回退，不阻断遥测；不读取 OpenCode 会话历史，也不新增第三方依赖。

## [0.3.0] - 2026-07-13

### Added

- 新增按 OpenCode 实例解析独立插件配置文件的能力。
- 设置 `OPENCODE_CONFIG=/path/to/profile/opencode.json` 时，插件读取同目录的 `observ-opencode.json`。
- 未设置 `OPENCODE_CONFIG`、但设置 `OPENCODE_CONFIG_DIR=/path/to/profile` 时，插件读取该目录下的 `observ-opencode.json`。
- 新增配置路径解析测试，覆盖默认目录、实例目录和相对 `OPENCODE_CONFIG` 路径。

### Changed

- 默认配置仍为 `~/.config/opencode/observ-opencode.json`，但多个 OpenCode 进程现在可以通过各自的配置路径隔离 observ-opencode 设置。
- 更新 README 与完整配置示例，说明实例级配置查找顺序。
- package、lockfile 与插件 manifest 版本统一升级到 `0.3.0`。

## [0.2.9] - 2026-07-13

### Added

- 新增顶层 `dynamicUsername` 与 `dynamicUid` 配置。启用后，每次记录 span、event/log 或 metric 时动态读取 `AIVISION_ANT_USERNAME` 与 `AIVISION_ANT_UID`。
- 新增 `otelExporter.attributes`，用于向 spans、events/logs 与 metrics 注入公共信号属性，包括 `ant.username`、`ant.uid` 和自定义维度。
- 新增 `otelExporter.resourceAttributes`，用于配置 `ant.agent.*` 等 Resource 属性。
- 新增统一 `test` 入口和事件语义测试，覆盖配置优先级、动态身份、公共属性合并、隐私模式及事件字段。

### Changed

- 配置结构与 observ-openclaw 对齐，身份与 Agent 元数据统一迁移到 OTel attributes/resource attributes。
- 公共信号属性在记录时解析，允许 sandbox 或运行环境中的动态身份在同一进程内变化。
- spans、events/logs 与 metrics 统一应用公共属性；具体信号已设置的语义属性优先于公共属性。
- `ant.username` 与 `ant.uid` 作为信号属性处理；`ant.uid` 不再写入 Resource。
- 更新插件 manifest、配置示例、README、report/replay 和 edition 默认配置以使用统一配置模型。

### Removed

- 移除顶层 `ant` 配置对象。旧配置会被拒绝并提示迁移。
- 移除通过旧 `ant.*` 字段配置 `username`、`uid`、`agentType`、`agentProduct`、`agentInstance`、`agentEnv`、`agentScene` 与 `publicKey` 的路径。

### Migration

- 将 `ant.username` 与 `ant.uid` 迁移到 `otelExporter.attributes`。
- 将 `ant.agent.*` 元数据迁移到 `otelExporter.resourceAttributes`。
- 如需运行时动态身份，在顶层启用 `dynamicUsername` 和/或 `dynamicUid`。
