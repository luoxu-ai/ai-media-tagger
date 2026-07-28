from __future__ import annotations

import queue
import os
import hashlib
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QSize, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, QMouseEvent, QKeyEvent, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout, QWidget, QListWidgetItem, QStyle, QStyleOptionViewItem,
    QStackedLayout,
)

from core import APP_NAME, TAG_VALUE, ExifToolService, TagResult, bundled_path, collect_media, copy_without_overwrite, export_tagged_copies_batch, export_tagged_copy, find_exiftool, remove_source_after_verified_export, rename_tagged_file
from person_detector import DetectionResult, ModelUnavailableError, PersonDetector


FEEDBACK_URL = "https://github.com/luoxu-ai/ai-media-tagger/issues/new"
WINDOWS_APP_USER_MODEL_ID = "CBT.AIMediaTagTool"


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

QProgressBar {
    background-color: #e7ebf1;
    border: none;
    border-radius: 4px;
    min-height: 7px;
    max-height: 7px;
}
QProgressBar::chunk { background-color: #1677ff; border-radius: 4px; }
"""


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
        self.events: queue.Queue[tuple] = queue.Queue()
        self.running = False
        self._detector: PersonDetector | None = None
        self._detector_lock = threading.Lock()
        self.current_destination: Path | None = None
        self.active_file_keys: set[str] = set()
        self.log_lines: list[str] = []
        self.task_started_at = 0.0
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start(80)

    def showEvent(self, event):
        super().showEvent(event)
        # A one-file PyInstaller app changes from its launcher process to the
        # extracted GUI process during startup. Refreshing WM_SETICON after the
        # native window exists prevents Windows from intermittently retaining
        # the launcher's blank taskbar icon.
        QTimer.singleShot(0, self._apply_windows_taskbar_icon)

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

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22, 24, 22, 22)

        header = QHBoxLayout()
        title = QLabel(APP_NAME); title.setObjectName("title")
        header.addWidget(title)
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

        drop_zone = QFrame(); drop_zone.setObjectName("dropZone")
        drop_layout = QVBoxLayout(drop_zone); drop_layout.setContentsMargins(20, 22, 20, 22); drop_layout.setSpacing(7)
        drop_title = QLabel("将文件或文件夹拖到此处"); drop_title.setObjectName("dropTitle"); drop_title.setAlignment(Qt.AlignCenter)
        drop_sub = QLabel("支持递归扫描；自动忽略其他格式和已有标签的文件")
        drop_sub.setObjectName("muted"); drop_sub.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(drop_title); drop_layout.addWidget(drop_sub); drop_layout.addSpacing(8)
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
        add_buttons.addStretch(); drop_layout.addLayout(add_buttons)
        layout.addWidget(drop_zone)

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
            file_header_layout.addWidget(button)
        files_layout.addWidget(file_header)

        list_host = QWidget(); list_host.setObjectName("listHost")
        self.list_stack = QStackedLayout(list_host); self.list_stack.setContentsMargins(0, 0, 0, 0)
        empty = QWidget(); empty.setObjectName("emptyState"); empty_layout = QVBoxLayout(empty); empty_layout.setAlignment(Qt.AlignCenter)
        empty_title = QLabel("尚未添加文件"); empty_title.setObjectName("emptyTitle"); empty_title.setAlignment(Qt.AlignCenter)
        empty_subtitle = QLabel("拖入文件后可点击整行勾选，按 Delete 从列表移除")
        empty_subtitle.setObjectName("muted"); empty_subtitle.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_title); empty_layout.addWidget(empty_subtitle)
        self.list = CheckableListWidget(); self.list.setObjectName("fileList"); self.list.setAlternatingRowColors(False); self.list.setTextElideMode(Qt.ElideMiddle)
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
        self.progress_text = QLabel("0%"); self.progress_text.setObjectName("status"); progress_header.addWidget(self.progress_text)
        action_layout.addLayout(progress_header)
        self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0); self.progress.setTextVisible(False)
        action_layout.addWidget(self.progress)

        actionbar = QHBoxLayout()
        log_button = QPushButton("查看处理日志"); log_button.setObjectName("outlineAction"); log_button.clicked.connect(self.show_log)
        export_button = QPushButton("导出日志"); export_button.setObjectName("outlineAction"); export_button.clicked.connect(self.export_log)
        self.contact_button = QPushButton("反馈与联系")
        self.contact_button.setObjectName("contactAction")
        self.contact_button.clicked.connect(self.open_contact)
        actionbar.addWidget(log_button); actionbar.addWidget(export_button); actionbar.addWidget(self.contact_button); actionbar.addStretch()
        self.start_button = QPushButton("导出已勾选（0）"); self.start_button.setObjectName("secondaryAction"); self.start_button.setFixedSize(170, 42); self.start_button.clicked.connect(self.start)
        actionbar.addWidget(self.start_button)
        self.smart_button = QPushButton("智能识别并导出")
        self.smart_button.setObjectName("smartPrimary"); self.smart_button.setFixedSize(230, 46)
        self.smart_button.clicked.connect(self.start_smart)
        actionbar.addWidget(self.smart_button)
        action_layout.addLayout(actionbar)
        layout.addWidget(action_panel)

        outer.addWidget(card, 1)
        self._update_action_states()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if not self.running and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        if not self.running:
            self.add_paths([url.toLocalFile() for url in event.mimeData().urls()])
            event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent):
        if self.running:
            QMessageBox.warning(self, APP_NAME, "文件正在处理中，请等待任务完成后再关闭程序。")
            event.ignore()
            return
        event.accept()

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择媒体文件", "", "支持的媒体 (*.jpg *.jpeg *.png *.mp4);;所有文件 (*.*)")
        self.add_paths(paths)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择媒体文件夹")
        if path: self.add_paths([path])

    def add_paths(self, paths: list[str]):
        if self.running: return
        checked_keys = {str(path).casefold() for path in self.checked_files()}
        known = {str(path).casefold() for path in self.files}
        discovered = collect_media(paths)
        new_files = [path for path in discovered if str(path).casefold() not in known]
        already_tagged: set[str] = set()
        renamed_tagged = 0
        if new_files:
            try:
                already_tagged = ExifToolService(find_exiftool()).paths_with_target_subject(new_files)
                for path in new_files:
                    if str(path).casefold() in already_tagged:
                        renamed_tagged += int(rename_tagged_file(path) != path)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"读取现有标签失败，文件尚未加入：\n{exc}")
                return
        new_files = [path for path in new_files if str(path).casefold() not in already_tagged]
        self.files.extend(new_files)
        checked_keys.update(str(path).casefold() for path in new_files)
        self.files.sort(key=lambda p: str(p).casefold())
        self.refresh(checked_keys)
        if new_files:
            self.progress.setRange(0, 1); self.progress.setValue(0)
            self.progress_text.setText("0%")
            self.status.setText(f"已添加 {len(new_files)} 个新文件；已完成的旧文件保持未勾选")

        if already_tagged:
            self.status.setText(
                f"已忽略 {len(already_tagged)} 个已有标签的文件；"
                f"其中 {renamed_tagged} 个已补充 _AI 文件名"
            )

    def clear_files(self):
        if not self.running:
            self.files.clear()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress_text.setText("0%")
            self.refresh(set())

    def remove_selected_files(self):
        """Remove highlighted rows from the list without touching source files."""
        if self.running or not self.files:
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
        self.status.setText(f"已从列表移除 {len(valid_rows)} 个文件；原文件未删除")

    def refresh(self, checked_keys: set[str] | None = None):
        if checked_keys is None:
            checked_keys = {str(path).casefold() for path in self.files}
        self.list.blockSignals(True); self.list.clear()
        for path in self.files:
            item = QListWidgetItem(f"{path.name}\n{path.parent}")
            item.setSizeHint(QSize(0, 54))
            item.setToolTip(str(path))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if str(path).casefold() in checked_keys else Qt.Unchecked)
            self.list.addItem(item)
            item.setSelected(item.checkState() == Qt.Checked)
        self.list.blockSignals(False)
        self.list_stack.setCurrentIndex(1 if self.files else 0)
        self.update_checked_count()
        self.status.setText("文件已就绪" if self.files else "等待添加文件")

    def checked_files(self) -> list[Path]:
        checked: list[Path] = []
        for index, path in enumerate(self.files):
            if self.list.item(index).checkState() == Qt.Checked:
                checked.append(path)
        return checked

    def update_checked_count(self):
        selected = sum(self.list.item(i).checkState() == Qt.Checked for i in range(self.list.count()))
        self.count.setText(f"已选择：{selected} / {len(self.files)} 个文件")
        self.start_button.setText(f"导出已勾选（{selected}）")
        self._update_action_states()

    def _update_action_states(self):
        enabled = not self.running
        self.choose_file_button.setEnabled(enabled)
        self.choose_folder_button.setEnabled(enabled)
        # Keep list/export controls interactive while idle so their hover
        # feedback remains visible in every list state (including an empty
        # list, fully selected list, or zero selected items). The handlers are
        # safe no-ops where appropriate; export shows an actionable prompt.
        self.select_all_button.setEnabled(enabled)
        self.select_none_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.start_button.setEnabled(enabled)
        # Keep the primary smart action visible and usable even with an empty
        # list; _start_export provides the actionable "先添加文件" prompt.
        self.smart_button.setEnabled(enabled)

    def _on_item_changed(self, item: QListWidgetItem):
        item.setSelected(item.checkState() == Qt.Checked)
        self.update_checked_count()

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

    def start(self):
        self._start_export(smart=False)

    def start_smart(self):
        self._start_export(smart=True)

    def _start_export(self, smart: bool):
        if self.running: return
        selected = self.checked_files()
        if not selected:
            QMessageBox.information(self, APP_NAME, "请先添加文件并勾选需要导出的项目。"); return
        destination_label = "各原文件所在目录"
        self.current_destination = None
        self.active_file_keys = {str(path).casefold() for path in selected}
        try: executable = find_exiftool()
        except FileNotFoundError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc)); return
        self.running = True
        self._update_action_states()
        self.progress.setRange(0, 1000); self.progress.setValue(0)
        self.progress_text.setText("0%")
        self.task_started_at = time.time()
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
        target = self._smart_worker if smart else self._worker
        args = (executable, selected)
        threading.Thread(target=target, args=args, daemon=True).start()

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

        batch_size = 16
        for batch_start in range(0, total, batch_size):
            batch = files[batch_start:batch_start + batch_size]
            detections: dict[str, DetectionResult] = {}
            candidates: list[Path] = []
            early_results: dict[str, TagResult | None] = {}
            for offset, source in enumerate(batch):
                index = batch_start + offset + 1
                self.events.put(("status", f"正在识别 {index}/{total}：{source.name}"))
                try:
                    detection = detector.detect(source)
                    detections[str(source).casefold()] = detection
                    if detection.error:
                        early_results[str(source).casefold()] = TagResult(source, False, detection.reason)
                    elif not detection.detected:
                        early_results[str(source).casefold()] = None
                    else:
                        candidates.append(source)
                except Exception as exc:
                    detections[str(source).casefold()] = DetectionResult(
                        False, 0.0, f"识别异常：{exc}", 0.0, error=True
                    )
                    early_results[str(source).casefold()] = TagResult(source, False, str(exc))
                recognized_count += 1
                if source not in candidates:
                    written_count += 1
                    finalized_count += 1
                emit_phase(f"已识别 {index}/{total}：{source.name}")

            if candidates:
                self.events.put((
                    "status",
                    f"正在写入并验证标签：本批 {len(candidates)} 个文件",
                ))

            batch_exports: dict[str, tuple[Path | None, TagResult]] = {}
            try:
                for source, exported, result in export_tagged_copies_batch(service, candidates):
                    written_count += 1
                    emit_phase(f"正在复核标签：{source.name}")
                    if result.success and source.exists():
                        result = remove_source_after_verified_export(source, exported, result)
                    finalized_count += 1
                    emit_phase(f"已完成原图处理：{source.name}")
                    batch_exports[str(source).casefold()] = (exported, result)
            except Exception as exc:
                for source in candidates:
                    written_count += 1
                    finalized_count += 1
                    batch_exports[str(source).casefold()] = (
                        None, TagResult(source, False, f"批量导出异常：{exc}")
                    )
                emit_phase("本批处理结束，正在继续下一批")

            for offset, source in enumerate(batch):
                index = batch_start + offset + 1
                key = str(source).casefold()
                detection = detections[key]
                exported = None
                if key in batch_exports:
                    exported, result = batch_exports[key]
                else:
                    result = early_results[key]
                if result is None:
                    skipped_count += 1
                elif result.success:
                    exported_count += 1
                else:
                    failed_count += 1
                self.events.put(("smart_result", index, total, source, exported, detection, result))
        self.events.put(("smart_done", exported_count, skipped_count, failed_count, total))

    def _worker(self, executable: Path, files: list[Path]):
        service = ExifToolService(executable); success = 0
        total = len(files)
        batch_size = 50
        for batch_start in range(0, total, batch_size):
            batch = files[batch_start:batch_start + batch_size]
            try:
                batch_results = export_tagged_copies_batch(service, batch)
            except Exception as exc:
                batch_results = [
                    (source, None, TagResult(source, False, f"批量导出失败：{exc}"))
                    for source in batch
                ]
            written_through = min(total, batch_start + len(batch))
            self.events.put((
                "phase_progress",
                min(950, round(800 * written_through / total)) if total else 0,
                f"已写入并复核 {written_through}/{total} 个文件",
            ))
            for offset, (source, exported, result) in enumerate(batch_results):
                if result.success and source.exists():
                    result = remove_source_after_verified_export(source, exported, result)
                index = batch_start + offset + 1
                success += int(result.success)
                value = round((800 * written_through + 200 * index) / total) if total else 0
                self.events.put(("phase_progress", min(999, value), f"已处理原图 {index}/{total}：{source.name}"))
                self.events.put(("result", index, total, source, exported, result))
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
            f"  识别耗时：{detection.elapsed_seconds:.2f} 秒；检查次数：{detection.passes}",
            f"  导出文件：{exported if exported else '未生成'}",
            f"  原图状态：{('已永久删除，仅保留已验证的 _AI 文件' if result is not None and '永久删除' in result.message else '已移至回收站，仅保留已验证的 _AI 文件') if not source.exists() else '已保留'}",
        ])
        if result is not None:
            self.log_lines.append(f"  标签结果：{result.message}")
        self.log_lines.append("")

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

    def _finalize_completed_files(self):
        """Remove deleted sources from the UI and uncheck retained inputs."""
        retained_checked = {
            str(path).casefold() for path in self.checked_files()
            if str(path).casefold() not in self.active_file_keys
        }
        self.files = [path for path in self.files if path.exists()]
        self.active_file_keys.clear()
        self.refresh(retained_checked)

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "status":
                    self.status.setText(event[1])
                elif event[0] == "model_error":
                    self.running = False
                    self._update_action_states()
                    self.progress.setValue(0)
                    self.progress_text.setText("0%")
                    QMessageBox.critical(self, APP_NAME, event[1])
                elif event[0] == "phase_progress":
                    _, value, status = event
                    value = max(self.progress.value(), value)
                    self.progress.setValue(value)
                    self.progress_text.setText(f"{value // 10}%")
                    self.status.setText(status)
                elif event[0] == "smart_progress":
                    _, index, total, source = event
                    value = max(self.progress.value(), index)
                    self.progress.setValue(value)
                    self.progress_text.setText(f"{int(value / total * 100) if total else 0}%")
                    self.status.setText(f"已识别 {index}/{total}：{source.name}")
                elif event[0] == "result":
                    _, index, total, source, exported, result = event
                    self._append_log_result(index, total, source, exported, result)
                    self.status.setText(f"正在处理 {index}/{total}：{result.path.name}")
                elif event[0] == "smart_result":
                    _, index, total, source, exported, detection, result = event
                    self._append_smart_log_result(index, total, source, exported, detection, result)
                    self.status.setText(f"已处理 {index}/{total}：{source.name}")
                elif event[0] == "smart_done":
                    _, exported_count, skipped_count, failed_count, total = event
                    self._finalize_completed_files()
                    self.running = False
                    self.progress.setValue(1000)
                    self.progress_text.setText("100%")
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
                    QMessageBox.information(
                        self, APP_NAME,
                        f"智能处理完成\n\n已导出：{exported_count}\n未检测到人物：{skipped_count}\n失败：{failed_count}\n\n导出目录：各原文件所在目录",
                    )
                else:
                    _, success, total = event; failed = total - success
                    self._append_log_summary(success, total)
                    self._finalize_completed_files()
                    self.running = False
                    self.progress.setValue(1000)
                    self.progress_text.setText("100%")
                    self._update_action_states()
                    self.status.setText(f"处理完成：成功 {success}，失败 {failed}")
                    destination = "各原文件所在目录"
                    if failed: QMessageBox.warning(self, APP_NAME, f"处理完成\n成功：{success}\n失败：{failed}\n导出目录：{destination}\n\n可查看处理日志了解详情。")
                    else: QMessageBox.information(self, APP_NAME, f"全部处理成功，共 {success} 个文件。\n\n导出目录：{destination}")
        except queue.Empty: pass

    def show_log(self):
        dialog = QMainWindow(self); dialog.setWindowTitle("媒体标签导出报告"); dialog.resize(900, 620)
        text = QTextEdit(); text.setReadOnly(True); text.setPlainText("\n".join(self.log_lines) if self.log_lines else "暂无处理日志。")
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
        if not QDesktopServices.openUrl(QUrl(FEEDBACK_URL)):
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
    icon_path = bundled_path("assets/app-icon.ico")
    if not icon_path.is_file():
        icon_path = bundled_path("assets/app-icon.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(STYLE)
    window = MainWindow()
    instance = SingleInstanceController(single_instance_server_name(), lambda: activate_main_window(window))
    if not instance.acquire_or_notify():
        return
    window.show(); sys.exit(app.exec())


if __name__ == "__main__": main()
