"""Build a standalone Python web application for the current desktop OS."""

from pathlib import Path
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parent
APPLICATIONS = {
    "ticket": ("requirements.txt", "Ticket"),
    "douyin": ("requirements-web.txt", "Douyin"),
}


def run(command, **kwargs):
    subprocess.run(command, check=True, timeout=900, **kwargs)


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in APPLICATIONS or sys.argv[2] not in ("macos-arm64", "macos-x64", "windows-x64"):
        raise SystemExit("usage: python_desktop_build.py ticket|douyin macos-arm64|macos-x64|windows-x64 OUTPUT")
    app_id, target, output = sys.argv[1], sys.argv[2], Path(sys.argv[3]).resolve()
    mac = target.startswith("macos-")
    if mac and platform.system() != "Darwin":
        raise SystemExit("此配方需要 macOS 构建环境")
    if target == "macos-arm64" and platform.machine() != "arm64":
        raise SystemExit("此配方需要 macOS Apple 芯片构建环境")
    if not mac and (platform.system() != "Windows" or platform.machine().lower() not in ("amd64", "x86_64")):
        raise SystemExit("此配方需要 Windows x64 构建环境")
    app_dir = ROOT / "applications" / app_id
    requirements, name = APPLICATIONS[app_id]
    environment = ROOT / ".runtime" / "package-envs" / (app_id if target != "macos-x64" else app_id + "-macos-x64")
    python = environment / ("bin/python" if mac else "Scripts/python.exe")
    if not python.is_file():
        if mac:
            if target == "macos-x64":
                uv = shutil.which("uv")
                if uv is None:
                    raise SystemExit("缺少 uv，无法定位 macOS x64 Python 3.12")
                found = subprocess.run([uv, "python", "find", "--managed-python", "--no-python-downloads",
                                        "cpython-3.12-macos-x86_64-none"], text=True, capture_output=True, check=False)
                if found.returncode:
                    raise SystemExit("缺少 macOS x64 Python 3.12 或 Rosetta")
                interpreter = found.stdout.strip()
                architecture = subprocess.run([interpreter, "-c", "import platform; print(platform.machine())"],
                                              text=True, capture_output=True, check=False)
                if architecture.returncode or architecture.stdout.strip() != "x86_64":
                    raise SystemExit("macOS x64 Python 无法运行，请安装 Rosetta")
            else:
                interpreter = shutil.which("python3.12")
                if interpreter is None:
                    raise SystemExit("缺少 Python 3.12 构建环境")
            run([interpreter, "-m", "venv", str(environment)])
        else:
            launcher = shutil.which("py")
            if launcher is None:
                raise SystemExit("缺少 Windows Python 3.12 启动器")
            run([launcher, "-3.12", "-m", "venv", str(environment)])
    dependencies = [str(python), "-m", "pip", "install", "--only-binary=:all:",
                    "-r", str(app_dir / requirements), "pyinstaller>=6.10,<7"]
    if target == "macos-x64" and app_id == "douyin":
        dependencies.append("cryptography<49")
    run(dependencies)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        command = [str(python), "-m", "PyInstaller", "--noconfirm", "--clean",
                   "--onedir" if mac else "--onefile",
                   "--windowed", "--name", name, "--paths", str(app_dir),
                   "--add-data", f"{app_dir / 'web' / 'static'}{os.pathsep}web/static",
                   "--distpath", str(temp / "dist"), "--workpath", str(temp / "build"),
                   "--specpath", str(temp / "spec")]
        if target == "macos-x64":
            command += ["--target-arch", "x86_64"]
        if app_id == "ticket":
            command += ["--add-data", f"{app_dir / 'assets'}{os.pathsep}assets"]
        if app_id == "douyin":
            command += ["--collect-all", "curl_cffi"]
        command.append(str(app_dir / "desktop.py"))
        build_env = os.environ.copy()
        if target == "macos-x64":
            developer_tools = Path("/Library/Developer/CommandLineTools/usr/bin")
            build_env["PATH"] = str(developer_tools) + os.pathsep + build_env.get("PATH", "")
        run(command, cwd=app_dir, env=build_env)
        if not mac:
            binary = temp / "dist" / f"{name}.exe"
            if not binary.is_file():
                raise RuntimeError("PyInstaller 未生成 Windows 程序")
            shutil.copy2(binary, output)
            return
        bundle = temp / "dist" / f"{name}.app"
        if not bundle.is_dir():
            raise RuntimeError("PyInstaller 未生成 macOS 应用")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_symlink():
                    entry = zipfile.ZipInfo(path.relative_to(bundle.parent).as_posix())
                    entry.create_system = 3
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(entry, os.readlink(path))
                elif path.is_file():
                    archive.write(path, path.relative_to(bundle.parent))


if __name__ == "__main__":
    main()
