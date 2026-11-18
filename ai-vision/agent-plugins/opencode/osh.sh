#!/usr/bin/env bash
#
# osh.sh — OpenCode + Observability 一键启动脚本
#
# 用法:
#   ./osh.sh                  # 正常启动（自动检测 Langfuse 配置）
#   ./osh.sh --debug          # DEBUG 模式（额外双写 OTLP 到本地文件）
#   ./osh.sh --dry-run        # 只打印环境变量，不启动 opencode
#   ./osh.sh run "你好"       # 非交互式运行
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="${SCRIPT_DIR}/observ-opencode"
PLUGIN_DIST="${PLUGIN_DIR}/dist"

# ─── 颜色 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { echo -e "${CYAN}[osh]${RESET} $*"; }
ok()    { echo -e "${GREEN}[osh]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[osh]${RESET} $*"; }
error() { echo -e "${RED}[osh]${RESET} $*" >&2; }

# ─── 参数解析 ─────────────────────────────────────────────────────────────────
DEBUG_MODE=false
DRY_RUN=false
OPENCODE_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug|-d)
      DEBUG_MODE=true
      shift
      ;;
    --dry-run|-n)
      DRY_RUN=true
      shift
      ;;
    *)
      OPENCODE_ARGS+=("$1")
      shift
      ;;
  esac
done

# ─── 1. 构建插件（如果 dist 不存在或 src 更新了）─────────────────────────────
build_plugin() {
  if [[ ! -d "$PLUGIN_DIST" ]] || \
     [[ -n "$(find "${PLUGIN_DIR}/src" -newer "${PLUGIN_DIST}/index.js" -type f 2>/dev/null)" ]]; then
    info "Building observ-opencode plugin..."
    (cd "$PLUGIN_DIR" && npm install --silent && npm run build --silent)
    ok "Plugin built: ${PLUGIN_DIST}"
  else
    ok "Plugin up to date"
  fi
}

# ─── 2. Langfuse 密钥解析 ────────────────────────────────────────────────────
KEYS_JSON="${PLUGIN_DIR}/keys.json"

# 从 JSON 文件中读取字段值
json_get() {
  local file="$1" key="$2"
  python3 -c "import json; print(json.load(open('$file')).get('$key',''))" 2>/dev/null || echo ""
}

resolve_langfuse_keys() {
  # 优先级: 环境变量 > observ-opencode/keys.json

  if [[ -n "${LANGFUSE_PUBLIC_KEY:-}" && -n "${LANGFUSE_SECRET_KEY:-}" ]]; then
    ok "Langfuse keys from env vars"
    return 0
  fi

  # 确定环境：prod 或 gray
  local env_key="prod"
  if [[ "${LANGFUSE_ENV:-prod}" == "gray" || "${ANT_GRAY:-}" == "true" ]]; then
    env_key="gray"
  fi

  # 从 observ-opencode/keys.json 读取
  if [[ -f "$KEYS_JSON" ]]; then
    local pub_key sec_key host
    pub_key=$(json_get "$KEYS_JSON" "${env_key}_public_key")
    sec_key=$(json_get "$KEYS_JSON" "${env_key}_secret_key")
    host=$(json_get "$KEYS_JSON" "${env_key}_host")

    if [[ -n "$pub_key" && -n "$sec_key" ]]; then
      export LANGFUSE_PUBLIC_KEY="$pub_key"
      export LANGFUSE_SECRET_KEY="$sec_key"
      [[ -n "$host" ]] && export LANGFUSE_HOST="$host"
      ok "Langfuse keys from keys.json (${env_key})"
      return 0
    fi
  fi

  warn "No Langfuse keys found — telemetry will be disabled"
  warn "Fix: add keys to observ-opencode/keys.json or set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY"
  return 1
}

# Append a key=value pair without replacing caller-provided OTel attributes.
append_otel_attribute() {
  local variable_name="$1"
  local key="$2"
  local value="$3"
  local current="${!variable_name:-}"
  if [[ ",${current}," != *",${key}="* ]]; then
    printf -v "$variable_name" '%s%s%s=%s' "$current" "${current:+,}" "$key" "$value"
    export "$variable_name"
  fi
}

# ─── 3. 设置环境变量 ──────────────────────────────────────────────────────────
setup_env() {
  # 总开关
  export AGENT_ENABLE_TELEMETRY=1

  # 用户信号属性与 Agent Resource 元数据统一走 OTel 配置。
  local git_user
  git_user=$(git config user.name 2>/dev/null || true)
  append_otel_attribute OBSERV_OPENCODE_OTEL_ATTRIBUTES ant.username "${git_user:-$(whoami)}"
  append_otel_attribute OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES ant.agent.type opencode
  append_otel_attribute OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES ant.agent.product OpenCode

  # 内容日志：全部打开
  export AGENT_LOG_USER_PROMPTS=true
  export AGENT_LOG_SYSTEM_PROMPTS=true
  export AGENT_LOG_MODEL_OUTPUTS=true
  export AGENT_LOG_TOOL_DETAILS=true
  export AGENT_LOG_TOOL_OUTPUTS=true

  # Transcript 输出目录
  export OPENCODE_SESSION_DIR="${OPENCODE_SESSION_DIR:-${SCRIPT_DIR}/transcripts}"
  mkdir -p "$OPENCODE_SESSION_DIR"

  # DEBUG 模式：双写 OTLP 到本地文件
  if [[ "$DEBUG_MODE" == true ]]; then
    export OPENCODE_DEBUG=true
    export OPENCODE_REPLAY_DIR="${OPENCODE_REPLAY_DIR:-${SCRIPT_DIR}/replays}"
    mkdir -p "$OPENCODE_REPLAY_DIR"
    ok "DEBUG mode: OTLP dual-write → ${OPENCODE_REPLAY_DIR}"
  fi

  # 解析 Langfuse 密钥
  resolve_langfuse_keys || true

  # 将 Langfuse 密钥转换为标准 OTEL 环境变量
  if [[ -n "${LANGFUSE_PUBLIC_KEY:-}" && -n "${LANGFUSE_SECRET_KEY:-}" ]]; then
    local lf_host="${LANGFUSE_HOST:-https://cloud.langfuse.com}"
    local lf_endpoint="${lf_host}/api/public/otel"
    local lf_auth
    lf_auth="Basic $(echo -n "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" | base64)"

    # 设置 traces endpoint（Langfuse OTLP 入口）
    export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-${lf_endpoint}/v1/traces}"
    export OTEL_EXPORTER_OTLP_TRACES_HEADERS="${OTEL_EXPORTER_OTLP_TRACES_HEADERS:-Authorization=${lf_auth}}"
    export OTEL_EXPORTER_OTLP_PROTOCOL="${OTEL_EXPORTER_OTLP_PROTOCOL:-http/protobuf}"
    ok "Langfuse → OTEL: ${lf_endpoint}"
  fi
}

# ─── 4. 确保插件已注册到 opencode.json ────────────────────────────────────────
OPENCODE_CONFIG="${HOME}/.config/opencode/opencode.json"

ensure_plugin_registered() {
  if [[ ! -f "$OPENCODE_CONFIG" ]]; then
    warn "opencode.json not found at ${OPENCODE_CONFIG}"
    return
  fi

  # 检查是否已注册 observ-opencode
  if python3 -c "
import json, sys
cfg = json.load(open('$OPENCODE_CONFIG'))
plugins = cfg.get('plugin', [])
for p in plugins:
    name = p if isinstance(p, str) else (p[0] if isinstance(p, list) else '')
    if 'observ-opencode' in name:
        sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
    ok "Plugin already registered in opencode.json"
    return
  fi

  info "Registering observ-opencode plugin..."
  python3 -c "
import json
cfg_path = '$OPENCODE_CONFIG'
with open(cfg_path) as f:
    cfg = json.load(f)

plugins = cfg.get('plugin', [])
plugins.append(['${PLUGIN_DIST}', {
    'enableTelemetry': True,
    'ant': {
        'agentType': 'opencode',
        'agentProduct': 'opencode'
    }
}])
cfg['plugin'] = plugins

with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print('Done')
"
  ok "Plugin registered: ${PLUGIN_DIST}"
}

# ─── 5. 打印配置摘要 ──────────────────────────────────────────────────────────
print_summary() {
  echo ""
  echo -e "${BOLD}┌─ OpenCode Observability ─────────────────────────┐${RESET}"
  echo -e "│ ${CYAN}AGENT_ENABLE_TELEMETRY${RESET}  = ${GREEN}${AGENT_ENABLE_TELEMETRY}${RESET}"
  echo -e "│ ${CYAN}OTEL signal attributes${RESET} = ${GREEN}${OBSERV_OPENCODE_OTEL_ATTRIBUTES:-<not set>}${RESET}"
  echo -e "│ ${CYAN}OTEL resource attrs${RESET}    = ${GREEN}${OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES:-<not set>}${RESET}"
  if [[ -n "${LANGFUSE_PUBLIC_KEY:-}" ]]; then
  echo -e "│ ${CYAN}LANGFUSE_PUBLIC_KEY${RESET}     = ${GREEN}***set*** (${LANGFUSE_PUBLIC_KEY:0:12}...)${RESET}"
  else
  echo -e "│ ${CYAN}LANGFUSE_PUBLIC_KEY${RESET}     = ${RED}<not set>${RESET}"
  fi
  if [[ -n "${LANGFUSE_SECRET_KEY:-}" ]]; then
  echo -e "│ ${CYAN}LANGFUSE_SECRET_KEY${RESET}     = ${GREEN}***set***${RESET}"
  else
  echo -e "│ ${CYAN}LANGFUSE_SECRET_KEY${RESET}     = ${RED}<not set>${RESET}"
  fi
  echo -e "│ ${CYAN}LANGFUSE_HOST${RESET}           = ${GREEN}${LANGFUSE_HOST:-<auto>}${RESET}"
  if [[ -n "${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-}" ]]; then
  echo -e "│ ${CYAN}OTEL_TRACES_ENDPOINT${RESET}    = ${GREEN}${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT}${RESET}"
  fi
  echo -e "│ ${CYAN}OPENCODE_SESSION_DIR${RESET}     = ${GREEN}${OPENCODE_SESSION_DIR}${RESET}"
  if [[ "$DEBUG_MODE" == true ]]; then
  echo -e "│ ${CYAN}OPENCODE_DEBUG${RESET}           = ${YELLOW}true${RESET}"
  echo -e "│ ${CYAN}OPENCODE_REPLAY_DIR${RESET}      = ${GREEN}${OPENCODE_REPLAY_DIR}${RESET}"
  fi
  echo -e "${BOLD}└──────────────────────────────────────────────────┘${RESET}"
  echo ""
}

# ─── Main ─────────────────────────────────────────────────────────────────────
main() {
  info "Setting up OpenCode observability..."

  build_plugin
  setup_env
  ensure_plugin_registered
  print_summary

  if [[ "$DRY_RUN" == true ]]; then
    info "Dry run — not starting opencode"
    info "To start manually: opencode ${OPENCODE_ARGS[*]:-}"
    exit 0
  fi

  ok "Launching opencode..."
  exec opencode "${OPENCODE_ARGS[@]:-}"
}

main
