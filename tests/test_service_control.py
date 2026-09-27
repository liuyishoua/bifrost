import tempfile
import unittest
from unittest.mock import Mock, patch
import psutil
from pathlib import Path
import sys

from app_registry import AppRegistry

from service_control import Controller, ServiceError


class ServiceControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = Controller(self.temp.name)

    def test_existing_port_never_starts_another_instance(self):
        with patch.object(self.control, "_listening", return_value=True), patch.object(self.control, "_owner", return_value=None):
            with self.assertRaisesRegex(ServiceError, "端口或配置进程已存在"):
                self.control.operate("douyin", "start")

    def test_unknown_owner_never_receives_stop(self):
        with patch.object(self.control, "_listening", return_value=True), patch.object(self.control, "_owner", return_value=None):
            with self.assertRaisesRegex(ServiceError, "进程归属"):
                self.control.operate("douyin", "stop")

    def test_busy_or_unreadable_state_never_receives_stop(self):
        owner = Mock()
        for failure in (ServiceError("业务繁忙"), OSError("unreadable")):
            with self.subTest(failure=failure), patch.object(self.control, "_listening", return_value=True), \
                 patch.object(self.control, "_owner", return_value=owner), \
                 patch.object(self.control, "_ready", return_value=True), \
                 patch.object(self.control, "_idle", side_effect=failure):
                with self.assertRaises(ServiceError):
                    self.control.operate("ticket", "stop")
        owner.send_signal.assert_not_called()

    def test_exited_nonchild_with_released_port_counts_as_stopped(self):
        owner = Mock()
        owner.is_running.return_value = True
        owner.status.return_value = psutil.STATUS_ZOMBIE
        with patch.object(self.control, "_listening", side_effect=[True, False]), \
             patch.object(self.control, "_owner", return_value=owner), \
             patch.object(self.control, "_ready", return_value=True), \
             patch.object(self.control, "_idle"):
            self.assertEqual(self.control.operate("douyin", "stop"), "stopped")
        owner.send_signal.assert_called_once()

    def test_manifest_build_failure_prevents_start(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        (app_dir / "app.yaml").write_text(
            "schema: 1\nid: weixin\nname: 微信\n"
            f"build: ['{sys.executable}', '-c', 'import sys; sys.exit(7)']\n"
            "start: [bin/weixin, '--port', '${PORT}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        control = Controller(root / "runtime", registry)
        with patch.object(control, "_listening", return_value=False), \
             patch.object(control, "_configured_processes", return_value=[]):
            with self.assertRaisesRegex(ServiceError, "构建失败"):
                control.operate("weixin", "start")
        self.assertTrue((root / "runtime" / "weixin.build.log").exists())

    def test_manifest_unknown_owner_never_receives_stop(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        (app_dir / "app.yaml").write_text(
            "schema: 1\nid: weixin\nname: 微信\nstart: [bin/weixin, '${PORT}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        control = Controller(root / "runtime", registry)
        with patch.object(control, "_listening", return_value=True), patch.object(control, "_owner", return_value=None):
            with self.assertRaisesRegex(ServiceError, "进程归属"):
                control.operate("weixin", "stop")

    def test_failed_manifest_spawn_is_reported_and_unpinned(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        executable = app_dir / "server"
        executable.write_text("not executable")
        (app_dir / "app.yaml").write_text(
            "schema: 1\nid: weixin\nname: 微信\nstart: [server, '${PORT}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        control = Controller(root / "runtime", registry)
        with patch.object(control, "_listening", return_value=False), \
             patch.object(control, "_configured_processes", return_value=[]):
            with self.assertRaisesRegex(ServiceError, "启动失败"):
                control.operate("weixin", "start")
        self.assertNotIn("weixin", registry.pinned)


if __name__ == "__main__":
    unittest.main()
