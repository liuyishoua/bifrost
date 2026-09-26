from datetime import date, datetime

import pytest

from ticket_app.configuration import AppError
from web.reservations import SHANGHAI, cadence_delay, query_sale_time, reference_date, sale_datetime


def test_reference_date_preserves_weekday_and_sale_day():
    current = date(2026, 9, 27)
    target = date(2026, 11, 3)
    reference = reference_date(target.isoformat(), current)
    assert reference > current
    assert reference.weekday() == target.weekday()
    assert sale_datetime(target.isoformat(), "0800") == datetime(2026, 10, 20, 8, tzinfo=SHANGHAI)


def test_sale_time_requires_unambiguous_official_station_record():
    class Session:
        def post(self, *_args, **_kwargs):
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"status": True, "data": [
                {"station_name": "北京西", "start_date": "20260101", "stop_date": "20261231", "sale_time": "0800"},
                {"station_name": "北京南", "start_date": "20260101", "stop_date": "20261231", "sale_time": "1230"},
            ]}

    assert query_sale_time(Session(), "北京西", date(2026, 10, 1)) == "0800"
    with pytest.raises(AppError, match="缺失或冲突"):
        query_sale_time(Session(), "上海虹桥", date(2026, 10, 1))


def test_query_cadence_does_not_schedule_catch_up_requests():
    assert cadence_delay(0.2, 100, 100.08) == pytest.approx(0.12)
    assert cadence_delay(0.2, 100, 100.4) == 0
