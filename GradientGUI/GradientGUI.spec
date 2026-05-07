# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for GradientGUI.
Build with: pyinstaller GradientGUI.spec
"""

import os
import sys

block_cipher = None
spec_file = globals().get('__file__') or globals().get('SPEC')
spec_dir = os.path.dirname(os.path.abspath(spec_file)) if spec_file else os.getcwd()
libass_dir = os.path.join(spec_dir, 'libass')

is_windows = sys.platform == 'win32'
is_macos = sys.platform == 'darwin'


def existing_file(path, dest='.'):
    return [(path, dest)] if os.path.isfile(path) else []


def existing_dir(path, dest):
    return [(path, dest)] if os.path.isdir(path) else []


def native_suffixes():
    if is_windows:
        return ('.dll',)
    if is_macos:
        return ('.dylib',)
    return ('.so',)


def native_binaries_from(directory, dest):
    if not os.path.isdir(directory):
        return []
    suffixes = native_suffixes()
    binaries = []
    for name in os.listdir(directory):
        lower = name.lower()
        if any(suffix in lower for suffix in suffixes):
            binaries.append((os.path.join(directory, name), dest))
    return binaries


native_binaries = []
if is_windows:
    native_binaries += existing_file(os.path.join(spec_dir, 'libmpv-2.dll'), '.')
    native_binaries += native_binaries_from(libass_dir, 'libass')
else:
    # Linux/macOS development builds usually use system libmpv/libass.
    # If portable native libraries are placed beside the app, bundle them.
    native_binaries += native_binaries_from(spec_dir, '.')
    native_binaries += native_binaries_from(libass_dir, 'libass')

data_files = existing_dir(os.path.join(spec_dir, 'res'), 'res')

a = Analysis(
    [os.path.join(spec_dir, 'main.py')],
    pathex=[spec_dir],
    binaries=native_binaries,
    datas=data_files,
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtWidgets',
        'mpv',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'html',
        'xml', 'pydoc', 'doctest',
        'PySide6.QtNetwork', 'PySide6.QtQml',
        'PySide6.QtQuick', 'PySide6.QtSvg',
        'PySide6.QtWebEngine', 'PySide6.QtMultimedia',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GradientGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # No console window for GUI app
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='GradientGUI',
)
