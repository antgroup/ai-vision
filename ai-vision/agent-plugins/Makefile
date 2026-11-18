# aivision-agents — Unified Makefile
#
# Usage:  make {target} {agent} [VAR=val ...]
#
# Agent shorthands (second argument, required for most targets):
#   cc    claudecode       cdx   codex
#   ocw   openclaw         ocw-replay  openclaw-replay
#   ocd   opencode          ocd-replay  opencode-replay
#   pi    observ-pi          pi-replay  local replay plugin only
#   all   all agents       cc,cdx  comma-separated
#
# Examples:
#   make replay-all all                Replay all agents
#   make validate cc                   Validate claudecode only
#   make validate ocw FORMAT=json      Validate OpenClaw replays
#   make validate-one ocd FILE=$$HOME/.aivision/ocd/replays/spans_x.jsonl FORMAT=json
#   make report ocw TS=session_x.jsonl CONFIG=report.config.toml PROFILE=dev
#   make report-shift ocd TS=session_x.jsonl CONFIG=report.config.toml PROFILE=dev
#   make report-check cc               Replay and POST to local weaver
#   make build-edition cc EDITION=common  Build the standalone Claude Code plugin
#   make golden-check cc,cdx          Golden check for cc and cdx
#   make release wg                    Build/stage multi-platform weaver-guard release under releases/weaver-guard/
#   make help                          Show this help
#   make help cc                       Show agent-specific help

# ── Agent registry ──────────────────────────────────────────────────

AGENT_MAP_cc  := claudecode
AGENT_MAP_cdx := codex
AGENT_MAP_ocw := openclaw
AGENT_MAP_ocw-replay := openclaw
AGENT_MAP_ocd := opencode
AGENT_MAP_ocd-replay := opencode
AGENT_MAP_hm  := hermes
AGENT_MAP_hms := hermes
AGENT_MAP_hms-replay := hermes
AGENT_MAP_hms-cohort := hermes
AGENT_MAP_km  := kimi
AGENT_MAP_pi  := pi
AGENT_MAP_pi-replay := pi
AGENT_MAP_ds  := deepseek
AGENT_MAP_dsh := deepseek

ALL_DIRS   := claudecode codex openclaw opencode hermes kimi pi deepseek
ALL_SHORTS := cc cdx ocw ocw-replay ocd ocd-replay hm hms hms-replay hms-cohort km pi pi-replay ds dsh

GOLDEN_CAPABLE_AGENTS := claudecode pi

MATURE_AGENTS := claudecode codex openclaw opencode
MATURE_SHORTS := cc cdx ocw ocd

WEAVER_URL ?= http://localhost:4319/
REPORT_CHECK_DRY_RUN ?= 0
REPORT_CHECK_VERBOSE ?= 0
REPORT_CHECK_SCRIPT := scripts/report_check.py
ARTIFACT_PATH_SCRIPT := scripts/aivision_artifacts.py
MIGRATE_ARTIFACTS_SCRIPT := scripts/migrate-artifacts
override AIVISION_HOME := $(or $(strip $(AIVISION_HOME)),$(shell printf '%s/.aivision' "$$HOME"))
MIGRATE_COMPONENT ?=
MIGRATE_APPLY ?= 0
REPORT_CHECK_FLAGS := $(if $(filter 1 true yes,$(REPORT_CHECK_DRY_RUN)),--dry-run) \
                      $(if $(filter 1 true yes,$(REPORT_CHECK_VERBOSE)),--verbose)
WORKTREE_SETUP_FLAGS := $(if $(filter 1 true yes,$(DRY_RUN)),--dry-run) \
                        $(if $(filter 1 true yes,$(FORCE)),--force)

# ── Parse agent spec from command line ──────────────────────────────

comma := ,
empty :=
space := $(empty) $(empty)

_PRIMARY_GOAL := $(word 1,$(MAKECMDGOALS))
_AGENT_SPEC := $(word 2,$(MAKECMDGOALS))
_RELEASE_WG := $(if $(filter release,$(_PRIMARY_GOAL)),$(filter wg,$(_AGENT_SPEC)))

# Resolve spec -> directory list; error on unknown shorthand
_resolve = $(if $(filter all,$(1)),$(ALL_DIRS),\
  $(foreach a,$(subst $(comma),$(space),$(1)),\
    $(or $(AGENT_MAP_$(a)),$(error Unknown agent '$(a)'. Use: $(ALL_SHORTS) all))))

AGENT_DIRS := $(if $(_AGENT_SPEC),$(if $(_RELEASE_WG),,$(call _resolve,$(_AGENT_SPEC))))

# Replay profiles are installed through their agent-specific plugin targets.
_route_target = $(if $(and $(filter install,$(1)),$(filter ocw-replay ocd-replay hms-replay,$(_AGENT_SPEC))),replay-plugin-install,$(if $(and $(filter install,$(1)),$(filter pi-replay,$(_AGENT_SPEC))),install-replay,$(if $(and $(filter install,$(1)),$(filter hms-cohort,$(_AGENT_SPEC))),install-cohort,$(1))))

# Swallow the agent spec so Make doesn't treat it as an unknown target
ifneq ($(_AGENT_SPEC),)
.PHONY: $(_AGENT_SPEC)
$(_AGENT_SPEC):
	@:
endif

# ── Variable forwarding ────────────────────────────────────────────

_FWD := $(if $(EDITION),EDITION=$(EDITION)) \
        $(if $(ED),ED=$(ED)) \
        $(if $(FORMAT),FORMAT=$(FORMAT)) \
        $(if $(TS),TS=$(TS)) \
        $(if $(TRANSCRIPT),TRANSCRIPT=$(TRANSCRIPT)) \
        $(if $(FILE),FILE=$(FILE)) \
        $(if $(CONFIG),CONFIG=$(CONFIG)) \
        $(if $(PROFILE),PROFILE=$(PROFILE)) \
        $(if $(SHIFT),SHIFT=$(SHIFT)) \
        $(if $(OTEL_JSON),OTEL_JSON=$(OTEL_JSON)) \
        $(if $(DEBUG),DEBUG=$(DEBUG)) \
        $(if $(VERSION),VERSION=$(VERSION)) \
        $(if $(TARGETS),TARGETS=$(TARGETS)) \
        $(if $(AIVISION_HOME),AIVISION_HOME=$(AIVISION_HOME)) \
        $(if $(TRANSCRIPT_DIR),TRANSCRIPT_DIR=$(TRANSCRIPT_DIR)) \
        $(if $(REPLAY_DIR),REPLAY_DIR=$(REPLAY_DIR)) \
        $(if $(GOLDEN_DIR),GOLDEN_DIR=$(GOLDEN_DIR)) \
        $(if $(CCHOOKS_SESSION_DIR),CCHOOKS_SESSION_DIR=$(CCHOOKS_SESSION_DIR)) \
        $(if $(CCHOOKS_REPLAY_DIR),CCHOOKS_REPLAY_DIR=$(CCHOOKS_REPLAY_DIR)) \
        $(if $(CDXHOOKS_SESSION_DIR),CDXHOOKS_SESSION_DIR=$(CDXHOOKS_SESSION_DIR)) \
        $(if $(CDXHOOKS_REPLAY_DIR),CDXHOOKS_REPLAY_DIR=$(CDXHOOKS_REPLAY_DIR)) \
        $(if $(OPENCLAW_REPLAY_SESSION_DIR),OPENCLAW_REPLAY_SESSION_DIR=$(OPENCLAW_REPLAY_SESSION_DIR)) \
        $(if $(OPENCLAW_REPLAY_DIR),OPENCLAW_REPLAY_DIR=$(OPENCLAW_REPLAY_DIR)) \
        $(if $(OPENCODE_REPLAY_SESSION_DIR),OPENCODE_REPLAY_SESSION_DIR=$(OPENCODE_REPLAY_SESSION_DIR)) \
        $(if $(PIREPLAY_SESSION_DIR),PIREPLAY_SESSION_DIR=$(PIREPLAY_SESSION_DIR)) \
        $(if $(OBSERV_PI_REPLAY_DIR),OBSERV_PI_REPLAY_DIR=$(OBSERV_PI_REPLAY_DIR)) \
        $(if $(KIMI_SESSION_DIR),KIMI_SESSION_DIR=$(KIMI_SESSION_DIR)) \
        $(if $(KIMI_REPLAY_DIR),KIMI_REPLAY_DIR=$(KIMI_REPLAY_DIR)) \
        $(if $(DSH_PROFILE),DSH_PROFILE=$(DSH_PROFILE)) \
        $(if $(OBSERV_HERMES_SESSION_DIR),OBSERV_HERMES_SESSION_DIR=$(OBSERV_HERMES_SESSION_DIR)) \
        $(if $(OBSERV_HERMES_REPLAY_DIR),OBSERV_HERMES_REPLAY_DIR=$(OBSERV_HERMES_REPLAY_DIR)) \
        $(if $(HERMES_REPLAY_SESSION_DIR),HERMES_REPLAY_SESSION_DIR=$(HERMES_REPLAY_SESSION_DIR)) \
        $(if $(COLLECTION_MODE),COLLECTION_MODE=$(COLLECTION_MODE)) \
        $(if $(HERMES_HOME),HERMES_HOME=$(HERMES_HOME))

# ── Worktree setup ─────────────────────────────────────────────────

.PHONY: worktree-setup

worktree-setup:
	@scripts/worktree-setup $(WORKTREE_SETUP_FLAGS)

.PHONY: artifact-paths migrate-artifacts test-artifact-migration test-artifact-paths test-worktree-setup

artifact-paths:
	@python3 "$(ARTIFACT_PATH_SCRIPT)" --aivision-home "$(AIVISION_HOME)" list

migrate-artifacts:
	@"$(MIGRATE_ARTIFACTS_SCRIPT)" --aivision-home "$(AIVISION_HOME)" \
		$(if $(MIGRATE_COMPONENT),--component "$(MIGRATE_COMPONENT)") \
		$(if $(filter 1 true yes,$(MIGRATE_APPLY)),--apply,--dry-run)

test-artifact-migration:
	@python3 -m unittest discover -s scripts/tests -p 'test_artifact_migration.py' -v

test-artifact-paths:
	@python3 -m unittest discover -s scripts/tests -p 'test_make_artifact_paths.py' -v

test-worktree-setup:
	@python3 -m unittest discover -s scripts/tests -p 'test_worktree_setup.py' -v

# ── Routable targets ───────────────────────────────────────────────
#
# Every target below is dispatched to each resolved agent directory.
# The agent's own Makefile handles the actual work.

_ROUTE_TARGETS := \
  replay-all replay validate validate-one report report-shift \
  golden-check golden-update golden-init \
  build build-edition build-all install \
  test clean transcripts replays list-artifacts

.PHONY: $(_ROUTE_TARGETS)

$(_ROUTE_TARGETS):
	@if [ -z "$(AGENT_DIRS)" ]; then \
		echo "Usage: make $@ {agent}  (agent: $(ALL_SHORTS) all)" >&2; exit 1; fi; \
	for dir in $(AGENT_DIRS); do \
		echo ""; \
		echo "=== [$@] $$dir ==="; \
		$(MAKE) -C $$dir $(call _route_target,$@) $(_FWD) || exit 1; \
	done

.PHONY: release
release:
	@if [ "$(_RELEASE_WG)" = "wg" ]; then \
		$(MAKE) -C weaver release-wg $(_FWD); \
	else \
		if [ -z "$(AGENT_DIRS)" ]; then \
			echo "Usage: make release {agent|wg}  (agent: $(ALL_SHORTS) all, wg: weaver-guard)" >&2; exit 1; \
		fi; \
		for dir in $(AGENT_DIRS); do \
			echo ""; \
			echo "=== [release] $$dir ==="; \
			$(MAKE) -C $$dir release $(_FWD) || exit 1; \
		done; \
	fi

# ── Local weaver report-check ──────────────────────────────────────
#
# Replay files are OTLP JSONL. Each record is POSTed to local AiVision/weaver:
#   spans_/traces_  -> /v1/traces
#   events_/logs_   -> /v1/logs
#   metrics_        -> /v1/metrics
#
# 用法:
#   make report-check cc
#   make report-check-golden cc
#   make report-check-all
#   make report-check-golden-all

_REPLAY_FIND_EXPR := \( -name 'spans_*.jsonl' -o -name 'traces_*.jsonl' -o -name 'events_*.jsonl' -o -name 'logs_*.jsonl' -o -name 'metrics_*.jsonl' \)

.PHONY: report-check report-check-golden report-check-all report-check-golden-all golden-collect

report-check:
	@if [ -z "$(AGENT_DIRS)" ]; then \
		echo "Usage: make report-check {agent}  (agent: $(ALL_SHORTS) all)" >&2; \
		echo "Example: make report-check cc WEAVER_URL=$(WEAVER_URL)" >&2; \
		exit 1; fi; \
	for dir in $(AGENT_DIRS); do \
		echo ""; \
		echo "=== [report-check] $$dir -> $(WEAVER_URL) ==="; \
		$(MAKE) -C $$dir replay-all $(_FWD) || exit 1; \
		replay_dir="$(if $(strip $(REPLAY_DIR)),$(REPLAY_DIR),$$(python3 "$(ARTIFACT_PATH_SCRIPT)" --aivision-home "$(AIVISION_HOME)" path "$$dir" replays))"; \
		files="$$(find -H "$$replay_dir" -maxdepth 1 -type f $(_REPLAY_FIND_EXPR) | sort)"; \
		if [ -z "$$files" ]; then \
			echo "[report-check] no replay JSONL files under $$replay_dir" >&2; \
			exit 1; \
		fi; \
		python3 "$(REPORT_CHECK_SCRIPT)" --base-url "$(WEAVER_URL)" $(REPORT_CHECK_FLAGS) $$files || exit 1; \
	done

report-check-golden:
	@if [ -z "$(AGENT_DIRS)" ]; then \
		echo "Usage: make report-check-golden {agent}  (agent: $(ALL_SHORTS) all)" >&2; \
		echo "Example: make report-check-golden cc WEAVER_URL=$(WEAVER_URL)" >&2; \
		exit 1; fi; \
	for dir in $(AGENT_DIRS); do \
		echo ""; \
		golden_dir="$(if $(strip $(GOLDEN_DIR)),$(GOLDEN_DIR),$$(python3 "$(ARTIFACT_PATH_SCRIPT)" --aivision-home "$(AIVISION_HOME)" path "$$dir" golden))"; \
		echo "=== [report-check-golden] $$golden_dir -> $(WEAVER_URL) ==="; \
		if [ ! -d "$$golden_dir" ]; then \
			echo "[report-check-golden] no golden directory: $$golden_dir" >&2; \
			exit 1; \
		fi; \
		if [ -d "$$golden_dir/transcripts" ] && find -H "$$golden_dir/transcripts" -maxdepth 1 -type f -name '*.jsonl' | grep -q .; then \
			$(MAKE) -C $$dir golden-replay-all $(_FWD) || exit 1; \
			if [ -f "$$golden_dir/replays/.report-check-files" ]; then \
				files="$$(cat "$$golden_dir/replays/.report-check-files")"; \
			else \
				files="$$(find -H "$$golden_dir/replays" -maxdepth 1 -type f $(_REPLAY_FIND_EXPR) | sort)"; \
			fi; \
		else \
			files="$$(find "$$golden_dir" -maxdepth 1 -type f $(_REPLAY_FIND_EXPR) | sort)"; \
		fi; \
		if [ -z "$$files" ]; then \
			echo "[report-check-golden] no golden replay JSONL files under $$golden_dir" >&2; \
			exit 1; \
		fi; \
		python3 "$(REPORT_CHECK_SCRIPT)" --base-url "$(WEAVER_URL)" $(REPORT_CHECK_FLAGS) $$files || exit 1; \
	done

report-check-all:
	@for dir in $(MATURE_AGENTS); do \
		echo ""; \
		echo "=== [report-check-all] $$dir -> $(WEAVER_URL) ==="; \
		$(MAKE) -C $$dir replay-all $(_FWD) || exit 1; \
		replay_dir="$(if $(strip $(REPLAY_DIR)),$(REPLAY_DIR),$$(python3 "$(ARTIFACT_PATH_SCRIPT)" --aivision-home "$(AIVISION_HOME)" path "$$dir" replays))"; \
		if [ ! -d "$$replay_dir" ]; then \
			echo "[report-check-all] skip: no replay directory $$replay_dir"; \
			continue; \
		fi; \
		files="$$(find -H "$$replay_dir" -maxdepth 1 -type f $(_REPLAY_FIND_EXPR) | sort)"; \
		if [ -z "$$files" ]; then \
			echo "[report-check-all] skip: no replay JSONL files under $$replay_dir"; \
			continue; \
		fi; \
		python3 "$(REPORT_CHECK_SCRIPT)" --base-url "$(WEAVER_URL)" $(REPORT_CHECK_FLAGS) $$files || exit 1; \
	done

report-check-golden-all:
	@for dir in $(MATURE_AGENTS); do \
		echo ""; \
		golden_dir="$(if $(strip $(GOLDEN_DIR)),$(GOLDEN_DIR),$$(python3 "$(ARTIFACT_PATH_SCRIPT)" --aivision-home "$(AIVISION_HOME)" path "$$dir" golden))"; \
		echo "=== [report-check-golden-all] $$golden_dir -> $(WEAVER_URL) ==="; \
		if [ ! -d "$$golden_dir" ]; then \
			echo "[report-check-golden-all] skip: no golden directory $$golden_dir"; \
			continue; \
		fi; \
		if [ -d "$$golden_dir/transcripts" ] && find -H "$$golden_dir/transcripts" -maxdepth 1 -type f -name '*.jsonl' | grep -q .; then \
			$(MAKE) -C $$dir golden-replay-all $(_FWD) || exit 1; \
			if [ -f "$$golden_dir/replays/.report-check-files" ]; then \
				files="$$(cat "$$golden_dir/replays/.report-check-files")"; \
			else \
				files="$$(find -H "$$golden_dir/replays" -maxdepth 1 -type f $(_REPLAY_FIND_EXPR) | sort)"; \
			fi; \
		else \
			files="$$(find "$$golden_dir" -maxdepth 1 -type f $(_REPLAY_FIND_EXPR) | sort)"; \
		fi; \
		if [ -z "$$files" ]; then \
			echo "[report-check-golden-all] skip: no golden replay JSONL files under $$golden_dir"; \
			continue; \
		fi; \
		python3 "$(REPORT_CHECK_SCRIPT)" --base-url "$(WEAVER_URL)" $(REPORT_CHECK_FLAGS) $$files || exit 1; \
	done

# ── Doctor (接入验收) ────────────────────────────────────────────────
#
# 跨语言、跨 agent 的骨架 + Resource + e2e 闭环验收。详见 METHODOLOGY.md §3.5
#
# 用法:
#   make doctor cc          检查 claudecode
#   make doctor-all         检查 4 个成熟 agent (claudecode/codex/openclaw/opencode)

.PHONY: doctor doctor-all

doctor:
	@if [ -z "$(AGENT_DIRS)" ]; then \
		echo "Usage: make doctor {agent}  (agent: $(ALL_SHORTS) all)" >&2; \
		echo "Tip: 'make doctor-all' 检查 4 个成熟 agent (claudecode/codex/openclaw/opencode)" >&2; \
		exit 1; fi; \
	for dir in $(AGENT_DIRS); do \
		bash scripts/doctor.sh $$dir || true; \
	done

doctor-all:
	@for dir in $(MATURE_AGENTS); do \
		bash scripts/doctor.sh $$dir || true; \
	done

# ── Help ───────────────────────────────────────────────────────────

.PHONY: help
.DEFAULT_GOAL := help


# --- Golden collect: run golden scenarios via agent CLI and collect replay ---
#
# Usage:
#   make golden-collect cc                    Run all golden scenarios for claudecode
#   make golden-collect cc WEAVER_URL=...     Override AiVision URL
#   make golden-collect cc GOLDEN_SKIP=compaction  Skip specific scenario IDs
#
GOLDEN_SKIP ?=

define _GOLDEN_SCENARIOS
basic-llm|你好，用一句话介绍你自己。
tool-success|读取当前目录下的 README.md 文件，告诉我文件有多少行。
tool-error|读取 /nonexistent/this-file-does-not-exist-12345.txt 的内容。
multi-tool|在当前目录创建一个 /tmp/golden-test.py 文件，内容是 print("hello")，然后执行它，最后删除它。
subagent|启动一个前台子 agent 执行 sleep 5 && echo done，同时你继续告诉我当前时间。
mcp-tool|使用已配置的 MCP 搜索工具检索一条公开文档，并告诉我标题。
skill-call|使用skill-creator帮我创建一个查看当前杭州天气的skill，名称叫hangzhou-weather
endef
export _GOLDEN_SCENARIOS

golden-collect:
	@if [ -z "$(AGENT_DIRS)" ]; then \
		echo "Usage: make golden-collect {agent}  (supported: $(GOLDEN_CAPABLE_AGENTS))" >&2; exit 1; fi; \
	for dir in $(AGENT_DIRS); do case " $(GOLDEN_CAPABLE_AGENTS) " in *" $$dir "*) ;; *) \
		echo "[golden-collect] unsupported agent: $$dir (supported: $(GOLDEN_CAPABLE_AGENTS))" >&2; exit 1 ;; esac; done; \
	for dir in $(AGENT_DIRS); do \
		echo ""; \
		echo "=== [golden-collect] $$dir ==="; \
		if [ "$$dir" = "pi" ]; then \
			make -C pi golden-evaluate AIVISION_HOME="$(AIVISION_HOME)" $(if $(strip $(GOLDEN_DIR)),GOLDEN_DIR="$(GOLDEN_DIR)") WEAVER_URL="$(WEAVER_URL)" || exit $$?; \
			continue; \
		fi; \
		golden_dir="$(if $(strip $(GOLDEN_DIR)),$(GOLDEN_DIR),$$(python3 "$(ARTIFACT_PATH_SCRIPT)" --aivision-home "$(AIVISION_HOME)" path "$$dir" golden))"; \
		replay_dir="$$golden_dir/replays"; \
		transcript_dir="$$golden_dir/transcripts"; \
		log_dir="$$golden_dir/logs"; \
		mkdir -p "$$replay_dir" "$$transcript_dir" "$$log_dir"; \
		run_marker="$$log_dir/.golden-collect-$$$$.marker"; \
		: > "$$run_marker"; \
		total=0; ok=0; skipped=0; failed=0; \
		echo "--- Clearing AiVision recent data ---"; \
		curl -fsS -X POST "$(WEAVER_URL)/api/clear" > /dev/null || exit 1; \
		scenario_file="$$(mktemp "$${TMPDIR:-/tmp}/aivision-golden-scenarios.XXXXXX")"; \
		printf '%s\n' "$$_GOLDEN_SCENARIOS" > "$$scenario_file"; \
		while IFS='|' read -r scenario_id prompt; do \
			[ -z "$$scenario_id" ] && continue; \
			total=$$((total + 1)); \
			skip_list=",$(GOLDEN_SKIP),"; \
			case "$$skip_list" in *",$$scenario_id,"*) \
				echo "[$$scenario_id] SKIPPED (in GOLDEN_SKIP)"; \
				skipped=$$((skipped + 1)); \
				continue ;; \
			esac; \
			echo ""; \
			echo "--- [$$scenario_id] $$prompt ---"; \
			scenario_log="$$log_dir/$$scenario_id.log"; \
			if CCHOOKS_DEBUG=true \
				CCHOOKS_REPLAY_DIR="$$replay_dir" \
				CCHOOKS_SESSION_DIR="$$transcript_dir" \
				claude -p "$$prompt" \
					--dangerously-skip-permissions \
					> "$$scenario_log" 2>&1; then \
				echo "[$$scenario_id] OK"; \
				ok=$$((ok + 1)); \
			elif grep -Eiq 'not installed|unknown (mcp|skill|tool)|mcp.*not found|skill.*not found' "$$scenario_log"; then \
				echo "[$$scenario_id] SKIPPED (capability unavailable; see $$scenario_log)"; \
				skipped=$$((skipped + 1)); \
			else \
				echo "[$$scenario_id] FAILED (see $$scenario_log)"; \
				failed=$$((failed + 1)); \
			fi; \
		done < "$$scenario_file"; \
		rm -f "$$scenario_file"; \
		echo ""; \
		echo "--- POST replays to AiVision ---"; \
		replay_files="$$(find "$$replay_dir" -maxdepth 1 -type f -newer "$$run_marker" $(_REPLAY_FIND_EXPR) | sort)"; \
		if [ -n "$$replay_files" ]; then \
			python3 "$(REPORT_CHECK_SCRIPT)" --base-url "$(WEAVER_URL)" $(REPORT_CHECK_FLAGS) $$replay_files || exit 1; \
		else \
			echo "[golden-collect] no replay files produced in $$replay_dir"; exit 1; \
		fi; \
		echo ""; \
		echo "=== [golden-collect] Summary ==="; \
		echo "Replay files: $$(find "$$replay_dir" -maxdepth 1 -type f -newer "$$run_marker" $(_REPLAY_FIND_EXPR) | wc -l | tr -d ' ')"; \
		echo "Transcript files: $$(find "$$transcript_dir" -maxdepth 1 -type f -newer "$$run_marker" -name '*.jsonl' | wc -l | tr -d ' ')"; \
		echo "Scenarios: total=$$total passed=$$ok skipped=$$skipped failed=$$failed"; \
		rm -f "$$run_marker"; \
		[ "$$failed" -eq 0 ] || exit 1; \
		echo ""; \
		echo "View results: open $(WEAVER_URL)/dashboard"; \
		echo "Fix violations: use 提示词工程 > 语义修正 tab in dashboard"; \
	done

help:
ifdef AGENT_DIRS
	@$(MAKE) -C $(firstword $(AGENT_DIRS)) help $(_FWD)
else
	@echo "aivision-agents — Unified Makefile"
	@echo ""
	@echo "Usage: make {target} {agent} [VAR=val ...]"
	@echo ""
	@echo "Agent shorthands:"
	@echo "  cc    claudecode        cdx   codex"
	@echo "  ocw   openclaw          ocw-replay  openclaw-replay"
	@echo "  ocd   opencode          ocd-replay  opencode-replay"
	@echo "  hm/hms hermes"
	@echo "  km    kimi               pi    piagent"
	@echo "  ds    deepseek (dsh)    all   all agents"
	@echo ""
	@echo "Pipeline:"
	@echo "  replay-all              Replay all transcripts"
	@echo "  replay TRANSCRIPT=f     Replay single transcript"
	@echo "  validate                Validate all replays"
	@echo "  validate-one FILE=f     Validate single replay"
	@echo "  report TRANSCRIPT=f     Report to Langfuse"
	@echo "  report-shift TRANSCRIPT=f  Shift timestamps and report to Langfuse"
	@echo "  report-check            Replay and POST replays to local weaver"
	@echo "  report-check-all        Same as report-check for 4 mature agents"
	@echo "  report-check-golden     Replay golden/transcripts when present, then POST golden OTLP JSONL"
	@echo "  report-check-golden-all Same as report-check-golden for 4 mature agents"
	@echo ""
	@echo "Golden:"
	@echo "  golden-collect          Run golden scenarios via agent CLI, collect replays"
	@echo "  golden-check            Compare against golden baseline"
	@echo "  golden-update           Update golden from replays"
	@echo "  golden-init             Init golden examples"
	@echo ""
	@echo "Build:"
	@echo "  build                   Build (debug)"
	@echo "  build-edition EDITION=  Build specific edition"
	@echo "  build-all               Build all editions"
	@echo "  release                 Build all editions x all platforms"
	@echo "  release wg              Build/stage multi-platform weaver-guard release under releases/weaver-guard/"
	@echo "  install EDITION= / ED=  Install edition locally (kimi 支持 ED=aivision|common)"
	@echo "  worktree-setup          Configure shared target/releases/data links for this worktree"
	@echo "  artifact-paths          List canonical ~/.aivision artifact paths"
	@echo "  migrate-artifacts       Dry-run legacy artifact migration (MIGRATE_APPLY=1 to copy)"
	@echo ""
	@echo "Tools:"
	@echo "  test                    Run tests"
	@echo "  clean                   Clean generated files"
	@echo "  transcripts             List transcript files"
	@echo "  replays                 List replay files"
	@echo "  list-artifacts          List build artifacts"
	@echo "  test-artifact-migration Run canonical path and migration safety tests"
	@echo ""
	@echo "Doctor (接入验收):"
	@echo "  doctor cc               检查 claudecode (骨架/Makefile/Resource/e2e)"
	@echo "  doctor-all              检查 4 个成熟 agent"
	@echo ""
	@echo "Examples:"
	@echo "  make replay-all all"
	@echo "  make validate cc"
	@echo "  make validate ocw FORMAT=json"
	@echo "  make validate-one ocd FILE=$(AIVISION_HOME)/ocd/replays/spans_x.jsonl FORMAT=json"
	@echo "  make report ocw TS=session_x.jsonl CONFIG=report.config.toml PROFILE=dev"
	@echo "  make report-shift ocd TS=session_x.jsonl CONFIG=report.config.toml PROFILE=dev"
	@echo "  make report-check cc WEAVER_URL=http://localhost:4319/"
	@echo "  make report-check-golden ocw"
	@echo "  make report-check-all"
	@echo "  make build-edition cc EDITION=common"
	@echo "  make release wg"
	@echo "  make install km ED=aivision           Build & install kimi aivision edition"
	@echo "  make install km ED=common             Build & install kimi common edition"
	@echo "  make install ocw-replay               Build, register, and configure openclaw-replay"
	@echo "  make install ocd                      Build and register observ-opencode"
	@echo "  make install ocd-replay               Build, register, and configure opencode-replay"
	@echo "  make install pi                       Build/register observ-pi production plugin only"
	@echo "  make install pi-replay                Build/register the local pireplay plugin only"
	@echo "  make install ds                       Register observ-dsh into dsh profiles (web+tui: plugin + cordis patch)"
	@echo "  make replay-all ds                    Replay dsh session fixtures/corpus"
	@echo "  make release pi                       Package observ-pi common + aivision editions"
	@echo "  make golden-check cc,cdx"
	@echo "  make help cc"
endif

# AI Vision local distribution: production plugins only, no replay or internal agents.
.PHONY: plugin-packages test-onboarding verify-plugin-dist verify-hook-dist
.PHONY: test-privacy-node
.PHONY: test-privacy-hooks test-privacy-hook-probe
.PHONY: test-metrics-auth
test-metrics-auth:
	cargo test --locked --manifest-path agentic-otel-common/Cargo.toml --features metrics metrics_headers
	cargo test --locked --manifest-path codex/cdxhooks/Cargo.toml --no-default-features --features common --bin cdxhooks metrics_headers
	cargo test --locked --manifest-path claudecode/cchooks/Cargo.toml --no-default-features --features common --bin cchooks metrics_headers
.PHONY: test-privacy-replay
test-privacy-replay:
	cargo build --manifest-path codex/cdxhooks/Cargo.toml --no-default-features --features common --bin cdxhooks-replay
	cargo build --manifest-path claudecode/cchooks/Cargo.toml --no-default-features --features common --bin cchooks-replay
	cargo build --manifest-path kimi/kimihooks/Cargo.toml --no-default-features --bin kimihooks-replay
	$(PLUGIN_PYTHON) scripts/privacy_replay_probe.py
test-privacy-hook-probe:
	$(PLUGIN_PYTHON) -m unittest discover -s scripts/tests -p 'test_privacy_hook_probe.py'
test-privacy-hooks: test-privacy-hook-probe
	@set -e; for agent in codex claude-code kimi; do \
		for mode in full privacy; do \
			$(PLUGIN_PYTHON) scripts/privacy_hook_probe.py --agent $$agent --mode $$mode; \
		done; \
	done
test-privacy-node:
	@set -e; for agent in opencode/observ-opencode openclaw/observ-openclaw pi/observ-pi deepseek/observ-dsh; do \
		for mode in full privacy; do \
			./opencode/observ-opencode/node_modules/.bin/tsx scripts/privacy_node_probe.mts $$agent $$mode; \
		done; \
	done
PLUGIN_AGENT ?= all
PLUGIN_PYTHON ?= python3
plugin-packages:
	$(PLUGIN_PYTHON) scripts/build_plugins.py --agent "$(PLUGIN_AGENT)" $(if $(TARGET),--target "$(TARGET)",) $(if $(INSTALL_DEPS),--install-deps,)

test-onboarding:
	$(PLUGIN_PYTHON) -m unittest discover -s scripts/tests -p 'test_onboarding*.py'

verify-plugin-dist:
	$(PLUGIN_PYTHON) scripts/verify_plugin_dist.py $(if $(PLATFORM),--platform "$(PLATFORM)",)

verify-hook-dist:
	$(PLUGIN_PYTHON) scripts/verify_plugin_dist.py --agent codex --agent claude-code --agent kimi --platform "$(PLATFORM)" --require-privacy --run-native $(if $(MAX_GLIBC),--max-glibc "$(MAX_GLIBC)",)
