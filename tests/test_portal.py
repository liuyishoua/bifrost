import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from portal import create_app, password_hash
from link_catalog import load_links
import portal


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = "http://127.0.0.1:8080"
        self.env = patch.dict(os.environ, {"BIFROST_PUBLIC_ORIGIN": self.origin,
                                           "BIFROST_DATA_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.catalog = self.root / "links.json"
        self.catalog.write_text(json.dumps([
            {"id": "douyin", "name": "Douyin", "url": "https://dy.example/work?tab=1", "category": "应用"},
            {"id": "ticket", "name": "Ticket", "url": "http://127.0.0.1:8767/", "category": "工具"}]))
        self.app = create_app(self.root, links_path=self.catalog)
        self.client = self.app.test_client()
        self.db = self.root / "portal.sqlite3"
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

    def login(self):
        return self.post("/login", {"username": "admin", "password": "administrator1"})

    def test_login_and_direct_links_without_application_runtime(self):
        self.assertEqual(self.client.get("/").location, "/login")
        self.assertEqual(self.login().status_code, 302)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'https://dy.example/work?tab=1', response.data)
        self.assertNotIn(b'/admin/service/', response.data)
        self.assertNotIn(b'/admin/packages', response.data)
        self.assertFalse({"controller", "gateway_routes", "app_registry", "package_manager"} & self.app.extensions.keys())
        self.assertFalse((self.root / "apps.local.caddy").exists())
        for path in ("/internal/auth/douyin", "/admin/packages"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_search_category_empty_and_catalog_reload(self):
        self.login()
        self.assertNotIn(b'127.0.0.1:8767', self.client.get('/?q=dOuYiN').data)
        self.assertNotIn(b'https://dy.example', self.client.get('/?category=工具').data)
        self.assertIn('没有匹配的链接'.encode(), self.client.get('/?q=missing').data)
        self.catalog.write_text('[]')
        self.assertIn('还没有链接'.encode(), self.client.get('/').data)

    def test_registration_visibility_reset_and_disable_keep_existing_accounts(self):
        user = self.app.test_client()
        self.post('/register', {"username": "alice", "password": "long-password1"}, user)
        self.assertEqual(self.post('/login', {"username": "alice", "password": "long-password1"}, user).status_code, 403)
        self.login()
        with sqlite3.connect(self.db) as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        self.post(f'/admin/user/{uid}', {"status": "active", "grant": "douyin"})
        # Reopening the same database preserves accounts and link visibility settings.
        reopened = create_app(self.root, links_path=self.catalog).test_client()
        self.assertEqual(self.post('/login', {"username": "alice", "password": "long-password1"}, reopened).status_code, 302)
        self.assertIn(b'https://dy.example', reopened.get('/').data)
        self.assertNotIn(b'127.0.0.1:8767', reopened.get('/').data)
        self.assertNotIn(b'category=%E5%B7%A5%E5%85%B7', reopened.get('/').data)
        self.assertEqual(reopened.get('/admin').status_code, 403)
        self.post(f'/admin/user/{uid}', {"status": "active"})
        self.assertNotIn(b'https://dy.example', reopened.get('/').data)
        self.post(f'/admin/reset/{uid}', {"password": "temporary123"})
        self.assertEqual(reopened.get('/').location, '/login')
        self.assertEqual(self.post('/login', {"username": "alice", "password": "temporary123"}, reopened).location, '/settings')
        self.assertEqual(reopened.get('/').location, '/settings')
        self.post('/settings', {"old": "temporary123", "new": "new-password123"}, reopened)
        self.assertEqual(reopened.get('/').status_code, 200)
        self.post(f'/admin/user/{uid}', {"status": "disabled"})
        self.assertEqual(reopened.get('/').location, '/login')

    def test_redirect_csrf_and_logout(self):
        self.assertEqual(self.post('/login', {"username": "admin", "password": "administrator1", "next": "https://evil.example/"}).location, '/')
        self.assertEqual(self.client.post('/admin/user/1', data={"status": "disabled", "csrf": "bad"}, headers={"Origin": self.origin}).status_code, 303)
        self.assertEqual(self.client.post('/logout', data={"csrf": self.token(path='/settings')}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.post('/logout', {})
        self.assertEqual(self.client.get('/').location, '/login')

    def test_catalog_validation_and_html_escaping(self):
        self.login()
        for url in ('javascript:alert(1)', '//evil.example', 'https://user:pass@example.com', 'http://example.com:bad', 'https://example.com/ bad'):
            self.catalog.write_text(json.dumps([{"id": "test", "name": "Test", "url": url}]))
            with self.subTest(url=url), self.assertRaises(ValueError):
                load_links(self.catalog)
        self.catalog.write_text(json.dumps([{"id": "test", "name": "<script>alert(1)</script>", "url": "https://example.com/"}]))
        response = self.client.get('/')
        self.assertNotIn(b'<script>', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)
        entry = {"id": "same", "name": "test", "url": "https://example.com/"}
        self.catalog.write_text(json.dumps([entry, entry]))
        with self.assertRaises(ValueError):
            load_links(self.catalog)

    def test_portal_trusts_gateway_forwarded_origin_on_loopback(self):
        with patch.object(sys, "argv", ["portal.py"]), patch.object(portal, "create_app", return_value=object()), patch.object(portal, "serve") as serve:
            portal.main()
        self.assertEqual(serve.call_args.kwargs["trusted_proxy"], "127.0.0.1")
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
