--- BEGIN RESTORE SCRIPT ---
import hashlib, json, pathlib, sys
bundle, target = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
entries = []
with bundle.open("rb") as stream:
    while stream.readline() != b"@@BEGIN_FILES_V1@@\n":
        if stream.tell() == bundle.stat().st_size:
            raise ValueError("Missing bundle start")
    while True:
        line = stream.readline()
        if line.startswith(b"@@END_BUNDLE "):
            assert int(line.split()[1]) == len(entries), "File count mismatch"
            assert stream.read() == b"", "Unexpected trailing data"
            break
        assert line.startswith(b"@@FILE "), "Invalid file marker"
        meta = json.loads(line[len(b"@@FILE "):])
        path = pathlib.PurePosixPath(meta["path"])
        assert not path.is_absolute() and ".." not in path.parts
        assert path.parts[0] == "web" and "\\" not in meta["path"]
        data = stream.read(meta["bytes"])
        assert len(data) == meta["bytes"], "Truncated file"
        assert hashlib.sha256(data).hexdigest() == meta["sha256"], "Checksum mismatch"
        assert stream.readline() == b"\n", "Missing separator"
        assert stream.readline() == b"@@END_FILE@@\n", "Missing file end"
        entries.append((path, data))
assert len({str(path) for path, _ in entries}) == len(entries), "Duplicate path"
# Validate every entry before writing. Never overwrite existing files.
for path, _ in entries:
    dest = target.joinpath(*path.parts)
    assert dest.resolve().is_relative_to(target.resolve()), "Path escapes target"
    assert not dest.exists() and not dest.is_symlink(), "Destination exists: " + str(dest)
for path, data in entries:
    dest = target.joinpath(*path.parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("xb") as output:
        output.write(data)
print("Restored", len(entries), "files")
--- END RESTORE SCRIPT ---

文件目录：
web/__init__.py
web/__main__.py
web/account_performance.py
web/accounts.py
web/app.py
web/conversation.py
web/db.py
web/douyin.py
web/filters.py
web/library.py
web/login.py
web/media.py
web/messages.py
web/outbound.py
web/overview.py
web/reply_review.py
web/search.py
web/send_order.py
web/send_records.py
web/service.py
web/tasks.py
web/verification.py
web/work_time.py

@@BEGIN_FILES_V1@@
@@FILE {"path": "web/__init__.py", "bytes": 51, "sha256": "3bef124812e355f23c606cee83dc603fae871e3be1d4a28d00ead54ea7acaea4"}
"""Local user management and messaging console."""

@@END_FILE@@
@@FILE {"path": "web/__main__.py", "bytes": 1385, "sha256": "bffc3cd8ad3a1fbf5560a1fff2de022883f527c812b9194b66ea3c7ad3812871"}
import argparse
import atexit
import fcntl
from pathlib import Path
from .app import create_app
from .douyin import DemoAdapter


def main():
    parser = argparse.ArgumentParser(description='抖音用户与消息管理控制台（本机运行）')
    parser.add_argument('--demo', action='store_true', help='模拟账号和数据，不发出真实抖音请求')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    data_dir = args.data_dir or Path('datas/web-demo' if args.demo else 'datas/web')
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    instance_lock = (data_dir / 'server.lock').open('w')
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('这个数据目录已有服务运行，不能重复启动执行器')
    app = create_app(data_dir, adapter=DemoAdapter() if args.demo else None)
    atexit.register(app.extensions['console'].close)
    mode = '演示模式：所有账号、搜索和发送均为模拟' if args.demo else '真实模式：仅用户创建并开始任务后才发送'
    print(f'\n{mode}\n打开 http://127.0.0.1:{args.port}\n', flush=True)
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()

@@END_FILE@@
@@FILE {"path": "web/account_performance.py", "bytes": 4444, "sha256": "fc14275ab3dab7a7702dc91264c14c0ca0c50e93138783733d9faa174b52a394"}
"""Read-only account outcome totals and trends from local sends."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .send_records import TASK_SEND_STATS_SQL


SHANGHAI = ZoneInfo('Asia/Shanghai')


def account_performance(service, filters):
    period = filters.get('range', 'today')
    scope = filters.get('scope', 'all')
    if period not in ('today', '7', '30', 'all'):
        raise ValueError('账号统计范围支持今天、近 7 天、近 30 天或全部')
    if scope not in ('all', 'first_touch'):
        raise ValueError('发送统计口径无效')
    now = service.clock()
    current = datetime.fromtimestamp(now, SHANGHAI)
    today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    start = None if period == 'all' else today-timedelta(days=0 if period=='today' else int(period)-1)
    hourly = period == 'today'
    fmt = '%Y-%m-%d %H:00' if hourly else '%Y-%m-%d'
    where, args = "status IN ('sent','failed') AND (attempted_at IS NULL OR attempted_at<=?)", [now]
    if scope == 'first_touch':
        where += ' AND first_touch=1'
    with service.db.connect() as c:
        c.execute('BEGIN')
        accounts = [dict(row) for row in c.execute('SELECT id,name FROM accounts ORDER BY name,id')]
        rows = c.execute(f"""WITH task_sends AS ({TASK_SEND_STATS_SQL}), sends AS (
            SELECT account_id,status,attempted_at,first_touch FROM task_sends
            UNION ALL SELECT account_id,status,attempted_at,0 FROM chat_sends)
            SELECT account_id,status,strftime(?,attempted_at,'unixepoch','+8 hours') AS bucket,count(*) AS n
            FROM sends WHERE {where} GROUP BY account_id,status,bucket""", [fmt,*args]).fetchall()
    if start is None:
        first = min((row['bucket'] for row in rows if row['bucket']), default=today.strftime(fmt))
        beginning = datetime.strptime(first, fmt).replace(tzinfo=SHANGHAI)
    else:
        beginning = start
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    buckets, cursor = [], beginning
    while cursor <= current:
        buckets.append({'key':cursor.strftime(fmt), 'label':
                        f"{cursor:%m-%d %H:00}–{cursor+step:%H:00}" if hourly else cursor.strftime('%Y-%m-%d'),
                        'tick':cursor.strftime('%H:%M' if hourly else '%m-%d')})
        cursor += step
    indexes = {bucket['key']:i for i,bucket in enumerate(buckets)}

    def empty(aid, name):
        return {'account_id':aid, 'account_name':name, 'sent':0, 'failed':0, 'undated':0,
                'lifetime':{'sent':0,'failed':0,'undated':0},
                'series':[{'sent':0,'failed':0} for _ in buckets]}

    items = {a['id']:empty(a['id'],a['name']) for a in accounts}
    summary = empty(None, '全部账号')
    for row in rows:
        aid = row['account_id'] or 'missing'
        if aid not in items:
            items[aid] = empty(aid, '旧记录未记录账号' if aid=='missing' else '历史账号')
        for item in (items[aid],summary):
            item['lifetime'][row['status']] += row['n']
            if row['bucket'] is None:
                item['lifetime']['undated'] += row['n']
            if row['bucket'] not in indexes and not (period=='all' and row['bucket'] is None):
                continue
            item[row['status']] += row['n']
            if row['bucket'] is None:
                item['undated'] += row['n']
            else:
                item['series'][indexes[row['bucket']]][row['status']] += row['n']
    for item in [summary,*items.values()]:
        for point in [item,item['lifetime'],*item['series']]:
            total = point['sent']+point['failed']
            point['error_rate'] = point['failed']/total if total else None
    detail_filters = {'category':'outgoing', 'source_scope':'first_touch' if scope=='first_touch' else 'local'}
    lifetime_detail_filters = dict(detail_filters)
    if start is not None:
        detail_filters.update(sent_from=start.date().isoformat(), sent_to=today.date().isoformat())
    return {'now':now, 'range':period, 'scope':scope, 'start':start.timestamp() if start else None,
            'end':now, 'granularity':'hour' if hourly else 'day', 'buckets':buckets,
            'summary':summary, 'items':sorted(items.values(), key=lambda r:(-r['failed'],-r['sent'],r['account_name'],r['account_id'])),
            'detail_filters':detail_filters, 'lifetime_detail_filters':lifetime_detail_filters}

@@END_FILE@@
@@FILE {"path": "web/accounts.py", "bytes": 23897, "sha256": "3474b090f5128490a06d73f505686df7774fb72f6d2e540e6e6619e5982d153b"}
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

@@END_FILE@@
@@FILE {"path": "web/app.py", "bytes": 13243, "sha256": "130c84c48c58cbd682c4b48d04fc2f59cb2115ff42d999cf7aea2d0e8e008e5b"}
import io
import os
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException
from .service import Service
from .media import MAX_UPLOAD_BODY
from .overview import snapshot
from .account_performance import account_performance
from .send_records import records, task_followups
from .verification import VERIFICATION_METHODS
from dy_apis.douyin_im_history import HistoryError

VERIFICATION_HOST = 'verification.localhost'


def create_app(directory=None, adapter=None, background=True):
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config.update(MAX_CONTENT_LENGTH=2*1024*1024, TRUSTED_HOSTS=['localhost', '127.0.0.1', '[::1]', VERIFICATION_HOST])
    s = Service(directory or Path('datas/web'), adapter=adapter, background=background)
    app.extensions['console'] = s

    @app.before_request
    def same_origin():
        # This origin permits component cookies but must never expose console APIs.
        if urlsplit(request.host_url).hostname == VERIFICATION_HOST:
            if request.path.startswith('/verification-request/'):
                if (request.headers.get('Origin', request.host_url.rstrip('/')) != request.host_url.rstrip('/') or
                        request.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin'):
                    return jsonify(error='验证请求来源无效'), 403
            elif request.method not in ('GET', 'HEAD') or request.path not in (
                    '/verification-frame', '/static/verification-frame.js'):
                return jsonify(error='记录不存在'), 404
        elif request.path.startswith('/verification-request/') or request.path in ('/verification-frame', '/static/verification-frame.html'):
            return jsonify(error='记录不存在'), 404
        if request.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            if (request.headers.get('X-App-Request') != '1' or not request.is_json or
                    (origin and urlsplit(origin).netloc != request.host)):
                return jsonify(error='请求来源无效，请从本机页面操作'), 403

    @app.after_request
    def headers(response):
        if response.mimetype == 'text/html' and response.status_code == 200:
            prefix = request.headers.get('X-Forwarded-Prefix', '')
            prefix = prefix if prefix == '/douyin' else ''
            tags = f'<base href="{prefix}/">'
            for name, env in (('verification-origin', 'ANYDOOR_VERIFY_ORIGIN'), ('portal-origin', 'ANYDOOR_PUBLIC_ORIGIN')):
                if name == 'verification-origin' and not prefix:
                    continue
                origin = os.environ.get(env, '')
                parsed = urlsplit(origin)
                if parsed.scheme in ('http', 'https') and parsed.netloc and not parsed.path and not parsed.query and not parsed.fragment:
                    tags += f'<meta name="{name}" content="{escape(origin, quote=True)}">'
            response.direct_passthrough = False
            html = response.get_data(as_text=True)
            html = html.replace('<head>', '<head>' + tags, 1) if '<head>' in html else html.replace('<meta', tags + '<meta', 1)
            response.set_data(html)
            response.headers.pop('ETag', None)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data: blob:; connect-src 'self'; frame-ancestors 'none'"
        if response.status_code == 200 and request.path == '/verification-frame':
            from .login import VERIFY_DOMAINS
            hosts = ' '.join('https://' + prefix + domain for domain in VERIFY_DOMAINS for prefix in ('', '*.'))
            public_origin = request.headers.get('X-Forwarded-Proto', request.scheme) + '://' + request.headers.get('X-Forwarded-Host', request.host)
            response.headers['Content-Security-Policy'] = (
                "sandbox allow-scripts allow-forms allow-popups allow-same-origin; default-src 'none'; "
                f"script-src {public_origin}/static/verification-frame.js https://lf-ucenter-web.yhgfb-cn-static.com {hosts}; "
                f"connect-src {public_origin}/verification-request/ {hosts}; frame-src {hosts}; img-src https: data: blob:; "
                f"style-src 'unsafe-inline' {hosts}; font-src {hosts} data:; frame-ancestors 'none'")
        if request.path.startswith('/api/') or 'verification' in request.path:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, HistoryError):
            return jsonify(error='历史同步未完成，已保存的消息和进度保留，请重试'), 502
        if isinstance(exc, LookupError):
            return jsonify(error='记录不存在'), 404
        if isinstance(exc, ValueError):
            return jsonify(error=str(exc)), 400
        if isinstance(exc, HTTPException):
            return jsonify(error='请求格式或地址无效'), exc.code
        # Never expose upstream exception text: it may contain login credentials.
        return jsonify(error='操作未完成，请刷新状态后重试'), 500

    def body():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('请求内容必须为对象')
        return data

    @app.post('/api/media')
    def media_upload():
        request.max_content_length = MAX_UPLOAD_BODY
        return s.media.upload(body())

    @app.get('/api/media/<media_id>')
    def media_file(media_id):
        return send_file(s.media.path(media_id))

    @app.get('/api/meta')
    def meta():
        return {'demo': s.adapter.demo, 'now': s.clock()}

    @app.get('/api/overview')
    def overview():
        return snapshot(s, request.args.to_dict())

    @app.get('/api/overview/accounts')
    def overview_accounts():
        return account_performance(s, request.args.to_dict())

    @app.get('/api/send-records')
    def send_records():
        return records(s, request.args.to_dict())

    @app.route('/api/accounts', methods=['GET', 'POST'])
    def accounts():
        return s.accounts.list() if request.method == 'GET' else s.accounts.add(body())

    @app.route('/api/accounts/<aid>', methods=['PATCH', 'DELETE'])
    def account_update(aid):
        data = body()
        if request.method == 'DELETE':
            return s.accounts.delete(aid)
        return s.accounts.update_cookie(aid, data)

    @app.post('/api/accounts/<aid>/check')
    def account_check(aid):
        body()
        return s.accounts.check(aid)

    @app.patch('/api/accounts/<aid>/sending')
    def account_sending(aid):
        return s.accounts.configure_sending(aid, body())

    @app.patch('/api/accounts/batch/sending')
    def account_batch_sending():
        return s.accounts.configure_batch(body())

    @app.route('/api/accounts/<aid>/qr', methods=['GET', 'POST'])
    def account_qr(aid):
        if request.method == 'GET':
            return s.accounts.get(aid)
        body()
        return s.accounts.qr(aid)

    @app.route('/api/accounts/<aid>/verification', methods=['GET', 'POST'])
    def account_verification(aid):
        if request.method == 'POST':
            return s.accounts.finish_verification(aid, body())
        return s.accounts.verification(aid)

    @app.get('/verification-frame')
    def verification_frame():
        return app.send_static_file('verification-frame.html')

    @app.route('/verification-request/<nonce>/<path:path>', methods=['GET', 'POST'])
    def verification_request(nonce, path):
        path = '/' + path
        if VERIFICATION_METHODS.get(path) != request.method:
            return jsonify(error='不支持该验证接口或请求方法'), 404
        try:
            return s.accounts.verification_request(nonce, path, request.method,
                list(request.args.items(multi=True)), request.get_data(), request.headers)
        except ValueError:
            raise
        except Exception:
            # No exception text, request data, redirects, Cookies or upstream headers.
            return jsonify(message='error', data={'description': '验证接口请求失败，请稍后重试'}), 502

    @app.get('/api/accounts/<aid>/qr-image')
    def qr_image(aid):
        import qrcode
        row = s.accounts.get(aid)
        if not row['qr_url']:
            raise LookupError('二维码尚未就绪')
        output = io.BytesIO()
        qrcode.make(row['qr_url']).save(output, format='PNG')
        output.seek(0)
        return send_file(output, mimetype='image/png', max_age=0)

    @app.post('/api/searches')
    def search_create():
        return s.search.create(body())

    @app.get('/api/searches/<sid>')
    def search_get(sid):
        return s.search.get(sid)

    @app.get('/api/searches/<sid>/results')
    def search_results(sid):
        return s.search.results(sid, request.args.to_dict())

    @app.post('/api/searches/<sid>/selection')
    def search_selection(sid):
        return s.search.selection(sid, body())

    @app.post('/api/searches/<sid>/import')
    def search_import(sid):
        data = body()
        return s.search.import_users(sid, data.get('uids'), data.get('tag_ids'))

    @app.route('/api/tags', methods=['GET', 'POST'])
    @app.route('/api/message-templates', methods=['GET', 'POST'])
    def library_list():
        kind = request.path.rsplit('/', 1)[-1]
        return s.library.list(kind) if request.method == 'GET' else s.library.save(kind, body())

    @app.route('/api/tags/<item_id>', methods=['PATCH', 'DELETE'])
    @app.route('/api/message-templates/<item_id>', methods=['PATCH', 'DELETE'])
    def library_item(item_id):
        kind = request.path.split('/')[2]
        data = body()
        return s.library.delete(kind, item_id) if request.method == 'DELETE' else s.library.save(kind, data, item_id)

    @app.post('/api/users/tags')
    def user_tags():
        return s.library.user_tags(body())

    @app.get('/api/users')
    def users():
        return s.users(request.args.to_dict())

    @app.post('/api/users/selection')
    def users_selection():
        return s.selection(body())

    @app.post('/api/users/bulk-delete')
    def users_delete():
        return s.delete_users(body().get('uids'))

    @app.post('/api/users/bulk-edit')
    def users_edit():
        return s.batch_edit(body())

    @app.patch('/api/users/<uid>')
    def user_edit(uid):
        return s.edit_user(uid, body())

    @app.get('/api/users/<uid>/history')
    def user_history(uid):
        data = s.history(uid)
        for row in data['items']:
            row['message'] = json.loads(row.pop('config'))['message']
        return data

    @app.get('/api/users/<uid>/messages')
    def user_messages(uid):
        return s.messages.list(uid, request.args.to_dict())

    @app.get('/api/users/<uid>/conversation')
    def user_conversation(uid):
        return s.conversation.list(uid, request.args.to_dict())

    @app.post('/api/users/<uid>/conversation/send')
    def user_conversation_send(uid):
        return s.conversation.send(uid, body())

    @app.post('/api/users/<uid>/messages/sync')
    def user_messages_sync(uid):
        return s.messages.sync(uid, body())

    @app.route('/api/tasks', methods=['GET', 'POST'])
    def tasks():
        return s.tasks.list(request.args.to_dict()) if request.method == 'GET' else s.tasks.create(body())

    @app.route('/api/tasks/<tid>', methods=['GET', 'PATCH', 'DELETE'])
    def task(tid):
        if request.method == 'DELETE':
            body()
            return s.tasks.delete(tid)
        return s.tasks.get(tid) if request.method == 'GET' else s.tasks.edit(tid, body())

    @app.get('/api/tasks/<tid>/recipients')
    def task_recipients(tid):
        return s.tasks.recipients(tid, request.args.to_dict())

    @app.get('/api/tasks/<tid>/replies')
    def task_replies(tid):
        return s.reply_reviews.replies(tid, request.args.to_dict())

    @app.get('/api/tasks/<tid>/followups')
    def followups(tid):
        return task_followups(s, tid, request.args.to_dict())

    @app.post('/api/tasks/<tid>/<action>')
    def task_action(tid, action):
        body()
        if action not in ('start', 'pause', 'resume'):
            raise LookupError('操作不存在')
        return getattr(s.tasks, action)(tid)

    @app.get('/api/tasks/<tid>/dashboard')
    def dashboard(tid):
        return s.tasks.dashboard(tid, request.args.get('after_event_id', 0))

    @app.get('/')
    @app.get('/overview')
    @app.get('/search')
    @app.get('/users')
    @app.get('/send-details')
    @app.get('/accounts')
    @app.get('/tasks')
    @app.get('/tasks/<tid>')
    @app.get('/tags')
    @app.get('/message-templates')
    @app.get('/tasks/<tid>/dashboard')
    def page(tid=None):
        return app.send_static_file('index.html')

    return app

@@END_FILE@@
@@FILE {"path": "web/conversation.py", "bytes": 8854, "sha256": "30332dc4264f68630e9dce1602319b5f22bf86ea0706a958bbce588553e22e64"}
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

@@END_FILE@@
@@FILE {"path": "web/db.py", "bytes": 14983, "sha256": "b5dd7d93d3d87f3da4c80838a835df1f9059ecd462f4caedf44f9b512991d710"}
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Missing fields in historical records retain the defaults they originally used.
LEGACY_SEND_POLICY = {'interval_seconds': 0, 'random_extra_seconds': 0, 'max_batch_size': 1,
                      'error_rate_percent': 80, 'min_attempts': 8, 'rest_minutes': 30}
DEFAULT_SEND_POLICY = {'interval_seconds': 200, 'random_extra_seconds': 100, 'max_batch_size': 2,
                       'error_rate_percent': 50, 'min_attempts': 10, 'rest_minutes': 30}


def dump(value):
    return json.dumps(value, ensure_ascii=False)


class Database:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'app.sqlite3'
        with self.connect() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, uid TEXT,
                    credential TEXT NOT NULL, status TEXT, can_search INTEGER DEFAULT 0,
                    can_send INTEGER DEFAULT 0, checked_at REAL, next_send_at REAL DEFAULT 0,
                    qr_status TEXT DEFAULT 'idle', qr_url TEXT, error TEXT DEFAULT '');
                CREATE UNIQUE INDEX IF NOT EXISTS account_uid ON accounts(uid) WHERE uid IS NOT NULL;
                CREATE TABLE IF NOT EXISTS searches (
                    id TEXT PRIMARY KEY, account_id TEXT, query TEXT, max_pages INTEGER,
                    completed_pages INTEGER DEFAULT 0, returned_count INTEGER DEFAULT 0, invalid_count INTEGER DEFAULT 0, status TEXT, cursor TEXT DEFAULT '0',
                    collect INTEGER, collection_done INTEGER DEFAULT 0, error TEXT DEFAULT '', created_at REAL);
                CREATE TABLE IF NOT EXISTS search_results (
                    search_id TEXT, uid TEXT, data TEXT, ordinal INTEGER,
                    PRIMARY KEY(search_id, uid));
                CREATE TABLE IF NOT EXISTS users (
                    uid TEXT PRIMARY KEY, data TEXT NOT NULL, overrides TEXT DEFAULT '{}',
                    note TEXT DEFAULT '', created_at REAL, updated_at REAL, deleted_at REAL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    account_id TEXT REFERENCES accounts(id), config TEXT NOT NULL,
                    status TEXT NOT NULL, phase TEXT DEFAULT 'idle', created_at REAL,
                    started_at REAL, finished_at REAL, next_send_at REAL, error TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS recipients (
                    task_id TEXT REFERENCES tasks(id), uid TEXT, data TEXT, ordinal INTEGER,
                    status TEXT DEFAULT 'pending', client_message_id TEXT, attempted_at REAL,
                    sent_at REAL, error TEXT DEFAULT '', PRIMARY KEY(task_id,uid));
                CREATE INDEX IF NOT EXISTS recipient_history ON recipients(uid,status,sent_at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, uid TEXT,
                    time REAL, type TEXT, detail TEXT);
                CREATE INDEX IF NOT EXISTS task_event ON events(task_id,id);
                CREATE INDEX IF NOT EXISTS recipient_result_event ON events(task_id,uid,type,id);
                CREATE TABLE IF NOT EXISTS message_conversations (
                    account_id TEXT REFERENCES accounts(id), uid TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, conversation_short_id TEXT NOT NULL,
                    older_cursor TEXT NOT NULL, newer_cursor TEXT NOT NULL,
                    history_complete INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    PRIMARY KEY(account_id,uid), UNIQUE(account_id,conversation_id));
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    account_id TEXT REFERENCES accounts(id), uid TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, message_index TEXT NOT NULL, sender_uid TEXT NOT NULL,
                    message_type INTEGER NOT NULL, created_at REAL, text TEXT, content TEXT NOT NULL,
                    PRIMARY KEY(account_id,message_id));
                CREATE INDEX IF NOT EXISTS conversation_message_user ON conversation_messages(uid,created_at);
                CREATE TABLE IF NOT EXISTS chat_sends (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), uid TEXT NOT NULL,
                    message TEXT NOT NULL, status TEXT NOT NULL, attempted_at REAL NOT NULL, finished_at REAL,
                    error TEXT NOT NULL DEFAULT '', diagnostic TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS chat_send_user ON chat_sends(uid,account_id,attempted_at);
                CREATE TABLE IF NOT EXISTS task_messages (
                    task_id TEXT NOT NULL REFERENCES tasks(id), uid TEXT NOT NULL, position INTEGER NOT NULL,
                    account_id TEXT NOT NULL REFERENCES accounts(id), kind TEXT NOT NULL,
                    text TEXT, image_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
                    client_message_id TEXT NOT NULL, attempted_at REAL, finished_at REAL,
                    error TEXT NOT NULL DEFAULT '', diagnostic TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(task_id,uid,position));
                CREATE INDEX IF NOT EXISTS task_message_sender ON task_messages(account_id,status,attempted_at);
                CREATE INDEX IF NOT EXISTS task_message_pair ON task_messages(uid,account_id);
                CREATE TABLE IF NOT EXISTS send_orders (
                    record_key TEXT PRIMARY KEY, account_id TEXT NOT NULL, uid TEXT NOT NULL,
                    send_number INTEGER NOT NULL, occurred_at REAL NOT NULL,
                    UNIQUE(account_id,uid,send_number));
                CREATE TABLE IF NOT EXISTS task_reply_reviews (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id), status TEXT NOT NULL,
                    anchor_at REAL NOT NULL, finished_at REAL);
                CREATE TABLE IF NOT EXISTS task_reply_targets (
                    task_id TEXT NOT NULL REFERENCES task_reply_reviews(task_id),
                    account_id TEXT NOT NULL, uid TEXT NOT NULL, since REAL NOT NULL,
                    phase TEXT NOT NULL DEFAULT 'older', status TEXT NOT NULL DEFAULT 'pending',
                    checked_at REAL, error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(task_id,account_id,uid));
                CREATE INDEX IF NOT EXISTS task_reply_pending ON task_reply_targets(status,task_id);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS tags (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS user_tags (
                    uid TEXT REFERENCES users(uid) ON DELETE CASCADE,
                    tag_id TEXT REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY(uid,tag_id));
                CREATE INDEX IF NOT EXISTS user_tag_lookup ON user_tags(tag_id);
                CREATE TABLE IF NOT EXISTS message_templates (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            ''')
            seeded = c.execute("INSERT OR IGNORE INTO metadata VALUES('default_tag_seeded','1')").rowcount
            if seeded:
                c.execute("INSERT INTO tags VALUES('used-car-dealer','二手车商','二手车商用户，入库时默认选中',strftime('%s','now'),strftime('%s','now'))")
            columns = {row['name'] for row in c.execute('PRAGMA table_info(searches)')}
            for name in ('returned_count', 'invalid_count'):
                if name not in columns:
                    c.execute(f'ALTER TABLE searches ADD COLUMN {name} INTEGER DEFAULT 0')
            columns = {row['name'] for row in c.execute('PRAGMA table_info(recipients)')}
            if 'send_message' not in columns:
                c.execute('ALTER TABLE recipients ADD COLUMN send_message TEXT')
            if 'sender_account_id' not in columns:
                c.execute('ALTER TABLE recipients ADD COLUMN sender_account_id TEXT REFERENCES accounts(id)')
                c.execute("""UPDATE recipients SET sender_account_id=(SELECT account_id FROM tasks WHERE id=task_id)
                    WHERE attempted_at IS NOT NULL OR status IN ('sending','sent','failed','uncertain')""")
            columns = {row['name'] for row in c.execute('PRAGMA table_info(chat_sends)')}
            if 'message_type' not in columns:
                c.execute("ALTER TABLE chat_sends ADD COLUMN message_type TEXT NOT NULL DEFAULT 'text'")
            if 'image_id' not in columns:
                c.execute('ALTER TABLE chat_sends ADD COLUMN image_id TEXT')
        self._migrate_shared_pool()
        with self.connect() as c:
            for table, additions in {
                'tasks': {'reply_check_next_at': 'REAL'},
                'recipients': {'followup_enqueued': 'INTEGER NOT NULL DEFAULT 0'},
                'task_messages': {'followup': 'INTEGER NOT NULL DEFAULT 0'},
                'task_reply_reviews': {'kind': "TEXT NOT NULL DEFAULT 'final'", 'generation': 'INTEGER NOT NULL DEFAULT 1'},
                'task_reply_targets': {'generation': 'INTEGER NOT NULL DEFAULT 1'},
                'conversation_messages': {'reply_number': 'INTEGER'},
            }.items():
                columns = {row['name'] for row in c.execute(f'PRAGMA table_info({table})')}
                for name, definition in additions.items():
                    if name not in columns:
                        c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
            c.execute('CREATE INDEX IF NOT EXISTS recipient_attempt_window ON recipients(status,attempted_at)')
            c.execute('CREATE INDEX IF NOT EXISTS recipient_sender_state ON recipients(sender_account_id,status)')
        self.path.chmod(0o600)
        from .send_order import backfill_send_orders, backfill_reply_numbers
        backfill_send_orders(self)
        backfill_reply_numbers(self)

    def _migrate_shared_pool(self):
        with self.connect() as c:
            # SQLite requires rebuilding the old NOT NULL column. Keep dependent
            # tables intact and validate their foreign keys before committing.
            c.execute('PRAGMA foreign_keys=OFF')
            c.execute('BEGIN IMMEDIATE')
            columns = {r['name']: r for r in c.execute('PRAGMA table_info(tasks)')}
            if columns['account_id']['notnull']:
                c.execute('''CREATE TABLE tasks_new (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    account_id TEXT REFERENCES accounts(id), config TEXT NOT NULL,
                    status TEXT NOT NULL, phase TEXT DEFAULT 'idle', created_at REAL,
                    started_at REAL, finished_at REAL, next_send_at REAL, error TEXT DEFAULT '')''')
                c.execute('INSERT INTO tasks_new SELECT * FROM tasks')
                c.execute('DROP TABLE tasks')
                c.execute('ALTER TABLE tasks_new RENAME TO tasks')
            c.execute('DROP INDEX IF EXISTS running_account')
            if 'execution_mode' not in columns:
                c.execute("ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'specified'")
                c.execute('ALTER TABLE tasks ADD COLUMN last_dispatched INTEGER NOT NULL DEFAULT 0')
            c.execute('''CREATE TABLE IF NOT EXISTS task_accounts (
                task_id TEXT REFERENCES tasks(id), account_id TEXT REFERENCES accounts(id),
                PRIMARY KEY(task_id,account_id))''')
            c.execute('CREATE INDEX IF NOT EXISTS account_tasks ON task_accounts(account_id,task_id)')
            columns = {r['name'] for r in c.execute('PRAGMA table_info(accounts)')}
            for name, definition in {
                'send_policy': "TEXT NOT NULL DEFAULT '{}'", 'send_enabled': 'INTEGER NOT NULL DEFAULT 1',
                'work_schedule': 'TEXT',
                'pool_enabled': 'INTEGER NOT NULL DEFAULT 0', 'rest_until': 'REAL NOT NULL DEFAULT 0',
                'error_window_start': 'REAL NOT NULL DEFAULT 0', 'rest_reason': "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    c.execute(f'ALTER TABLE accounts ADD COLUMN {name} {definition}')
            if not c.execute("SELECT 1 FROM metadata WHERE key='shared_account_pool'").fetchone():
                c.execute('INSERT OR IGNORE INTO task_accounts SELECT id,account_id FROM tasks WHERE account_id IS NOT NULL')
                for account in c.execute('SELECT id,next_send_at FROM accounts').fetchall():
                    tasks = c.execute("SELECT config,next_send_at FROM tasks WHERE account_id=? AND status IN ('pending','running','paused')", (account['id'],)).fetchall()
                    configs = [json.loads(t['config']) for t in tasks]
                    policy = dict(LEGACY_SEND_POLICY)
                    if configs:
                        for key in ('interval_seconds', 'random_extra_seconds'):
                            policy[key] = max(t.get(key, 0) for t in configs)
                        policy['max_batch_size'] = min(t.get('max_batch_size', 1) for t in configs)
                    wait = max([account['next_send_at'] or 0] + [t['next_send_at'] or 0 for t in tasks])
                    c.execute('UPDATE accounts SET send_policy=?,next_send_at=? WHERE id=?', (dump(policy), wait, account['id']))
                c.execute("INSERT INTO metadata VALUES('shared_account_pool','1')")
            if c.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('账号池迁移失败：存在未关联的历史记录')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def all(self, sql, params=()):
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        if not rows:
            raise LookupError('记录不存在')
        return rows[0]

    def run(self, sql, params=()):
        with self.connect() as c:
            return c.execute(sql, params).rowcount

@@END_FILE@@
@@FILE {"path": "web/douyin.py", "bytes": 9149, "sha256": "d736d6e9c2e9dfa096ee36a0ce033075dd7336225968df4505330c87cd8e747a"}
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

@@END_FILE@@
@@FILE {"path": "web/filters.py", "bytes": 5007, "sha256": "dca00f7bec69f97c9ae5384dd6c8657e8da5d67b8161a69cb7b3e618982af983"}
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


NUMERIC = {'min_followers': 'follower_count', 'min_likes': 'received_like_count'}


def integer(value, label, minimum=0, maximum=None):
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise ValueError(f'{label}请输入整数')
    result = int(value)
    if result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f'{label}超出允许范围')
    return result


def date_bound(value, end=False):
    try:
        dt = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        return (dt + timedelta(days=int(end))).timestamp()
    except (TypeError, ValueError):
        raise ValueError('日期格式应为 YYYY-MM-DD') from None


def select(rows, filters):
    if not isinstance(filters, dict):
        raise ValueError('筛选条件格式错误')
    if 'min_collections' in filters:
        raise ValueError('收藏筛选已移除，请刷新页面后重新筛选')
    thresholds = {field: integer(filters[key], key) for key, field in NUMERIC.items()
                  if filters.get(key) not in (None, '')}
    bounds = {}
    for stem, field in [('created', 'created_at'), ('sent', 'last_sent_at')]:
        lo = date_bound(filters[stem+'_from']) if filters.get(stem+'_from') else None
        hi = date_bound(filters[stem+'_to'], True) if filters.get(stem+'_to') else None
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError('开始日期不能晚于结束日期')
        bounds[field] = lo, hi
    sent = filters.get('sent', 'all')
    if sent not in ('', 'all', 'sent', 'unsent', 'replied'):
        raise ValueError('发送状态无效')
    blue_v = filters.get('blue_v') or 'all'
    if blue_v not in ('all', 'yes', 'no', 'unknown'):
        raise ValueError('蓝 V 筛选条件无效')
    query = str(filters.get('query') or '').strip().casefold()
    tags = set(tag_ids(filters.get('tag_ids', [])))
    tag_mode = filters.get('tag_mode') or 'any'
    if tag_mode not in ('any', 'all'):
        raise ValueError('标签匹配方式无效')
    result = []
    for row in rows:
        user_tags = {tag['id'] for tag in row.get('tags', [])}
        if tags and (not tags.intersection(user_tags) if tag_mode == 'any' else not tags.issubset(user_tags)):
            continue
        if blue_v != 'all' and row.get('is_blue_v') is not {'yes': True, 'no': False, 'unknown': None}[blue_v]:
            continue
        if query and not any(query in str(row.get(k) or '').casefold()
                             for k in ['nickname', 'douyinhao', 'uid', 'name', 'source_keyword']):
            continue
        if sent == 'sent' and not row.get('sent'):
            continue
        if sent == 'unsent' and row.get('sent'):
            continue
        if sent == 'replied' and not row.get('replied'):
            continue
        if any(row.get(field) is None or row[field] <= value for field, value in thresholds.items()):
            continue
        if any((lo is not None and (value is None or value < lo)) or
               (hi is not None and (value is None or value >= hi))
               for field, (lo, hi) in bounds.items()
               for value in [row.get('_sent_filter_at', row.get(field)) if field == 'last_sent_at' else row.get(field)]):
            continue
        result.append(row)
    order = filters.get('sort') or 'created_desc'
    sorts = {'created_desc': 'created_at', 'followers_desc': 'follower_count',
             'likes_desc': 'received_like_count', 'name': 'nickname'}
    if order not in sorts:
        raise ValueError('排序方式无效')
    field = sorts[order]
    if order == 'name':
        return sorted(result, key=lambda r: (str(r.get(field) or ''), r.get('uid', '')))
    return sorted(result, key=lambda r: (r.get(field) if r.get(field) is not None else -1, r.get('uid', '')), reverse=True)


def paginate(rows, filters):
    page = integer(filters.get('page', 1), '页码', 1)
    size = integer(filters.get('page_size', 20), '每页条数', 1, 100)
    return {'items': rows[(page-1)*size:page*size], 'total': len(rows), 'page': page, 'page_size': size}


def chosen_ids(value):
    if not isinstance(value, list) or not value:
        raise ValueError('请至少选择一位用户')
    if any(not isinstance(x, str) or not x for x in value):
        raise ValueError('用户 ID 格式错误')
    return list(dict.fromkeys(value))


def tag_ids(value):
    if isinstance(value, str):
        value = value.split(',') if value else []
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or not x or ',' in x for x in value):
        raise ValueError('请选择有效标签，最多 100 个')
    return list(dict.fromkeys(value))


def id_batches(uids):
    # Keep even three repeated IN clauses below SQLite's 999-variable limit.
    for start in range(0, len(uids), 300):
        yield uids[start:start+300]

@@END_FILE@@
@@FILE {"path": "web/library.py", "bytes": 4746, "sha256": "794e7ad46302270b2742beaab3e1540738dbd9cd81187475caf79198ff682406"}
import sqlite3
import uuid
from .filters import id_batches, chosen_ids, tag_ids


DEFAULT_TAG = 'used-car-dealer'
CATALOGS = {
    'tags': ('tags', {'name': (64, True), 'description': (500, False)}),
    'message-templates': ('message_templates', {'title': (120, True), 'description': (500, False), 'content': (2000, True)}),
}


class Library:
    def __init__(self, service):
        self.s, self.db = service, service.db

    def list(self, kind):
        table, _ = CATALOGS[kind]
        if kind == 'tags':
            rows = self.db.all('''SELECT t.*, (SELECT count(*) FROM user_tags ut JOIN users u ON u.uid=ut.uid
                WHERE ut.tag_id=t.id AND u.deleted_at IS NULL) AS user_count FROM tags t ORDER BY created_at,id''')
            for row in rows:
                row['is_default'] = row['id'] == DEFAULT_TAG
        else:
            rows = self.db.all(f'SELECT * FROM {table} ORDER BY updated_at DESC,id')
        return {'items': rows}

    def save(self, kind, data, item_id=None):
        table, fields = CATALOGS[kind]
        if not data or set(data) - fields.keys():
            raise ValueError('请填写有效的名称、描述或话术内容')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone() if item_id else None
            if item_id and not old:
                raise LookupError('记录不存在')
            values = {}
            for field, (limit, required) in fields.items():
                value = data.get(field, old[field] if old else '')
                if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
                    names = {'name': '标签名称', 'title': '话术标题', 'description': '描述', 'content': '话术内容'}
                    raise ValueError(f'{names[field]}格式无效，最多 {limit} 字' + ('且不能为空' if required else ''))
                values[field] = value.strip()
            now = self.s.clock()
            try:
                if item_id:
                    assignments = ','.join(f'{key}=?' for key in values)
                    c.execute(f'UPDATE {table} SET {assignments},updated_at=? WHERE id=?', (*values.values(), now, item_id))
                else:
                    item_id = uuid.uuid4().hex
                    columns = ','.join(values)
                    marks = ','.join('?' for _ in values)
                    c.execute(f'INSERT INTO {table}(id,{columns},created_at,updated_at) VALUES(?,{marks},?,?)',
                              (item_id, *values.values(), now, now))
            except sqlite3.IntegrityError:
                raise ValueError('标签名称已存在，请使用其他名称') from None
            return dict(c.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone())

    def delete(self, kind, item_id):
        table, _ = CATALOGS[kind]
        self.db.run(f'DELETE FROM {table} WHERE id=?', (item_id,))
        return {'ok': True}

    def validate_tags(self, c, values):
        if not isinstance(values, list):
            raise ValueError('标签选项格式错误')
        ids = tag_ids(values)
        existing = {r['id'] for r in c.execute('SELECT id FROM tags')}
        if set(ids) - existing:
            raise ValueError('部分标签已被删除，请刷新标签后重新选择')
        return ids

    def user_tags(self, data):
        uids = chosen_ids(data.get('uids'))
        action = data.get('action')
        if action not in ('add', 'remove', 'replace'):
            raise ValueError('标签操作无效')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            ids = self.validate_tags(c, data.get('tag_ids'))
            if not ids and action != 'replace':
                raise ValueError('请至少选择一个标签')
            found = 0
            for batch in id_batches(uids):
                marks = ','.join('?' for _ in batch)
                found += c.execute(f'SELECT count(*) FROM users WHERE uid IN ({marks}) AND deleted_at IS NULL', batch).fetchone()[0]
            if found != len(uids):
                raise ValueError('部分用户已被移除，请重新选择')
            for uid in uids:
                if action == 'replace':
                    c.execute('DELETE FROM user_tags WHERE uid=?', (uid,))
                for tag_id in ids:
                    if action == 'remove':
                        c.execute('DELETE FROM user_tags WHERE uid=? AND tag_id=?', (uid, tag_id))
                    else:
                        c.execute('INSERT OR IGNORE INTO user_tags VALUES(?,?)', (uid, tag_id))
        return {'updated': len(uids)}

@@END_FILE@@
@@FILE {"path": "web/login.py", "bytes": 8354, "sha256": "66d54ec44bc7856026d2f52bddebd67d333596844e46fe06da4a9647dd75434c"}
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

@@END_FILE@@
@@FILE {"path": "web/media.py", "bytes": 3993, "sha256": "45108237857156f1f0702016630d5ee86a83b8d70d34629451c90ae5853124b4"}
"""Validated local image assets shared by tasks and conversations."""
import base64
import binascii
import io
import json
import re
import uuid

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_UPLOAD_BODY = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 4096
FORMATS = {'PNG': ('.png', 'image/png'), 'JPEG': ('.jpg', 'image/jpeg'),
           'GIF': ('.gif', 'image/gif'), 'WEBP': ('.webp', 'image/webp')}


class Media:
    def __init__(self, service):
        self.directory = (service.db.directory / 'media').resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def upload(self, data):
        if not isinstance(data, dict) or set(data) - {'data', 'name'}:
            raise ValueError('请上传本地图片文件')
        encoded, name = data.get('data'), data.get('name', '图片')
        if not isinstance(name, str) or len(name) > 255:
            raise ValueError('图片名称无效')
        if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError('图片不能为空且不能超过 5 MiB')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('图片内容格式无效') from None
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('图片不能为空且不能超过 5 MiB')
        try:
            with Image.open(io.BytesIO(raw)) as img:
                fmt, width, height = img.format, img.width, img.height
                if fmt not in FORMATS:
                    raise ValueError('仅支持 PNG、JPEG、GIF、WebP 图片')
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError('图片像素不能超过 2000 万')
                img.verify()
            # Decode after structural verification; animation has a total pixel budget.
            with Image.open(io.BytesIO(raw)) as img:
                pixels = 0
                for frame in range(getattr(img, 'n_frames', 1)):
                    img.seek(frame)
                    pixels += img.width * img.height
                    if pixels > MAX_IMAGE_PIXELS:
                        raise ValueError('图片总像素不能超过 2000 万')
                    img.load()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
            raise ValueError('图片内容无效或无法完整解码') from None
        media_id = uuid.uuid4().hex
        name = name.replace('\\', '/').rsplit('/', 1)[-1]
        name = ''.join(c for c in name if c.isprintable()).strip() or '图片'
        metadata = {'id': media_id, 'name': name, 'width': width, 'height': height,
                    'size': len(raw), 'url': '/api/media/' + media_id}
        extension, _ = FORMATS[fmt]
        with (self.directory / (media_id + extension)).open('xb') as output:
            output.write(raw)
        with (self.directory / (media_id + '.json')).open('x', encoding='utf-8') as output:
            json.dump({**metadata, 'format': fmt}, output, ensure_ascii=False)
        return metadata

    def _record(self, media_id):
        if not isinstance(media_id, str) or not re.fullmatch('[0-9a-f]{32}', media_id):
            raise LookupError('图片不存在')
        try:
            return json.loads((self.directory / (media_id + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise LookupError('图片不存在') from None

    def get(self, media_id):
        record = self._record(media_id)
        return {key: record[key] for key in ('id', 'name', 'width', 'height', 'size', 'url')}

    def path(self, media_id):
        record = self._record(media_id)
        path = self.directory / (media_id + FORMATS[record['format']][0])
        if not path.is_file():
            raise LookupError('图片不存在')
        return path

@@END_FILE@@
@@FILE {"path": "web/messages.py", "bytes": 8647, "sha256": "9e4871f4f42e59ee2b8b889d4450dde54d271d8af5b3af9a64d8f5988c948143"}
"""Persist one history page and its checkpoint in the same transaction."""
from dy_apis.douyin_im_history import HistoryError
from .filters import integer, paginate
from .send_order import assign_send_number, assign_reply_numbers
from .send_records import message_records, message_preview


class Messages:
    def __init__(self, service):
        self.s, self.db = service, service.db

    def _user(self, uid):
        return self.db.one('SELECT uid FROM users WHERE uid=? AND deleted_at IS NULL', (uid,))

    def list(self, uid, filters):
        self._user(uid)
        sql = '''SELECT m.account_id,a.name AS account_name,m.message_id,m.conversation_id,
            m.sender_uid,m.message_index,m.message_type,m.created_at,m.text,
            CASE WHEN m.sender_uid=a.uid THEN 'outgoing' WHEN m.sender_uid=m.uid THEN 'incoming'
                ELSE 'system' END AS direction
            FROM conversation_messages m JOIN accounts a ON a.id=m.account_id WHERE m.uid=?'''
        args = [uid]
        if filters.get('account_id'):
            sql += ' AND m.account_id=?'
            args.append(filters['account_id'])
        sql += ' ORDER BY m.created_at DESC,CAST(m.message_index AS INTEGER) DESC,m.account_id,m.message_id'
        result = paginate(self.db.all(sql, args), filters)
        states = self.db.all('''SELECT account_id,conversation_id,conversation_short_id,older_cursor,
            newer_cursor,history_complete,updated_at FROM message_conversations WHERE uid=? ORDER BY account_id''', (uid,))
        result['conversations'] = [self._state(row) for row in states
                                   if not filters.get('account_id') or row['account_id'] == filters['account_id']]
        return result

    @staticmethod
    def _state(row):
        return {**row, 'history_complete': bool(row['history_complete'])}

    def sync(self, uid, data):
        self._user(uid)
        if set(data) - {'account_id', 'conversation_id', 'conversation_short_id', 'direction'}:
            raise ValueError('历史同步参数无效')
        aid = data.get('account_id')
        if not isinstance(aid, str) or not aid:
            raise ValueError('请选择执行账号')
        direction = data.get('direction', 'older')
        if direction not in ('older', 'newer'):
            raise ValueError('同步方向无效')
        lock = self.s.accounts.lock(aid)
        if not lock.acquire(blocking=False):
            raise ValueError('账号正在请求中，请稍后同步')
        try:
            account = self.s.accounts.get(aid)
            if account['status'] != 'ready' or account['qr_status'] != 'confirmed':
                raise ValueError('请先完成该账号鉴权')
            existing = self.db.all('SELECT * FROM message_conversations WHERE account_id=? AND uid=?', (aid, uid))
            state = existing[0] if existing else None
            conv = data.get('conversation_id', state['conversation_id'] if state else None)
            short = data.get('conversation_short_id', state['conversation_short_id'] if state else None)
            if (not isinstance(conv, str) or conv not in
                    (f"0:1:{account['uid']}:{uid}", f"0:1:{uid}:{account['uid']}")):
                raise ValueError('会话参与者与所选账号、用户不一致')
            short = str(integer(short, '会话 short ID', 1, 2**63-1))
            if state and (state['conversation_id'] != conv or state['conversation_short_id'] != short):
                raise ValueError('会话信息与已保存历史不一致')
            if state and direction == 'older' and state['history_complete']:
                return {**self._state(state), 'inserted': 0, 'received': 0, 'has_more': False}
            initial = not state or (state['older_cursor']=='0' and state['newer_cursor']=='0' and not state['history_complete'])
            request_direction = 'latest' if initial else direction
            cursor = state[direction + '_cursor'] if state else '0'
            try:
                page = self.s.adapter.history_page(self.s.accounts.auth(aid), conv, short,
                                                   direction=request_direction, cursor=cursor, count=50)
                next_cursor = integer(page['next_cursor'], '消息游标', 0, 2**63-1)
                more = page['has_more']
                if type(more) is not bool or not isinstance(page['messages'], list):
                    raise ValueError('invalid page')
                if state and not initial:
                    if direction == 'older' and (next_cursor > int(cursor) or (more and next_cursor >= int(cursor))):
                        raise ValueError('older cursor did not progress')
                    if direction == 'newer' and (next_cursor < int(cursor) or (more and next_cursor <= int(cursor))):
                        raise ValueError('newer cursor did not progress')
                elif more and next_cursor == 0:
                    raise ValueError('initial cursor did not progress')
                for message in page['messages']:
                    if message['conversation_id'] != conv:
                        raise ValueError('foreign conversation')
                    for key in ('message_id', 'index', 'sender_uid'):
                        integer(message[key], key, 1, 2**63-1)
                newest = max([int(m['index']) for m in page['messages']], default=0)
                if state and not initial and direction == 'newer' and newest > next_cursor:
                    raise ValueError('newer checkpoint precedes returned messages')
            except HistoryError:
                raise
            except Exception as exc:
                raise HistoryError('history_sync_failed', exception_type=type(exc).__name__) from None
            older = str(next_cursor) if initial or direction == 'older' else state['older_cursor']
            newer = (str(newest) if initial else str(next_cursor) if direction == 'newer' else state['newer_cursor'])
            complete = not more if initial or direction == 'older' else bool(state['history_complete'])
            now, inserted = self.s.clock(), 0
            known = {r['message_id'] for r in message_records(self.s,uid,aid)
                     if r['message_source']!='conversation' and r.get('message_id')}
            with self.db.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                for m in sorted(page['messages'],key=lambda m:(m['created_at'] or 0,int(m['index']))):
                    existing_message = c.execute('SELECT uid,conversation_id FROM conversation_messages WHERE account_id=? AND message_id=?',
                                                 (aid, m['message_id'])).fetchone()
                    if existing_message and (existing_message['uid'] != uid or existing_message['conversation_id'] != conv):
                        raise HistoryError('message_identity_conflict')
                    inserted += existing_message is None
                    c.execute('''INSERT INTO conversation_messages
                        (account_id,uid,conversation_id,message_id,message_index,sender_uid,message_type,created_at,text,content)
                        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET
                        text=excluded.text,content=excluded.content,message_type=excluded.message_type''',
                        (aid, uid, conv, m['message_id'], m['index'], m['sender_uid'], m['message_type'],
                         m['created_at'], m['text'], m['content']))
                    if m['sender_uid']==account['uid'] and m['message_id'] not in known and message_preview(m)[0]:
                        assign_send_number(c,f"platform:{aid}:{m['message_id']}",aid,uid,m['created_at'],only_if_newer=True)
                assign_reply_numbers(c,aid,uid)
                c.execute('''INSERT INTO message_conversations
                    (account_id,uid,conversation_id,conversation_short_id,older_cursor,newer_cursor,history_complete,updated_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account_id,uid) DO UPDATE SET
                    older_cursor=excluded.older_cursor,newer_cursor=excluded.newer_cursor,
                    history_complete=excluded.history_complete,updated_at=excluded.updated_at''',
                    (aid, uid, conv, short, older, newer, complete, now))
            state = self.db.one('SELECT * FROM message_conversations WHERE account_id=? AND uid=?', (aid, uid))
            return {**self._state(state), 'inserted': inserted, 'received': len(page['messages']), 'has_more': more}
        finally:
            lock.release()

@@END_FILE@@
@@FILE {"path": "web/outbound.py", "bytes": 4746, "sha256": "ead241dad207d18a833322e121748a65c205b830cc56efbc6e98662be40e375a"}
"""Shared text/image validation and contact eligibility for tasks and chat."""
from .send_records import message_preview


def normalize_message(service, value):
    if not isinstance(value, dict):
        raise ValueError('请选择一条文字或图片消息')
    if value.get('kind') == 'text' and set(value) <= {'kind', 'text'}:
        text = value.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError('文字消息应为 1～2000 字')
        return {'kind': 'text', 'text': text.strip()}
    if value.get('kind') == 'image' and set(value) <= {'kind', 'image_id'}:
        asset = service.media.get(value.get('image_id'))
        return {'kind': 'image', 'image_id': asset['id']}
    raise ValueError('一条消息只能选择文字或图片')


def message_summary(value):
    return value['text'] if value['kind'] == 'text' else '[图片]'


def message_config(service, data, current=None):
    current = current or {}
    if 'first_messages' in data:
        candidates = data['first_messages']
    elif set(data) & {'message', 'first_message'} or 'first_messages' not in current:
        first = data.get('first_message', current.get('first_message'))
        if first is None or ('message' in data and 'first_message' not in data):
            first = {'kind': 'text', 'text': data.get('message', current.get('message', ''))}
        candidates = [first]
    else:
        candidates = current['first_messages']
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 20:
        raise ValueError('首次消息请选择 1～20 条候选文案')
    candidates = [normalize_message(service, value) for value in candidates]
    if len(candidates) > 1 and any(value['kind'] != 'text' for value in candidates):
        raise ValueError('首次多候选仅支持文字，图片只能单条发送')
    first = candidates[0]
    replies = data.get('reply_messages', current.get('reply_messages', [first]))
    if not isinstance(replies, list) or len(replies) > 20:
        raise ValueError('回复后消息应为列表，最多 20 条')
    return {'first_messages': candidates, 'first_message': first, 'reply_messages': [normalize_message(service, m) for m in replies],
            'message': message_summary(first)}


def send_permission(c, aid, uid):
    # A UID-level reservation prevents two accounts racing to send the first
    # message. The same account's task and chat also share an account lock.
    busy = c.execute("""SELECT 1 FROM recipients WHERE uid=? AND status='sending'
        UNION ALL SELECT 1 FROM chat_sends WHERE uid=? AND status='sending' LIMIT 1""", (uid, uid)).fetchone()
    if busy:
        return {'can_send': False, 'state': 'busy', 'reason': '该联系人有消息正在发送，请稍后再试'}
    for row in c.execute('SELECT * FROM conversation_messages WHERE uid=? AND account_id=? AND sender_uid=uid', (uid, aid)):
        if message_preview(row)[0]:
            return {'can_send': True, 'state': 'replied', 'reason': '对方已回复当前账号，可按顺序发送多条消息'}
    sent = c.execute("""SELECT 1 FROM recipients WHERE uid=? AND status='sent'
        UNION ALL SELECT 1 FROM task_messages WHERE uid=? AND status='sent'
        UNION ALL SELECT 1 FROM chat_sends WHERE uid=? AND status='sent' LIMIT 1""", (uid, uid, uid)).fetchone()
    if not sent:
        for row in c.execute('''SELECT m.* FROM conversation_messages m JOIN accounts a ON a.id=m.account_id
            WHERE m.uid=? AND m.sender_uid IN (a.uid,m.uid)''', (uid,)):
            if message_preview(row)[0]:
                sent = True
                break
    if sent:
        return {'can_send': False, 'state': 'unreplied', 'reason': '已成功发送，尚未确认对方回复当前账号，跳过发送'}
    return {'can_send': True, 'state': 'new', 'reason': '未成功发送，只能发送一条文字或一张图片'}


def dispatch_message(service, auth, uid, message, message_id):
    if message['kind'] == 'image':
        return service.adapter.send_image(auth, uid, service.media.path(message['image_id']), message_id)
    return service.adapter.send(auth, uid, message['text'], message_id)


def remember_conversation(c, account, uid, accepted, now):
    if not isinstance(accepted, dict):
        return
    conv, short = accepted.get('conversation_id'), str(accepted.get('conversation_short_id', ''))
    if (conv in (f"0:1:{account['uid']}:{uid}", f"0:1:{uid}:{account['uid']}")
            and short.isascii() and short.isdigit() and 0 < int(short) < 2**63):
        c.execute('INSERT OR IGNORE INTO message_conversations VALUES(?,?,?,?,?,?,?,?)',
                  (account['id'],uid,conv,short,'0','0',False,now))

@@END_FILE@@
@@FILE {"path": "web/overview.py", "bytes": 7988, "sha256": "6171491d970a805ef19112742ebbc1e270f286dfe4e5bed89693046cd2a04ae8"}
"""Workspace statistics from one SQLite snapshot; no platform requests."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .filters import integer, paginate
from .send_records import TASK_SENDS_SQL, TASK_SEND_STATS_SQL


def snapshot(service, filters):
    days = integer(filters.get('days', 7), '统计天数', 1)
    if days not in (7, 30):
        raise ValueError('统计范围支持近 7 天或近 30 天')
    page = integer(filters.get('timeline_page', 1), '时间轴页码', 1)
    now = service.clock()
    today = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0)
    beginning = today-timedelta(days=days-1)
    start = beginning.timestamp()
    daily = {(beginning+timedelta(days=i)).date().isoformat():
             {'date': (beginning+timedelta(days=i)).date().isoformat(),
              'sent_people': 0, 'sent_messages': 0, 'completed_tasks': 0} for i in range(days)}
    with service.db.connect() as c:
        c.execute('BEGIN')
        task_counts = {r['status']: r['n'] for r in c.execute('SELECT status,count(*) AS n FROM tasks GROUP BY status')}
        summary = {'total_tasks': sum(task_counts.values()),
                   'running_tasks': task_counts.get('running', 0), 'pending_tasks': task_counts.get('pending', 0),
                   'paused_tasks': task_counts.get('paused', 0), 'deleted_tasks': task_counts.get('deleted', 0),
                   'completed_tasks': c.execute('SELECT count(*) FROM tasks WHERE finished_at IS NOT NULL').fetchone()[0],
                   'sent_people': c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(DISTINCT uid) FROM sends WHERE status='sent'").fetchone()[0],
                   'sent_messages': c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(*) FROM sends WHERE status='sent'").fetchone()[0],
                   'failed_messages': c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(*) FROM sends WHERE status='failed'").fetchone()[0]}
        for r in c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT strftime('%Y-%m-%d',sent_at,'unixepoch','+8 hours') AS day,count(DISTINCT uid) AS people,count(*) AS messages FROM sends WHERE status='sent' AND sent_at>=? AND sent_at<=? GROUP BY day", (start, now)):
            daily[r['day']].update(sent_people=r['people'], sent_messages=r['messages'])
        for r in c.execute("SELECT strftime('%Y-%m-%d',finished_at,'unixepoch','+8 hours') AS day,count(*) AS n FROM tasks WHERE finished_at>=? AND finished_at<=? GROUP BY day", (start, now)):
            daily[r['day']]['completed_tasks'] = r['n']
        range_totals = {'sent_people': c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(DISTINCT uid) FROM sends WHERE status='sent' AND sent_at>=? AND sent_at<=?", (start, now)).fetchone()[0],
                        'completed_tasks': sum(r['completed_tasks'] for r in daily.values())}
        codes = {}
        for row in c.execute(f"""WITH sends AS ({TASK_SENDS_SQL}) SELECT r.status,r.diagnostic AS detail FROM sends r
                WHERE r.status IN ('sent','failed') AND r.attempted_at>=? AND r.attempted_at<=?""", (start, now)):
            try:
                diagnostic = json.loads(row['detail'] or '{}')
            except ValueError:
                diagnostic = {}
            code = diagnostic.get('business_code') if isinstance(diagnostic, dict) else None
            if type(code) is not int:
                code = None
            item = codes.setdefault(code, {'code': code, 'count': 0, 'sent': 0, 'failed': 0})
            item['count'] += 1
            item[row['status']] += 1
        business_codes = {'start': start, 'end': now, 'total': sum(r['count'] for r in codes.values()),
                          'items': sorted(codes.values(), key=lambda r: (-r['count'], r['code'] is None, r['code'] or 0))}
        running = []
        account_snapshots = {}
        for row in c.execute("SELECT id FROM tasks WHERE status='running' ORDER BY started_at,id").fetchall():
            task = service.tasks._snapshot(c, row['id'], account_snapshots)
            current = c.execute("SELECT data,status FROM recipients WHERE task_id=? AND status IN ('sending','pending') ORDER BY CASE status WHEN 'sending' THEN 0 ELSE 1 END,ordinal LIMIT 1", (row['id'],)).fetchone()
            task['current'] = {**json.loads(current['data']), 'status': current['status']} if current else None
            task['activity'] = ('sending' if current and current['status']=='sending' else
                                'scheduled' if (task['scheduled_at'] or 0)>now else
                                'waiting' if (task['next_send_at'] or 0)>now else 'queued')
            running.append(task)
        account_errors = {'window_minutes': 30, 'start': now-1800, 'end': now, 'items': []}
        associated = {}
        for task in running:
            active = c.execute("""SELECT r.data,r.sender_account_id,a.name AS account_name FROM recipients r
                JOIN accounts a ON a.id=r.sender_account_id WHERE r.task_id=? AND r.status='sending' ORDER BY r.ordinal""", (task['id'],)).fetchall()
            task['current_recipients'] = [{**json.loads(r['data']), 'account_id': r['sender_account_id'],
                                          'account_name': r['account_name'], 'status': 'sending'} for r in active]
            aids = {a['id'] for a in task['accounts']} | {r['sender_account_id'] for r in active}
            for aid in aids:
                associated.setdefault(aid, []).append(task)
        for aid, tasks in associated.items():
            if aid not in account_snapshots:
                account_snapshots[aid] = service.accounts.snapshot(c, aid)
            account = account_snapshots[aid]
            counts = account['recent']
            account_errors['items'].append({
                'account_id': aid, 'account_name': account['name'],
                'task_id': tasks[0]['id'], 'task_name': '、'.join(t['name'] for t in tasks),
                'tasks': [{'id': t['id'], 'name': t['name']} for t in tasks],
                **counts, 'dispatch_state': account['dispatch_state'],
                'next_send_at': account['next_send_at'], 'available_at': account['available_at'],
                'rest_until': account['rest_until'], 'rest_reason': account['rest_reason'],
            })
        rows = [dict(r) for r in c.execute('''SELECT t.id,t.name,t.status,t.created_at,t.started_at,t.finished_at,
                    CASE WHEN t.execution_mode='auto' THEN '共享账号池' ELSE
                    (SELECT group_concat(a.name,'、') FROM task_accounts ta JOIN accounts a ON a.id=ta.account_id WHERE ta.task_id=t.id) END AS account_name,COALESCE(t.started_at,t.created_at) AS began,
                    COALESCE(t.finished_at,CASE WHEN t.status='deleted' THEN COALESCE(d.time,t.created_at)
                    WHEN t.status='pending' THEN t.created_at ELSE ? END) AS ended,
                    t.status='deleted' AS deleted
                FROM tasks t
                LEFT JOIN (SELECT task_id,max(time) AS time FROM events WHERE type='deleted' GROUP BY task_id) d ON d.task_id=t.id
                WHERE t.created_at<=? ORDER BY began DESC,t.id''', (now, now))
                if r['ended']>=start and r['began']<=now]
        timeline = paginate(rows, {'page': page, 'page_size': 20})
        # Zoom to actual activity within the selected window; paused/waiting time remains visible.
        axis_start = max(start, min((r['began'] for r in rows), default=start))
        axis_end = min(now, max((r['ended'] for r in rows), default=now))
        timeline.update(start=axis_start, end=max(axis_start+60, axis_end))
    return {'now': now, 'days': days, 'summary': summary, 'range_totals': range_totals,
            'running': running, 'account_errors': account_errors,
            'business_codes': business_codes,
            'daily': list(daily.values()), 'timeline': timeline}

@@END_FILE@@
@@FILE {"path": "web/reply_review.py", "bytes": 16231, "sha256": "8214291f0bfb685cceef2e22d6922bc91e88743b3fcc2ad39e58aa71343f080f"}
"""Durable periodic reply checks and one final read-only task review."""
import json
import threading

from .send_records import TASK_SENDS_SQL, message_preview
from .filters import paginate
from utils.send_diagnostics import exception_diagnostic


def has_followups(config):
    # Older tasks implicitly reuse their first message after a reply.
    return bool(config.get('reply_messages', [config.get('first_message')]))


class ReviewIncomplete(Exception):
    pass


class ReplyReviews:
    def __init__(self, service):
        self.s, self.db = service, service.db
        self.guard = threading.Lock()
        self.worker = None
        # Resume an interrupted read at its stored phase/cursor, never a send.
        self.db.run("UPDATE task_reply_targets SET status='pending' WHERE status='running'")
        with self.db.connect() as c:
            for task in c.execute('SELECT id,config FROM tasks').fetchall():
                config = json.loads(task['config'])
                if not has_followups(config):
                    self.schedule(c,task['id'],config)

    def schedule(self, c, tid, config):
        interval = config.get('reply_check_interval_minutes', 0) if has_followups(config) else 0
        if not has_followups(config):
            # Invalidate active pages without reusing their generation if enabled
            # again. Completed history remains stored, but hidden while disabled.
            active = c.execute("SELECT 1 FROM task_reply_reviews WHERE task_id=? AND status IN ('pending','running')",(tid,)).fetchone()
            if active:
                c.execute('DELETE FROM task_reply_targets WHERE task_id=?',(tid,))
                c.execute("UPDATE task_reply_reviews SET status='completed',kind='periodic',finished_at=?,generation=generation+1 WHERE task_id=?",(self.s.clock(),tid))
        c.execute('''UPDATE tasks SET reply_check_next_at=CASE WHEN status='running' AND ?>0
            THEN ? ELSE NULL END WHERE id=?''', (interval,self.s.clock()+interval*60,tid))

    def enqueue(self, c, tid, anchor, kind='final'):
        config = json.loads(c.execute('SELECT config FROM tasks WHERE id=?',(tid,)).fetchone()['config'])
        if not has_followups(config):
            self.schedule(c,tid,config)
            return
        old = c.execute('SELECT * FROM task_reply_reviews WHERE task_id=?',(tid,)).fetchone()
        if old and old['kind']=='final':
            return
        generation = old['generation']+1 if old else 1
        # Completion replaces the round atomically. A request already in flight
        # may save its page, but its old generation cannot settle these targets.
        c.execute('DELETE FROM task_reply_targets WHERE task_id=?',(tid,))
        c.execute('''INSERT INTO task_reply_reviews(task_id,status,anchor_at,finished_at,kind,generation)
            VALUES(?,'pending',?,NULL,?,?) ON CONFLICT(task_id) DO UPDATE SET
            status='pending',anchor_at=excluded.anchor_at,finished_at=NULL,
            kind=excluded.kind,generation=excluded.generation''',(tid,anchor,kind,generation))
        c.execute('UPDATE tasks SET reply_check_next_at=NULL WHERE id=?',(tid,))
        c.execute(f"""WITH sends AS ({TASK_SENDS_SQL})
            INSERT INTO task_reply_targets(task_id,account_id,uid,since,generation)
            SELECT task_id,COALESCE(account_id,''),uid,MIN(COALESCE(attempted_at,sent_at,0)),?
            FROM sends WHERE task_id=? AND status='sent' GROUP BY task_id,account_id,uid""", (generation,tid))
        if kind=='periodic':
            for account_id, uid in self._replied(c,tid):
                c.execute('DELETE FROM task_reply_targets WHERE task_id=? AND account_id=? AND uid=?',(tid,account_id,uid))
        self._finish(c, tid)

    def _replied(self, c, tid):
        return {(m['account_id'],m['uid']) for m in c.execute('''SELECT m.* FROM conversation_messages m
            JOIN task_reply_targets r ON r.account_id=m.account_id AND r.uid=m.uid
            WHERE r.task_id=? AND m.sender_uid=m.uid''',(tid,)) if message_preview(m)[0]}

    def snapshot(self, c, tid):
        task = c.execute('SELECT status,config,reply_check_next_at FROM tasks WHERE id=?',(tid,)).fetchone()
        if not has_followups(json.loads(task['config'])):
            return None
        review = c.execute('SELECT * FROM task_reply_reviews WHERE task_id=?',(tid,)).fetchone()
        if not review:
            return None
        result = dict(review)
        result.pop('generation')
        result['next_check_at'] = (task['reply_check_next_at'] if task['status']=='running'
            and json.loads(task['config']).get('reply_check_interval_minutes',0) else None)
        counts = dict.fromkeys(('pending','running','completed','failed'),0)
        for row in c.execute('SELECT status,count(*) AS n FROM task_reply_targets WHERE task_id=? GROUP BY status',(tid,)):
            counts[row['status']] = row['n']
        counts['total'] = sum(counts.values())
        counts['replied'] = len(self._replied(c,tid))
        result['counts'] = counts
        result['errors'] = [dict(r) for r in c.execute('''SELECT r.uid,COALESCE(a.name,'未知账号') AS account_name,r.error
            FROM task_reply_targets r LEFT JOIN accounts a ON a.id=r.account_id
            WHERE r.task_id=? AND r.status='failed' ORDER BY r.account_id,r.uid LIMIT 20''',(tid,))]
        return result

    def replies(self, tid, filters):
        with self.db.connect() as c:
            c.execute('BEGIN')
            if not c.execute("SELECT 1 FROM tasks WHERE id=? AND status!='deleted'",(tid,)).fetchone():
                raise LookupError('任务不存在')
            rows = c.execute(f'''WITH sends AS ({TASK_SENDS_SQL}), pairs AS
                (SELECT DISTINCT account_id,uid FROM sends WHERE task_id=? AND status='sent')
                SELECT m.*,a.name AS account_name,r.data AS recipient_data,
                    u.data AS user_data,u.overrides,u.deleted_at
                FROM pairs p JOIN conversation_messages m ON m.account_id=p.account_id AND m.uid=p.uid
                JOIN accounts a ON a.id=p.account_id JOIN recipients r ON r.task_id=? AND r.uid=p.uid
                LEFT JOIN users u ON u.uid=p.uid WHERE m.sender_uid=m.uid
                ORDER BY m.created_at DESC,CAST(m.message_index AS INTEGER) DESC,m.message_id DESC''',(tid,tid))
            contacts = {}
            for row in rows:
                visible, text = message_preview(row)
                if not visible:
                    continue
                key = (row['account_id'],row['uid'])
                if key not in contacts:
                    user = {**json.loads(row['recipient_data']), **json.loads(row['user_data'] or '{}'),
                            **json.loads(row['overrides'] or '{}')}
                    contacts[key] = {'uid':row['uid'],'nickname':user.get('nickname') or row['uid'],
                        'douyinhao':user.get('douyinhao') or '', 'avatar_url':user.get('avatar_url') or '',
                        'account_id':row['account_id'],'account_name':row['account_name'],
                        'last_reply_at':row['created_at'],'last_reply_text':text,'reply_count':0,
                        'user_deleted':row['deleted_at'] is not None}
                contacts[key]['reply_count'] += 1
            return paginate(list(contacts.values()),filters)

    def _finish(self, c, tid):
        if c.execute("SELECT 1 FROM task_reply_targets WHERE task_id=? AND status IN ('pending','running') LIMIT 1",(tid,)).fetchone():
            return
        failed = c.execute("SELECT 1 FROM task_reply_targets WHERE task_id=? AND status='failed' LIMIT 1",(tid,)).fetchone()
        updated = c.execute("UPDATE task_reply_reviews SET status=?,finished_at=? WHERE task_id=? AND status IN ('pending','running')",
                  ('partial' if failed else 'completed',self.s.clock(),tid)).rowcount
        if updated and c.execute('SELECT kind FROM task_reply_reviews WHERE task_id=?',(tid,)).fetchone()['kind']=='periodic':
            self.schedule(c,tid,json.loads(c.execute('SELECT config FROM tasks WHERE id=?',(tid,)).fetchone()['config']))
        elif updated and hasattr(self.s,'tasks'):
            # Only this task's final review may extend its execution. Finished
            # historical tasks and replies arriving after that review stay idle.
            if self.s.tasks.enqueue_followups(c,tid):
                c.execute("UPDATE tasks SET status='running',phase='following_up',finished_at=NULL,next_send_at=NULL WHERE id=? AND status='completed'",(tid,))

    def _due(self):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            tasks = c.execute('''SELECT t.* FROM tasks t LEFT JOIN task_reply_reviews r ON r.task_id=t.id
                WHERE t.status='running' AND t.reply_check_next_at<=?
                AND (r.task_id IS NULL OR (r.kind='periodic' AND r.status IN ('completed','partial')))''',
                (self.s.clock(),)).fetchall()
            for task in tasks:
                if json.loads(task['config']).get('reply_check_interval_minutes',0):
                    self.enqueue(c,task['id'],self.s.clock(),'periodic')

    def tick(self):
        with self.guard:
            if self.s.stopped.is_set() or (self.worker and self.worker.is_alive()):
                return
            self._due()
            if not self._pending(limit=1):
                return
            if self.s.background:
                self.worker = threading.Thread(target=self._run,daemon=True,name='reply-review')
                self.worker.start()
            else:
                self._run()

    def _run(self):
        # Serial queries do not overlap each other. Busy accounts are left pending
        # so another account's review can progress; there is no query rate policy.
        targets = self._pending()
        for target in targets:
            if self.s.stopped.is_set():
                break
            lock = self.s.accounts.lock(target['account_id'])
            if not lock.acquire(blocking=False):
                continue
            try:
                self._one(target)
            finally:
                lock.release()

    def _pending(self, limit=None):
        sql = """SELECT t.* FROM task_reply_targets t JOIN task_reply_reviews r ON r.task_id=t.task_id
            JOIN tasks task ON task.id=t.task_id WHERE t.status='pending' AND task.status!='deleted'
            AND COALESCE(json_array_length(task.config,'$.reply_messages'),1)>0
            AND (r.kind='final' OR (task.status='running'
                AND COALESCE(json_extract(task.config,'$.reply_check_interval_minutes'),0)>0
                AND (task.reply_check_next_at IS NULL OR task.reply_check_next_at<=?)))
            ORDER BY r.anchor_at,t.task_id,t.account_id,t.uid"""
        return self.db.all(sql + (' LIMIT ?' if limit else ''),
                           (self.s.clock(),limit) if limit else (self.s.clock(),))

    def _one(self, target):
        key = (target['task_id'],target['account_id'],target['uid'],target['generation'])
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if not self._allowed(c,target):
                return
            kind = c.execute('SELECT kind FROM task_reply_reviews WHERE task_id=?',(target['task_id'],)).fetchone()['kind']
            if kind=='periodic' and any(message_preview(m)[0] for m in c.execute('''SELECT * FROM conversation_messages
                    WHERE account_id=? AND uid=? AND sender_uid=uid''',(target['account_id'],target['uid']))):
                c.execute("UPDATE task_reply_targets SET status='completed',error='',checked_at=? WHERE task_id=? AND account_id=? AND uid=? AND generation=?",
                          (self.s.clock(),*key))
                self._finish(c,target['task_id'])
                return
            if not c.execute("UPDATE task_reply_targets SET status='running' WHERE task_id=? AND account_id=? AND uid=? AND generation=? AND status='pending'",key).rowcount:
                return
            c.execute("UPDATE task_reply_reviews SET status='running' WHERE task_id=?",(target['task_id'],))
        try:
            self._check(target)
            phase = target['phase']
            while not self.s.stopped.is_set():
                with self.db.connect() as c:
                    if not self._allowed(c,target):
                        break
                if phase == 'older' and self._older_covered(target):
                    phase = 'newer'
                    self.db.run("UPDATE task_reply_targets SET phase='newer' WHERE task_id=? AND account_id=? AND uid=? AND generation=?",key)
                page = self.s.messages.sync(target['uid'],{'account_id':target['account_id'],'direction':phase})
                if phase == 'newer' and not page['has_more']:
                    self._settle(key,'completed','')
                    return
            self.db.run("UPDATE task_reply_targets SET status='pending' WHERE task_id=? AND account_id=? AND uid=? AND generation=?",key)
        except ReviewIncomplete as exc:
            self._settle(key,'failed',str(exc))
        except Exception as exc:
            reason = exception_diagnostic(exc).get('reason')
            error = ('查询超时，回查未完成' if reason=='network_timeout' else
                     '查询网络异常，回查未完成' if reason=='network_error' else
                     '历史查询或分页未完成，已保存消息保留')
            self._settle(key,'failed',error)

    def _allowed(self, c, target):
        row = c.execute('''SELECT r.kind,r.generation,t.status,t.config,t.reply_check_next_at
            FROM task_reply_reviews r JOIN tasks t ON t.id=r.task_id WHERE r.task_id=?''',
            (target['task_id'],)).fetchone()
        if not row or row['generation']!=target['generation'] or row['status']=='deleted':
            return False
        if not has_followups(json.loads(row['config'])):
            return False
        return row['kind']=='final' or (row['status']=='running'
            and json.loads(row['config']).get('reply_check_interval_minutes',0)>0
            and (row['reply_check_next_at'] is None or row['reply_check_next_at']<=self.s.clock()))

    def _check(self, target):
        task = self.db.one('SELECT status FROM tasks WHERE id=?',(target['task_id'],))
        if task['status']=='deleted':
            raise ReviewIncomplete('任务已删除，回查未完成')
        users = self.db.all('SELECT deleted_at FROM users WHERE uid=?',(target['uid'],))
        if not users or users[0]['deleted_at'] is not None:
            raise ReviewIncomplete('用户已移除，回查未完成')
        accounts = self.db.all('SELECT status,qr_status FROM accounts WHERE id=?',(target['account_id'],))
        if not accounts or accounts[0]['status']!='ready' or accounts[0]['qr_status']!='confirmed':
            raise ReviewIncomplete('实际发送账号未就绪，回查未完成')
        if not self.db.all('SELECT 1 FROM message_conversations WHERE account_id=? AND uid=?',(target['account_id'],target['uid'])):
            raise ReviewIncomplete('缺少已保存的会话信息，回查未完成')

    def _older_covered(self, target):
        state = self.db.one('SELECT * FROM message_conversations WHERE account_id=? AND uid=?',
                            (target['account_id'],target['uid']))
        if state['history_complete']:
            return True
        oldest = self.db.one('''SELECT MIN(created_at) AS time FROM conversation_messages
            WHERE account_id=? AND uid=? AND created_at>0''',(target['account_id'],target['uid']))['time']
        return oldest is not None and oldest <= target['since']

    def _settle(self, key, status, error):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            updated = c.execute('UPDATE task_reply_targets SET status=?,error=?,checked_at=? WHERE task_id=? AND account_id=? AND uid=? AND generation=?',
                      (status,error,self.s.clock(),*key)).rowcount
            if updated:
                self._finish(c,key[0])

    def close(self):
        if self.worker:
            self.worker.join(timeout=2)

@@END_FILE@@
@@FILE {"path": "web/search.py", "bytes": 5791, "sha256": "51cb04e4bf18a55703f7a0283c058306efed63c5126426e5146eb712b717506d"}
import json
import uuid
from .db import dump
from .douyin import normalize
from .filters import chosen_ids, integer, select, paginate


class Searches:
    def __init__(self, service):
        self.s, self.db = service, service.db
        self.db.run("UPDATE searches SET status='interrupted',error='服务重启，已取得结果保留，可重新搜索' WHERE status='running'")

    def create(self, data):
        aid = str(data.get('account_id') or '')
        a = self.s.accounts.get(aid)
        if not a['can_search'] or a['qr_status'] in ('waiting', 'starting', 'scanned', 'verifying'):
            raise ValueError('请先完成账号校验和扫码')
        query = str(data.get('query') or '').strip()
        if not query or len(query) > 100:
            raise ValueError('请输入 1～100 字搜索关键词')
        pages = integer(data.get('max_pages', 1), '查询页数', 1, 100)
        sid = uuid.uuid4().hex
        self.db.run("INSERT INTO searches(id,account_id,query,max_pages,status,created_at) VALUES(?,?,?,?,'running',?)",
                    (sid, aid, query, pages, self.s.clock()))
        self.s.spawn(self._run, sid)
        return {'id': sid}

    def get(self, sid):
        row = self.db.one('SELECT * FROM searches WHERE id=?', (sid,))
        row['total'] = self.db.one('SELECT count(*) AS n FROM search_results WHERE search_id=?', (sid,))['n']
        row.pop('collect', None)
        row.pop('collection_done', None)
        return row

    def _run(self, sid):
        job = self.get(sid)
        aid, cursor, seen = job['account_id'], '0', set()
        remote_search_id = ''
        try:
            for page_no in range(job['max_pages']):
                if self.s.stopped.is_set():
                    raise RuntimeError('stopped')
                if cursor in seen:
                    raise ValueError('cursor repeated')
                seen.add(cursor)
                with self.s.accounts.lock(aid):
                    auth = self.s.accounts.auth(aid)
                    response = self.s.adapter.search_page(auth, job['query'], cursor, search_id=remote_search_id)
                    self.s.accounts.save(aid, auth)
                if response.get('status_code', 0) != 0 or not isinstance(response.get('user_list'), list):
                    raise ValueError('invalid result')
                items = response['user_list']
                with self.db.connect() as c:
                    invalid = 0
                    for index, raw in enumerate(items):
                        profile = normalize(raw, job['query'])
                        if profile:
                            profile['created_at'] = job['created_at']
                            c.execute('INSERT OR IGNORE INTO search_results VALUES(?,?,?,?)',
                                      (sid, profile['uid'], dump(profile), page_no*25+index))
                        else:
                            invalid += 1
                    c.execute('UPDATE searches SET completed_pages=?,cursor=?,returned_count=returned_count+?,invalid_count=invalid_count+? WHERE id=?', (page_no+1, cursor, len(items), invalid, sid))
                if response.get('has_more') == 0:
                    break
                if response.get('has_more') != 1 or not items:
                    raise ValueError('pagination incomplete')
                # Upstream cursor is authoritative, fixed-size offset only for older responses.
                cursor = str(response.get('cursor', int(cursor)+25))
                remote_search_id = (response.get('log_pb') or {}).get('impr_id', '')
            self.db.run("UPDATE searches SET status='completed' WHERE id=?", (sid,))
        except Exception:
            self.db.run("UPDATE searches SET status='interrupted',error='查询中断，已取得结果保留。请检查账号、网络或平台验证状态后重新查询' WHERE id=?", (sid,))

    def rows(self, sid):
        self.get(sid)
        return [json.loads(r['data']) for r in self.db.all('SELECT data FROM search_results WHERE search_id=? ORDER BY ordinal', (sid,))]

    def results(self, sid, filters):
        return paginate(select(self.rows(sid), filters), filters)

    def selection(self, sid, data):
        rows = select(self.rows(sid), data.get('filters') or {})
        if data.get('limit') not in (None, ''):
            rows = rows[:integer(data['limit'], '选择人数', 1)]
        return {'uids': [r['uid'] for r in rows], 'total': len(rows)}

    def import_users(self, sid, uids, tag_ids=None):
        uids = chosen_ids(uids)
        available = {r['uid']: r for r in self.rows(sid)}
        if set(uids) - available.keys():
            raise ValueError('选择包含不属于本次搜索的用户，请刷新结果')
        inserted = updated = 0
        now = self.s.clock()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if tag_ids is None:
                tag_ids = [r['id'] for r in c.execute("SELECT id FROM tags WHERE id='used-car-dealer'")]
            tag_ids = self.s.library.validate_tags(c, tag_ids)
            for uid in uids:
                profile = available[uid]
                old = c.execute('SELECT data FROM users WHERE uid=?', (uid,)).fetchone()
                if old:
                    c.execute('UPDATE users SET data=?,updated_at=?,deleted_at=NULL WHERE uid=?', (dump(profile), now, uid))
                    updated += 1
                else:
                    c.execute('INSERT INTO users(uid,data,created_at,updated_at) VALUES(?,?,?,?)', (uid, dump(profile), now, now))
                    inserted += 1
                c.executemany('INSERT OR IGNORE INTO user_tags VALUES(?,?)', [(uid, tag_id) for tag_id in tag_ids])
        return {'inserted': inserted, 'updated': updated}

@@END_FILE@@
@@FILE {"path": "web/send_order.py", "bytes": 3974, "sha256": "7df255dabd5a25154389634a20a5f207fcc5c82c3da4b30ed1d17ef81d8222e3"}
"""Stable local send numbering per actual sender and recipient."""
from types import SimpleNamespace


def order_key(record_id):
    # A legacy recipient becomes position 0 when its first followup is queued.
    return record_id+':0' if record_id.startswith('task:') and len(record_id.split(':'))==3 else record_id


def assign_send_number(c, record_id, account_id, uid, occurred_at, *, only_if_newer=False):
    """Called inside the same IMMEDIATE transaction that reserves a send."""
    key = order_key(record_id)
    old = c.execute('SELECT send_number FROM send_orders WHERE record_key=?',(key,)).fetchone()
    if old:
        return old['send_number']
    if not account_id or occurred_at is None:
        return None
    last = c.execute('''SELECT send_number,occurred_at FROM send_orders WHERE account_id=? AND uid=?
        ORDER BY send_number DESC LIMIT 1''',(account_id,uid)).fetchone()
    if only_if_newer and last and occurred_at < last['occurred_at']:
        return None
    number = last['send_number']+1 if last else 1
    c.execute('INSERT INTO send_orders(record_key,account_id,uid,send_number,occurred_at) VALUES(?,?,?,?,?)',
              (key,account_id,uid,number,occurred_at))
    return number


def backfill_send_orders(db):
    from .send_records import message_records, TASK_SENDS_SQL
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM metadata WHERE key='send_orders_v1'").fetchone():
            return
        rows = [r for r in message_records(SimpleNamespace(db=db)) if r['direction']=='outgoing']
        # Include old in-flight task attempts before startup marks them failed.
        rows += [dict(r) for r in c.execute(f'''WITH sends AS ({TASK_SENDS_SQL})
            SELECT 'task:'||task_id||':'||uid||':'||COALESCE(position,0) AS record_id,
                account_id,uid,attempted_at FROM sends WHERE status IN ('sending','uncertain') ''')]
        def key(row):
            parts = order_key(row['record_id']).split(':')
            position = int(parts[-1]) if parts[0]=='task' else -1
            return (row['attempted_at'] or 0, ':'.join(parts[:-1]), position, row['record_id'])
        for row in sorted(rows,key=key):
            assign_send_number(c,row['record_id'],row['account_id'],row['uid'],row['attempted_at'])
        c.execute("INSERT INTO metadata VALUES('send_orders_v1','1')")


def assign_reply_numbers(c, account_id, uid):
    """Number visible incoming messages in the history-page write transaction."""
    from .send_records import message_preview
    rows = c.execute('''SELECT * FROM conversation_messages WHERE account_id=? AND uid=?
        AND sender_uid=uid ORDER BY created_at,CAST(message_index AS INTEGER),message_id''',
        (account_id,uid)).fetchall()
    last = max((r for r in rows if r['reply_number'] is not None),
               key=lambda r:r['reply_number'],default=None)
    number = last['reply_number'] if last else 0
    for row in rows:
        if row['reply_number'] is not None or row['created_at'] is None or not message_preview(row)[0]:
            continue
        # Late older history cannot change the numbers already shown to the user.
        if last and (row['created_at'],int(row['message_index'])) < (last['created_at'],int(last['message_index'])):
            continue
        number += 1
        c.execute('UPDATE conversation_messages SET reply_number=? WHERE account_id=? AND message_id=?',
                  (number,account_id,row['message_id']))


def backfill_reply_numbers(db):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM metadata WHERE key='reply_numbers_v1'").fetchone():
            return
        for row in c.execute('SELECT DISTINCT account_id,uid FROM conversation_messages WHERE sender_uid=uid').fetchall():
            assign_reply_numbers(c,row['account_id'],row['uid'])
        c.execute("INSERT INTO metadata VALUES('reply_numbers_v1','1')")

@@END_FILE@@
@@FILE {"path": "web/send_records.py", "bytes": 14509, "sha256": "346932c0746d5dd84d195575ba9d776b282513a3105efbfbfbf7e1c3e00881b7"}
"""Read-only sending history from recipient snapshots and sanitized diagnostics."""
import json
import re

from utils.send_diagnostics import send_failure_reason, send_result_note
from .filters import date_bound, paginate
from .send_order import order_key


# One result per message. Legacy recipients remain readable until they have message rows.
TASK_SENDS_SQL = """SELECT m.task_id,m.uid,m.position,m.account_id,m.kind,m.text AS send_message,m.image_id,
    m.status,m.attempted_at,CASE WHEN m.status='sent' THEN m.finished_at END AS sent_at,
    m.finished_at,m.error,m.diagnostic,(m.position=0 AND m.followup=0) AS first_touch
    FROM task_messages m
    UNION ALL
    SELECT r.task_id,r.uid,NULL,COALESCE(r.sender_account_id,t.account_id),'text',r.send_message,NULL,
    r.status,r.attempted_at,r.sent_at,
    (SELECT time FROM events WHERE task_id=r.task_id AND uid=r.uid AND type IN ('sent','failed') ORDER BY id DESC LIMIT 1),
    r.error,(SELECT detail FROM events WHERE task_id=r.task_id AND uid=r.uid AND type='send_diagnostic' ORDER BY id DESC LIMIT 1),1
    FROM recipients r JOIN tasks t ON t.id=r.task_id
    WHERE NOT EXISTS(SELECT 1 FROM task_messages m WHERE m.task_id=r.task_id AND m.uid=r.uid)"""


# Statistics do not need per-message content, diagnostics, or event lookups.
# Keep the same new-message/legacy-recipient exclusion as TASK_SENDS_SQL.
TASK_SEND_STATS_SQL = """SELECT m.task_id,m.uid,m.account_id,m.status,m.attempted_at,
    CASE WHEN m.status='sent' THEN m.finished_at END AS sent_at,
    (m.position=0 AND m.followup=0) AS first_touch FROM task_messages m
    UNION ALL
    SELECT r.task_id,r.uid,COALESCE(r.sender_account_id,t.account_id),r.status,r.attempted_at,r.sent_at,1
    FROM recipients r JOIN tasks t ON t.id=r.task_id
    WHERE NOT EXISTS(SELECT 1 FROM task_messages m WHERE m.task_id=r.task_id AND m.uid=r.uid)"""


def message_preview(m):
    if m['message_type'] in (1,40001,50001):
        return False, None
    try:
        content = json.loads(m['content'])
    except ValueError:
        content = {}
    if not isinstance(content, dict):
        content = {}
    if content.get('is_system_type') is True:
        return False, None
    text = m['text']
    if text is None:
        preview = content.get('push_detail')
        text = preview if isinstance(preview, str) and preview else f"[非文本消息 · 类型 {m['message_type']}]"
    return True, text


def message_records(service, uid=None, account_id=None):
    task_where, message_where, manual_where, args = '', '', '', []
    if uid:
        task_where += ' AND r.uid=?'; message_where += ' AND m.uid=?'; manual_where += ' AND r.uid=?'; args.append(uid)
    if account_id:
        task_where += ' AND r.account_id=?'
        message_where += ' AND m.account_id=?'; manual_where += ' AND r.account_id=?'; args.append(account_id)
    with service.db.connect() as c:
        c.execute('BEGIN')
        rows = c.execute(f"""WITH sends AS ({TASK_SENDS_SQL})
            SELECT 'task:'||r.task_id||':'||r.uid||CASE WHEN r.position IS NULL THEN '' ELSE ':'||r.position END AS record_id,
            0 AS manual,r.task_id,r.uid,p.data,r.status,r.attempted_at,r.sent_at,r.error,
            r.send_message,r.account_id,r.kind AS message_kind,r.image_id,
            a.name AS account_name,a.uid AS account_uid,t.name AS task_name,t.status AS task_status,t.config,
            r.diagnostic,r.finished_at,r.first_touch
            FROM sends r JOIN tasks t ON t.id=r.task_id
            JOIN recipients p ON p.task_id=r.task_id AND p.uid=r.uid
            LEFT JOIN accounts a ON a.id=r.account_id
            WHERE r.status IN ('sent','failed') {task_where}
            ORDER BY r.attempted_at DESC,r.task_id,p.ordinal,r.position DESC""", args).fetchall()
        rows += c.execute(f"""SELECT 'manual:'||r.id AS record_id,1 AS manual,NULL AS task_id,
            r.uid,u.data,r.status,r.attempted_at,CASE WHEN r.status='sent' THEN r.finished_at END AS sent_at,
            r.error,r.message AS send_message,r.account_id,r.message_type AS message_kind,r.image_id,
            a.name AS account_name,a.uid AS account_uid,
            '手动对话' AS task_name,'' AS task_status,'{{}}' AS config,r.diagnostic,r.finished_at,0 AS first_touch
            FROM chat_sends r JOIN accounts a ON a.id=r.account_id LEFT JOIN users u ON u.uid=r.uid
            WHERE 1=1 {manual_where}""", args).fetchall()
        messages = c.execute(f'''SELECT m.*,a.name AS account_name,a.uid AS account_uid,u.data
            FROM conversation_messages m JOIN accounts a ON a.id=m.account_id
            LEFT JOIN users u ON u.uid=m.uid
            WHERE m.message_type NOT IN (1,40001,50001)
            AND m.sender_uid IN (m.uid,a.uid) {message_where} ORDER BY m.created_at DESC''', args).fetchall()
        numbers = {r['record_key']:r['send_number'] for r in c.execute('SELECT record_key,send_number FROM send_orders')}
    candidates, known_messages = [], set()
    for raw in rows:
        row = dict(raw)
        user = json.loads(row.pop('data') or '{}')
        manual = row.pop('manual')
        config = json.loads(row.pop('config'))
        try:
            diagnostic = json.loads(row.pop('diagnostic') or '{}')
        except ValueError:
            diagnostic = {}
        if not isinstance(diagnostic, dict):
            diagnostic = {}
        # Publish only known numeric/boolean fields, never the full upstream object.
        numeric = {k: diagnostic[k] for k in ('business_code','http_status','outer_status','send_status',
                   'check_code','response_code','elapsed_ms','response_bytes','business_result_bytes')
                   if type(diagnostic.get(k)) is int}
        code = numeric.get('business_code')
        message = row.pop('send_message')
        row.update(nickname=user.get('nickname') or '', douyinhao=user.get('douyinhao') or '',
                   is_blue_v=user.get('is_blue_v'), enterprise_verify_reason=user.get('enterprise_verify_reason') or '',
                   business_code=code, codes=numeric, task_deleted=row.pop('task_status')=='deleted',
                   account_name=row['account_name'] or '旧记录未记录账号',
                   message_source='manual' if manual else 'snapshot' if message is not None else 'legacy_task',
                   message=message if message is not None else config.get('message'))
        if row['message'] is None:
            row['message_source'] = 'missing'
        if row['message_kind'] == 'image':
            row['message'] = message or '[图片]'
            row['message_source'] = 'manual' if manual else 'snapshot'
        row['image_url'] = '/api/media/'+row['image_id'] if row['image_id'] else None
        if row['status']=='failed':
            row['error'] = send_failure_reason(diagnostic) if diagnostic else row['error'] or '旧记录未保存失败原因'
        row['has_diagnostic'] = bool(diagnostic)
        row['result_note'] = send_result_note(diagnostic)
        row['message_ok'] = diagnostic.get('message_ok') if type(diagnostic.get('message_ok')) is bool else None
        row['has_error_desc'] = diagnostic.get('has_error_desc') if type(diagnostic.get('has_error_desc')) is bool else None
        row['direction'] = 'outgoing'
        mid = diagnostic.get('server_message_id')
        if type(mid) in (str, int) and str(mid).isascii() and str(mid).isdigit() and int(mid) > 0:
            known_messages.add((row['account_id'], str(mid)))
            row['message_id'] = str(mid)
        candidates.append(row)
    for raw in messages:
        m = dict(raw)
        if (m['account_id'], m['message_id']) in known_messages:
            continue
        visible, message = message_preview(m)
        if not visible:
            continue
        user = json.loads(m['data'] or '{}')
        incoming = m['sender_uid'] == m['uid']
        candidates.append({'record_id':'platform:'+m['account_id']+':'+m['message_id'], 'task_id':None, 'uid':m['uid'], 'account_id':m['account_id'],
            'account_name':m['account_name'], 'account_uid':m['account_uid'], 'task_name':'会话历史',
            'task_deleted':False, 'nickname':user.get('nickname') or '', 'douyinhao':user.get('douyinhao') or '',
            'is_blue_v':user.get('is_blue_v'), 'enterprise_verify_reason':user.get('enterprise_verify_reason') or '',
            'status':'received' if incoming else 'sent', 'direction':'incoming' if incoming else 'outgoing',
            'attempted_at':m['created_at'], 'sent_at':m['created_at'], 'finished_at':m['created_at'],
            'message':message,
            'message_type':m['message_type'], 'message_id':m['message_id'], 'message_source':'conversation',
            'reply_number':m['reply_number'] if incoming else None,
            'error':'', 'business_code':None, 'codes':{}, 'has_diagnostic':False,
            'message_ok':None, 'has_error_desc':None,
            'result_note':'从平台会话历史同步'} )
    for row in candidates:
        row['send_number'] = numbers.get(order_key(row['record_id'])) if row['direction']=='outgoing' else None
        row.setdefault('reply_number',None)
        row['message_number'] = row['reply_number'] if row['direction']=='incoming' else row['send_number']
        row['message_stage'] = row['direction']+':'+str(row['message_number'] or 'unknown')
    return candidates


def task_followups(service, tid, filters):
    if not service.db.one("SELECT 1 FROM tasks WHERE id=? AND status!='deleted'", (tid,)):
        raise LookupError('任务不存在')
    candidates = message_records(service)
    # Local message position resolves ties within a task; do not invent ordering
    # between unrelated sources whose timestamps happen to be identical.
    def position(row):
        parts = row['record_id'].split(':')
        return int(parts[3]) if parts[0] == 'task' and len(parts) == 4 else -1

    anchors = {}
    for row in candidates:
        if row['task_id'] != tid or row['status'] != 'sent' or not row['account_id'] or row['attempted_at'] is None:
            continue
        key = (row['account_id'], row['uid'])
        order = (row['attempted_at'], position(row))
        if key not in anchors or order < anchors[key]:
            anchors[key] = order
    items = []
    for row in candidates:
        anchor = anchors.get((row['account_id'], row['uid']))
        if anchor is None or row['direction'] != 'outgoing' or row['status'] not in ('sent', 'failed') or row['attempted_at'] is None:
            continue
        if row['attempted_at'] > anchor[0] or (row['task_id'] == tid and row['attempted_at'] == anchor[0] and position(row) > anchor[1]):
            items.append(row)
    items.sort(key=lambda row: (row['attempted_at'], position(row), row['record_id']), reverse=True)
    return paginate(items, filters)


def records(service, filters):
    scope = filters.get('source_scope', '')
    if scope not in ('', 'local', 'first_touch'):
        raise ValueError('发送统计口径无效')
    now = service.clock()
    category = filters.get('category', 'outgoing')
    if category not in ('all', 'outgoing', 'incoming'):
        raise ValueError('消息分类无效')
    status = filters.get('status', '')
    if status not in ('', 'sent', 'failed'):
        raise ValueError('发送结果无效')
    business = filters.get('business_code', '')
    if business not in ('', 'missing') and not re.fullmatch(r'-?\d+', business):
        raise ValueError('业务码请输入整数')
    stage = filters.get('message_stage','')
    if stage and not re.fullmatch(r'(outgoing|incoming):([1-9][0-9]*|later|unknown)',stage):
        raise ValueError('消息归属无效，请选择发送或回复的次序')
    lo = date_bound(filters['sent_from']) if filters.get('sent_from') else None
    hi = date_bound(filters['sent_to'], True) if filters.get('sent_to') else None
    if lo is not None and hi is not None and lo >= hi:
        raise ValueError('开始日期不能晚于结束日期')
    query = str(filters.get('query') or '').strip().casefold()
    candidates = [row for row in message_records(service)
                  if row['status'] != 'sending']
    items, accounts, tasks, codes, stages = [], {}, {}, set(), set()
    candidates.sort(key=lambda row: row['attempted_at'] or 0, reverse=True)
    for row in candidates:
        stages.add(row['message_stage'])
        code = row['business_code']
        if code is not None:
            codes.add(code)
        accounts[row['account_id'] or 'missing'] = row['account_name']
        if row['task_id']:
            tasks[row['task_id']] = {'id':row['task_id'], 'name':row['task_name'], 'deleted':row['task_deleted']}
        if category != 'all' and row['direction'] != category:
            continue
        if scope and (row['message_source'] == 'conversation' or
                      (row['attempted_at'] is not None and row['attempted_at'] > now)):
            continue
        if scope == 'first_touch' and not row.get('first_touch'):
            continue
        if stage:
            if stage.endswith(':later'):
                if row['direction'] != stage.split(':')[0] or (row['message_number'] or 0) < 2:
                    continue
            elif row['message_stage'] != stage:
                continue
        if status and row['status'] != status:
            continue
        if business == 'missing' and code is not None:
            continue
        if business not in ('', 'missing') and code != int(business):
            continue
        if any(filters.get(key) and (row[key] or 'missing') != filters[key] for key in ('account_id','task_id','uid')):
            continue
        timestamp = row['attempted_at']
        if (lo is not None and (timestamp is None or timestamp < lo)) or (hi is not None and (timestamp is None or timestamp >= hi)):
            continue
        if query and not any(query in str(row.get(k) or '').casefold() for k in ('nickname','douyinhao','uid','message')):
            continue
        items.append(row)
    result = paginate(items, filters)
    result['reply_count'] = sum(row['direction'] == 'incoming' for row in items)
    result['summary'] = {state:sum(row['status']==state for row in items) for state in ('sent','failed')}
    result['options'] = {'accounts':[{'id':key,'name':name} for key,name in accounts.items()],
                         'tasks':list(tasks.values()), 'business_codes':sorted(codes),
                         'message_stages':sorted(stages)}
    return result

@@END_FILE@@
@@FILE {"path": "web/service.py", "bytes": 9849, "sha256": "966b9eb246687e567d0069a2bf2277d742f73032e74668e4c0a046889fdcb085"}
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from .accounts import Accounts
from .db import Database, dump
from .douyin import RealAdapter
from .filters import id_batches, chosen_ids, integer, select, paginate, date_bound
from .search import Searches
from .tasks import Tasks
from .library import Library
from .messages import Messages
from .send_records import message_preview, TASK_SENDS_SQL, TASK_SEND_STATS_SQL
from .conversation import Conversation
from .media import Media
from .reply_review import ReplyReviews


class Service:
    def __init__(self, directory, adapter=None, clock=time.time, background=True):
        self.clock, self.background = clock, background
        self.adapter = adapter if adapter is not None else RealAdapter()
        self.db = Database(directory)
        # A path cannot accidentally switch between real and demo credentials/results.
        mode = 'demo' if self.adapter.demo else 'real'
        with self.db.connect() as c:
            row = c.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
            if row and row['value'] != mode:
                raise ValueError('此数据目录属于另一运行模式，请使用独立数据目录')
            c.execute("INSERT OR IGNORE INTO metadata VALUES('mode',?)", (mode,))
        self.stopped = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='console') if background else None
        self.accounts = Accounts(self)
        self.library = Library(self)
        self.media = Media(self)
        self.messages = Messages(self)
        self.conversation = Conversation(self)
        self.search = Searches(self)
        self.reply_reviews = ReplyReviews(self)
        self.tasks = Tasks(self)
        self.worker = None
        if background:
            self.worker = threading.Thread(target=self._loop, daemon=True, name='sender')
            self.worker.start()

    def spawn(self, function, *args):
        if self.pool:
            self.pool.submit(function, *args)
        else:
            function(*args)

    def _loop(self):
        while not self.stopped.wait(0.4):
            self.tasks.tick()
            self.reply_reviews.tick()

    def close(self):
        self.stopped.set()
        self.accounts.close()
        if self.worker:
            self.worker.join(timeout=2)
        self.tasks.close()
        self.reply_reviews.close()
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)

    def user_rows(self, filters=None):
        filters = filters or {}
        sql = f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT r.uid,r.sent_at FROM sends r WHERE r.status='sent'"
        params = []
        if filters.get('account_id'):
            sql += ' AND r.account_id=?'
            params.append(filters['account_id'])
        manual_sql = "SELECT uid,finished_at AS sent_at FROM chat_sends WHERE status='sent'"
        if filters.get('account_id'):
            manual_sql += ' AND account_id=?'
        history = {}
        matched = {}
        lo = date_bound(filters['sent_from']) if filters.get('sent_from') else None
        hi = date_bound(filters['sent_to'], True) if filters.get('sent_to') else None
        for row in self.db.all(sql, params) + self.db.all(manual_sql, params):
            history[row['uid']] = max(row['sent_at'] or 0, history.get(row['uid'], 0))
            if row['sent_at'] is not None and (lo is None or row['sent_at'] >= lo) and (hi is None or row['sent_at'] < hi):
                matched[row['uid']] = max(row['sent_at'], matched.get(row['uid'], 0))
        replies = {}
        reply_sql = 'SELECT * FROM conversation_messages WHERE sender_uid=uid'
        if filters.get('account_id'):
            reply_sql += ' AND account_id=?'
        for message in self.db.all(reply_sql, params):
            if message_preview(message)[0]:
                replies[message['uid']] = max(message['created_at'] or 0, replies.get(message['uid'], 0))
        rows = []
        tags = {}
        for tag in self.db.all('SELECT ut.uid,t.id,t.name FROM user_tags ut JOIN tags t ON t.id=ut.tag_id ORDER BY t.created_at,t.id'):
            tags.setdefault(tag.pop('uid'), []).append(tag)
        for row in self.db.all('SELECT * FROM users WHERE deleted_at IS NULL'):
            data = json.loads(row['data'])
            data.update(json.loads(row['overrides']))
            data.update(uid=row['uid'], note=row['note'], created_at=row['created_at'], updated_at=row['updated_at'],
                        sent=row['uid'] in history, last_sent_at=history.get(row['uid']),
                        replied=row['uid'] in replies, last_reply_at=replies.get(row['uid']),
                        _sent_filter_at=matched.get(row['uid']), tags=tags.get(row['uid'], []))
            rows.append(data)
        return rows

    def users(self, filters):
        result = paginate(select(self.user_rows(filters), filters), filters)
        summary = self.db.one(f"""WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(*) AS total,
            coalesce(sum(EXISTS(SELECT 1 FROM sends r WHERE r.uid=u.uid AND r.status='sent')
                OR EXISTS(SELECT 1 FROM chat_sends m WHERE m.uid=u.uid AND m.status='sent')),0) AS sent
            FROM users u WHERE u.deleted_at IS NULL""")
        summary['unsent'] = summary['total'] - summary['sent']
        result['summary'] = summary
        return result

    def selection(self, data):
        filters = data.get('filters') or {}
        rows = select(self.user_rows(filters), filters)
        if data.get('limit') not in (None, ''):
            rows = rows[:integer(data['limit'], '选择人数', 1)]
        return {'uids': [r['uid'] for r in rows], 'total': len(rows)}

    def edit_user(self, uid, data):
        if not data or set(data) - {'nickname', 'signature', 'note'}:
            raise ValueError('只允许修改昵称、简介和备注，身份与统计不能修改')
        row = self.db.one('SELECT * FROM users WHERE uid=? AND deleted_at IS NULL', (uid,))
        if any(not isinstance(v, str) or len(v) > 2000 for v in data.values()):
            raise ValueError('文本格式无效或超过 2000 字')
        overrides = json.loads(row['overrides'])
        overrides.update({k: v for k, v in data.items() if k != 'note'})
        self.db.run('UPDATE users SET overrides=?,note=?,updated_at=? WHERE uid=?',
                    (dump(overrides), data.get('note', row['note']), self.clock(), uid))
        return {'ok': True}

    def delete_users(self, values):
        uids = chosen_ids(values)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            now = self.clock()
            deleted = 0
            for batch in id_batches(uids):
                marks = ','.join('?' for _ in batch)
                if c.execute(f"SELECT 1 FROM recipients WHERE uid IN ({marks}) AND status='sending' LIMIT 1", batch).fetchone():
                    raise ValueError('所选用户正在发送任务消息，请等待请求结束')
                busy = c.execute(f"SELECT r.uid FROM recipients r JOIN tasks t ON t.id=r.task_id WHERE r.uid IN ({marks}) AND t.status='running' AND r.status IN ('pending','sending') LIMIT 1", batch).fetchone()
                if busy:
                    raise ValueError('所选用户属于进行中的任务，请先暂停任务')
                if c.execute(f"SELECT 1 FROM chat_sends WHERE uid IN ({marks}) AND status='sending' LIMIT 1", batch).fetchone():
                    raise ValueError('所选用户正在发送对话消息，请等待请求结束')
                result = c.execute(f'UPDATE users SET deleted_at=? WHERE uid IN ({marks}) AND deleted_at IS NULL', [now, *batch])
                deleted += result.rowcount
        return {'deleted': deleted}

    def batch_edit(self, data):
        if set(data) != {'uids', 'note'} or not isinstance(data['note'], str) or len(data['note']) > 2000:
            raise ValueError('批量修改仅支持最多 2000 字的本地备注')
        uids = chosen_ids(data['uids'])
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            now = self.clock()
            for batch in id_batches(uids):
                marks = ','.join('?' for _ in batch)
                found = c.execute(f'SELECT count(*) AS n FROM users WHERE deleted_at IS NULL AND uid IN ({marks})', batch).fetchone()['n']
                if found != len(batch):
                    raise ValueError('部分选中用户已被移除，请重新选择')
                c.execute(f'UPDATE users SET note=?,updated_at=? WHERE uid IN ({marks})', [data['note'], now, *batch])
        return {'updated': len(uids)}

    def history(self, uid):
        items = self.db.all(f'''WITH sends AS ({TASK_SENDS_SQL})
                    SELECT t.id AS task_id,t.name AS task_name,COALESCE(a.name,'尚未发送') AS account_name,
                    t.config,r.status,r.sent_at,r.attempted_at,r.error,r.send_message,
                    r.position,r.kind AS message_kind,r.image_id
                    FROM sends r JOIN tasks t ON t.id=r.task_id LEFT JOIN accounts a ON a.id=
                        CASE WHEN r.attempted_at IS NOT NULL OR r.status IN ('sent','failed') THEN r.account_id END
                    WHERE r.uid=? ORDER BY COALESCE(r.attempted_at,t.created_at) DESC,r.position''', (uid,))
        for item in items:
            item['image_url'] = '/api/media/'+item['image_id'] if item['image_id'] else None
            if item['message_kind'] == 'image':
                item['send_message'] = item['send_message'] or '[图片]'
            if item['send_message'] is not None:
                item['config'] = dump({**json.loads(item['config']), 'message': item['send_message']})
        return {'items': items}

@@END_FILE@@
@@FILE {"path": "web/tasks.py", "bytes": 53779, "sha256": "a157fbb309297aa414647a268b3165262b878c9d924b935961d2ca8c421d5785"}
import json
import re
import random
import threading
import uuid
import time
from datetime import datetime
from .db import dump
from .douyin import AccountIssue, SendRejected
from utils.send_diagnostics import (SendUncertain, exception_diagnostic, send_failure_reason,
                                    BUSINESS_SUCCESS_CODES, accepted_without_business_code, send_result_note)
from .filters import id_batches, chosen_ids, integer, paginate, select
from .work_time import next_work_at
from .outbound import message_config, message_summary, send_permission, dispatch_message, remember_conversation
from .send_records import message_preview
from .send_order import assign_send_number


STATES = ('pending', 'sending', 'sent', 'failed', 'skipped')


def schedule(value):
    if value in (None, ''):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.timestamp()
    except (ValueError, OverflowError):
        raise ValueError('预约时间需要包含时区') from None


class Tasks:
    def __init__(self, service):
        self.s, self.db = service, service.db
        self.tick_lock = threading.Lock()
        self.workers = {}
        self.rng = random.SystemRandom()
        self.recover()

    def event(self, c, tid, kind, detail, uid=None):
        return c.execute('INSERT INTO events(task_id,uid,time,type,detail) VALUES(?,?,?,?,?)',
                         (tid, uid, self.s.clock(), kind, detail)).lastrowid

    def recover(self):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            migrate = not c.execute("SELECT 1 FROM metadata WHERE key='binary_send_results_v1'").fetchone()
            rows = c.execute("""SELECT task_id,uid,status,error FROM recipients r
                WHERE (status='uncertain' OR (status='failed' AND ?)) AND NOT EXISTS
                (SELECT 1 FROM task_messages m WHERE m.task_id=r.task_id AND m.uid=r.uid)""", (migrate,)).fetchall()
            for row in rows:
                event = c.execute("SELECT detail FROM events WHERE task_id=? AND uid=? AND type='send_diagnostic' ORDER BY id DESC LIMIT 1", (row['task_id'], row['uid'])).fetchone()
                diagnostic = json.loads(event['detail']) if event else {}
                if 'business_code' not in diagnostic:
                    match = re.search(r'(?:业务码|错误码)\s*(\d+)', row['error'] or '')
                    if match:
                        diagnostic['business_code'] = int(match[1])
                if row['status'] == 'failed' and not diagnostic:
                    continue
                error = send_failure_reason(diagnostic)
                c.execute("UPDATE recipients SET status='failed',error=? WHERE task_id=? AND uid=?", (error, row['task_id'], row['uid']))
                # Preserve result order and timestamps for curves and streaks;
                # original sanitized response diagnostics remain untouched.
                updated = c.execute("UPDATE events SET type='failed',detail=? WHERE task_id=? AND uid=? AND type IN ('uncertain','failed')", (error, row['task_id'], row['uid'])).rowcount
                if not updated and row['status'] == 'uncertain':
                    self.event(c, row['task_id'], 'failed', error, row['uid'])
            c.execute("INSERT OR IGNORE INTO metadata VALUES('binary_send_results_v1','1')")
            self._migrate_business_success(c)
            inflight = c.execute("SELECT task_id,uid FROM recipients WHERE status='sending'").fetchall()
            for row in inflight:
                error = send_failure_reason({'reason': 'interrupted'})
                self._interrupt_sequence(c, row['task_id'], row['uid'], error)
                c.execute("UPDATE recipients SET status='failed',error=? WHERE task_id=? AND uid=?", (error, row['task_id'], row['uid']))
                self.event(c, row['task_id'], 'failed', error, row['uid'])
            c.execute("UPDATE tasks SET status='paused',phase='idle',error='服务重启，确认状态后可恢复' WHERE status='running'")
            c.execute("UPDATE tasks SET error='失败结果已记录，恢复后继续剩余目标' WHERE status='paused' AND error IN ('发送结果未知，请核对接收端后登记结果','执行中断，请检查任务结果后恢复','未确认结果已记录，恢复后继续剩余目标')")
            queued = c.execute('''SELECT DISTINCT m.task_id,m.account_id,m.uid FROM task_messages m
                JOIN recipients r ON r.task_id=m.task_id AND r.uid=m.uid
                WHERE m.status='pending' AND (m.followup=1 OR m.position>0) AND r.status='pending' ''').fetchall()
            for row in queued:
                self._skip_duplicate_followup(c,row['task_id'],row['account_id'],row['uid'])
            finished = c.execute("SELECT id FROM tasks WHERE status='paused' AND NOT EXISTS (SELECT 1 FROM recipients WHERE task_id=tasks.id AND status IN ('pending','sending'))").fetchall()
            for task in finished:
                self._finish(c, task['id'])

    def _migrate_business_success(self, c):
        if (c.execute("SELECT 1 FROM metadata WHERE key='business_success_4002_8101_v1'").fetchone()
                and c.execute("SELECT 1 FROM metadata WHERE key='business_success_missing_v1'").fetchone()
                and c.execute("SELECT 1 FROM metadata WHERE key='business_success_21003_31003_v1'").fetchone()):
            return
        rows = c.execute("""SELECT r.task_id,r.uid,r.attempted_at,r.sent_at,e.detail,e.time AS diagnostic_time
            FROM recipients r JOIN events e ON e.id=(SELECT MAX(id) FROM events
                WHERE task_id=r.task_id AND uid=r.uid AND type='send_diagnostic')
            WHERE r.status='failed' AND NOT EXISTS
                (SELECT 1 FROM task_messages m WHERE m.task_id=r.task_id AND m.uid=r.uid)""").fetchall()
        for row in rows:
            diagnostic = json.loads(row['detail'])
            code = diagnostic.get('business_code')
            missing_accepted = accepted_without_business_code(diagnostic)
            if not missing_accepted and (type(code) is not int or code not in BUSINESS_SUCCESS_CODES):
                continue
            result = c.execute("SELECT time FROM events WHERE task_id=? AND uid=? AND type='failed' ORDER BY id DESC LIMIT 1",
                               (row['task_id'], row['uid'])).fetchone()
            sent_at = row['sent_at'] if row['sent_at'] is not None else result['time'] if result else row['diagnostic_time']
            c.execute("UPDATE recipients SET status='sent',sent_at=?,error='' WHERE task_id=? AND uid=?",
                      (sent_at, row['task_id'], row['uid']))
            c.execute("UPDATE events SET type='sent',detail=? WHERE task_id=? AND uid=? AND type='failed'",
                      ('发送成功（未返回业务码）' if missing_accepted else f'发送成功（业务码 {code}）', row['task_id'], row['uid']))
            c.execute("""UPDATE tasks SET error='发送结果已按业务码口径更新；恢复后继续剩余目标'
                WHERE id=? AND status='paused' AND error LIKE '累计失败 %'""", (row['task_id'],))
        c.execute("INSERT OR IGNORE INTO metadata VALUES('business_success_4002_8101_v1','1')")
        c.execute("INSERT OR IGNORE INTO metadata VALUES('business_success_missing_v1','1')")
        c.execute("INSERT OR IGNORE INTO metadata VALUES('business_success_21003_31003_v1','1')")

    def _interrupt_sequence(self, c, tid, uid, error):
        c.execute("""UPDATE task_messages SET status='failed',error=?,finished_at=?,diagnostic=?
            WHERE task_id=? AND uid=? AND status='sending'""",
                  (error,self.s.clock(),dump({'reason':'interrupted'}),tid,uid))
        c.execute("UPDATE task_messages SET status='skipped',error='前一条发送中断，停止该联系人后续消息' WHERE task_id=? AND uid=? AND status='pending'", (tid,uid))

    def create(self, data):
        request_id = str(data.get('request_id') or '').strip()
        name = str(data.get('name') or '').strip()
        messages = message_config(self.s, data)
        message = messages['message']
        if not request_id or not name or len(name) > 120:
            raise ValueError('请填写任务名称和消息（最多 2000 字），并提供创建请求 ID')
        existing = self.db.all('SELECT id,status FROM tasks WHERE request_id=?', (request_id,))
        if existing:
            if existing[0]['status'] == 'deleted':
                raise ValueError('原任务已删除，请重新创建任务')
            return {'id': existing[0]['id']}
        repeat_policy = data.get('repeat_policy')
        if repeat_policy not in (None, 'allow', 'exclude'):
            raise ValueError('重复发送选项无效')
        self._reject_account_policy(data)
        mode, aids = self._execution(data)
        aid = aids[0] if aids else None
        uids = chosen_ids(data.get('uids'))
        users = {u['uid']: u for u in self.s.user_rows()}
        if set(uids) - users.keys():
            raise ValueError('目标用户已被移除或不存在，请重新选择')
        filters = data.get('filters') or {}
        if set(filters) - {'min_followers', 'min_likes'}:
            raise ValueError('发送门槛包含无效字段')
        eligible = {r['uid'] for r in select([users[u] for u in uids], filters)}
        if not eligible:
            raise ValueError('没有符合发送门槛的用户')
        config = {**messages, 'filters': filters,
                  'reply_check_interval_minutes': integer(data.get('reply_check_interval_minutes', 30), '回复回查间隔（分钟）', 0, 10080),
                  'max_errors': integer(data.get('max_errors', 1000), '允许累计失败次数', 0, 10000),
                  'max_consecutive_errors': integer(data.get('max_consecutive_errors', 1000), '允许连续失败次数', 0, 10000),
                  'scheduled_at': schedule(data.get('scheduled_at'))}
        tid = uuid.uuid4().hex
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            previous = c.execute('SELECT id,status FROM tasks WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if previous['status'] == 'deleted':
                    raise ValueError('原任务已删除，请重新创建任务')
                return {'id': previous['id']}
            sent = set()
            for batch in id_batches(uids):
                placeholders = ','.join('?' for _ in batch)
                sent.update({r['uid'] for r in c.execute(f"""SELECT uid FROM recipients WHERE status='sent' AND uid IN ({placeholders})
                    UNION SELECT uid FROM task_messages WHERE status='sent' AND uid IN ({placeholders})
                    UNION SELECT uid FROM chat_sends WHERE status='sent' AND uid IN ({placeholders})""", batch*3)})
            if sent and repeat_policy is None and 'first_message' not in data and 'first_messages' not in data:
                return {'requires_confirmation': True, 'sent_count': len(sent),
                        'total': len(uids), 'remaining_count': len(uids) - len(sent)}
            if repeat_policy == 'exclude':
                uids = [uid for uid in uids if uid not in sent]
                if not uids:
                    raise ValueError('所选用户均已发送，没有剩余目标；可选择重复发送或重新选人')
                if not eligible.intersection(uids):
                    raise ValueError('排除已发送用户后，没有符合发送门槛的用户')
            config.update(repeat_policy=repeat_policy,
                          excluded_sent_count=len(sent) if repeat_policy == 'exclude' else 0)
            c.execute("INSERT INTO tasks(id,request_id,name,account_id,config,status,created_at,execution_mode) VALUES(?,?,?,?,?,'pending',?,?)",
                      (tid, request_id, name, aid, dump(config), self.s.clock(), mode))
            c.executemany('INSERT INTO task_accounts VALUES(?,?)', [(tid, aid) for aid in aids])
            for index, uid in enumerate(uids):
                c.execute('INSERT INTO recipients(task_id,uid,data,ordinal,status,client_message_id,error) VALUES(?,?,?,?,?,?,?)',
                          (tid, uid, dump(users[uid]), index, 'pending' if uid in eligible else 'skipped',
                           str(uuid.uuid4()), '' if uid in eligible else '未满足发送门槛或资料不足'))
            self.event(c, tid, 'created', f'任务已创建，等待开始，共 {len(uids)} 位用户')
        return {'id': tid}

    @staticmethod
    def _reject_account_policy(data):
        if set(data) & {'interval_seconds', 'random_extra_seconds', 'max_batch_size'}:
            raise ValueError('发送节奏已移至账号管理，请修改账号发送配置')

    def _execution(self, data, current=None):
        mode = data.get('execution_mode', current['execution_mode'] if current else 'specified')
        if mode not in ('auto', 'specified'):
            raise ValueError('执行模式无效')
        if 'account_id' in data and 'account_ids' in data:
            raise ValueError('执行账号字段重复')
        aids = data.get('account_ids', [data['account_id']] if 'account_id' in data else
                        current['account_ids'] if current and mode == current['execution_mode'] and mode == 'specified' else [])
        if not isinstance(aids, list) or any(not isinstance(a, str) or not a for a in aids):
            raise ValueError('请选择有效执行账号')
        aids = list(dict.fromkeys(aids))
        if mode == 'auto':
            if aids:
                raise ValueError('自动分配不需要指定账号')
            return mode, []
        if not aids:
            raise ValueError('请至少指定一个执行账号')
        for aid in aids:
            account = self.s.accounts.get(aid)
            # Existing selections may be temporarily unavailable; editing them does
            # not authenticate an account, and the scheduler always checks readiness.
            if (not current or aid not in current['account_ids']) and not self.s.accounts.send_ready(account):
                raise ValueError('所选账号尚未就绪，请先完成发送鉴权')
        return mode, aids

    def account_ids(self, c, task):
        if task['execution_mode'] == 'specified':
            return [r[0] for r in c.execute('SELECT account_id FROM task_accounts WHERE task_id=? ORDER BY account_id', (task['id'],))]
        return [r[0] for r in c.execute("""SELECT a.id FROM accounts a WHERE NOT EXISTS (SELECT 1 FROM task_accounts ta JOIN tasks t ON t.id=ta.task_id
                WHERE ta.account_id=a.id AND t.status='running' AND t.execution_mode='specified') ORDER BY a.id""")]

    def _snapshot(self, c, tid, account_snapshots=None):
        row = c.execute("SELECT * FROM tasks WHERE id=? AND status!='deleted'", (tid,)).fetchone()
        if not row:
            raise LookupError('任务不存在')
        task = dict(row)
        config = json.loads(task.pop('config'))
        for key in ('message', 'first_message', 'first_messages', 'reply_messages', 'filters', 'scheduled_at', 'repeat_policy', 'excluded_sent_count'):
            if key in config:
                task[key] = config[key]
        task.setdefault('first_message', {'kind': 'text', 'text': config['message']})
        task.setdefault('first_messages', [task['first_message']])
        task.setdefault('reply_messages', [task['first_message']])
        for key in ('max_errors', 'max_consecutive_errors'):
            task[key] = config.get(key, 1000)
        task['reply_check_interval_minutes'] = config.get('reply_check_interval_minutes', 0)
        if task['status'] != 'running' or not task['reply_check_interval_minutes'] or not task['reply_messages']:
            task['reply_check_next_at'] = None
        task['consecutive_errors'] = self._consecutive_errors(c, tid)
        task['reply_review'] = self.s.reply_reviews.snapshot(c, tid)
        aids = self.account_ids(c, task)
        task['account_ids'] = aids if task['execution_mode'] == 'specified' else []
        if account_snapshots is None:
            account_snapshots = {}
        for aid in aids:
            if aid not in account_snapshots:
                account_snapshots[aid] = self.s.accounts.snapshot(c, aid)
        task['accounts'] = [account_snapshots[aid] for aid in aids]
        task['account_name'] = ('共享账号池' if task['execution_mode'] == 'auto' else
                                '、'.join(a['name'] for a in task['accounts']))
        counts = dict.fromkeys(STATES, 0)
        for row in c.execute('SELECT status,count(*) AS n FROM recipients WHERE task_id=? GROUP BY status', (tid,)):
            counts[row['status']] = row['n']
        counts['total'] = sum(counts.values())
        task['counts'] = counts
        task['phase'] = 'sending' if counts['sending'] else 'waiting' if task['status'] == 'running' else 'idle'
        task['wait_reason'] = ''
        if task['status'] == 'running':
            available = [a for a in task['accounts'] if a['send_enabled'] and self.s.accounts.send_ready(a)]
            task['next_send_at'] = (min(next_work_at(a['work_schedule'], max(self.s.clock(),
                task['scheduled_at'] or 0,a['available_at'])) for a in available) if available else None)
            if task['execution_mode'] == 'auto':
                # Account availability is shared by competing tasks. It is not a
                # reservation or a promised send time for any individual task.
                task['next_send_at'] = None
                if not counts['sending']:
                    if (task['scheduled_at'] or 0) > self.s.clock():
                        task['wait_reason'] = '等待预约时间，到期后参与账号分配'
                    elif not available:
                        task['wait_reason'] = '等待可用共享账号：账号可能已借调、已暂停或需鉴权'
                    elif not any(a['work_active'] for a in available):
                        task['wait_reason'] = '等待共享账号进入工作时间'
                    else:
                        task['wait_reason'] = '等待共享账号空闲，按任务轮流领取'
            elif not available:
                task['wait_reason'] = '等待可用账号：账号可能已借调、已暂停或需鉴权'
            elif not counts['sending'] and task['next_send_at'] > self.s.clock():
                task['wait_reason'] = '等待预约时间或账号工作时间、冷却、休息结束'
            elif not counts['sending']:
                task['wait_reason'] = '等待账号领取下一位接收者'
        return task

    @staticmethod
    def _consecutive_errors(c, tid):
        # Different accounts can finish out of order. Use committed result events,
        # not recipient ordinals or request start timestamps, for the failure streak.
        return c.execute("""SELECT count(*) FROM events WHERE task_id=? AND type='failed'
            AND id>COALESCE((SELECT MAX(id) FROM events WHERE task_id=? AND type='sent'),0)""", (tid, tid)).fetchone()[0]

    def get(self, tid):
        with self.db.connect() as c:
            c.execute('BEGIN')
            return self._snapshot(c, tid)

    def list(self, filters):
        status = filters.get('status')
        if status and status not in ('all', 'pending', 'running', 'paused', 'completed'):
            raise ValueError('任务状态无效')
        with self.db.connect() as c:
            c.execute('BEGIN')
            rows = [dict(r) for r in c.execute("SELECT id,name,status,created_at FROM tasks WHERE status!='deleted' ORDER BY created_at DESC")]
            if status and status != 'all':
                rows = [r for r in rows if r['status'] == status]
            result = paginate(select(rows, filters), filters)
            # Share account computation only within this read transaction. Paginate
            # first so hidden tasks do not trigger account/reply/history queries.
            account_snapshots = {}
            result['items'] = [self._snapshot(c, r['id'], account_snapshots) for r in result['items']]
            return result

    def recipients(self, tid, filters):
        self.get(tid)
        rows = []
        for r in self.db.all('''SELECT r.*,a.name AS sender_account_name,e.detail AS diagnostic FROM recipients r
            LEFT JOIN accounts a ON a.id=r.sender_account_id
            LEFT JOIN events e ON e.id=(SELECT MAX(id) FROM events
                WHERE task_id=r.task_id AND uid=r.uid AND type='send_diagnostic')
            WHERE r.task_id=? ORDER BY ordinal''', (tid,)):
            try:
                diagnostic = json.loads(r.pop('diagnostic') or '{}')
            except ValueError:
                diagnostic = {}
            r['result_note'] = send_result_note(diagnostic if isinstance(diagnostic, dict) else {})
            data = json.loads(r.pop('data'))
            data.update(r)
            if filters.get('status') not in (None, '', 'all') and r['status'] != filters['status']:
                continue
            rows.append(data)
        return paginate(rows, filters)

    def delete(self, tid):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT status FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row:
                raise LookupError('任务不存在')
            if row['status'] == 'deleted':
                return {'ok': True}
            if row['status'] == 'running':
                raise ValueError('请先暂停任务，再删除')
            if c.execute("SELECT 1 FROM recipients WHERE task_id=? AND status='sending'", (tid,)).fetchone():
                raise ValueError('当前发送请求尚未结束，请稍后删除')
            c.execute("UPDATE tasks SET status='deleted',phase='idle',next_send_at=NULL WHERE id=?", (tid,))
            c.execute("UPDATE recipients SET status='skipped',error='任务已删除，未执行' WHERE task_id=? AND status='pending'", (tid,))
            c.execute("UPDATE task_messages SET status='skipped',error='任务已删除，未执行' WHERE task_id=? AND status='pending'", (tid,))
            self.event(c, tid, 'deleted', '任务已删除，已有发送记录保留')
        return {'ok': True}

    def start(self, tid):
        return self._activate(tid, 'pending')

    def resume(self, tid):
        return self._activate(tid, 'paused')

    def _activate(self, tid, expected):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row or row['status'] == 'deleted':
                raise LookupError('任务不存在')
            if row['status'] == 'running':
                return self._snapshot(c, tid)
            if row['status'] != expected:
                raise ValueError('当前状态不能执行此操作')
            if row['execution_mode'] == 'specified' and c.execute("""SELECT 1 FROM accounts a
                JOIN task_accounts ta ON ta.account_id=a.id WHERE ta.task_id=?
                AND (a.status='checking' OR a.qr_status IN ('starting','waiting','scanned','verifying'))""", (tid,)).fetchone():
                raise ValueError('账号正在鉴权，请等待鉴权结束后开始')
            now = self.s.clock()
            config = json.loads(row['config'])
            next_time = max(now, config['scheduled_at'] or 0)
            c.execute("UPDATE tasks SET status='running',phase='waiting',started_at=COALESCE(started_at,?),next_send_at=?,error='' WHERE id=?", (now, next_time, tid))
            self.s.reply_reviews.schedule(c, tid, config)
            self.event(c, tid, 'started', '任务已激活，等待预约时间或可用账号')
        return self.get(tid)

    def pause(self, tid):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT status FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row:
                raise LookupError('任务不存在')
            if row['status'] == 'paused':
                return self.get(tid)
            if row['status'] != 'running':
                raise ValueError('只有进行中的任务可以暂停')
            c.execute("UPDATE tasks SET status='paused',phase='idle',error='用户暂停' WHERE id=?", (tid,))
            self.event(c, tid, 'paused', '已暂停，不再启动下一条；当前请求结束后保留结果')
        return self.get(tid)

    def edit(self, tid, data):
        self._reject_account_policy(data)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current = self._snapshot(c, tid)
            allowed = {'account_id', 'account_ids', 'execution_mode', 'max_errors', 'max_consecutive_errors', 'message', 'first_message', 'first_messages', 'reply_messages', 'reply_check_interval_minutes'}
            if current['status'] == 'pending':
                allowed |= {'name', 'message', 'scheduled_at'}
            elif current['status'] not in ('paused', 'running'):
                raise ValueError('只有未开始、运行中或已暂停的任务可以修改')
            if not data or set(data)-allowed:
                raise ValueError('当前状态不能修改这些字段')
            mode, aids = self._execution(data, current)
            if mode=='specified' and c.execute("""SELECT 1 FROM task_messages WHERE task_id=? AND status='pending'
                AND account_id NOT IN (SELECT value FROM json_each(?)) LIMIT 1""", (tid,dump(aids))).fetchone():
                raise ValueError('该账号有未完成的联系人消息队列，完成或删除任务后才能移除该账号')
            config = json.loads(c.execute('SELECT config FROM tasks WHERE id=?', (tid,)).fetchone()[0])
            if 'reply_check_interval_minutes' in data:
                config['reply_check_interval_minutes'] = integer(data['reply_check_interval_minutes'], '回复回查间隔（分钟）', 0, 10080)
            for key in ('max_errors', 'max_consecutive_errors'):
                if key in data:
                    config[key] = integer(data[key], '允许失败次数', 0, 10000)
            if 'scheduled_at' in data:
                config['scheduled_at'] = schedule(data['scheduled_at'])
            if set(data) & {'message', 'first_message', 'first_messages', 'reply_messages'}:
                config.update(message_config(self.s, data, config))
            if set(data) & {'reply_check_interval_minutes', 'reply_messages', 'message', 'first_message', 'first_messages'}:
                self.s.reply_reviews.schedule(c, tid, config)
            name = data.get('name', current['name'])
            if not isinstance(name, str) or not name.strip() or len(name) > 120:
                raise ValueError('任务名称无效')
            if config['message'] != current['message']:
                # Before message editing was supported, started tasks could not
                # change content. Freeze those legacy attempts before replacing it.
                c.execute("""UPDATE recipients SET send_message=? WHERE task_id=?
                    AND attempted_at IS NOT NULL AND send_message IS NULL""", (current['message'], tid))
                self.event(c, tid, 'message_updated', '发送话术已更新，仅对后续领取生效；在途请求与历史正文保留')
            c.execute('UPDATE tasks SET name=?,config=?,execution_mode=?,account_id=? WHERE id=?',
                      (name.strip(), dump(config), mode, aids[0] if aids else None, tid))
            c.execute('DELETE FROM task_accounts WHERE task_id=?', (tid,))
            c.executemany('INSERT INTO task_accounts VALUES(?,?)', [(tid, aid) for aid in aids])
            if mode != current['execution_mode'] or set(aids) != set(current['account_ids']):
                self.event(c, tid, 'account_changed', '执行账号已更新，仅影响后续领取；在途请求与历史归属保留')
            if any(config.get(key, 1000) != current[key] for key in ('max_errors', 'max_consecutive_errors')):
                self.event(c, tid, 'error_limit_updated', f"失败阈值已更新：累计允许 {config.get('max_errors', 1000)} 次，连续允许 {config.get('max_consecutive_errors', 1000)} 次；两项都超过才暂停")
        return self.get(tid)

    def tick(self):
        if not self.tick_lock.acquire(blocking=False):
            return
        try:
            if self.s.stopped.is_set():
                return
            self.workers = {aid: worker for aid, worker in self.workers.items() if worker.is_alive()}
            if not self.db.all("SELECT 1 FROM tasks WHERE status='running' LIMIT 1"):
                return
            with self.db.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                for task in c.execute("SELECT id FROM tasks WHERE status='running'").fetchall():
                    self._finish(c, task['id'])
            for account in self.db.all("SELECT id,work_schedule FROM accounts WHERE send_enabled=1 AND can_send=1 AND status='ready' ORDER BY rowid"):
                aid = account['id']
                now = self.s.clock()
                work = json.loads(account['work_schedule']) if account['work_schedule'] else None
                if next_work_at(work, now) > now:
                    continue
                if aid in self.workers:
                    continue
                if self.s.background:
                    worker = threading.Thread(target=self._run_account, args=(aid,), daemon=True, name='sender-'+aid)
                    self.workers[aid] = worker
                    worker.start()
                else:
                    self._run_account(aid)
        finally:
            self.tick_lock.release()

    def _run_account(self, aid):
        lock = self.s.accounts.lock(aid)
        if not lock.acquire(blocking=False):
            return
        try:
            # Replies have a separate local pace, but share the account lock and
            # explicit pause/auth/work-time checks with first-contact sending.
            while not self.s.stopped.is_set():
                if self._send_next(aid, followup_only=True) is None:
                    break
            policy = self.s.accounts.get(aid)['send_policy']
            maximum = policy['max_batch_size']
            slots = self.rng.randint(1, maximum) if maximum > 1 else 1
            in_batch = False
            while slots and not self.s.stopped.is_set():
                result = self._send_next(aid, in_batch=in_batch)
                if result is None:
                    break
                if result != 'skipped':
                    slots -= 1
                    in_batch = True
        except Exception:
            # A worker failure must never overwrite another account's inflight result.
            with self.db.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                affected = c.execute("SELECT task_id,uid FROM recipients WHERE sender_account_id=? AND status='sending'", (aid,)).fetchall()
                for row in affected:
                    error = send_failure_reason({'reason': 'interrupted'})
                    self._interrupt_sequence(c, row['task_id'], row['uid'], error)
                    c.execute("UPDATE recipients SET status='failed',error=? WHERE task_id=? AND uid=?", (error, row['task_id'], row['uid']))
                    self.event(c, row['task_id'], 'failed', error, row['uid'])
                    self._finish(c, row['task_id'])
                c.execute("UPDATE accounts SET send_enabled=0,error='发送执行中断，请检查后恢复账号调度' WHERE id=?", (aid,))
        finally:
            lock.release()

    def _candidate(self, c, aid, now, excluded=(), followup_only=False):
        return c.execute("""SELECT t.* FROM tasks t JOIN accounts a ON a.id=?
            WHERE t.status='running' AND COALESCE(t.next_send_at,0)<=?
            AND t.id NOT IN (SELECT value FROM json_each(?))
            AND (?=1 OR (t.execution_mode='specified' AND EXISTS
                (SELECT 1 FROM task_accounts ta WHERE ta.task_id=t.id AND ta.account_id=a.id))
                OR (t.execution_mode='auto' AND NOT EXISTS
                (SELECT 1 FROM task_accounts ta JOIN tasks fixed ON fixed.id=ta.task_id
                    WHERE ta.account_id=a.id AND fixed.status='running' AND fixed.execution_mode='specified')))
            AND NOT EXISTS (SELECT 1 FROM recipients r WHERE r.sender_account_id=a.id AND r.status='sending')
            AND EXISTS (SELECT 1 FROM recipients r WHERE r.task_id=t.id AND r.status='pending')
            AND (?=0 OR EXISTS (SELECT 1 FROM recipients r JOIN task_messages m
                ON m.task_id=r.task_id AND m.uid=r.uid WHERE r.task_id=t.id AND r.status='pending'
                AND m.account_id=a.id AND m.status='pending' AND m.followup=1))
            ORDER BY CASE WHEN ?=1 AND EXISTS (SELECT 1 FROM recipients r JOIN task_messages m
                ON m.task_id=r.task_id AND m.uid=r.uid WHERE r.task_id=t.id AND r.status='pending'
                AND m.account_id=a.id AND m.followup=1 AND m.status='sent') THEN 0 ELSE 1 END,
                t.last_dispatched,t.created_at,t.id LIMIT 1""", (aid, now, dump(list(excluded)),followup_only,followup_only,followup_only)).fetchone()

    @staticmethod
    def _followup_owner(c, aid, uid):
        # A reply grants chat permission; it must not replay automation in a new task.
        # Existing attempts win over reservations, including deleted-task history.
        owner = c.execute('''SELECT m.task_id FROM task_messages m JOIN tasks t ON t.id=m.task_id
            WHERE m.uid=? AND m.account_id=? AND (m.followup=1 OR m.position>0)
            AND (m.attempted_at IS NOT NULL OR m.status IN ('sending','sent','failed')
                OR (m.status='pending' AND t.status!='deleted'))
            ORDER BY CASE WHEN m.attempted_at IS NOT NULL OR m.status IN ('sending','sent','failed') THEN 0 ELSE 1 END,
                COALESCE(m.attempted_at,t.created_at),t.created_at,t.id,m.position LIMIT 1''',(uid,aid)).fetchone()
        return owner['task_id'] if owner else None

    def _skip_duplicate_followup(self, c, tid, aid, uid):
        owner = self._followup_owner(c,aid,uid)
        if owner is None or owner==tid:
            return False
        reason = '该联系人与当前发送账号已在其他任务安排或执行自动跟进，本任务跳过重复跟进；可手动对话'
        pending = c.execute("SELECT 1 FROM recipients WHERE task_id=? AND uid=? AND status='pending'",(tid,uid)).fetchone()
        c.execute('''UPDATE recipients SET followup_enqueued=1,
            error=CASE WHEN status='pending' THEN ? ELSE error END,
            status=CASE WHEN status='pending' THEN 'skipped' ELSE status END
            WHERE task_id=? AND uid=?''',(reason,tid,uid))
        c.execute("UPDATE task_messages SET status='skipped',error=? WHERE task_id=? AND uid=? AND status='pending'",(reason,tid,uid))
        self.event(c,tid,'skipped' if pending else 'followup_skipped',reason,uid)
        return True

    def enqueue_followups(self, c, tid):
        task = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        if not task or task['status'] not in ('running','completed'):
            return 0
        config = json.loads(task['config'])
        plan = config.get('reply_messages', [])
        if not plan:
            return 0
        added = 0
        # Contacts who had already replied before this task starts use the same
        # fast queue, without waiting for a first-contact dispatch slot.
        if task['status']=='running':
            aids = (self.account_ids(c, task) if task['execution_mode']=='specified' else
                    [r['id'] for r in c.execute('SELECT id FROM accounts ORDER BY rowid')])
            pending = c.execute('''SELECT r.* FROM recipients r WHERE r.task_id=? AND r.status='pending'
                AND NOT EXISTS (SELECT 1 FROM task_messages m WHERE m.task_id=r.task_id AND m.uid=r.uid)
                AND EXISTS (SELECT 1 FROM conversation_messages m WHERE m.uid=r.uid AND m.sender_uid=m.uid)''',(tid,)).fetchall()
            for recipient in pending:
                replied = [aid for aid in aids if send_permission(c,aid,recipient['uid'])['state']=='replied']
                aid = next((aid for aid in replied if self._followup_owner(c,aid,recipient['uid']) in (None,tid)),
                           replied[0] if replied else None)
                if aid is None:
                    continue
                if self._skip_duplicate_followup(c,tid,aid,recipient['uid']):
                    continue
                for position, payload in enumerate(plan):
                    c.execute('''INSERT INTO task_messages(task_id,uid,position,account_id,kind,text,image_id,client_message_id,followup)
                        VALUES(?,?,?,?,?,?,?,?,1)''',(tid,recipient['uid'],position,aid,payload['kind'],payload.get('text'),payload.get('image_id'),str(uuid.uuid4())))
                c.execute('UPDATE recipients SET followup_enqueued=1 WHERE task_id=? AND uid=?',(tid,recipient['uid']))
        for recipient in c.execute('''SELECT r.* FROM recipients r WHERE r.task_id=? AND r.status='sent'
                AND r.followup_enqueued=0 AND EXISTS (SELECT 1 FROM conversation_messages m
                    WHERE m.uid=r.uid AND m.account_id=COALESCE(r.sender_account_id,?) AND m.sender_uid=m.uid)''', (tid,task['account_id'])).fetchall():
            uid = recipient['uid']
            aid = recipient['sender_account_id'] or task['account_id']
            if not aid or not any(message_preview(m)[0] for m in c.execute(
                    'SELECT * FROM conversation_messages WHERE account_id=? AND uid=? AND sender_uid=uid', (aid,uid))):
                continue
            rows = c.execute('SELECT * FROM task_messages WHERE task_id=? AND uid=? ORDER BY position', (tid,uid)).fetchall()
            # Older multi-message sequences have already used their reply plan.
            if len(rows)>1 or any(row['followup'] for row in rows):
                c.execute('UPDATE recipients SET followup_enqueued=1 WHERE task_id=? AND uid=?',(tid,uid))
                continue
            if self._skip_duplicate_followup(c,tid,aid,uid):
                continue
            if not rows:
                # Materialize the legacy first send before adding children, so
                # the existing history union keeps its original result intact.
                diagnostic = c.execute("SELECT detail FROM events WHERE task_id=? AND uid=? AND type='send_diagnostic' ORDER BY id DESC LIMIT 1",(tid,uid)).fetchone()
                c.execute('''INSERT INTO task_messages(task_id,uid,position,account_id,kind,text,status,
                    client_message_id,attempted_at,finished_at,diagnostic) VALUES(?,?,0,?,'text',?,'sent',?,?,?,?)''',
                    (tid,uid,aid,recipient['send_message'] if recipient['send_message'] is not None else config['message'],
                     recipient['client_message_id'] or str(uuid.uuid4()),recipient['attempted_at'],recipient['sent_at'],
                     diagnostic['detail'] if diagnostic else '{}'))
            position = max((row['position'] for row in rows), default=0)+1
            for index, payload in enumerate(plan, position):
                c.execute('''INSERT INTO task_messages(task_id,uid,position,account_id,kind,text,image_id,client_message_id,followup)
                    VALUES(?,?,?,?,?,?,?,?,1)''',(tid,uid,index,aid,payload['kind'],payload.get('text'),payload.get('image_id'),str(uuid.uuid4())))
            c.execute("UPDATE recipients SET status='pending',followup_enqueued=1 WHERE task_id=? AND uid=?",(tid,uid))
            self.event(c,tid,'followup_queued','已确认回复，按原账号排队跟进',uid)
            added += 1
        return added

    def _finish(self, c, tid):
        task = c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()
        if task and task['status']=='running':
            self.enqueue_followups(c,tid)
        if not c.execute("SELECT 1 FROM recipients WHERE task_id=? AND status IN ('pending','sending')", (tid,)).fetchone():
            finished_at = self.s.clock()
            updated = c.execute("UPDATE tasks SET status='completed',phase='idle',next_send_at=NULL,finished_at=? WHERE id=? AND status IN ('running','paused')", (finished_at, tid)).rowcount
            if updated:
                self.event(c, tid, 'completed', '全部目标处理结束')
                self.s.reply_reviews.enqueue(c, tid, finished_at)

    def close(self):
        with self.tick_lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.join(timeout=2)

    def _send_next(self, aid, *, in_batch=False, followup_only=False):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            now = self.s.clock()
            account = c.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
            if (not account or not account['send_enabled'] or not self.s.accounts.send_ready(account)
                    or next_work_at(json.loads(account['work_schedule']) if account['work_schedule'] else None, now) > now
                    or (not followup_only and ((not in_batch and (account['next_send_at'] or 0) > now)
                        or not self.s.accounts.protect(c, aid)))):
                return
            excluded = []
            chosen = None
            while chosen is None:
                selected = self._candidate(c, aid, now, excluded, followup_only)
                if not selected:
                    return
                task = {**dict(selected), **json.loads(selected['config'])}
                tid = task['id']
                excluded.append(tid)
                for pending in c.execute("""SELECT r.* FROM recipients r WHERE task_id=? AND status='pending'
                    ORDER BY CASE WHEN ?=1 AND EXISTS (SELECT 1 FROM task_messages m WHERE m.task_id=r.task_id
                        AND m.uid=r.uid AND m.followup=1 AND m.status='sent') THEN 0 ELSE 1 END,ordinal""", (tid,followup_only)).fetchall():
                    row = dict(pending)
                    existing = c.execute('SELECT * FROM task_messages WHERE task_id=? AND uid=? ORDER BY position', (tid, row['uid'])).fetchall()
                    if followup_only and not any(m['followup'] and m['status']=='pending' for m in existing):
                        continue
                    if existing and existing[0]['account_id'] != aid:
                        continue  # A started sequence always stays with its original account.
                    if self._skip_duplicate_followup(c,tid,aid,row['uid']):
                        self._finish(c,tid)
                        return 'skipped'
                    user = c.execute('SELECT deleted_at FROM users WHERE uid=?', (row['uid'],)).fetchone()
                    permission = send_permission(c, aid, row['uid'])
                    if permission['state'] == 'busy':
                        continue
                    if not permission['can_send'] and user and user['deleted_at'] is None:
                        # Give an eligible account with a reply the opportunity to claim.
                        if any(other != aid and send_permission(c, other, row['uid'])['state'] == 'replied'
                               for other in self.account_ids(c, task)):
                            continue
                    reason = ('用户已从用户库移除，跳过' if not user or user['deleted_at'] is not None
                              else permission['reason'] if not permission['can_send'] else '')
                    if reason:
                        c.execute("UPDATE recipients SET status='skipped',error=? WHERE task_id=? AND uid=?", (reason, tid, row['uid']))
                        c.execute("UPDATE task_messages SET status='skipped',error=? WHERE task_id=? AND uid=? AND status='pending'", (reason,tid,row['uid']))
                        self.event(c, tid, 'skipped', reason, row['uid'])
                        self._finish(c, tid)
                        return 'skipped'
                    if not existing:
                        first = task.get('first_message', {'kind':'text','text':task['message']})
                        candidates = task.get('first_messages', [first])
                        if permission['state'] != 'replied' and len(candidates) > 1:
                            first = self.rng.choice(candidates)
                        plan = task.get('reply_messages', [first]) if permission['state']=='replied' else [first]
                        if not plan:
                            reason = '已回复联系人未配置后续消息，跳过'
                            c.execute("UPDATE recipients SET status='skipped',error=? WHERE task_id=? AND uid=?",(reason,tid,row['uid']))
                            self.event(c,tid,'skipped',reason,row['uid']); self._finish(c,tid)
                            return 'skipped'
                        for position, payload in enumerate(plan):
                            c.execute('''INSERT INTO task_messages
                                (task_id,uid,position,account_id,kind,text,image_id,client_message_id)
                                VALUES(?,?,?,?,?,?,?,?)''', (tid,row['uid'],position,aid,payload['kind'],payload.get('text'),payload.get('image_id'),
                                                         row['client_message_id'] if position==0 else str(uuid.uuid4())))
                        if permission['state']=='replied':
                            c.execute('UPDATE recipients SET followup_enqueued=1 WHERE task_id=? AND uid=?',(tid,row['uid']))
                            c.execute('UPDATE task_messages SET followup=1 WHERE task_id=? AND uid=?',(tid,row['uid']))
                    chosen = c.execute("SELECT * FROM task_messages WHERE task_id=? AND uid=? AND status='pending' ORDER BY position LIMIT 1", (tid,row['uid'])).fetchone()
                    if chosen:
                        break
            outgoing = dict(chosen)
            assign_send_number(c,f"task:{tid}:{row['uid']}:{outgoing['position']}",aid,row['uid'],now)
            payload = ({'kind':'image','image_id':outgoing['image_id']} if outgoing['kind']=='image'
                       else {'kind':'text','text':outgoing['text']})
            c.execute("UPDATE task_messages SET status='sending',attempted_at=? WHERE task_id=? AND uid=? AND position=?", (now,tid,row['uid'],outgoing['position']))
            c.execute("UPDATE recipients SET status='sending',attempted_at=COALESCE(attempted_at,?),sender_account_id=?,send_message=? WHERE task_id=? AND uid=?", (now,aid,message_summary(payload),tid,row['uid']))
            order = self.event(c, tid, 'sending', '正在发送消息 · '+account['name'], row['uid'])
            c.execute("UPDATE tasks SET phase='sending',last_dispatched=? WHERE id=?", (order, tid))
        auth = None
        accepted = None
        status, error = 'sent', ''
        diagnostic = {}
        started = time.monotonic()
        try:
            auth = self.s.accounts.auth(aid)
            accepted = dispatch_message(self.s, auth, row['uid'], payload, outgoing['client_message_id'])
            if isinstance(accepted, dict):
                diagnostic = accepted.get('diagnostic', {})
            if not accepted:
                diagnostic = {'reason': 'incomplete_response'}
                status, error = 'failed', send_failure_reason(diagnostic)
        except (SendRejected, AccountIssue, SendUncertain) as exc:
            diagnostic = getattr(exc, 'diagnostic', {'reason': 'account_issue' if isinstance(exc, AccountIssue) else 'platform_rejected'})
            status, error = 'failed', send_failure_reason(diagnostic)
        except Exception as exc:
            diagnostic = exception_diagnostic(exc)
            status, error = 'failed', send_failure_reason(diagnostic)
        diagnostic['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        diagnostic['client_message_id'] = outgoing['client_message_id']
        now = self.s.clock()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            policy = self.s.accounts.snapshot(c, aid)['send_policy']
            delay = policy['interval_seconds'] + self.rng.randint(0, policy['random_extra_seconds'])
            c.execute("UPDATE task_messages SET status=?,finished_at=?,error=?,diagnostic=? WHERE task_id=? AND uid=? AND position=? AND status='sending'",
                      (status,now,error,dump(diagnostic),tid,row['uid'],outgoing['position']))
            if status=='failed':
                c.execute("UPDATE task_messages SET status='skipped',error='前一条发送失败，停止该联系人后续消息' WHERE task_id=? AND uid=? AND status='pending'", (tid,row['uid']))
            remaining = c.execute("SELECT 1 FROM task_messages WHERE task_id=? AND uid=? AND status='pending'",(tid,row['uid'])).fetchone()
            recipient_status = 'pending' if remaining else status
            c.execute("UPDATE recipients SET status=?,sent_at=?,error=? WHERE task_id=? AND uid=? AND status='sending'",
                      (recipient_status, now if recipient_status=='sent' else None, error, tid, row['uid']))
            if not outgoing['followup']:
                c.execute('UPDATE accounts SET next_send_at=? WHERE id=?', (now+delay, aid))
            if status=='sent':
                remember_conversation(c, account, row['uid'], accepted, now)
            note = send_result_note(diagnostic)
            self.event(c, tid, status, ('发送成功' + (' · '+note if note else '')) if status=='sent' else error, row['uid'])
            self.event(c, tid, 'send_diagnostic', dump(diagnostic), row['uid'])
            if diagnostic.get('reason') == 'account_issue' and diagnostic.get('cause_reason') not in ('network_timeout', 'network_error'):
                c.execute("UPDATE accounts SET can_send=0,error='发送凭证需要重新检查' WHERE id=?", (aid,))
            elif status == 'sent':
                c.execute("""UPDATE accounts SET can_send=1,error='' WHERE id=? AND status='ready' AND can_search=1
                    AND qr_status='confirmed' AND error IN ('发送凭证需要重新检查','发送校验网络异常，请稍后检查；保留原发送状态')""", (aid,))
            if not outgoing['followup']:
                self.s.accounts.protect(c, aid)
            if status == 'failed':
                config = json.loads(c.execute('SELECT config FROM tasks WHERE id=?', (tid,)).fetchone()[0])
                failed = c.execute("SELECT count(*) FROM recipients WHERE task_id=? AND status='failed'", (tid,)).fetchone()[0]
                consecutive = self._consecutive_errors(c, tid)
                limit, streak_limit = config.get('max_errors', 1000), config.get('max_consecutive_errors', 1000)
                if failed > limit and consecutive > streak_limit:
                    reason = f'累计失败 {failed} 次，超过允许 {limit} 次；连续失败 {consecutive} 次，超过允许 {streak_limit} 次，两项均超限，任务已暂停；最近错误：{error}'
                    if c.execute("UPDATE tasks SET status='paused',phase='idle',error=? WHERE id=? AND status='running'", (reason, tid)).rowcount:
                        self.event(c, tid, 'error_limit_reached', reason)
            self._finish(c, tid)
        # Persist rotating credentials after the business outcome; a persistence error cannot re-send it.
        if auth is not None:
            self.s.accounts.save(aid, auth)
        return status

    def dashboard(self, tid, after=0):
        after = integer(after, '事件游标', 0)
        # One read transaction keeps counts, current recipient, and events in the same frame.
        with self.db.connect() as c:
            c.execute('BEGIN')
            task = self._snapshot(c, tid)
            active = c.execute("""SELECT r.data,r.uid,r.status,r.sender_account_id,a.name AS account_name
                FROM recipients r LEFT JOIN accounts a ON a.id=r.sender_account_id
                WHERE r.task_id=? AND r.status='sending' ORDER BY r.ordinal""", (tid,)).fetchall()
            targets = [{**json.loads(r['data']), 'status': r['status'],
                        'account_id': r['sender_account_id'], 'account_name': r['account_name']} for r in active]
            target = targets[0] if targets else None
            if not target and task['status'] == 'running':
                current = c.execute("SELECT data,status FROM recipients WHERE task_id=? AND status='pending' ORDER BY ordinal LIMIT 1", (tid,)).fetchone()
                target = {**json.loads(current['data']), 'status': current['status']} if current else None
            events = [dict(r) for r in c.execute('SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT 200', (tid, after))]
            trend = [dict(r) for r in c.execute("""SELECT CAST(time/60 AS INTEGER)*60 AS minute,
                SUM(type='sending') AS attempted,SUM(type='sent') AS sent,SUM(type='failed') AS failed
                FROM events WHERE task_id=? AND type IN ('sending','sent','failed') GROUP BY minute ORDER BY minute DESC LIMIT 30""", (tid,))]
            return {'task': task, 'current': target, 'current_recipients': targets, 'events': events, 'trend': list(reversed(trend)), 'now': self.s.clock()}

@@END_FILE@@
@@FILE {"path": "web/verification.py", "bytes": 2326, "sha256": "8f97754ca3f813f97b510fe5c610d183406fb6dd5436eed9d86ebfd704c4fe99"}
"""Transport for the official component, bound to a pending QR login only."""
import logging

from dy_apis.login_api import DYLoginApi


# Exact paths used by the official component's SMS, password and phone-face flows.
VERIFICATION_METHODS = {
    '/passport/safe/get_auth_ticket/v1/': 'POST',
    '/passport/safe/verify_auth_ticket/': 'POST',
    '/passport/safe/query_decision/': 'GET',
    '/passport/web/get_qrcode/': 'POST',
    '/passport/web/send_code/': 'POST',
    '/passport/web/validate_code/': 'POST',
    '/passport/web/mobile/check_code/': 'POST',
    '/passport/web/account/verify/': 'POST',
    '/passport/upsms/verify/': 'POST',
    '/passport/upsms/safe_mobile/verify/': 'POST',
    '/passport/upsms/chain_mobile/verify/': 'POST',
}


def omit_verification_access_log(record):
    # The SDK puts challenge material in both URL parameters and form bodies.
    return '/verification-request/' not in record.getMessage()


logging.getLogger('werkzeug').addFilter(omit_verification_access_log)


def request_verification(auth, path, method, query, data, incoming_headers):
    if VERIFICATION_METHODS.get(path) != method:
        raise ValueError('不支持该验证接口或请求方法')
    if len(data) > 65536 or sum(len(k) + len(v) for k, v in query) > 32768:
        raise ValueError('验证请求过大')
    try:
        headers = DYLoginApi._passport_headers(auth, form=True, api=path).get()
        for name in ('Content-Type', 'X-Tt-Passport-Trace-Id', 'X-Tt-Passport-Verify-Portrait'):
            if incoming_headers.get(name):
                headers[name.lower()] = incoming_headers[name]
        # No browser Cookies or caller-selected host. Set-Cookie stays in the QR Auth.
        response = auth.request(method, 'https://login.douyin.com' + path,
                                headers=headers, params=query, data=data,
                                allow_redirects=False, verify=True, timeout=20)
        if not 200 <= response.status_code < 300:
            raise RuntimeError('verification upstream rejected request')
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError('verification upstream response format')
        return result
    except Exception:
        raise RuntimeError('verification upstream request failed') from None

@@END_FILE@@
@@FILE {"path": "web/work_time.py", "bytes": 1958, "sha256": "d1a1d79c4691af1b4a158e52c55fc94538a08b14874fbcc3211218b8e15228ab"}
"""One local-time working window on selected weekdays, shared by account scheduling."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')


def _minutes(value, *, end=False):
    if end and value == '24:00':
        return 1440
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
        raise ValueError('工作时间请使用 HH:MM 格式')
    hour, minute = map(int, value.split(':'))
    return hour*60+minute


def validate_schedule(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'weekdays','start','end'}:
        raise ValueError('工作时间配置无效')
    days = value['weekdays']
    if (not isinstance(days, list) or not 1 <= len(days) <= 7
            or any(type(day) is not int or not 1 <= day <= 7 for day in days)):
        raise ValueError('请至少选择一个工作日，周一至周日为 1～7')
    start, end = _minutes(value['start']), _minutes(value['end'], end=True)
    if start >= end:
        raise ValueError('结束时间必须晚于开始时间，暂不支持跨午夜时段')
    return {'weekdays':sorted(set(days)), 'start':value['start'], 'end':value['end']}


def next_work_at(schedule, timestamp):
    if schedule is None:
        return timestamp
    local = datetime.fromtimestamp(timestamp, ZONE)
    midnight = local.replace(hour=0,minute=0,second=0,microsecond=0)
    start, end = _minutes(schedule['start']), _minutes(schedule['end'], end=True)
    for offset in range(8):
        day = midnight + timedelta(days=offset)
        if day.isoweekday() not in schedule['weekdays']:
            continue
        begin, finish = (day+timedelta(minutes=start)).timestamp(), (day+timedelta(minutes=end)).timestamp()
        if timestamp < finish:
            return max(timestamp, begin)
    raise ValueError('工作时间未包含有效工作日')

@@END_FILE@@
@@END_BUNDLE 23
