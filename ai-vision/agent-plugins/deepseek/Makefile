# dsh (DeepSeek Harness) observability Makefile
#
# Responsibility split:
#   observ-dsh — production cordis plugin. Real-time OTLP traces/metrics/events.
#                NO transcript writing, NO replay capture.
#   dsh-replay — developer CLI. Reads the host-maintained dsh canonical session
#                JSONL transcript and rebuilds OTLP JSONL offline.
#
# dsh is a mixed family: plugin-shaped production API + binary-style replay
# input (the transcript is host-owned, not captured by a replay plugin).

AGENT := dsh
override AIVISION_HOME := $(or $(strip $(AIVISION_HOME)),$(HOME)/.aivision)
override ARTIFACT_DIR := $(or $(strip $(ARTIFACT_DIR)),$(AIVISION_HOME)/dsh)
TELEMETRY_PKG := observ-dsh
RECORD_PKG := dsh-replay
FORMAT ?= text
override REPLAY_DIR := $(or $(strip $(REPLAY_DIR)),$(ARTIFACT_DIR)/replays)
override GOLDEN_DIR := $(or $(strip $(GOLDEN_DIR)),$(ARTIFACT_DIR)/golden)
DSH_PROFILES ?= web tui
WEAVER_URL ?= http://localhost:4319/

# Replay inputs are dsh session artifacts: either session.jsonl(.zstd) files or
# session directories. Fixtures are the checked-in developer corpus.
TRANSCRIPT ?=
override TRANSCRIPT_DIR := $(or $(strip $(TRANSCRIPT_DIR)),$(ARTIFACT_DIR)/transcripts)
FIXTURES := $(RECORD_PKG)/tests/fixtures/session-fixture-001.jsonl \
            $(RECORD_PKG)/tests/fixtures/session-fixture-001.jsonl.zstd
HOST_TRANSCRIPTS := $(wildcard $(TRANSCRIPT_DIR)/*/session.jsonl $(TRANSCRIPT_DIR)/*/session.jsonl.zstd)

WEAVER_GUARD_TARGET_DIR := $(shell cargo metadata --manifest-path ../weaver/weaver-guard/Cargo.toml --no-deps --format-version 1 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])' 2>/dev/null || printf '../weaver/weaver-guard/target')
WEAVER_GUARD := $(WEAVER_GUARD_TARGET_DIR)/debug/weaver-guard
POLICIES := ../weaver/policies
SEMCONV_BASE := ../docs/semconv

.PHONY: build build-observ build-replay test test-release typecheck replay replay-all validate validate-one \
        report report-shift clean install uninstall release golden-init golden-check golden-update \
        transcripts replays list-artifacts help

# dsh-replay depends on observ-dsh via the workspace; build observ-dsh first.
build: build-replay

build-observ:
	cd $(TELEMETRY_PKG) && npm run build

build-replay: build-observ
	cd $(RECORD_PKG) && npm run build

test: build
	cd $(TELEMETRY_PKG) && npm test
	cd $(RECORD_PKG) && npm test
	$(MAKE) test-release

test-release: build-observ
	node --test scripts/release.test.mjs

typecheck:
	cd $(TELEMETRY_PKG) && npm run typecheck
	cd $(RECORD_PKG) && npm run typecheck

# Register observ-dsh into each profile in DSH_PROFILES: link the plugin
# package (`dsh plugin add`) and install the managed cordis.patch.yml block
# (disable native session-telemetry-otel + insert observ-dsh) via
# scripts/install-profile.mjs. Idempotent; rerun after config changes.
install: build-observ
	@command -v dsh >/dev/null 2>&1 || { echo "dsh CLI not found" >&2; exit 1; }
	@for profile in $(DSH_PROFILES); do \
		dsh plugin --profile $$profile add "$(abspath $(TELEMETRY_PKG))" || exit 1; \
		node scripts/install-profile.mjs --profile $$profile \
			--config-src "$(abspath $(TELEMETRY_PKG)/configs/aivision.config.json)" || exit 1; \
		echo "observ-dsh registered in profile '$$profile'."; \
	done
	@echo "Done. DSH_PROFILES=$(DSH_PROFILES)"
	@echo "Overrides: DSH_OBSERV_ENABLED=false / DSH_OBSERV_CONFIG / per-signal DSH_OBSERV_OTLP_*_ENDPOINT."

uninstall:
	@command -v dsh >/dev/null 2>&1 || { echo "dsh CLI not found" >&2; exit 1; }
	@for profile in $(DSH_PROFILES); do \
		node scripts/install-profile.mjs --profile $$profile --remove || exit 1; \
		dsh plugin --profile $$profile remove observ-dsh || true; \
		echo "Removed observ-dsh from profile '$$profile'."; \
	done

replay: build
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make replay TRANSCRIPT=path/to/session.jsonl[.zstd]"; exit 1; \
	fi
	@mkdir -p "$(REPLAY_DIR)"
	@base=$$(basename "$(abspath $(TRANSCRIPT))"); base=$${base%.zstd}; base=$${base%.jsonl}; \
	node $(RECORD_PKG)/dist/replay.js "$(abspath $(TRANSCRIPT))" --otel "$(REPLAY_DIR)/spans_session_$$base.jsonl"

# Replay the checked-in developer corpus. Host transcripts under
# TRANSCRIPT_DIR are appended when present.
replay-all: build
	@mkdir -p "$(REPLAY_DIR)"
	@count=0; for transcript in $(FIXTURES) $(HOST_TRANSCRIPTS); do \
		[ -f "$$transcript" ] || continue; \
		base=$$(basename "$$(dirname "$$transcript")")-$$(basename "$$transcript"); \
		base=$${base%.zstd}; base=$${base%.jsonl}; \
		node $(RECORD_PKG)/dist/replay.js "$$transcript" --otel "$(REPLAY_DIR)/spans_$$base.jsonl" || exit 1; \
		count=$$((count + 1)); \
	done; echo "[$(AGENT)] Replay complete: $$count transcript(s)."

$(WEAVER_GUARD):
	cargo build --manifest-path ../weaver/weaver-guard/Cargo.toml

validate: $(WEAVER_GUARD)
	@if ls "$(REPLAY_DIR)"/*.jsonl >/dev/null 2>&1; then \
		$(WEAVER_GUARD) validate "$(REPLAY_DIR)" --policies-dir $(POLICIES) --semconv-dir $(SEMCONV_BASE) --format $(FORMAT); \
	else echo "[$(AGENT)] No replay files to validate."; fi

validate-one: $(WEAVER_GUARD)
	@if [ -z "$(FILE)" ]; then echo "Usage: make validate-one FILE=$(REPLAY_DIR)/spans_x.jsonl"; exit 1; fi
	$(WEAVER_GUARD) validate "$(FILE)" --policies-dir $(POLICIES) --semconv-dir $(SEMCONV_BASE) --format $(FORMAT)

report:
	@if ! ls "$(REPLAY_DIR)"/*.jsonl >/dev/null 2>&1; then echo "[$(AGENT)] No replay files to report."; exit 1; fi
	python3 ../scripts/report_check.py --base-url "$(WEAVER_URL)" "$(REPLAY_DIR)"/*.jsonl

report-shift: report

golden-init: replay-all
	@mkdir -p "$(GOLDEN_DIR)/replays"
	@echo "[$(AGENT)] Golden candidates:"
	@ls -1 "$(REPLAY_DIR)"/spans_*.jsonl 2>/dev/null || true

golden-check: $(WEAVER_GUARD)
	@if [ ! -d "$(GOLDEN_DIR)/replays" ] || [ -z "$$(ls -A $(GOLDEN_DIR)/replays/*.jsonl 2>/dev/null)" ]; then \
		echo "[$(AGENT)] No golden replays in $(GOLDEN_DIR)/replays"; exit 1; fi
	$(WEAVER_GUARD) compare "$(GOLDEN_DIR)/replays" --replay "$(REPLAY_DIR)" --config ../weaver/golden-compare.toml --diff-only \
		|| echo "[$(AGENT)] Golden compare differs. If intended, run 'make golden-update'."

golden-update: validate
	@mkdir -p "$(GOLDEN_DIR)/replays"
	@echo "[$(AGENT)] Updating golden replays from $(REPLAY_DIR)/..."
	@for f in "$(REPLAY_DIR)"/spans_*.jsonl; do \
		base=$$(basename "$$f"); \
		cp "$$f" "$(GOLDEN_DIR)/replays/$$base"; \
	done
	@echo "[$(AGENT)] Golden replays updated."

clean:
	rm -rf $(TELEMETRY_PKG)/dist $(RECORD_PKG)/dist
	rm -f "$(REPLAY_DIR)"/spans_*.jsonl "$(REPLAY_DIR)"/events_*.jsonl "$(REPLAY_DIR)"/metrics_*.jsonl

transcripts:
	@echo "[$(AGENT)] dsh transcripts are host-maintained session artifacts:"
	@find $(TRANSCRIPT_DIR) -name 'session.jsonl*' -print 2>/dev/null || true
	@echo "[$(AGENT)] Checked-in fixtures:"
	@ls -1 $(FIXTURES) 2>/dev/null || true

replays:
	@find $(REPLAY_DIR) -maxdepth 1 -name '*.jsonl' -print 2>/dev/null || true

list-artifacts: transcripts replays

# Release packages only the production collection plugin (observ-dsh), one
# tarball per edition. dsh-replay is a developer-only CLI and is intentionally
# NOT shipped here. Layout matches sibling agents:
#   releases/dsh/<edition>/<version>/<pkg>-<version>.tgz
#   aivision -> observ-aivision-dsh-<version>.tgz
#   common   -> observ-dsh-<version>.tgz
EDITION ?= all
release: build-observ
	node scripts/release.mjs --edition "$(EDITION)"

help:
	@echo "dsh (DeepSeek Harness) Makefile targets:"
	@echo "  build / test / typecheck   build and test observ-dsh + dsh-replay"
	@echo "  test-release               package and validate both release editions"
	@echo "  install [DSH_PROFILES=\"web tui\"]  register observ-dsh into dsh profiles (plugin + cordis patch)"
	@echo "  uninstall                  remove observ-dsh from dsh profiles"
	@echo "  replay TRANSCRIPT=<file>   transcript -> OTLP replay"
	@echo "  replay-all                 replay checked-in fixtures + host corpus"
	@echo "  validate / validate-one    weaver-guard validate replay JSONL"
	@echo "  report / report-shift      POST replay JSONL to local weaver"
	@echo "  golden-*                   stable golden baseline helpers"
	@echo "  clean                      remove dist + replay artifacts"
