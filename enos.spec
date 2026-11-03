# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds a single-file ``links-enos`` executable.

    python3 -m pip install "pyinstaller>=6.0"
    python3 -m PyInstaller --clean --noconfirm enos.spec

Result: ``dist/links-enos`` (one file, no Python required on the target machine).
The binary speaks the same stdio protocol, so clients can be pointed at it with
``install_mcp.py --mode binary --binary dist/links-enos``.
"""

from pathlib import Path

project_root = Path(SPECPATH).resolve()

a = Analysis(
    [str(project_root / "enos" / "mcp_server.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    # The package is imported lazily by name; make sure it is collected.
    hiddenimports=["enos", "enos.store", "enos.mcp_server"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
        "test",
        "distutils",
        "setuptools",
        "pip",
        "lib2to3",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="links-enos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # stdio transport requires a console binary
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
