#!/bin/bash
#
# doctor.sh — 跨 agent 接入验收
#
# 用法:   bash scripts/doctor.sh <agent-dir>
# 例子:   bash scripts/doctor.sh claudecode
#
# 检查项 (按 METHODOLOGY.md §3.5 分层 doctor 设计):
#   (a) 目录骨架: ~/.aivision/<component>/{transcripts,replays}, <agent>/CAPABILITIES.md, <agent>/Makefile 存在
#   (b) Makefile 8 个标准 target: build / replay / replay-all / validate / validate-one / report / report-shift / clean
#   (c) Resource 必填 4 项 (sample 取 1 个 replay): ant.agent.env / ant.agent.scene / ant.username / ant.agent.type
#   (d) e2e 证据: transcripts 与 replays 各至少 1 份样本，最新 replay 可被 jq 解析为 OTLP JSONL
#
# 输出:   PASS / WARN / FAIL 三态分类
# 退出码: 0 (即使有 WARN)；只有 FAIL 才返回非 0
#
# 依赖:   bash, jq, grep, ls, find. 不依赖 Rust/TS 编译产物.

set -e

AGENT_DIR="${1:-}"
if [ -z "$AGENT_DIR" ]; then
    echo "Usage: $0 <agent-dir>" >&2
    echo "  例: bash scripts/doctor.sh claudecode" >&2
    exit 2
fi

# 尝试解析 AGENT_DIR (支持组件简写)
case "$AGENT_DIR" in
    cc)  AGENT_DIR="claudecode" ;;
    cdx) AGENT_DIR="codex" ;;
    ocw) AGENT_DIR="openclaw" ;;
    ocd) AGENT_DIR="opencode" ;;
    km|kimi) AGENT_DIR="kimi" ;;
    hm) AGENT_DIR="hermes" ;;
    pi-replay) AGENT_DIR="pi" ;;
    ds|dsh) AGENT_DIR="deepseek" ;;
esac

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
AGENT_PATH="$REPO_ROOT/$AGENT_DIR"
ARTIFACT_RESOLVER="$REPO_ROOT/scripts/aivision_artifacts.py"
TRANSCRIPT_DIR=$(python3 "$ARTIFACT_RESOLVER" path "$AGENT_DIR" transcripts)
REPLAY_DIR=$(python3 "$ARTIFACT_RESOLVER" path "$AGENT_DIR" replays)

# dsh transcripts are host-maintained session artifacts, not flat files in the
# AIVision transcript directory. The checked-in fixture corpus is the stable
# doctor transcript evidence for the dsh component.
DSH_FIXTURE_TRANSCRIPT="$AGENT_PATH/dsh-replay/tests/fixtures/session-fixture-001.jsonl"
if [ "$AGENT_DIR" = "deepseek" ]; then
    TRANSCRIPT_SAMPLE_DIR="$AGENT_PATH/dsh-replay/tests/fixtures"
else
    TRANSCRIPT_SAMPLE_DIR="$TRANSCRIPT_DIR"
fi

if [ ! -d "$AGENT_PATH" ]; then
    echo "ERROR: $AGENT_DIR 目录不存在" >&2
    exit 2
fi

# 计数器
PASS=0
WARN=0
FAIL=0

# L 级估算专用标志（仅追踪 [a/b/c/d] 各组的失败/必填项通过情况）
A_FAIL=0           # [a] 骨架: 任一失败置 1
B_FAIL=0           # [b] Makefile target: 任一缺失（warn）置 1
D_FAIL=0           # [d] e2e 证据: 任一失败/无样本置 1
RES_TYPE_OK=0      # [c] ant.agent.type 是否通过
RES_ALL4_OK=0      # [c] 4 项 Resource 是否全部通过
RES_CHECKED=0      # [c] 是否真的跑过 Resource 检查（jq 可用 + 有 sample）

ok()    { echo "    [✓] $1"; PASS=$((PASS+1)); }
warn()  { echo "    [⚠] $1"; WARN=$((WARN+1)); }
fail()  { echo "    [✗] $1"; FAIL=$((FAIL+1)); }

echo ""
echo "=== doctor: $AGENT_DIR ==="

# (a) 目录骨架
echo ""
echo "[a] 目录骨架"
[ -d "$TRANSCRIPT_SAMPLE_DIR" ] && ok "$TRANSCRIPT_SAMPLE_DIR/" || { fail "$TRANSCRIPT_SAMPLE_DIR/ 缺失"; A_FAIL=1; }
[ -d "$REPLAY_DIR" ]     && ok "$REPLAY_DIR/"     || { fail "$REPLAY_DIR/ 缺失"; A_FAIL=1; }
[ -f "$AGENT_PATH/CAPABILITIES.md" ] && ok "$AGENT_DIR/CAPABILITIES.md" || { fail "$AGENT_DIR/CAPABILITIES.md 缺失"; A_FAIL=1; }
[ -f "$AGENT_PATH/Makefile" ]    && ok "$AGENT_DIR/Makefile"    || { fail "$AGENT_DIR/Makefile 缺失"; A_FAIL=1; }

# (b) Makefile 8 个标准 target
echo ""
echo "[b] Makefile 8 个标准 target"
if [ -f "$AGENT_PATH/Makefile" ]; then
    for t in build replay replay-all validate validate-one report report-shift clean; do
        if grep -qE "^${t}:" "$AGENT_PATH/Makefile"; then
            ok "$t"
        else
            warn "$t 未定义"
            B_FAIL=1
        fi
    done
else
    fail "Makefile 不存在，无法检查 target"
    B_FAIL=1
fi

# (c) Resource 必填 4 项
echo ""
echo "[c] Resource 必填 4 项 (sample 取 1 个 replay)"
SAMPLE=$(ls "$REPLAY_DIR/"spans_*.jsonl 2>/dev/null | head -1 || true)
if [ -z "$SAMPLE" ]; then
    warn "$REPLAY_DIR/ 中无 spans_*.jsonl，无法验证 Resource (建议先 make replay-all)"
else
    if ! command -v jq >/dev/null 2>&1; then
        warn "jq 不可用，跳过 Resource 检查 (brew install jq 或 apt install jq)"
    else
        RES_CHECKED=1
        RES_PASS_COUNT=0
        for attr in ant.agent.env ant.agent.scene ant.username ant.agent.type; do
            val=$(head -1 "$SAMPLE" | jq -r --arg k "$attr" \
                '.resourceSpans[0].resource.attributes[]? | select(.key==$k) | (.value.stringValue // .value.intValue // .value.boolValue // "")' \
                2>/dev/null || true)
            if [ -n "$val" ] && [ "$val" != "null" ]; then
                ok "$attr = $val"
                RES_PASS_COUNT=$((RES_PASS_COUNT+1))
                if [ "$attr" = "ant.agent.type" ]; then
                    RES_TYPE_OK=1
                fi
            else
                warn "$attr 缺失或为空 (强制约束，参见 METHODOLOGY §2.2)"
            fi
        done
        if [ "$RES_PASS_COUNT" -eq 4 ]; then
            RES_ALL4_OK=1
        fi
    fi
fi

# (d) e2e 证据
echo ""
echo "[d] e2e 证据 (transcripts + replays 样本)"
TS_COUNT=$(ls "$TRANSCRIPT_SAMPLE_DIR/"*.jsonl 2>/dev/null | wc -l | tr -d ' ')
REP_COUNT=$(ls "$REPLAY_DIR/"spans_*.jsonl 2>/dev/null | wc -l | tr -d ' ')
[ "$TS_COUNT" -ge 1 ] && ok "transcripts: $TS_COUNT 份样本" || { warn "transcripts: 0 份 (建议至少 1 份)"; D_FAIL=1; }
[ "$REP_COUNT" -ge 1 ] && ok "replays: $REP_COUNT 份样本"     || { warn "replays: 0 份 (执行 make replay-all 生成)"; D_FAIL=1; }

# (d-extra) replay 样本可被 jq 解析为 OTLP
if [ -n "$SAMPLE" ] && command -v jq >/dev/null 2>&1; then
    if head -1 "$SAMPLE" | jq -e '.resourceSpans' >/dev/null 2>&1; then
        ok "$(basename $SAMPLE) 是合法 OTLP JSONL"
    else
        fail "$(basename $SAMPLE) 不是合法 OTLP JSONL (jq 解析失败)"
        D_FAIL=1
    fi
fi

# 汇总
echo ""
echo "Summary: $PASS PASS / $WARN WARN / $FAIL FAIL"

# L 级估算（仅基于 doctor 4 项检查，参见 CONSTITUTION §3.7）
# 规则：
#   [a/b/d] 全过 + [c] 4 项全通过        = L0-complete
#   [a/b/d] 全过 + [c] ant.agent.type 通过 = L0-skeleton
#   [a/b/d] 任一失败 或 [c] 全空           = L0-incomplete
ABD_OK=1
if [ "$A_FAIL" -ne 0 ] || [ "$B_FAIL" -ne 0 ] || [ "$D_FAIL" -ne 0 ]; then
    ABD_OK=0
fi

if [ "$ABD_OK" -eq 1 ] && [ "$RES_ALL4_OK" -eq 1 ]; then
    L_LEVEL="L0-complete"
elif [ "$ABD_OK" -eq 1 ] && [ "$RES_TYPE_OK" -eq 1 ]; then
    L_LEVEL="L0-skeleton"
else
    L_LEVEL="L0-incomplete"
fi

printf 'L 级估算: %s（基于 doctor 4 项检查；完整 L 级评估见 %s/CAPABILITIES.md 末尾的"L0-L3 合规等级自评"）\n' "$L_LEVEL" "$AGENT_DIR"

if [ $FAIL -eq 0 ]; then
    echo "Result: PASS (warning 不影响通过)"
    exit 0
else
    echo "Result: FAIL"
    exit 1
fi
