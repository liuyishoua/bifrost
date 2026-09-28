"""Conservative local service lifecycle. Unknown ownership or work always fails closed."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
from urllib.request import urlopen

import psutil


class ServiceError(Exception):
    pass


def read_json(app, path, timeout=3):
    try:
        with urlopen(f"http://127.0.0.1:{app.port}{path}", timeout=timeout) as response:
            if response.status != 200:
                raise ServiceError("服务状态接口未返回 200")
            data = json.load(response)
        if not isinstance(data, dict):
            raise ServiceError("服务状态响应格式错误")
        return data
    except (OSError, TimeoutError, ValueError) as exc:
        raise ServiceError("服务状态不可读") from exc


class Controller:
    def __init__(self, runtime, registry):
        self.runtime = Path(runtime)
        self.registry = registry
        self.locks = {}
        self.transitioning = set()
        self.children = {}

    def _app(self, app_id):
        app = self.registry.get(app_id)
        if app is None:
            raise ServiceError("未知应用")
        return app

    def _command(self, app):
        return tuple(part.replace("${PORT}", str(app.port)).replace("${DATA_DIR}", str(app.data_dir))
                     for part in app.command)

    def _build(self, app):
        if not app.build:
            return
        (app.repository / ".bifrost" / "bin").mkdir(parents=True, exist_ok=True)
        command = tuple(part.replace("${PORT}", str(app.port)).replace("${DATA_DIR}", str(app.data_dir))
                        for part in app.build)
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with open(self.runtime / f"{app.id}.build.log", "wb") as log:
                result = subprocess.run(command, cwd=app.repository, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ServiceError("构建失败，请查看构建日志") from exc
        if result.returncode:
            raise ServiceError("构建失败，请查看构建日志")

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
                if (tuple(proc.info["cmdline"] or ()) == self._command(app) and
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
                if (tuple(proc.info["cmdline"] or ()) == self._command(app) and
                        Path(proc.info["cwd"] or "").resolve() == app.repository.resolve()):
                    found.append(proc)
            except (psutil.Error, OSError):
                continue
        return found

    def _ready(self, app):
        if not app.ready_path:
            return True
        try:
            return read_json(app, app.ready_path).get("ready") is True
        except ServiceError:
            return False

    def status(self, app_id):
        app = self._app(app_id)
        if app_id in self.transitioning:
            return "starting", "服务正在启停"
        if not self._listening(app):
            try:
                if self._configured_processes(app):
                    return "unavailable", "配置进程存在但端口未就绪"
            except ServiceError as exc:
                return "unavailable", str(exc)
            if app_id in self.registry.pinned:
                lock = self.locks.setdefault(app_id, threading.Lock())
                if not lock.acquire(blocking=False):
                    return "starting", "服务正在启停"
                try:
                    if not self._listening(app) and not self._configured_processes(app):
                        self.registry.unpin(app_id)
                except ServiceError as exc:
                    return "unavailable", str(exc)
                finally:
                    lock.release()
            return "stopped", ""
        if not self._owner(app):
            return "unavailable", "端口由无法确认的进程占用"
        if not self._ready(app):
            return "unavailable", "服务未通过就绪检查"
        return "running", ""

    def _idle(self, app):
        if app.activity_path:
            state = read_json(app, app.activity_path)
            if state.get("idle") is not True:
                raise ServiceError(str(state.get("reason") or "服务存在在途工作"))

    def operate(self, app_id, action):
        if action not in ("start", "stop"):
            raise ServiceError("未知操作")
        app = self._app(app_id)
        lock = self.locks.setdefault(app_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ServiceError("服务正在启停")
        self.transitioning.add(app_id)
        try:
            # Read state directly while transition marker is set.
            listening = self._listening(app)
            owner = self._owner(app) if listening else None
            if action == "start":
                if listening or self._configured_processes(app):
                    raise ServiceError("端口或配置进程已存在，不能重复启动")
                pinned = True
                spawned = False
                if pinned:
                    self.registry.pin(app_id, app)
                try:
                    self._build(app)
                    if not app.repository.is_dir() or not Path(self._command(app)[0]).is_file():
                        raise ServiceError("业务源码目录或可执行文件不存在")
                    if self._listening(app) or self._configured_processes(app):
                        raise ServiceError("端口或配置进程已存在，不能重复启动")
                    if pinned:
                        app.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                    self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with open(self.runtime / f"{app_id}.log", "ab", buffering=0) as log:
                        try:
                            proc = subprocess.Popen(self._command(app), cwd=app.repository, stdin=subprocess.DEVNULL,
                                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                                    env={**os.environ, "ANYDOOR_PUBLIC_ORIGIN": os.environ.get("BIFROST_PUBLIC_ORIGIN") or os.environ.get("ANYDOOR_PUBLIC_ORIGIN", "http://127.0.0.1:8080"),
                                                         "ANYDOOR_VERIFY_ORIGIN": os.environ.get("BIFROST_VERIFY_ORIGIN") or os.environ.get("ANYDOOR_VERIFY_ORIGIN", "http://127.0.0.1:8081"),
                                                         "BIFROST_APP_ORIGIN": app.origin})
                        except OSError as exc:
                            raise ServiceError("启动失败，请查看业务日志") from exc
                    spawned = True
                    self.children[app_id] = proc
                except (ServiceError, OSError):
                    if pinned and not spawned:
                        self.registry.unpin(app_id)
                    raise
                for _ in range(50):
                    if proc.poll() is not None:
                        proc.wait()
                        self.children.pop(app_id, None)
                        if pinned:
                            self.registry.unpin(app_id)
                        raise ServiceError("进程提前退出，请查看业务日志")
                    if self._listening(app) and self._owner(app) and self._ready(app):
                        return "running"
                    time.sleep(.2)
                raise ServiceError("启动后未通过进程、端口及就绪检查")
            if not listening:
                if app_id in self.registry.pinned:
                    if self._configured_processes(app):
                        raise ServiceError("配置进程存在但端口未就绪")
                    self.registry.unpin(app_id)
                    return "stopped"
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
                    child = self.children.pop(app_id, None)
                    if child:
                        child.wait(timeout=0)
                    if app_id in self.registry.pinned:
                        self.registry.unpin(app_id)
                    return "stopped"
                time.sleep(.2)
            raise ServiceError("服务未正常退出或端口仍被占用")
        finally:
            self.transitioning.discard(app_id)
            lock.release()
