from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


NUMERIC = {'min_followers': 'follower_count', 'min_likes': 'received_like_count'}


def integer(value, label, minimum=0, maximum=None):
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise ValueError(f'{label}请输入整数')
    result = int(value)
    if result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f'{label}超出允许范围')
    return result


def date_bound(value, end=False):
    try:
        dt = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        return (dt + timedelta(days=int(end))).timestamp()
    except (TypeError, ValueError):
        raise ValueError('日期格式应为 YYYY-MM-DD') from None


def select(rows, filters):
    if not isinstance(filters, dict):
        raise ValueError('筛选条件格式错误')
    if 'min_collections' in filters:
        raise ValueError('收藏筛选已移除，请刷新页面后重新筛选')
    thresholds = {field: integer(filters[key], key) for key, field in NUMERIC.items()
                  if filters.get(key) not in (None, '')}
    bounds = {}
    for stem, field in [('created', 'created_at'), ('sent', 'last_sent_at')]:
        lo = date_bound(filters[stem+'_from']) if filters.get(stem+'_from') else None
        hi = date_bound(filters[stem+'_to'], True) if filters.get(stem+'_to') else None
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError('开始日期不能晚于结束日期')
        bounds[field] = lo, hi
    sent = filters.get('sent', 'all')
    if sent not in ('', 'all', 'sent', 'unsent', 'replied'):
        raise ValueError('发送状态无效')
    blue_v = filters.get('blue_v') or 'all'
    if blue_v not in ('all', 'yes', 'no', 'unknown'):
        raise ValueError('蓝 V 筛选条件无效')
    query = str(filters.get('query') or '').strip().casefold()
    tags = set(tag_ids(filters.get('tag_ids', [])))
    tag_mode = filters.get('tag_mode') or 'any'
    if tag_mode not in ('any', 'all'):
        raise ValueError('标签匹配方式无效')
    result = []
    for row in rows:
        user_tags = {tag['id'] for tag in row.get('tags', [])}
        if tags and (not tags.intersection(user_tags) if tag_mode == 'any' else not tags.issubset(user_tags)):
            continue
        if blue_v != 'all' and row.get('is_blue_v') is not {'yes': True, 'no': False, 'unknown': None}[blue_v]:
            continue
        if query and not any(query in str(row.get(k) or '').casefold()
                             for k in ['nickname', 'douyinhao', 'uid', 'name', 'source_keyword']):
            continue
        if sent == 'sent' and not row.get('sent'):
            continue
        if sent == 'unsent' and row.get('sent'):
            continue
        if sent == 'replied' and not row.get('replied'):
            continue
        if any(row.get(field) is None or row[field] <= value for field, value in thresholds.items()):
            continue
        if any((lo is not None and (value is None or value < lo)) or
               (hi is not None and (value is None or value >= hi))
               for field, (lo, hi) in bounds.items()
               for value in [row.get('_sent_filter_at', row.get(field)) if field == 'last_sent_at' else row.get(field)]):
            continue
        result.append(row)
    order = filters.get('sort') or 'created_desc'
    sorts = {'created_desc': 'created_at', 'followers_desc': 'follower_count',
             'likes_desc': 'received_like_count', 'name': 'nickname'}
    if order not in sorts:
        raise ValueError('排序方式无效')
    field = sorts[order]
    if order == 'name':
        return sorted(result, key=lambda r: (str(r.get(field) or ''), r.get('uid', '')))
    return sorted(result, key=lambda r: (r.get(field) if r.get(field) is not None else -1, r.get('uid', '')), reverse=True)


def paginate(rows, filters):
    page = integer(filters.get('page', 1), '页码', 1)
    size = integer(filters.get('page_size', 20), '每页条数', 1, 100)
    return {'items': rows[(page-1)*size:page*size], 'total': len(rows), 'page': page, 'page_size': size}


def chosen_ids(value):
    if not isinstance(value, list) or not value:
        raise ValueError('请至少选择一位用户')
    if any(not isinstance(x, str) or not x for x in value):
        raise ValueError('用户 ID 格式错误')
    return list(dict.fromkeys(value))


def tag_ids(value):
    if isinstance(value, str):
        value = value.split(',') if value else []
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or not x or ',' in x for x in value):
        raise ValueError('请选择有效标签，最多 100 个')
    return list(dict.fromkeys(value))


def id_batches(uids):
    # Keep even three repeated IN clauses below SQLite's 999-variable limit.
    for start in range(0, len(uids), 300):
        yield uids[start:start+300]
