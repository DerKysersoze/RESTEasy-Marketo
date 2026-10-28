"""OAuth client-credentials flow + per-process token cache.

Marketo tokens live ~20 minutes; we refresh 2 minutes before expiry and, on a
401, force a fresh token and retry the request once.

Tokens are re-derivable from the client credentials, so they live in memory
only (never on disk). The client secret itself is protected by secrets.py.
"""
from __future__ import annotations

import threading
import time

import httpx

from .config import Profile

TOKEN_MARGIN_SEC = 120
REQUEST_TIMEOUT = 120.0

_token_cache: dict[int, tuple[str, float]] = {}  # profile_id -> (token, expires_at)
_token_lock = threading.Lock()


class AuthError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def base_url(profile: Profile) -> str:
    return f"https://{profile.instance}"


def fetch_token(profile: Profile) -> tuple[str, int]:
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
            f"Marketo rejected the credentials (HTTP {resp.status_code}). "
            "Check the client ID and client secret.",
            status=resp.status_code,
            body=resp.text,
        )
    data = resp.json()
    return data["access_token"], int(data.get("expires_in", 1200))


def get_token(profile: Profile, force: bool = False) -> str:
    """A valid token for the profile, reusing the in-memory cache when possible."""
    now = time.time()
    with _token_lock:
        if not force:
            entry = _token_cache.get(profile.id)
            if entry and entry[1] - TOKEN_MARGIN_SEC > now:
                return entry[0]
    token, expires_in = fetch_token(profile)
    expires_at = time.time() + max(expires_in, 60)
    with _token_lock:
        entry = _token_cache.get(profile.id)
        if entry and entry[1] - TOKEN_MARGIN_SEC > time.time():
            return entry[0]  # another worker refreshed while we were fetching
        _token_cache[profile.id] = (token, expires_at)
    return token


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
    """Run one Marketo API request, auto-refreshing the token (one 401 retry)."""
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
        if last.status_code != 401:
            return last
    return last
