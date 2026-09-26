"""QR login stage diagnostics without recording tokens or response bodies."""
import time
import json
from pathlib import Path
from urllib.parse import urlsplit
from dy_apis.login_api import DYLoginApi
from utils.send_diagnostics import exception_diagnostic


class QRLoginError(RuntimeError):
    pass


VERIFY_DOMAINS = ('douyin.com', 'douyinstatic.com', 'bytescm.com', 'bytegoofy.com',
                  'byteimg.com', 'ibytedtos.com', 'bytedance.com', 'pstatp.com', 'zijieapi.com')

VERIFICATION_ERRORS = {
    'component_error': '官方验证组件加载或运行失败，具体阶段未记录',
    'script_load_error': '官方验证组件脚本加载失败；请查看验证页面控制台中的网络或浏览器策略报错',
    'script_load_timeout': '官方验证组件脚本加载超时（30 秒）',
    'component_missing': '官方验证组件入口未就绪；脚本已加载，但未提供 ucWebSecondVerify',
    'component_runtime_error': '官方验证组件初始化异常；请查看验证页面控制台中的脚本报错',
    'component_dependencies_missing': '官方验证组件依赖未就绪；官方 SDK 未提供所需的 React 运行环境',
}


def verification_diagnostic(decision):
    # Only known field names/types; never retain tokens, descriptions or URL queries.
    fields = ('url', 'error_code', 'verify_from', 'verify_data', 'captcha', 'extra',
              'data', 'verify_center_decision_conf', 'verify_center_secondary_decision_conf',
              'biz_params', 'sms_code_key', 'decision', 'verify_ticket', 'verify_scene')
    shape = lambda data: {key: ('empty_str' if data[key] == '' else type(data[key]).__name__)
                          for key in fields if key in data}
    result = {'fields': shape(decision), 'nested': {}}
    for key in ('extra', 'data', 'verify_data', 'decision'):
        value = decision.get(key)
        if isinstance(value, str) and len(value) <= 100000:
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            result['nested'][key] = shape(value)
    if decision.get('verify_from') == 'verify_center':
        result['verify_from'] = 'verify_center'
    headers = decision.get('_verification_header_presence')
    if isinstance(headers, dict):
        result['headers'] = {key: bool(headers.get(key)) for key in (
            'x-vc-bdturing-parameters',)}
    return result


def validate_verification(decision):
    raw = decision.get('url')
    reason = None
    if raw is None or raw == '':
        reason = 'missing_url'
    elif not isinstance(raw, str):
        reason = 'invalid_url_type'
    else:
        try:
            url = urlsplit(raw)
            trusted = any(url.hostname == d or (url.hostname or '').endswith('.' + d)
                          for d in VERIFY_DOMAINS)
            if not url.scheme:
                reason = 'relative_url'
            elif url.scheme != 'https':
                reason = 'insecure_url'
            elif not trusted:
                reason = 'untrusted_host'
            elif url.username or url.password or url.port not in (None, 443):
                reason = 'invalid_url'
        except ValueError:
            reason = 'invalid_url'
    if reason:
        labels = {'missing_url': '响应未包含组件 URL', 'invalid_url_type': '组件 URL 类型异常',
                  'relative_url': '组件使用相对地址', 'insecure_url': '组件地址不是 HTTPS',
                  'untrusted_host': '组件域名尚未核验', 'invalid_url': '组件地址格式异常'}
        diagnostic = verification_diagnostic(decision)
        diagnostic['reason'] = reason
        if reason == 'untrusted_host' and url.hostname:
            # Public domain suffix only; never include path, subdomain tokens or query.
            domain = '.'.join(url.hostname.split('.')[-2:])
            if len(domain) <= 80 and all(c.isascii() and (c.isalnum() or c in '.-') for c in domain):
                diagnostic['url_domain'] = domain
        raise QRLoginError('平台要求二次验证（2046），' + labels[reason] +
                           '；当前验证分支尚未接通。诊断：' +
                           json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':')))


class VerificationRequired(Exception):
    def __init__(self, decision):
        self.decision = decision
        super().__init__('official verification required')


class DiagnosticLogin(DYLoginApi):
    verification_wait_seconds = 0
    on_verification = None
    stage = '登录初始化'
    last_code = None
    last_status = None

    def bootstrap_auth(self, *args, **kwargs):
        self.stage = '登录初始化'
        return super().bootstrap_auth(*args, **kwargs)

    def get_qrcode(self, auth):
        self.stage = '二维码生成'
        self.last_code = None
        return self.capture(super().get_qrcode(auth))

    def check_qrcode(self, auth, token):
        self.stage = '二维码状态查询'
        self.last_code = None
        params = None
        for attempt in range(3):
            try:
                kwargs = {} if params is None else {'verification_params': params}
                return self.capture(super().check_qrcode(auth, token, **kwargs))
            except VerificationRequired as exc:
                if attempt == 2:
                    raise QRLoginError('平台仍要求二次验证（2046）；请在抖音官网完成验证后重试') from None
                self.stage = '官方二次验证'
                started = time.monotonic()
                try:
                    verified = self.on_verification and self.on_verification(exc.decision, auth)
                finally:
                    self.verification_wait_seconds += time.monotonic() - started
                if not verified:
                    raise QRLoginError('官方二次验证已取消或超时，请重新扫码') from None
                params = exc.decision.get('biz_params') or {}
                self.stage = '验证后的二维码状态查询'

    def _raise_if_blocked(self, api, result):
        # Capture before the base implementation raises on a rejected response.
        self.capture(result)
        if self.last_code == 2046 and self.on_verification and '二维码状态查询' in self.stage:
            decision = dict(result.get('data') or {})
            decision['_verification_header_presence'] = getattr(self, '_qr_verification_headers', {})
            raise VerificationRequired(decision)
        return super()._raise_if_blocked(api, result)

    def _follow_login_redirect(self, *args, **kwargs):
        self.stage = '扫码确认后的登录跳转'
        return super()._follow_login_redirect(*args, **kwargs)

    def capture(self, result):
        data = result.get('data') or {}
        code = data.get('error_code')
        self.last_code = code if type(code) is int else None
        status = data.get('status')
        if status in ('new', 'scanned', 'confirmed', 'expired'):
            self.last_status = status
        return result


def login_qr(callback, on_verification=None):
    login = DiagnosticLogin()
    login.on_verification = on_verification
    try:
        auth = login.qrcode_login(show_qr=False, on_qrcode=callback)
        login.stage = '登录会话初始化'
        auth._proxies = None
        auth.ensure_http_session()
        return auth
    except QRLoginError:
        raise
    except Exception as exc:
        reason = exception_diagnostic(exc)['reason']
        label = {'network_timeout': '超时', 'network_error': '网络异常',
                 'protobuf_decode_error': '响应解析异常'}.get(reason, '处理失败')
        detail = f'{login.stage}{label}'
        detail += f'（{type(exc).__name__}）'
        frame = exc.__traceback__
        if frame:
            while frame.tb_next:
                frame = frame.tb_next
            detail += f'；位置 {Path(frame.tb_frame.f_code.co_filename).name}:{frame.tb_lineno}'
        if login.last_code is not None:
            detail += f'；最近一次轮询/获取错误码 {login.last_code}'
        if login.last_status:
            detail += f'；最近扫码状态 {login.last_status}'
        raise QRLoginError(detail + '；请重新发起扫码') from None
