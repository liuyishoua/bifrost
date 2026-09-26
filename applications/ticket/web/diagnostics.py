"""Credential-free, durable diagnostics for account session operations."""
import logging
import sqlite3
import time
import uuid
from urllib.parse import urlsplit

import requests

from ticket_app.logging_utils import redact_text


COOKIE_NAMES = {'JSESSIONID', 'tk', 'uamtk', '_passport_session', '_passport_ct', 'BIGipServerotn', 'BIGipServerpassport', 'SF_cookie_2'}

AUTH_ENDPOINTS = {'checkUser', 'uamtk', 'uamauthclient', 'uamtk-static', 'create-qr64', 'checkqr', 'conf', 'getLoginBanner'}
ERROR_LABELS = {
    'connect_timeout': '建立连接超时', 'read_timeout': '等待响应超时', 'timeout': '请求超时',
    'tls_error': 'TLS证书或握手失败', 'proxy_error': '代理连接失败', 'dns_error': '域名解析失败',
    'connection_error': '网络连接失败', 'request_error': '网络请求失败', 'rate_limited': '接口限流',
    'forbidden': '接口拒绝访问', 'upstream_error': '上游服务异常', 'http_error': 'HTTP响应异常',
    'invalid_json': '响应不是有效JSON', 'invalid_schema': '响应缺少约定字段',
}


def cookie_summary(cookies):
    values = list(cookies)
    expiries = [c.expires for c in values if c.expires is not None]
    return dict(count=len(values), expired=sum(c.is_expired() for c in values),
                session_cookies=sum(c.expires is None for c in values), earliest_expiry=min(expiries, default=None))


def failure_type(exc):
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in {'gaierror', 'NameResolutionError'}:
            return 'dns_error'
        current = getattr(current, 'reason', None) or getattr(current, '__cause__', None) or getattr(current, '__context__', None)
    for cls, label in [(requests.exceptions.SSLError, 'tls_error'), (requests.exceptions.ProxyError, 'proxy_error'),
                       (requests.ConnectTimeout, 'connect_timeout'), (requests.ReadTimeout, 'read_timeout'),
                       (requests.Timeout, 'timeout'), (requests.ConnectionError, 'connection_error')]:
        if isinstance(exc, cls):
            return label
    return 'request_error'


def session_event(account, store, kind, message, preview=True, **details):
    message = redact_text(message, sensitive_terms=[c.value for c in account.session.cookies if c.value])[:600]
    event = dict(at=time.time(), instance_id=store.instance_id, trace_id=getattr(account, 'trace_id', ''),
                 operation=getattr(account, 'operation', 'idle'), kind=kind, message=message,
                 logged_in=account.logged_in, **details)
    if preview:
        account.record['session_events'] = (account.record.get('session_events', []) + [event])[-20:]
    try:
        store.append_session_event(account.record['id'], event)
        if preview:
            store.put('account', account.record)
    except (sqlite3.Error, OSError):
        account.record['diagnostic_error'] = '会话诊断写入失败，请检查本机磁盘或数据库'
        logging.error('会话诊断写入失败；未输出请求或凭证内容')


def start_trace(account, store, operation):
    account.trace_id = uuid.uuid4().hex
    account.operation = operation
    session_event(account, store, 'trace_start', '开始会话操作', preview=False,
                  cookies=cookie_summary(account.session.cookies))


def end_trace(account, store):
    session_event(account, store, 'trace_end', '会话操作结束', preview=False,
                  cookies=cookie_summary(account.session.cookies))
    account.operation = 'idle'


class DiagnosticSession(requests.Session):
    def __init__(self, observer=None):
        super().__init__()
        self.observer = observer

    def emit(self, kind, message, **details):
        if self.observer:
            self.observer(kind, message, **details)

    def request(self, method, url, *args, **kwargs):
        endpoint = urlsplit(url).path.rsplit('/', 1)[-1]
        if endpoint not in AUTH_ENDPOINTS:
            return super().request(method, url, *args, **kwargs)
        started = time.perf_counter()
        before = {(c.name, c.domain, c.path): c.value for c in self.cookies}
        fields = dict(endpoint=endpoint, cookie_before=cookie_summary(self.cookies))
        try:
            result = super().request(method, url, *args, **kwargs)
        except requests.RequestException as exc:
            category = failure_type(exc)
            fields.update(failure_type=category, exception_type=type(exc).__name__)
            raise
        else:
            fields.update(http_status=result.status_code, redirects=[r.status_code for r in result.history], response_bytes=len(result.content))
            category = {403: 'forbidden', 429: 'rate_limited'}.get(result.status_code)
            if not category and result.status_code >= 500:
                category = 'upstream_error'
            if not category and result.status_code != 200:
                category = 'http_error'
            try:
                payload = result.json()
            except ValueError:
                fields['response_format'] = 'empty' if not result.content else 'html' if 'text/html' in result.headers.get('Content-Type', '') else 'non_json'
                category = category or 'invalid_json'
            else:
                fields['response_format'] = 'json'
                payload = payload if isinstance(payload, dict) else {}
                code = payload.get('result_code')
                if type(code) is int or isinstance(code, str) and code.isdigit():
                    fields['result_code'] = code
                data = payload.get('data')
                flag = data.get('flag') if isinstance(data, dict) else None
                if type(flag) is bool:
                    fields['flag'] = flag
                if endpoint == 'checkUser' and type(flag) is not bool:
                    category = category or 'invalid_schema'
                if endpoint in {'uamtk', 'uamauthclient', 'create-qr64', 'checkqr'} and 'result_code' not in fields:
                    category = category or 'invalid_schema'
                # Record reason categories, never arbitrary upstream text or identities.
                message = str(payload.get('result_message', ''))
                fields['upstream_reason'] = next((label for word, label in [
                    ('未登录', 'not_logged_in'), ('登录已过期', 'login_expired'), ('登录状态已失效', 'login_expired'),
                    ('繁忙', 'server_busy'), ('票据', 'ticket_error'), ('二维码已过期', 'qr_expired'),
                ] if word in message), 'unspecified')
            if category:
                fields['failure_type'] = category
            return result
        finally:
            after = {(c.name, c.domain, c.path): c.value for c in self.cookies}
            label = lambda keys: sorted({key[0] for key in keys if key[0] in COOKIE_NAMES})
            fields['cookie_changes'] = dict(added=label(after.keys()-before.keys()), removed=label(before.keys()-after.keys()), rotated=label(key for key in before.keys() & after.keys() if before[key] != after[key]))
            fields.update(duration_ms=round((time.perf_counter()-started)*1000, 2), cookie_after=cookie_summary(self.cookies))
            label = ERROR_LABELS.get(fields.get('failure_type'), '接口已返回，业务结果见记录')
            self.emit('request', f'{endpoint}：{label}', preview=False, **fields)
