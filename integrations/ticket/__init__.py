"""Read-only compatibility adapter for the Ticket workbench."""
from ..base import IntegrationError, read_json


class TicketAdapter:
    def ready(self, app):
        state = read_json(app, app.ready_path)
        return isinstance(state.get("tasks"), list) and isinstance(state.get("accounts"), list)

    def ensure_idle(self, app):
        state = read_json(app, "/api/state")
        try:
            tasks, accounts = state["tasks"], state["accounts"]
            if any(t.get("status") in ("starting", "waiting", "querying", "submitting", "queueing", "stopping", "reserved") for t in tasks):
                raise IntegrationError("票务任务正在执行或预约")
            if any(a.get("busy") for a in accounts):
                raise IntegrationError("票务账号有在途操作")
            if any(a.get("login", {}).get("status") in ("starting", "waiting", "scanned", "verifying") for a in accounts):
                raise IntegrationError("票务账号正在扫码登录")
        except (TypeError, KeyError, AttributeError) as exc:
            raise IntegrationError("票务活动状态格式错误") from exc
