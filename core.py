from __future__ import annotations

import json
import os
import shutil
import subprocess
import stat
import sys
import re
import ctypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


APP_NAME = "AI 媒体标签工具"
APP_VERSION = "1.1.0"
TAG_VALUE = "contains-synthetic-performer"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4"}
FILE_RETRY_DELAYS = (0.5, 1.0, 2.0)


def _is_transient_file_error(value: BaseException | str) -> bool:
    """Return whether an error is likely caused by a short-lived Windows lock."""
    if isinstance(value, BaseException):
        if isinstance(value, PermissionError) or getattr(value, "winerror", None) in (5, 32, 33):
            return True
        detail = str(value)
    else:
        detail = value
    lower = detail.casefold()
    return any(
        marker in lower
        for marker in (
            "permission denied", "access is denied", "sharing violation",
            "used by another process", "being used by another process",
            "file is locked", "文件被占用", "拒绝访问",
        )
    )


def bundled_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def find_exiftool() -> Path:
    candidates = [
        bundled_path("exiftool.exe"),
        Path(sys.executable).resolve().parent / "exiftool.exe",
        Path(__file__).resolve().parent / "vendor" / "exiftool.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 exiftool.exe，请重新安装或重新下载本软件。")


def collect_media(paths: Iterable[str | Path]) -> list[Path]:
    result: dict[str, Path] = {}
    for raw in paths:
        path = Path(raw)
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            result[str(path.resolve()).casefold()] = path.resolve()
        elif path.is_dir():
            try:
                for item in path.rglob("*"):
                    if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
                        result[str(item.resolve()).casefold()] = item.resolve()
            except (OSError, PermissionError):
                continue
    return sorted(result.values(), key=lambda p: str(p).casefold())


def copy_without_overwrite(source: Path, destination_dir: Path) -> Path:
    """Copy a media file to destination_dir without replacing any file."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    candidate = destination_dir / f"{source.stem}_AI{source.suffix}"
    index = 1
    while True:
        reserved_by_other_file = False
        copied = False
        for attempt in range(len(FILE_RETRY_DELAYS) + 1):
            try:
                # ``xb`` reserves the name atomically. A plain exists()+copy2()
                # sequence can overwrite a file created by another process in
                # the tiny gap between those two operations.
                with source.open("rb") as source_stream, candidate.open("xb") as target_stream:
                    shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                shutil.copystat(source, candidate)
                copied = True
                break
            except FileExistsError:
                reserved_by_other_file = True
                break
            except OSError as exc:
                candidate.unlink(missing_ok=True)
                if attempt < len(FILE_RETRY_DELAYS) and _is_transient_file_error(exc):
                    time.sleep(FILE_RETRY_DELAYS[attempt])
                    continue
                raise
        if reserved_by_other_file:
            candidate = destination_dir / f"{source.stem}_AI ({index}){source.suffix}"
            index += 1
            continue
        if copied:
            break
    # copy2 may preserve a Windows read-only attribute. The source remains
    # untouched, but the exported copy must be writable for metadata updates.
    candidate.chmod(candidate.stat().st_mode | stat.S_IWRITE)
    return candidate


@dataclass
class TagResult:
    path: Path
    success: bool
    message: str


class ExifToolService:
    def __init__(self, executable: Path):
        self.executable = executable

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        env = os.environ.copy()
        perl_lib = self.executable.parent / "exiftool_files" / "lib"
        file_args = args[2:] if args[:2] == ["-charset", "filename=UTF8"] else args
        # Feed the UTF-8 argument file through stdin. A disk-backed argument
        # file inside %TEMP% breaks when the Windows user profile contains
        # non-ASCII characters (for example C:\Users\测试用户).
        argfile_text = "\n".join(file_args) + "\n"
        command = [str(self.executable), "-charset", "filename=UTF8", "-@", "-"]
        if getattr(sys, "_MEIPASS", None) and perl_lib.is_dir():
            # The ExifTool launcher normally supplies this path itself. In a
            # PyInstaller one-file extraction directory its path detection is
            # unreliable. Invoke the bundled Perl runtime and script directly.
            env["PERL5LIB"] = str(perl_lib)
            perl = self.executable.parent / "exiftool_files" / "perl.exe"
            script = self.executable.parent / "exiftool_files" / "exiftool.pl"
            command = [str(perl), str(script), "-charset", "filename=UTF8", "-@", "-"]
        return subprocess.run(
            command, input=argfile_text, capture_output=True, text=True,
            encoding="utf-8", errors="replace", startupinfo=startupinfo,
            creationflags=creationflags, timeout=180, env=env,
        )

    def _read_subject(self, path: Path) -> tuple[bool, list[str], str]:
        checked = self._run(["-charset", "filename=UTF8", "-j", "-XMP-dc:Subject", str(path)])
        if checked.returncode != 0:
            detail = (checked.stderr or checked.stdout).strip()
            return False, [], detail or "ExifTool 读取失败"
        data = json.loads(checked.stdout)
        value = data[0].get("Subject") if data else None
        if value is None:
            return True, [], ""
        return True, value if isinstance(value, list) else [value], ""

    def read_subjects(
        self, paths: Iterable[Path]
    ) -> dict[str, tuple[bool, list[str], str]]:
        """Read multiple Subject fields with one ExifTool process."""
        ordered = list(paths)
        if not ordered:
            return {}
        try:
            checked = self._run([
                "-charset", "filename=UTF8", "-j", "-XMP-dc:Subject",
                *(str(path) for path in ordered),
            ])
            data = json.loads(checked.stdout)
            if len(data) != len(ordered):
                raise ValueError("ExifTool 返回的文件数量与请求不一致")
            results: dict[str, tuple[bool, list[str], str]] = {}
            for path, item in zip(ordered, data):
                error = str(item.get("Error") or "").strip()
                if error:
                    results[str(path).casefold()] = (False, [], error)
                    continue
                value = item.get("Subject")
                values = value if isinstance(value, list) else ([] if value is None else [value])
                results[str(path).casefold()] = (True, values, "")
            return results
        except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
            # Preserve per-file fault isolation if a batch contains one unusual
            # file or ExifTool cannot produce a complete JSON response.
            return {str(path).casefold(): self._read_subject(path) for path in ordered}

    def paths_with_target_subject(self, paths: Iterable[Path]) -> set[str]:
        """Return case-folded paths that already contain the fixed XMP tag."""
        ordered = list(paths)
        if not ordered:
            return set()
        states = self.read_subjects(ordered)
        tagged: set[str] = set()
        for path in ordered:
            readable, values, detail = states[str(path).casefold()]
            if not readable:
                raise RuntimeError(detail or f"ExifTool 无法读取标签：{path}")
            if TAG_VALUE in values:
                tagged.add(str(path).casefold())
        return tagged

    def ensure_subject(self, path: Path, keep_backup: bool) -> TagResult:
        last_result = TagResult(path, False, "标签写入未执行")
        for attempt in range(len(FILE_RETRY_DELAYS) + 1):
            try:
                last_result = self._ensure_subject_once(path, keep_backup)
            except subprocess.TimeoutExpired:
                last_result = TagResult(path, False, "处理超时（180 秒）")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                last_result = TagResult(path, False, str(exc))
            if last_result.success:
                return last_result
            if attempt >= len(FILE_RETRY_DELAYS) or not _is_transient_file_error(last_result.message):
                return last_result
            time.sleep(FILE_RETRY_DELAYS[attempt])
        return last_result

    def _ensure_subject_once(self, path: Path, keep_backup: bool) -> TagResult:
        """Perform one write-and-verify attempt; caller handles lock retries."""
        try:
            args = ["-charset", "filename=UTF8"]
            if not keep_backup:
                args.append("-overwrite_original")
            # Remove all existing copies, then append exactly one. This keeps
            # every other keyword and needs only one write plus one verify.
            args.extend([
                f"-XMP-dc:Subject-={TAG_VALUE}",
                f"-XMP-dc:Subject+={TAG_VALUE}",
                str(path),
            ])
            written = self._run(args)
            if written.returncode != 0:
                detail = (written.stderr or written.stdout).strip()
                return TagResult(path, False, detail or "ExifTool 写入失败")
            readable, values, detail = self._read_subject(path)
            if not readable:
                return TagResult(path, False, f"已写入，但验证失败：{detail}")
            if TAG_VALUE in values:
                return TagResult(path, True, "标签已追加并验证")
            return TagResult(path, False, f"验证时未找到目标标签，当前值：{values!r}")
        except subprocess.TimeoutExpired:
            return TagResult(path, False, "处理超时（180 秒）")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return TagResult(path, False, str(exc))

    def ensure_subject_batch(
        self, paths: Iterable[Path], keep_backup: bool = False
    ) -> dict[str, TagResult]:
        """Write and verify one tag on many files using two ExifTool runs."""
        ordered = list(paths)
        if not ordered:
            return {}
        try:
            args = ["-charset", "filename=UTF8"]
            if not keep_backup:
                args.append("-overwrite_original")
            args.extend([
                f"-XMP-dc:Subject-={TAG_VALUE}",
                f"-XMP-dc:Subject+={TAG_VALUE}",
                *(str(path) for path in ordered),
            ])
            written = self._run(args)
            write_detail = (written.stderr or written.stdout).strip()
            states = self.read_subjects(ordered)
            results: dict[str, TagResult] = {}
            for path in ordered:
                key = str(path).casefold()
                readable, values, detail = states[key]
                if readable and TAG_VALUE in values:
                    results[key] = TagResult(path, True, "标签已批量追加并验证")
                elif not readable:
                    results[key] = TagResult(
                        path, False, f"批量写入后验证失败：{detail or write_detail or '未知错误'}"
                    )
                else:
                    results[key] = TagResult(
                        path, False,
                        f"批量验证时未找到目标标签；当前值：{values!r}；{write_detail}",
                    )
            # Retry only failed files individually. This handles short-lived
            # locks from antivirus, Explorer previews and cloud-sync clients
            # without slowing down successful batch items.
            for path in ordered:
                key = str(path).casefold()
                if results[key].success:
                    continue
                time.sleep(0.2)
                retried = self.ensure_subject(path, keep_backup=keep_backup)
                if retried.success:
                    results[key] = retried
            return results
        except subprocess.TimeoutExpired:
            return {
                str(path).casefold(): TagResult(path, False, "批量处理超时（180 秒）")
                for path in ordered
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {
                str(path).casefold(): TagResult(path, False, str(exc))
                for path in ordered
            }

    def remove_target_subject(self, path: Path) -> TagResult:
        """Remove only the fixed disclosure keyword and verify the result."""
        last_result = TagResult(path, False, "标签移除未执行")
        for attempt in range(len(FILE_RETRY_DELAYS) + 1):
            try:
                written = self._run([
                    "-charset", "filename=UTF8", "-overwrite_original",
                    f"-XMP-dc:Subject-={TAG_VALUE}", str(path),
                ])
                if written.returncode != 0:
                    detail = (written.stderr or written.stdout).strip()
                    last_result = TagResult(path, False, detail or "ExifTool 移除标签失败")
                else:
                    readable, values, detail = self._read_subject(path)
                    if not readable:
                        last_result = TagResult(path, False, f"标签移除后验证失败：{detail}")
                    elif TAG_VALUE in values:
                        last_result = TagResult(path, False, "验证时目标标签仍然存在")
                    else:
                        return TagResult(path, True, "目标标签已移除并验证；其他关键词已保留")
            except subprocess.TimeoutExpired:
                last_result = TagResult(path, False, "处理超时（180 秒）")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                last_result = TagResult(path, False, str(exc))
            if attempt >= len(FILE_RETRY_DELAYS) or not _is_transient_file_error(last_result.message):
                return last_result
            time.sleep(FILE_RETRY_DELAYS[attempt])
        return last_result

    def remove_target_subjects_batch(
        self, paths: Iterable[Path]
    ) -> dict[str, TagResult]:
        """Remove and verify the fixed keyword for many files efficiently."""
        ordered = list(paths)
        if not ordered:
            return {}
        try:
            written = self._run([
                "-charset", "filename=UTF8", "-overwrite_original",
                f"-XMP-dc:Subject-={TAG_VALUE}",
                *(str(path) for path in ordered),
            ])
            write_detail = (written.stderr or written.stdout).strip()
            states = self.read_subjects(ordered)
            results: dict[str, TagResult] = {}
            for path in ordered:
                key = str(path).casefold()
                readable, values, detail = states[key]
                if readable and TAG_VALUE not in values:
                    results[key] = TagResult(
                        path, True, "目标标签已批量移除并验证；其他关键词已保留"
                    )
                elif not readable:
                    results[key] = TagResult(
                        path, False,
                        f"批量移除后验证失败：{detail or write_detail or '未知错误'}",
                    )
                else:
                    results[key] = TagResult(
                        path, False,
                        f"批量验证时目标标签仍然存在；{write_detail}",
                    )

            # Preserve fault isolation for files temporarily locked by
            # Explorer, antivirus or sync software.
            for path in ordered:
                key = str(path).casefold()
                if results[key].success:
                    continue
                time.sleep(0.2)
                retried = self.remove_target_subject(path)
                if retried.success:
                    results[key] = retried
            return results
        except subprocess.TimeoutExpired:
            return {
                str(path).casefold(): TagResult(path, False, "批量清理超时（180 秒）")
                for path in ordered
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {
                str(path).casefold(): TagResult(path, False, str(exc))
                for path in ordered
            }


def export_tagged_copy(
    service: ExifToolService, source: Path, destination_dir: Path
) -> tuple[Path | None, TagResult]:
    """Export and tag a copy; remove the new copy if tagging fails."""
    readable, values, detail = service._read_subject(source)
    if readable and TAG_VALUE in values:
        return None, TagResult(source, True, "原文件已含目标标签，已跳过重复处理")
    if not readable:
        return None, TagResult(source, False, f"读取原文件标签失败：{detail}")
    exported = copy_without_overwrite(source, destination_dir)
    result = service.ensure_subject(exported, keep_backup=False)
    if result.success:
        return exported, result
    try:
        exported.unlink(missing_ok=True)
        result.message += "；未保留无标签副本"
    except OSError as exc:
        result.message += f"；清理失败：{exc}"
    return None, result


def export_tagged_copies_batch(
    service: ExifToolService, sources: Iterable[Path]
) -> list[tuple[Path, Path | None, TagResult]]:
    """Export and tag copies in source order with batched ExifTool calls."""
    ordered = list(sources)
    if not ordered:
        return []
    source_states = service.read_subjects(ordered)
    prepared: dict[str, tuple[Path | None, TagResult | None]] = {}
    copies: list[Path] = []

    for source in ordered:
        source_key = str(source).casefold()
        readable, values, detail = source_states[source_key]
        if not readable:
            prepared[source_key] = (
                None, TagResult(source, False, f"读取原文件标签失败：{detail}")
            )
            continue
        if TAG_VALUE in values:
            try:
                renamed = rename_tagged_file(source)
                prepared[source_key] = (
                    renamed,
                    TagResult(renamed, True, "原文件已含目标标签，已补充 _AI 文件名"),
                )
            except OSError as exc:
                prepared[source_key] = (
                    None, TagResult(source, False, f"已有标签，但补充 _AI 文件名失败：{exc}")
                )
            continue
        try:
            exported = copy_without_overwrite(source, source.parent)
            copies.append(exported)
            prepared[source_key] = (exported, None)
        except OSError as exc:
            prepared[source_key] = (None, TagResult(source, False, f"复制失败：{exc}"))

    tagged_results = service.ensure_subject_batch(copies, keep_backup=False)
    output: list[tuple[Path, Path | None, TagResult]] = []
    for source in ordered:
        source_key = str(source).casefold()
        exported, ready_result = prepared[source_key]
        result = ready_result
        if result is None and exported is not None:
            result = tagged_results[str(exported).casefold()]
            if not result.success:
                try:
                    exported.unlink(missing_ok=True)
                    result.message += "；未保留无标签副本"
                except OSError as exc:
                    result.message += f"；清理失败：{exc}"
                exported = None
        assert result is not None
        output.append((source, exported, result))
    return output


def send_to_recycle_bin(path: Path) -> None:
    """Move one file to the Windows recycle bin without showing shell UI."""
    if os.name != "nt":
        raise OSError("当前系统不支持 Windows 回收站，原图已保留")

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    # SHFileOperation requires a double-NUL-terminated list of paths.
    source_list = f"{path.resolve()}\0\0"
    operation = SHFILEOPSTRUCTW()
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = source_list
    operation.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO, NOCONFIRMATION, SILENT, NOERRORUI
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if not path.exists():
        return
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(f"移动到回收站失败（Windows 错误代码 {result}）")
    raise OSError("移动到回收站后原图仍然存在")


def remove_source_after_verified_export(
    source: Path, exported: Path | None, result: TagResult
) -> TagResult:
    """Delete source only after a distinct, verified export exists."""
    if not result.success or exported is None:
        return result
    try:
        if not exported.is_file():
            return TagResult(exported, False, "标签副本验证成功，但导出文件不存在；原图已保留")
        if source.resolve() == exported.resolve():
            return TagResult(exported, False, "导出文件与原图路径相同；为避免误删，原图已保留")
        recycle_error: OSError | None = None
        for attempt in range(len(FILE_RETRY_DELAYS) + 1):
            try:
                send_to_recycle_bin(source)
                removal_message = "原图已移至回收站"
                break
            except OSError as exc:
                recycle_error = exc
                if attempt < len(FILE_RETRY_DELAYS) and _is_transient_file_error(exc):
                    time.sleep(FILE_RETRY_DELAYS[attempt])
                    continue
                break
        else:  # pragma: no cover - loop always exits through break
            recycle_error = OSError("移动到回收站失败")

        if source.exists():
            delete_error: OSError | None = None
            for attempt in range(len(FILE_RETRY_DELAYS) + 1):
                try:
                    source.unlink()
                    removal_message = "回收站不可用，原图已永久删除"
                    break
                except OSError as exc:
                    delete_error = exc
                    if attempt < len(FILE_RETRY_DELAYS) and _is_transient_file_error(exc):
                        time.sleep(FILE_RETRY_DELAYS[attempt])
                        continue
                    break
            if source.exists():
                return TagResult(
                    exported,
                    False,
                    f"{result.message}；标签副本已保留，但原图无法移除："
                    f"回收站={recycle_error}；永久删除={delete_error}",
                )
        return TagResult(exported, True, f"{result.message}；{removal_message}")
    except OSError as exc:
        return TagResult(exported, False, f"{result.message}；标签副本已保留，但原图无法移除：{exc}")


def has_ai_filename(path: Path) -> bool:
    return bool(re.search(r"_AI(?: \(\d+\))?$", path.stem, flags=re.IGNORECASE))


def rename_tagged_file(path: Path) -> Path:
    """Add the _AI filename marker in place without replacing another file."""
    if has_ai_filename(path):
        return path
    candidate = path.with_name(f"{path.stem}_AI{path.suffix}")
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_AI ({index}){path.suffix}")
        index += 1
    return path.rename(candidate)
