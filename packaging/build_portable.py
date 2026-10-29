from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = ("PRTT.bat", "PRTT.sh", "README.md")


def _archive_directory(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for path in sorted(source.rglob("*")):
            relative = Path("PRTT") / path.relative_to(source)
            arcname = relative.as_posix()
            info = zipfile.ZipInfo(arcname + ("/" if path.is_dir() else ""))
            info.compress_type = zipfile.ZIP_DEFLATED
            if path.is_symlink():
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, os.readlink(path))
            elif path.is_dir():
                info.create_system = 3
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(info, b"")
            else:
                mode = 0o755 if os.access(path, os.X_OK) else 0o644
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.write(path, arcname)


def _platform_target(value: str) -> str:
    if value:
        return value
    machine = platform.machine().lower()
    machine = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    if sys.platform == "win32":
        return f"windows-{machine}"
    if sys.platform == "darwin":
        return f"macos-{machine}"
    return f"linux-{machine}"


def build(target: str) -> Path:
    output_dir = ROOT / "dist"
    output_dir.mkdir(exist_ok=True)
    output_zip = output_dir / f"PRTT-{target}.zip"

    with tempfile.TemporaryDirectory(prefix="prtt-portable-") as temp_name:
        temp = Path(temp_name)
        pyinstaller_dist = temp / "pyinstaller-dist"
        command = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "PRTT",
            "--distpath", str(pyinstaller_dist),
            "--workpath", str(temp / "work"),
            "--specpath", str(temp / "spec"),
            "--paths", str(ROOT),
            "--collect-all", "PIL",
            "--collect-all", "pygame",
            "--collect-all", "numpy",
            "--collect-all", "soundfile",
            "--collect-all", "_soundfile_data",
            str(ROOT / "prtt.py"),
        ]
        subprocess.run(command, cwd=ROOT, check=True)

        portable_dir = temp / "portable" / "PRTT"
        portable_dir.mkdir(parents=True)
        app_bundle = pyinstaller_dist / "PRTT.app"
        executable_bundle = pyinstaller_dist / "PRTT"
        if app_bundle.is_dir():
            shutil.copytree(app_bundle, portable_dir / "PRTT.app", symlinks=True)
        else:
            shutil.copytree(executable_bundle, portable_dir, dirs_exist_ok=True, symlinks=True)
        for source_name in SOURCE_FILES:
            shutil.copy2(ROOT / source_name, portable_dir / source_name)
        for source in ROOT.glob("*.py"):
            shutil.copy2(source, portable_dir / source.name)
        shutil.copy2(ROOT / "requirements-runtime.txt", portable_dir / "requirements-runtime.txt")

        unix_launcher = portable_dir / "PRTT.sh"
        if unix_launcher.exists():
            unix_launcher.chmod(unix_launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        _archive_directory(portable_dir, output_zip)
    return output_zip


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a portable PRTT ZIP for the current OS.")
    parser.add_argument("--target", default="", help="artifact label, e.g. windows-x86_64")
    args = parser.parse_args()
    target = _platform_target(args.target)
    result = build(target)
    print(f"Portable package created: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
