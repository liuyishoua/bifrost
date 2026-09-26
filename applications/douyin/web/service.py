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
