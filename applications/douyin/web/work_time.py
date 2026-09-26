"""One local-time working window on selected weekdays, shared by account scheduling."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')


def _minutes(value, *, end=False):
    if end and value == '24:00':
        return 1440
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
        raise ValueError('工作时间请使用 HH:MM 格式')
    hour, minute = map(int, value.split(':'))
    return hour*60+minute


def validate_schedule(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'weekdays','start','end'}:
        raise ValueError('工作时间配置无效')
    days = value['weekdays']
    if (not isinstance(days, list) or not 1 <= len(days) <= 7
            or any(type(day) is not int or not 1 <= day <= 7 for day in days)):
        raise ValueError('请至少选择一个工作日，周一至周日为 1～7')
    start, end = _minutes(value['start']), _minutes(value['end'], end=True)
    if start >= end:
        raise ValueError('结束时间必须晚于开始时间，暂不支持跨午夜时段')
    return {'weekdays':sorted(set(days)), 'start':value['start'], 'end':value['end']}


def next_work_at(schedule, timestamp):
    if schedule is None:
        return timestamp
    local = datetime.fromtimestamp(timestamp, ZONE)
    midnight = local.replace(hour=0,minute=0,second=0,microsecond=0)
    start, end = _minutes(schedule['start']), _minutes(schedule['end'], end=True)
    for offset in range(8):
        day = midnight + timedelta(days=offset)
        if day.isoweekday() not in schedule['weekdays']:
            continue
        begin, finish = (day+timedelta(minutes=start)).timestamp(), (day+timedelta(minutes=end)).timestamp()
        if timestamp < finish:
            return max(timestamp, begin)
    raise ValueError('工作时间未包含有效工作日')
