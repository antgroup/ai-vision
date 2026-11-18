# weaver - Observability Validation Makefile
#
# Usage:
#   make build                                    # Build weaver-guard
#   make validate AGENT=claudecode                # Validate agent's replays
#   make validate AGENT=claudecode FORMAT=json    # JSON output
#   make validate AGENT=claudecode FILE=path.jsonl # Validate specific file
#   make validate-all                             # Validate all agents
#   make serve-restart                            # Restart local AiVision OTLP service

AGENTS       := claudecode codex openclaw opencode
FORMAT       ?= text
LISTEN       ?= 127.0.0.1:4319
WEAVER_GUARD := weaver-guard/target/debug/weaver-guard
RELEASE_TARGET ?= aarch64-apple-darwin
CARGO_PACKAGE_VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' weaver-guard/Cargo.toml | head -1)
RELEASE_VERSION ?= $(CARGO_PACKAGE_VERSION)
PACKAGE_DIR ?= dist/weaver-guard/macos-arm64
RELEASE_BIN := weaver-guard/target/$(RELEASE_TARGET)/release/weaver-guard
WG_RELEASE_DIR ?= ../releases/weaver-guard
WG_BIN_NAME := weaver-guard
ALL_TARGETS := \
	aarch64-apple-darwin \
	x86_64-apple-darwin \
	aarch64-unknown-linux-musl \
	x86_64-unknown-linux-musl \
	aarch64-unknown-linux-gnu.2.17 \
	x86_64-unknown-linux-gnu.2.17
TARGETS ?= $(ALL_TARGETS)
WEAVER_GUARD_OSS_PREFIX ?=
AIVISION_OSS_REMOTE_PREFIX ?= aivision/signed/weaver-guard
AIVISION_SETUP_DIR ?= ../../aivision-setup
AIVISION_OSS_ENV_FILE ?= $(AIVISION_SETUP_DIR)/.env
AIVISION_OSS_MANIFEST ?= $(AIVISION_SETUP_DIR)/crates/aivision-oss/Cargo.toml
AIVISION_OSS_BIN ?= cargo run --manifest-path $(AIVISION_OSS_MANIFEST) --
AIVISION_OSS_NO_PROXY_HOST ?=
AIVISION_OSS_ENV ?= env HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= NO_PROXY=$(AIVISION_OSS_NO_PROXY_HOST)
BACKEND_ASSET_CATALOG ?= backend-assets.json
BACKEND_PLATFORM ?= macos
BACKEND_ARCH ?= arm64
BACKEND_SYNC_TARGETS ?= macos-amd64,macos-arm64,linux-amd64,linux-arm64

.PHONY: build spec-generate spec-check serve serve-stop serve-restart restart validate validate-all golden-check golden-check-all check-release-version release wg release-wg release-macos-arm64 backend-assets-macos-arm64 backend-assets backend-sync backend-sync-upload package-macos-arm64 package publish-macos-arm64 publish help

# --- Build ---

build:
	cd weaver-guard && CARGO_TARGET_DIR=target cargo build

$(WEAVER_GUARD):
	cd weaver-guard && CARGO_TARGET_DIR=target cargo build

# --- Release packaging ---

release-macos-arm64:
	cd weaver-guard && CARGO_TARGET_DIR=target cargo build --release --target $(RELEASE_TARGET) --bin weaver-guard

check-release-version:
	@if [ "$(RELEASE_VERSION)" != "$(CARGO_PACKAGE_VERSION)" ]; then \
		echo "RELEASE_VERSION ($(RELEASE_VERSION)) must match Cargo.toml version ($(CARGO_PACKAGE_VERSION))"; \
		exit 1; \
	fi

release: release-wg
	@:

wg: release-wg
	@:

release-wg: check-release-version
	@set -eu; \
	stage="$(WG_RELEASE_DIR)/$(RELEASE_VERSION)"; \
	tmp="$$(mktemp -d "$${TMPDIR:-/tmp}/weaver-guard-runtime.XXXXXX")"; \
	trap 'rm -rf "$$tmp"' EXIT INT TERM; \
	mkdir -p "$$stage"; \
	rm -f "$$stage/weaver-guard-$(RELEASE_VERSION)-"* "$$stage/aivision-guard-$(RELEASE_VERSION)-"*; \
	for target in $(TARGETS); do \
		if echo "$$target" | grep -q darwin; then platform=darwin; \
		elif echo "$$target" | grep -q "linux-musl"; then platform=linux-musl; \
		elif echo "$$target" | grep -q "linux-gnu"; then platform=linux-gnu; \
		else platform=windows; fi; \
		arch=$$(echo "$$target" | sed -e 's/^aarch64-.*/arm64/' -e 's/^x86_64-.*/x64/'); \
		ext=""; case "$$target" in *windows*) ext=".exe" ;; esac; \
		target_dir=$$(echo "$$target" | sed 's/\.[0-9]*\.[0-9]*$$//'); \
		dest="$(WG_BIN_NAME)-$(RELEASE_VERSION)-$${platform}-$${arch}$${ext}"; \
		echo ""; \
		echo "--- [weaver-guard] $${platform}-$${arch} ($$target) ---"; \
		if echo "$$target" | grep -q darwin; then \
			build_cmd="cargo build"; \
		else \
			build_cmd="cargo zigbuild"; \
		fi; \
		cd weaver-guard && CARGO_TARGET_DIR=target $$build_cmd --release --target "$$target" --bin "$(WG_BIN_NAME)"; \
		cd ..; \
		src="weaver-guard/target/$$target_dir/release/$(WG_BIN_NAME)$${ext}"; \
		if [ ! -f "$$src" ]; then \
			echo "Build failed: $$src not found" >&2; \
			exit 1; \
		fi; \
		install -m 0755 "$$src" "$$stage/$$dest"; \
		echo "✓ $$dest"; \
	done; \
	mkdir -p "$$tmp/runtime/scripts"; \
	cp -R policies "$$tmp/runtime/policies"; \
	cp -R ../docs/semconv "$$tmp/runtime/semconv"; \
	cp weaver-guard/config.example.yaml "$$tmp/runtime/config.example.yaml"; \
		cp scripts/victoria-macos-arm64.sh "$$tmp/runtime/scripts/"; \
		cp scripts/jaeger-macos-arm64.sh "$$tmp/runtime/scripts/"; \
		python3 scripts/backend_assets.py manifest \
			--catalog "$(BACKEND_ASSET_CATALOG)" \
			--platform "$(BACKEND_PLATFORM)" \
			--arch "$(BACKEND_ARCH)" \
			--guard-version "$(RELEASE_VERSION)" \
			--oss-prefix "$(WEAVER_GUARD_OSS_PREFIX)" \
			> "$$tmp/runtime/runtime-manifest.json"; \
		find "$$tmp/runtime" \( -name '._*' -o -name '.DS_Store' \) -delete; \
		COPYFILE_DISABLE=1 tar -C "$$tmp/runtime" -czf "$$stage/runtime-assets.tar.gz" .; \
	echo "✓ runtime-assets.tar.gz"; \
		echo ""; \
		echo "weaver-guard release artifacts staged at $$stage"; \
		ls -1 "$$stage" | sed 's/^/  /'

package package-macos-arm64: check-release-version release-macos-arm64
	@set -eu; \
	stage="$(PACKAGE_DIR)"; \
	version_dir="$$stage/$(RELEASE_VERSION)"; \
	tmp="$$(mktemp -d "$${TMPDIR:-/tmp}/weaver-guard-package.XXXXXX")"; \
	trap 'rm -rf "$$tmp"' EXIT INT TERM; \
	mkdir -p "$$stage"; \
	rm -rf "$$version_dir"; \
	rm -f "$$stage/weaver-guard" "$$stage/aivision-guard" "$$stage/install-aivision-guard.sh" "$$stage/runtime-assets.tar.gz"; \
	mkdir -p "$$version_dir"; \
	install -m 0755 scripts/install-weaver-guard.sh "$$stage/install-weaver-guard.sh"; \
	install -m 0755 "$(RELEASE_BIN)" "$$version_dir/weaver-guard"; \
	mkdir -p "$$tmp/runtime/scripts"; \
	cp -R policies "$$tmp/runtime/policies"; \
	cp -R ../docs/semconv "$$tmp/runtime/semconv"; \
	cp weaver-guard/config.example.yaml "$$tmp/runtime/config.example.yaml"; \
	cp scripts/victoria-macos-arm64.sh "$$tmp/runtime/scripts/"; \
	cp scripts/jaeger-macos-arm64.sh "$$tmp/runtime/scripts/"; \
		python3 scripts/backend_assets.py manifest \
			--catalog "$(BACKEND_ASSET_CATALOG)" \
			--platform "$(BACKEND_PLATFORM)" \
			--arch "$(BACKEND_ARCH)" \
			--guard-version "$(RELEASE_VERSION)" \
			--oss-prefix "$(WEAVER_GUARD_OSS_PREFIX)" \
			> "$$tmp/runtime/runtime-manifest.json"; \
		find "$$tmp/runtime" \( -name '._*' -o -name '.DS_Store' \) -delete; \
		COPYFILE_DISABLE=1 tar -C "$$tmp/runtime" -czf "$$version_dir/runtime-assets.tar.gz" .; \
		echo "[package] staged $$stage/install-weaver-guard.sh"; \
		echo "[package] staged $$version_dir/weaver-guard"; \
		echo "[package] staged $$version_dir/runtime-assets.tar.gz"

backend-assets backend-assets-macos-arm64:
	python3 scripts/backend_assets.py stage \
		--catalog "$(BACKEND_ASSET_CATALOG)" \
		--platform "$(BACKEND_PLATFORM)" \
		--arch "$(BACKEND_ARCH)" \
		--dest-dir "$(PACKAGE_DIR)"

backend-sync:
	python3 scripts/backend-sync.py \
		--catalog "$(BACKEND_ASSET_CATALOG)" \
		--dest-dir "dist/weaver-guard/backend-assets" \
		--oss-prefix "$(WEAVER_GUARD_OSS_PREFIX)" \
		--remote-prefix "$(AIVISION_OSS_REMOTE_PREFIX)" \
		--targets "$(BACKEND_SYNC_TARGETS)"

backend-sync-upload:
	python3 scripts/backend-sync.py \
		--catalog "$(BACKEND_ASSET_CATALOG)" \
		--dest-dir "dist/weaver-guard/backend-assets" \
		--oss-prefix "$(WEAVER_GUARD_OSS_PREFIX)" \
		--remote-prefix "$(AIVISION_OSS_REMOTE_PREFIX)" \
		--env-file "$(AIVISION_OSS_ENV_FILE)" \
		--aivision-oss-manifest "$(AIVISION_OSS_MANIFEST)" \
		--targets "$(BACKEND_SYNC_TARGETS)" \
		--upload

publish publish-macos-arm64: package-macos-arm64 backend-assets-macos-arm64
	@set -eu; \
	if [ -f "$(AIVISION_OSS_ENV_FILE)" ]; then \
		set -a; . "$(AIVISION_OSS_ENV_FILE)"; set +a; \
	fi; \
	upload() { \
		echo "[publish] $$1 -> $$2"; \
		$(AIVISION_OSS_ENV) $(AIVISION_OSS_BIN) upload "$$1" "$$2"; \
	}; \
	upload_list="$(PACKAGE_DIR)/backend-upload-list.tsv"; \
	python3 scripts/backend_assets.py upload-list \
		--catalog "$(BACKEND_ASSET_CATALOG)" \
		--platform "$(BACKEND_PLATFORM)" \
		--arch "$(BACKEND_ARCH)" \
		--dest-dir "$(PACKAGE_DIR)" \
		--remote-prefix "$(AIVISION_OSS_REMOTE_PREFIX)" \
		> "$$upload_list"; \
	while IFS="$$(printf '\t')" read -r local remote; do \
		[ -n "$$local" ] || continue; \
		upload "$$local" "$$remote"; \
	done < "$$upload_list"; \
	upload "$(PACKAGE_DIR)/$(RELEASE_VERSION)/weaver-guard" "$(AIVISION_OSS_REMOTE_PREFIX)/$(RELEASE_VERSION)/weaver-guard"; \
		upload "$(PACKAGE_DIR)/$(RELEASE_VERSION)/runtime-assets.tar.gz" "$(AIVISION_OSS_REMOTE_PREFIX)/$(RELEASE_VERSION)/runtime-assets.tar.gz"; \
		upload "$(PACKAGE_DIR)/install-weaver-guard.sh" "$(AIVISION_OSS_REMOTE_PREFIX)/install-weaver-guard.sh"

# --- Canonical semantic spec ---

spec-generate:
	python3 scripts/spec_sync.py generate

spec-check:
	python3 scripts/spec_sync.py check

# --- Local AiVision OTLP service ---

serve: $(WEAVER_GUARD)
	@mkdir -p live live/replays
	$(WEAVER_GUARD) serve \
		--listen $(LISTEN) \
		--policies-dir policies \
		--semconv-dir ../docs/semconv \
		--victoria-home .victoria \
		--victoria-script scripts/victoria-macos-arm64.sh \
		--victoria-metrics-addr 127.0.0.1:8428 \
		--victoria-logs-addr 127.0.0.1:9428 \
		--jaeger-home .jaeger \
		--jaeger-script scripts/jaeger-macos-arm64.sh \
		--jaeger-ui-addr 127.0.0.1:16686 \
		--jaeger-otlp-http-addr 127.0.0.1:14318 \
		--jaeger-otlp-grpc-addr 127.0.0.1:14317 \
		--diagnostic-log live/diagnostics.jsonl \
		--replay-dir live/replays \
		--forward-policy always \
		--fail-on violation \
		--max-body-bytes 16777216 \
		--recent-limit 500

serve-stop:
	@PORT="$$(printf '%s' "$(LISTEN)" | awk -F: '{print $$NF}')"; \
	PIDS="$$(lsof -nP -tiTCP:$$PORT -sTCP:LISTEN 2>/dev/null || true)"; \
	for pid in $$PIDS; do \
		CMD="$$(ps -p $$pid -o command= 2>/dev/null || true)"; \
		case "$$CMD" in \
			*"weaver-guard"*" serve "*) echo "[serve-stop] stopping weaver-guard serve pid=$$pid"; kill $$pid ;; \
			*) echo "[serve-stop] skip pid=$$pid cmd=$$CMD" ;; \
		esac; \
	done
	@for file in .jaeger/run/jaeger-all-in-one.pid .victoria/run/metrics.pid .victoria/run/logs.pid; do \
		if [ -f "$$file" ]; then \
			pid="$$(cat "$$file" 2>/dev/null || true)"; \
			if [ -n "$$pid" ] && kill -0 "$$pid" 2>/dev/null; then \
				echo "[serve-stop] stopping child pid=$$pid ($$file)"; kill "$$pid" || true; \
			fi; \
			rm -f "$$file"; \
		fi; \
	done

serve-restart restart:
	@$(MAKE) --no-print-directory serve-stop LISTEN="$(LISTEN)"
	@PORTS="$$(printf '%s' "$(LISTEN)" | awk -F: '{print $$NF}') 8428 9428 16686 14318 14317"; \
	for attempt in $$(seq 1 50); do \
		BUSY=""; \
		for port in $$PORTS; do \
			if lsof -nP -tiTCP:$$port -sTCP:LISTEN >/dev/null 2>&1; then \
				BUSY="$$BUSY $$port"; \
			fi; \
		done; \
		if [ -z "$$BUSY" ]; then \
			echo "[serve-restart] ports released"; \
			exit 0; \
		fi; \
		sleep 0.2; \
	done; \
	echo "[serve-restart] ports still busy:$$BUSY"; \
	for port in $$BUSY; do lsof -nP -iTCP:$$port -sTCP:LISTEN || true; done; \
	exit 1
	@echo "[serve-restart] starting local AiVision OTLP service on $(LISTEN)"
	@$(MAKE) --no-print-directory serve LISTEN="$(LISTEN)" FORMAT="$(FORMAT)"

# --- Validation ---

validate: spec-check $(WEAVER_GUARD)
	@if [ -z "$(AGENT)" ]; then \
		echo "Usage: make validate AGENT=claudecode [FILE=path] [FORMAT=json]"; exit 1; fi
	@REPLAY_PATH="$(or $(FILE),../$(AGENT)/replays/)"; \
	SEMCONV_AGENT_FLAG=""; \
	if [ -d "../docs/semconv-$(AGENT)" ]; then \
		SEMCONV_AGENT_FLAG="--semconv-dir ../docs/semconv-$(AGENT)"; \
	fi; \
	$(WEAVER_GUARD) validate "$$REPLAY_PATH" \
		--policies-dir policies \
		--semconv-dir ../docs/semconv \
		$$SEMCONV_AGENT_FLAG \
		--format $(FORMAT)

validate-all: spec-check $(WEAVER_GUARD)
	@for agent in $(AGENTS); do \
		REPLAY_DIR="../$$agent/replays/"; \
		if [ -d "$$REPLAY_DIR" ] && ls "$$REPLAY_DIR"*.jsonl >/dev/null 2>&1; then \
			echo "=== Validating $$agent ===" && \
			$(MAKE) validate AGENT=$$agent FORMAT=$(FORMAT) || true; \
		else \
			echo "=== Skipping $$agent (no replays) ==="; \
		fi; \
	done

# --- Golden example regression ---

golden-check:
	@if [ -z "$(AGENT)" ]; then \
		echo "Usage: make golden-check AGENT=claudecode"; exit 1; fi
	$(MAKE) -C ../$(AGENT) golden-check FORMAT=$(FORMAT)

golden-check-all: $(WEAVER_GUARD)
	@for agent in $(AGENTS); do \
		if [ -d "../$$agent/golden" ]; then \
			echo "=== Golden check: $$agent ===" && \
			$(MAKE) -C ../$$agent golden-check FORMAT=$(FORMAT) || exit 1; \
		fi; \
	done

# --- Help ---

help:
	@echo "weaver Observability Validation Makefile"
	@echo ""
	@echo "  make build                          Build weaver-guard"
	@echo "  make release-wg                     Build/stage multi-platform weaver-guard release under ../releases/weaver-guard/"
	@echo "  make release wg                     Alias for release-wg"
	@echo "  make release-macos-arm64            Build release binary for macOS arm64"
	@echo "  make backend-assets-macos-arm64     Stage backend archives from backend-assets.json"
	@echo "  make backend-sync                   Sync latest mainstream backend packages locally"
	@echo "  make backend-sync-upload            Sync and upload latest mainstream backend packages"
	@echo "  make package-macos-arm64            Stage weaver-guard release artifacts"
	@echo "  make package                        Alias for package-macos-arm64"
	@echo "  make publish-macos-arm64            Upload binary, runtime, and backend assets"
	@echo "  make spec-generate                  Generate docs/Rego data from canonical specs"
	@echo "  make spec-check                     Check generated spec artifacts are in sync"
	@echo "  make serve                          Start local AiVision OTLP service"
	@echo "  make serve-stop                     Stop local AiVision service and child backends"
	@echo "  make serve-restart                  Stop, wait for ports, then start service"
	@echo "  make restart                        Alias for serve-restart"
	@echo "  make validate AGENT=<name>          Validate agent's replays"
	@echo "  make validate-all                   Validate all agents"
	@echo ""
	@echo "Golden example regression:"
	@echo "  make golden-check AGENT=<name>   Check golden baselines for one agent"
	@echo "  make golden-check-all            Check golden baselines for all agents"
	@echo ""
	@echo "  AGENT=claudecode|codex|openclaw|opencode"
	@echo "  FORMAT=text|json (default: text)"
	@echo "  FILE=<path>      Override replay path"
