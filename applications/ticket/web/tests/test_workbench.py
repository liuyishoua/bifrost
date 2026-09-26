from types import SimpleNamespace

import pytest
import requests

from ticket_app.client import RailwayClient
from ticket_app.configuration import AppError, ResponseFormatError
from web.booking_options import seat_stock
from web.service import Workbench


def test_workbench_catalog_uses_observed_sleeper_codes(tmp_path):
    workbench = Workbench(tmp_path)
    try:
        parts = [""] * 36
        parts[2], parts[3] = "train-no", "D1"
        parts[6], parts[7] = "BJP", "ZZF"
        parts[16], parts[17] = "01", "02"
        parts[23], parts[28], parts[35] = "有", "2", "IJ"
        ticket = RailwayClient._parse_tickets(
            ["|".join(parts)], {"BJP": "北京西", "ZZF": "郑州东"}
        )[0]

        workbench.remember_trains([ticket], "2026-10-01")
        catalog = workbench.store.train_catalog("北京西", "郑州东")

        assert len(catalog) == 1
        assert catalog[0]["seat_types"] == ["一等卧", "二等卧"]
        assert catalog[0]["price_query"]["from_station_no"] == "01"
        assert catalog[0]["price_query"]["to_station_no"] == "02"
    finally:
        workbench.shutdown()


def test_session_probe_keeps_transient_http_failure_distinct_from_expiry(monkeypatch):
    cfg = SimpleNamespace(persist_session=False, request_timeout_seconds=1)
    client = RailwayClient(cfg, session=requests.Session())
    monkeypatch.setattr(
        client.session,
        "post",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=503),
    )
    with pytest.raises(AppError, match="暂时无法确认"):
        client.check_session()


@pytest.mark.parametrize("codes,label,expected", [
    ("I", "一等卧", "有"),
    ("I", "软卧", "--"),
    ("4I", "一等卧", "--"),
    ("4I", "软卧", "--"),
])
def test_workbench_shows_only_unambiguous_shared_berth_stock(codes, label, expected):
    ticket = {"seat_types": codes, "seats": {"rw": "有"}}
    assert seat_stock(ticket, label) == expected


def test_order_redirect_is_rejected_before_json_parsing():
    response = SimpleNamespace(
        status_code=302,
        headers={"Location": "https://example.invalid/login"},
        json=lambda: pytest.fail("redirect body must not be parsed as an order response"),
    )
    with pytest.raises(ResponseFormatError, match="其他站点"):
        RailwayClient._response_json_object(response, "submitOrderRequest")


def test_strict_ticket_query_does_not_report_malformed_response_as_no_tickets(monkeypatch):
    cfg = SimpleNamespace(
        persist_session=False,
        request_timeout_seconds=1,
        train_date="2026-10-01",
        purpose_codes="ADULT",
    )
    client = RailwayClient(cfg, session=requests.Session())
    monkeypatch.setattr(
        client.session,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(json=lambda: {"status": True, "data": {}}),
    )
    with pytest.raises(AppError, match="不能据此判断无票"):
        client.query_tickets("BJP", "ZZF", raise_on_error=True)
