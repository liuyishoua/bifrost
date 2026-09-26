"""Origin checks for standalone and Bifrost-mounted requests."""
import os
import tempfile
import unittest
from unittest.mock import patch

from .app import create_app
from .douyin import DemoAdapter


class OriginTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.environ = patch.dict(os.environ, {
            'ANYDOOR_PUBLIC_ORIGIN': 'http://127.0.0.1:8080',
            'ANYDOOR_VERIFY_ORIGIN': 'http://127.0.0.1:8081',
        })
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.app = create_app(self.data.name, adapter=DemoAdapter(), background=False)
        self.addCleanup(self.app.extensions['console'].close)
        self.client = self.app.test_client()

    def post_account(self, origin, *, prefix='/douyin', remote='127.0.0.1'):
        headers = {'X-App-Request': '1'}
        if origin is not None:
            headers['Origin'] = origin
        if prefix is not None:
            headers['X-Forwarded-Prefix'] = prefix
        return self.client.post('/api/accounts', json={'name': '测试账号'}, headers=headers,
                                base_url='http://127.0.0.1:8766',
                                environ_overrides={'REMOTE_ADDR': remote})

    def test_mounted_account_write_accepts_only_portal_origin(self):
        self.assertEqual(self.post_account('http://127.0.0.1:8080').status_code, 200)
        self.assertEqual(self.post_account('https://other.example').status_code, 403)
        self.assertEqual(self.post_account(None).status_code, 403)
        self.assertEqual(self.post_account('http://127.0.0.1:8080', remote='192.0.2.1').status_code, 403)

    def test_standalone_origin_still_works(self):
        self.assertEqual(self.post_account('http://127.0.0.1:8766', prefix=None).status_code, 200)
        self.assertEqual(self.post_account('http://127.0.0.1:8080', prefix=None).status_code, 403)

    def test_verification_port_accepts_its_configured_origin(self):
        headers = {'Origin': 'http://127.0.0.1:8081', 'Sec-Fetch-Site': 'same-origin'}
        response = self.client.post('/verification-request/test/unknown', headers=headers,
                                    base_url='http://verification.localhost',
                                    environ_overrides={'REMOTE_ADDR': '127.0.0.1'})
        self.assertEqual(response.status_code, 404)
        headers['Origin'] = 'https://other.example'
        response = self.client.post('/verification-request/test/unknown', headers=headers,
                                    base_url='http://verification.localhost',
                                    environ_overrides={'REMOTE_ADDR': '127.0.0.1'})
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
