import os
import gc
from pathlib import Path
import shutil
import tempfile
import unittest
from urllib.request import urlopen
import warnings

from app_registry import AppRegistry
from service_control import Controller


class ManifestExampleTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("go"), "Go toolchain unavailable")
    def test_go_app_builds_runs_normal_paths_and_stops(self):
        source = Path(__file__).resolve().parents[1] / "examples" / "weixin-go"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "applications" / "weixin"
            shutil.copytree(source, target)
            runtime = root / "runtime"
            registry = AppRegistry(target.parent, runtime)
            registry.refresh()
            app = registry.get("weixin")
            self.assertIsNotNone(app, registry.errors)
            controller = Controller(runtime, registry)
            old_cache = os.environ.get("GOCACHE")
            os.environ["GOCACHE"] = str(root / "go-cache")
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always", ResourceWarning)
                    self.assertEqual(controller.operate("weixin", "start"), "running")
                    try:
                        for path, expected in (("/", b"Weixin demo"),
                                               ("/api/hello", b"hello"),
                                               ("/static/app.css", b"background")):
                            with urlopen(f"http://127.0.0.1:{app.port}{path}", timeout=2) as response:
                                self.assertIn(expected, response.read())
                    finally:
                        self.assertEqual(controller.operate("weixin", "stop"), "stopped")
                    gc.collect()
                self.assertFalse([warning for warning in caught if issubclass(warning.category, ResourceWarning)])
            finally:
                if old_cache is None:
                    os.environ.pop("GOCACHE", None)
                else:
                    os.environ["GOCACHE"] = old_cache


if __name__ == "__main__":
    unittest.main()
