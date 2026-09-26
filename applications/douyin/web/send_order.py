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
