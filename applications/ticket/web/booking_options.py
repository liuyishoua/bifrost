"""Per-train seat choices backed by observed timetable metadata."""
import re
from ticket_app.configuration import AppError, SEAT_SPECS, SHARED_BERTH_CODES


def seat_stock(ticket, label):
    """Show shared berth stock only when its actual order code is unambiguous."""
    spec = SEAT_SPECS[label]
    stock = ticket['seats'].get(spec.stock_key, '--')
    if spec.stock_key in SHARED_BERTH_CODES:
        raw_codes = ticket.get('seat_types')
        codes = set(raw_codes.strip()) if isinstance(raw_codes, str) else set()
        matching = codes & SHARED_BERTH_CODES[spec.stock_key]
        if len(matching) != 1 or spec.submit_code not in matching:
            return '--'
    return stock


def validate_options(options, catalog):
    if not isinstance(options, list) or not options:
        raise AppError('请添加至少一个车次')
    known = {row['train']: row for row in catalog}
    selected = []
    seen = set()
    for item in options:
        if not isinstance(item, dict):
            raise AppError('车次配置格式错误')
        train = str(item.get('train', '')).strip().upper()
        if not re.fullmatch(r'[A-Z]?\d{1,5}', train):
            raise AppError('请输入正确车次，例如 Z111、G101')
        if train in seen:
            raise AppError(f'{train} 重复，请在同一行选择席别')
        seen.add(train)
        row = known.get(train)
        if row and any(item.get(k) and item[k] != row.get(k) for k in ('from_station','to_station')):
            raise AppError(f'{train} 的车站与参考资料不符，请刷新后重选')
        if not row:
            raise AppError(f'{train} 暂无该区间的参考资料，请先刷新车次')
        seats = item.get('seat_types')
        if not isinstance(seats, list) or not seats or any(not isinstance(s, str) for s in seats) or len(set(seats)) != len(seats):
            raise AppError(f'请为 {train} 选择不重复的席别')
        if any(s not in row['seat_types'] for s in seats):
            raise AppError(f'{train} 的席别与参考资料不符，请刷新后重选')
        selected.append(dict(from_station=row.get('from_station',''), to_station=row.get('to_station',''), train=train, seat_types=list(seats), reference_date=row['reference_date'], observed_at=row['observed_at'], departure=row.get('departure',''), arrival=row.get('arrival',''), duration=row.get('duration',''), prices=dict(row.get('prices',{}))))
    return selected


def filter_candidates(candidates, config):
    options = config.get('train_options')
    if not options:
        return candidates
    by_train = {item['train']: item['seat_types'] for item in options}
    routes = {item['train']: item for item in options}
    candidates = [c for c in candidates if all(not routes.get(c['ticket']['station_train_code'].upper(), {}).get(k) or c['ticket'].get(k) == routes[c['ticket']['station_train_code'].upper()][k] for k in ('from_station','to_station'))]
    candidates = [c for c in candidates if c['seat_label'] in by_train.get(c['ticket']['station_train_code'].upper(), [])]
    if config.get('priority_strategy', 'train_first') == 'train_first':
        order = {item['train']: index for index, item in enumerate(options)}
        candidates.sort(key=lambda c: (order[c['ticket']['station_train_code'].upper()], by_train[c['ticket']['station_train_code'].upper()].index(c['seat_label'])))
    return candidates


def sleeper_labels(labels, codes):
    """Correct legacy shared-column labels only when the observed code is explicit."""
    mapping = {}
    if 'I' in codes and '4' not in codes:
        mapping['软卧'] = '一等卧'
    if 'J' in codes and '3' not in codes:
        mapping['硬卧'] = '二等卧'
    return list(dict.fromkeys(mapping.get(label, label) for label in labels))
