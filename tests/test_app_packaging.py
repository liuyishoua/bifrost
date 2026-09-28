from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml

from app_registry import AppRegistry
from app_packaging import PackageManager, PackageError


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps = self.root / "applications"
        self.app_dir = self.apps / "sample"
        self.app_dir.mkdir(parents=True)
        (self.app_dir / "app.yaml").write_text(
            "schema: 1\nid: sample\nname: Sample\nstart: [bin/app, '${PORT}']\n")
        self.source = self.app_dir / "source.txt"
        self.source.write_text("first")
        self.runtime = self.root / "runtime"
        self.registry = AppRegistry(self.apps, self.runtime)
        self.registry.refresh()
        self.manager = PackageManager(self.runtime, self.registry)

    def recipe(self, output=".bifrost/packages/windows-x64/sample.exe", fail=False):
        script = ("import pathlib,sys; h=bytearray(128); h[:2]=b'MZ'; "
                  "h[60:64]=(64).to_bytes(4,'little'); h[64:68]=b'PE\\0\\0'; "
                  "pathlib.Path(sys.argv[1]).write_bytes(h+pathlib.Path('source.txt').read_bytes())")
        if fail:
            script = "import sys; sys.exit(7)"
        (self.app_dir / "package.yaml").write_text(yaml.safe_dump({
            "schema": 1,
            "targets": {"windows-x64": {"artifact": output,
                                        "build": [sys.executable, "-c", script, "${OUTPUT}"]}},
        }))

    def test_build_then_reuse_matching_artifact_and_rebuild_after_source_change(self):
        self.recipe()
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "missing")
        self.manager.build("sample", "windows-x64")
        artifact = self.manager.artifact("sample", "windows-x64")
        self.assertTrue(artifact.read_bytes().endswith(b"first"))
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "ready")
        self.assertEqual(self.manager.build("sample", "windows-x64"), "reused")
        self.source.write_text("second")
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "missing")
        self.assertEqual(self.manager.build("sample", "windows-x64"), "built")
        self.assertTrue(self.manager.artifact("sample", "windows-x64").read_bytes().endswith(b"second"))

    def test_ready_artifact_can_be_rebuilt_on_request(self):
        self.recipe()
        self.manager.build("sample", "windows-x64")
        status = self.manager.status("sample", "windows-x64")
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["can_build"])
        self.assertEqual(self.manager.build("sample", "windows-x64", force=True), "built")
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "ready")

    def test_unconfigured_mobile_target_is_not_claimed_as_buildable(self):
        self.recipe()
        self.assertEqual(self.manager.status("sample", "android-arm64")["state"], "unavailable")
        with self.assertRaises(PackageError):
            self.manager.build("sample", "android-arm64")

    def test_declared_intel_mac_bundle_is_downloadable(self):
        relative = ".bifrost/packages/macos-x64/sample.app.zip"
        (self.app_dir / "package.yaml").write_text(yaml.safe_dump({
            "schema": 1, "targets": {"macos-x64": {"artifact": relative}},
        }))
        artifact = self.app_dir / relative
        artifact.parent.mkdir(parents=True)
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("Sample.app/Contents/Info.plist", "plist")
            archive.writestr("Sample.app/Contents/MacOS/Sample", "binary")
        self.assertEqual(self.manager.status("sample", "macos-x64")["state"], "ready")
        self.assertEqual(self.manager.artifact("sample", "macos-x64"), artifact.resolve())

    def test_declared_prebuilt_artifact_is_available_without_build_command(self):
        relative = ".bifrost/packages/windows-x64/sample.exe"
        (self.app_dir / "package.yaml").write_text(yaml.safe_dump({
            "schema": 1, "targets": {"windows-x64": {"artifact": relative}},
        }))
        artifact = self.app_dir / relative
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(self.pe_stub())
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "ready")
        self.assertEqual(self.manager.build("sample", "windows-x64"), "reused")
        self.assertEqual(self.manager.artifact("sample", "windows-x64").read_bytes(), self.pe_stub())

    @staticmethod
    def pe_stub():
        header = bytearray(128)
        header[:2] = b"MZ"
        header[60:64] = (64).to_bytes(4, "little")
        header[64:68] = b"PE\0\0"
        return bytes(header)

    def test_empty_prebuilt_file_is_not_downloadable(self):
        relative = ".bifrost/packages/android-arm64/sample.apk"
        (self.app_dir / "package.yaml").write_text(yaml.safe_dump({
            "schema": 1, "targets": {"android-arm64": {"artifact": relative}},
        }))
        artifact = self.app_dir / relative
        artifact.parent.mkdir(parents=True)
        artifact.touch()
        self.assertNotEqual(self.manager.status("sample", "android-arm64")["state"], "ready")
        with self.assertRaises(PackageError):
            self.manager.artifact("sample", "android-arm64")

    def test_untracked_partial_build_is_not_downloadable(self):
        self.recipe()
        artifact = self.app_dir / ".bifrost/packages/windows-x64/sample.exe"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"partial")
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "missing")
        with self.assertRaises(PackageError):
            self.manager.artifact("sample", "windows-x64")

    def test_host_requirement_blocks_build_but_allows_declared_prebuilt_file(self):
        relative = ".bifrost/packages/windows-x64/sample.exe"
        (self.app_dir / "package.yaml").write_text(yaml.safe_dump({
            "schema": 1,
            "targets": {"windows-x64": {"artifact": relative, "build_host": "windows-x64"}},
        }))
        with patch("app_packaging.current_host", return_value="macos-arm64"):
            self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "unavailable")
            artifact = self.app_dir / relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(self.pe_stub())
            self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "ready")

    def test_symlinked_build_directory_cannot_target_business_data(self):
        data_dir = self.app_dir / "datas"
        data_dir.mkdir()
        build_base = self.app_dir / ".bifrost"
        build_base.mkdir()
        (build_base / "packages").symlink_to(data_dir, target_is_directory=True)
        self.recipe()
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "invalid")
        with self.assertRaises(PackageError):
            self.manager.build("sample", "windows-x64")
        self.assertEqual(list(data_dir.iterdir()), [])

    def test_recipe_cannot_write_outside_package_directory(self):
        self.recipe("../../outside.exe")
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "invalid")
        with self.assertRaises(PackageError):
            self.manager.build("sample", "windows-x64")
        self.assertFalse((self.root / "outside.exe").exists())

    def test_failed_build_has_no_downloadable_stale_artifact(self):
        self.recipe()
        self.manager.build("sample", "windows-x64")
        self.recipe(fail=True)
        with self.assertRaises(PackageError):
            self.manager.build("sample", "windows-x64")
        self.assertEqual(self.manager.status("sample", "windows-x64")["state"], "failed")
        with self.assertRaises(PackageError):
            self.manager.artifact("sample", "windows-x64")


if __name__ == "__main__":
    unittest.main()
