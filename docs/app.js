const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
let motionPaused = false;
function motionEnabled() { return !motionPreference.matches && !motionPaused; }
function enterContent(element) {
  if (!motionEnabled()) return;
  element.classList.remove('motion-enter');
  // These panels replace their children; the new nodes start fresh animations.
  element.classList.add('motion-enter');
}
const lensData = {
  cost: ['看得清', '先筛选和排序，再沿行动轨迹追查。', 'Trace 列表可按耗时、总 Token 或状态筛选，按耗时或总 Token 排序。进入调用树，可进一步筛选 Span，再回看执行过程。', '本次模型调用用量', '18.6', 'k Token', '输入、输出与缓存分别查看'],
  tools: ['看见执行', '请求发出去了，工具真的做到了吗？', '把模型的工具请求与实际执行连起来：参数、返回内容、耗时和错误，回到任务卡住的那一步。', '连续相同调用', '3', '次', '展开每次参数与返回内容'],
  rules: ['看见要求', '“修改之后运行测试”，它照做了吗？', '先看本次加载了哪些项目指令，再往下追踪实际动作。把要求和测试结果放在一起复查。', '本次加载的约定', '1', '份', '按目录与内容版本查看']
};
const stepData = {
  rules: ['项目指令', '先确认，你写的要求有没有被读到。', '示例项目的 AGENTS.md 写着“修改接口后，运行契约测试”。本次任务已加载，后续可以继续核对验证步骤。', '加载的指令文件', '1', '份', '查看适用目录与记录版本'],
  model: ['模型与上下文', '在它回答之前，先看它收到了什么。', '展开实际模型、系统指令、对话历史与工具返回，定位关键要求是否缺失、上下文是否被重复带入。', '模型调用用量', '18.6', 'k Token', 'GPT、Claude、DeepSeek、Kimi 等'],
  tool: ['工具请求与结果', '拿到的资料，是否足够完成这一步？', '展开工具名称、参数和返回内容。外部文档通过 MCP 服务访问；读取文件等本地工具也在同一条轨迹中。', '相同文件读取', '3', '次', '查看每次调用的完整记录'],
  retry: ['看见消耗', '重复的步骤，也可能推高消耗。', '重复读取、重试与上下文恢复，都可能继续消耗 Token。沿着轨迹找到它们，才知道先改哪里。', '本次模型调用用量', '18.6', 'k Token', '输入、输出与缓存分别查看'],
  test: ['验证与交付', '看到“完成了”，再看看测试结果。', '把具体测试执行、工具返回和最终回复关联起来。诊断提供复查线索，是否满足要求还要结合验收。', '验收需要', '你', '来判断', '证据、测试与业务目标一起看']
};
function renderDetail(d) {
  $('#lens-detail').innerHTML = `<div class="detail-main"><span class="detail-label">${d[0]}</span><h2>${d[1]}</h2><p>${d[2]}</p></div><div class="detail-readout"><span>${d[3]}</span><strong>${d[4]}<span>${d[5]}</span></strong><div class="mini-breakdown"><i></i><i></i><i></i></div><small>${d[6]}</small></div>`;
  enterContent($('#lens-detail'));
  window.AIVisionUI.translate($('#lens-detail'));
}
$$('[data-lens]').forEach(button => button.addEventListener('click', () => {
  $$('[data-lens]').forEach(b => { const active = b === button; b.setAttribute('aria-selected', String(active)); b.tabIndex = active ? 0 : -1; });
  $('.trace-canvas').dataset.mode = button.dataset.lens;
  $('#lens-detail').setAttribute('aria-labelledby', button.id);
  $$('.trace-step').forEach(b => b.classList.toggle('highlighted', button.dataset.lens === 'cost' && b.dataset.step === 'retry'));
  renderDetail(lensData[button.dataset.lens]);
}));
$$('[data-step]').forEach(button => button.addEventListener('click', () => {
  $$('.trace-step').forEach(b => b.classList.toggle('highlighted', b === button));
  renderDetail(stepData[button.dataset.step]);
}));
function openDialog(id) { const dialog = document.getElementById(id); window.AIVisionUI.translate(dialog); if (!dialog.open) dialog.showModal(); }
$$('[data-open]').forEach(button => button.addEventListener('click', () => openDialog(button.dataset.open)));
$$('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
$$('dialog').forEach(dialog => dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const r = dialog.getBoundingClientRect();
  if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
}));
$('[data-switch-deploy]').addEventListener('click', () => { $('#compat-dialog').close(); openDialog('deploy-dialog'); });
function toast(text) {
  $('#toast').textContent = text; $('#toast').classList.add('visible');
  window.AIVisionUI.translate($('#toast'));
  clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 3200);
}
async function copyPrompt(button, text) {
  try {
    if (typeof navigator.clipboard?.writeText === 'function') {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* Fall back when clipboard access is denied. */ }

  const focused = document.activeElement;
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
  (button.closest('dialog') || document.body).appendChild(area);
  try {
    area.focus({ preventScroll: true });
    if (document.activeElement !== area) return false;
    area.select();
    return typeof document.execCommand === 'function' && document.execCommand('copy') && wroteText;
  } catch { return false; }
  finally {
    area.remove();
    if (focused?.isConnected) focused.focus({ preventScroll: true });
  }
}
$$('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  const text = document.getElementById(button.dataset.copy)?.textContent;
  const copied = text != null && await copyPrompt(button, text);
  toast(copied ? '已复制。粘贴给你的 Coding Agent 即可。' : '请选中提示词，手动复制。');
}));
const featureData = {
  conversation: ["对话增强", "按轮次回看 Agent 看到和说过的内容。", "在根概览打开执行过程，按角色查看已采集的提问、模型消息和工具返回。", "<div class=\"feature-example\">从有来源记录的消息回到关联 Span，核对这一轮的输入和输出。</div>", "可见内容随接入方式和上报数据而变化。", "guides/trace-process", "查看执行过程指南"],
  mcp: ["MCP 增强", "查清调用了哪个服务和工具。", "在 Trace 中查看已采集的工具说明、参数、返回内容与耗时。", "<div class=\"feature-example\">在 MCP 总览查看各服务的工具调用，也可从 MCP 仓库搜索并导入配置。</div>", "可见内容随接入方式和上报数据而变化。", "guides/mcp-observability", "查看 MCP 观测与配置指南"],
  a2a: ['A2A · Agent 间协作', '交出去的任务，进行到哪了？', 'A2A 让不同 Agent 服务交换任务、进度和结果。Ant AI Vision 将请求交给谁、任务状态和返回内容放在一起，帮助定位任务交接的问题。', '<div class="feature-example">修改接口 → 发起复核 → 等待审查结果<br>对方正在处理，还是需要补充信息？</div>', '远端 Agent 也接入观测后，可继续查看它的内部步骤与用量；只有通信记录时，查看双方交换的内容。', 'https://a2a-protocol.org/', '了解 A2A'],
  skill: ["Skill 增强", "从根概览定位具体技能执行。", "点开 Skill 条目，查看已上报的技能描述、可用工具和运行条件。", "<div class=\"feature-example\">在 Skill 总览回看调用记录，也可以批量导入本地技能目录。</div>", "可见内容随接入方式和上报数据而变化。", "guides/skill-observability", "查看 Skill 观测与导入指南"],
  decision: ["决策增强", "查看选项、概率和实际选择。", "有决策上报时，可以查看模型路由或工具审批的结果，以及关联的后续调用。", "<div class=\"feature-example\">例如在模型路由决策中，对照候选模型与实际执行用到的模型。</div>", "可见内容随接入方式和上报数据而变化。", "guides/decision-traces", "查看决策观测指南"],
  costcase: ['一次真实使用记录', '问题拆得更细，为什么消耗更大了？', '一位开发者把约 70 个待修问题拆成任务，交给 Codex 夜间并行处理。第二天，额度耗尽了。回看过程，这批任务产生了 910 次接力轮次；同一份背景在多任务、多轮次中反复加载。', '<div class="feature-metrics"><div><strong>≈70</strong><span>待修问题</span></div><div><strong>910</strong><span>接力轮次</span></div></div><div class="feature-example">先定位反复发生的步骤，再尝试合并相关修复、明确完成标准，用一小批任务观察效果。</div>', '根据一次 Codex 使用记录改写，已隐去项目和人员信息。接力轮次不等于模型调用次数，不能直接推算账单或节省比例。', '', '']
};
$$('[data-feature]').forEach(button => button.addEventListener('click', () => {
  const f = featureData[button.dataset.feature];
  const guide = f[5];
  const docsPage = guide && !guide.startsWith('https://') ? guide : '';
  const guideLink = docsPage ? `<a data-docs-page="${docsPage}" href="https://aivision.mintlify.site/zh-CN/${docsPage}">${f[6]}</a>` : `<a href="${guide}">${f[6]}</a>`;
  $('#feature-content').innerHTML = `<span class="section-tag">${f[0]}</span><h2 id="feature-title">${f[1]}</h2><p>${f[2]}</p>${f[3]}<p class="fine">${f[4]}</p>${guide ? `<p>${guideLink}</p>` : ''}`;
  openDialog('feature-dialog');
}));
const cases = {
  repeat: ['↻', '同一份文件，连续读取了 3 次。', '工具返回内容相同，任务仍继续执行。', [['14:32:08', 'read_file', 'orders.py', '内容未变化'], ['14:32:11', 'read_file', 'orders.py', '内容未变化'], ['14:32:15', 'read_file', 'orders.py', '内容未变化']], '重复读取是否必要？同一段内容，是否再次进入模型上下文？'],
  failure: ['!', '工具连续返回认证失败。', '同一请求重试了多次，仍未取得订单状态。', [['14:40:02', 'get_order', '401 Unauthorized', '执行失败'], ['14:40:05', 'get_order', '401 Unauthorized', '执行失败'], ['14:40:09', 'get_order', '401 Unauthorized', '执行失败']], '凭据是否有效？修复配置后，再观察工具调用是否恢复。'],
  validation: ['✓', '修改后，未发现测试执行记录。', '接口已修改，但本次轨迹中没有对应的验证。', [['15:10:02', 'edit_file', 'response.py', '文件修改'], ['15:10:18', 'final_response', '修改完成', '任务结束'], ['验证记录', 'not_found', '本次采集范围', '需要复查']], '测试是在采集范围之外执行，还是确实遗漏？结合实际验收确认。']
};
$$('[data-case]').forEach(button => button.addEventListener('click', () => {
  $$('[data-case]').forEach(b => { const active = b === button; b.setAttribute('aria-selected', String(active)); b.tabIndex = active ? 0 : -1; });
  const d = cases[button.dataset.case];
  $('#case-panel').setAttribute('aria-labelledby', button.id);
  $('#case-panel').innerHTML = `<div class="finding"><span class="finding-mark">${d[0]}</span><div><h3>${d[1]}</h3><p>${d[2]}</p></div></div><div class="event-stream">${d[3].map(row => `<div><time>${row[0]}</time><span>${row[1]}</span><code>${row[2]}</code><b>${row[3]}</b></div>`).join('')}</div><div class="review-question"><span>下一步怎么查</span><p>${d[4]}</p></div>`;
  enterContent($('#case-panel'));
  window.AIVisionUI.translate($('#case-panel'));
}));
const connectData = {
  app: `<div class="connect-message"><h3>给日常使用的 Agent，<br>加上观测。</h3><p>给 Codex、Claude Code 等已支持的应用安装采集插件，按接入指引启用后，就能回看任务轨迹。</p><button class="button primary" data-deploy>先部署 Ant AI Vision</button></div><ol class="connect-steps"><li><span>1</span><div><b>选应用</b><p><a data-docs-page="integrations/agents" href="https://aivision.mintlify.site/zh-CN/integrations/agents">查看支持的应用与安装方式</a></p></div></li><li><span>2</span><div><b>装插件</b><p>生成并执行安装命令，按提示启用采集。</p></div></li><li><span>3</span><div><b>看任务</b><p>完成一次熟悉的任务，回来查看轨迹。</p></div></li></ol>`,
  framework: `<div class="connect-message"><h3>自己的智能体，<br>也能打开过程。</h3><p>用 LangChain、Google ADK 等框架开发的应用，按现有采集方案选择接入。从测试请求开始，把用户反馈对应到具体轨迹。</p><button class="button primary" data-deploy>先部署 Ant AI Vision</button></div><div class="framework-paths"><div><h4>Langfuse SDK</h4><p>沿用 SDK、Callback 和插桩，配置上报地址与凭据。</p></div><div><h4>NVIDIA NeMo Relay</h4><p>采集框架运行数据，配置导出地址与凭据，再发送到 Ant AI Vision。</p></div><a data-docs-page="integrations/frameworks" href="https://aivision.mintlify.site/zh-CN/integrations/frameworks">查看框架接入示例</a></div>`,
  langfuse: `<div class="connect-message"><h3>埋点已经做了，<br>换个接入地址就好。</h3><p>保留 Langfuse SDK、Callback 和插桩代码。切换 Server Endpoint 与接入凭据，无需修改业务代码或重新埋点。</p><button class="button primary" data-compat>了解兼容范围</button></div><div class="env-block"><pre>LANGFUSE_BASE_URL=&lt;接入地址&gt;\nLANGFUSE_PUBLIC_KEY=&lt;Public Key&gt;\nLANGFUSE_SECRET_KEY=&lt;Secret Key&gt;</pre><p>加载新配置，发起一次请求，查看第一条轨迹。</p></div>`
};
$$('[data-connect]').forEach(button => button.addEventListener('click', () => {
  $$('[data-connect]').forEach(b => { const active = b === button; b.setAttribute('aria-selected', String(active)); b.tabIndex = active ? 0 : -1; });
  $('#connect-panel').setAttribute('aria-labelledby', button.id);
  $('#connect-panel').innerHTML = connectData[button.dataset.connect];
  enterContent($('#connect-panel'));
  window.AIVisionUI.translate($('#connect-panel'));
  $('[data-deploy]', $('#connect-panel'))?.addEventListener('click', () => openDialog('deploy-dialog'));
  $('[data-compat]', $('#connect-panel'))?.addEventListener('click', () => openDialog('compat-dialog'));
}));
$$('[role="tablist"]').forEach(list => list.addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
  const tabs = $$('[role="tab"]', list); let i = tabs.indexOf(document.activeElement); if (i < 0) return;
  event.preventDefault();
  if (event.key === 'Home') i = 0; else if (event.key === 'End') i = tabs.length - 1;
  else i = (i + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) + tabs.length) % tabs.length;
  tabs[i].click(); tabs[i].focus();
}));
let playbackTimer = null;
let playbackFrame = null;
function stopPlayback() {
  clearTimeout(playbackTimer); cancelAnimationFrame(playbackFrame);
  playbackTimer = null; playbackFrame = null;
  $('.trace-canvas').classList.remove('is-playing');
  $$('.trace-step').forEach(button => button.classList.remove('is-current'));
  $('#replay-task').setAttribute('aria-pressed', 'false'); $('#replay-task').textContent = '回放任务';
  window.AIVisionUI.translate($('#replay-task'));
  $('#flow-signal').setAttribute('opacity', '0');
}
function playStep(index) {
  const steps = $$('.trace-step'); const button = steps[index];
  steps.forEach(b => { b.classList.toggle('highlighted', b === button); b.classList.toggle('is-current', b === button); });
  renderDetail(stepData[button.dataset.step]);
  $('#flow-signal').setAttribute('opacity', index === 4 || !motionEnabled() ? '0' : '1');
  if (index === 4) { playbackTimer = setTimeout(stopPlayback, 700); return; }
  const start = performance.now(); const from = 112 + index * 224;
  const animate = now => {
    const progress = Math.min((now - start) / 850, 1);
    $('#flow-signal').setAttribute('cx', String(from + progress * 224));
    if (progress < 1) playbackFrame = requestAnimationFrame(animate);
  };
  if (motionEnabled()) playbackFrame = requestAnimationFrame(animate);
  playbackTimer = setTimeout(() => playStep(index + 1), 1050);
}
$('#replay-task').addEventListener('click', () => {
  if ($('#replay-task').getAttribute('aria-pressed') === 'true') { stopPlayback(); return; }
  $('#lens-cost').click();
  $('.trace-canvas').classList.add('is-playing');
  $('#replay-task').setAttribute('aria-pressed', 'true'); $('#replay-task').textContent = '暂停回放';
  window.AIVisionUI.translate($('#replay-task'));
  playStep(0);
});
$$('[data-lens], [data-step]').forEach(button => button.addEventListener('click', stopPlayback));

const motionToggle = $('#motion-toggle');
function syncMotion() {
  document.documentElement.dataset.motion = motionEnabled() ? 'on' : 'off';
  motionToggle.hidden = motionPreference.matches;
  motionToggle.setAttribute('aria-pressed', String(motionPaused));
  const label = motionPaused ? '开启页面动效' : '暂停页面动效';
  motionToggle.setAttribute('aria-label', label); motionToggle.title = label;
  $('span', motionToggle).textContent = motionPaused ? '▶' : 'Ⅱ';
  window.AIVisionUI.translate(motionToggle);
}
motionToggle.addEventListener('click', () => { motionPaused = !motionPaused; stopPlayback(); syncMotion(); });
motionPreference.addEventListener('change', () => { stopPlayback(); syncMotion(); });
syncMotion();
document.documentElement.classList.add('motion-ready');

if ('IntersectionObserver' in window) {
  // Evidence visuals and loop accents reveal once; reading content stays visible.
  const revealObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('motion-revealed');
      revealObserver.unobserve(entry.target);
    });
  }, { threshold: 0.18 });
  $$('.evidence-explorer, .usage-bar, .diagnosis-loop, .footer-signoff').forEach(element => { element.classList.add('motion-watch'); revealObserver.observe(element); });
  const traceObserver = new IntersectionObserver(entries => {
    const visible = entries[0].isIntersecting;
    $('.trace-canvas').classList.toggle('motion-in-view', visible);
    if (!visible) stopPlayback();
  }, { threshold: 0.08 });
  traceObserver.observe($('.trace-canvas'));
}
document.addEventListener('visibilitychange', () => {
  document.documentElement.classList.toggle('motion-suspended', document.hidden);
  if (document.hidden) stopPlayback();
});
document.documentElement.classList.toggle('motion-suspended', document.hidden);
document.addEventListener('aivision:languagechange', () => { stopPlayback(); syncMotion(); });
