from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
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
    "AI-Media-Tagger-Setup.exe",
    "AI媒体标签工具安装程序.exe",
    "AI媒体标签工具.exe",
    "AI-Media-Tagger.exe",
    "AI.exe",
)
TRUSTED_RELEASE_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
USER_AGENT = "AI-Media-Tagger-Updater"
AUTOMATIC_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
UPDATE_INSTALL_GRACE_SECONDS = 15 * 60


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


def is_installable_signature_status(status: str) -> bool:
    """Accept valid signatures and explicitly unsigned official builds only."""
    return status in {"Valid", "NotSigned"}


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
        raise UpdateError("最新版本没有找到 Windows EXE。")
    digest = _sha256_from_digest(asset.get("digest"))
    if not digest:
        raise UpdateError("最新版本缺少 SHA256 校验信息，已停止更新。")
    version = str(payload.get("tag_name", "")).strip().lstrip("vV")
    url = str(asset.get("browser_download_url", "")).strip()
    if not version or not url:
        raise UpdateError("最新版本信息不完整。")
    parsed_url = urllib.parse.urlparse(url)
    if (
        parsed_url.scheme.casefold() != "https"
        or (parsed_url.hostname or "").casefold() not in TRUSTED_RELEASE_HOSTS
    ):
        raise UpdateError("更新文件不是来自可信的 GitHub HTTPS 地址，已停止更新。")
    return ReleaseInfo(
        version=version,
        notes=str(payload.get("body") or "本次版本包含功能优化和问题修复。"),
        download_url=url,
        sha256=digest,
        size=max(0, int(asset.get("size") or 0)),
        page_url=str(payload.get("html_url") or ""),
        asset_name=str(asset.get("name") or "AI媒体标签工具安装程序.exe"),
    )


def fetch_latest_release(timeout: float = 25.0, attempts: int = 2) -> ReleaseInfo:
    payload = None
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            with _request(LATEST_RELEASE_API, timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < max(1, attempts):
                time.sleep(0.6)
    if payload is None:
        raise UpdateError(
            "无法连接 GitHub 更新服务器。请检查网络或代理设置后重试；"
            "这不会影响软件的离线识别和标签处理。"
        ) from last_error
    if not isinstance(payload, dict):
        raise UpdateError("更新服务器返回了无效数据。")
    return release_from_payload(payload)


def update_storage_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "AI媒体标签工具" / "updates"


def update_install_marker_path() -> Path:
    return update_storage_directory() / "pending_install.json"


def record_update_install_started(target_version: str, current_version: str) -> None:
    directory = update_storage_directory()
    directory.mkdir(parents=True, exist_ok=True)
    marker = update_install_marker_path()
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "target_version": target_version,
                "previous_version": current_version,
                "started_at": time.time(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, marker)


def clear_update_install_marker() -> None:
    try:
        update_install_marker_path().unlink(missing_ok=True)
    except OSError:
        pass


def consume_update_startup_result(
    current_version: str,
    *,
    clear: bool = True,
    now: float | None = None,
) -> tuple[str, str] | None:
    """Return the completed/failed status after an installer restart.

    ``clear=False`` lets the UI keep the marker until the notification has
    actually been shown.  A newly created marker is also kept while the
    installer may still be replacing files, so a transient old process cannot
    incorrectly consume the result before the new version starts.
    """
    marker = update_install_marker_path()
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        target = str(payload.get("target_version") or "").strip()
        previous = str(payload.get("previous_version") or "").strip()
        started_at = float(payload.get("started_at") or 0.0)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, AttributeError):
        clear_update_install_marker()
        return None
    if not target:
        clear_update_install_marker()
        return None
    if is_newer_version(target, current_version):
        now = time.time() if now is None else now
        if started_at > 0 and now - started_at < UPDATE_INSTALL_GRACE_SECONDS:
            return None
        result = "failed", previous or current_version
    else:
        result = "completed", current_version
    if clear:
        clear_update_install_marker()
    return result


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
    environment = os.environ.copy()
    environment["AI_MEDIA_TAGGER_SIGNATURE_PATH"] = str(path)
    command = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "(Get-AuthenticodeSignature -LiteralPath "
        "$env:AI_MEDIA_TAGGER_SIGNATURE_PATH).Status"
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            env=environment,
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


def launch_installer(
    downloaded: Path,
    install_directory: Path | None = None,
) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    try:
        arguments = [
            str(downloaded),
            "/SP-",
            "/SILENT",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
            "/CURRENTUSER",
        ]
        if install_directory is not None:
            arguments.append(f"/DIR={Path(install_directory).resolve()}")
        subprocess.Popen(
            arguments,
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        raise UpdateError(f"无法启动安装程序：{exc}") from exc
