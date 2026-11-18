# Build Makefile for cchooks
#
# Usage:
#   make                          Build all editions for current platform
#   make aivision                 Build aivision edition only (default)
#   make common                   Build common edition only
#   make release            Build all editions for all platforms (requires zig, cargo-zigbuild, and Rust targets)
#   make install                  Alias for install-aivision
#   make install-aivision         Build aivision edition and install to ~/.aivision/cc/hooks/
#   make install-common           Build common edition and install to ~/.claude/hooks/
#   make clean                    Clean build artifacts

SHELL := /bin/bash

VERSION ?= $(or $(V),$(shell grep '^version' Cargo.toml | head -1 | sed 's/.*"\(.*\)".*/\1/'))
DIST_DIR := ../../releases/claudecode
CARGO_TARGET_DIR ?= target
export CARGO_TARGET_DIR

# Detect current platform
HOST_TARGET := $(shell rustc -vV | grep host | awk '{print $$2}')
HOST_ARCH := $(if $(findstring aarch64,$(HOST_TARGET)),arm64,x64)
HOST_OS := $(if $(findstring darwin,$(HOST_TARGET)),darwin,$(if $(findstring linux,$(HOST_TARGET)),linux,windows))
HOST_SUFFIX := $(if $(findstring windows,$(HOST_TARGET)),.exe,)

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

# ── Build rules ──────────────────────────────────────────────────────

.PHONY: all aivision common release clean install install-aivision install-common check-zigbuild check-rust-targets

all: aivision common

aivision: check-zigbuild check-rust-targets
	@$(call build_edition,aivision,aivision,observ-aivision-claude-code-hook,aivision)

common: check-zigbuild check-rust-targets
	@$(call build_edition,common,common,observ-claude-code-hook,common)


release:
	@echo "Building all platforms (requires cargo-zigbuild)"
	@TARGETS="$(ALL_TARGETS)" $(MAKE) all

install: install-aivision

install-aivision:
	cargo build --release --bin cchooks
	mkdir -p $(HOME)/.aivision/cc/hooks
	cp $(CARGO_TARGET_DIR)/release/cchooks $(HOME)/.aivision/cc/hooks/cchooks
	@echo "Installed aivision → ~/.aivision/cc/hooks/cchooks"

install-common:
	cargo build --release --no-default-features --features common --bin cchooks
	mkdir -p $(HOME)/.claude/hooks
	cp $(CARGO_TARGET_DIR)/release/cchooks $(HOME)/.claude/hooks/cchooks
	@echo "Installed common → ~/.claude/hooks/cchooks"


clean:
	rm -rf $(CARGO_TARGET_DIR)/$(HOST_TARGET)/release/cchooks$(HOST_SUFFIX)
	rm -rf $(DIST_DIR)
	@echo "Cleaned build artifacts"

# ── Build function ───────────────────────────────────────────────────

define build_edition
	@echo ""
	@echo "========================================"
	@echo "  Building $(1) edition"
	@echo "  Version: $(VERSION)"
	@echo "  Target:  $(TARGETS)"
	@echo "========================================"
	@set -euo pipefail; \
	for target in $(TARGETS); do \
		if echo "$$target" | grep -q darwin; then platform=darwin; \
		elif echo "$$target" | grep -q "linux-musl"; then platform=linux-musl; \
		elif echo "$$target" | grep -q "linux-gnu"; then platform=linux-gnu; \
		else platform=windows; fi; \
		arch=$$(echo $$target | grep -q aarch64 && echo arm64 || echo x64); \
		ext=""; [ "$$platform" = "windows" ] && ext=".exe"; \
		target_dir=$$(echo "$$target" | sed 's/\.[0-9]*\.[0-9]*$$//'); \
		bin_prefix="$(3)"; \
		out_dir="$(DIST_DIR)/$(4)/$(VERSION)"; \
		features="--no-default-features"; [ -n "$(2)" ] && features="--no-default-features --features $(2)"; \
		echo ""; \
		echo "--- [$(1)] $$platform-$$arch ($$target) ---"; \
		if echo "$$target" | grep -q darwin; then \
			build_cmd="cargo build"; \
		else \
			build_cmd="cargo zigbuild"; \
		fi; \
		HOOK_VERSION=$(VERSION) $$build_cmd \
			--release \
			--target $$target \
			--bin cchooks \
			$$features; \
		src_bin="$(CARGO_TARGET_DIR)/$$target_dir/release/cchooks$$ext"; \
		dest_name="$${bin_prefix}-$(VERSION)-$$platform-$$arch$$ext"; \
		mkdir -p "$$out_dir"; \
		cp "$$src_bin" "$$out_dir/$$dest_name"; \
		chmod +x "$$out_dir/$$dest_name"; \
		echo "✓ $$dest_name"; \
	done
endef

# Native Darwin builds use Cargo directly. Linux, musl, and Windows release
# targets need cargo-zigbuild (and zig); fail before creating any artifacts when
# the cross-compilation toolchain is unavailable.
check-zigbuild:
	@needs_zig=false; \
	for target in $(TARGETS); do \
		case "$$target" in \
			*darwin) ;; \
			*) needs_zig=true ;; \
		esac; \
	done; \
	if [ "$$needs_zig" = true ]; then \
		if ! command -v cargo-zigbuild >/dev/null 2>&1; then \
			echo "ERROR: cargo-zigbuild is required for non-Darwin targets ($(TARGETS))." >&2; \
			echo "Install it with: cargo install cargo-zigbuild" >&2; \
			exit 1; \
		fi; \
		if ! command -v zig >/dev/null 2>&1; then \
			echo "ERROR: zig is required for non-Darwin targets ($(TARGETS))." >&2; \
			echo "Install it with: brew install zig" >&2; \
			exit 1; \
		fi; \
	fi

# The glibc version suffix is a cargo-zigbuild selector, not part of the Rust
# target name (for example, x86_64-unknown-linux-gnu.2.17 ->
# x86_64-unknown-linux-gnu). Check every target before the first edition is
# built so a release cannot stop halfway through after producing partial files.
check-rust-targets:
	@command -v rustup >/dev/null 2>&1 || { \
		echo "ERROR: rustup is required to install/check Rust release targets." >&2; \
		exit 1; \
	}; \
	installed="$$(rustup target list --installed)" || { \
		echo "ERROR: unable to query installed Rust targets with rustup." >&2; \
		exit 1; \
	}; \
	missing=""; \
	for target in $(TARGETS); do \
		rust_target="$$(printf '%s\n' "$$target" | sed -E 's/\.[0-9]+\.[0-9]+$$//')"; \
		if ! printf '%s\n' "$$installed" | grep -Fxq "$$rust_target"; then \
			case " $$missing " in \
				*" $$rust_target "*) ;; \
				*) missing="$$missing $$rust_target" ;; \
			esac; \
		fi; \
	done; \
	if [ -n "$$missing" ]; then \
		echo "ERROR: missing Rust target(s):$$missing" >&2; \
		echo "Install them with: rustup target add$$missing" >&2; \
		exit 1; \
	fi
