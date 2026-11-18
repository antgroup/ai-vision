# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added
- 恢复顶层 `dynamicUsername` / `dynamicUid`；启用后在信号记录时动态读取 `AIVISION_ANT_USERNAME` / `AIVISION_ANT_UID`。

## [0.5.3] - 2026-08-11

### Added
- 仅由 OpenClaw 拥有的真实 Turn span 写入字符串属性
  `_internal.meta.root="1"`；上游 diagnostic OTel parent 不会抑制该标记，
  借用的 SubAgent AGENT span 和 synthetic compaction CHAT span 保持不标记。

### Security
- `otelExporter.attributes` 与 `OBSERV_OPENCLAW_OTEL_ATTRIBUTES` 中的保留键
  `_internal.meta.root` 会在公共信号属性合并后被精确过滤，其他
  `_internal.*` 自定义属性继续保留。

## [0.5.0] - 2026-07-11

### Added
- 新增 LLM/API 错误解析与错误 span 修正：支持嵌套 provider error、rate-limit 分类及 assistant 最终错误回填。
- 当无法关联已有 model-call span 时，创建去重的 fallback error span；privacy 模式下错误消息保持脱敏。
- 补充 `gen_ai.response.id` 关联，避免把同一响应 ID 错配到多个 model-call span。
- 新增 API error、response ID、session lifecycle、edition/config contract 和打包产物验证用例。

### Changed
- 仅保留 `common` 与 `aivision` edition，并统一 0.5.0 打包产物。
- 移除顶层 `ant` 配置：`ant.username`、`ant.uid` 改由 `otelExporter.attributes` 提供，`ant.agent.*` 改由 `otelExporter.resourceAttributes` 提供。
- `otelExporter.attributes` 统一注入 spans、events/logs 与 metrics；具体信号的语义字段优先于通用属性。
- 加强受管 aivision edition 的配置边界：认证 header、输出目录及信号端点不再被非受管环境变量意外覆盖。
- 构建流程新增生成配置校验和 edition 语义测试。

### Removed
- 不再读取旧 AIVISION 身份环境变量、动态身份开关和 `publicKey` 配置。
- 移除旧 `ant` edition 与对应打包脚本。

## [0.2.11] - 2026-04-02

### Changed
- 构建链切换为 esbuild bundle，发布包改为少量自包含 JS 产物，避免安装阶段解压数千个依赖文件导致超时
- 保留 packed smoke test，发布前继续校验打包产物在未额外执行 `npm install` 的情况下也能成功导入

## [0.2.10] - 2026-04-02

### Changed
- npm 发布包改为内置 OpenTelemetry 运行时依赖，用户执行 `openclaw plugin install observ-openclaw` 后无需再额外安装依赖即可加载插件
- 发布流程新增 packed smoke test，发布前会校验打包产物在未额外执行 `npm install` 的情况下也能成功导入

## [0.2.9] - 2026-04-01

### Added
- 上报指标添加 `ant.username` label，支持按用户维度分析指标数据

## [0.2.8]

### Changed
- log 上报事件使用 `BatchLogRecordProcessor`，优化性能
