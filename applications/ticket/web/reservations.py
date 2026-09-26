"""Reservation dates and the official station sale-time lookup."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from ticket_app.configuration import AppError

SHANGHAI = ZoneInfo('Asia/Shanghai')
SALE_URL = 'https://kyfw.12306.cn/otn/index12306/queryAllCacheSaleTime'


def today():
    return datetime.now(SHANGHAI).date()


def reference_date(target, current=None):
    current = current or today()
    target = date.fromisoformat(target)
    return current + timedelta(days=7 + (target.weekday() - current.weekday()) % 7)


def sale_datetime(target, sale_time):
    if not re.fullmatch(r'(?:[01]\d|2[0-3])[0-5]\d', sale_time):
        raise AppError('官方起售时间格式无法识别')
    day = date.fromisoformat(target) - timedelta(days=14)
    return datetime.combine(day, datetime.strptime(sale_time, '%H%M').time(), SHANGHAI)


def query_sale_time(session, station, on_date):
    try:
        response = session.post(SALE_URL, data={}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('status') is not True or not isinstance(payload.get('data'), list):
            raise AppError('官方起售时间查询未成功')
        day = on_date.strftime('%Y%m%d')
        matches = {r.get('sale_time') for r in payload['data'] if isinstance(r, dict)
                   and r.get('station_name') == station
                   and str(r.get('start_date', '')) <= day <= str(r.get('stop_date', ''))}
        if len(matches) != 1:
            raise AppError('官方起售时间缺失或冲突，等待重新核验')
        value = matches.pop()
        sale_datetime(on_date.isoformat(), value or '')
        return value
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise AppError('官方起售时间暂不可用，等待重新核验') from exc


def cadence_delay(interval, started, now):
    # Serial requests: no concurrent query and no accumulated catch-up requests.
    return max(0.0, interval - (now - started))


def train_sale_datetime(text, expected):
    """A train-specific announcement overrides the general station time."""
    if '起售' not in text:
        return None
    match = re.search(r'(?:(\d{1,2})月(\d{1,2})日\s*)?(\d{1,2})(?::|：|点|时)(?:(\d{1,2})分?)?钟?\s*起售', text)
    if not match:
        raise AppError('车次有起售公告，但时间格式无法识别，等待核验')
    month, day, hour, minute = match.groups()
    minute = minute or '0'
    try:
        if month:
            candidates = []
            for year in (expected.year - 1, expected.year, expected.year + 1):
                try:
                    candidates.append(expected.replace(year=year, month=int(month), day=int(day), hour=int(hour), minute=int(minute), second=0, microsecond=0))
                except ValueError:
                    continue
            return min(candidates, key=lambda dt: abs((dt - expected).total_seconds()))
        return expected.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    except ValueError as exc:
        raise AppError('车次起售公告日期无效，等待核验') from exc


def station_evidence(boarding_date, station, sale_time, checked_at):
    return dict(kind='station_estimate', station=station, boarding_date=boarding_date,
                presale_days=15, sale_day=(date.fromisoformat(boarding_date)-timedelta(days=14)).isoformat(),
                station_time=f'{sale_time[:2]}:{sale_time[2:]}', checked_at=checked_at,
                source_url='https://www.12306.cn/index/view/infos/sale_time.html')
