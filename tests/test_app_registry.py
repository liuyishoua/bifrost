import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_registry import AppRegistry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps = self.root / "applications"
        self.apps.mkdir()
        self.runtime = self.root / "runtime"
        available = patch("app_registry.port_available", return_value=True)
        available.start()
        self.addCleanup(available.stop)

    def manifest(self, directory, text):
        target = self.apps / directory
        target.mkdir()
        (target / "app.yaml").write_text(text)

    def test_discovers_valid_app_without_executing_build(self):
        self.manifest("weixin", """schema: 1
id: weixin
name: 微信应用
build: [go, build, -o, .bifrost/bin/weixin, .]
start: [.bifrost/bin/weixin, --port, '${PORT}']
""")
        registry = AppRegistry(self.apps, self.runtime)
        registry.refresh()
        app = registry.get("weixin")
        self.assertEqual(app.name, "微信应用")
        self.assertEqual(app.build[0], "go")
        self.assertEqual(app.command[-1], "${PORT}")
        self.assertEqual(app.kind, "manifest")
        self.assertFalse((self.apps / "weixin" / ".bifrost").exists())

    def test_invalid_app_does_not_hide_valid_app(self):
        self.manifest("weixin", "schema: 1\nid: weixin\nname: 微信\nstart: [bin/app, '${PORT}']\n")
        self.manifest("broken", "schema: 1\nid: ../broken\nname: 错误\nstart: [bin/app]\n")
        registry = AppRegistry(self.apps, self.runtime)
        registry.refresh()
        self.assertIsNotNone(registry.get("weixin"))
        self.assertIsNone(registry.get("broken"))
        self.assertIn("broken", registry.errors)

    def test_bad_encoding_and_nul_path_do_not_hide_valid_app(self):
        self.manifest("weixin", "schema: 1\nid: weixin\nname: 微信\nstart: [bin/app, '${PORT}']\n")
        bad_bytes = self.apps / "badbytes"
        bad_bytes.mkdir()
        (bad_bytes / "app.yaml").write_bytes(b"\xff")
        self.manifest("badnul", 'schema: 1\nid: badnul\nname: bad\nstart: ["\\0", "${PORT}"]\n')
        registry = AppRegistry(self.apps, self.runtime)
        registry.refresh()
        self.assertIsNotNone(registry.get("weixin"))
        self.assertIn("badbytes", registry.errors)
        self.assertIn("badnul", registry.errors)

    def test_rejects_invalid_command_and_duplicate_legacy_id(self):
        cases = {
            "badargv": "schema: 1\nid: badargv\nname: bad\nstart: 'bin/app --port 4'\n",
            "badplaceholder": "schema: 1\nid: badplaceholder\nname: bad\nstart: [bin/app, '${HOME}']\n",
            "badpath": "schema: 1\nid: badpath\nname: bad\nstart: [../escape]\n",
            "ticket": "schema: 1\nid: ticket\nname: bad\nstart: [bin/app]\n",
            "badport": "schema: 1\nid: badport\nname: bad\nstart: [bin/app]\n",
            "boolschema": "schema: true\nid: boolschema\nname: bad\nstart: [bin/app, '${PORT}']\n",
        }
        for directory, manifest in cases.items():
            self.manifest(directory, manifest)
        registry = AppRegistry(self.apps, self.runtime)
        registry.refresh()
        for directory in cases:
            self.assertIn(directory, registry.errors)
        self.assertEqual(registry.get("ticket").kind, "legacy")

    def test_port_assignment_survives_restart_and_pinned_manifest_edit(self):
        self.manifest("weixin", "schema: 1\nid: weixin\nname: 微信\nstart: [bin/app, '${PORT}']\n")
        first = AppRegistry(self.apps, self.runtime)
        first.refresh()
        original = first.get("weixin")
        self.assertGreater(original.port, 0)
        self.assertGreater(original.external_port, 0)
        first.pin("weixin")
        (self.apps / "weixin" / "app.yaml").write_text(
            "schema: 1\nid: weixin\nname: 被修改\nstart: [bin/other, '${PORT}']\n")
        second = AppRegistry(self.apps, self.runtime)
        second.refresh()
        self.assertEqual(second.get("weixin"), original)
        second.unpin("weixin")
        self.assertEqual(second.get("weixin").name, "被修改")
        self.assertEqual(second.get("weixin").port, original.port)

    def test_skips_occupied_port_when_allocating(self):
        self.manifest("weixin", "schema: 1\nid: weixin\nname: 微信\nstart: [bin/app, '${PORT}']\n")
        with patch("app_registry.INTERNAL_PORTS", [15000, 15001]), \
             patch("app_registry.port_available", side_effect=lambda port: port != 15000):
            registry = AppRegistry(self.apps, self.runtime)
            registry.refresh()
        self.assertEqual(registry.get("weixin").port, 15001)


if __name__ == "__main__":
    unittest.main()
