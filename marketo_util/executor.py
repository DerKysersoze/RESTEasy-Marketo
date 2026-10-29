"""Builds and executes Marketo calls defined by catalog entries.

Contains the rate-limit gate (the scale seam for shared-server deployments —
per-instance quotas are shared across users later, enforced here centrally)
and translation of Marketo's numeric error codes into plain English.
"""
from __future__ import annotations

import json
import re
import threading
import time

from . import auth, errors
from .catalog import CallDef
from .config import Profile


class ValidationError(ValueError):
    pass


# --- Rate-limit gate ---------------------------------------------------------
# Marketo allows ~100 calls / 20 seconds (error 606). We stay far below that:
_MIN_INTERVAL_SEC = 0.5
_gate_lock = threading.Lock()
_last_request_at = 0.0


def _gate() -> None:
    global _last_request_at
    with _gate_lock:
        wait = _MIN_INTERVAL_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    with _gate_lock:
        _last_request_at = time.monotonic()


def gate() -> None:
    """Public rate-limit gate (used by executor.run_call and the bulk engine)."""
    _gate()


# --- Error translation -------------------------------------------------------
# The official code→description table lives in catalog/error_codes.json (loaded
# by errors.py, P8). It was verified from the experienceleague error-codes page
# and covers HTTP-level, response-level (600s/700s) and record-level (1000s)
# codes. Unknown codes fall back to the HTTP status + Marketo's raw message.


def _extract_error(body_text: str) -> tuple[str, str]:
    try:
        data = json.loads(body_text)
    except (ValueError, TypeError):
        return "", (body_text or "")[:300]
    if isinstance(data, dict):
        # Marketo's response-level error shape (e.g. 601/602 auth errors):
        # {"success": false, "errors": [{"code": "601", "message": "..."}]}
        errs = data.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict):
            e = errs[0]
            code = str(e.get("code") or "")
            msg = str(e.get("message") or "")
        else:
            err = data.get("error") if isinstance(data.get("error"), dict) else data
            code = str(err.get("code") or err.get("error_code") or "")
            msg = str(err.get("message") or err.get("error_description") or "")
    return code, msg


def _friendly(status: int, code: str, msg: str) -> str:
    return errors.describe(code, status, msg)


def translate_error(resp) -> str:
    """Public helper: turn a non-2xx Marketo response into a friendly message."""
    code, msg = _extract_error(resp.text)
    return _friendly(resp.status_code, code, msg)


def _interpret(resp, latency_ms: int) -> dict:
    status = resp.status_code
    if 200 <= status < 300:
        try:
            data = resp.json()
        except ValueError:
            data = resp.text
        return {"ok": True, "status": status, "latency_ms": latency_ms, "data": data}
    code, msg = _extract_error(resp.text)
    return {
        "ok": False,
        "status": status,
        "latency_ms": latency_ms,
        "error": {
            "friendly": _friendly(status, code, msg),
            "code": code,
            "doc": errors.doc_url() if code else None,
            "raw": (resp.text or "")[:4000],
        },
    }


def _form_value(v) -> str:
    """Serialize one body member for application/x-www-form-urlencoded.

    Marketo asset write endpoints (clone, …) expect form-urlencoded bodies,
    where nested JSON members (e.g. folder) are passed as JSON strings.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


# --- Request building --------------------------------------------------------
def _validate_params(call: CallDef, params: dict) -> None:
    spec = call.params
    problems = []
    for section in ("path", "query"):
        for p in spec.get(section) or []:
            if not p.get("required"):
                continue
            val = (params.get(section) or {}).get(p["name"])
            if val is None or (isinstance(val, str) and not val.strip()):
                problems.append(f"missing required field '{p.get('label') or p['name']}'")
    body_spec = spec.get("body")
    if body_spec and body_spec.get("mode") in ("record", "multipart", "values", "sync"):
        bparams = params.get("body") or {}
        if body_spec.get("mode") == "sync":
            fixed_fields = body_spec.get("fixed") or []
            bparams = bparams.get("fixed") or {}
        elif body_spec.get("mode") == "values":
            fixed_fields = body_spec.get("fields") or []
        else:
            fixed_fields = body_spec.get("fields") or body_spec.get("parts") or []
        for f in fixed_fields:
            if not f.get("required"):
                continue
            val = bparams.get(f["name"])
            if val is None or (isinstance(val, str) and not val.strip()):
                problems.append(f"missing required field '{f.get('label') or f['name']}'")
    if problems:
        raise ValidationError("Fix the form first: " + "; ".join(problems) + ".")


def _build(call: CallDef, params: dict):
    """Return (path, query, json_body, files, data) ready for auth.request()."""
    spec = call.params
    path = call.raw["path"]

    for p in spec.get("path") or []:
        value = (params.get("path") or {}).get(p["name"])
        if value is None or (isinstance(value, str) and not str(value).strip()):
            if p.get("required"):
                continue  # reported by _validate_params
            continue
        path = path.replace("{" + p["name"] + "}", str(value))
    leftover = re.findall(r"\{(\w+)\}", path)
    if leftover:
        raise ValidationError("Missing value(s) for: " + ", ".join(leftover))

    query: list[tuple[str, str]] = []
    for p in spec.get("query") or []:
        value = (params.get("query") or {}).get(p["name"])
        if value is None or (isinstance(value, str) and not str(value).strip()):
            continue
        if p.get("type") == "csv" and isinstance(value, list):
            value = ",".join(str(v) for v in value if str(v).strip())
        if p.get("repeat"):
            # Some endpoints (e.g. Add/Remove from List) take one repeated
            # `id=…` query parameter per value: id=1&id=2, not id=1,2.
            parts = [v.strip() for v in str(value).split(",") if v.strip()]
            for part in parts:
                query.append((p["name"], part))
        else:
            query.append((p["name"], str(value)))

    json_body = None
    files: dict | None = None
    data: dict | None = None
    body_spec = spec.get("body")
    if body_spec:
        body = params.get("body") or {}
        mode = body_spec.get("mode")
        if mode == "freeform":
            raw = body.get("json")
            if isinstance(raw, str) and raw.strip():
                try:
                    json_body = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValidationError("The request body is not valid JSON: " + str(exc)) from exc
        elif mode == "record":
            members = {
                f["name"]: body.get(f["name"])
                for f in body_spec.get("fields") or []
                if body.get(f["name"]) is not None
            }
            if body_spec.get("encoding") == "form":
                # Marketo asset write endpoints (clone, …) require
                # application/x-www-form-urlencoded, not JSON.
                data = {k: _form_value(v) for k, v in members.items()}
            else:
                json_body = members
        elif mode == "values":
            raw = body.get("values")
            if isinstance(raw, str):
                values = [v.strip() for v in raw.split(",") if v.strip()]
            elif isinstance(raw, list):
                values = [str(v).strip() for v in raw if str(v).strip()]
            else:
                values = []
            if not values:
                raise ValidationError("Enter at least one value (comma-separated) before running.")
            max_per_call = body_spec.get("maxPerCall")
            if max_per_call and len(values) > max_per_call:
                raise ValidationError(
                    f"That's {len(values)} values — Marketo allows at most {max_per_call} per call. Split into several runs."
                )
            typ = body_spec.get("type") or "string"
            item_key = body_spec.get("itemKey")

            def coerce(v: str):
                if typ == "integer":
                    try:
                        return int(v)
                    except ValueError:
                        raise ValidationError(
                            f"'{v}' is not a whole number — enter numeric IDs separated by commas."
                        ) from None
                return v

            if item_key:
                arr = [{item_key: coerce(v)} for v in values]
            else:
                arr = [coerce(v) for v in values]
            json_body = {
                f["name"]: body.get(f["name"])
                for f in body_spec.get("fields") or []
                if body.get(f["name"]) is not None
            }
            key = body_spec.get("key") or "input"
            wrap_key = body_spec.get("wrapKey")
            # Some endpoints nest the array one level deeper, e.g. the
            # trigger-campaign body {"input": {"leads": [{"id": …}]}}.
            json_body[key] = {wrap_key: arr} if wrap_key else arr
        elif mode == "sync":
            rows = body.get("rows")
            if not isinstance(rows, list) or not rows:
                raise ValidationError("Add at least one record to the table before running.")
            max_per_call = body_spec.get("maxPerCall")
            if max_per_call and len(rows) > max_per_call:
                raise ValidationError(
                    f"That's {len(rows)} records — Marketo allows at most {max_per_call} per call. Split into several runs."
                )
            for i, r in enumerate(rows, 1):
                if not isinstance(r, dict) or not any(v not in (None, "") for v in r.values()):
                    raise ValidationError(f"Record {i} is empty — fill in at least one value or remove that row.")
            # The UI nests fixed members under body.fixed; accept both shapes.
            fixed_src = body.get("fixed") if isinstance(body.get("fixed"), dict) else body
            json_body = {
                f["name"]: fixed_src.get(f["name"])
                for f in body_spec.get("fixed") or []
                if fixed_src.get(f["name"]) is not None
            }
            json_body[body_spec.get("wrapper") or "input"] = rows
        elif mode == "records":
            rows = body.get("rows") or []
            wrapper = body_spec.get("wrapper")
            json_body = {wrapper: rows} if wrapper else rows
        elif mode == "multipart":
            files = {}
            data = {}
            for f in body_spec.get("parts") or []:
                v = body.get(f["name"])
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                if f.get("type") == "file" and isinstance(v, dict) and v.get("content") is not None:
                    files[f["name"]] = (
                        v.get("filename") or "upload",
                        v["content"],
                        v.get("content_type") or "application/octet-stream",
                    )
                else:
                    data[f["name"]] = str(v)
    return path, query or None, json_body, files, data


def run_call(profile: Profile, call: CallDef, params: dict) -> dict:
    params = params or {}
    _validate_params(call, params)
    path, query, json_body, files, data = _build(call, params)

    _gate()
    t0 = time.perf_counter()
    try:
        resp = auth.request(
            profile,
            call.raw["method"],
            path,
            params=query,
            json=json_body,
            files=files,
            data=data,
        )
    except auth.AuthError as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": False,
            "error": {"friendly": str(exc), "raw": exc.body},
            "latency_ms": latency_ms,
        }
    return _interpret(resp, int((time.perf_counter() - t0) * 1000))
