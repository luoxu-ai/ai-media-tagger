from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qt_app import MainWindow, apply_application_theme


def main() -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    paths = [
        Path(r"C:\样图\人身雨伞轻松站立郊游JPEG\2.jpg"),
        Path(r"C:\样图\人身雨伞轻松站立郊游JPEG\CBT1628_01_白底图.jpg"),
        Path(r"C:\样图\人身雨伞轻松站立郊游JPEG\CBT1628_02_卖点图_主要配件，简便还原.jpg"),
        Path(r"C:\样图\人身雨伞轻松站立郊游JPEG\CBT1628_03_卖点图_轻便随飞，活动自如.jpg"),
        Path(
            r"D:\软件\SuperBrowser\Super Browser\AMZ022UK-长沙市创优达贸易有限公司-第600旺季900单"
            r"\邓颖组\GB470644195\正式图片\肖倩\开学返校季\超长产品资料目录\最终审核版本"
            r"\CBT1628_04_超长文件名_用于验证路径不会覆盖右侧处理状态标签.jpg"
        ),
    ]
    window.files = paths
    window.refresh({str(path).casefold() for path in paths})
    statuses = (
        ("未检测到人物", "skipped"),
        ("已导出", "success"),
        ("处理中", "processing"),
        ("等待处理", "pending"),
        ("未检测到人物", "skipped"),
    )
    for path, status in zip(paths, statuses):
        window._set_file_status(path, *status)
    window.resize(1180, 820)
    apply_application_theme()
    window.show()
    app.processEvents()
    output = Path(__file__).resolve().parents[1] / "qa" / "ui-file-rows.png"
    success = window.grab().save(str(output))
    window.close()
    print(output)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
