"""Client-secret storage: OS credential manager first, local SQLite as fallback.

Policy (see PLAN.md "Secrets at rest"):
- When the OS credential store is available (Windows Credential Manager,
  macOS Keychain, Linux Secret Service) the client secret lives THERE and the
  SQLite copy is blanked.
- When it is not (e.g. headless Linux with no secret service) the secret
  falls back to the local SQLite DB — the pre-existing behavior.
- Legacy rows (secret still in the DB, store empty) are migrated lazily on
  first read.
- OAuth tokens are NOT stored here: they are re-derivable from the
  credentials, so auth.py keeps them in memory only.

The secret is consumed only by auth.py (the API never returns it to the UI),
so all resolution is centralized in this module.
"""
from __future__ import annotations

import uuid

import keyring

SERVICE = "Marketo Utility"

# Result of the one-per-process availability probe (None = not probed yet).
_keyring_ok: bool | None = None


def _username(profile_id: int) -> str:
    return f"profile-{profile_id}"


def reset_probe() -> None:
    """Testing hook: force the availability probe to run again."""
    global _keyring_ok
    _keyring_ok = None


def available() -> bool:
    """Whether the OS credential store can actually round-trip a value.

    Probed once per process with a throwaway entry. A bare try/except is not
    enough: some backends (e.g. the headless-Linux "fail" keyring) silently
    accept writes and return nothing, which would lose the secret.
    """
    global _keyring_ok
    if _keyring_ok is None:
        probe = f"__probe-{uuid.uuid4().hex}"
        try:
            keyring.set_password(SERVICE, probe, probe)
            ok = keyring.get_password(SERVICE, probe) == probe
            keyring.delete_password(SERVICE, probe)
        except Exception:
            ok = False
        _keyring_ok = bool(ok)
    return _keyring_ok


def store(profile_id: int, secret: str) -> None:
    """Write the secret to the credential store (no-op if unavailable)."""
    if not available():
        return
    keyring.set_password(SERVICE, _username(profile_id), secret)


def get(profile_id: int) -> str | None:
    """The secret from the credential store, or None if not there."""
    if not available():
        return None
    return keyring.get_password(SERVICE, _username(profile_id)) or None


def delete(profile_id: int) -> None:
    """Best-effort removal of the credential-store entry."""
    try:
        keyring.delete_password(SERVICE, _username(profile_id))
    except Exception:
        pass
