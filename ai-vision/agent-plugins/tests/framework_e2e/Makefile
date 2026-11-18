PYTHON ?= python3
SDK_ARGS ?=
LIVE_MODE ?= --collector-only
OUT ?= artifacts/framework-e2e
REPO ?= ../../../..
BACKEND ?= seekdb
BACKEND_PYTHON ?= $(REPO)/ai-vision/aivision-py-be/.venv/bin/python
NODE ?= node
BROWSER_ARGS ?=

.PHONY: test collector platform ci docker-database docker-platform live-python browser-fake browser-nvidia
test:
	$(PYTHON) -m unittest discover -s . -p 'test_*.py'

collector:
	$(PYTHON) suite.py --mode collector --out $(OUT) $(SDK_ARGS)

platform:
	$(PYTHON) suite.py --mode platform --out $(OUT) $(SDK_ARGS)

ci:
	$(PYTHON) suite.py --mode ci --out $(OUT) $(SDK_ARGS)

docker-database:
	$(BACKEND_PYTHON) docker_database.py --backend $(BACKEND) --out $(OUT) --backend-tests $(REPO) --python $(BACKEND_PYTHON)

docker-platform:
	$(PYTHON) docker_platform.py --repo $(REPO) --out $(OUT)

live-python:
	$(PYTHON) live_python.py --provider-base-url "$(PROVIDER_BASE_URL)" --model "$(MODEL)" --out $(OUT) $(LIVE_MODE)

browser-fake:
	$(PYTHON) browser_platform.py --repo $(REPO) --out $(OUT) --source fake --node $(NODE) $(BROWSER_ARGS)

browser-nvidia:
	$(PYTHON) browser_platform.py --repo $(REPO) --out $(OUT) --source nvidia --node $(NODE) $(BROWSER_ARGS)
