(() => {
  const controller = window.opener || parent;
  const allowedOrigins = new Set(['127.0.0.1', 'localhost', '[::1]'].map(host =>
    `${location.protocol}//${host}${location.port ? ':' + location.port : ''}`));
  const portalOrigin = document.querySelector?.('meta[name=portal-origin]')?.content;
  if (portalOrigin) allowedOrigins.add(portalOrigin);
  let started = false;
  window.addEventListener('message', event => {
    if (event.source !== controller || !allowedOrigins.has(event.origin) || event.data?.type !== 'verification-start' || started) return;
    started = true;
    const {id, decision} = event.data;
    let finished = false;
    const finish = (success, reason = '') => {
      if (finished) return;
      finished = true;
      controller.postMessage({type: 'verification-result', id, success, reason}, event.origin);
    };
    const watchdog = setTimeout(() => finish(false, 'script_load_timeout'), 30000);
    try {
      const url = new URL(decision.url);
      url.searchParams.set('aid', '6383');
      url.searchParams.set('verify_reason', decision.event_params?.verify_reason || '');
      url.searchParams.set('verify_scene', decision.event_params?.verify_scene || '');
      const script = document.createElement('script');
      script.crossOrigin = 'anonymous';
      script.src = url.href;
      script.onerror = () => { clearTimeout(watchdog); finish(false, 'script_load_error'); };
      script.onload = () => {
        if (finished) return;
        clearTimeout(watchdog);
        try {
          if (typeof window.ucWebSecondVerify !== 'function') {
            finish(false, 'component_missing');
            return;
          }
          window.ucWebSecondVerify({
            aid: 6383, region: 'cn', isBoe: false, appName: 'douyin_web', isNewVerifyUI: true,
            secondVerifyWebOptions: {useSmsMode: 6},
            ...decision,
            newSecondVerifyRequestHost: `${location.origin}/verification-request/${encodeURIComponent(id)}`,
            fun: decision.verify_from === 'verify_center' ? 'verify_center' : 'verify',
            monitorTime: {startTime: Date.now(), renderStartTime: Date.now()},
            verifyFinishCallback: result => finish(result?.status === true || result?.status === 1),
          });
        } catch { finish(false, 'component_runtime_error'); }
      };
      window.$$account_verify_portrait_id = decision.verify_portrait_id || '';
      // The official loader supplies these React globals before loading the component.
      const sdk = document.createElement('script');
      sdk.crossOrigin = 'anonymous';
      sdk.src = 'https://lf-ucenter-web.yhgfb-cn-static.com/obj/passport-fe/ucenter_fe/@byted/uc-account-second-verification-web/1.0.16/dist/index.umd.production.js';
      sdk.onerror = script.onerror;
      sdk.onload = () => {
        if (finished) return;
        if (typeof window.ucSecondVerifyReact?.createElement !== 'function' ||
            typeof window.ucSecondVerifyReactDom?.render !== 'function') {
          clearTimeout(watchdog);
          finish(false, 'component_dependencies_missing');
          return;
        }
        document.head.append(script);
      };
      document.head.append(sdk);
    } catch { clearTimeout(watchdog); finish(false, 'component_error'); }
  });
  controller.postMessage({type: 'verification-ready'}, '*');
})();
