"""Workspace statistics from one SQLite snapshot; no platform requests."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .filters import integer, paginate
from .send_records import TASK_SENDS_SQL, TASK_SEND_STATS_SQL


def snapshot(service, filters):
    days = integer(filters.get('days', 7), '统计天数', 1)
    if days not in (7, 30):
        raise ValueError('统计范围支持近 7 天或近 30 天')
    page = integer(filters.get('timeline_page', 1), '时间轴页码', 1)
    now = service.clock()
    today = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0)
    beginning = today-timedelta(days=days-1)
    start = beginning.timestamp()
    daily = {(beginning+timedelta(days=i)).date().isoformat():
             {'date': (beginning+timedelta(days=i)).date().isoformat(),
              'sent_people': 0, 'sent_messages': 0, 'completed_tasks': 0} for i in range(days)}
    with service.db.connect() as c:
        c.execute('BEGIN')
        task_counts = {r['status']: r['n'] for r in c.execute('SELECT status,count(*) AS n FROM tasks GROUP BY status')}
        summary = {'total_tasks': sum(task_counts.values()),
                   'running_tasks': task_counts.get('running', 0), 'pending_tasks': task_counts.get('pending', 0),
                   'paused_tasks': task_counts.get('paused', 0), 'deleted_tasks': task_counts.get('deleted', 0),
                   'completed_tasks': c.execute('SELECT count(*) FROM tasks WHERE finished_at IS NOT NULL').fetchone()[0],
                   'sent_people': c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(DISTINCT uid) FROM sends WHERE status='sent'").fetchone()[0],
                   'sent_messages': c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(*) FROM sends WHERE status='sent'").fetchone()[0],
                   'failed_messages': c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(*) FROM sends WHERE status='failed'").fetchone()[0]}
        for r in c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT strftime('%Y-%m-%d',sent_at,'unixepoch','+8 hours') AS day,count(DISTINCT uid) AS people,count(*) AS messages FROM sends WHERE status='sent' AND sent_at>=? AND sent_at<=? GROUP BY day", (start, now)):
            daily[r['day']].update(sent_people=r['people'], sent_messages=r['messages'])
        for r in c.execute("SELECT strftime('%Y-%m-%d',finished_at,'unixepoch','+8 hours') AS day,count(*) AS n FROM tasks WHERE finished_at>=? AND finished_at<=? GROUP BY day", (start, now)):
            daily[r['day']]['completed_tasks'] = r['n']
        range_totals = {'sent_people': c.execute(f"WITH sends AS ({TASK_SEND_STATS_SQL}) SELECT count(DISTINCT uid) FROM sends WHERE status='sent' AND sent_at>=? AND sent_at<=?", (start, now)).fetchone()[0],
                        'completed_tasks': sum(r['completed_tasks'] for r in daily.values())}
        codes = {}
        for row in c.execute(f"""WITH sends AS ({TASK_SENDS_SQL}) SELECT r.status,r.diagnostic AS detail FROM sends r
                WHERE r.status IN ('sent','failed') AND r.attempted_at>=? AND r.attempted_at<=?""", (start, now)):
            try:
                diagnostic = json.loads(row['detail'] or '{}')
            except ValueError:
                diagnostic = {}
            code = diagnostic.get('business_code') if isinstance(diagnostic, dict) else None
            if type(code) is not int:
                code = None
            item = codes.setdefault(code, {'code': code, 'count': 0, 'sent': 0, 'failed': 0})
            item['count'] += 1
            item[row['status']] += 1
        business_codes = {'start': start, 'end': now, 'total': sum(r['count'] for r in codes.values()),
                          'items': sorted(codes.values(), key=lambda r: (-r['count'], r['code'] is None, r['code'] or 0))}
        running = []
        account_snapshots = {}
        for row in c.execute("SELECT id FROM tasks WHERE status='running' ORDER BY started_at,id").fetchall():
            task = service.tasks._snapshot(c, row['id'], account_snapshots)
            current = c.execute("SELECT data,status FROM recipients WHERE task_id=? AND status IN ('sending','pending') ORDER BY CASE status WHEN 'sending' THEN 0 ELSE 1 END,ordinal LIMIT 1", (row['id'],)).fetchone()
            task['current'] = {**json.loads(current['data']), 'status': current['status']} if current else None
            task['activity'] = ('sending' if current and current['status']=='sending' else
                                'scheduled' if (task['scheduled_at'] or 0)>now else
                                'waiting' if (task['next_send_at'] or 0)>now else 'queued')
            running.append(task)
        account_errors = {'window_minutes': 30, 'start': now-1800, 'end': now, 'items': []}
        associated = {}
        for task in running:
            active = c.execute("""SELECT r.data,r.sender_account_id,a.name AS account_name FROM recipients r
                JOIN accounts a ON a.id=r.sender_account_id WHERE r.task_id=? AND r.status='sending' ORDER BY r.ordinal""", (task['id'],)).fetchall()
            task['current_recipients'] = [{**json.loads(r['data']), 'account_id': r['sender_account_id'],
                                          'account_name': r['account_name'], 'status': 'sending'} for r in active]
            aids = {a['id'] for a in task['accounts']} | {r['sender_account_id'] for r in active}
            for aid in aids:
                associated.setdefault(aid, []).append(task)
        for aid, tasks in associated.items():
            if aid not in account_snapshots:
                account_snapshots[aid] = service.accounts.snapshot(c, aid)
            account = account_snapshots[aid]
            counts = account['recent']
            account_errors['items'].append({
                'account_id': aid, 'account_name': account['name'],
                'task_id': tasks[0]['id'], 'task_name': '、'.join(t['name'] for t in tasks),
                'tasks': [{'id': t['id'], 'name': t['name']} for t in tasks],
                **counts, 'dispatch_state': account['dispatch_state'],
                'next_send_at': account['next_send_at'], 'available_at': account['available_at'],
                'rest_until': account['rest_until'], 'rest_reason': account['rest_reason'],
            })
        rows = [dict(r) for r in c.execute('''SELECT t.id,t.name,t.status,t.created_at,t.started_at,t.finished_at,
                    CASE WHEN t.execution_mode='auto' THEN '共享账号池' ELSE
                    (SELECT group_concat(a.name,'、') FROM task_accounts ta JOIN accounts a ON a.id=ta.account_id WHERE ta.task_id=t.id) END AS account_name,COALESCE(t.started_at,t.created_at) AS began,
                    COALESCE(t.finished_at,CASE WHEN t.status='deleted' THEN COALESCE(d.time,t.created_at)
                    WHEN t.status='pending' THEN t.created_at ELSE ? END) AS ended,
                    t.status='deleted' AS deleted
                FROM tasks t
                LEFT JOIN (SELECT task_id,max(time) AS time FROM events WHERE type='deleted' GROUP BY task_id) d ON d.task_id=t.id
                WHERE t.created_at<=? ORDER BY began DESC,t.id''', (now, now))
                if r['ended']>=start and r['began']<=now]
        timeline = paginate(rows, {'page': page, 'page_size': 20})
        # Zoom to actual activity within the selected window; paused/waiting time remains visible.
        axis_start = max(start, min((r['began'] for r in rows), default=start))
        axis_end = min(now, max((r['ended'] for r in rows), default=now))
        timeline.update(start=axis_start, end=max(axis_start+60, axis_end))
    return {'now': now, 'days': days, 'summary': summary, 'range_totals': range_totals,
            'running': running, 'account_errors': account_errors,
            'business_codes': business_codes,
            'daily': list(daily.values()), 'timeline': timeline}
