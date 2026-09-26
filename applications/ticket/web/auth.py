"""Recover the booking session before asking the user to scan again."""
import copy

import requests

from ticket_app.client import RailwayClient
from ticket_app.configuration import AppError, BASE_URL
from .diagnostics import DiagnosticSession, session_event


def check_account_session(account, client, store, checked=False):
    original_cookies = copy.deepcopy(client.session.cookies)

    def restore_credentials():
        client.session.cookies = copy.deepcopy(original_cookies)
        if client.session is account.session:
            account.session.save()

    def event(kind, message, **details):
        session_event(account, store, kind, message, **details)

    try:
        if not checked:
            if client.check_session():
                if account.record.get('session_events', [{}])[-1].get('kind') == 'unconfirmed':
                    event('confirmed', '会话校验已恢复正常，无需扫码')
                return True
        restore_credentials()
        event('business_expired', '业务会话未通过校验，尝试用已有凭证恢复', endpoint='checkUser', flag=False)
        if not account.record['identity'] or not client.session.cookies:
            return False
        with DiagnosticSession(observer=account.session.observer) as candidate:
            candidate.headers.update(client.session.headers)
            candidate.cookies.update(copy.deepcopy(client.session.cookies))

            def post(path, data):
                result = candidate.post(BASE_URL + path, data=data, timeout=client.cfg.request_timeout_seconds)
                fields = dict(endpoint=path.rsplit('/', 1)[-1], http_status=result.status_code)
                try:
                    payload = result.json()
                except ValueError:
                    event('response', '会话恢复接口返回非 JSON 响应', **fields)
                    raise AppError('会话恢复暂时失败，凭证已保留，请稍后重试')
                code = payload.get('result_code') if isinstance(payload, dict) else None
                if type(code) is int or isinstance(code, str) and code.isdigit():
                    fields['result_code'] = code
                event('response', '收到会话恢复接口响应', **fields)
                if result.status_code != 200 or 'result_code' not in fields:
                    raise AppError('会话恢复暂时失败，凭证已保留，请稍后重试')
                return payload

            passport = post('/passport/web/auth/uamtk', {'appid': 'otn'})
            if str(passport['result_code']) != '0':
                message = str(passport.get('result_message', ''))
                if '未登录' in message or '登录已过期' in message or '登录状态已失效' in message:
                    account.logged_in = False
                    account.record['session_state'] = 'expired'
                    event('expired', '登录中心明确返回未登录或已失效，需要扫码；本地凭证保留')
                    return False
                raise AppError('登录中心暂未恢复会话，凭证已保留，请稍后重试')
            token = passport.get('newapptk')
            if not token:
                raise AppError('登录中心未返回恢复凭据，原凭证已保留，请稍后重试')
            auth = post('/otn/uamauthclient', {'tk': token})
            if str(auth['result_code']) != '0':
                raise AppError('业务会话暂未恢复，凭证已保留，请稍后重试')
            if auth.get('username') != account.record['identity']:
                raise AppError('恢复返回的账号身份不一致，已拒绝使用新凭证')
            probe = RailwayClient(client.cfg, session=candidate, cancel_token=client.cancel_token)
            if not probe.check_session():
                raise AppError('恢复后的会话尚未通过校验，凭证已保留，请稍后重试')
            client.session.cookies = candidate.cookies
            if client.session is account.session:
                account.session.save()
                account.logged_in = True
            account.record['session_state'] = 'valid'
            event('recovered', '业务会话已自动恢复，无需扫码')
            return True
    except (requests.RequestException, AppError) as exc:
        restore_credentials()
        message = str(exc) if isinstance(exc, AppError) else '会话恢复网络异常，凭证已保留，请稍后重试'
        event('unconfirmed', message)
        raise AppError(message) from exc
