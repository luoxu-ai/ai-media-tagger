import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core import TagResult
from person_detector import DetectionResult
from qt_app import (
    FEEDBACK_URL,
    WINDOWS_APP_USER_MODEL_ID,
    MainWindow,
    SingleInstanceController,
    configure_windows_app_identity,
    single_instance_server_name,
)


class QtBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_single_instance_name_is_stable_ascii(self):
        first = single_instance_server_name()
        self.assertEqual(first, single_instance_server_name())
        self.assertTrue(first.isascii())

    def test_windows_app_identity_is_stable(self):
        self.assertTrue(WINDOWS_APP_USER_MODEL_ID.isascii())
        self.assertEqual(WINDOWS_APP_USER_MODEL_ID, "CBT.AIMediaTagTool")
        configure_windows_app_identity()

    def test_second_instance_notifies_first(self):
        name = f"ai_media_tagger_test_{os.getpid()}_{time.time_ns()}"
        notifications = []
        first = SingleInstanceController(name, lambda: notifications.append(True))
        second = SingleInstanceController(name, lambda: None)
        try:
            self.assertTrue(first.acquire_or_notify())
            self.assertFalse(second.acquire_or_notify())
            QTest.qWait(100)
            self.assertEqual(notifications, [True])
        finally:
            first.server.close()
            second.server.close()
            QLocalServer.removeServer(name)

    def test_delete_removes_highlighted_rows_but_not_source_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = [root / f"商品图_{index}.jpg" for index in range(3)]
            for path in paths:
                path.write_bytes(b"original")

            window = MainWindow()
            try:
                window.files = paths.copy()
                checked = {str(paths[0]).casefold(), str(paths[2]).casefold()}
                window.refresh(checked)
                window.list.setFocus()
                QTest.keyClick(window.list, Qt.Key_Delete)
                QApplication.processEvents()

                self.assertEqual(window.files, [paths[1]])
                self.assertTrue(all(path.read_bytes() == b"original" for path in paths))
                self.assertIn("原文件未删除", window.status.text())
            finally:
                window.close()

    def test_empty_state_keeps_hoverable_actions_enabled_and_export_all_is_removed(self):
        window = MainWindow()
        try:
            self.assertEqual(window.list_stack.currentIndex(), 0)
            self.assertTrue(window.select_all_button.isEnabled())
            self.assertTrue(window.select_none_button.isEnabled())
            self.assertTrue(window.clear_button.isEnabled())
            self.assertTrue(window.start_button.isEnabled())
            self.assertTrue(window.smart_button.isEnabled())
            self.assertFalse(hasattr(window, "all_export_button"))
        finally:
            window.close()

    def test_phase_progress_never_moves_backwards(self):
        window = MainWindow()
        source = Path("C:/samples/person.jpg")
        try:
            window.running = True
            window.progress.setRange(0, 1000)
            window.progress.setValue(0)
            window.events.put(("phase_progress", 300, "识别中"))
            window._drain_events()
            self.assertEqual(window.progress.value(), 300)
            self.assertEqual(window.progress_text.text(), "30%")

            window.events.put(("phase_progress", 250, "旧事件"))
            window._drain_events()
            self.assertEqual(window.progress.value(), 300)
            self.assertEqual(window.progress_text.text(), "30%")
        finally:
            window.running = False
            window.close()

    def test_contact_button_always_uses_default_browser(self):
        window = MainWindow()
        try:
            self.assertEqual(
                FEEDBACK_URL,
                "https://github.com/luoxu-ai/ai-media-tagger/issues/new",
            )
            with patch("qt_app.QDesktopServices.openUrl", return_value=True) as open_url:
                window.contact_button.click()
            opened = open_url.call_args.args[0].toString()
            self.assertEqual(opened, FEEDBACK_URL)
        finally:
            window.close()

    def test_file_row_shows_filename_and_parent_path(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "中文目录" / "商品图.jpg"
            source.parent.mkdir()
            source.write_bytes(b"image")
            window = MainWindow()
            try:
                window.files = [source]
                window.refresh({str(source).casefold()})
                item = window.list.item(0)
                self.assertEqual(item.text(), f"{source.name}\n{source.parent}")
                self.assertEqual(item.toolTip(), str(source))
                self.assertEqual(window.list_stack.currentIndex(), 1)
                self.assertTrue(window.start_button.isEnabled())
                self.assertTrue(window.smart_button.isEnabled())
            finally:
                window.close()

    def test_detector_is_loaded_once_and_cached(self):
        window = MainWindow()
        detector = object()
        try:
            with patch("qt_app.PersonDetector", return_value=detector) as create:
                self.assertIs(window._get_detector(), detector)
                self.assertIs(window._get_detector(), detector)
            create.assert_called_once_with()
        finally:
            window.close()

    def test_new_files_are_checked_without_rechecking_old_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "old.jpg"
            new = root / "new.jpg"
            old.write_bytes(b"old")
            new.write_bytes(b"new")

            class FakeService:
                def __init__(self, _executable):
                    pass

                def paths_with_target_subject(self, _paths):
                    return set()

            window = MainWindow()
            try:
                window.files = [old.resolve()]
                window.refresh(set())
                with patch("qt_app.ExifToolService", FakeService), patch(
                    "qt_app.find_exiftool", return_value=Path("exiftool.exe")
                ):
                    window.add_paths([str(new)])
                states = {
                    path.name: window.list.item(index).checkState()
                    for index, path in enumerate(window.files)
                }
                self.assertEqual(states["old.jpg"], Qt.Unchecked)
                self.assertEqual(states["new.jpg"], Qt.Checked)
            finally:
                window.close()

    def test_file_list_renders_2000_unicode_paths_within_reasonable_time(self):
        root = Path(r"D:\示例素材\测试目录_中文")
        paths = [root / f"商品图_{index:04d}.jpg" for index in range(2000)]
        window = MainWindow()
        try:
            started = time.perf_counter()
            window.files = paths
            window.refresh({str(path).casefold() for path in paths})
            elapsed = time.perf_counter() - started
            self.assertEqual(window.list.count(), 2000)
            self.assertEqual(window.count.text(), "已选择：2000 / 2000 个文件")
            self.assertIn(str(root), window.list.item(0).text())
            self.assertLess(elapsed, 10.0)

            started = time.perf_counter()
            window.uncheck_all()
            uncheck_elapsed = time.perf_counter() - started
            self.assertEqual(window.count.text(), "已选择：0 / 2000 个文件")

            started = time.perf_counter()
            window.check_all()
            check_elapsed = time.perf_counter() - started
            self.assertEqual(window.count.text(), "已选择：2000 / 2000 个文件")
            self.assertLess(uncheck_elapsed, 2.0)
            self.assertLess(check_elapsed, 2.0)
        finally:
            window.close()

    def test_smart_worker_exports_each_file_to_its_source_directory(self):
        class FakeDetector:
            def detect(self, path):
                return DetectionResult(True, 0.9, "test", 0.01)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "甲" / "a.jpg"
            second = root / "乙" / "b.jpg"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            destinations = []

            def fake_export_batch(_service, sources):
                output = []
                for source in sources:
                    destinations.append(source.parent)
                    exported = source.parent / f"{source.stem}_AI{source.suffix}"
                    exported.write_bytes(source.read_bytes())
                    output.append((source, exported, TagResult(exported, True, "ok")))
                return output

            window = MainWindow()
            try:
                with patch("qt_app.export_tagged_copies_batch", side_effect=fake_export_batch), patch(
                    "core.send_to_recycle_bin", side_effect=lambda path: path.unlink()
                ):
                    with patch.object(window, "_get_detector", return_value=FakeDetector()):
                        window._smart_worker(Path("exiftool.exe"), [first, second])
                self.assertEqual(destinations, [first.parent, second.parent])
                self.assertFalse(first.exists())
                self.assertFalse(second.exists())
                self.assertTrue((first.parent / "a_AI.jpg").exists())
                self.assertTrue((second.parent / "b_AI.jpg").exists())
            finally:
                window.close()

    def test_manual_worker_exports_to_source_directory_and_removes_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "中文原图.jpg"
            source.write_bytes(b"image")

            def fake_export_batch(_service, sources):
                output = []
                for item in sources:
                    exported = item.parent / "中文原图_AI.jpg"
                    exported.write_bytes(item.read_bytes())
                    output.append((item, exported, TagResult(exported, True, "标签已验证")))
                return output

            window = MainWindow()
            try:
                with patch("qt_app.export_tagged_copies_batch", side_effect=fake_export_batch), patch(
                    "core.send_to_recycle_bin", side_effect=lambda path: path.unlink()
                ):
                    window._worker(Path("exiftool.exe"), [source])
                self.assertFalse(source.exists())
                self.assertTrue((Path(folder) / "中文原图_AI.jpg").exists())
            finally:
                window.close()

    def test_add_paths_renames_and_ignores_already_tagged_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tagged = root / "tagged.jpg"
            plain = root / "plain.jpg"
            tagged.write_bytes(b"tagged")
            plain.write_bytes(b"plain")

            class FakeService:
                def __init__(self, _executable):
                    pass

                def paths_with_target_subject(self, _paths):
                    return {str(tagged.resolve()).casefold()}

            window = MainWindow()
            try:
                with patch("qt_app.ExifToolService", FakeService), patch(
                    "qt_app.find_exiftool", return_value=Path("exiftool.exe")
                ):
                    window.add_paths([str(root)])
                self.assertEqual(window.files, [plain.resolve()])
                self.assertFalse(tagged.exists())
                self.assertTrue((root / "tagged_AI.jpg").exists())
                self.assertIn("已忽略 1 个", window.status.text())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
