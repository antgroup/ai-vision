# AiVision Weaver Agent Loop

本文件是 agent 面向本地 `weaver-guard serve` 的自主迭代协议。目标：把 replay / 实时 OTLP 上报到 `http://127.0.0.1:4319`，读取语义 findings，修复 agent instrumentation 或 Rego 规则，再重复验证，直到当前内存窗口没有需要处理的 violations。

## 1. 启动本地 Weaver

```bash
make -C weaver serve
```

默认入口：

```text
Dashboard:    http://127.0.0.1:4319/dashboard
OTLP traces:  http://127.0.0.1:4319/v1/traces
OTLP logs:    http://127.0.0.1:4319/v1/logs
OTLP metrics: http://127.0.0.1:4319/v1/metrics
```

`serve` 启动后会在内存保存 recent diagnostics；重启后不加载旧内存状态。非重启、非清理情况下，recent 数据不会因为超过窗口而自动减少。

## 2. Replay 上报入口

根 Makefile 已恢复下面四个命令：

```bash
# 单 agent：生成 replay，然后把 replay JSONL POST 到本地 weaver
make report-check cc
make report-check ocd

# 单 agent：把 golden JSONL POST 到本地 weaver
make report-check-golden cc

# 4 个成熟 agent：claudecode/codex/openclaw/opencode
make report-check-all
make report-check-golden-all
```

可配置项：

```bash
# 指定 Weaver 地址，默认就是 http://127.0.0.1:4319
make report-check cc WEAVER_URL=http://127.0.0.1:4319

# 只验证文件路由，不真正 POST；用于检查命令是否可用
make report-check-all REPORT_CHECK_DRY_RUN=1

# 输出每条 JSONL record 的成功日志；默认只按文件汇总
make report-check cc REPORT_CHECK_VERBOSE=1
```

文件到 OTLP endpoint 的映射：

| replay 文件名 | POST endpoint |
| --- | --- |
| `spans_*.jsonl` / `traces_*.jsonl` | `/v1/traces` |
| `events_*.jsonl` / `logs_*.jsonl` | `/v1/logs` |
| `metrics_*.jsonl` | `/v1/metrics` |

## 3. Agent Loop API

### 3.1 清理当前内存数据

清理 dashboard recent diagnostics、payload 展示和页面统计；不删除磁盘上的 `weaver/live/diagnostics.jsonl` / `weaver/live/replays/*`。

```bash
curl -sS -X POST http://127.0.0.1:4319/api/clear | jq .
# 或
curl -sS -X DELETE http://127.0.0.1:4319/api/clear | jq .
```

返回示例：

```json
{
  "cleared_records": 12,
  "remaining_records": 0,
  "scope": "recent_diagnostics"
}
```

### 3.2 获取 findings / violations 批次

默认一次返回最多 10 条 `violation`。通过 `signal` 和 `level(s)` 可以按信号、等级过滤。

```bash
curl -sS 'http://127.0.0.1:4319/api/violations?limit=10' | jq .
```

兼容历史错拼路径：

```bash
curl -sS 'http://127.0.0.1:4319/api/voilations?limit=10' | jq .
```

查询参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `limit` | `10` | 返回 finding 条数，服务端限制在 `1..100` 且不超过 recent limit |
| `signal` | 空 | `trace`/`traces`/`span`/`spans`，`event`/`events`/`log`/`logs`，`metric`/`metrics` |
| `level` | `violation` | `violation` / `improvement` / `recommend` / `recommand` / `information` / `semconv` / `all` |
| `levels` | 空 | 逗号分隔多等级，例如 `levels=violation,recommend,semconv` |
| `include_payload` | `false` | 是否返回完整 OTLP payload；默认关闭，避免 prompt/tool payload 过大 |

常用查询：

```bash
# traces violations
curl -sS 'http://127.0.0.1:4319/api/violations?signal=trace&level=violation&limit=10' | jq .

# events/logs recommendations；兼容 recommand 拼写
curl -sS 'http://127.0.0.1:4319/api/violations?signal=event&level=recommand&limit=10' | jq .

# metrics violations + recommendations
curl -sS 'http://127.0.0.1:4319/api/violations?signal=metric&levels=violation,recommend&limit=10' | jq .

# 全部等级
curl -sS 'http://127.0.0.1:4319/api/violations?level=all&limit=10' | jq .

# 带完整 payload。仅本机调试使用，payload 可能包含 prompt/tool 参数/输出
curl -sS 'http://127.0.0.1:4319/api/violations?limit=3&signal=traces&level=all&include_payload=true' | jq .
```

核心返回字段：

```json
{
  "findings": [
    {
      "id": "V-req-53-0-missing_foo",
      "request": {
        "timestamp": "2026-07-03T00:00:00Z",
        "request_id": "req-53",
        "path": "/v1/traces",
        "method": "POST",
        "signal": "traces",
        "signal_group": "traces",
        "agent": "ClaudeCode",
        "source": "claudecode",
        "sample_count": 5,
        "rejected_count": 1,
        "status": "violation",
        "http_status": 200,
        "replay_file": "live/replays/received_traces_20260703.jsonl"
      },
      "finding": {
        "level": "violation",
        "id": "missing_foo",
        "rule": "policy:missing_foo",
        "sample": "Turn 1",
        "message": "...",
        "context": {}
      },
      "rule": {
        "id": "missing_foo",
        "key": "aivision:missing_foo",
        "rule_group": "aivision",
        "policy_dir": "traces",
        "group": "traces",
        "level": "violation",
        "file": "aivision/traces/base/violations_required.rego",
        "line": 12,
        "purpose_zh": "...",
        "check_zh": "...",
        "fix_zh": "...",
        "source": "deny contains make_advice(...) ..."
      },
      "otlp_path": "resourceSpans[*].scopeSpans[*].spans[\"Turn 1\"]",
      "payload_included": false,
      "payload": null
    }
  ],
  "violations": [
    {
      "id": "V-req-53-0-missing_foo",
      "...": "same item shape as findings[] for backward compatibility"
    }
  ],
  "returned": 1,
  "limit": 10,
  "max_limit": 100,
  "default_limit": 10,
  "total_recent_findings": 1,
  "total_recent_violations": 1,
  "truncated": false,
  "filters": {
    "signal": "traces",
    "levels": ["violation"],
    "include_all_levels": false,
    "default_level_filter": true,
    "ignored_levels": []
  },
  "supported_levels": ["violation", "improvement", "information", "semconv"],
  "records_scanned": 12
}
```

兼容说明：`violations` 字段保留给旧 loop 使用；现在它和 `findings` 返回同一批过滤后的 finding，不再只代表 violation 等级。

### 3.3 获取最近 records / 单条 payload

Dashboard 默认用这个接口读取所有内存 recent records，但不带 payload：

```bash
curl -sS 'http://127.0.0.1:4319/api/recent?all=true&include_payload=false' | jq .
```

按 request id 取完整 payload：

```bash
curl -sS 'http://127.0.0.1:4319/api/payload/req-1' | jq .
```

### 3.4 状态 / 规则

```bash
# 当前统计、转发后端、recent record 数量
curl -sS http://127.0.0.1:4319/api/stats | jq .

# 当前加载的 Rego 规则、开关状态、中文说明和源码片段
curl -sS http://127.0.0.1:4319/api/rules | jq .
```

## 4. 推荐自主迭代流程

### 单次 loop

```bash
# 1. 清理旧内存数据
curl -sS -X POST http://127.0.0.1:4319/api/clear | jq .

# 2. 生成 replay 并 POST 到 Weaver HTTP 服务
make report-check cc

# 3. 获取最多 10 条 violations
curl -sS 'http://127.0.0.1:4319/api/violations?limit=10' > /tmp/weaver-violations.json
cat /tmp/weaver-violations.json | jq '.returned, .total_recent_violations'

# 4. agent 读取 /tmp/weaver-violations.json，按 finding/rule/otlp_path 定位修复
# 5. 运行相关测试 / replay / report-check
# 6. 重复步骤 3-5，直到 returned == 0 且 total_recent_violations == 0
```

### 分信号 loop

```bash
# Traces
curl -sS 'http://127.0.0.1:4319/api/violations?signal=trace&level=violation&limit=10' > /tmp/weaver-trace-violations.json

# Events / logs
curl -sS 'http://127.0.0.1:4319/api/violations?signal=event&level=violation&limit=10' > /tmp/weaver-event-violations.json

# Metrics
curl -sS 'http://127.0.0.1:4319/api/violations?signal=metric&level=violation&limit=10' > /tmp/weaver-metric-violations.json
```

## 5. 修复判定规则

- `returned == 0` 且 `total_recent_violations == 0`：当前内存窗口无 violation。
- `returned > 0`：优先修复前 10 条；同类 violation 应批量修复。
- `truncated == true`：还有更多 finding，继续循环拉取。
- 需要处理推荐项时使用 `level=recommend`；需要完整诊断时使用 `level=all` 并读取 `total_recent_findings`。
- finding 是 agent 上报字段缺失 / 字段错误：修 agent instrumentation、hook payload 映射或 replay 生成逻辑。
- finding 是规则误报：修 `weaver/policies/**.rego`，并补 pass/fail fixture 或单测；不能为了变绿直接降低语义标准。
- `include_payload=true` 只用于本机调试；不要把 payload 贴到外部系统，除非已确认不含敏感 prompt/tool 数据。

## 6. 最小命令速查

```bash
# 清理
curl -sS -X POST http://127.0.0.1:4319/api/clear | jq .

# 当前 agent replay -> weaver
make report-check cc
make report-check ocd

# 全部成熟 agent replay -> weaver
make report-check-all

# 获取最新 10 条 violations
curl -sS 'http://127.0.0.1:4319/api/violations?limit=10' | jq .

# 只看 traces / events / metrics
curl -sS 'http://127.0.0.1:4319/api/violations?signal=trace&level=violation&limit=10' | jq .
curl -sS 'http://127.0.0.1:4319/api/violations?signal=event&level=violation&limit=10' | jq .
curl -sS 'http://127.0.0.1:4319/api/violations?signal=metric&level=violation&limit=10' | jq .

# 推荐项 / 全部等级
curl -sS 'http://127.0.0.1:4319/api/violations?level=recommend&limit=10' | jq .
curl -sS 'http://127.0.0.1:4319/api/violations?level=all&limit=10' | jq .
```
