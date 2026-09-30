# -*- mode: python ; coding: utf-8 -*-

import os


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('soundradar.ico', '.')],
    hiddenimports=['soundcard', 'comtypes'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_soundradar.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# PyInstaller can resolve Qt's ICU import to an unrelated third-party ICU DLL
# found on the build machine's PATH. Qt expects the unversioned ICU exports
# provided by Windows, so keep those host DLLs out of the redistributable.
a.binaries = [
    entry for entry in a.binaries
    if not os.path.basename(entry[0]).lower().startswith("icu")
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SoundRadar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['soundradar.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SoundRadar',
)
