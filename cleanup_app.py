from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
    QWidget,
)

from core import TAG_VALUE, ExifToolService, bundled_path, collect_media, find_exiftool


APP_NAME = "AI 媒体标签清理工具"
AI_SUFFIX = re.compile(r"_AI(?P<number>\s*\(\d+\))?$", re.IGNORECASE)

STYLE = """
QMainWindow, QWidget { background: #f3f3f3; color: #202020; font-family: "Microsoft YaHei UI"; }
QWidget#card { background: #ffffff; border: 1px solid #cfcfcf; }
QLabel#title { font-size: 22px; font-weight: 600; color: #202020; }
QLabel#muted { color: #666666; font-size: 12px; }
QLabel#tag { color: #333333; font-family: Consolas; padding: 2px 4px; }
QListWidget { background: white; border: 1px solid #c9c9c9; padding: 4px; outline: none; }
QListWidget::item { min-height: 32px; padding: 4px 6px; }
QListWidget::item:selected { background: #dbe9f8; color: #202020; }
QPushButton { background: #f8f8f8; color: #202020; border: 1px solid #bdbdbd; border-radius: 3px; min-height: 34px; padding: 0 14px; }
QPushButton:hover { background: #e9f2fb; border-color: #6a9fd0; }
QPushButton#primary { background: #2678c8; color: white; border-color: #1f69ad; min-width: 210px; font-weight: 600; }
QPushButton#primary:hover { background: #1f69ad; }
QPushButton#stop { background: #fff7f7; color: #a12622; border: 1px solid #d9a4a1; }
QPushButton:disabled { background: #eeeeee; color: #aaaaaa; border-color: #d4d4d4; }
QProgressBar { background: #e1e1e1; border: 1px solid #cccccc; height: 9px; }
QProgressBar::chunk { background: #2678c8; }
"""


def log_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "AI媒体标签清理工具" / "logs"


def restore_filename(path: Path) -> tuple[Path, str]:
    """Remove a final _AI marker without replacing an existing file."""
    match = AI_SUFFIX.search(path.stem)
    if match is None:
        return path, "文件名无需修改"
    number = match.group("number") or ""
    clean_stem = path.stem[:match.start()] + number
    candidate = path.with_name(clean_stem + path.suffix)
    if candidate.exists() and candidate.resolve() != path.resolve():
        candidate = path.with_name(f"{clean_stem}_restored{path.suffix}")
        index = 2
        while candidate.exists():
            candidate = path.with_name(f"{clean_stem}_restored ({index}){path.suffix}")
            index += 1
    path.rename(candidate)
    return candidate, f"文件名已恢复为 {candidate.name}"


def clean_file(service: ExifToolService, source: Path) -> tuple[Path, str, str]:
    """Remove the fixed tag, verify it, then restore the filename."""
    current = source
    messages: list[str] = []
    try:
        if AI_SUFFIX.search(source.stem) is None:
            return source, "skipped", "已跳过：文件名末尾没有 _AI"
        readable, values, detail = service._read_subject(source)
        if not readable:
            raise RuntimeError(detail or "无法读取 XMP 标签")
        if TAG_VALUE not in values:
            return source, "skipped", "已跳过：文件不含目标标签"
        result = service.remove_target_subject(source)
        if not result.success:
            raise RuntimeError(result.message)
        messages.append(result.message)
        current, rename_message = restore_filename(source)
        messages.append(rename_message)
        return current, "success", "；".join(messages)
    except Exception as exc:
        return current, "failed", str(exc)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1050, 760)
        self.setMinimumSize(850, 620)
        self.setAcceptDrops(True)
        self.files: list[Path] = []
        self.events: queue.Queue[tuple] = queue.Queue()
        self.running = False
        self.stop_requested = threading.Event()
        self.log_lines: list[str] = []
        self.current_log_path: Path | None = None
        self._pressed_check_state = Qt.Unchecked
        self._build_ui()
        self._load_latest_log()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start(80)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 26, 28, 26)
        outer.setSpacing(16)

        title = QLabel("标签撤销工具")
        title.setObjectName("title")
        outer.addWidget(title)
        subtitle = QLabel("仅处理同时含固定标签、且文件名以 _AI 结尾的文件")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        card = QWidget()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel("目标标签："))
        tag = QLabel(TAG_VALUE)
        tag.setObjectName("tag")
        tag_row.addWidget(tag)
        tag_row.addStretch()
        layout.addLayout(tag_row)

        controls = QHBoxLayout()
        choose_files = QPushButton("添加文件")
        choose_files.clicked.connect(self.choose_files)
        choose_folder = QPushButton("添加文件夹")
        choose_folder.clicked.connect(self.choose_folder)
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        select_none = QPushButton("取消全选")
        select_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        clear = QPushButton("清空列表")
        clear.clicked.connect(self.clear_files)
        for button in (choose_files, choose_folder, select_all, select_none, clear):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)

        self.count = QLabel("已选择：0 / 0 个文件")
        layout.addWidget(self.count)
        self.list = QListWidget()
        self.list.itemChanged.connect(self._update_count)
        self.list.itemPressed.connect(self._remember_check_state)
        self.list.itemClicked.connect(self._toggle_row)
        layout.addWidget(self.list, 1)

        status_row = QHBoxLayout()
        self.status = QLabel("将文件或文件夹拖入窗口即可开始")
        self.status.setObjectName("muted")
        self.percent = QLabel("0%")
        self.percent.setObjectName("muted")
        status_row.addWidget(self.status)
        status_row.addStretch()
        status_row.addWidget(self.percent)
        layout.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        actions = QHBoxLayout()
        log_button = QPushButton("查看最近日志")
        log_button.clicked.connect(self.show_log)
        actions.addWidget(log_button)
        actions.addStretch()
        self.stop_button = QPushButton("安全停止")
        self.stop_button.setObjectName("stop")
        self.stop_button.clicked.connect(self.request_stop)
        actions.addWidget(self.stop_button)
        self.start_button = QPushButton("移除标签并恢复文件名")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        outer.addWidget(card, 1)
        self._update_states()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self.running and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if not self.running:
            self.add_paths([url.toLocalFile() for url in event.mimeData().urls()])
            event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.running:
            QMessageBox.warning(self, APP_NAME, "任务正在运行，请先点击“安全停止”。")
            event.ignore()
        else:
            event.accept()

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择媒体文件", "", "媒体文件 (*.jpg *.jpeg *.png *.mp4)"
        )
        self.add_paths(paths)

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self.add_paths([path])

    def add_paths(self, paths: list[str]) -> None:
        existing = {str(path).casefold(): path for path in self.files}
        for path in collect_media(paths):
            if AI_SUFFIX.search(path.stem) is not None:
                existing[str(path).casefold()] = path
        self.files = sorted(existing.values(), key=lambda item: str(item).casefold())
        self._refresh({str(path).casefold() for path in self.files})
        self.status.setText(f"已添加 {len(self.files)} 个支持的媒体文件")

    def _refresh(self, checked: set[str]) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for path in self.files:
            item = QListWidgetItem(f"{path.name}\n{path.parent}")
            item.setToolTip(str(path))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if str(path).casefold() in checked else Qt.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_count()

    def _remember_check_state(self, item: QListWidgetItem) -> None:
        self._pressed_check_state = item.checkState()

    def _toggle_row(self, item: QListWidgetItem) -> None:
        target = Qt.Unchecked if self._pressed_check_state == Qt.Checked else Qt.Checked
        item.setCheckState(target)

    def _set_all(self, state: Qt.CheckState) -> None:
        self.list.blockSignals(True)
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(state)
        self.list.blockSignals(False)
        self._update_count()

    def _selected(self) -> list[Path]:
        return [
            path for index, path in enumerate(self.files)
            if self.list.item(index).checkState() == Qt.Checked
        ]

    def _update_count(self, _item: QListWidgetItem | None = None) -> None:
        selected = len(self._selected()) if self.list.count() else 0
        self.count.setText(f"已选择：{selected} / {len(self.files)} 个文件")

    def clear_files(self) -> None:
        if self.running:
            return
        self.files.clear()
        self._refresh(set())

    def _update_states(self) -> None:
        self.start_button.setEnabled(not self.running)
        self.stop_button.setEnabled(self.running and not self.stop_requested.is_set())
        self.list.setEnabled(not self.running)

    def request_stop(self) -> None:
        if self.running and not self.stop_requested.is_set():
            self.stop_requested.set()
            self.stop_button.setEnabled(False)
            self.stop_button.setText("正在停止…")
            self.status.setText("正在完成当前文件，随后安全停止…")

    def _new_log(self, total: int) -> None:
        folder = log_directory()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.current_log_path = folder / f"标签清理报告_{stamp}.txt"
        self.log_lines = [
            f"{APP_NAME} - 自动保存日志", "=" * 72,
            f"开始时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"目标标签：{TAG_VALUE}", f"任务文件：{total}", "",
        ]
        self._persist_log()

    def _persist_log(self) -> None:
        if self.current_log_path is None:
            return
        try:
            self.current_log_path.parent.mkdir(parents=True, exist_ok=True)
            self.current_log_path.write_text("\n".join(self.log_lines), encoding="utf-8-sig")
        except OSError:
            pass

    def _load_latest_log(self) -> None:
        try:
            latest = max(log_directory().glob("*.txt"), key=lambda path: path.stat().st_mtime)
            self.current_log_path = latest
            self.log_lines = latest.read_text(encoding="utf-8-sig").splitlines()
        except (ValueError, OSError):
            pass

    def start(self) -> None:
        if self.running:
            return
        selected = self._selected()
        if not selected:
            QMessageBox.information(self, APP_NAME, "请先添加并勾选需要清理的文件。")
            return
        try:
            executable = find_exiftool()
        except FileNotFoundError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self.running = True
        self.stop_requested.clear()
        self.stop_button.setText("安全停止")
        self.progress.setValue(0)
        self.percent.setText("0%")
        self._new_log(len(selected))
        self._update_states()
        threading.Thread(target=self._worker, args=(executable, selected), daemon=True).start()

    def _worker(self, executable: Path, files: list[Path]) -> None:
        service = ExifToolService(executable)
        success = skipped = failed = 0
        total = len(files)
        processed = 0
        chunk_size = 40
        for chunk_start in range(0, total, chunk_size):
            if self.stop_requested.is_set():
                break
            chunk = files[chunk_start:chunk_start + chunk_size]
            states = service.read_subjects(chunk)
            tagged: list[Path] = []
            outcomes: dict[str, tuple[str, str]] = {}
            for source in chunk:
                key = str(source).casefold()
                readable, values, detail = states[key]
                if not readable:
                    outcomes[key] = ("failed", detail or "无法读取 XMP 标签")
                elif TAG_VALUE not in values:
                    outcomes[key] = ("skipped", "已跳过：文件不含目标标签")
                else:
                    tagged.append(source)

            removed = service.remove_target_subjects_batch(tagged)
            for offset, source in enumerate(chunk):
                index = chunk_start + offset + 1
                original = source
                key = str(source).casefold()
                if key in outcomes:
                    outcome, detail = outcomes[key]
                    current = source
                else:
                    result = removed[key]
                    if not result.success:
                        outcome, detail, current = "failed", result.message, source
                    else:
                        try:
                            current, rename_message = restore_filename(source)
                            outcome = "success"
                            detail = f"{result.message}；{rename_message}"
                        except OSError as exc:
                            outcome, detail, current = "failed", f"标签已移除，但恢复文件名失败：{exc}", source
                if outcome == "success":
                    success += 1
                elif outcome == "skipped":
                    skipped += 1
                else:
                    failed += 1
                processed += 1
                self.events.put(("result", index, total, original, current, outcome, detail))
        self.events.put((
            "done", success, skipped, failed, processed, total, self.stop_requested.is_set()
        ))

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "result":
                    _, index, total, original, current, outcome, detail = event
                    state = {"success": "成功", "skipped": "跳过", "failed": "失败"}[outcome]
                    self.log_lines.extend([
                        f"[{index:04d}/{total:04d}] {state}",
                        f"  原文件：{original}", f"  当前文件：{current}",
                        f"  处理结果：{detail}", "",
                    ])
                    self._persist_log()
                    value = round(1000 * index / total) if total else 0
                    self.progress.setValue(value)
                    self.percent.setText(f"{value // 10}%")
                    self.status.setText(f"已处理 {index}/{total}：{original.name}")
                elif event[0] == "done":
                    _, success, skipped, failed, processed, total, stopped = event
                    self.running = False
                    self.stop_requested.clear()
                    self.stop_button.setText("安全停止")
                    self._update_states()
                    self.files = [path for path in self.files if path.exists()]
                    self._refresh(set())
                    remaining = total - processed
                    self.log_lines.extend([
                        "任务汇总", "-" * 72,
                        f"结束时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                        f"成功：{success}", f"跳过：{skipped}", f"失败：{failed}",
                        f"剩余未处理：{remaining}",
                        f"任务状态：{'已安全停止' if stopped else '处理完成'}",
                        "=" * 72,
                    ])
                    self._persist_log()
                    self.status.setText(
                        f"{'已安全停止' if stopped else '处理完成'}：成功 {success}，跳过 {skipped}，失败 {failed}"
                    )
                    QMessageBox.information(
                        self, APP_NAME,
                        f"{'任务已安全停止' if stopped else '处理完成'}\n\n"
                        f"成功：{success}\n跳过：{skipped}\n失败：{failed}\n剩余：{remaining}\n\n"
                        f"日志已自动保存：\n{self.current_log_path}",
                    )
        except queue.Empty:
            pass

    def show_log(self) -> None:
        dialog = QMainWindow(self)
        dialog.setWindowTitle("最近处理日志")
        dialog.resize(820, 600)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n".join(self.log_lines) if self.log_lines else "暂无处理日志。")
        dialog.setCentralWidget(text)
        dialog.show()
        self._log_window = dialog


def main() -> None:
    self_test_source = os.environ.get("AI_CLEANUP_SELFTEST_SOURCE")
    self_test_result = os.environ.get("AI_CLEANUP_SELFTEST_RESULT")
    if self_test_source and self_test_result:
        source = Path(self_test_source)
        current, outcome, detail = clean_file(ExifToolService(find_exiftool()), source)
        Path(self_test_result).write_text(
            f"outcome={outcome}\noriginal={source}\ncurrent={current}\nmessage={detail}",
            encoding="utf-8",
        )
        return
    app = QApplication(sys.argv)
    icon = bundled_path("assets/app-icon.ico")
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
