import hashlib
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from update_manager import (
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
    authenticode_status,
    download_release,
    consume_update_startup_result,
    is_installable_signature_status,
    is_newer_version,
    release_from_payload,
    record_successful_check,
    record_update_install_started,
    should_check_automatically,
    version_tuple,
)


def test_version_comparison_handles_v_prefix_and_missing_patch():
    assert version_tuple("v1.2.3") == (1, 2, 3)
    assert is_newer_version("v1.2.0", "1.1.9")
    assert not is_newer_version("1.2", "1.2.0")
    assert not is_newer_version("invalid", "1.2.0")


def test_only_valid_or_explicitly_unsigned_installers_can_continue():
    assert is_installable_signature_status("Valid")
    assert is_installable_signature_status("NotSigned")
    assert not is_installable_signature_status("HashMismatch")
    assert not is_installable_signature_status("NotTrusted")
    assert not is_installable_signature_status("UnknownError")


def test_authenticode_status_passes_unicode_path_through_environment(monkeypatch):
    target = Path(r"C:\测试目录\AI媒体标签工具安装程序.exe")
    captured = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["environment"] = kwargs["env"]
        return SimpleNamespace(stdout="NotSigned\n")

    monkeypatch.setattr("update_manager.os.name", "nt")
    monkeypatch.setattr("update_manager.subprocess.run", fake_run)

    assert authenticode_status(target) == "NotSigned"
    assert str(target) not in captured["arguments"]
    assert captured["environment"]["AI_MEDIA_TAGGER_SIGNATURE_PATH"] == str(target)


def test_release_payload_prefers_the_official_installer_asset():
    digest = "a" * 64
    release = release_from_payload(
        {
            "tag_name": "v1.2.0",
            "body": "更新说明",
            "html_url": "https://github.com/luoxu-ai/ai-media-tagger/releases/tag/v1.2.0",
            "assets": [
                {
                    "name": "other.exe",
                    "browser_download_url": "https://github.com/luoxu-ai/ai-media-tagger/releases/download/v1.2.0/other.exe",
                    "digest": f"sha256:{'b' * 64}",
                    "size": 1,
                },
                {
                    "name": "AI媒体标签工具.exe",
                    "browser_download_url": "https://github.com/luoxu-ai/ai-media-tagger/releases/download/v1.2.0/AI-Media-Tagger.exe",
                    "digest": f"sha256:{digest}",
                    "size": 203,
                },
                {
                    "name": "AI媒体标签工具安装程序.exe",
                    "browser_download_url": "https://github.com/luoxu-ai/ai-media-tagger/releases/download/v1.2.0/AI-Media-Tagger-Setup.exe",
                    "digest": f"sha256:{digest}",
                    "size": 204,
                },
            ],
        }
    )
    assert release.version == "1.2.0"
    assert release.sha256 == digest
    assert release.asset_name == "AI媒体标签工具安装程序.exe"
    assert release.download_url.casefold().endswith("setup.exe")


def test_release_without_sha256_is_rejected():
    with pytest.raises(UpdateError, match="SHA256"):
        release_from_payload(
            {
                "tag_name": "v1.2.0",
                "assets": [
                    {
                        "name": "AI媒体标签工具.exe",
                        "browser_download_url": "https://github.com/luoxu-ai/ai-media-tagger/releases/download/v1.2.0/AI-Media-Tagger.exe",
                    }
                ],
            }
        )


def test_release_rejects_unknown_executable_asset_name():
    with pytest.raises(UpdateError):
        release_from_payload(
            {
                "tag_name": "v9.9.9",
                "assets": [
                    {
                        "name": "unexpected.exe",
                        "browser_download_url": "https://github.com/example/release/unexpected.exe",
                        "digest": f"sha256:{'a' * 64}",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/luoxu-ai/ai-media-tagger/app.exe",
        "https://example.test/AI-Media-Tagger-Setup.exe",
    ],
)
def test_release_rejects_untrusted_download_url(url):
    with pytest.raises(UpdateError, match="GitHub HTTPS"):
        release_from_payload(
            {
                "tag_name": "v1.2.3",
                "assets": [
                    {
                        "name": "AI-Media-Tagger-Setup.exe",
                        "browser_download_url": url,
                        "digest": f"sha256:{'a' * 64}",
                    }
                ],
            }
        )


class FakeResponse:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        if size < 0:
            size = len(self.data)
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_download_verifies_hash_before_publishing_file(tmp_path):
    data = b"signed executable bytes"
    release = ReleaseInfo(
        "1.2.0",
        "notes",
        "https://example.test/app.exe",
        hashlib.sha256(data).hexdigest(),
        len(data),
        "https://example.test/release",
        "AI媒体标签工具.exe",
    )
    progress = []
    with patch("update_manager.update_storage_directory", return_value=tmp_path), patch(
        "update_manager._request", return_value=FakeResponse(data)
    ):
        result = download_release(release, lambda current, total: progress.append((current, total)))
    assert result.read_bytes() == data
    assert progress[-1] == (len(data), len(data))


def test_download_deletes_corrupt_partial_file(tmp_path):
    data = b"corrupt"
    release = ReleaseInfo(
        "1.2.0",
        "notes",
        "https://example.test/app.exe",
        "0" * 64,
        len(data),
        "https://example.test/release",
        "AI媒体标签工具.exe",
    )
    with patch("update_manager.update_storage_directory", return_value=tmp_path), patch(
        "update_manager._request", return_value=FakeResponse(data)
    ), pytest.raises(UpdateError, match="校验失败"):
        download_release(release)
    assert not (tmp_path / "AI媒体标签工具安装程序.download").exists()
    assert not (tmp_path / "AI媒体标签工具安装程序.new.exe").exists()


def test_cancelled_download_deletes_partial_file(tmp_path):
    data = b"x" * (2 * 1024 * 1024)
    release = ReleaseInfo(
        "1.2.1",
        "notes",
        "https://example.test/app.exe",
        hashlib.sha256(data).hexdigest(),
        len(data),
        "https://example.test/release",
        "AI-Media-Tagger-Setup.exe",
    )
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 2

    with patch("update_manager.update_storage_directory", return_value=tmp_path), patch(
        "update_manager._request", return_value=FakeResponse(data)
    ), pytest.raises(UpdateCancelled):
        download_release(release, cancelled=cancelled)
    assert not (tmp_path / "AI媒体标签工具安装程序.download").exists()
    assert not (tmp_path / "AI媒体标签工具安装程序.new.exe").exists()


def test_automatic_check_is_limited_to_once_per_day(tmp_path):
    with patch("update_manager.update_storage_directory", return_value=tmp_path):
        assert should_check_automatically(now=1000.0)
        record_successful_check(now=1000.0)
        assert not should_check_automatically(now=1001.0)
        assert should_check_automatically(now=1000.0 + 24 * 60 * 60)


def test_update_completion_marker_is_reported_once(tmp_path):
    with patch("update_manager.update_storage_directory", return_value=tmp_path):
        record_update_install_started("1.3.0", "1.2.2")
        assert consume_update_startup_result("1.3.0") == ("completed", "1.3.0")
        assert consume_update_startup_result("1.3.0") is None


def test_update_completion_marker_can_be_acknowledged_after_ui_notification(tmp_path):
    with patch("update_manager.update_storage_directory", return_value=tmp_path):
        record_update_install_started("1.3.0", "1.2.2")
        assert consume_update_startup_result("1.3.0", clear=False) == (
            "completed",
            "1.3.0",
        )
        assert consume_update_startup_result("1.3.0") == ("completed", "1.3.0")
        assert consume_update_startup_result("1.3.0") is None


def test_update_failure_keeps_current_version_available(tmp_path):
    with patch("update_manager.update_storage_directory", return_value=tmp_path):
        record_update_install_started("1.3.0", "1.2.2")
        assert consume_update_startup_result("1.2.2") is None
        assert consume_update_startup_result("1.2.2", now=time.time() + 901) == (
            "failed",
            "1.2.2",
        )
