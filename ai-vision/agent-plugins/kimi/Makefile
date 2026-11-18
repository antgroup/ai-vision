# kimi - AI Agent Observability Makefile
#
# Pipeline:
#   make build                                   Build kimihooks + kimihooks-replay (debug, common edition)
#   make build-common                            Release build, common edition (env-driven endpoints)
#   make build-aivision                          Release build, aivision edition (embeds aivision.keys.json)
#   make replay-all                              Convert all hook_log NDJSON to OTLP replays
#                                                (writes spans_*.jsonl + logs_*.jsonl + metrics_*.jsonl)
#   make replay HL=foo.ndjson                    Convert one canonical transcript (3 signals)
#   make validate                                Validate all replays via weaver-guard
#                                                (traces + logs + metrics together)
#   make validate-one FILE=$$HOME/.aivision/kimi/replays/spans_xxx.jsonl
#   make transcripts                             List collected hook_log files
#   make replays                                 List generated OTLP replay files
#   make clean                                   Remove all replays
#   make help                                    Show this help

AGENT          := kimicli
override AIVISION_HOME := $(or $(strip $(AIVISION_HOME)),$(HOME)/.aivision)
override ARTIFACT_DIR := $(or $(strip $(ARTIFACT_DIR)),$(AIVISION_HOME)/kimi)
override TRANSCRIPT_DIR := $(or $(strip $(TRANSCRIPT_DIR)),$(ARTIFACT_DIR)/transcripts)
override REPLAY_DIR := $(or $(strip $(REPLAY_DIR)),$(ARTIFACT_DIR)/replays)
override GOLDEN_DIR := $(or $(strip $(GOLDEN_DIR)),$(ARTIFACT_DIR)/golden)
HOOKS_BIN      := kimihooks/target/debug/kimihooks
REPLAY_BIN     := kimihooks/target/debug/kimihooks-replay
WEAVER_GUARD   := ../weaver/weaver-guard/target/debug/weaver-guard
SEMCONV_BASE   := ../docs/semconv
SEMCONV_AGENT  := $(wildcard ../docs/semconv-$(AGENT))
POLICIES       := ../weaver/policies

# Short alias: HL=foo.ndjson → HOOK_LOG=transcripts/foo.ndjson
ifdef HL
ifneq ($(filter /% ./%,$(HL)),)
HOOK_LOG := $(HL)
else
HOOK_LOG := $(TRANSCRIPT_DIR)/$(HL)
endif
endif

# TS=wire_xxx.jsonl → TRANSCRIPT=transcripts/wire_xxx.jsonl (根 Makefile 转发)
ifdef TS
ifneq ($(filter /% ./%,$(TS)),)
TRANSCRIPT := $(TS)
else
TRANSCRIPT := $(TRANSCRIPT_DIR)/$(TS)
endif
endif

FORMAT         ?= text

# 自动发现 transcripts 目录下所有 transcript 文件
# 支持三种格式：*.ndjson（hook_log）、wire_*.jsonl（旧 wire）、*.wire.jsonl（新 wire）
TRANSCRIPTS_NDJSON := $(wildcard $(TRANSCRIPT_DIR)/*.ndjson)
TRANSCRIPTS_WIRE   := $(wildcard $(TRANSCRIPT_DIR)/wire_*.jsonl) $(wildcard $(TRANSCRIPT_DIR)/*.wire.jsonl)
TRANSCRIPTS        := $(TRANSCRIPTS_NDJSON) $(TRANSCRIPTS_WIRE)

# ndjson → replay 产物映射
SPANS_NDJSON   := $(patsubst $(TRANSCRIPT_DIR)/%.ndjson,$(REPLAY_DIR)/spans_%.jsonl,$(TRANSCRIPTS_NDJSON))
LOGS_NDJSON    := $(patsubst $(TRANSCRIPT_DIR)/%.ndjson,$(REPLAY_DIR)/logs_%.jsonl,$(TRANSCRIPTS_NDJSON))
METRICS_NDJSON := $(patsubst $(TRANSCRIPT_DIR)/%.ndjson,$(REPLAY_DIR)/metrics_%.jsonl,$(TRANSCRIPTS_NDJSON))

# wire → replay 产物映射（wire_*.jsonl 和 *.wire.jsonl 统一处理）
SPANS_WIRE     := $(patsubst $(TRANSCRIPT_DIR)/%.jsonl,$(REPLAY_DIR)/spans_%.jsonl,$(TRANSCRIPTS_WIRE))
LOGS_WIRE      := $(patsubst $(TRANSCRIPT_DIR)/%.jsonl,$(REPLAY_DIR)/logs_%.jsonl,$(TRANSCRIPTS_WIRE))
METRICS_WIRE   := $(patsubst $(TRANSCRIPT_DIR)/%.jsonl,$(REPLAY_DIR)/metrics_%.jsonl,$(TRANSCRIPTS_WIRE))

SPANS_FILES    := $(SPANS_NDJSON) $(SPANS_WIRE)
LOGS_FILES     := $(LOGS_NDJSON) $(LOGS_WIRE)
METRICS_FILES  := $(METRICS_NDJSON) $(METRICS_WIRE)
REPLAYS        := $(SPANS_FILES) $(LOGS_FILES) $(METRICS_FILES)

.PHONY: build build-common build-aivision release \
        install install-aivision install-common uninstall test \
        replay-all replay validate validate-one report-shift \
        transcripts replays clean help

# ── Build ──────────────────────────────────────────────────────────
# 两种 edition（编译时切换）：
#   build / build-common      → common edition（无内置 keys，靠 KIMICLI_OTLP_* 环境变量）
#   build-aivision            → aivision edition（编译时把 ./kimihooks/aivision.keys.json
#                               的 endpoints + auth + headers 内嵌进二进制）
# 仅 release 构建是 aivision/common 真正区分目标；debug 构建沿用 common 即可。

build: $(HOOKS_BIN) $(REPLAY_BIN)
	@echo "kimihooks built (debug, edition=common)."

$(HOOKS_BIN) $(REPLAY_BIN):
	cd kimihooks && CARGO_TARGET_DIR=target cargo build --bins

build-common:
	cd kimihooks && CARGO_TARGET_DIR=target cargo build --release --no-default-features --bins
	@echo "kimihooks built (release, edition=common). Run with KIMICLI_OTLP_*_ENDPOINT env vars."

build-aivision:
	@if [ ! -f kimihooks/aivision.keys.json ]; then \
		echo "ERROR: kimihooks/aivision.keys.json not found, required for aivision edition"; exit 1; fi
	cd kimihooks && CARGO_TARGET_DIR=target cargo build --release --no-default-features --features aivision --bins
	@echo "kimihooks built (release, edition=aivision). Endpoints embedded; env vars still override."

release:
	$(MAKE) -C kimihooks release

# ── Install ────────────────────────────────────────────────────────
# 安装 release 二进制 + 默认配置到 edition 特定目录：
#   common:   ~/.kimi/hooks/kimihooks/{hooks,config,logs,state}
#   aivision: ~/.aivision/kimi/{hooks,config,logs,state}

INSTALL_ROOT_COMMON   := $(HOME)/.kimi/hooks/kimihooks
INSTALL_ROOT_AIVISION := $(AIVISION_HOME)/kimi

# 通用 install dispatcher：根 Makefile 只会原样转发 `install` target 名，
# 这里根据 ED= / EDITION= 参数翻译到具体 install-* target。
# 用法：
#   make install ED=aivision        （在 kimi/ 目录直接跑）
#   make install km ED=aivision     （在仓库根跑，根 Makefile 转发）
install:
	@ed=""; \
	if [ -n "$(ED)" ];      then ed="$(ED)"; \
	elif [ -n "$(EDITION)" ]; then ed="$(EDITION)"; \
	else \
		echo "Usage: make install ED=aivision|common"; exit 1; fi; \
	case "$$ed" in \
		aivision|common) ;; \
		*) echo "Unknown edition '$$ed'. Supported: aivision, common"; exit 1 ;; \
	esac; \
	$(MAKE) install-$$ed

install-aivision: build-aivision
	@mkdir -p $(INSTALL_ROOT_AIVISION)/{hooks,config,logs,state}
	install -m 0755 kimihooks/target/release/kimihooks         $(INSTALL_ROOT_AIVISION)/hooks/kimihooks
	install -m 0755 kimihooks/target/release/kimihooks-replay  $(INSTALL_ROOT_AIVISION)/hooks/kimihooks-replay
	install -m 0644 kimihooks/configs/aivision.config.json     $(INSTALL_ROOT_AIVISION)/config/config.json
	@echo ""
	@echo "Installed aivision edition:"
	@echo "  $(INSTALL_ROOT_AIVISION)/hooks/kimihooks"
	@echo "  $(INSTALL_ROOT_AIVISION)/hooks/kimihooks-replay"
	@echo "  $(INSTALL_ROOT_AIVISION)/config/config.json"
	@echo ""
	@echo "Verify edition + endpoints:"
	@echo "  $(INSTALL_ROOT_AIVISION)/hooks/kimihooks-replay /dev/null --otel /tmp/_t.jsonl 2>&1 | head -1"
	@echo ""
	@echo "─────────────────────────────────────────────────────────────"
	@echo " Append the following block to  ~/.kimi/config.toml"
	@echo " (Stop hook is the only required event; it reconstructs the full turn topology)"
	@echo "─────────────────────────────────────────────────────────────"
	@echo ""
	@echo "[[hooks]]"
	@echo "event = \"Stop\""
	@echo "command = \"$(INSTALL_ROOT_AIVISION)/hooks/kimihooks\""
	@echo "matcher = \"\""
	@echo ""
	@echo "[[hooks]]"
	@echo "event = \"StopFailure\""
	@echo "command = \"$(INSTALL_ROOT_AIVISION)/hooks/kimihooks\""
	@echo "matcher = \"\""
	@echo ""
	@echo "─────────────────────────────────────────────────────────────"
	@echo " Test:  start a kimi session, send a message, exit"
	@echo " Verify:  tail -f $(INSTALL_ROOT_AIVISION)/logs/kimihooks.log"
	@echo "          Look for:  [http] traces POST https://... -> 200"
	@echo "─────────────────────────────────────────────────────────────"

install-common: build-common
	@mkdir -p $(INSTALL_ROOT_COMMON)/{hooks,config,logs,state}
	install -m 0755 kimihooks/target/release/kimihooks         $(INSTALL_ROOT_COMMON)/hooks/kimihooks
	install -m 0755 kimihooks/target/release/kimihooks-replay  $(INSTALL_ROOT_COMMON)/hooks/kimihooks-replay
	install -m 0644 kimihooks/configs/common.config.json        $(INSTALL_ROOT_COMMON)/config/config.json
	@echo "Installed common edition to $(INSTALL_ROOT_COMMON)/"
	@echo "Set KIMI_OTLP_*_ENDPOINT env vars before running."

uninstall:
	rm -f $(INSTALL_ROOT_COMMON)/hooks/kimihooks $(INSTALL_ROOT_COMMON)/hooks/kimihooks-replay
	rm -f $(INSTALL_ROOT_AIVISION)/hooks/kimihooks $(INSTALL_ROOT_AIVISION)/hooks/kimihooks-replay
	@echo "Removed kimihooks from install directories"

test:
	cd kimihooks && CARGO_TARGET_DIR=target cargo test

# ── Replay ─────────────────────────────────────────────────────────
# 一次 replay 同时产出三种信号文件：
#   replays/spans_<base>.jsonl    — OTLP traces
#   replays/logs_<base>.jsonl     — OTLP logs（ant.* events）
#   replays/metrics_<base>.jsonl  — OTLP metrics（agent.* + aivision.agentic.*）

# 任一目标文件触发都同时产出三个文件（用 spans 作为代表 target；其余两个由 sentinel 共享）
# hook_log ndjson 格式（旧）
$(REPLAY_DIR)/spans_%.jsonl $(REPLAY_DIR)/logs_%.jsonl $(REPLAY_DIR)/metrics_%.jsonl: $(TRANSCRIPT_DIR)/%.ndjson $(REPLAY_BIN)
	@mkdir -p "$(REPLAY_DIR)"
	$(REPLAY_BIN) $< \
	  --otel    "$(REPLAY_DIR)/spans_$*.jsonl" \
	  --logs    "$(REPLAY_DIR)/logs_$*.jsonl" \
	  --metrics "$(REPLAY_DIR)/metrics_$*.jsonl"

# wire_*.jsonl 格式（新，kimi-cli 原生 wire 协议）
$(REPLAY_DIR)/spans_%.jsonl $(REPLAY_DIR)/logs_%.jsonl $(REPLAY_DIR)/metrics_%.jsonl: $(TRANSCRIPT_DIR)/%.jsonl $(REPLAY_BIN)
	@mkdir -p "$(REPLAY_DIR)"
	$(REPLAY_BIN) $< \
	  --otel    "$(REPLAY_DIR)/spans_$*.jsonl" \
	  --logs    "$(REPLAY_DIR)/logs_$*.jsonl" \
	  --metrics "$(REPLAY_DIR)/metrics_$*.jsonl"

replay-all: $(SPANS_FILES) $(LOGS_FILES) $(METRICS_FILES)
	@echo "Generated $(words $(SPANS_FILES)) traces + $(words $(LOGS_FILES)) logs + $(words $(METRICS_FILES)) metrics file(s) from $(words $(TRANSCRIPTS)) transcript(s)"

replay: $(REPLAY_BIN)
	@if [ -z "$(HOOK_LOG)" ]; then \
		echo "Usage: make replay HOOK_LOG=<file-or-dir>"; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	@if [ -d "$(HOOK_LOG)" ]; then \
		count=0; \
		for f in "$(HOOK_LOG)"/*.ndjson; do \
			[ -f "$$f" ] || continue; \
			base=$$(basename "$$f" .ndjson); \
			echo "Replay: $$f -> spans_$$base.jsonl + logs_$$base.jsonl + metrics_$$base.jsonl"; \
			$(REPLAY_BIN) "$$f" \
			  --otel    "$(REPLAY_DIR)/spans_$$base.jsonl" \
			  --logs    "$(REPLAY_DIR)/logs_$$base.jsonl" \
			  --metrics "$(REPLAY_DIR)/metrics_$$base.jsonl"; \
			count=$$((count + 1)); \
		done; \
		echo "Replayed $$count file(s) from $(HOOK_LOG) (3 signals each)"; \
	else \
		base=$$(basename "$(HOOK_LOG)" .ndjson); \
		$(REPLAY_BIN) "$(HOOK_LOG)" \
		  --otel    "$(REPLAY_DIR)/spans_$$base.jsonl" \
		  --logs    "$(REPLAY_DIR)/logs_$$base.jsonl" \
		  --metrics "$(REPLAY_DIR)/metrics_$$base.jsonl"; \
	fi

# ── Validate ───────────────────────────────────────────────────────

validate: replay-all
	@if [ ! -x "$(WEAVER_GUARD)" ]; then \
		echo "weaver-guard 未编译, 请先 cd ../weaver/weaver-guard && cargo build"; exit 1; fi
	$(WEAVER_GUARD) validate "$(REPLAY_DIR)" \
		--policies-dir $(POLICIES) \
		--semconv-dir $(SEMCONV_BASE) \
		$(if $(SEMCONV_AGENT),--semconv-dir $(SEMCONV_AGENT)) \
		--format $(FORMAT)

validate-one:
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make validate-one FILE=$(REPLAY_DIR)/spans_xxx.jsonl"; exit 1; fi
	@if [ ! -x "$(WEAVER_GUARD)" ]; then \
		echo "weaver-guard 未编译, 请先 cd ../weaver/weaver-guard && cargo build"; exit 1; fi
	$(WEAVER_GUARD) validate $(FILE) \
		--policies-dir $(POLICIES) \
		--semconv-dir $(SEMCONV_BASE) \
		$(if $(SEMCONV_AGENT),--semconv-dir $(SEMCONV_AGENT)) \
		--format $(FORMAT)

# ── Report-shift (replay → HTTP 上报) ──────────────────────────────
# kimihooks-replay 内置 HTTP 上报，一步完成 replay + POST。
# 用法：
#   make report-shift TS=wire_xxx.jsonl
#   make report-shift TS=wire_xxx.jsonl          （短别名）

report-shift: $(REPLAY_BIN)
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make report-shift TRANSCRIPT=<file-or-dir> [TS=<short>] [CONFIG=path] [PROFILE=name]"; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	@_cfg="$(or $(CONFIG),$(wildcard report.config.toml),$(wildcard ../report.config.toml))"; \
	_run_one() { \
		_base=$$(basename "$$1"); \
		_stem="$${_base%.*}"; \
		echo "[report-shift] Replay + POST: $$1"; \
		$(REPLAY_BIN) "$$1" \
			--otel    "$(REPLAY_DIR)/spans_$${_stem}.jsonl" \
			--logs    "$(REPLAY_DIR)/logs_$${_stem}.jsonl" \
			--metrics "$(REPLAY_DIR)/metrics_$${_stem}.jsonl" \
			--shift \
			$${_cfg:+--config "$$_cfg"} \
			$(if $(PROFILE),--profile $(PROFILE)) && \
		echo "[report-shift] Done: $$1"; \
	}; \
	if [ -d "$(TRANSCRIPT)" ]; then \
		for f in "$(TRANSCRIPT)"/*.jsonl "$(TRANSCRIPT)"/*.ndjson; do \
			[ -f "$$f" ] || continue; \
			_run_one "$$f"; \
		done; \
	else \
		_run_one "$(TRANSCRIPT)"; \
	fi

# ── Inspect ────────────────────────────────────────────────────────

transcripts:
	@ls -1 "$(TRANSCRIPT_DIR)"/*.ndjson "$(TRANSCRIPT_DIR)"/wire_*.jsonl 2>/dev/null || echo "(no transcripts)"

replays:
	@ls -1 "$(REPLAY_DIR)"/*.jsonl 2>/dev/null || echo "(no replays)"

# ── Clean ──────────────────────────────────────────────────────────

clean:
	rm -f "$(REPLAY_DIR)"/spans_*.jsonl "$(REPLAY_DIR)"/logs_*.jsonl "$(REPLAY_DIR)"/metrics_*.jsonl
	@echo "Cleaned $(REPLAY_DIR)/ (traces + logs + metrics)"

# ── Help ───────────────────────────────────────────────────────────

help:
	@grep -E '^# ' Makefile | head -20 | sed 's/^# //'
