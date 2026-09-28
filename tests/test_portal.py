import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from app_registry import AppRegistry
from portal import create_app, password_hash
import portal
import yaml


class PortalTests(unittest.TestCase):
    def test_portal_trusts_gateway_forwarded_origin_on_loopback(self):
        with patch.object(sys, "argv", ["portal.py"]), \
             patch.object(portal, "create_app", return_value=object()), \
             patch.object(portal, "serve") as serve:
            portal.main()
        self.assertEqual(serve.call_args.kwargs["trusted_proxy"], "127.0.0.1")
        self.assertEqual(set(serve.call_args.kwargs["trusted_proxy_headers"]),
                         {"x-forwarded-host", "x-forwarded-proto"})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = "http://127.0.0.1:8080"
        self.env = patch.dict(os.environ, {"ANYDOOR_PUBLIC_ORIGIN": self.origin, "ANYDOOR_DATA_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        applications = Path(self.temp.name) / "applications"
        for app_id in ("douyin", "ticket"):
            directory = applications / app_id
            directory.mkdir(parents=True)
            (directory / "app.yaml").write_text(
                f"schema: 1\nid: {app_id}\nname: {app_id}\nstart: [bin/app, '${{PORT}}']\n")
        registry = AppRegistry(applications, Path(self.temp.name), self.origin)
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        self.app = create_app(self.temp.name, registry=registry)
        self.client = self.app.test_client()
        self.db = Path(self.temp.name) / "portal.sqlite3"
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO users(username,password_hash,role,status,created_at) VALUES(?,?,'admin','active',0)",
                         ("admin", password_hash("administrator1")))

    def token(self, client=None, path="/login"):
        response = (client or self.client).get(path)
        return re.search(rb'name=csrf value="([^"]+)', response.data).group(1).decode()

    def post(self, path, data, client=None):
        client = client or self.client
        token_path = path if path in ("/login", "/register", "/settings") else "/admin"
        return client.post(path, data={**data, "csrf": self.token(client, token_path)},
                           headers={"Origin": self.origin})

    def auth(self, client, app="douyin", method="GET", origin=None, path=None):
        target = self.app.extensions["app_registry"].get(app)
        forwarded = urlsplit(target.origin)
        headers = {"X-Forwarded-Method": method, "X-Forwarded-Uri": path or "/accounts",
                   "X-Forwarded-Host": forwarded.netloc,
                   "X-Forwarded-Proto": forwarded.scheme, "Accept": "application/json"}
        if origin:
            headers["Origin"] = origin
        return client.get(f"/internal/auth/{app}", headers=headers)

    def with_manifest_app(self):
        app_dir = Path(self.temp.name) / "applications" / "weixin"
        app_dir.mkdir(parents=True)
        (app_dir / "app.yaml").write_text(
            "schema: 1\nid: weixin\nname: 微信应用\nstart: [bin/weixin, '--port', '${PORT}']\n")
        registry = AppRegistry(app_dir.parent, Path(self.temp.name), self.origin)
        with patch("app_registry.port_available", return_value=True):
            registry.refresh()
        self.app = create_app(self.temp.name, registry=registry)
        self.client = self.app.test_client()
        return registry.get("weixin")

    def test_manifest_appears_in_admin_and_can_be_granted(self):
        manifest = self.with_manifest_app()
        self.assertEqual(self.post("/login", {"username": "admin", "password": "administrator1"}).status_code, 302)
        self.assertIn("微信应用".encode(), self.client.get("/admin").data)
        with patch.object(self.app.extensions["controller"], "status", return_value=("running", "")):
            self.assertIn(manifest.origin.encode(), self.client.get("/").data)
        user = self.app.test_client()
        self.post("/register", {"username": "alice", "password": "long-password1"}, user)
        with sqlite3.connect(self.db) as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        self.post(f"/admin/user/{uid}", {"status": "active", "grant": "weixin"})
        self.post("/login", {"username": "alice", "password": "long-password1"}, user)
        headers = {"X-Forwarded-Method": "GET", "X-Forwarded-Uri": "/api/messages",
                   "X-Forwarded-Host": urlsplit(manifest.origin).netloc,
                   "X-Forwarded-Proto": urlsplit(manifest.origin).scheme}
        self.assertEqual(user.get("/internal/auth/weixin", headers=headers).status_code, 204)

    def test_admin_can_build_and_download_package_but_user_cannot(self):
        app_dir = Path(self.temp.name) / "applications" / "douyin"
        source = app_dir / "source.txt"
        source.write_text("package bytes")
        artifact = ".bifrost/packages/windows-x64/douyin.exe"
        (app_dir / "package.yaml").write_text(yaml.safe_dump({
            "schema": 1,
            "targets": {"windows-x64": {"artifact": artifact, "build": [
                sys.executable, "-c",
                "import pathlib,sys; h=bytearray(128); h[:2]=b'MZ'; h[60:64]=(64).to_bytes(4,'little'); h[64:68]=b'PE\\0\\0'; pathlib.Path(sys.argv[1]).write_bytes(h+pathlib.Path('source.txt').read_bytes())",
                "${OUTPUT}"]}},
        }))
        self.assertEqual(self.post("/login", {"username": "admin", "password": "administrator1"}).status_code, 302)
        self.assertIn("Windows".encode(), self.client.get("/admin/packages").data)
        self.assertEqual(self.post("/admin/packages/douyin/windows-x64/build", {}).status_code, 303)
        manager = self.app.extensions["package_manager"]
        for _ in range(100):
            if manager.status("douyin", "windows-x64")["state"] == "ready":
                break
            time.sleep(.02)
        self.assertEqual(manager.status("douyin", "windows-x64")["state"], "ready")
        ready_page = self.client.get("/admin/packages").data
        self.assertIn("下载安装包".encode(), ready_page)
        self.assertIn("重新构建".encode(), ready_page)
        metadata = Path(self.temp.name) / "packages" / "douyin" / "windows-x64.json"
        previous_mtime = metadata.stat().st_mtime_ns
        self.assertEqual(self.post("/admin/packages/douyin/windows-x64/build", {"force": "1"}).status_code, 303)
        for _ in range(100):
            if (metadata.exists() and metadata.stat().st_mtime_ns != previous_mtime and
                    manager.status("douyin", "windows-x64")["state"] == "ready"):
                break
            time.sleep(.02)
        self.assertEqual(manager.status("douyin", "windows-x64")["state"], "ready")
        result = self.client.get("/admin/packages/douyin/windows-x64/download")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data.endswith(b"package bytes"))
        result.close()

        user = self.app.test_client()
        self.post("/register", {"username": "alice", "password": "long-password1"}, user)
        with sqlite3.connect(self.db) as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        self.post(f"/admin/user/{uid}", {"status": "active", "grant": "douyin"})
        self.post("/login", {"username": "alice", "password": "long-password1"}, user)
        self.assertEqual(user.get("/admin/packages").status_code, 403)
        self.assertEqual(user.get("/admin/packages/douyin/windows-x64/download").status_code, 403)
        self.assertEqual(user.post("/admin/packages/douyin/windows-x64/build",
                                   data={"csrf": self.token(user, "/settings")},
                                   headers={"Origin": self.origin}).status_code, 403)

    def test_manifest_auth_rejects_wrong_host_and_write_origin(self):
        manifest = self.with_manifest_app()
        self.post("/login", {"username": "admin", "password": "administrator1"})
        headers = {"X-Forwarded-Method": "POST", "X-Forwarded-Uri": "/api/messages",
                   "X-Forwarded-Host": urlsplit(manifest.origin).netloc,
                   "X-Forwarded-Proto": urlsplit(manifest.origin).scheme,
                   "Origin": manifest.origin}
        self.assertEqual(self.client.get("/internal/auth/weixin", headers=headers).status_code, 204)
        for changed in ({"Origin": self.origin}, {"X-Forwarded-Host": "127.0.0.1:9999"},
                        {"X-Forwarded-Uri": "//evil.example"}):
            self.assertEqual(self.client.get("/internal/auth/weixin", headers={**headers, **changed}).status_code, 403)

    def test_manifest_login_returns_to_registered_origin_only(self):
        manifest = self.with_manifest_app()
        self.assertEqual(self.post("/login", {"username": "admin", "password": "administrator1",
                                             "next": manifest.origin + "/"}).location, manifest.origin + "/")
        self.assertEqual(self.post("/login", {"username": "admin", "password": "administrator1",
                                             "next": "http://evil.example/"}).location, "/")

    def test_old_prefixed_link_redirects_to_independent_origin(self):
        target = self.app.extensions["app_registry"].get("ticket")
        response = self.client.get("/ticket/api/state?view=1")
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.location, target.origin + "/api/state?view=1")

    def test_manifest_service_action_calls_controller(self):
        self.with_manifest_app()
        self.post("/login", {"username": "admin", "password": "administrator1"})
        with patch.object(self.app.extensions["controller"], "operate", return_value="running") as operate:
            self.assertEqual(self.post("/admin/service/weixin/start", {}).status_code, 302)
            operate.assert_called_once_with("weixin", "start")

    def test_manifest_validation_error_is_visible_and_audited(self):
        self.with_manifest_app()
        self.post("/login", {"username": "admin", "password": "administrator1"})
        invalid = Path(self.temp.name) / "applications" / "broken"
        invalid.mkdir()
        (invalid / "app.yaml").write_text("schema: 1\nid: wrong\nname: 错误\nstart: [bin/app]\n")
        self.assertIn("broken".encode(), self.client.get("/admin").data)
        with sqlite3.connect(self.db) as conn:
            actions = [row[0] for row in conn.execute("SELECT action FROM audit")]
        self.assertIn("app_validation_error", actions)

    def test_registration_grant_revoke_disable_and_reset(self):
        user = self.app.test_client()
        result = self.post("/register", {"username": "ALICE", "password": "long-password1"}, user)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.post("/login", {"username": "alice", "password": "long-password1"}, user).status_code, 403)
        self.assertEqual(self.auth(user).status_code, 401)
        self.assertEqual(self.post("/login", {"username": "admin", "password": "administrator1"}).status_code, 302)
        with sqlite3.connect(self.db) as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        self.post(f"/admin/user/{uid}", {"status": "active", "grant": "douyin"})
        self.assertEqual(self.post("/login", {"username": "alice", "password": "long-password1"}, user).status_code, 302)
        self.assertEqual(self.auth(user).status_code, 204)
        self.assertEqual(self.auth(user, "ticket").status_code, 403)
        self.post(f"/admin/user/{uid}", {"status": "active"})
        self.assertEqual(self.auth(user).status_code, 403)
        self.post(f"/admin/user/{uid}", {"status": "active", "grant": "douyin"})
        self.post(f"/admin/reset/{uid}", {"password": "temporary123"})
        self.assertEqual(self.auth(user).status_code, 401)
        self.assertEqual(self.post("/login", {"username": "alice", "password": "temporary123"}, user).location, "/settings")
        self.assertEqual(self.auth(user).status_code, 403)
        self.post("/settings", {"old": "temporary123", "new": "new-password123"}, user)
        self.assertEqual(self.auth(user).status_code, 204)
        self.post(f"/admin/user/{uid}", {"status": "disabled", "grant": "douyin"})
        self.assertEqual(self.auth(user).status_code, 401)
        with sqlite3.connect(self.db) as conn:
            audit_text = " ".join(str(row) for row in conn.execute("SELECT actor,action,target FROM audit"))
        self.assertNotIn("temporary123", audit_text)
        self.assertNotIn("new-password123", audit_text)

    def test_origin_csrf_redirect_and_admin_boundary(self):
        self.assertEqual(self.post("/login", {"username": "admin", "password": "administrator1", "next": "https://evil.example"}).location, "/")
        self.assertEqual(self.auth(self.client, method="POST", origin="https://evil.example").status_code, 403)
        douyin_origin = self.app.extensions["app_registry"].get("douyin").origin
        self.assertEqual(self.auth(self.client, method="POST", origin=douyin_origin).status_code, 204)
        self.assertEqual(self.client.post("/admin/service/douyin/start", data={"csrf": "bad"}, headers={"Origin": self.origin}).status_code, 303)
        other = self.app.test_client()
        self.assertEqual(other.get("/admin").status_code, 302)
        self.assertEqual(self.auth(other, path="//douyinish/").status_code, 403)

    def test_portal_form_without_origin_still_requires_csrf(self):
        token = self.token(path="/register")
        accepted = self.client.post("/register", data={"username": "x", "password": "bad", "csrf": token},
                                    headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(accepted.status_code, 400)
        rejected = self.client.post("/register", data={"username": "x", "password": "bad", "csrf": "wrong"},
                                    headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(rejected.status_code, 303)
        self.assertEqual(rejected.location, "/register?expired=1")

    def test_null_origin_with_same_origin_fetch_metadata(self):
        token = self.token(path="/register")
        data = {"username": "x", "password": "bad", "csrf": token}
        accepted = self.client.post("/register", data=data,
                                    headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(accepted.status_code, 400)
        rejected = self.client.post("/register", data=data,
                                    headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(rejected.status_code, 403)

    def test_verification_routes_require_gateway_marker_and_authorization(self):
        self.assertEqual(self.post("/login", {"username": "admin", "password": "administrator1"}).status_code, 302)
        headers = {"X-Forwarded-Method": "GET", "X-Forwarded-Uri": "/verification-frame",
                   "X-Bifrost-Verification": "1"}
        self.assertEqual(self.client.get("/internal/auth/douyin", headers=headers).status_code, 204)
        without_marker = {key: value for key, value in headers.items() if key != "X-Bifrost-Verification"}
        self.assertEqual(self.client.get("/internal/auth/douyin", headers=without_marker).status_code, 403)
        headers["X-Forwarded-Uri"] = "/douyin/api/accounts"
        self.assertEqual(self.client.get("/internal/auth/douyin", headers=headers).status_code, 403)
        headers.update({"X-Forwarded-Uri": "/verification-request/test/unknown",
                        "X-Forwarded-Method": "POST", "Origin": "http://127.0.0.1:8081"})
        self.assertEqual(self.client.get("/internal/auth/douyin", headers=headers).status_code, 204)
        headers["Origin"] = "https://other.example"
        self.assertEqual(self.client.get("/internal/auth/douyin", headers=headers).status_code, 403)


if __name__ == "__main__":
    unittest.main()
