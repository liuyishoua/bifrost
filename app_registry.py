"""Discover trusted application manifests without executing their commands."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

from apps import App, LEGACY_APPS


ID_RE = re.compile(r"[a-z][a-z0-9_-]{1,31}\Z")
PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")
FIELDS = {"schema", "id", "name", "description", "build", "start", "activity_path", "ready_path"}


class ManifestError(ValueError):
    pass


class AppRegistry:
    def __init__(self, applications_dir: Path, runtime: Path,
                 public_origin: str = "http://127.0.0.1:8080"):
        self.applications_dir = Path(applications_dir)
        self.runtime = Path(runtime)
        self.public_origin = public_origin.rstrip("/")
        self.records = dict(LEGACY_APPS)
        self.errors: dict[str, str] = {}
        self.pinned = self._load_pinned()

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

    def pin(self, app_id: str) -> None:
        self.pinned[app_id] = self.records[app_id]
        self._save_pinned()

    def unpin(self, app_id: str) -> None:
        self.pinned.pop(app_id, None)
        self._save_pinned()
        self.refresh()

    def get(self, app_id: str) -> App | None:
        return self.records.get(app_id)

    def all(self) -> tuple[App, ...]:
        return tuple(self.records.values())

    def _argv(self, value, field):
        if not isinstance(value, list) or not value or any(not isinstance(arg, str) or not arg for arg in value):
            raise ManifestError(f"{field} 必须是非空参数数组")
        for arg in value:
            if "${" in arg and (arg.count("${") != len(PLACEHOLDER_RE.findall(arg)) or
                               any(token not in ("PORT", "DATA_DIR") for token in PLACEHOLDER_RE.findall(arg))):
                raise ManifestError("未知参数占位符")
        return tuple(value)

    def _parse(self, directory: Path) -> App:
        try:
            value = yaml.safe_load((directory / "app.yaml").read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ManifestError("YAML 无法读取") from exc
        if not isinstance(value, dict) or set(value) - FIELDS:
            raise ManifestError("配置字段不正确")
        app_id = value.get("id")
        if value.get("schema") != 1 or app_id != directory.name or not ID_RE.fullmatch(str(app_id)):
            raise ManifestError("schema 或应用 ID 不正确")
        if app_id in LEGACY_APPS:
            raise ManifestError("应用 ID 与现有应用重复")
        name = value.get("name")
        description = value.get("description", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ManifestError("应用名称不正确")
        if not isinstance(description, str) or len(description) > 240:
            raise ManifestError("应用描述过长")
        command = self._argv(value.get("start"), "start")
        build = self._argv(value["build"], "build") if "build" in value else ()
        executable = (directory / command[0]).resolve()
        if not executable.is_relative_to(directory.resolve()):
            raise ManifestError("启动文件必须位于应用目录内")
        origin = urlsplit(self.public_origin)
        if origin.scheme not in ("http", "https") or not origin.hostname:
            raise ManifestError("平台访问地址无效")
        ready_path = value.get("ready_path", "")
        activity_path = value.get("activity_path", "")
        if any(not isinstance(path, str) or path and not path.startswith("/")
               for path in (ready_path, activity_path)):
            raise ManifestError("状态路径必须以 / 开头")
        return App(app_id, name.strip(), description, "/", 0, directory.resolve(),
                   directory.resolve() / ".runtime", ready_path,
                   (str(executable), *command[1:]), "manifest", build, 0, "", activity_path)

    def refresh(self) -> None:
        records = dict(LEGACY_APPS)
        errors = {}
        for directory in sorted(self.applications_dir.iterdir()) if self.applications_dir.is_dir() else ():
            if not directory.is_dir() or not (directory / "app.yaml").is_file():
                continue
            try:
                parsed = self._parse(directory)
                records[parsed.id] = parsed
            except ManifestError as exc:
                errors[directory.name] = str(exc)
        records.update(self.pinned)
        self.records = records
        self.errors = errors
