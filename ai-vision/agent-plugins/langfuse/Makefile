PYTHON ?= python3
GO ?= go

.PHONY: test test-js test-js5 test-js6 test-eino
test:
	$(PYTHON) -m unittest discover -s tests -v

test-js: test-js5 test-js6

test-js5:
	npm --prefix tests/js test

test-js6:
	npm --prefix tests/js6 test

test-eino:
	$(MAKE) -C examples/eino test GO=$(GO)
	node --test tests/eino/test_verifier.mjs
