import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from portal import create_app, password_hash
from ops_identity import COOKIE, IdentityClient, IdentityUnavailable
from deploy.import_ops_identity import migrate


class UnifiedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = 'https://d.58nft.net'
        env = patch.dict(os.environ, {'BIFROST_PUBLIC_ORIGIN': self.origin,
                                      'OPS_IDENTITY_URL': 'http://127.0.0.1:8787'})
        env.start(); self.addCleanup(env.stop)
        catalog = self.root / 'links.json'
        catalog.write_text(json.dumps([{'id': 'xhs', 'name': '小红书', 'url': 'https://d.58nft.net:9450/'}]))
        self.app = create_app(self.root, catalog)
        self.client = self.app.test_client()
        self.database = self.root / 'portal.sqlite3'
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO users VALUES(7,'tao',?,'user','active',1,0)", (password_hash('legacy-password'),))
            conn.execute('INSERT INTO identity_links VALUES(3,7)')
        self.verify = patch.object(IdentityClient, 'verify', side_effect=lambda token: {'id': 3, 'username': 'tao'} if token == 'central-session' else None).start()
        self.addCleanup(patch.stopall)

    def authenticated(self):
        self.client.set_cookie(COOKIE, 'central-session')

    def post(self, path, data=None):
        page = self.client.get('/settings')
        csrf = re.search(rb'name=csrf value="([^"]+)', page.data).group(1).decode()
        return self.client.post(path, data={**(data or {}), 'csrf': csrf}, headers={'Origin': self.origin})

    def test_central_cookie_authenticates_and_legacy_session_does_not(self):
        self.assertEqual(self.client.get('/').location, '/login')
        self.assertEqual(self.client.get('/login').location, 'https://d.58nft.net:9461/login?return=bifrost')
        self.client.set_cookie('portal_session', 'legacy-session')
        self.assertEqual(self.client.get('/').location, '/login')
        self.authenticated()
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/login').location, '/')
        self.assertEqual(self.client.get('/admin').status_code, 403)
        self.verify.side_effect = lambda token: None
        self.assertEqual(self.client.get('/').location, '/login')

    def test_unavailable_identity_fails_closed(self):
        self.authenticated()
        self.verify.side_effect = IdentityUnavailable
        self.assertEqual(self.client.get('/').status_code, 503)

    def test_session_state_tracks_central_identity_and_permissions_without_exposing_credentials(self):
        self.authenticated()
        response = self.client.get('/api/session-state')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        previous = response.json['stamp']
        self.assertEqual(set(response.json), {'authenticated', 'stamp'})
        self.verify.side_effect = lambda _: {'id': 3, 'username': 'renamed-tao', 'role': 'admin', 'status': 'active',
            'app_permissions': {'rift': {'state': 'available'}}}
        self.assertNotEqual(self.client.get('/api/session-state').json['stamp'], previous)
        self.verify.side_effect = lambda _: None
        self.assertEqual(self.client.get('/api/session-state').status_code, 401)
        self.assertEqual(self.client.get('/api/session-state').json, {'authenticated': False})

    def test_identity_ids_cannot_claim_legacy_username_privileges(self):
        self.authenticated()
        self.verify.side_effect = lambda token: {'id': 99, 'username': 'tao'}
        self.assertEqual(self.client.get('/').status_code, 403)
        self.verify.side_effect = lambda token: {'id': 99, 'username': 'new-user'}
        self.assertEqual(self.client.get('/').status_code, 200)
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(conn.execute("SELECT role FROM users WHERE username='new-user'").fetchone(), ('user',))
            conn.execute("UPDATE users SET status='disabled' WHERE username='new-user'")
        self.assertEqual(self.client.get('/').status_code, 403)

    def test_central_permissions_and_admin_role_are_the_single_authority(self):
        self.authenticated()
        self.verify.side_effect = lambda token: {'id': 3, 'username': 'tao', 'role': 'user', 'status': 'active',
            'app_permissions': {'xhs': {'state': 'pending', 'space': 'personal', 'policy': 'approval'}}}
        body = self.client.get('/').data.decode()
        self.assertIn('/permissions?app=xhs', body)
        self.assertIn('审批中', body)
        with sqlite3.connect(self.database) as conn:
            conn.execute("UPDATE users SET role='admin',status='disabled' WHERE id=7")
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/admin').status_code, 403)
        self.verify.side_effect = lambda token: {'id': 3, 'username': 'tao', 'role': 'admin', 'status': 'active'}
        with patch.object(IdentityClient, 'access', side_effect=lambda path, *args: {'users': [], 'requests': [], 'audit': []} if path == 'admin' else {'apps': []}):
            response = self.client.get('/admin')
            self.assertEqual(response.status_code, 200)
            self.assertIn('用户与权限', response.data.decode())
            self.assertNotIn('platform-select?view=admin', response.data.decode())

    def test_settings_changes_central_password_and_logout_revokes_central_session(self):
        self.authenticated()
        cookie = COOKIE + '=replacement; Secure; HttpOnly; Path=/; SameSite=strict'
        with patch.object(IdentityClient, 'call', return_value=({'status': 'ok'}, [cookie])) as call:
            response = self.post('/settings', {'old': 'current-password', 'new': 'new-password-123'})
            self.assertEqual(response.location, '/settings?changed=1')
            self.assertIn(cookie, response.headers.getlist('Set-Cookie'))
            call.assert_called_once_with('password', 'central-session', {'old_password': 'current-password', 'new_password': 'new-password-123'})
        self.authenticated()
        with patch.object(IdentityClient, 'call', return_value=({'status': 'ok'}, [])) as call:
            self.assertEqual(self.post('/logout').location, '/login')
            call.assert_called_once_with('logout', 'central-session', {})
        self.assertIsNone(self.client.get_cookie(COOKIE))

    def test_csrf_from_another_identity_and_foreign_origins_are_rejected(self):
        self.authenticated()
        page = self.client.get('/settings')
        csrf = re.search(rb'name=csrf value="([^"]+)', page.data).group(1).decode()
        self.client.set_cookie(COOKIE, 'another-session')
        with patch.object(IdentityClient, 'call') as call:
            self.assertEqual(self.client.post('/logout', data={'csrf': csrf}, headers={'Origin': self.origin}).status_code, 303)
            self.assertEqual(self.client.post('/logout', data={'csrf': csrf}, headers={'Origin': 'https://evil.example'}).status_code, 403)
            call.assert_not_called()

    def test_migration_retains_central_ids_passwords_and_local_roles(self):
        target = self.root / 'identity.sqlite3'
        with sqlite3.connect(target) as conn:
            conn.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,auth_version INTEGER,created_at TEXT)')
            conn.execute("INSERT INTO users VALUES(3,'tao','central-hash',4,'2026-10-23')")
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO users VALUES(8,'admin','legacy-admin-hash','admin','active',0,0)")
        self.assertEqual(migrate(self.database, target), 2)
        self.assertEqual(migrate(self.database, target), 2)
        with sqlite3.connect(target) as conn:
            self.assertEqual(conn.execute("SELECT id,password_hash,auth_version FROM users WHERE username='tao'").fetchone(), (3, 'central-hash', 4))
            self.assertEqual(conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone(), ('legacy-admin-hash',))
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(conn.execute("SELECT role FROM users WHERE id=8").fetchone(), ('admin',))
            self.assertEqual(conn.execute('SELECT user_id FROM identity_links WHERE identity_id=3').fetchone(), (7,))


class AdminDirectoryTests(unittest.TestCase):
    setUp = UnifiedIdentityTests.setUp
    authenticated = UnifiedIdentityTests.authenticated
    post = UnifiedIdentityTests.post

    def test_saved_password_view_requires_admin_and_returns_noncacheable_json(self):
        self.authenticated()
        self.verify.side_effect = lambda _: {'id': 3, 'username': 'tao', 'role': 'admin', 'status': 'active'}
        with patch.object(IdentityClient, 'access', return_value={'password': 'synthetic-view-only', 'expires_in': 30}) as access:
            response = self.post('/admin/accounts/2/password/reveal', {'revision': '4'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['password'], 'synthetic-view-only')
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
            access.assert_called_once_with('admin/users/2/password/reveal', 'central-session', {'revision': '4'}, 'POST')
            access.reset_mock()
            self.verify.side_effect = lambda _: {'id': 3, 'username': 'tao', 'role': 'user', 'status': 'active'}
            self.assertEqual(self.post('/admin/accounts/2/password/reveal', {'revision': '4'}).status_code, 403)
            access.assert_not_called()

    def test_separate_account_and_permission_forms_do_not_erase_grants(self):
        self.authenticated()
        self.verify.side_effect = lambda _: {'id': 3, 'username': 'tao', 'role': 'admin', 'status': 'active'}
        with patch.object(IdentityClient, 'access', return_value={'status': 'ok'}) as access:
            response = self.post('/admin/accounts/2', {'intent': 'account', 'role': 'user', 'status': 'active', 'revision': '4'})
            self.assertEqual(response.status_code, 303)
            access.assert_called_once_with('admin/users/2', 'central-session', {'role': 'user', 'status': 'active', 'revision': '4'}, 'POST')
            access.reset_mock()
            response = self.post('/admin/accounts/2', {'intent': 'permissions', 'apps': ['coffee-orders'], 'revision': '4'})
            self.assertEqual(response.status_code, 303)
            access.assert_called_once_with('admin/users/2', 'central-session', {'apps': ['coffee-orders'], 'revision': '4'}, 'POST')

    def test_deletion_uses_central_identity_csrf_and_username_confirmation(self):
        self.authenticated()
        self.verify.side_effect = lambda _: {'id': 3, 'username': 'tao', 'role': 'admin', 'status': 'active'}
        with patch.object(IdentityClient, 'access', return_value={'status': 'deleted'}) as access:
            response = self.post('/admin/accounts/2/delete', {'revision': '4', 'username': 'another'})
            self.assertEqual(response.status_code, 303)
            access.assert_called_once_with('admin/users/2', 'central-session', {'revision': '4', 'username': 'another'}, 'DELETE')
            access.reset_mock()
            response = self.client.post('/admin/accounts/2/delete', data={'revision': '4', 'username': 'another'}, headers={'Origin': self.origin})
            self.assertEqual(response.status_code, 303)
            access.assert_not_called()
            self.verify.side_effect = lambda _: {'id': 3, 'username': 'tao', 'role': 'user', 'status': 'active'}
            self.assertEqual(self.post('/admin/accounts/2/delete', {'revision': '4', 'username': 'another'}).status_code, 403)
            access.assert_not_called()


if __name__ == '__main__':
    unittest.main()
