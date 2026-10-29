"""Start RESTEasy-Marketo: local server + browser.

Usage:  python run.py
Env:    MARKETO_UTIL_HOST (default 127.0.0.1)
        MARKETO_UTIL_PORT (default 8787)
        MARKETO_UTIL_DB   (default: per-OS app-data dir — see marketo_util/db.py)
        MARKETO_UTIL_NO_BROWSER=1 to skip opening the browser
"""
import os
import sys
import threading
import webbrowser

import uvicorn


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
    if os.environ.get("MARKETO_UTIL_NO_BROWSER") != "1":
        url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"

        def open_browser() -> None:
            import time

            time.sleep(0.7)
            webbrowser.open_new_tab(url)

        threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("marketo_util.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
