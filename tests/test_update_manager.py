import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from update_manager import (
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
    download_release,
    is_installable_signature_status,
    is_newer_version,
    release_from_payload,
    record_successful_check,
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


def test_release_payload_prefers_the_official_installer_asset():
    digest = "a" * 64
    release = release_from_payload(
        {
            "tag_name": "v1.2.0",
            "body": "更新说明",
            "html_url": "https://example.test/release",
            "assets": [
                {
                    "name": "other.exe",
                    "browser_download_url": "https://example.test/other.exe",
                    "digest": f"sha256:{'b' * 64}",
                    "size": 1,
                },
                {
                    "name": "AI媒体标签工具.exe",
                    "browser_download_url": "https://example.test/app.exe",
                    "digest": f"sha256:{digest}",
                    "size": 203,
                },
                {
                    "name": "AI媒体标签工具安装程序.exe",
                    "browser_download_url": "https://example.test/setup.exe",
                    "digest": f"sha256:{digest}",
                    "size": 204,
                },
            ],
        }
    )
    assert release.version == "1.2.0"
    assert release.sha256 == digest
    assert release.asset_name == "AI媒体标签工具安装程序.exe"
    assert release.download_url.endswith("setup.exe")


def test_release_without_sha256_is_rejected():
    with pytest.raises(UpdateError, match="SHA256"):
        release_from_payload(
            {
                "tag_name": "v1.2.0",
                "assets": [
                    {
                        "name": "AI媒体标签工具.exe",
                        "browser_download_url": "https://example.test/app.exe",
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
