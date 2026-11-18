PYTHON ?= python3
REPO ?= ../../../..

.PHONY: test source replay calibrate database validate-local help

help:
	@echo 'test: offline contracts; source: run_suite ARGS; replay: CORPUS TARGETS APP OUT; calibrate: CORPUS TARGETS CONFIG OUT; database: DB_BACKEND DB_IMAGE OUT AL_PYTHON; validate-local: CONFIG OUT'

test:
	$(PYTHON) -m unittest discover -s . -p 'test_*.py' -v

source:
	$(PYTHON) run_suite.py --repo "$(REPO)" $(ARGS)

replay:
	@test -n "$(CORPUS)" -a -n "$(TARGETS)" -a -n "$(APP)" -a -n "$(OUT)"
	$(PYTHON) replay.py --corpus "$(CORPUS)" --targets "$(TARGETS)" --app "$(APP)" --out "$(OUT)"

calibrate:
	@test -n "$(CORPUS)" -a -n "$(TARGETS)" -a -n "$(CONFIG)" -a -n "$(OUT)"
	$(PYTHON) calibrate.py --corpus "$(CORPUS)" --targets "$(TARGETS)" --config "$(CONFIG)" --out "$(OUT)"

database:
	@test -n "$(DB_BACKEND)" -a -n "$(DB_IMAGE)" -a -n "$(OUT)" -a -n "$(AL_PYTHON)"
	$(PYTHON) docker_database.py --backend "$(DB_BACKEND)" --image "$(DB_IMAGE)" --repo "$(REPO)" --out "$(OUT)" --python "$(AL_PYTHON)" $(DB_ARGS)

validate-local:
	@test -n "$(CONFIG)" -a -n "$(OUT)"
	$(PYTHON) validate_local.py --repo "$(REPO)" --out "$(OUT)" --config "$(CONFIG)"
