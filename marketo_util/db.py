"""SQLite storage: profiles, bulk jobs, presets, recent calls.

Client secrets live in the OS credential manager (secrets.py) with this DB as
fallback; OAuth tokens are in memory only (auth.py) — neither is stored here.

Local-first (one file in the user's app-data dir), but the same schema will
carry over to a future shared-server deployment. All SQL lives in this module
so the storage backend can be swapped later without touching callers
(see PLAN.md section 7 and NOTES.md "Scaling notes").
"""
from __future__ import annotations

import json
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
CREATE TABLE IF NOT EXISTS field_sets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  fields TEXT NOT NULL DEFAULT '[]',   -- JSON array of lead-field API names, order kept
  position INTEGER NOT NULL DEFAULT 0, -- user ordering (↑/↓ reorder); 0 = first
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (profile_id, name)
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


class FieldSetError(ValueError):
    """Friendly validation error for saved field sets (→ HTTP 400)."""


class FieldSetNotFound(FieldSetError):
    """The field set id doesn't belong to this connection (→ HTTP 404)."""


# --- saved field sets (P12): named, per-connection collections of lead fields ---

def _fs_name(name) -> str:
    n = str(name or "").strip()
    if not n:
        raise FieldSetError("Give the field set a name.")
    if len(n) > 60:
        raise FieldSetError("Keep the field set name under 60 characters.")
    return n


def _fs_fields(fields) -> list[str]:
    """Normalize a raw field list to clean API names (order kept, exact dups dropped)."""
    if fields is None:
        raise FieldSetError("A field set needs at least one field.")
    if not isinstance(fields, (list, tuple)):
        raise FieldSetError("Fields must be a list of field names.")
    seen: set[str] = set()
    out: list[str] = []
    for f in fields:
        n = str(f or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    if not out:
        raise FieldSetError("A field set needs at least one field.")
    return out


def _fs_shape(row) -> dict:
    try:
        fs = json.loads(row["fields"])
        if not isinstance(fs, list):
            fs = []
    except (TypeError, ValueError):
        fs = []
    return {"id": row["id"], "name": row["name"], "fields": fs, "createdAt": row["created_at"]}


def get_field_set(profile_id: int, set_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name, fields, created_at FROM field_sets WHERE id = ? AND profile_id = ?",
        (set_id, profile_id),
    ).fetchone()
    return _fs_shape(row) if row else None


def list_field_sets(profile_id: int) -> list[dict]:
    """A connection's saved field sets in user order (position, then id)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, fields, created_at FROM field_sets "
        "WHERE profile_id = ? ORDER BY position, id",
        (profile_id,),
    ).fetchall()
    return [_fs_shape(r) for r in rows]


def create_field_set(profile_id: int, name: str, fields: list[str]) -> int:
    """Create a set (appended after the last). Raises FieldSetError on bad input / duplicate name."""
    nm = _fs_name(name)
    fs = _fs_fields(fields)
    conn = get_conn()
    dup = conn.execute(
        "SELECT 1 FROM field_sets WHERE profile_id = ? AND lower(name) = lower(?)",
        (profile_id, nm),
    ).fetchone()
    if dup:
        raise FieldSetError(f"A field set named “{nm}” already exists for this connection.")
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM field_sets WHERE profile_id = ?",
        (profile_id,),
    ).fetchone()[0]
    try:
        cur = conn.execute(
            "INSERT INTO field_sets (profile_id, name, fields, position) VALUES (?, ?, ?, ?)",
            (profile_id, nm, json.dumps(fs), pos),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return cur.lastrowid


def rename_field_set(profile_id: int, set_id: int, name: str) -> None:
    nm = _fs_name(name)
    conn = get_conn()
    if conn.execute(
        "SELECT 1 FROM field_sets WHERE id = ? AND profile_id = ?", (set_id, profile_id)
    ).fetchone() is None:
        raise FieldSetNotFound("No such field set for this connection.")
    dup = conn.execute(
        "SELECT 1 FROM field_sets WHERE profile_id = ? AND id != ? AND lower(name) = lower(?)",
        (profile_id, set_id, nm),
    ).fetchone()
    if dup:
        raise FieldSetError(f"A field set named “{nm}” already exists for this connection.")
    try:
        conn.execute("UPDATE field_sets SET name = ? WHERE id = ? AND profile_id = ?", (nm, set_id, profile_id))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def replace_field_set_fields(profile_id: int, set_id: int, fields: list[str]) -> None:
    fs = _fs_fields(fields)
    conn = get_conn()
    if conn.execute(
        "SELECT 1 FROM field_sets WHERE id = ? AND profile_id = ?", (set_id, profile_id)
    ).fetchone() is None:
        raise FieldSetNotFound("No such field set for this connection.")
    try:
        conn.execute(
            "UPDATE field_sets SET fields = ? WHERE id = ? AND profile_id = ?",
            (json.dumps(fs), set_id, profile_id),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def move_field_set(profile_id: int, set_id: int, delta: int) -> None:
    """Swap with the neighbor (delta = -1 up, +1 down); renumber the whole list. Edge = no-op."""
    if delta not in (-1, 1):
        raise FieldSetError("Use -1 (up) or 1 (down) to move a field set.")
    conn = get_conn()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM field_sets WHERE profile_id = ? ORDER BY position, id",
        (profile_id,),
    ).fetchall()]
    if set_id not in ids:
        raise FieldSetNotFound("No such field set for this connection.")
    i, j = ids.index(set_id), ids.index(set_id) + delta
    if j < 0 or j >= len(ids):
        return  # already at that edge
    ids[i], ids[j] = ids[j], ids[i]
    try:
        for k, sid in enumerate(ids):
            conn.execute("UPDATE field_sets SET position = ? WHERE id = ?", (k, sid))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def delete_field_set(profile_id: int, set_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM field_sets WHERE id = ? AND profile_id = ?", (set_id, profile_id))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def import_field_sets(profile_id: int, sets) -> dict:
    """Bulk import {name, fields} entries: add new names, skip existing ones
    (case-insensitive), count invalid entries. Returns {added, skipped, invalid}."""
    if sets is None:
        sets = []
    if not isinstance(sets, (list, tuple)):
        raise FieldSetError("Expected a list of {name, fields} entries under “sets”.")
    added = skipped = invalid = 0
    for s in sets:
        if not isinstance(s, dict):
            invalid += 1
            continue
        name = str(s.get("name") or "").strip()
        raw = s.get("fields")
        if not name or not isinstance(raw, (list, tuple)):
            invalid += 1
            continue
        fs = [str(f).strip() for f in raw if str(f).strip()]
        if not fs:
            invalid += 1
            continue
        conn = get_conn()
        if conn.execute(
            "SELECT 1 FROM field_sets WHERE profile_id = ? AND lower(name) = lower(?)",
            (profile_id, name),
        ).fetchone():
            skipped += 1
            continue
        try:
            create_field_set(profile_id, name, fs)
            added += 1
        except FieldSetError:
            invalid += 1
    return {"added": added, "skipped": skipped, "invalid": invalid}


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
