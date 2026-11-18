# opencode observ-opencode packaging Makefile
#
# Usage:
#   make                   Build all editions to releases/opencode/
#   make aivision          Build aivision edition only
#   make cowork            Build cowork edition only
#   make common            Build common edition only
#   make dev               Build plugin for local development (no packaging)
#   make install           Alias for install-aivision
#   make install-aivision  Build aivision edition and install to opencode npm cache layout
#   make install-cowork    Build cowork edition and install to opencode npm cache layout
#   make install-common    Build common edition and install to opencode npm cache layout
#   make test              Run typecheck
#   make clean             Remove releases/opencode/ and build artifacts
#   VERSION=0.2.0 make     Override version
#
# Replay / Validate:
#   make replay-all        Convert all transcripts → replays
#   make replay TRANSCRIPT=../transcripts/session_xxx.jsonl
#   make validate          Validate all replays
#   make validate-one FILE=../replays/xxx.jsonl

SHELL := /bin/bash --noprofile --norc

VERSION    ?= $(shell node -p "require('./package.json').version")
DIST_DIR   := ../../releases/opencode
PKG_NAME   := observ-opencode

# Replay / validate config
AGENT          := opencode
TRANSCRIPT_DIR := ../transcripts
REPLAY_DIR     := ../replays
WEAVER_GUARD_TARGET_DIR := ../../weaver/weaver-guard/target
WEAVER_GUARD   := $(WEAVER_GUARD_TARGET_DIR)/debug/weaver-guard
SEMCONV_BASE   := ../../docs/semconv
SEMCONV_AGENT  := $(wildcard ../../docs/semconv-$(AGENT))
POLICIES       := ../../weaver/policies
FORMAT         ?= text
TRANSCRIPT     ?=
FILE           ?=

# ── Phony targets ────────────────────────────────────────────────────
.PHONY: all aivision cowork common dev install install-aivision install-cowork install-common test clean \
        replay-all replay validate validate-one transcripts replays report help

all: aivision cowork common
	@echo ""
	@echo "=== Build complete ==="
	@$(MAKE) --no-print-directory list-artifacts

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

dev:
	cd .. && npm install && npm run build

install: install-aivision

CACHE_BASE := $(HOME)/.cache/opencode/packages/$(PKG_NAME)@latest
CACHE_NM   := $(CACHE_BASE)/node_modules/$(PKG_NAME)

# Install to opencode npm cache layout:
#   ~/.cache/opencode/packages/observ-opencode@latest/
#     package.json          (wrapper with dependencies)
#     package-lock.json     (lockfileVersion 3 for Arborist)
#     node_modules/observ-opencode/  (plugin files)

install-aivision:
	cd .. && npm install && EDITION=aivision npm run build --workspace observ-opencode
	@$(call install-to-cache)
	@echo "Installed $(PKG_NAME) (aivision) v$(VERSION) to $(CACHE_BASE)/"

install-cowork:
	cd .. && npm install && EDITION=cowork npm run build --workspace observ-opencode
	@$(call install-to-cache)
	@echo "Installed $(PKG_NAME) (cowork) v$(VERSION) to $(CACHE_BASE)/"

install-common:
	cd .. && npm install && npm run build --workspace observ-opencode
	@$(call install-to-cache)
	@echo "Installed $(PKG_NAME) (common) v$(VERSION) to $(CACHE_BASE)/"

define install-to-cache
	rm -rf $(CACHE_NM)
	mkdir -p $(CACHE_NM)
	cp -r dist opencode.plugin.json package.json $(CACHE_NM)/
	echo '{"dependencies":{"$(PKG_NAME)":"$(VERSION)"}}' > $(CACHE_BASE)/package.json
	printf '{"lockfileVersion":3,"name":"$(PKG_NAME)@latest","requires":true,"packages":{"":{"dependencies":{"$(PKG_NAME)":"$(VERSION)"}},"node_modules/$(PKG_NAME)":{"version":"$(VERSION)"}}}' > $(CACHE_BASE)/package-lock.json
endef

test:
	npm test

clean:
	rm -rf $(DIST_DIR)
	rm -f $(REPLAY_DIR)/*.jsonl
	npm run clean 2>/dev/null || true

# ── Internal: build one edition and pack ─────────────────────────────
.PHONY: build-edition list-artifacts

build-edition:
	@echo ""
	@echo "========================================"
	@echo "  Building $(or $(EDITION),common) edition ($(PKG_PREFIX))"
	@echo "  Version: $(VERSION)"
	@if [ -n "$(EDITION)" ]; then echo "  Edition: $(EDITION)"; fi
	@echo "========================================"
	cd .. && npm install && EDITION=$(EDITION) npm run build --workspace observ-opencode
	@mkdir -p $(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)
	npm pack --pack-destination $(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)
	@src="$(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$(PKG_NAME)-$(VERSION).tgz"; \
	dest="$(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$(PKG_PREFIX)-$(VERSION).tgz"; \
	if [ "$$src" != "$$dest" ]; then mv "$$src" "$$dest"; fi
	@echo "✓ $(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$(PKG_PREFIX)-$(VERSION).tgz"

list-artifacts:
	@echo "Artifacts:"
	@if [ -d "$(DIST_DIR)/aivision/$(VERSION)" ]; then \
		echo ""; echo "  releases/opencode/aivision/$(VERSION)/"; ls -1h $(DIST_DIR)/aivision/$(VERSION)/ | sed 's/^/    /'; \
	fi
	@if [ -d "$(DIST_DIR)/cowork/$(VERSION)" ]; then \
		echo ""; echo "  releases/opencode/cowork/$(VERSION)/"; ls -1h $(DIST_DIR)/cowork/$(VERSION)/ | sed 's/^/    /'; \
	fi
	@if [ -d "$(DIST_DIR)/common/$(VERSION)" ]; then \
		echo ""; echo "  releases/opencode/common/$(VERSION)/"; ls -1h $(DIST_DIR)/common/$(VERSION)/ | sed 's/^/    /'; \
	fi

# ── Replay ───────────────────────────────────────────────────────────
replay-all: dev
	@if ls $(TRANSCRIPT_DIR)/*.jsonl >/dev/null 2>&1; then \
		status=0; \
		for f in $(TRANSCRIPT_DIR)/*.jsonl; do \
			base=$$(basename "$$f" .jsonl); \
			out="$(REPLAY_DIR)/spans_$${base#session_}.jsonl"; \
			echo "[$(AGENT)] $$f → $$out"; \
			npx tsx ../opencode-replay/src/replay.ts "$$f" --otel "$$out" || status=1; \
		done; \
		exit $$status; \
	else \
		echo "[$(AGENT)] No transcript files found in $(TRANSCRIPT_DIR)/"; \
	fi

replay: dev
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make replay TRANSCRIPT=$(TRANSCRIPT_DIR)/session_xxx.jsonl"; exit 1; fi
	@base=$$(basename "$(TRANSCRIPT)" .jsonl); \
	out="$(REPLAY_DIR)/spans_$${base#session_}.jsonl"; \
	echo "[$(AGENT)] $(TRANSCRIPT) → $$out"; \
	npx tsx ../opencode-replay/src/replay.ts "$(TRANSCRIPT)" --otel "$$out"

# ── Validate ─────────────────────────────────────────────────────────
validate:
	@if ls $(REPLAY_DIR)/*.jsonl >/dev/null 2>&1; then \
		$(WEAVER_GUARD) validate $(REPLAY_DIR)/ \
			--policies-dir $(POLICIES) \
			--semconv-dir $(SEMCONV_BASE) \
			$(if $(SEMCONV_AGENT),--semconv-dir $(SEMCONV_AGENT)) \
			--format $(FORMAT); \
	else \
		echo "[$(AGENT)] No replay files to validate."; \
	fi

validate-one:
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make validate-one FILE=$(REPLAY_DIR)/xxx.jsonl"; exit 1; fi
	$(WEAVER_GUARD) validate $(FILE) \
		--policies-dir $(POLICIES) \
		--semconv-dir $(SEMCONV_BASE) \
		$(if $(SEMCONV_AGENT),--semconv-dir $(SEMCONV_AGENT)) \
		--format $(FORMAT)

# ── Listing ──────────────────────────────────────────────────────────
transcripts:
	@echo "Transcripts:"
	@ls -lh $(TRANSCRIPT_DIR)/*.jsonl 2>/dev/null || echo "  (none)"

replays:
	@echo "Replays:"
	@ls -lh $(REPLAY_DIR)/*.jsonl 2>/dev/null || echo "  (none)"

# ── Report ───────────────────────────────────────────────────────────

REPORT_CLI := npx tsx ../opencode-replay/src/report.ts

report:
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make report TRANSCRIPT=<file> [SHIFT=1]"; exit 1; fi
	$(REPORT_CLI) $(TRANSCRIPT) \
		$(if $(SHIFT),--shift)

# ── Help ─────────────────────────────────────────────────────────────
help:
	@echo "$(AGENT) Observability Makefile"
	@echo ""
	@echo "  Packaging:"
	@echo "  make                              Build all editions to releases/opencode/"
	@echo "  make aivision                     Build aivision edition only"
	@echo "  make cowork                       Build cowork edition only"
	@echo "  make common                       Build common edition only"
	@echo "  make dev                          Build for local development"
	@echo "  make install                      Install aivision to opencode cache"
	@echo "  make install-aivision             Install aivision edition to cache"
	@echo "  make install-cowork               Install cowork edition to cache"
	@echo "  make install-common               Install common edition to cache"
	@echo "  make test                         Run typecheck"
	@echo "  make clean                        Clean all build artifacts"
	@echo ""
	@echo "  Replay / Validate:"
	@echo "  make replay-all                   Convert all transcripts → replays"
	@echo "  make replay TRANSCRIPT=<file>     Convert single transcript"
	@echo "  make validate                     Validate all replays"
	@echo "  make validate-one FILE=<file>     Validate single replay"
	@echo "  make transcripts                  List transcript files"
	@echo "  make replays                      List replay files"
	@echo ""
	@echo "  Report:"
	@echo "  make report TRANSCRIPT=<file>     Send transcript to fixed local collector 127.0.0.1:4319"
	@echo "    SHIFT=1                         Shift timestamps to current time"
	@echo ""
	@echo "  Variables:"
	@echo "  VERSION=x.y.z                     Override package version"
