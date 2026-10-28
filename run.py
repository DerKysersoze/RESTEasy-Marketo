"""Start the Marketo REST Utility: local server + browser.

Usage:  python run.py
Env:    MARKETO_UTIL_HOST (default 127.0.0.1)
        MARKETO_UTIL_PORT (default 8787)
        MARKETO_UTIL_DB   (default: per-OS app-data dir — see marketo_util/db.py)
        MARKETO_UTIL_NO_BROWSER=1 to skip opening the browser
"""
import os
import threading
import webbrowser

import uvicorn


def main() -> None:
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
