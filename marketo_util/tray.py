"""System-tray icon so users can see the local server is running.

The packaged app has no window of its own — the tray icon (notification
area, right side of the taskbar) is the always-visible sign of life, and
its menu provides the actions the UI used to be the only way to get:

  • Open RESTEasy  — (re)open the app in the browser
  • Copy URL       — put the app URL on the clipboard
  • Quit RESTEasy  — stop the server and exit (the app previously had no
                     graceful way to quit)

Graceful degradation: if pystray/Pillow are missing (or the user sets
MARKETO_UTIL_NO_TRAY=1), ``make_icon`` returns None and the app behaves
exactly as before.

Env:
  MARKETO_UTIL_NO_TRAY=1      disable the tray icon (headless/CI)
  MARKETO_UTIL_TRAY_ICON=path use a custom icon image
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("marketo_util.tray")

APP_NAME = "RESTEasy-Marketo"


def _icon_path() -> Path | None:
    env = os.environ.get("MARKETO_UTIL_TRAY_ICON")
    if env and Path(env).is_file():
        return Path(env)
    # marketo_util/tray.py -> project root; when frozen, -> the _MEIPASS dir
    # (the spec bundles packaging/icon.png there, see RESTEasy-Marketo.spec).
    bundled = Path(__file__).resolve().parent.parent / "packaging" / "icon.png"
    return bundled if bundled.is_file() else None


def _load_image():
    """Load the tray icon (RGBA), falling back to a generated one."""
    from PIL import Image

    path = _icon_path()
    if path is not None:
        img = Image.open(path)
    else:
        from PIL import ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(37, 99, 235, 255))
        draw.ellipse([16, 16, 48, 48], fill=(255, 255, 255, 255))
    img = img.convert("RGBA")  # Windows needs a 32-bit icon with alpha
    if max(img.size) > 64:
        img.thumbnail((64, 64))
    return img


def _copy_to_clipboard(text: str) -> None:
    """Best-effort clipboard write using OS-native tools (no extra deps)."""
    def run(cmd: list[str]) -> None:
        subprocess.run(cmd, input=text.encode("utf-8"),
                       check=False, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        if sys.platform.startswith("win"):
            run(["clip"])
        elif sys.platform == "darwin":
            run(["pbcopy"])
        else:
            for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
                try:
                    run(cmd)
                    return
                except FileNotFoundError:
                    continue
    except Exception as exc:  # clipboard is a nicety, never break the menu
        log.warning("copy to clipboard failed: %s", exc)


def make_icon(url: str, on_quit) -> "object | None":
    """Build the tray icon. Returns a pystray.Icon, or None if unavailable.

    ``on_quit`` is called by the Quit menu item before the icon stops —
    point it at the uvicorn server shutdown (``server.should_exit = True``).
    The returned icon has NOT been started yet; call ``icon.run()`` on a
    thread (Windows/Linux) or the main thread (macOS).
    """
    if os.environ.get("MARKETO_UTIL_NO_TRAY") == "1":
        return None
    try:
        import pystray

        image = _load_image()
    except Exception as exc:
        log.warning("tray icon unavailable (continuing without it): %s", exc)
        return None

    def open_app(_item=None) -> None:
        import webbrowser

        webbrowser.open(url)

    def quit_app(_item=None) -> None:
        try:
            on_quit()
        except Exception as exc:
            log.warning("quit handler failed: %s", exc)
        finally:
            # Ends the icon's message loop (icon.run() returns); safe to call
            # from the menu action — pystray's stop() just signals the loop.
            try:
                icon.stop()
            except Exception:
                pass

    icon = pystray.Icon(
        "resteasy-marketo",
        image,
        f"{APP_NAME} — running at {url}",
        pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", open_app, default=True),
            pystray.MenuItem("Copy URL", lambda _i=None: _copy_to_clipboard(url)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Quit {APP_NAME}", quit_app),
        ),
    )
    return icon
