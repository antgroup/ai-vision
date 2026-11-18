PYTHON ?= python3
REPO ?= ../../../..
OUT ?= /tmp/aivision-native-e2e
RUNTIME_OUT ?= /tmp/aivision-native-runtimes
RUNTIMES ?= $(RUNTIME_OUT)/runtimes.json
TARGETS ?=
APPS ?= pi,hermes,opencode,dsh01,dsh02,kimi
LIVE_APPS ?= pi,hermes,opencode,dsh01,dsh02
PROXY ?=
SEMANTICS_OUT ?= $(OUT)-semantics

.PHONY: test install collector docker-platform live semantics
test:
	$(PYTHON) -m unittest discover -s . -p 'test_*.py'

install:
	$(PYTHON) install_runtimes.py --repo $(REPO) --out $(RUNTIME_OUT) --apps $(APPS)

collector:
	$(PYTHON) run_suite.py --repo $(REPO) --out $(OUT) --runtimes $(RUNTIMES) --apps $(APPS) --mode fake

docker-platform:
	$(PYTHON) docker_platform.py --repo $(REPO) --out $(OUT) --runtimes $(RUNTIMES)

# Requires the repository weaver-guard binary on PATH and a completed fake49.
semantics:
	$(PYTHON) weaver_gate.py --repo $(REPO) --input-dir $(OUT) --out $(SEMANTICS_OUT)

# Explicit local smoke only. NVIDIA_API_KEY stays in the caller's environment.
live:
	test -n "$(MODEL)" && test -n "$(TARGETS)"
	$(PYTHON) run_suite.py --repo $(REPO) --out $(OUT) --runtimes $(RUNTIMES) --apps $(LIVE_APPS) --targets $(TARGETS) --mode nvidia --model $(MODEL) --scenarios tool-loop,multi-turn,stream $(if $(PROXY),--proxy "$(PROXY)",)

# Isolated production plugin + official Hermes CLI: three resumed user turns.
.PHONY: hermes-turn-numbering hermes-compression
hermes-turn-numbering:
	$(PYTHON) hermes_turn_numbering.py --repo $(REPO) --runtimes $(RUNTIMES) --out $(OUT)

# Official ACP resume + automatic compaction; no synthetic hook inputs.
hermes-compression:
	$(PYTHON) hermes_compression.py --repo $(REPO) --runtimes $(RUNTIMES) --out $(OUT)
