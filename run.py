"""Start RESTEasy-Marketo: local server + browser + tray icon.

Usage:  python run.py
Env:    MARKETO_UTIL_HOST (default 127.0.0.1)
        MARKETO_UTIL_PORT (default 8787)
        MARKETO_UTIL_DB   (default: per-OS app-data dir — see marketo_util/db.py)
        MARKETO_UTIL_NO_BROWSER=1 to skip opening the browser
        MARKETO_UTIL_NO_TRAY=1 to skip the system-tray icon (headless/CI)
"""
import os
import subprocess
import sys


def _venv_python_path():
    """Path to the app's own Python (`.venv`), relative to run.py — works from any folder."""
    root = os.path.dirname(os.path.abspath(__file__))
    if os.name == "nt":
        cand = os.path.join(root, ".venv", "Scripts", "python.exe")
    else:
        cand = os.path.join(root, ".venv", "bin", "python")
    return cand if os.path.exists(cand) else None


def _ensure_deps():
    """The app runs inside its own Python environment (`.venv`).

    If it was started with a different Python (e.g. the system `python`),
    re-start under the bundled one so a plain `python run.py` still works.
    """
    try:
        import uvicorn  # noqa: F401
        import keyring  # noqa: F401
        return
    except ImportError:
        pass

    if os.environ.get("_MARKETO_UTIL_VENV_RETRY") == "1":
        # Already inside the bundled Python and still missing packages → real problem.
        pip_fix = (r".venv\Scripts\python.exe -m pip install -r requirements.txt"
                   if os.name == "nt" else ".venv/bin/python -m pip install -r requirements.txt")
        print("This app couldn't start: its Python environment is missing required packages.\n"
              "If you installed it from source, reinstall them with:\n    " + pip_fix)
        sys.exit(1)

    venv_python = _venv_python_path()
    if venv_python is None:
        setup = ("    python -m venv .venv\n"
                 + r"    .venv\Scripts\python.exe -m pip install -r requirements.txt"
                 if os.name == "nt" else
                 "    python3 -m venv .venv\n"
                 + "    .venv/bin/python -m pip install -r requirements.txt")
        print("This app needs its own Python environment, but no `.venv` folder was found\n"
              "next to run.py. If you installed it from source, create one with:\n" + setup)
        sys.exit(1)

    os.environ["_MARKETO_UTIL_VENV_RETRY"] = "1"
    print("Switching to the app's own Python environment...")
    code = subprocess.run([venv_python, os.path.abspath(__file__)] + sys.argv[1:]).returncode
    sys.exit(code)


_ensure_deps()

import threading
import webbrowser

import uvicorn

from marketo_util import tray as tray_mod


def _attach_log_file() -> None:
    """The packaged app has no console window; route stdout/stderr to a file
    (app-data dir / marketo-utility.log) so a failed startup can be diagnosed.
    Never blocks startup if logging can't be set up."""
    if not getattr(sys, "frozen", False):
        return
    try:
        from marketo_util.db import app_data_dir

        stream = open(app_data_dir() / "marketo-utility.log", "a", buffering=1, encoding="utf-8")
    except Exception:
        return
    sys.stdout = stream
    sys.stderr = stream


def main() -> None:
    _attach_log_file()
    host = os.environ.get("MARKETO_UTIL_HOST", "127.0.0.1")
    port = int(os.environ.get("MARKETO_UTIL_PORT", "8787"))
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"

    if os.environ.get("MARKETO_UTIL_NO_BROWSER") != "1":
        def open_browser() -> None:
            import time

            time.sleep(0.7)
            webbrowser.open_new_tab(url)

        threading.Thread(target=open_browser, daemon=True).start()

    config = uvicorn.Config("marketo_util.app:app", host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    icon = tray_mod.make_icon(url, lambda: setattr(server, "should_exit", True))
    if sys.platform == "darwin" and icon is not None:
        # macOS: pystray must own the main thread; uvicorn's selector-based
        # asyncio loop runs fine on a background thread.
        threading.Thread(target=server.run, name="uvicorn", daemon=True).start()
        icon.run()  # blocks on the main thread until the tray menu says Quit
        return
    if icon is not None:
        # Windows/Linux: icon on a background thread, server on the main thread
        # (Windows' Proactor asyncio loop must stay on the main thread).
        threading.Thread(target=icon.run, name="tray", daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
