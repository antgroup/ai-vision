// dashboard-render.js — finding tree, signal panels, overview, diagnostics, and refresh loop.

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
