(async () => {
  const status = document.querySelector('#status');
  const cancel = document.querySelector('#cancel');
  const aid = new URLSearchParams(location.search).get('account');
  let context, popup, checkClosed, finished = false;
  const prefix = new URL(document.baseURI).pathname.replace(/\/$/, '');
  const endpoint = `${prefix}/api/accounts/${encodeURIComponent(aid)}/verification`;
  async function finish(success, reason = '') {
    if (!context || finished) return;
    finished = true;
    cancel.disabled = true;
    clearInterval(checkClosed);
    if (popup && !popup.closed) popup.close();
    try {
      const response = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Request': '1'},
        body: JSON.stringify({id: context.id, success, reason})});
      if (!response.ok) throw new Error();
      status.textContent = success ? '验证步骤已完成，后台正在继续登录。请返回账号页面查看最终结果。' :
        reason ? '官方验证未完成。请返回账号页面查看具体失败阶段。' : '已取消本次验证。';
    } catch {
      status.textContent = '验证会话已结束、过期或网络异常，请返回账号页面查看状态。';
    }
  }
  cancel.onclick = () => finish(false);
  try {
    const response = await fetch(endpoint, {cache: 'no-store'});
    if (!response.ok) throw new Error();
    context = await response.json();
    const componentURL = new URL('/verification-frame', location.href);
    const configuredOrigin = document.querySelector('meta[name=verification-origin]')?.content;
    if (configuredOrigin) {
      const target = new URL(configuredOrigin);
      componentURL.protocol = target.protocol;
      componentURL.host = target.host;
    } else {
      componentURL.hostname = 'verification.localhost';
    }
    window.addEventListener('message', event => {
      if (finished || !popup || event.source !== popup || event.origin !== componentURL.origin) return;
      if (event.data?.type === 'verification-ready') {
        popup.postMessage({type: 'verification-start', ...context}, componentURL.origin);
      } else if (event.data?.type === 'verification-result' && event.data.id === context.id) {
        finish(event.data.success === true, event.data.reason);
      }
    });
    const open = document.createElement('button');
    open.textContent = '打开官方身份验证窗口';
    open.onclick = () => {
      if (finished) return;
      if (popup && !popup.closed) { popup.focus(); return; }
      popup = window.open(componentURL.href, `douyin_verify_${context.id}`, 'popup,width=520,height=760');
      status.textContent = popup ? '请在新窗口完成官方身份验证（最多等待 5 分钟），完成后后台会继续登录。' : '浏览器未打开窗口，请点击下方按钮继续。';
    };
    document.querySelector('#component').append(open);
    open.onclick();
    checkClosed = setInterval(() => { if (!finished && popup?.closed) finish(false); }, 1000);
    setTimeout(() => { if (!finished) finish(false); }, 300000);
  } catch {
    status.textContent = '没有待处理的验证，或验证已过期。请返回账号页面查看扫码状态。';
    cancel.disabled = true;
  }
})();
