# cdxhooks cross-compilation Makefile
#
# Prerequisites:
#   brew install zig cargo-zigbuild   (or: pip3 install cargo-zigbuild)
#   rustup target add aarch64-apple-darwin x86_64-apple-darwin \
#     aarch64-unknown-linux-musl x86_64-unknown-linux-musl \
#     aarch64-unknown-linux-gnu x86_64-unknown-linux-gnu \
#     aarch64-pc-windows-gnullvm x86_64-pc-windows-gnullvm
#
# Usage:
#   make                   Build all editions for current platform to releases/codex/
#   make aivision          Build aivision edition only
#   make common            Build common edition only
#   make release     Build all editions for all platforms (requires zig)
#   make dev               Build debug binary for local development
#   make install           Alias for install-aivision
#   make install-aivision  Build aivision release + copy to ~/.aivision/cdx/hooks/
#   make install-common    Build common release + copy to ~/.codex/hooks/
#   make test              Run tests (no default features)
#   make clean             Remove releases/codex/
#   HOOK_VERSION=0.1.0 make
#   TARGETS="aarch64-apple-darwin x86_64-apple-darwin" make

SHELL := /bin/bash --noprofile --norc

VERSION   ?= $(or $(V),$(shell grep '^version' Cargo.toml | head -1 | sed 's/.*"\(.*\)".*/\1/'))
DIST_DIR  := ../../releases/codex
CARGO_TARGET_DIR ?= target
export CARGO_TARGET_DIR
BIN_NAME  := cdxhooks

# Detect current platform
HOST_TARGET := $(shell rustc -vV | grep host | awk '{print $$2}')

# All cross-compilation targets
ALL_TARGETS := \
	aarch64-apple-darwin \
	x86_64-apple-darwin \
	aarch64-unknown-linux-musl \
	x86_64-unknown-linux-musl \
	aarch64-unknown-linux-gnu.2.17 \
	x86_64-unknown-linux-gnu.2.17 \
	aarch64-pc-windows-gnullvm \
	x86_64-pc-windows-gnullvm

# Only build current platform by default
TARGETS ?= $(HOST_TARGET)

# ── Phony targets ────────────────────────────────────────────────────
.PHONY: all release aivision common dev install install-aivision install-common test clean check-zigbuild

all: aivision common
	@echo ""
	@echo "=== Build complete ==="
	@$(MAKE) --no-print-directory list-artifacts

aivision: check-zigbuild
	@$(MAKE) --no-print-directory build-edition \
		EDITION=aivision \
		FEATURES=aivision,tool_context \
		BIN_PREFIX=observ-aivision-codex-hook \
		OUT_SUBDIR=aivision

common: check-zigbuild
	@$(MAKE) --no-print-directory build-edition \
		EDITION=common \
		FEATURES=common,tool_context \
		BIN_PREFIX=observ-codex-hook \
		OUT_SUBDIR=common

release:
	@echo "Building all platforms (requires cargo-zigbuild)"
	@TARGETS="$(ALL_TARGETS)" $(MAKE) all

dev:
	cargo build

install: install-aivision

install-aivision:
	cargo build --release --bin $(BIN_NAME)
	@mkdir -p ~/.aivision/cdx/hooks
	cp $(CARGO_TARGET_DIR)/release/$(BIN_NAME) ~/.aivision/cdx/hooks/$(BIN_NAME)
	@echo "Installed $(BIN_NAME) (aivision) to ~/.aivision/cdx/hooks/$(BIN_NAME)"

install-common:
	cargo build --release --bin $(BIN_NAME) --no-default-features --features common,tool_context
	@mkdir -p ~/.codex/hooks
	cp $(CARGO_TARGET_DIR)/release/$(BIN_NAME) ~/.codex/hooks/$(BIN_NAME)
	@echo "Installed $(BIN_NAME) (common) to ~/.codex/hooks/$(BIN_NAME)"

test:
	cargo test --no-default-features --features tool_context

clean:
	rm -rf $(DIST_DIR)

# ── Internal: build one edition across all targets ───────────────────
.PHONY: build-edition list-artifacts
build-edition:
	@echo ""
	@echo "========================================"
	@echo "  Building $(EDITION) edition ($(BIN_PREFIX))"
	@echo "  Version: $(VERSION)"
	@if [ -n "$(FEATURES)" ]; then echo "  Features: $(FEATURES)"; fi
	@echo "========================================"
	@mkdir -p $(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)
	@for target in $(TARGETS); do \
		if echo "$$target" | grep -q darwin; then platform=darwin; \
		elif echo "$$target" | grep -q "linux-musl"; then platform=linux-musl; \
		elif echo "$$target" | grep -q "linux-gnu"; then platform=linux-gnu; \
		else platform=windows; fi; \
		arch=$$(echo "$$target" | sed \
			-e 's/^aarch64-.*/arm64/' \
			-e 's/^x86_64-.*/x64/'); \
		ext=""; \
		case "$$target" in *windows*) ext=".exe" ;; esac; \
		target_dir=$$(echo "$$target" | sed 's/\.[0-9]*\.[0-9]*$$//'); \
		dest="$(BIN_PREFIX)-$(VERSION)-$${platform}-$${arch}$${ext}"; \
		echo ""; \
		echo "--- [$(EDITION)] $${platform}-$${arch} ($$target) ---"; \
		feat_flag=""; \
		if [ -n "$(FEATURES)" ]; then feat_flag="--features $(FEATURES)"; fi; \
		if echo "$$target" | grep -q darwin; then \
			build_cmd="cargo build"; \
		else \
			build_cmd="cargo zigbuild"; \
		fi; \
		HOOK_VERSION=$(VERSION) $$build_cmd \
			--release --target "$$target" --bin $(BIN_NAME) \
			--no-default-features $$feat_flag 2>&1 || exit 1; \
		src="$(CARGO_TARGET_DIR)/$$target_dir/release/$(BIN_NAME)$${ext}"; \
		if [ -f "$$src" ]; then \
			cp "$$src" "$(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$$dest"; \
			chmod +x "$(DIST_DIR)/$(OUT_SUBDIR)/$(VERSION)/$$dest"; \
			echo "✓ $$dest"; \
		else \
			echo "✗ Build failed: $$src not found"; \
			exit 1; \
		fi; \
	done

list-artifacts:
	@echo "Artifacts:"
	@if [ -d "$(DIST_DIR)/aivision/$(VERSION)" ]; then \
		echo ""; echo "  releases/codex/aivision/$(VERSION)/"; ls -1h $(DIST_DIR)/aivision/$(VERSION)/ | sed 's/^/    /'; \
	fi
	@if [ -d "$(DIST_DIR)/common/$(VERSION)" ]; then \
		echo ""; echo "  releases/codex/common/$(VERSION)/"; ls -1h $(DIST_DIR)/common/$(VERSION)/ | sed 's/^/    /'; \
	fi

check-zigbuild:
	@command -v cargo-zigbuild >/dev/null 2>&1 || { \
		echo "ERROR: cargo-zigbuild not found. Install it with:"; \
		echo "  brew install cargo-zigbuild"; \
		echo "  # or: pip3 install cargo-zigbuild"; \
		exit 1; \
	}
