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
