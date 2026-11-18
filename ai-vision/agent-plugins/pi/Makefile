# Piagent + Pireplay Makefile
#
# Responsibility split:
#   piagent  — real-time OTLP telemetry (library + extension). NO transcript, NO replay.
#   pireplay — canonical transcript recording + replay CLI + golden superloop + demo.
#
# All replay/demo/golden targets route to PKG=pireplay because it owns the
# transcript→OTLP pipeline. build/test cover both packages.

AGENT := pi
override AIVISION_HOME := $(or $(strip $(AIVISION_HOME)),$(HOME)/.aivision)
override ARTIFACT_DIR := $(or $(strip $(ARTIFACT_DIR)),$(AIVISION_HOME)/pi)
TELEMETRY_PKG := observ-pi
RECORD_PKG := pireplay
FORMAT ?= text
override TRANSCRIPT_DIR := $(or $(strip $(TRANSCRIPT_DIR)),$(ARTIFACT_DIR)/transcripts)
override REPLAY_DIR := $(or $(strip $(REPLAY_DIR)),$(ARTIFACT_DIR)/replays)
DEMO_DIR ?= demo-output
GOLDEN_RUN_DIR ?=
override WEAVER_URL := http://127.0.0.1:4319
LOCAL_OTLP_TRACES_ENDPOINT ?= http://localhost:4319/v1/traces
LOCAL_OTLP_METRICS_ENDPOINT ?= http://localhost:4319/v1/metrics
LOCAL_OTLP_LOGS_ENDPOINT ?= http://localhost:4319/v1/logs
PI_CLI ?= pi
PI_AGENT_DIR ?= $(shell cd $(TELEMETRY_PKG) && node --input-type=module -e 'import { getAgentDir } from "@earendil-works/pi-coding-agent"; process.stdout.write(getAgentDir())')
PI_CONFIG_FILE ?= $(PI_AGENT_DIR)/observ-pi.json
VERSION ?= $(shell node -p "require('./$(TELEMETRY_PKG)/package.json').version")
DIST_DIR := ../releases/pi
EDITION ?= common
EDITION_OUT_SUBDIR = $(if $(filter aivision,$(EDITION)),aivision,common)
EDITION_PKG_PREFIX = $(if $(filter aivision,$(EDITION)),observ-aivision-pi,observ-pi)
RELEASE_DIR = $(DIST_DIR)/$(EDITION_OUT_SUBDIR)/$(VERSION)
RELEASE_PACKAGE = $(EDITION_PKG_PREFIX)-$(VERSION).tgz
WEAVER_GUARD_TARGET_DIR := $(shell cargo metadata --manifest-path ../weaver/weaver-guard/Cargo.toml --no-deps --format-version 1 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])' 2>/dev/null || printf '../weaver/weaver-guard/target')
WEAVER_GUARD := $(WEAVER_GUARD_TARGET_DIR)/debug/weaver-guard
POLICIES := ../weaver/policies
SEMCONV_BASE := ../docs/semconv
TRANSCRIPTS := $(wildcard $(TRANSCRIPT_DIR)/*.jsonl)

# Short alias: TS=session_xxx.jsonl -> TRANSCRIPT=$(TRANSCRIPT_DIR)/session_xxx.jsonl
ifdef TS
ifneq ($(filter /% ./%,$(TS)),)
TRANSCRIPT := $(TS)
else
TRANSCRIPT := $(TRANSCRIPT_DIR)/$(TS)
endif
endif

# Stable golden baseline in the shared AiVision artifact tree.
override GOLDEN_DIR := $(or $(strip $(GOLDEN_DIR)),$(ARTIFACT_DIR)/golden)
GOLDEN_RUNS_DIR := $(GOLDEN_DIR)/runs
GOLDEN_TRANSCRIPT_DIR := $(GOLDEN_DIR)/transcripts
GOLDEN_REPLAY_DIR := $(GOLDEN_DIR)/replays
GOLDEN_CONFIG := ../weaver/golden-compare.toml
GOLDEN_TRANSCRIPTS := $(wildcard $(GOLDEN_TRANSCRIPT_DIR)/*.jsonl)

.PHONY: build build-observ build-replay build-edition build-all aivision common \
        test replay replay-all validate validate-one report report-shift clean \
        install install-replay uninstall-replay release \
        demo golden-evaluate golden-loop golden-re-evaluate golden-run-scenario \
        golden-promote golden-replay-all golden-check golden-update golden-init \
        transcripts replays list-artifacts help

# pireplay depends on observ-pi (file: link), so observ-pi must build first.
build: build-replay

build-observ:
	cd $(TELEMETRY_PKG) && npm install && EDITION=common npm run build

build-replay: build-observ
	cd $(RECORD_PKG) && npm install && npm run build

install: build-observ
	@command -v "$(PI_CLI)" >/dev/null 2>&1 || { echo "Pi CLI not found: $(PI_CLI)" >&2; exit 1; }
	@$(PI_CLI) remove "$(abspath $(RECORD_PKG))" >/dev/null 2>&1 || true
	$(PI_CLI) install "$(abspath $(TELEMETRY_PKG))"
	node scripts/configure-local-install.mjs "$(PI_CONFIG_FILE)" \
		"$(LOCAL_OTLP_TRACES_ENDPOINT)" \
		"$(LOCAL_OTLP_METRICS_ENDPOINT)" \
		"$(LOCAL_OTLP_LOGS_ENDPOINT)"
	@echo ""
	@echo "Installed observ-pi production plugin through Pi package management."
	@echo "Configuration: $(PI_CONFIG_FILE)"
	@echo "Traces endpoint: $(LOCAL_OTLP_TRACES_ENDPOINT)"
	@echo "Metrics endpoint: $(LOCAL_OTLP_METRICS_ENDPOINT)"
	@echo "Events/logs endpoint: $(LOCAL_OTLP_LOGS_ENDPOINT)"

install-replay: build
	@command -v "$(PI_CLI)" >/dev/null 2>&1 || { echo "Pi CLI not found: $(PI_CLI)" >&2; exit 1; }
	$(PI_CLI) install "$(abspath $(RECORD_PKG))"
	@echo ""
	@echo "Installed pireplay local debugging plugin."
	@echo "observ-pi was not installed or configured."
	@echo "Enable capture explicitly with PIREPLAY_CAPTURE_ENABLED=true."
	@echo "Transcript directory: $(TRANSCRIPT_DIR)"
	@echo "Transcripts default to $(AIVISION_HOME)/pi/transcripts/."
	@echo "Set PIREPLAY_SESSION_DIR to override the default."

uninstall-replay:
	@command -v "$(PI_CLI)" >/dev/null 2>&1 || { echo "Pi CLI not found: $(PI_CLI)" >&2; exit 1; }
	$(PI_CLI) remove "$(abspath $(RECORD_PKG))"
	@echo "Removed pireplay; observ-pi remains installed."

release:
	cd $(TELEMETRY_PKG) && npm install && npm test && npm run typecheck
	@$(MAKE) --no-print-directory aivision
	@$(MAKE) --no-print-directory common

build-all:
	@$(MAKE) --no-print-directory aivision
	@$(MAKE) --no-print-directory common

aivision:
	@$(MAKE) --no-print-directory build-edition EDITION=aivision

common:
	@$(MAKE) --no-print-directory build-edition EDITION=common

build-edition:
	@if [ "$(EDITION)" != "common" ] && [ "$(EDITION)" != "aivision" ]; then \
		echo "Unsupported EDITION=$(EDITION); expected common or aivision" >&2; exit 1; \
	fi
	@echo ""
	@echo "========================================"
	@echo "  Building $(EDITION) edition ($(EDITION_PKG_PREFIX))"
	@echo "  Version: $(VERSION)"
	@echo "========================================"
	cd $(TELEMETRY_PKG) && EDITION=$(EDITION) npm run build
	@mkdir -p "$(RELEASE_DIR)"
	@set -e; \
	tmpdir=$$(mktemp -d); \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	packed=$$(cd $(TELEMETRY_PKG) && npm pack --ignore-scripts --silent --pack-destination "$$tmpdir" | tail -1); \
	mv "$$tmpdir/$$packed" "$(RELEASE_DIR)/$(RELEASE_PACKAGE)"; \
	trap - EXIT; \
	rm -rf "$$tmpdir"
	cd $(TELEMETRY_PKG) && npm run validate:release -- "../$(RELEASE_DIR)/$(RELEASE_PACKAGE)" "$(VERSION)" "$(EDITION)"
	@echo "  $(RELEASE_DIR)/$(RELEASE_PACKAGE)"

test:
	cd $(TELEMETRY_PKG) && npm test && npm run typecheck
	cd $(RECORD_PKG) && npm test && npm run typecheck

replay:
	@if [ -z "$(TRANSCRIPT)" ]; then echo "Usage: make replay TS=session_x.jsonl"; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	node $(RECORD_PKG)/dist/src/replay.js "$(abspath $(TRANSCRIPT))" --otel "$(REPLAY_DIR)/spans_$$(basename "$(TRANSCRIPT)")"

replay-all: build
	@mkdir -p "$(REPLAY_DIR)"
	@count=0; for transcript in $(TRANSCRIPTS); do \
		base=$$(basename "$$transcript"); \
		node $(RECORD_PKG)/dist/src/replay.js "$$transcript" --otel "$(REPLAY_DIR)/spans_$$base" || exit 1; \
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

# Demo loads BOTH piagent (telemetry) + pireplay (transcript) extensions.
demo: build
	@mkdir -p "$(DEMO_DIR)/transcripts" "$(DEMO_DIR)/replays"
	cd $(RECORD_PKG) && OBSERV_PI_DEBUG=true OBSERV_PI_SESSION_NAME=demo PIREPLAY_SESSION_NAME=demo \
		PIREPLAY_CAPTURE_ENABLED=true \
		PIREPLAY_SESSION_DIR="$(abspath $(DEMO_DIR)/transcripts)" \
		OBSERV_PI_REPLAY_DIR="$(abspath $(DEMO_DIR)/replays)" node dist/src/demo/run-demo.js

golden-evaluate: build
	cd $(RECORD_PKG) && $(if $(GOLDEN_RUN_DIR),PIREPLAY_GOLDEN_RUN_DIR="$(abspath $(GOLDEN_RUN_DIR))") node dist/src/golden/superloop.js

golden-loop: golden-evaluate

golden-re-evaluate: build
	@if [ -z "$(RUN_DIR)" ]; then echo "Usage: make golden-re-evaluate RUN_DIR=$(GOLDEN_RUNS_DIR)/<run-id>"; exit 1; fi
	cd $(RECORD_PKG) && PIREPLAY_GOLDEN_REUSE_RUN="$(abspath $(RUN_DIR))" node dist/src/golden/superloop.js

golden-run-scenario:
	@if [ -z "$(PROMPT)" ]; then echo "Usage: make golden-run-scenario PROMPT='...' SCENARIO=id"; exit 1; fi
	cd $(RECORD_PKG) && PIREPLAY_CAPTURE_ENABLED=true OBSERV_PI_DEMO_PROMPT="$(PROMPT)" OBSERV_PI_SESSION_NAME="$(SCENARIO)" PIREPLAY_SESSION_NAME="$(SCENARIO)" node dist/src/demo/run-demo.js

# ── Stable golden baseline ────────────────────────────────────────

golden-promote:
	@run="$(RUN_DIR)"; \
	if [ -z "$$run" ]; then \
		run="$$(ls -1d "$(GOLDEN_RUNS_DIR)"/*/ 2>/dev/null | sort | tail -1)"; \
		if [ -z "$$run" ]; then echo "[$(AGENT)] No golden run found. Run 'make golden-evaluate' first."; exit 1; fi; \
		echo "[$(AGENT)] No RUN_DIR given; using latest: $$run"; \
	fi; \
	manifest="$$run/manifest.json"; \
	if [ ! -f "$$manifest" ]; then echo "[$(AGENT)] Missing manifest: $$manifest"; exit 1; fi; \
	result="$$(python3 -c 'import json,sys; print(json.load(sys.stdin)["result"])' < "$$manifest")"; \
	if [ "$$result" != "pass" ]; then echo "[$(AGENT)] Run $$run result=$$result (not pass). Fix issues before promoting."; exit 1; fi; \
	mkdir -p "$(GOLDEN_TRANSCRIPT_DIR)" "$(GOLDEN_REPLAY_DIR)"; \
	rm -f "$(GOLDEN_TRANSCRIPT_DIR)"/*.jsonl "$(GOLDEN_TRANSCRIPT_DIR)"/*.jsonl.integrity.json \
	      "$(GOLDEN_REPLAY_DIR)"/*.jsonl; \
	cp "$$run"/transcripts/*.jsonl "$(GOLDEN_TRANSCRIPT_DIR)"/ 2>/dev/null || true; \
	cp "$$run"/transcripts/*.jsonl.integrity.json "$(GOLDEN_TRANSCRIPT_DIR)"/ 2>/dev/null || true; \
	cp "$$run"/replays/*.jsonl "$(GOLDEN_REPLAY_DIR)"/ 2>/dev/null || true; \
	tr=$$(find "$(GOLDEN_TRANSCRIPT_DIR)" -name '*.jsonl' | wc -l | tr -d ' '); \
	rp=$$(find "$(GOLDEN_REPLAY_DIR)" -name '*.jsonl' | wc -l | tr -d ' '); \
	echo "[$(AGENT)] Promoted $$run -> golden/{transcripts,replays}/: $$tr transcript(s), $$rp replay file(s)"

golden-replay-all: build
	@if [ -z "$(GOLDEN_TRANSCRIPTS)" ]; then echo "[$(AGENT)] No golden transcripts in $(GOLDEN_TRANSCRIPT_DIR)/. Run 'make golden-promote' first."; exit 1; fi
	@mkdir -p "$(GOLDEN_REPLAY_DIR)"
	@count=0; for transcript in $(GOLDEN_TRANSCRIPTS); do \
		base=$$(basename "$$transcript"); \
		node $(RECORD_PKG)/dist/src/replay.js "$$transcript" --otel "$(GOLDEN_REPLAY_DIR)/spans_$$base" || exit 1; \
		count=$$((count + 1)); \
	done; echo "[$(AGENT)] Golden replay complete: $$count transcript(s)."

golden-check: $(WEAVER_GUARD)
	@if [ ! -d "$(GOLDEN_REPLAY_DIR)" ] || [ -z "$$(ls -A $(GOLDEN_REPLAY_DIR)/*.jsonl 2>/dev/null)" ]; then \
		echo "[$(AGENT)] No golden replays in $(GOLDEN_REPLAY_DIR)/. Run 'make golden-promote' or 'make golden-replay-all' first."; exit 1; fi
	$(WEAVER_GUARD) compare "$(GOLDEN_REPLAY_DIR)" --replay "$(REPLAY_DIR)" --config "$(GOLDEN_CONFIG)" --diff-only || \
		echo "[$(AGENT)] Golden compare differs from baseline. If intended, run 'make golden-update'."

golden-update: validate
	@mkdir -p "$(GOLDEN_REPLAY_DIR)"
	@echo "[$(AGENT)] Updating golden replays from $(REPLAY_DIR)/..."
	@for f in "$(REPLAY_DIR)"/spans_*.jsonl; do \
		base=$$(basename "$$f"); \
		if [ -f "$(GOLDEN_REPLAY_DIR)/$$base" ]; then cp "$$f" "$(GOLDEN_REPLAY_DIR)/$$base"; fi; \
	done
	@echo "[$(AGENT)] Golden replays updated. Review with: git diff $(GOLDEN_REPLAY_DIR)/"

golden-init: replay-all
	@mkdir -p "$(GOLDEN_TRANSCRIPT_DIR)" "$(GOLDEN_REPLAY_DIR)"
	@echo "[$(AGENT)] Available replay files:"
	@ls -1 "$(REPLAY_DIR)"/spans_*.jsonl 2>/dev/null || echo "  (none)"
	@echo ""
	@echo "Promote a golden run:      make golden-promote RUN_DIR=golden/runs/<run-id>"
	@echo "Replay golden transcripts: make golden-replay-all"
	@echo "Compare vs baseline:        make golden-check REPLAY_DIR=replays"

clean:
	rm -f "$(REPLAY_DIR)"/spans_*.jsonl "$(REPLAY_DIR)"/events_*.jsonl "$(REPLAY_DIR)"/logs_*.jsonl "$(REPLAY_DIR)"/metrics_*.jsonl

transcripts:
	@find $(TRANSCRIPT_DIR) -maxdepth 1 -name '*.jsonl' -print 2>/dev/null || true

replays:
	@find $(REPLAY_DIR) -maxdepth 1 -name '*.jsonl' -print 2>/dev/null || true

list-artifacts: transcripts replays

help:
	@echo "Pi (piagent + pireplay) Makefile targets:"
	@echo "  build / test / clean        build/test both packages (piagent=telemetry, pireplay=transcript+replay)"
	@echo "  install                     register observ-pi; configure traces/metrics/events endpoints"
	@echo "  install-replay              register only the local pireplay plugin"
	@echo "  uninstall-replay            remove pireplay without changing observ-pi"
	@echo "  release                     package common + aivision observ-pi editions"
	@echo "  build-edition EDITION=...   package one edition (common or aivision)"
	@echo "  demo                        real pi session (loads piagent + pireplay)"
	@echo "  replay / replay-all         transcript(s) -> OTLP replay  [pireplay]"
	@echo "  validate / validate-one     weaver-guard validate replays"
	@echo "  report / report-shift       POST replays to local AiVision"
	@echo "  Golden (automated superloop, in pireplay):"
	@echo "    golden-evaluate [GOLDEN_RUN_DIR=]   run 8 scenarios end-to-end"
	@echo "    golden-re-evaluate RUN_DIR=<id>      re-replay an existing run (no model calls)"
	@echo "    golden-run-scenario PROMPT='..'      run one scenario manually"
	@echo "  Golden (stable baseline):"
	@echo "    golden-promote [RUN_DIR=<id>]        promote a run to golden/{transcripts,replays}/"
	@echo "    golden-replay-all                    replay golden/transcripts -> golden/replays"
	@echo "    golden-check [REPLAY_DIR=replays]    compare replays vs golden baseline"
	@echo "    golden-update                        copy current replays into golden baseline"
	@echo "    golden-init                          list replays / instructions"
