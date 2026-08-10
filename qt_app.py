from __future__ import annotations

import queue
import os
import hashlib
import json
import platform
import shutil
import statistics
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QSize, QUrl, QRect, QPoint, QSettings, QEvent, QObject, QLibraryInfo, QTranslator
from PySide6.QtGui import QColor, QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, QMouseEvent, QKeyEvent, QIcon, QPainter, QPixmap, QPolygon, QKeySequence, QShortcut, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout, QWidget, QListWidgetItem, QStyle, QStyleOptionViewItem, QLayout, QGridLayout,
    QStackedLayout, QStyledItemDelegate, QDialog, QButtonGroup,
)

from core import APP_NAME, APP_VERSION, TAG_VALUE, ExifToolService, TagResult, bundled_path, collect_media, copy_without_overwrite, export_tagged_copies_batch, export_tagged_copy, find_exiftool, remove_source_after_verified_export, rename_tagged_file
from cleanup_app import AI_SUFFIX, restore_filename
from person_detector import (
    DetectionResult,
    MODEL_RELEASE_DATE,
    MODEL_RELATIVE_PATHS,
    MODEL_RUNTIME,
    MODEL_VERSION,
    ModelUnavailableError,
    PersonDetector,
    clear_detection_cache_file,
    detection_cache_file_info,
)
from update_manager import (
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
    authenticode_status,
    download_release,
    fetch_latest_release,
    is_installable_signature_status,
    is_newer_version,
    launch_installer,
    clear_update_install_marker,
    consume_update_startup_result,
    record_update_install_started,
    record_successful_check,
    should_check_automatically,
)


FEISHU_CONTACT_URL = (
    "https://www.feishu.cn/invitation/page/add_contact/"
    "?token=ecao2a09-025f-48ea-a005-245a4c86d7a6"
)

FILE_STATUS_TEXT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
FILE_STATUS_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 2
FILE_MEDIA_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 3
WINDOWS_APP_USER_MODEL_ID = "CBT.AIMediaTagTool"
DEVELOPER_NAME = "Xu Luo"
COMPANY_NAME = "深圳市艾润特贸易有限公司"
GITHUB_PROJECT_URL = "https://github.com/luoxu-ai/ai-media-tagger"
SETTINGS_ORGANIZATION = "CBT"
SETTINGS_APPLICATION = "AIMediaTagTool"


def show_information_message(parent: QWidget, title: str, text: str) -> None:
    """Show a localized information dialog without a retained focus marker."""
    dialog = QMessageBox(parent)
    dialog.setIcon(QMessageBox.Icon.Information)
    dialog.setWindowTitle(title)
    dialog.setText(text)
    confirm_button = dialog.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
    confirm_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    dialog.exec()


def install_qt_chinese_translations(app: QApplication) -> bool:
    """Localize standard Qt dialog buttons such as OK, Yes and No."""
    translator = QTranslator(app)
    loaded = translator.load(
        "qtbase_zh_CN",
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
    )
    if loaded:
        app.installTranslator(translator)
        app._qt_chinese_translator = translator
    return loaded


def automatic_update_checks_enabled() -> bool:
    value = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION).value(
        "updates/automatic", True
    )
    if isinstance(value, str):
        return value.casefold() not in {"false", "0", "no"}
    return bool(value)


def set_automatic_update_checks_enabled(enabled: bool) -> None:
    QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION).setValue(
        "updates/automatic", enabled
    )


def selected_theme() -> str:
    value = str(
        QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION).value(
            "appearance/theme", "system"
        )
    ).casefold()
    return value if value in {"system", "light", "dark"} else "system"


def set_selected_theme(theme: str) -> None:
    if theme not in {"system", "light", "dark"}:
        theme = "system"
    QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION).setValue(
        "appearance/theme", theme
    )


def effective_theme() -> str:
    theme = selected_theme()
    if theme != "system":
        return theme
    app = QApplication.instance()
    if app is not None:
        try:
            if app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                return "dark"
        except (AttributeError, RuntimeError):
            pass
    return "light"


def _set_windows_immersive_dark_mode(hwnd: int, dark: bool) -> bool:
    """Apply the Windows non-client title-bar theme to one native window."""
    import ctypes

    setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
    setter.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_uint,
    )
    setter.restype = ctypes.c_long
    enabled = ctypes.c_int(1 if dark else 0)
    for attribute in (20, 19):  # Windows 10 20H1+, then the older fallback.
        result = setter(
            ctypes.c_void_p(hwnd),
            attribute,
            ctypes.byref(enabled),
            ctypes.sizeof(enabled),
        )
        if result == 0:
            return True
    return False


def apply_windows_title_bar_theme(window: QWidget, dark: bool | None = None) -> bool:
    """Keep native Windows title bars aligned with the app-selected theme."""
    if os.name != "nt":
        return False
    try:
        return _set_windows_immersive_dark_mode(
            int(window.winId()),
            effective_theme() == "dark" if dark is None else dark,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


class WindowsTitleBarThemeFilter(QObject):
    """Theme every top-level window, including transient message boxes."""

    def eventFilter(self, watched, event):
        if (
            isinstance(watched, QWidget)
            and watched.isWindow()
            and event.type() in {QEvent.Type.Show, QEvent.Type.WinIdChange}
        ):
            apply_windows_title_bar_theme(watched)
        return super().eventFilter(watched, event)


def install_windows_title_bar_theme_filter(app: QApplication) -> None:
    if getattr(app, "_windows_title_bar_theme_filter", None) is not None:
        return
    theme_filter = WindowsTitleBarThemeFilter(app)
    app.installEventFilter(theme_filter)
    app._windows_title_bar_theme_filter = theme_filter


def legacy_log_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "AI媒体标签工具" / "logs"


def persistent_log_directory() -> Path:
    """Keep installed-build logs beside the application, not in a temp folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"
    return legacy_log_directory()


def persistent_session_path() -> Path | None:
    """Keep the last task outside temporary extraction and update folders."""
    if os.environ.get("QT_QPA_PLATFORM", "").casefold() == "offscreen":
        return None
    return legacy_log_directory().parent / "last_task.json"


def runtime_environment_issues() -> list[str]:
    """Run inexpensive startup checks without loading the large ONNX models."""
    issues: list[str] = []
    for relative in MODEL_RELATIVE_PATHS:
        model = bundled_path(relative)
        try:
            if not model.is_file() or model.stat().st_size <= 0:
                issues.append(f"人物识别模型缺失或损坏：{Path(relative).name}")
        except OSError:
            issues.append(f"无法读取人物识别模型：{Path(relative).name}")
    try:
        executable = find_exiftool()
        if executable.stat().st_size <= 0:
            issues.append("ExifTool 文件为空，请重新安装软件。")
    except (FileNotFoundError, OSError) as exc:
        issues.append(str(exc))

    log_folder = persistent_log_directory()
    probe = log_folder / f".write_test_{os.getpid()}"
    try:
        log_folder.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="ascii")
        probe.unlink(missing_ok=True)
        if shutil.disk_usage(log_folder).free < 512 * 1024 * 1024:
            issues.append("软件所在磁盘剩余空间不足 512 MB。")
    except OSError:
        issues.append("日志目录不可写，请检查安装目录权限。")
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
    return issues


_LOG_MIGRATION_ATTEMPTED = False


def migrate_legacy_logs() -> None:
    """Copy old LocalAppData reports into the installed logs folder once."""
    global _LOG_MIGRATION_ATTEMPTED
    if _LOG_MIGRATION_ATTEMPTED or not getattr(sys, "frozen", False):
        return
    _LOG_MIGRATION_ATTEMPTED = True
    source = legacy_log_directory()
    destination = persistent_log_directory()
    try:
        if source.resolve() == destination.resolve() or not source.exists():
            return
        destination.mkdir(parents=True, exist_ok=True)
        for old_log in source.glob("AI媒体标签导出报告_*.txt"):
            target = destination / old_log.name
            if not target.exists():
                shutil.copy2(old_log, target)
    except OSError:
        # Logging must never prevent the application from starting.
        pass


def persistent_log_files() -> list[Path]:
    """Return every saved task report, newest first."""
    migrate_legacy_logs()
    try:
        return sorted(
            persistent_log_directory().glob("AI媒体标签导出报告_*.txt"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []


def configure_windows_app_identity() -> None:
    """Give Windows a stable identity so the taskbar uses the EXE icon."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
    except (AttributeError, OSError):
        pass


def single_instance_server_name() -> str:
    """Return a stable ASCII-only local server name for the current user."""
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    user_key = hashlib.sha256(profile.casefold().encode("utf-8")).hexdigest()[:16]
    return f"ai_media_tagger_{user_key}"


def activate_main_window(window: QMainWindow) -> None:
    """Restore and focus the existing window after a second launch."""
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()
    if os.name == "nt":
        try:
            import ctypes

            hwnd = int(window.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except (AttributeError, OSError, ValueError):
            pass


class SingleInstanceController:
    """Allow one GUI process per Windows user and activate it on relaunch."""

    def __init__(self, server_name: str, on_activate):
        self.server_name = server_name
        self.on_activate = on_activate
        self.server = QLocalServer()
        self.server.newConnection.connect(self._handle_connections)

    def acquire_or_notify(self) -> bool:
        if self._notify_existing():
            return False
        QLocalServer.removeServer(self.server_name)
        if self.server.listen(self.server_name):
            return True
        # Handle two copies starting at almost exactly the same time.
        self._notify_existing()
        return False

    def _notify_existing(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(500):
            return False
        socket.write(b"activate")
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return True

    def _handle_connections(self) -> None:
        received = False
        while self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            if connection is None:
                continue
            if not connection.bytesAvailable():
                connection.waitForReadyRead(100)
            connection.readAll()
            connection.disconnectFromServer()
            received = True
        if received:
            QTimer.singleShot(0, self.on_activate)


# ==============================================================================
# 苹果极简白色风格 QSS 样式表
# ==============================================================================
STYLE = """
/* 全局基础设置 */
QMainWindow, QWidget {
    background-color: #f5f5f7;
    color: #1d1d1f;
    font-family: "Microsoft YaHei UI";
}

QLabel, QCheckBox {
    background: transparent;
    color: #1d1d1f;
}

/* 核心纯白卡片容器 */
QFrame#card {
    background-color: #ffffff;
    border: 1px solid #e5e5ea;
    border-radius: 16px;
}

/* 拖拽交互区 */
QFrame#dropZone {
    background-color: #fafafa;
    border: 1.5px dashed #d2d2d7;
    border-radius: 12px;
}

QFrame#dropZone:hover {
    background-color: #f2f7ff;
    border-color: #007aff;
}

/* 标题与副标题 */
QLabel#title {
    font-size: 28px;
    font-weight: 700;
    color: #1d1d1f;
    letter-spacing: -0.5px;
}

QPushButton#updateBadge {
    background-color: #ff3b30;
    color: #ffffff;
    border: none;
    border-radius: 11px;
    padding: 2px 9px;
    font-size: 12px;
    font-weight: 800;
}

QPushButton#updateBadge:hover {
    background-color: #e83228;
}

QPushButton#themeOption {
    min-height: 34px;
    min-width: 62px;
    padding: 0 14px;
    background-color: #ffffff;
    color: #0867df;
    border: 1px solid #9dc8ff;
    border-radius: 0;
    font-weight: 600;
}
QPushButton#themeOption[segment="first"] {
    border-top-left-radius: 7px;
    border-bottom-left-radius: 7px;
}
QPushButton#themeOption[segment="last"] {
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
}
QPushButton#themeOption:hover { background-color: #eef6ff; }
QPushButton#themeOption:checked {
    background-color: #1677ff;
    color: #ffffff;
    border-color: #1677ff;
}

QLabel#subtitle {
    color: #86868b;
    font-size: 14px;
}

/* 标签胶囊样式 */
QLabel#tag {
    background-color: #f2f2f7;
    color: #007aff;
    font-family: Consolas;
    font-size: 13px;
    font-weight: 600;
    padding: 6px 12px;
    border-radius: 10px;
}

QLabel#count {
    color: #1d1d1f;
    font-size: 14px;
    font-weight: 700;
}

/* 次要按钮（苹果浅灰胶囊形态） */
QPushButton {
    background-color: #f2f2f7;
    color: #007aff;
    border: none;
    border-radius: 18px;
    padding: 7px 16px;
    font-size: 13px;
    font-weight: 600;
    min-height: 22px;
}

QPushButton:hover {
    background-color: #e5e5ea;
}

QPushButton:pressed {
    background-color: #d1d1d6;
}

QPushButton:disabled {
    background-color: #f2f2f7;
    color: #c7c7cc;
}

/* 主要操作按钮（苹果蓝高亮胶囊） */
QPushButton#primary {
    background-color: #007aff;
    color: #ffffff;
    border: none;
    font-size: 14px;
    font-weight: 700;
    padding: 7px 16px;
    border-radius: 18px;
}

QPushButton#primary:hover {
    background-color: #0062cc;
}

QPushButton#primary:pressed {
    background-color: #004fb0;
}

QPushButton#primary:disabled {
    background-color: #e5e5ea;
    color: #a1a1a6;
}

/* 文件列表样式 */
QListWidget {
    background-color: #ffffff;
    border: 1px solid #e5e5ea;
    border-radius: 14px;
    font-size: 13px;
    outline: 0;
    padding: 4px;
}

QListWidget::item {
    padding: 8px 10px;
    border-radius: 10px;
    margin: 2px;
    color: #6e6e73;
    background-color: #f2f2f7;
    border: none;
}

QListWidget::item:hover {
    background-color: #e9e9ee;
}

QListWidget::item:selected {
    background-color: #e8f1ff;
    color: #007aff;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 6px 2px 6px 2px;
}

QScrollBar::handle:vertical {
    background-color: #c7c7cc;
    border-radius: 5px;
    min-height: 40px;
}

QScrollBar::handle:vertical:hover {
    background-color: #aeb0b5;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    border: none;
    background: transparent;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

/* 极简进度条 */
QProgressBar {
    border: none;
    background-color: #e5e5ea;
    border-radius: 8px;
    text-align: center;
    color: #86868b;
    font-size: 11px;
    height: 16px;
}

QProgressBar::chunk {
    background-color: #007aff;
    border-radius: 8px;
}

/* 底部状态提示 */
QLabel#status {
    color: #86868b;
    font-size: 12px;
}

/* 确认稿：Slate/Blue 桌面布局 */
QMainWindow, QWidget {
    background-color: #f6f8fb;
    color: #142033;
    font-family: "Segoe UI", "Microsoft YaHei UI";
}

QLabel#title {
    color: #142033;
    font-size: 30px;
    font-weight: 750;
}

QLabel#muted, QLabel#status {
    color: #64748b;
    font-size: 12px;
}

QLabel#sectionLabel, QLabel#count {
    color: #142033;
    font-size: 13px;
    font-weight: 700;
}

QLabel#dropTitle {
    color: #142033;
    font-size: 17px;
    font-weight: 700;
}

QLabel#emptyTitle {
    color: #44536a;
    font-size: 14px;
    font-weight: 700;
}

/* QWidget 的页面底色会被 QLabel 继承；文字标签必须保持透明，
   否则拖拽区悬停变色时会显出两条灰色矩形。 */
QLabel {
    background-color: transparent;
    border: none;
}

QFrame#card {
    background-color: #ffffff;
    border: 1px solid #e4e9f0;
    border-radius: 18px;
}

QFrame#dropZone {
    background-color: #f8fafc;
    border: 1.5px dashed #bac5d3;
    border-radius: 14px;
}

QFrame#dropZone:hover {
    background-color: #edf5ff;
    border-color: #1677ff;
}

QLabel#tag {
    background-color: #edf5ff;
    color: #0867df;
    border-radius: 12px;
    padding: 7px 13px;
}

QFrame#filesPanel, QFrame#actionPanel {
    background-color: #ffffff;
    border: 1px solid #e3e8ef;
    border-radius: 14px;
}

QWidget#fileHeader {
    background-color: #f7f9fc;
    border: none;
    border-bottom: 1px solid #e7ebf1;
    border-top-left-radius: 13px;
    border-top-right-radius: 13px;
}

QWidget#listHost, QWidget#emptyState {
    background-color: #ffffff;
    border: none;
    border-bottom-left-radius: 13px;
    border-bottom-right-radius: 13px;
}

QListWidget#fileList {
    background-color: #ffffff;
    border: none;
    border-bottom-left-radius: 13px;
    border-bottom-right-radius: 13px;
    padding: 6px;
    outline: none;
}

QListWidget#fileList::item {
    color: #253247;
    background-color: #f7f9fc;
    border-radius: 9px;
    margin: 2px;
    padding: 7px 10px;
}

QListWidget#fileList::item:hover { background-color: #f0f5fb; }
QListWidget#fileList::item:selected { background-color: #eaf3ff; color: #0867df; }

QPushButton {
    background-color: #eef2f7;
    color: #1e293b;
    border: none;
    border-radius: 10px;
    padding: 7px 16px;
    font-size: 13px;
    font-weight: 650;
    min-height: 24px;
}

QPushButton:hover { background-color: #e4e9f0; }
QPushButton:pressed { background-color: #d9e0e9; }
QPushButton:disabled { background-color: #edf0f4; color: #a2acb9; }
QPushButton:focus { border: 2px solid #1677ff; }

QPushButton#outlineAction {
    background-color: #ffffff;
    color: #0867df;
    border: 1px solid #b9d5fb;
}
QPushButton#outlineAction:hover { background-color: #edf5ff; border-color: #7db5fa; }

QPushButton#contactAction {
    background-color: #eaf3ff;
    color: #0867df;
    border: 1px solid #b9d5fb;
}
QPushButton#contactAction:hover { background-color: #dcecff; border-color: #7db5fa; }

QPushButton#smallAction {
    min-height: 20px;
    padding: 5px 12px;
    border-radius: 9px;
    color: #0867df;
}
QPushButton#smallAction:hover {
    background-color: #dcecff;
    color: #075ac8;
    border: 1px solid #7db5fa;
}
QPushButton#smallAction:pressed { background-color: #c9e0ff; }

QPushButton#secondaryAction {
    background-color: #eaf3ff;
    color: #0867df;
    border-radius: 11px;
    font-size: 14px;
    font-weight: 700;
}
QPushButton#secondaryAction:hover {
    background-color: #dcecff;
    color: #075ac8;
    border: 1px solid #7db5fa;
}
QPushButton#secondaryAction:pressed { background-color: #c9e0ff; }

QPushButton#smartPrimary {
    background-color: #1677ff;
    color: #ffffff;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 750;
    padding: 8px 22px;
}
QPushButton#smartPrimary:hover { background-color: #0867e8; }
QPushButton#smartPrimary:pressed { background-color: #075ac8; }
QPushButton#smartPrimary:disabled { background-color: #1677ff; color: #ffffff; }

QPushButton#stopAction {
    background-color: #fff1f0;
    color: #c9362b;
    border: 1px solid #ffc9c4;
    border-radius: 11px;
    font-size: 13px;
    font-weight: 700;
}
QPushButton#stopAction:hover { background-color: #ffe2df; border-color: #ff9f96; }
QPushButton#stopAction:pressed { background-color: #ffd3cf; }
QPushButton#stopAction:disabled { background-color: #edf0f4; color: #a2acb9; border: none; }

QProgressBar {
    background-color: #e7ebf1;
    border: none;
    border-radius: 4px;
    min-height: 7px;
    max-height: 7px;
}
QProgressBar::chunk { background-color: #1677ff; border-radius: 4px; }
"""

DARK_STYLE = """
QMainWindow, QWidget, QDialog {
    background-color: #101722;
    color: #e7edf6;
}
QLabel, QCheckBox { color: #e7edf6; background: transparent; }
QLabel#title, QLabel#sectionLabel, QLabel#count, QLabel#dropTitle {
    color: #f3f7fc;
}
QLabel#muted, QLabel#status, QLabel#subtitle { color: #9aa9bd; }
QFrame#card, QFrame#filesPanel, QFrame#actionPanel {
    background-color: #182231;
    border-color: #2b394b;
}
QFrame#dropZone {
    background-color: #141e2c;
    border-color: #52627a;
}
QFrame#dropZone:hover { background-color: #172a43; border-color: #4b9cff; }
QLabel#tag { background-color: #17345a; color: #71b1ff; }
QWidget#fileHeader {
    background-color: #202c3c;
    border-bottom-color: #304055;
}
QWidget#listHost, QWidget#emptyState, QListWidget#fileList {
    background-color: #182231;
}
QListWidget#fileList::item {
    color: #d6deea;
    background-color: #202c3c;
}
QListWidget#fileList::item:hover { background-color: #28374a; }
QListWidget#fileList::item:selected { background-color: #173b67; color: #75b6ff; }
QPushButton { background-color: #263447; color: #e5edf7; }
QPushButton:hover { background-color: #304158; }
QPushButton:pressed { background-color: #354a64; }
QPushButton:disabled { background-color: #202a38; color: #69778b; }
QPushButton#outlineAction {
    background-color: #182231;
    color: #75b6ff;
    border-color: #365f91;
}
QPushButton#outlineAction:hover { background-color: #17345a; border-color: #4b91df; }
QPushButton#smallAction { background-color: #202c3c; color: #75b6ff; }
QPushButton#smallAction:hover { background-color: #173b67; color: #9bc9ff; }
QPushButton#themeOption {
    background-color: #182231;
    color: #75b6ff;
    border-color: #365f91;
}
QPushButton#themeOption:hover { background-color: #17345a; }
QPushButton#themeOption:checked {
    background-color: #2f8cff;
    color: #ffffff;
    border-color: #2f8cff;
}
QPushButton#secondaryAction { background-color: #173b67; color: #8fc4ff; }
QPushButton#secondaryAction:hover { background-color: #1c4a80; color: #b4d9ff; }
QPushButton#stopAction { background-color: #4a2528; color: #ff9f98; border-color: #6b3438; }
QPushButton#stopAction:hover { background-color: #5a2b30; border-color: #8b4148; }
QPushButton#stopAction:disabled { background-color: #202a38; color: #69778b; border: none; }
QProgressBar { background-color: #2a3749; color: #a8b6c8; }
QProgressBar::chunk { background-color: #2f8cff; }
QTextEdit, QComboBox {
    background-color: #111a27;
    color: #e7edf6;
    border: 1px solid #33445a;
    border-radius: 8px;
    padding: 6px;
    selection-background-color: #1f5f9f;
}
QComboBox QAbstractItemView {
    background-color: #182231;
    color: #e7edf6;
    selection-background-color: #1f5f9f;
}
QScrollBar::handle:vertical { background-color: #52627a; }
QToolTip { background-color: #202c3c; color: #f3f7fc; border: 1px solid #52627a; }
"""


def apply_application_theme() -> None:
    app = QApplication.instance()
    if app is None:
        return
    dark = effective_theme() == "dark"
    app.setProperty("darkTheme", dark)
    app.setStyleSheet(STYLE + (DARK_STYLE if dark else ""))
    for window in app.topLevelWidgets():
        apply_windows_title_bar_theme(window, dark)


class CheckableListWidget(QListWidget):
    """A checkable list where clicking anywhere on a row toggles the item."""

    deleteRequested = Signal()

    def mousePressEvent(self, event: QMouseEvent):
        position = event.position().toPoint()
        item = self.itemAt(position)
        if item is None:
            super().mousePressEvent(event)
            return
        old_state = item.checkState()
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.rect = self.visualItemRect(item)
        option.checkState = old_state
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, option, self
        )
        super().mousePressEvent(event)
        if not indicator.contains(position):
            item.setCheckState(Qt.Unchecked if old_state == Qt.Checked else Qt.Checked)
        item.setSelected(item.checkState() == Qt.Checked)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class FileStatusDelegate(QStyledItemDelegate):
    """Paint a compact per-file state without creating thousands of widgets."""

    COLORS = {
        "pending": ("#667085", "#eef1f5"),
        "processing": ("#0867df", "#eaf3ff"),
        "success": ("#15803d", "#eaf7ee"),
        "skipped": ("#9a6700", "#fff6d8"),
        "failure": ("#c9362b", "#fff1f0"),
    }
    DARK_COLORS = {
        "pending": ("#b0bdd0", "#2a3749"),
        "processing": ("#8fc4ff", "#173b67"),
        "success": ("#7bd99a", "#183d2a"),
        "skipped": ("#ffd073", "#49391a"),
        "failure": ("#ff9f98", "#4a2528"),
    }

    @staticmethod
    def _badge_rect(option: QStyleOptionViewItem, width: int) -> QRect:
        return QRect(
            option.rect.right() - width - 14,
            option.rect.center().y() - 13,
            width,
            26,
        )

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        """Reserve enough height for two text lines at every DPI/font scale."""
        base = super().sizeHint(option, index)
        metrics = option.fontMetrics
        two_lines = metrics.lineSpacing() * 2 + 32
        return QSize(base.width(), max(72, base.height(), two_lines))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        status = index.data(FILE_STATUS_TEXT_ROLE)
        kind = index.data(FILE_STATUS_KIND_ROLE) or "pending"
        app = QApplication.instance()
        dark = app is not None and bool(app.property("darkTheme"))
        palette = (
            self.DARK_COLORS
            if dark
            else self.COLORS
        )
        foreground, background = palette.get(kind, palette["pending"])

        # Ask Qt to draw the complete row background and checkbox, but draw
        # both text lines ourselves so a fixed status column is always free.
        content_option = QStyleOptionViewItem(option)
        self.initStyleOption(content_option, index)
        full_text = content_option.text
        content_option.text = ""
        style = content_option.widget.style() if content_option.widget else QApplication.style()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            content_option,
            painter,
            content_option.widget,
        )
        # Some Windows styles leave a one-line text clip region active after
        # drawing the base item. Restore the painter before rendering our two
        # lines so the path is not cut in half.
        painter.restore()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(option.rect, Qt.ClipOperation.ReplaceClip)

        badge = None
        badge_font = content_option.font
        if status:
            badge_font.setBold(True)
            badge_font.setPointSizeF(max(9.0, badge_font.pointSizeF() - 0.5))
            painter.setFont(badge_font)
            width = max(
                76,
                min(148, painter.fontMetrics().horizontalAdvance(str(status)) + 24),
            )
            badge = self._badge_rect(option, width)

        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText,
            content_option,
            content_option.widget,
        )
        media_kind = index.data(FILE_MEDIA_KIND_ROLE) or "image"
        icon_rect = QRect(
            text_rect.left(), option.rect.center().y() - 11, 22, 22
        )
        media_kind_icon(str(media_kind)).paint(painter, icon_rect)
        text_rect.setLeft(icon_rect.right() + 8)
        text_rect.setRight(
            badge.left() - 12 if badge is not None else option.rect.right() - 18
        )
        if text_rect.width() > 20:
            painter.setFont(content_option.font)
            metrics = painter.fontMetrics()
            # QStyleOptionViewItem normalizes embedded newlines to U+2028 on
            # some Qt/Windows combinations, so splitlines() is required here.
            lines = full_text.splitlines()
            name = lines[0] if lines else full_text
            parent = " ".join(lines[1:])
            line_spacing = metrics.lineSpacing()
            combined_height = line_spacing * 2 + 4
            top = option.rect.center().y() - combined_height // 2
            # Give each line extra vertical padding. A rectangle that exactly
            # matches the font height clips glyphs on some Windows DPI/font
            # combinations, while these padded single-line rectangles keep the
            # filename and path visually separate.
            line_height = line_spacing + 6
            name_rect = QRect(text_rect.left(), top, text_rect.width(), line_height)
            parent_rect = QRect(
                text_rect.left(), top + line_spacing + 4, text_rect.width(), line_height
            )
            selected = bool(
                content_option.state & QStyle.StateFlag.State_Selected
            )
            if dark:
                name_color = "#75b6ff" if selected else "#d6deea"
                parent_color = "#75b6ff" if selected else "#aebbd0"
            else:
                name_color = "#0867df" if selected else "#253247"
                parent_color = "#0867df" if selected else "#5f6f86"
            text_palette = QPalette(content_option.palette)
            text_palette.setColor(QPalette.ColorRole.Text, QColor(name_color))
            style.drawItemText(
                painter,
                name_rect,
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextSingleLine,
                text_palette,
                True,
                metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, text_rect.width()),
                QPalette.ColorRole.Text,
            )
            text_palette.setColor(QPalette.ColorRole.Text, QColor(parent_color))
            style.drawItemText(
                painter,
                parent_rect,
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextSingleLine,
                text_palette,
                True,
                metrics.elidedText(parent, Qt.TextElideMode.ElideMiddle, text_rect.width()),
                QPalette.ColorRole.Text,
            )

        if badge is not None:
            painter.setFont(badge_font)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(badge, 9, 9)
            painter.setPen(QColor(foreground))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(status))
        painter.restore()


def media_file_icon(path: Path) -> QIcon:
    """Return a compact, unmistakable image/video icon for a file row."""
    kind = "video" if path.suffix.casefold() == ".mp4" else "image"
    return media_kind_icon(kind)


def media_kind_icon(kind: str) -> QIcon:
    """Return a cached icon; rows paint it lazily through the delegate."""
    cached = _MEDIA_FILE_ICONS.get(kind)
    if cached is not None:
        return cached

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1478f2" if kind == "video" else "#24a86b"))
    painter.drawRoundedRect(QRect(3, 3, 26, 26), 6, 6)
    painter.setBrush(QColor("#ffffff"))
    if kind == "video":
        painter.drawPolygon(QPolygon([QPoint(12, 9), QPoint(24, 16), QPoint(12, 23)]))
    else:
        painter.drawEllipse(QRect(8, 8, 5, 5))
        painter.drawPolygon(
            QPolygon(
                [
                    QPoint(7, 24),
                    QPoint(13, 16),
                    QPoint(17, 20),
                    QPoint(21, 14),
                    QPoint(26, 24),
                ]
            )
        )
    painter.end()
    icon = QIcon(pixmap)
    _MEDIA_FILE_ICONS[kind] = icon
    return icon


_MEDIA_FILE_ICONS: dict[str, QIcon] = {}


class SettingsDialog(QDialog):
    def __init__(
        self, on_check, on_show_log, on_export_log,
        parent: QWidget | None = None,
        on_cache_info=None, on_clear_cache=None, on_diagnostics=None,
        on_open_log_folder=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.resize(500, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(12)

        appearance = QLabel("外观")
        appearance.setObjectName("sectionLabel")
        layout.addWidget(appearance)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)
        theme_row.addWidget(QLabel("主题模式"))
        theme_row.addStretch()
        theme_options = QHBoxLayout()
        theme_options.setSpacing(0)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        current_theme = selected_theme()
        theme_values = (("浅色", "light"), ("深色", "dark"), ("系统", "system"))
        for index, (label, value) in enumerate(theme_values):
            option = QPushButton(label)
            option.setObjectName("themeOption")
            option.setProperty("themeValue", value)
            option.setProperty(
                "segment",
                "first" if index == 0 else "last" if index == len(theme_values) - 1 else "middle",
            )
            option.setCheckable(True)
            option.setChecked(value == current_theme)
            option.clicked.connect(
                lambda checked=False, theme=value: (
                    set_selected_theme(theme), apply_application_theme()
                ) if checked else None
            )
            self.theme_group.addButton(option)
            theme_options.addWidget(option)
        theme_row.addLayout(theme_options)
        layout.addLayout(theme_row)

        update_row = QHBoxLayout()
        update_row.setSpacing(12)
        self.automatic_check = QCheckBox("自动检查更新")
        self.automatic_check.setChecked(automatic_update_checks_enabled())
        self.automatic_check.toggled.connect(set_automatic_update_checks_enabled)
        update_row.addWidget(self.automatic_check)
        update_row.addStretch()
        self.check_update_button = QPushButton("立即检查更新")
        self.check_update_button.setObjectName("outlineAction")
        self.check_update_button.clicked.connect(lambda: on_check(True))
        update_row.addWidget(self.check_update_button)
        layout.addLayout(update_row)
        logs_heading = QLabel("处理日志")
        logs_heading.setObjectName("sectionLabel")
        layout.addWidget(logs_heading)
        log_buttons = QHBoxLayout()
        show_log = QPushButton("查看处理日志")
        show_log.setObjectName("outlineAction")
        show_log.clicked.connect(on_show_log)
        log_buttons.addWidget(show_log)
        export_log = QPushButton("导出日志")
        export_log.setObjectName("outlineAction")
        export_log.clicked.connect(on_export_log)
        log_buttons.addWidget(export_log)
        open_logs = QPushButton("打开日志文件夹")
        open_logs.setObjectName("outlineAction")
        open_logs.setEnabled(on_open_log_folder is not None)
        if on_open_log_folder is not None:
            open_logs.clicked.connect(on_open_log_folder)
        log_buttons.addWidget(open_logs)
        log_buttons.addStretch()
        layout.addLayout(log_buttons)
        maintenance_heading = QLabel("存储与诊断")
        maintenance_heading.setObjectName("sectionLabel")
        layout.addWidget(maintenance_heading)
        maintenance_row = QHBoxLayout()
        self.cache_status = QLabel(on_cache_info() if on_cache_info else "检测缓存：暂无")
        self.cache_status.setObjectName("muted")
        maintenance_row.addWidget(self.cache_status, 1)
        clear_cache = QPushButton("清除缓存")
        clear_cache.setObjectName("outlineAction")
        clear_cache.setEnabled(on_clear_cache is not None)
        if on_clear_cache is not None:
            clear_cache.clicked.connect(
                lambda: self.cache_status.setText(on_clear_cache())
            )
        maintenance_row.addWidget(clear_cache)
        diagnostics = QPushButton("系统诊断")
        diagnostics.setObjectName("outlineAction")
        diagnostics.setEnabled(on_diagnostics is not None)
        if on_diagnostics is not None:
            diagnostics.clicked.connect(on_diagnostics)
        maintenance_row.addWidget(diagnostics)
        layout.addLayout(maintenance_row)
        layout.addSpacing(2)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close_button = QPushButton("完成")
        close_button.setObjectName("primary")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, on_diagnostics=None):
        super().__init__(parent)
        self.setWindowTitle("关于我们")
        self.setModal(True)
        self.setMinimumSize(500, 370)
        self.resize(540, 410)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(11)

        name = QLabel(APP_NAME)
        name.setObjectName("title")
        layout.addWidget(name)
        layout.addWidget(QLabel(f"版本 v{APP_VERSION}"))
        developer = QLabel(f"开发者：{DEVELOPER_NAME}")
        developer.setObjectName("sectionLabel")
        layout.addWidget(developer)
        company = QLabel(f"公司：{COMPANY_NAME}")
        company.setObjectName("sectionLabel")
        layout.addWidget(company)
        layout.addWidget(QLabel(f"模型版本：{MODEL_VERSION}"))
        layout.addWidget(QLabel(f"模型发布日期：{MODEL_RELEASE_DATE}"))
        runtime = QLabel(f"运行方式：{MODEL_RUNTIME}")
        runtime.setObjectName("muted")
        layout.addWidget(runtime)
        statement = QLabel(
            "媒体扫描、人物识别和标签处理均在本地完成。"
            "检查更新仅获取 GitHub 版本信息；“反馈与联系”仅打开联系页面。"
            "软件不会自动上传图片、日志或文件名。"
        )
        statement.setObjectName("muted")
        statement.setWordWrap(True)
        layout.addWidget(statement)
        layout.addStretch()

        buttons = QHBoxLayout()
        self.contact_button = QPushButton("反馈与联系")
        self.contact_button.setObjectName("outlineAction")
        self.contact_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(FEISHU_CONTACT_URL))
        )
        buttons.addWidget(self.contact_button)
        project = QPushButton("GitHub 项目")
        project.setObjectName("outlineAction")
        project.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(GITHUB_PROJECT_URL))
        )
        buttons.addWidget(project)
        diagnostics = QPushButton("系统诊断")
        diagnostics.setObjectName("outlineAction")
        diagnostics.setEnabled(on_diagnostics is not None)
        if on_diagnostics is not None:
            diagnostics.clicked.connect(on_diagnostics)
        buttons.addWidget(diagnostics)
        buttons.addStretch()
        close_button = QPushButton("关闭")
        close_button.setObjectName("primary")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)


class UpdateDialog(QDialog):
    cancelUpdateRequested = Signal()

    def __init__(self, release: ReleaseInfo, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("软件更新")
        self.setModal(True)
        self.resize(520, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        heading = QLabel(f"发现新版本 v{release.version}")
        heading.setObjectName("sectionLabel")
        layout.addWidget(heading)
        current = QLabel(f"当前版本：v{APP_VERSION}")
        current.setObjectName("muted")
        layout.addWidget(current)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(release.notes.strip() or "本次版本包含功能优化和问题修复。")
        layout.addWidget(notes, 1)

        self.update_status = QLabel("点击“立即更新”后将在软件内下载并自动重启。")
        self.update_status.setWordWrap(True)
        self.update_status.setObjectName("muted")
        layout.addWidget(self.update_status)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 1000)
        self.update_progress.setValue(0)
        self.update_progress.setTextVisible(False)
        self.update_progress.hide()
        layout.addWidget(self.update_progress)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.later_button = QPushButton("稍后")
        self.later_button.setObjectName("outlineAction")
        self.later_button.clicked.connect(self._handle_secondary_action)
        buttons.addWidget(self.later_button)
        self.install_button = QPushButton("立即更新")
        self.install_button.setObjectName("primary")
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

        self.downloading = False

    def _handle_secondary_action(self) -> None:
        if self.downloading:
            self.cancelUpdateRequested.emit()
        else:
            self.reject()

    def reject(self) -> None:
        if self.downloading:
            self.cancelUpdateRequested.emit()
            return
        super().reject()

    def set_downloading(self) -> None:
        self.downloading = True
        self.install_button.setEnabled(False)
        self.later_button.setText("取消更新")
        self.later_button.setEnabled(True)
        self.update_progress.show()
        self.update_status.setText("正在下载新版，请不要关闭软件……")

    def set_cancel_pending(self) -> None:
        self.update_status.setText("正在取消更新……")
        self.later_button.setEnabled(False)

    def set_cancelled(self) -> None:
        self.downloading = False
        self.update_progress.hide()
        self.update_progress.setRange(0, 1000)
        self.update_progress.setValue(0)
        self.update_status.setText("更新已取消，未完成的下载文件已删除。")
        self.install_button.setText("立即更新")
        self.install_button.setEnabled(True)
        self.later_button.setText("关闭")
        self.later_button.setEnabled(True)

    def set_ready_to_install(self, unsigned: bool = False) -> None:
        self.downloading = False
        self.update_progress.show()
        self.update_progress.setRange(0, 1000)
        self.update_progress.setValue(1000)
        self.update_status.setText(
            "下载和 SHA-256 校验已完成；安装包尚未数字签名，安装前会再次确认。"
            if unsigned
            else "下载和安全校验已完成，可以安装新版。"
        )
        self.install_button.setText("安装并重启")
        self.install_button.setEnabled(True)
        self.later_button.setText("稍后")
        self.later_button.setEnabled(True)

    def set_progress(self, completed: int, total: int) -> None:
        if total > 0:
            value = min(1000, round(1000 * completed / total))
            self.update_progress.setRange(0, 1000)
            self.update_progress.setValue(value)
            self.update_status.setText(
                f"正在下载新版：{completed / 1024 / 1024:.1f} / "
                f"{total / 1024 / 1024:.1f} MB"
            )
        else:
            self.update_progress.setRange(0, 0)

    def set_error(self, message: str) -> None:
        self.downloading = False
        self.update_progress.setRange(0, 1000)
        self.update_progress.setValue(0)
        self.update_status.setText(
            f"{message}\n当前版本未受影响，可以关闭窗口继续使用，或重试下载。"
        )
        self.install_button.setEnabled(True)
        self.install_button.setText("重试下载")
        self.later_button.setEnabled(True)
        self.later_button.setText("关闭")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon_path = bundled_path("assets/app-icon.ico")
        if not icon_path.is_file():
            icon_path = bundled_path("assets/app-icon.png")
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._icon_path = icon_path if icon_path.is_file() else None
        self._native_icon_handles: list[int] = []
        self.resize(1180, 820)
        self.setMinimumSize(900, 650)
        self.setAcceptDrops(True)
        self.files: list[Path] = []
        self.tagged_file_keys: set[str] = set()
        self.file_statuses: dict[str, tuple[str, str]] = {}
        self._list_items_by_key: dict[str, QListWidgetItem] = {}
        self._list_indexes_by_key: dict[str, int] = {}
        self._checked_count = 0
        self.events: queue.Queue[tuple] = queue.Queue()
        self.running = False
        self.importing = False
        self.cancel_requested = threading.Event()
        self._detector: PersonDetector | None = None
        self._detector_lock = threading.Lock()
        self.current_destination: Path | None = None
        self.active_file_keys: set[str] = set()
        self.active_files: list[Path] = []
        self.log_lines: list[str] = []
        self.current_log_path: Path | None = None
        self._persisted_log_line_count = 0
        self.task_started_at = 0.0
        self._recent_item_seconds: list[float] = []
        self.processing_current = 0
        self.processing_total = 0
        self.available_release: ReleaseInfo | None = None
        self._update_check_in_progress = False
        self._update_download_in_progress = False
        self._update_cancel_requested = threading.Event()
        self._downloaded_update_path: Path | None = None
        self._downloaded_update_signature: str | None = None
        self._update_dialog: UpdateDialog | None = None
        self.environment_issues: list[str] = []
        self._restoring_session = False
        self._build_ui()
        self._install_keyboard_shortcuts()
        self._session_save_timer = QTimer(self)
        self._session_save_timer.setSingleShot(True)
        self._session_save_timer.timeout.connect(self._persist_session_state)
        self._restore_session_state()
        self._load_latest_log()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start(80)
        if getattr(sys, "frozen", False) and should_check_automatically():
            QTimer.singleShot(2500, self._start_update_check)
        if getattr(sys, "frozen", False):
            QTimer.singleShot(900, self._start_environment_check)
        self._startup_update_result = None
        if getattr(sys, "frozen", False):
            self._startup_update_result = consume_update_startup_result(
                APP_VERSION, clear=False
            )
            if self._startup_update_result is not None:
                QTimer.singleShot(1400, self._show_startup_update_result)

    def showEvent(self, event):
        super().showEvent(event)
        # A one-file PyInstaller app changes from its launcher process to the
        # extracted GUI process during startup. Refreshing WM_SETICON after the
        # native window exists prevents Windows from intermittently retaining
        # the launcher's blank taskbar icon.
        QTimer.singleShot(0, self._apply_windows_taskbar_icon)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "actionbar"):
            self._layout_action_buttons(event.size().width() < 1100)

    def _install_keyboard_shortcuts(self) -> None:
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in (
            (QKeySequence.StandardKey.Open, self.choose_files),
            (QKeySequence("Ctrl+Shift+O"), self.choose_folder),
            (QKeySequence.StandardKey.SelectAll, self.check_all),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        self.choose_file_button.setToolTip("选择文件（Ctrl+O）")
        self.choose_folder_button.setToolTip("选择文件夹（Ctrl+Shift+O）")
        self.select_all_button.setToolTip("全选（Ctrl+A）")

    def _show_startup_update_result(self) -> None:
        result = self._startup_update_result
        self._startup_update_result = None
        if result is None:
            return
        status, version = result
        try:
            if status == "completed":
                QMessageBox.information(
                    self, "更新完成", f"软件已成功更新至 v{version}。"
                )
            else:
                QMessageBox.warning(
                    self,
                    "更新未完成",
                    f"新版安装没有完成，当前仍可继续使用 v{version}。\n"
                    "你可以稍后重新检查更新。",
                )
        finally:
            # Keep the marker until the message has genuinely been displayed.
            # This prevents a short-lived installer-launched process from
            # consuming the only completion notification.
            clear_update_install_marker()

    def _apply_windows_taskbar_icon(self) -> None:
        if os.name != "nt" or self._icon_path is None:
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            image_icon = 1
            load_from_file = 0x0010
            default_size = 0x0040
            icon = self._native_icon_handles[0] if self._native_icon_handles else user32.LoadImageW(
                None,
                str(self._icon_path),
                image_icon,
                0,
                0,
                load_from_file | default_size,
            )
            if not icon:
                return
            if not self._native_icon_handles:
                self._native_icon_handles.append(int(icon))
            hwnd = int(self.winId())
            wm_seticon = 0x0080
            user32.SendMessageW(hwnd, wm_seticon, 0, icon)  # small icon
            user32.SendMessageW(hwnd, wm_seticon, 1, icon)  # large icon
        except (AttributeError, OSError, TypeError, ValueError):
            pass

    def _start_environment_check(self) -> None:
        def worker() -> None:
            self.events.put(("environment_check_done", runtime_environment_issues()))

        threading.Thread(target=worker, daemon=True).start()

    def _schedule_session_save(self) -> None:
        if self._restoring_session or persistent_session_path() is None:
            return
        self._session_save_timer.start(250)

    def _persist_session_state(self) -> None:
        state_path = persistent_session_path()
        if state_path is None:
            return
        items: list[dict[str, object]] = []
        for index, path in enumerate(self.files):
            key = str(path).casefold()
            item = self.list.item(index) if index < self.list.count() else None
            status = self.file_statuses.get(key)
            items.append({
                "path": str(path),
                "checked": bool(item is not None and item.checkState() == Qt.Checked),
                "tagged": key in self.tagged_file_keys,
                "status_text": status[0] if status else "",
                "status_kind": status[1] if status else "",
            })
        payload = {"version": 1, "saved_at": int(time.time()), "items": items}
        temporary = state_path.with_suffix(".tmp")
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, state_path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _restore_session_state(self) -> None:
        state_path = persistent_session_path()
        if state_path is None or not state_path.is_file():
            return
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            records = payload.get("items", []) if payload.get("version") == 1 else []
            if not isinstance(records, list):
                return
        except (OSError, ValueError, AttributeError):
            return

        files: list[Path] = []
        checked_keys: set[str] = set()
        tagged_keys: set[str] = set()
        statuses: dict[str, tuple[str, str]] = {}
        for record in records[:50000]:
            if not isinstance(record, dict):
                continue
            path = Path(str(record.get("path", "")))
            try:
                exists = path.is_file()
            except OSError:
                exists = False
            if not exists or path.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".mp4"}:
                continue
            key = str(path).casefold()
            files.append(path)
            if bool(record.get("tagged")):
                tagged_keys.add(key)
            text = str(record.get("status_text", ""))
            kind = str(record.get("status_kind", ""))
            if kind in {"pending", "processing"}:
                text, kind = "未处理", "pending"
                checked_keys.add(key)
            elif bool(record.get("checked")):
                checked_keys.add(key)
            if text and kind:
                statuses[key] = (text, kind)

        if not files:
            return
        self._restoring_session = True
        try:
            self.files = files
            self.tagged_file_keys = tagged_keys
            self.file_statuses = statuses
            self.refresh(checked_keys)
            self.status.setText(f"已恢复上次任务：{len(files)} 个文件")
        finally:
            self._restoring_session = False

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22, 24, 22, 22)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel(APP_NAME); title.setObjectName("title")
        header.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        self.update_badge = QPushButton("NEW")
        self.update_badge.setObjectName("updateBadge")
        self.update_badge.setFixedSize(44, 20)
        self.update_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_badge.setToolTip("发现新版本，点击更新")
        self.update_badge.clicked.connect(self.show_update_dialog)
        self.update_badge.hide()
        header.addWidget(self.update_badge, 0, Qt.AlignmentFlag.AlignTop)
        header.addStretch()
        outer.addLayout(header)
        outer.addSpacing(18)

        card = QFrame(); card.setObjectName("card")
        layout = QVBoxLayout(card); layout.setContentsMargins(18, 18, 18, 18); layout.setSpacing(18)

        tag_row = QHBoxLayout()
        fixed_label = QLabel("固定标签"); fixed_label.setObjectName("sectionLabel")
        tag_row.addWidget(fixed_label)
        tag = QLabel(TAG_VALUE); tag.setObjectName("tag"); tag_row.addWidget(tag)
        tag_row.addStretch()
        layout.addLayout(tag_row)

        files_panel = QFrame(); files_panel.setObjectName("filesPanel")
        files_layout = QVBoxLayout(files_panel); files_layout.setContentsMargins(0, 0, 0, 0); files_layout.setSpacing(0)
        file_header = QWidget(); file_header.setObjectName("fileHeader")
        file_header_layout = QHBoxLayout(file_header); file_header_layout.setContentsMargins(14, 10, 14, 10)
        self.count = QLabel("已选择：0 / 0 个文件"); self.count.setObjectName("count"); file_header_layout.addWidget(self.count)
        file_header_layout.addStretch()
        self.select_all_button = QPushButton("全选"); self.select_all_button.setObjectName("smallAction"); self.select_all_button.clicked.connect(self.check_all)
        self.select_none_button = QPushButton("取消全选"); self.select_none_button.setObjectName("smallAction"); self.select_none_button.clicked.connect(self.uncheck_all)
        self.clear_button = QPushButton("清空列表"); self.clear_button.setObjectName("smallAction"); self.clear_button.clicked.connect(self.clear_files)
        for button in (self.select_all_button, self.select_none_button, self.clear_button):
            # Remove the persistent mouse-click focus ring after activation,
            # while keeping the button reachable with Tab for keyboard users.
            button.clicked.connect(
                lambda checked=False, target=button: target.clearFocus()
            )
            file_header_layout.addWidget(button)
        files_layout.addWidget(file_header)

        list_host = QWidget(); list_host.setObjectName("listHost")
        self.list_stack = QStackedLayout(list_host); self.list_stack.setContentsMargins(0, 0, 0, 0)
        # The add-file controls are the list's empty state. As soon as files
        # are added, this view is replaced in-place by the actual file list.
        empty = QFrame(); empty.setObjectName("dropZone")
        empty_layout = QVBoxLayout(empty); empty_layout.setContentsMargins(20, 28, 20, 28); empty_layout.setSpacing(7); empty_layout.setAlignment(Qt.AlignCenter)
        empty_title = QLabel("将文件或文件夹拖到此处"); empty_title.setObjectName("dropTitle"); empty_title.setAlignment(Qt.AlignCenter)
        empty_subtitle = QLabel("支持递归扫描；自动忽略其他格式，已有标签文件保持未勾选")
        empty_subtitle.setObjectName("muted"); empty_subtitle.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_title); empty_layout.addWidget(empty_subtitle); empty_layout.addSpacing(8)
        add_buttons = QHBoxLayout(); add_buttons.addStretch()
        self.choose_file_button = QPushButton("选择文件")
        self.choose_folder_button = QPushButton("选择文件夹")
        for button, handler in (
            (self.choose_file_button, self.choose_files),
            (self.choose_folder_button, self.choose_folder),
        ):
            button.setObjectName("outlineAction")
            button.setFixedWidth(120)
            button.clicked.connect(handler)
            add_buttons.addWidget(button)
        add_buttons.addStretch(); empty_layout.addLayout(add_buttons)
        self.list = CheckableListWidget(); self.list.setObjectName("fileList"); self.list.setAlternatingRowColors(False); self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.setIconSize(QSize(22, 22))
        self.list.setItemDelegate(FileStatusDelegate(self.list))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.deleteRequested.connect(self.remove_selected_files)
        self.list_stack.addWidget(empty); self.list_stack.addWidget(self.list)
        files_layout.addWidget(list_host, 1)
        layout.addWidget(files_panel, 1)

        action_panel = QFrame(); action_panel.setObjectName("actionPanel")
        action_layout = QVBoxLayout(action_panel); action_layout.setContentsMargins(14, 12, 14, 12); action_layout.setSpacing(10)
        progress_header = QHBoxLayout()
        self.status = QLabel("等待添加文件"); self.status.setObjectName("status"); progress_header.addWidget(self.status)
        progress_header.addStretch()
        self.eta_label = QLabel("预计剩余：--"); self.eta_label.setObjectName("status"); progress_header.addWidget(self.eta_label)
        progress_header.addSpacing(12)
        self.progress_text = QLabel("0%"); self.progress_text.setObjectName("status"); progress_header.addWidget(self.progress_text)
        action_layout.addLayout(progress_header)
        self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0); self.progress.setTextVisible(False)
        action_layout.addWidget(self.progress)

        self.actionbar = QGridLayout()
        self.actionbar.setHorizontalSpacing(10)
        self.actionbar.setVerticalSpacing(8)
        self.settings_button = QPushButton("⚙ 设置")
        self.settings_button.setObjectName("outlineAction")
        self.settings_button.setFixedSize(110, 42)
        self.settings_button.clicked.connect(self.show_settings_dialog)
        self.about_button = QPushButton("关于我们")
        self.about_button.setObjectName("outlineAction")
        self.about_button.setFixedSize(110, 42)
        self.about_button.clicked.connect(self.show_about_dialog)
        self.stop_button = QPushButton("安全停止")
        self.stop_button.setObjectName("stopAction")
        self.stop_button.setFixedSize(110, 42)
        self.stop_button.clicked.connect(self.request_stop)
        self.cleanup_button = QPushButton("撤销标签")
        self.cleanup_button.setObjectName("outlineAction")
        self.cleanup_button.setFixedSize(110, 42)
        self.cleanup_button.clicked.connect(self.start_cleanup)
        self.start_button = QPushButton("导出已勾选（0）"); self.start_button.setObjectName("secondaryAction"); self.start_button.setFixedSize(205, 42); self.start_button.clicked.connect(self.start)
        self.smart_button = QPushButton("智能识别并导出")
        self.smart_button.setObjectName("smartPrimary"); self.smart_button.setFixedSize(230, 46)
        self.smart_button.clicked.connect(self.start_smart)
        self._actionbar_compact: bool | None = None
        self._layout_action_buttons(self.width() < 1100)
        action_layout.addLayout(self.actionbar)
        layout.addWidget(action_panel)

        outer.addWidget(card, 1)
        self._update_action_states()

    def _layout_action_buttons(self, compact: bool) -> None:
        """Keep every action visible when the main window becomes narrow."""
        if self._actionbar_compact is compact:
            return
        self._actionbar_compact = compact
        while self.actionbar.count():
            self.actionbar.takeAt(0)
        for column in range(7):
            self.actionbar.setColumnStretch(column, 0)
        if compact:
            self.actionbar.addWidget(self.settings_button, 0, 0)
            self.actionbar.addWidget(self.about_button, 0, 1)
            self.actionbar.setColumnStretch(2, 1)
            self.actionbar.addWidget(self.stop_button, 0, 3)
            self.actionbar.addWidget(self.cleanup_button, 0, 4)
            self.actionbar.addWidget(self.start_button, 1, 3)
            self.actionbar.addWidget(self.smart_button, 1, 4)
        else:
            self.actionbar.addWidget(self.settings_button, 0, 0)
            self.actionbar.addWidget(self.about_button, 0, 1)
            self.actionbar.setColumnStretch(2, 1)
            self.actionbar.addWidget(self.stop_button, 0, 3)
            self.actionbar.addWidget(self.cleanup_button, 0, 4)
            self.actionbar.addWidget(self.start_button, 0, 5)
            self.actionbar.addWidget(self.smart_button, 0, 6)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if not self.running and not self.importing and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        if not self.running and not self.importing:
            self.add_paths([url.toLocalFile() for url in event.mimeData().urls()])
            event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent):
        if self._update_download_in_progress:
            QMessageBox.information(
                self, APP_NAME, "新版正在下载，请等待更新完成后再关闭软件。"
            )
            event.ignore()
            return
        if self.importing:
            QMessageBox.information(self, APP_NAME, "正在导入文件，请等待扫描完成后再关闭程序。")
            event.ignore()
            return
        if self.running:
            QMessageBox.warning(self, APP_NAME, "文件正在处理中。请点击“安全停止”，等待当前批次完成后再关闭程序。")
            event.ignore()
            return
        if self._detector is not None:
            flush_cache = getattr(self._detector, "flush_cache", None)
            if flush_cache is not None:
                flush_cache()
        if self._session_save_timer.isActive():
            self._session_save_timer.stop()
        self._persist_session_state()
        self.timer.stop()
        event.accept()

    def _start_update_check(self, manual: bool = False) -> None:
        if self._update_check_in_progress:
            return
        self._update_check_in_progress = True

        def worker() -> None:
            try:
                release = fetch_latest_release()
                record_successful_check()
                if is_newer_version(release.version, APP_VERSION):
                    self.events.put(("update_available", release))
                elif manual:
                    self.events.put(("update_current",))
            except UpdateError as exc:
                if manual:
                    self.events.put(("update_check_error", str(exc)))
            finally:
                self.events.put(("update_check_finished",))

        threading.Thread(target=worker, daemon=True).start()

    def show_settings_dialog(self) -> None:
        dialog: SettingsDialog | None = None

        def check_now(_manual: bool = True) -> None:
            if dialog is not None:
                dialog.accept()
            self._start_update_check(manual=True)

        def show_logs() -> None:
            if dialog is not None:
                dialog.accept()
            self.show_log()

        def export_logs() -> None:
            if dialog is not None:
                dialog.accept()
            self.export_log()

        dialog = SettingsDialog(
            check_now,
            show_logs,
            export_logs,
            self,
            on_cache_info=self.detection_cache_summary,
            on_clear_cache=self.clear_detection_cache,
            on_diagnostics=self.show_system_diagnostics,
            on_open_log_folder=self.open_log_folder,
        )
        dialog.exec()

    def show_about_dialog(self) -> None:
        AboutDialog(self, on_diagnostics=self.show_system_diagnostics).exec()

    def open_log_folder(self) -> None:
        folder = persistent_log_directory()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"无法创建日志文件夹：{exc}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, APP_NAME, "无法打开日志文件夹。")

    def detection_cache_summary(self) -> str:
        with self._detector_lock:
            if self._detector is not None:
                count, size = self._detector.cache_info()
            else:
                count, size = detection_cache_file_info()
        return f"检测缓存：{count} 条，{size / 1024 / 1024:.1f} MB"

    def clear_detection_cache(self) -> str:
        if self.running or self.importing:
            QMessageBox.information(
                self, APP_NAME, "请等待当前导入或处理任务完成后再清除检测缓存。"
            )
            return self.detection_cache_summary()
        answer = QMessageBox.question(
            self,
            "清除检测缓存",
            "清除后，之前处理过的图片再次导入时需要重新识别。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return self.detection_cache_summary()
        try:
            with self._detector_lock:
                if self._detector is not None:
                    self._detector.clear_cache()
                else:
                    clear_detection_cache_file()
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"无法清除检测缓存：{exc}")
            return self.detection_cache_summary()
        return "检测缓存：已清除"

    def system_diagnostics_text(self) -> str:
        count, size = detection_cache_file_info()
        issues = runtime_environment_issues()
        return "\n".join(
            [
                f"软件版本：v{APP_VERSION}",
                f"系统：{platform.system()} {platform.release()} ({platform.machine()})",
                f"模型版本：{MODEL_VERSION}",
                f"模型运行方式：{MODEL_RUNTIME}",
                f"检测缓存：{count} 条，{size / 1024 / 1024:.1f} MB",
                f"ExifTool：{'异常' if any('ExifTool' in item for item in issues) else '正常'}",
                f"识别模型：{'异常' if any('模型' in item for item in issues) else '正常'}",
                f"日志写入：{'异常' if any('日志目录' in item for item in issues) else '正常'}",
                "隐私说明：诊断信息不包含图片、文件名或本机路径。",
                *( ["", "发现的问题：", *[f"- {item}" for item in issues]] if issues else [] ),
            ]
        )

    def show_system_diagnostics(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("系统诊断")
        dialog.resize(600, 420)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.system_diagnostics_text())
        layout.addWidget(text, 1)
        buttons = QHBoxLayout()
        copy_button = QPushButton("复制诊断信息")
        copy_button.setObjectName("outlineAction")
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(text.toPlainText())
        )
        buttons.addWidget(copy_button)
        buttons.addStretch()
        close_button = QPushButton("关闭")
        close_button.setObjectName("primary")
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        dialog.exec()

    def show_update_dialog(self) -> None:
        release = self.available_release
        if release is None:
            return
        dialog = UpdateDialog(release, self)
        dialog.install_button.clicked.connect(self.handle_update_action)
        dialog.cancelUpdateRequested.connect(self.request_cancel_update)
        self._update_dialog = dialog
        if self._downloaded_update_path is not None and self._downloaded_update_path.is_file():
            dialog.set_ready_to_install(
                unsigned=self._downloaded_update_signature == "NotSigned"
            )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def handle_update_action(self) -> None:
        downloaded = self._downloaded_update_path
        if downloaded is not None and downloaded.is_file():
            unsigned = self._downloaded_update_signature == "NotSigned"
            message = (
                "新版安装包尚未获得数字签名，但已通过 GitHub Release 提供的 "
                "SHA-256 完整性校验。Windows 可能显示 SmartScreen 提醒。\n\n"
                "仅当更新来自本软件内置的官方 GitHub 地址时才继续安装。现在是否继续？"
                if unsigned
                else
                "新版已下载并完成安全校验。现在将关闭软件、安装新版并重新启动，是否继续？"
            )
            answer = QMessageBox.question(
                self,
                "安装更新",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                (
                    QMessageBox.StandardButton.No
                    if unsigned
                    else QMessageBox.StandardButton.Yes
                ),
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._install_downloaded_update(downloaded)
            return
        self.begin_update_download()

    def request_cancel_update(self) -> None:
        if not self._update_download_in_progress:
            return
        self._update_cancel_requested.set()
        if self._update_dialog is not None:
            self._update_dialog.set_cancel_pending()

    def begin_update_download(self) -> None:
        release = self.available_release
        dialog = self._update_dialog
        if release is None or dialog is None or self._update_download_in_progress:
            return
        if self.running or self.importing:
            QMessageBox.information(
                self,
                APP_NAME,
                "请等待当前导入或处理任务完成后再更新。",
            )
            return
        answer = QMessageBox.question(
            self,
            "确认更新",
            f"即将下载 v{release.version}。下载完成后软件会自动关闭并重新启动，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._update_download_in_progress = True
        self._update_cancel_requested.clear()
        self._downloaded_update_path = None
        self._downloaded_update_signature = None
        dialog.set_downloading()

        def worker() -> None:
            try:
                downloaded = download_release(
                    release,
                    lambda completed, total: self.events.put(
                        ("update_download_progress", completed, total)
                    ),
                    cancelled=self._update_cancel_requested.is_set,
                )
                if self._update_cancel_requested.is_set():
                    downloaded.unlink(missing_ok=True)
                    raise UpdateCancelled("已取消更新。")
                signature = authenticode_status(downloaded)
                if self._update_cancel_requested.is_set():
                    downloaded.unlink(missing_ok=True)
                    raise UpdateCancelled("已取消更新。")
                if not is_installable_signature_status(signature):
                    downloaded.unlink(missing_ok=True)
                    raise UpdateError(
                        "新版数字签名状态异常，已停止安装。"
                    )
                self.events.put(("update_downloaded", downloaded, signature))
            except UpdateCancelled:
                self.events.put(("update_download_cancelled",))
            except (OSError, UpdateError) as exc:
                self.events.put(("update_download_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _install_downloaded_update(self, downloaded: Path) -> None:
        marker_created = False
        try:
            if self.available_release is not None:
                record_update_install_started(
                    self.available_release.version, APP_VERSION
                )
                marker_created = True
            launch_installer(downloaded)
        except (OSError, UpdateError) as exc:
            if marker_created:
                clear_update_install_marker()
            self._update_download_in_progress = False
            if self._update_dialog is not None:
                self._update_dialog.set_error(str(exc))
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        QApplication.quit()

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择媒体文件", "", "支持的媒体 (*.jpg *.jpeg *.png *.mp4);;所有文件 (*.*)")
        self.add_paths(paths)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择媒体文件夹")
        if path: self.add_paths([path])

    def add_paths(self, paths: list[str]):
        if self.running or self.importing or not paths:
            return
        checked_keys = {str(path).casefold() for path in self.checked_files()}
        known = {str(path).casefold() for path in self.files}
        self.importing = True
        self.status.setText("正在导入：扫描文件中…")
        self.eta_label.setText("正在扫描")
        # The number of files is not known until recursive discovery finishes.
        # Qt's 0..0 range gives useful activity feedback during that phase.
        self.progress.setRange(0, 0)
        self.progress_text.setText("扫描中")
        self._update_action_states()
        threading.Thread(
            target=self._import_worker,
            args=(list(paths), known, checked_keys),
            daemon=True,
        ).start()

    def _import_worker(
        self, paths: list[str], known: set[str], checked_keys: set[str]
    ) -> None:
        try:
            discovered = collect_media(paths)
            new_files = [path for path in discovered if str(path).casefold() not in known]
            already_tagged: set[str] = set()
            renamed_tagged = 0
            if new_files:
                service = ExifToolService(find_exiftool())
                total = len(new_files)
                self.events.put(("import_progress", 0, total, "正在检查已有标签"))
                # ExifTool remains substantially faster in batches, while
                # bounded batches let the UI report real per-file progress.
                batch_size = 250
                for offset in range(0, total, batch_size):
                    batch = new_files[offset:offset + batch_size]
                    already_tagged.update(service.paths_with_target_subject(batch))
                    completed = min(offset + len(batch), total)
                    self.events.put(
                        ("import_progress", completed, total, "正在检查已有标签")
                    )
            imported_files: list[Path] = []
            tagged_keys: set[str] = set()
            for path in new_files:
                if str(path).casefold() in already_tagged:
                    current = rename_tagged_file(path)
                    renamed_tagged += int(current != path)
                    imported_files.append(current)
                    tagged_keys.add(str(current).casefold())
                else:
                    imported_files.append(path)
            self.events.put(
                (
                    "import_done", imported_files, checked_keys, tagged_keys,
                    len(already_tagged), renamed_tagged,
                )
            )
        except Exception as exc:
            self.events.put(("import_error", str(exc)))

    def clear_files(self):
        if not self.running and not self.importing:
            self.files.clear()
            self.tagged_file_keys.clear()
            self.file_statuses.clear()
            self._list_items_by_key.clear()
            self._list_indexes_by_key.clear()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress_text.setText("0%")
            self.refresh(set())

    def remove_selected_files(self):
        """Remove highlighted rows from the list without touching source files."""
        if self.running or self.importing or not self.files:
            return
        rows = {self.list.row(item) for item in self.list.selectedItems()}
        if not rows and self.list.currentRow() >= 0:
            rows.add(self.list.currentRow())
        valid_rows = sorted((row for row in rows if 0 <= row < len(self.files)), reverse=True)
        if not valid_rows:
            return
        checked_keys = {str(path).casefold() for path in self.checked_files()}
        removed_keys = {str(self.files[row]).casefold() for row in valid_rows}
        for row in valid_rows:
            del self.files[row]
        self.refresh(checked_keys - removed_keys)
        self.tagged_file_keys.difference_update(removed_keys)
        for key in removed_keys:
            self.file_statuses.pop(key, None)
        self.status.setText(f"已从列表移除 {len(valid_rows)} 个文件；原文件未删除")

    def refresh(self, checked_keys: set[str] | None = None):
        if checked_keys is None:
            checked_keys = {str(path).casefold() for path in self.files}
        self.list.setUpdatesEnabled(False)
        self.list.blockSignals(True)
        try:
            self.list.clear()
            self._list_items_by_key.clear()
            self._list_indexes_by_key.clear()
            for index, path in enumerate(self.files):
                key = str(path).casefold()
                item = QListWidgetItem(f"{path.name}\n{path.parent}")
                item.setData(
                    FILE_MEDIA_KIND_ROLE,
                    "video" if path.suffix.casefold() == ".mp4" else "image",
                )
                row_height = max(72, self.list.fontMetrics().lineSpacing() * 2 + 32)
                item.setSizeHint(QSize(0, row_height))
                item.setToolTip(str(path))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if key in checked_keys else Qt.Unchecked)
                status = self.file_statuses.get(key)
                if status is None and key in self.tagged_file_keys:
                    status = ("已有标签", "success")
                    self.file_statuses[key] = status
                if status is not None:
                    item.setData(FILE_STATUS_TEXT_ROLE, status[0])
                    item.setData(FILE_STATUS_KIND_ROLE, status[1])
                accessible_status = status[0] if status is not None else "未处理"
                item.setData(
                    Qt.ItemDataRole.AccessibleTextRole,
                    f"{path.name}，{accessible_status}",
                )
                item.setData(
                    Qt.ItemDataRole.AccessibleDescriptionRole,
                    f"路径：{path.parent}",
                )
                self.list.addItem(item)
                self._list_items_by_key[key] = item
                self._list_indexes_by_key[key] = index
                item.setSelected(item.checkState() == Qt.Checked)
        finally:
            self.list.blockSignals(False)
            self.list.setUpdatesEnabled(True)
        self.list_stack.setCurrentIndex(1 if self.files else 0)
        self.update_checked_count()
        self.status.setText("文件已就绪" if self.files else "等待添加文件")
        self._schedule_session_save()

    def _set_file_status(self, path: Path, text: str, kind: str) -> None:
        key = str(path).casefold()
        self.file_statuses[key] = (text, kind)
        item = self._list_items_by_key.get(key)
        if item is not None:
            item.setData(FILE_STATUS_TEXT_ROLE, text)
            item.setData(FILE_STATUS_KIND_ROLE, kind)
            path = self.files[self._list_indexes_by_key[key]]
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                f"{path.name}，{text}",
            )
            if kind == "processing":
                # Do not recenter on every file. Advance the viewport only
                # when the active row would otherwise fall below its bottom.
                self.list.scrollToItem(item, QAbstractItemView.EnsureVisible)
        self._schedule_session_save()

    def _set_file_checked(self, path: Path, checked: bool) -> None:
        item = self._list_items_by_key.get(str(path).casefold())
        if item is None:
            return
        target = Qt.Checked if checked else Qt.Unchecked
        if item.checkState() == target:
            return
        self.list.blockSignals(True)
        try:
            item.setCheckState(target)
            item.setSelected(checked)
        finally:
            self.list.blockSignals(False)
        self._checked_count += 1 if checked else -1
        self._checked_count = max(0, min(self._checked_count, len(self.files)))
        self.count.setText(f"已选择：{self._checked_count} / {len(self.files)} 个文件")
        self.start_button.setText(f"导出已勾选（{self._checked_count}）")
        self._schedule_session_save()

    def _replace_file_path(self, source: Path, replacement: Path) -> Path:
        """Keep a completed row and point it at the verified output file."""
        old_key = str(source).casefold()
        new_path = replacement.resolve()
        new_key = str(new_path).casefold()
        index = self._list_indexes_by_key.pop(old_key, None)
        item = self._list_items_by_key.pop(old_key, None)
        if index is None or item is None:
            return new_path
        self.files[index] = new_path
        self._list_indexes_by_key[new_key] = index
        self._list_items_by_key[new_key] = item
        status = self.file_statuses.pop(old_key, None)
        if status is not None:
            self.file_statuses[new_key] = status
        self.tagged_file_keys.discard(old_key)
        item.setText(f"{new_path.name}\n{new_path.parent}")
        item.setData(
            FILE_MEDIA_KIND_ROLE,
            "video" if new_path.suffix.casefold() == ".mp4" else "image",
        )
        item.setToolTip(str(new_path))
        item.setData(
            Qt.ItemDataRole.AccessibleTextRole,
            f"{new_path.name}，{status[0] if status is not None else '未处理'}",
        )
        item.setData(
            Qt.ItemDataRole.AccessibleDescriptionRole,
            f"路径：{new_path.parent}",
        )
        self._schedule_session_save()
        return new_path

    def _prepare_file_statuses(self, files: list[Path]) -> None:
        self.list.setUpdatesEnabled(False)
        try:
            for path in files:
                self._set_file_status(path, "等待处理", "pending")
        finally:
            self.list.setUpdatesEnabled(True)
            self.list.viewport().update()

    def _show_processing_progress(
        self, current: int | None = None, total: int | None = None
    ) -> None:
        if total is not None:
            self.processing_total = max(0, total)
        if current is not None:
            self.processing_current = min(
                max(0, current), self.processing_total or max(0, current)
            )
        if self.processing_total:
            self.status.setText(
                f"处理中：{self.processing_current}/{self.processing_total}"
            )
        else:
            self.status.setText("处理中")

    def checked_files(self) -> list[Path]:
        checked: list[Path] = []
        for index, path in enumerate(self.files):
            if self.list.item(index).checkState() == Qt.Checked:
                checked.append(path)
        return checked

    def update_checked_count(self):
        selected = sum(self.list.item(i).checkState() == Qt.Checked for i in range(self.list.count()))
        self._checked_count = selected
        self.count.setText(f"已选择：{selected} / {len(self.files)} 个文件")
        self.start_button.setText(f"导出已勾选（{selected}）")
        self._update_action_states()

    def _update_action_states(self):
        enabled = not self.running and not self.importing
        self.choose_file_button.setEnabled(enabled)
        self.choose_folder_button.setEnabled(enabled)
        # Keep list/export controls interactive while idle so their hover
        # feedback remains visible in every list state (including an empty
        # list, fully selected list, or zero selected items). The handlers are
        # safe no-ops where appropriate; export shows an actionable prompt.
        self.select_all_button.setEnabled(enabled)
        self.select_none_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.cleanup_button.setEnabled(enabled)
        self.start_button.setEnabled(enabled)
        # Keep the primary smart action visible and usable even with an empty
        # list; _start_export provides the actionable "先添加文件" prompt.
        self.smart_button.setEnabled(enabled)
        self.stop_button.setEnabled(self.running and not self.cancel_requested.is_set())

    def request_stop(self):
        """Stop after the current file reaches a safe, verified boundary."""
        if not self.running or self.cancel_requested.is_set():
            return
        self.cancel_requested.set()
        self.stop_button.setEnabled(False)
        self.stop_button.setText("正在停止…")
        self.status.setText("已请求安全停止；正在完成当前文件…")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        if seconds < 60:
            return f"约 {max(1, seconds)} 秒"
        if seconds < 3600:
            return f"约 {(seconds + 59) // 60} 分钟"
        hours, remainder = divmod(seconds, 3600)
        minutes = (remainder + 59) // 60
        return f"约 {hours} 小时 {minutes} 分钟" if minutes else f"约 {hours} 小时"

    def _update_eta(self, progress_value: int) -> None:
        """Keep progress updates separate from the item-based ETA."""
        if not self.running:
            return

    def _update_eta_by_items(
        self, completed: int, total: int, item_seconds: float
    ) -> None:
        """Estimate remaining time from completed files, not phase progress.

        The detector can finish an easy image in one view while a difficult
        image needs four tiled rechecks.  A rolling sample reacts to that
        change without letting one unusually slow file dominate the display.
        """
        if not self.running or total <= 0 or completed <= 0:
            return
        self._recent_item_seconds.append(max(0.0, item_seconds))
        if len(self._recent_item_seconds) > 120:
            del self._recent_item_seconds[:-120]
        if completed < 12:
            self.eta_label.setText("预计剩余：正在估算")
            return
        elapsed = max(0.0, time.time() - self.task_started_at)
        overall_average = elapsed / completed
        recent = self._recent_item_seconds[-min(80, len(self._recent_item_seconds)):]
        recent_average = statistics.median(recent) if recent else overall_average
        seconds_per_file = overall_average * 0.55 + recent_average * 0.45
        remaining = seconds_per_file * max(0, total - completed)
        self.eta_label.setText(f"预计剩余：{self._format_duration(remaining)}")

    def _on_item_changed(self, item: QListWidgetItem):
        item.setSelected(item.checkState() == Qt.Checked)
        self.update_checked_count()
        self._schedule_session_save()

    def check_all(self):
        self._set_all_checked(Qt.Checked)

    def uncheck_all(self):
        self._set_all_checked(Qt.Unchecked)

    def _set_all_checked(self, state: Qt.CheckState):
        """Update every row in one batch instead of recounting after each item."""
        self.list.blockSignals(True)
        try:
            for index in range(self.list.count()):
                item = self.list.item(index)
                item.setCheckState(state)
                item.setSelected(state == Qt.Checked)
        finally:
            self.list.blockSignals(False)
        self.update_checked_count()
        self._schedule_session_save()

    def start(self):
        if self.running:
            return
        if not self.checked_files():
            QMessageBox.information(self, APP_NAME, "请先添加文件并勾选需要处理的项目。")
            return
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Warning)
        confirm.setWindowTitle("确认导出")
        confirm.setText("该操作不会识别人物。")
        confirm.setInformativeText("所有已勾选文件都会直接添加标签，是否继续？")
        continue_button = confirm.addButton("继续导出", QMessageBox.AcceptRole)
        cancel_button = confirm.addButton("取消", QMessageBox.RejectRole)
        confirm.setDefaultButton(cancel_button)
        confirm.exec()
        if confirm.clickedButton() is not continue_button:
            return
        self._start_export(smart=False)

    def start_smart(self):
        self._start_export(smart=True)

    def start_cleanup(self):
        """Remove the fixed tag and trailing _AI marker in this window."""
        if self.running or self.importing:
            return
        selected = self.checked_files()
        if not selected:
            QMessageBox.information(self, APP_NAME, "请先勾选需要撤销标签的文件。")
            return
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Warning)
        confirm.setWindowTitle("确认撤销标签")
        confirm.setText("将移除固定标签并恢复文件名。")
        confirm.setInformativeText("只处理同时含目标标签、且名称以 _AI 结尾的已勾选文件。")
        continue_button = confirm.addButton("开始撤销", QMessageBox.AcceptRole)
        cancel_button = confirm.addButton("取消", QMessageBox.RejectRole)
        confirm.setDefaultButton(cancel_button)
        confirm.exec()
        if confirm.clickedButton() is not continue_button:
            return
        try:
            executable = find_exiftool()
        except FileNotFoundError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self.running = True
        self.cancel_requested.clear()
        self._prepare_file_statuses(selected)
        self._show_processing_progress(0, len(selected))
        self.active_files = list(selected)
        self.active_file_keys = {str(path).casefold() for path in selected}
        self._recent_item_seconds.clear()
        self.task_started_at = time.time()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress_text.setText("0%")
        self.eta_label.setText("预计剩余：正在估算")
        self.stop_button.setText("安全停止")
        self.status.setText("处理中")
        self.log_lines = [
            f"{APP_NAME} - 标签撤销报告", "=" * 72, "",
            f"开始时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"目标标签：{TAG_VALUE}", f"任务文件：{len(selected)} 个", "",
        ]
        self._begin_persistent_log()
        self._update_action_states()
        threading.Thread(
            target=self._cleanup_worker, args=(executable, selected), daemon=True
        ).start()

    def _start_export(self, smart: bool):
        if self.running: return
        selected = [
            path for path in self.checked_files()
            if str(path).casefold() not in self.tagged_file_keys
        ]
        if not selected:
            QMessageBox.information(
                self, APP_NAME,
                "已勾选文件都含有目标标签，无需重复处理。"
                "\n如需移除标签，请点击“撤销标签”。",
            )
            return
        destination_label = "各原文件所在目录"
        self.current_destination = None
        self.active_file_keys = {str(path).casefold() for path in selected}
        self.active_files = list(selected)
        try: executable = find_exiftool()
        except FileNotFoundError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc)); return
        self.running = True
        self.cancel_requested.clear()
        self._prepare_file_statuses(selected)
        self._show_processing_progress(0, len(selected))
        self.stop_button.setText("安全停止")
        self._update_action_states()
        self.progress.setRange(0, 1000); self.progress.setValue(0)
        self.progress_text.setText("0%")
        self.eta_label.setText("预计剩余：正在估算")
        self.task_started_at = time.time()
        self._recent_item_seconds.clear()
        self.log_lines = [
            f"{APP_NAME} - 媒体标签导出报告",
            "=" * 72,
            "",
            "一、任务信息",
            "-" * 72,
            f"开始时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"目标标签：{TAG_VALUE}",
            f"导出目录：{destination_label}",
            f"任务文件：{len(selected)} 个",
            f"处理模式：{'智能人物识别（图片识别；MP4直接标记）' if smart else '手动选择直接标记'}",
            "支持格式：JPG、JPEG、PNG、MP4",
            "其他格式：自动忽略，不显示、不复制、不处理",
            "原图处理：副本写入并复核成功后将原图移至回收站，仅保留 _AI 文件",
            "安全策略：复制、写入、复核或回收站操作任一步骤失败时保留原图",
            "覆盖策略：不覆盖任何已有文件；同名时自动添加编号",
            "验证方式：每个副本写入后重新读取 XMP dc:Subject 进行复核",
            "",
            "二、处理明细",
            "-" * 72,
            "",
        ]
        self._begin_persistent_log()
        target = self._smart_worker if smart else self._worker
        args = (executable, selected)
        threading.Thread(target=target, args=args, daemon=True).start()

    def _cleanup_worker(self, executable: Path, files: list[Path]) -> None:
        service = ExifToolService(executable)
        success = skipped = failed = processed = 0
        total = len(files)
        batch_size = 20
        for offset in range(0, total, batch_size):
            if self.cancel_requested.is_set():
                break
            chunk = files[offset:offset + batch_size]
            for chunk_index, source in enumerate(chunk, start=offset + 1):
                self.events.put((
                    "file_status", source, "处理中", "processing", chunk_index, total,
                ))
            started = time.perf_counter()
            eligible: list[Path] = []
            states = service.read_subjects(chunk)
            preliminary: dict[str, tuple[str, str]] = {}
            for source in chunk:
                key = str(source).casefold()
                readable, values, detail = states[key]
                if AI_SUFFIX.search(source.stem) is None:
                    preliminary[key] = ("skipped", "文件名末尾没有 _AI，已跳过")
                elif not readable:
                    preliminary[key] = ("failed", detail or "无法读取 XMP 标签")
                elif TAG_VALUE not in values:
                    preliminary[key] = ("skipped", "文件不含目标标签，已跳过")
                else:
                    eligible.append(source)
            removals = service.remove_target_subjects_batch(eligible)
            per_file_seconds = (time.perf_counter() - started) / max(1, len(chunk))
            for source in chunk:
                key = str(source).casefold()
                current = source
                if key in preliminary:
                    outcome, detail = preliminary[key]
                else:
                    result = removals[key]
                    if not result.success:
                        outcome, detail = "failed", result.message
                    else:
                        try:
                            current, rename_detail = restore_filename(source)
                            outcome = "success"
                            detail = f"{result.message}；{rename_detail}"
                        except OSError as exc:
                            outcome, detail = "failed", f"标签已移除，但文件名恢复失败：{exc}"
                success += int(outcome == "success")
                skipped += int(outcome == "skipped")
                failed += int(outcome == "failed")
                processed += 1
                self.events.put((
                    "cleanup_result", processed, total, source, current,
                    outcome, detail, per_file_seconds,
                ))
            if self.cancel_requested.is_set():
                break
        event_name = "cleanup_cancelled" if self.cancel_requested.is_set() else "cleanup_done"
        self.events.put((event_name, success, skipped, failed, processed, total))

    def _get_detector(self) -> PersonDetector:
        """Load the recognition model off the UI thread and reuse it."""
        with self._detector_lock:
            if self._detector is None:
                self._detector = PersonDetector()
            return self._detector

    def _smart_worker(self, executable: Path, files: list[Path]):
        self.events.put(("status", "正在加载人物识别模型…"))
        try:
            detector = self._get_detector()
        except Exception as exc:
            self.events.put(("model_error", str(exc)))
            return
        service = ExifToolService(executable)
        exported_count = skipped_count = failed_count = 0
        total = len(files)
        recognized_count = written_count = finalized_count = 0

        def emit_phase(status: str) -> None:
            work = 0.70 * recognized_count + 0.20 * written_count + 0.10 * finalized_count
            value = min(999, round(1000 * work / total)) if total else 0
            self.events.put(("phase_progress", value, status))

        processed_count = 0
        for index, source in enumerate(files, start=1):
            if self.cancel_requested.is_set():
                break
            item_started = time.perf_counter()
            self.events.put((
                "file_status", source, "处理中", "processing", index, total,
            ))
            try:
                detection = detector.detect(source)
            except Exception as exc:
                detection = DetectionResult(
                    False, 0.0, f"识别异常：{exc}", 0.0, error=True
                )
            recognized_count += 1
            emit_phase(f"已识别 {index}/{total}：{source.name}")

            # If stop was requested during recognition, leave this source
            # untouched and checked for a later resume.
            if self.cancel_requested.is_set():
                break

            exported: Path | None = None
            if detection.error:
                result: TagResult | None = TagResult(source, False, detection.reason)
            elif not detection.detected:
                result = None
            else:
                try:
                    exported, result = export_tagged_copy(service, source, source.parent)
                except Exception as exc:
                    result = TagResult(source, False, f"导出异常：{exc}")
                written_count += 1
                emit_phase(f"正在复核标签：{source.name}")
                if result.success and source.exists():
                    result = remove_source_after_verified_export(source, exported, result)
                finalized_count += 1
                emit_phase(f"已完成原图处理：{source.name}")

            if not detection.detected or detection.error:
                written_count += 1
                finalized_count += 1
            if result is None:
                skipped_count += 1
            elif result.success:
                exported_count += 1
            else:
                failed_count += 1
            self.events.put(("smart_result", index, total, source, exported, detection, result))
            processed_count += 1
            self.events.put((
                "eta_sample", processed_count, total,
                time.perf_counter() - item_started,
            ))
            if self.cancel_requested.is_set():
                break
        flush_cache = getattr(detector, "flush_cache", None)
        if flush_cache is not None:
            flush_cache()
        if self.cancel_requested.is_set():
            self.events.put((
                "smart_cancelled", exported_count, skipped_count, failed_count,
                processed_count, total,
            ))
        else:
            self.events.put(("smart_done", exported_count, skipped_count, failed_count, total))

    def _worker(self, executable: Path, files: list[Path]):
        service = ExifToolService(executable); success = 0
        total = len(files)
        processed_count = 0
        for index, source in enumerate(files, start=1):
            if self.cancel_requested.is_set():
                break
            item_started = time.perf_counter()
            self.events.put((
                "file_status", source, "处理中", "processing", index, total,
            ))
            try:
                exported, result = export_tagged_copy(service, source, source.parent)
            except Exception as exc:
                exported, result = None, TagResult(source, False, f"导出失败：{exc}")
            if result.success and source.exists():
                result = remove_source_after_verified_export(source, exported, result)
            success += int(result.success)
            processed_count += 1
            value = min(999, round(1000 * processed_count / total)) if total else 0
            self.events.put(("phase_progress", value, f"已处理 {index}/{total}：{source.name}"))
            self.events.put(("result", index, total, source, exported, result))
            self.events.put((
                "eta_sample", processed_count, total,
                time.perf_counter() - item_started,
            ))
            if self.cancel_requested.is_set():
                break
        if self.cancel_requested.is_set():
            self.events.put(("cancelled", success, processed_count, total))
        else:
            self.events.put(("done", success, total))

    @staticmethod
    def _friendly_failure(detail: str) -> str:
        lower = detail.lower()
        mappings = (
            (("file format error", "not a valid", "corrupt"), "媒体文件可能已损坏，或文件内容与扩展名不一致。"),
            (("permission denied", "access is denied", "read-only"), "无法写入导出副本，请检查目标文件夹权限。"),
            (("error opening", "no such file"), "无法读取文件，文件可能已移动、被占用或不可访问。"),
            (("timeout", "超时"), "处理时间超过限制，请确认文件可正常读取后重试。"),
            (("strict.pm", "@inc"), "内置标签组件未能正常启动，请重新下载完整软件。"),
        )
        for needles, message in mappings:
            if any(needle in lower for needle in needles):
                return message
        return "标签导出未完成，请根据下方技术信息检查文件。"

    def _append_log_result(self, index: int, total: int, source: Path, exported: Path | None, result: TagResult):
        status = "成功" if result.success else "失败"
        self.log_lines.extend([
            f"[{index:04d}/{total:04d}] {status}",
            f"  原文件：{source}",
            f"  导出文件：{exported if exported else '未生成（失败副本已清理）'}",
            f"  原图状态：{('已永久删除，仅保留已验证的 _AI 文件' if '永久删除' in result.message else '已移至回收站，仅保留已验证的 _AI 文件') if not source.exists() else '已保留'}",
        ])
        if result.success:
            self.log_lines.extend([
                f"  标签状态：{result.message}",
                "  验证结果：XMP dc:Subject 已包含目标标签",
            ])
        else:
            self.log_lines.extend([
                f"  失败说明：{self._friendly_failure(result.message)}",
                f"  技术信息：{result.message}",
            ])
        self.log_lines.append("")
        self._persist_log()

    def _append_smart_log_result(
        self, index: int, total: int, source: Path, exported: Path | None,
        detection: DetectionResult, result: TagResult | None,
    ):
        if result is None:
            status = "未检测到人物，未导出"
        elif result.success:
            status = "识别通过并导出成功"
        else:
            status = "识别或导出失败"
        self.log_lines.extend([
            f"[{index:04d}/{total:04d}] {status}",
            f"  原文件：{source}",
            f"  识别结论：{detection.reason}",
            f"  最高置信度：{detection.confidence:.3f}",
            f"  分阶段结果：{detection.details or '当前文件未提供分阶段信息'}",
            f"  结果来源：{'持久化检测缓存' if detection.cached else '本次模型检测'}",
            f"  识别耗时：{detection.elapsed_seconds:.2f} 秒；检查次数：{detection.passes}",
            f"  导出文件：{exported if exported else '未生成'}",
            f"  原图状态：{('已永久删除，仅保留已验证的 _AI 文件' if result is not None and '永久删除' in result.message else '已移至回收站，仅保留已验证的 _AI 文件') if not source.exists() else '已保留'}",
        ])
        if result is not None:
            self.log_lines.append(f"  标签结果：{result.message}")
        self.log_lines.append("")
        self._persist_log()

    def _append_log_summary(self, success: int, total: int):
        failed = total - success
        elapsed = max(0.0, time.time() - self.task_started_at)
        rate = (success / total * 100) if total else 0.0
        average = (elapsed / total) if total else 0.0
        conclusion = "全部文件导出并验证成功。" if failed == 0 else "部分文件未完成，请查看上方失败项目。"
        self.log_lines.extend([
            "三、任务汇总",
            "-" * 72,
            f"结束时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"总文件数：{total}",
            f"成功数量：{success}",
            f"失败数量：{failed}",
            f"成功率：{rate:.1f}%",
            f"总耗时：{elapsed:.2f} 秒",
            f"平均耗时：{average:.2f} 秒/文件",
            "导出目录：各原文件所在目录",
            f"处理结论：{conclusion}",
            "",
            "说明：成功项目已在标签复核后将原图移至回收站；失败项目保留原图。",
            "=" * 72,
        ])
        self._persist_log()

    def _begin_persistent_log(self) -> None:
        """Create an auto-saved task log that survives application restarts."""
        try:
            migrate_legacy_logs()
            folder = persistent_log_directory()
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            candidate = folder / f"AI媒体标签导出报告_{stamp}.txt"
            index = 2
            while candidate.exists():
                candidate = folder / f"AI媒体标签导出报告_{stamp}_{index}.txt"
                index += 1
            self.current_log_path = candidate
            self._persisted_log_line_count = 0
            self._persist_log()
        except OSError:
            self.current_log_path = None

    def _persist_log(self) -> None:
        if self.current_log_path is None:
            return
        try:
            self.current_log_path.parent.mkdir(parents=True, exist_ok=True)
            if (
                not self.current_log_path.exists()
                or self._persisted_log_line_count > len(self.log_lines)
            ):
                self.current_log_path.write_text(
                    "\n".join(self.log_lines), encoding="utf-8-sig"
                )
                self._persisted_log_line_count = len(self.log_lines)
                return
            pending = self.log_lines[self._persisted_log_line_count :]
            if not pending:
                return
            prefix = "\n" if self.current_log_path.stat().st_size > 3 else ""
            with self.current_log_path.open("a", encoding="utf-8") as output:
                output.write(prefix + "\n".join(pending))
            self._persisted_log_line_count = len(self.log_lines)
        except OSError:
            pass

    def _load_latest_log(self) -> None:
        """Restore the most recent report for the next application session."""
        try:
            latest = persistent_log_files()[0]
            self.current_log_path = latest
            self.log_lines = latest.read_text(encoding="utf-8-sig").splitlines()
            self._persisted_log_line_count = len(self.log_lines)
        except (IndexError, OSError):
            pass

    def _finalize_completed_files(self):
        """Keep every processed row while leaving all completed inputs unchecked."""
        retained_checked = {
            str(path).casefold() for path in self.checked_files()
            if str(path).casefold() not in self.active_file_keys
        }
        existing_keys = {str(path).casefold() for path in self.files}
        self.tagged_file_keys.intersection_update(existing_keys)
        self.active_file_keys.clear()
        self.active_files.clear()
        self.cancel_requested.clear()
        self.stop_button.setText("安全停止")
        self.refresh(retained_checked)

    def _finalize_cancelled_files(self, processed_count: int):
        """Keep unprocessed inputs checked so a stopped job can be resumed."""
        processed_keys = {
            str(path).casefold() for path in self.active_files[:processed_count]
        }
        for path in self.active_files[processed_count:]:
            self._set_file_status(path, "未处理", "pending")
        retained_checked = {
            str(path).casefold() for path in self.checked_files()
            if str(path).casefold() not in processed_keys
        }
        existing_keys = {str(path).casefold() for path in self.files}
        self.tagged_file_keys.intersection_update(existing_keys)
        self.active_file_keys.clear()
        self.active_files.clear()
        self.cancel_requested.clear()
        self.stop_button.setText("安全停止")
        self.refresh(retained_checked)

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "status":
                    if self.running:
                        self._show_processing_progress()
                    else:
                        self.status.setText(event[1])
                elif event[0] == "update_available":
                    self.available_release = event[1]
                    self.update_badge.setToolTip(
                        f"发现新版本 v{event[1].version}，点击更新"
                    )
                    self.update_badge.show()
                elif event[0] == "update_current":
                    show_information_message(
                        self, APP_NAME, f"当前已是最新版本 v{APP_VERSION}。"
                    )
                elif event[0] == "update_check_error":
                    QMessageBox.warning(self, APP_NAME, event[1])
                elif event[0] == "update_check_finished":
                    self._update_check_in_progress = False
                elif event[0] == "environment_check_done":
                    self.environment_issues = list(event[1])
                    if self.environment_issues:
                        QMessageBox.warning(
                            self,
                            "运行环境检查",
                            "发现以下问题：\n\n"
                            + "\n".join(f"• {issue}" for issue in self.environment_issues)
                            + "\n\n请重新安装软件，或检查磁盘空间与目录权限。",
                        )
                elif event[0] == "update_download_progress":
                    if self._update_dialog is not None:
                        self._update_dialog.set_progress(event[1], event[2])
                elif event[0] == "update_download_error":
                    self._update_download_in_progress = False
                    self._downloaded_update_signature = None
                    if self._update_dialog is not None:
                        self._update_dialog.set_error(event[1])
                    QMessageBox.warning(self, APP_NAME, event[1])
                elif event[0] == "update_download_cancelled":
                    self._update_download_in_progress = False
                    self._downloaded_update_path = None
                    self._downloaded_update_signature = None
                    if self._update_dialog is not None:
                        self._update_dialog.set_cancelled()
                elif event[0] == "update_downloaded":
                    self._update_download_in_progress = False
                    self._downloaded_update_path = event[1]
                    self._downloaded_update_signature = event[2]
                    if self._update_dialog is not None:
                        self._update_dialog.set_ready_to_install(
                            unsigned=event[2] == "NotSigned"
                        )
                elif event[0] == "file_status":
                    _, source, text, kind, *progress = event
                    self._set_file_status(source, text, kind)
                    if self.running:
                        if len(progress) == 2:
                            self._show_processing_progress(progress[0], progress[1])
                        else:
                            self._show_processing_progress()
                elif event[0] == "import_progress":
                    _, completed, total, stage = event
                    total = max(1, total)
                    completed = min(max(0, completed), total)
                    self.progress.setRange(0, total)
                    self.progress.setValue(completed)
                    self.status.setText(f"正在导入：{completed}/{total}")
                    self.eta_label.setText(stage)
                    self.progress_text.setText(f"{round(100 * completed / total)}%")
                elif event[0] == "import_done":
                    (
                        _, new_files, checked_keys, tagged_keys,
                        tagged_count, renamed_tagged,
                    ) = event
                    self.importing = False
                    self.files.extend(new_files)
                    self.tagged_file_keys.update(tagged_keys)
                    checked_keys.update(
                        str(path).casefold() for path in new_files
                        if str(path).casefold() not in tagged_keys
                    )
                    self.files.sort(key=lambda path: str(path).casefold())
                    self.refresh(checked_keys)
                    if new_files:
                        self.progress.setRange(0, len(new_files))
                        self.progress.setValue(len(new_files))
                        self.progress_text.setText("100%")
                        self.eta_label.setText("导入完成")
                    else:
                        self.progress.setRange(0, 1)
                        self.progress.setValue(0)
                        self.progress_text.setText("0%")
                        self.eta_label.setText("预计剩余：--")
                    if tagged_count:
                        self.status.setText(
                            f"已添加 {len(new_files)} 个文件；{tagged_count} 个已有标签文件保持未勾选，"
                            f"其中 {renamed_tagged} 个已补充 _AI 文件名"
                        )
                    else:
                        self.status.setText(f"已添加 {len(new_files)} 个新文件")
                    self._update_action_states()
                elif event[0] == "import_error":
                    self.importing = False
                    self.progress.setRange(0, 1)
                    self.progress.setValue(0)
                    self.progress_text.setText("0%")
                    self.eta_label.setText("预计剩余：--")
                    self.status.setText("文件导入失败")
                    self._update_action_states()
                    QMessageBox.warning(
                        self, APP_NAME, f"读取现有标签失败，文件尚未加入：\n{event[1]}"
                    )
                elif event[0] == "model_error":
                    self.log_lines.extend([
                        "", "任务中止：人物识别模型加载失败", f"错误信息：{event[1]}",
                        f"中止时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "=" * 72,
                    ])
                    self._persist_log()
                    self.running = False
                    self._finalize_cancelled_files(0)
                    self._update_action_states()
                    self.progress.setValue(0)
                    self.progress_text.setText("0%")
                    self.eta_label.setText("预计剩余：--")
                    QMessageBox.critical(self, APP_NAME, event[1])
                elif event[0] == "phase_progress":
                    _, value, _status = event
                    value = max(self.progress.value(), value)
                    self.progress.setValue(value)
                    self.progress_text.setText(f"{value // 10}%")
                    self._show_processing_progress()
                    self._update_eta(value)
                elif event[0] == "eta_sample":
                    _, completed, total, item_seconds = event
                    self._update_eta_by_items(completed, total, item_seconds)
                elif event[0] == "smart_progress":
                    _, index, total, source = event
                    value = max(self.progress.value(), round(1000 * index / total) if total else 0)
                    self.progress.setValue(value)
                    self.progress_text.setText(f"{value // 10}%")
                    self._set_file_status(source, "处理中", "processing")
                    self._show_processing_progress(index, total)
                    self._update_eta(value)
                elif event[0] == "result":
                    _, index, total, source, exported, result = event
                    self._append_log_result(index, total, source, exported, result)
                    self._set_file_checked(source, False)
                    display_path = source
                    if result.success and exported is not None:
                        display_path = self._replace_file_path(source, exported)
                        self.tagged_file_keys.add(str(display_path).casefold())
                    self._set_file_status(
                        display_path, "已导出" if result.success else "失败",
                        "success" if result.success else "failure",
                    )
                    self._show_processing_progress(index, total)
                elif event[0] == "smart_result":
                    _, index, total, source, exported, detection, result = event
                    self._append_smart_log_result(index, total, source, exported, detection, result)
                    self._set_file_checked(source, False)
                    display_path = source
                    if result is not None and result.success and exported is not None:
                        display_path = self._replace_file_path(source, exported)
                        self.tagged_file_keys.add(str(display_path).casefold())
                    if result is None:
                        self._set_file_status(display_path, "未检测到人物", "skipped")
                    elif result.success:
                        self._set_file_status(display_path, "已导出", "success")
                    else:
                        self._set_file_status(display_path, "失败", "failure")
                    self._show_processing_progress(index, total)
                elif event[0] == "cleanup_result":
                    (
                        _, index, total, source, current, outcome,
                        detail, item_seconds,
                    ) = event
                    labels = {
                        "success": "撤销成功",
                        "skipped": "已跳过",
                        "failed": "撤销失败",
                    }
                    self.log_lines.extend([
                        f"[{index:04d}/{total:04d}] {labels[outcome]}",
                        f"  原文件：{source}",
                        f"  当前文件：{current}",
                        f"  处理结果：{detail}", "",
                    ])
                    self._persist_log()
                    value = min(999, round(1000 * index / total)) if total else 0
                    self.progress.setValue(value)
                    self.progress_text.setText(f"{value // 10}%")
                    self._set_file_checked(source, False)
                    display_path = source
                    if outcome == "success" and current != source:
                        display_path = self._replace_file_path(source, current)
                        self.tagged_file_keys.discard(str(display_path).casefold())
                    self._set_file_status(display_path, labels[outcome], "success" if outcome == "success" else "skipped" if outcome == "skipped" else "failure")
                    self._show_processing_progress(index, total)
                    self._update_eta_by_items(index, total, item_seconds)
                elif event[0] in {"cleanup_done", "cleanup_cancelled"}:
                    _, success, skipped, failed, processed, total = event
                    cancelled = event[0] == "cleanup_cancelled"
                    self.running = False
                    if cancelled:
                        self._finalize_cancelled_files(processed)
                    else:
                        self._finalize_completed_files()
                    self._update_action_states()
                    remaining = max(0, total - processed)
                    if cancelled:
                        self.eta_label.setText("预计剩余：已停止")
                        self.status.setText(
                            f"已停止撤销：完成 {processed}/{total}，剩余 {remaining}"
                        )
                    else:
                        self.progress.setValue(1000)
                        self.progress_text.setText("100%")
                        self.eta_label.setText("预计剩余：已完成")
                        self.status.setText(
                            f"撤销完成：成功 {success}，跳过 {skipped}，失败 {failed}"
                        )
                    self.log_lines.extend([
                        "任务汇总", "-" * 72,
                        f"结束时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                        f"成功：{success}", f"跳过：{skipped}", f"失败：{failed}",
                        f"未处理：{remaining}", "=" * 72,
                    ])
                    self._persist_log()
                    QMessageBox.information(
                        self, APP_NAME,
                        ("撤销任务已停止" if cancelled else "撤销完成")
                        + f"\n\n成功：{success}\n跳过：{skipped}\n失败：{failed}"
                        + (f"\n未处理：{remaining}" if cancelled else ""),
                    )
                elif event[0] == "smart_done":
                    _, exported_count, skipped_count, failed_count, total = event
                    self.running = False
                    self._finalize_completed_files()
                    self.progress.setValue(1000)
                    self.progress_text.setText("100%")
                    self.eta_label.setText("预计剩余：已完成")
                    self._update_action_states()
                    self.status.setText(f"智能处理完成：导出 {exported_count}，未检测到人物 {skipped_count}，失败 {failed_count}")
                    self.log_lines.extend([
                        "三、智能识别汇总", "-" * 72,
                        f"结束时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                        f"总文件数：{total}", f"识别通过并导出：{exported_count}",
                        f"未检测到人物、未导出：{skipped_count}", f"处理失败：{failed_count}",
                        "导出目录：各原文件所在目录",
                        "说明：识别通过且标签复核成功的项目已将原图移至回收站。",
                        "说明：MP4 不进行画面识别，按规则直接添加标签。",
                        "说明：未通过项目建议人工检查单独手脚等人体局部。", "=" * 72,
                    ])
                    self._persist_log()
                    QMessageBox.information(
                        self, APP_NAME,
                        f"智能处理完成\n\n已导出：{exported_count}\n未检测到人物：{skipped_count}\n失败：{failed_count}\n\n导出目录：各原文件所在目录",
                    )
                elif event[0] == "smart_cancelled":
                    _, exported_count, skipped_count, failed_count, processed_count, total = event
                    remaining = max(0, total - processed_count)
                    self.running = False
                    self._finalize_cancelled_files(processed_count)
                    self._update_action_states()
                    self.eta_label.setText("预计剩余：已停止")
                    self.status.setText(
                        f"已安全停止：完成 {processed_count}/{total}，剩余 {remaining} 个文件"
                    )
                    self.log_lines.extend([
                        "三、任务已安全停止", "-" * 72,
                        f"停止时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                        f"总文件数：{total}",
                        f"已完成：{processed_count}",
                        f"剩余待处理：{remaining}",
                        f"识别通过并导出：{exported_count}",
                        f"未检测到人物、未导出：{skipped_count}",
                        f"处理失败：{failed_count}",
                        "说明：当前批次已完整结束，未处理文件仍保持勾选，可直接继续处理。",
                        "=" * 72,
                    ])
                    self._persist_log()
                    QMessageBox.information(
                        self, APP_NAME,
                        f"任务已安全停止。\n\n已完成：{processed_count}/{total}\n"
                        f"剩余：{remaining}\n\n未处理文件已保留勾选，可直接继续。",
                    )
                elif event[0] == "cancelled":
                    _, success, processed_count, total = event
                    failed = processed_count - success
                    remaining = max(0, total - processed_count)
                    self.running = False
                    self._finalize_cancelled_files(processed_count)
                    self._update_action_states()
                    self.eta_label.setText("预计剩余：已停止")
                    self.status.setText(
                        f"已安全停止：完成 {processed_count}/{total}，剩余 {remaining} 个文件"
                    )
                    self.log_lines.extend([
                        "三、任务已安全停止", "-" * 72,
                        f"停止时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                        f"总文件数：{total}",
                        f"已完成：{processed_count}",
                        f"成功：{success}",
                        f"失败：{failed}",
                        f"剩余待处理：{remaining}",
                        "说明：当前批次已完整结束，未处理文件仍保持勾选，可直接继续处理。",
                        "=" * 72,
                    ])
                    self._persist_log()
                    QMessageBox.information(
                        self, APP_NAME,
                        f"任务已安全停止。\n\n已完成：{processed_count}/{total}\n"
                        f"剩余：{remaining}\n\n未处理文件已保留勾选，可直接继续。",
                    )
                else:
                    _, success, total = event; failed = total - success
                    self._append_log_summary(success, total)
                    self.running = False
                    self._finalize_completed_files()
                    self.progress.setValue(1000)
                    self.progress_text.setText("100%")
                    self.eta_label.setText("预计剩余：已完成")
                    self._update_action_states()
                    self.status.setText(f"处理完成：成功 {success}，失败 {failed}")
                    destination = "各原文件所在目录"
                    if failed: QMessageBox.warning(self, APP_NAME, f"处理完成\n成功：{success}\n失败：{failed}\n导出目录：{destination}\n\n可查看处理日志了解详情。")
                    else: QMessageBox.information(self, APP_NAME, f"全部处理成功，共 {success} 个文件。\n\n导出目录：{destination}")
        except queue.Empty: pass

    def show_log(self):
        dialog = QMainWindow(self); dialog.setWindowTitle("媒体标签导出报告"); dialog.resize(900, 620)
        reports: list[str] = []
        for path in persistent_log_files():
            try:
                content = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            reports.extend([
                f"历史任务文件：{path.name}",
                "=" * 72,
                content,
                "\n",
            ])
        if not reports and self.log_lines:
            reports.append("\n".join(self.log_lines))
        text = QTextEdit(); text.setReadOnly(True); text.setPlainText("\n".join(reports) if reports else "暂无处理日志。")
        dialog.setCentralWidget(text); dialog.show(); self._log_window = dialog

    def export_log(self):
        if not self.log_lines:
            QMessageBox.information(self, APP_NAME, "暂无可导出的处理日志。"); return
        path, _ = QFileDialog.getSaveFileName(self, "导出处理报告", f"AI媒体标签导出报告_{time.strftime('%Y%m%d_%H%M%S')}.txt", "文本文件 (*.txt)")
        if path:
            Path(path).write_text("\n".join(self.log_lines), encoding="utf-8-sig")
            QMessageBox.information(self, APP_NAME, "日志已导出。")

    def open_contact(self):
        """Open the contact page predictably in the default browser."""
        if not QDesktopServices.openUrl(QUrl(FEISHU_CONTACT_URL)):
            QMessageBox.warning(self, APP_NAME, "无法打开联系页面，请检查默认浏览器设置。")


def main():
    self_test_file = os.environ.get("AI_TAG_SELFTEST_SOURCE")
    self_test_target = os.environ.get("AI_TAG_SELFTEST_TARGET")
    self_test_result = os.environ.get("AI_TAG_SELFTEST_RESULT")
    if self_test_file and self_test_target and self_test_result:
        try:
            source = Path(self_test_file)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            detection = None
            if os.environ.get("AI_TAG_SELFTEST_SMART") == "1":
                detection = PersonDetector().detect(source)
            if detection is not None and not detection.detected:
                exported = None
                result = TagResult(source, True, "未检测到人物，按智能规则未导出")
            else:
                exported, result = export_tagged_copy(ExifToolService(find_exiftool()), source, Path(self_test_target))
            after = hashlib.sha256(source.read_bytes()).hexdigest()
            Path(self_test_result).write_text(
                f"success={result.success}\nsource_unchanged={before == after}\n"
                f"detected={detection.detected if detection is not None else 'not_run'}\n"
                f"confidence={detection.confidence if detection is not None else 'not_run'}\n"
                f"reason={detection.reason if detection is not None else 'not_run'}\n"
                f"message={result.message}\nexported={exported}", encoding="utf-8"
            )
        except Exception as exc:
            Path(self_test_result).write_text(f"success=False\nmessage={exc}", encoding="utf-8")
        return
    configure_windows_app_identity()
    app = QApplication(sys.argv)
    install_qt_chinese_translations(app)
    install_windows_title_bar_theme_filter(app)
    icon_path = bundled_path("assets/app-icon.ico")
    if not icon_path.is_file():
        icon_path = bundled_path("assets/app-icon.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_application_theme()
    try:
        app.styleHints().colorSchemeChanged.connect(
            lambda _scheme: apply_application_theme()
            if selected_theme() == "system" else None
        )
    except AttributeError:
        pass
    window = MainWindow()
    instance = SingleInstanceController(single_instance_server_name(), lambda: activate_main_window(window))
    if not instance.acquire_or_notify():
        return
    window.show(); sys.exit(app.exec())


if __name__ == "__main__": main()
