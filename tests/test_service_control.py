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
        root = Path(self.temp.name)
        for app_id in ("douyin", "ticket"):
            directory = root / "apps" / app_id
            directory.mkdir(parents=True)
            (directory / "app.yaml").write_text(
                f"schema: 1\nid: {app_id}\nname: {app_id}\nstart: [bin/app, '${{PORT}}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        self.control = Controller(root / "runtime", registry)

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

    def test_refresh_during_build_pins_selected_manifest(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        (app_dir / "server").write_text("old")
        manifest = app_dir / "app.yaml"
        manifest.write_text("schema: 1\nid: weixin\nname: old\nstart: [server, '${PORT}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        selected = registry.get("weixin")
        control = Controller(root / "runtime", registry)

        def edit_while_building(_app):
            manifest.write_text("schema: 1\nid: weixin\nname: new\nstart: [newserver, '${PORT}']\n")
            registry.refresh()
            self.assertEqual(registry.get("weixin"), selected)

        with patch.object(control, "_listening", return_value=False), \
             patch.object(control, "_configured_processes", return_value=[]), \
             patch.object(control, "_build", side_effect=edit_while_building), \
             patch("service_control.subprocess.Popen", side_effect=OSError("spawn failed")):
            with self.assertRaises(ServiceError):
                control.operate("weixin", "start")
        self.assertEqual(registry.get("weixin").name, "new")

    def test_removal_during_build_keeps_selected_record_until_launch_failure(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        (app_dir / "server").write_text("old")
        manifest = app_dir / "app.yaml"
        manifest.write_text("schema: 1\nid: weixin\nname: old\nstart: [server, '${PORT}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        control = Controller(root / "runtime", registry)

        def remove_while_building(_app):
            manifest.unlink()
            registry.refresh()
            self.assertIsNotNone(registry.get("weixin"))

        with patch.object(control, "_listening", return_value=False), \
             patch.object(control, "_configured_processes", return_value=[]), \
             patch.object(control, "_build", side_effect=remove_while_building), \
             patch("service_control.subprocess.Popen", side_effect=OSError("spawn failed")):
            with self.assertRaisesRegex(ServiceError, "启动失败"):
                control.operate("weixin", "start")
        self.assertIsNone(registry.get("weixin"))

    def test_stale_pin_released_after_external_exit(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        manifest = app_dir / "app.yaml"
        manifest.write_text("schema: 1\nid: weixin\nname: old\nstart: [server, '${PORT}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        registry.pin("weixin")
        manifest.write_text("schema: 1\nid: weixin\nname: new\nstart: [server, '${PORT}']\n")
        registry.refresh()
        control = Controller(root / "runtime", registry)
        with patch.object(control, "_listening", return_value=False), \
             patch.object(control, "_configured_processes", return_value=[]):
            self.assertEqual(control.status("weixin")[0], "stopped")
        self.assertEqual(registry.get("weixin").name, "new")
        self.assertNotIn("weixin", registry.pinned)

    def test_removed_manifest_can_be_stopped_after_external_exit_and_restart(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        manifest = app_dir / "app.yaml"
        manifest.write_text("schema: 1\nid: weixin\nname: old\nstart: [server, '${PORT}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        registry.pin("weixin")
        manifest.unlink()
        restarted = AppRegistry(root / "apps", root / "runtime")
        restarted.refresh()
        control = Controller(root / "runtime", restarted)
        with patch.object(control, "_listening", return_value=False), \
             patch.object(control, "_configured_processes", return_value=[]):
            self.assertEqual(control.operate("weixin", "stop"), "stopped")
        self.assertIsNone(restarted.get("weixin"))
        self.assertFalse(restarted.pinned)

    def test_occupied_port_after_build_prevents_spawn(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        (app_dir / "server").write_text("binary")
        (app_dir / "app.yaml").write_text(
            "schema: 1\nid: weixin\nname: 微信\nstart: [server, '${PORT}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        control = Controller(root / "runtime", registry)
        with patch.object(control, "_listening", side_effect=[False, True]), \
             patch.object(control, "_configured_processes", return_value=[]), \
             patch("service_control.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ServiceError, "端口或配置进程已存在"):
                control.operate("weixin", "start")
        spawn.assert_not_called()
        self.assertFalse(registry.pinned)

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

    def test_manifest_data_directory_exists_before_launch(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        (app_dir / "server").write_text("not executable")
        (app_dir / "app.yaml").write_text(
            "schema: 1\nid: weixin\nname: 微信\nstart: [server, '${PORT}', '${DATA_DIR}']\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        control = Controller(root / "runtime", registry)
        with patch.object(control, "_listening", return_value=False), \
             patch.object(control, "_configured_processes", return_value=[]):
            with self.assertRaises(ServiceError):
                control.operate("weixin", "start")
        self.assertTrue((app_dir / ".runtime").is_dir())

    def test_unreadable_manifest_activity_blocks_stop_as_service_error(self):
        root = Path(self.temp.name)
        app_dir = root / "apps" / "weixin"
        app_dir.mkdir(parents=True)
        (app_dir / "app.yaml").write_text(
            "schema: 1\nid: weixin\nname: 微信\nstart: [bin/weixin, '${PORT}']\nactivity_path: /activity\n")
        registry = AppRegistry(root / "apps", root / "runtime")
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        control = Controller(root / "runtime", registry)
        owner = Mock()
        with patch.object(control, "_listening", return_value=True), \
             patch.object(control, "_owner", return_value=owner), \
             patch.object(control, "_ready", return_value=True), \
             patch("service_control.read_json", side_effect=ServiceError("无法读取")):
            with self.assertRaisesRegex(ServiceError, "无法读取"):
                control.operate("weixin", "stop")
        owner.send_signal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
