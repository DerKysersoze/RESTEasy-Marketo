"""Server-side data sources for UI pickers.

Sources: "describe" (lead fields), "activityTypes" (bulk activity extract).
Add more fetch functions here and register them in app.py's lookup route.

P7 (lead field library): fetch_lead_fields also STORES the result in the
profile-scoped `lead_fields` table (see db.py) so pickers read a saved,
tagged list (custom vs system, via Marketo's isCustom flag) instead of
re-hitting Marketo on every page load.
"""
from __future__ import annotations

from . import auth, db
from .config import Profile


def _map_field(f: dict) -> dict | None:
    """Normalize the different field-metadata shapes Marketo returns.

    Shapes seen: Browse schema/fields.json (flat, richest — has isCustom),
    classic describe.json (rest/soap child objects, no isCustom), describe2.
    """
    name = f.get("restApiName") or f.get("name") or (f.get("rest") or {}).get("name")
    if not name:
        return None

    def tri(key: str):
        v = f.get(key)
        return None if v is None else bool(v)

    return {
        "name": name,
        "label": f.get("displayName") or f.get("label") or name,
        "dataType": f.get("dataType") or "",
        "readOnly": bool(f.get("readOnly") or (f.get("rest") or {}).get("readOnly")),
        # P7: system/custom tag. isCustom only comes from the Browse endpoint;
        # the describe.json fallback leaves it unknown (None).
        "isCustom": tri("isCustom"),
        "isHidden": tri("isHidden"),
        "isSensitive": tri("isSensitive"),
        "description": f.get("description") or "",
        "fieldLen": f.get("length"),
    }


def _dedupe(fields: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for f in fields:
        if f["name"] in seen:
            continue
        seen.add(f["name"])
        out.append(f)
    return out


def fetch_lead_fields(profile: Profile, max_pages: int = 10) -> list[dict]:
    """All lead fields for the field picker.

    Primary: the paginated Browse endpoint (schema/fields.json, 300/page).
    Fallback: classic Describe Leads (describe.json).
    """
    fields: list[dict] = []
    page_token: str | None = None
    browse_ok = False
    for _ in range(max_pages):
        params = [("batchSize", "300")]
        if page_token:
            params.append(("nextPageToken", page_token))
        resp = auth.request(profile, "GET", "/rest/v1/leads/schema/fields.json", params=params)
        if resp.status_code != 200:
            break
        browse_ok = True
        try:
            data = resp.json()
        except ValueError:
            break
        result = data.get("result") if isinstance(data, dict) else data
        if not isinstance(result, list):
            break
        for f in result:
            m = _map_field(f) if isinstance(f, dict) else None
            if m:
                fields.append(m)
        page_token = data.get("nextPageToken") if isinstance(data, dict) else None
        if not (isinstance(data, dict) and data.get("moreResult")):
            break

    if not fields:
        # Fallback: Describe Leads (single page, classic endpoint).
        if not browse_ok:
            resp = auth.request(profile, "GET", "/rest/v1/leads/describe.json")
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    for f in data.get("result", []) if isinstance(data, dict) else []:
                        m = _map_field(f) if isinstance(f, dict) else None
                        if m:
                            fields.append(m)
                except ValueError:
                    pass

    if not fields:
        raise auth.AuthError(
            "Couldn't load the field list from Marketo. Check that your API user has "
            "the 'Read-Only Lead' permission."
        )
    return _dedupe(fields)


def stored_fields(profile: Profile) -> list[dict]:
    """The stored field library for this profile (empty until first load)."""
    return db.list_lead_fields(profile.id)


def field_stats(profile: Profile) -> dict:
    """Home-card stats: total / custom / system / unknown + last load time."""
    return db.lead_field_stats(profile.id)


def fetch_and_store(profile: Profile, max_pages: int = 10) -> list[dict]:
    """Live-fetch lead fields from Marketo, store them in the library, return them."""
    fields = fetch_lead_fields(profile, max_pages=max_pages)
    db.store_lead_fields(profile.id, fields)
    return fields


def load_and_store(profile: Profile, max_pages: int = 10) -> dict:
    """The one-click 'Load field list' flow: fetch, store, return stats."""
    fetch_and_store(profile, max_pages=max_pages)
    return field_stats(profile)


def clear(profile: Profile) -> int:
    """Drop the stored library (e.g. to force a clean re-load)."""
    return db.clear_lead_fields(profile.id)


def fetch_activity_types(profile: Profile) -> list[dict]:
    """Activity type picker for bulk activity extract (checkboxes → ids)."""
    resp = auth.request(profile, "GET", "/rest/v1/activities/types.json")
    if resp.status_code != 200:
        raise auth.AuthError(
            f"Couldn't load activity types from Marketo (HTTP {resp.status_code}). "
            "Check the 'Read-Only Activity' permission."
        )
    try:
        data = resp.json()
    except ValueError:
        raise auth.AuthError("Couldn't read the activity types response.") from None
    out: list[dict] = []
    for t in data.get("result", []) if isinstance(data, dict) else []:
        if not isinstance(t, dict) or t.get("id") is None:
            continue
        out.append(
            {
                "name": str(t["id"]),
                "label": t.get("type") or str(t["id"]),
                "dataType": "integer",
                "readOnly": False,
            }
        )
    if not out:
        raise auth.AuthError("Marketo returned no activity types.")
    return out
