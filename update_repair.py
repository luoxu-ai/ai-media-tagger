from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


APP_EXE_NAME = "AI媒体标签工具.exe"
REPAIR_TARGET_ENV = "AI_MEDIA_TAGGER_REPAIR_TARGET"


def registry_install_locations() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    uninstall_key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    locations: list[Path] = []
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                uninstall_key,
                0,
                winreg.KEY_READ | view,
            ) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        subkey_name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, subkey_name) as entry:
                            display_name = str(
                                winreg.QueryValueEx(entry, "DisplayName")[0]
                            ).strip()
                            if display_name != "AI媒体标签工具":
                                continue
                            for value_name in (
                                "InstallLocation",
                                "DisplayIcon",
                                "UninstallString",
                            ):
                                try:
                                    raw_value = str(
                                        winreg.QueryValueEx(entry, value_name)[0]
                                    ).strip()
                                except OSError:
                                    continue
                                if value_name == "InstallLocation":
                                    location = Path(raw_value.strip('"'))
                                else:
                                    match = re.match(r'^"([^"]+\.exe)"|^([^ ]+\.exe)', raw_value)
                                    if match is None:
                                        continue
                                    location = Path(match.group(1) or match.group(2)).parent
                                locations.append(location)
                    except OSError:
                        continue
        except OSError:
            continue
    return locations


def bundled_payload() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / "payload" / APP_EXE_NAME


def install_candidates() -> list[Path]:
    candidates: list[Path] = []
    override = os.environ.get(REPAIR_TARGET_ENV, "").strip()
    if override:
        candidates.append(Path(override))

    executable_dir = Path(sys.executable).resolve().parent
    candidates.append(executable_dir / APP_EXE_NAME)

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Programs" / "AI媒体标签工具" / APP_EXE_NAME
        )
    candidates.extend(location / APP_EXE_NAME for location in registry_install_locations())
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        if root.exists():
            candidates.append(root / "AI媒体标签工具" / APP_EXE_NAME)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def find_installed_application() -> Path | None:
    return next((path for path in install_candidates() if path.is_file()), None)


def stop_running_application() -> None:
    subprocess.run(
        ["taskkill.exe", "/F", "/T", "/IM", APP_EXE_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(0.8)


def replace_application(target: Path, payload: Path) -> None:
    if not payload.is_file() or payload.stat().st_size < 1024 * 1024:
        raise RuntimeError("修复工具中的程序文件不完整。")
    if not target.is_file():
        raise RuntimeError("没有找到已安装的程序文件。")

    backup = target.with_name(f".{target.name}.update-backup")
    replacement = target.with_name(f".{target.name}.update-new")
    shutil.copy2(target, backup)
    try:
        shutil.copy2(payload, replacement)
        os.replace(replacement, target)
    except Exception:
        replacement.unlink(missing_ok=True)
        if backup.is_file():
            shutil.copy2(backup, target)
        raise


def launch_application(target: Path) -> None:
    subprocess.Popen(
        [str(target)],
        cwd=str(target.parent),
        close_fds=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )


def remove_backup(target: Path) -> None:
    target.with_name(f".{target.name}.update-backup").unlink(missing_ok=True)


def message(text: str, *, error: bool = False) -> None:
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(
        None, text, "AI 媒体标签工具更新修复", flags
    )


def main() -> int:
    target = find_installed_application()
    if target is None:
        message(
            "没有找到已安装的 AI 媒体标签工具。\n"
            "请确认软件已安装到当前 Windows 用户。",
            error=True,
        )
        return 1

    payload = bundled_payload()
    try:
        stop_running_application()
        replace_application(target, payload)
        launch_application(target)
        remove_backup(target)
    except Exception as exc:
        message(f"更新修复失败：{exc}\n原有日志、设置和模型未被删除。", error=True)
        return 1

    message(
        "在线更新功能修复完成，软件已重新打开。\n"
        "当前版本、日志、设置、模型和历史数据均已保留。\n"
        "是否升级新版本，请由您在软件中自行选择。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
