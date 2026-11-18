PYTHON ?= python3
NODE ?= node
NODE_PROJECT ?=

.PHONY: test test-nodejs
test:
	$(PYTHON) -m unittest discover -s tests -v

test-nodejs:
	@test -n "$(NODE_PROJECT)" || (echo "Set NODE_PROJECT to a temporary project with the Node smoke dependencies"; exit 1)
	$(PYTHON) tests/nodejs_smoke.py --node-project "$(NODE_PROJECT)" --node "$(NODE)"
