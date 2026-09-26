"""Account turns and per-train sale windows for the local workbench."""
from collections import deque
from datetime import datetime
import threading

from ticket_app.configuration import AppError


class AccountOrderBlocked(AppError):
    """Another task owns an outstanding or uncertain order."""


class RetryableOrderRejection(AppError):
    """A confirmed pre-queue inventory rejection, with no order confirmation sent."""


def inventory_rejection(message):
    if any(text in str(message) for text in ('未处理', '未完成', '排队', '待支付', '登录', '验证')):
        return False
    return any(text in str(message) for text in ('余票不足', '票额不足', '车票已售完', '无足够的余票'))


class AccountTurns:
    def __init__(self):
        self.waiters = deque()
        self.lock = threading.Lock()

    def acquire(self, account, token):
        waiter = object()
        with self.lock:
            self.waiters.append(waiter)
        try:
            while True:
                token.checkpoint()
                with self.lock:
                    if self.waiters[0] is waiter and account.lock.acquire(blocking=False):
                        self.waiters.popleft()
                        return
                token.wait(0.02)
        finally:
            with self.lock:
                if waiter in self.waiters:
                    self.waiters.remove(waiter)


def query_interval(task, now, normal, hot):
    times = [datetime.fromisoformat(v['sale_at']).timestamp() for v in task.get('confirmed_sales', {}).values()]
    if not times and task.get('sale_at'):
        times = [datetime.fromisoformat(task['sale_at']).timestamp()]
    if any(at-1 <= now <= at+60 for at in times):
        return hot
    upcoming = [at-1-now for at in times if at-1 > now]
    return min([normal, *upcoming])
