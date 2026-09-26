import tempfile
import unittest
from unittest.mock import Mock, patch
import psutil

from service_control import Controller, ServiceError


class ServiceControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = Controller(self.temp.name)

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


if __name__ == "__main__":
    unittest.main()
