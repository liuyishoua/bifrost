"""Build and serve trusted application release artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import zipfile

import yaml


TARGETS = {
    "windows-x64": "Windows · x64",
    "macos-arm64": "macOS · Apple 芯片",
    "macos-x64": "macOS · Intel",
    "android-arm64": "Android · ARM64",
    "ios-arm64": "iOS · ARM64",
}
SKIP_DIRS = {".bifrost", ".runtime", ".runtime_web", "datas", ".git", "__pycache__"}


def current_host():
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x64"
    if system == "Windows":
        return "windows-x64" if machine in ("amd64", "x86_64") else "windows-arm64"
    return f"{system.lower()}-{machine}"


class PackageError(ValueError):
    pass


class PackageManager:
    def __init__(self, runtime: Path, registry):
        self.runtime = Path(runtime)
        self.registry = registry
        self.lock = threading.RLock()
        self.active: set[tuple[str, str]] = set()
        self.errors: dict[tuple[str, str], str] = {}

    def _app(self, app_id):
        app = self.registry.get(app_id)
        if app is None:
            raise PackageError("应用不存在")
        return app

    def _recipe(self, app_id, target):
        if target not in TARGETS:
            raise PackageError("未知打包目标")
        app = self._app(app_id)
        path = app.repository / "package.yaml"
        if not path.is_file():
            raise PackageError("应用尚未提供打包配置 package.yaml")
        try:
            config = yaml.safe_load(path.read_text())
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise PackageError("打包配置无法读取") from exc
        if not isinstance(config, dict) or config.get("schema") != 1 or not isinstance(config.get("targets"), dict):
            raise PackageError("打包配置格式不正确")
        recipe = config["targets"].get(target)
        if recipe is None:
            raise PackageError("此应用尚未提供该系统的构建配方")
        if not isinstance(recipe, dict) or set(recipe) - {"artifact", "build", "build_host"}:
            raise PackageError("目标打包配置不正确")
        host = recipe.get("build_host", "")
        if host and host not in TARGETS:
            raise PackageError("构建环境配置不正确")
        relative = recipe.get("artifact")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise PackageError("产物路径不正确")
        relative_path = Path(relative)
        if relative_path.parts[:2] != (".bifrost", "packages") or ".." in relative_path.parts:
            raise PackageError("产物必须位于应用的 .bifrost/packages 目录")
        current = app.repository
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                raise PackageError("产物路径不能包含符号链接")
        output = current.resolve()
        if output.suffix not in (".exe", ".zip", ".apk", ".ipa"):
            raise PackageError("产物格式不正确")
        command = recipe.get("build", [])
        if not isinstance(command, list) or any(not isinstance(arg, str) or not arg or "\0" in arg
                                                for arg in command):
            raise PackageError("build 必须是参数数组")
        if command and not any("${OUTPUT}" in arg for arg in command):
            raise PackageError("build 必须使用 ${OUTPUT} 指定产物")
        if any("${" in arg.replace("${OUTPUT}", "").replace("${PYTHON}", "") for arg in command):
            raise PackageError("未知打包占位符")
        return app, output, tuple(arg.replace("${OUTPUT}", str(output)).replace("${PYTHON}", sys.executable)
                                  for arg in command), host

    def _source_digest(self, app, command=()):
        digest = hashlib.sha256()
        for directory, names, files in os.walk(app.repository):
            names[:] = sorted(name for name in names if name not in SKIP_DIRS and
                              not (Path(directory) / name).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                relative = path.relative_to(app.repository)
                digest.update(relative.as_posix().encode() + b"\0")
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
        for arg in command:
            if not arg.endswith(".py"):
                continue
            path = (app.repository / arg).resolve()
            if path.is_file() and not path.is_relative_to(app.repository):
                digest.update(str(path).encode() + b"\0")
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def _meta_path(self, app_id, target):
        return self.runtime / "packages" / app_id / f"{target}.json"

    def _log_path(self, app_id, target):
        return self.runtime / "packages" / app_id / f"{target}.log"

    def status(self, app_id, target):
        key = (app_id, target)
        if key in self.active:
            return {"state": "building", "reason": "正在构建"}
        try:
            app, output, command, host = self._recipe(app_id, target)
        except PackageError as exc:
            return {"state": "invalid" if any(word in str(exc) for word in ("路径", "格式", "产物", "符号链接")) else "unavailable",
                    "reason": str(exc)}
        meta_path = self._meta_path(app_id, target)
        if self._valid_artifact(output):
            can_build = bool(command) and (not host or host == current_host())
            try:
                metadata = json.loads(meta_path.read_text())
            except (OSError, ValueError):
                metadata = None
            if metadata is None and not command:
                return {"state": "ready", "reason": "发现已有安装包", "name": output.name,
                        "can_build": can_build}
            if (metadata is not None and metadata.get("source") == self._source_digest(app, command) and
                    metadata.get("sha256") == self._file_digest(output)):
                return {"state": "ready", "reason": "安装包可下载", "name": output.name,
                        "can_build": can_build}
        if key in self.errors:
            return {"state": "failed", "reason": self.errors[key]}
        if host and host != current_host():
            return {"state": "unavailable", "reason": f"需要 {TARGETS[host]} 构建环境"}
        if not command:
            return {"state": "unavailable", "reason": "安装包不存在，且没有构建命令"}
        return {"state": "missing", "reason": "尚未构建"}

    @staticmethod
    def _file_digest(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _valid_artifact(path):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                return False
            if path.suffix == ".exe":
                with path.open("rb") as stream:
                    header = stream.read(64)
                    if len(header) < 64 or header[:2] != b"MZ":
                        return False
                    offset = int.from_bytes(header[60:64], "little")
                    if not 64 <= offset <= 4096:
                        return False
                    stream.seek(offset)
                    return stream.read(4) == b"PE\0\0"
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
            if path.suffix == ".apk":
                return ("AndroidManifest.xml" in names and
                        ("classes.dex" in names or any(name.startswith("lib/") and name.endswith(".so") for name in names)))
            if path.suffix == ".ipa":
                prefixes = {name.split(".app/", 1)[0] + ".app/" for name in names
                            if name.startswith("Payload/") and ".app/Info.plist" in name}
                return any(prefix + "_CodeSignature/CodeResources" in names for prefix in prefixes)
            prefixes = {name.split(".app/", 1)[0] + ".app/" for name in names
                        if name.endswith(".app/Contents/Info.plist")}
            return any(any(name.startswith(prefix + "Contents/MacOS/") for name in names) for prefix in prefixes)
        except (OSError, ValueError, zipfile.BadZipFile):
            return False

    def artifact(self, app_id, target):
        if self.status(app_id, target)["state"] != "ready":
            raise PackageError("安装包尚不可下载")
        return self._recipe(app_id, target)[1]

    def log(self, app_id, target):
        self._recipe(app_id, target)
        try:
            with self._log_path(app_id, target).open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 4000))
                return stream.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def build(self, app_id, target, force=False):
        key = (app_id, target)
        app, output, command, host = self._recipe(app_id, target)
        with self.lock:
            if key in self.active:
                return "building"
            if not force and self.status(app_id, target)["state"] == "ready":
                return "reused"
            if not command:
                raise PackageError("安装包不存在，且没有构建命令")
            if host and host != current_host():
                raise PackageError(f"需要 {TARGETS[host]} 构建环境")
            self.active.add(key)
            self.errors.pop(key, None)
        try:
            source_digest = self._source_digest(app, command)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.unlink(missing_ok=True)
            meta_path = self._meta_path(app_id, target)
            meta_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            meta_path.unlink(missing_ok=True)
            with self._log_path(app_id, target).open("wb") as log:
                result = subprocess.run(command, cwd=app.repository, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=False)
            if result.returncode or not self._valid_artifact(output):
                raise PackageError("构建失败，请查看构建日志")
            metadata = {"source": source_digest, "sha256": self._file_digest(output)}
            meta_path.write_text(json.dumps(metadata))
            return "built"
        except (OSError, subprocess.TimeoutExpired, PackageError) as exc:
            output.unlink(missing_ok=True)
            message = str(exc) if isinstance(exc, PackageError) else "构建未完成，请查看构建日志"
            self.errors[key] = message
            raise PackageError(message) from exc
        finally:
            with self.lock:
                self.active.discard(key)

    def queue(self, app_id, target, force=False):
        state = self.status(app_id, target)
        if state["state"] == "ready":
            if not force:
                return "reused"
            if not state["can_build"]:
                raise PackageError("当前构建环境不可用")
        if state["state"] == "building":
            return "building"
        if state["state"] in ("invalid", "unavailable"):
            raise PackageError(state["reason"])

        def work():
            try:
                self.build(app_id, target, force=force)
            except PackageError:
                pass

        threading.Thread(target=work, name=f"package-{app_id}-{target}", daemon=True).start()
        return "queued"
