import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from apps import App
from service_control import Controller, ServiceError


class ContractTests(unittest.TestCase):
    def test_optional_http_status_protocol_works_for_any_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = App("sample", "示例", "", "/", 10000, root, root / ".runtime",
                      "/ready", (str(root / "server"), "${PORT}"),
                      activity_path="/activity")
            controller = Controller(root, Mock())
            with patch("service_control.read_json", side_effect=[{"ready": True}, {"idle": True}]):
                self.assertTrue(controller._ready(app))
                controller._idle(app)
            with patch("service_control.read_json", return_value={"idle": False, "reason": "正在工作"}):
                with self.assertRaisesRegex(ServiceError, "正在工作"):
                    controller._idle(app)
            with patch("service_control.read_json", side_effect=ServiceError("状态不可读")):
                self.assertFalse(controller._ready(app))
                with self.assertRaisesRegex(ServiceError, "状态不可读"):
                    controller._idle(app)


if __name__ == "__main__":
    unittest.main()
