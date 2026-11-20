(() => {
  const languages = ['zh-CN', 'en', 'ja'];
  const themes = ['light', 'dark', 'system'];
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const saved = (key, choices, fallback) => {
    try { const value = localStorage.getItem(key); return choices.includes(value) ? value : fallback; }
    catch { return fallback; }
  };
  const remember = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  let language = saved('aivision.language', languages, 'zh-CN');
  let theme = saved('aivision.theme', themes, 'system');
  const textSources = new WeakMap();
  const attributeSources = new WeakMap();
  const root = document.documentElement;

  function applyTheme() {
    const resolved = theme === 'system' ? (systemTheme.matches ? 'dark' : 'light') : theme;
    root.dataset.theme = resolved;
    root.dataset.themeMode = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#11131c' : '#7355ec');
  }
  // Apply saved settings before the stylesheet paints the page.
  root.lang = language;
  applyTheme();
  systemTheme.addEventListener('change', () => { if (theme === 'system') applyTheme(); });

  function translated(source) {
    const key = source.trim();
    const value = window.AIVisionTranslations?.[language]?.[key];
    if (typeof value !== 'string') return source;
    const leading = source.match(/^\s*/)[0];
    const trailing = source.match(/\s*$/)[0];
    return leading + value + trailing;
  }
  function translate(scope = document) {
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.parentElement?.closest('script, style, [data-no-i18n]')) continue;
      let record = textSources.get(node);
      // App interactions can replace a node's text without replacing the node.
      if (!record || node.nodeValue !== record.rendered) record = { source: node.nodeValue };
      record.rendered = translated(record.source);
      node.nodeValue = record.rendered;
      textSources.set(node, record);
    }
    const elements = [...scope.querySelectorAll('[aria-label], [title], [alt], [placeholder], meta[name="description"]')];
    if (scope instanceof Element) elements.unshift(scope);
    elements.forEach(element => {
      if (element.closest('[data-no-i18n]')) return;
      let records = attributeSources.get(element);
      if (!records) { records = new Map(); attributeSources.set(element, records); }
      ['aria-label', 'title', 'alt', 'placeholder', ...(element.matches('meta[name="description"]') ? ['content'] : [])].forEach(attribute => {
        const current = element.getAttribute(attribute);
        if (current === null) return;
        let record = records.get(attribute);
        if (!record || current !== record.rendered) record = { source: current };
        record.rendered = translated(record.source);
        element.setAttribute(attribute, record.rendered);
        records.set(attribute, record);
      });
    });
    const configuredDocsUrl = document.querySelector('meta[name="aivision-docs-base-url"]')?.content.trim();
    const localPreview = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
    const docsBaseUrl = configuredDocsUrl || (localPreview ? 'http://localhost:3333' : 'https://aivision.mintlify.site');
    scope.querySelectorAll('[data-docs-page], [data-deployment-guide]').forEach(link => {
      const page = link.dataset.docsPage || `deployment/${link.dataset.deploymentGuide.replace(/\.md$/, '')}`;
      link.href = `${docsBaseUrl.replace(/\/+$/, '')}/${language}/${page}`;
    });
  }
  function setLanguage(value) {
    if (!languages.includes(value)) return;
    language = value; root.lang = language;
    remember('aivision.language', language);
    renderPreferences();
    document.dispatchEvent(new CustomEvent('aivision:languagechange'));
    translate();
  }
  function setTheme(value) {
    if (!themes.includes(value)) return;
    theme = value;
    remember('aivision.theme', theme);
    applyTheme();
    renderPreferences();
  }
  function renderPreferences() {
    const languageNames = { 'zh-CN': '中文', en: 'English', ja: '日本語' };
    const themeNames = { light: '浅色', dark: '深色', system: '跟随系统' };
    const languageTrigger = document.getElementById('language-trigger');
    const themeTrigger = document.getElementById('theme-trigger');
    document.getElementById('language-label').textContent = languageNames[language];
    languageTrigger.setAttribute('aria-label', `${translated('网站语言')}: ${languageNames[language]}`);
    const themeLabel = `${translated('外观主题')}: ${translated(themeNames[theme])}`;
    themeTrigger.setAttribute('aria-label', themeLabel);
    themeTrigger.title = themeLabel;
    document.querySelectorAll('[data-language]').forEach(item => item.setAttribute('aria-checked', String(item.dataset.language === language)));
    document.querySelectorAll('[data-theme-choice]').forEach(item => item.setAttribute('aria-checked', String(item.dataset.themeChoice === theme)));
    const icon = document.querySelector(`[data-theme-choice="${theme}"] svg`).cloneNode(true);
    document.getElementById('theme-icon').replaceChildren(icon);
  }
  window.AIVisionUI = { translate };
  document.addEventListener('DOMContentLoaded', () => {
    const controls = document.querySelector('.preference-controls');
    let activePicker = null;
    let search = '';
    let searchTimer;
    function closePicker(restoreFocus = false) {
      if (!activePicker) return;
      const picker = activePicker;
      activePicker = null;
      picker.querySelector('[role="menu"]').hidden = true;
      picker.querySelector('.preference-trigger').setAttribute('aria-expanded', 'false');
      search = ''; clearTimeout(searchTimer);
      if (restoreFocus) picker.querySelector('.preference-trigger').focus();
    }
    function openPicker(picker, edge) {
      closePicker();
      activePicker = picker;
      const menu = picker.querySelector('[role="menu"]');
      menu.hidden = false;
      picker.querySelector('.preference-trigger').setAttribute('aria-expanded', 'true');
      const items = [...menu.querySelectorAll('[role="menuitemradio"]')];
      const target = edge === 'first' ? items[0] : edge === 'last' ? items.at(-1) : menu.querySelector('[aria-checked="true"]');
      target.focus();
    }
    controls.querySelectorAll('.preference-picker').forEach(picker => {
      const trigger = picker.querySelector('.preference-trigger');
      const menu = picker.querySelector('[role="menu"]');
      trigger.addEventListener('click', () => activePicker === picker ? closePicker(true) : openPicker(picker));
      trigger.addEventListener('keydown', event => {
        if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
        event.preventDefault();
        openPicker(picker, event.key === 'ArrowDown' ? 'first' : 'last');
      });
      menu.querySelectorAll('[role="menuitemradio"]').forEach(item => item.addEventListener('click', () => {
        if (item.dataset.language) setLanguage(item.dataset.language);
        if (item.dataset.themeChoice) setTheme(item.dataset.themeChoice);
        closePicker(true);
      }));
      menu.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closePicker(true); return; }
        // Return to the trigger before native Tab navigation continues.
        if (event.key === 'Tab') { closePicker(true); return; }
        const items = [...menu.querySelectorAll('[role="menuitemradio"]')];
        const index = items.indexOf(document.activeElement);
        let next;
        if (event.key === 'ArrowDown') next = (index + 1) % items.length;
        if (event.key === 'ArrowUp') next = (index + items.length - 1) % items.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = items.length - 1;
        if (next !== undefined) { event.preventDefault(); items[next].focus(); return; }
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key !== ' ') {
          search += event.key.toLowerCase();
          clearTimeout(searchTimer); searchTimer = setTimeout(() => { search = ''; }, 500);
          const match = items.find(item => item.textContent.trim().toLowerCase().startsWith(search));
          if (match) { event.preventDefault(); match.focus(); }
        }
      });
    });
    document.addEventListener('pointerdown', event => { if (!activePicker?.contains(event.target)) closePicker(); });
    document.addEventListener('focusin', event => { if (!activePicker?.contains(event.target)) closePicker(); });
    renderPreferences();
    translate();
  });
})();
