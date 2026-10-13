"""Check DLL filtering without invoking PyInstaller or creating artifacts."""
import ast
from pathlib import Path


def runtime_filter():
    source = Path(__file__).resolve().parents[1] / "release.spec"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "keep_runtime_entry")
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["keep_runtime_entry"]


def test_unrelated_icu_cannot_shadow_windows_qt_dependency():
    keep = runtime_filter()
    assert not keep(("icuuc.dll", "third-party/icuuc.dll", "BINARY"))
    assert not keep(("icudt78.dll", "third-party/icudt78.dll", "BINARY"))
    assert keep(("PySide6/Qt6Core.dll", "Qt6Core.dll", "BINARY"))
    assert keep(("python312.dll", "python312.dll", "BINARY"))
    assert keep(("PySide6/plugins/platforms/qwindows.dll", "qwindows.dll", "BINARY"))
