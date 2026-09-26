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
