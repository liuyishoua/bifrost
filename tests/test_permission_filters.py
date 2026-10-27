"""Permission filters render a complete, session-only list with accurate SSR visibility."""
import copy
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portal import create_app
from ops_identity import COOKIE, IdentityClient


def applications():
    return [dict(id=app_id, name=name, state=state, policy='approval', space='shared',
                 requestable=requestable, url='https://example.test/', reason='', review_note='')
            for app_id, name, state, requestable in [
                ('opened', '已开通应用', 'available', True),
                ('waiting', '审批中的应用', 'pending', True),
                ('locked', '小红书 Alpha', 'locked', True),
                ('rejected', '未通过应用', 'rejected', True),
                ('revoked', '已收回应用', 'revoked', True),
                ('independent', '独立登录', 'independent', False),
            ]]


class Cards(HTMLParser):
    def __init__(self, markup):
        super().__init__(); self.cards = {}; self.empty = None; self.feed(markup)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'data-access-id' in attrs:
            self.cards[attrs['data-access-id']] = attrs
        if 'data-permission-empty' in attrs:
            self.empty = attrs


class PermissionFilters(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); catalog = root / 'links.json'; catalog.write_text('[]')
        environment = patch.dict(os.environ, {'OPS_IDENTITY_URL': 'http://127.0.0.1:8787',
                                               'BIFROST_PUBLIC_ORIGIN': 'http://portal.test'})
        environment.start(); self.addCleanup(environment.stop)
        verify = patch.object(IdentityClient, 'verify', return_value={'id': 81, 'username': 'filter-test', 'role': 'user', 'status': 'active'})
        verify.start(); self.addCleanup(verify.stop)
        access = patch.object(IdentityClient, 'access', side_effect=lambda *args: {'apps': copy.deepcopy(applications())})
        self.access = access.start(); self.addCleanup(access.stop)
        self.client = create_app(root, catalog).test_client(); self.client.set_cookie(COOKIE, 'synthetic-permissions-session')

    def cards(self, query=''):
        response = self.client.get('/permissions' + query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return Cards(response.get_data(as_text=True))

    def test_every_filter_loads_all_cards_but_initial_visibility_matches_the_url(self):
        for state, expected in [('all', set(item['id'] for item in applications())),
                                ('available', {'opened'}), ('pending', {'waiting'}),
                                ('requestable', {'locked', 'rejected', 'revoked'})]:
            with self.subTest(state=state):
                cards = self.cards('?state=' + state)
                self.assertEqual(len(cards.cards), 6)
                self.assertEqual({key for key, attrs in cards.cards.items() if 'hidden' not in attrs}, expected)
                self.assertIn('hidden', cards.empty)
        self.assertEqual(self.access.call_count, 4)

    def test_search_and_state_intersect_and_empty_state_remains_available_for_local_updates(self):
        cards = self.cards('?state=requestable&q=aLPHa')
        self.assertEqual({key for key, attrs in cards.cards.items() if 'hidden' not in attrs}, {'locked'})
        cards = self.cards('?state=available&q=Alpha')
        self.assertTrue(all('hidden' in attrs for attrs in cards.cards.values()))
        self.assertNotIn('hidden', cards.empty)

    def test_unknown_filter_falls_back_and_requestability_does_not_include_pending(self):
        cards = self.cards('?state=unknown')
        self.assertTrue(all('hidden' not in attrs for attrs in cards.cards.values()))
        self.assertEqual(cards.cards['waiting']['data-permission-requestable'], 'false')
        self.assertEqual(cards.cards['locked']['data-permission-requestable'], 'true')

    def test_filter_markup_keeps_real_links_and_application_forms(self):
        body = self.client.get('/permissions?state=pending').get_data(as_text=True)
        self.assertIn('data-permission-filter="requestable"', body)
        self.assertIn('data-permission-refresh', body)
        self.assertIn('method="post" action="/permissions/apply/locked"', body)
        self.assertIn('name=csrf', body)
        self.assertIn('/static/permissions.js', body)


if __name__ == '__main__':
    unittest.main()
