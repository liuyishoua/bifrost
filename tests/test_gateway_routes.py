import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps import App, LEGACY_APPS
from gateway_routes import GatewayError, GatewayRoutes, render_fragments


class GatewayRouteTests(unittest.TestCase):
    def manifest_app(self):
        return App("weixin", "微信", "", "/", 10000, Path("/tmp/weixin"), Path("/tmp/weixin/.runtime"),
                   "", ("/tmp/weixin/bin/app",), "manifest", (), 9000,
                   "https://203.0.113.7:9000")

    def test_new_app_uses_separate_origin_with_authorization(self):
        local, public = render_fragments((self.manifest_app(),), "https://203.0.113.7")
        self.assertIn("http://127.0.0.1:9000", local)
        self.assertIn("https://{$BIFROST_PUBLIC_IP}:9000", public)
        for rendered in (local, public):
            self.assertIn("uri /internal/auth/weixin", rendered)
            self.assertIn("import clean_request", rendered)
            self.assertIn('header_up Cookie "^portal_session=', rendered)
            self.assertNotIn("header_up -Cookie", rendered)
            self.assertIn("reverse_proxy 127.0.0.1:10000", rendered)
            self.assertNotIn("strip_prefix", rendered)

    def test_legacy_apps_do_not_get_duplicate_sites(self):
        local, public = render_fragments(tuple(LEGACY_APPS.values()), "http://127.0.0.1:8080")
        self.assertEqual(local.strip(), "")
        self.assertEqual(public.strip(), "")

    def test_empty_fragments_exist_before_caddy_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            GatewayRoutes(runtime, "http://127.0.0.1:8080", None).sync(tuple(LEGACY_APPS.values()))
            self.assertTrue((runtime / "apps.local.caddy").is_file())
            self.assertTrue((runtime / "apps.public.caddy").is_file())

    def test_failed_reload_restores_previous_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            local = runtime / "apps.local.caddy"
            public = runtime / "apps.public.caddy"
            local.write_text("old local\n")
            public.write_text("old public\n")
            gateway = GatewayRoutes(runtime, "https://203.0.113.7", Path("Caddyfile.public"))
            with patch("gateway_routes.subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 0, stderr=""),
                    subprocess.CompletedProcess([], 1, stderr="reload failed")]):
                with self.assertRaises(GatewayError):
                    gateway.sync((self.manifest_app(),))
            self.assertEqual(local.read_text(), "old local\n")
            self.assertEqual(public.read_text(), "old public\n")

    def test_routes_remain_ready_when_caddy_has_not_started(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            gateway = GatewayRoutes(runtime, "https://203.0.113.7", Path("Caddyfile.public"))
            with patch("gateway_routes.subprocess.run") as command:
                gateway.sync((self.manifest_app(),), reload=False)
                command.assert_not_called()
            self.assertIn("weixin", (runtime / "apps.public.caddy").read_text())

    def test_refresh_reloads_routes_written_at_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = GatewayRoutes(Path(directory), "https://203.0.113.7", Path("Caddyfile.public"))
            gateway.sync((self.manifest_app(),), reload=False)
            with patch("gateway_routes.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stderr="")) as command:
                gateway.sync((self.manifest_app(),))
            self.assertEqual(command.call_count, 2)


if __name__ == "__main__":
    unittest.main()
