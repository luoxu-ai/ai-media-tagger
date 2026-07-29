import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import ExifToolService, TAG_VALUE, TagResult, collect_media, copy_without_overwrite, export_tagged_copy, remove_source_after_verified_export, rename_tagged_file


class CollectMediaTests(unittest.TestCase):
    def test_collects_supported_files_recursively_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nested = root / "nested"
            nested.mkdir()
            photo = root / "A.JPG"
            video = nested / "clip.mp4"
            ignored = root / "notes.txt"
            also_ignored = [root / "image.gif", root / "design.psd", root / "document.pdf", root / "clip.webm"]
            for path in (photo, video, ignored, *also_ignored):
                path.write_bytes(b"test")
            found = collect_media([root, photo])
            self.assertEqual(found, sorted([photo.resolve(), video.resolve()], key=lambda p: str(p).casefold()))

    def test_copy_never_overwrites_source_or_existing_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_dir = root / "source"; target_dir = root / "target"
            source_dir.mkdir(); target_dir.mkdir()
            source = source_dir / "photo.jpg"; source.write_bytes(b"original")
            existing = target_dir / "photo_AI.jpg"; existing.write_bytes(b"existing")
            exported = copy_without_overwrite(source, target_dir)
            self.assertEqual(source.read_bytes(), b"original")
            self.assertEqual(existing.read_bytes(), b"existing")
            self.assertEqual(exported.name, "photo_AI (1).jpg")
            self.assertEqual(exported.read_bytes(), b"original")

    def test_source_is_removed_only_after_verified_export_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "原图.jpg"
            exported = root / "原图_AI.jpg"
            source.write_bytes(b"source")
            exported.write_bytes(b"tagged-copy")
            with patch("core.send_to_recycle_bin", side_effect=lambda path: path.unlink()) as recycle:
                result = remove_source_after_verified_export(
                    source, exported, TagResult(exported, True, "标签已验证")
                )
            self.assertTrue(result.success)
            recycle.assert_called_once_with(source)
            self.assertFalse(source.exists())
            self.assertEqual(exported.read_bytes(), b"tagged-copy")

    def test_source_is_preserved_when_export_is_missing_or_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "原图.jpg"
            source.write_bytes(b"source")
            missing = root / "原图_AI.jpg"
            result = remove_source_after_verified_export(
                source, missing, TagResult(missing, True, "标签已验证")
            )
            self.assertFalse(result.success)
            self.assertTrue(source.exists())
            failed = remove_source_after_verified_export(
                source, None, TagResult(source, False, "写入失败")
            )
            self.assertFalse(failed.success)
            self.assertTrue(source.exists())

    def test_permanent_delete_is_used_when_recycle_bin_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "原图.jpg"
            exported = root / "原图_AI.jpg"
            source.write_bytes(b"source")
            exported.write_bytes(b"tagged-copy")
            with patch("core.send_to_recycle_bin", side_effect=OSError("recycle unavailable")):
                result = remove_source_after_verified_export(
                    source, exported, TagResult(exported, True, "标签已验证")
                )
            self.assertTrue(result.success)
            self.assertIn("永久删除", result.message)
            self.assertFalse(source.exists())
            self.assertTrue(exported.exists())

    def test_recycle_bin_retries_short_lived_file_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "被占用原图.jpg"
            exported = root / "被占用原图_AI.jpg"
            source.write_bytes(b"source")
            exported.write_bytes(b"tagged-copy")

            def succeed(path):
                path.unlink()

            lock = PermissionError("file is being used by another process")
            with patch("core.send_to_recycle_bin", side_effect=[lock, lock, succeed]) as recycle, patch(
                "core.time.sleep"
            ) as sleep:
                result = remove_source_after_verified_export(
                    source, exported, TagResult(exported, True, "标签已验证")
                )
            self.assertTrue(result.success)
            self.assertEqual(recycle.call_count, 3)
            self.assertEqual(sleep.call_count, 2)


class ServiceTests(unittest.TestCase):
    def test_individual_tagging_retries_transient_lock(self):
        service = ExifToolService(Path("exiftool.exe"))
        locked = TagResult(Path("demo.jpg"), False, "file is being used by another process")
        success = TagResult(Path("demo.jpg"), True, "标签已验证")
        with patch.object(service, "_ensure_subject_once", side_effect=[locked, locked, success]) as attempt, patch(
            "core.time.sleep"
        ) as sleep:
            result = service.ensure_subject(Path("demo.jpg"), keep_backup=False)
        self.assertTrue(result.success)
        self.assertEqual(attempt.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_removes_only_target_subject_and_verifies(self):
        service = ExifToolService(Path("exiftool.exe"))
        completed = type("Result", (), {"returncode": 0, "stdout": "updated", "stderr": ""})()
        with patch.object(service, "_run", return_value=completed) as run, patch.object(
            service, "_read_subject", return_value=(True, ["other-keyword"], "")
        ):
            result = service.remove_target_subject(Path("demo.jpg"))
        self.assertTrue(result.success)
        arguments = run.call_args.args[0]
        self.assertIn(f"-XMP-dc:Subject-={TAG_VALUE}", arguments)
        self.assertNotIn(f"-XMP-dc:Subject+={TAG_VALUE}", arguments)
        self.assertIn("其他关键词已保留", result.message)

    def test_batch_removes_target_subject_with_one_write_and_one_verification(self):
        service = ExifToolService(Path("exiftool.exe"))
        paths = [Path("中文甲_AI.jpg"), Path("中文乙_AI.png")]
        completed = type("Result", (), {"returncode": 0, "stdout": "updated", "stderr": ""})()
        verified = {
            str(path).casefold(): (True, ["other-keyword"], "") for path in paths
        }
        with patch.object(service, "_run", return_value=completed) as run, patch.object(
            service, "read_subjects", return_value=verified
        ) as read:
            results = service.remove_target_subjects_batch(paths)
        self.assertEqual(run.call_count, 1)
        read.assert_called_once_with(paths)
        self.assertTrue(all(result.success for result in results.values()))
        arguments = run.call_args.args[0]
        self.assertIn(f"-XMP-dc:Subject-={TAG_VALUE}", arguments)
        self.assertNotIn(f"-XMP-dc:Subject+={TAG_VALUE}", arguments)

    @patch("core.subprocess.run")
    def test_batch_finds_files_that_already_have_target_subject(self, run):
        run.return_value = type(
            "Result", (), {
                "returncode": 0,
                "stdout": f'[{{"Subject":["{TAG_VALUE}"]}},{{}}]',
                "stderr": "",
            }
        )()
        first, second = Path("first.jpg"), Path("second.jpg")
        tagged = ExifToolService(Path("exiftool.exe")).paths_with_target_subject([first, second])
        self.assertEqual(tagged, {str(first).casefold()})

    def test_export_skips_source_that_already_has_target_tag(self):
        service = ExifToolService(Path("exiftool.exe"))
        with patch.object(service, "_read_subject", return_value=(True, [TAG_VALUE], "")):
            with patch("core.copy_without_overwrite") as copy:
                exported, result = export_tagged_copy(service, Path("tagged.jpg"), Path("out"))
        self.assertIsNone(exported)
        self.assertTrue(result.success)
        self.assertIn("跳过重复处理", result.message)
        copy.assert_not_called()

    def test_tagged_file_is_renamed_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "图片.jpg"
            source.write_bytes(b"source")
            (root / "图片_AI.jpg").write_bytes(b"existing")
            renamed = rename_tagged_file(source)
            self.assertEqual(renamed.name, "图片_AI (1).jpg")
            self.assertEqual(renamed.read_bytes(), b"source")

    @patch("core.subprocess.run")
    def test_streams_utf8_arguments_without_temp_argfile(self, run):
        run.return_value = type("Result", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

        chinese_path = Path(r"C:\Users\测试用户\Desktop\导出目录\商品图.jpg")
        ExifToolService(Path("exiftool.exe"))._read_subject(chinese_path)

        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(command[-2:], ["-@", "-"])
        self.assertIn(str(chinese_path), options["input"])
        self.assertEqual(options["encoding"], "utf-8")
        self.assertNotIn(".args", " ".join(command))

    @patch("core.subprocess.run")
    def test_appends_and_verifies_fixed_subject(self, run):
        run.side_effect = [
            type("Result", (), {"returncode": 0, "stdout": "1 image files updated", "stderr": ""})(),
            type("Result", (), {"returncode": 0, "stdout": f'[{ {"Subject": [TAG_VALUE]} }]'.replace("'", '"'), "stderr": ""})(),
        ]
        result = ExifToolService(Path("exiftool.exe")).ensure_subject(Path("demo.jpg"), keep_backup=False)
        self.assertTrue(result.success)
        write_args = run.call_args_list[0].args[0]
        self.assertIn("-@", write_args)

    @patch("core.subprocess.run")
    def test_does_not_write_duplicate_subject(self, run):
        run.return_value = type("Result", (), {"returncode": 0, "stdout": f'[{ {"Subject": [TAG_VALUE]} }]'.replace("'", '"'), "stderr": ""})()
        result = ExifToolService(Path("exiftool.exe")).ensure_subject(Path("demo.jpg"), keep_backup=True)
        self.assertTrue(result.success)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
