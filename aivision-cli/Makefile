GO ?= go
PYTHON ?= python3
OUTPUT ?= aiv
VERSION_SOURCE := ../VERSION

.PHONY: build native-build native-test test assets assets-check

assets:
	$(PYTHON) ../deploy/scripts/package_assets.py
	$(GO) run -mod=vendor ./internal/deploy/renderproduct

assets-check:
	$(PYTHON) ../deploy/scripts/package_assets.py --check
	$(GO) run -mod=vendor ./internal/deploy/renderproduct --check

test: assets-check
	$(GO) test -mod=vendor ./...
	$(PYTHON) -m unittest discover -s scripts -p 'test_install.py'
	$(PYTHON) -m unittest discover -s ../deploy/tests

native-test: assets-check
	$(GO) test -mod=vendor -tags native_distribution ./... -run '^TestNativeDistribution'

# Private binary delivery must omit source bootstrap resources at link time.
native-build: override AIV_GO_BUILD_FLAGS = -tags native_distribution
build native-build: assets-check
	@set -eu; \
	aiv_version="$$(GOOS="$$($(GO) env GOHOSTOS)" GOARCH="$$($(GO) env GOHOSTARCH)" \
		$(GO) run -mod=vendor ./internal/buildversion "$(VERSION_SOURCE)")"; \
	$(GO) build -mod=vendor $(AIV_GO_BUILD_FLAGS) \
		-ldflags "-X github.com/antgroup/ai-vision/aivision-cli/internal/cli.Version=$$aiv_version" \
		-o "$(OUTPUT)" .
