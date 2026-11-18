# Changelog

本文件记录 observ-pi 的重要版本变更。

## [0.2.1] - 2026-08-11

### Added

- Pi 的真实用户 Turn span 固定写入字符串属性 `_internal.meta.root="1"`；
  上游 OTel parent 不会抑制该逻辑根标记，LLM、Tool、Compaction、事件和指标
  保持不标记。

### Security

- `otelExporter.attributes` 与 `OBSERV_PI_OTEL_ATTRIBUTES` 中的保留键
  `_internal.meta.root` 会在公共信号属性合并后被精确过滤，其他
  `_internal.*` 自定义属性继续保留。
