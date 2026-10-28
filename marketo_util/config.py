"""Market connection profiles: instance name + OAuth client credentials.

Client secrets live in the OS credential manager when one is available
(secrets.py), with the local SQLite DB as fallback — see PLAN.md
"Secrets at rest". Swapping storage or scoping profiles per user later only
touches this module. See PLAN.md section 7 and NOTES.md "Scaling notes".
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import db, secrets


class ConfigError(ValueError):
    pass


@dataclass
class Profile:
    id: int
    name: str
    instance: str
    client_id: str
    client_secret: str
    is_active: bool


def normalize_instance(value: str) -> str:
    """Accept '100-AEK-913', '100-AEK-913.mktorest.com', or a full URL."""
    v = (value or "").strip()
    for prefix in ("https://", "http://"):
        if v.lower().startswith(prefix):
            v = v[len(prefix) :]
    v = v.split("/")[0].strip(".").lower()
    if not v:
        raise ConfigError("Instance name is required (e.g. 100-AEK-913)")
    if "." not in v:
        v = f"{v}.mktorest.com"
    return v


def _store_secret(profile_id: int, secret: str) -> None:
    """Move a fresh secret into the credential store when one is available.

    The caller has already persisted it to the DB as a safety net, so any
    failure here just leaves the secret in the DB (safe, never lost).
    """
    if not secrets.available():
        return
    try:
        secrets.store(profile_id, secret)
    except Exception:
        return
    try:
        conn = db.get_conn()
        conn.execute("UPDATE profiles SET client_secret = '' WHERE id = ?", (profile_id,))
        conn.commit()
    except sqlite3.Error:
        pass  # secret now exists in both places — safe


def _resolve_secret(profile_id: int, stored: str) -> str:
    """Resolve a profile's secret for auth: credential store first.

    Legacy rows (secret only in the DB) are migrated into the store here.
    """
    s = secrets.get(profile_id)
    if s:
        return s
    if stored:
        _store_secret(profile_id, stored)
    return stored


def _to_profile(row) -> Profile:
    return Profile(
        id=row["id"],
        name=row["name"],
        instance=row["instance"],
        client_id=row["client_id"],
        client_secret=_resolve_secret(row["id"], row["client_secret"]),
        is_active=bool(row["is_active"]),
    )


def list_profiles() -> list[Profile]:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM profiles ORDER BY is_active DESC, name COLLATE NOCASE"
    ).fetchall()
    return [_to_profile(r) for r in rows]


def get_profile(profile_id: int) -> Profile | None:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return _to_profile(row) if row else None


def get_active() -> Profile | None:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM profiles WHERE is_active = 1 LIMIT 1").fetchone()
    return _to_profile(row) if row else None


def create_profile(name: str, instance: str, client_id: str, client_secret: str) -> Profile:
    name = (name or "").strip()
    client_id = (client_id or "").strip()
    if not name:
        raise ConfigError("Connection name is required")
    if not client_id:
        raise ConfigError("Client ID is required")
    if not (client_secret or "").strip():
        raise ConfigError("Client secret is required")
    instance = normalize_instance(instance)
    secret = client_secret.strip()
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO profiles (name, instance, client_id, client_secret) VALUES (?, ?, ?, ?)",
            (name, instance, client_id, secret),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()  # don't leave an open (locking) transaction behind
        raise ConfigError("A connection with that name already exists") from exc
    except sqlite3.OperationalError as exc:
        conn.rollback()
        raise ConfigError("The local database is busy — try again in a moment.") from exc
    _store_secret(cur.lastrowid, secret)
    return get_profile(cur.lastrowid)


def update_profile(profile_id: int, **fields) -> Profile | None:
    allowed = {"name", "instance", "client_id"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "instance" in updates:
        updates["instance"] = normalize_instance(updates["instance"])
    new_secret = (fields.get("client_secret") or "").strip()
    conn = db.get_conn()
    try:
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE profiles SET {sets} WHERE id = ?", (*updates.values(), profile_id))
        if new_secret:
            # rotation: land it in the DB first (no window where it lives
            # nowhere), then move it to the credential store and blank the DB
            conn.execute(
                "UPDATE profiles SET client_secret = ? WHERE id = ?", (new_secret, profile_id)
            )
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise ConfigError("Could not save the connection — the local database is busy.") from exc
    if new_secret:
        _store_secret(profile_id, new_secret)
    return get_profile(profile_id)


def set_active(profile_id: int) -> Profile | None:
    conn = db.get_conn()
    try:
        conn.execute("UPDATE profiles SET is_active = 0")
        conn.execute("UPDATE profiles SET is_active = 1 WHERE id = ?", (profile_id,))
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise ConfigError("Could not switch the active connection.") from exc
    return get_profile(profile_id)


def delete_profile(profile_id: int) -> None:
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise ConfigError("Could not delete the connection.") from exc
    secrets.delete(profile_id)  # best-effort: also remove the credential-store entry
