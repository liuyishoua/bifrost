(() => {
  const toast = document.querySelector('[data-toast]');
  if (!toast) return;
  const url = new URL(location.href);
  url.searchParams.delete('message');
  url.searchParams.delete('changed');
  history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  const close = () => { toast.hidden = true; };
  toast.querySelector('.feedback-close')?.addEventListener('click', close);
  let timer;
  const schedule = () => { clearTimeout(timer); timer = setTimeout(close, 4500); };
  toast.addEventListener('pointerenter', () => clearTimeout(timer));
  toast.addEventListener('focusin', () => clearTimeout(timer));
  toast.addEventListener('pointerleave', schedule);
  toast.addEventListener('focusout', schedule);
  window.addEventListener('pagehide', close);
  schedule();
})();
