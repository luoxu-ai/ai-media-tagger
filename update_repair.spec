# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['update_repair.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('.\\repair_payload\\AI媒体标签工具.exe', 'payload'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PySide6', 'numpy', 'cv2', 'onnxruntime'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AI-Media-Tagger-Update-Repair',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=['assets\\app-icon.ico'],
    version='version_info_repair.txt',
)
