#!/usr/bin/env python3
"""Minimal local test page for 12306FairTicket.

Run this script and open http://127.0.0.1:8765 to exercise:
- 登录状态检查（扫码登录）
- 查票查询
- 下单预检（提交订单 -> initDc -> checkOrderInfo）
- 真实下单（提交订单->排队）

All operations reuse the same requests session and cookie file so you can test
the "scan once, then reuse" flow directly.
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import date, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

import config as app_config_module
from ticket_app.configuration import AppConfig, AppError, SEAT_SPECS
from ticket_app.runner import TicketRunner
from ticket_app.runtime import CancellationToken, RuntimeEvent


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.py"
_BASE_CONFIG: dict[str, Any] = dict(vars(app_config_module))
try:
    DEFAULT_CONFIG = AppConfig.from_module(app_config_module, DEFAULT_CONFIG_PATH)
except AppError:
    _BASE_CONFIG["TRAIN_DATE"] = (date.today() + timedelta(days=1)).isoformat()
    DEFAULT_CONFIG = AppConfig.from_mapping(_BASE_CONFIG, DEFAULT_CONFIG_PATH)
RUNTIME_DIR = BASE_DIR / ".runtime_simple_page"


def _safe_date(days_ahead: int = 1) -> str:
    return (date.today() + timedelta(days=days_ahead)).isoformat()


def _parse_names(raw: str) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]


def _parse_json(body: bytes) -> Dict[str, Any]:
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def _event_payload(events: Sequence[RuntimeEvent]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for event in events:
        data = {}
        for key, value in event.data.items():
            if isinstance(value, bytes):
                data[key] = f"<bytes:{len(value)}>"
            elif isinstance(value, Path):
                data[key] = str(value)
            else:
                data[key] = value
        result.append({"kind": event.kind, "message": event.message, "data": data, "ts": event.timestamp})
    return result


def _to_runtime_cfg(
    *,
    auto_submit: bool,
    from_station: str,
    to_station: str,
    train_date: str,
    seat_types: Sequence[str],
    passenger_names: Sequence[str],
) -> AppConfig:
    base = DEFAULT_CONFIG.to_mapping()
    base.update(
        {
            "FROM_STATION": from_station,
            "TO_STATION": to_station,
            "TRAIN_DATE": train_date,
            "SEAT_TYPES": list(seat_types),
            "PASSENGER_NAMES": list(passenger_names),
            "AUTO_SUBMIT": auto_submit,
            "PREFERRED_TRAINS": [],
            "ONLY_PREFERRED_TRAINS": False,
            "PERSIST_SESSION": True,
            "SESSION_FILE": str((RUNTIME_DIR / "session.cookies").resolve()),
            "STATION_CACHE_FILE": str((RUNTIME_DIR / "stations.json").resolve()),
            "QR_CODE_FILE": str((RUNTIME_DIR / "login_qr.png").resolve()),
        }
    )
    return AppConfig.from_mapping(base, DEFAULT_CONFIG_PATH)



def _query_results(runner: TicketRunner, from_station: str, to_station: str) -> Dict[str, Any]:
    from_code = runner.stations.code(from_station)
    to_code = runner.stations.code(to_station)
    tickets = runner.client.query_tickets(from_code, to_code, raise_on_error=True)
    candidates = runner._find_candidates(tickets)
    compact: List[Dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:200]):
        ticket = candidate["ticket"]
        compact.append(
            {
                "index": index,
                "train_code": ticket["station_train_code"],
                "seat_label": candidate["seat_label"],
                "seat_type": candidate["seat_type"],
                "stock": candidate["stock"],
                "start_time": ticket["start_time"],
                "arrive_time": ticket["arrive_time"],
                "duration": ticket["duration"],
                "from_station": ticket["from_station"],
                "to_station": ticket["to_station"],
                "date": ticket["date"],
            }
        )
    trains = []
    for ticket in tickets:
        trains.append({
            "train_code": ticket["station_train_code"],
            "from_station": ticket["from_station"],
            "to_station": ticket["to_station"],
            "start_time": ticket["start_time"],
            "arrive_time": ticket["arrive_time"],
            "duration": ticket["duration"],
            "status": "可预订" if ticket["can_buy"] else (ticket["button_text"] or "暂不可预订"),
            "seats": {label: ticket["seats"].get(spec.stock_key, "--") for label, spec in SEAT_SPECS.items()},
        })
    return {"trains": trains, "count": len(trains), "candidates": compact}


def _serialize_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


@dataclass
class SharedRuntime:
    session: requests.Session
    lock: threading.Lock


RUNTIME = SharedRuntime(session=requests.Session(), lock=threading.Lock())


@dataclass
class LoginState:
    lock: threading.Lock
    active: bool = False
    logged_in: bool = False
    status: str = "idle"
    message: str = "尚未登录"
    qr_base64: str | None = None
    events: List[RuntimeEvent] | None = None


LOGIN = LoginState(lock=threading.Lock(), events=[])


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
  <meta charset="utf-8" />
  <title>12306FairTicket 简易测试页</title>
  <style>
    body { font-family: Arial, "Microsoft YaHei", sans-serif; max-width: 980px; margin: 24px auto; padding: 16px; }
    h1 { margin-top: 0; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
    .group { border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
    label { display: block; margin-bottom: 6px; font-weight: bold; }
    input, textarea, button, select { padding: 8px; }
    textarea { width: 100%; min-height: 180px; }
    .inline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    img { max-width: 140px; border: 1px solid #ddd; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    th, td { border: 1px solid #ddd; padding: 6px; font-size: 13px; text-align: left; }
    th { background: #f7f7f7; }
    .danger { background: #ffe8e8; border: 1px solid #f6bcbc; padding: 8px; border-radius: 6px; }
  </style>
  <h1>12306FairTicket 简易页面（脚本能力）</h1>
  <div class="group">
    <div class="row">
      <div>
        <label>出发站</label><input id="from_station" value="北京南" />
      </div>
      <div>
        <label>到达站</label><input id="to_station" value="上海虹桥" />
      </div>
      <div>
        <label>乘车日期</label><input id="train_date" type="date" />
      </div>
    </div>
    <div class="row">
      <div>
        <label>可选座席（逗号分隔）</label><input id="seat_types" value="二等座,一等座" />
      </div>
      <div>
        <label>乘车人（逗号分隔）</label><input id="passenger_names" value="张三" />
      </div>
      <div>
        <label>请求超时秒</label><input id="request_timeout" type="number" step="0.1" value="10" />
      </div>
    </div>
    <div class="row inline">
      <button onclick="loginNow()">登录/检查登录</button>
      <button onclick="queryNow()">查票</button>
      <button onclick="statusNow()">仅检查会话</button>
      <button onclick="orderNow(false)">订单预检（不提交排队）</button>
      <button onclick="orderNow(true)" class="danger">真实下单（提交排队）</button>
      <span id="qrWrap">二维码：<img id="qrImage" src="" alt="扫码二维码" /></span>
    </div>
    <div>提示：真实下单会触发提交排队请求，请确认你要这么测再点。</div>
  </div>

  <div class="group">
    <label>全部车次及各席别余票</label>
    <div id="querySummary">填写路线和日期后点击查票</div>
    <div style="overflow-x:auto"><table id="trains"></table></div>
    <div>有 / 数字：有票；无 / 0：无票；--：不提供或未返回；*：状态未知。可否预订以车次状态为准。</div>
  </div>

  <div class="group">
    <label>候选车次</label>
    <select id="candidate"></select>
    <div>候选索引：<span id="candCount">0</span></div>
  </div>

  <div class="group">
    <label>日志 / 返回结果</label>
    <textarea id="logArea"></textarea>
    <div id="events"></div>
  </div>

  <script>
    const state = { candidates: [] };
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth()+1).padStart(2, '0');
    const dd = String(today.getDate()+1).padStart(2, '0');
    document.getElementById('train_date').value = `${yyyy}-${mm}-${dd}`;

    function collect() {
      return {
        from_station: document.getElementById('from_station').value,
        to_station: document.getElementById('to_station').value,
        train_date: document.getElementById('train_date').value,
        seat_types: document.getElementById('seat_types').value,
        passenger_names: document.getElementById('passenger_names').value,
        request_timeout_seconds: Number(document.getElementById('request_timeout').value || 10),
        candidate_index: Number(document.getElementById('candidate').value || 0)
      };
    }

    async function callApi(path, data) {
      const r = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
      });
      const text = await r.text();
      try { return JSON.parse(text); } catch (_) { return { ok: false, message: text }; }
    }

    function appendLog(payload) {
      document.getElementById('logArea').value = JSON.stringify(payload, null, 2);
      if (Array.isArray(payload.events)) {
        const list = payload.events.map(item => `${item.kind}: ${item.message}`).join('\n');
        document.getElementById('events').textContent = list;
      } else {
        document.getElementById('events').textContent = '';
      }
      if (payload.qr_base64) {
        document.getElementById('qrImage').src = `data:image/png;base64,${payload.qr_base64}`;
      }
    }

    function fillCandidates(items) {
      const select = document.getElementById('candidate');
      state.candidates = items || [];
      select.innerHTML = '';
      for (const item of state.candidates) {
        const option = document.createElement('option');
        option.value = String(item.index);
        option.textContent = `${item.index} - ${item.train_code} ${item.seat_label} ${item.stock} 库位 ${item.start_time}-${item.arrive_time}`;
        select.appendChild(option);
      }
      if (!state.candidates.length) {
        const option = document.createElement('option');
        option.value = '0';
        option.textContent = '无候选';
        select.appendChild(option);
      }
      document.getElementById('candCount').textContent = String(state.candidates.length);
    }

    async function loginNow() {
      const body = collect();
      const data = await callApi('/api/login', body);
      appendLog(data);
      if (data.active) pollLogin(body);
    }

    async function pollLogin(body) {
      const data = await callApi('/api/login', body);
      appendLog(data);
      if (data.active) setTimeout(() => pollLogin(body), 1000);
    }

    async function statusNow() {
      const body = collect();
      const data = await callApi('/api/status', body);
      appendLog(data);
    }

    async function queryNow() {
      const body = collect();
      const summary = document.getElementById('querySummary');
      const table = document.getElementById('trains');
      table.replaceChildren();
      fillCandidates([]);
      summary.textContent = '正在查询…';
      try {
        const data = await callApi('/api/query', body);
        appendLog(data);
        fillCandidates(data.candidates || []);
        if (!data.ok) { summary.textContent = data.message || '查询失败'; return; }
        summary.textContent = `${body.from_station} → ${body.to_station} ${body.train_date}，共 ${data.count} 趟车次`;
        if (!data.count) { summary.textContent += '（接口返回空列表）'; return; }
        const labels = Object.keys(data.trains[0].seats);
        const header = table.insertRow();
        for (const label of ['车次', '区间', '出发 / 到达', '历时', '预订状态', ...labels]) {
          const th = document.createElement('th'); th.textContent = label; header.appendChild(th);
        }
        for (const train of data.trains) {
          const row = table.insertRow();
          for (const value of [train.train_code, `${train.from_station} → ${train.to_station}`, `${train.start_time} / ${train.arrive_time}`, train.duration, train.status]) {
            row.insertCell().textContent = value;
          }
          for (const label of labels) {
            const stock = train.seats[label];
            const cell = row.insertCell();
            const available = stock === '有' || /^\d+$/.test(stock) && Number(stock) > 0;
            cell.textContent = available ? (stock === '有' ? '有票' : `${stock} 张`) : ['无', '0'].includes(stock) ? '无票' : stock;
            cell.style.color = available ? '#08783f' : '#777';
            if (available) cell.style.fontWeight = 'bold';
          }
        }
      } catch (error) { summary.textContent = `查询失败：${error.message}`; }
    }

    async function orderNow(realSubmit) {
      const body = collect();
      const selected = state.candidates[body.candidate_index];
      if (!selected) { appendLog({ok:false, message:'请先查票并选择有票的车次及席别'}); return; }
      body.train_code = selected.train_code;
      body.seat_label = selected.seat_label;
      body.real_submit = !!realSubmit;
      const data = await callApi('/api/order', body);
      appendLog(data);
    }
  </script>
</html>
"""


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "Simple12306Page/1.0"

    def _write_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bad_request(self, message: str) -> None:
        self._write_json({"ok": False, "message": message}, HTTPStatus.BAD_REQUEST)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = HTML_PAGE.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        raw_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(raw_length) if raw_length > 0 else b""
        payload = _parse_json(raw_body)

        try:
            response = self._handle_api(payload)
            self._write_json(response)
        except Exception as exc:  # pragma: no cover - response layer fallback
            self._write_json({"ok": False, "message": _serialize_error(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_api(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/status":
            return self._handle_status(payload)
        if path == "/api/login":
            return self._handle_login(payload)
        if path == "/api/query":
            return self._handle_query(payload)
        if path == "/api/order":
            return self._handle_order(payload)
        return {"ok": False, "message": f"unknown path: {path}"}

    def _handle_status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            cfg = _to_runtime_cfg(
                auto_submit=False,
                from_station=payload.get("from_station", DEFAULT_CONFIG.from_station),
                to_station=payload.get("to_station", DEFAULT_CONFIG.to_station),
                train_date=payload.get("train_date", _safe_date()),
                seat_types=_parse_names(payload.get("seat_types", "") ) or list(DEFAULT_CONFIG.seat_types),
                passenger_names=[],
            )
            cfg.request_timeout_seconds = float(payload.get("request_timeout_seconds", DEFAULT_CONFIG.request_timeout_seconds))
            events: List[RuntimeEvent] = []
            runner = TicketRunner(cfg, event_sink=events.append, session=RUNTIME.session)
            valid = runner.client.check_session()
            return {"ok": True, "logged_in": bool(valid), "events": _event_payload(events)}
        except Exception as exc:
            return {"ok": False, "message": _serialize_error(exc)}

    def _handle_login(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with LOGIN.lock:
            if LOGIN.active:
                return self._login_snapshot()
            if RUNTIME.session.cookies and self._session_valid(payload):
                LOGIN.logged_in = True
                LOGIN.status = "logged_in"
                LOGIN.message = "当前登录会话仍然有效，无需重新扫码"
                return self._login_snapshot()
            LOGIN.active = True
            LOGIN.logged_in = False
            LOGIN.status = "starting"
            LOGIN.message = "正在生成二维码"
            LOGIN.qr_base64 = None
            LOGIN.events = []
            threading.Thread(target=self._run_login, args=(payload,), daemon=True).start()
            return self._login_snapshot()

    def _session_valid(self, payload: Dict[str, Any]) -> bool:
        cfg = _to_runtime_cfg(auto_submit=False, from_station=payload.get("from_station", DEFAULT_CONFIG.from_station), to_station=payload.get("to_station", DEFAULT_CONFIG.to_station), train_date=payload.get("train_date", _safe_date()), seat_types=["二等座"], passenger_names=[])
        cfg.request_timeout_seconds = float(payload.get("request_timeout_seconds", DEFAULT_CONFIG.request_timeout_seconds))
        return TicketRunner(cfg, session=RUNTIME.session).client.check_session()

    def _run_login(self, payload: Dict[str, Any]) -> None:
        def record(event: RuntimeEvent) -> None:
            with LOGIN.lock:
                assert LOGIN.events is not None
                LOGIN.events.append(event)
                LOGIN.message = event.message
                if event.kind == "qr_status":
                    LOGIN.status = event.data.get("status", LOGIN.status)
                if event.kind == "qr_ready":
                    image = event.data.get("image_bytes")
                    if isinstance(image, (bytes, bytearray)):
                        LOGIN.qr_base64 = base64.b64encode(image).decode("ascii")

        try:
            cfg = _to_runtime_cfg(auto_submit=False, from_station=payload.get("from_station", DEFAULT_CONFIG.from_station), to_station=payload.get("to_station", DEFAULT_CONFIG.to_station), train_date=payload.get("train_date", _safe_date()), seat_types=_parse_names(payload.get("seat_types", "")) or list(DEFAULT_CONFIG.seat_types), passenger_names=[])
            cfg.request_timeout_seconds = float(payload.get("request_timeout_seconds", DEFAULT_CONFIG.request_timeout_seconds))
            with RUNTIME.lock:
                runner = TicketRunner(cfg, event_sink=record, session=RUNTIME.session)
                runner.client.ensure_login()
                logged_in = runner.client.check_session()
            with LOGIN.lock:
                LOGIN.logged_in = bool(logged_in)
                LOGIN.status = "confirmed" if logged_in else "failed"
                LOGIN.message = "登录成功" if logged_in else "登录校验失败"
        except Exception as exc:
            with LOGIN.lock:
                LOGIN.status = "failed"
                LOGIN.message = _serialize_error(exc)
        finally:
            with LOGIN.lock:
                LOGIN.active = False

    def _login_snapshot(self) -> Dict[str, Any]:
        events = LOGIN.events or []
        return {"ok": LOGIN.status != "failed", "active": LOGIN.active, "logged_in": LOGIN.logged_in, "qr_status": LOGIN.status, "qr_message": LOGIN.message, "qr_base64": LOGIN.qr_base64, "events": _event_payload(events)}

    def _handle_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        events: List[RuntimeEvent] = []
        with RUNTIME.lock:
            try:
                cfg = _to_runtime_cfg(
                    auto_submit=False,
                    from_station=payload.get("from_station", DEFAULT_CONFIG.from_station),
                    to_station=payload.get("to_station", DEFAULT_CONFIG.to_station),
                    train_date=payload.get("train_date", _safe_date()),
                    seat_types=_parse_names(payload.get("seat_types", "") ) or ["二等座"],
                    passenger_names=[],
                )
                cfg.request_timeout_seconds = float(payload.get("request_timeout_seconds", DEFAULT_CONFIG.request_timeout_seconds))
                runner = TicketRunner(cfg, event_sink=events.append, session=RUNTIME.session)

                if not runner.client.check_session():
                    return {
                        "ok": False,
                        "message": "当前未登录或会话已失效，请先点‘登录/检查登录’",
                        "events": _event_payload(events),
                    }

                runner.stations.load(CancellationToken(), events.append)
                results = _query_results(
                    runner,
                    payload.get("from_station", DEFAULT_CONFIG.from_station),
                    payload.get("to_station", DEFAULT_CONFIG.to_station),
                )
                return {
                    "ok": True,
                    **results,
                    "events": _event_payload(events),
                }
            except Exception as exc:
                return {"ok": False, "message": _serialize_error(exc), "events": _event_payload(events)}

    def _handle_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        events: List[RuntimeEvent] = []
        with RUNTIME.lock:
            try:
                from_station = payload.get("from_station", DEFAULT_CONFIG.from_station)
                to_station = payload.get("to_station", DEFAULT_CONFIG.to_station)
                train_date = payload.get("train_date", _safe_date())
                seat_types = _parse_names(payload.get("seat_types", "")) or ["二等座"]
                passenger_names = _parse_names(payload.get("passenger_names", ""))
                real_submit = bool(payload.get("real_submit", False))

                cfg = _to_runtime_cfg(
                    auto_submit=True,
                    from_station=from_station,
                    to_station=to_station,
                    train_date=train_date,
                    seat_types=seat_types,
                    passenger_names=passenger_names,
                )
                cfg.request_timeout_seconds = float(payload.get("request_timeout_seconds", DEFAULT_CONFIG.request_timeout_seconds))
                runner = TicketRunner(cfg, event_sink=events.append, session=RUNTIME.session)

                if not runner.client.check_session():
                    return {
                        "ok": False,
                        "message": "未登录，先点‘登录/检查登录’",
                        "events": _event_payload(events),
                    }

                runner.stations.load(CancellationToken(), events.append)
                candidates = runner._find_candidates(
                    runner.client.query_tickets(
                        runner.stations.code(from_station),
                        runner.stations.code(to_station),
                        raise_on_error=True,
                    )
                )
                if not candidates:
                    return {"ok": False, "message": "当前无候选，先查一次票再试", "events": _event_payload(events)}

                candidate = next((item for item in candidates
                    if item["ticket"]["station_train_code"] == payload.get("train_code")
                    and item["seat_label"] == payload.get("seat_label")), None)
                if candidate is None:
                    return {"ok": False, "message": "所选车次席别已无票或未选择，请重新查票", "events": _event_payload(events)}

                if not passenger_names:
                    return {"ok": False, "message": "订单接口需要乘车人，填写 passenger_names", "events": _event_payload(events)}

                passengers = runner._select_passengers()
                prepared = runner._prepare_passengers_by_seat_code(passengers)

                if not real_submit:
                    ticket = candidate["ticket"]
                    seat_type = candidate["seat_type"]
                    ok, message = runner.client.submit_order_request(ticket)
                    if not ok:
                        return {"ok": False, "message": f"submit_order_request 未通过: {message}", "events": _event_payload(events)}

                    token, info = runner.client.init_dc()
                    check_result = runner.client.check_order_info(prepared[seat_type], token)
                    return {
                        "ok": bool(check_result.success),
                        "message": check_result.message,
                        "stage": "dry_run",
                        "train_code": ticket["station_train_code"],
                        "seat_label": candidate["seat_label"],
                        "capabilities": {
                            "can_choose_seats": check_result.capabilities.can_choose_seats,
                            "can_choose_beds": check_result.capabilities.can_choose_beds,
                            "allowed_seat_types": sorted(check_result.capabilities.allowed_seat_types),
                        },
                        "events": _event_payload(events),
                    }

                success = runner._book_ticket(candidate, prepared)
                return {
                    "ok": bool(success),
                    "message": "订单提交流程执行完成" if success else "订单流程失败",
                    "stage": "real_submit",
                    "events": _event_payload(events),
                }
            except Exception as exc:
                return {"ok": False, "message": _serialize_error(exc), "events": _event_payload(events)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Start a minimal page for 12306 query/order checks.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"打开页面: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
