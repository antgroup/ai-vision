# Cross-compilation Makefile for kimihooks
#
# Prerequisites:
#   brew install zig cargo-zigbuild
#   rustup target add aarch64-apple-darwin x86_64-apple-darwin \
#     aarch64-unknown-linux-musl x86_64-unknown-linux-musl \
#     aarch64-unknown-linux-gnu x86_64-unknown-linux-gnu \
#     aarch64-pc-windows-gnullvm x86_64-pc-windows-gnullvm
#
# Usage:
#   make release           Build aivision edition for all platforms (requires zig)
#   make clean             Remove release artifacts

SHELL := /bin/bash

VERSION ?= $(shell grep '^version' Cargo.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
DIST_DIR := ../../releases/kimi
CARGO_TARGET_DIR ?= target
export CARGO_TARGET_DIR

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

# ── Build rules ──────────────────────────────────────────────────────

.PHONY: release release-aivision clean

release:
	@echo "Building all platforms (requires cargo-zigbuild)"
	@TARGETS="$(ALL_TARGETS)" $(MAKE) release-aivision

release-aivision:
	@$(call build_edition,aivision,aivision,observ-aivision-kimi-hook,aivision)

clean:
	rm -rf $(DIST_DIR)
	@echo "Cleaned release artifacts"

# ── Build function ───────────────────────────────────────────────────

define build_edition
	@echo ""
	@echo "========================================"
	@echo "  Building $(1) edition"
	@echo "  Version: $(VERSION)"
	@echo "  Target:  $(TARGETS)"
	@echo "========================================"
	@for target in $(TARGETS); do \
		if echo "$$target" | grep -q darwin; then platform=darwin; \
		elif echo "$$target" | grep -q "linux-musl"; then platform=linux-musl; \
		elif echo "$$target" | grep -q "linux-gnu"; then platform=linux-gnu; \
		else platform=windows; fi; \
		arch=$$(echo $$target | grep -q aarch64 && echo arm64 || echo x64); \
		ext=""; [ "$$platform" = "windows" ] && ext=".exe"; \
		target_dir=$$(echo "$$target" | sed 's/\.[0-9]*\.[0-9]*$$//'); \
		bin_prefix="$(3)"; \
		out_dir="$(DIST_DIR)/$(4)/$(VERSION)"; \
		features="--no-default-features --features $(2)"; \
		echo ""; \
		echo "--- [$(1)] $$platform-$$arch ($$target) ---"; \
		if echo "$$target" | grep -q darwin; then \
			build_cmd="cargo build"; \
		else \
			build_cmd="cargo zigbuild"; \
		fi; \
		$$build_cmd \
			--release \
			--target $$target \
			--bin kimihooks \
			$$features; \
		src_bin="target/$$target_dir/release/kimihooks$$ext"; \
		dest_name="$${bin_prefix}-$(VERSION)-$$platform-$$arch$$ext"; \
		mkdir -p "$$out_dir"; \
		cp "$$src_bin" "$$out_dir/$$dest_name"; \
		chmod +x "$$out_dir/$$dest_name"; \
		echo "✓ $$dest_name"; \
	done
endef
