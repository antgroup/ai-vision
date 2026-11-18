// dashboard-utils.js — pure helpers, data accessors, payload analysis, and rule classification.

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
