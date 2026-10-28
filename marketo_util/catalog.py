"""Loads the endpoint catalog (one JSON file per Marketo call) from ./catalog.

The catalog is the single source of truth for the UI: adding a call means
adding one file, no code changes. Entries are validated at load time with
clear error messages (see catalog/schema.json for the full spec).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

CALL_ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_]+)+$")
PARAM_TYPES = {"string", "integer", "boolean", "csv", "datetime", "json", "file", "lookup", "select"}
BODY_MODES = {"freeform", "record", "records", "multipart", "sync", "values"}
METHODS = {"GET", "POST", "DELETE"}

# Sidebar order; groups not listed here are appended alphabetically at the end.
GROUP_ORDER = [
    "Leads",
    "Activities",
    "Static Lists",
    "Programs",
    "Opportunities",
    "Sales Persons",
    "Custom Objects",
    "Landing Pages",
    "Emails",
    "Forms",
    "Lists & Campaigns",
    "Other Assets",
    "Bulk Extract",
    "Bulk Import",
]


class CatalogError(RuntimeError):
    pass


@dataclass
class CallDef:
    raw: dict
    id: str
    name: str
    group: str

    @property
    def params(self) -> dict:
        return self.raw.get("params") or {"path": [], "query": [], "body": None}


def catalog_dir() -> Path:
    env = os.environ.get("MARKETO_UTIL_CATALOG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "catalog"


def _validate(entry: dict, path: Path) -> None:
    problems: list[str] = []

    def need(key: str, typ: type) -> None:
        if key not in entry:
            problems.append(f"missing '{key}'")
        elif not isinstance(entry[key], typ):
            problems.append(f"'{key}' must be a {typ.__name__}")

    need("id", str)
    need("name", str)
    need("group", str)
    need("summary", str)
    need("method", str)
    need("path", str)
    if "id" in entry and not CALL_ID_RE.match(str(entry["id"])):
        problems.append(f"bad id '{entry['id']}' (use lowercase dot.separated, e.g. leads.get_by_id)")
    if entry.get("method") not in METHODS:
        problems.append(f"method must be one of {sorted(METHODS)}")
    if not str(entry.get("path", "")).startswith("/"):
        problems.append("path must start with '/'")

    params = entry.get("params") or {}

    def check_param(p: dict, where: str) -> None:
        for key in ("name", "label", "type"):
            if key not in p:
                problems.append(f"{where} missing '{key}'")
        if p.get("type") not in PARAM_TYPES:
            problems.append(f"{where} type must be one of {sorted(PARAM_TYPES)}")
        if p.get("type") == "select":
            opts = p.get("options")
            if not isinstance(opts, list) or not opts or not all(
                isinstance(o, dict) and o.get("value") is not None and "label" in o for o in opts
            ):
                problems.append(f"{where} select param needs a non-empty 'options' list of {{value, label}}")

    for section in ("path", "query"):
        for i, p in enumerate(params.get(section) or []):
            where = f"params.{section}[{i}]"
            if not isinstance(p, dict):
                problems.append(f"{where} must be an object")
                continue
            check_param(p, where)
            if "repeat" in p and not isinstance(p.get("repeat"), bool):
                problems.append(f"{where} 'repeat' must be a boolean")
    body = params.get("body")
    if body is not None:
        if not isinstance(body, dict) or body.get("mode") not in BODY_MODES:
            problems.append("params.body.mode must be one of " + ", ".join(sorted(BODY_MODES)))
        else:
            mode = body.get("mode")
            if mode in ("record", "multipart"):
                if "encoding" in body and body.get("encoding") not in ("json", "form"):
                    problems.append("params.body.encoding must be 'json' or 'form'")
                for i, f in enumerate(body.get("fields") or body.get("parts") or []):
                    where = f"params.body.{'fields' if mode == 'record' else 'parts'}[{i}]"
                    if not isinstance(f, dict):
                        problems.append(f"{where} must be an object")
                        continue
                    check_param(f, where)
            elif mode == "values":
                for k in ("key", "itemKey", "wrapKey"):
                    if k in body and not isinstance(body.get(k), str):
                        problems.append(f"params.body.{k} must be a string")
                if body.get("type") not in (None, "string", "integer"):
                    problems.append("params.body.type must be 'string' or 'integer'")
                for i, f in enumerate(body.get("fields") or []):
                    where = f"params.body.fields[{i}]"
                    if not isinstance(f, dict):
                        problems.append(f"{where} must be an object")
                        continue
                    check_param(f, where)
            elif mode == "sync":
                if "wrapper" in body and not isinstance(body.get("wrapper"), str):
                    problems.append("params.body.wrapper must be a string")
                if body.get("fieldPicker") not in (None, "describe"):
                    problems.append("params.body.fieldPicker must be 'describe' or omitted")
                req = body.get("requiredFields")
                if req is not None and (not isinstance(req, list) or not all(isinstance(x, str) for x in req)):
                    problems.append("params.body.requiredFields must be a list of field names")
                for i, f in enumerate(body.get("fixed") or []):
                    where = f"params.body.fixed[{i}]"
                    if not isinstance(f, dict):
                        problems.append(f"{where} must be an object")
                        continue
                    check_param(f, where)

    if problems:
        raise CatalogError(f"{path.name}: " + "; ".join(problems))


def load_catalog() -> list[CallDef]:
    root = catalog_dir()
    if not root.is_dir():
        raise CatalogError(f"Catalog directory not found: {root}")
    calls: list[CallDef] = []
    seen: set[str] = set()
    for path in sorted(root.glob("*.json")):
        # Non-call reference files that live in the catalog dir alongside the
        # one-file-per-call entries.
        if path.name in ("schema.json", "error_codes.json"):
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CatalogError(f"{path.name}: invalid JSON — {exc}") from exc
        _validate(entry, path)
        if entry["id"] in seen:
            raise CatalogError(f"{path.name}: duplicate call id '{entry['id']}'")
        seen.add(entry["id"])
        calls.append(CallDef(raw=entry, id=entry["id"], name=entry["name"], group=entry["group"]))
    calls.sort(
        key=lambda c: (
            GROUP_ORDER.index(c.group) if c.group in GROUP_ORDER else len(GROUP_ORDER),
            c.group,
            c.name.lower(),
        )
    )
    return calls


def group_catalog(calls: list[CallDef]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for c in calls:
        groups.setdefault(c.group, []).append(c.raw)
    ordered = [g for g in GROUP_ORDER if g in groups] + sorted(
        g for g in groups if g not in GROUP_ORDER
    )
    return [{"group": g, "calls": groups[g]} for g in ordered]


def get_call(call_id: str, calls: list[CallDef]) -> CallDef | None:
    for c in calls:
        if c.id == call_id:
            return c
    return None
