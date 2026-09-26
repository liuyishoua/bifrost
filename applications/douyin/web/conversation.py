"""A user/account conversation and explicitly requested manual sends."""
import base64
import json
import math
import re
import time

from .db import dump
from .douyin import AccountIssue, SendRejected, SendUncertain
from .filters import integer
from .send_records import message_records
from .send_order import assign_send_number
from .outbound import normalize_message, message_summary, send_permission, dispatch_message, remember_conversation
from utils.send_diagnostics import exception_diagnostic, send_failure_reason


def _key(row):
    return (row['attempted_at'] or 0, row['record_id'])


def _cursor(row):
    return base64.urlsafe_b64encode(dump(_key(row)).encode()).decode()


def _decode(value):
    try:
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError()
        pair = json.loads(base64.urlsafe_b64decode(value))
        if (not isinstance(pair, list) or len(pair) != 2 or type(pair[0]) not in (int,float)
                or not math.isfinite(pair[0]) or not isinstance(pair[1], str)):
            raise ValueError()
        return tuple(pair)
    except Exception:
        raise ValueError('消息分页位置无效') from None


class Conversation:
    def __init__(self, service):
        self.s, self.db = service, service.db
        # An interrupted submission is never retried automatically.
        self.db.run("UPDATE chat_sends SET status='failed',error=?,finished_at=? WHERE status='sending'",
                    (send_failure_reason({'reason':'interrupted'}), self.s.clock()))

    def list(self, uid, filters):
        self.s.messages._user(uid)
        rows = message_records(self.s, uid)
        accounts = self.db.all("SELECT id,name,uid,status,can_send,qr_status FROM accounts ORDER BY rowid DESC")
        for account in accounts:
            own = [r for r in rows if r['account_id'] == account['id']]
            account['last_reply_at'] = max((r['attempted_at'] or 0 for r in own if r['direction']=='incoming'), default=0)
            account['last_message_at'] = max((r['attempted_at'] or 0 for r in own), default=0)
            account['can_send'] = bool(account['can_send'] and account['status']=='ready' and account['qr_status']=='confirmed')
        accounts.sort(key=lambda a:(a['last_reply_at'],a['last_message_at'],a['can_send']), reverse=True)
        aid = filters.get('account_id') or (accounts[0]['id'] if accounts else None)
        if aid and not any(a['id']==aid for a in accounts):
            raise LookupError('账号不存在')
        rows = sorted([r for r in rows if r['account_id']==aid], key=_key)
        limit = integer(filters.get('limit',50), '每页条数', 1, 100)
        before, after = filters.get('before'), filters.get('after')
        if before and after:
            raise ValueError('不能同时向前和向后翻页')
        if before:
            marker = _decode(before)
            rows = [r for r in rows if _key(r)<marker]
        if after:
            marker = _decode(after)
            rows = [r for r in rows if _key(r)>marker]
        more = len(rows)>limit
        rows = rows[:limit] if after else rows[-limit:]
        states = self.db.all('SELECT * FROM message_conversations WHERE account_id=? AND uid=?', (aid,uid))
        with self.db.connect() as c:
            permission = send_permission(c, aid, uid)
        return {'account_id':aid,'accounts':accounts,'items':rows,'has_more':more,
                'send_permission':permission,
                'older_cursor':_cursor(rows[0]) if rows else before,
                'newer_cursor':_cursor(rows[-1]) if rows else after,
                'conversation':self.s.messages._state(states[0]) if states else None}

    def _saved(self, request_id, uid, aid, message, kind='text', image_id=None):
        rows = self.db.all('SELECT * FROM chat_sends WHERE id=?', (request_id,))
        if not rows:
            return None
        row = rows[0]
        if (row['uid'],row['account_id'],row['message'],row['message_type'],row['image_id']) != (uid,aid,message,kind,image_id):
            raise ValueError('发送请求标识已被其他消息使用')
        row.pop('diagnostic')
        number = self.db.all('SELECT send_number FROM send_orders WHERE record_key=?',('manual:'+request_id,))
        row['send_number'] = number[0]['send_number'] if number else None
        if image_id:
            row['image_url'] = self.s.media.get(image_id)['url']
        return row

    def send(self, uid, data):
        self.s.messages._user(uid)
        kind = data.get('message_type', 'text')
        allowed = {'account_id','request_id','message'} if kind=='text' else {'account_id','request_id','message_type','image_id'}
        if set(data) not in (allowed, allowed | {'message_type'}):
            raise ValueError('发送参数无效')
        aid, request_id, message = (data.get(k) for k in ('account_id','request_id','message'))
        if not isinstance(aid,str) or not aid:
            raise ValueError('请选择发送账号')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request_id):
            raise ValueError('发送请求标识无效')
        payload = normalize_message(self.s, {'kind':kind, 'text':message} if kind=='text' else {'kind':kind,'image_id':data.get('image_id')})
        message, image_id = message_summary(payload), payload.get('image_id')
        saved = self._saved(request_id,uid,aid,message,kind,image_id)
        if saved:
            return saved
        lock = self.s.accounts.lock(aid)
        if not lock.acquire(blocking=False):
            raise ValueError('账号正在请求中，请稍后发送')
        try:
            saved = self._saved(request_id,uid,aid,message,kind,image_id)
            if saved:
                return saved
            account = self.s.accounts.get(aid)
            if account['status']!='ready' or not account['can_send'] or account['qr_status']!='confirmed':
                raise ValueError('请先完成该账号发送鉴权')
            with self.db.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if not c.execute('SELECT 1 FROM users WHERE uid=? AND deleted_at IS NULL',(uid,)).fetchone():
                    raise LookupError('用户已被移除')
                permission = send_permission(c, aid, uid)
                if not permission['can_send']:
                    raise ValueError(permission['reason'])
                assign_send_number(c,'manual:'+request_id,aid,uid,self.s.clock())
                c.execute("INSERT INTO chat_sends(id,account_id,uid,message,message_type,image_id,status,attempted_at) VALUES(?,?,?,?,?,?,'sending',?)",
                          (request_id,aid,uid,message,kind,image_id,self.s.clock()))
            started, status, error, diagnostic, accepted = time.monotonic(), 'sent', '', {}, None
            auth = None
            try:
                auth = self.s.accounts.auth(aid)
                accepted = dispatch_message(self.s,auth,uid,payload,request_id)
                if isinstance(accepted,dict):
                    diagnostic = accepted.get('diagnostic',{})
                if not accepted:
                    status, diagnostic = 'failed', {'reason':'incomplete_response'}
            except (AccountIssue,SendRejected,SendUncertain) as exc:
                status = 'failed'
                diagnostic = getattr(exc,'diagnostic',{'reason':'account_issue' if isinstance(exc,AccountIssue) else 'platform_rejected'})
            except Exception as exc:
                status, diagnostic = 'failed', exception_diagnostic(exc)
            if status=='failed':
                error = send_failure_reason(diagnostic)
            diagnostic = {**diagnostic,'elapsed_ms':round((time.monotonic()-started)*1000),'client_message_id':request_id}
            with self.db.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('UPDATE chat_sends SET status=?,error=?,diagnostic=?,finished_at=? WHERE id=?',
                          (status,error,dump(diagnostic),self.s.clock(),request_id))
                if diagnostic.get('reason')=='account_issue' and diagnostic.get('cause_reason') not in ('network_timeout','network_error'):
                    c.execute("UPDATE accounts SET can_send=0,error='发送凭证需要重新检查' WHERE id=?",(aid,))
                if status=='sent':
                    remember_conversation(c, account, uid, accepted, self.s.clock())
            if auth is not None:
                try:
                    self.s.accounts.save(aid,auth)
                except Exception:
                    # A credential persistence failure must not erase or resend an accepted message.
                    pass
            return self._saved(request_id,uid,aid,message,kind,image_id)
        finally:
            lock.release()
