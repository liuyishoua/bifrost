(() => {
  'use strict';
  const rows = [...document.querySelectorAll('.user-row')];
  const search = document.querySelector('#user-search');
  search.addEventListener('input', () => {
    const query = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const row of rows) {
      row.hidden = !row.dataset.search.includes(query);
      if (!row.hidden) visible++;
    }
    document.querySelector('#user-list-empty').hidden = visible !== 0;
    document.querySelector('#user-list-count').textContent = query
      ? `找到 ${visible} 位用户，共 ${rows.length} 位` : `共 ${rows.length} 位用户`;
  });

  for (const form of document.querySelectorAll('[data-identity-form]')) {
    const controls = [...form.querySelectorAll('select')];
    const original = controls.map(control => control.value);
    form.addEventListener('change', () => {
      form.querySelector('.row-save').disabled = controls.every((control, i) => control.value === original[i]);
    });
  }

  const permissionDialog = document.querySelector('#permissions-dialog');
  const permissionForm = document.querySelector('#permissions-form');
  const choices = [...permissionForm.querySelectorAll('[data-app-choice]')];
  const updateCount = () => {
    document.querySelector('#permission-selection-count').textContent = `已选 ${choices.filter(box => box.checked).length} / ${choices.length} 个应用`;
  };
  permissionForm.addEventListener('change', updateCount);
  document.querySelector('#permissions-all').addEventListener('click', () => {
    for (const box of choices) if (!box.disabled) box.checked = true;
    updateCount();
  });
  document.querySelector('#permissions-none').addEventListener('click', () => {
    for (const box of choices) if (!box.disabled) box.checked = false;
    updateCount();
  });

  const deleteDialog = document.querySelector('#delete-dialog');
  const deleteForm = document.querySelector('#delete-form');
  for (const button of document.querySelectorAll('[data-permissions]')) {
    button.addEventListener('click', () => {
      const member = JSON.parse(button.dataset.member);
      const admin = member.role === 'admin';
      permissionForm.reset();
      permissionForm.action = `/admin/accounts/${member.id}`;
      permissionForm.elements.revision.value = member.revision;
      document.querySelector('#permissions-user').textContent = `${member.username} · ${admin ? '管理员' : '普通用户'}`;
      document.querySelector('#permissions-title').textContent = `${member.username} 的应用权限`;
      for (const box of choices) {
        box.checked = admin || member.apps.includes(box.value);
        box.disabled = admin;
      }
      document.querySelector('#permissions-admin-note').hidden = !admin;
      document.querySelector('#permission-selection-tools').hidden = admin;
      document.querySelector('#permissions-save').hidden = admin;
      updateCount();
      permissionDialog.showModal();
    });
  }
  for (const button of document.querySelectorAll('[data-delete]')) {
    button.addEventListener('click', () => {
      const member = JSON.parse(button.dataset.member);
      deleteForm.reset();
      deleteForm.action = `/admin/accounts/${member.id}/delete`;
      deleteForm.elements.revision.value = member.revision;
      deleteForm.elements.username.value = member.username;
      document.querySelector('#delete-username').textContent = member.username;
      deleteDialog.showModal();
    });
  }
  const credentialsDialog = document.querySelector('#credentials-dialog');
  const credentialsForm = document.querySelector('#credentials-form');
  for (const button of document.querySelectorAll('[data-credentials]')) {
    button.addEventListener('click', () => {
      const member = JSON.parse(button.dataset.member);
      credentialsForm.reset();
      credentialsForm.querySelector('[name=new_password]').type = 'password';
      const toggle = credentialsForm.querySelector('[data-password-toggle]');
      toggle.textContent = '显示';
      toggle.setAttribute('aria-pressed', 'false');
      credentialsForm.action = `/admin/accounts/${member.id}/credentials`;
      credentialsForm.elements.revision.value = member.revision;
      credentialsForm.elements.username.value = member.username;
      document.querySelector('#credentials-user').textContent = `正在编辑 ${member.username}`;
      credentialsDialog.showModal();
    });
  }
  for (const dialog of [permissionDialog, deleteDialog, credentialsDialog]) {
    for (const button of dialog.querySelectorAll('[data-close]')) {
      button.addEventListener('click', () => dialog.close());
    }
    dialog.addEventListener('close', () => {
      for (const input of dialog.querySelectorAll('input[type=password], [name=new_password]')) { input.value = ''; input.type = 'password'; }
      document.documentElement.classList.toggle('account-modal-open', !!document.querySelector('dialog[open]'));
    });
    // Prevent background scrolling on narrow screens while keeping the form scrollable.
    dialog.addEventListener('beforetoggle', event => {
      if (event.newState === 'open') document.documentElement.classList.add('account-modal-open');
    });
  }
})();

(() => {
  const slots = [...document.querySelectorAll('[data-password-cell]')];
  const timers = new WeakMap();
  const generations = new WeakMap();
  function hide(slot) {
    const button = slot.querySelector('[data-reveal-password]');
    if (!button || button.disabled && !button.dataset.pending) return;
    generations.set(slot, (generations.get(slot) || 0) + 1);
    clearTimeout(timers.get(slot));
    slot.querySelector('[data-saved-password]').textContent = '••••••';
    button.textContent = '显示';
    button.setAttribute('aria-pressed', 'false');
    if (button.dataset.pending) { button.disabled = false; delete button.dataset.pending; }
  }
  for (const slot of slots) {
    const button = slot.querySelector('[data-reveal-password]');
    if (button.disabled) continue;
    const output = slot.querySelector('[data-saved-password]');
    const error = slot.querySelector('[data-password-error]');
    button.addEventListener('click', async () => {
      if (button.getAttribute('aria-pressed') === 'true') { hide(slot); return; }
      error.hidden = true;
      button.textContent = '读取中…';
      button.disabled = true;
      button.dataset.pending = '1';
      const generation = (generations.get(slot) || 0) + 1;
      generations.set(slot, generation);
      try {
        const body = new URLSearchParams({csrf: document.querySelector('input[name=csrf]').value, revision: button.dataset.revision});
        const response = await fetch(`/admin/accounts/${button.dataset.user}/password/reveal`, {method: 'POST', body, credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
        if (generations.get(slot) !== generation || document.hidden) return;
        if (response.redirected || response.status === 401) { location.reload(); return; }
        const data = await response.json();
        if (!response.ok) {
          if (response.status === 409 && slot.querySelector('.password-sync-hint')) {
            const name = slot.closest('.user-row').querySelector('h3').textContent;
            throw new Error(`请用 ${name} 账号重新登录，或通过“编辑账号”重设该账号密码。`);
          }
          throw new Error(typeof data.detail === 'string' ? data.detail : '暂时无法查看，请稍后再试。');
        }
        if (typeof data.password !== 'string') throw new Error('密码读取失败，请重试。');
        output.textContent = data.password;
        button.textContent = '隐藏';
        button.setAttribute('aria-pressed', 'true');
        slot.querySelector('.password-sync-hint')?.remove();
        timers.set(slot, setTimeout(() => hide(slot), 30000));
      } catch (failure) {
        if (generations.get(slot) !== generation) return;
        output.textContent = '••••••';
        button.textContent = '显示';
        error.textContent = failure instanceof SyntaxError ? '登录或页面已更新，请刷新后再试。' : failure.message;
        error.hidden = false;
      } finally {
        if (generations.get(slot) === generation) { button.disabled = false; delete button.dataset.pending; }
      }
    });
  }
  const hideAll = () => { for (const slot of slots) hide(slot); };
  document.addEventListener('bifrost:admin-tab-change', hideAll);
  window.addEventListener('blur', hideAll);
  window.addEventListener('pagehide', hideAll);
  document.addEventListener('visibilitychange', () => { if (document.hidden) hideAll(); });
  document.addEventListener('click', event => { if (event.target.closest('[data-delete],[data-credentials]')) hideAll(); });
})();
