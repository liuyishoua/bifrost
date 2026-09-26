import json
import os
import sqlite3
import threading
import time
import copy
import uuid
from cryptography.fernet import Fernet
from .db import dump, DEFAULT_SEND_POLICY, LEGACY_SEND_POLICY
from .filters import integer, chosen_ids
from .work_time import validate_schedule, next_work_at


class Accounts:
    def __init__(self, service):
        self.s = service
        self.db = service.db
        self.auths = {}
        self.locks = {}
        self.guard = threading.RLock()
        self.verifications = {}
        key_path = self.db.directory / 'credential.key'
        if not key_path.exists():
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())
        self.db.run("UPDATE accounts SET qr_status='interrupted',qr_url=NULL WHERE qr_status IN ('waiting','starting','scanned','verifying')")
        self.db.run("UPDATE accounts SET status='error',error='校验被服务重启中断，请重新检查' WHERE status='checking'")

    def lock(self, aid):
        with self.guard:
            return self.locks.setdefault(aid, threading.RLock())

    def encode(self, value):
        return self.cipher.encrypt(dump(value).encode()).decode()

    def get(self, aid):
        with self.db.connect() as c:
            c.execute('BEGIN')
            return self.snapshot(c, aid)

    def snapshot(self, c, aid):
        record = c.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
        if not record:
            raise LookupError('账号不存在')
        row = dict(record)
        row.pop('credential')
        row['can_search'] = bool(row['can_search'])
        row['can_send'] = bool(row['can_send'])
        row['send_enabled'] = bool(row['send_enabled'])
        row.pop('pool_enabled', None)  # Legacy opt-in column no longer affects scheduling.
        row['send_policy'] = {**LEGACY_SEND_POLICY, **json.loads(row['send_policy'])}
        row['work_schedule'] = json.loads(row['work_schedule']) if row['work_schedule'] else None
        now = self.s.clock()
        row['recent'] = self.send_counts(c, aid, now-1800, now)
        row['protection'] = self.send_counts(c, aid, max(now-1800, row['error_window_start']), now)
        row['available_at'] = max(row['next_send_at'] or 0, row['rest_until'])
        row['work_active'] = next_work_at(row['work_schedule'], now) == now
        if not row['work_active'] or row['available_at'] > now:
            row['available_at'] = next_work_at(row['work_schedule'], max(now,row['available_at']))
        row['borrowed_task_ids'] = [r[0] for r in c.execute("""SELECT t.id FROM tasks t JOIN task_accounts ta ON ta.task_id=t.id
            WHERE ta.account_id=? AND t.status='running' AND t.execution_mode='specified' ORDER BY t.id""", (aid,))]
        inflight = (c.execute("SELECT 1 FROM recipients WHERE sender_account_id=? AND status='sending' LIMIT 1", (aid,)).fetchone()
                    or c.execute("SELECT 1 FROM chat_sends WHERE account_id=? AND status='sending' LIMIT 1", (aid,)).fetchone())
        row['dispatch_state'] = ('sending' if inflight else
            'paused' if not row['send_enabled'] else
            'unavailable' if not self.send_ready(row) else
            'off_hours' if not row['work_active'] else
            'resting' if row['rest_until'] > now else
            'cooling' if (row['next_send_at'] or 0) > now else 'available')
        return row

    @staticmethod
    def send_ready(row):
        return (row['status'] == 'ready' and row['can_send']
                and row['qr_status'] not in ('starting', 'waiting', 'scanned', 'verifying'))

    @staticmethod
    def send_counts(c, aid, start, end):
        # Count only first contacts in the requested account/time window. The
        # full history projection also loads diagnostics and finished events.
        row = c.execute("""WITH sends AS (
            SELECT status FROM task_messages
            WHERE account_id=? AND attempted_at BETWEEN ? AND ?
                AND status IN ('sending','sent','failed') AND position=0 AND followup!=1
            UNION ALL
            SELECT r.status FROM recipients r JOIN tasks t ON t.id=r.task_id
            WHERE COALESCE(r.sender_account_id,t.account_id)=? AND r.attempted_at BETWEEN ? AND ?
                AND r.status IN ('sending','sent','failed')
                AND NOT EXISTS(SELECT 1 FROM task_messages m WHERE m.task_id=r.task_id AND m.uid=r.uid))
            SELECT count(*) AS attempted,COALESCE(SUM(status='sent'),0) AS sent,
                COALESCE(SUM(status='failed'),0) AS failed,COALESCE(SUM(status='sending'),0) AS sending
            FROM sends""", (aid, start, end, aid, start, end)).fetchone()
        counts = dict(row)
        known = counts['sent'] + counts['failed']
        counts['error_rate'] = counts['failed']/known if known else None
        return counts

    def protect(self, c, aid):
        row = c.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
        now = self.s.clock()
        if row['rest_until'] > now:
            return False
        policy = {**LEGACY_SEND_POLICY, **json.loads(row['send_policy'])}
        counts = self.send_counts(c, aid, max(now-1800, row['error_window_start']), now)
        known = counts['sent'] + counts['failed']
        # Integer comparison preserves the user's strictly greater-than threshold.
        if counts['attempted'] >= policy['min_attempts'] and known and counts['failed']*100 > policy['error_rate_percent']*known:
            until = now + policy['rest_minutes']*60
            reason = (f"近30分钟首次触达 {counts['attempted']} 次，首发错误率 {counts['failed']/known:.1%}"
                      f"，大于 {policy['error_rate_percent']}%，休息 {policy['rest_minutes']} 分钟")
            c.execute('UPDATE accounts SET rest_until=?,error_window_start=?,rest_reason=? WHERE id=?',
                      (until, until, reason, aid))
            return False
        return True

    def configure_sending(self, aid, data):
        self._configure_sending([aid], data)
        return self.get(aid)

    def configure_batch(self, data):
        if set(data) != {'account_ids','changes'}:
            raise ValueError('批量编辑参数无效')
        ids = chosen_ids(data['account_ids'])
        if len(ids) > 1000:
            raise ValueError('一次最多编辑 1000 个账号')
        self._configure_sending(ids, data['changes'])
        return {'updated':len(ids)}

    def _configure_sending(self, ids, data):
        limits = {'interval_seconds': (0,86400), 'random_extra_seconds': (0,86400),
                  'max_batch_size': (1,10000), 'error_rate_percent': (0,100),
                  'min_attempts': (1,10000), 'rest_minutes': (1,1440)}
        if not isinstance(data, dict) or not data or set(data)-limits.keys()-{'send_enabled','work_schedule'}:
            raise ValueError('账号发送配置包含无效字段')
        policy_changes = {key:integer(data[key], key, lo, hi) for key,(lo,hi) in limits.items() if key in data}
        if 'send_enabled' in data and type(data['send_enabled']) is not bool:
            raise ValueError('账号开关必须为布尔值')
        schedule = validate_schedule(data['work_schedule']) if 'work_schedule' in data else None
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for aid in ids:
                row = c.execute('SELECT send_policy,send_enabled,work_schedule FROM accounts WHERE id=?', (aid,)).fetchone()
                if row is None:
                    raise LookupError('账号不存在，未修改任何账号')
                policy = {**LEGACY_SEND_POLICY, **json.loads(row['send_policy']), **policy_changes}
                work = (dump(schedule) if schedule is not None else None) if 'work_schedule' in data else row['work_schedule']
                # Only requested fields change; ongoing sends, credentials and wait deadlines remain intact.
                c.execute('UPDATE accounts SET send_policy=?,send_enabled=?,work_schedule=? WHERE id=?',
                          (dump(policy), data.get('send_enabled',row['send_enabled']), work, aid))

    def list(self):
        return {'items': [self.get(r['id']) for r in self.db.all('SELECT id FROM accounts ORDER BY rowid DESC')]}

    def auth(self, aid):
        if aid not in self.auths:
            row = self.db.one('SELECT credential FROM accounts WHERE id=?', (aid,))
            material = json.loads(self.cipher.decrypt(row['credential'].encode()))
            self.auths[aid] = self.s.adapter.restore(material)
        return self.auths[aid]

    def save(self, aid, auth):
        self.db.run('UPDATE accounts SET credential=? WHERE id=?',
                    (self.encode(self.s.adapter.export(auth)), aid))

    def add(self, data):
        name, cookie = str(data.get('name') or '').strip(), str(data.get('cookie') or '').strip()
        if not name:
            raise ValueError('请填写账号名称')
        if len(name) > 80 or len(cookie) > 50000:
            raise ValueError('账号名称或 Cookie 过长')
        aid = uuid.uuid4().hex
        self.db.run("INSERT INTO accounts(id,name,credential,status,send_policy) VALUES(?,?,?,?,?)",
                    (aid, name, self.encode({'cookie': cookie}), 'checking' if cookie else 'unbound', dump(DEFAULT_SEND_POLICY)))
        if cookie:
            self.s.spawn(self._check, aid)
        return self.get(aid)

    def _check(self, aid):
        with self.lock(aid):
            try:
                auth = self.auth(aid)
                uid = self.s.adapter.identity(auth)
                row = self.get(aid)
                if row['uid'] and row['uid'] != uid:
                    raise ValueError('identity mismatch')
                ready = self.s.adapter.ready(auth)
                error = ('发送校验网络异常，请稍后检查；保留原发送状态' if ready is None else
                         '' if ready else '发送校验未通过，请检查状态或重新扫码')
                self.save(aid, auth)
                self.db.run("UPDATE accounts SET uid=?,can_search=1,can_send=?,status='ready',checked_at=?,error=? WHERE id=?",
                            (uid, int(row['can_send'] if ready is None else ready), self.s.clock(), error, aid))
            except sqlite3.IntegrityError:
                self.db.run("UPDATE accounts SET status='error',error='该抖音账号已存在，请使用原账号记录' WHERE id=?", (aid,))
            except Exception:
                self.db.run("UPDATE accounts SET status='error',can_search=0,can_send=0,error='账号校验失败，请检查 Cookie、网络与平台验证状态' WHERE id=?", (aid,))

    def update_cookie(self, aid, data):
        if set(data) != {'cookie'} or not isinstance(data['cookie'], str):
            raise ValueError('请填写新的 Cookie')
        cookie = data['cookie'].strip()
        if cookie.lower().startswith('cookie:'):
            cookie = cookie[7:].strip()
        if not cookie or len(cookie) > 50000:
            raise ValueError('Cookie 不能为空且最多 50000 字')
        lock = self.lock(aid)
        if not lock.acquire(blocking=False):
            raise ValueError('账号正在鉴权或请求中，请稍后更新 Cookie')
        previous = candidate = retired = None
        committed = False
        try:
            row = self.get(aid)
            self.require_idle(aid)
            if row['status'] == 'checking' or row['qr_status'] in ('starting', 'waiting', 'scanned', 'verifying'):
                raise ValueError('请等待账号校验或扫码结束后再更新 Cookie')
            if self.db.all("SELECT id FROM searches WHERE account_id=? AND status='running'", (aid,)):
                raise ValueError('请等待当前搜索结束后再更新 Cookie')
            previous = row
            self.db.run("UPDATE accounts SET status='checking',can_search=0,can_send=0 WHERE id=?", (aid,))
            try:
                candidate = self.s.adapter.restore({'cookie': cookie})
                uid = self.s.adapter.identity(candidate)
            except Exception:
                raise ValueError('新 Cookie 校验失败，原凭证已保留；请检查 Cookie、网络与平台验证状态') from None
            if row['uid'] and row['uid'] != uid:
                raise ValueError('新 Cookie 的账号与原账号不一致，原凭证已保留')
            try:
                self.db.run("""UPDATE accounts SET credential=?,uid=?,status='ready',can_search=1,can_send=0,
                    qr_status='idle',qr_url=NULL,checked_at=?,error='' WHERE id=?""",
                    (self.encode(self.s.adapter.export(candidate)), uid, self.s.clock(), aid))
            except sqlite3.IntegrityError:
                raise ValueError('该抖音账号已存在，请更新对应账号；原凭证已保留') from None
            retired = self.auths.get(aid)
            self.auths[aid] = candidate
            committed = True
            return self.get(aid)
        finally:
            try:
                if previous and not committed:
                    self.db.run('UPDATE accounts SET status=?,can_search=?,can_send=? WHERE id=?',
                                (previous['status'], previous['can_search'], previous['can_send'], aid))
                unused = retired if committed else candidate
                if unused is not None and hasattr(unused, 'close'):
                    unused.close()
            finally:
                lock.release()

    def check(self, aid):
        if self.get(aid)['qr_status'] == 'verifying':
            raise ValueError('请先完成或取消当前官方验证')
        with self.lock(aid):
            self.require_idle(aid)
            row = self.get(aid)
            if row['qr_status'] in ('starting', 'waiting', 'scanned', 'verifying'):
                raise ValueError('请先完成当前扫码')
            if not row['uid'] and row['status'] == 'unbound':
                raise ValueError('请先扫码登录并绑定账号')
            if row['status'] == 'checking':
                return {'status': 'checking'}
            self.db.run("UPDATE accounts SET status='checking' WHERE id=?", (aid,))
        self.s.spawn(self._check, aid)
        return {'status': self.get(aid)['status']}

    def require_idle(self, aid):
        account = self.get(aid)
        if (account['dispatch_state'] == 'sending' or
                (account['send_enabled'] and (account['borrowed_task_ids'] or
                 (self.send_ready(account) and self.db.all("SELECT id FROM tasks WHERE execution_mode='auto' AND status='running'"))))):
            raise ValueError('请先暂停该账号的发送调度，并等待当前请求结束')

    def qr(self, aid):
        self.require_idle(aid)
        row = self.get(aid)
        if row['status'] == 'checking':
            raise ValueError('请等待 Cookie 校验完成后再扫码')
        if not self.s.adapter.demo and row['qr_status'] in ('starting', 'waiting', 'scanned', 'verifying'):
            return {'status': row['qr_status']}
        with self.lock(aid):
            self.require_idle(aid)
            row = self.get(aid)
            if row['status'] == 'checking':
                raise ValueError('请等待 Cookie 校验完成后再扫码')
            if row['qr_status'] in ('starting', 'waiting', 'scanned', 'verifying'):
                if self.s.adapter.demo:
                    self._finish_demo_qr(aid)
                return {'status': self.get(aid)['qr_status']}
            self.db.run("UPDATE accounts SET qr_status='starting',error='',qr_url=NULL WHERE id=?", (aid,))
            if self.s.adapter.demo:
                self.db.run("UPDATE accounts SET qr_status='waiting',qr_url='douyin-console:demo-authorization' WHERE id=?", (aid,))
            else:
                self.s.spawn(self._qr, aid)
            return {'status': self.get(aid)['qr_status']}

    def _finish_demo_qr(self, aid):
        expected = self.get(aid)['uid']
        if expected and self.s.adapter.qr_uid and self.s.adapter.qr_uid != expected:
            self.db.run("UPDATE accounts SET qr_status='failed',qr_url=NULL,error='扫码账号与所选账号不一致，原凭证已保留' WHERE id=?", (aid,))
            return
        auth = (self.auth(aid) if expected else
                self.s.adapter.restore({'cookie': 'demo-qr:'+aid}))
        auth.material['authorized'] = True
        self._complete_qr(aid, auth, self.s.adapter.identity(auth))

    def _complete_qr(self, aid, auth, uid):
        expected = self.get(aid)['uid']
        if expected and uid != expected:
            self.db.run("UPDATE accounts SET qr_status='failed',qr_url=NULL,error='扫码账号与所选账号不一致，原凭证已保留' WHERE id=?", (aid,))
            return
        ready = self.s.adapter.ready(auth)
        error = ('' if ready else '扫码已完成，但发送校验网络异常，请稍后检查状态' if ready is None else
                 '扫码已完成，但发送校验未通过，请检查状态或重新鉴权')
        try:
            # Bind identity and the complete session atomically; the unique UID
            # constraint also prevents simultaneous scans creating duplicate accounts.
            self.db.run("""UPDATE accounts SET uid=?,credential=?,status='ready',can_search=1,can_send=?,
                qr_status='confirmed',qr_url=NULL,checked_at=?,error=? WHERE id=?""",
                (uid, self.encode(self.s.adapter.export(auth)), int(bool(ready)), self.s.clock(), error, aid))
        except sqlite3.IntegrityError:
            self.db.run("UPDATE accounts SET qr_status='failed',qr_url=NULL,error='该抖音账号已存在，请在原账号上重新扫码；原账号凭证已保留' WHERE id=?", (aid,))
            return
        self.auths[aid] = auth

    def _qr(self, aid):
        # The account lock spans the complete login, so no request mutates that Auth in parallel.
        with self.lock(aid):
            stage = '扫码登录'
            try:
                def show(url):
                    self.db.run("UPDATE accounts SET qr_status='waiting',qr_url=? WHERE id=?", (url, aid))
                auth = self.s.adapter.login_qr(show, on_verification=lambda decision, current: self.wait_verification(aid, decision, current))
                stage = '登录后的账号身份查询'
                uid = self.s.adapter.identity(auth)
                stage = '扫码账号确认与会话保存'
                self._complete_qr(aid, auth, uid)
            except Exception as exc:
                from .login import QRLoginError
                error = str(exc) if isinstance(exc, QRLoginError) else stage + '失败，请重试'
                self.db.run("UPDATE accounts SET qr_status='failed',qr_url=NULL,error=? WHERE id=?", (error, aid))

    def wait_verification(self, aid, decision, auth=None):
        from .login import validate_verification, QRLoginError, VERIFICATION_ERRORS
        validate_verification(decision)
        challenge = {'id': uuid.uuid4().hex, 'decision': copy.deepcopy(decision),
                     'auth': auth, 'request_lock': threading.RLock(),
                     'event': threading.Event(), 'success': False,
                     'expires': time.monotonic() + 300}
        with self.guard:
            if self.s.stopped.is_set():
                return False
            self.verifications[aid] = challenge
            self.db.run("UPDATE accounts SET qr_status='verifying',error='' WHERE id=?", (aid,))
        try:
            finished = challenge['event'].wait(300)
            if not finished:
                raise QRLoginError('官方二次验证等待超时，请重新扫码')
            if challenge.get('reason') in VERIFICATION_ERRORS:
                raise QRLoginError(VERIFICATION_ERRORS[challenge['reason']])
            if challenge['success']:
                self.db.run("UPDATE accounts SET qr_status='waiting' WHERE id=?", (aid,))
            return challenge['success']
        finally:
            with challenge['request_lock'], self.guard:
                self.verifications.pop(aid, None)

    def verification_request(self, nonce, path, method, query, data, headers):
        from .verification import request_verification
        with self.guard:
            challenge = next((c for c in self.verifications.values() if c['id'] == nonce), None)
        if not challenge:
            raise ValueError('验证已结束或过期，请重新扫码')
        with challenge['request_lock']:
            if (challenge['event'].is_set() or time.monotonic() >= challenge['expires'] or
                    challenge['auth'] is None):
                raise ValueError('验证已结束或过期，请重新扫码')
            return request_verification(challenge['auth'], path, method, query, data, headers)

    def verification(self, aid):
        self.get(aid)
        with self.guard:
            challenge = self.verifications.get(aid)
            if not challenge or challenge['event'].is_set() or time.monotonic() >= challenge['expires']:
                raise ValueError('验证已结束或过期，请刷新扫码状态')
            return {'id': challenge['id'], 'decision': copy.deepcopy(challenge['decision'])}

    def finish_verification(self, aid, data):
        from .login import VERIFICATION_ERRORS
        with self.guard:
            current = self.verification(aid)
            if data.get('id') != current['id'] or type(data.get('success')) is not bool:
                raise ValueError('验证会话不匹配，请刷新扫码状态')
            challenge = self.verifications[aid]
            # This only resumes polling. Login confirmation and UID checks remain authoritative.
            challenge['success'] = data['success']
            reason = data.get('reason')
            challenge['reason'] = reason if isinstance(reason, str) and reason in VERIFICATION_ERRORS else ''
            challenge['event'].set()
        return {'ok': True}

    def close(self):
        with self.guard:
            for challenge in self.verifications.values():
                challenge['event'].set()

    def delete(self, aid):
        if self.get(aid)['qr_status'] == 'verifying':
            raise ValueError('请先完成或取消当前官方验证')
        self.require_idle(aid)
        with self.lock(aid):
            if (self.db.all('SELECT id FROM tasks WHERE account_id=?', (aid,)) or
                    self.db.all('SELECT task_id FROM task_accounts WHERE account_id=?', (aid,))):
                raise ValueError('该账号已关联任务，需保留账号以维护历史')
            if self.db.all('SELECT 1 FROM recipients WHERE sender_account_id=? LIMIT 1', (aid,)):
                raise ValueError('该账号存在发送历史，需保留账号以维护历史')
            if self.db.all('SELECT 1 FROM task_messages WHERE account_id=? LIMIT 1', (aid,)):
                raise ValueError('该账号存在发送历史，需保留账号以维护历史')
            if self.db.all('SELECT 1 FROM message_conversations WHERE account_id=? LIMIT 1', (aid,)):
                raise ValueError('该账号存在会话历史，需保留账号以维护历史')
            if self.db.all('SELECT 1 FROM chat_sends WHERE account_id=? LIMIT 1', (aid,)):
                raise ValueError('该账号存在手动发送历史，需保留账号以维护历史')
            if self.db.all("SELECT id FROM searches WHERE account_id=? AND status='running'", (aid,)):
                raise ValueError('请等待当前搜索结束后再删除账号')
            self.db.run('DELETE FROM accounts WHERE id=?', (aid,))
            self.auths.pop(aid, None)
        return {'ok': True}
