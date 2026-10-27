(() => {
  const directory = document.querySelector('[data-permissions-directory]');
  if (!directory) return;
  const filters = [...directory.querySelectorAll('[data-permission-filter]')];
  const cards = [...directory.querySelectorAll('[data-access-id]')];
  const form = directory.querySelector('.access-search');
  const query = form.elements.q;
  const empty = directory.querySelector('[data-permission-empty]');
  const reset = directory.querySelector('[data-permission-reset]');
  const refresh = directory.querySelector('[data-permission-refresh]');
  const status = directory.querySelector('[data-permission-status]');
  const states = new Set(filters.map(link => link.dataset.permissionFilter));
  let state = 'all';
  let typing = false;
  let composing = false;

  function destination(nextState = state) {
    const url = new URL(location.href);
    url.searchParams.delete('message');
    url.searchParams.delete('changed');
    if (nextState === 'all') url.searchParams.delete('state');
    else url.searchParams.set('state', nextState);
    const value = query.value.trim();
    if (value) url.searchParams.set('q', value);
    else url.searchParams.delete('q');
    return url.pathname + url.search + url.hash;
  }

  function render(historyMode) {
    const term = query.value.trim().toLowerCase();
    let count = 0;
    for (const card of cards) {
      const matchesState = state === 'all' || (state === 'requestable'
        ? card.dataset.permissionRequestable === 'true'
        : card.dataset.permissionState === state);
      card.hidden = !(matchesState && card.dataset.permissionSearch.includes(term));
      if (!card.hidden) count++;
    }
    for (const link of filters) {
      const selected = link.dataset.permissionFilter === state;
      if (selected) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
      link.href = destination(link.dataset.permissionFilter);
    }
    form.elements.state.value = state;
    refresh.href = destination();
    empty.hidden = count > 0;
    const noPending = state === 'pending' && !term;
    empty.querySelector('h2').textContent = noPending ? '暂时没有审批中的申请' : '没有匹配的应用';
    empty.querySelector('p').textContent = noPending ? '需要其他应用时，可切换到“可申请”。' : '试试其他关键词，或查看全部应用。';
    const summary = `显示 ${count} 个应用`;
    if (status.textContent !== summary) status.textContent = summary;
    const next = destination();
    if (historyMode && next !== location.pathname + location.search + location.hash) {
      history[historyMode](history.state, '', next);
    }
  }

  function restore() {
    const params = new URLSearchParams(location.search);
    state = states.has(params.get('state')) ? params.get('state') : 'all';
    query.value = params.get('q') || '';
    typing = false;
    render();
  }

  const ordinaryClick = event => event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  for (const link of filters) link.addEventListener('click', event => {
    if (!ordinaryClick(event)) return;
    event.preventDefault();
    typing = false;
    state = link.dataset.permissionFilter;
    render('pushState');
  });
  function search() {
    if (composing) return;
    render(typing ? 'replaceState' : 'pushState');
    typing = true;
  }
  query.addEventListener('input', event => { if (!event.isComposing) search(); });
  query.addEventListener('compositionstart', () => { composing = true; });
  query.addEventListener('compositionend', () => { composing = false; search(); });
  query.addEventListener('blur', () => { typing = false; });
  form.addEventListener('submit', event => { event.preventDefault(); render('pushState'); typing = false; });
  reset.addEventListener('click', event => {
    if (!ordinaryClick(event)) return;
    event.preventDefault();
    state = 'all'; query.value = ''; typing = false;
    render('pushState');
  });
  window.addEventListener('popstate', restore);
  restore();
})();
