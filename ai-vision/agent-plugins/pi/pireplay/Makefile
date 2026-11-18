# pireplay — Makefile
#
# Standard AiVision agent targets for the pireplay extension. pireplay reuses
# `@observ/pi`'s semantic engine, so its build depends on observ-pi being
# built first.
#
#   make -C pi/pireplay build
#   make -C pi/pireplay replay-all
#   make -C pi/pireplay validate
#   make -C pi/pireplay report            POST replays to local AiVision
#   make -C pi/pireplay report-check      (alias of report)
#   make -C pi/pireplay demo              run a real pi session with pireplay
#   make -C pi/pireplay clean

AGENT := pireplay
PKG_DIR := .
FORMAT ?= text
RAW_DIR ?= transcripts
REPLAY_DIR ?= replays
DEMO_DIR ?= demo-output
override WEAVER_URL := http://127.0.0.1:4319

# weaver-guard binary + policy/semconv roots (resolve against the repo root).
WEAVER_GUARD_TARGET_DIR := $(shell cargo metadata --manifest-path ../../weaver/weaver-guard/Cargo.toml --no-deps --format-version 1 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])' 2>/dev/null || printf '../../weaver/weaver-guard/target')
WEAVER_GUARD := $(WEAVER_GUARD_TARGET_DIR)/debug/weaver-guard
POLICIES := ../../weaver/policies
SEMCONV_BASE := ../../docs/semconv

RAW_FILES := $(wildcard $(RAW_DIR)/*.jsonl)

.PHONY: build test replay replay-all validate validate-one report report-check report-shift \
        demo clean transcripts replays list-artifacts help

# observ-pi must be built first so the file: dependency resolves its dist.
# (observ-pi has no Makefile of its own; its build is `npm install && tsc`.)
build:
	cd ../observ-pi && npm install && npm run build
	cd $(PKG_DIR) && npm install && npm run build

test:
	cd $(PKG_DIR) && npm test && npm run typecheck

replay:
	@if [ -z "$(RAW)" ]; then echo "Usage: make replay RAW=transcripts/pireplay_x.jsonl"; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	base=$$(basename "$(RAW)" .jsonl); \
	cd $(PKG_DIR) && node dist/src/replay.js "$(RAW)" --otel "$(REPLAY_DIR)/spans_$$base.jsonl"

replay-all: build
	@mkdir -p "$(REPLAY_DIR)"
	@count=0; for raw in $(RAW_FILES); do \
		base=$$(basename "$$raw" .jsonl); \
		(cd $(PKG_DIR) && node dist/src/replay.js "$$raw" --otel "$(REPLAY_DIR)/spans_$$base.jsonl") || exit 1; \
		count=$$((count + 1)); \
	done; echo "[$(AGENT)] Replay complete: $$count raw log(s)."

$(WEAVER_GUARD):
	cargo build --manifest-path ../../weaver/weaver-guard/Cargo.toml

validate: $(WEAVER_GUARD)
	@if ls "$(REPLAY_DIR)"/*.jsonl >/dev/null 2>&1; then \
		$(WEAVER_GUARD) validate "$(REPLAY_DIR)" --policies-dir $(POLICIES) --semconv-dir $(SEMCONV_BASE) --format $(FORMAT); \
	else echo "[$(AGENT)] No replay files to validate."; fi

validate-one: $(WEAVER_GUARD)
	@if [ -z "$(FILE)" ]; then echo "Usage: make validate-one FILE=replays/spans_x.jsonl"; exit 1; fi
	$(WEAVER_GUARD) validate "$(FILE)" --policies-dir $(POLICIES) --semconv-dir $(SEMCONV_BASE) --format $(FORMAT)

report report-check:
	@if ! ls "$(REPLAY_DIR)"/*.jsonl >/dev/null 2>&1; then echo "[$(AGENT)] No replay files to report."; exit 1; fi
	python3 ../../scripts/report_check.py --base-url "$(WEAVER_URL)" "$(REPLAY_DIR)"/*.jsonl

report-shift: replay-all report

demo: build
	@mkdir -p "$(DEMO_DIR)/raw"
	cd $(PKG_DIR) && OBSERV_PI_SESSION_NAME=pireplay-demo PIREPLAY_CAPTURE_ENABLED=true \
		PIREPLAY_SESSION_DIR="$(abspath $(DEMO_DIR)/raw)" \
		node dist/src/demo/run-demo.js

clean:
	cd $(PKG_DIR) && npm run clean
	rm -rf $(REPLAY_DIR)

transcripts:
	@find $(RAW_DIR) -maxdepth 1 -name '*.jsonl' -print 2>/dev/null || true

replays:
	@find $(REPLAY_DIR) -maxdepth 1 -name '*.jsonl' -print 2>/dev/null || true

list-artifacts: transcripts replays

help:
	@echo "pireplay: build test replay replay-all validate validate-one report report-check report-shift demo clean"
