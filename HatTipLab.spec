# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata


PROJECT_DIR = Path(SPECPATH)

a = Analysis(
    [str(PROJECT_DIR / "desktop_pet.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    datas=[
        (str(PROJECT_DIR / "web"), "web"),
        (str(PROJECT_DIR / "docs"), "docs"),
        (str(PROJECT_DIR / "model-pack-template"), "model-pack-template"),
        (str(PROJECT_DIR / "assets" / "piper"), "assets/piper"),
        (str(PROJECT_DIR / "assets" / "piper-runtime"), "assets/piper-runtime"),
        (str(PROJECT_DIR / "README.md"), "."),
        (str(PROJECT_DIR / "LICENSE"), "."),
    ] + copy_metadata("edge-tts"),
    hiddenimports=[
        "edge_tts",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ],
    module_collection_mode={"edge_tts": "py"},
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HatTipLab",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="HatTipLab",
)
