// dashboard-app.js — modals, API mutations, navigation, event handlers, and initialization.

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
function promptEngActiveTab() {
  const active = document.querySelector('.prompt-eng-tabs .tab.active');
  return active ? active.dataset.promptTab : 'bootstrap';
}

function promptEngCopyText() {
  const tab = promptEngActiveTab();
  if (tab === 'loop') return aiFixLoopPromptText();
  if (tab === 'golden') {
    const steps = goldenReplaySteps();
    return steps.map((s, i) => `=== 第 ${i + 1} 步: ${s.title} ===\n${s.prompt}\n(${s.notes})`).join('\n\n');
  }
  if (tab === 'superloop') return aiSuperloopPromptText();
  return aiBootstrapPromptText();
}

function renderPromptEngPreview() {
  renderBootstrapPrompt();
  renderLoopPrompt();
  renderGoldenReplayPrompt();
  renderSuperloopPrompt();
}

function setPromptEngTab(tabName) {
  document.querySelectorAll('.prompt-eng-tabs .tab').forEach(t => t.classList.toggle('active', t.dataset.promptTab === tabName));
  document.querySelectorAll('.prompt-tab-panel').forEach(p => p.classList.toggle('active', p.id === 'prompt-panel-' + tabName));
}

function openPromptEngineeringModal() {
  renderPromptEngPreview();
  $('promptEngineeringBackdrop').classList.add('open');
}

function closePromptEngineeringModal() {
  $('promptEngineeringBackdrop').classList.remove('open');
}
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
  $('openPromptEngineeringBtn').addEventListener('click', openPromptEngineeringModal);
  $('closePromptEngineeringBtn').addEventListener('click', closePromptEngineeringModal);
  $('copyPromptEngineeringBtn').addEventListener('click', () => copyText(promptEngCopyText()));
  document.querySelectorAll('.prompt-eng-tabs .tab').forEach(tab => {
    tab.addEventListener('click', () => { setPromptEngTab(tab.dataset.promptTab); });
  });
  $('bootstrapLevelSelect').addEventListener('change', renderBootstrapPrompt);
  $('bootstrapConfirmSelect').addEventListener('change', renderBootstrapPrompt);
  LOOP_SIGNAL_ORDER.forEach(signal => {
    $('bootstrapSignal' + cap(signal)).addEventListener('change', renderBootstrapPrompt);
    $('goldenSignal' + cap(signal))?.addEventListener('change', renderGoldenReplayPrompt);
    $('superloopSignal' + cap(signal))?.addEventListener('change', renderSuperloopPrompt);
  });
  if ($('superloopAgentSelect')) $('superloopAgentSelect').addEventListener('change', renderSuperloopPrompt);

  $('loopFixLevelSelect').addEventListener('change', renderLoopPrompt);
  $('loopConfirmSelect').addEventListener('change', renderLoopPrompt);
  $('loopReportModeSelect').addEventListener('change', renderLoopPrompt);
  $('loopReportInput').addEventListener('input', renderLoopPrompt);
  $('loopIncludeSemanticDoc')?.addEventListener('change', renderLoopPrompt);
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
  ['findingBackdrop', 'payloadBackdrop', 'rulesBackdrop', 'promptEngineeringBackdrop', 'apiDocsBackdrop', 'guideBackdrop', 'confirmBackdrop'].forEach(id => $(id).addEventListener('click', event => {
    if (event.target !== $(id)) return;
    if (id === 'findingBackdrop') closeFinding();
    if (id === 'payloadBackdrop') closePayload();
    if (id === 'rulesBackdrop') closeRulesModal();
    if (id === 'promptEngineeringBackdrop') closePromptEngineeringModal();
    if (id === 'apiDocsBackdrop') closeApiDocsModal();
    if (id === 'guideBackdrop') closeGuideModal();
    if (id === 'confirmBackdrop') closeConfirm(false);
  }));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { closeFinding(); closePayload(); closeRulesModal(); closePromptEngineeringModal(); closeApiDocsModal(); closeGuideModal(); closeConfirm(false); }
  });
}

function dashboardTestHooks() {
  return {
    aiSuperloopPromptText,
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
