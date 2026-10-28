"""Official Marketo Engage REST error-code reference (P8).

Loads `catalog/error_codes.json` — a curated table verified from the
experienceleague error-codes page (see NOTES.md P8). Maps a numeric code to its
official description + the docs' clarification. Cached after first load.

Unknown codes return None; callers fall back to the HTTP status + Marketo's raw
message. This is important for overloaded codes (e.g. 1003 is officially
"Invalid data" but Marketo also returns it when a filter matches too many
leads) — the specific Marketo message is always appended so it's never ambiguous.
"""
from __future__ import annotations

import json
import threading

from .catalog import catalog_dir

_lock = threading.Lock()
_cache: dict | None = None
DOC_URL = (
    "https://experienceleague.adobe.com/en/docs/marketo-developer/marketo/rest/error-codes"
)


def _load() -> dict:
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                data = {"docUrl": DOC_URL, "codes": {}}
                try:
                    path = catalog_dir() / "error_codes.json"
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass  # keep the safe default if the file is missing/corrupt
                _cache = data
    return _cache


def doc_url() -> str:
    return _load().get("docUrl") or DOC_URL


def info(code) -> dict | None:
    """Return {code, desc, cat, comment} for a code, or None if unknown/blank."""
    if code in (None, ""):
        return None
    entry = _load().get("codes", {}).get(str(code))
    if entry is None:
        return None
    return {"code": str(code), **entry}


def describe(code, status, msg: str) -> str:
    """Plain-English message for a code.

    Official description + the docs' comment, then Marketo's specific message
    (when it adds information). Falls back to the HTTP status, then a generic
    line, when the code isn't in the reference.
    """
    e = info(code)
    if e:
        base = e.get("desc") or f"Marketo error {code}"
        comment = (e.get("comment") or "").strip()
        if comment:
            base = f"{base} — {comment}"
    else:
        base = f"Marketo returned HTTP {status}." if status else "Marketo returned an error."
    if msg and msg not in base:
        base = f"{base} Marketo said: {msg}"
    return base
