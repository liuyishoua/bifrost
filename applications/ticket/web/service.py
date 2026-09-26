"""Account isolation, read-only queries and explicit booking task lifecycle."""

import base64
from contextlib import nullcontext
import copy
import hashlib
import json
import logging
import re
import random
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from ticket_app.client import RailwayClient, order_redirect_kind
from ticket_app.configuration import AppConfig, AppError, SEAT_SPECS
from ticket_app.logging_utils import redact_text
from ticket_app.runner import TicketRunner
from ticket_app.runtime import CancellationToken, RunCancelled
from .store import Store
from .order_queue import AccountOrderBlocked, AccountTurns, RetryableOrderRejection, inventory_rejection, query_interval
from .sessions import AccountSession
from .auth import check_account_session, session_event
from .diagnostics import DiagnosticSession, start_trace, end_trace
from .booking_options import validate_options, filter_candidates, seat_stock
from .reservations import today, reference_date, sale_datetime, cadence_delay, SHANGHAI, train_sale_datetime


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = {'starting', 'waiting', 'querying', 'submitting', 'queueing', 'stopping'}
ORDER_ENDPOINTS = {'submitOrderRequest', 'initDc', 'checkOrderInfo', 'getQueueCount', 'confirmSingleForQueue', 'queryOrderWaitTime'}


def clean(value):
    return redact_text(re.sub(r'<[^>]*>', '', str(value)))[:600]


def validate_date(value, future=False):
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise AppError('请选择正确的乘车日期')
    if parsed < today() or (not future and parsed > today() + timedelta(days=14)):
        raise AppError(f'可查询日期为 {today()} 至 {today() + timedelta(days=14)}（含今天共15天）')
    return value


def make_config(values):
    return AppConfig.from_mapping({
        'FROM_STATION': values.get('from_station', '北京南'),
        'TO_STATION': values.get('to_station', '上海虹桥'),
        'TRAIN_DATE': validate_date(values.get('train_date', today().isoformat()), future=values.get('reservation', False)),
        'PASSENGER_NAMES': values.get('passenger_names', []),
        'SEAT_TYPES': values.get('seat_types', list(SEAT_SPECS)),
        'PREFERRED_TRAINS': values.get('trains', []),
        'ONLY_PREFERRED_TRAINS': bool(values.get('trains')),
        'EMPTY_TRAIN_SCOPE': 'all',
        'PRIORITY_STRATEGY': values.get('priority_strategy', 'train_first'),
        'AUTO_SUBMIT': values.get('auto_submit', False),
        'START_AT': values.get('start_at', ''),
        'STOP_AT': values.get('stop_at', ''),
        'QUERY_INTERVAL_SECONDS': values.get('interval', 3),
        'HOT_QUERY_INTERVAL_SECONDS': values.get('hot_interval', 0.5),
        'HOT_WINDOW_SECONDS': 60,
        'MAX_RETRIES': values.get('max_retries', 1000),
        'PRE_QUERY_SECONDS': 0,
        'PERSIST_SESSION': False,
        'SEAT_POSITION_PREFERENCES': values.get('positions', []),
        'BERTH_PREFERENCE': values.get('berth', {}),
        'REQUEST_TIMEOUT_SECONDS': 10,
        'ORDER_WAIT_ATTEMPTS': 60,
        'TIME_SYNC_SAMPLES': 3,
        'PERF_LOG': False,
    }, ROOT / 'config.py')


class Account:
    def __init__(self, record, directory, store, restoring=False):
        self.record = record
        self.session = AccountSession(directory / (record["id"] + ".cookies"), observer=lambda kind, message, **details: session_event(self, store, kind, message, **details))
        self.lock = threading.Lock()
        self.turns = AccountTurns()
        self.logged_in = False
        self.passengers = {}
        self.login = {'status': 'idle', 'message': '尚未登录', 'qr': '', 'expires_at': 0}
        self.token = CancellationToken()
        start_trace(self, store, 'service_restore' if restoring else 'account_create')
        if record['identity']:
            try:
                self.logged_in = self.session.restore() and record.get('session_state') != 'expired'
                if self.logged_in:
                    self.login.update(status='confirmed', message='会话已恢复，使用时自动校验')
            except (OSError, ValueError):
                self.login['message'] = '本地会话读取失败，请重新登录'
        end_trace(self, store)

    def invalidate(self):
        self.session.forget()
        self.logged_in = False
        self.passengers = {}


class LoginClient(RailwayClient):
    identity = ''
    on_invalid = None

    def check_session(self):
        original = copy.deepcopy(self.session.cookies)
        try:
            valid = super().check_session()
        except (requests.RequestException, AppError):
            self.session.cookies = original
            if isinstance(self.session, AccountSession):
                self.session.save()
            raise
        if not valid and self.on_invalid:
            self.session.cookies = original
            if isinstance(self.session, AccountSession):
                self.session.save()
            return self.on_invalid()
        return valid

    def _complete_login(self):
        ok, identity = super()._complete_login()
        if ok:
            self.identity = identity
        return ok, identity


class BookingClient(RailwayClient):
    """Only explicit inventory rejection before queue confirmation permits retry."""
    def submit_order_request(self, ticket):
        # The query payload may contain the train's origin date, not the selected boarding date.
        ok, message = super().submit_order_request(dict(ticket, date=self.cfg.train_date))
        if not ok:
            if inventory_rejection(message):
                raise RetryableOrderRejection('提交前已确认余票不足')
            raise AppError(f'提交被拒绝：{clean(message)}')
        return ok, message

    def check_order_info(self, passengers, token):
        result = super().check_order_info(passengers, token)
        if not result.success and inventory_rejection(result.message):
            raise RetryableOrderRejection('订单校验已确认余票不足，尚未提交排队')
        return result


class Workbench:
    def __init__(self, directory=ROOT / '.runtime_web', launch=None):
        self.session_directory = Path(directory) / 'sessions'
        self.store = Store(Path(directory) / 'workbench.sqlite3')
        self.lock = threading.RLock()
        self.accounts = {r['id']: Account(r, self.session_directory, self.store, restoring=True) for r in self.store.list('account')}
        self.tasks = {r['id']: r for r in self.store.list('task')}
        self.tokens = {}
        self.launch = launch or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        snapshot = json.loads((ROOT / 'assets/stations_snapshot.json').read_text())
        self.stations = snapshot.get('stations', snapshot)
        self.station_cities = json.loads((ROOT / 'assets/station_cities.json').read_text())['station_cities']
        self.closing = False
        self.scheduler_stop = threading.Event()
        for task in self.tasks.values():
            if task['status'] in {'draft','stopped','reserved'} and not task.get('order_attempted') and not task.get('order_id'):
                from .booking_options import sleeper_labels
                cfg = task['config']
                catalog = {r['train']: r for r in self.store.train_catalog(cfg['from_station'], cfg['to_station'])}
                changed = False
                for option in cfg.get('train_options', []):
                    codes = catalog.get(option['train'], {}).get('price_query', {}).get('seat_types', '')
                    labels = sleeper_labels(option['seat_types'], codes)
                    changed |= labels != option['seat_types']
                    option['seat_types'] = labels
                if changed:
                    cfg['seat_types'] = list(dict.fromkeys(s for o in cfg['train_options'] for s in o['seat_types']))
                    self.event(task, '已按官方席别代码纠正一等卧 / 二等卧，车次与优先级保留')
            if not task['config'].get('auto_submit') and not task.get('order_attempted') and task['status'] in ACTIVE | {'draft','reserved'}:
                task['config']['auto_submit'] = True
                task.update(status='draft', message='任务规则已升级为自动提交订单，请查看规则后重新启动')
                self.store.put('task', task)
            if task['status'] == 'reserved' and 'verification_attempts' not in task:
                task['next_check'] = 0
                task.pop('sale_at', None)
                task.pop('sale_evidence', None)
                self.store.put('task', task)
            if task['status'] in ACTIVE:
                if task['config'].get('reservation') and not task.get('order_attempted'):
                    task.update(status='reserved', next_check=0, message='服务已重启，预约保留；请登录账号')
                    self.store.put('task', task)
                    continue
                task['status'] = 'interrupted'
                task['message'] = '服务重启，任务未恢复；请核对官方订单后重新配置'
                account = self.accounts.get(task['account_id'])
                if account and task['config'].get('auto_submit'):
                    account.record['review_required'] = True
                    self.store.put('account', account.record)
                self.store.put('task', task)

    def account(self, key):
        if key not in self.accounts:
            raise AppError('账号不存在')
        return self.accounts[key]

    def state(self):
        with self.lock:
            tasks = copy.deepcopy([t for t in self.tasks.values() if t['status'] != 'deleted'][::-1])
            for task in tasks:
                cfg = task['config']
                saved = {r['train']: r for r in cfg.get('train_options', [])}
                task['timetable'] = []
                for train in cfg['trains']:
                    option = saved.get(train, {})
                    catalog = self.store.train_catalog(option.get('from_station') or cfg['from_station'], option.get('to_station') or cfg['to_station'])
                    row = next((r for r in catalog if r['train'] == train), option)
                    task['timetable'].append(dict(row, train=train))
            return {'accounts': [dict(a.record, logged_in=a.logged_in, busy=a.lock.locked()) for a in self.accounts.values()],
                    'tasks': tasks,
                    'today': today().isoformat(), 'max_date': (today() + timedelta(days=14)).isoformat()}

    def add_account(self, name):
        name = str(name).strip()
        if not name or len(name) > 40:
            raise AppError('账号备注需要1至40个字')
        with self.lock:
            record = {'id': uuid.uuid4().hex, 'name': name, 'identity': '', 'review_required': False, 'created_at': time.time()}
            self.store.put('account', record)
            self.accounts[record['id']] = Account(record, self.session_directory, self.store)
            return record

    def change_account(self, key, action, data):
        with self.lock:
            a = self.account(key)
            if a.lock.locked():
                raise AppError('账号正在登录或执行任务，请先停止并等待结束')
            if action == 'rename':
                name = str(data.get('name', '')).strip()
                if not 1 <= len(name) <= 40:
                    raise AppError('账号备注需要1至40个字')
                a.record['name'] = name
            elif action == 'review':
                if data.get('confirmed') is not True:
                    raise AppError('请先在12306核对并处理未完成订单')
                a.record['review_required'] = False
            elif action == 'logout':
                start_trace(a, self.store, 'logout')
                a.invalidate()
                end_trace(a, self.store)
                a.login = dict(status='idle', message='已退出登录', qr='', expires_at=0)
            elif action == 'delete':
                if a.record['review_required']:
                    raise AppError('该账号有待核对订单，请先处理')
                if any(t['account_id'] == key and t['status'] != 'deleted' for t in self.tasks.values()):
                    raise AppError('账号有关联任务记录，可退出登录保留历史')
                start_trace(a, self.store, 'delete_account')
                a.invalidate()
                end_trace(a, self.store)
                a.session.close()
                self.store.delete('account', key)
                del self.accounts[key]
                return {}
            else:
                raise AppError('不支持的账号操作')
            self.store.put('account', a.record)
            return a.record

    def login(self, key):
        with self.lock:
            a = self.account(key)
            if not a.lock.acquire(blocking=False):
                raise AppError('账号正忙，请稍后再试')
            a.token = CancellationToken()
            a.login = dict(status='starting', message='正在准备登录', qr='', expires_at=0, active=True)
            self.launch(lambda: self._login(a))
            return a.login.copy()

    def _login(self, a):
        start_trace(a, self.store, 'check_login')
        def event(e):
            with self.lock:
                a.login['message'] = clean(e.message)
                if e.kind == 'qr_ready':
                    session_event(a, self.store, 'qr_ready', '需要扫码，二维码已生成', preview=False, expires_at=e.data['expires_at'])
                    a.login.update(qr=base64.b64encode(e.data['image_bytes']).decode(), expires_at=e.data['expires_at'])
                if e.kind == 'qr_status':
                    status = e.data.get('status', 'waiting')
                    if status in {'waiting', 'scanned', 'confirmed', 'logged_in', 'expired'}:
                        session_event(a, self.store, 'qr_status', '二维码状态变化', preview=False, qr_status=status)
                    a.login['status'] = 'verifying' if status in {'confirmed', 'logged_in'} else status
        try:
            # Validate a candidate session before committing it to this account.
            candidate = DiagnosticSession(observer=a.session.observer)
            if a.record['identity']:
                candidate.cookies.update(copy.deepcopy(a.session.cookies))
            client = LoginClient(make_config({}), session=candidate, cancel_token=a.token, event_sink=event)
            client.on_invalid = lambda: check_account_session(a, client, self.store, checked=True)
            client.ensure_login()
            if not client.check_session():
                raise AppError('扫码确认后会话校验失败，请重新登录')
            identity = client.identity or a.record['identity']
            if not identity or identity == 'Success':
                raise AppError('未获取到可验证的登录身份')
            with self.lock:
                if a.record['identity'] and a.record['identity'] != identity:
                    raise AppError('扫码身份与该账号不一致，请使用原账号扫码或新增账号')
                if any(other is not a and other.record['identity'] == identity for other in self.accounts.values()):
                    raise AppError('这个12306身份已绑定其他账号，请使用已有账号')
                a.record['identity'] = identity
                a.record['session_state'] = 'valid'
                a.record['session_check_next'] = time.time() + max(1, random.uniform(0, 1200))
                self.store.put('account', a.record)
                a.session.cookies = candidate.cookies
                a.session.enabled = True
                a.session.save()
                a.logged_in = True
                a.login.update(status='confirmed', message='登录成功', qr='')
                session_event(a, self.store, 'login', '登录成功，会话已保存')
        except Exception as exc:
            if not a.record['identity'] or not a.session.cookies:
                a.invalidate()
            a.login.update(status='failed', message=clean(exc), qr='')
            session_event(a, self.store, 'login_failed', '登录未完成，原因见本次接口及二维码状态记录', exception_type=type(exc).__name__, reason='identity_mismatch' if '身份' in str(exc) else 'cancelled' if isinstance(exc, RunCancelled) else 'login_failed')
        finally:
            if 'candidate' in locals():
                candidate.close()
            a.login['active'] = False
            end_trace(a, self.store)
            a.lock.release()

    def cancel_login(self, key):
        with self.lock:
            a = self.account(key)
            if a.login.get('active'):
                session_event(a, self.store, 'cancel_requested', '用户请求停止登录')
                a.token.cancel()
            return {'message': '已请求停止登录'}

    def passengers(self, key):
        a = self.account(key)
        if not a.lock.acquire(blocking=False):
            raise AppError('账号正在执行任务，暂时不能更新乘车人')
        start_trace(a, self.store, 'passengers')
        try:
            client = RailwayClient(make_config({}), session=a.session)
            if not a.logged_in or not check_account_session(a, client, self.store):
                a.logged_in = False
                raise AppError('登录已失效，请扫码登录')
            records = client.get_passengers()
            a.passengers = {hashlib.sha256((str(p.get('passenger_id_type_code')) + ':' + str(p.get('passenger_id_no'))).encode()).hexdigest()[:20]: p for p in records}
            return [{'id': key, 'name': p['passenger_name'], 'type': str(p.get('passenger_type', '1'))} for key, p in a.passengers.items()]
        finally:
            end_trace(a, self.store)
            a.lock.release()

    def prices(self, data):
        from .prices import query_prices
        params = {key: data.get(key, '') for key in ('train_no', 'from_station_no', 'to_station_no', 'seat_types', 'train_date')}
        cached = self.store.fare(params)
        if cached and data.get('refresh') is not True and time.time() - cached.get('fetched_at', 0) < 300:
            return cached
        result = query_prices(params)
        if result['prices']:
            result['fetched_at'] = time.time()
            self.store.save_fare(params, result)
        return result

    @staticmethod
    def price_params(ticket, query_date):
        return dict(train_no=ticket.get('train_no',''), from_station_no=ticket.get('from_station_no',''), to_station_no=ticket.get('to_station_no',''), seat_types=ticket.get('seat_types',''), train_date=query_date) if ticket.get('train_no') and ticket.get('seat_types') else {}

    def query(self, data):
        target = validate_date(data.get('train_date'), future=True)
        reference = date.fromisoformat(target) > today() + timedelta(days=14)
        query_date = reference_date(target).isoformat() if reference else target
        cfg = make_config(dict(data, train_date=query_date, auto_submit=False))
        if cfg.from_station not in self.stations or cfg.to_station not in self.stations:
            raise AppError('请从站点列表选择正确的出发站和到达站')
        a = self.account(data.get('account_id'))
        if not a.lock.acquire(blocking=False):
            raise AppError('此账号正在执行任务或登录，请选择其他空闲账号查询')
        start_trace(a, self.store, 'query')
        try:
            client = RailwayClient(cfg, session=a.session)
            if not a.logged_in or not check_account_session(a, client, self.store):
                a.logged_in = False
                raise AppError('登录已失效，请先扫码登录')
            rows = client.query_tickets(self.stations[cfg.from_station], self.stations[cfg.to_station], raise_on_error=True)
            self.remember_trains(rows, query_date)
            fares = {id(t): self.store.fare(self.price_params(t, query_date)) for t in rows}
            return {'trains': [dict(train=t['station_train_code'], from_station=t['from_station'], to_station=t['to_station'],
                                   price_query=self.price_params(t, query_date) or None,
                                   prices=fares[id(t)].get('prices', {}),
                                   priceDone=time.time()-fares[id(t)].get('fetched_at', 0)<300,
                                   departure=t['start_time'], arrival=t['arrive_time'], duration=t['duration'], can_buy=False if reference else t['can_buy'],
                                   status='参考车次，目标日期待核验' if reference else t['button_text'], seats={name: ('参考' if seat_stock(t, name) not in ('--', '') else '--') if reference else seat_stock(t, name) for name in SEAT_SPECS}) for t in rows],
                    'queried_at': time.time(), 'reference': reference, 'reference_date': query_date,
                    'route': {'from_station': cfg.from_station, 'to_station': cfg.to_station, 'train_date': target}}
        finally:
            end_trace(a, self.store)
            a.lock.release()

    def remember_trains(self, rows, query_date):
        observed = time.time()
        self.store.save_train_catalog([dict(train=t['station_train_code'].upper(),from_station=t['from_station'],to_station=t['to_station'],
            reference_date=query_date,price_query=self.price_params(t, query_date),departure=t.get('start_time',''),arrival=t.get('arrive_time',''),duration=t.get('duration',''),observed_at=observed,
            seat_types=[name for name in SEAT_SPECS if seat_stock(t, name) not in (None,'','--')]) for t in rows])

    def sale_plan(self, data):
        target = validate_date(data.get('train_date'), future=True)
        station = data.get('from_station')
        if station not in self.stations:
            raise AppError('请选择正确的出发站')
        midnight = sale_datetime(target, '0000')
        return dict(verification_schedule=[(midnight + timedelta(minutes=20*i)).isoformat() for i in range(3)])

    def train_options(self, data):
        origin, destination = data.get('from_station',''), data.get('to_station','')
        if not data.get('same_city'):
            return self.store.train_catalog(origin, destination)
        def peers(station):
            city = self.station_cities.get(station)
            return [s for s in self.stations if city and self.station_cities.get(s) == city] or [station]
        rows = self.store.train_catalog(peers(origin), peers(destination))
        # One boarding choice per train. Prefer the original route when it is available.
        rows.sort(key=lambda r: (r['from_station'] != origin, r['to_station'] != destination, r['departure']))
        unique = {}
        for row in rows:
            unique.setdefault(row['train'], row)
        return sorted(unique.values(), key=lambda r: (r['departure'], r['train']))

    def query_task_routes(self, client, values, task=None):
        options = values.get('train_options') or [dict(train=t) for t in values['trains']]
        routes = {}
        for option in options:
            route = tuple(option.get(k) or values[k] for k in ('from_station','to_station'))
            routes.setdefault(route, set()).add(option['train'])
        tickets = []
        failures = []
        for (origin, destination), trains in routes.items():
            try:
                rows = client.query_tickets(self.stations[origin], self.stations[destination], raise_on_error=True)
            except AppError as exc:
                message = f'{origin} → {destination} 查询失败：{clean(exc)}'
                failures.append(message)
                if task is not None:
                    self.event(task, message, kind='route_query_failed', from_station=origin, to_station=destination, trains=sorted(trains))
                continue
            tickets.extend(t for t in rows if t.get('from_station') == origin and t.get('to_station') == destination and t['station_train_code'].upper() in trains)
        if failures and not tickets:
            raise AppError('；'.join(failures))
        return tickets

    def validate_task(self, data, a):
        values = {key: copy.deepcopy(data[key]) for key in ('from_station', 'to_station', 'train_date', 'trains', 'seat_types', 'passenger_ids', 'auto_submit', 'priority_strategy', 'interval', 'max_retries', 'start_at', 'stop_at', 'positions', 'berth', 'reservation', 'hot_interval') if key in data}
        values.setdefault('interval', 60)
        if 'train_options' in data:
            options = validate_options(data['train_options'], self.train_options(dict(data, same_city=bool(data.get('reservation')))))
            values['train_options'] = options
            values['trains'] = [item['train'] for item in options]
            values['seat_types'] = list(dict.fromkeys(seat for item in options for seat in item['seat_types']))
        if not isinstance(values.get('trains'), list) or not values['trains']:
            raise AppError('至少选择一个目标车次')
        ids = values.get('passenger_ids', [])
        if not isinstance(ids, list) or not ids or len(ids) > 5 or len(set(ids)) != len(ids):
            raise AppError('请选择1至5位不重复的乘车人')
        if any(key not in a.passengers for key in ids):
            raise AppError('乘车人不属于该账号或已失效，请重新加载')
        # This first release supports the adult purchase protocol already used by the engine.
        if any(str(a.passengers[key].get('passenger_type', '1')) != '1' for key in ids):
            raise AppError('当前工作台仅支持成人票，请选择成人乘车人')
        values['passenger_names'] = [a.passengers[key]['passenger_name'] for key in ids]
        if values.get('auto_submit', True) is not True:
            raise AppError('任务现已统一自动提交订单，请刷新页面后重新配置')
        values['auto_submit'] = True
        if not isinstance(values.get('reservation', False), bool):
            raise AppError('预约模式格式错误')
        cfg = make_config(values)
        if not 0.2 <= cfg.hot_query_interval_seconds <= 300:
            raise AppError('开售查询间隔须为0.2至300秒')
        if cfg.from_station not in self.stations or cfg.to_station not in self.stations:
            raise AppError('车站不在站点列表中')
        if not 1 <= cfg.query_interval_seconds <= 300 or not 1 <= cfg.max_retries <= 100000:
            raise AppError('查询间隔须为1至300秒，最大次数为1至100000')
        for key in ('start_at', 'stop_at'):
            if values.get(key):
                try:
                    dt = datetime.strptime(values[key], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    raise AppError('开始和结束时间须包含日期与时分秒')
                if dt <= datetime.now():
                    raise AppError('定时时间必须晚于当前时间')
        if values.get('start_at') and values.get('stop_at') and values['stop_at'] <= values['start_at']:
            raise AppError('结束时间必须晚于开始时间')
        return values

    def edit_task(self, key, data):
        with self.lock:
            task = self.tasks.get(key)
            if task is None:
                raise AppError('任务不存在')
            if task['status'] not in {'draft', 'reserved', 'stopped'} or task.get('order_attempted') or task.get('order_id'):
                raise AppError('仅尚未开始抢票的任务可以编辑开始时间')
            if set(data) != {'start_at'}:
                raise AppError('只能编辑抢票开始时间')
            values = copy.deepcopy(task['config'])
            values['start_at'] = str(data['start_at']).strip()
            values = self.validate_task(values, self.account(task['account_id']))
            task['config'] = values
            for field in ('sale_at', 'sale_evidence', 'confirmed_sales', 'verification_attempts', 'verification_next', 'verification_complete', 'unconfirmed_trains'):
                task.pop(field, None)
            if task['status'] == 'reserved':
                task['next_check'] = 0
            self.event(task, '已更新抢票开始时间' if values['start_at'] else '已改为自动核验起售时间')
            return copy.deepcopy(task)

    def create_task(self, data):
        with self.lock:
            request_id = str(data.get('request_id', ''))
            if not re.fullmatch(r'[\w-]{8,80}', request_id):
                raise AppError('缺少有效请求标识，请刷新页面')
            existing = next((t for t in self.tasks.values() if t['request_id'] == request_id), None)
            if existing:
                return copy.deepcopy(existing)
            a = self.account(data.get('account_id'))
            if not a.logged_in:
                raise AppError('请先登录账号')
            cfg = self.validate_task(data, a)
            task = dict(id=uuid.uuid4().hex, request_id=request_id, account_id=a.record['id'], config=cfg,
                        status='draft', message='配置已保存，等待启动', created_at=time.time(), attempt=0, events=[], order_id='', order_attempted=False)
            self.store.put('task', task)
            self.tasks[task['id']] = task
            return copy.deepcopy(task)

    def start(self, key):
        with self.lock:
            task = self.tasks.get(key)
            if task is None:
                raise AppError('任务不存在')
            if task['status'] in ACTIVE:
                return copy.deepcopy(task)
            if task['status'] not in {'draft', 'stopped'}:
                raise AppError('此任务已结束，请重新配置新任务')
            if key in self.tokens:
                raise AppError('上一次执行尚未结束，请稍后继续')
            if task.get('order_attempted') or task.get('order_id'):
                raise AppError('此任务已提交过订单，请先核对官方订单')
            a = self.account(task['account_id'])
            if task['config'].get('reservation'):
                if self.closing:
                    raise AppError('服务正在停止')
                validate_date(task['config']['train_date'], future=True)
                message = '已预约，按设置的抢票开始时间执行' if task['config'].get('start_at') else '已预约，等待核验官方起售时间'
                task.update(status='reserved', message=message, next_check=0)
                if task.get('verification_failures', 0) >= 6:
                    task.update(verification_failures=0, verification_next=0)
                self.store.put('task', task)
                return copy.deepcopy(task)
            if self.closing or not a.logged_in:
                raise AppError('服务正在停止或账号未登录')
            if a.record['review_required']:
                raise AppError('该账号有待核对订单，请在账号管理中处理后继续')
            self.validate_task(task['config'], a)
            token = CancellationToken()
            self.tokens[key] = token
            task.update(status='starting', message='正在准备任务')
            self.store.put('task', task)
            self.launch(lambda: self._run(task, a, token))
            return copy.deepcopy(task)

    def delete_task(self, key):
        with self.lock:
            task = self.tasks.get(key)
            if task is None:
                raise AppError('任务不存在')
            if key in self.tokens or task['status'] in ACTIVE:
                raise AppError('请先停止任务，等待执行结束后再删除')
            if task['status'] == 'deleted':
                return {}
            # Keep the audit and request id; never clear an outstanding account order.
            updated = dict(task, deleted_status=task['status'], status='deleted', deleted_at=time.time())
            self.store.put('task', updated)
            task.update(updated)
            return {}

    def stop(self, key):
        with self.lock:
            task = self.tasks.get(key)
            if task is None:
                raise AppError('任务不存在')
            if key in self.tokens:
                self.tokens[key].cancel()
                task.update(status='stopping', message='正在停止，等待当前请求结束')
            elif task['status'] in {'draft', 'reserved'}:
                task.update(status='stopped', message='已暂停，可继续抢票')
            self.store.put('task', task)
            return copy.deepcopy(task)

    def event(self, task, message, **details):
        with self.lock:
            task['message'] = clean(message)
            task['events'].append(dict(at=time.time(), message=clean(message), **details))
            task['events'] = task['events'][-150:]
            self.store.put('task', task)

    def _run(self, task, a, token):
        def sink(event):
            with self.lock:
                if event.kind == 'phase' and event.data.get('phase') in {'submitting', 'queueing'}:
                    task['status'] = event.data['phase']
                if event.kind == 'order_success':
                    task['order_id'] = event.data['order_id']
            details = {key: event.data[key] for key in ('offset_seconds', 'rtt_ms') if key in event.data}
            self.event(task, event.message, **details)

        def diagnostics(response, *args, **kwargs):
            endpoint = response.url.split('?')[0].rsplit('/', 1)[-1]
            if endpoint not in ORDER_ENDPOINTS:
                return
            fields = {'endpoint': endpoint, 'http_status': response.status_code}
            if 300 <= response.status_code < 400:
                fields['redirect_kind'] = order_redirect_kind(response)
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    for key in ('status', 'httpstatus', 'result_code', 'code'):
                        if isinstance(payload.get(key), (str, int, bool)):
                            fields[key] = clean(payload[key]) if isinstance(payload[key], str) else payload[key]
                    for key in ('message', 'messages', 'validateMessages'):
                        value = payload.get(key)
                        if isinstance(value, str):
                            fields[key] = clean(value)
                        elif isinstance(value, list):
                            fields[key] = [clean(v) for v in value[:3] if isinstance(v, str)]
                    data = payload.get('data')
                    if isinstance(data, dict):
                        for key in ('errMsg', 'errorMsg', 'msg'):
                            if isinstance(data.get(key), str):
                                fields[key] = clean(data[key])
                        for key in ('submitStatus', 'errCode', 'waitTime'):
                            if isinstance(data.get(key), (str, int, bool)):
                                fields[key] = clean(data[key]) if isinstance(data[key], str) else data[key]
            except ValueError:
                fields['format'] = 'html' if endpoint == 'initDc' else 'non_json'
            self.event(task, f'{endpoint} 返回 HTTP {response.status_code}', **fields)

        owns_turn = False
        def acquire_turn():
            nonlocal owns_turn
            if a.lock.locked():
                task['status'] = 'waiting'
                self.event(task, '等待账号下单队列；其他任务当前正在使用会话')
            a.turns.acquire(a, token)
            owns_turn = True
            if a.record['review_required']:
                raise AccountOrderBlocked('账号有订单正在处理或待核对，本任务保留，暂停提交')
            if not a.logged_in:
                raise AppError('账号已退出登录，停止本任务')
            start_trace(a, self.store, 'booking_task')
            session_event(a, self.store, 'task_link', '账号下单队列放行本任务', preview=False, task_id=task['id'])
            a.session.hooks['response'].append(diagnostics)

        def release_turn():
            nonlocal owns_turn
            if owns_turn:
                if diagnostics in a.session.hooks['response']:
                    a.session.hooks['response'].remove(diagnostics)
                    end_trace(a, self.store)
                owns_turn = False
                a.lock.release()

        def wait_turn(seconds):
            release_turn()
            token.wait(seconds)
            acquire_turn()

        try:
            acquire_turn()
            token.checkpoint()
            cfg = make_config(task['config'])
            if not cfg.auto_submit:
                raise AppError('旧任务规则已停用，请刷新页面重新配置')
            runner = TicketRunner(cfg, session=a.session, cancel_token=token, event_sink=sink)
            runner.client = BookingClient(cfg, session=a.session, cancel_token=token, event_sink=sink)
            if task['config'].get('reservation') and task.get('sale_evidence', {}).get('kind') not in {'manual_start', 'train_announcement', 'on_sale'}:
                raise AppError('具体车次起售时间未确认，不能开始抢票')
            target_start = datetime.fromisoformat(task['sale_at']) if task.get('sale_at') else (datetime.strptime(cfg.start_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI) if cfg.start_at else None)
            if target_start:
                task['status'] = 'waiting'
                self.event(task, '等待开售前准备窗口')
                release_turn()
                runner.clock.sleep_until(target_start - timedelta(seconds=120), token)
                acquire_turn()
            if not check_account_session(a, runner.client, self.store):
                a.logged_in = False
                raise AppError('账号登录失效，请重新扫码')
            fresh = runner.client.get_passengers()
            by_id = {hashlib.sha256((str(p.get('passenger_id_type_code')) + ':' + str(p.get('passenger_id_no'))).encode()).hexdigest()[:20]: p for p in fresh}
            if any(key not in by_id for key in task['config']['passenger_ids']):
                raise AppError('乘车人已变化，请重新配置')
            selected = [by_id[key] for key in task['config']['passenger_ids']]
            if any(str(p.get('passenger_type', '1')) != '1' for p in selected):
                raise AppError('乘车人类型已变化，当前仅支持成人票')
            prepared = runner._prepare_passengers_by_seat_code(selected)
            if target_start:
                task['status'] = 'waiting'
                self.event(task, '正在校准12306服务器时间；HTTP Date仅提供秒级信息')
                runner.clock.sync(token, sink)
                self.event(task, f'准备完成，等待起售 {target_start.isoformat()}；提前1秒进入查询覆盖校时误差')
                release_turn()
                runner.clock.sleep_until(target_start - timedelta(seconds=1), token)
                acquire_turn()
            errors = 0
            rejected = set()
            for attempt in range(task.get('attempt', 0) + 1, cfg.max_retries + 1):
                token.checkpoint()
                validate_date(cfg.train_date)
                if cfg.stop_at and runner.clock.now_timestamp() >= datetime.strptime(cfg.stop_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI).timestamp():
                    break
                gap = getattr(a, 'next_booking_query', 0) - time.monotonic()
                while gap > 0:
                    token.wait(gap)
                    gap = getattr(a, 'next_booking_query', 0) - time.monotonic()
                a.next_booking_query = time.monotonic() + min(cfg.hot_query_interval_seconds, 0.5)
                started = time.perf_counter()
                interval = query_interval(task, runner.clock.now_timestamp(), cfg.query_interval_seconds, cfg.hot_query_interval_seconds)
                if attempt > 1 and attempt % 20 == 0 and (not target_start or runner.clock.now_timestamp() > target_start.timestamp() + 60):
                    if not check_account_session(a, runner.client, self.store):
                        a.logged_in = False
                        raise AppError('会话已失效，任务停止，请重新扫码')
                task.update(status='querying', attempt=attempt)
                if attempt == 1 and target_start:
                    self.event(task, '开始首轮余票查询', dispatch_offset_ms=round((runner.clock.now_timestamp() - target_start.timestamp()) * 1000, 1))
                try:
                    tickets = self.query_task_routes(runner.client, task['config'], task)
                    errors = 0
                except AppError as exc:
                    errors += 1
                    self.event(task, f'查询失败（连续{errors}/3次）：{exc}')
                    if errors >= 3:
                        raise
                    wait_turn(cadence_delay(interval, started, time.perf_counter()))
                    continue
                if attempt == 1 or attempt % 20 == 0:
                    self.event(task, '查询时效记录', query_rtt_ms=round((time.perf_counter()-started)*1000, 1), interval_seconds=interval)
                tickets_in_route = tickets
                candidates = filter_candidates(runner._find_candidates(tickets_in_route), task['config'])
                candidates = [c for c in candidates if c['stock'] == '有' or str(c['stock']).isdigit() and int(c['stock']) >= len(selected)]
                self.event(task, f'第 {attempt} 次查询：{len(tickets)} 趟车，{len(candidates)} 个符合人数的席别')
                now = runner.clock.now_timestamp()
                sales = task.setdefault('confirmed_sales', {})
                for ticket in tickets_in_route:
                    train = ticket['station_train_code'].upper()
                    if train in cfg.preferred_trains and train not in sales:
                        release = datetime.fromtimestamp(now, SHANGHAI) if ticket['can_buy'] else train_sale_datetime(ticket.get('button_text', ''), datetime.fromtimestamp(now, SHANGHAI))
                        if release is not None:
                            sales[train] = dict(sale_at=release.isoformat(), kind='on_sale' if ticket['can_buy'] else 'train_announcement')
                candidates = [c for c in candidates if c['ticket']['station_train_code'].upper() not in sales or sales[c['ticket']['station_train_code'].upper()]['kind'] == 'on_sale' or datetime.fromisoformat(sales[c['ticket']['station_train_code'].upper()]['sale_at']).timestamp() <= now]
                interval = query_interval(task, now, cfg.query_interval_seconds, cfg.hot_query_interval_seconds)
                candidate_key = lambda c: (c['ticket']['station_train_code'], c['seat_label'])
                remaining = [c for c in candidates if candidate_key(c) not in rejected]
                if candidates and not remaining:
                    rejected.clear()
                    remaining = candidates
                if remaining:
                    chosen = remaining[0]
                    self.event(task, f"发现 {chosen['ticket']['station_train_code']} {chosen['seat_label']} 余票 {chosen['stock']}")
                    token.checkpoint()
                    # Persist BEFORE any order request: crashes must not auto-resume an uncertain order.
                    task['order_attempted'] = True
                    a.record['review_required'] = True
                    self.store.put('account', a.record)
                    self.store.put('task', task)
                    try:
                        booked = runner._book_ticket(chosen, prepared)
                    except RetryableOrderRejection as exc:
                        rejected.add(candidate_key(chosen))
                        task['order_attempted'] = False
                        a.record['review_required'] = False
                        # Clear the task latch first: a crash between writes leaves the account blocked.
                        self.store.put('task', task)
                        self.store.put('account', a.record)
                        self.event(task, f'{exc}；让位给其他候选，重新确认余票后继续', rejected_train=candidate_key(chosen)[0], rejected_seat=chosen['seat_label'])
                        wait_turn(cadence_delay(interval, started, time.perf_counter()))
                        continue
                    if booked and task['order_id']:
                        task['status'] = 'success'
                        self.event(task, '订单已生成，请到12306核对并支付')
                    else:
                        task['status'] = 'review'
                        self.event(task, '未取得可确认的订单号，请核对官方订单；不会重复提交')
                    return
                wait_turn(cadence_delay(interval, started, time.perf_counter()))
            task['status'] = 'exhausted'
            self.event(task, '已到结束时间或最大次数，本次未取得订单')
        except AccountOrderBlocked as exc:
            task.update(status='reserved' if task['config'].get('reservation') else 'draft', next_check=time.time()+15)
            self.event(task, str(exc))
        except RunCancelled:
            task['status'] = 'review' if task['order_attempted'] else ('reserved' if self.closing and task['config'].get('reservation') else 'stopped')
            self.event(task, '已停止；提交阶段停止请核对官方订单' if task['order_attempted'] else '任务已停止')
        except Exception as exc:
            task['status'] = 'review' if task['order_attempted'] else ('reserved' if task['config'].get('reservation') and not task['attempt'] else 'failed')
            if token.is_cancelled and not task['order_attempted'] and not self.closing:
                task['status'] = 'stopped'
            if task['status'] == 'reserved':
                task['next_check'] = time.time() + 60
            self.event(task, clean(exc))
        finally:
            with self.lock:
                release_turn()
                self.store.put('task', task)
                self.tokens.pop(task['id'], None)

    def check_sessions(self):
        # A separate maintenance loop never blocks the sale-time scheduler.
        for a in list(self.accounts.values()):
            with self.lock:
                if self.closing or not a.logged_in or a.record['review_required']:
                    continue
                now = time.time()
                due = a.record.get('session_check_next')
                if not due:
                    a.record['session_check_next'] = now + max(1, random.uniform(0, 1200))
                    self.store.put('account', a.record)
                    continue
                if due > now:
                    continue
                tasks = [t for t in self.tasks.values() if t['account_id'] == a.record['id']]
                busy = any(t['id'] in self.tokens or t['status'] == 'reserved' and t.get('sale_at') and datetime.fromisoformat(t['sale_at']).timestamp() <= now+180 for t in tasks)
                if busy or not a.lock.acquire(blocking=False):
                    a.record['session_check_next'] = now + 60
                    self.store.put('account', a.record)
                    continue
            try:
                start_trace(a, self.store, 'session_maintenance')
                client = RailwayClient(make_config({}), session=a.session)
                valid = check_account_session(a, client, self.store)
                a.logged_in = valid
                a.record['session_check_status'] = 'valid' if valid else 'expired'
                session_event(a, self.store, 'maintenance_result', '定时检查：会话有效；服务端若更新 Cookie 会自动保存' if valid else '定时检查：需要重新登录，预约配置保留', preview=False)
            except (requests.RequestException, AppError):
                a.record['session_check_status'] = 'unconfirmed'
                session_event(a, self.store, 'maintenance_unconfirmed', '定时检查暂未成功，保留登录状态和凭证', preview=False)
            finally:
                a.record['session_check_at'] = time.time()
                a.record['session_check_next'] = time.time() + max(1, random.uniform(0, 1200))
                try:
                    self.store.put('account', a.record)
                    end_trace(a, self.store)
                finally:
                    a.lock.release()

    def start_scheduler(self):
        def loop():
            while not self.scheduler_stop.is_set():
                self.check_reservations()
                self.scheduler_stop.wait(1)
        threading.Thread(target=loop, daemon=True).start()
        def maintain():
            while not self.scheduler_stop.is_set():
                try:
                    self.check_sessions()
                except Exception as exc:
                    logging.error('会话定时检查失败：%s', type(exc).__name__)
                self.scheduler_stop.wait(1)
        threading.Thread(target=maintain, daemon=True).start()

    def verify_sale(self, task):
        """Persist each attempt before I/O so restarts cannot reset the three-check limit."""
        cfg = make_config(task['config'])
        now = time.time()
        midnight = sale_datetime(cfg.train_date, '0000')
        confirmed = task.setdefault('confirmed_sales', {})
        if cfg.start_at:
            release = datetime.strptime(cfg.start_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI)
            for train in cfg.preferred_trains:
                confirmed[train] = dict(sale_at=release.isoformat(), kind='manual_start', checked_at=now)
        attempts = task.get('verification_attempts', 0)
        pending = set(cfg.preferred_trains) - confirmed.keys()
        prepare_at = min((datetime.fromisoformat(v['sale_at']).timestamp()-120 for v in confirmed.values()), default=float('inf'))
        check_at = task.get('verification_next', midnight.timestamp())
        if pending and attempts < 3 and now >= check_at and (not confirmed or now < prepare_at):
            a = self.account(task['account_id'])
            if not a.lock.acquire(blocking=False):
                task['next_check'] = now + 5
                self.store.put('task', task)
                return False
            try:
                task['verification_attempts'] = attempts + 1
                task['verification_next'] = now + 1200
                self.event(task, f'第 {attempts+1}/3 次核验目标日期起售公告')
                start_trace(a, self.store, 'sale_verification')
                with (nullcontext(a.session) if a.logged_in else requests.Session()) as session:
                    client = BookingClient(cfg, session=session)
                    rows = self.query_task_routes(client, task['config'], task)
                with self.lock:
                    if self.closing or task['status'] != 'reserved':
                        return False
                    actual = [t for t in rows if t['station_train_code'].upper() in pending]
                    checks = {t['train']: t for t in task.get('train_checks', [])}
                    for row in actual:
                        train = row['station_train_code'].upper()
                        checks[train] = dict(train=train, status=clean(row.get('button_text','')), can_buy=row['can_buy'], checked_at=now)
                        release = datetime.fromtimestamp(now, SHANGHAI) if row['can_buy'] else train_sale_datetime(row.get('button_text',''), datetime.fromtimestamp(max(now, midnight.timestamp()), SHANGHAI))
                        if release is not None:
                            confirmed[train] = dict(sale_at=release.isoformat(), kind='on_sale' if row['can_buy'] else 'train_announcement', checked_at=now)
                    task['train_checks'] = list(checks.values())
                    task['train_checked_at'] = now
                    self.remember_trains(rows, cfg.train_date)
                    task['verification_failures'] = 0
            except Exception as exc:
                task.update(verification_attempts=attempts, verification_next=now+60, next_check=now+60)
                failures = task.get('verification_failures', 0) + 1
                task['verification_failures'] = failures
                if failures >= 6:
                    task['status'] = 'stopped'
                    self.event(task, f'车次状态查询失败，5次重试仍未成功，已暂停；可手动继续：{clean(exc)}')
                else:
                    self.event(task, f'车次状态查询失败，60秒后进行第 {failures}/5 次重试：{clean(exc)}')
                return False
            finally:
                try:
                    end_trace(a, self.store)
                finally:
                    a.lock.release()
        if self.closing or task['status'] != 'reserved':
            return False
        pending = set(cfg.preferred_trains) - confirmed.keys()
        task['unconfirmed_trains'] = sorted(pending)
        attempts = task.get('verification_attempts', 0)
        if confirmed:
            earliest = min(confirmed.values(), key=lambda v: v['sale_at'])
            task['sale_at'] = earliest['sale_at']
            task['sale_evidence'] = dict(earliest, trains=copy.deepcopy(task.get('train_checks', [])))
            task['verified_trains'] = list(confirmed)
            prepare_at = datetime.fromisoformat(earliest['sale_at']).timestamp()-120
            if now >= prepare_at:
                task['verification_complete'] = True
                return True
            next_check = prepare_at
        else:
            task.pop('sale_at', None)
            task.pop('sale_evidence', None)
            if attempts >= 3:
                task.update(status='unconfirmed', verification_complete=True)
                self.event(task, '3次核验均未确认起售时间，已停止；请核对12306后重新配置')
                return False
            next_check = task.get('verification_next', midnight.timestamp())
        task['verification_complete'] = not pending or attempts >= 3
        if not task['verification_complete']:
            next_check = min(next_check, task.get('verification_next', midnight.timestamp()))
        task['next_check'] = next_check
        self.event(task, ('已确认车次起售时间，等待准备抢票' if confirmed else '起售时间未确认，等待下一次核验') + (f'；未确认：{"、".join(sorted(pending))}' if pending else ''))
        return False

    def check_reservations(self):
        with self.lock:
            if self.closing:
                return
            due = [t for t in self.tasks.values() if t['status'] == 'reserved' and t.get('next_check', 0) <= time.time()]
            for task in due:
                task['next_check'] = time.time() + 300
        for task in due:
            try:
                target = date.fromisoformat(task['config']['train_date'])
                if target < today() or (task['config'].get('stop_at') and datetime.now(SHANGHAI) >= datetime.strptime(task['config']['stop_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=SHANGHAI)):
                    with self.lock:
                        if task['status'] == 'reserved':
                            task['status'] = 'exhausted'
                            self.event(task, '已到预约结束时间或乘车日期已过期，未执行订单')
                    continue
                if not self.verify_sale(task):
                    continue
                with self.lock:
                    if self.closing or task['status'] != 'reserved':
                        continue
                    task['next_check'] = time.time() + 15
                    a = self.account(task['account_id'])
                    if not a.logged_in:
                        self.event(task, '已确认起售时间，账号需登录；预约保留，登录后自动继续')
                        continue
                    if a.record['review_required']:
                        self.event(task, '账号正在使用或有待核对订单；预约等待执行')
                        continue
                    token = CancellationToken()
                    self.tokens[task['id']] = token
                    task.update(status='starting', message='预约进入开售准备')
                    self.store.put('task', task)
                    self.launch(lambda t=task, account=a, cancel=token: self._run(t, account, cancel))
            except Exception as exc:
                with self.lock:
                    if task['status'] == 'reserved':
                        self.event(task, f'预约待核验：{clean(exc)}')

    def shutdown(self):
        with self.lock:
            self.closing = True
            self.scheduler_stop.set()
            for token in self.tokens.values():
                token.cancel()
            for a in self.accounts.values():
                session_event(a, self.store, 'service_shutdown', '服务停止，保留会话和任务记录', preview=False)
                a.token.cancel()
