"""Generate and activate Caddy routes for manifest applications."""

from __future__ import annotations

from pathlib import Path
import subprocess

from apps import App


class GatewayError(RuntimeError):
    pass


def render_fragments(apps: tuple[App, ...], public_origin: str) -> tuple[str, str]:
    local, public = [], []
    for app in apps:
        body = f"""
\t@internal path /internal /internal/*
\thandle @internal {{
\t\trespond 404
\t}}
\thandle {{
\t\troute {{
\t\t\timport clean_request
\t\t\tforward_auth 127.0.0.1:8790 {{
\t\t\t\turi /internal/auth/{app.id}
\t\t\t\theader_up Host 127.0.0.1:8790
\t\t\t}}
\t\t\treverse_proxy 127.0.0.1:{app.port} {{
\t\t\t\theader_up Host 127.0.0.1:{app.port}
\t\t\t\theader_up Cookie "^portal_session=[^;]*(; *)?" ""
\t\t\t\theader_up Cookie "; *portal_session=[^;]*" ""
\t\t\t}}
\t\t}}
\t}}
"""
        local.append(f"http://127.0.0.1:{app.external_port} {{\n\tbind 127.0.0.1\n{body}}}\n")
        public.append(f"https://{{$BIFROST_PUBLIC_IP}}:{app.external_port} {{\n"
                      f"\ttls {{$BIFROST_TLS_CERT}} {{$BIFROST_TLS_KEY}}\n{body}}}\n")
    return "\n".join(local), "\n".join(public)


class GatewayRoutes:
    def __init__(self, runtime: Path, public_origin: str, config_path: Path | None):
        self.runtime = Path(runtime)
        self.public_origin = public_origin
        self.config_path = Path(config_path) if config_path else None
        self.pending_reload = False

    @staticmethod
    def _write(path: Path, content: str):
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content)
        temp.replace(path)

    def sync(self, apps: tuple[App, ...], reload: bool = True) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        paths = (self.runtime / "apps.local.caddy", self.runtime / "apps.public.caddy")
        content = render_fragments(apps, self.public_origin)
        previous = tuple(path.read_text() if path.exists() else "" for path in paths)
        if content == previous and all(path.exists() for path in paths) and not (reload and self.pending_reload):
            return
        try:
            for path, value in zip(paths, content):
                self._write(path, value)
            if self.config_path and not reload:
                self.pending_reload = True
            if self.config_path and reload:
                for action in ("validate", "reload"):
                    result = subprocess.run(("caddy", action, "--config", str(self.config_path)),
                                            capture_output=True, text=True, check=False, timeout=20)
                    if result.returncode:
                        raise GatewayError(f"Caddy {action} 失败: {result.stderr.strip()}")
                self.pending_reload = False
        except (OSError, subprocess.TimeoutExpired, GatewayError) as exc:
            for path, value in zip(paths, previous):
                self._write(path, value)
            if isinstance(exc, GatewayError):
                raise
            raise GatewayError("网关配置更新失败") from exc
