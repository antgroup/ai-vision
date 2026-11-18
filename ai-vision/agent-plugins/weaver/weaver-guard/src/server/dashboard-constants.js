// dashboard-constants.js — shared state, configuration constants, and design tokens.

const $ = id => document.getElementById(id);

const state = {
  stats: {},
  settings: { forward_policy: 'always' },
  records: [],
  rules: { groups: { spans: [], metrics: [], events: [] }, policy_groups: {}, disabled: [], disabled_policy_groups: [] },
  findings: [],
  activeFinding: null,
  activePayloadText: '',
  activePayloadTarget: null,
  activeDiagnosis: 'trace',
  activeRuleGroups: {},
  expandedRules: {},
  expandedRuleTrees: {},
  activeRuleSelections: {},
  expandedFindingGroups: {},
  expandedFindingTrees: {},
  activeFindingGroups: {},
  scrolling: false,
  scrollTimer: null,
  queuedRenderOptions: null,
  refreshInFlight: false,
  refreshPromise: null,
  queuedRefreshOptions: null,
  dataRevision: 0,
};
let pendingConfirmResolve = null;

const AUTO_REFRESH_MS = 3000;
const SCROLL_IDLE_MS = 240;
const htmlCache = new WeakMap();

const SIGNALS = {
  traces: { group: 'spans', label: 'Traces', tabLabel: 'Traces', singular: 'trace/span', backends: ['jaeger'] },
  events: { group: 'events', label: 'Events / Logs', tabLabel: 'Events', singular: 'event/log', backends: ['victoria-logs'] },
  metrics: { group: 'metrics', label: 'Metrics', tabLabel: 'Metrics', singular: 'metric', backends: ['victoria-metrics'] },
};
const GROUP_LABELS = { spans: 'Spans', metrics: 'Metrics', events: 'Events / Logs' };
const SIGNAL_POLICY_DIR = { traces: 'traces', events: 'events', metrics: 'metrics' };
const POLICY_DIRECTORIES = {
  base: { label: 'Base', signalGroups: ['spans', 'metrics', 'events'] },
  traces: { label: 'Traces', signalGroups: ['spans'] },
  metrics: { label: 'Metrics', signalGroups: ['metrics'] },
  events: { label: 'Events', signalGroups: ['events'] },
};
const EMPTY_RULE_GROUPS = { spans: [], metrics: [], events: [] };
const OWNER_LABELS = {
  instrumentation: '上报/埋点问题',
  rules: '规则/语义规则问题',
  triage: '待确认问题',
};
const FINDING_FIELD_BUCKET_ORDER = ['required', 'recommended', 'optional', 'forbidden'];
const METRIC_FIELD_BUCKET_ORDER = ['required', 'recommended', 'optional', 'constraint', 'forbidden'];
const FINDING_FIELD_BUCKET_LABELS = {
  required: '必须字段',
  recommended: '推荐字段',
  optional: '可选字段',
  constraint: '值/不变量',
  forbidden: '禁止字段',
};
const SEMANTIC_CONDITION_ORDER = ['common', 'success', 'error', 'unclassified'];
const SEMANTIC_CONDITION_LABELS = {
  common: '通用约束',
  success: '正确请求',
  error: '错误请求',
  unclassified: '需要补注释',
};
const SEMANTIC_BUCKET_HINTS = {
  required: '缺少会产生 violation。',
  recommended: '缺少会产生 improvement，用于丰富诊断。',
  optional: '可选采集字段；缺少不影响通过。',
  constraint: '类型、单位、枚举、数值边界等非 presence 约束。',
  forbidden: '出现会产生 violation；仅表示禁止出现的字段或 prefix。',
};
const CONSTRAINT_TYPE_LABELS = {
  required_presence: '必须存在',
  recommended_presence: '建议存在',
  forbidden_presence: '禁止出现',
  enum: '允许值',
  fixed_value: '固定值',
  format: '格式',
  placement: '位置',
  namespace: '命名空间',
  compatibility: '兼容性',
  optional_info: '可选信息',
  invariant: '不变量',
};
const DETAIL_ONLY_CONSTRAINT_TYPES = new Set(['enum', 'fixed_value', 'format', 'placement', 'namespace', 'compatibility', 'optional_info', 'invariant']);
const CONSTRAINT_TYPE_ORDER = [
  'required_presence',
  'recommended_presence',
  'forbidden_presence',
  'fixed_value',
  'enum',
  'format',
  'placement',
  'namespace',
  'compatibility',
  'optional_info',
  'invariant',
];
const FORWARD_POLICY_HINTS = {
  always: '默认：无论 pass / warning / violation，都写入 Victoria endpoint。',
  'pass-or-improvement': '只有没有 rejected violation 时写入；warning/improvement 仍会写入。',
  'pass-only': '只有完全通过且没有 finding 时写入。',
  never: '只做本地诊断，不写入 Victoria endpoint。',
};
const RED_API_METRICS = [
  'agent.api_request.count',
  'agent.api_request.error.count',
  'agent.api_request.duration',
];
const RED_TOOL_METRICS = [
  'agent.tool_call.count',
  'agent.tool_call.error.count',
  'agent.tool_call.duration',
];
const RED_ERROR_METRICS = [
  'agent.api_request.error.count',
  'agent.tool_call.error.count',
];
const RED_COUNT_METRICS = [
  'agent.api_request.count',
  'agent.api_request.error.count',
  'agent.tool_call.count',
  'agent.tool_call.error.count',
];
const RED_DURATION_METRICS = [
  'agent.api_request.duration',
  'agent.tool_call.duration',
];
const RED_METRICS = [...RED_API_METRICS, ...RED_TOOL_METRICS];
const RED_METRIC_INFO = {
  'agent.api_request.count': { family: 'API request', red: 'Rate', shape: 'counter/sum', unit: '{count}', dims: ['gen_ai.request.model', 'ant.username'] },
  'agent.api_request.error.count': { family: 'API request', red: 'Errors', shape: 'counter/sum', unit: '{count}', dims: ['gen_ai.request.model', 'ant.username', 'agentic.error.category'] },
  'agent.api_request.duration': { family: 'API request', red: 'Duration', shape: 'histogram', unit: 'ms', dims: ['gen_ai.request.model', 'ant.username'] },
  'agent.tool_call.count': { family: 'Tool', red: 'Rate', shape: 'counter/sum', unit: '{count}', dims: ['agentic.tool.type', 'ant.username'] },
  'agent.tool_call.error.count': { family: 'Tool', red: 'Errors', shape: 'counter/sum', unit: '{count}', dims: ['agentic.tool.type', 'ant.username', 'agentic.error.category'] },
  'agent.tool_call.duration': { family: 'Tool', red: 'Duration', shape: 'histogram', unit: 'ms', dims: ['agentic.tool.type', 'ant.username'] },
};
const RED_METRIC_SET = new Set(RED_METRICS);
const METRIC_LANE_ORDER = [
  'metric_dimensions',
  'metric_dimension_constraints',
  'metric_constraints',
  'metric_datapoint_constraints',
  'metric_forbidden_dimensions',
];
const METRIC_LANE_LABELS = {
  metric_dimensions: '维度',
  metric_dimension_constraints: '维度约束',
  metric_constraints: '指标约束',
  metric_datapoint_constraints: '数据点约束',
  metric_forbidden_dimensions: '禁止维度',
};
const METRIC_LANE_HINTS = {
  metric_dimensions: '该 metric data point 必须携带的低基数字段。',
  metric_dimension_constraints: 'metric data point 维度的枚举、格式、固定值或兼容约束。',
  metric_constraints: 'metric 对象自身的类型、单位、单调性和聚合形态约束。',
  metric_datapoint_constraints: 'metric data point 数值、bucket、count 或 histogram 边界约束。',
  metric_forbidden_dimensions: '禁止作为 metric 维度上报的高基数或明细字段；明细应保留在 events/logs。',
};
const METRIC_IDENTITY_CONSTRAINT_LABEL = '指标类型 / 形状';

const LOOP_FIX_LEVELS = {
  violation: {
    title: 'Violation only',
    query: 'level=violation',
    target: '只修复 violation。semconv / improvement / information 只读取、归类并在最终报告中列出，不主动修改。',
    done: '停止条件：violation 为 0；其它等级保留为报告项。',
  },
  blocking: {
    title: 'Violation + Semconv',
    query: 'level=violation,semconv',
    target: '修复 violation 和 semconv issue。improvement / information 只在低风险且与同一根因直接相关时顺手处理，否则记录为剩余建议。',
    done: '停止条件：violation 为 0，semconv issue 为 0；improvement / information 有明确剩余说明。',
  },
  all: {
    title: 'All levels',
    query: 'levels=all',
    target: '修复所有等级：violation、semconv、improvement、information。先处理 blocking 级问题，再做推荐项和信息项。',
    done: '停止条件：所有可修复 finding 为 0；无法修复项必须有证据、原因和 owner 建议。',
  },
};

const LOOP_SIGNAL_ORDER = ['traces', 'events', 'metrics'];
const LOOP_SIGNAL_CONFIG = {
  traces: { label: 'Traces', endpoint: '/v1/traces', query: 'signal=traces' },
  events: { label: 'Events / Logs', endpoint: '/v1/logs', query: 'signal=events' },
  metrics: { label: 'Metrics', endpoint: '/v1/metrics', query: 'signal=metrics' },
};

const LOOP_CONFIRMATION = {
  risk: '默认自主修复；触及语义标准降低、删除规则、批量 suppression、外部上报行为变化、敏感 payload 外传或无法判断归因时，必须先向用户确认。',
  always: '每一轮准备修改前都先给用户列出计划、文件、风险和预期验证命令，等用户确认后再改。',
  never: '不需要逐步确认；在本地可逆改动范围内自主修复、验证和迭代，但最终报告必须列出所有修改与风险。',
};

const LOOP_REPORT_MODES = {
  manual: {
    title: '等待用户手动触发后继续校验',
    instruction: '每轮修复后暂停，明确要求用户手动触发一次目标 agent/session/tool 的真实上报；用户确认完成后，再继续查询 AiVision。',
  },
  command: {
    title: '自动执行命令',
    placeholder: '例如：make replay-all；或 CCHOOKS_DEBUG=true <agent command>；命令必须产生真实 OTLP 上报到 127.0.0.1:4319。',
    fallback: '用户尚未填写命令。先让用户补充可在本仓库执行、且会产生真实 OTLP 上报的命令；不要用假数据代替。',
    label: '上报命令',
  },
  'report-check': {
    title: '约定使用 make report-check 命令',
    instruction: '每轮修复后执行 `make report-check` 触发上报和校验；该命令必须由用户在项目 Makefile 中定义，并负责产生真实 OTLP 上报到 127.0.0.1:4319。',
  },
};

const BOOTSTRAP_LEVELS = {
  lightweight: {
    title: '轻量级 Bootstrap',
    audience: '自研 agent 或非 claudecode/codex 的项目。',
    goal: '只完成目标项目 producer / instrumentation 的首版接入，让选中信号能通过标准 OpenTelemetry SDK + OTLP HTTP 上报到本地 AiVision。',
    stop: '停止条件：选中信号已经能向 127.0.0.1:4319 产生第一版真实上报，并可交给“语义修正Loop”继续 validate / clear / re-report。',
    scope: [
      '只改目标项目的 producer / instrumentation 代码和必要依赖。',
      '允许引入标准 OpenTelemetry SDK 与 OTLP HTTP exporter。',
      '禁止修改 AiVision / Weaver 侧 Rego、semconv、dashboard、校验规则。',
      '禁止补 transcript / replay / Makefile / Golden / CAPABILITIES；这些只属于标准 Agent Bootstrap。',
      '禁止硬编码假字段；字段必须来自真实 runtime、session、hook payload 或合理的目标项目上下文。',
    ],
  },
  standard: {
    title: '标准 Agent Bootstrap',
    audience: 'claudecode/codex-like 的新 agent 接入；需要完整 transcript → replay → validate → report 工程闭环。',
    goal: '完成完整 agent 接入工程闭环：实时 OTLP 上报 + 离线 transcript 采集 + replay 生成 + validate 校验 + report 诊断 + Makefile 标准化。',
    stop: '停止条件：(1) 实时上报到 127.0.0.1:4319 返回 HTTP 200 且 dashboard 可见数据；(2) make replay-all 能从 transcripts 生成 replay 文件；(3) make validate 通过且无 blocking violation；(4) make report-check 能把 replay POST 到本地 AiVision 并获取校验结果。剩余字段问题交给"语义修正Loop"。',
    scope: [
      '包含轻量级 Bootstrap 的全部 endpoint、SDK、OTLP HTTP exporter 和语义字段要求。',
      'Transcript 采集：实现环境变量 {PREFIX}_SESSION_DIR / {PREFIX}_REPLAY_DIR / {PREFIX}_DEBUG；按数据源能力设计采集策略（持久化 session 文件 / hook callback / API 日志），输出 transcripts/*.jsonl。',
      'Replay 生成：实现 replay CLI 或脚本，将 transcript JSONL 转为标准 OTLP replay 文件（spans_*.jsonl / events_*.jsonl / metrics_*.jsonl），输出到 replays/ 目录。',
      'Report 与校验：make validate 用 weaver-guard 校验 replays/；make report-check 将 replay POST 到本地 AiVision 获取实时校验结果；make report-shift 从新 transcript 一步生成 replay 并校验。',
      'Makefile 标准化：补齐 build / replay / replay-all / validate / validate-one / report / report-check / report-shift / golden-check / clean；引用 ../weaver/policies 和 ../docs/semconv。',
      'Golden 基线（可选）：{agent}/golden/ 存放基线 replay，make golden-check 对比回归；仅在需要稳定回归检测时纳入。',
    ],
  },
};

const GOLDEN_REPLAY_SIGNALS = {
  traces: {
    label: 'Traces (spans)',
    file: 'spans_golden.jsonl',
    rootKey: 'resourceSpans',
    resource: {
      entity: 'resource:traces',
      required: ['service.name', 'ant.agent.type', 'ant.agent.env', 'ant.agent.scene', 'agentic.collection.mode'],
      recommended: ['service.version', 'ant.agent.instance', 'ant.agent.product'],
      enums: {
        'ant.agent.type': ['claudecode', 'codex', 'opencode', 'openclaw'],
        'agentic.collection.mode': ['full', 'privacy'],
      },
    },
    spans: [
      {
        entity: 'span:turn',
        label: 'Turn (CHAT)',
        spanName: 'Turn 1',
        fixed: { 'gen_ai.span.kind': 'CHAT', 'gen_ai.operation.name': 'invoke_agent' },
        required: ['agentic.runtime.name', 'agentic.turn.number'],
        common: true,
      },
      {
        entity: 'span:llm',
        label: 'LLM',
        spanName: 'LLM Request 1',
        fixed: { 'gen_ai.span.kind': 'LLM', 'gen_ai.operation.name': 'chat' },
        required: ['gen_ai.request.model', 'gen_ai.provider.name', 'gen_ai.usage.input_tokens', 'gen_ai.usage.output_tokens', 'agentic.llm.duration_ms'],
        recommended: ['gen_ai.response.model', 'gen_ai.response.finish_reasons', 'error.type'],
        common: true,
      },
      {
        entity: 'span:tool',
        label: 'Tool',
        spanName: 'Tool: Bash',
        fixed: { 'gen_ai.span.kind': 'TOOL', 'gen_ai.operation.name': 'execute_tool' },
        required: ['gen_ai.tool.call.arguments', 'gen_ai.tool.call.result', 'agentic.tool.duration_ms'],
        recommended: ['gen_ai.tool.name', 'gen_ai.tool.call.id', 'error.type'],
        common: true,
      },
      {
        entity: 'span:compaction',
        label: 'Compaction',
        spanName: 'Compaction 1',
        fixed: { 'gen_ai.span.kind': 'COMPACTION' },
        required: [],
        recommended: ['agentic.compaction.trigger', 'agentic.compaction.tokens.before', 'agentic.compaction.tokens.after'],
        common: true,
      },
    ],
    commonRequired: ['user.id', 'ant.username', 'gen_ai.conversation.id', 'gen_ai.chat.id', 'gen_ai.operation.name', 'gen_ai.span.kind'],
  },
  events: {
    label: 'Events / Logs',
    file: 'events_golden.jsonl',
    rootKey: 'resourceLogs',
    resource: {
      entity: 'resource:events',
      required: ['service.name', 'ant.agent.type', 'ant.agent.env', 'ant.agent.scene', 'agentic.collection.mode'],
      recommended: ['service.version', 'ant.agent.instance', 'ant.agent.product'],
    },
    events: [
      {
        entity: 'event:user_prompt',
        label: 'user_prompt',
        fixed: { 'event.name': 'user_prompt', 'gen_ai.operation.name': 'user_prompt' },
        required: ['gen_ai.input.messages'],
      },
      {
        entity: 'event:api_request',
        label: 'api_request (success)',
        fixed: { 'event.name': 'api_request', 'gen_ai.operation.name': 'chat' },
        required: ['duration_ms', 'gen_ai.system_instructions'],
        recommended: ['gen_ai.provider.name', 'agentic.usage.cache_read_tokens'],
        condition: 'success',
      },
      {
        entity: 'event:api_request',
        label: 'api_request (error)',
        fixed: { 'event.name': 'api_request', 'gen_ai.operation.name': 'chat' },
        required: ['duration_ms', 'error.type', 'error.message', 'gen_ai.system_instructions'],
        condition: 'error',
      },
      {
        entity: 'event:tool_result',
        label: 'tool_result (success)',
        fixed: { 'event.name': 'tool_result', 'gen_ai.operation.name': 'execute_tool' },
        required: ['gen_ai.tool.call.result'],
        enums: { 'agentic.tool.type': ['builtin', 'mcp', 'skill'] },
      },
      {
        entity: 'event:tool_result',
        label: 'tool_result (error)',
        fixed: { 'event.name': 'tool_result', 'gen_ai.operation.name': 'execute_tool' },
        required: ['gen_ai.tool.call.result', 'error.type', 'error.message'],
      },
      {
        entity: 'event:session_start',
        label: 'session_start',
        fixed: { 'event.name': 'session_start', 'gen_ai.operation.name': 'session_start' },
        required: ['gen_ai.session.id'],
      },
      {
        entity: 'event:session_end',
        label: 'session_end',
        fixed: { 'event.name': 'session_end', 'gen_ai.operation.name': 'session_end' },
        required: ['agentic.turn.number'],
      },
      {
        entity: 'event:compaction_result',
        label: 'compaction_result',
        fixed: { 'event.name': 'compaction_result', 'gen_ai.operation.name': 'execute_tool', 'gen_ai.tool.name': 'compaction', 'agentic.tool.type': 'system' },
        required: ['duration_ms'],
      },
    ],
    commonRequired: ['event.name', 'traceId', 'spanId', 'agentic.runtime.name'],
    commonRecommended: ['event.sequence'],
  },
  metrics: {
    label: 'Metrics',
    file: 'metrics_golden.jsonl',
    rootKey: 'resourceMetrics',
    metrics: [
      { name: 'agent.api_request.count', shape: 'sum', unit: '{count}', monotonic: true, dims: ['gen_ai.request.model', 'ant.username'] },
      { name: 'agent.api_request.error.count', shape: 'sum', unit: '{count}', monotonic: true, dims: ['gen_ai.request.model', 'ant.username', 'agentic.error.category'] },
      { name: 'agent.api_request.duration', shape: 'histogram', unit: 'ms', dims: ['gen_ai.request.model', 'ant.username'] },
      { name: 'agent.tool_call.count', shape: 'sum', unit: '{count}', monotonic: true, dims: ['agentic.tool.type', 'ant.username'] },
      { name: 'agent.tool_call.error.count', shape: 'sum', unit: '{count}', monotonic: true, dims: ['agentic.tool.type', 'ant.username', 'agentic.error.category'] },
      { name: 'agent.tool_call.duration', shape: 'histogram', unit: 'ms', dims: ['agentic.tool.type', 'ant.username'] },
    ],
    errorCategories: ['api_error', 'network_error', 'timeout', 'rate_limit', 'auth', 'permission', 'validation', 'tool_error', 'cancelled', 'other'],
    toolTypes: ['builtin', 'mcp', 'skill'],
  },
};

const SUPERLOOP_AGENT_CONFIGS = {
  claudecode: {
    id: 'claudecode',
    label: 'Claude Code',
    short: 'cc',
    arch: 'cli',
    cli: 'claude -p "{prompt}" --dangerously-skip-permissions',
    envVars: {
      debug: 'CCHOOKS_DEBUG=true',
      replayDir: 'CCHOOKS_REPLAY_DIR',
      sessionDir: 'CCHOOKS_SESSION_DIR',
    },
    replaySubdir: 'golden/replays',
    transcriptSubdir: 'golden/transcripts',
    prerequisites: [
      'claude --version (确认 Claude Code CLI 已安装)',
      'curl http://127.0.0.1:4319/api/stats (确认 AiVision 已启动；未启动则先运行 make -C weaver serve)',
    ],
    makeTarget: 'make golden-collect cc',
  },
  openclaw: {
    id: 'openclaw',
    label: 'OpenClaw',
    short: 'ocw',
    arch: 'cli',
    cli: 'openclaw agent --agent main --session-key main -m "{prompt}" --json',
    envVars: {
      runtime: [
        'AGENT_ENABLE_TELEMETRY=true',
        'OPENCLAW_REPLAY_DEBUG=true',
        'OPENCLAW_REPLAY_DIR="$PWD/{agentId}/{replaySubdir}"',
        'OPENCLAW_REPLAY_SESSION_DIR="$PWD/{agentId}/{transcriptSubdir}"',
        'OPENCLAW_REPLAY_SESSION_NAME=golden-superloop',
        'OBSERV_OPENCLAW_LOG_DIR="$PWD/{agentId}/{logSubdir}"',
        'AIVISION_COLLECTION_MODE=full',
        'AIVISION_ANT_USERNAME="${AIVISION_ANT_USERNAME:-openclaw-golden}"',
        'AIVISION_USER_ID="${AIVISION_USER_ID:-openclaw-golden}"',
        'OPENCLAW_USER_ID="${OPENCLAW_USER_ID:-openclaw-golden}"',
        'AIVISION_AGENT_TYPE=openclaw',
        'AIVISION_AGENT_PRODUCT=OpenClaw',
        'AIVISION_AGENT_INSTANCE="${AIVISION_AGENT_INSTANCE:-openclaw-golden}"',
        'AIVISION_AGENT_ENV=local',
        'AIVISION_AGENT_SCENE=golden-superloop',
        'AIVISION_DYNAMIC_USERNAME=true',
        'AIVISION_DYNAMIC_UID=true',
        'OTEL_SERVICE_NAME=openclaw',
        'OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4319',
        'OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf',
        'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4319/v1/traces',
        'OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://127.0.0.1:4319/v1/metrics',
        'OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4319/v1/logs',
        'OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta',
        'OTEL_METRIC_EXPORT_INTERVAL=5000',
        'OTEL_LOGS_EXPORT_INTERVAL=1000',
      ],
      optionalRuntime: [
        'AIVISION_ANT_PUBLIC_KEY=<public-key>',
        'ANT_PUBLIC_KEY=<public-key>',
        'OTEL_EXPORTER_OTLP_HEADERS=key=value',
        'OTEL_RESOURCE_ATTRIBUTES=key=value',
        'AIVISION_OTEL_RESOURCE_ATTRIBUTES=key=value',
        'OTEL_EXPORTER_OTLP_TRACES_HEADERS=key=value',
        'OTEL_EXPORTER_OTLP_METRICS_HEADERS=key=value',
        'OTEL_EXPORTER_OTLP_LOGS_HEADERS=key=value',
      ],
      replayTool: [],
    },
    replaySubdir: 'golden/replays',
    transcriptSubdir: 'golden/transcripts',
    replayInputSubdir: 'golden/hook-inputs',
    logSubdir: 'golden/logs',
    fixtureSubdir: 'golden/fixtures',
    fixtureFile: 'README.fixture.md',
    prerequisites: [
      'openclaw --version (确认 OpenClaw CLI 已安装)',
      'openclaw agent --help (默认使用 agent 子命令执行 golden；不依赖 channel/account)',
      'openclaw message send --help (仅当你明确要走 gateway/message route 时检查)',
      'openclaw plugins inspect observ-openclaw --runtime (确认实时 OTel 主插件已安装、启用且可加载；失败则先运行 make -C openclaw install-aivision)',
      'openclaw plugins inspect openclaw-replay --runtime (确认 transcript/replay 插件已安装、启用且可加载；失败则先运行 make -C openclaw replay-plugin-install)',
      'curl http://127.0.0.1:4319/api/stats (确认 AiVision 已启动；未启动则先运行 make -C weaver serve)',
    ],
    setupCommands: [
      'if openclaw config get plugins.entries.openclaw-replay --json > "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.json" 2>/dev/null; then printf present > "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.state"; else printf absent > "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.state"; printf \'{}\\n\' > "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.json"; fi',
      'openclaw config set plugins.entries.openclaw-replay.enabled true --strict-json',
      'openclaw config set plugins.entries.openclaw-replay.hooks.allowConversationAccess true --strict-json',
      'openclaw config set plugins.entries.openclaw-replay.config.sessionDir "$PWD/{agentId}/{transcriptSubdir}"',
      'openclaw config set plugins.entries.openclaw-replay.config.sessionName golden-superloop',
      'openclaw config set plugins.entries.openclaw-replay.config.debug true --strict-json',
      'openclaw config set plugins.entries.openclaw-replay.config.replayDir "$PWD/{agentId}/{replaySubdir}"',
      'touch "$PWD/{agentId}/{fixtureSubdir}/golden-superloop.started"',
    ],
    postScenarioChecks: [
      'find "$PWD/{agentId}/{transcriptSubdir}" -type f -name \'*.jsonl\' -size +0c -newer "$PWD/{agentId}/{fixtureSubdir}/golden-superloop.started" -print -quit | grep -q . || { echo "ERROR: openclaw-replay 本轮未生成或更新非空 transcript；检查插件加载状态和 hooks.allowConversationAccess" >&2; golden_finalize_status=1; }',
      'find "$PWD/{agentId}/{replaySubdir}" -type f -name \'*.jsonl\' -size +0c -newer "$PWD/{agentId}/{fixtureSubdir}/golden-superloop.started" -print -quit | grep -q . || { echo "ERROR: openclaw-replay 本轮未生成或更新非空 trace replay；检查 config.debug 和 config.replayDir" >&2; golden_finalize_status=1; }',
    ],
    cleanupCommands: [
      'if [ ! -f "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.state" ]; then echo "ERROR: replay 插件配置备份状态丢失，保留备份文件并停止" >&2; golden_finalize_status=1; elif [ "$(cat "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.state")" = present ]; then if openclaw config set plugins.entries.openclaw-replay "$(cat "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.json")" --strict-json; then rm -f "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.json" "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.state"; else echo "ERROR: replay 插件原配置恢复失败；备份文件已保留" >&2; golden_finalize_status=1; fi; elif [ "$(cat "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.state")" = absent ]; then if openclaw config unset plugins.entries.openclaw-replay; then rm -f "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.json" "$PWD/{agentId}/{fixtureSubdir}/openclaw-replay.config.backup.state"; else echo "ERROR: replay 插件原空配置恢复失败；备份状态已保留" >&2; golden_finalize_status=1; fi; else echo "ERROR: replay 插件配置备份状态无效，保留备份文件并停止" >&2; golden_finalize_status=1; fi',
      'rm -f "$PWD/{agentId}/{fixtureSubdir}/golden-superloop.started"',
    ],
    gatewaySetup: [
      '默认不要走 message send；本提示词默认用 openclaw agent --agent main --session-key main -m "..." --json，避免依赖 channel/account。',
      '如果必须走 openclaw message send，命令必须显式提供 --target，并按本机配置提供 --channel / --account；无 channels configured 时不要尝试 message send，直接使用默认 agent 路由。',
      '当前 OpenClaw gateway start 只有 --json，没有 --background；不要使用不存在的 --background 参数。',
      'gateway start 走 LaunchAgent/system service 时，shell inline env 不一定注入到实际 gateway 进程；需要可注入环境时，优先用 foreground openclaw gateway run，或使用上方已临时写入的 openclaw-replay 配置后 gateway restart。',
    ],
    gatewayConfigCommands: [
      'openclaw gateway restart',
    ],
    gatewayStopCmd: 'openclaw gateway stop',
    gatewayRunCmd: 'openclaw gateway run',
    gatewayStartCmd: 'openclaw gateway start --json',
    messageSendCmd: 'openclaw message send --target <target> --channel <channel> --account <account> --message "{prompt}"',
    makeTarget: 'make report-check-golden ocw',
    fallbackFlow: [
      '如果 openclaw agent 不可用，再考虑 message send 或人工 channel。',
      'message send 路由必须先确认 target/channel/account 已配置；否则标记为 blocked，不要伪造执行。',
      'openclaw-replay 消费已有 transcript/hook JSONL；运行时 transcript 只有在 replay 插件的 sessionDir / OPENCLAW_REPLAY_SESSION_DIR 已启用时才会写入 golden/transcripts。',
    ],
    signalBoundary: [
      'OPENCLAW_REPLAY_DIR 当前用于 debug dual-write 的 OTLP trace spans 文件；不要默认期待 events/logs/metrics 也落到 replay 目录。',
      'Events/Logs/Metrics 是本轮语义目标和 HTTP 上报目标；如果本轮没有实际产出对应信号，应在结果中说明，不要补造 JSONL。',
      'OPENCLAW_REPLAY_SESSION_DIR 或 replay 插件 config.sessionDir 用于启用运行时 transcript 输出；必须先启用它，OPENCLAW_REPLAY_SESSION_NAME / config.sessionName 才控制文件名。',
      'OPENCLAW_REPLAY_SESSION_NAME=aa 会写到 session_aa.jsonl；OPENCLAW_REPLAY_SESSION_NAME=aa.jsonl 会写到 aa.jsonl；不设置时保持 session_<sessionKey>_<timestamp>.jsonl；名称只按 basename 使用并清理路径字符。',
    ],
    scenarioOverrides: {
      'tool-success': {
        prompt: '读取这个明确 fixture 文件：$PWD/openclaw/golden/fixtures/README.fixture.md，告诉我文件有多少行，并引用第一行内容。',
        notes: '使用固定 fixture 路径，避免 OpenClaw workspace 不是 repo 根目录时读取失败。',
      },
      'mcp-tool': {
        notes: '需要 OpenClaw 暴露一个可用的只读 MCP 搜索工具。缺少工具或授权时，标记为 skipped 并记录原因。',
      },
      'skill-call': {
        prompt: '使用 skill-creator 生成一个名为 hangzhou-weather 的 skill proposal，用于查看当前杭州天气；只生成 proposal 和文件计划，不要 apply/写入文件。如果流程要求确认是否 apply，请停在 proposal 阶段并说明 skipped-apply。',
        notes: 'skill-creator 可能进入交互式 proposal/apply 确认。非交互 golden 只要求 proposal，不要求 apply，避免卡在确认态。',
      },
    },
  },
};
