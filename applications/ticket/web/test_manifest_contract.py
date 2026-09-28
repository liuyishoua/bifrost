import unittest

from .__main__ import activity_snapshot


class ManifestContractTests(unittest.TestCase):
    def test_activity_reports_tasks_and_account_operations(self):
        idle = {'tasks': [], 'accounts': []}
        self.assertEqual(activity_snapshot(idle), {'idle': True, 'reason': ''})
        active_task = {'tasks': [{'status': 'waiting'}], 'accounts': []}
        self.assertFalse(activity_snapshot(active_task)['idle'])
        active_login = {'tasks': [], 'accounts': [{'login': {'status': 'verifying'}}]}
        self.assertFalse(activity_snapshot(active_login)['idle'])
