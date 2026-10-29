"""OAuth client-credentials flow + per-process token cache.

Aligned with the Marketo Identity spec (experienceleague, verified 2026-10-29):
- Token: {Identity URL}/oauth/token with grant_type=client_credentials
  (Identity URL = https://{instance}/identity — live-verified 2026-10-29).
- A fresh token lives 1 hour (expires_in ≈ 3600). We refresh 2 minutes before
  expiry, and on HTTP 401 or Marketo codes 601/602 (token invalid/expired) we
  force a fresh token and retry the request once — per the docs, 601/602 are
  recovered by re-authenticating.
- Bearer header only: the access_token query-param auth was removed from
  Marketo 2026-08-31 and we never use it.
- The response's scope + expiry are surfaced to the UI via token_status();
  the full token is never sent to the client or written to disk.

Tokens are re-derivable from the client credentials, so they live in memory
only (never on disk). The client secret itself is protected by secrets.py.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx

from .config import Profile

TOKEN_MARGIN_SEC = 120
REQUEST_TIMEOUT = 120.0
DEFAULT_TOKEN_LIFETIME_SEC = 3600  # docs: a new access token lives 1 hour

_token_cache: dict[int, "TokenInfo"] = {}  # profile_id -> TokenInfo
_token_lock = threading.Lock()


class AuthError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class TokenInfo:
    """One Marketo access token + the Identity response metadata around it."""

    token: str
    expires_in: int
    token_type: str = "bearer"
    scope: str = ""
    obtained_at: float = field(default_factory=time.time)

    @property
    def expires_at(self) -> float:
        return self.obtained_at + max(self.expires_in, 60)

    @property
    def valid(self) -> bool:
        return self.expires_at - TOKEN_MARGIN_SEC > time.time()

    def masked(self) -> str:
        if len(self.token) <= 12:
            return self.token[:2] + "…"
        return f"{self.token[:8]}…{self.token[-4:]}"


def base_url(profile: Profile) -> str:
    return f"https://{profile.instance}"


def _token_error_message(resp: httpx.Response) -> str:
    """Plain-English, actionable message for an Identity-endpoint failure."""
    error = description = ""
    try:
        d = resp.json()
        if isinstance(d, dict):
            error = str(d.get("error") or "")
            description = str(d.get("error_description") or "")
    except ValueError:
        pass
    base = f"Marketo rejected the credentials (HTTP {resp.status_code})."
    if error == "invalid_client":
        return (
            f"{base} The saved Client ID/secret pair was rejected (invalid_client). "
            "The secret was likely regenerated in Marketo since this connection was saved — "
            "get the current one from Marketo (Admin → Integration → Web Services) and "
            "update the connection."
        )
    if error or description:
        extra = " — ".join(x for x in (error, description) if x)
        return f"{base} ({extra})"
    return f"{base} Check the client ID and client secret."


def fetch_token(profile: Profile) -> TokenInfo:
    url = f"{base_url(profile)}/identity/oauth/token"
    try:
        resp = httpx.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": profile.client_id,
                "client_secret": profile.client_secret,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise AuthError(
            f"Could not reach {profile.instance} — check the instance name and your network connection."
        ) from exc
    if resp.status_code != 200:
        raise AuthError(
            _token_error_message(resp), status=resp.status_code, body=resp.text
        )
    try:
        data = resp.json()
        return TokenInfo(
            token=str(data["access_token"]),
            expires_in=int(data.get("expires_in", DEFAULT_TOKEN_LIFETIME_SEC)),
            token_type=str(data.get("token_type") or "bearer"),
            scope=str(data.get("scope") or ""),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthError(
            "Marketo's token response was missing the access token — see the connection status for details.",
            body=(resp.text or "")[:2000],
        ) from exc


def get_token(profile: Profile, force: bool = False) -> str:
    """A valid token for the profile, reusing the in-memory cache when possible.

    force=True (401/601/602 retry path) always installs and returns the freshly
    fetched token — a still-unexpired cached entry must not shadow it.
    """
    with _token_lock:
        if not force:
            entry = _token_cache.get(profile.id)
            if entry and entry.valid:
                return entry.token
    info = fetch_token(profile)
    with _token_lock:
        if force:
            _token_cache[profile.id] = info  # forced refresh wins
        else:
            entry = _token_cache.get(profile.id)
            if entry and entry.valid:
                return entry.token  # another worker refreshed while we were fetching
            _token_cache[profile.id] = info
    return info.token


def token_status(profile: Profile) -> dict:
    """UI-facing state of the cached token. Never includes the full token."""
    with _token_lock:
        info = _token_cache.get(profile.id)
    if info is None:
        return {"state": "none"}
    return {
        "state": "valid" if info.valid else "expired",
        "obtainedAt": info.obtained_at,
        "expiresAt": info.expires_at,
        "scope": info.scope,
        "tokenMasked": info.masked(),
    }


def _body_error_code(resp: httpx.Response) -> str:
    """First code from Marketo's response-level errors[] array, if any."""
    try:
        data = resp.json()
    except ValueError:
        return ""
    errs = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errs, list) and errs and isinstance(errs[0], dict):
        return str(errs[0].get("code") or "")
    return ""


def request(
    profile: Profile,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str]] | None = None,
    json: object = None,
    files: dict | None = None,
    data: dict | None = None,
) -> httpx.Response:
    """Run one Marketo API request, auto-refreshing the token on 401/601/602 (one retry)."""
    url = f"{base_url(profile)}{path}"
    last: httpx.Response | None = None
    for attempt in (1, 2):
        token = get_token(profile, force=(attempt == 2))
        try:
            last = httpx.request(
                method,
                url,
                params=params,
                json=json,
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"The request failed before reaching Marketo: {exc}") from exc
        if last.status_code != 401 and _body_error_code(last) not in ("601", "602"):
            return last
    return last
