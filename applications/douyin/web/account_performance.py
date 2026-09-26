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
