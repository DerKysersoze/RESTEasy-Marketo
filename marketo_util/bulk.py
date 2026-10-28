"""Bulk job engine: operation definitions + job lifecycle (start → poll → fetch).

Design (see PLAN.md Phase 4 + NOTES.md):
- Definitions live in catalog/bulk/*.json — one file per bulk operation. They
  carry the friendly form spec, the Marketo endpoint templates, and metadata.
- This module is the engine: validates the form, builds the exact request,
  tracks jobs in the `jobs` DB table (profile-scoped, survives restarts), and
  proxies status/file/failures/warnings calls with the same rate-limit gate
  and friendly error translation as the regular executor.

Note: Marketo bulk paths do NOT include `/rest/` — they start at /bulk/v1/…
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from . import auth, db, executor
from .config import Profile

MAX_IMPORT_BYTES = 10 * 1024 * 1024  # 10 MB import file cap (HTTP 413 above this)

TERMINAL_STATES = {"completed", "failed", "canceled", "expired"}

# Marketo status strings (case-insensitive) → our job states.
_STATE_MAP = {
    "created": "created",
    "queued": "queued",
    "processing": "processing",
    "importing": "processing",
    "completed": "completed",
    "complete": "completed",
    "failed": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
}

_PARAM_TYPES = {"string", "integer", "datetime", "csv", "json", "file", "lookup", "select"}


class BulkError(ValueError):
    """Friendly, user-facing bulk error (rendered by the UI as-is).

    `status` carries the HTTP status of the failing Marketo call (if any),
    so callers can react to specific codes (e.g. 404 = expired job ID).
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# --- operation definitions ----------------------------------------------------

_lock = threading.Lock()
_OPS: list[dict] = []
OPS_BY_ID: dict[str, dict] = {}


def bulk_dir() -> Path:
    env = os.environ.get("MARKETO_UTIL_CATALOG")
    if env:
        return Path(env) / "bulk"
    return Path(__file__).resolve().parent.parent / "catalog" / "bulk"


def _validate_op(op: dict, path: Path) -> None:
    problems: list[str] = []
    for k in ("id", "name", "group", "kind", "summary", "create", "form"):
        if k not in op:
            problems.append(f"missing '{k}'")
    if op.get("kind") not in ("extract", "import"):
        problems.append("'kind' must be 'extract' or 'import'")
    if not str(op.get("id", "")).startswith("bulk."):
        problems.append("id must start with 'bulk.'")
    create = op.get("create") or {}
    if not str(create.get("path", "")).startswith("/"):
        problems.append("create.path must start with '/'")
    if op.get("kind") == "extract":
        for k in ("enqueue", "status", "file", "cancel"):
            if k not in op:
                problems.append(f"extract ops need a '{k}' endpoint")
    else:
        for k in ("status", "failures", "warnings"):
            if k not in op:
                problems.append(f"import ops need a '{k}' endpoint")
        if not create.get("multipart"):
            problems.append("import ops need create.multipart = true")
    forms = op.get("form") or []
    if not forms:
        problems.append("form must have at least one field")
    seen: set[str] = set()
    for f in forms:
        if not isinstance(f, dict):
            problems.append("form entries must be objects")
            continue
        for k in ("name", "label", "type"):
            if k not in f:
                problems.append(f"form field missing '{k}'")
        if f.get("type") not in _PARAM_TYPES:
            problems.append(f"bad type '{f.get('type')}' for '{f.get('name')}'")
        if f.get("name") in seen:
            problems.append(f"duplicate form field '{f.get('name')}'")
        f.get("name") and seen.add(f["name"])
        dyn = f.get("dynamicFields")
        if dyn is not None and (not isinstance(dyn, dict) or not all(isinstance(v, list) for v in dyn.values())):
            problems.append(f"'dynamicFields' for '{f.get('name')}' must be an object of option → field lists")
    if problems:
        raise BulkError(f"{path.name}: " + "; ".join(problems))


def load_ops(force: bool = False) -> list[dict]:
    """Load + validate bulk operation definitions (cached; force=reload)."""
    with _lock:
        if _OPS and not force:
            return list(_OPS)
        _OPS.clear()
        OPS_BY_ID.clear()
        root = bulk_dir()
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                try:
                    op = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise BulkError(f"{path.name}: invalid JSON — {exc}") from exc
                _validate_op(op, path)
                _OPS.append(op)
        _OPS.sort(key=lambda o: (0 if o["group"] == "Bulk Extract" else 1, o["name"].lower()))
        OPS_BY_ID.update({o["id"]: o for o in _OPS})
        return list(_OPS)


def get_op(op_id: str) -> dict | None:
    return OPS_BY_ID.get(op_id)


# --- small parsing helpers -----------------------------------------------------

def _req_str(value, label: str) -> str:
    v = (str(value).strip() if value is not None else "")
    if not v:
        raise BulkError(f"Missing required field: {label}.")
    return v


def _as_int(value, label: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise BulkError(f"{label} must be a whole number.") from None


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _as_int_list(value, label: str) -> list[int]:
    out = []
    for v in _as_str_list(value):
        try:
            out.append(int(v))
        except ValueError:
            raise BulkError(f"{label} must be whole numbers separated by commas (got '{v}').") from None
    return out


def _maybe_json(value) -> dict | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise BulkError("That's not valid JSON — check the braces and commas.") from exc
    if not isinstance(data, dict):
        raise BulkError("Expected a JSON object like {\"field\": \"Header\"}.")
    return data


def _parse_dt(value: str, label: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise BulkError(f"{label} is not a valid date/time — use ISO format like 2026-01-01T00:00:00Z.") from None
    if dt.tzinfo is None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _check_range(start: str, end: str) -> None:
    s = _parse_dt(start, "start date/time")
    e = _parse_dt(end, "end date/time")
    if e <= s:
        raise BulkError("The 'to' date must be after the 'from' date.")
    if e - s > timedelta(days=31):
        raise BulkError("That range is more than 31 days — Marketo caps bulk extract ranges at 31 days. Split it into smaller windows.")


def _norm_state(mk_status: str | None) -> str:
    if not mk_status:
        return "created"
    return _STATE_MAP.get(str(mk_status).strip().lower(), "processing")


# --- Marketo call helper -------------------------------------------------------

def _mkt(profile: Profile, method: str, path: str, *, params=None, json=None, files=None) -> httpx.Response:
    """One Marketo call with the shared rate-limit gate + friendly errors."""
    executor.gate()
    try:
        resp = auth.request(profile, method, path, params=params, json=json, files=files)
    except auth.AuthError as exc:
        raise BulkError(str(exc)) from exc
    if 200 <= resp.status_code < 300:
        return resp
    raise BulkError(executor.translate_error(resp), status=resp.status_code)


def _result(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise BulkError("Marketo returned a non-JSON response: " + (resp.text or "")[:200]) from None
    r = data.get("result") if isinstance(data, dict) else None
    if isinstance(r, list) and r and isinstance(r[0], dict):
        return r[0]
    if isinstance(r, dict):
        return r
    if isinstance(data, dict):
        return data
    return {}


def _fill(path: str, marketo_job_id: str, detail: dict) -> str:
    out = path.replace("{jobId}", str(marketo_job_id))
    for key in ("programId", "apiName"):
        if "{" + key + "}" in out and (detail.get("pathParams") or {}).get(key):
            out = out.replace("{" + key + "}", str(detail["pathParams"][key]))
    return out


# --- job rows (DB) -------------------------------------------------------------

def _row_to_job(row) -> dict:
    try:
        detail = json.loads(row["detail"] or "{}")
    except json.JSONDecodeError:
        detail = {}
    last = detail.get("lastStatus") or {}
    kind = detail.get("kind") or "extract"
    job = {
        "id": row["id"],
        "opId": row["kind"],
        "opName": detail.get("opName") or row["kind"],
        "kind": kind,
        "state": row["state"],
        "marketoJobId": row["marketo_job_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "format": detail.get("format"),
        "fileName": detail.get("fileName"),
        "fileSize": detail.get("fileSize"),
        "lastStatus": last or None,
    }
    job["counts"] = _counts(kind, last)
    job["message"] = last.get("message") or detail.get("message")
    job["canDownload"] = kind == "extract" and row["state"] == "completed"
    job["canCancel"] = kind == "extract" and row["state"] not in TERMINAL_STATES
    job["canFailures"] = kind == "import" and row["state"] == "completed"
    job["canWarnings"] = job["canFailures"]
    return job


def _counts(kind: str, last: dict) -> dict | None:
    if kind == "extract":
        out = {}
        if last.get("numberOfRecords") is not None:
            out["records"] = last["numberOfRecords"]
        if last.get("fileSize") is not None:
            out["fileSize"] = last["fileSize"]
        return out or None
    out = {}
    for src, dst in (
        ("numOfLeadsProcessed", "processed"),
        ("numOfRowsFailed", "failed"),
        ("numOfRowsWithWarning", "warnings"),
    ):
        if last.get(src) is not None:
            out[dst] = last[src]
    return out or None


def get_job(job_id: int) -> dict | None:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = _row_to_job(row)
    job["profile_id"] = row["profile_id"]
    return job


def list_jobs(profile_id: int, limit: int = 50) -> list[dict]:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE profile_id = ? ORDER BY id DESC LIMIT ?", (profile_id, limit)
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def _insert_job(profile_id: int, op: dict, marketo_job_id: str, state: str, detail: dict) -> dict:
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO jobs (profile_id, kind, marketo_job_id, state, detail) VALUES (?, ?, ?, ?, ?)",
            (profile_id, op["id"], marketo_job_id, state, json.dumps(detail)),
        )
        conn.commit()
        job_id = cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    return get_job(job_id)


def _update_job(job_id: int, state: str | None = None, detail: dict | None = None, message: str | None = None) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise BulkError("Job not found.")
    new_state = state if state is not None else row["state"]
    if detail is None and message is not None:
        try:
            detail = json.loads(row["detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        detail["message"] = message
    new_detail = json.dumps(detail) if detail is not None else row["detail"]
    try:
        conn.execute(
            "UPDATE jobs SET state = ?, detail = ?, updated_at = datetime('now') WHERE id = ?",
            (new_state, new_detail, job_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_job(job_id)


# --- form validation + request building -----------------------------------------

def _validate_form(op: dict, form: dict, file: tuple[bytes, str] | None) -> None:
    if op["kind"] == "import":
        if file is None:
            raise BulkError("Choose a data file first.")
        if len(file[0]) > MAX_IMPORT_BYTES:
            raise BulkError(
                f"That file is {len(file[0]) / 1048576:.1f} MB — Marketo's bulk import limit is 10 MB. Split it into smaller files."
            )
    for f in op["form"]:
        if not f.get("required"):
            continue
        if f["type"] == "file":
            continue
        v = form.get(f["name"])
        if v is None or (isinstance(v, str) and not v.strip()):
            raise BulkError(f"Missing required field: {f['label']}.")
    # Dynamic sub-fields (e.g. the lead extract filter).
    for f in op["form"]:
        dyn = f.get("dynamicFields")
        if not dyn:
            continue
        sel = str(form.get(f["name"]) or "").strip()
        for sf in dyn.get(sel) or []:
            if not sf.get("required"):
                continue
            v = form.get(sf["name"])
            if v is None or (isinstance(v, str) and not v.strip()):
                raise BulkError(f"Missing required field: {sf['label']} (for “{sel}”).")


def _lead_extract_body(form: dict) -> dict:
    fields = _as_str_list(form.get("fields"))
    if not fields:
        raise BulkError("Pick at least one field to export.")
    body: dict = {"fields": fields}
    fmt = str(form.get("format") or "").strip().upper()
    if fmt:
        if fmt not in ("CSV", "TSV", "SSV"):
            raise BulkError("File format must be CSV, TSV, or SSV.")
        body["format"] = fmt
    chn = _maybe_json(form.get("columnHeaderNames"))
    if chn:
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in chn.items()):
            raise BulkError("Column header names must map field API names to text headers, e.g. {\"email\": \"Email Address\"}.")
        body["columnHeaderNames"] = chn
    body["filter"] = _lead_filter(form)
    return body


def _lead_filter(form: dict) -> dict:
    ftype = str(form.get("filterType") or "").strip()
    if ftype in ("createdAt", "updatedAt"):
        start = _req_str(form.get("startAt"), "start date/time")
        end = _req_str(form.get("endAt"), "end date/time")
        _check_range(start, end)
        return {ftype: {"startAt": start, "endAt": end}}
    if ftype == "staticListId":
        return {"staticListId": _as_int(form.get("listId"), "Static list ID")}
    if ftype == "staticListName":
        return {"staticListName": _req_str(form.get("listName"), "static list name")}
    if ftype == "smartListId":
        return {"smartListId": _as_int(form.get("listId"), "Smart list ID")}
    if ftype == "smartListName":
        return {"smartListName": _req_str(form.get("listName"), "smart list name")}
    raise BulkError("Choose which leads to export (a list or a date range) first.")


def _activity_extract_body(form: dict) -> dict:
    start = _req_str(form.get("startAt"), "start date/time")
    end = _req_str(form.get("endAt"), "end date/time")
    _check_range(start, end)
    f: dict = {"createdAt": {"startAt": start, "endAt": end}}
    atids = _as_int_list(form.get("activityTypeIds"), "Activity types")
    if atids:
        f["activityTypeIds"] = atids
    pids = _as_int_list(form.get("primaryAttributeValueIds"), "primary attribute IDs")
    pvals = _as_str_list(form.get("primaryAttributeValues"))
    if pids and pvals:
        raise BulkError("Use either primary-attribute IDs or names — not both.")
    if (pids or pvals) and not atids:
        raise BulkError("Filtering by a specific asset requires activity types to be selected first.")
    if pids:
        if len(pids) > 50:
            raise BulkError("At most 50 primary attribute IDs per job.")
        f["primaryAttributeValueIds"] = pids
    if pvals:
        if len(pvals) > 50:
            raise BulkError("At most 50 primary attribute names per job.")
        f["primaryAttributeValues"] = pvals
    body: dict = {"filter": f}
    fmt = str(form.get("format") or "").strip().upper()
    if fmt:
        if fmt not in ("CSV", "TSV", "SSV"):
            raise BulkError("File format must be CSV, TSV, or SSV.")
        body["format"] = fmt
    fields = _as_str_list(form.get("fields"))
    if fields:
        body["fields"] = fields
    chn = _maybe_json(form.get("columnHeaderNames"))
    if chn:
        body["columnHeaderNames"] = chn
    return body


def _file_part(form: dict, file: tuple[bytes, str]) -> dict:
    name = file[1] or "upload"
    low = name.lower()
    ctype = "application/octet-stream"
    if low.endswith(".csv"):
        ctype = "text/csv"
    elif low.endswith(".tsv"):
        ctype = "text/tab-separated-values"
    elif low.endswith((".ssv", ".txt")):
        ctype = "text/plain"
    return {"file": (name, file[0], ctype)}


def _build_create_request(op: dict, form: dict, file: tuple[bytes, str] | None):
    """Return (path, query, json_body, files) for the create call."""
    if op["kind"] == "extract":
        if op["id"] == "bulk.extract_leads":
            return op["create"]["path"], None, _lead_extract_body(form), None
        if op["id"] == "bulk.extract_activities":
            return op["create"]["path"], None, _activity_extract_body(form), None
        raise BulkError(f"No create builder for '{op['id']}'.")
    # Imports: path params + query params + one multipart file part.
    path = op["create"]["path"]
    if "{programId}" in path:
        path = path.replace("{programId}", str(_as_int(form.get("programId"), "Program ID")))
    if "{apiName}" in path:
        path = path.replace("{apiName}", _req_str(form.get("apiName"), "Custom object API name"))
    fmt = str(form.get("format") or "csv").strip().lower()
    if fmt not in ("csv", "tsv", "ssv"):
        raise BulkError("File format must be csv, tsv, or ssv.")
    query: list[tuple[str, str]] = [("format", fmt)]
    if op["id"] == "bulk.import_leads":
        if form.get("lookupField"):
            query.append(("lookupField", str(form["lookupField"]).strip()))
        if form.get("listId"):
            query.append(("listId", str(_as_int(form.get("listId"), "Static list ID"))))
        if form.get("partitionName"):
            query.append(("partitionName", str(form["partitionName"]).strip()))
    elif op["id"] == "bulk.import_program_members":
        query.append(("programMemberStatus", _req_str(form.get("programMemberStatus"), "Program status")))
    return path, query, None, _file_part(form, file)


# --- job lifecycle ---------------------------------------------------------------

def start_job(profile: Profile, op: dict, form: dict, file: tuple[bytes, str] | None = None) -> dict:
    """Create (and, for extracts, enqueue) a Marketo bulk job; store a job row."""
    form = form or {}
    _validate_form(op, form, file)
    path, query, body, files = _build_create_request(op, form, file)
    resp = _mkt(profile, op["create"]["method"], path, params=query, json=body, files=files)
    data = _result(resp)
    job_id = data.get(op.get("resultId") or "batchId") or data.get("batchId") or data.get("exportId") or data.get("importId")
    if not job_id:
        raise BulkError("Marketo accepted the job but didn't return a job ID: " + json.dumps(data)[:200])
    detail = {
        "opId": op["id"],
        "opName": op["name"],
        "kind": op["kind"],
        "format": str(form.get("format") or "").strip().upper() or None,
        "fileName": file[1] if file else None,
        "fileSize": len(file[0]) if file else None,
        "pathParams": {
            "programId": form.get("programId"),
            "apiName": form.get("apiName"),
        },
        "lastStatus": data,
        "params": form,
    }
    job = _insert_job(profile.id, op, str(job_id), _norm_state(data.get("status")), detail)
    if op["kind"] == "extract":
        # Created jobs aren't in the queue yet — enqueue them immediately.
        enq = op["enqueue"]
        resp2 = _mkt(profile, enq["method"], _fill(enq["path"], str(job_id), detail))
        data2 = _result(resp2)
        detail["lastStatus"] = data2
        job = _update_job(job["id"], state=_norm_state(data2.get("status")), detail=detail)
    return job


def refresh_job(profile: Profile, job_id: int) -> dict:
    """Poll Marketo for the job's current status (no-op when already terminal)."""
    job = get_job(job_id)
    if job is None:
        raise BulkError("Job not found.")
    if job["state"] in TERMINAL_STATES:
        return job
    op = get_op(job["opId"])
    if op is None:
        raise BulkError(f"Unknown bulk operation '{job['opId']}' — the catalog changed after this job was started.")
    try:
        resp = _mkt(profile, op["status"]["method"], _fill(op["status"]["path"], job["marketoJobId"], _detail(job_id)))
    except BulkError as exc:
        if exc.status == 404:
            return _update_job(job_id, state="expired", message="The job ID has expired — Marketo keeps results for 7 days (imports) or 10 days (extracts). Start a new job.")
        raise
    data = _result(resp)
    state = _norm_state(data.get("status"))
    detail = _detail(job_id)
    detail["lastStatus"] = data
    return _update_job(job_id, state=state, detail=detail)


def cancel_job(profile: Profile, job_id: int) -> dict:
    job = get_job(job_id)
    if job is None:
        raise BulkError("Job not found.")
    if job["kind"] != "extract":
        raise BulkError("Only extract jobs can be canceled — imports run to completion.")
    if job["state"] in TERMINAL_STATES:
        raise BulkError("This job has already finished.")
    op = get_op(job["opId"])
    if op is None or "cancel" not in op:
        raise BulkError("This job type doesn't support cancellation.")
    resp = _mkt(profile, op["cancel"]["method"], _fill(op["cancel"]["path"], job["marketoJobId"], _detail(job_id)))
    data = _result(resp)
    detail = _detail(job_id)
    detail["lastStatus"] = data
    return _update_job(job_id, state=_norm_state(data.get("status") or "Canceled"), detail=detail)


def download_file(profile: Profile, job_id: int) -> tuple[bytes, str, str]:
    """Fetch the completed extract file. Returns (bytes, mime, filename)."""
    job = get_job(job_id)
    if job is None:
        raise BulkError("Job not found.")
    if job["kind"] != "extract":
        raise BulkError("Import jobs don't produce a data file — use failures/warnings instead.")
    if job["state"] != "completed":
        raise BulkError("The job isn't finished yet — the file appears once the status is Completed.")
    op = get_op(job["opId"])
    if op is None or "file" not in op:
        raise BulkError("This job type has no file endpoint.")
    resp = _mkt(profile, op["file"]["method"], _fill(op["file"]["path"], job["marketoJobId"], _detail(job_id)))
    slug = op["id"].split(".", 1)[1].replace("_", "-")
    ext = (job["format"] or "csv").lower()
    if ext not in ("csv", "tsv", "ssv"):
        ext = "csv"
    return resp.content, _mime(ext), f"marketo-{slug}-{job['marketoJobId']}.{ext}"


def download_report(profile: Profile, job_id: int, kind: str) -> tuple[bytes, str, str]:
    """Fetch a job's failures/warnings file. Returns (bytes, mime, filename)."""
    if kind not in ("failures", "warnings"):
        raise BulkError("Unknown report type.")
    job = get_job(job_id)
    if job is None:
        raise BulkError("Job not found.")
    if job["kind"] != "import":
        raise BulkError("Extract jobs don't have " + kind + " — download the data file instead.")
    if job["state"] != "completed":
        raise BulkError("Wait for the job to finish before downloading " + kind + ".")
    op = get_op(job["opId"])
    if op is None or kind not in op:
        raise BulkError("This job type has no " + kind + " endpoint.")
    try:
        resp = _mkt(profile, op[kind]["method"], _fill(op[kind]["path"], job["marketoJobId"], _detail(job_id)))
    except BulkError as exc:
        if exc.status == 404:
            if kind == "failures":
                raise BulkError("No failures file — that usually means every row imported successfully.") from exc
            raise BulkError("No warnings file — that usually means no rows had warnings.") from exc
        raise
    slug = op["id"].split(".", 1)[1].replace("_", "-")
    return resp.content, "text/csv", f"marketo-{slug}-{kind}-{job['marketoJobId']}.csv"


def _detail(job_id: int) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT detail FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise BulkError("Job not found.")
    try:
        return json.loads(row["detail"] or "{}")
    except json.JSONDecodeError:
        return {}


def _mime(ext: str) -> str:
    return {
        "csv": "text/csv",
        "tsv": "text/tab-separated-values",
        "ssv": "text/plain",
    }.get(ext, "application/octet-stream")
