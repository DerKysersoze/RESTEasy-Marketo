"""FastAPI app: JSON API under /api, static UI at /."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    __version__,
    auth,
    bulk,
    calllog,
    catalog,
    config,
    db,
    executor,
    lookups,
    settings,
)

STATIC_DIR = Path(__file__).parent / "static"

try:
    CALLS = catalog.load_catalog()
except catalog.CatalogError as exc:  # surface bad catalog entries loudly at startup
    raise SystemExit(f"Catalog error: {exc}") from exc

try:
    BULK_OPS = bulk.load_ops()
except bulk.BulkError as exc:
    raise SystemExit(f"Bulk catalog error: {exc}") from exc

app = FastAPI(title="RESTEasy-Marketo", version=__version__)


# --- connections (profiles) --------------------------------------------------
class ProfileIn(BaseModel):
    name: str
    instance: str
    client_id: str
    client_secret: str = ""


def _public(p: config.Profile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "instance": p.instance,
        "client_id": p.client_id,
        "is_active": p.is_active,
    }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "catalog_calls": len(CALLS)}


@app.get("/api/catalog")
def get_catalog() -> dict:
    return {"groups": catalog.group_catalog(CALLS)}


@app.get("/api/recent")
def recent_calls(profile_id: int | None = None, limit: int = 12) -> dict:
    """Recently run calls for the home page (bulk op ids included, as 'bulk.*')."""
    p = config.get_profile(profile_id) if profile_id else config.get_active()
    if p is None:
        return {"recent": []}
    return {"recent": db.list_recent(p.id, limit)}


@app.get("/api/catalog/{call_id}")
def get_call(call_id: str) -> dict:
    c = catalog.get_call(call_id, CALLS)
    if c is None:
        raise HTTPException(404, "Unknown call id")
    return c.raw


@app.get("/api/config/profiles")
def list_profiles() -> dict:
    return {"profiles": [_public(p) for p in config.list_profiles()]}


@app.post("/api/config/profiles")
def create_profile(body: ProfileIn) -> dict:
    try:
        p = config.create_profile(body.name, body.instance, body.client_id, body.client_secret)
    except config.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    if config.get_active() is None:  # first connection auto-activates
        p = config.set_active(p.id)
    return _public(p)


@app.patch("/api/config/profiles/{profile_id}")
def update_profile(profile_id: int, body: ProfileIn) -> dict:
    try:
        p = config.update_profile(
            profile_id,
            name=body.name,
            instance=body.instance,
            client_id=body.client_id,
            client_secret=body.client_secret or None,
        )
    except config.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    if p is None:
        raise HTTPException(404, "Connection not found")
    return _public(p)


@app.delete("/api/config/profiles/{profile_id}")
def delete_profile(profile_id: int) -> dict:
    config.delete_profile(profile_id)
    return {"ok": True}


@app.post("/api/config/active")
def set_active(profile_id: int) -> dict:
    p = config.set_active(profile_id)
    if p is None:
        raise HTTPException(404, "Connection not found")
    return _public(p)


@app.post("/api/config/test")
def test_connection(profile_id: int | None = None) -> dict:
    p = config.get_profile(profile_id) if profile_id else config.get_active()
    if p is None:
        raise HTTPException(400, "No connection to test — add one first.")
    try:
        auth.get_token(p, force=True)
    except auth.AuthError as exc:
        return {"ok": False, "detail": str(exc)}
    # A cheap real API call proves the token works end-to-end.
    try:
        resp = auth.request(p, "GET", "/rest/v1/activities/types.json")
    except auth.AuthError as exc:
        return {"ok": False, "detail": str(exc)}
    if resp.status_code == 200:
        return {"ok": True, "detail": "Connection verified — credentials and permissions look good."}
    if resp.status_code in (401, 403):
        return {
            "ok": True,
            "detail": (
                f"Credentials work, but the test call was denied (HTTP {resp.status_code}). "
                "Your API user may lack the 'Read-Only Activity' permission — other calls may still work."
            ),
        }
    return {"ok": True, "detail": f"Credentials accepted (test call returned HTTP {resp.status_code})."}


@app.get("/api/auth/status")
def auth_status(profile_id: int | None = None) -> dict:
    """Cached access-token state for a connection (masked — never the full token)."""
    p = config.get_profile(profile_id) if profile_id else config.get_active()
    if p is None:
        raise HTTPException(400, "No connection configured — add one first.")
    return {
        "profile": {"id": p.id, "name": p.name, "instance": p.instance},
        "token": auth.token_status(p),
    }


# --- lookup sources for UI pickers ---------------------------------------------
@app.get("/api/lookup/{source}")
def lookup(source: str, profile_id: int | None = None) -> JSONResponse:
    """Data source for field pickers (catalog params with type 'lookup')."""
    p = config.get_profile(profile_id) if profile_id else config.get_active()
    if p is None:
        raise HTTPException(400, "No Marketo connection configured — add one first.")
    try:
        if source == "describe":
            # P7: prefer the stored field library (instant, no API call);
            # on first use, live-fetch, store, and return.
            stored = lookups.stored_fields(p)
            if stored:
                return JSONResponse({"fields": stored})
            return JSONResponse({"fields": lookups.fetch_and_store(p)})
        if source == "activityTypes":
            return JSONResponse({"fields": lookups.fetch_activity_types(p)})
    except auth.AuthError as exc:
        raise HTTPException(502, str(exc)) from exc
    raise HTTPException(404, "Unknown lookup source")


# --- lead field library (P7) ------------------------------------------------------
@app.get("/api/fields")
def list_fields(profile_id: int | None = None) -> JSONResponse:
    """Stored lead-field list + stats (home card)."""
    p = config.get_profile(profile_id) if profile_id else config.get_active()
    if p is None:
        raise HTTPException(400, "No Marketo connection configured — add one first.")
    return JSONResponse({"fields": lookups.stored_fields(p), "stats": lookups.field_stats(p)})


@app.post("/api/fields/refresh")
def refresh_fields(profile_id: int | None = None) -> JSONResponse:
    """One-click load: pull the field list from Marketo (Browse, isCustom tags) and store it."""
    p = config.get_profile(profile_id) if profile_id else config.get_active()
    if p is None:
        raise HTTPException(400, "No Marketo connection configured — add one first.")
    try:
        stats = lookups.load_and_store(p)
    except auth.AuthError as exc:
        raise HTTPException(502, str(exc)) from exc
    return JSONResponse({"ok": True, "stats": stats})


@app.delete("/api/fields")
def clear_fields(profile_id: int | None = None) -> JSONResponse:
    """Clear the stored list (then Refresh to re-load)."""
    p = config.get_profile(profile_id) if profile_id else config.get_active()
    if p is None:
        raise HTTPException(400, "No Marketo connection configured — add one first.")
    return JSONResponse({"ok": True, "cleared": lookups.clear(p)})


# --- settings (P8): export location + call logging + bulk-file save ----------
class SettingsIn(BaseModel):
    export_dir: str | None = None
    log_calls: bool | None = None
    save_bulk_files: bool | None = None


@app.get("/api/settings")
def get_settings() -> JSONResponse:
    return JSONResponse(settings.get_all())


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> JSONResponse:
    try:
        updated = settings.set_all(
            export_dir=body.export_dir,
            log_calls=body.log_calls,
            save_bulk_files=body.save_bulk_files,
        )
    except settings.SettingsError as exc:
        return JSONResponse({"ok": False, "error": {"friendly": str(exc)}}, status_code=400)
    return JSONResponse({"ok": True, "settings": updated})


@app.post("/api/settings/test-dir")
def test_settings_dir(body: dict) -> JSONResponse:
    ok, reason = settings.test_dir(body.get("path", ""))
    return JSONResponse({"ok": ok, "reason": reason})


@app.delete("/api/settings/log")
def clear_settings_log() -> JSONResponse:
    """Remove all call-log files in the export dir (best-effort)."""
    try:
        d = settings.export_dir()
        removed = 0
        if d.is_dir():
            for f in d.glob("marketo-calls-*.jsonl"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return JSONResponse({"ok": True, "removed": removed})
    except OSError as exc:
        return JSONResponse({"ok": False, "error": {"friendly": str(exc)}}, status_code=400)


# --- running calls ------------------------------------------------------------
class CallIn(BaseModel):
    params: dict = {}
    profile_id: int | None = None


@app.post("/api/call/{call_id}")
def run_call(call_id: str, body: CallIn) -> JSONResponse:
    c = catalog.get_call(call_id, CALLS)
    if c is None:
        raise HTTPException(404, "Unknown call id")
    p = config.get_profile(body.profile_id) if body.profile_id else config.get_active()
    if p is None:
        return JSONResponse(
            {
                "ok": False,
                "error": {
                    "friendly": (
                        "No Marketo connection configured yet. Add your instance name and "
                        "client ID/secret (top-right) to run calls."
                    )
                },
            },
            status_code=400,
        )
    try:
        result = executor.run_call(p, c, body.params)
    except executor.ValidationError as exc:
        db.record_recent(p.id, c.id, False)
        calllog.maybe_log(p.name, c.id, body.params, {"ok": False, "error": {"friendly": str(exc)}})
        return JSONResponse({"ok": False, "error": {"friendly": str(exc)}}, status_code=400)
    db.record_recent(p.id, c.id, bool(result.get("ok", True)))
    calllog.maybe_log(p.name, c.id, body.params, result)
    return JSONResponse(result)


# --- bulk jobs (Phase 4 wizards) -------------------------------------------------
def _profile_or_none(profile_id: int | None = None) -> config.Profile | None:
    return config.get_profile(profile_id) if profile_id else config.get_active()


@app.get("/api/bulk/ops")
def bulk_ops_list() -> dict:
    return {"ops": BULK_OPS}


@app.get("/api/bulk/op/{op_id}")
def bulk_op_get(op_id: str) -> dict:
    op = bulk.get_op(op_id)
    if op is None:
        raise HTTPException(404, "Unknown bulk operation")
    return op


@app.post("/api/bulk/op/{op_id}/start")
async def bulk_op_start(op_id: str, request: Request) -> JSONResponse:
    """Start a bulk job. Extracts send JSON {form, profile_id}; imports send
    multipart form data with a `file` part + fields."""
    op = bulk.get_op(op_id)
    if op is None:
        raise HTTPException(404, "Unknown bulk operation")
    profile = None
    form: dict = {}
    file: tuple[bytes, str] | None = None
    ctype = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ctype:
        data = await request.form()
        for key, value in data.items():
            if isinstance(value, (bytes,)) or hasattr(value, "read"):
                if file is not None:
                    return JSONResponse({"ok": False, "error": {"friendly": "Send only one file."}}, status_code=400)
                content = await value.read() if hasattr(value, "read") else value
                file = (content, value.filename or "upload")
            elif key == "profile_id":
                continue
            else:
                form[key] = value
        profile = _profile_or_none()
    else:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"ok": False, "error": {"friendly": "Send a JSON body: {form: {…}}."}}, status_code=400)
        form = body.get("form") or {}
        pid = body.get("profile_id")
        profile = config.get_profile(int(pid)) if pid else config.get_active()
    if profile is None:
        return JSONResponse(
            {"ok": False, "error": {"friendly": "No Marketo connection configured yet. Add your instance name and client ID/secret (top-right) first."}},
            status_code=400,
        )
    try:
        job = bulk.start_job(profile, op, form, file)
    except bulk.BulkError as exc:
        return JSONResponse({"ok": False, "error": {"friendly": str(exc)}}, status_code=400)
    db.record_recent(profile.id, op.id, True)
    return JSONResponse({"ok": True, "job": job})


@app.get("/api/bulk/jobs")
def bulk_jobs_list(profile_id: int | None = None) -> dict:
    p = config.get_profile(profile_id) if profile_id else config.get_active()
    if p is None:
        return {"jobs": []}
    return {"jobs": bulk.list_jobs(p.id)}


def _job_profile(job_id: int) -> tuple[dict | None, config.Profile | None]:
    job = bulk.get_job(job_id)
    if job is None:
        return None, None
    return job, config.get_profile(job["profile_id"])


@app.get("/api/bulk/jobs/{job_id}")
def bulk_job_status(job_id: int) -> JSONResponse:
    job, p = _job_profile(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if p is None:
        return JSONResponse({"ok": False, "error": {"friendly": "The connection this job used no longer exists."}}, status_code=400)
    try:
        return JSONResponse({"ok": True, "job": bulk.refresh_job(p, job_id)})
    except bulk.BulkError as exc:
        return JSONResponse({"ok": False, "error": {"friendly": str(exc)}}, status_code=400)


@app.post("/api/bulk/jobs/{job_id}/cancel")
def bulk_job_cancel(job_id: int) -> JSONResponse:
    job, p = _job_profile(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if p is None:
        return JSONResponse({"ok": False, "error": {"friendly": "The connection this job used no longer exists."}}, status_code=400)
    try:
        return JSONResponse({"ok": True, "job": bulk.cancel_job(p, job_id)})
    except bulk.BulkError as exc:
        return JSONResponse({"ok": False, "error": {"friendly": str(exc)}}, status_code=400)


def _file_response(job_id: int, fetch) -> Response | JSONResponse:
    job, p = _job_profile(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if p is None:
        return JSONResponse({"ok": False, "error": {"friendly": "The connection this job used no longer exists."}}, status_code=400)
    try:
        content, mime, filename = fetch(p, job_id)
    except bulk.BulkError as exc:
        return JSONResponse({"ok": False, "error": {"friendly": str(exc)}}, status_code=400)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    saved = calllog.save_bulk_file(content, filename)  # best-effort; only if toggled on
    if saved is not None:
        headers["X-Marketo-Util-Saved-To"] = str(saved)
    return Response(content=content, media_type=mime, headers=headers)


@app.get("/api/bulk/jobs/{job_id}/file")
def bulk_job_file(job_id: int):
    return _file_response(job_id, bulk.download_file)


@app.get("/api/bulk/jobs/{job_id}/failures")
def bulk_job_failures(job_id: int):
    return _file_response(job_id, lambda p, j: bulk.download_report(p, j, "failures"))


@app.get("/api/bulk/jobs/{job_id}/warnings")
def bulk_job_warnings(job_id: int):
    return _file_response(job_id, lambda p, j: bulk.download_report(p, j, "warnings"))


# --- static UI -----------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
