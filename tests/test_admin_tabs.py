"""Admin panels share one authenticated document and keep native form fallbacks."""
import copy
from html.parser import HTMLParser
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portal import create_app
from ops_identity import COOKIE, IdentityClient, IdentityRejected


def directory_data():
    members = [dict(id=user_id, username=name, role=role, status=status,
                    is_superadmin=user_id == 1, can_manage=user_id != 1,
                    apps=[], revision=2, password_available=True)
               for user_id, name, role, status in [
                   (1, 'preview-owner', 'admin', 'active'),
                   (2, 'preview-member', 'user', 'active'),
                   (3, 'preview-disabled', 'user', 'disabled')]]
    pending = [dict(user_id=member['id'], username=member['username'], app_id='ticket',
                    name='抢票工作台', space='shared', reason='需要协作', status='pending',
                    user_status=member['status'], can_review=True) for member in members[1:]]
    audit = [dict(created_at='2026-10-27 01:00:00', actor_name='preview-owner',
                  username='preview-member', action='request', app_id='ticket', note='需要协作')]
    return {'users': members, 'requests': pending, 'audit': audit}


def catalog_data():
    return [dict(id='ticket', name='抢票工作台', policy='approval', space='shared'),
            dict(id='rift', name='虹域', policy='basic', space='personal')]


class Panels(HTMLParser):
    def __init__(self, markup):
        super().__init__(); self.panels = {}; self.feed(markup)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'data-admin-panel' in attrs:
            self.panels[attrs['data-admin-panel']] = attrs


class AdminTabs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); catalog = root / 'links.json'; catalog.write_text('[]')
        env = patch.dict(os.environ, {'OPS_IDENTITY_URL': 'http://127.0.0.1:8787',
                                      'BIFROST_PUBLIC_ORIGIN': 'http://portal.test'})
        env.start(); self.addCleanup(env.stop)
        self.actor = {'id': 1, 'username': 'preview-owner', 'role': 'admin',
                      'status': 'active', 'is_superadmin': True}
        verify = patch.object(IdentityClient, 'verify', side_effect=lambda session: self.actor if session else None)
        verify.start(); self.addCleanup(verify.stop)
        access = patch.object(IdentityClient, 'access', side_effect=lambda path, *args:
                              copy.deepcopy(directory_data() if path == 'admin' else {'apps': catalog_data()}))
        self.access = access.start(); self.addCleanup(access.stop)
        self.client = create_app(root, catalog).test_client()
        self.client.set_cookie(COOKIE, 'synthetic-admin-session')

    def test_every_url_renders_all_panels_with_only_the_selected_one_visible(self):
        for tab, selected in [('users', 'users'), ('requests', 'requests'), ('audit', 'audit'), ('unknown', 'users')]:
            with self.subTest(tab=tab):
                self.access.reset_mock()
                response = self.client.get('/admin?tab=' + tab)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                markup = response.get_data(as_text=True)
                panels = Panels(markup).panels
                self.assertEqual(set(panels), {'users', 'requests', 'audit'})
                self.assertEqual([key for key, attrs in panels.items() if 'hidden' not in attrs], [selected])
                self.assertEqual([call.args[0] for call in self.access.call_args_list], ['admin', 'me'])
                self.assertEqual(markup.count('<script src="/static/admin-users.js'), 1)
                self.assertEqual(markup.count('<script src="/static/admin-tabs.js'), 1)

    def test_admin_data_remains_unavailable_to_ordinary_and_unauthenticated_users(self):
        self.actor.update(role='user', is_superadmin=False)
        for tab in ('users', 'requests', 'audit'):
            self.assertEqual(self.client.get('/admin?tab=' + tab).status_code, 403)
        self.access.assert_not_called()
        self.client.delete_cookie(COOKIE)
        self.assertEqual(self.client.get('/admin').status_code, 302)
        self.access.assert_not_called()

    def test_native_links_and_all_existing_post_forms_remain_available(self):
        markup = self.client.get('/admin?tab=audit').get_data(as_text=True)
        for path in ('/admin?tab=requests', '/admin?tab=audit', '/admin/accounts/2', '/admin/requests/2/ticket'):
            self.assertIn('"' + path + '"', markup)
        self.assertIn('name=csrf', markup)
        self.assertIn('name="decision" value="approved"', markup)
        self.assertIn('name="decision" value="rejected"', markup)
        self.assertIn('data-admin-refresh', markup)

    def test_rejected_save_still_renders_switchable_panels_and_visible_error(self):
        response = self.client.get('/admin')
        import re
        csrf = re.search(rb'name=csrf value="([^"]+)', response.data).group(1).decode()
        original = self.access.side_effect
        self.access.side_effect = lambda path, token, data=None, method='GET': (
            original(path) if method == 'GET' else self.reject_stale())
        response = self.client.post('/admin/accounts/2', data={'csrf': csrf, 'intent': 'permissions', 'revision': '1'},
                                    headers={'Origin': 'http://portal.test'})
        self.assertEqual(response.status_code, 409)
        markup = response.get_data(as_text=True)
        self.assertIn('版本已更新', markup)
        self.assertIn('role="alert"', markup)
        self.assertEqual(len(Panels(markup).panels), 3)

    @staticmethod
    def reject_stale():
        raise IdentityRejected(409, '版本已更新，请刷新后重试。')


if __name__ == '__main__':
    unittest.main()
