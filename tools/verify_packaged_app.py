"""Smoke-test the actual Windows EXE using synthetic, disposable media only."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image


def verify(exe: Path, workdir: Path) -> None:
    exe = exe.resolve(strict=True)
    workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="packaged-smoke-", dir=workdir) as directory:
        root = Path(directory)
        # ExifTool's Windows Perl launcher uses the system ANSI code page for
        # its support-directory path. English CI runners cannot open the
        # Chinese build-output name. Test an unchanged copy from RUNNER_TEMP,
        # whose path is ASCII, rather than depending on the runner's locale.
        application = root / "app"
        shutil.copytree(exe.parent, application)
        test_exe = application / "app.exe"
        shutil.copy2(application / exe.name, test_exe)
        source = root / "blank.jpg"
        Image.new("RGB", (1000, 1000), "white").save(source)
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        for smart in (False, True):
            name = "smart" if smart else "export"
            report = root / f"{name}.txt"
            output = root / name
            environment = os.environ.copy()
            environment.update({
                "AI_TAG_SELFTEST_SOURCE": str(source),
                "AI_TAG_SELFTEST_TARGET": str(output),
                "AI_TAG_SELFTEST_RESULT": str(report),
                "AI_TAG_SELFTEST_SMART": "1" if smart else "0",
                "TEMP": str(root), "TMP": str(root),
            })
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            completed = subprocess.run(
                [str(test_exe)], cwd=application, env=environment, timeout=90,
                creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup,
            )
            if completed.returncode != 0 or not report.is_file():
                raise RuntimeError(f"Packaged EXE startup failed: {name}")
            result = dict(line.split("=", 1) for line in
                          report.read_text(encoding="utf-8").splitlines() if "=" in line)
            if result.get("success") != "True":
                raise RuntimeError(f"Packaged self-test failed: {result}")
            if result.get("source_unchanged") != "True" or hashlib.sha256(source.read_bytes()).hexdigest() != original_hash:
                raise RuntimeError("Packaged self-test changed the original media")
            exported = result.get("exported", "None")
            if smart:
                if result.get("detected") != "False" or exported != "None":
                    raise RuntimeError("Blank image was incorrectly accepted")
            elif exported == "None" or not Path(exported).is_file():
                raise RuntimeError("Verified tagged copy is missing")
            print(f"Packaged {name} smoke test passed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    arguments = parser.parse_args()
    verify(arguments.exe, arguments.workdir)
