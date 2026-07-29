import tempfile
from pathlib import Path

from cleanup_app import clean_file, restore_filename


def test_restore_filename_removes_ai_suffix():
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "中文商品图_AI.jpg"
        source.write_bytes(b"image")
        restored, message = restore_filename(source)
        assert restored.name == "中文商品图.jpg"
        assert restored.read_bytes() == b"image"
        assert not source.exists()
        assert "已恢复" in message


def test_restore_filename_keeps_copy_number_and_never_overwrites():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "商品图_AI (1).png"
        source.write_bytes(b"clean-me")
        existing = root / "商品图 (1).png"
        existing.write_bytes(b"keep-me")
        restored, _message = restore_filename(source)
        assert restored.name == "商品图 (1)_restored.png"
        assert restored.read_bytes() == b"clean-me"
        assert existing.read_bytes() == b"keep-me"


def test_restore_filename_leaves_plain_name_unchanged():
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "商品图.jpg"
        source.write_bytes(b"image")
        restored, message = restore_filename(source)
        assert restored == source
        assert message == "文件名无需修改"


def test_clean_file_skips_ai_name_without_target_tag():
    class FakeService:
        def _read_subject(self, _path):
            return True, ["other-keyword"], ""

    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "商品图_AI.jpg"
        source.write_bytes(b"image")
        current, outcome, message = clean_file(FakeService(), source)
        assert outcome == "skipped"
        assert current == source
        assert source.exists()
        assert "不含目标标签" in message


def test_clean_file_skips_tagged_file_without_ai_name():
    class FakeService:
        def _read_subject(self, _path):
            raise AssertionError("filename gate should run before metadata access")

    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "商品图.jpg"
        source.write_bytes(b"image")
        current, outcome, message = clean_file(FakeService(), source)
        assert outcome == "skipped"
        assert current == source
        assert source.exists()
        assert "没有 _AI" in message
