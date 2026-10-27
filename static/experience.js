(() => {
  const baseline = JSON.parse(document.querySelector('#portal-session-state')?.textContent || '{}');
  let checking = false;
  let lastCheck = Date.now();
  async function refreshIdentity(force = false) {
    if (!baseline.authenticated || checking || document.hidden || !force && Date.now() - lastCheck < 5000) return;
    checking = true;
    lastCheck = Date.now();
    try {
      const response = await fetch('/api/session-state', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
      if (response.status === 401) { location.reload(); return; }
      if (response.ok && (await response.json()).stamp !== baseline.stamp) location.reload();
    } catch {} finally { checking = false; }
  }
  window.addEventListener('focus', () => refreshIdentity());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshIdentity(); });
  window.addEventListener('pageshow', event => { if (event.persisted) refreshIdentity(true); });

  for (const button of document.querySelectorAll('[data-password-toggle]')) {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.getAttribute('aria-controls'));
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.textContent = visible ? '隐藏' : '显示';
      button.setAttribute('aria-pressed', String(visible));
    });
  }
  const menu = document.querySelector('.account-menu');
  if (menu) {
    document.addEventListener('click', event => { if (!menu.contains(event.target)) menu.open = false; });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && menu.open) { menu.open = false; menu.querySelector('summary').focus(); } });
  }
  window.addEventListener('pageshow', () => { for (const button of document.querySelectorAll('[data-submit-label]')) { button.disabled = false; button.textContent = button.dataset.submitLabel; delete button.dataset.submitLabel; } });
  for (const form of document.querySelectorAll('form[method=post]')) {
    form.addEventListener('submit', () => {
      if (!form.checkValidity()) return;
      // Keep name/value-bearing submit buttons enabled so approval decisions are submitted.
      const button = [...form.querySelectorAll('button[type=submit],button:not([type])')].find(button => !button.name);
      if (button) { button.dataset.submitLabel = button.textContent; button.disabled = true; button.textContent = '正在保存…'; }
    });
  }
})();
