# Makefile 使用指南

## 命令格式

```
make {target} {agent} [VAR=val ...]
```

第二个参数固定为 agent 简写，必填（`help` 除外）。

## Agent 简写

| 简写 | Agent | 目录 |
|------|-------|------|
| `cc` | Claude Code | claudecode/ |
| `cdx` | Codex | codex/ |
| `ocw` | OpenClaw | openclaw/ |
| `ocd` | OpenCode | opencode/ |
| `hm` | Hermes | hermes/ |
| `km` | Kimi | kimi/ |
| `pi` | Pi | pi/ |
| `ds`, `dsh` | DeepSeek Harness | deepseek/ |
| `all` | 所有 agent | — |
| `cc,cdx` | 逗号分隔多选 | — |

## Pipeline

Runtime artifacts are shared across worktrees through `AIVISION_HOME` (default
`~/.aivision`). Component roots are `cc`, `cdx`, `ocw`, `ocd`, `pi`,
`kimi`, `hermes`, and `dsh`. Each root has canonical `transcripts/`, `replays/`
and `golden/` paths. Explicit `TRANSCRIPT_DIR`, `REPLAY_DIR`, `GOLDEN_DIR` and
component-specific environment
variables remain authoritative. Runtime artifacts are not tracked by Git.

`make migrate-artifacts` is dry-run by default. Set `MIGRATE_APPLY=1` only
after reviewing the plan. Apply aborts before writing on any collision; resolve
every collision explicitly before applying the copy.

```bash
make replay-all cc              # 转换 claudecode 所有 transcript → replay
make replay-all all             # 转换所有 agent 的 transcript
make replay cc TS=session_xxx.jsonl                      # 转换单个 transcript
make validate cc                # 验证 claudecode 所有 replay
make validate cc,cdx            # 验证 claudecode + codex
make validate-one cc FILE=~/.aivision/cc/replays/spans_xxx.jsonl
make report cc TS=xxx.jsonl                              # 上报到 Langfuse
make report ocw TS=xxx.jsonl CONFIG=report.config.toml PROFILE=dev SHIFT=1
```

## Golden 基线回归

```bash
make golden-check cc            # 对比 replay 与 golden 基线
make golden-check all           # 检查所有 agent
make golden-update cc           # 用当前 replay 更新 golden
make golden-init cc             # 初始化 golden 示例
```

## Build

```bash
make build cc                   # debug 构建
make build-edition cc EDITION=common       # 构建指定 edition
make build-all cc               # 构建所有 edition
make release cc           # 构建所有 edition × 所有平台（Rust agent 需要 zig）
make install cc EDITION=aivision           # 安装 edition 到本地
make test cc                    # 运行测试
make list-artifacts cc          # 列出构建产物
```

## Edition 体系

| Edition | 说明 | 适用 agent |
|---------|------|-----------|
| `aivision` | 内部分发 | cc, cdx, ocw, ocd |
| `common` | 开源 / vanilla | cc, cdx, ocw, ocd |
| `common-eval` | 带 eval 功能 | cc |

## Tools

```bash
make transcripts cc             # 列出 transcript 文件
make replays cc                 # 列出 replay 文件
make clean cc                   # 清理生成物
make help                       # 显示顶层帮助
make help cc                    # 显示 claudecode 帮助
```

## 完整 Target 列表

| Target | 说明 | 需要变量 |
|--------|------|----------|
| `replay-all` | 批量转换 transcript → replay | — |
| `replay` | 单文件转换 | `TRANSCRIPT=` |
| `validate` | 验证所有 replay | `FORMAT=text\|json` |
| `validate-one` | 验证单个 replay | `FILE=` |
| `report` | 上报到 Langfuse | `TRANSCRIPT=` `CONFIG=` `PROFILE=` `SHIFT=` |
| `golden-check` | golden 基线对比 | — |
| `golden-update` | 更新 golden 基线 | — |
| `golden-init` | 初始化 golden | — |
| `build` | debug 构建 | — |
| `build-edition` | 构建指定 edition | `EDITION=` |
| `build-all` | 构建所有 edition | `VERSION=` `TARGETS=` |
| `release` | 全平台构建 | `VERSION=` |
| `install` | 安装 edition | `EDITION=` |
| `test` | 运行测试 | — |
| `clean` | 清理生成物 | — |
| `transcripts` | 列出 transcript | — |
| `replays` | 列出 replay | — |
| `list-artifacts` | 列出构建产物 | — |
| `help` | 显示帮助 | — |
