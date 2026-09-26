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
