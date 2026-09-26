"""Thin adapters. Importing the console never logs in or sends a message."""
import hashlib
from types import SimpleNamespace
from utils.send_diagnostics import ConversationError, ImageUploadError, SendUncertain, exception_diagnostic


class AccountIssue(Exception):
    """The request was stopped before sending; account needs attention."""


class SendRejected(RuntimeError):
    """An explicit platform refusal, requiring the task to pause."""


def count(value):
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
        return n if n >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize(item, keyword=''):
    u = item.get('user_info', item)
    uid = str(u.get('uid') or '')
    if not uid.isdigit():
        return None
    avatar = (u.get('avatar_thumb') or {}).get('url_list') or []
    enterprise = u.get('enterprise_verify_reason')
    reason = enterprise.strip() if isinstance(enterprise, str) else ''
    return {'uid': uid, 'sec_uid': str(u.get('sec_uid') or ''),
            'douyinhao': str(u.get('unique_id') or u.get('short_id') or ''),
            'nickname': str(u.get('nickname') or ''), 'signature': str(u.get('signature') or ''),
            'avatar_url': avatar[0] if avatar and str(avatar[0]).startswith('https://') else '',
            'follower_count': count(u.get('follower_count')),
            'received_like_count': count(u.get('total_favorited')),
            'is_blue_v': bool(reason) if isinstance(enterprise, str) else None,
            'enterprise_verify_reason': reason,
            'source_keyword': keyword}



class RealAdapter:
    demo = False

    def __init__(self):
        # Upstream debug logs can include login material; the web boundary emits only sanitized errors.
        from loguru import logger
        logger.disable('builder')
        logger.disable('dy_apis')
        logger.disable('utils')

    def restore(self, material):
        from builder.auth import DouyinAuth
        # Explicit empty values prevent the legacy constructor from borrowing another .env credential.
        auth = DouyinAuth.from_cookie(
            material['cookie'], bootstrap_creator=False,
            **{key: material.get(key) or '' for key in
               ['ticket', 'ts_sign', 'client_cert', 'private_key', 'dtrait_blob', 'session_dtrait']})
        if material.get('dtrait_profile'):
            auth.dtrait_profile = material['dtrait_profile']
        return auth

    def export(self, auth):
        result = {key: getattr(auth, key, None) for key in
                  ['ticket', 'ts_sign', 'client_cert', 'private_key', 'dtrait_blob', 'session_dtrait', 'dtrait_profile']}
        result['cookie'] = auth.cookie_str
        return result

    def identity(self, auth):
        from dy_apis.douyin_api import DouyinAPI
        uid = str(DouyinAPI.get_my_uid(auth))
        auth.uid = uid
        if not uid.isdigit() or int(uid) == 0:
            raise AccountIssue('未识别到登录账号')
        return uid

    def ready(self, auth):
        # None means a transient network failure, not proof of invalid credentials.
        if not (auth.ticket and auth.private_key and auth.cookie.get('sessionid')):
            return False
        from dy_apis.douyin_api import DouyinAPI
        try:
            DouyinAPI.get_identity_security_token(auth, force=True)
            return True
        except Exception as exc:
            if exception_diagnostic(exc)['reason'] in ('network_timeout', 'network_error'):
                return None
            return False

    def login_qr(self, callback, on_verification=None):
        from .login import login_qr
        return login_qr(callback, on_verification=on_verification)

    def search_page(self, auth, query, cursor, search_id=''):
        from dy_apis.douyin_api import DouyinAPI
        return DouyinAPI.search_user(auth, query, offset=str(cursor), num='25', search_id=search_id)

    def history_page(self, auth, conversation_id, conversation_short_id, **kwargs):
        from dy_apis.douyin_api import DouyinAPI
        return DouyinAPI.get_conversation_messages(auth, conversation_id, conversation_short_id, **kwargs)


    def send(self, auth, uid, message, message_id):
        return self._send(auth, uid, message, message_id)

    def send_image(self, auth, uid, image_path, message_id):
        return self._send(auth, uid, str(image_path), message_id, image=True)

    def _send(self, auth, uid, message, message_id, image=False):
        from dy_apis.douyin_api import DouyinAPI
        stage = 'create_conversation'
        try:
            conversation, short_id, ticket = DouyinAPI.create_conversation(auth, int(uid))
            stage = 'identity_token'
            DouyinAPI.get_identity_security_token(auth)
        except Exception as exc:
            cause = exc.diagnostic if isinstance(exc, ConversationError) else exception_diagnostic(exc)
            error = AccountIssue('会话或发送凭证检查失败，请检查账号状态')
            error.diagnostic = {**cause, 'reason': 'account_issue', 'stage': stage,
                                'cause_reason': cause['reason'], 'exception_type': cause['exception_type']}
            raise error from None
        try:
            send = DouyinAPI.send_image if image else DouyinAPI.send_msg
            details = send(auth, conversation, short_id, ticket, message,
                                         client_message_id=message_id, return_details=True)
        except ImageUploadError as exc:
            error = SendRejected('图片上传失败，本条消息未提交')
            error.diagnostic = exc.diagnostic
            raise error from None
        except SendUncertain:
            raise
        except Exception as exc:
            raise SendUncertain(exception_diagnostic(exc)) from None
        response = details['response']
        diagnostic = details.get('diagnostic', {})
        reason = diagnostic.get('reason')
        if reason == 'business_accepted':
            return {'diagnostic': diagnostic, 'conversation_id': conversation, 'conversation_short_id': str(short_id)}
        if reason in ('platform_rejected', 'rate_limited'):
            error = SendRejected('平台拒绝发送')
            error.diagnostic = diagnostic
            raise error
        if response.get('message') != 'OK' or response.get('error_desc'):
            error = SendRejected('平台拒绝了消息，请检查账号与平台提示')
            error.diagnostic = {**details.get('diagnostic', {}), 'reason': 'platform_rejected'}
            raise error
        if reason in ('interface_accepted', 'accepted_without_business_code'):
            return {'diagnostic': diagnostic, 'conversation_id': conversation, 'conversation_short_id': str(short_id)}
        if reason:
            raise SendUncertain(diagnostic)
        if details['has_unknown_fields']:
            raise SendUncertain({**details.get('diagnostic', {}), 'reason': 'unknown_fields'})
        raise SendUncertain({**diagnostic, 'reason': 'incomplete_response'})


class DemoAdapter:
    """Explicitly simulated, deterministic records; no network or real recipients."""
    demo = True

    def __init__(self):
        self.sent = []
        self.qr_uid = None

    def restore(self, material):
        cookie = material['cookie']
        uid = str(int(hashlib.sha256(cookie.encode()).hexdigest()[:10], 16))
        return SimpleNamespace(uid=uid, material=dict(material))

    def export(self, auth):
        return dict(auth.material)

    def identity(self, auth):
        return auth.uid

    def ready(self, auth):
        return bool(auth.material.get('authorized'))

    def history_page(self, auth, conversation_id, conversation_short_id, **kwargs):
        return {'messages': [], 'next_cursor': str(kwargs.get('cursor', 0)), 'has_more': False}

    def search_page(self, auth, query, cursor, search_id=''):
        offset = int(cursor)
        names = ['山间咖啡', '慢调生活', '木白手作', '青禾工作室', '一间好店', '拾光日记',
                 '沿途风景', '橙子同学', '松果设计', '城市漫游', '风物笔记', '小岛电台']
        items = []
        for i in range(offset, min(offset+25, 125)):
            items.append({'user_info': {'uid': str(9000000000000000+i), 'sec_uid': 'demo-'+str(i),
                'unique_id': 'demo_creator_'+str(i+1), 'nickname': names[i % len(names)]+' '+str(i+1),
                'signature': f'演示用户 · {query} · 记录值得分享的日常',
                'follower_count': 300+i*731, 'total_favorited': 800+i*2671,
                'enterprise_verify_reason': '演示企业认证' if i % 3 == 0 else ''}})
        return {'user_list': items, 'cursor': offset+25, 'has_more': int(offset+25 < 125)}


    def send(self, auth, uid, message, message_id):
        self.sent.append((auth.uid, uid, message, message_id))
        return True

    def send_image(self, auth, uid, image_path, message_id):
        self.sent.append((auth.uid, uid, {'kind': 'image', 'path': str(image_path)}, message_id))
        return True
