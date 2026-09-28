"""Discover trusted application manifests without executing their commands."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import socket
import threading
from urllib.parse import urlsplit

import yaml

from apps import App


ID_RE = re.compile(r"[a-z][a-z0-9_-]{1,31}\Z")
PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")
FIELDS = {"schema", "id", "name", "description", "build", "start", "activity_path", "ready_path", "internal_port", "data_dir"}
INTERNAL_PORTS = range(10000, 11000)
EXTERNAL_PORTS = range(9000, 10000)
PLATFORM_PORTS = {8080, 8081, 8443, 8790}


def port_available(port: int) -> bool:
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


class ManifestError(ValueError):
    pass


class AppRegistry:
    def __init__(self, applications_dir: Path, runtime: Path,
                 public_origin: str = "http://127.0.0.1:8080"):
        self.applications_dir = Path(applications_dir)
        self.runtime = Path(runtime)
        self.public_origin = public_origin.rstrip("/")
        self.lock = threading.RLock()
        self.records: dict[str, App] = {}
        self.errors: dict[str, str] = {}
        self.pinned = self._load_pinned()
        self.ports = self._load_ports()

    def _port_file(self):
        return self.runtime / "apps-ports.json"

    def _load_ports(self):
        try:
            raw = json.loads(self._port_file().read_text())
            return {key: (int(value[0]), int(value[1])) for key, value in raw.items()}
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return {}

    def _save_ports(self):
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self._port_file()
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(self.ports))
        temp.replace(target)

    def _allocated(self, app: App) -> App:
        if app.port in PLATFORM_PORTS:
            raise ManifestError("internal_port 与平台端口冲突")
        if app.id in self.ports:
            internal, external = self.ports[app.id]
            if app.port and app.port != internal:
                raise ManifestError("internal_port 与已分配端口不一致")
            if internal == external or internal in PLATFORM_PORTS:
                raise ManifestError("已分配端口冲突")
        else:
            reserved = {port for pair in self.ports.values() for port in pair}
            internal = app.port or next((port for port in INTERNAL_PORTS if port not in reserved and port_available(port)), None)
            if app.port and app.port in reserved:
                raise ManifestError("internal_port 已被其他应用使用")
            if internal is not None:
                reserved.add(internal)
            external = next((port for port in EXTERNAL_PORTS if port not in reserved and port_available(port)), None)
            if internal is None or external is None:
                raise ManifestError("没有可用端口")
            self.ports[app.id] = (internal, external)
            self._save_ports()
        parsed = urlsplit(self.public_origin)
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        return replace(app, port=internal, external_port=external,
                       origin=f"{parsed.scheme}://{host}:{external}")

    def _pin_file(self):
        return self.runtime / "apps-registry.json"

    def _load_pinned(self):
        try:
            raw = json.loads(self._pin_file().read_text())
            return {key: App(**{**value,
                                "repository": Path(value["repository"]),
                                "data_dir": Path(value["data_dir"]),
                                "command": tuple(value["command"]),
                                "build": tuple(value["build"])})
                    for key, value in raw.items()}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def _save_pinned(self):
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self._pin_file()
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps({key: {field: str(value) if isinstance(value, Path) else value
                                          for field, value in asdict(app).items()}
                                    for key, app in self.pinned.items()}, ensure_ascii=False))
        temp.replace(target)

    def pin(self, app_id: str, selected: App | None = None) -> None:
        with self.lock:
            self.pinned[app_id] = selected if selected is not None else self.records[app_id]
            self.records[app_id] = self.pinned[app_id]
            self._save_pinned()

    def unpin(self, app_id: str) -> None:
        with self.lock:
            self.pinned.pop(app_id, None)
            self._save_pinned()
            self.refresh()

    def get(self, app_id: str) -> App | None:
        return self.records.get(app_id)

    def all(self) -> tuple[App, ...]:
        return tuple(self.records.values())

    def _argv(self, value, field):
        if not isinstance(value, list) or not value or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in value):
            raise ManifestError(f"{field} 必须是非空参数数组")
        for arg in value:
            if "${" in arg and (arg.count("${") != len(PLACEHOLDER_RE.findall(arg)) or
                               any(token not in ("PORT", "DATA_DIR") for token in PLACEHOLDER_RE.findall(arg))):
                raise ManifestError("未知参数占位符")
        return tuple(value)

    def _parse(self, directory: Path) -> App:
        try:
            value = yaml.safe_load((directory / "app.yaml").read_text())
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ManifestError("YAML 无法读取") from exc
        if not isinstance(value, dict) or set(value) - FIELDS:
            raise ManifestError("配置字段不正确")
        app_id = value.get("id")
        if type(value.get("schema")) is not int or value["schema"] != 1 or app_id != directory.name or not ID_RE.fullmatch(str(app_id)):
            raise ManifestError("schema 或应用 ID 不正确")
        name = value.get("name")
        description = value.get("description", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ManifestError("应用名称不正确")
        if not isinstance(description, str) or len(description) > 240:
            raise ManifestError("应用描述过长")
        command = self._argv(value.get("start"), "start")
        if not any("${PORT}" in arg for arg in command):
            raise ManifestError("start 必须使用 ${PORT} 接收平台端口")
        build = self._argv(value["build"], "build") if "build" in value else ()
        internal_port = value.get("internal_port", 0)
        if type(internal_port) is not int or internal_port and not 1024 <= internal_port <= 65535:
            raise ManifestError("internal_port 不正确")
        configured_data_dir = value.get("data_dir", ".runtime")
        if (not isinstance(configured_data_dir, str) or not configured_data_dir or
                "\0" in configured_data_dir or Path(configured_data_dir).is_absolute()):
            raise ManifestError("data_dir 必须是应用目录内的相对路径")
        data_dir = (directory / configured_data_dir).resolve()
        if not data_dir.is_relative_to(directory.resolve()):
            raise ManifestError("data_dir 必须位于应用目录内")
        executable = Path(os.path.normpath(str(directory.resolve() / command[0])))
        origin = urlsplit(self.public_origin)
        if origin.scheme not in ("http", "https") or not origin.hostname:
            raise ManifestError("平台访问地址无效")
        ready_path = value.get("ready_path", "")
        activity_path = value.get("activity_path", "")
        if any(not isinstance(path, str) or path and not path.startswith("/")
               for path in (ready_path, activity_path)):
            raise ManifestError("状态路径必须以 / 开头")
        return App(app_id, name.strip(), description, "/", internal_port, directory.resolve(),
                   data_dir, ready_path,
                   (str(executable), *command[1:]), "manifest", build, 0, "", activity_path)

    def refresh(self) -> None:
        with self.lock:
            records = {}
            errors = {}
            for directory in sorted(self.applications_dir.iterdir()) if self.applications_dir.is_dir() else ():
                if not directory.is_dir() or not (directory / "app.yaml").is_file():
                    continue
                try:
                    parsed = self._allocated(self._parse(directory))
                    records[parsed.id] = parsed
                except (ManifestError, ValueError) as exc:
                    errors[directory.name] = str(exc)
            records.update(self.pinned)
            self.records = records
            self.errors = errors
