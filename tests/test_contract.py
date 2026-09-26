import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from apps import App
from integrations import adapter_for
from integrations.base import HttpContractAdapter, IntegrationError
from integrations.douyin import DouyinAdapter


class ContractTests(unittest.TestCase):
    def test_new_language_uses_http_contract_without_portal_change(self):
        app = App("sample", "示例", "", "/sample/", 9000, None, None, "/.well-known/bifrost/ready", ())
        adapter = adapter_for("sample")
        self.assertIsInstance(adapter, HttpContractAdapter)
        with patch("integrations.base.read_json", side_effect=[{"ready": True}, {"idle": True}]):
            self.assertTrue(adapter.ready(app))
            adapter.ensure_idle(app)
        with patch("integrations.base.read_json", return_value={"idle": False, "reason": "正在工作"}):
            with self.assertRaisesRegex(IntegrationError, "正在工作"):
                adapter.ensure_idle(app)
        with patch("integrations.base.read_json", return_value={}):
            self.assertFalse(adapter.ready(app))
            with self.assertRaises(IntegrationError):
                adapter.ensure_idle(app)

    def test_douyin_idle_check_reads_wal_database_without_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA journal_mode=WAL")
                for table in ("tasks", "recipients", "task_messages", "chat_sends",
                              "searches", "task_reply_reviews", "task_reply_targets"):
                    db.execute(f"CREATE TABLE {table}(status TEXT)")
                db.execute("CREATE TABLE accounts(status TEXT, qr_status TEXT)")
            db.close()
            with sqlite3.connect(path) as checkpoint:
                self.assertEqual(checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0)
            checkpoint.close()
            for suffix in ("-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)
            self.assertFalse(path.with_name(path.name + "-wal").exists())
            app = SimpleNamespace(data_dir=Path(directory))
            adapter = DouyinAdapter()
            adapter.ensure_idle(app)
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO tasks(status) VALUES('running')")
            db.close()
            with self.assertRaisesRegex(IntegrationError, "在途"):
                adapter.ensure_idle(app)


if __name__ == "__main__":
    unittest.main()
