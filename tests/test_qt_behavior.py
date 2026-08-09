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
from PySide6.QtWidgets import QApplication, QAbstractItemView, QWidget

from core import TagResult
from person_detector import DetectionResult
from update_manager import ReleaseInfo
import qt_app
from qt_app import (
    AboutDialog,
    SettingsDialog,
    UpdateDialog,
    FEISHU_CONTACT_URL,
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

    def test_main_window_title_hides_version_number(self):
        window = MainWindow()
        try:
            self.assertEqual(window.windowTitle(), "AI 媒体标签工具")
            self.assertNotIn("v1.2.1", window.windowTitle())
        finally:
            window.close()

    def test_frozen_build_keeps_and_migrates_logs_beside_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            install = root / "安装目录"
            install.mkdir()
            executable = install / "AI媒体标签工具.exe"
            executable.touch()
            local_app_data = root / "LocalAppData"
            legacy = local_app_data / "AI媒体标签工具" / "logs"
            legacy.mkdir(parents=True)
            old_log = legacy / "AI媒体标签导出报告_20260801_120000.txt"
            old_log.write_text("旧版日志", encoding="utf-8-sig")

            with (
                patch.object(qt_app.sys, "frozen", True, create=True),
                patch.object(qt_app.sys, "executable", str(executable)),
                patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}),
                patch.object(qt_app, "_LOG_MIGRATION_ATTEMPTED", False),
            ):
                expected = install.resolve() / "logs"
                self.assertEqual(qt_app.persistent_log_directory(), expected)
                qt_app.migrate_legacy_logs()
                self.assertEqual(
                    (expected / old_log.name).read_text(encoding="utf-8-sig"),
                    "旧版日志",
                )
                self.assertTrue(old_log.exists())

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

    def test_new_badge_appears_only_after_a_new_release_event(self):
        window = MainWindow()
        try:
            self.assertTrue(window.update_badge.isHidden())
            release = ReleaseInfo(
                "9.9.9",
                "测试更新",
                "https://example.test/setup.exe",
                "a" * 64,
                100,
                "https://example.test/release",
                "AI媒体标签工具安装程序.exe",
            )
            window.events.put(("update_available", release))
            window._drain_events()
            self.assertFalse(window.update_badge.isHidden())
            self.assertIs(window.available_release, release)
            self.assertIn("v9.9.9", window.update_badge.toolTip())
        finally:
            window.close()

    def test_update_dialog_can_cancel_and_requires_a_second_install_action(self):
        release = ReleaseInfo(
            "1.2.2",
            "更新测试",
            "https://example.test/setup.exe",
            "a" * 64,
            100,
            "https://example.test/release",
            "AI-Media-Tagger-Setup.exe",
        )
        dialog = UpdateDialog(release)
        try:
            dialog.set_downloading()
            self.assertEqual(dialog.later_button.text(), "取消更新")
            self.assertTrue(dialog.later_button.isEnabled())
            self.assertFalse(dialog.install_button.isEnabled())

            dialog.set_cancelled()
            self.assertEqual(dialog.update_status.text(), "更新已取消，未完成的下载文件已删除。")
            self.assertEqual(dialog.install_button.text(), "立即更新")

            dialog.set_ready_to_install()
            self.assertEqual(dialog.install_button.text(), "安装并重启")
            self.assertTrue(dialog.install_button.isEnabled())
            self.assertIn("校验已完成", dialog.update_status.text())
        finally:
            dialog.close()

    def test_settings_offer_system_light_and_dark_themes(self):
        dialog = SettingsDialog(
            lambda *_: None,
            lambda: None,
            lambda: None,
            on_open_log_folder=lambda: None,
        )
        try:
            options = [
                button.property("themeValue")
                for button in dialog.theme_group.buttons()
            ]
            self.assertEqual(options, ["light", "dark", "system"])
            self.assertEqual(dialog.automatic_check.text(), "自动检查更新")
            self.assertEqual(dialog.check_update_button.text(), "立即检查更新")
            button_texts = [button.text() for button in dialog.findChildren(qt_app.QPushButton)]
            self.assertIn("打开日志文件夹", button_texts)
            self.assertGreater(dialog.maximumWidth(), dialog.minimumWidth())
        finally:
            dialog.close()

    def test_windows_title_bar_uses_the_effective_app_theme(self):
        window = QWidget()
        try:
            hwnd = int(window.winId())
            with (
                patch.object(qt_app.os, "name", "nt"),
                patch.object(qt_app, "effective_theme", return_value="dark"),
                patch.object(
                    qt_app, "_set_windows_immersive_dark_mode", return_value=True
                ) as setter,
            ):
                self.assertTrue(qt_app.apply_windows_title_bar_theme(window))
                setter.assert_called_once_with(hwnd, True)
        finally:
            window.close()

    def test_title_bar_filter_is_installed_only_once(self):
        qt_app.install_windows_title_bar_theme_filter(self.app)
        installed = self.app._windows_title_bar_theme_filter
        qt_app.install_windows_title_bar_theme_filter(self.app)
        self.assertIs(self.app._windows_title_bar_theme_filter, installed)

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
                    content = latest.read_text(encoding="utf-8-sig")
                    self.assertIn("追加结果", content)
                    self.assertEqual(content.count("上一次任务"), 1)
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
        dialog = AboutDialog()
        try:
            self.assertIn("feishu.cn/invitation/page/add_contact/", FEISHU_CONTACT_URL)
            self.assertNotIn("unique_id=", FEISHU_CONTACT_URL)
            self.assertNotIn("&amp;", FEISHU_CONTACT_URL)
            with patch("qt_app.QDesktopServices.openUrl", return_value=True) as open_url:
                dialog.contact_button.click()
            opened = open_url.call_args.args[0].toString()
            self.assertIn("feishu.cn/invitation/page/add_contact/", opened)
            self.assertNotIn("unique_id=", opened)
        finally:
            dialog.close()

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
                self.assertEqual(
                    item.data(Qt.ItemDataRole.AccessibleTextRole),
                    f"{source.name}，未处理",
                )
                self.assertEqual(
                    item.data(Qt.ItemDataRole.AccessibleDescriptionRole),
                    f"路径：{source.parent}",
                )
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

    def test_file_row_reserves_two_lines_at_scaled_font_height(self):
        source = Path("C:/samples/很长的中文目录/CBT1628_02_卖点图_主要配件，简便还原.jpg")
        window = MainWindow()
        try:
            window.files = [source]
            window.refresh({str(source).casefold()})
            item = window.list.item(0)
            required = window.list.fontMetrics().lineSpacing() * 2 + 32
            self.assertGreaterEqual(item.sizeHint().height(), 72)
            self.assertGreaterEqual(item.sizeHint().height(), required)
        finally:
            window.close()

    def test_narrow_window_wraps_primary_actions_to_a_second_row(self):
        window = MainWindow()
        try:
            window.show()
            window.resize(900, 650)
            QApplication.processEvents()
            start_index = window.actionbar.indexOf(window.start_button)
            smart_index = window.actionbar.indexOf(window.smart_button)
            self.assertEqual(window.actionbar.getItemPosition(start_index)[0], 1)
            self.assertEqual(window.actionbar.getItemPosition(smart_index)[0], 1)

            window.resize(1200, 820)
            QApplication.processEvents()
            start_index = window.actionbar.indexOf(window.start_button)
            smart_index = window.actionbar.indexOf(window.smart_button)
            self.assertEqual(window.actionbar.getItemPosition(start_index)[0], 0)
            self.assertEqual(window.actionbar.getItemPosition(smart_index)[0], 0)
        finally:
            window.close()

    def test_file_header_actions_remain_keyboard_focusable(self):
        window = MainWindow()
        try:
            for button in (
                window.select_all_button,
                window.select_none_button,
                window.clear_button,
            ):
                self.assertNotEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)
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
        root = Path(r"C:\测试样本\测试目录_中文")
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

    def test_file_status_updates_keep_every_row_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = [root / f"sample_{index}.jpg" for index in range(4)]
            for path in paths:
                path.write_bytes(b"image")
            window = MainWindow()
            try:
                window.files = paths
                window.refresh({str(path).casefold() for path in paths})
                window._set_file_status(paths[0], "已导出", "success")
                window._set_file_status(paths[1], "未检测到人物", "skipped")
                window._set_file_status(paths[2], "处理失败", "failure")
                self.assertFalse(any(window.list.item(index).isHidden() for index in range(4)))
                self.assertFalse(hasattr(window, "status_filter_buttons"))
            finally:
                window.close()

    def test_last_task_is_restored_and_interrupted_rows_return_to_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state_path = root / "last_task.json"
            paths = [root / f"restore_{index}.jpg" for index in range(3)]
            for path in paths:
                path.write_bytes(b"image")
            with patch("qt_app.persistent_session_path", return_value=state_path):
                first = MainWindow()
                try:
                    first.files = paths
                    first.tagged_file_keys = {str(paths[0]).casefold()}
                    first.file_statuses = {
                        str(paths[0]).casefold(): ("已导出", "success"),
                        str(paths[1]).casefold(): ("处理中", "processing"),
                        str(paths[2]).casefold(): ("未检测到人物", "skipped"),
                    }
                    first.refresh({str(paths[1]).casefold()})
                    first._persist_session_state()
                finally:
                    first.close()

                restored = MainWindow()
                try:
                    self.assertEqual(restored.files, paths)
                    self.assertEqual(
                        restored.file_statuses[str(paths[0]).casefold()],
                        ("已导出", "success"),
                    )
                    self.assertEqual(
                        restored.file_statuses[str(paths[1]).casefold()],
                        ("未处理", "pending"),
                    )
                    self.assertEqual(restored.list.item(1).checkState(), Qt.Checked)
                    self.assertEqual(
                        restored.file_statuses[str(paths[2]).casefold()],
                        ("未检测到人物", "skipped"),
                    )
                    self.assertIn("已恢复上次任务", restored.status.text())
                finally:
                    restored.close()

    def test_runtime_environment_check_accepts_complete_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in qt_app.MODEL_RELATIVE_PATHS:
                model = root / relative
                model.parent.mkdir(parents=True, exist_ok=True)
                model.write_bytes(b"model")
            exiftool = root / "exiftool.exe"
            exiftool.write_bytes(b"tool")
            logs = root / "logs"
            with (
                patch("qt_app.bundled_path", side_effect=lambda relative: root / relative),
                patch("qt_app.find_exiftool", return_value=exiftool),
                patch("qt_app.persistent_log_directory", return_value=logs),
            ):
                self.assertEqual(qt_app.runtime_environment_issues(), [])

    def test_about_dialog_includes_company_and_model_details(self):
        dialog = AboutDialog()
        try:
            text = "\n".join(label.text() for label in dialog.findChildren(qt_app.QLabel))
            self.assertIn("开发者：Xu Luo", text)
            self.assertIn(f"模型版本：{qt_app.MODEL_VERSION}", text)
            self.assertIn(f"模型发布日期：{qt_app.MODEL_RELEASE_DATE}", text)
            self.assertIn(f"运行方式：{qt_app.MODEL_RUNTIME}", text)
            self.assertIn("公司：深圳市艾润特贸易有限公司", text)
        finally:
            dialog.close()

    def test_main_window_exposes_keyboard_shortcuts_and_privacy_safe_diagnostics(self):
        window = MainWindow()
        try:
            self.assertIn("Ctrl+O", window.choose_file_button.toolTip())
            self.assertIn("Ctrl+Shift+O", window.choose_folder_button.toolTip())
            with (
                patch("qt_app.runtime_environment_issues", return_value=[]),
                patch("qt_app.detection_cache_file_info", return_value=(12, 1024)),
            ):
                diagnostics = window.system_diagnostics_text()
            self.assertIn("检测缓存：12 条", diagnostics)
            self.assertIn("不包含图片、文件名或本机路径", diagnostics)
            self.assertNotIn(str(Path.home()), diagnostics)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
