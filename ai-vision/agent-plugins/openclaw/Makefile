# openclaw - AI Agent Observability Makefile
#
# Pipeline:
#   make replay-all                              Convert all transcripts to replays
#   make replay TS=xxx.jsonl                     Convert one canonical transcript
#   make validate                                Validate all replays
#   make validate-one FILE=$$HOME/.aivision/ocw/replays/xxx.jsonl
#   make report TS=xxx.jsonl                     Send to fixed local collector
#
# Build:
#   make build                                   Build observ-openclaw + openclaw-replay
#   make build EDITION=aivision                  Build specific edition
#   make build-edition EDITION=aivision          Build specific edition (explicit)
#   make build-all                               Build all editions
#   make install EDITION=aivision                Install edition locally
#   make install EDITION=common                  Install common edition locally
#   make test                                    Run typecheck and edition tests
#
# Short aliases: ED=aivision (→ EDITION), TS=file.jsonl (→ TRANSCRIPT)
#
# Tools:
#   make transcripts / replays / list-artifacts / clean / help

AGENT         := openclaw
override AIVISION_HOME := $(or $(strip $(AIVISION_HOME)),$(HOME)/.aivision)
override ARTIFACT_DIR := $(or $(strip $(ARTIFACT_DIR)),$(AIVISION_HOME)/ocw)
override TRANSCRIPT_DIR := $(or $(strip $(TRANSCRIPT_DIR)),$(ARTIFACT_DIR)/transcripts)
override REPLAY_DIR := $(or $(strip $(REPLAY_DIR)),$(ARTIFACT_DIR)/replays)
PKG_NAME      := observ-openclaw
PKG_DIR       := observ-openclaw
INSTALL_DIR   := ~/.openclaw/extensions/$(PKG_NAME)
REPLAY_PKG_DIR  := openclaw-replay
REPLAY_INSTALL_DIR := ~/.openclaw/extensions/openclaw-replay
REPLAY_TRANSCRIPT_DIR ?= $(if $(OPENCLAW_REPLAY_SESSION_DIR),$(OPENCLAW_REPLAY_SESSION_DIR),$(TRANSCRIPT_DIR))
OPENCLAW_CLI   ?= openclaw
NPM_REGISTRY   ?= $(shell npm config get registry 2>/dev/null)
REPORT_CLI    := npx tsx --tsconfig openclaw-replay/tsconfig.json openclaw-replay/src/report.ts
WEAVER_GUARD_TARGET_DIR := $(shell cargo metadata --manifest-path ../weaver/weaver-guard/Cargo.toml --no-deps --format-version 1 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)[\"target_directory\"])" 2>/dev/null || printf "../weaver/weaver-guard/target")
WEAVER_GUARD  := $(WEAVER_GUARD_TARGET_DIR)/debug/weaver-guard
SEMCONV_BASE  := ../docs/semconv
SEMCONV_AGENT := $(wildcard ../docs/semconv-$(AGENT))
POLICIES      := ../weaver/policies
# Short alias: ED=aivision → EDITION=aivision
ifdef ED
EDITION := $(ED)
endif
# Short alias: TS=session_xxx.jsonl → TRANSCRIPT=transcripts/session_xxx.jsonl
# Supports absolute paths: TS=/abs/path/file.jsonl or TS=./relative/file.jsonl
ifdef TS
ifneq ($(filter /% ./%,$(TS)),)
TRANSCRIPT := $(TS)
else
TRANSCRIPT := $(TRANSCRIPT_DIR)/$(TS)
endif
endif
FORMAT        ?= text
VERSION       ?= $(shell node -p "require('./$(PKG_DIR)/package.json').version")
DIST_DIR      := ../releases/openclaw

override GOLDEN_DIR := $(or $(strip $(GOLDEN_DIR)),$(ARTIFACT_DIR)/golden)
GOLDEN_CONFIG := ../weaver/golden-compare.toml

TRANSCRIPTS   := $(wildcard $(TRANSCRIPT_DIR)/*.jsonl)
REPLAY_ALL_OUTPUT := $(REPLAY_DIR)/spans_all.jsonl

.PHONY: replay-all replay validate validate-one report report-shift \
        golden-replay-all golden-check golden-update golden-init \
        build build-edition build-all release install dev \
        aivision common publish-common \
        _install-local install-aivision install-common install-link \
        replay-plugin-deps replay-plugin-build replay-plugin-install replay-plugin-uninstall \
        test clean transcripts replays list-artifacts help

# ── Replay ─────────────────────────────────────────────────────────

$(REPLAY_DIR)/spans_%.jsonl: $(TRANSCRIPT_DIR)/%.jsonl
	@mkdir -p "$(REPLAY_DIR)"
	$(REPORT_CLI) $< --replay --otel $@

replay-all:
	@if [ -z "$(TRANSCRIPTS)" ]; then \
		echo "[$(AGENT)] No transcript files found in $(TRANSCRIPT_DIR)/."; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	rm -f "$(REPLAY_ALL_OUTPUT)" "$(REPLAY_DIR)/events_all.jsonl"
	$(REPORT_CLI) "$(TRANSCRIPT_DIR)" --replay --otel "$(REPLAY_ALL_OUTPUT)" --events "$(REPLAY_DIR)/events_all.jsonl"
	@echo "[$(AGENT)] Replay complete: $(words $(TRANSCRIPTS)) correlated input file(s) -> $(REPLAY_ALL_OUTPUT)."

replay:
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make replay TS=xxx.jsonl"; exit 1; fi
	@mkdir -p "$(REPLAY_DIR)"
	$(REPORT_CLI) "$(TRANSCRIPT)" --replay --otel "$(REPLAY_DIR)/spans_$(notdir $(basename $(TRANSCRIPT))).jsonl"

# ── Validate ───────────────────────────────────────────────────────

validate: replay-all
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

golden-replay-all:
	@if ! ls $(GOLDEN_DIR)/transcripts/*.jsonl >/dev/null 2>&1; then \
		echo "[$(AGENT)] No golden transcripts found in $(GOLDEN_DIR)/transcripts/"; \
		exit 1; \
	fi
	@mkdir -p $(GOLDEN_DIR)/replays
	@count=0; \
	manifest="$(GOLDEN_DIR)/replays/.report-check-files"; \
	: > "$$manifest"; \
	for f in $(GOLDEN_DIR)/transcripts/*.jsonl; do \
		base=$$(basename "$$f" .jsonl); \
		out="$(GOLDEN_DIR)/replays/spans_$$base.jsonl"; \
		events="$(GOLDEN_DIR)/replays/events_$$base.jsonl"; \
		rm -f "$$out" "$$events"; \
		$(REPORT_CLI) "$$f" --replay --otel "$$out" || exit 1; \
		if [ -s "$$out" ]; then printf '%s\n' "$$out" >> "$$manifest"; fi; \
		if [ -s "$$events" ]; then printf '%s\n' "$$events" >> "$$manifest"; fi; \
		count=$$((count + 1)); \
	done; \
	echo "[$(AGENT)] Golden replay complete: $$count transcript(s) -> $(GOLDEN_DIR)/replays"

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
	cd $(PKG_DIR) && npm install && EDITION=$(EDITION) npm run build
	cd $(REPLAY_PKG_DIR) && npm install && npm run build

dev:
	cd $(PKG_DIR) && npm install && npm run build
	cd $(REPLAY_PKG_DIR) && npm install && npm run build

# Edition packaging targets
build-all: aivision common
	@echo ""
	@echo "=== Build complete ==="
	@$(MAKE) --no-print-directory list-artifacts

# Keep "all" as alias for build-all
all: build-all

release: build-all

aivision:
	@$(MAKE) --no-print-directory build-edition \
		EDITION=aivision \
		PKG_PREFIX=observ-aivision-openclaw \
		OUT_SUBDIR=aivision

common:
	@$(MAKE) --no-print-directory build-edition \
		EDITION=common \
		PKG_PREFIX=observ-openclaw \
		OUT_SUBDIR=common

build-edition:
	@echo ""
	@echo "========================================"
	@echo "  Building $(or $(EDITION),common) edition ($(PKG_PREFIX))"
	@echo "  Version: $(VERSION)"
	@if [ -n "$(EDITION)" ]; then echo "  Edition: $(EDITION)"; fi
	@echo "========================================"
	cd $(PKG_DIR) && npm install && EDITION=$(EDITION) npm run build
	cd $(PKG_DIR) && EDITION=$(EDITION) npm run verify:packed
	@mkdir -p $(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)
	@set -e; \
	dest="$$(cd . && pwd)/$(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$(PKG_PREFIX)-$(VERSION).tgz"; \
	tmpdest="$$dest.tmp.$$$$"; \
	tmpdir=$$(mktemp -d); \
	trap 'rm -rf "$$tmpdir" "$$tmpdest"' EXIT; \
	cd $(PKG_DIR) && EDITION=$(EDITION) npm pack --ignore-scripts --quiet --pack-destination "$$tmpdir" >/dev/null; \
	cd "$$tmpdir"; \
	tar -xzf *.tgz; \
	rm -f *.tgz; \
	mv package $(PKG_PREFIX); \
	find $(PKG_PREFIX) -type d -exec chmod 0755 {} +; \
	find $(PKG_PREFIX) -type f -exec chmod 0644 {} + -exec touch -t 198001010000 {} +; \
	find $(PKG_PREFIX) -type f -print | LC_ALL=C sort > files.list; \
	tar --uid 0 --gid 0 --numeric-owner --format ustar -cf - -T files.list | gzip -n > "$$tmpdest"; \
	mv "$$tmpdest" "$$dest"; \
	rm -rf "$$tmpdir"; \
	trap - EXIT; \
	echo "  $(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$(PKG_PREFIX)-$(VERSION).tgz"

publish-common:
	cd $(PKG_DIR) && npm install && npm run build
	@echo "==> Verifying packed plugin"
	cd $(PKG_DIR) && npm run verify:packed
	@echo "==> Publishing $(PKG_NAME)@$(VERSION) to tnpm"
	cd $(PKG_DIR) && tnpm publish
	@echo "==> Published $(PKG_NAME)@$(VERSION)"

# ── Install ────────────────────────────────────────────────────────

install:
	@if [ -z "$(EDITION)" ]; then \
		echo "Usage: make install EDITION=aivision|common"; exit 1; fi
	@$(MAKE) --no-print-directory install-$(EDITION)

# Unified local install for aivision/common editions
_install-local:
	cd $(PKG_DIR) && npm install && EDITION=$(EDITION) npm run build
	@mkdir -p $(INSTALL_DIR)/dist
	rm -rf $(INSTALL_DIR)/dist/*
	cp -r $(PKG_DIR)/dist/* $(INSTALL_DIR)/dist/
	cp $(PKG_DIR)/package.json $(INSTALL_DIR)/
	@if [ -f $(PKG_DIR)/openclaw.plugin.json ]; then cp $(PKG_DIR)/openclaw.plugin.json $(INSTALL_DIR)/; fi
	@echo "Installed $(PKG_NAME) ($(or $(EDITION),common)) to $(INSTALL_DIR)/"

install-aivision:
	@$(MAKE) --no-print-directory _install-local EDITION=aivision

install-common:
	@$(MAKE) --no-print-directory _install-local EDITION=common

install-link: dev
	@mkdir -p $(INSTALL_DIR)
	@rm -rf $(INSTALL_DIR)/dist
	ln -sf $(abspath $(PKG_DIR)/dist) $(INSTALL_DIR)/dist
	@echo "Linked $(INSTALL_DIR)/dist -> $(abspath $(PKG_DIR)/dist)"

# ── Replay plugin ─────────────────────────────────────────────────

replay-plugin-deps:
	@if ! (cd $(PKG_DIR) && node -e "require.resolve('esbuild'); require.resolve('typescript'); require.resolve('openclaw/plugin-sdk/plugin-entry')" >/dev/null 2>&1); then \
		cd $(PKG_DIR) && npm install --registry="$(NPM_REGISTRY)" --replace-registry-host=always; \
	fi
	@if [ "$$(readlink $(REPLAY_PKG_DIR)/node_modules 2>/dev/null)" != "$(abspath $(PKG_DIR)/node_modules)" ]; then \
		rm -rf $(REPLAY_PKG_DIR)/node_modules; \
		ln -s $(abspath $(PKG_DIR)/node_modules) $(REPLAY_PKG_DIR)/node_modules; \
	fi
	@cd $(REPLAY_PKG_DIR) && node -e "require.resolve('esbuild'); require.resolve('typescript'); require.resolve('openclaw/plugin-sdk/plugin-entry')"

replay-plugin-build: replay-plugin-deps
	cd $(REPLAY_PKG_DIR) && npm run build

replay-plugin-install: replay-plugin-build
	@mkdir -p $(REPLAY_INSTALL_DIR)/dist
	rm -rf $(REPLAY_INSTALL_DIR)/dist/*
	cp -r $(REPLAY_PKG_DIR)/dist/* $(REPLAY_INSTALL_DIR)/dist/
	cp $(REPLAY_PKG_DIR)/package.json $(REPLAY_INSTALL_DIR)/
	cp $(REPLAY_PKG_DIR)/openclaw.plugin.json $(REPLAY_INSTALL_DIR)/
	$(OPENCLAW_CLI) plugins enable openclaw-replay
	$(OPENCLAW_CLI) config set plugins.entries.openclaw-replay.config.captureEnabled true --strict-json
	$(OPENCLAW_CLI) config set plugins.entries.openclaw-replay.config.sessionDir '"$(REPLAY_TRANSCRIPT_DIR)"' --strict-json
	$(OPENCLAW_CLI) config set plugins.entries.openclaw-replay.hooks.allowConversationAccess true --strict-json
	@echo "Installed and registered openclaw-replay at $(REPLAY_INSTALL_DIR)/"
	@echo "Transcript directory: $(REPLAY_TRANSCRIPT_DIR)"

replay-plugin-uninstall:
	@$(OPENCLAW_CLI) plugins disable openclaw-replay >/dev/null 2>&1 || true
	rm -rf $(REPLAY_INSTALL_DIR)
	@echo "Uninstalled openclaw-replay code from $(REPLAY_INSTALL_DIR)/; transcripts were preserved."

test:
	cd $(PKG_DIR) && npm run typecheck
	cd $(PKG_DIR) && npm run test:edition
	cd $(REPLAY_PKG_DIR) && npm run typecheck

# ── File listing ───────────────────────────────────────────────────

transcripts:
	@echo "Transcripts:"
	@ls -lh "$(TRANSCRIPT_DIR)"/*.jsonl 2>/dev/null || echo "  (none)"

replays:
	@echo "Replays:"
	@ls -lh "$(REPLAY_DIR)"/*.jsonl 2>/dev/null || echo "  (none)"

list-artifacts:
	@echo "Artifacts (openclaw):"
	@found=0; \
	for sub in aivision common; do \
		dir="$(DIST_DIR)/$$sub/$(VERSION)"; \
		if [ -d "$$dir" ]; then \
			echo ""; echo "  releases/openclaw/$$sub/$(VERSION)/"; \
			ls -1h "$$dir" | sed 's/^/    /'; \
			found=1; \
		fi; \
	done; \
	if [ "$$found" = "0" ]; then echo "  (none)"; fi

# ── Cleanup ────────────────────────────────────────────────────────

clean:
	rm -f "$(REPLAY_DIR)"/spans_*.jsonl "$(REPLAY_DIR)"/events_*.jsonl "$(REPLAY_DIR)"/logs_*.jsonl "$(REPLAY_DIR)"/metrics_*.jsonl
	@echo "[$(AGENT)] Cleaned replay files."

# ── Help ───────────────────────────────────────────────────────────

help:
	@echo "openclaw Observability Makefile"
	@echo ""
	@echo "  Pipeline:"
	@echo "  make replay-all              Convert all transcripts to replays"
	@echo "  make replay TRANSCRIPT=<f>   Convert single transcript"
	@echo "  make validate                Replay-all then validate"
	@echo "  make validate-one FILE=<f>   Validate single replay"
	@echo "  make report TRANSCRIPT=<f>   Send transcript to fixed local collector 127.0.0.1:4319"
	@echo "    SHIFT=1                    Stretch+shift timestamps to current time"
	@echo ""
	@echo "  Build:"
	@echo "  make build                   Build observ-openclaw + openclaw-replay"
	@echo "  make build ED=aivision       Build specific edition (short alias)"
	@echo "  make build-edition EDITION=  Build edition (aivision|common)"
	@echo "  make build-all               Build all editions"
	@echo "  make install ED=aivision     Install edition locally"
	@echo "  make install ED=common       Install common edition locally"
	@echo "  make dev                     Build for local development"
	@echo "  make install-link            Symlink dist (dev)"
	@echo "  make test                    Run typecheck and edition tests"
	@echo ""
	@echo ""
	@echo "  Replay Plugin:"
	@echo "  make replay-plugin-build      Build openclaw-replay"
	@echo "  make replay-plugin-install    Build, register, and configure openclaw-replay"
	@echo "    REPLAY_TRANSCRIPT_DIR=<dir> Override transcript directory"
	@echo "  make replay-plugin-uninstall  Remove openclaw-replay from extensions"
	@echo ""
	@echo "  Golden:"
	@echo "  make golden-replay-all       Replay golden/transcripts into golden/replays"
	@echo "  make golden-check            Compare replays against golden baseline"
	@echo "  make golden-update           Update golden from current replays"
	@echo "  make golden-init             Show available replays for golden init"
	@echo ""
	@echo "  Tools:"
	@echo "  make transcripts             List transcript files"
	@echo "  make replays                 List replay files"
	@echo "  make list-artifacts          List build artifacts"
	@echo "  make clean                   Remove replay files"
	@echo ""
	@echo "  VERSION=x.y.z               Override package version"
