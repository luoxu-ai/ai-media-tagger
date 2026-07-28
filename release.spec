# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['qt_app.py'],
    pathex=[],
    binaries=[('.\\vendor\\exiftool.exe', '.')],
    datas=[
        ('.\\vendor\\exiftool_files', 'exiftool_files'),
        ('.\\assets\\app-icon.png', 'assets'),
        ('.\\assets\\app-icon.ico', 'assets'),
        ('.\\models\\dfine_m_human_parts_trial.onnx', 'models'),
        ('.\\models\\face_detection_yunet_2023mar.onnx', 'models'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'transformers', 'tensorboard',
        'sympy', 'mpmath', 'onnxruntime.transformers',
        'PySide6.QtBluetooth', 'PySide6.QtDesigner', 'PySide6.QtHelp',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtPdf',
        'PySide6.QtPdfWidgets', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.QtQuickWidgets', 'PySide6.QtSql', 'PySide6.QtTest',
        'PySide6.QtWebChannel', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtXml',
    ],
    noarchive=False,
    optimize=0,
)

# Keep only runtime plugins used by this Windows Widgets application. These
# entries do not affect detection models or media metadata support.
def keep_runtime_entry(entry):
    target = str(entry[0]).replace('\\', '/').casefold()
    if 'cv2/opencv_videoio_ffmpeg' in target:
        return False
    if target.endswith('pyside6/opengl32sw.dll'):
        return False
    if '/plugins/platforms/' in target and not target.endswith('/qwindows.dll'):
        return False
    if '/plugins/imageformats/' in target and not target.endswith('/qico.dll'):
        return False
    if any(part in target for part in (
        '/plugins/tls/', '/plugins/networkinformation/',
        '/plugins/platforminputcontexts/', '/plugins/generic/',
        '/plugins/iconengines/',
    )):
        return False
    return True

a.binaries = [entry for entry in a.binaries if keep_runtime_entry(entry)]
a.datas = [entry for entry in a.datas if keep_runtime_entry(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AI媒体标签工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app-icon.ico'],
    version='version_info.txt',
)
