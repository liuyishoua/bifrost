"""Small persistent ledger. Credentials never enter this database."""

import json
import sqlite3
import threading
import uuid
from .booking_options import sleeper_labels
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.instance_id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))")
        self.db.execute("""CREATE TABLE IF NOT EXISTS train_catalog (
            from_station TEXT NOT NULL, to_station TEXT NOT NULL, train TEXT NOT NULL,
            reference_date TEXT NOT NULL, seat_types TEXT NOT NULL, departure TEXT NOT NULL,
            arrival TEXT NOT NULL, observed_at REAL NOT NULL,
            PRIMARY KEY(from_station,to_station,train))""")
        if 'duration' not in {row[1] for row in self.db.execute('PRAGMA table_info(train_catalog)')}:
            self.db.execute("ALTER TABLE train_catalog ADD COLUMN duration TEXT NOT NULL DEFAULT ''")
        if 'price_query' not in {row[1] for row in self.db.execute('PRAGMA table_info(train_catalog)')}:
            self.db.execute("ALTER TABLE train_catalog ADD COLUMN price_query TEXT NOT NULL DEFAULT '{}'")
        history_exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_history'").fetchone()
        self.db.execute("CREATE TABLE IF NOT EXISTS session_history (id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, body TEXT NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS session_history_account ON session_history(account_id, id)")
        if not history_exists:
            for (body,) in self.db.execute("SELECT body FROM records WHERE kind='account'").fetchall():
                record = json.loads(body)
                for event in record.get('session_events', []):
                    self.db.execute("INSERT INTO session_history(account_id,body) VALUES (?,?)", (record['id'], json.dumps(dict(event, legacy=True), ensure_ascii=False)))
        self.db.commit()

    def put(self, kind, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, value['id'], json.dumps(value, ensure_ascii=False)))

    def list(self, kind):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def delete(self, kind, key):
        with self.lock, self.db:
            self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, key))

    def append_session_event(self, account_id, event):
        with self.lock, self.db:
            self.db.execute("INSERT INTO session_history(account_id,body) VALUES (?,?)", (account_id, json.dumps(event, ensure_ascii=False)))

    def session_history(self, account_id, before=None, limit=50):
        limit = max(1, min(int(limit), 100))
        with self.lock:
            rows = self.db.execute("SELECT id,body FROM session_history WHERE account_id=? AND id<? ORDER BY id DESC LIMIT ?",
                                   (account_id, int(before) if before is not None else 9223372036854775807, limit+1)).fetchall()
        events = [dict(json.loads(body), id=key) for key, body in rows[:limit]]
        return dict(events=events, next_before=events[-1]['id'] if len(rows)>limit else None)

    def save_train_catalog(self, rows):
        with self.lock, self.db:
            self.db.executemany('''INSERT INTO train_catalog
                (from_station,to_station,train,reference_date,seat_types,departure,arrival,observed_at,duration,price_query)
                VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(from_station,to_station,train) DO UPDATE SET
                reference_date=excluded.reference_date,seat_types=excluded.seat_types,
                departure=excluded.departure,arrival=excluded.arrival,observed_at=excluded.observed_at,duration=excluded.duration,price_query=excluded.price_query''',
                [(r['from_station'],r['to_station'],r['train'],r['reference_date'],json.dumps(r['seat_types'],ensure_ascii=False),r['departure'],r['arrival'],r['observed_at'],r.get('duration',''),json.dumps(r.get('price_query',{}),sort_keys=True)) for r in rows])

    def train_catalog(self, origin, destination):
        origins = [origin] if isinstance(origin, str) else origin
        destinations = [destination] if isinstance(destination, str) else destination
        with self.lock:
            rows = self.db.execute(f'''SELECT train,reference_date,seat_types,departure,arrival,observed_at,duration,price_query,from_station,to_station
                FROM train_catalog WHERE from_station IN ({','.join('?' for _ in origins)}) AND to_station IN ({','.join('?' for _ in destinations)}) ORDER BY departure,train''', (*origins,*destinations)).fetchall()
        return [dict(train=r[0],reference_date=r[1],seat_types=sleeper_labels(json.loads(r[2]),json.loads(r[7]).get('seat_types','')),departure=r[3],arrival=r[4],observed_at=r[5],duration=r[6],price_query=json.loads(r[7]),prices=self.fare(json.loads(r[7])).get('prices',{}),from_station=r[8],to_station=r[9]) for r in rows]

    def fare(self, params):
        with self.lock:
            row = self.db.execute("SELECT body FROM records WHERE kind='fare' AND id=?", (json.dumps(params, sort_keys=True),)).fetchone()
        result = json.loads(row[0]) if row else {}
        if any(code in params.get('seat_types', '') and label not in result.get('prices', {}) for code,label in [('I','一等卧'),('J','二等卧')]):
            return {}
        return result

    def save_fare(self, params, result):
        self.put('fare', dict(result, id=json.dumps(params, sort_keys=True)))
