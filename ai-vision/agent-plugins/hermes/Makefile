# hermes - AI Agent Observability Makefile
#
# Pipeline:
#   make replay-all                              Convert all transcripts to replays
#   make replay TRANSCRIPT=transcripts/xxx.jsonl Convert one transcript
#   make validate                                Validate all replays
#   make validate-one FILE=replays/xxx.jsonl     Validate one replay
#
# Tools:
#   make report / report-shift / golden-collect / install / rollback

SHELL := /bin/bash --noprofile --norc

AGENT         := hermes
override AIVISION_HOME := $(or $(strip $(AIVISION_HOME)),$(HOME)/.aivision)
override ARTIFACT_DIR := $(or $(strip $(ARTIFACT_DIR)),$(AIVISION_HOME)/hermes)
override TRANSCRIPT_DIR := $(or $(strip $(TRANSCRIPT_DIR)),$(strip $(OBSERV_HERMES_SESSION_DIR)),$(ARTIFACT_DIR)/transcripts)
override REPLAY_DIR := $(or $(strip $(REPLAY_DIR)),$(strip $(OBSERV_HERMES_REPLAY_DIR)),$(ARTIFACT_DIR)/replays)
override GOLDEN_DIR := $(or $(strip $(GOLDEN_DIR)),$(ARTIFACT_DIR)/golden)
override GOLDEN_BASELINE_DIR := $(or $(strip $(GOLDEN_BASELINE_DIR)),$(GOLDEN_DIR)/baselines)
PKG_DIR       := observ_hermes
CORE_DIR      := hermes-core
REPLAY_PKG_DIR := hermes-replay
HERMES_COMMAND := $(shell command -v hermes 2>/dev/null)
HERMES_PYTHON := $(shell if [ -n "$(HERMES_COMMAND)" ]; then \
	hermes_bin=$$(realpath "$(HERMES_COMMAND)" 2>/dev/null || printf '%s' "$(HERMES_COMMAND)"); \
	candidate="$$(dirname "$$hermes_bin")/python"; \
	if [ -x "$$candidate" ]; then printf '%s' "$$candidate"; fi; \
fi)
PYTHON        ?= $(or $(HERMES_PYTHON),python3)
REPLAY_CLI    ?= $(PYTHON) -m hermes_replay.replay
REPORT_CLI    ?= $(PYTHON) -m hermes_replay.report
LIFECYCLE_CLI = $(PYTHON) scripts/hermes_lifecycle.py --python "$(PYTHON)" \
	--cohort-root "$(COHORT_ROOT)" --plugin "$(INSTALL_DIR)" \
	--config "$(INSTALL_CONFIG)" --legacy-env "$(LEGACY_ENV)"
GOLDEN_COLLECT_CLI := $(PYTHON) scripts/golden_collect.py
GOLDEN_FINALIZE_CLI := $(PYTHON) scripts/golden_finalize.py
WEAVER_GUARD  := ../weaver/weaver-guard/target/debug/weaver-guard
SEMCONV_BASE  := ../docs/semconv
POLICIES      := ../weaver/policies
# Short alias: TS=session_xxx.jsonl → TRANSCRIPT=$(TRANSCRIPT_DIR)/session_xxx.jsonl
ifdef TS
ifneq ($(filter /% ./%,$(TS)),)
TRANSCRIPT := $(TS)
else
TRANSCRIPT := $(TRANSCRIPT_DIR)/$(TS)
endif
endif
FORMAT        ?= text
GOLDEN_CONFIG := ../weaver/golden-compare.toml
AIVISION_URL  ?= http://127.0.0.1:4319

TRANSCRIPTS   := $(wildcard $(TRANSCRIPT_DIR)/*.jsonl)
REPLAYS       := $(wildcard $(REPLAY_DIR)/spans_*.jsonl)

.PHONY: build replay-all replay validate validate-one report report-shift \
        golden-collect golden-finalize \
        golden-check golden-update golden-init \
        install uninstall rollback install-cohort rollback-cohort \
        replay-plugin-build replay-plugin-install replay-plugin-uninstall \
        test test-production test-replay clean transcripts replays list-artifacts help

# Explicit opt-in acceptance for the official Hermes-owned Relay runtime.
# Credentials are read from NVIDIA_API_KEY and a private target JSON, never argv.
.PHONY: relay-native-e2e
relay-native-e2e:
	@test -n "$(RELAY_RUNTIME)" -a -n "$(RELAY_TARGETS)" -a -n "$(RELAY_OUT)" -a -n "$(RELAY_MODEL)" || { \
		echo "Set RELAY_RUNTIME, RELAY_TARGETS, RELAY_OUT and RELAY_MODEL; see docs/RELAY_INTEGRATION.md"; exit 2; }
	$(PYTHON) tests/relay_native_e2e.py --live --hermes "$(RELAY_RUNTIME)" \
		--target-file "$(RELAY_TARGETS)" --out "$(RELAY_OUT)" --model "$(RELAY_MODEL)" \
		$(if $(RELAY_PROXY),--proxy "$(RELAY_PROXY)")

# No external model credentials; use an environment containing nemo-relay 0.10.4.
.PHONY: relay-redaction-policy relay-redaction-e2e
relay-redaction-policy:
	$(PYTHON) tests/relay_redaction_policy.py

relay-redaction-e2e:
	@test -n "$(RELAY_RUNTIME)" -a -n "$(RELAY_OUT)" || { \
		echo "Set RELAY_RUNTIME and RELAY_OUT; see docs/RELAY_PRIVACY.md"; exit 2; }
	$(PYTHON) tests/relay_redaction_e2e.py --hermes "$(RELAY_RUNTIME)" --out "$(RELAY_OUT)" \
		$(if $(RELAY_SOURCE),--hermes-source "$(RELAY_SOURCE)") \
		$(if $(RELAY_TARGETS),--target-file "$(RELAY_TARGETS)")

define require-replay-package
	@$(PYTHON) -c "import hermes_replay" >/dev/null 2>&1 || { \
		echo "[$(AGENT)] hermes-replay is not installed; run 'make replay-plugin-install'." >&2; \
		exit 2; \
	}
endef

# ── Weaver-guard auto-build ───────────────────────────────────────

$(WEAVER_GUARD):
	@echo "[$(AGENT)] Building weaver-guard..."
	@cd ../weaver/weaver-guard && CARGO_TARGET_DIR=target cargo build 2>/dev/null || { \
		echo "[$(AGENT)] cargo build failed, looking for pre-built binary..."; \
		MAIN_ROOT=$$(git worktree list --porcelain | head -1 | cut -d' ' -f2); \
		MAIN_BIN="$$MAIN_ROOT/weaver/weaver-guard/target/debug/weaver-guard"; \
		TARGET="$(CURDIR)/$@"; \
		if [ -x "$$MAIN_BIN" ]; then \
			mkdir -p $$(dirname "$$TARGET"); \
			ln -sf "$$MAIN_BIN" "$$TARGET"; \
			echo "[$(AGENT)] Linked to main worktree binary"; \
		else \
			echo "[$(AGENT)] ERROR: No pre-built binary found. Build in main repo first."; exit 1; \
		fi; \
	}

# ── Replay ─────────────────────────────────────────────────────────

replay-all:
	$(require-replay-package)
	@if ls "$(TRANSCRIPT_DIR)"/*.jsonl >/dev/null 2>&1; then \
		if [ ! -d "$(REPLAY_DIR)" ]; then install -d -m 700 "$(REPLAY_DIR)"; fi; \
		for f in "$(TRANSCRIPT_DIR)"/*.jsonl; do \
			base=$$(basename "$$f" .jsonl); \
			echo "[$(AGENT)] $$f -> $(REPLAY_DIR)/"; \
			$(REPLAY_CLI) "$$f" --otel "$(REPLAY_DIR)" || exit 1; \
		done; \
	else \
		echo "[$(AGENT)] No transcript files found in $(TRANSCRIPT_DIR)/" >&2; exit 1; \
	fi

replay:
	$(require-replay-package)
	@if [ -z "$(TRANSCRIPT)" ]; then \
		echo "Usage: make replay TS=session_xxx.jsonl"; exit 1; fi
	@if [ -d "$(TRANSCRIPT)" ]; then \
		if [ ! -d "$(REPLAY_DIR)" ]; then install -d -m 700 "$(REPLAY_DIR)"; fi; \
		for f in "$(TRANSCRIPT)"/*.jsonl; do \
			[ -f "$$f" ] || continue; \
			echo "[$(AGENT)] $$f -> $(REPLAY_DIR)/"; \
			$(REPLAY_CLI) "$$f" --otel "$(REPLAY_DIR)" || exit 1; \
		done; \
	else \
		echo "[$(AGENT)] $(TRANSCRIPT) -> $(REPLAY_DIR)/"; \
		$(REPLAY_CLI) "$(TRANSCRIPT)" --otel "$(REPLAY_DIR)"; \
	fi

# ── Validate ───────────────────────────────────────────────────────

validate: $(WEAVER_GUARD)
	@echo "[$(AGENT)] Validating all replays..."
	@if ls "$(REPLAY_DIR)"/*.jsonl >/dev/null 2>&1; then \
		$(WEAVER_GUARD) validate "$(REPLAY_DIR)" \
			--policies-dir $(POLICIES) \
			--semconv-dir $(SEMCONV_BASE) \
			--format $(FORMAT); \
	else \
		echo "[$(AGENT)] No replay files to validate." >&2; exit 1; \
	fi

validate-one: $(WEAVER_GUARD)
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make validate-one FILE=$(REPLAY_DIR)/spans_xxx.jsonl"; exit 1; fi
	@echo "[$(AGENT)] Validating $(FILE)"
	@$(WEAVER_GUARD) validate "$(FILE)" \
		--policies-dir $(POLICIES) \
		--semconv-dir $(SEMCONV_BASE) \
		--format $(FORMAT)

# ── Report ────────────────────────────────────────────────────────

report:
	$(require-replay-package)
	@if ! ls "$(REPLAY_DIR)"/*.jsonl >/dev/null 2>&1; then \
		echo "[$(AGENT)] No replay files to report in $(REPLAY_DIR)."; exit 1; \
	fi
	@$(REPORT_CLI) "$(REPLAY_DIR)" --base-url "$(AIVISION_URL)"

report-shift:
	$(require-replay-package)
	@if [ -n "$(TRANSCRIPT)" ]; then \
		$(MAKE) replay TRANSCRIPT="$(TRANSCRIPT)" REPLAY_DIR="$(REPLAY_DIR)"; \
	fi
	@if ! ls "$(REPLAY_DIR)"/*.jsonl >/dev/null 2>&1; then \
		echo "[$(AGENT)] No replay files to shift/report in $(REPLAY_DIR)."; exit 1; \
	fi
	@$(REPORT_CLI) "$(REPLAY_DIR)" --shift --base-url "$(AIVISION_URL)"

# ── Build & Install ────────────────────────────────────────────────

build:
	@$(PYTHON) -m compileall -q $(CORE_DIR)/aivision_hermes_core $(PKG_DIR) scripts
	@echo "[$(AGENT)] Core and production Python sources compiled"

EDITION     ?= aivision
HERMES_HOME ?= $(HOME)/.hermes
INSTALL_DIR ?= $(HERMES_HOME)/plugins/observ_hermes
INSTALL_CONFIG ?= $(HERMES_HOME)/observ-hermes.json
LEGACY_ENV ?= $(HERMES_HOME)/observ-hermes.env
CONFIG_TEMPLATE := $(PKG_DIR)/configs/$(EDITION).json
COHORT_ROOT ?= $(HERMES_HOME)/plugin-backups/aivision-hermes-cohort
override REPLAY_BUILD_DIR := $(or $(strip $(REPLAY_BUILD_DIR)),$(CURDIR)/dist/hermes-replay)

install: build
	@test -f "$(CONFIG_TEMPLATE)" || { echo "[$(AGENT)] Unknown edition: $(EDITION)"; exit 1; }
	@$(LIFECYCLE_CLI) install-product \
		--product production --artifact "$(PKG_DIR)" --core "$(CORE_DIR)" \
		--config-template "$(CONFIG_TEMPLATE)"
	@echo "[$(AGENT)] Installed (edition=$(EDITION)); rollback: make rollback INSTALL_DIR='$(INSTALL_DIR)'"

rollback:
	@$(LIFECYCLE_CLI) rollback-product --product production

uninstall:
	@$(LIFECYCLE_CLI) uninstall-product --product production
	@echo "[$(AGENT)] Uninstalled observ-hermes; replay installation and artifacts were preserved."

replay-plugin-build:
	@$(PYTHON) -c 'from pathlib import Path; import sys; root = Path(sys.argv[1]).expanduser().resolve(); home = Path.home().resolve(); cwd = Path.cwd().resolve(); unsafe = {Path("/"), home, cwd, *home.parents, *cwd.parents}; sys.exit("unsafe REPLAY_BUILD_DIR: " + str(root)) if root in unsafe else None' "$(REPLAY_BUILD_DIR)"
	@if [ ! -d "$(REPLAY_BUILD_DIR)/core" ]; then install -d -m 700 "$(REPLAY_BUILD_DIR)/core"; fi
	@if [ ! -d "$(REPLAY_BUILD_DIR)/replay" ]; then install -d -m 700 "$(REPLAY_BUILD_DIR)/replay"; fi
	@find "$(REPLAY_BUILD_DIR)/core" "$(REPLAY_BUILD_DIR)/replay" \
		-maxdepth 1 -type f -name '*.whl' -delete
	@uv build --wheel --out-dir "$(REPLAY_BUILD_DIR)/core" "$(CURDIR)/$(CORE_DIR)"
	@uv build --wheel --out-dir "$(REPLAY_BUILD_DIR)/replay" "$(CURDIR)/$(REPLAY_PKG_DIR)"
	@echo "[$(AGENT)] Built replay-owned core and hermes-replay wheels"

replay-plugin-install: replay-plugin-build
	@$(LIFECYCLE_CLI) install-product \
		--product replay --artifact "$(REPLAY_PKG_DIR)" --core "$(CORE_DIR)"
	@echo "[$(AGENT)] Installed hermes-replay only. Capture remains disabled until explicitly configured."

replay-plugin-uninstall:
	@$(LIFECYCLE_CLI) uninstall-product --product replay
	@echo "[$(AGENT)] Uninstalled hermes-replay; production and transcripts were preserved."

install-cohort: build replay-plugin-build
	@test -f "$(CONFIG_TEMPLATE)" || { echo "[$(AGENT)] Unknown edition: $(EDITION)"; exit 1; }
	@$(LIFECYCLE_CLI) install-cohort \
		--production "$(PKG_DIR)" --replay "$(REPLAY_PKG_DIR)" --core "$(CORE_DIR)" \
		--config-template "$(CONFIG_TEMPLATE)"
	@echo "[$(AGENT)] Installed compatible production + replay cohort"

rollback-cohort:
	@$(LIFECYCLE_CLI) rollback-cohort
	@echo "[$(AGENT)] Restored previous compatible cohort"

test-production:
	@echo "[$(AGENT)] Syntax check..."
	@python3 -c "import ast, sys; \
		files = [f for root in ('$(CORE_DIR)', '$(PKG_DIR)', 'scripts') for f in __import__('glob').glob(root + '/**/*.py', recursive=True)]; \
		[ast.parse(open(f).read()) or print(f'  OK {f}') for f in files]; \
		print(f'All {len(files)} files OK')"
	@uv run --no-project \
		--with 'opentelemetry-api>=1.20.0' \
		--with 'opentelemetry-sdk>=1.20.0' \
		--with 'opentelemetry-exporter-otlp-proto-http>=1.20.0' \
		python -m unittest -v \
			tests.test_config tests.test_config_migration tests.test_normalize \
			tests.test_privacy tests.test_semantic_state tests.test_turn_numbers tests.test_hook_adapters \
			tests.test_native_usage tests.test_execution_usage tests.test_auxiliary_hooks \
			tests.test_telemetry tests.test_privacy_boundary tests.test_protocol_spike \
			tests.test_metric_intervals \
			tests.test_diagnostics \
			tests.test_security_config tests.test_common_schema_alignment

test-replay:
	@PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(CURDIR)/$(REPLAY_PKG_DIR):$(CURDIR)/$(CORE_DIR):$(CURDIR)" \
		$(PYTHON) -m unittest discover -s tests -p 'test_replay*.py' -v
	@PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(CURDIR)/$(REPLAY_PKG_DIR):$(CURDIR)/$(CORE_DIR):$(CURDIR)" \
		$(PYTHON) -m unittest -v tests.test_transcript_integrity tests.test_live_replay_parity \
			tests.test_cohort tests.test_core_boundary tests.test_packaging

test: test-production test-replay
	@uv run --no-project \
		--with 'opentelemetry-api>=1.20.0' \
		--with 'opentelemetry-sdk>=1.20.0' \
		--with 'opentelemetry-exporter-otlp-proto-http>=1.20.0' \
		python -m unittest discover -s tests -v

# ── Golden ─────────────────────────────────────────────────────────

golden-update golden-init: override TRANSCRIPT_DIR := $(GOLDEN_DIR)/transcripts
golden-update golden-init: override REPLAY_DIR := $(GOLDEN_DIR)/replays

golden-collect:
	@$(GOLDEN_COLLECT_CLI) --golden-dir "$(GOLDEN_DIR)" --aivision-url "$(AIVISION_URL)"

golden-finalize:
	@$(GOLDEN_FINALIZE_CLI) --golden-dir "$(GOLDEN_DIR)" \
		--validation "$(GOLDEN_DIR)/validate.json" --plugin-yaml "$(PKG_DIR)/plugin.yaml" \
		--aivision-url "$(AIVISION_URL)"

golden-check:
	@set -eu; \
	if ! ls "$(GOLDEN_DIR)/transcripts"/*.jsonl >/dev/null 2>&1; then \
		echo "No golden transcripts in $(GOLDEN_DIR)/transcripts/." >&2; exit 1; \
	fi; \
	if ! ls "$(GOLDEN_BASELINE_DIR)"/*.jsonl >/dev/null 2>&1; then \
		echo "No golden examples in $(GOLDEN_DIR)/. Run 'make golden-init' first." >&2; exit 1; \
	fi; \
	if [ ! -d "$(GOLDEN_DIR)" ]; then install -d -m 700 "$(GOLDEN_DIR)"; fi; \
	fresh=$$(mktemp -d "$(GOLDEN_DIR)/.golden-check.XXXXXX"); \
	cleanup() { \
		case "$$fresh" in \
			"$(GOLDEN_DIR)"/.golden-check.*) rm -rf -- "$$fresh" ;; \
			*) echo "Refusing to clean unexpected golden temp path: $$fresh" >&2 ;; \
		esac; \
	}; \
	trap cleanup EXIT; \
	$(MAKE) --no-print-directory replay-all \
		TRANSCRIPT_DIR="$(GOLDEN_DIR)/transcripts" REPLAY_DIR="$$fresh" >&2; \
	if ! ls "$$fresh"/*.jsonl >/dev/null 2>&1; then \
		echo "Golden replay generation produced no files." >&2; exit 1; \
	fi; \
	for baseline in "$(GOLDEN_BASELINE_DIR)"/*.jsonl; do \
		actual="$$fresh/$$(basename "$$baseline")"; \
		if [ ! -f "$$actual" ]; then \
			echo "Missing fresh replay for golden baseline: $$actual" >&2; exit 1; \
		fi; \
	done; \
	$(WEAVER_GUARD) compare "$(GOLDEN_BASELINE_DIR)" --replay "$$fresh" \
		--config $(GOLDEN_CONFIG) --diff-only --format json; \
	if [ ! -d "$(GOLDEN_DIR)/replays" ]; then install -d -m 700 "$(GOLDEN_DIR)/replays"; fi; \
	find "$(GOLDEN_DIR)/replays" -maxdepth 1 -type f -name '*.jsonl' -delete; \
	cp "$$fresh"/*.jsonl "$(GOLDEN_DIR)/replays/"

golden-update: validate
	@if [ ! -d "$(GOLDEN_BASELINE_DIR)" ]; then install -d -m 700 "$(GOLDEN_BASELINE_DIR)"; fi
	@for f in "$(REPLAY_DIR)"/*.jsonl; do \
		[ -f "$$f" ] || continue; \
		base=$$(basename "$$f"); \
		if [ -f "$(GOLDEN_BASELINE_DIR)/$$base" ]; then cp "$$f" "$(GOLDEN_BASELINE_DIR)/$$base"; fi; \
	done

golden-init: replay-all validate
	@if ls "$(GOLDEN_BASELINE_DIR)"/*.jsonl >/dev/null 2>&1; then \
		echo "Golden baselines already exist; use 'make golden-update'."; exit 1; fi
	@install -d -m 700 "$(GOLDEN_BASELINE_DIR)"
	@for f in "$(REPLAY_DIR)"/*.jsonl; do \
		[ -f "$$f" ] || continue; cp "$$f" "$(GOLDEN_BASELINE_DIR)/"; \
	done
	@if ! ls "$(GOLDEN_BASELINE_DIR)"/*.jsonl >/dev/null 2>&1; then \
		echo "No validated replay files available for golden initialization."; exit 1; fi
	@chmod 600 "$(GOLDEN_BASELINE_DIR)"/*.jsonl
	@echo "[$(AGENT)] Golden baselines initialized in $(GOLDEN_BASELINE_DIR)"

# ── Tools ──────────────────────────────────────────────────────────

transcripts:
	@ls -la "$(TRANSCRIPT_DIR)"/*.jsonl 2>/dev/null || echo "No transcripts found in $(TRANSCRIPT_DIR)"

replays:
	@ls -la "$(REPLAY_DIR)"/*.jsonl 2>/dev/null || echo "No replays found in $(REPLAY_DIR)"

clean:
	@rm -f "$(REPLAY_DIR)"/spans_*.jsonl "$(REPLAY_DIR)"/events_*.jsonl \
		"$(REPLAY_DIR)"/metrics_*.jsonl "$(REPLAY_DIR)"/diagnostics_*.ndjson
	@echo "[$(AGENT)] Replays cleaned"

list-artifacts:
	@echo "transcripts: $(TRANSCRIPT_DIR)"
	@echo "replays:     $(REPLAY_DIR)"
	@echo "golden:      $(GOLDEN_DIR)"
	@echo "baselines:   $(GOLDEN_BASELINE_DIR)"

help:
	@echo "hermes observability targets:"
	@echo "  replay-all     Convert all transcripts to OTLP replays"
	@echo "  replay         Convert single transcript (TRANSCRIPT=... or TS=...)"
	@echo "  validate       Validate all replays via weaver-guard"
	@echo "  validate-one   Validate single replay (FILE=...)"
	@echo "  report         POST current replay files to AiVision"
	@echo "  report-shift   Shift permitted timestamps and POST replay files"
	@echo "  golden-collect Run sequential real Hermes golden scenarios"
	@echo "  golden-finalize Enrich provenance from replay and validation evidence"
	@echo "  golden-check   Compare current replays with golden baselines"
	@echo "  golden-update  Update existing golden baselines"
	@echo "  golden-init    List replay candidates for golden baselines"
	@echo "  build          Compile Python plugin and scripts"
	@echo "  install        Install the production observ-hermes plugin only"
	@echo "  uninstall      Remove production only; retain replay and shared core when needed"
	@echo "  rollback       Restore retained previous plugin"
	@echo "  replay-plugin-build     Build the independent replay package"
	@echo "  replay-plugin-install   Install only hermes-replay and its three commands"
	@echo "  replay-plugin-uninstall Remove replay without changing production/transcripts"
	@echo "  install-cohort          Atomically stage a compatible production/replay/core cohort"
	@echo "  rollback-cohort         Restore the previous compatible cohort"
	@echo "  test-production         Validate core + production without replay"
	@echo "  test-replay             Validate independent replay ownership"
	@echo "  test           Syntax check all Python files"
	@echo "  transcripts    List transcript files"
	@echo "  replays        List replay files"
	@echo "  clean          Remove all replay files"
