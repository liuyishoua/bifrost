"""Build the standalone Weixin demo for one desktop platform."""

import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import zipfile


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("windows-x64", "macos-arm64"):
        raise SystemExit("usage: package_build.py windows-x64|macos-arm64 OUTPUT")
    target, output = sys.argv[1], Path(sys.argv[2]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        windows = target == "windows-x64"
        executable = root / ("weixin.exe" if windows else "Weixin.app/Contents/MacOS/weixin")
        executable.parent.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, "GOOS": "windows" if windows else "darwin",
                       "GOARCH": "amd64" if windows else "arm64", "CGO_ENABLED": "0"}
        subprocess.run(["go", "build", "-o", str(executable), "."], cwd=Path(__file__).parent,
                       env=environment, check=True, timeout=120)
        if windows:
            output.write_bytes(executable.read_bytes())
        else:
            info = executable.parent.parent / "Info.plist"
            with info.open("wb") as stream:
                plistlib.dump({"CFBundleName": "Weixin", "CFBundleDisplayName": "Weixin",
                               "CFBundleIdentifier": "dev.bifrost.weixin",
                               "CFBundleExecutable": "weixin", "CFBundlePackageType": "APPL",
                               "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0",
                               "LSUIElement": True}, stream)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for path in sorted((root / "Weixin.app").rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(root))


if __name__ == "__main__":
    main()
