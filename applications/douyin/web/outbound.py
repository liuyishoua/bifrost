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
