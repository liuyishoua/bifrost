import sqlite3
import uuid
from .filters import id_batches, chosen_ids, tag_ids


DEFAULT_TAG = 'used-car-dealer'
CATALOGS = {
    'tags': ('tags', {'name': (64, True), 'description': (500, False)}),
    'message-templates': ('message_templates', {'title': (120, True), 'description': (500, False), 'content': (2000, True)}),
}


class Library:
    def __init__(self, service):
        self.s, self.db = service, service.db

    def list(self, kind):
        table, _ = CATALOGS[kind]
        if kind == 'tags':
            rows = self.db.all('''SELECT t.*, (SELECT count(*) FROM user_tags ut JOIN users u ON u.uid=ut.uid
                WHERE ut.tag_id=t.id AND u.deleted_at IS NULL) AS user_count FROM tags t ORDER BY created_at,id''')
            for row in rows:
                row['is_default'] = row['id'] == DEFAULT_TAG
        else:
            rows = self.db.all(f'SELECT * FROM {table} ORDER BY updated_at DESC,id')
        return {'items': rows}

    def save(self, kind, data, item_id=None):
        table, fields = CATALOGS[kind]
        if not data or set(data) - fields.keys():
            raise ValueError('请填写有效的名称、描述或话术内容')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone() if item_id else None
            if item_id and not old:
                raise LookupError('记录不存在')
            values = {}
            for field, (limit, required) in fields.items():
                value = data.get(field, old[field] if old else '')
                if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
                    names = {'name': '标签名称', 'title': '话术标题', 'description': '描述', 'content': '话术内容'}
                    raise ValueError(f'{names[field]}格式无效，最多 {limit} 字' + ('且不能为空' if required else ''))
                values[field] = value.strip()
            now = self.s.clock()
            try:
                if item_id:
                    assignments = ','.join(f'{key}=?' for key in values)
                    c.execute(f'UPDATE {table} SET {assignments},updated_at=? WHERE id=?', (*values.values(), now, item_id))
                else:
                    item_id = uuid.uuid4().hex
                    columns = ','.join(values)
                    marks = ','.join('?' for _ in values)
                    c.execute(f'INSERT INTO {table}(id,{columns},created_at,updated_at) VALUES(?,{marks},?,?)',
                              (item_id, *values.values(), now, now))
            except sqlite3.IntegrityError:
                raise ValueError('标签名称已存在，请使用其他名称') from None
            return dict(c.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone())

    def delete(self, kind, item_id):
        table, _ = CATALOGS[kind]
        self.db.run(f'DELETE FROM {table} WHERE id=?', (item_id,))
        return {'ok': True}

    def validate_tags(self, c, values):
        if not isinstance(values, list):
            raise ValueError('标签选项格式错误')
        ids = tag_ids(values)
        existing = {r['id'] for r in c.execute('SELECT id FROM tags')}
        if set(ids) - existing:
            raise ValueError('部分标签已被删除，请刷新标签后重新选择')
        return ids

    def user_tags(self, data):
        uids = chosen_ids(data.get('uids'))
        action = data.get('action')
        if action not in ('add', 'remove', 'replace'):
            raise ValueError('标签操作无效')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            ids = self.validate_tags(c, data.get('tag_ids'))
            if not ids and action != 'replace':
                raise ValueError('请至少选择一个标签')
            found = 0
            for batch in id_batches(uids):
                marks = ','.join('?' for _ in batch)
                found += c.execute(f'SELECT count(*) FROM users WHERE uid IN ({marks}) AND deleted_at IS NULL', batch).fetchone()[0]
            if found != len(uids):
                raise ValueError('部分用户已被移除，请重新选择')
            for uid in uids:
                if action == 'replace':
                    c.execute('DELETE FROM user_tags WHERE uid=?', (uid,))
                for tag_id in ids:
                    if action == 'remove':
                        c.execute('DELETE FROM user_tags WHERE uid=? AND tag_id=?', (uid, tag_id))
                    else:
                        c.execute('INSERT OR IGNORE INTO user_tags VALUES(?,?)', (uid, tag_id))
        return {'updated': len(uids)}
