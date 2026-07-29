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
from PySide6.QtWidgets import QApplication, QAbstractItemView

from core import TagResult
from person_detector import DetectionResult
from qt_app import (
    CONTACT_URL,
    FILE_STATUS_KIND_ROLE,
    FILE_STATUS_TEXT_ROLE,
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

    def wait_for_import(self, window, timeout_seconds=3.0):
        deadline = time.perf_counter() + timeout_seconds
        while window.importing and time.perf_counter() < deadline:
            QTest.qWait(20)
            QApplication.processEvents()
        self.assertFalse(window.importing, "background import did not finish")

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
            self.assertIs(window.choose_file_button.window(), window)
            self.assertIsNotNone(window.choose_file_button.parentWidget())
            self.assertTrue(window.select_all_button.isEnabled())
            self.assertTrue(window.select_none_button.isEnabled())
            self.assertTrue(window.clear_button.isEnabled())
            self.assertTrue(window.start_button.isEnabled())
            self.assertTrue(window.smart_button.isEnabled())
            self.assertFalse(window.stop_button.isEnabled())
            self.assertEqual(window.start_button.text(), "导出已勾选（0）")
            self.assertFalse(hasattr(window, "all_export_button"))
            self.assertEqual(window.cleanup_button.text(), "撤销标签")
        finally:
            window.close()

    def test_file_list_replaces_combined_add_file_empty_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "商品图.jpg"
            path.write_bytes(b"image")
            window = MainWindow()
            try:
                self.assertEqual(window.list_stack.currentIndex(), 0)
                window.files = [path]
                window.refresh({str(path).casefold()})
                self.assertEqual(window.list_stack.currentIndex(), 1)
                self.assertEqual(window.list.count(), 1)
            finally:
                window.close()

    def test_safe_stop_button_sets_cancellation_request(self):
        window = MainWindow()
        try:
            window.running = True
            window.cancel_requested.clear()
            window._update_action_states()
            self.assertTrue(window.stop_button.isEnabled())

            window.request_stop()

            self.assertTrue(window.cancel_requested.is_set())
            self.assertFalse(window.stop_button.isEnabled())
            self.assertEqual(window.stop_button.text(), "正在停止…")
            self.assertIn("安全停止", window.status.text())
        finally:
            window.running = False
            window.close()

    def test_duration_and_eta_formatting(self):
        self.assertEqual(MainWindow._format_duration(30), "约 30 秒")
        self.assertEqual(MainWindow._format_duration(61), "约 2 分钟")
        self.assertEqual(MainWindow._format_duration(3600), "约 1 小时")

        window = MainWindow()
        try:
            window.running = True
            window.task_started_at = 100.0
            with patch("qt_app.time.time", return_value=110.0):
                window._update_eta(500)
                self.assertEqual(window.eta_label.text(), "预计剩余：--")
                window._update_eta_by_items(12, 24, 1.0)
            self.assertEqual(window.eta_label.text(), "预计剩余：约 11 秒")
        finally:
            window.running = False
            window.close()

    def test_latest_persistent_log_is_restored_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            log_folder = Path(folder) / "logs"
            log_folder.mkdir()
            latest = log_folder / "AI媒体标签导出报告_20260728_120000.txt"
            latest.write_text("上一次任务\n识别模式：智能人物识别", encoding="utf-8-sig")
            with patch("qt_app.persistent_log_directory", return_value=log_folder):
                window = MainWindow()
                try:
                    self.assertEqual(window.current_log_path, latest)
                    self.assertIn("上一次任务", window.log_lines)
                    window.log_lines.append("追加结果")
                    window._persist_log()
                    self.assertIn("追加结果", latest.read_text(encoding="utf-8-sig"))
                finally:
                    window.close()

    def test_log_history_keeps_older_tasks_visible_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            log_folder = Path(folder) / "logs"
            log_folder.mkdir()
            older = log_folder / "AI媒体标签导出报告_20260728_110000.txt"
            latest = log_folder / "AI媒体标签导出报告_20260728_120000.txt"
            older.write_text("较早任务永久记录", encoding="utf-8-sig")
            latest.write_text("最新任务永久记录", encoding="utf-8-sig")
            with patch("qt_app.persistent_log_directory", return_value=log_folder):
                window = MainWindow()
                try:
                    window.show_log()
                    text = window._log_window.centralWidget().toPlainText()
                    self.assertIn("较早任务永久记录", text)
                    self.assertIn("最新任务永久记录", text)
                    self.assertTrue(older.exists())
                    self.assertTrue(latest.exists())
                finally:
                    window._log_window.close()
                    window.close()

    def test_cancelled_job_keeps_only_unprocessed_files_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f"图片_{index}.jpg" for index in range(3)]
            for path in paths:
                path.write_bytes(b"image")
            window = MainWindow()
            try:
                window.files = paths.copy()
                window.active_files = paths.copy()
                window.active_file_keys = {str(path).casefold() for path in paths}
                window.refresh({str(path).casefold() for path in paths})

                window._finalize_cancelled_files(1)

                self.assertEqual(window.list.item(0).checkState(), Qt.Unchecked)
                self.assertEqual(window.list.item(1).checkState(), Qt.Checked)
                self.assertEqual(window.list.item(2).checkState(), Qt.Checked)
                self.assertEqual(window.checked_files(), paths[1:])
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
                CONTACT_URL,
                "https://github.com/luoxu-ai/ai-media-tagger/issues/new",
            )
            with patch("qt_app.QDesktopServices.openUrl", return_value=True) as open_url:
                window.contact_button.click()
            opened = open_url.call_args.args[0].toString()
            self.assertIn("github.com/luoxu-ai/ai-media-tagger/issues/new", opened)
            self.assertNotIn("unique_id=", opened)
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

    def test_file_row_status_is_shown_and_survives_refresh(self):
        source = Path("C:/samples/product.jpg")
        window = MainWindow()
        try:
            window.files = [source]
            window.refresh({str(source).casefold()})
            window._set_file_status(source, "处理中", "processing")
            item = window.list.item(0)
            self.assertEqual(item.data(FILE_STATUS_TEXT_ROLE), "处理中")
            self.assertEqual(item.data(FILE_STATUS_KIND_ROLE), "processing")

            window.refresh({str(source).casefold()})
            item = window.list.item(0)
            self.assertEqual(item.data(FILE_STATUS_TEXT_ROLE), "处理中")
            self.assertEqual(item.data(FILE_STATUS_KIND_ROLE), "processing")
        finally:
            window.close()

    def test_list_follows_the_file_that_is_currently_processing(self):
        paths = [Path(f"C:/samples/product_{index:03d}.jpg") for index in range(50)]
        window = MainWindow()
        try:
            window.files = paths
            window.refresh({str(path).casefold() for path in paths})
            active_item = window.list.item(37)
            with patch.object(window.list, "scrollToItem") as scroll:
                window._set_file_status(paths[37], "处理中", "processing")
                scroll.assert_called_once()
                self.assertIs(scroll.call_args.args[0], active_item)
                self.assertEqual(
                    scroll.call_args.args[1], QAbstractItemView.EnsureVisible
                )

                window._set_file_status(paths[37], "已导出", "success")
                self.assertEqual(scroll.call_count, 1)
        finally:
            window.close()

    def test_processing_details_stay_in_log_while_summary_is_simple(self):
        source = Path("C:/samples/product.jpg")
        window = MainWindow()
        try:
            window.files = [source]
            window.refresh({str(source).casefold()})
            window.running = True
            window.processing_current = 2
            window.processing_total = 50
            window.events.put(("status", "正在写入并验证标签：product.jpg"))
            window.events.put(("phase_progress", 250, "正在复核标签：product.jpg"))
            window._drain_events()
            self.assertEqual(window.status.text(), "处理中：2/50")

            detection = DetectionResult(False, 0.2, "未检测到人物", 0.01)
            window.events.put(("smart_result", 1, 1, source, None, detection, None))
            window._drain_events()
            self.assertEqual(window.status.text(), "处理中：1/1")
            self.assertEqual(
                window.list.item(0).data(FILE_STATUS_TEXT_ROLE), "未检测到人物"
            )
            self.assertEqual(
                window.list.item(0).data(FILE_STATUS_KIND_ROLE), "skipped"
            )
            self.assertEqual(window.list.item(0).checkState(), Qt.Unchecked)
            self.assertEqual(window.count.text(), "已选择：0 / 1 个文件")
        finally:
            window.running = False
            window.close()

    def test_successful_export_keeps_row_as_output_and_unchecks_it(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "product.jpg"
            exported = Path(folder) / "product_AI.jpg"
            source.write_bytes(b"source")
            exported.write_bytes(b"exported")
            window = MainWindow()
            try:
                window.files = [source]
                window.active_files = [source]
                window.active_file_keys = {str(source).casefold()}
                window.refresh({str(source).casefold()})
                window.running = True
                result = TagResult(exported, True, "标签已追加并验证")

                window.events.put((
                    "smart_result", 1, 1, source, exported,
                    DetectionResult(True, 0.9, "检测到人物", 0.01), result,
                ))
                window._drain_events()

                self.assertEqual(window.files, [exported.resolve()])
                self.assertEqual(window.list.count(), 1)
                self.assertTrue(window.list.item(0).text().startswith("product_AI.jpg\n"))
                self.assertEqual(window.list.item(0).checkState(), Qt.Unchecked)
                self.assertEqual(
                    window.list.item(0).data(FILE_STATUS_TEXT_ROLE), "已导出"
                )
                self.assertIn(str(exported.resolve()).casefold(), window.tagged_file_keys)

                window._finalize_completed_files()
                self.assertEqual(window.files, [exported.resolve()])
                self.assertEqual(window.list.count(), 1)
                self.assertEqual(window.list.item(0).checkState(), Qt.Unchecked)
            finally:
                window.running = False
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
                    self.wait_for_import(window)
                states = {
                    path.name: window.list.item(index).checkState()
                    for index, path in enumerate(window.files)
                }
                self.assertEqual(states["old.jpg"], Qt.Unchecked)
                self.assertEqual(states["new.jpg"], Qt.Checked)
            finally:
                window.close()

    def test_import_worker_reports_batched_file_progress(self):
        paths = [Path(f"sample_{index:04d}.jpg") for index in range(501)]
        batch_sizes = []

        class FakeService:
            def __init__(self, _executable):
                pass

            def paths_with_target_subject(self, batch):
                batch_sizes.append(len(batch))
                return set()

        window = MainWindow()
        try:
            with patch("qt_app.collect_media", return_value=paths), patch(
                "qt_app.ExifToolService", FakeService
            ), patch("qt_app.find_exiftool", return_value=Path("exiftool.exe")):
                window._import_worker(["unused"], set(), set())

            events = []
            while not window.events.empty():
                events.append(window.events.get_nowait())
            progress = [event for event in events if event[0] == "import_progress"]

            self.assertEqual(batch_sizes, [250, 250, 1])
            self.assertEqual(
                [(event[1], event[2]) for event in progress],
                [(0, 501), (250, 501), (500, 501), (501, 501)],
            )
            self.assertEqual(events[-1][0], "import_done")
        finally:
            window.close()

    def test_file_list_renders_2000_unicode_paths_within_reasonable_time(self):
        root = Path(r"D:\公开测试数据\测试目录_中文")
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

            def fake_export_one(_service, source, destination):
                destinations.append(destination)
                exported = source.parent / f"{source.stem}_AI{source.suffix}"
                exported.write_bytes(source.read_bytes())
                return exported, TagResult(exported, True, "ok")

            window = MainWindow()
            try:
                with patch("qt_app.export_tagged_copy", side_effect=fake_export_one), patch(
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

            def fake_export_one(_service, item, _destination):
                exported = item.parent / "中文原图_AI.jpg"
                exported.write_bytes(item.read_bytes())
                return exported, TagResult(exported, True, "标签已验证")

            window = MainWindow()
            try:
                with patch("qt_app.export_tagged_copy", side_effect=fake_export_one), patch(
                    "core.send_to_recycle_bin", side_effect=lambda path: path.unlink()
                ):
                    window._worker(Path("exiftool.exe"), [source])
                self.assertFalse(source.exists())
                self.assertTrue((Path(folder) / "中文原图_AI.jpg").exists())
            finally:
                window.close()

    def test_manual_worker_stops_after_current_file(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f"图片_{index:02d}.jpg" for index in range(25)]
            for path in paths:
                path.write_bytes(b"image")
            window = MainWindow()

            def fake_export_one(_service, source, _destination):
                window.cancel_requested.set()
                exported = source.with_name(f"{source.stem}_AI{source.suffix}")
                exported.write_bytes(source.read_bytes())
                return exported, TagResult(exported, True, "标签已验证")

            try:
                with patch("qt_app.export_tagged_copy", side_effect=fake_export_one), patch(
                    "core.send_to_recycle_bin", side_effect=lambda path: path.unlink()
                ):
                    window._worker(Path("exiftool.exe"), paths)
                events = []
                while not window.events.empty():
                    events.append(window.events.get_nowait())
                self.assertEqual(events[-1], ("cancelled", 1, 1, 25))
                self.assertFalse(paths[0].exists())
                self.assertTrue(all(path.exists() for path in paths[1:]))
            finally:
                window.running = False
                window.close()

    def test_smart_worker_stop_during_detection_does_not_start_export(self):
        class CancellingDetector:
            def __init__(self, window):
                self.window = window

            def detect(self, _path):
                self.window.cancel_requested.set()
                return DetectionResult(True, 0.9, "人物", 0.01)

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "人物.jpg"
            source.write_bytes(b"image")
            window = MainWindow()
            try:
                with patch.object(window, "_get_detector", return_value=CancellingDetector(window)), patch(
                    "qt_app.export_tagged_copy"
                ) as export_one:
                    window._smart_worker(Path("exiftool.exe"), [source])
                export_one.assert_not_called()
                self.assertTrue(source.exists())
                events = []
                while not window.events.empty():
                    events.append(window.events.get_nowait())
                self.assertEqual(events[-1], ("smart_cancelled", 0, 0, 0, 0, 1))
            finally:
                window.running = False
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
                    self.wait_for_import(window)
                renamed = root / "tagged_AI.jpg"
                self.assertEqual(window.files, [plain.resolve(), renamed.resolve()])
                self.assertFalse(tagged.exists())
                self.assertTrue(renamed.exists())
                states = {
                    path.name: window.list.item(index).checkState()
                    for index, path in enumerate(window.files)
                }
                self.assertEqual(states["plain.jpg"], Qt.Checked)
                self.assertEqual(states["tagged_AI.jpg"], Qt.Unchecked)
                self.assertIn("1 个已有标签文件保持未勾选", window.status.text())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
