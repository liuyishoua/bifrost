"""Conservative local service lifecycle. Unknown ownership or work always fails closed."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time

import psutil

from apps import APPS
from integrations import adapter_for
from integrations.base import IntegrationError


class ServiceError(Exception):
    pass


class Controller:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.locks = {key: threading.Lock() for key in APPS}
        self.transitioning = set()

    def _listening(self, app):
        with socket.socket() as sock:
            sock.settimeout(.2)
            return sock.connect_ex(("127.0.0.1", app.port)) == 0

    def _owner(self, app):
        matches = []
        try:
            processes = list(psutil.process_iter(["pid", "cmdline", "cwd"]))
        except (psutil.Error, OSError):
            return None
        for proc in processes:
            try:
                if (tuple(proc.info["cmdline"] or ()) == app.command and
                        Path(proc.info["cwd"] or "").resolve() == app.repository.resolve()):
                    matches.append(proc)
            except (psutil.Error, OSError):
                continue
        if len(matches) != 1:
            return None
        proc = matches[0]
        try:
            if any(c.status == psutil.CONN_LISTEN and c.laddr.port == app.port and
                   c.laddr.ip in ("127.0.0.1", "::1") for c in proc.net_connections(kind="tcp")):
                return proc
        except (psutil.Error, OSError):
            pass
        return None

    def _configured_processes(self, app):
        found = []
        try:
            processes = list(psutil.process_iter(["cmdline", "cwd"]))
        except (psutil.Error, OSError) as exc:
            raise ServiceError("无法检查本机进程") from exc
        for proc in processes:
            try:
                if (tuple(proc.info["cmdline"] or ()) == app.command and
                        Path(proc.info["cwd"] or "").resolve() == app.repository.resolve()):
                    found.append(proc)
            except (psutil.Error, OSError):
                continue
        return found

    def _ready(self, app):
        try:
            return adapter_for(app.id).ready(app)
        except IntegrationError:
            return False

    def status(self, app_id):
        app = APPS[app_id]
        if app_id in self.transitioning:
            return "starting", "服务正在启停"
        if not self._listening(app):
            try:
                if self._configured_processes(app):
                    return "unavailable", "配置进程存在但端口未就绪"
            except ServiceError as exc:
                return "unavailable", str(exc)
            return "stopped", ""
        if not self._owner(app):
            return "unavailable", "端口由无法确认的进程占用"
        if not self._ready(app):
            return "unavailable", "服务未通过就绪检查"
        return "running", ""

    def _idle(self, app):
        try:
            adapter_for(app.id).ensure_idle(app)
        except IntegrationError as exc:
            raise ServiceError(str(exc)) from exc

    def operate(self, app_id, action):
        if action not in ("start", "stop"):
            raise ServiceError("未知操作")
        lock = self.locks[app_id]
        if not lock.acquire(blocking=False):
            raise ServiceError("服务正在启停")
        app = APPS[app_id]
        self.transitioning.add(app_id)
        try:
            # Read state directly while transition marker is set.
            listening = self._listening(app)
            owner = self._owner(app) if listening else None
            if action == "start":
                if listening or self._configured_processes(app):
                    raise ServiceError("端口或配置进程已存在，不能重复启动")
                if not app.repository.is_dir() or not Path(app.command[0]).is_file():
                    raise ServiceError("业务源码目录或可执行文件不存在")
                self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
                log = open(self.runtime / f"{app_id}.log", "ab", buffering=0)
                try:
                    proc = subprocess.Popen(app.command, cwd=app.repository, stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                            env={**os.environ, "ANYDOOR_PUBLIC_ORIGIN": os.environ.get("BIFROST_PUBLIC_ORIGIN") or os.environ.get("ANYDOOR_PUBLIC_ORIGIN", "http://127.0.0.1:8080"),
                                                 "ANYDOOR_VERIFY_ORIGIN": os.environ.get("BIFROST_VERIFY_ORIGIN") or os.environ.get("ANYDOOR_VERIFY_ORIGIN", "http://127.0.0.1:8081")})
                finally:
                    log.close()
                for _ in range(50):
                    if proc.poll() is not None:
                        raise ServiceError("进程提前退出，请查看业务日志")
                    if self._listening(app) and self._owner(app) and self._ready(app):
                        return "running"
                    time.sleep(.2)
                raise ServiceError("启动后未通过进程、端口及就绪检查")
            if not listening:
                raise ServiceError("服务未运行")
            if not owner or not self._ready(app):
                raise ServiceError("进程归属或就绪状态不明确，拒绝关闭")
            try:
                self._idle(app)
            except (OSError, ValueError, KeyError) as exc:
                raise ServiceError("活动状态读取失败，拒绝关闭") from exc
            owner.send_signal(signal.SIGTERM)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                listening = self._listening(app)
                try:
                    exited = not owner.is_running() or owner.status() == psutil.STATUS_ZOMBIE
                except psutil.NoSuchProcess:
                    exited = True
                except psutil.Error as exc:
                    raise ServiceError("无法确认进程已退出") from exc
                if exited and not listening:
                    try:
                        owner.wait(timeout=0)
                    except (psutil.Error, OSError):
                        pass
                    return "stopped"
                time.sleep(.2)
            raise ServiceError("服务未正常退出或端口仍被占用")
        finally:
            self.transitioning.discard(app_id)
            lock.release()
