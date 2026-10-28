"""App settings (P8): export/save location + call-logging + bulk-file-save toggles.

Stored as a single row in the `settings` table (see db.py). Kept separate from
config.py (which is about Marketo connections) so the two concerns don't tangle.

Decisions (user-confirmed 2026-10-28):
  - `log_calls` defaults OFF (opt-in) — lead data isn't written to disk until
    the user flips it on.
  - `save_bulk_files` is a SEPARATE toggle (bulk pulls are rare for one user).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from . import db


class SettingsError(ValueError):
    pass


def default_dir() -> str:
    """Default export folder: <app-data>/exports."""
    return str(db.app_data_dir() / "exports")


def _row() -> dict:
    conn = db.get_conn()
    r = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    if r is None:
        try:
            conn.execute("INSERT INTO settings (id) VALUES (1)")
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
        r = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    return dict(r)


def get_all() -> dict:
    r = _row()
    return {
        "export_dir": r["export_dir"] or default_dir(),
        "log_calls": bool(r["log_calls"]),
        "save_bulk_files": bool(r["save_bulk_files"]),
        "default_dir": default_dir(),
    }


def get(key: str, default=None):
    r = _row()
    v = r.get(key)
    return default if v in (None, "") else v


def export_dir() -> Path:
    return Path(get("export_dir") or default_dir())


def log_enabled() -> bool:
    return bool(get("log_calls", 0))


def save_bulk_enabled() -> bool:
    return bool(get("save_bulk_files", 0))


def _validate_dir(value: str) -> Path:
    v = (value or "").strip()
    if not v:
        raise SettingsError("A folder path is required.")
    p = Path(v).expanduser()
    if not p.is_absolute():
        raise SettingsError("Use an absolute folder path (e.g. C:\\Exports\\marketo).")
    return p


def test_dir(value: str) -> tuple[bool, str]:
    """Return (ok, reason). Checks the folder can be created and is writable."""
    try:
        p = _validate_dir(value)
    except SettingsError as exc:
        return False, str(exc)
    try:
        p.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=str(p))
        with os.fdopen(fd, "wb") as f:
            f.write(b"ok")
        Path(probe).unlink()
        return True, "Folder exists and is writable."
    except OSError as exc:
        return False, f"Could not write there: {exc}"


def set_all(
    export_dir: str | None = None,
    log_calls: bool | None = None,
    save_bulk_files: bool | None = None,
) -> dict:
    updates: dict = {}
    if export_dir is not None:
        p = _validate_dir(export_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)  # ensure the folder exists
        except OSError as exc:
            raise SettingsError(f"Could not create that folder: {exc}") from exc
        updates["export_dir"] = str(p)
    if log_calls is not None:
        updates["log_calls"] = 1 if log_calls else 0
    if save_bulk_files is not None:
        updates["save_bulk_files"] = 1 if save_bulk_files else 0
    if updates:
        conn = db.get_conn()
        sets = ", ".join(f"{k} = ?" for k in updates)
        try:
            conn.execute(
                f"UPDATE settings SET {sets}, updated_at = datetime('now') WHERE id = 1",
                (*updates.values(),),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()  # thread-local conn: don't hold a failed transaction
            raise SettingsError("Could not save settings — the local database is busy.") from exc
    return get_all()
