"""Read-only public adult fares; never use or alter an account session."""
import re
import threading

import requests

from ticket_app.configuration import AppError, BASE_URL
from .service import validate_date

PRICE_REQUESTS = threading.BoundedSemaphore(3)

PRICE_KEYS = {'A9': '商务座', 'P': '特等座', 'M': '一等座', 'O': '二等座',
              'AI': '一等卧', 'AJ': '二等卧', 'A6': '高级软卧', 'A4': '软卧', 'A3': '硬卧', 'A2': '软座', 'A1': '硬座', 'WZ': '无座'}


def parse_prices(data):
    prices = {}
    for key, name in PRICE_KEYS.items():
        value = str(data.get(key, '')).strip().lstrip('¥￥').strip()
        if re.fullmatch(r'\d{1,6}(?:\.\d{1,2})?', value) and float(value) > 0:
            prices[name] = float(value)
    return prices


def query_prices(data):
    params = {}
    for key, pattern in [('train_no', r'[A-Za-z0-9]{1,20}'), ('from_station_no', r'\d{2,3}'),
                         ('to_station_no', r'\d{2,3}'), ('seat_types', r'[A-Za-z0-9]{1,40}')]:
        value = data.get(key, '')
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise AppError('票价查询参数缺失，请重新查询车次')
        params[key] = value
    params['train_date'] = validate_date(data.get('train_date'))
    try:
        with PRICE_REQUESTS, requests.Session() as session:
            response = session.get(f'{BASE_URL}/otn/leftTicket/queryTicketPrice', params=params,
                                   headers={'User-Agent': 'Mozilla/5.0', 'Referer': f'{BASE_URL}/otn/leftTicket/init'}, timeout=8)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or payload.get('status') is not True or not isinstance(payload.get('data'), dict):
            raise ValueError('invalid price response')
        return {'prices': parse_prices(payload['data']), 'date': params['train_date']}
    except (requests.RequestException, ValueError):
        raise AppError('票价暂未获取，请稍后重试') from None
