import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "applications" / "weixin"


@unittest.skipUnless(shutil.which("go"), "Go toolchain not installed")
class GoPackageExampleTests(unittest.TestCase):
    def test_desktop_outputs_are_real_executables(self):
        with tempfile.TemporaryDirectory() as temp:
            windows = Path(temp) / "weixin.exe"
            mac = Path(temp) / "weixin.app.zip"
            for target, output in (("windows-x64", windows), ("macos-arm64", mac)):
                result = subprocess.run([sys.executable, "package_build.py", target, str(output)],
                                        cwd=EXAMPLE, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(windows.read_bytes()[:2], b"MZ")
            with zipfile.ZipFile(mac) as bundle:
                self.assertIn("Weixin.app/Contents/Info.plist", bundle.namelist())
                self.assertIn("Weixin.app/Contents/MacOS/weixin", bundle.namelist())
                self.assertEqual(bundle.read("Weixin.app/Contents/MacOS/weixin")[:4], b"\xcf\xfa\xed\xfe")


if __name__ == "__main__":
    unittest.main()
