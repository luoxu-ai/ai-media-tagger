from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qt_app import MainWindow, STYLE


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.resize(1180, 820)
    window.show()
    app.processEvents()
    output = Path(__file__).resolve().parents[1] / "qa" / "ui-final-empty.png"
    if not window.grab().save(str(output)):
        return 1
    window.close()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
