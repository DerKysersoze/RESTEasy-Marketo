"""Call logger (P8): appends each Marketo call + its response to a daily JSONL.

Only writes when the "Log every call" setting is ON. A log-write failure must
NEVER break the actual API call — every write is wrapped and swallowed.

Files are daily-rotated (user decision 2026-10-28):
    <export_dir>/marketo-calls-YYYY-MM-DD.jsonl
— one file per day, NOT one ever-growing file and NOT one file per call.

Also hosts the (separately-toggled) bulk-file save helper.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import settings

_lock = threading.Lock()


def _log_path() -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return settings.export_dir() / f"marketo-calls-{day}.jsonl"


def maybe_log(profile_name: str, call_id: str, params: dict, result: dict) -> None:
    """Append one JSON line for a call if logging is enabled. Never raises."""
    if not settings.log_enabled():
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "profile": profile_name,
        "call_id": call_id,
        "params": params,
        "status": result.get("status"),
        "ok": result.get("ok"),
        "latency_ms": result.get("latency_ms"),
    }
    if result.get("ok"):
        entry["data"] = result.get("data")
    else:
        entry["error"] = result.get("error")
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass  # a logging failure must never break the call


def bulk_filename(call_id: str, marketo_job_id: str, ext: str = "csv") -> str:
    slug = call_id.split(".", 1)[-1].replace("_", "-")
    return f"marketo-{slug}-{marketo_job_id or 'job'}.{ext}"


def save_bulk_file(data: bytes, filename: str) -> Path | None:
    """If "save bulk files here" is ON, write a bulk file to the export dir.

    Best-effort: never raises, returns the written path or None.
    """
    if not settings.save_bulk_enabled():
        return None
    try:
        d = settings.export_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / filename
        p.write_bytes(data)
        return p
    except Exception:
        return None
