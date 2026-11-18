GO ?= go
LEDGER ?= eino-ledger.json

.PHONY: test build run-fixture run-live
test:
	$(GO) test -race ./...

build:
	$(GO) build -o /dev/null .

run-fixture:
	$(GO) run . -mode fixture -ledger "$(LEDGER)"

run-live:
	$(GO) run . -mode live -ledger "$(LEDGER)"
