import json
import uuid
from .db import dump
from .douyin import normalize
from .filters import chosen_ids, integer, select, paginate


class Searches:
    def __init__(self, service):
        self.s, self.db = service, service.db
        self.db.run("UPDATE searches SET status='interrupted',error='服务重启，已取得结果保留，可重新搜索' WHERE status='running'")

    def create(self, data):
        aid = str(data.get('account_id') or '')
        a = self.s.accounts.get(aid)
        if not a['can_search'] or a['qr_status'] in ('waiting', 'starting', 'scanned', 'verifying'):
            raise ValueError('请先完成账号校验和扫码')
        query = str(data.get('query') or '').strip()
        if not query or len(query) > 100:
            raise ValueError('请输入 1～100 字搜索关键词')
        pages = integer(data.get('max_pages', 1), '查询页数', 1, 100)
        sid = uuid.uuid4().hex
        self.db.run("INSERT INTO searches(id,account_id,query,max_pages,status,created_at) VALUES(?,?,?,?,'running',?)",
                    (sid, aid, query, pages, self.s.clock()))
        self.s.spawn(self._run, sid)
        return {'id': sid}

    def get(self, sid):
        row = self.db.one('SELECT * FROM searches WHERE id=?', (sid,))
        row['total'] = self.db.one('SELECT count(*) AS n FROM search_results WHERE search_id=?', (sid,))['n']
        row.pop('collect', None)
        row.pop('collection_done', None)
        return row

    def _run(self, sid):
        job = self.get(sid)
        aid, cursor, seen = job['account_id'], '0', set()
        remote_search_id = ''
        try:
            for page_no in range(job['max_pages']):
                if self.s.stopped.is_set():
                    raise RuntimeError('stopped')
                if cursor in seen:
                    raise ValueError('cursor repeated')
                seen.add(cursor)
                with self.s.accounts.lock(aid):
                    auth = self.s.accounts.auth(aid)
                    response = self.s.adapter.search_page(auth, job['query'], cursor, search_id=remote_search_id)
                    self.s.accounts.save(aid, auth)
                if response.get('status_code', 0) != 0 or not isinstance(response.get('user_list'), list):
                    raise ValueError('invalid result')
                items = response['user_list']
                with self.db.connect() as c:
                    invalid = 0
                    for index, raw in enumerate(items):
                        profile = normalize(raw, job['query'])
                        if profile:
                            profile['created_at'] = job['created_at']
                            c.execute('INSERT OR IGNORE INTO search_results VALUES(?,?,?,?)',
                                      (sid, profile['uid'], dump(profile), page_no*25+index))
                        else:
                            invalid += 1
                    c.execute('UPDATE searches SET completed_pages=?,cursor=?,returned_count=returned_count+?,invalid_count=invalid_count+? WHERE id=?', (page_no+1, cursor, len(items), invalid, sid))
                if response.get('has_more') == 0:
                    break
                if response.get('has_more') != 1 or not items:
                    raise ValueError('pagination incomplete')
                # Upstream cursor is authoritative, fixed-size offset only for older responses.
                cursor = str(response.get('cursor', int(cursor)+25))
                remote_search_id = (response.get('log_pb') or {}).get('impr_id', '')
            self.db.run("UPDATE searches SET status='completed' WHERE id=?", (sid,))
        except Exception:
            self.db.run("UPDATE searches SET status='interrupted',error='查询中断，已取得结果保留。请检查账号、网络或平台验证状态后重新查询' WHERE id=?", (sid,))

    def rows(self, sid):
        self.get(sid)
        return [json.loads(r['data']) for r in self.db.all('SELECT data FROM search_results WHERE search_id=? ORDER BY ordinal', (sid,))]

    def results(self, sid, filters):
        return paginate(select(self.rows(sid), filters), filters)

    def selection(self, sid, data):
        rows = select(self.rows(sid), data.get('filters') or {})
        if data.get('limit') not in (None, ''):
            rows = rows[:integer(data['limit'], '选择人数', 1)]
        return {'uids': [r['uid'] for r in rows], 'total': len(rows)}

    def import_users(self, sid, uids, tag_ids=None):
        uids = chosen_ids(uids)
        available = {r['uid']: r for r in self.rows(sid)}
        if set(uids) - available.keys():
            raise ValueError('选择包含不属于本次搜索的用户，请刷新结果')
        inserted = updated = 0
        now = self.s.clock()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if tag_ids is None:
                tag_ids = [r['id'] for r in c.execute("SELECT id FROM tags WHERE id='used-car-dealer'")]
            tag_ids = self.s.library.validate_tags(c, tag_ids)
            for uid in uids:
                profile = available[uid]
                old = c.execute('SELECT data FROM users WHERE uid=?', (uid,)).fetchone()
                if old:
                    c.execute('UPDATE users SET data=?,updated_at=?,deleted_at=NULL WHERE uid=?', (dump(profile), now, uid))
                    updated += 1
                else:
                    c.execute('INSERT INTO users(uid,data,created_at,updated_at) VALUES(?,?,?,?)', (uid, dump(profile), now, now))
                    inserted += 1
                c.executemany('INSERT OR IGNORE INTO user_tags VALUES(?,?)', [(uid, tag_id) for tag_id in tag_ids])
        return {'inserted': inserted, 'updated': updated}
