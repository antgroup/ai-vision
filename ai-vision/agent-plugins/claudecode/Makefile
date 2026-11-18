# claudecode - AI Agent Observability Makefile
#
# Pipeline:
#   make replay-all                              Convert all transcripts to replays
#   make replay TS=xxx.jsonl                     Convert one canonical transcript
#   make validate                                Validate all replays
#   make validate-one FILE=$$HOME/.aivision/cc/replays/xxx.jsonl
#   make report TS=xxx.jsonl                     Report transcript to Langfuse
#   make report OTEL_JSON=$$HOME/.aivision/cc/replays/xxx.jsonl
#
# Build:
#   make build                                   Build cchooks (debug)
#   make build-edition EDITION=aivision          Build specific edition
#   make build-all                               Build all editions
#   make release                            Build all editions x all platforms
#   make install EDITION=aivision                Install edition locally
#   make test                                    Run tests
#
# Tools:
#   make transcripts / replays / list-artifacts / clean / help

AGENT         := claudecode
override AIVISION_HOME := $(or $(strip $(AIVISION_HOME)),$(HOME)/.aivision)
override ARTIFACT_DIR := $(or $(strip $(ARTIFACT_DIR)),$(AIVISION_HOME)/cc)
override TRANSCRIPT_DIR := $(or $(strip $(TRANSCRIPT_DIR)),$(ARTIFACT_DIR)/transcripts)
override REPLAY_DIR := $(or $(strip $(REPLAY_DIR)),$(ARTIFACT_DIR)/replays)
CCHOOKS_TARGET_DIR := cchooks/target
WEAVER_GUARD_TARGET_DIR := ../weaver/weaver-guard/target
REPLAY_BIN    := $(CCHOOKS_TARGET_DIR)/debug/cchooks-replay
REPORT_BIN    := $(CCHOOKS_TARGET_DIR)/debug/cchooks
WEAVER_GUARD  := $(WEAVER_GUARD_TARGET_DIR)/debug/weaver-guard
SEMCONV_BASE  := ../docs/semconv
SEMCONV_AGENT := $(wildcard ../docs/semconv-$(AGENT))
POLICIES      := ../weaver/policies
# Short aliases: TS=session_xxx.jsonl → TRANSCRIPT=$(TRANSCRIPT_DIR)/session_xxx.jsonl
# TS starting with / or ./ is treated as a direct path (absolute or relative)
ifdef TS
ifneq ($(filter /% ./%,$(TS)),)
TRANSCRIPT := $(TS)
else
TRANSCRIPT := $(TRANSCRIPT_DIR)/$(TS)
endif
endif
# Short alias: ED=aivision → EDITION=aivision
ifdef ED
EDITION := $(ED)
endif
FORMAT        ?= text
DIST_DIR      := ../releases/claudecode
VERSION       ?= $(or $(V),$(shell grep '^version' cchooks/Cargo.toml | head -1 | sed 's/.*"\(.*\)".*/\1/'))

override GOLDEN_DIR := $(or $(strip $(GOLDEN_DIR)),$(ARTIFACT_DIR)/golden)
GOLDEN_CONFIG := ../weaver/golden-compare.toml

TRANSCRIPTS   := $(filter-out %.timing.jsonl,$(wildcard $(TRANSCRIPT_DIR)/*.jsonl))
REPLAYS       := $(patsubst $(TRANSCRIPT_DIR)/%.jsonl,$(REPLAY_DIR)/spans_%.jsonl,$(TRANSCRIPTS))

.PHONY: replay-all replay validate validate-one report report-shift \
        golden-check golden-update golden-init \
        build build-edition build-all release install \
        test clean transcripts replays list-artifacts help

# ── Replay ─────────────────────────────────────────────────────────

$(REPLAY_DIR)/spans_%.jsonl: $(TRANSCRIPT_DIR)/%.jsonl $(REPLAY_BIN)
	@mkdir -p "$(REPLAY_DIR)"
	$(REPLAY_BIN) $< --otel $@

replay-all: $(REPLAYS)
	@echo "Generated $(words $(REPLAYS)) replay file(s) from $(words $(TRANSCRIPTS)) transcript(s)"

replay:
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make replay TRANSCRIPT=<file-or-dir>"; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	@if [ -d "$(TRANSCRIPT)" ]; then \
		count=0; \
		for f in "$(TRANSCRIPT)"/*.jsonl; do \
			[ -f "$$f" ] || continue; \
			out="$(REPLAY_DIR)/spans_$$(basename "$$f")"; \
			echo "Replay: $$f -> $$out"; \
			$(REPLAY_BIN) "$$f" --otel "$$out"; \
			count=$$((count + 1)); \
		done; \
		echo "Replayed $$count file(s) from $(TRANSCRIPT)"; \
	else \
		out="$(REPLAY_DIR)/spans_$$(basename "$(TRANSCRIPT)")"; \
		$(REPLAY_BIN) "$(TRANSCRIPT)" --otel "$$out"; \
	fi

# ── Validate ───────────────────────────────────────────────────────

validate: replay-all $(WEAVER_GUARD)
	@if ls "$(REPLAY_DIR)"/*.jsonl >/dev/null 2>&1; then \
		$(WEAVER_GUARD) validate "$(REPLAY_DIR)" \
			--policies-dir $(POLICIES) \
			--semconv-dir $(SEMCONV_BASE) \
			$(if $(SEMCONV_AGENT),--semconv-dir $(SEMCONV_AGENT)) \
			--format $(FORMAT); \
	else \
		echo "[$(AGENT)] No replay files to validate."; \
	fi

validate-one:
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make validate-one FILE=$(REPLAY_DIR)/spans_xxx.jsonl"; exit 1; fi
	$(WEAVER_GUARD) validate $(FILE) \
		--policies-dir $(POLICIES) \
		--semconv-dir $(SEMCONV_BASE) \
		$(if $(SEMCONV_AGENT),--semconv-dir $(SEMCONV_AGENT)) \
		--format $(FORMAT)

# ── Report ─────────────────────────────────────────────────────────

report: $(REPORT_BIN)
	@if [ -z "$(TRANSCRIPT)" ] && [ -z "$(OTEL_JSON)" ]; then \
		echo "Usage: make report TRANSCRIPT=<file> | OTEL_JSON=<file> [CONFIG=path] [PROFILE=name]"; exit 1; fi
	@if [ -n "$(TRANSCRIPT)" ] && [ -n "$(OTEL_JSON)" ]; then \
		echo "Error: TRANSCRIPT and OTEL_JSON are mutually exclusive"; exit 1; fi
	$(REPORT_BIN) report \
		$(if $(TRANSCRIPT),--session-path $(TRANSCRIPT)) \
		$(if $(OTEL_JSON),--otel-json $(OTEL_JSON)) \
		--config $(or $(CONFIG),$(wildcard report.config.toml),../report.config.toml) \
		$(if $(PROFILE),--profile $(PROFILE)) \
		$(if $(DEBUG),--debug)

# ── Report-shift (replay → otel-json report) ─────────────────────

report-shift: $(REPORT_BIN) $(REPLAY_BIN)
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make report-shift TRANSCRIPT=<file-or-dir> [CONFIG=path] [PROFILE=name] [DEBUG=1]"; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	@_run_one() { \
		_replay="$(REPLAY_DIR)/spans_$$(basename "$$1")"; \
		echo "[report-shift] Step 1/2: Replay -> $$_replay"; \
		$(REPLAY_BIN) "$$1" --otel "$$_replay" && \
		echo "[report-shift] Step 2/2: POST $$_replay" && \
		$(REPORT_BIN) report \
			--otel-json "$$_replay" \
			--shift \
			--config $(or $(CONFIG),$(wildcard report.config.toml),../report.config.toml) \
			$(if $(PROFILE),--profile $(PROFILE)) \
			$(if $(DEBUG),--debug); \
	}; \
	if [ -d "$(TRANSCRIPT)" ]; then \
		for f in "$(TRANSCRIPT)"/*.jsonl; do \
			[ -f "$$f" ] || continue; \
			_run_one "$$f"; \
		done; \
	else \
		_run_one "$(TRANSCRIPT)"; \
	fi

# ── Golden ─────────────────────────────────────────────────────────

golden-check: replay-all
	@if [ ! -d "$(GOLDEN_DIR)" ] || [ -z "$$(ls -A $(GOLDEN_DIR) 2>/dev/null)" ]; then \
		echo "No golden examples in $(GOLDEN_DIR)/. Run 'make golden-init' first."; exit 1; fi
	$(WEAVER_GUARD) compare "$(GOLDEN_DIR)" --replay "$(REPLAY_DIR)" --config $(GOLDEN_CONFIG)

golden-update: validate
	@mkdir -p $(GOLDEN_DIR)
	@echo "Updating golden examples from $(REPLAY_DIR)/..."
	@for f in "$(REPLAY_DIR)"/spans_*.jsonl; do \
		base=$$(basename "$$f"); \
		if [ -f "$(GOLDEN_DIR)/$$base" ]; then \
			cp "$$f" "$(GOLDEN_DIR)/$$base"; \
		fi; \
	done
	@echo "Golden examples updated. Review with: git diff $(GOLDEN_DIR)/"

golden-init: replay-all
	@mkdir -p $(GOLDEN_DIR)
	@echo "Available replay files:"
	@ls -1 "$(REPLAY_DIR)"/spans_*.jsonl 2>/dev/null || echo "  (none)"
	@echo ""
	@echo "Copy desired files to $(GOLDEN_DIR)/ manually, then commit."
	@echo "  cp $(REPLAY_DIR)/spans_xxx.jsonl $(GOLDEN_DIR)/"

# ── Build ──────────────────────────────────────────────────────────

build:
	cd cchooks && CARGO_TARGET_DIR=target cargo build

build-edition:
	@if [ -z "$(EDITION)" ]; then \
		echo "Usage: make build-edition EDITION=aivision|common"; exit 1; fi
	$(MAKE) -C cchooks $(EDITION) $(if $(VERSION),VERSION=$(VERSION)) $(if $(TARGETS),TARGETS=$(TARGETS))

build-all:
	$(MAKE) -C cchooks all $(if $(VERSION),VERSION=$(VERSION)) $(if $(TARGETS),TARGETS=$(TARGETS))

release:
	$(MAKE) -C cchooks release $(if $(VERSION),VERSION=$(VERSION))

install:
	@if [ -z "$(EDITION)" ]; then \
		echo "Usage: make install EDITION=aivision|common"; exit 1; fi
	$(MAKE) -C cchooks install-$(EDITION) $(if $(VERSION),VERSION=$(VERSION))

test:
	cd cchooks && CARGO_TARGET_DIR=target cargo test

# Always rebuild — cargo is incremental, near-instant when nothing changed.
# Without .PHONY, stale binaries from previous builds would be used silently.
.PHONY: ensure-build
ensure-build:
	cd cchooks && CARGO_TARGET_DIR=target cargo build

$(REPLAY_BIN): ensure-build
$(REPORT_BIN): ensure-build

$(WEAVER_GUARD):
	cd ../weaver/weaver-guard && CARGO_TARGET_DIR=target cargo build

# ── File listing ───────────────────────────────────────────────────

transcripts:
	@echo "Transcripts:"
	@ls -lh "$(TRANSCRIPT_DIR)"/*.jsonl 2>/dev/null || echo "  (none)"

replays:
	@echo "Replays:"
	@ls -lh "$(REPLAY_DIR)"/spans_*.jsonl 2>/dev/null || echo "  (none)"

list-artifacts:
	@echo "Artifacts (claudecode):"
	@found=0; \
	for sub in aivision ant common; do \
		dir="$(DIST_DIR)/$$sub/$(VERSION)"; \
		if [ -d "$$dir" ]; then \
			echo ""; echo "  releases/claudecode/$$sub/$(VERSION)/"; \
			ls -1h "$$dir" | sed 's/^/    /'; \
			found=1; \
		fi; \
	done; \
	if [ "$$found" = "0" ]; then echo "  (none)"; fi

# ── Cleanup ────────────────────────────────────────────────────────

clean:
	rm -f "$(REPLAY_DIR)"/spans_*.jsonl
	@echo "Cleaned replay files"

# ── Help ───────────────────────────────────────────────────────────

help:
	@echo "claudecode Observability Makefile"
	@echo ""
	@echo "  Pipeline:"
	@echo "  make replay-all              Convert all transcripts to OTLP replays"
	@echo "  make replay TRANSCRIPT=<f>   Convert one transcript (file or directory)"
	@echo "  make validate                Validate all replays with weaver-guard"
	@echo "  make validate-one FILE=<f>   Validate one replay"
	@echo "  make report TRANSCRIPT=<f>   Report transcript to Langfuse via HTTP"
	@echo "  make report OTEL_JSON=<f>    Report OTLP replay to Langfuse (mutually exclusive with TRANSCRIPT)"
	@echo "    CONFIG=<path>              Config file (default: report.config.toml)"
	@echo "    PROFILE=<name>             Profile in config file"
	@echo ""
	@echo "  Build:"
	@echo "  make build                   Build cchooks (debug)"
	@echo "  make build-edition EDITION=  Build edition (aivision|common)"
	@echo "  make build-all               Build all editions"
	@echo "  make release           Build all editions x all platforms"
	@echo "  make install EDITION=        Install edition locally"
	@echo "  make test                    Run tests"
	@echo ""
	@echo "  Golden:"
	@echo "  make golden-check            Compare replays against golden baselines"
	@echo "  make golden-update           Update golden from current replays"
	@echo "  make golden-init             List replays available for golden init"
	@echo ""
	@echo "  Tools:"
	@echo "  make transcripts             List transcript files"
	@echo "  make replays                 List replay files"
	@echo "  make list-artifacts          List build artifacts"
	@echo "  make clean                   Remove generated replays"
	@echo ""
	@echo "  FORMAT=text|json             Output format (default: text)"
