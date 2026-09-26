"""Read-only compatibility adapter for the Douyin console."""
import sqlite3
from contextlib import closing
from ..base import IntegrationError, read_json


class DouyinAdapter:
    def ready(self, app):
        data = read_json(app, app.ready_path)
        return type(data.get("demo")) is bool and isinstance(data.get("now"), (int, float))

    def ensure_idle(self, app):
        db = app.data_dir / "app.sqlite3"
        if not db.is_file():
            raise IntegrationError("无法确认抖音活动状态")
        checks = (
            "SELECT 1 FROM tasks WHERE status='running' LIMIT 1",
            "SELECT 1 FROM recipients WHERE status='sending' LIMIT 1",
            "SELECT 1 FROM task_messages WHERE status='sending' LIMIT 1",
            "SELECT 1 FROM chat_sends WHERE status='sending' LIMIT 1",
            "SELECT 1 FROM searches WHERE status IN ('running','pending') LIMIT 1",
            "SELECT 1 FROM task_reply_reviews WHERE status IN ('running','pending') LIMIT 1",
            "SELECT 1 FROM task_reply_targets WHERE status='running' LIMIT 1",
            "SELECT 1 FROM accounts WHERE status='checking' OR qr_status IN ('starting','waiting','scanned','verifying') LIMIT 1",
        )
        try:
            # A WAL database without -wal/-shm sidecars cannot be opened with
            # mode=ro on some SQLite builds. Open the existing file in rw mode,
            # then forbid SQL writes and read every activity flag in one snapshot.
            with closing(sqlite3.connect(f"file:{db}?mode=rw", uri=True, timeout=2)) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                if any(conn.execute(sql).fetchone() for sql in checks):
                    raise IntegrationError("抖音存在在途任务、搜索、发送或扫码")
        except sqlite3.Error as exc:
            raise IntegrationError("抖音活动状态读取失败") from exc
