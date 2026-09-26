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
