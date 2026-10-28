# Marketo REST Utility — Project Plan

> **Compaction protocol:** At the start of every session, read `NOTES.md` first (facts, gotchas,
> progress log), then this file (roadmap, architecture, schema). Update both as work happens.
> This project is explicitly expected to span many compactions; notes are the source of truth.

## 1. Purpose

A local utility that makes the Marketo Engage REST API usable by **non-technical** people:

- A browsable **catalog of API calls** grouped by function, each with plain-English
  "what it does / when to use" copy.
- Clicking a call renders a **form**: required fields up top, optional fields in a
  collapsible section, each field labeled with a hint and example taken from the docs.
- One click **runs the call**, pretty-prints the response, and offers **CSV/JSON export**.
- Handles the tedious parts automatically: token acquisition/refresh, path templates,
  bulk job lifecycle (create → enqueue → poll → download), error translation.

**Non-goals (v1):**
- ❌ Marketo **reporting** — reports are NOT exposed by the REST API (only SOAP/CSV-from-UI).
  Note this in-app so users know why "my report" isn't a call they can make.
- ❌ SOAP API, webhooks, Flow Triggers API, Marketo Engage bulk *SOAP* import.
- ❌ Multi-tenant/multi-instance switching (config supports one instance at a time;
  a second config can be added later).

## 2. Key API facts (verified from docs, Oct 2026)

| Fact | Value |
|---|---|
| Auth | `POST https://<instance>.mktorest.com/identity/oauth/token`, grant_type=`client_credentials`, client_id + client_secret. Token lives ~20 min — must refresh before expiry. |
| REST base | `https://<instance>.mktorest.com/rest/v1/...` (asset calls: `/rest/asset/v1/...`) |
| Bulk base | `https://<instance>.mktorest.com/bulk/v1/...` (no `/rest/` prefix!) |
| Daily quota | 50,000 REST calls/day, resets 12:00 AM CST → error **607** |
| Rate limit | 100 calls / 20 seconds → error **606** |
| Concurrency | concurrent in-flight limit → error **615** |
| URI length | 8 KB max (error 414); workaround = POST with `_method=GET` |
| Body size | 1 MB standard, 10 MB bulk |
| Batch sizes | 300 records (lead API), 200 (asset API); >1000 lead filter match → error **1003** |
| Bulk export | 500 MB/day, files retained 10 days |
| 2026 change | `listId` on Get Lead Activities/Changes fails (1003) if list ≥ 10,000 leads (since 2026-09-30) |
| Reporting | **Not available via REST API** (user-confirmed + docs) |

## 3. Architecture

```
┌───────────────────────────── browser ─────────────────────────────┐
│  index.html + app.js (vanilla JS, data-driven UI, no build step)  │
│  · catalog sidebar (grouped calls)   · dynamic form renderer      │
│  · response viewer (pretty JSON)     · CSV/JSON export            │
└───────────────▲──────────────────────────────────▲────────────────┘
                │ /api/catalog                     │ /api/call/{id}
                │ /api/config                      │ /api/bulk/*
┌───────────────┴──────────────────────────────────┴────────────────┐
│  Python 3.11 + FastAPI (uvicorn) — local-only server             │
│  · Catalog loader (catalog/*.json) — single source of truth      │
│  · Auth manager: token cache, auto-refresh, expiry tracking      │
│  · Call builder: path template + query/body assembly + validation│
│  · Executor: httpx client, 606/607/615/414/1003 error translation│
│  · Bulk job service: create→enqueue→poll→file URL, job history   │
│  · Config store: instance + client_id + client_secret (local JSON)│
└───────────────────────────────────────────────────────────────────┘
```

**Why a backend at all:** Marketo does not permit browser CORS calls with client
credentials; the backend holds the secret, refreshes tokens, and normalizes errors.

### 3.1 The catalog is the product

Every UI element is generated from a catalog entry. Adding an API call = adding one JSON
file; no code changes. Schema (see `catalog/schema.json` once scaffolded):

```jsonc
{
  "id": "lead.get.byId",              // stable id used by /api/call/{id}
  "name": "Get Lead by ID",           // sidebar label
  "group": "Leads",                   // sidebar section
  "summary": "Fetch one lead…",       // 1-line plain English
  "whenToUse": "When you already know…",
  "method": "GET",
  "path": "/rest/v1/lead/{id}.json",  // {placeholders} filled from params
  "docUrl": "https://experienceleague.adobe.com/…",
  "permission": "Read-Only Lead",
  "params": {
    "path":  [{ "name":"id", "label":"Lead ID", "required":true,
                "type":"integer", "hint":"The numeric Marketo ID", "example":"123456" }],
    "query": [{ "name":"leadFieldIds", "label":"Fields to return", "required":false,
                "type":"csv", "hint":"Comma-separated REST field names; blank = all" }],
    "body":  []                       // POST JSON body: { "name":"lead", "type":"array", … }
  },
  "response": { "shape": "object", "exportable": true },
  "notes": ["404 if lead doesn't exist"]
}
```

Field types to support in the renderer: `string, integer, boolean, csv, datetime,
json (free-form), file (bulk imports), multi-select (activity types, programs),
lookup (activity type picker fed by a companion call)`.

### 3.2 Non-technical UX rules (design contract)

1. Every call shows **plain English** summary + "when to use"; jargon gets a tooltip.
2. Required vs optional is visually distinct; optional section collapsed by default.
3. Every field has a **hint + example** copied/adapted from the docs.
4. Errors are **translated**: 606 → "You're sending requests too fast — wait ~20s and retry",
   607 → "Daily quota exhausted — resets at midnight CST", 1003 → "Filter matched too
   many leads — narrow the filter or use Bulk Export".
5. Responses render as pretty JSON **and** a "Download CSV" button when tabular.
6. Destructive calls (delete, deactivate, overwrite content) get a **confirm dialog**
   restating what will happen in plain English.
7. A persistent "Reporting isn't available via API" note near the top of the catalog.

## 4. Endpoint scope & phase plan

Groups in the API (counts approximate from the endpoint reference):

| Group | Examples | Phase |
|---|---|---|
| Core: Auth | token endpoint | 0 |
| Leads (read) | describe, describe2, get by id, get by filter, get by list/program/partition | 1 |
| Activities (read) | paging token, lead activities, lead changes, activity types, deleted leads | 1 |
| Leads (write) | sync leads, add to static list, program status, associate, merge, submit form | 2 |
| Programs / Opps / Sales Persons / Custom Objects | list, describe, members, sync, roles | 2 |
| Assets: Landing pages | list/get/content/variables/preview, redirect rules | 3 |
| Assets: Emails | list/get/full content/variables/send sample | 3 |
| Assets: Forms + form fields | list/get/fields/thank-you page | 3 |
| Assets: Lists & campaigns | static lists, smart lists, smart campaigns (incl. activate/deactivate) | 3 |
| Assets: misc | snippets, files, templates, segmentations, LP domains | 3 |
| Bulk extract | lead extract, activity extract (wizards: create→enqueue→poll→download) | 4 |
| Bulk import | lead import (file + mapping), warnings/failures, custom objects, program members | 4 |
| Custom activity types | CRUD (metadata mgmt — advanced) | 6 (stretch) |

### Phases (each phase ends in a shippable state)

Priority order confirmed by user (2026-10-28): **Lead API → Assets → Bulk**.

- **Phase 0 — Skeleton & vertical slice**
  Repo scaffold; `PLAN.md`+`NOTES.md` (done); config store; auth manager with
  auto-refresh; catalog loader + `/api/catalog` + `/api/call/{id}`; UI shell with
  sidebar + form renderer + response viewer; **2 real calls working end-to-end**:
  `Describe Leads` and `Get Lead by ID`.
  *Accept: enter instance + creds in UI, click the two calls, see real JSON, CSV export works.*
- **Phase 1 — Read the data (the "pull data" goal)**
  All lead read endpoints + activity read endpoints + field-picker helper fed by
  `describe.json` (so users can tick fields instead of typing names).
  *Accept: a user can pull "all fields of lead X", "leads in static list Y",
  "activities since last week" with zero JSON knowledge.*
- **Phase 2 — Lead writes + related lead-DB groups**
  Sync leads (single), static list add, program status change, associate/merge,
  submitForm; confirm dialogs for anything destructive. Plus read/write for
  Programs, Opportunities, Sales Persons, Custom Objects.
- **Phase 3 — Asset catalog** (LPs, emails, forms, smart lists, smart
  campaigns, campaigns, snippets, segmentations, domains). ✅ SHIPPED 2026-10-28:
  47 read-focused entries + safe writes (send sample email, form approve/clone/
  delete, smart list clone/delete, campaign activate/deactivate, Request Campaign
  trigger), all doc-verified. Deferred: LP/email/snippet content writes, LP
  templates, files, redirect rules, schedule campaign (see NOTES.md).
- **Phase 4 — Bulk wizards**
  Lead extract + activity extract as 4-step wizards with job history pane;
  lead import (file upload + field mapping), warnings/failures drill-down;
  program member + custom object import/export. ✅ SHIPPED 2026-10-28:
  5 bulk ops in `catalog/bulk/*.json` + `marketo_util/bulk.py` job engine
  (create→enqueue→poll→cancel→download for extracts; multipart upload +
  failures/warnings drill-down for imports), 9 `/api/bulk/*` routes, wizard UI
  with auto-poll + profile-scoped job history; all doc-verified; 27/27 tests.
  NOT shipped: custom object extract + program member extract (P4.5 candidates —
  doc pages exist, patterns known; see NOTES.md Phase 4 entry).
- **Phase 5 — Custom activity types** (metadata mgmt — advanced, stretch).
- **Phase 6 — Polish & packaging**
  Recent-calls log + home “Recently run” strip. ✅ SHIPPED 2026-10-28
  (`db.record_recent`/`list_recent`, `/api/recent`, recorded on every call +
  bulk start; home page itself rebuilt as a task hub — see NOTES.md).
  Saved presets (name + save a configured call), favorites,
  PyInstaller single-file exe for Windows (double-click → server + browser opens),
  README with setup + permissions guide (which Marketo role each group needs).
  ✅ P6.2 DARK MODE SHIPPED 2026-10-28: toggleable light/dark (top-bar button),
  token-based `:root[data-theme]` theming with `color-scheme` for native controls,
  persisted in localStorage, first-visit follows OS, FOUC-free inline boot script,
  plus fix of the undefined `--amber-bg` token. See DARK-MODE-PLAN.md + NOTES.md.
- **Phase 7 — Lead Field Library** (store + tag lead fields, feed the pickers).
  ✅ SHIPPED 2026-10-28: one-click load of the lead-fields schema from Browse
  `leads/schema/fields.json` (isCustom / isHidden / isSensitive / description /
  length), stored per profile in a `lead_fields` table; `describe.json` is the
  fallback (isCustom→unknown). Home “Lead field library” card (stats + Refresh +
  Clear); every lead-field picker (lookup param + sync-grid columns) unified into
  `renderTaggedPicker` with custom(system) badges + read-only chip +
  All/Custom/System filter + custom-first sort; pickers auto-load from the stored
  library on first use (no per-call network hit after load). Routes `GET/POST/
  DELETE /api/fields` + `GET /api/lookup/describe` (stored-first). 38/38 tests;
  DOM + visual (Pillow pixel) verified. See NOTES.md “P7 BUILT” entry.
  FUTURE (not approved): extend the pattern to custom-object / program-member
  field libraries.
- **Phase 8 — Settings, call logging & error-code reference.**
  ✅ SHIPPED 2026-10-28: a Settings menu (top-bar ⚙️ modal) to set the
  **export/save location** (where all calls + Marketo returns are stored) +
  a **toggle to log every call/response** (daily-rotated JSONL
  `marketo-calls-YYYY-MM-DD.jsonl` in the export dir; default OFF) + a
  **separate “also save bulk export files here”** toggle (independent of
  logging; response gets an `X-Marketo-Util-Saved-To` header when saved); when
  a Marketo response carries an **error code**, the code maps to the OFFICIAL
  description (experienceleague error-codes page) + a doc link, shown inline in
  the response viewer. Full official code table (48 codes) in
  `catalog/error_codes.json`, loaded by `marketo_util/errors.py` — the old
  hand-picked `executor.ERROR_MESSAGES` map was REMOVED (one source of truth).
  Routes: `GET/PUT /api/settings`, `POST /api/settings/test-dir`,
  `DELETE /api/settings/log`. 47/47 tests. See NOTES.md “P8 BUILT”.

## 5. Tech stack (recommended, pending user confirmation)

- **Python 3.11+ / FastAPI / uvicorn / httpx** — local server + proxy + token manager.
- **Vanilla HTML/CSS/JS** frontend served by FastAPI — no build step, trivial to package.
  (If the UI grows too big, swap in React-via-CDN without touching the backend.)
- **PyInstaller** one-file exe (Windows) for the "non-technical user" distribution.
- Deps: `fastapi, uvicorn, httpx, pydantic`. Dev: `pytest, ruff`.

### Project layout

```
Marketo-REST-Proj/
├─ PLAN.md  NOTES.md
├─ README.md
├─ requirements.txt
├─ marketo_util/
│  ├─ app.py              # FastAPI app, static serving
│  ├─ auth.py             # token acquire/refresh
│  ├─ config.py           # local config store
│  ├─ catalog.py          # catalog loader + schema validation
│  ├─ executor.py         # call building, http, error translation
│  ├─ bulk.py             # job lifecycle service
│  ├─ export.py           # JSON→CSV
│  └─ static/             # index.html, app.js, styles.css
├─ catalog/               # ONE FILE PER ENDPOINT (the product content)
│  ├─ schema.json
│  ├─ leads.get_by_id.json
│  └─ …
└─ tests/
```

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Token expiry mid-bulk-poll | Auth manager auto-refreshes; executor retries once on 401 |
| Rate limits (606/615) | Client-side min interval + server-side 429-style backoff with translated message |
| Big responses (300-record batches) | CSV export + "fetch next batch" helper where pagination exists |
| Secrets at rest | Client secret in the OS credential manager (Win Credential Manager / macOS Keychain), DB fallback when no store; OAuth token in memory only (re-derivable); loopback-only server; documented |
| Docs drift (2026 listId change already seen) | Pin docUrl per call; re-verify each endpoint's params with `web_extract` at implementation time, not from memory |
| Compaction losing context | NOTES.md progress log + phase checklist are the resume point |

## 7. Decisions (resolved 2026-10-28)

1. **Stack:** ✅ Python/FastAPI + vanilla JS (user approved the recommendation).
2. **Distribution:** ✅ Local-first for now, **must stay scale-ready** for a future
   shared-server/team deployment. Design rules adopted:
   - All non-derivable mutable state in **SQLite** (profiles, jobs, presets,
     recents) — same file works locally and on a server. Two safe exceptions:
     the OAuth token (memory only — re-derivable from credentials) and the
     client secret (OS credential manager — see decision 4).
   - **Profiles table** (multiple instance + credential sets, one active) instead of
     a single config file — multi-user later just means scoping rows per user.
   - Executor behind a **rate-limit gate** (min interval + max concurrency)
     from day 1, so team-wide quota sharing is handled centrally later.
   - Server binds **127.0.0.1** by default; host/port + `MARKETO_UTIL_DB` are env-
     overridable so a shared deployment is a config change, not a rewrite.
   - ✅ 2026-10-28: client secret lives in the **OS credential manager**
     (`keyring`), with SQLite fallback + lazy migration of legacy rows —
     see decision 4.
   - No session/auth layer yet (loopback only); FastAPI middleware is the planned
     insertion point for login when it goes shared.
   - Token cache **per profile, in memory** so multiple instances/users never
     share tokens; a restart costs at most one OAuth refresh per profile.
3. **v1 scope:** ✅ Priority order **Lead API → Assets → Bulk** (phases reordered above).
4. **Secrets at rest (2026-10-28):** client secret goes to the OS credential
   manager (`keyring` — Win Credential Manager / macOS Keychain / Linux Secret
   Service) instead of the DB: `secrets.py` probes availability with a real
   round-trip (some headless backends silently drop writes, so a bare
   try/except would lose the secret), stores per profile id, and migrates
   legacy DB rows lazily on first read. Falls back to the DB when no store is
   usable. OAuth tokens are memory-only (re-derivable); Munchkin/cookie
   values are never persisted (form input only).
