# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for RESTEasy-Marketo.

One spec, both OSes:
  Windows -> dist/RESTEasy-Marketo.exe  (single file, no console)
  macOS   -> dist/RESTEasy-Marketo.app (bundle, no console)

Data layout matches the code's path resolution exactly, so the app needs no
frozen-specific tweaks:
  catalog/            <- catalog.py:  Path(__file__).parent.parent / "catalog"
  marketo_util/static <- app.py:      Path(__file__).parent / "static"
  packaging/icon.png  <- tray.py:     Path(__file__).parent.parent / "packaging/icon.png"
Everything mutable (DB, settings, exports, logs) lives in the OS app-data dir,
never inside the bundle.

The app shows a system-tray icon (pystray) so users can see the server is
running and can Open / Copy URL / Quit from its menu.

Build:  pip install -r requirements.txt pyinstaller
        pyinstaller RESTEasy-Marketo.spec --noconfirm

Pitfall (fixed below): `collect_submodules()` checks package-ness with
importlib.util.find_spec() in the spec process and scans in an isolated
subprocess that inherits that same sys.path. When PyInstaller is launched
as a console script (`.venv/Scripts/pyinstaller ...`), the project root is
NOT on sys.path, and collect_submodules() then silently collects NOTHING
for marketo_util ("marketo_util is not a package") — shipping an exe that
dies at startup with `Could not import module "marketo_util.app"`.
The bootstrap below pins the project root onto sys.path so the build is
deterministic however pyinstaller is invoked, and the sanity check after
`hiddenimports` turns any regression into a loud build failure.
"""
import os
import sys

# Pin the spec's directory (project root) onto sys.path BEFORE any module
# collection — see the docstring note above. `SPEC` is provided by
# PyInstaller's spec namespace.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(SPEC))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PyInstaller.utils.hooks import collect_submodules

# Single source of truth for the version number: marketo_util/__init__.py.
# The bootstrap above puts the project root on sys.path, so this import is
# safe however pyinstaller is invoked.
from marketo_util import __version__ as APP_VERSION


def _win_version_resource():
    """Windows version resource (exe file properties) built from APP_VERSION.

    PyInstaller's EXE(version=...) accepts a VSVersionInfo structure; a plain
    string is rejected, so build the structure here. Cross-platform import is
    safe: PyInstaller's versioninfo module is pure Python (win32api is only
    touched at write time on Windows).
    """
    from PyInstaller.utils.win32 import versioninfo

    parts = [int(x) for x in APP_VERSION.split(".")[:4]]
    parts += [0] * (4 - len(parts))
    # Layout required by the Windows version reader (GetFileVersionInfoW):
    #   VS_VERSION_INFO -> FixedFileInfo + StringFileInfo -> StringTable -> StringStructs
    # The StringFileInfo layer is mandatory — omitting it produces a resource
    # Windows silently rejects (empty "Details" tab).
    return versioninfo.VSVersionInfo(
        versioninfo.FixedFileInfo(filevers=tuple(parts), prodvers=tuple(parts)),
        kids=[versioninfo.StringFileInfo(kids=[
            versioninfo.StringTable("040904B0", [
                versioninfo.StringStruct("FileDescription", "RESTEasy-Marketo"),
                versioninfo.StringStruct("ProductName", "RESTEasy-Marketo"),
                versioninfo.StringStruct("LegalCopyright",
                                         "Community project. Not affiliated with or endorsed by Adobe."),
                versioninfo.StringStruct("FileVersion", APP_VERSION),
                versioninfo.StringStruct("ProductVersion", APP_VERSION),
            ]),
        ])],
    )


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
    # tray icon: pystray imports its platform backend (pystray._win32, …)
    # at runtime; Pillow renders the icon image
    + _subs("pystray")
    + _subs("PIL")
)

# Fail loudly if the app package was only partially collected — a silent gap
# here ships an exe that crashes at startup (see docstring pitfall note).
_required = ("marketo_util", "marketo_util.app", "marketo_util.bulk",
            "marketo_util.executor", "marketo_util.catalog")
_missing = [m for m in _required if m not in hiddenimports]
if _missing:
    raise SystemExit(
        f"spec sanity check failed: hiddenimports is missing {_missing}. "
        "Do NOT ship this build — restore the sys.path bootstrap above."
    )

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("catalog", "catalog"),
        ("marketo_util/static", "marketo_util/static"),
    ]
    # Tray icon image (tray.py falls back to a generated image if absent).
    + ([("packaging/icon.png", "packaging")]
       if os.path.exists("packaging/icon.png") else []),
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
        name="RESTEasy-Marketo",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,  # no console window; run.py redirects output to a log file
        icon="packaging/icon.ico",
        # File properties → Details tab shows the current version (see _win_version_resource).
        version=_win_version_resource(),
        # Windows: PyInstaller (6.x) has no built-in Authenticode support — the
        # exe is signed in a post-build step (CI does this in .github/workflows/build.yml;
        # locally: Sign-Authenticode / signtool with your code-signing .pfx).
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="RESTEasy-Marketo",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon="packaging/icon.icns",
        # macOS: when a signing identity is in the keychain (set MACOS_CODESIGN_IDENTITY,
        # e.g. "Developer ID Application: Name (TEAMID)"), PyInstaller signs the exe,
        # its dependencies, and the .app bundle with hardened runtime + timestamp.
        # Without it, signing is ad-hoc ("-"), which satisfies nothing downstream.
        # Post-build signing also works:
        #   codesign --force --deep --options runtime --timestamp --sign "<identity>" dist/RESTEasy-Marketo.app
        codesign_identity=os.environ.get("MACOS_CODESIGN_IDENTITY") or None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="RESTEasy-Marketo",
    )
    # NOTE: the name must include the .app extension — PyInstaller does not
    # append it (building/osx.py uses the name verbatim as the dist folder).
    app = BUNDLE(
        coll,
        name="RESTEasy-Marketo.app",
        icon="packaging/icon.icns",
        bundle_identifier="com.marketo.resteasy",
        version=APP_VERSION,  # CFBundleShortVersionString in the .app's Info.plist
    )
