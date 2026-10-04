# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Subtitle Text Fixer (one-folder portable build).

Build locally:  build_exe.bat
Or via CI:      .github/workflows/build.yml
"""

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# tkinterdnd2 ships native drag & drop binaries (tkdnd/*) that must ride along,
# and sv-ttk ships its Sun Valley theme assets (*.tcl).
tkdnd_datas = collect_data_files("tkinterdnd2")
sv_ttk_datas = collect_data_files("sv_ttk")

a = Analysis(
    ["subtitle_fixer.py"],
    pathex=[],
    binaries=[],
    datas=tkdnd_datas + sv_ttk_datas + [
        ("custom_rules.example.json", "."),
        ("examples", "examples"),
    ],
    hiddenimports=["tkinterdnd2"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SubtitleTextFixer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app; CLI still works via `SubtitleTextFixer.exe *.srt`
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SubtitleTextFixer",
)
