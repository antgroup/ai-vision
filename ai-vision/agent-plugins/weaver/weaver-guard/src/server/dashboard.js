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

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}

function markdownInline(value) {
  return esc(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

function markdownToHtml(markdown) {
  const lines = String(markdown || '').split(/\r?\n/);
  const html = [];
  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i];
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith('### ')) {
      html.push(`<h4>${markdownInline(line.slice(4))}</h4>`);
    } else if (line.startsWith('## ')) {
      html.push(`<h3>${markdownInline(line.slice(3))}</h3>`);
    } else if (line.startsWith('# ')) {
      html.push(`<h2>${markdownInline(line.slice(2))}</h2>`);
    } else if (line.startsWith('|')) {
      const tableLines = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i].trim());
        i += 1;
      }
      i -= 1;
      const rows = tableLines
        .filter(row => !/^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(row))
        .map(row => row.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim()));
      if (rows.length) {
        const [head, ...body] = rows;
        html.push(`<table class="markdown-table"><thead><tr>${head.map(cell => `<th>${markdownInline(cell)}</th>`).join('')}</tr></thead><tbody>${body.map(row => `<tr>${row.map(cell => `<td>${markdownInline(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
      }
    } else if (line.startsWith('- ')) {
      const items = [];
      while (i < lines.length && lines[i].trim().startsWith('- ')) {
        items.push(lines[i].trim().slice(2));
        i += 1;
      }
      i -= 1;
      html.push(`<ul>${items.map(item => `<li>${markdownInline(item)}</li>`).join('')}</ul>`);
    } else {
      html.push(`<p>${markdownInline(line)}</p>`);
    }
  }
  return html.join('');
}

function setHtmlIfChanged(target, html) {
  const el = typeof target === 'string' ? $(target) : target;
  if (!el) return false;
  if (htmlCache.get(el) === html) return false;
  htmlCache.set(el, html);
  el.innerHTML = html;
  return true;
}

function captureRuleTreeScroll(containerId) {
  const tree = $(containerId)?.querySelector('.semantic-rule-tree');
  return tree ? { top: tree.scrollTop, left: tree.scrollLeft } : null;
}

function restoreRuleTreeScroll(containerId, snapshot) {
  if (!snapshot) return;
  const apply = () => {
    const tree = $(containerId)?.querySelector('.semantic-rule-tree');
    if (!tree) return;
    tree.scrollTop = snapshot.top;
    tree.scrollLeft = snapshot.left;
  };
  apply();
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(apply);
}

function cssIdent(value) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(String(value));
  return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
}

function cap(value) {
  return String(value).charAt(0).toUpperCase() + String(value).slice(1);
}

function levelClass(level) {
  const normalized = String(level || 'information').toLowerCase();
  if (normalized.includes('violation')) return 'violation';
  if (normalized.includes('improvement') || normalized.includes('warning')) return 'improvement';
  if (normalized.includes('semconv')) return 'semconv';
  return normalized || 'information';
}

function badge(level, label = level) {
  return `<span class="badge badge-${esc(levelClass(level))}">${esc(label || 'information')}</span>`;
}

function statusBadge(status) {
  const normalized = String(status || 'unknown').toLowerCase();
  if (normalized === 'pass') return badge('pass', 'pass');
  if (normalized === 'warning') return badge('improvement', 'warning');
  if (normalized === 'violation' || normalized === 'error' || normalized === 'decode_error') return badge('violation', status);
  return badge('muted', status || 'unknown');
}

function forwardItems(record) {
  if (Array.isArray(record?.forwards) && record.forwards.length) return record.forwards;
  return record?.forward ? [record.forward] : [];
}

function forwardPills(record) {
  const items = forwardItems(record);
  if (!items.length) return '<span class="meta-line">no backend decision</span>';
  return `<div class="forward-pills">${items.map(item => {
    const label = item.backend || 'backend';
    const detail = item.reason || item.status || '';
    return `<span class="forward-pill ${esc(levelClass(item.decision))}">
      <b>${esc(label)}</b> ${esc(item.decision || 'unknown')}${detail ? `<small>${esc(detail)}</small>` : ''}
    </span>`;
  }).join('')}</div>`;
}

function signalGroup(signal) {
  const value = String(signal || '').toLowerCase();
  if (value.includes('metric')) return 'metrics';
  if (value.includes('log') || value.includes('event')) return 'events';
  return 'spans';
}

function signalKeyForGroup(group) {
  return Object.entries(SIGNALS).find(([, config]) => config.group === group)?.[0] || 'traces';
}

function signalKeyForRecord(record) {
  return signalKeyForGroup(signalGroup(record?.signal));
}

function groupLabel(group) {
  return GROUP_LABELS[group] || group || 'Unknown';
}

function policyGroupEntries() {
  const entries = Object.entries(state.rules.policy_groups || {});
  if (entries.length) return entries;
  const groups = state.rules.groups || EMPTY_RULE_GROUPS;
  const rules = Object.values(groups).flat();
  return [[
    'default',
    {
      name: 'default',
      enabled: true,
      total: rules.length,
      enabled_rules: rules.filter(rule => rule.enabled !== false).length,
      sections: { default: { name: 'default', key: 'default:default', enabled: true, total: rules.length, enabled_rules: rules.filter(rule => rule.enabled !== false).length, groups } },
      groups,
    },
  ]];
}

function policyGroupLabel(name) {
  if (!name || name === 'default') return 'default';
  return name;
}

function allRules() {
  return policyGroupEntries().flatMap(([, policyGroup]) => {
    const sections = Object.values(policyGroup.sections || {});
    if (sections.length) return sections.flatMap(section => Object.values(section.groups || {}).flat());
    return Object.values(policyGroup.groups || {}).flat();
  });
}

function recordsForSignal(signal) {
  return state.records.filter(record => signalGroup(record.signal) === SIGNALS[signal].group);
}

function findingsForSignal(signal) {
  return state.findings.filter(item => item.group === SIGNALS[signal].group);
}

function signalFindingCounts(signal) {
  return findingItems(signal).reduce((acc, item) => {
    const level = levelClass(item.finding?.level);
    if (level === 'violation') acc.violation += 1;
    else if (level === 'improvement') acc.improvement += 1;
    else acc.information += 1;
    return acc;
  }, { violation: 0, improvement: 0, information: 0 });
}

function renderPrimaryTabs() {
  document.querySelectorAll('[data-primary-tab]').forEach(tab => {
    const signal = tab.dataset.primaryTab;
    const config = SIGNALS[signal];
    if (!config) return;
    const counts = signalFindingCounts(signal);
    const label = config.tabLabel || config.label;
    const countHtml = [
      ['violation', counts.violation, 'violations'],
      ['improvement', counts.improvement, 'improvements'],
      ['information', counts.information, 'information'],
    ]
      .filter(([, count]) => count > 0)
      .map(([level, count, title]) => `<span class="tab-level-count ${esc(level)}" title="${esc(count)} ${esc(title)}">${esc(count)}</span>`)
      .join('');
    const html = `<span class="tab-signal-label">${esc(label)}</span>
      ${countHtml ? `<span class="tab-level-counts" aria-hidden="true">${countHtml}</span>` : ''}`;
    setHtmlIfChanged(tab, html);
    tab.setAttribute('aria-label', `${label}: ${counts.violation} violations, ${counts.improvement} improvements, ${counts.information} information`);
  });
}

function rulesForSignal(signal) {
  const group = SIGNALS[signal].group;
  return allRules().filter(rule => rule.group === group);
}

function backendState(backendId) {
  return state.stats.backends?.[backendId] || { id: backendId, configured: false, proxy_available: false, counts: {}, last: null };
}

function backendCardShell(backendId) {
  return `<div class="backend-row" data-backend-card="${esc(backendId)}">
    <div class="backend-main">
      <div class="backend-title">
        <span class="section-kicker" data-backend-signal>backend</span>
        <strong data-backend-id>${esc(backendId)}</strong>
        <span data-backend-badge>${badge('disabled', 'loading')}</span>
      </div>
      <code class="backend-url" data-backend-endpoint>loading…</code>
      <div class="backend-counts">
        <span data-backend-count="forwarded">ok 0</span>
        <span data-backend-count="failed">failed 0</span>
        <span data-backend-count="blocked">blocked 0</span>
        <span data-backend-count="skipped">skipped 0</span>
      </div>
      <div class="backend-actions" data-backend-actions></div>
    </div>
    <div class="backend-subline">
      <span class="meta-line" data-backend-last>Last: none</span>
    </div>
    <div data-backend-frame></div>
  </div>`;
}

function prepareBackendFrame(backendId, frameUrl) {
  if (backendId === 'jaeger' || String(frameUrl || '').startsWith('/jaeger/')) {
    try {
      window.localStorage.setItem('jaeger-ui-theme', 'light');
    } catch (_) {
      // Local storage can be unavailable in hardened browser profiles; Jaeger
      // still loads, only falling back to its own theme detection.
    }
  }
}

function backendFrameIsReady(frameRoot) {
  if (!frameRoot) return false;
  const rect = frameRoot.getBoundingClientRect();
  return rect.width >= 320 && rect.height >= 0 && frameRoot.offsetParent !== null;
}

function activateBackendFrame(card, backendId) {
  const iframe = card?.querySelector('iframe.backend-iframe');
  if (!iframe) return false;
  const frameUrl = iframe.dataset.backendFrameUrl || '';
  if (!frameUrl || iframe.dataset.loaded === 'true') return true;
  const frameRoot = iframe.closest('[data-backend-frame]');
  if (!backendFrameIsReady(frameRoot)) return false;
  prepareBackendFrame(backendId, frameUrl);
  iframe.setAttribute('src', frameUrl);
  iframe.dataset.loaded = 'true';
  return true;
}

function activateVisibleBackendFrames() {
  document.querySelectorAll('[data-backend-card]').forEach(card => {
    activateBackendFrame(card, card.dataset.backendCard);
  });
}

function scheduleBackendFrameActivation() {
  const run = () => activateVisibleBackendFrames();
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
  else setTimeout(run, 0);
}

function updateBackendCard(card, backendId) {
  const backend = backendState(backendId);
  const counts = backend.counts || {};
  const last = backend.last || {};
  const directUrl = backend.direct_url || backend.ui_url || '';
  const frameUrl = backend.iframe_url || '';
  const openUrl = frameUrl || directUrl;
  const proxyAvailable = backend.proxy_available || Boolean(frameUrl || directUrl);
  const cardState = backend.configured ? 'forwarding' : proxyAvailable ? 'ui-only' : 'disabled';
  card.classList.toggle('disabled', cardState === 'disabled');
  card.querySelector('[data-backend-signal]').textContent = backend.signal || 'backend';
  card.querySelector('[data-backend-id]').textContent = backend.id || backendId;
  card.querySelector('[data-backend-badge]').innerHTML = backend.configured
    ? badge('ok', 'forwarding')
    : proxyAvailable ? badge('improvement', 'ui only') : badge('disabled', 'not configured');
  card.querySelector('[data-backend-endpoint]').textContent = backend.endpoint || '未配置 endpoint；不会转发';
  card.querySelector('[data-backend-count="forwarded"]').textContent = `ok ${counts.forwarded || 0}`;
  card.querySelector('[data-backend-count="failed"]').textContent = `failed ${counts.failed || 0}`;
  card.querySelector('[data-backend-count="blocked"]').textContent = `blocked ${counts.blocked || 0}`;
  card.querySelector('[data-backend-count="skipped"]').textContent = `skipped ${counts.skipped || 0}`;
  card.querySelector('[data-backend-last]').textContent = `Last: ${last.decision || 'none'}${last.reason ? ` · ${last.reason}` : ''}${last.status ? ` · HTTP ${last.status}` : ''}`;
  setHtmlIfChanged(card.querySelector('[data-backend-actions]'), `
    ${openUrl ? `<a class="btn btn-outline" href="${esc(openUrl)}" target="_blank" rel="noreferrer">Open UI</a>` : '<button class="btn btn-outline" type="button" disabled>No UI URL</button>'}
    ${directUrl && directUrl !== openUrl ? `<a class="btn btn-ghost" href="${esc(directUrl)}" target="_blank" rel="noreferrer">Open direct</a>` : ''}
    <button class="btn btn-secondary" type="button" data-refresh-backend-frame="${esc(backendId)}" ${frameUrl ? '' : 'disabled'}>刷新页面</button>`);

  const frameRoot = card.querySelector('[data-backend-frame]');
  const iframe = frameRoot.querySelector('iframe.backend-iframe');
  if (!frameUrl) {
    setHtmlIfChanged(frameRoot, '<div class="backend-frame-placeholder">配置 backend UI 或 endpoint 后，这里会嵌入对应 dashboard。</div>');
    return;
  }
  if (iframe && iframe.dataset.backendFrameUrl === frameUrl) {
    activateBackendFrame(card, backendId);
    return;
  }
  setHtmlIfChanged(frameRoot, `<div class="backend-frame-wrap">
    <iframe class="backend-iframe" title="${esc((backend.id || backendId) + ' dashboard')}" src="about:blank" data-backend-frame-url="${esc(frameUrl)}" loading="eager"></iframe>
  </div>`);
  scheduleBackendFrameActivation();
}

function refreshBackendFrame(backendId) {
  const card = document.querySelector(`[data-backend-card="${cssIdent(backendId)}"]`);
  const iframe = card?.querySelector('iframe.backend-iframe');
  if (!iframe) {
    toast('当前 backend 没有可刷新的页面');
    return;
  }
  if (!activateBackendFrame(card, backendId)) {
    toast('页面显示后会自动加载');
    return;
  }
  try {
    iframe.contentWindow.location.reload();
  } catch (_) {
    const src = iframe.dataset.backendFrameUrl || iframe.getAttribute('src');
    iframe.setAttribute('src', 'about:blank');
    const restore = () => iframe.setAttribute('src', src);
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(restore);
    else setTimeout(restore, 0);
  }
  toast('页面已刷新');
}

function renderBackendGrid(container, backendIds) {
  const wanted = new Set(backendIds);
  container.querySelectorAll('[data-backend-card]').forEach(card => {
    if (!wanted.has(card.dataset.backendCard)) card.remove();
  });
  backendIds.forEach(backendId => {
    let card = container.querySelector(`[data-backend-card="${cssIdent(backendId)}"]`);
    if (!card) {
      container.insertAdjacentHTML('beforeend', backendCardShell(backendId));
      card = container.querySelector(`[data-backend-card="${cssIdent(backendId)}"]`);
    }
    updateBackendCard(card, backendId);
  });
}

function sectionEntriesForPolicyGroup(policyGroup, signal = null) {
  const sections = policyGroup.sections || {};
  let names = Object.keys(sections);
  if (!names.length) names = ['default'];
  if (signal) {
    const wanted = new Set(['base', SIGNAL_POLICY_DIR[signal], 'default']);
    names = names.filter(name => wanted.has(name));
  }
  return names.sort(policyDirSort).map(name => [name, sections[name] || {
    name,
    key: `${policyGroup.name || 'default'}:${name}`,
    enabled: true,
    total: 0,
    enabled_rules: 0,
    groups: policyGroup.groups || EMPTY_RULE_GROUPS,
  }]);
}

function policyDirSort(a, b) {
  const order = ['base', 'traces', 'metrics', 'events', 'default'];
  const ia = order.indexOf(a);
  const ib = order.indexOf(b);
  return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
}

function policyDirLabel(name) {
  return POLICY_DIRECTORIES[name]?.label || cap(name || 'default');
}

function rulesForSection(section, signal = null) {
  if (!signal) return Object.values(section.groups || {}).flat();
  return section.groups?.[SIGNALS[signal].group] || [];
}

function ruleTitle(rule) {
  return rule?.purpose_zh || rule?.title_zh || rule?.id || rule?.rule || '规则';
}

function ruleEntityLabel(rule) {
  return rule?.entity || (rule?.group ? `${rule.group}:${rule.policy_dir || 'unknown'}` : '未声明 entity');
}

function ruleCategoryLabel(rule) {
  return rule?.category_zh || '未分组规则';
}

function ruleFields(rule) {
  return Array.isArray(rule?.fields) ? rule.fields.filter(Boolean) : [];
}

function uniqueValues(values) {
  return Array.from(new Set(values.map(value => String(value || '').trim()).filter(Boolean)));
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function redMetricNamesFromText(text) {
  const source = String(text || '');
  return RED_METRICS.filter(metric => {
    const pattern = new RegExp(`(^|[^A-Za-z0-9_.:-])${escapeRegExp(metric)}([^A-Za-z0-9_.:-]|$)`);
    return pattern.test(source);
  });
}

function isExactRedMetricName(value) {
  return RED_METRIC_SET.has(String(value || '').trim());
}

function isExactRedMetricSubject(subject) {
  const normalized = normalizeRuleSubject(subject);
  return normalized?.kind === 'field' && isExactRedMetricName(normalized.value);
}

function ruleRedMetricSubjectNames(rule) {
  return ruleSubjects(rule)
    .filter(isExactRedMetricSubject)
    .map(subject => subject.value);
}

function ruleRedMetricScopes(rule) {
  const fromSubjects = ruleRedMetricSubjectNames(rule);
  const direct = ruleFields(rule).filter(field => RED_METRIC_SET.has(field));
  const fromSource = redMetricNamesFromText(rule?.source);
  const exactScopes = uniqueValues([...fromSubjects, ...direct, ...fromSource]).filter(metric => RED_METRIC_SET.has(metric));
  if (exactScopes.length) return exactScopes;
  let inferred = [];
  const entity = String(rule?.entity || '').toLowerCase();
  const id = String(rule?.id || rule?.rule || '').toLowerCase();
  const source = String(rule?.source || '');

  if (entity === 'metric:api_request') inferred = RED_API_METRICS;
  if (entity === 'metric:llm') inferred = RED_API_METRICS;
  if (entity === 'metric:tool') inferred = RED_TOOL_METRICS;
  if (entity === 'metric:golden' || entity === 'metric:common') {
    if (id.includes('error_count') || id.includes('error_category')) {
      inferred = RED_ERROR_METRICS;
    } else if (id.includes('duration')) {
      inferred = RED_DURATION_METRICS;
    } else if (id.includes('count') || id.includes('monotonic')) {
      inferred = RED_COUNT_METRICS;
    } else {
      inferred = RED_METRICS;
    }
  }
  if (!direct.length && !fromSource.length && !inferred.length && source.includes('_is_red_golden_metric')) {
    inferred = RED_METRICS;
  }

  return uniqueValues(inferred).filter(metric => RED_METRIC_SET.has(metric));
}

function redMetricSummaryLabel(metric) {
  const info = RED_METRIC_INFO[metric];
  return info ? `${metric} (${info.family} ${info.red})` : metric;
}

function redMetricSort(metric) {
  const index = RED_METRICS.indexOf(metric);
  return index === -1 ? 99 : index;
}

function metricNameEntity(metric) {
  const info = RED_METRIC_INFO[metric] || {};
  return {
    kind: 'metric_name',
    key: normalizedSemanticKey(metric),
    label: metric,
    subtitle: `${info.family || 'Metric'} ${info.red || 'RED'} · ${info.shape || 'metric'} · ${info.unit || ''}`,
    sort: 40 + redMetricSort(metric),
    metric_name: metric,
  };
}

function metricScopeEntitiesForRule(rule, signal) {
  if (signal !== 'metrics') return [];
  const entity = String(rule?.entity || '').trim().toLowerCase();
  if (!entity.startsWith('metric:')) return [];
  const key = entity.slice('metric:'.length);
  if (key === 'base' || key === 'common') return [];
  return ruleRedMetricScopes(rule).map(metricNameEntity);
}

function compactRedMetricScope(metrics, limit = 2) {
  const values = uniqueValues(metrics);
  if (!values.length) return '';
  const shown = values.slice(0, limit).join(', ');
  return values.length > limit ? `${shown} +${values.length - limit}` : shown;
}

function redMetricScopeChipsHtml(metrics) {
  const values = uniqueValues(metrics);
  if (!values.length) return '';
  return `<div class="field-chip-row metric-scope-row compact">
    ${values.map(metric => `<span class="field-chip metric-scope-chip" title="${esc(redMetricSummaryLabel(metric))}">${esc(redMetricSummaryLabel(metric))}</span>`).join('')}
  </div>`;
}

function redMetricScopeSummaryHtml(metrics) {
  const values = uniqueValues(metrics);
  if (!values.length) return '';
  return `<div class="metric-scope-panel">
    <b>适用 RED 指标</b>
    <div class="metric-scope-grid">
      ${values.map(metric => {
        const info = RED_METRIC_INFO[metric] || {};
        return `<span>
          <code>${esc(metric)}</code>
          <small>${esc(info.family || 'Metric')} · ${esc(info.red || 'RED')} · ${esc(info.shape || 'metric')} · ${esc(info.unit || '')}</small>
          <em>${esc((info.dims || []).join(', '))}</em>
        </span>`;
      }).join('')}
    </div>
  </div>`;
}

function isSemanticFieldName(value) {
  const text = String(value || '').trim();
  if (!text) return false;
  if (/^[A-Z][A-Z0-9_:-]*$/.test(text)) return false;
  return /^[a-z_][a-z0-9_.:-]*$/i.test(text);
}

function uniqueSemanticFields(values) {
  return Array.from(new Set(values.map(value => String(value || '').trim()).filter(isSemanticFieldName)));
}

function ruleSourceFields(rule) {
  const source = String(rule?.source || '');
  const fields = [];
  const attrCall = /\b(?:has|get)_(?:span|log|resource|metric_dp)_attr\s*\(\s*"([^"]+)"\s*\)/g;
  let match;
  while ((match = attrCall.exec(source)) !== null) {
    fields.push(match[1]);
  }
  return uniqueSemanticFields(fields);
}

function ruleDisplayFields(rule) {
  const sourceFields = ruleSourceFields(rule);
  return sourceFields.length ? sourceFields : uniqueSemanticFields(ruleFields(rule));
}

function displaySemanticField(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  return text.endsWith('.') ? `${text}*` : text;
}

function ruleDisplayFieldLabels(rule) {
  return ruleDisplayFields(rule).map(displaySemanticField);
}

function normalizePrefixSubjectValue(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  if (text.endsWith('.*')) return text;
  if (text.endsWith('.')) return `${text}*`;
  return text;
}

function normalizeRuleSubject(subject) {
  if (!subject) return null;
  if (typeof subject === 'string') {
    const [kind, ...rest] = subject.split(':');
    const value = rest.join(':').trim();
    return normalizeRuleSubject({ kind: kind.trim(), value });
  }
  const kind = String(subject.kind || '').trim();
  let value = String(subject.value || '').trim();
  if (!kind || !value) return null;
  if (kind === 'prefix') value = normalizePrefixSubjectValue(value);
  if (kind !== 'field' && kind !== 'prefix') return null;
  return { kind, value };
}

function ruleSubjects(rule) {
  const subjects = Array.isArray(rule?.subjects) ? rule.subjects : [];
  return subjects.map(normalizeRuleSubject).filter(Boolean);
}

function subjectKey(subject) {
  const normalized = normalizeRuleSubject(subject);
  return normalized ? `${normalized.kind}:${normalized.value}` : '';
}

function subjectDisplayLabel(subject) {
  const normalized = normalizeRuleSubject(subject);
  if (!normalized) return '未声明字段';
  if (normalized.kind === 'prefix') return `prefix: ${normalized.value}`;
  return normalized.value;
}

function looksLikeSemanticField(value) {
  const text = String(value || '').trim();
  return Boolean(text) && text.includes('.') && /^[A-Za-z_][A-Za-z0-9_.:-]*\*?$/.test(text);
}

function addPayloadTargetTerm(terms, kind, value, source = 'finding') {
  let text = String(value || '').trim();
  if (!text) return;
  if (kind === 'prefix') {
    text = text.replace(/\*$/, '');
    if (text.endsWith('.*')) text = text.slice(0, -1);
  }
  const isField = kind === 'field' && /^[A-Za-z_][A-Za-z0-9_.:-]*$/.test(text);
  if (!isField && !looksLikeSemanticField(text)) return;
  const key = `${kind}:${text}`;
  if (terms.some(term => term.key === key)) return;
  terms.push({ key, kind, value: text, source });
}

function quotedFieldCandidates(text) {
  const candidates = [];
  const source = String(text || '');
  const patterns = [
    /`([A-Za-z_][A-Za-z0-9_.:-]+\*?)`/g,
    /'([A-Za-z_][A-Za-z0-9_.:-]+\*?)'/g,
    /"([A-Za-z_][A-Za-z0-9_.:-]+\*?)"/g,
  ];
  patterns.forEach(pattern => {
    let match;
    while ((match = pattern.exec(source)) !== null) candidates.push(match[1]);
  });
  return candidates;
}

function payloadTargetForFinding(item) {
  if (!item) return null;
  const terms = [];
  const ctx = item.finding?.context && typeof item.finding.context === 'object' ? item.finding.context : {};
  ['attr', 'attribute', 'attribute_id', 'missing', 'field', 'key', 'deprecated'].forEach(key => {
    const value = ctx?.[key];
    if (typeof value === 'string') addPayloadTargetTerm(terms, value.endsWith('.*') ? 'prefix' : 'field', value, `context.${key}`);
  });
  if (!terms.length) {
    ruleSubjects(item.rule).forEach(subject => addPayloadTargetTerm(terms, subject.kind, subject.value, 'rule.subject'));
  }
  if (!terms.length) {
    ruleDisplayFields(item.rule).forEach(field => addPayloadTargetTerm(terms, 'field', field, 'rule.fields'));
  }
  if (!terms.length) {
    quotedFieldCandidates(item.finding?.message).forEach(field => addPayloadTargetTerm(terms, field.endsWith('.*') ? 'prefix' : 'field', field, 'message'));
  }
  const sampleNames = [];
  [item.finding?.sample, ctx?.span_name, ctx?.metric_name].forEach(value => {
    const text = String(value || '').trim();
    if (text && !sampleNames.includes(text)) sampleNames.push(text);
  });
  const contextName = String(ctx?.name || '').trim();
  if (contextName && !looksLikeSemanticField(contextName) && !sampleNames.includes(contextName)) sampleNames.push(contextName);
  const actualValues = [];
  ['actual_value', 'actual', 'value'].forEach(key => {
    const value = ctx?.[key];
    if (['string', 'number', 'boolean'].includes(typeof value)) {
      const text = String(value);
      if (text && !actualValues.includes(text)) actualValues.push(text);
    }
  });
  return {
    findingId: item.id,
    requestId: item.record?.request_id,
    sampleNames,
    terms,
    actualValues,
  };
}

function lineMatchesPayloadTerm(line, term) {
  if (!term?.value) return false;
  if (term.kind === 'prefix') return line.includes(`"${term.value}`) || line.includes(term.value);
  return line.includes(`"${term.value}"`);
}

function payloadLineMatchesAnyTerm(line, terms) {
  return terms.some(term => lineMatchesPayloadTerm(line, term));
}

function lineIndent(line) {
  return String(line || '').match(/^\s*/)?.[0].length || 0;
}

function jsonBraceDelta(line) {
  let delta = 0;
  let inString = false;
  let escaped = false;
  for (const char of String(line || '')) {
    if (escaped) {
      escaped = false;
      continue;
    }
    if (char === '\\' && inString) {
      escaped = true;
      continue;
    }
    if (char === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (char === '{') delta += 1;
    if (char === '}') delta -= 1;
  }
  return delta;
}

function highlightPayloadLine(line, terms) {
  const ranges = [];
  terms.forEach(term => {
    const needle = term?.kind === 'field' ? `"${term?.value || ''}"` : (term?.value || '');
    if (!needle) return;
    let from = 0;
    while (from < line.length) {
      const index = line.indexOf(needle, from);
      if (index === -1) break;
      ranges.push([index, index + needle.length]);
      from = index + Math.max(needle.length, 1);
    }
  });
  if (!ranges.length) return esc(line);
  ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const merged = [];
  ranges.forEach(range => {
    const last = merged[merged.length - 1];
    if (!last || range[0] > last[1]) merged.push([...range]);
    else last[1] = Math.max(last[1], range[1]);
  });
  let html = '';
  let cursor = 0;
  merged.forEach(([start, end]) => {
    html += esc(line.slice(cursor, start));
    html += `<strong class="payload-hit-token">${esc(line.slice(start, end))}</strong>`;
    cursor = end;
  });
  html += esc(line.slice(cursor));
  return html;
}

function payloadSampleLineIndexes(lines, target) {
  const sampleNames = target?.sampleNames || [];
  if (!sampleNames.length) return [];
  const indexes = [];
  for (const name of sampleNames) {
    lines.forEach((line, index) => {
      if (
        line.includes(`"name": ${JSON.stringify(name)}`) ||
        line.includes(`"body": ${JSON.stringify(name)}`) ||
        line.includes(JSON.stringify(name))
      ) {
        indexes.push(index);
      }
    });
  }
  return Array.from(new Set(indexes)).sort((a, b) => a - b);
}

function payloadSampleLineIndex(lines, target) {
  return payloadSampleLineIndexes(lines, target)[0] ?? -1;
}

function payloadObjectRangeAroundLine(lines, lineIndex) {
  if (lineIndex < 0) return null;
  const sampleIndent = lineIndent(lines[lineIndex]);
  let start = -1;
  for (let index = lineIndex; index >= 0; index -= 1) {
    const trimmed = String(lines[index] || '').trim();
    if (trimmed === '{' && lineIndent(lines[index]) < sampleIndent) {
      start = index;
      break;
    }
  }
  if (start === -1) return null;
  let balance = 0;
  for (let index = start; index < lines.length; index += 1) {
    balance += jsonBraceDelta(lines[index]);
    if (balance === 0) return { start, end: index, sampleLine: lineIndex };
  }
  return null;
}

function lineWindowContainsAny(lines, index, values, scope = null) {
  const actualValues = (values || []).filter(Boolean);
  if (!actualValues.length) return false;
  const start = Math.max(scope?.start ?? 0, index - 1);
  const end = Math.min(scope?.end ?? lines.length - 1, index + 6);
  for (let lineIndex = start; lineIndex <= end; lineIndex += 1) {
    if (actualValues.some(value => lines[lineIndex].includes(JSON.stringify(value)) || lines[lineIndex].includes(String(value)))) {
      return true;
    }
  }
  return false;
}

function payloadHitLineIndexes(lines, target, scope = null) {
  const terms = target?.terms || [];
  if (!terms.length) return [];
  const start = scope?.start ?? 0;
  const end = scope?.end ?? (lines.length - 1);
  const hits = [];
  for (let index = start; index <= end; index += 1) {
    if (payloadLineMatchesAnyTerm(lines[index], terms)) hits.push(index);
  }
  const actualHits = hits.filter(index => lineWindowContainsAny(lines, index, target?.actualValues || [], scope));
  return actualHits.length ? actualHits : hits;
}

function payloadSearchScope(lines, target) {
  const indexes = payloadSampleLineIndexes(lines, target);
  if (!indexes.length) return null;
  const ranges = indexes.map(index => payloadObjectRangeAroundLine(lines, index)).filter(Boolean);
  if (!ranges.length) return null;
  return ranges
    .slice()
    .sort((a, b) => {
      const aHits = payloadHitLineIndexes(lines, target, a);
      const bHits = payloadHitLineIndexes(lines, target, b);
      const aActual = aHits.some(index => lineWindowContainsAny(lines, index, target?.actualValues || [], a));
      const bActual = bHits.some(index => lineWindowContainsAny(lines, index, target?.actualValues || [], b));
      return Number(bActual) - Number(aActual) || bHits.length - aHits.length || a.sampleLine - b.sampleLine;
    })[0];
}

function primaryPayloadHitLine(lines, target, scope = null, hits = null) {
  const hitIndexes = hits || payloadHitLineIndexes(lines, target, scope);
  if (hitIndexes.length) return hitIndexes[0];
  return scope?.sampleLine ?? payloadSampleLineIndex(lines, target);
}

function renderPayloadText(text, target = null) {
  const lines = String(text ?? '').split('\n');
  const terms = target?.terms || [];
  const hasTarget = terms.length || (target?.sampleNames || []).length;
  if (!hasTarget) return esc(text);
  const scope = payloadSearchScope(lines, target);
  if (!scope) return esc(text);
  const scopedHits = payloadHitLineIndexes(lines, target, scope);
  const primaryLine = primaryPayloadHitLine(lines, target, scope, scopedHits);
  const hitSet = new Set(scopedHits);
  return lines.map((line, index) => {
    const hit = hitSet.has(index);
    const primary = index === primaryLine;
    const classes = ['payload-line'];
    if (hit) classes.push('payload-line-hit');
    if (primary) classes.push('payload-line-primary');
    const attrs = primary ? ' data-payload-primary="true"' : '';
    const lineHtml = hit ? highlightPayloadLine(line, terms) : esc(line);
    return `<span class="${classes.join(' ')}"${attrs}>${lineHtml || ' '}</span>`;
  }).join('');
}

function scrollPayloadToTarget() {
  const target = $('payloadBody').querySelector('[data-payload-primary="true"]') || $('payloadBody').querySelector('.payload-line-hit');
  if (!target) return;
  target.scrollIntoView({ block: 'center', inline: 'nearest' });
}

function ruleConstraintType(rule) {
  return String(rule?.constraint_type || '').trim();
}

function ruleConstraintLabel(rule) {
  const type = ruleConstraintType(rule);
  return CONSTRAINT_TYPE_LABELS[type] || type || '未声明约束类型';
}

function rulePresenceBucket(rule) {
  const type = ruleConstraintType(rule);
  if (type === 'required_presence') return 'required';
  if (type === 'recommended_presence') return 'recommended';
  if (type === 'optional_info') return 'optional';
  if (type === 'forbidden_presence') return 'forbidden';
  return '';
}

function ruleTreeSubjects(rule) {
  return ruleSubjects(rule);
}

function formatRuleMetaValue(value) {
  const subject = normalizeRuleSubject(value);
  return subject ? subjectDisplayLabel(subject) : String(value || '');
}

function rulePurposeText(rule) {
  const summary = rule?.purpose_zh || rule?.check_zh || '校验该规则声明的语义约束。';
  if (ruleConstraintType(rule) !== 'forbidden_presence' || !rule?.replacement) return summary;
  const replacement = formatRuleMetaValue(rule.replacement);
  if (!replacement || summary.includes(replacement)) return summary;
  return `${summary} 替代字段：${replacement}。`;
}

function ruleConstraintSummaryHtml(rule) {
  const rows = [];
  const type = ruleConstraintType(rule);
  if (type) rows.push(['约束', ruleConstraintLabel(rule)]);
  if (rule?.placement) rows.push(['位置', rule.placement]);
  if (Array.isArray(rule?.allowed_values) && rule.allowed_values.length) rows.push(['允许值', rule.allowed_values.join(', ')]);
  if (rule?.expected_value) rows.push(['固定值', rule.expected_value]);
  if (rule?.replacement) rows.push(['替换为', formatRuleMetaValue(rule.replacement)]);
  if (!rows.length) return '';
  return `<div class="constraint-summary">
    ${rows.map(([label, value]) => `<span><b>${esc(label)}</b>${esc(value)}</span>`).join('')}
  </div>`;
}

function ruleConstraintChipHtml(rule) {
  const type = ruleConstraintType(rule);
  if (!type) return '<span class="constraint-chip missing">未声明约束</span>';
  return `<span class="constraint-chip">${esc(ruleConstraintLabel(rule))}</span>`;
}

function ruleFieldChipsHtml(rule) {
  const fields = ruleDisplayFieldLabels(rule);
  if (!fields.length) return '<span class="field-chip muted">未声明字段</span>';
  return fields.map(field => `<span class="field-chip">${esc(field)}</span>`).join('');
}

function ruleDocHtml(rule, compact = false, signal = null) {
  const summary = rulePurposeText(rule);
  const check = rule?.check_zh || '根据 Rego 条件命中对应的 OTLP span / log / metric data point。';
  const fix = rule?.fix_zh || '按当前语义约定补齐、重命名或移动字段；不要仅为消除告警而降低规则标准。';
  const note = rule?.note_zh || '';
  const rows = [
    ['语义对象', ruleEntityLabel(rule)],
    ['规则分组', ruleCategoryLabel(rule)],
    ['Subject', ruleSubjects(rule).map(subjectDisplayLabel).join(', ') || '未声明 subject'],
    ['约束类型', ruleConstraintType(rule) ? ruleConstraintLabel(rule) : '未声明约束类型'],
    ['位置', rule?.placement || '未声明位置'],
    ['重点字段', ruleDisplayFieldLabels(rule).join(', ') || '未声明字段'],
    ['规则作用', summary],
    ['命中条件', check],
    ['修复建议', fix],
  ];
  const metricScopes = signal === 'metrics' ? ruleRedMetricScopes(rule) : [];
  if (metricScopes.length) rows.splice(3, 0, ['适用指标', metricScopes.map(redMetricSummaryLabel).join(', ')]);
  if (Array.isArray(rule?.allowed_values) && rule.allowed_values.length) rows.splice(5, 0, ['允许值', rule.allowed_values.join(', ')]);
  if (rule?.expected_value) rows.splice(5, 0, ['固定值', rule.expected_value]);
  if (rule?.replacement) rows.splice(5, 0, ['替换为', formatRuleMetaValue(rule.replacement)]);
  if (rule?.condition_zh || rule?.condition_key) rows.splice(3, 0, ['适用场景', rule.condition_zh || rule.condition_key]);
  if (rule?.span_kind) rows.splice(3, 0, ['Span kind', rule.span_kind]);
  if (rule?.spec_id) rows.push(['Spec ID', rule.spec_id]);
  if (rule?.when_zh) rows.push(['适用条件', rule.when_zh]);
  if (rule?.branch) rows.push(['规则分支', rule.branch]);
  if (note) rows.push(['补充说明', note]);
  return `<div class="rule-doc ${compact ? 'compact' : ''}">
    ${rows.map(([label, value]) => `<div class="rule-doc-row"><span>${esc(label)}</span><p>${esc(value)}</p></div>`).join('')}
  </div>`;
}

function findingInstanceId(record, finding, index) {
  return `F-live-${record.request_id || 'request'}-${index}-${finding.id || finding.rule || 'finding'}`.replace(/[^a-zA-Z0-9_.:-]+/g, '-');
}

function contextText(finding) {
  if (!finding?.context || (typeof finding.context === 'object' && Object.keys(finding.context).length === 0)) return '';
  return JSON.stringify(finding.context, null, 2);
}

function inferExpected(finding) {
  const id = String(finding?.id || finding?.rule || '').toLowerCase();
  const message = String(finding?.message || '').toLowerCase();
  if (id.includes('missing_required') || message.includes('missing')) return 'Required semantic attribute or structure is present.';
  if (id.includes('invalid') || message.includes('invalid')) return 'Semantic attribute value matches the allowed rule/semconv values.';
  if (levelClass(finding?.level) === 'improvement') return 'Recommended semantic metadata is provided for richer diagnostics.';
  return 'Telemetry satisfies the semantic rule contract.';
}

function inferActual(finding) {
  const ctx = finding?.context;
  if (ctx && typeof ctx === 'object') {
    const keys = ['attr', 'attribute', 'missing', 'value', 'actual', 'name', 'span_name', 'metric_name'];
    for (const key of keys) {
      if (ctx[key] !== undefined) return `${key}: ${JSON.stringify(ctx[key])}`;
    }
  }
  return finding?.message || 'See validator message/context.';
}

function normalizedPath(record, finding) {
  const group = signalGroup(record?.signal);
  const sample = finding?.sample || 'sample[0]';
  if (group === 'metrics') return `resourceMetrics[*].scopeMetrics[*].metrics[${JSON.stringify(sample)}]`;
  if (group === 'events') return `resourceLogs[*].scopeLogs[*].logRecords[${JSON.stringify(sample)}]`;
  return `resourceSpans[*].scopeSpans[*].spans[${JSON.stringify(sample)}]`;
}

function findRuleForFinding(finding, group = null) {
  const target = String(finding?.id || finding?.rule || '').replace(/^policy:/, '');
  const matches = allRules().filter(rule => rule.id === target || rule.rule === finding.rule || rule.rule === `policy:${target}`);
  return matches.find(rule => !group || rule.group === group) || matches[0];
}

function flattenFindings(records) {
  return records.flatMap(record => (record.findings || []).map((finding, index) => {
    const group = signalGroup(record.signal);
    const rule = findRuleForFinding(finding, group);
    const fieldBucket = inferFindingFieldBucket(finding, rule);
    return {
      id: findingInstanceId(record, finding, index),
      index,
      record,
      finding,
      rule,
      group,
      signal: signalKeyForGroup(group),
      path: normalizedPath(record, finding),
      expected: inferExpected(finding),
      actual: inferActual(finding),
      owner: inferOwner(finding),
      fieldBucket,
    };
  }));
}

function inferOwner(finding) {
  const id = String(finding?.id || '').toLowerCase();
  const msg = String(finding?.message || '').toLowerCase();
  if (id.includes('deprecated') || msg.includes('deprecated')) return 'rules';
  if (id.includes('missing') || msg.includes('missing') || id.includes('invalid') || msg.includes('invalid')) return 'instrumentation';
  if (levelClass(finding?.level) === 'semconv') return 'rules';
  return 'triage';
}

function findingText(item) {
  const ctx = contextText(item.finding);
  return [
    `Finding: ${item.id}`,
    `Level: ${item.finding.level || ''}`,
    `Rule: ${item.finding.rule || item.finding.id || ''}`,
    `Policy Group: ${item.rule?.rule_group || 'unknown'}`,
    `Signal: ${item.record.signal || ''} (${groupLabel(item.group)})`,
    `Request: ${item.record.request_id || ''}`,
    `Path: ${item.path}`,
    `Expected: ${item.expected}`,
    `Actual: ${item.actual}`,
    `Message: ${item.finding.message || ''}`,
    ctx ? `Context:\n${ctx}` : '',
  ].filter(Boolean).join('\n');
}

function liveReportText() {
  const lines = [
    'AIVISION LIVE REPORT',
    `Generated: ${new Date().toISOString()}`,
    `Requests: ${state.records.length}`,
    `Violations: ${state.stats.violations ?? 0}`,
    `Improvements: ${state.stats.improvements ?? 0}`,
    `Semconv Issues: ${state.stats.semconv_issues ?? 0}`,
    '',
    'Findings:',
  ];
  if (!state.findings.length) lines.push('No findings in recent diagnostics.');
  state.findings.forEach((item, idx) => lines.push(`\n${idx + 1}. ${findingText(item)}`));
  return lines.join('\n');
}

function aiPrompt(item, diagnosis = state.activeDiagnosis, includePayload = false) {
  const rule = item.rule;
  const base = [
    `TASK_TYPE: ${diagnosis === 'rule' ? 'RULE_FIX' : diagnosis === 'exception' ? 'SUPPRESSION_OR_EXCEPTION' : 'TRACE_INSTRUMENTATION_FIX'}`,
    `FINDING_ID: ${item.id}`,
    `REQUEST_ID: ${item.record.request_id || ''}`,
    `SIGNAL: ${item.record.signal || ''} (${groupLabel(item.group)})`,
    `RULE: ${item.finding.rule || item.finding.id || ''}`,
    `POLICY_GROUP: ${rule?.rule_group || 'unknown'}`,
    `POLICY_DIR: ${rule?.policy_dir || 'unknown'}`,
    `RULE_TITLE_ZH: ${ruleTitle(rule)}`,
    `RULE_ENTITY: ${ruleEntityLabel(rule)}`,
    `RULE_CATEGORY_ZH: ${ruleCategoryLabel(rule)}`,
    `RULE_FIELDS: ${ruleFields(rule).join(', ') || 'unknown'}`,
    `RULE_CONDITION_ZH: ${rule?.condition_zh || rule?.condition_key || 'unknown'}`,
    `RULE_SPAN_KIND: ${rule?.span_kind || 'unknown'}`,
    `RULE_SPEC_ID: ${rule?.spec_id || 'unknown'}`,
    `RULE_PURPOSE_ZH: ${rule?.purpose_zh || 'unknown'}`,
    `RULE_CHECK_ZH: ${rule?.check_zh || 'unknown'}`,
    `RULE_FIX_ZH: ${rule?.fix_zh || 'unknown'}`,
    `RULE_LOCATION: ${rule ? `${rule.file}:${rule.line}` : 'unknown'}`,
    `OTLP_PATH: ${item.path}`,
    `EXPECTED: ${item.expected}`,
    `ACTUAL: ${item.actual}`,
    `MESSAGE: ${item.finding.message || ''}`,
  ];

  if (diagnosis === 'rule') {
    base.push('', 'GOAL: Fix a suspected rule/semconv false positive while preserving the semantic contract.');
    base.push('CONSTRAINTS: Do not weaken required semantic conventions only to make validation green. Add or update pass/fail fixtures proving true positives still fail.');
  } else if (diagnosis === 'exception') {
    base.push('', 'GOAL: Propose a scoped exception/suppression for an expected telemetry deviation.');
    base.push('CONSTRAINTS: Include selector scope, owner, rationale, expiry, and report annotation. Do not globally disable the rule.');
  } else {
    base.push('', 'GOAL: Update agent telemetry instrumentation so this finding passes without weakening semantic rules.');
    base.push('CONSTRAINTS: Change telemetry generation/mapping, not policy rules, unless evidence proves the rule is wrong.');
  }

  base.push('ACCEPTANCE: Re-run the relevant replay/validation command; this finding is resolved and no new violations are introduced.');
  base.push('VALIDATION_HINTS: make replay-all && make validate, or the agent-specific replay/validate target if available.');

  const ctx = contextText(item.finding);
  if (ctx) base.push('', 'VALIDATOR_CONTEXT:', ctx);
  if (includePayload && item.record.payload) base.push('', 'FULL_PAYLOAD:', JSON.stringify(item.record.payload, null, 2));
  return base.join('\n');
}

function findingItems(signal = null) {
  return signal ? state.findings.filter(item => item.signal === signal) : state.findings;
}

function diagnosisForFinding(item) {
  if (item.owner === 'rules') return 'rule';
  return 'trace';
}

function findingRuleId(item) {
  return String(item.finding.rule || item.finding.id || item.rule?.id || 'unknown_rule');
}

function inferFindingFieldBucket(finding, rule = null) {
  const level = levelClass(finding?.level);
  const id = String([finding?.id, finding?.rule, rule?.id].filter(Boolean).join(' ')).toLowerCase();
  const message = String(finding?.message || '').toLowerCase();
  const category = String(rule?.category_zh || rule?.category || '').toLowerCase();
  const file = String(rule?.file || '').toLowerCase();

  if (
    level === 'improvement' ||
    level === 'semconv' ||
    category.includes('推荐') ||
    file.includes('improvements_recommended') ||
    id.includes('recommended')
  ) {
    return 'recommended';
  }

  if (
    category.includes('必须') ||
    category.includes('必需') ||
    file.includes('violations_required') ||
    id.includes('missing') ||
    id.includes('required') ||
    message.includes('missing required') ||
    message.includes('required attribute')
  ) {
    return 'required';
  }

  if (
    category.includes('禁止') ||
    category.includes('禁用') ||
    file.includes('violations_forbidden') ||
    id.includes('forbidden') ||
    id.includes('disallowed') ||
    message.includes('forbidden') ||
    message.includes('disallowed') ||
    message.includes('not allowed') ||
    message.includes('must not')
  ) {
    return 'forbidden';
  }

  return 'recommended';
}

function findingFieldBucket(value) {
  if (typeof value === 'string') return FINDING_FIELD_BUCKET_LABELS[value] ? value : 'recommended';
  return value?.fieldBucket || inferFindingFieldBucket(value?.finding, value?.rule);
}

function findingFieldBucketLabel(value) {
  return FINDING_FIELD_BUCKET_LABELS[findingFieldBucket(value)] || FINDING_FIELD_BUCKET_LABELS.recommended;
}

function findingFieldBucketRank(value) {
  const index = FINDING_FIELD_BUCKET_ORDER.indexOf(findingFieldBucket(value));
  return index === -1 ? FINDING_FIELD_BUCKET_ORDER.length : index;
}

function compareFindingItems(a, b) {
  return findingFieldBucketRank(a) - findingFieldBucketRank(b) ||
    findingLevelRank(a.finding.level) - findingLevelRank(b.finding.level) ||
    findingRuleId(a).localeCompare(findingRuleId(b));
}

function findingGroupKey(item) {
  return `${item.signal || 'all'}::${findingFieldBucket(item)}::${levelClass(item.finding.level)}::${item.owner || 'triage'}::${findingRuleId(item)}`;
}

function findingLevelRank(level) {
  const normalized = levelClass(level);
  if (normalized === 'violation') return 0;
  if (normalized === 'semconv') return 1;
  if (normalized === 'improvement') return 2;
  return 3;
}

function findingGroupEntries(items = findingItems()) {
  const groups = new Map();
  items.forEach(item => {
    const key = findingGroupKey(item);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        level: levelClass(item.finding.level),
        signal: item.signal,
        owner: item.owner || 'triage',
        fieldBucket: findingFieldBucket(item),
        rule_id: findingRuleId(item),
        rule: item.rule,
        items: [],
      });
    }
    groups.get(key).items.push(item);
  });
  return Array.from(groups.values()).sort((a, b) => findingFieldBucketRank(a) - findingFieldBucketRank(b) || findingLevelRank(a.level) - findingLevelRank(b.level) || b.items.length - a.items.length || a.rule_id.localeCompare(b.rule_id));
}

function findingPrompt(items, scopeLabel = '全部 findings') {
  const normalized = items.filter(Boolean);
  const counts = normalized.reduce((acc, item) => {
    acc[item.signal] = (acc[item.signal] || 0) + 1;
    return acc;
  }, {});
  const summary = Object.entries(counts).map(([signal, count]) => `${signal}:${count}`).join(', ') || 'none';
  return [
    'AIVISION FINDING FIX PROMPT',
    `SCOPE: ${scopeLabel}`,
    `Generated: ${new Date().toISOString()}`,
    `TOTAL_FINDINGS: ${normalized.length}`,
    `SIGNAL_COUNTS: ${summary}`,
    '',
    'GOAL: 处理这些 semantic findings，使 telemetry 满足当前 Rego/semconv 约束，并保留必要的兼容说明。',
    'CONSTRAINTS: 优先修上报字段、结构和映射；只有证据证明规则误报时才修改规则。不要为了变绿而降低必需语义标准。',
    'EXPECTED_OUTPUT: 给出代码/规则修改点、回归测试或 replay/validate 命令、以及每个 finding 如何被解决或被有范围地说明。',
    '',
    normalized.length ? normalized.map((item, index) => [
      `## ${index + 1}. ${item.id}`,
      `LEVEL: ${item.finding.level || 'unknown'}`,
      `FIELD_GROUP: ${findingFieldBucketLabel(item)}`,
      `CATEGORY: ${OWNER_LABELS[item.owner] || OWNER_LABELS.triage}`,
      `GROUP_RULE: ${findingRuleId(item)}`,
      aiPrompt(item, diagnosisForFinding(item)),
    ].join('\n')).join('\n\n---\n\n') : 'No findings in current recent diagnostics.',
  ].join('\n');
}

function treeOpen(store, key, defaultOpen = true) {
  if (!key) return defaultOpen;
  return store[key] === undefined ? defaultOpen : Boolean(store[key]);
}

function treeCountBadge(count, level = 'muted') {
  return `<span class="tree-count ${esc(levelClass(level))}">${esc(count)}</span>`;
}

function renderFindingTree(groups, browserKey, signal, activeKey) {
  const byBucket = new Map();
  groups.forEach(group => {
    const bucket = findingFieldBucket(group);
    if (!byBucket.has(bucket)) byBucket.set(bucket, []);
    byBucket.get(bucket).push(group);
  });

  const bucketHtml = FINDING_FIELD_BUCKET_ORDER
    .map(bucket => [bucket, byBucket.get(bucket) || []])
    .filter(([, bucketGroups]) => bucketGroups.length)
    .map(([bucket, bucketGroups]) => {
      const count = bucketGroups.reduce((sum, group) => sum + group.items.length, 0);
      const bucketKey = `finding:${browserKey}:field:${bucket}`;
      const leaves = bucketGroups
        .sort((a, b) => findingLevelRank(a.level) - findingLevelRank(b.level) || b.items.length - a.items.length || a.rule_id.localeCompare(b.rule_id))
        .map(group => `<button class="tree-leaf ${group.key === activeKey ? 'active' : ''} ${esc(group.level)}" type="button" data-select-finding-group="${esc(group.key)}" data-finding-signal="${esc(signal || '')}" title="${esc(group.rule_id)}">
          <span class="tree-leaf-main">
            <span class="tree-leaf-title">${compactLevelMark(group.level)}${esc(group.rule_id)}</span>
            <small>${esc(group.level)} · ${esc(OWNER_LABELS[group.owner] || OWNER_LABELS.triage)} · ${esc(group.rule?.policy_dir || 'unknown')} · ${esc(group.rule?.rule_group || 'unknown')}</small>
          </span>
          ${treeCountBadge(group.items.length, group.level)}
        </button>`).join('');
      return `<details class="tree-dir" data-tree-open-key="${esc(bucketKey)}" ${treeOpen(state.expandedFindingTrees, bucketKey) ? 'open' : ''}>
        <summary><span class="tree-dir-name">${esc(findingFieldBucketLabel(bucket))}</span><span class="tree-dir-actions">${treeCountBadge(count, bucketGroups[0]?.level)}</span></summary>
        <div class="tree-leaves">${leaves}</div>
      </details>`;
    }).join('');

  return `<aside class="split-tree finding-tree compact-tree">
    <div class="tree-head compact">
      <div><b>Findings</b><small>字段类型 / rule</small></div>
      ${treeCountBadge(groups.reduce((sum, group) => sum + group.items.length, 0))}
    </div>
    ${bucketHtml}
  </aside>`;
}
function renderFindingGroupDetail(group) {
  if (!group) return '<section class="split-detail"><div class="empty">请选择左侧 finding 规则节点。</div></section>';
  const fieldBucket = findingFieldBucketLabel(group);
  const category = OWNER_LABELS[group.owner] || OWNER_LABELS.triage;
  const signals = Array.from(new Set(group.items.map(item => SIGNALS[item.signal]?.label || item.signal))).join(' / ');
  const rulePurpose = group.rule?.purpose_zh || group.rule?.check_zh || '未匹配到规则说明';
  const rows = group.items.map(item => `<div class="finding-group-row">
    <div>
      <div class="finding-group-row-title">${esc(item.id)}</div>
      <div class="meta-line">${esc(findingFieldBucketLabel(item))} · ${esc(SIGNALS[item.signal]?.label || item.signal)} · ${esc(item.record.agent || item.record.source || 'unknown source')} · ${esc(item.path)}</div>
      <div class="finding-message">${esc(item.finding.message || '')}</div>
    </div>
    <div class="finding-group-row-actions">
      <button class="btn btn-secondary" type="button" data-copy-finding-single="${esc(item.id)}">复制单个 Prompt</button>
      <button class="btn btn-outline" type="button" data-open-finding="${esc(item.id)}">诊断</button>
      <button class="btn btn-ghost" type="button" data-open-payload="${esc(item.record.request_id)}" data-open-payload-finding="${esc(item.id)}">Payload</button>
    </div>
  </div>`).join('');

  return `<section class="split-detail finding-detail-panel">
    <header class="detail-panel-head ${esc(group.level)}">
      <div>
        ${badge(group.level, `${group.items.length} ${group.level}`)} ${badge('muted', fieldBucket)}
        <span class="rule-code">${esc(group.rule_id)}</span>
        <h3>${esc(fieldBucket)}</h3>
        <p>${esc(rulePurpose)}</p>
        <div class="meta-line">Signals: ${esc(signals)} · ${esc(category)} · policy group: ${esc(group.rule?.rule_group || 'unknown')} · dir: ${esc(group.rule?.policy_dir || 'unknown')}</div>
      </div>
      <button class="btn btn-primary" type="button" data-copy-finding-group="${esc(group.key)}">复制本组 Prompt</button>
    </header>
    ${group.rule ? `<section class="detail-section inline-rule-doc">
      <h3>命中规则说明</h3>
      ${ruleDocHtml(group.rule, false, group.signal)}
      <div class="meta-line">Source: ${esc(group.rule.file)}:${esc(group.rule.line)}</div>
    </section>` : ''}
    <section class="detail-section">
      <h3>命中列表</h3>
      <div class="finding-group-row-list">${rows}</div>
    </section>
  </section>`;
}

function renderFindingGroups(signal) {
  const items = findingItems(signal);
  const groups = findingGroupEntries(items);
  const containerId = signal ? `${signal}FindingsList` : 'findingsList';
  const browserKey = signal || 'all';
  const copyAll = document.querySelector(`[data-copy-signal-finding-prompts="${cssIdent(signal)}"]`);
  if (copyAll) copyAll.disabled = !items.length;
  if (!items.length) {
    setHtmlIfChanged(containerId, `<div class="empty">当前 recent diagnostics 没有 ${esc(SIGNALS[signal]?.label || 'this signal')} finding。</div>`);
    return;
  }

  let activeKey = state.activeFindingGroups[browserKey];
  if (!groups.some(group => group.key === activeKey)) activeKey = groups[0].key;
  state.activeFindingGroups[browserKey] = activeKey;
  const activeGroup = groups.find(group => group.key === activeKey) || groups[0];

  setHtmlIfChanged(containerId, `<div class="split-browser finding-browser">
    ${renderFindingTree(groups, browserKey, signal, activeKey)}
    ${renderFindingGroupDetail(activeGroup)}
  </div>`);
}
function fixPackText() {
  const groups = { instrumentation: [], rules: [], triage: [] };
  state.findings.forEach(item => (groups[item.owner] || groups.triage).push(item));
  return [
    'AIVISION AI FIX PACK',
    `Generated: ${new Date().toISOString()}`,
    '',
    '## Instrumentation fixes',
    ...(groups.instrumentation.length ? groups.instrumentation.map(item => aiPrompt(item, 'trace')) : ['No instrumentation-owned findings.']),
    '',
    '## Rule / semconv fixes',
    ...(groups.rules.length ? groups.rules.map(item => aiPrompt(item, 'rule')) : ['No rule-owned findings.']),
    '',
    '## Needs triage',
    ...(groups.triage.length ? groups.triage.map(item => findingText(item)) : ['No unclassified findings.']),
  ].join('\n\n---\n\n');
}

function guideText() {
  return [
    'AIVISION 接入/修复指南',
    '',
    '1. 作用',
    'AiVision 是本地 OTLP HTTP 语义校验网关：接收 agent 上报的 traces、events/logs、metrics，加载 Rego 规则和语义约定生成 diagnostics，并按转发策略写入本地 Jaeger / Victoria 后端。',
    '页面顶级按信号类型切换：Traces / Events / Metrics；每个信号内部再查看 Overview、Findings、Diagnostics、语义约束与 Payloads。',
    '语义约束来自 Rego 注释。Traces/Events 按通用 attributes、Resource attributes、span/event specific 组织；Metrics 按通用 attributes、Resource attributes、metric name、维度、维度约束、指标约束、数据点约束、禁止维度组织。点击字段后汇总该字段关联的所有 Rego 规则。',
    '',
    '2. 接入配置',
    '启动：make -C weaver serve',
    '新项目或新 agent：点击 新接入Bootstrap，选择接入等级和信号类型，复制 prompt 给 AI 完成第一版真实上报。',
    '默认 endpoint：http://127.0.0.1:4319',
    '标准路径：/v1/traces、/v1/logs、/v1/metrics',
    'OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4319',
    'OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf',
    'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4319/v1/traces',
    'OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4319/v1/logs',
    'OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://127.0.0.1:4319/v1/metrics',
    '',
    '3. 语义出现问题后的操作',
    '已有真实上报后：点击 语义修正Loop，选择信号类型、修复等级和确认策略，复制 prompt 给 AI 执行 validate / clear / re-report 循环。',
    '进入 Findings/Diagnostics，点击诊断；上报侧问题复制 Trace / instrumentation bug prompt 给 AI 修 agent；规则误报复制 Rule / semconv bug prompt 给 AI 修 Rego 和测试；需要完整证据时复制 Prompt + Payload。',
    '',
    '4. 忽略语义',
    '仅在确认是预期偏差时忽略。优先在 Finding modal 选择 Expected exception 生成有范围的例外说明；或在语义约束关闭单条规则。长期忽略必须记录 owner、原因、过期时间和恢复条件。',
  ].join('\n');
}

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
    audience: 'claudecode/codex-like 的新 agent 接入。',
    goal: '完成完整 agent 接入工程闭环，同时包含轻量级 endpoint、SDK、HTTP exporter 和语义首版上报要求。',
    stop: '停止条件：新 agent 具备本地 AiVision 上报路径，以及 transcript / replay / validate / debug / Makefile 的标准接入闭环；剩余字段问题交给“语义修正Loop”。',
    scope: [
      '包含轻量级 Bootstrap 的全部 endpoint、SDK、HTTP exporter 和语义字段要求。',
      '实现环境变量支持：{PREFIX}_SESSION_DIR / {PREFIX}_REPLAY_DIR / {PREFIX}_DEBUG。',
      '按数据源能力设计 transcript 策略；有持久化 transcript 时实现 replay CLI。',
      '输出 OTLP replay，并接入 validate / validate-one / debug replay 文件。',
      '补齐 build / replay / replay-all / validate / validate-one / report / report-shift / clean 标准 Makefile target。',
      'Golden / CAPABILITIES 仅在该 agent 接入目标需要时纳入，不能混进轻量级 Bootstrap。',
    ],
  },
};

function loopSelectedSignals() {
  return LOOP_SIGNAL_ORDER.filter(signal => $('loopSignal' + cap(signal))?.checked);
}

function bootstrapSelectedSignals() {
  return LOOP_SIGNAL_ORDER.filter(signal => $('bootstrapSignal' + cap(signal))?.checked);
}

function signalConfigsFor(signals) {
  return (signals || []).filter(signal => LOOP_SIGNAL_CONFIG[signal]).map(signal => ({
    key: signal,
    ...LOOP_SIGNAL_CONFIG[signal],
  }));
}

function loopPromptOptions() {
  return {
    signals: loopSelectedSignals(),
    level: $('loopFixLevelSelect')?.value || 'blocking',
    confirmation: $('loopConfirmSelect')?.value || 'risk',
    reportMode: $('loopReportModeSelect')?.value || 'manual',
    reportInput: $('loopReportInput')?.value || '',
  };
}

function bootstrapPromptOptions() {
  return {
    signals: bootstrapSelectedSignals(),
    level: $('bootstrapLevelSelect')?.value || 'lightweight',
    confirmation: $('bootstrapConfirmSelect')?.value || 'risk',
  };
}

function normalizedLoopReportText(value) {
  return String(value || '').trim();
}

function loopReportModeConfig(mode) {
  return LOOP_REPORT_MODES[mode] || LOOP_REPORT_MODES.manual;
}

function loopReportInstruction(options = loopPromptOptions()) {
  const config = loopReportModeConfig(options.reportMode);
  const text = config.label ? normalizedLoopReportText(options.reportInput) : '';
  const instruction = text || config.fallback;
  return {
    ...config,
    mode: LOOP_REPORT_MODES[options.reportMode] ? options.reportMode : 'manual',
    instruction: instruction || config.instruction,
    hasUserInput: Boolean(text),
  };
}

function loopReportInstructionLines(report) {
  if (report.mode === 'command') {
    return [
      `- 模式: ${report.title}`,
      `- ${report.label}: ${report.instruction}`,
      '- 执行前确认命令只影响本地可逆环境；执行后必须重新查询 AiVision recent diagnostics。',
    ];
  }
  return [
    `- 模式: ${report.title}`,
    `- ${report.instruction}`,
    report.mode === 'manual'
      ? '- 手动模式下不要自行伪造上报结果；必须等用户确认已经触发新一轮真实上报。'
      : '- 该模式依赖项目 Makefile 提供 report-check target；若 target 不存在或不产生真实上报，先修 Makefile/脚本再继续。',
  ];
}

function updateLoopReportInputHint() {
  const input = $('loopReportInput');
  const wrapper = $('loopReportInputOption');
  if (!input) return;
  const config = loopReportModeConfig($('loopReportModeSelect')?.value || 'manual');
  const showInput = Boolean(config.label);
  if (wrapper) wrapper.hidden = !showInput;
  input.disabled = !showInput;
  input.placeholder = config.placeholder || '';
}

function semanticObjectTypeLabel(signal) {
  if (signal === 'traces') return 'span 类型';
  if (signal === 'events') return 'event.name 类型';
  if (signal === 'metrics') return 'metric 类型';
  return '语义对象';
}

function semanticRequiredFieldLabels(groups, signal, area = null) {
  return uniqueValues((groups || []).flatMap(group => {
    if (area === 'resource' && group.kind !== 'resource') return [];
    if (area === 'attributes' && group.kind === 'resource') return [];
    return semanticFieldBuckets(group, signal)
      .filter(bucket => bucket.bucket === 'required')
      .flatMap(bucket => bucket.fields.map(field => field.label));
  })).sort((a, b) => a.localeCompare(b));
}

function compactPromptSentence(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function semanticFieldConditionLabels(field) {
  return uniqueValues(Array.from(field?.conditions?.values?.() || [])
    .map(entry => entry?.condition?.label)
    .filter(label => label && label !== SEMANTIC_CONDITION_LABELS.common));
}

function semanticPromptRuleRank(rule) {
  const bucket = rulePresenceBucket(rule);
  const type = ruleConstraintType(rule);
  if (bucket === 'required') return 10;
  if (type === 'fixed_value') return 20;
  if (type === 'enum') return 30;
  if (type === 'invariant') return 40;
  if (bucket === 'recommended') return 50;
  if (bucket === 'optional') return 60;
  if (bucket === 'forbidden') return 70;
  return 90;
}

function semanticPromptRuleForField(field) {
  const rules = uniqueRulesFromRows(field?.rows || []);
  return rules.sort((a, b) =>
    semanticPromptRuleRank(a) - semanticPromptRuleRank(b) ||
    ruleDisplayOrder(a) - ruleDisplayOrder(b) ||
    String(a?.id || '').localeCompare(String(b?.id || ''))
  )[0] || null;
}

function semanticFieldPromptMeaning(field) {
  const rule = semanticPromptRuleForField(field);
  if (!rule) return '读取 docs/semconv 与 weaver/policies/aivision 核对字段含义。';

  const summary = compactPromptSentence(rule.when_zh || rulePurposeText(rule) || rule.check_zh || rule.fix_zh);
  const details = [];
  const constraint = ruleConstraintLabel(rule);
  if (constraint && constraint !== '未声明约束类型') details.push(`约束: ${constraint}`);
  if (rule.expected_value) details.push(`固定值: ${rule.expected_value}`);
  if (Array.isArray(rule.allowed_values) && rule.allowed_values.length) {
    details.push(`允许值: ${rule.allowed_values.join(', ')}`);
  }
  if (rule.replacement) details.push(`替代: ${formatRuleMetaValue(rule.replacement)}`);
  const conditions = semanticFieldConditionLabels(field);
  if (conditions.length) details.push(`适用条件: ${conditions.join(', ')}`);

  const detailText = details.length ? `（${details.join('；')}）` : '';
  return `${summary || '读取 docs/semconv 与 weaver/policies/aivision 核对字段含义。'}${detailText}`;
}

function semanticFieldPromptLine(field) {
  return `- \`${field.label}\`: ${semanticFieldPromptMeaning(field)}`;
}

function uniqueSemanticPromptFields(fields) {
  return Array.from(new Map((fields || []).map(field => [field.label, field])).values())
    .sort((a, b) => a.label.localeCompare(b.label));
}

function semanticFieldsForPromptBuckets(group, signal, buckets) {
  const selected = new Set(buckets);
  return uniqueSemanticPromptFields(
    semanticFieldBuckets(group, signal)
      .filter(bucket => selected.has(bucket.bucket))
      .flatMap(bucket => bucket.fields)
  );
}

function semanticRequiredPromptLinesForGroups(groups, signal) {
  const fields = uniqueSemanticPromptFields((groups || []).flatMap(group =>
    semanticFieldsForPromptBuckets(group, signal, ['required'])
  ));
  return fields.length
    ? fields.map(semanticFieldPromptLine)
    : ['- 当前规则未声明必须字段；读取语义规范确认。'];
}

function semanticObjectTypeLines(signal, objectGroups) {
  const objectTitle = semanticObjectTypeLabel(signal);
  const labels = uniqueValues((objectGroups || []).map(group => group.label));
  return labels.length
    ? [`${objectTitle}:`, ...labels.map(label => `- ${label}`)]
    : [`${objectTitle}:`, '- 当前规则未声明具体对象类型；读取语义规范确认。'];
}

function semanticObjectRequirementLines(group, signal) {
  const fields = semanticFieldsForPromptBuckets(group, signal, ['required']);
  return [
    `### ${group.label}`,
    ...(fields.length ? fields.map(semanticFieldPromptLine) : ['- 当前规则未声明必须字段；读取语义规范确认。']),
  ];
}

function semanticMetricRequirementLines(group) {
  if (isMetricNameGroup(group)) {
    const info = RED_METRIC_INFO[group.metric_name] || {};
    const lines = [
      `### ${group.label}`,
      `- 类型: ${info.family || 'Metric'} / ${info.red || 'RED'} / ${info.shape || 'metric'} / ${info.unit || 'unit 未声明'}`,
    ];
    const laneGroups = semanticMetricLaneGroups(group).filter(lane => lane.fields.length);
    if (!laneGroups.length) {
      lines.push('- 当前规则未声明字段或约束；读取语义规范确认。');
      return lines;
    }
    laneGroups.forEach(lane => {
      lines.push(`${lane.label}:`);
      uniqueSemanticPromptFields(lane.fields).forEach(field => lines.push(semanticFieldPromptLine(field)));
    });
    return lines;
  }
  return semanticObjectRequirementLines(group, 'metrics');
}

function semanticRequirementLinesFromGroups(signal, groups) {
  const config = LOOP_SIGNAL_CONFIG[signal];
  const resourceGroups = (groups || []).filter(group => group.kind === 'resource');
  const objectGroups = (groups || []).filter(group => group.kind !== 'resource' && group.kind !== 'unclassified');
  const lines = [
    `## ${config.label}`,
    ...semanticObjectTypeLines(signal, objectGroups),
    '',
    'resourceAttributes 必须字段:',
    ...semanticRequiredPromptLinesForGroups(resourceGroups, signal),
  ];

  if (!objectGroups.length) return lines;

  lines.push('', signal === 'metrics' ? '按具体 metric 分类:' : '按具体语义对象分类:');
  objectGroups.forEach((group, index) => {
    if (index) lines.push('');
    lines.push(...(signal === 'metrics'
      ? semanticMetricRequirementLines(group)
      : semanticObjectRequirementLines(group, signal)));
  });
  return lines;
}

function semanticRequirementLines(signal) {
  const config = LOOP_SIGNAL_CONFIG[signal];
  const rules = rulesForSignal(signal);
  if (!rules.length) {
    const objectTitle = semanticObjectTypeLabel(signal);
    return [
      `## ${config.label}`,
      `${objectTitle}:`,
      '- 当前页面尚未加载该 signal 的 Rego 语义规则；先刷新规则或读取 docs/semconv 与 weaver/policies/aivision 后再实现。',
      '',
      'resourceAttributes 必须字段:',
      '- 当前页面尚未加载；读取语义规范确认。',
      '',
      '按具体语义对象分类:',
      '- 当前页面尚未加载；读取语义规范确认。',
    ];
  }

  const groups = buildSemanticRuleMatrix(rules, signal);
  return semanticRequirementLinesFromGroups(signal, groups);
}

function bootstrapSemanticRequirementLines(signals) {
  const selectedSignals = (signals || []).filter(signal => LOOP_SIGNAL_CONFIG[signal]);
  if (!selectedSignals.length) {
    return ['当前没有选择 signal。请先选择 traces / events / metrics 后再开始新接入。'];
  }
  return selectedSignals.flatMap((signal, index) => [
    ...(index ? [''] : []),
    ...semanticRequirementLines(signal),
  ]);
}

function bootstrapEndpointLines(configs) {
  return configs.length
    ? configs.map(signal => `- ${signal.label}: http://127.0.0.1:4319${signal.endpoint}`)
    : ['- 未选择 signal；不要开始接入。'];
}

function aiBootstrapPromptText(options = bootstrapPromptOptions()) {
  const level = BOOTSTRAP_LEVELS[options.level] || BOOTSTRAP_LEVELS.lightweight;
  const confirmation = LOOP_CONFIRMATION[options.confirmation] || LOOP_CONFIRMATION.risk;
  const selectedSignalConfigs = signalConfigsFor(options.signals);
  const signalScope = selectedSignalConfigs.length
    ? selectedSignalConfigs.map(signal => signal.label).join(' / ')
    : '未选择任何信号类型';
  return [
    '你是 AiVision 新接入 Bootstrap 代理。目标是把一个新项目或新 agent 接到本地 AiVision OTLP 语义验证网关，先产出第一版真实 telemetry，再交给“语义修正Loop”做字段修正循环。',
    '',
    `接入等级: ${level.title}`,
    `适用对象: ${level.audience}`,
    `接入信号类型: ${signalScope}`,
    '',
    '用户确认策略:',
    confirmation,
    '',
    '本地 endpoint:',
    '- Base: http://127.0.0.1:4319',
    ...bootstrapEndpointLines(selectedSignalConfigs),
    '',
    'SDK / 协议要求:',
    '- 使用目标语言/runtime 的标准 OpenTelemetry SDK。',
    '- 使用 OTLP HTTP exporter；不要引入私有协议或非标准上报格式。',
    '- endpoint 固定指向本地 AiVision 4319 端口；按 signal 使用 /v1/traces、/v1/logs、/v1/metrics。',
    '',
    '当前选中语义要求（来自页面已加载 Rego 语义规则；实现前仍需读取 docs/semconv 与 weaver/policies/aivision 核对）:',
    ...bootstrapSemanticRequirementLines(options.signals),
    '',
    '执行目标:',
    level.goal,
    '',
    '硬约束:',
    ...level.scope.map(item => `- ${item}`),
    '- 不要为了变绿而降低语义标准。',
    '- 不要伪造 telemetry；字段必须来自真实 runtime、session、hook payload 或合理的目标项目上下文。',
    '',
    '执行流程:',
    '1. 读取当前项目的运行入口、生命周期事件、可用上下文和依赖管理方式。',
    '2. 读取 AiVision 语义规范：docs/semconv、weaver/policies/aivision，以及页面列出的选中 signal 必须字段和约束。',
    '3. 配置标准 OpenTelemetry SDK 和 OTLP HTTP exporter 到本地 127.0.0.1:4319。',
    '4. 为选中 signal 实现第一版 producer instrumentation，确保 resourceAttributes、语义对象类型和必须字段来自真实上下文。',
    '5. 触发一次真实运行并上报到 AiVision；如页面出现字段问题，记录为后续“语义修正Loop”的输入，不要在 bootstrap 阶段扩大范围。',
    `6. ${level.stop}`,
    '',
    '最终输出:',
    '- 列出修改文件、引入依赖、endpoint 配置和触发真实上报的命令。',
    '- 按 traces / events / metrics 分组说明已经覆盖的语义对象和必须字段。',
    '- 明确剩余问题应交给“语义修正Loop”继续 validate / clear / re-report。',
  ].join('\n');
}

function aiFixLoopPromptText(options = loopPromptOptions()) {
  const level = LOOP_FIX_LEVELS[options.level] || LOOP_FIX_LEVELS.blocking;
  const confirmation = LOOP_CONFIRMATION[options.confirmation] || LOOP_CONFIRMATION.risk;
  const report = loopReportInstruction(options);
  const selectedSignals = (options.signals || []).filter(signal => LOOP_SIGNAL_CONFIG[signal]);
  const selectedSignalConfigs = selectedSignals.map(signal => LOOP_SIGNAL_CONFIG[signal]);
  const signalScope = selectedSignalConfigs.length
    ? selectedSignalConfigs.map(signal => signal.label).join(' / ')
    : '未选择任何信号类型';
  const endpointLines = selectedSignalConfigs.length
    ? selectedSignalConfigs.map(signal => `- ${signal.label}: http://127.0.0.1:4319${signal.endpoint}`)
    : ['- 未选择 signal；请先回到 Loop modal 选择 traces / events / metrics。'];
  const signalQueryLines = selectedSignalConfigs.length
    ? selectedSignalConfigs.map(signal => `- ${signal.label}: curl 'http://127.0.0.1:4319/api/violations?${signal.query}&levels=all&limit=100'`)
    : ['- 未选择 signal；不要开始修复。'];
  return [
    '你是 AiVision telemetry instrumentation 修复代理。请配置本地 OTLP endpoint，上报当前 agent 的 traces/events/metrics，读取校验 API，定位字段不规范处，修复后 clear，再次上报并循环，直到没有必须修复的问题。',
    '',
    `修复信号类型: ${signalScope}`,
    selectedSignalConfigs.length ? '只修复所选信号类型；不要处理未选择 signal 的 findings，除非用户确认扩大范围。' : '当前没有选择 signal。请先让用户选择 traces / events / metrics 后再继续。',
    '',
    `修复等级: ${level.title}`,
    level.target,
    '',
    '用户确认策略:',
    confirmation,
    '',
    '重新触发上报方式:',
    ...loopReportInstructionLines(report),
    '',
    '本地 endpoint:',
    '- Base: http://127.0.0.1:4319',
    ...endpointLines,
    '',
    '信号类型必须分开看，不要把不同 signal 的 finding 混在一起修:',
    ...signalQueryLines,
    '',
    '等级必须按当前选项过滤，同时保留全量分层视图:',
    `- 当前修复范围: curl 'http://127.0.0.1:4319/api/violations?${level.query}&limit=100'`,
    "- Violation: curl 'http://127.0.0.1:4319/api/violations?level=violation&limit=100'",
    "- Semconv: curl 'http://127.0.0.1:4319/api/violations?level=semconv&limit=100'",
    "- Improvement: curl 'http://127.0.0.1:4319/api/violations?level=improvement&limit=100'",
    "- Information: curl 'http://127.0.0.1:4319/api/violations?level=information&limit=100'",
    '',
    '循环流程:',
    '1. 配置 agent 使用上述 OTLP endpoint，并按“重新触发上报方式”产生一次真实上报。',
    '2. 先按 signal 查询 traces/events/metrics，再按 level 查询 violation/semconv/improvement/information。',
    '3. 对 instrumentation-owned finding 修 producer 代码；对 rule/semconv-owned finding 修 Rego 或 semconv 测试；不确定归因时保留证据并说明。',
    '4. 如需完整 payload，再用 include_payload=true，但注意 payload 可能包含敏感数据，避免外传。',
    "5. 修复后清空当前 recent diagnostics: curl -X POST 'http://127.0.0.1:4319/api/clear'。",
    `6. 按同一“重新触发上报方式”再触发一轮真实上报，然后重复查询。${level.done}`,
    '',
    '约束:',
    '- 不要为了变绿而降低语义标准。',
    '- 不要硬编码假数据；必须从 agent session / hook payload / transcript 中提取真实字段。',
    '- 不要跳过 validate / clear / re-report 循环；无法自动触发上报时，必须停下来要求用户按上报方式执行。',
    '- 输出最终报告时按 signal 和 level 分组说明修复内容、剩余问题、验证命令和结果。',
  ].join('\n');
}

function renderLoopPrompt() {
  updateLoopReportInputHint();
  const preview = $('loopPromptPreview');
  if (preview) preview.textContent = aiFixLoopPromptText();
}

function renderBootstrapPrompt() {
  const preview = $('bootstrapPromptPreview');
  if (preview) preview.textContent = aiBootstrapPromptText();
}

function apiDocsText() {
  return [
    'AIVISION API 文档',
    '',
    '1. 获取 violations / findings',
    'GET http://127.0.0.1:4319/api/violations',
    '兼容历史拼写：GET /api/voilations；新脚本应使用 /api/violations。',
    '',
    'Query:',
    '- limit: 返回条数，默认 10，最大 100。',
    '- signal: traces / events / metrics；兼容 trace/span/event/log/metric 等别名。',
    '- level: violation / improvement / information / semconv；支持逗号分隔。',
    '- levels: level 的复数别名；可使用 all 返回所有等级。',
    '- include_payload: 默认 false；true 时返回原始 payload。',
    '',
    'Examples:',
    "curl 'http://127.0.0.1:4319/api/violations?limit=10'",
    "curl 'http://127.0.0.1:4319/api/violations?signal=events&level=violation,improvement&limit=20'",
    "curl 'http://127.0.0.1:4319/api/violations?levels=all&include_payload=true&limit=5'",
    '',
    '2. 清理本地诊断数据',
    'POST http://127.0.0.1:4319/api/clear',
    'DELETE http://127.0.0.1:4319/api/clear',
    '只清空当前进程内 recent diagnostics / findings / payload 记录；不会删除 Rego、semconv，也不会清理 Jaeger/Victoria 后端数据。',
    '',
    'Examples:',
    "curl -X POST 'http://127.0.0.1:4319/api/clear'",
    "curl -X DELETE 'http://127.0.0.1:4319/api/clear'",
    '',
    'Response:',
    '{\"cleared_records\":12,\"remaining_records\":0,\"scope\":\"recent_diagnostics\"}',
  ].join('\n');
}

async function writeClipboardText(text) {
  try {
    if (typeof navigator.clipboard?.writeText === 'function') {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* Fall back when clipboard access is denied. */ }

  const focused = document.activeElement;
  const selection = document.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
  const area = document.createElement('textarea');
  area.value = ' ';
  area.readOnly = true;
  area.tabIndex = -1;
  area.setAttribute('aria-hidden', 'true');
  area.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none';
  let wroteText = false;
  area.addEventListener('copy', event => {
    event.preventDefault();
    if (!event.clipboardData) return;
    event.clipboardData.setData('text/plain', text);
    wroteText = true;
  });
  (focused?.closest('dialog, [role="dialog"]') || document.querySelector('dialog[open]') || document.body).appendChild(area);
  try {
    area.focus({ preventScroll: true });
    if (document.activeElement !== area) return false;
    area.select();
    return typeof document.execCommand === 'function' && document.execCommand('copy') && wroteText;
  } catch { return false; }
  finally {
    area.remove();
    if (focused?.isConnected) focused.focus({ preventScroll: true });
    if (ranges.length) {
      selection.removeAllRanges();
      ranges.forEach(range => selection.addRange(range));
    }
  }
}

async function copyText(text) {
  const copied = await writeClipboardText(text);
  toast(copied ? '已复制' : '复制失败，请检查浏览器的剪贴板权限后重试。');
}

function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.classList.add('open');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('open'), 1800);
}

function closeConfirm(result = false) {
  const backdrop = $('confirmBackdrop');
  if (!backdrop?.classList.contains('open')) return;
  backdrop.classList.remove('open');
  if (pendingConfirmResolve) {
    const resolve = pendingConfirmResolve;
    pendingConfirmResolve = null;
    resolve(result);
  }
}

function confirmAction({ title, subtitle = '请确认后继续。', message, confirmLabel = '确认', danger = true }) {
  closeConfirm(false);
  $('confirmTitle').textContent = title;
  $('confirmSubtitle').textContent = subtitle;
  $('confirmMessage').textContent = Array.isArray(message) ? message.join('\n') : String(message || '');
  const ok = $('confirmOkBtn');
  ok.textContent = confirmLabel;
  ok.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
  $('confirmBackdrop').classList.add('open');
  $('confirmCancelBtn').focus();
  return new Promise(resolve => {
    pendingConfirmResolve = resolve;
  });
}

function setPrimaryTab(signal) {
  document.querySelectorAll('[data-primary-tab]').forEach(tab => tab.classList.toggle('active', tab.dataset.primaryTab === signal));
  document.querySelectorAll('[data-signal-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.signalPanel === signal));
  scheduleBackendFrameActivation();
}

function setSecondaryTab(signal, tabName) {
  const panel = $(`signal-${signal}`);
  if (!panel) return;
  panel.querySelectorAll('[data-secondary-tab]').forEach(tab => tab.classList.toggle('active', tab.dataset.secondaryTab === tabName));
  panel.querySelectorAll('.secondary-panel').forEach(section => section.classList.toggle('active', section.id === `${signal}-${tabName}`));
  scheduleBackendFrameActivation();
}

function jumpToSignal(signal, secondary = 'overview') {
  setPrimaryTab(signal);
  setSecondaryTab(signal, secondary);
}

function renderStats() {
  $('total').textContent = state.stats.total_requests ?? 0;
  $('violations').textContent = state.stats.violations ?? 0;
  $('improvements').textContent = state.stats.improvements ?? 0;
  $('semconv').textContent = state.stats.semconv_issues ?? 0;
  $('forwarded').textContent = state.stats.forwarded ?? 0;
  $('skipped').textContent = state.stats.skipped ?? 0;
  $('updated').textContent = new Date().toLocaleString();
}

function normalizedForwardPolicy(value) {
  return FORWARD_POLICY_HINTS[value] ? value : 'always';
}

function renderForwardPolicy() {
  const policy = normalizedForwardPolicy(state.settings.forward_policy || state.stats.forward_policy);
  const select = $('forwardPolicySelect');
  if (select && document.activeElement !== select && select.value !== policy) select.value = policy;
  const hint = $('forwardPolicyHint');
  if (hint) hint.textContent = FORWARD_POLICY_HINTS[policy];
}

function ruleStats(rules) {
  const total = rules.length;
  const enabled = rules.filter(rule => rule.enabled).length;
  return { total, enabled, disabled: total - enabled };
}

function renderRulesSummary() {
  const parts = Object.entries(SIGNALS).map(([, config]) => {
    const stats = ruleStats(rulesForSignal(signalKeyForGroup(config.group)));
    return `${groupLabel(config.group)} ${stats.enabled}/${stats.total}`;
  });
  const all = allRules();
  const disabled = ruleStats(all).disabled;
  const policyGroups = policyGroupEntries().map(([name, policyGroup]) => {
    const enabled = policyGroup.enabled !== false;
    return `<span class="policy-chip ${enabled ? '' : 'disabled'}">
      ${esc(policyGroupLabel(name))} ${esc(policyGroup.enabled_rules ?? 0)}/${esc(policyGroup.total ?? 0)} ${enabled ? 'enabled' : 'disabled'}
    </span>`;
  }).join('');
  setHtmlIfChanged('rulesSummary', `${parts.map(part => `<span>${esc(part)}</span>`).join('')}<span class="badge badge-disabled">disabled ${esc(disabled)}</span><span class="policy-divider">Policy groups</span>${policyGroups}`);
}

function renderFindingCard(item) {
  const level = levelClass(item.finding.level);
  return `<article class="finding-card ${esc(level)}">
    <header>
      <div>
        ${badge(item.finding.level)} ${badge('muted', findingFieldBucketLabel(item))} <span class="rule-code">${esc(item.finding.rule || item.finding.id || '')}</span>
        <div class="finding-title">${esc(item.id)}</div>
      </div>
      <button class="btn btn-secondary" type="button" data-open-finding="${esc(item.id)}">诊断</button>
    </header>
    <div class="finding-message">${esc(item.finding.message || '')}</div>
    <div class="meta-line">${esc(findingFieldBucketLabel(item))} · ${esc(groupLabel(item.group))} · policy group: ${esc(item.rule?.rule_group || 'unknown')} · dir: ${esc(item.rule?.policy_dir || 'unknown')} · ${esc(item.record.agent || item.record.source || 'unknown source')} · ${esc(item.path)}</div>
    <div class="toolbar" style="justify-content:flex-start;margin-top:10px">
      <button class="btn btn-outline" type="button" data-copy-trace-prompt="${esc(item.id)}">复制 trace prompt</button>
      <button class="btn btn-outline" type="button" data-copy-rule-prompt="${esc(item.id)}">复制 rule prompt</button>
      <button class="btn btn-ghost" type="button" data-open-payload="${esc(item.record.request_id)}" data-open-payload-finding="${esc(item.id)}">Payload</button>
    </div>
  </article>`;
}

function renderSignalOverview(signal) {
  const config = SIGNALS[signal];
  const records = recordsForSignal(signal);
  const findings = findingsForSignal(signal);
  const rules = rulesForSignal(signal);
  const violations = findings.filter(item => levelClass(item.finding.level) === 'violation').length;
  const improvements = findings.filter(item => levelClass(item.finding.level) === 'improvement').length;
  const top = findings.slice().sort(compareFindingItems).slice(0, 4);
  const root = $(`${signal}Overview`);
  if (root.dataset.initialized !== 'true') {
    root.innerHTML = `<div class="bucket-grid" id="${esc(signal)}Buckets"></div>
      <article class="card">
        <div class="card-head"><div><h2>${esc(config.label)} Backends</h2></div></div>
        <div class="backend-grid" id="${esc(signal)}BackendGrid"></div>
      </article>
      <article class="card">
        <div class="card-head"><div><h2>${esc(config.label)} Top Findings</h2><p>当前信号内最高优先级问题。</p></div><button class="btn btn-outline" type="button" data-secondary-jump-signal="${esc(signal)}" data-secondary-jump-tab="findings">打开 Findings</button></div>
        <div class="finding-grid" id="${esc(signal)}TopFindings"></div>
      </article>`;
    root.dataset.initialized = 'true';
  }
  setHtmlIfChanged(`${signal}Buckets`, `
    <div class="bucket"><span class="section-kicker">Requests</span><strong>${esc(records.length)}</strong><p>${esc(config.singular)} recent diagnostics</p></div>
    <div class="bucket"><span class="section-kicker">Findings</span><strong>${esc(findings.length)}</strong><p>${esc(violations)} violations · ${esc(improvements)} improvements</p></div>
    <div class="bucket"><span class="section-kicker">Rego Rules</span><strong>${esc(rules.length)}</strong><p>${esc(ruleStats(rules).enabled)}/${esc(rules.length)} enabled</p></div>`);
  renderBackendGrid($(`${signal}BackendGrid`), config.backends);
  scheduleBackendFrameActivation();
  setHtmlIfChanged(`${signal}TopFindings`, top.length ? top.map(renderFindingCard).join('') : '<div class="empty">No findings for this signal.</div>');
}

function renderSignalFindings(signal) {
  renderFindingGroups(signal);
}

function renderDiagnosticRows(signal) {
  const records = recordsForSignal(signal);
  const html = records.length ? records.map(record => {
    const findingSummary = `V:${record.violations ?? 0} I:${record.improvements ?? 0} S:${record.semconv_issues ?? 0}`;
    return `<tr>
      <td>${esc(record.timestamp)}</td>
      <td>${statusBadge(record.status)}</td>
      <td>${esc(record.method)} ${esc(record.path)}<br><span class="meta-line">HTTP ${esc(record.http_status)} · ${esc(record.content_type)}</span></td>
      <td>${esc(record.agent || record.source || '')}</td>
      <td>${esc(record.sample_count)}<br><span class="meta-line">rejected ${esc(record.rejected_count)}</span></td>
      <td>${esc(findingSummary)}</td>
      <td>${forwardPills(record)}</td>
      <td><div class="toolbar" style="justify-content:flex-start">
        <button class="btn btn-secondary" type="button" data-request-findings="${esc(record.request_id)}">Findings</button>
        <button class="btn btn-outline" type="button" data-open-payload="${esc(record.request_id)}">Payload</button>
      </div></td>
    </tr>`;
  }).join('') : '<tr><td colspan="8">No diagnostics for this signal.</td></tr>';
  setHtmlIfChanged(`${signal}DiagnosticRows`, html);
}

function normalizedSemanticKey(value, defaultKey = 'unclassified') {
  const text = String(value || '').trim();
  if (!text) return defaultKey;
  return text.replace(/\s+/g, '_').replace(/[^a-zA-Z0-9_.:-]+/g, '_').toLowerCase();
}

function semanticBucketRank(bucket) {
  const index = FINDING_FIELD_BUCKET_ORDER.indexOf(bucket);
  return index === -1 ? FINDING_FIELD_BUCKET_ORDER.length : index;
}

function semanticFieldBucketOrder(signal = null) {
  return signal === 'metrics' ? METRIC_FIELD_BUCKET_ORDER : FINDING_FIELD_BUCKET_ORDER;
}

function constraintTypeRank(rule) {
  const index = CONSTRAINT_TYPE_ORDER.indexOf(ruleConstraintType(rule));
  return index === -1 ? CONSTRAINT_TYPE_ORDER.length : index;
}

function normalizedSpanKind(value) {
  const normalized = String(value || '').trim().toUpperCase();
  if (!normalized) return '';
  if (normalized === 'TURN') return 'CHAT';
  if (normalized === 'GENERATION') return 'LLM';
  return normalized.replace(/[^A-Z0-9_:-]+/g, '_');
}

function inferSpanKindFromRule(rule) {
  const explicit = normalizedSpanKind(rule?.span_kind);
  if (explicit) return explicit;
  const entity = String(rule?.entity || '').toLowerCase();
  if (entity.startsWith('span:')) {
    const value = entity.slice('span:'.length);
    if (value === 'llm') return 'LLM';
    if (value === 'tool') return 'TOOL';
    if (value === 'turn') return 'CHAT';
    if (value === 'compaction') return 'COMPACTION';
    if (value === 'common' || value === 'base') return 'COMMON';
    return normalizedSpanKind(value);
  }
  return '';
}

function semanticEntityForRule(rule, signal) {
  const entity = String(rule?.entity || '').trim();
  const lower = entity.toLowerCase();

  if (signal === 'events') {
    if (lower.startsWith('resource:')) {
      return { kind: 'resource', key: 'resource', label: 'resource attributes', subtitle: 'events/logs resource attributes', sort: 20 };
    }
    if (lower.startsWith('event:')) {
      const key = entity.slice('event:'.length).trim() || 'event';
      if (key === 'base' || key === 'common') {
        return {
          kind: 'event',
          key: 'common',
          label: '通用 attributes',
          subtitle: 'events/logs common attributes',
          sort: 10,
        };
      }
      return {
        kind: 'event',
        key: normalizedSemanticKey(key),
        label: `event.name: ${key}`,
        subtitle: 'event specific attributes',
        sort: key === 'api_request' ? 40 : 60,
      };
    }
  }

  if (signal === 'traces') {
    if (lower.startsWith('resource:')) {
      return { kind: 'resource', key: 'resource', label: 'resource attributes', subtitle: 'traces resource attributes', sort: 20 };
    }
    const spanKind = inferSpanKindFromRule(rule);
    if (spanKind) {
      if (spanKind === 'COMMON') {
        return {
          kind: 'span',
          key: 'COMMON',
          label: '通用 attributes',
          subtitle: 'span common attributes',
          sort: 10,
        };
      }
      return {
        kind: 'span',
        key: spanKind,
        label: `span.kind: ${spanKind}`,
        subtitle: 'span specific attributes',
        sort: { CHAT: 40, LLM: 50, TOOL: 60, COMPACTION: 70, RESOURCE: 20 }[spanKind] || 80,
      };
    }
  }

  if (signal === 'metrics') {
    if (lower.startsWith('resource:')) {
      return { kind: 'resource', key: 'resource', label: 'Resource attributes', subtitle: 'metrics resource attributes', sort: 20 };
    }
    if (lower.startsWith('metric:')) {
      const key = entity.slice('metric:'.length).trim() || 'metric';
      if (key === 'base' || key === 'common') {
        return { kind: 'metric_common', key: 'common', label: '通用 attributes', subtitle: 'metric common data point attributes', sort: 10 };
      }
      if (key === 'golden') {
        return {
          kind: 'metric_family',
          key: 'golden',
          label: 'RED golden metrics',
          subtitle: '六个 RED 指标通用约束：type / unit / value / error boundary',
          sort: 30,
        };
      }
      if (key === 'api_request' || key === 'llm') {
        return {
          kind: 'metric_family',
          key: 'api_request',
          label: 'API request RED metrics',
          subtitle: 'agent.api_request.count / error.count / duration',
          sort: 40,
        };
      }
      if (key === 'tool') {
        return {
          kind: 'metric_family',
          key: 'tool',
          label: 'Tool RED metrics',
          subtitle: 'agent.tool_call.count / error.count / duration',
          sort: 50,
        };
      }
      return { kind: 'metric_family', key: normalizedSemanticKey(key), label: `metric: ${key}`, subtitle: 'metric specific data point attributes', sort: 40 };
    }
  }

  return {
    kind: 'unclassified',
    key: 'unclassified',
    label: signal === 'metrics' ? 'Metric / 未归类 / 需要补注释' : '未归类 / 需要补注释',
    subtitle: 'missing @weaver.rule.entity or @weaver.rule.span.kind',
    sort: 999,
  };
}

function semanticEntityId(entity) {
  return `${entity?.kind || 'unknown'}:${entity?.key || 'unknown'}`;
}

function semanticEntityArea(entity) {
  return entity?.kind === 'resource' ? 'resource' : 'attributes';
}

function semanticDirectoryBucket(rule, signal = null) {
  if (rule?.semantic_bucket) return rule.semantic_bucket;
  const presence = rulePresenceBucket(rule);
  if (presence) return presence;
  if (signal === 'metrics' && DETAIL_ONLY_CONSTRAINT_TYPES.has(ruleConstraintType(rule))) return 'constraint';
  return '';
}

function semanticBucketStrength(bucket) {
  if (bucket === 'required') return 1;
  if (bucket === 'recommended') return 2;
  if (bucket === 'optional') return 3;
  if (bucket === 'forbidden') return 4;
  return 9;
}

function targetKeyFor(entity, bucket) {
  return `${semanticEntityId(entity)}:${bucket || 'details'}`;
}

function buildPositiveSubjectOwners(rules, signal) {
  const owners = new Map();
  rules.forEach(rule => {
    const bucket = rulePresenceBucket(rule);
    if (bucket !== 'required' && bucket !== 'recommended' && bucket !== 'optional') return;
    const entity = semanticEntityForRule(rule, signal);
    ruleTreeSubjects(rule).forEach(subject => {
      const key = subjectKey(subject);
      if (!key) return;
      if (!owners.has(key)) owners.set(key, []);
      owners.get(key).push({ entity, bucket });
    });
  });
  owners.forEach((items, key) => {
    const deduped = Array.from(new Map(items.map(item => [targetKeyFor(item.entity, item.bucket), item])).values())
      .sort((a, b) =>
        semanticBucketStrength(a.bucket) - semanticBucketStrength(b.bucket) ||
        (a.entity.sort || 0) - (b.entity.sort || 0) ||
        a.entity.label.localeCompare(b.entity.label)
      );
    owners.set(key, deduped);
  });
  return owners;
}

function targetEntriesForRuleSubject(rule, signal, subject, positiveOwners) {
  const metricEntities = metricScopeEntitiesForRule(rule, signal);
  if (metricEntities.length) {
    const bucket = rulePresenceBucket(rule) || semanticDirectoryBucket(rule, signal);
    return metricEntities.map(entity => ({ entity, bucket }));
  }
  const ruleEntity = semanticEntityForRule(rule, signal);
  const ruleBucket = rulePresenceBucket(rule);
  const owners = positiveOwners.get(subjectKey(subject)) || [];
  if (ruleBucket === 'required' || ruleBucket === 'recommended' || ruleBucket === 'optional') {
    return [{ entity: ruleEntity, bucket: ruleBucket }];
  }
  if (ruleBucket === 'forbidden') {
    const oppositeAreaOwners = owners.filter(owner => semanticEntityArea(owner.entity) !== semanticEntityArea(ruleEntity));
    if (oppositeAreaOwners.length) return oppositeAreaOwners;
    return [{ entity: ruleEntity, bucket: 'forbidden' }];
  }
  if (owners.length) return owners.map(owner => ({ entity: owner.entity, bucket: '' }));
  return [{ entity: ruleEntity, bucket: '' }];
}

function semanticConditionForRule(rule, entity) {
  const rawKey = String(rule?.condition_key || '').trim();
  const rawLabel = String(rule?.condition_zh || '').trim();
  if (rawKey || rawLabel) {
    const key = normalizedSemanticKey(rawKey || rawLabel);
    return { key, label: rawLabel || SEMANTIC_CONDITION_LABELS[key] || rawKey || '条件约束' };
  }
  if (entity.kind === 'event' && entity.key === 'api_request') {
    return { key: 'unclassified', label: SEMANTIC_CONDITION_LABELS.unclassified };
  }
  if (entity.kind === 'unclassified') {
    return { key: 'unclassified', label: SEMANTIC_CONDITION_LABELS.unclassified };
  }
  return { key: 'common', label: SEMANTIC_CONDITION_LABELS.common };
}

function semanticConditionRank(condition) {
  const index = SEMANTIC_CONDITION_ORDER.indexOf(condition?.key);
  return index === -1 ? SEMANTIC_CONDITION_ORDER.length : index;
}

function ruleDisplayOrder(rule) {
  const order = Number(rule?.display_order);
  if (Number.isFinite(order)) return order;
  return semanticBucketRank(rulePresenceBucket(rule)) * 100 + constraintTypeRank(rule) * 10 + findingLevelRank(rule?.level);
}

function semanticMetadataIssues(rule, signal, entity, condition) {
  const issues = [];
  if (!rule?.entity) issues.push('缺少 @weaver.rule.entity');
  const subjects = ruleSubjects(rule);
  const type = ruleConstraintType(rule);
  if (!subjects.length) {
    if (ruleFields(rule).length) {
      issues.push('@weaver.rule.fields 仅作兼容；建议补 @weaver.rule.subject');
    } else {
      issues.push('缺少 @weaver.rule.subject');
    }
  }
  if (!type) issues.push('缺少 @weaver.rule.constraint.type');
  if (type === 'enum' && !(Array.isArray(rule?.allowed_values) && rule.allowed_values.length)) {
    issues.push('enum 约束缺少 @weaver.rule.allowed_values');
  }
  if (type === 'fixed_value' && !rule?.expected_value) {
    issues.push('fixed_value 约束缺少 @weaver.rule.expected_value');
  }
  if (signal === 'traces' && entity.kind === 'span' && !rule?.span_kind && !String(rule?.entity || '').toLowerCase().startsWith('span:')) {
    issues.push('缺少 @weaver.rule.span.kind');
  }
  if (entity.kind === 'event' && entity.key === 'api_request' && condition.key === 'unclassified') {
    issues.push('缺少 @weaver.rule.condition.*');
  }
  return issues;
}

function buildSemanticRuleMatrix(rules, signal) {
  const groups = new Map();
  const positiveOwners = buildPositiveSubjectOwners(rules, signal);

  function ensureGroup(entity) {
    const entityKey = semanticEntityId(entity);
    if (!groups.has(entityKey)) {
      groups.set(entityKey, { ...entity, id: entityKey, rules: [], conditions: new Map() });
    }
    return groups.get(entityKey);
  }

  function addRuleTarget(rule, entity, bucket, subjects) {
    const group = ensureGroup(entity);
    const condition = semanticConditionForRule(rule, entity);
    const conditionKey = condition.key;
    if (!group.conditions.has(conditionKey)) {
      group.conditions.set(conditionKey, {
        ...condition,
        rules: [],
        buckets: new Map(semanticFieldBucketOrder(signal).map(bucket => [bucket, []])),
      });
    }
    const conditionGroup = group.conditions.get(conditionKey);
    const enrichedRule = {
      ...rule,
      semantic_bucket: bucket,
      semantic_subjects: subjects.map(normalizeRuleSubject).filter(Boolean),
      semantic_target_metric: group.metric_name || '',
      semantic_entity: group,
      semantic_condition: conditionGroup,
      semantic_issues: semanticMetadataIssues(rule, signal, group, condition),
    };
    group.rules.push(enrichedRule);
    conditionGroup.rules.push(enrichedRule);
    if (bucket) {
      if (!conditionGroup.buckets.has(bucket)) conditionGroup.buckets.set(bucket, []);
      conditionGroup.buckets.get(bucket).push(enrichedRule);
    }
  }

  rules.forEach(rule => {
    const subjects = ruleTreeSubjects(rule);
    if (!subjects.length) {
      const metricEntities = metricScopeEntitiesForRule(rule, signal);
      if (metricEntities.length) {
        const bucket = rulePresenceBucket(rule) || semanticDirectoryBucket(rule, signal);
        metricEntities.forEach(entity => addRuleTarget(rule, entity, bucket, []));
      } else {
        const entity = semanticEntityForRule(rule, signal);
        addRuleTarget(rule, entity, rulePresenceBucket(rule), []);
      }
      return;
    }
    const targets = new Map();
    subjects.forEach(subject => {
      targetEntriesForRuleSubject(rule, signal, subject, positiveOwners).forEach(target => {
        const key = targetKeyFor(target.entity, target.bucket);
        if (!targets.has(key)) targets.set(key, { entity: target.entity, bucket: target.bucket, subjects: [] });
        targets.get(key).subjects.push(subject);
      });
    });
    targets.forEach(target => addRuleTarget(rule, target.entity, target.bucket, target.subjects));
  });

  return Array.from(groups.values())
    .map(group => ({
      ...group,
      conditions: Array.from(group.conditions.values()).sort((a, b) =>
        semanticConditionRank(a) - semanticConditionRank(b) ||
        a.label.localeCompare(b.label)
      ),
    }))
    .sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label));
}

function semanticSelectableKey(group) {
  return `semantic:${group?.kind || 'unknown'}:${group?.key || 'unknown'}`;
}

function semanticFieldSelectableKey(group, bucket, label) {
  return `semantic-field:${group?.id || semanticSelectableKey(group)}:${bucket}:${encodeURIComponent(String(label || 'field'))}`;
}

function semanticFieldLevel(bucket) {
  return bucket === 'metric_dimensions'
    ? 'violation'
    : bucket === 'metric_dimension_constraints'
    ? 'violation'
    : bucket === 'metric_constraints'
    ? 'violation'
    : bucket === 'metric_datapoint_constraints'
    ? 'violation'
    : bucket === 'metric_forbidden_dimensions'
    ? 'violation'
    : bucket === 'recommended'
    ? 'improvement'
    : bucket === 'required' || bucket === 'forbidden' || bucket === 'constraint'
      ? 'violation'
      : 'muted';
}

function uniqueRulesFromRows(rows) {
  return Array.from(new Map(rows.map(row => [ruleSelectableKey(row.rule), row.rule])).values());
}

function semanticFieldAffected(field) {
  return uniqueRulesFromRows(field.rows).reduce((sum, rule) => sum + countAffected(rule), 0);
}

function semanticFieldMetricScopes(field) {
  if (field?.group?.metric_name) return [field.group.metric_name];
  return uniqueValues((field?.rows || []).flatMap(row => row.rule?.semantic_target_metric ? [row.rule.semantic_target_metric] : ruleRedMetricScopes(row.rule)));
}

function semanticRowMetricScopes(row) {
  if (row?.rule?.semantic_target_metric) return [row.rule.semantic_target_metric];
  return ruleRedMetricScopes(row?.rule);
}

function isMetricNameGroup(group) {
  return group?.kind === 'metric_name';
}

function semanticFieldDisplayBucketLabel(field) {
  if (METRIC_LANE_LABELS[field?.bucket]) return METRIC_LANE_LABELS[field.bucket];
  return FINDING_FIELD_BUCKET_LABELS[field?.bucket] || '字段';
}

function semanticFieldDisplayHint(field) {
  if (METRIC_LANE_HINTS[field?.bucket]) return METRIC_LANE_HINTS[field.bucket];
  return SEMANTIC_BUCKET_HINTS[field?.bucket] || '该字段来自 Rego 注释声明。';
}

function semanticMetricLaneSelectableKey(group, lane, field) {
  const identityKey = isExactRedMetricSubject(field?.subject) ? 'metric-identity' : '';
  const key = identityKey || subjectKey(field?.subject) || field?.label || 'field';
  return `semantic-field:${group?.id || semanticSelectableKey(group)}:${lane}:${encodeURIComponent(key)}`;
}

function mergeFieldConditions(target, source, rowLabel = null, rowIsField = null) {
  source.conditions.forEach((conditionRows, key) => {
    if (!target.conditions.has(key)) {
      target.conditions.set(key, { condition: conditionRows.condition, rows: [] });
    }
    const targetCondition = target.conditions.get(key);
    conditionRows.rows.forEach(row => {
      const rowKey = `${key}:${ruleSelectableKey(row.rule)}:${subjectKey(row.subject)}`;
      if (target.rowKeys.has(rowKey)) return;
      target.rowKeys.add(rowKey);
      const nextRow = {
        ...row,
        label: rowLabel || row.label,
        isField: rowIsField === null ? row.isField : rowIsField,
      };
      target.rows.push(nextRow);
      targetCondition.rows.push(nextRow);
    });
  });
}

function metricRulePlacements(field) {
  return uniqueValues((field?.rows || []).map(row => row?.rule?.placement).filter(Boolean));
}

function metricLaneForField(field) {
  const subject = normalizeRuleSubject(field?.subject);
  const subjectValue = subject?.value || field?.label || '';
  const bucket = field?.bucket || '';
  const placements = metricRulePlacements(field);

  if (isExactRedMetricSubject(subject)) return 'metric_constraints';
  if (bucket === 'forbidden') return 'metric_forbidden_dimensions';
  if (placements.includes('metric_data_point')) return 'metric_datapoint_constraints';
  if (placements.includes('metric')) return 'metric_constraints';
  if (placements.includes('metric_data_point_attributes')) {
    if (bucket === 'required') return 'metric_dimensions';
    return 'metric_dimension_constraints';
  }
  if (subjectValue.startsWith('metric.data_point.')) return 'metric_datapoint_constraints';
  if (subjectValue.startsWith('metric.')) return 'metric_constraints';
  if (bucket === 'required') return 'metric_dimensions';
  return 'metric_dimension_constraints';
}

function metricLaneFieldLabel(field) {
  return isExactRedMetricSubject(field?.subject) ? METRIC_IDENTITY_CONSTRAINT_LABEL : field.label;
}

function semanticMetricLaneGroups(group) {
  const lanes = new Map(METRIC_LANE_ORDER.map(lane => [lane, {
    key: lane,
    label: METRIC_LANE_LABELS[lane],
    hint: METRIC_LANE_HINTS[lane],
    fields: [],
    fieldMap: new Map(),
  }]));

  semanticFieldBuckets(group, 'metrics').flatMap(bucket => bucket.fields).forEach(field => {
    const lane = metricLaneForField(field);
    const laneGroup = lanes.get(lane);
    if (!laneGroup) return;
    const label = metricLaneFieldLabel(field);
    const isMetricIdentity = isExactRedMetricSubject(field.subject);
    const key = semanticMetricLaneSelectableKey(group, lane, field);
    if (!laneGroup.fieldMap.has(key)) {
      laneGroup.fieldMap.set(key, {
        ...field,
        key,
        label,
        bucket: lane,
        metric_lane: lane,
        group,
        rows: [],
        rowKeys: new Set(),
        conditions: new Map(),
        isField: isMetricIdentity ? false : field.isField,
        source_buckets: new Set(),
      });
    }
    const target = laneGroup.fieldMap.get(key);
    target.source_buckets.add(field.bucket);
    mergeFieldConditions(target, field, label, isMetricIdentity ? false : field.isField);
  });

  return METRIC_LANE_ORDER.map(lane => {
    const laneGroup = lanes.get(lane);
    const fields = Array.from(laneGroup.fieldMap.values())
      .map(field => {
        field.bucket_labels = Array.from(field.source_buckets)
          .map(bucket => FINDING_FIELD_BUCKET_LABELS[bucket] || METRIC_LANE_LABELS[bucket] || bucket)
          .filter(Boolean);
        delete field.source_buckets;
        delete field.rowKeys;
        return field;
      })
      .sort((a, b) =>
        Number(a.label !== METRIC_IDENTITY_CONSTRAINT_LABEL) - Number(b.label !== METRIC_IDENTITY_CONSTRAINT_LABEL) ||
        a.label.localeCompare(b.label)
      );
    return {
      key: laneGroup.key,
      label: laneGroup.label,
      hint: laneGroup.hint,
      fields,
    };
  });
}

function semanticMetricLaneFields(group) {
  return semanticMetricLaneGroups(group).flatMap(lane => lane.fields);
}

function semanticFieldBuckets(group, signal = null) {
  const bucketOrder = semanticFieldBucketOrder(signal);
  const buckets = new Map(bucketOrder.map(bucket => [bucket, new Map()]));
  const fieldsBySubject = new Map();

  function ensureField(bucket, subject) {
    const label = subjectDisplayLabel(subject);
    const fieldKey = semanticFieldSelectableKey(group, bucket, label);
    const bucketFields = buckets.get(bucket);
    if (!bucketFields.has(fieldKey)) {
      bucketFields.set(fieldKey, {
        key: fieldKey,
        label,
        bucket,
        group,
        rows: [],
        rowKeys: new Set(),
        conditions: new Map(),
        subject: normalizeRuleSubject(subject),
        subjectKey: subjectKey(subject),
        isField: normalizeRuleSubject(subject)?.kind === 'field',
      });
      const key = subjectKey(subject);
      if (!fieldsBySubject.has(key)) fieldsBySubject.set(key, []);
      fieldsBySubject.get(key).push(bucketFields.get(fieldKey));
    }
    return bucketFields.get(fieldKey);
  }

  function addRow(field, rule, condition, subject) {
    const rowKey = `${condition.key}:${ruleSelectableKey(rule)}:${subjectKey(subject)}`;
    if (field.rowKeys.has(rowKey)) return;
    field.rowKeys.add(rowKey);
    const row = {
      label: field.label,
      rule,
      condition,
      subject: normalizeRuleSubject(subject),
      isField: normalizeRuleSubject(subject)?.kind === 'field',
    };
    field.rows.push(row);
    if (!field.conditions.has(condition.key)) {
      field.conditions.set(condition.key, { condition, rows: [] });
    }
    field.conditions.get(condition.key).rows.push(row);
  }

  (group?.conditions || []).forEach(condition => {
    (condition.rules || []).forEach(rule => {
      const bucket = semanticDirectoryBucket(rule, signal);
      if (!bucket) return;
      const subjects = rule.semantic_subjects || ruleTreeSubjects(rule);
      subjects.forEach(subject => {
        const field = ensureField(bucket, subject);
        addRow(field, rule, condition, subject);
      });
    });
  });

  (group?.conditions || []).forEach(condition => {
    (condition.rules || []).forEach(rule => {
      (rule.semantic_subjects || ruleSubjects(rule)).forEach(subject => {
        const matchedFields = fieldsBySubject.get(subjectKey(subject)) || [];
        matchedFields.forEach(field => addRow(field, rule, condition, subject, false));
      });
    });
  });

  return bucketOrder.map(bucket => ({
    bucket,
    fields: Array.from((buckets.get(bucket) || new Map()).values())
      .map(field => {
        delete field.rowKeys;
        return field;
      })
      .sort((a, b) => a.label.localeCompare(b.label)),
  }));
}

function semanticFieldsForGroups(groups, signal = null) {
  return groups.flatMap(group => signal === 'metrics' && isMetricNameGroup(group)
    ? semanticMetricLaneFields(group)
    : semanticFieldBuckets(group, signal).flatMap(bucket => bucket.fields));
}

function renderSemanticFieldLeaf(field, bucket, activeKey, containerId, signal, subtitle = null) {
  const rules = uniqueRulesFromRows(field.rows);
  const metricScope = signal === 'metrics' ? compactRedMetricScope(semanticFieldMetricScopes(field)) : '';
  const labelSubtitle = subtitle || metricScope || `${rules.length} rules`;
  return `<button class="tree-leaf semantic-field-leaf ${field.key === activeKey ? 'active' : ''} ${esc(semanticFieldLevel(bucket))}" type="button" data-select-rule-tree="${esc(field.key)}" data-rules-container="${esc(containerId || '')}" data-rules-signal="${esc(signal || '')}" title="${esc(`${field.label}${metricScope ? ` · ${metricScope}` : ''}`)}">
    <span class="tree-leaf-main">
      <span class="tree-leaf-title">${esc(field.label)}</span>
      <small>${esc(labelSubtitle)}</small>
    </span>
    ${treeCountBadge(rules.length, semanticFieldLevel(bucket))}
  </button>`;
}

function renderSemanticMetricGroupNode(group, activeKey, containerId, signal) {
  const laneGroups = semanticMetricLaneGroups(group);
  const fieldCount = laneGroups.reduce((sum, lane) => sum + lane.fields.length, 0);
  const issueCount = group.rules.reduce((sum, rule) => sum + (rule.semantic_issues?.length || 0), 0);
  const entityOpenKey = `rule:${containerId}:${signal || 'all'}:${group.id}`;
  const info = RED_METRIC_INFO[group.metric_name] || {};
  const laneHtml = laneGroups
    .filter(lane => lane.fields.length)
    .map(lane => {
      const laneOpenKey = `${entityOpenKey}:${lane.key}`;
      const leaves = lane.fields.map(field => {
        const bucketSummary = field.bucket_labels?.length
          ? `${field.bucket_labels.join(' / ')} · ${uniqueRulesFromRows(field.rows).length} rules`
          : `${uniqueRulesFromRows(field.rows).length} rules`;
        return renderSemanticFieldLeaf(field, lane.key, activeKey, containerId, signal, bucketSummary);
      }).join('');
      return `<details class="tree-domain semantic-metric-lane-node" data-tree-open-key="${esc(laneOpenKey)}" ${treeOpen(state.expandedRuleTrees, laneOpenKey) ? 'open' : ''}>
        <summary>
          <span class="tree-domain-name">${esc(lane.label)}</span>
          <span class="tree-domain-meta">${treeCountBadge(lane.fields.length, semanticFieldLevel(lane.key))}</span>
        </summary>
        <div class="tree-leaves">${leaves}</div>
      </details>`;
    }).join('');
  return `<details class="tree-dir semantic-object-node metric-name-node" data-tree-open-key="${esc(entityOpenKey)}" ${treeOpen(state.expandedRuleTrees, entityOpenKey) ? 'open' : ''}>
    <summary>
      <span class="tree-dir-label">
        <span class="tree-dir-name">${esc(group.label)}</span>
        <small>${esc(`${info.family || 'Metric'} · ${info.red || 'RED'} · ${info.shape || 'metric'} · ${info.unit || ''}`)}</small>
      </span>
      <span class="tree-dir-actions">${treeCountBadge(fieldCount)}${issueCount ? treeCountBadge(issueCount, 'warning') : ''}</span>
    </summary>
    <div class="tree-domains">${laneHtml || '<div class="tree-empty">暂无 metric 约束</div>'}</div>
  </details>`;
}

function renderSemanticRuleTree(groups, activeKey, containerId, signal) {
  const treeTitle = signal === 'metrics' ? 'Metrics 语义规范' : '字段目录';
  const treeHint = signal === 'metrics'
    ? '通用 attributes / Resource attributes / metric name / 维度 / 约束'
    : '字段类型 / Rego 规则组 / 字段名';
  const groupNodes = groups.map(group => {
    if (signal === 'metrics' && isMetricNameGroup(group)) {
      return renderSemanticMetricGroupNode(group, activeKey, containerId, signal);
    }
    const issueCount = group.rules.reduce((sum, rule) => sum + (rule.semantic_issues?.length || 0), 0);
    const fieldBuckets = semanticFieldBuckets(group, signal);
    const fieldCount = fieldBuckets.reduce((sum, bucket) => sum + bucket.fields.length, 0);
    const entityOpenKey = `rule:${containerId}:${signal || 'all'}:${group.id}`;
    const bucketHtml = fieldBuckets.map(({ bucket, fields }) => {
      const bucketOpenKey = `${entityOpenKey}:${bucket}`;
      const leaves = fields.length ? fields.map(field => {
        return renderSemanticFieldLeaf(field, bucket, activeKey, containerId, signal);
      }).join('') : '<div class="tree-empty">暂无字段</div>';
      return `<details class="tree-domain semantic-bucket-node" data-tree-open-key="${esc(bucketOpenKey)}" ${treeOpen(state.expandedRuleTrees, bucketOpenKey) ? 'open' : ''}>
        <summary>
          <span class="tree-domain-name">${esc(FINDING_FIELD_BUCKET_LABELS[bucket])}</span>
          <span class="tree-domain-meta">${treeCountBadge(fields.length, semanticFieldLevel(bucket))}</span>
        </summary>
        <div class="tree-leaves">${leaves}</div>
      </details>`;
    }).join('');
    return `<details class="tree-dir semantic-object-node" data-tree-open-key="${esc(entityOpenKey)}" ${treeOpen(state.expandedRuleTrees, entityOpenKey) ? 'open' : ''}>
      <summary>
        <span class="tree-dir-name">${esc(group.label)}</span>
        <span class="tree-dir-actions">${treeCountBadge(fieldCount)}${issueCount ? treeCountBadge(issueCount, 'warning') : ''}</span>
      </summary>
      <div class="tree-domains">${bucketHtml}</div>
    </details>`;
  }).join('');
  return `<aside class="split-tree rule-tree semantic-rule-tree compact-tree">
    <div class="tree-head compact">
      <div><b>${esc(treeTitle)}</b><small>${esc(treeHint)}</small></div>
      ${treeCountBadge(groups.reduce((sum, group) => sum + group.rules.length, 0))}
    </div>
    ${groupNodes || '<div class="empty">No Rego rules loaded for this signal.</div>'}
  </aside>`;
}

function renderSemanticRuleRow(row, groupEnabled, signal = null) {
  const rule = row.rule;
  const ruleKey = ruleSelectableKey(rule);
  const canToggleRule = groupEnabled;
  const affected = countAffected(rule);
  const issues = rule.semantic_issues || [];
  const issueHtml = issues.length ? `<div class="meta-line metadata-warning">${esc(issues.join('；'))}</div>` : '';
  const metricScopeHtml = signal === 'metrics' ? redMetricScopeChipsHtml(semanticRowMetricScopes(row)) : '';
  return `<div class="field-rule-row ${esc(levelClass(rule.level))} ${rule.enabled ? '' : 'disabled'}">
    <div class="field-rule-main">
      <div class="field-rule-title">
        ${row.isField ? `<code>${esc(row.label)}</code>` : `<span>${esc(row.label)}</span>`}
        ${ruleConstraintChipHtml(rule)}
        ${rule.enabled ? badge(rule.level) : badge('disabled', groupEnabled ? 'disabled' : 'group disabled')}
      </div>
      <div class="meta-line">${esc(rule.id)} · ${esc(rule.file)}:${esc(rule.line)} · affected ${esc(affected)}</div>
      <p>${esc(rulePurposeText(rule))}</p>
      ${ruleConstraintSummaryHtml(rule)}
      ${metricScopeHtml}
      ${row.isField ? '' : `<div class="field-chip-row compact">${ruleFieldChipsHtml(rule)}</div>`}
      ${issueHtml}
    </div>
    <div class="field-rule-actions">
      <button class="btn btn-outline" type="button" data-rule-id="${esc(ruleKey)}" data-rule-group="${esc(rule.rule_group || 'default')}" data-rule-enabled="${esc(!rule.enabled)}" ${canToggleRule ? '' : 'disabled'}>${canToggleRule ? (rule.enabled ? '关闭' : '启用') : '上级已关闭'}</button>
      <button class="btn btn-secondary" type="button" data-copy-rule-fix="${esc(ruleKey)}">复制 prompt</button>
      <button class="btn btn-ghost" type="button" data-rule-findings="${esc(ruleKey)}">Findings</button>
    </div>
    <details class="debug-source compact">
      <summary>规则说明与 Rego source</summary>
      ${ruleDocHtml(rule, false, signal)}
      <pre class="rule-source">${esc(rule.source || 'no source available')}</pre>
    </details>
  </div>`;
}

function renderSemanticRuleDetail(field, groupEnabled, signal = null) {
  if (!field) return '<section class="split-detail"><div class="empty">请选择左侧字段。</div></section>';
  const group = field.group;
  const rules = uniqueRulesFromRows(field.rows);
  const affected = semanticFieldAffected(field);
  const metadataIssues = rules.reduce((sum, rule) => sum + (rule.semantic_issues?.length || 0), 0);
  const metricScopes = signal === 'metrics' ? semanticFieldMetricScopes(field) : [];
  const conditions = Array.from(field.conditions.values())
    .sort((a, b) => semanticConditionRank(a.condition) - semanticConditionRank(b.condition) || a.condition.label.localeCompare(b.condition.label));
  const conditionHtml = conditions.map(({ condition, rows }) => {
    const sortedRows = rows.slice().sort((a, b) =>
      ruleDisplayOrder(a.rule) - ruleDisplayOrder(b.rule) ||
      String(a.rule.id || a.rule.rule).localeCompare(String(b.rule.id || b.rule.rule))
    );
    return `<section class="field-matrix-condition">
      <header class="condition-head">
        <div>
          <span class="section-kicker">${esc(condition.key)}</span>
          <h3>${esc(condition.label)}</h3>
          <p>${esc(sortedRows.length)} rules · ${esc(group.label)}</p>
        </div>
      </header>
      <div class="field-rule-list field-page-rule-list">${sortedRows.map(row => renderSemanticRuleRow(row, groupEnabled, signal)).join('')}</div>
    </section>`;
  }).join('');
  return `<section class="split-detail rule-detail-panel semantic-rule-detail">
    <header class="detail-panel-head directory field-page-head">
      <div>
        ${groupEnabled ? badge('ok', 'active') : badge('disabled', 'policy group disabled')}
        <span class="rule-code">${esc(semanticFieldDisplayBucketLabel(field))}</span>
        <h3><code>${esc(field.label)}</code></h3>
        <p>${esc(semanticFieldDisplayHint(field))}</p>
        ${redMetricScopeSummaryHtml(metricScopes)}
        <div class="meta-line">${esc(group.label)} · ${esc(group.subtitle)} · rules ${esc(rules.length)}${affected ? ` · affected ${esc(affected)}` : ''}${metadataIssues ? ` · metadata gaps ${esc(metadataIssues)}` : ''}</div>
      </div>
    </header>
    ${conditionHtml || '<div class="empty">该字段没有可展示规则。</div>'}
  </section>`;
}

function ruleSelectableKey(rule) {
  return rule?.key || `${rule?.rule_group || 'default'}:${rule?.policy_dir || 'unknown'}:${rule?.group || 'unknown'}:${rule?.id || rule?.rule || 'rule'}`;
}

function compactLevelMark(level) {
  const normalized = levelClass(level);
  const label = normalized === 'violation' ? 'V' : normalized === 'improvement' ? 'R' : normalized === 'information' ? 'I' : normalized === 'semconv' ? 'S' : '?';
  return `<span class="level-mark ${esc(normalized)}">${label}</span>`;
}

function renderRules(containerId = 'rulesModalBody', signal = null) {
  const policyEntries = policyGroupEntries();
  if (!policyEntries.length) {
    setHtmlIfChanged(containerId, '<div class="empty">No Rego policy groups loaded.</div>');
    return;
  }

  let activeGroup = state.activeRuleGroups[containerId];
  if (!policyEntries.some(([name]) => name === activeGroup)) activeGroup = policyEntries[0][0];
  state.activeRuleGroups[containerId] = activeGroup;

  const activePolicyGroup = policyEntries.find(([name]) => name === activeGroup)?.[1];
  const groupEnabled = activePolicyGroup.enabled !== false;
  const visibleRules = sectionEntriesForPolicyGroup(activePolicyGroup, signal)
    .flatMap(([, section]) => rulesForSection(section, signal));
  const groups = buildSemanticRuleMatrix(visibleRules, signal);
  let activeKey = state.activeRuleSelections[containerId];
  const fields = semanticFieldsForGroups(groups, signal);
  let activeField = fields.find(field => field.key === activeKey);
  if (!activeField && activeKey?.startsWith('semantic:')) {
    const previousGroup = groups.find(group => semanticSelectableKey(group) === activeKey);
    activeField = fields.find(field => field.group.id === previousGroup?.id);
  }
  if (!activeField && fields.length) {
    activeField = fields[0];
    activeKey = activeField.key;
  }
  if (!activeField) activeKey = '';
  state.activeRuleSelections[containerId] = activeKey;
  const stats = ruleStats(visibleRules);
  const signalLabel = signal ? SIGNALS[signal].label : 'All Signals';
  const treeScroll = captureRuleTreeScroll(containerId);
  const organizationText = signal === 'metrics'
    ? 'Metrics 按层级展示：通用 attributes、Resource attributes、具体 metric name，以及该 metric 的维度、维度约束、指标约束、数据点约束、禁止维度。规范外指标不进入本 RED 约束视图，也不会因此产生 findings。'
    : `按字段所属区域组织：通用 attributes、resource attributes、span/event/metric specific；区域内按字段主 presence 组展示 ${FINDING_FIELD_BUCKET_LABELS.required} / ${FINDING_FIELD_BUCKET_LABELS.recommended} / ${FINDING_FIELD_BUCKET_LABELS.forbidden}；错位禁止规则归并到字段主目录详情。`;

  const groupTabs = policyEntries.map(([name, policyGroup]) => {
    const enabled = policyGroup.enabled !== false;
    return `<button class="tab ${name === activeGroup ? 'active' : ''}" type="button" data-rules-policy-tab="${esc(name)}" data-rules-container="${esc(containerId)}" data-rules-signal="${esc(signal || '')}">
      ${esc(policyGroupLabel(name))} <span class="tab-count">${esc(policyGroup.enabled_rules ?? 0)}/${esc(policyGroup.total ?? 0)}</span>${enabled ? '' : ' · off'}
    </button>`;
  }).join('');

  setHtmlIfChanged(containerId, `<section class="rules-browser" data-rules-browser="${esc(containerId)}" data-rules-signal="${esc(signal || '')}">
    <div class="rule-tab-row">
      <div>
        <div class="section-kicker">Policy group</div>
        <nav class="tabs rule-tabs" aria-label="Policy groups">${groupTabs}</nav>
      </div>
      ${switchButton('policy group', groupEnabled, {
        'data-policy-group-toggle': activeGroup,
        'data-policy-group-enabled': String(!groupEnabled),
      })}
    </div>
    <section class="rule-group rule-group-split">
      <div class="rule-group-head">
        <div class="rule-group-title-row">
          <div>
            <h2>${esc(policyGroupLabel(activeGroup))} / ${esc(signalLabel)}</h2>
            <p>${stats.enabled}/${stats.total} enabled · policy group: ${esc(activeGroup)} · source: Rego policy</p>
            <p>${esc(organizationText)}</p>
            <p>${esc(activePolicyGroup?.readme_summary_zh || policy_group_readme_summary(activeGroup))}</p>
          </div>
          <div class="badge ${groupEnabled ? 'badge-ok' : 'badge-disabled'}">${groupEnabled ? 'active' : 'disabled'}</div>
        </div>
      </div>
      <div class="split-browser rule-browser">
        ${renderSemanticRuleTree(groups, activeKey, containerId, signal)}
        ${renderSemanticRuleDetail(activeField, groupEnabled, signal)}
      </div>
    </section>
  </section>`);
  restoreRuleTreeScroll(containerId, treeScroll);
}

function policy_group_readme_summary(groupName) {
  return groupName === 'aivision'
    ? 'AI Vision 规则组：面向 AI agent 遥测的内置语义规则集合。'
    : `${groupName || 'default'} 规则组：自定义语义规则集合。`;
}

function switchButton(label, enabled, attrs, disabled = false) {
  const attrText = Object.entries(attrs).map(([key, value]) => `${key}="${esc(value)}"`).join(' ');
  return `<button class="switch-control ${enabled ? 'on' : ''}" type="button" ${attrText} ${disabled ? 'disabled' : ''} aria-pressed="${enabled ? 'true' : 'false'}">
    <span class="switch-track"><span class="switch-thumb"></span></span>
    <span>${enabled ? '启用' : '关闭'} ${esc(label)}</span>
  </button>`;
}

function countAffected(rule) {
  return state.findings.filter(item => item.rule?.key === rule.key || item.rule?.id === rule.id || item.finding.id === rule.id || item.finding.rule === rule.rule).length;
}

function renderSignalPayloads(signal) {
  const records = recordsForSignal(signal);
  const html = records.length ? records.map(record => {
    const findings = state.findings.filter(item => item.record.request_id === record.request_id);
    return `<article class="finding-card ${findings.length ? 'violation' : ''}">
      <header><div><div class="finding-title">${esc(record.request_id)}</div><div class="meta-line">${esc(record.method)} ${esc(record.path)} · ${esc(record.signal)} · ${esc(record.sample_count)} samples</div></div>${statusBadge(record.status)}</header>
      <div class="meta-line">Agent/source: ${esc(record.agent || record.source || 'unknown')} · Content: ${esc(record.content_type)}</div>
      <div class="toolbar" style="justify-content:flex-start;margin-top:10px">
        <button class="btn btn-secondary" type="button" data-request-findings="${esc(record.request_id)}">Open findings (${findings.length})</button>
        <button class="btn btn-outline" type="button" data-open-payload="${esc(record.request_id)}">Open payload</button>
      </div>
    </article>`;
  }).join('') : `<div class="empty">No ${esc(SIGNALS[signal].label)} payloads yet.</div>`;
  setHtmlIfChanged(`${signal}PayloadList`, html);
}

function renderSignal(signal, options = {}) {
  renderSignalOverview(signal);
  renderSignalFindings(signal);
  renderDiagnosticRows(signal);
  if (options.includeRules !== false) renderRules(`${signal}RulesInline`, signal);
  renderSignalPayloads(signal);
}

function renderAll(options = {}) {
  state.findings = flattenFindings(state.records);
  renderPrimaryTabs();
  renderStats();
  renderForwardPolicy();
  renderRulesSummary();
  Object.keys(SIGNALS).forEach(signal => renderSignal(signal, options));
  if (options.includeRules !== false && $('rulesBackdrop').classList.contains('open')) renderRules('rulesModalBody');
}

function scheduleRender(options = {}) {
  if (options.deferWhileScrolling && state.scrolling) {
    state.queuedRenderOptions = {
      includeRules: (state.queuedRenderOptions?.includeRules === true) || options.includeRules !== false,
    };
    return;
  }
  state.queuedRenderOptions = null;
  renderAll(options);
}

function markScrolling() {
  state.scrolling = true;
  clearTimeout(state.scrollTimer);
  state.scrollTimer = setTimeout(() => {
    state.scrolling = false;
    if (state.queuedRenderOptions) scheduleRender(state.queuedRenderOptions);
  }, SCROLL_IDLE_MS);
}

function mergeRefreshOptions(previous = null, next = {}) {
  if (!previous) return { ...next };
  return {
    includeRules: previous.includeRules !== false || next.includeRules !== false,
    deferWhileScrolling: Boolean(previous.deferWhileScrolling && next.deferWhileScrolling),
  };
}

async function refresh(options = {}) {
  if (state.refreshInFlight) {
    state.queuedRefreshOptions = mergeRefreshOptions(state.queuedRefreshOptions, options);
    return state.refreshPromise;
  }
  state.refreshInFlight = true;
  state.refreshPromise = (async () => {
    const includeRules = options.includeRules !== false;
    const revision = state.dataRevision;
    try {
      const [stats, recent, rules, settings] = await Promise.all([
        fetch('/api/stats').then(response => response.json()),
        fetch('/api/recent?all=true&include_payload=false').then(response => response.json()),
        includeRules ? fetch('/api/rules').then(response => response.json()) : Promise.resolve(state.rules),
        fetch('/api/settings').then(response => response.json()),
      ]);
      if (revision !== state.dataRevision) return;
      state.stats = stats || {};
      state.settings = settings || { forward_policy: state.stats.forward_policy || 'always' };
      state.records = recent.records || [];
      if (includeRules) {
        state.rules = rules || { groups: { spans: [], metrics: [], events: [] }, policy_groups: {}, disabled: [], disabled_policy_groups: [] };
      }
      scheduleRender({ includeRules, deferWhileScrolling: options.deferWhileScrolling });
    } finally {
      state.refreshInFlight = false;
      state.refreshPromise = null;
      const queuedOptions = state.queuedRefreshOptions;
      state.queuedRefreshOptions = null;
      if (queuedOptions) await refresh(queuedOptions);
    }
  })();
  return state.refreshPromise;
}

function openFinding(id) {
  const item = state.findings.find(finding => finding.id === id);
  if (!item) return;
  state.activeFinding = item;
  state.activeDiagnosis = 'trace';
  document.querySelectorAll('.diagnosis').forEach(button => button.classList.toggle('active', button.dataset.diagnosis === 'trace'));
  $('findingTitle').textContent = `Finding · ${item.id}`;
  $('findingSubtitle').textContent = `${item.record.signal} · ${item.path}`;
  renderFindingDetail();
  $('findingBackdrop').classList.add('open');
}

function renderFindingDetail() {
  const item = state.activeFinding;
  if (!item) return;
  const rule = item.rule;
  const ctx = contextText(item.finding);
  $('findingBody').innerHTML = `
    <section class="detail-section"><h3>Why failed</h3>
      <p><b>Expected:</b> ${esc(item.expected)}</p>
      <p><b>Actual:</b> ${esc(item.actual)}</p>
      <p><b>Message:</b> ${esc(item.finding.message || '')}</p>
    </section>
    <section class="detail-section"><h3>Telemetry evidence</h3>
      <p><b>Request:</b> ${esc(item.record.request_id)}</p>
      <p><b>Signal:</b> ${esc(item.record.signal)} / ${esc(groupLabel(item.group))}</p>
      <p><b>Agent:</b> ${esc(item.record.agent || item.record.source || 'unknown')}</p>
      <p><b>Path:</b> <code>${esc(item.path)}</code></p>
      ${ctx ? `<pre class="context-pre">${esc(ctx)}</pre>` : '<p>No validator context.</p>'}
      <button class="btn btn-outline" type="button" data-open-payload="${esc(item.record.request_id)}" data-open-payload-finding="${esc(item.id)}">打开完整 Payload</button>
    </section>
    <section class="detail-section"><h3>Rule evidence</h3>
      <p><b>Rule:</b> ${esc(item.finding.rule || item.finding.id || '')}</p>
      <p><b>Policy group:</b> ${esc(rule?.rule_group || 'unknown')}</p>
      <p><b>Directory:</b> ${esc(rule?.policy_dir || 'unknown')}</p>
      ${rule ? ruleDocHtml(rule, false, item.record.signal) : '<p><b>规则说明:</b> 未匹配到规则说明</p>'}
      <p><b>Source:</b> ${rule ? `${esc(rule.file)}:${esc(rule.line)}` : 'not matched in loaded policy list'}</p>
      <p><b>State:</b> ${rule ? (rule.enabled ? 'enabled' : 'disabled') : 'unknown'}</p>
      ${rule ? `<details class="debug-source compact"><summary>Debug: Rego source</summary><pre class="context-pre">${esc(rule.source || '')}</pre></details>` : ''}
    </section>`;
  $('promptPreview').textContent = aiPrompt(item, state.activeDiagnosis);
}

function closeFinding() { $('findingBackdrop').classList.remove('open'); state.activeFinding = null; }

async function openPayload(requestId, findingId = null) {
  const record = state.records.find(row => row.request_id === requestId);
  if (!record) return;
  const targetFinding = findingId ? state.findings.find(finding => finding.id === findingId) : null;
  state.activePayloadTarget = targetFinding ? payloadTargetForFinding(targetFinding) : null;
  $('payloadTitle').textContent = `Payload · ${record.request_id}`;
  const targetLabel = state.activePayloadTarget?.terms?.length
    ? ` · 高亮字段: ${state.activePayloadTarget.terms.map(term => term.kind === 'prefix' ? `${term.value}*` : term.value).join(', ')}`
    : '';
  $('payloadSubtitle').textContent = `${record.method} ${record.path} · ${record.signal} · ${record.content_type}. Local payload may include prompts/tool inputs.${targetLabel}`;
  state.activePayloadText = 'loading payload…';
  $('payloadBody').textContent = state.activePayloadText;
  $('payloadBackdrop').classList.add('open');

  try {
    if (record.payload === undefined || record.payload === null) {
      const response = await fetch(`/api/payload/${encodeURIComponent(requestId)}`);
      if (!response.ok) throw new Error(`failed to load payload: HTTP ${response.status}`);
      const payloadResponse = await response.json();
      record.payload = payloadResponse.payload ?? null;
    }
    state.activePayloadText = record.payload ? JSON.stringify(record.payload, null, 2) : 'no payload';
  } catch (error) {
    state.activePayloadText = error.message || String(error);
    toast(state.activePayloadText);
  }
  $('payloadBody').innerHTML = renderPayloadText(state.activePayloadText, state.activePayloadTarget);
  if (state.activePayloadTarget?.terms?.length || state.activePayloadTarget?.sampleNames?.length) {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(scrollPayloadToTarget);
    else scrollPayloadToTarget();
  }
}

function closePayload() { $('payloadBackdrop').classList.remove('open'); state.activePayloadText = ''; state.activePayloadTarget = null; }
function openRulesModal() { renderRules('rulesModalBody'); $('rulesBackdrop').classList.add('open'); }
function closeRulesModal() { $('rulesBackdrop').classList.remove('open'); }
function openBootstrapPromptModal() { renderBootstrapPrompt(); $('bootstrapPromptBackdrop').classList.add('open'); }
function closeBootstrapPromptModal() { $('bootstrapPromptBackdrop').classList.remove('open'); }
function openLoopPromptModal() { renderLoopPrompt(); $('loopPromptBackdrop').classList.add('open'); }
function closeLoopPromptModal() { $('loopPromptBackdrop').classList.remove('open'); }
function openApiDocsModal() { $('apiDocsBackdrop').classList.add('open'); }
function closeApiDocsModal() { $('apiDocsBackdrop').classList.remove('open'); }
function openGuideModal() { $('guideBackdrop').classList.add('open'); }
function closeGuideModal() { $('guideBackdrop').classList.remove('open'); }

async function toggleRule(id, enabled, ruleGroup = null) {
  const response = await fetch('/api/rules', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, rule_group: ruleGroup, enabled }),
  });
  if (!response.ok) throw new Error(`failed to update rule ${id}: HTTP ${response.status}`);
  await refresh();
  toast(enabled ? '规则已启用' : '规则已关闭');
}

async function togglePolicyGroup(ruleGroup, enabled) {
  const response = await fetch('/api/rules', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rule_group: ruleGroup, enabled }),
  });
  if (!response.ok) throw new Error(`failed to update policy group ${ruleGroup}: HTTP ${response.status}`);
  await refresh();
  toast(enabled ? '规则组已启用' : '规则组已关闭');
}

async function updateForwardPolicy(policy) {
  const normalized = normalizedForwardPolicy(policy);
  const response = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ forward_policy: normalized }),
  });
  if (!response.ok) throw new Error(`failed to update forwarding policy: HTTP ${response.status}`);
  state.settings = await response.json();
  state.stats.forward_policy = state.settings.forward_policy;
  renderForwardPolicy();
  toast(`转发策略已切换为 ${state.settings.forward_policy_label || state.settings.forward_policy}`);
}

async function clearData() {
  const count = state.stats.recent_records ?? state.stats.total_requests ?? state.records.length;
  const confirmed = await confirmAction({
    title: '清理当前诊断数据',
    subtitle: '只清空当前页面和进程内 recent diagnostics。',
    message: [
      `将清空 ${count} 条 recent diagnostics、payload 展示和页面统计计数。`,
      '不会修改 Rego 规则启用状态、转发策略或后端配置。',
      '不会删除 Jaeger / Victoria 中已经写入的数据。',
    ],
    confirmLabel: '清理数据',
    danger: true,
  });
  if (!confirmed) return;

  const button = $('clearDataBtn');
  if (button) button.disabled = true;
  try {
    const response = await fetch('/api/clear', { method: 'POST', cache: 'no-store' });
    if (!response.ok) throw new Error(`failed to clear data: HTTP ${response.status}`);
    const result = await response.json();
    state.dataRevision += 1;
    closeFinding();
    closePayload();
    state.records = [];
    state.findings = [];
    const backends = Object.fromEntries(Object.entries(state.stats.backends || {}).map(([id, backend]) => [
      id,
      { ...backend, counts: {}, last: null },
    ]));
    state.stats = {
      ...state.stats,
      total_requests: 0,
      recent_records: result.remaining_records ?? 0,
      violations: 0,
      improvements: 0,
      semconv_issues: 0,
      forwarded: 0,
      blocked: 0,
      failed: 0,
      skipped: 0,
      backends,
    };
    renderAll({ includeRules: false });
    await refresh({ includeRules: false });
    toast(`已清理 ${result.cleared_records ?? 0} 条数据`);
  } finally {
    if (button) button.disabled = false;
  }
}

function copyRuleFix(ruleId) {
  const rule = allRules().find(candidate => candidate.key === ruleId || candidate.id === ruleId);
  const affected = state.findings.filter(item => item.rule?.key === ruleId || item.rule?.id === rule?.id || item.finding.id === rule?.id || item.finding.rule === rule?.rule);
  const text = [
    'TASK_TYPE: RULE_FIX',
    `RULE_ID: ${rule?.id || ruleId}`,
    `POLICY_GROUP: ${rule?.rule_group || 'unknown'}`,
    `POLICY_DIR: ${rule?.policy_dir || 'unknown'}`,
    `RULE_TITLE_ZH: ${ruleTitle(rule)}`,
    `RULE_ENTITY: ${ruleEntityLabel(rule)}`,
    `RULE_CATEGORY_ZH: ${ruleCategoryLabel(rule)}`,
    `RULE_FIELDS: ${ruleFields(rule).join(', ') || 'unknown'}`,
    `RULE_CONDITION_ZH: ${rule?.condition_zh || rule?.condition_key || 'unknown'}`,
    `RULE_SPAN_KIND: ${rule?.span_kind || 'unknown'}`,
    `RULE_SPEC_ID: ${rule?.spec_id || 'unknown'}`,
    `RULE_PURPOSE_ZH: ${rule?.purpose_zh || 'unknown'}`,
    `RULE_CHECK_ZH: ${rule?.check_zh || 'unknown'}`,
    `RULE_FIX_ZH: ${rule?.fix_zh || 'unknown'}`,
    `RULE_LOCATION: ${rule ? `${rule.file}:${rule.line}` : 'unknown'}`,
    'GOAL: Fix or improve this semantic validation rule while preserving its semantic contract.',
    'CONSTRAINTS: Do not weaken required semantic conventions only to make validation green. Add/update pass and fail fixtures.',
    'AFFECTED_FINDINGS:',
    ...(affected.length ? affected.map(findingText) : ['No current affected live findings.']),
  ].join('\n');
  copyText(text);
}

function showRequestFindings(requestId) {
  const first = state.findings.find(item => item.record.request_id === requestId);
  const record = state.records.find(item => item.request_id === requestId);
  const signal = first?.signal || signalKeyForRecord(record);
  jumpToSignal(signal, 'findings');
  if (first) openFinding(first.id);
  else toast('该请求没有 findings');
}

function showRuleFindings(ruleId) {
  const rule = allRules().find(candidate => candidate.key === ruleId || candidate.id === ruleId);
  const signal = signalKeyForGroup(rule?.group || 'spans');
  const first = state.findings.find(item => item.rule?.key === ruleId || item.rule?.id === rule?.id || item.finding.id === rule?.id);
  jumpToSignal(signal, first ? 'findings' : 'rules');
  if (first) openFinding(first.id);
  else toast('当前 recent diagnostics 无 affected findings');
}

function installEvents() {
  window.addEventListener('scroll', markScrolling, { passive: true });
  document.addEventListener('scroll', markScrolling, { passive: true, capture: true });
  document.querySelectorAll('[data-primary-tab]').forEach(tab => tab.addEventListener('click', () => setPrimaryTab(tab.dataset.primaryTab)));
  document.querySelectorAll('[data-secondary-tab]').forEach(tab => tab.addEventListener('click', () => setSecondaryTab(tab.dataset.signalTab, tab.dataset.secondaryTab)));
  document.addEventListener('toggle', event => {
    const ruleDetails = event.target.closest?.('details[data-rule-details]');
    if (ruleDetails) state.expandedRules[ruleDetails.dataset.ruleDetails] = ruleDetails.open;
    const findingDetails = event.target.closest?.('details[data-finding-group-details]');
    if (findingDetails) state.expandedFindingGroups[findingDetails.dataset.findingGroupDetails] = findingDetails.open;
    const treeDetails = event.target.matches?.('details[data-tree-open-key]') ? event.target : null;
    if (treeDetails) {
      const key = treeDetails.dataset.treeOpenKey;
      if (key.startsWith('finding:')) state.expandedFindingTrees[key] = treeDetails.open;
      else state.expandedRuleTrees[key] = treeDetails.open;
    }
  }, true);
  document.addEventListener('click', event => {
    const jump = event.target.closest('[data-secondary-jump-signal]');
    if (jump) return jumpToSignal(jump.dataset.secondaryJumpSignal, jump.dataset.secondaryJumpTab || 'overview');
    const selectFindingGroup = event.target.closest('[data-select-finding-group]');
    if (selectFindingGroup) {
      const signal = selectFindingGroup.dataset.findingSignal || 'all';
      state.activeFindingGroups[signal] = selectFindingGroup.dataset.selectFindingGroup;
      return signal === 'all' ? renderAll({ includeRules: false }) : renderSignalFindings(signal);
    }
    const selectRuleTree = event.target.closest('[data-select-rule-tree]');
    if (selectRuleTree) {
      state.activeRuleSelections[selectRuleTree.dataset.rulesContainer] = selectRuleTree.dataset.selectRuleTree;
      return renderRules(selectRuleTree.dataset.rulesContainer, selectRuleTree.dataset.rulesSignal || null);
    }
    const open = event.target.closest('[data-open-finding]');
    if (open) return openFinding(open.dataset.openFinding);
    const payload = event.target.closest('[data-open-payload]');
    if (payload) return openPayload(payload.dataset.openPayload, payload.dataset.openPayloadFinding || null);
    const req = event.target.closest('[data-request-findings]');
    if (req) return showRequestFindings(req.dataset.requestFindings);
    const refreshFrame = event.target.closest('[data-refresh-backend-frame]');
    if (refreshFrame) return refreshBackendFrame(refreshFrame.dataset.refreshBackendFrame);
    const copyFindingGroup = event.target.closest('[data-copy-finding-group]');
    if (copyFindingGroup) {
      event.preventDefault(); event.stopPropagation();
      const group = findingGroupEntries().find(candidate => candidate.key === copyFindingGroup.dataset.copyFindingGroup);
      if (group) return copyText(findingPrompt(group.items, `${group.level} · ${OWNER_LABELS[group.owner] || OWNER_LABELS.triage} · ${group.rule_id}`));
    }
    const copyFindingSingle = event.target.closest('[data-copy-finding-single]');
    if (copyFindingSingle) {
      const item = findingItems().find(candidate => candidate.id === copyFindingSingle.dataset.copyFindingSingle);
      if (item) return copyText(findingPrompt([item], `单个 finding · ${item.id}`));
    }
    const tracePrompt = event.target.closest('[data-copy-trace-prompt]');
    if (tracePrompt) {
      const item = state.findings.find(finding => finding.id === tracePrompt.dataset.copyTracePrompt);
      if (item) return copyText(aiPrompt(item, 'trace'));
    }
    const rulePrompt = event.target.closest('[data-copy-rule-prompt]');
    if (rulePrompt) {
      const item = state.findings.find(finding => finding.id === rulePrompt.dataset.copyRulePrompt);
      if (item) return copyText(aiPrompt(item, 'rule'));
    }
    const signalFindingPrompts = event.target.closest('[data-copy-signal-finding-prompts]');
    if (signalFindingPrompts) {
      const signal = signalFindingPrompts.dataset.copySignalFindingPrompts;
      return copyText(findingPrompt(findingItems(signal), `${SIGNALS[signal]?.label || signal} 全部 findings`));
    }
    const refreshRules = event.target.closest('[data-refresh-rules]');
    if (refreshRules) return refresh().catch(error => toast(error.message));
    const policyTab = event.target.closest('[data-rules-policy-tab]');
    if (policyTab) {
      state.activeRuleGroups[policyTab.dataset.rulesContainer] = policyTab.dataset.rulesPolicyTab;
      return renderRules(policyTab.dataset.rulesContainer, policyTab.dataset.rulesSignal || null);
    }
    const policyGroupToggle = event.target.closest('[data-policy-group-toggle]');
    if (policyGroupToggle) {
      event.preventDefault(); event.stopPropagation();
      return togglePolicyGroup(policyGroupToggle.dataset.policyGroupToggle, policyGroupToggle.dataset.policyGroupEnabled === 'true').catch(error => toast(error.message));
    }
    const ruleToggle = event.target.closest('[data-rule-id]');
    if (ruleToggle) {
      event.preventDefault(); event.stopPropagation();
      return toggleRule(ruleToggle.dataset.ruleId, ruleToggle.dataset.ruleEnabled === 'true', ruleToggle.dataset.ruleGroup || null).catch(error => toast(error.message));
    }
    const ruleFix = event.target.closest('[data-copy-rule-fix]');
    if (ruleFix) return copyRuleFix(ruleFix.dataset.copyRuleFix);
    const ruleFindings = event.target.closest('[data-rule-findings]');
    if (ruleFindings) return showRuleFindings(ruleFindings.dataset.ruleFindings);
  });

  document.querySelectorAll('.diagnosis').forEach(button => button.addEventListener('click', () => {
    state.activeDiagnosis = button.dataset.diagnosis;
    document.querySelectorAll('.diagnosis').forEach(other => other.classList.toggle('active', other === button));
    renderFindingDetail();
  }));

  $('refreshBtn').addEventListener('click', () => refresh().catch(error => toast(error.message)));
  $('clearDataBtn').addEventListener('click', () => clearData().catch(error => toast(error.message)));
  $('refreshRulesModalBtn').addEventListener('click', () => refresh().catch(error => toast(error.message)));
  $('openBootstrapPromptBtn').addEventListener('click', openBootstrapPromptModal);
  $('closeBootstrapPromptBtn').addEventListener('click', closeBootstrapPromptModal);
  $('copyBootstrapPromptBtn').addEventListener('click', () => copyText(aiBootstrapPromptText()));
  $('bootstrapLevelSelect').addEventListener('change', renderBootstrapPrompt);
  $('bootstrapConfirmSelect').addEventListener('change', renderBootstrapPrompt);
  LOOP_SIGNAL_ORDER.forEach(signal => $('bootstrapSignal' + cap(signal)).addEventListener('change', renderBootstrapPrompt));
  $('openLoopPromptBtn').addEventListener('click', openLoopPromptModal);
  $('closeLoopPromptBtn').addEventListener('click', closeLoopPromptModal);
  $('copyLoopPromptBtn').addEventListener('click', () => copyText(aiFixLoopPromptText()));
  $('loopFixLevelSelect').addEventListener('change', renderLoopPrompt);
  $('loopConfirmSelect').addEventListener('change', renderLoopPrompt);
  $('loopReportModeSelect').addEventListener('change', renderLoopPrompt);
  $('loopReportInput').addEventListener('input', renderLoopPrompt);
  LOOP_SIGNAL_ORDER.forEach(signal => $('loopSignal' + cap(signal)).addEventListener('change', renderLoopPrompt));
  $('openApiDocsBtn').addEventListener('click', openApiDocsModal);
  $('openGuideBtn').addEventListener('click', openGuideModal);
  $('forwardPolicySelect').addEventListener('change', event => updateForwardPolicy(event.target.value).catch(error => toast(error.message)));
  $('closeApiDocsBtn').addEventListener('click', closeApiDocsModal);
  $('copyApiDocsBtn').addEventListener('click', () => copyText(apiDocsText()));
  $('closeGuideBtn').addEventListener('click', closeGuideModal);
  $('copyGuideBtn').addEventListener('click', () => copyText(guideText()));
  $('closeRulesBtn').addEventListener('click', closeRulesModal);
  $('copyReportBtn').addEventListener('click', () => copyText(liveReportText()));
  $('closeFindingBtn').addEventListener('click', closeFinding);
  $('copyPromptBtn').addEventListener('click', () => state.activeFinding && copyText(aiPrompt(state.activeFinding, state.activeDiagnosis)));
  $('copyPromptPayloadBtn').addEventListener('click', () => state.activeFinding && copyText(aiPrompt(state.activeFinding, state.activeDiagnosis, true)));
  $('copyFindingBtn').addEventListener('click', () => state.activeFinding && copyText(findingText(state.activeFinding)));
  $('closePayloadBtn').addEventListener('click', closePayload);
  $('copyPayloadBtn').addEventListener('click', () => copyText(state.activePayloadText));
  $('copyPayloadSelectedBtn').addEventListener('mousedown', event => event.preventDefault());
  $('copyPayloadSelectedBtn').addEventListener('click', () => {
    const selection = window.getSelection();
    const payload = $('payloadBody');
    const text = selection?.toString();
    if (!text || !payload.contains(selection.anchorNode) || !payload.contains(selection.focusNode)) {
      toast('请先在 Payload 中选中文本。');
      return;
    }
    return copyText(text);
  });
  $('confirmCancelBtn').addEventListener('click', () => closeConfirm(false));
  $('confirmOkBtn').addEventListener('click', () => closeConfirm(true));
  ['findingBackdrop', 'payloadBackdrop', 'rulesBackdrop', 'bootstrapPromptBackdrop', 'loopPromptBackdrop', 'apiDocsBackdrop', 'guideBackdrop', 'confirmBackdrop'].forEach(id => $(id).addEventListener('click', event => {
    if (event.target !== $(id)) return;
    if (id === 'findingBackdrop') closeFinding();
    if (id === 'payloadBackdrop') closePayload();
    if (id === 'rulesBackdrop') closeRulesModal();
    if (id === 'bootstrapPromptBackdrop') closeBootstrapPromptModal();
    if (id === 'loopPromptBackdrop') closeLoopPromptModal();
    if (id === 'apiDocsBackdrop') closeApiDocsModal();
    if (id === 'guideBackdrop') closeGuideModal();
    if (id === 'confirmBackdrop') closeConfirm(false);
  }));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { closeFinding(); closePayload(); closeRulesModal(); closeBootstrapPromptModal(); closeLoopPromptModal(); closeApiDocsModal(); closeGuideModal(); closeConfirm(false); }
  });
}

function dashboardTestHooks() {
  return {
    aiFixLoopPromptText,
    aiBootstrapPromptText,
    bootstrapSemanticRequirementLines,
    semanticRequirementLinesFromGroups,
    semanticFieldPromptLine,
    loopReportInstruction,
    loopReportInstructionLines,
    buildSemanticRuleMatrix,
    renderSemanticRuleDetail,
    renderSemanticRuleRow,
    renderSemanticFieldLeaf,
    ruleDocHtml,
    semanticMetricLaneGroups,
    semanticMetricLaneFields,
    semanticFieldBuckets,
    payloadTargetForFinding,
    renderPayloadText,
    ruleRedMetricScopes,
    isExactRedMetricSubject,
    RED_API_METRICS,
    RED_TOOL_METRICS,
    RED_METRICS,
    METRIC_LANE_ORDER,
    METRIC_LANE_LABELS,
  };
}

if (globalThis.__WEAVER_DASHBOARD_TEST__ === true) {
  globalThis.__WEAVER_DASHBOARD_TEST_HOOKS__ = dashboardTestHooks();
} else {
  installEvents();
  refresh().catch(error => {
    $('tracesDiagnosticRows').innerHTML = `<tr><td colspan="8">${esc(error.message || error)}</td></tr>`;
    toast(error.message || String(error));
  });
  setInterval(() => refresh({ includeRules: false, deferWhileScrolling: true }).catch(console.error), AUTO_REFRESH_MS);
}
