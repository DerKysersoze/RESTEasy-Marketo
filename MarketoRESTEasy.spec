# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Marketo RESTEasy.

One spec, both OSes:
  Windows -> dist/MarketoRESTEasy.exe  (single file, no console)
  macOS   -> dist/Marketo RESTEasy.app (bundle, no console)

Data layout matches the code's path resolution exactly, so the app needs no
frozen-specific tweaks:
  catalog/            <- catalog.py:  Path(__file__).parent.parent / "catalog"
  marketo_util/static <- app.py:      Path(__file__).parent / "static"
Everything mutable (DB, settings, exports, logs) lives in the OS app-data dir,
never inside the bundle.

Build:  pip install -r requirements.txt pyinstaller
        pyinstaller MarketoRESTEasy.spec --noconfirm
"""
import sys

from PyInstaller.utils.hooks import collect_submodules


def _subs(module: str) -> list:
    """collect_submodules that tolerates platform-optional packages
    (uvloop: macOS/Linux only; keyrings.alt: macOS only; ...)."""
    try:
        return collect_submodules(module)
    except Exception:
        return []


hiddenimports = (
    # run.py passes "marketo_util.app:app" to uvicorn as a string, so the app
    # module is only imported at runtime — collect the whole package explicitly
    collect_submodules("marketo_util")
    # uvicorn loads its loop/protocol/lifespan modules by name at runtime
    + _subs("uvicorn")
    # uvicorn[standard] extras (installed per platform)
    + _subs("uvloop")
    + _subs("httptools")
    + _subs("websockets")
    + _subs("wsproto")
    # keyring discovers backends via entry points; backends import platform libs
    + _subs("keyring")
    + _subs("keyrings.alt")
    + _subs("pywin32_ctypes")
)

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("catalog", "catalog"),
        ("marketo_util/static", "marketo_util/static"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

if sys.platform == "win32":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="MarketoRESTEasy",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,  # no console window; run.py redirects output to a log file
        icon="packaging/icon.ico",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="MarketoRESTEasy",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon="packaging/icon.icns",
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="MarketoRESTEasy",
    )
    # NOTE: the name must include the .app extension — PyInstaller does not
    # append it (building/osx.py uses the name verbatim as the dist folder).
    app = BUNDLE(
        coll,
        name="Marketo RESTEasy.app",
        icon="packaging/icon.icns",
        bundle_identifier="com.marketo.resteasy",
        version="0.1.0",
    )
