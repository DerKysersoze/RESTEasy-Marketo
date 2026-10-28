"""SQLite storage: profiles, bulk jobs, presets, recent calls.

Client secrets live in the OS credential manager (secrets.py) with this DB as
fallback; OAuth tokens are in memory only (auth.py) — neither is stored here.

Local-first (one file in the user's app-data dir), but the same schema will
carry over to a future shared-server deployment. All SQL lives in this module
so the storage backend can be swapped later without touching callers
(see PLAN.md section 7 and NOTES.md "Scaling notes").
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  instance TEXT NOT NULL,
  client_id TEXT NOT NULL,
  client_secret TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  marketo_job_id TEXT,
  state TEXT NOT NULL DEFAULT 'created',
  detail TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS presets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  call_id TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (profile_id, name)
);
CREATE TABLE IF NOT EXISTS recent_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  call_id TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}',
  ok INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lead_fields (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  api_name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  data_type TEXT NOT NULL DEFAULT '',
  is_custom INTEGER,            -- 1 custom / 0 standard(system) / NULL unknown (describe.json fallback)
  read_only INTEGER NOT NULL DEFAULT 0,
  is_hidden INTEGER,
  is_sensitive INTEGER,
  description TEXT,
  field_len INTEGER,
  loaded_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (profile_id, api_name)
);
CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),   -- single-row app settings (P8)
  export_dir TEXT NOT NULL DEFAULT '',
  log_calls INTEGER NOT NULL DEFAULT 0,
  save_bulk_files INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _default_data_dir(os_name: str, sys_platform: str, home: Path, environ: dict) -> Path:
    """Pure mapping (os.name, sys.platform, home, env) -> default data dir.

    Kept free of globals so tests can exercise every branch on any host OS.
    """
    if os_name == "nt":
        base = Path(environ.get("APPDATA", str(home)))
        return base / "MarketoUtility"
    if sys_platform == "darwin":
        return home / "Library" / "Application Support" / "MarketoUtility"
    base = Path(environ.get("XDG_DATA_HOME", str(home / ".local" / "share")))
    return base / "marketo-utility"


def app_data_dir() -> Path:
    """Default data directory, following each OS's convention.

    Windows: %APPDATA%/MarketoUtility
    macOS:   ~/Library/Application Support/MarketoUtility
    Linux:   $XDG_DATA_HOME/marketo-utility (or ~/.local/share/marketo-utility)
    (Fully override the DB file itself with MARKETO_UTIL_DB.)
    """
    d = _default_data_dir(os.name, sys.platform, Path.home(), os.environ)
    d.mkdir(parents=True, exist_ok=True)
    return d


def record_recent(profile_id: int, call_id: str, ok: bool) -> None:
    """Append one row to the recent-calls log (home page 'Recently run')."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO recent_calls (profile_id, call_id, ok) VALUES (?, ?, ?)",
        (profile_id, call_id, 1 if ok else 0),
    )
    conn.commit()


def list_recent(profile_id: int, limit: int = 12) -> list[dict]:
    """Most recent calls first, for one connection."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT call_id, ok, created_at FROM recent_calls "
        "WHERE profile_id = ? ORDER BY id DESC LIMIT ?",
        (profile_id, max(1, limit)),
    ).fetchall()
    return [
        {"call_id": r["call_id"], "ok": bool(r["ok"]), "created_at": r["created_at"]}
        for r in rows
    ]


def _tri(value) -> int | None:
    """bool|None -> 1/0/NULL for the tri-state flags (is_custom, is_hidden, is_sensitive)."""
    if value is None:
        return None
    return 1 if value else 0


def store_lead_fields(profile_id: int, fields: list[dict]) -> int:
    """Upsert a profile's lead-field library (P7). `fields` uses the lookups shape:
    {name, label, dataType, readOnly, isCustom?, isHidden?, isSensitive?, description?, fieldLen?}."""
    conn = get_conn()
    sql = """
      INSERT INTO lead_fields
        (profile_id, api_name, display_name, data_type, is_custom,
         read_only, is_hidden, is_sensitive, description, field_len)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT (profile_id, api_name) DO UPDATE SET
        display_name = excluded.display_name,
        data_type = excluded.data_type,
        is_custom = excluded.is_custom,
        read_only = excluded.read_only,
        is_hidden = excluded.is_hidden,
        is_sensitive = excluded.is_sensitive,
        description = excluded.description,
        field_len = excluded.field_len,
        loaded_at = datetime('now')
    """
    try:
        for f in fields:
            conn.execute(
                sql,
                (
                    profile_id,
                    f["name"],
                    f["label"],
                    f.get("dataType") or "",
                    _tri(f.get("isCustom")),
                    1 if f.get("readOnly") else 0,
                    _tri(f.get("isHidden")),
                    _tri(f.get("isSensitive")),
                    f.get("description") or "",
                    f.get("fieldLen"),
                ),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()  # thread-local conns: a failed DML must not hold a transaction
        raise
    return len(fields)


def list_lead_fields(profile_id: int) -> list[dict]:
    """Stored field library, custom fields first, then alphabetical."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM lead_fields WHERE profile_id = ?
           ORDER BY CASE WHEN is_custom = 1 THEN 0
                         WHEN is_custom IS NULL THEN 2
                         ELSE 1 END,
                    display_name COLLATE NOCASE""",
        (profile_id,),
    ).fetchall()
    return [
        {
            "name": r["api_name"],
            "label": r["display_name"],
            "dataType": r["data_type"],
            "readOnly": bool(r["read_only"]),
            "isCustom": None if r["is_custom"] is None else bool(r["is_custom"]),
            "isHidden": None if r["is_hidden"] is None else bool(r["is_hidden"]),
            "isSensitive": None if r["is_sensitive"] is None else bool(r["is_sensitive"]),
            "description": r["description"],
            "fieldLen": r["field_len"],
        }
        for r in rows
    ]


def lead_field_stats(profile_id: int) -> dict:
    """Counts for the home card: total / custom / system / unknown + last load time."""
    conn = get_conn()
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN is_custom = 1 THEN 1 ELSE 0 END) AS custom,
                  SUM(CASE WHEN is_custom = 0 THEN 1 ELSE 0 END) AS system,
                  MAX(loaded_at) AS last_loaded
             FROM lead_fields WHERE profile_id = ?""",
        (profile_id,),
    ).fetchone()
    total = row["total"] or 0
    custom = row["custom"] or 0
    system = row["system"] or 0
    return {
        "count": total,
        "custom": custom,
        "system": system,
        "unknown": total - custom - system,
        "lastLoaded": row["last_loaded"],
    }


def clear_lead_fields(profile_id: int) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM lead_fields WHERE profile_id = ?", (profile_id,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return cur.rowcount


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        db_path = os.environ.get("MARKETO_UTIL_DB") or str(app_data_dir() / "marketo_util.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL + busy timeout: thread-pool workers each hold a connection; WAL lets
        # readers proceed during writes, and busy_timeout avoids flaky 'database is locked'.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(SCHEMA)
        conn.commit()
        _local.conn = conn
    return conn
