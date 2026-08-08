from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


GITHUB_REPOSITORY = "luoxu-ai/ai-media-tagger"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
RELEASE_ASSET_NAMES = (
    "AI媒体标签工具安装程序.exe",
    "AI-Media-Tagger-Setup.exe",
    "AI媒体标签工具.exe",
    "AI-Media-Tagger.exe",
    "AI.exe",
)
USER_AGENT = "AI-Media-Tagger-Updater"
AUTOMATIC_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
LEGACY_UNSIGNED_UPDATE_ENV = "AI_TAG_ALLOW_UNSIGNED_UPDATE"


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


def is_installable_signature_status(status: str) -> bool:
    """Accept valid signatures and explicitly unsigned official builds only."""
    return status in {"Valid", "NotSigned"}


def clear_legacy_unsigned_update_override() -> None:
    """Remove the one-time compatibility flag used by v1.2.1 clients."""
    os.environ.pop(LEGACY_UNSIGNED_UPDATE_ENV, None)
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, LEGACY_UNSIGNED_UPDATE_ENV)
    except (FileNotFoundError, OSError):
        pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    notes: str
    download_url: str
    sha256: str
    size: int
    page_url: str
    asset_name: str


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)*", value or "")
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_parts = version_tuple(candidate)
    current_parts = version_tuple(current)
    if not candidate_parts or not current_parts:
        return False
    width = max(len(candidate_parts), len(current_parts))
    return candidate_parts + (0,) * (width - len(candidate_parts)) > (
        current_parts + (0,) * (width - len(current_parts))
    )


def _request(url: str, timeout: float) -> urllib.request.addinfourl:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _sha256_from_digest(value: object) -> str:
    if not isinstance(value, str):
        return ""
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", value.strip())
    return match.group(1).lower() if match else ""


def release_from_payload(payload: dict) -> ReleaseInfo:
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("最新版本没有可下载的程序文件。")
    by_name = {
        str(asset.get("name", "")): asset
        for asset in assets
        if isinstance(asset, dict)
    }
    asset = next(
        (by_name[name] for name in RELEASE_ASSET_NAMES if name in by_name),
        None,
    )
    if asset is None:
        asset = next(
            (
                item
                for item in assets
                if isinstance(item, dict)
                and str(item.get("name", "")).casefold().endswith(".exe")
            ),
            None,
        )
    if asset is None:
        raise UpdateError("最新版本没有找到 Windows EXE。")
    digest = _sha256_from_digest(asset.get("digest"))
    if not digest:
        raise UpdateError("最新版本缺少 SHA256 校验信息，已停止更新。")
    version = str(payload.get("tag_name", "")).strip().lstrip("vV")
    url = str(asset.get("browser_download_url", "")).strip()
    if not version or not url:
        raise UpdateError("最新版本信息不完整。")
    return ReleaseInfo(
        version=version,
        notes=str(payload.get("body") or "本次版本包含功能优化和问题修复。"),
        download_url=url,
        sha256=digest,
        size=max(0, int(asset.get("size") or 0)),
        page_url=str(payload.get("html_url") or ""),
        asset_name=str(asset.get("name") or "AI媒体标签工具安装程序.exe"),
    )


def fetch_latest_release(timeout: float = 10.0) -> ReleaseInfo:
    try:
        with _request(LATEST_RELEASE_API, timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise UpdateError(f"暂时无法连接更新服务器：{exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateError("更新服务器返回了无效数据。")
    return release_from_payload(payload)


def update_storage_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "AI媒体标签工具" / "updates"


def should_check_automatically(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    state_path = update_storage_directory() / "update_state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        last_check = float(payload.get("last_successful_check", 0.0))
    except (OSError, ValueError, TypeError, AttributeError):
        return True
    return now - last_check >= AUTOMATIC_CHECK_INTERVAL_SECONDS


def record_successful_check(now: float | None = None) -> None:
    now = time.time() if now is None else now
    directory = update_storage_directory()
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "update_state.json"
    temporary = directory / "update_state.tmp"
    temporary.write_text(
        json.dumps({"last_successful_check": now}), encoding="utf-8"
    )
    os.replace(temporary, state_path)


def download_release(
    release: ReleaseInfo,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = 30.0,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    directory = update_storage_directory()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "AI媒体标签工具安装程序.new.exe"
    temporary = directory / "AI媒体标签工具安装程序.download"
    hasher = hashlib.sha256()
    completed = 0
    try:
        with _request(release.download_url, timeout) as response, temporary.open("wb") as output:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            while True:
                if cancelled is not None and cancelled():
                    raise UpdateCancelled("已取消更新。")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                hasher.update(chunk)
                completed += len(chunk)
                if progress is not None:
                    progress(completed, total)
        if cancelled is not None and cancelled():
            raise UpdateCancelled("已取消更新。")
        actual = hasher.hexdigest().lower()
        if actual != release.sha256.lower():
            raise UpdateError("新版文件 SHA256 校验失败，已删除下载文件。")
        os.replace(temporary, destination)
        return destination
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def authenticode_status(path: Path) -> str:
    if os.name != "nt":
        return "Unsupported"
    command = (
        "$signature = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "Write-Output $signature.Status"
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return "UnknownError"
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "UnknownError"


def launch_installer(downloaded: Path) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    try:
        subprocess.Popen(
            [
                str(downloaded),
                "/SP-",
                "/SILENT",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
                "/CURRENTUSER",
            ],
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        raise UpdateError(f"无法启动安装程序：{exc}") from exc
