(() => {
  const directory = document.querySelector('[data-admin-directory]');
  if (!directory) return;
  const tabs = [...directory.querySelectorAll('[data-admin-tab]')];
  const panels = [...directory.querySelectorAll('[data-admin-panel]')];
  const refresh = directory.querySelector('[data-admin-refresh]');
  const status = directory.querySelector('[data-admin-status]');
  const names = new Set(tabs.map(tab => tab.dataset.adminTab));
  let active = null;

  function destination(tab) {
    const url = new URL(location.href);
    url.searchParams.delete('message');
    url.searchParams.delete('changed');
    if (tab === 'users') url.searchParams.delete('tab');
    else url.searchParams.set('tab', tab);
    return url.pathname + url.search + url.hash;
  }

  function show(tab, push = false) {
    if (!names.has(tab)) tab = 'users';
    const changed = active !== tab;
    if (changed && active === 'users') {
      for (const dialog of directory.querySelectorAll('dialog[open]')) dialog.close();
    }
    for (const panel of panels) panel.hidden = panel.dataset.adminPanel !== tab;
    for (const link of tabs) {
      const selected = link.dataset.adminTab === tab;
      if (selected) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
      link.href = destination(link.dataset.adminTab);
      if (selected && changed) status.textContent = link.textContent.trim();
    }
    active = tab;
    refresh.href = destination(tab);
    if (changed) document.dispatchEvent(new CustomEvent('bifrost:admin-tab-change', {detail: {tab}}));
    const next = destination(tab);
    if (push && next !== location.pathname + location.search + location.hash) {
      history.pushState(history.state, '', next);
    }
  }

  for (const link of tabs) link.addEventListener('click', event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    show(link.dataset.adminTab, true);
  });
  const restore = () => show(new URLSearchParams(location.search).get('tab'));
  window.addEventListener('popstate', restore);
  restore();
})();
