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
