# opencode - AI Agent Observability Makefile
#
# Pipeline:
#   make replay-all                              Convert all transcripts to replays
#   make replay TS=xxx.jsonl                     Convert one canonical transcript
#   make validate                                Validate all replays
#   make validate-one FILE=$$HOME/.aivision/ocd/replays/xxx.jsonl
#   make report TS=xxx.jsonl                     Send to fixed local collector
#
# Build:
#   make build                                   Build observ-opencode (dev)
#   make build-edition EDITION=aivision          Build specific edition
#   make build-all                               Build all editions
#   make install [EDITION=aivision]              Install/register production (default: common)
#   make replay-plugin-install                   Install/register local replay profile
#   make test                                    Run typecheck
#
# Tools:
#   make transcripts / replays / list-artifacts / clean / help

SHELL := /bin/bash --noprofile --norc

AGENT         := opencode
override AIVISION_HOME := $(or $(strip $(AIVISION_HOME)),$(HOME)/.aivision)
override ARTIFACT_DIR := $(or $(strip $(ARTIFACT_DIR)),$(AIVISION_HOME)/ocd)
override TRANSCRIPT_DIR := $(or $(strip $(TRANSCRIPT_DIR)),$(ARTIFACT_DIR)/transcripts)
override REPLAY_DIR := $(or $(strip $(REPLAY_DIR)),$(ARTIFACT_DIR)/replays)
PKG_NAME      := observ-opencode
PKG_DIR       := observ-opencode
REPLAY_PKG_DIR := opencode-replay
REPLAY_PKG_NAME := opencode-replay
REPLAY_CLI    := npx tsx $(REPLAY_PKG_DIR)/src/replay.ts
REPORT_CLI    := npx tsx $(REPLAY_PKG_DIR)/src/report.ts
WEAVER_GUARD  := ../weaver/weaver-guard/target/debug/weaver-guard
SEMCONV_BASE  := ../docs/semconv
SEMCONV_AGENT := $(wildcard ../docs/semconv-$(AGENT))
POLICIES      := ../weaver/policies
# Short alias: ts=session_xxx.jsonl → TRANSCRIPT=transcripts/session_xxx.jsonl
ifdef TS
ifneq ($(filter /% ./%,$(TS)),)
TRANSCRIPT := $(TS)
else
TRANSCRIPT := $(TRANSCRIPT_DIR)/$(TS)
endif
endif
FORMAT        ?= text
VERSION       ?= $(shell node -p "require('./$(PKG_DIR)/package.json').version")
DIST_DIR      := ../releases/opencode

override GOLDEN_DIR := $(or $(strip $(GOLDEN_DIR)),$(ARTIFACT_DIR)/golden)
GOLDEN_CONFIG := ../weaver/golden-compare.toml

TRANSCRIPTS   := $(wildcard $(TRANSCRIPT_DIR)/*.jsonl)
REPLAYS       := $(patsubst $(TRANSCRIPT_DIR)/%.jsonl,$(REPLAY_DIR)/spans_%.jsonl,$(TRANSCRIPTS))

# Install layout: opencode npm cache
CACHE_BASE := $(HOME)/.cache/opencode/packages/$(PKG_NAME)@latest
CACHE_NM   := $(CACHE_BASE)/node_modules/$(PKG_NAME)
REPLAY_CACHE_BASE := $(HOME)/.cache/opencode/packages/$(REPLAY_PKG_NAME)@local
REPLAY_CACHE_ENTRY := $(REPLAY_CACHE_BASE)/node_modules/$(REPLAY_PKG_NAME)/dist/index.js
PRODUCTION_CACHE_ENTRY := $(CACHE_NM)/dist/index.js

OPENCODE_CONFIG_DIR ?= $(HOME)/.config/opencode
OPENCODE_CONFIG ?= $(OPENCODE_CONFIG_DIR)/opencode.json
OPENCODE_LEGACY_CONFIG ?= $(OPENCODE_CONFIG_DIR)/config.json
OPENCODE_REPLAY_CONFIG ?= $(OPENCODE_CONFIG_DIR)/opencode-replay.json
OPENCODE_TRANSCRIPTS_DIR ?= $(if $(OPENCODE_REPLAY_SESSION_DIR),$(OPENCODE_REPLAY_SESSION_DIR),$(TRANSCRIPT_DIR))
REGISTER_PLUGIN := node scripts/register-opencode-plugin.mjs

.PHONY: replay-all replay validate validate-one report report-shift \
        golden-check golden-update golden-init \
        build build-edition build-all release install dev \
        aivision cowork common \
        install-aivision install-cowork install-common \
        register-production register-replay replay-plugin-install replay-plugin-uninstall \
        test clean transcripts replays list-artifacts help

# ── Replay ─────────────────────────────────────────────────────────

replay-all: dev
	@mkdir -p "$(REPLAY_DIR)"
	@if ls "$(TRANSCRIPT_DIR)"/*.jsonl >/dev/null 2>&1; then \
		status=0; \
		for f in "$(TRANSCRIPT_DIR)"/*.jsonl; do \
			base=$$(basename "$$f" .jsonl); \
			out="$(REPLAY_DIR)/spans_$${base#session_}.jsonl"; \
			echo "[$(AGENT)] $$f -> $$out"; \
			$(REPLAY_CLI) "$$f" --otel "$$out" || status=1; \
		done; \
		exit $$status; \
	else \
		echo "[$(AGENT)] No transcript files found in $(TRANSCRIPT_DIR)/"; \
	fi

replay: dev
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make replay TS=session_xxx.jsonl"; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	@base=$$(basename "$(TRANSCRIPT)" .jsonl); \
	out="$(REPLAY_DIR)/spans_$${base#session_}.jsonl"; \
	echo "[$(AGENT)] $(TRANSCRIPT) -> $$out"; \
	$(REPLAY_CLI) "$(TRANSCRIPT)" --otel "$$out"

# ── Validate ───────────────────────────────────────────────────────

validate:
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

report:
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make report TS=xxx.jsonl [SHIFT=1]"; exit 1; fi
	$(REPORT_CLI) $(TRANSCRIPT) \
		$(if $(SHIFT),--shift) \
		$(if $(DEBUG),--debug)

# ── Report-shift (replay → shift → report) ──────────────────────

report-shift:
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make report-shift TS=xxx.jsonl [DEBUG=1]"; exit 1; fi
	$(REPORT_CLI) $(TRANSCRIPT) \
		--shift \
		$(if $(DEBUG),--debug)

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
	npm run build

dev:
	npm install && npm run build

build-all: aivision cowork common
	@echo ""
	@echo "=== Build complete ==="
	@$(MAKE) --no-print-directory list-artifacts

all: build-all

release: build-all

aivision:
	@$(MAKE) --no-print-directory build-edition \
		EDITION=aivision \
		PKG_PREFIX=observ-aivision-opencode \
		OUT_SUBDIR=aivision

cowork:
	@$(MAKE) --no-print-directory build-edition \
		EDITION=cowork \
		PKG_PREFIX=observ-cowork-opencode \
		OUT_SUBDIR=cowork

common:
	@$(MAKE) --no-print-directory build-edition \
		EDITION= \
		PKG_PREFIX=observ-opencode \
		OUT_SUBDIR=common

build-edition:
	@echo ""
	@echo "========================================"
	@echo "  Building $(or $(EDITION),common) edition ($(PKG_PREFIX))"
	@echo "  Version: $(VERSION)"
	@if [ -n "$(EDITION)" ]; then echo "  Edition: $(EDITION)"; fi
	@echo "========================================"
	npm install && EDITION="$(EDITION)" npm run build --workspace observ-opencode
	@mkdir -p $(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)
	cd $(PKG_DIR) && npm pack --pack-destination ../$(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)
	@src="$(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$(PKG_NAME)-$(VERSION).tgz"; \
	dest="$(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$(PKG_PREFIX)-$(VERSION).tgz"; \
	if [ "$$src" != "$$dest" ]; then mv "$$src" "$$dest"; fi
	@echo "  $(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$(PKG_PREFIX)-$(VERSION).tgz"

# ── Install ────────────────────────────────────────────────────────

install:
	@$(MAKE) --no-print-directory install-$(or $(EDITION),common)
	@$(MAKE) --no-print-directory register-production

install-aivision:
	npm install && EDITION=aivision npm run build --workspace observ-opencode
	@$(call install-to-cache)
	@echo "Installed $(PKG_NAME) (aivision) v$(VERSION) to $(CACHE_BASE)/"

install-cowork:
	npm install && EDITION=cowork npm run build --workspace observ-opencode
	@$(call install-to-cache)
	@echo "Installed $(PKG_NAME) (cowork) v$(VERSION) to $(CACHE_BASE)/"

install-common:
	npm install && npm run build --workspace observ-opencode
	@$(call install-to-cache)
	@echo "Installed $(PKG_NAME) (common) v$(VERSION) to $(CACHE_BASE)/"

define install-to-cache
	rm -rf $(CACHE_NM)
	mkdir -p $(CACHE_NM)
	cp -r $(PKG_DIR)/dist $(PKG_DIR)/opencode.plugin.json $(PKG_DIR)/package.json $(CACHE_NM)/
	echo '{"dependencies":{"$(PKG_NAME)":"$(VERSION)"}}' > $(CACHE_BASE)/package.json
	printf '{"lockfileVersion":3,"name":"$(PKG_NAME)@latest","requires":true,"packages":{"":{"dependencies":{"$(PKG_NAME)":"$(VERSION)"}},"node_modules/$(PKG_NAME)":{"version":"$(VERSION)"}}}' > $(CACHE_BASE)/package-lock.json
endef

register-production:
	$(REGISTER_PLUGIN) \
		--profile production \
		--config "$(OPENCODE_CONFIG)" \
		--legacy-config "$(OPENCODE_LEGACY_CONFIG)" \
		--entry "$(PRODUCTION_CACHE_ENTRY)"

replay-plugin-install:
	npm install && npm run build
	@mkdir -p "$(dir $(REPLAY_CACHE_BASE))"
	@package_dir=$$(mktemp -d); \
	cache_dir=$$(mktemp -d "$(REPLAY_CACHE_BASE).tmp.XXXXXX"); \
	trap 'rm -rf "$$package_dir" "$$cache_dir"' EXIT; \
	core_name=$$(npm pack --workspace observ-opencode --pack-destination "$$package_dir" --silent | tail -1); \
	replay_name=$$(npm pack --workspace opencode-replay --pack-destination "$$package_dir" --silent | tail -1); \
	npm install --prefix "$$cache_dir" --ignore-scripts --legacy-peer-deps \
		"$$package_dir/$$core_name" "$$package_dir/$$replay_name"; \
	node scripts/promote-directory.mjs "$$cache_dir" "$(REPLAY_CACHE_BASE)"; \
	trap - EXIT; \
	rm -rf "$$package_dir"
	@$(MAKE) --no-print-directory register-replay
	@echo "Installed $(REPLAY_PKG_NAME) v$(VERSION) to $(REPLAY_CACHE_BASE)/"

register-replay:
	$(REGISTER_PLUGIN) \
		--profile replay \
		--config "$(OPENCODE_CONFIG)" \
		--legacy-config "$(OPENCODE_LEGACY_CONFIG)" \
		--entry "$(REPLAY_CACHE_ENTRY)" \
		--replay-config "$(OPENCODE_REPLAY_CONFIG)" \
		--session-dir "$(OPENCODE_TRANSCRIPTS_DIR)"

replay-plugin-uninstall:
	$(REGISTER_PLUGIN) \
		--profile replay \
		--remove true \
		--config "$(OPENCODE_CONFIG)" \
		--legacy-config "$(OPENCODE_LEGACY_CONFIG)"
	rm -rf "$(REPLAY_CACHE_BASE)"
	@if [ "$(PURGE_REPLAY_CONFIG)" = "1" ]; then \
		rm -f "$(OPENCODE_REPLAY_CONFIG)"; \
		echo "Removed replay config $(OPENCODE_REPLAY_CONFIG)"; \
	else \
		echo "Preserved replay config and transcripts (set PURGE_REPLAY_CONFIG=1 to remove replay config)"; \
	fi

test:
	node scripts/test-register-opencode-plugin.mjs
	node scripts/test-promote-directory.mjs
	npm test

# ── File listing ───────────────────────────────────────────────────

transcripts:
	@echo "Transcripts:"
	@ls -lh "$(TRANSCRIPT_DIR)"/*.jsonl 2>/dev/null || echo "  (none)"

replays:
	@echo "Replays:"
	@ls -lh "$(REPLAY_DIR)"/*.jsonl 2>/dev/null || echo "  (none)"

list-artifacts:
	@echo "Artifacts (opencode):"
	@found=0; \
	for sub in aivision cowork common; do \
		dir="$(DIST_DIR)/$$sub/$(VERSION)"; \
		if [ -d "$$dir" ]; then \
			echo ""; echo "  releases/opencode/$$sub/$(VERSION)/"; \
			ls -1h "$$dir" | sed 's/^/    /'; \
			found=1; \
		fi; \
	done; \
	if [ "$$found" = "0" ]; then echo "  (none)"; fi

# ── Cleanup ────────────────────────────────────────────────────────

clean:
	rm -f "$(REPLAY_DIR)"/spans_*.jsonl "$(REPLAY_DIR)"/events_*.jsonl "$(REPLAY_DIR)"/logs_*.jsonl "$(REPLAY_DIR)"/metrics_*.jsonl
	@echo "[$(AGENT)] Cleaned."

# ── Help ───────────────────────────────────────────────────────────

help:
	@echo "opencode Observability Makefile"
	@echo ""
	@echo "  Pipeline:"
	@echo "  make replay-all              Convert all transcripts to replays"
	@echo "  make replay TRANSCRIPT=<f>   Convert single transcript"
	@echo "  make validate                Validate all replays"
	@echo "  make validate-one FILE=<f>   Validate single replay"
	@echo "  make report TRANSCRIPT=<f>   Send transcript to fixed local collector 127.0.0.1:4319"
	@echo "    SHIFT=1                    Shift timestamps to current time"
	@echo ""
	@echo "  Build:"
	@echo "  make build                   Build observ-opencode and opencode-replay"
	@echo "  make build-edition EDITION=  Build edition (aivision|cowork|common)"
	@echo "  make build-all               Build all editions"
	@echo "  make install [EDITION=]      Install/register production (default: common)"
	@echo "  make replay-plugin-install   Install/register replay for local testing"
	@echo "  make replay-plugin-uninstall Remove only replay registration/cache"
	@echo "    PURGE_REPLAY_CONFIG=1      Also remove replay config; transcripts remain"
	@echo "    OPENCODE_TRANSCRIPTS_DIR=  Transcript directory"
	@echo "  make dev                     Build for local development"
	@echo "  make test                    Run typecheck and regression tests"
	@echo ""
	@echo "  Golden:"
	@echo "  make golden-check            Compare replays against golden baseline"
	@echo "  make golden-update           Update golden from current replays"
	@echo "  make golden-init             Show available replays for golden init"
	@echo ""
	@echo "  Tools:"
	@echo "  make transcripts             List transcript files"
	@echo "  make replays                 List replay files"
	@echo "  make list-artifacts          List build artifacts"
	@echo "  make clean                   Clean all build artifacts"
	@echo ""
	@echo "  VERSION=x.y.z               Override package version"
