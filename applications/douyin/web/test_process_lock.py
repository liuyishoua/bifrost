import tempfile
import unittest
from pathlib import Path

from .process_lock import acquire_exclusive


class ProcessLockTests(unittest.TestCase):
    def test_second_handle_cannot_use_same_data_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "server.lock"
            with path.open("w+") as first, path.open("r+") as second:
                acquire_exclusive(first)
                with self.assertRaises(OSError):
                    acquire_exclusive(second)


if __name__ == "__main__":
    unittest.main()
