"""Stable integration protocol for services written in any language.

New services should implement the two loopback-only JSON endpoints below. Existing
services can instead supply a small Python adapter which translates their status.
"""
from typing import Protocol
from urllib.request import urlopen
import json


class IntegrationError(Exception):
    pass


class Adapter(Protocol):
    def ready(self, app) -> bool:
        """True only after the service itself answers and reports readiness."""
        ...

    def ensure_idle(self, app) -> None:
        """Raise IntegrationError if work is active or state cannot be read."""
        ...


def read_json(app, path, timeout=3):
    try:
        with urlopen(f"http://127.0.0.1:{app.port}{path}", timeout=timeout) as response:
            if response.status != 200:
                raise IntegrationError("服务状态接口未返回 200")
            data = json.load(response)
        if not isinstance(data, dict):
            raise IntegrationError("服务状态响应格式错误")
        return data
    except (OSError, TimeoutError, ValueError) as exc:
        raise IntegrationError("服务状态不可读") from exc


class HttpContractAdapter:
    """Recommended v1 protocol for future applications, regardless of language.

    GET /.well-known/bifrost/ready -> {"ready": true}
    GET /.well-known/bifrost/activity -> {"idle": true, "reason": ""}
    Both must be loopback-only, read-only, JSON, and reflect live work.
    """

    def ready(self, app):
        return read_json(app, "/.well-known/bifrost/ready").get("ready") is True

    def ensure_idle(self, app):
        state = read_json(app, "/.well-known/bifrost/activity")
        if state.get("idle") is not True:
            raise IntegrationError(str(state.get("reason") or "服务存在在途工作"))
