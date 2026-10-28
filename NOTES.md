# NOTES — running log & verified facts

> Read this FIRST each session. Append a dated entry after meaningful work.
> Facts below were verified against Adobe Experience League docs on 2026-10-28.

## Session log

### 2026-10-28 — Planning
- Workspace was empty; greenfield.
- Pulled the full Marketo REST endpoint reference (experienceleague `endpoint-reference` page) —
  ~150 endpoints across: Activities, Identity, Bulk (leads/activities/custom objects/program
  members import+export), Campaigns, Emails, Files, Form fields, Forms, Landing page content,
  Landing page templates, Landing pages, Redirect rules, Leads, Opportunities, Sales persons,
  Segments, Smart campaigns, Smart lists, Snippets, Static lists.
- Confirmed limits: 50k REST calls/day (CST midnight reset, err 607), 100 calls/20s (606),
  concurrency cap (615), 8KB URI (414), 1MB body (10MB bulk), 300/200 record batches,
  bulk export 500MB/day + 10-day file retention.
- Confirmed 2026-09-30 change: Get Lead Activities/Changes with `listId` on lists ≥10k leads → 1003.
- User confirmed: reporting is NOT available via REST API — keep out of scope, note in-app.
- Auth: client_credentials to `/identity/oauth/token`; token ~20 min; instance URL like
  `https://100-AEK-913.mktorest.com`. Bulk paths do NOT include `/rest/`.
- Wrote PLAN.md (roadmap, architecture, catalog schema, UX contract, phases 0–6, open decisions).

### 2026-10-28 — Decisions + Phase 0 build
- User confirmed: (1) Python/FastAPI + vanilla JS; (2) local now, **scale-ready for
  future shared server**; (3) priority **Lead API → Assets → Bulk** (phases reordered).
- Scale-ready choices baked into Phase 0: SQLite for ALL state (profiles/tokens/jobs/
  presets/recents), multi-profile table, per-profile token cache, rate-limit gate in
  executor, 127.0.0.1 bind (env-overridable host/port/db), secrets seam in config.py.
- **Phase 0 BUILT & VERIFIED:** full scaffold (FastAPI app, config profiles in SQLite,
  OAuth token manager w/ auto-refresh + 401 retry, catalog loader w/ validation,
  executor w/ rate-limit gate + error translation, vanilla-JS UI w/ sidebar/form/
  response viewer/CSV+JSON download/profile modal). 2 real calls in catalog:
  leads.describe + leads.get_by_id. 6/6 pytest pass. E2E flow verified over HTTP
  (friendly errors for: no profile, missing required field, bad creds).
- BUG FOUND & FIXED: a failed SQLite INSERT (e.g. duplicate name) left an open
  transaction on that thread's connection → next write from another thread-pool
  worker hit 'database is locked'. Fix: explicit `conn.rollback()` on every failed
  DML + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000`.
- **Status: Phase 0 complete; live smoke test still pending real credentials** (user
  chose to proceed to Phase 1 first).

### 2026-10-28 — Phase 1 built
- Verified params from docs (2026-10-07 pages) BEFORE authoring. Key findings:
  - **Field-selection param is `fields` — NOT `leadFieldIds`** (current docs +
    Marketo's own REST-Sample-Code use `fields`; `leadFieldIds` is the legacy name).
    Fixed leads.get_by_id.json accordingly.
  - Get Leads by Filter: `filterType`+`filterValues` REQUIRED; filterValues up to
    **300** comma-separated; >1000 matches → 1003; long URL → 414.
  - With no `fields`, Get Lead by ID returns ONLY id/email/firstName/lastName/
    createdAt/updatedAt — the picker hint reflects this.
  - listId 10k-lead caveat dates conflict between pages (dev reference says
    2026-09-30, experienceleague says 2026-12-30) — catalog notes say 'late 2026'.
  - `access_token` query-param auth was REMOVED 2025-06-30 — Bearer header only
    (our implementation is already correct).
  - Lead endpoints 30s timeout (filter/list 60s; paging token 300s) — our 120s
    client timeout is fine.
  - Describe2 = searchable fields for filterType; schema/fields browse = all fields,
    300/page with nextPageToken+moreResult.
- Added 15 catalog entries: leads.describe2, get_by_filter, get_by_list,
  get_by_program, partitions, list_membership, program_membership,
  smart_campaign_membership, schema_fields, field_by_name; activities.types,
  paging_token, lead_activities, lead_changes, deleted_leads. Total: 17 calls.
- **Field picker shipped**: catalog param type `lookup` + source `describe` →
  new `/api/lookup/describe` endpoint (paginated schema/fields browse w/ describe
  fallback, per-profile, in-memory client cache) → searchable checkbox UI with
  select-all/clear/count, graceful textarea fallback if the fetch fails.
  Used by: get_by_id, get_by_filter, get_by_list, get_by_program, lead_changes.fields.
- Response viewer now shows a 'more results available — use nextPageToken' hint
  when `moreResult` is true.
- 9/9 tests pass (incl. new: all 17 entries present, required-param checks,
  lookup no-profile 404/400 paths, field-shape mapping). Server restarted, UI
  verified live (Leads 12 + Activities 5), app.js node --check clean.
- **Status: Phase 1 complete (pending live smoke test with real creds).**

### 2026-10-28 — Phase 2 BUILT (lead writes + programs/opps/sales persons/custom objects)
- Doc-verification sweep (all via web_extract on experienceleague; developer.adobe.com
  mapi pages are JS-rendered and CANNOT be extracted — use the experienceleague
  `lead-database/*` pages + the `endpoint-reference` page, which extract fine):
  - **leads page (2026-10-07):** Sync body `{action?, lookupField?, input:[…]}` —
    action ∈ createOrUpdate (default) | createOnly | updateOnly | createDuplicate;
    lookupField default email (used for createOrUpdate/updateOnly); ≤300 records;
    company fields NOT settable via Sync Leads. Push Lead `POST /rest/v1/leads/push.json`
    `{programName*, input*, source?, reason?, lookupField?}` — always upsert, writes a
    triggerable activity; mktToken member associates cookie. Submit Form
    `POST /rest/v1/leads/submitForm.json` `{formId*, programId?, input:[{leadFormFields*
    (email required), visitorData?{pageURL,queryString,leadClientIpAddress,userAgentString},
    cookie?}]}`. Merge `POST /rest/v1/leads/{id}/merge.json?leadId=|leadIds=` — path id =
    WINNER; **since 2026-03-31 max 25 leadIds else 1080**; optional mergeInCRM (SFDC);
    CRM lead/contact rules can override winner. Associate `POST /rest/v1/leads/{id}/
    associate.json?cookie=_mkto_trk value`. Delete Leads `POST /rest/v1/leads/delete.json`
    body `{"input":[{"id":235},{"id":766}]}` ≤300 (endpoint-reference table shows the
    wrong path `/rest/v1/leads.json` — the 2026-10-07 leads page confirms /delete.json).
    Timeouts: Sync 90s, Associate 60s, Merge 180s, Push 90s.
  - **list-membership (2026-08-07):** Add to List `POST /rest/v1/lists/{listId}/
    leads.json?id=…&id=…` — **repeated `id` QUERY params, max 300** (old body form
    `{leadIds:[…]}` is superseded in current docs). Remove from List is a real
    **DELETE** with the same params. Member of List `GET …/leads/ismember.json?id=…`.
    Get Leads by List `GET …/leads.json?batchSize≤300&nextPageToken&fields` (default
    fields: email,updatedAt,createdAt,lastName,firstName,id).
  - **program-members (2026-05-13):** Describe `GET /rest/v1/programs/members/
    describe.json` (**plural programs** — older notes said /program/, corrected).
    Get Members `GET /rest/v1/programs/{programId}/members.json?filterType&filterValues
    (≤300)&fields&batchSize≤300&nextPageToken` — **100k membership limit → 1003**; use
    Bulk Program Member Extract beyond that. Set Status (UPSERT) `POST /rest/v1/
    programs/{programId}/members/status.json` body `{statusName, input:[{leadId}]}`
    ≤300 — statusName must exist on the program's channel (e.g. Member/Influenced/
    Completed); leads not in program are ADDED; leads already past that step are
    skipped (1037); writes 'Change Program Status' activity. Sync Member Data `POST
    /rest/v1/programs/{programId}/members.json` body `{input:[{leadId, …fields}]}` ≤300.
    Delete Members `POST …/members/delete.json` body `{input:[{leadId}]}` ≤300.
    Member fields: `GET /rest/v1/programs/members/schema/fields[/{fieldApiName}].json`.
  - **opportunities (2026-10-07, re-verified):** as in prior notes. filterType ∈
    externalOpportunityId|marketoGUID. Sync ≤300, name must not be null, no
    lookupField (dedupeBy dedupeFields|idField, valid with updateOnly; default
    createOrUpdate). Delete `POST /rest/v1/opportunities/delete.json {deleteBy?,
    input:[…]}` ≤300. Roles endpoints exist but body shapes unverified — NOT included.
  - **sales-persons (2025-09-29):** describe; query `GET /rest/v1/salespersons.json
    ?filterType=dedupeFields|idField|email|id&filterValues`; sync `POST /rest/v1/
    salespersons.json {action?, dedupeBy?, input:[{externalSalesPersonId, email,
    firstName, lastName, …}]}`; delete `POST /rest/v1/salespersons/delete.json
    {deleteBy?, input:[{…one key…}]}`. API-only records; need SFDC/Dynamics Sync.
  - **custom-objects (2026-08-07):** List `GET /rest/v1/customobjects.json`; Describe
    `GET /rest/v1/customobjects/{apiName}/describe.json`; Query `GET /rest/v1/
    customobjects/{apiName}.json?filterType&filterValues&fields&batchSize&nextPageToken`
    (compound keys → POST `?_method=GET` with input[]); Sync `POST /rest/v1/
    customobjects/{apiName}.json {action, dedupeBy?, input:[…]}` ≤300 (120s timeout);
    Delete `POST /rest/v1/customobjects/{apiName}/delete.json {deleteBy,
    input:[{key}]}` ≤300 (60s). Types: `GET /rest/v1/customobjects/schema.json
    ?state=draft|approved|approvedWithDraft&names`; `GET /rest/v1/customobjects/
    schema/{apiName}/describe.json?state=`; `POST …/schema.json {action?,
    displayName, apiName, pluralName?, description?, showInLeadDetail?}`; Approve
    `POST …/schema/{apiName}/approve.json`; Discard `…/discardDraft.json`; Delete type
    `…/schema/{apiName}/delete.json`. Fields: `POST …/schema/{apiName}/addField.json
    {input:[{displayName,name,dataType,description?,isDedupeField?}]}` (≤20/call,
    ≤50 fields/type, ≤10 types); `POST …/{apiName}/{fieldApiName}/updateField.json`;
    `POST …/schema/{apiName}/deleteField.json {input:[{name}]}`; Data types `GET
    …/schema/fieldDataTypes.json` (string,boolean,integer,float,link,email,currency,
    date,datetime,phone,text); Linkable `GET …/schema/linkableObjects.json`;
    Dependent assets `GET …/schema/{apiName}/dependentAssets.json`.
  - **endpoint-reference (2026-07-21) — full table confirmed:** Programs asset API
    (`GET /rest/asset/v1/programs.json`, `GET /rest/asset/v1/program/{id}.json`,
    `byName.json`, byTag, create/update/delete/tag endpoints — Read-Only/Write Asset);
    Static Lists (`GET /rest/v1/lists.json`, `GET /rest/v1/lists/{id}.json` —
    Read-Only Lead); Sales Persons permissions (Read/Write Sales Person);
    Change Lead Program Status `POST /rest/v1/leads/programs/{programId}/status.json`
    (existing members only) — body shape from Marketo sample client
    `{status, input:[{id}]}`, included as freeform w/ caveat note.
- **NEW UI MECHANICS shipped (executor + app.js + catalog.py + schema.json):**
  - Param type **`select`** (`options:[{value,label}]`, optional `placeholder`
    for a leading default/omit option) — used for action/dedupeBy/deleteBy/
    filterType/state.
  - Query param flag **`repeat: true`** — comma input → repeated params
    (`id=1&id=2`) for Add/Remove/Member-of-List. Verified in executor tests.
  - Body mode **`values`** — comma-separated values → array under `key` (default
    `input`); `itemKey` wraps each value as `{itemKey: value}`; `type: integer`
    coerces; optional `fields[]` for extra scalar members (statusName, deleteBy);
    `maxPerCall` enforced client+server with friendly message.
  - Body mode **`sync`** — `fixed[]` scalar members (UI: normal fields) + a
    **record grid editor** (app.js `initSyncGrid`): columns from the describe
    field picker (`fieldPicker: "describe"`) OR user-typed column names;
    required columns locked (`requiredFields`); add/remove rows; reads
    `{fixed:{…}, rows:[{col:val}]}` → executor builds `{…fixed, input: rows}`.
    Grid picker reuses the lookup cache; graceful typed-columns fallback if
    describe fetch fails.
  - **DELETE method** supported (catalog METHODS + pass-through; httpx fine).
  - Record-mode `json` fields are JSON.parse'd client-side with friendly errors
    (used by Submit Form's leadFormFields/visitorData).
  - Server-side `_validate_params` now checks required body members for
    record/multipart/values/sync modes.
- **43 new catalog entries** (60 total, 7 groups: Leads 19, Activities 5,
  Static Lists 5, Programs 10, Opportunities 6, Sales Persons 4, Custom Objects 11).
  Destructive calls all carry `confirm` dialogs (merge, delete leads/lists/opps/
  salespersons/customobjects, remove program members, approve type).
- **Tests: 14/14 pass** (was 9) — new: phase-2 IDs + groups present, confirm/mode/
  select/repeat/DELETE shape checks, executor body-building for values+sync modes
  (exact JSON equality), repeat-query expansion, required-body validation.
  BUG FOUND & FIXED: executor sync branch read fixed members from the wrong level
  (body vs body.fixed) — now accepts both shapes.
- Server restarted (killed PID on 8787), `/api/health` → 60 calls, UI opened in
  in-app browser; app.js node --check clean.
- **Status: Phase 2 complete (pending live smoke test with real creds — user has
  not provided them yet). Next: Phase 3 — assets (LPs, emails, forms, smart lists,
  smart campaigns, snippets, files, templates, segments).**

### 2026-10-28 — Phase 3 BUILT (assets: landing pages, emails, forms, smart lists,
### smart campaigns, campaigns, snippets, segmentations)
- **KEY URL PATTERN FOUND (future sessions!):** asset doc pages live under
  `/en/docs/marketo-developer/marketo/rest/assets/<slug>` — NOT `/rest/<slug>`
  (that's why 10 URL guesses 404'd in the prior session). Verified working slugs:
  `assets/landing-pages` (2026-10-07), `assets/emails` (2026-08-07),
  `assets/forms` (2026-08-07), `assets/smart-lists` (2026-10-07),
  `assets/smart-campaigns` (2026-10-07), `assets/snippets` (2026-10-07),
  `assets/dynamic-content` (2026-10-07 — this page documents the SEGMENTATION
  endpoints), `assets/landing-page-redirect-rules` (documents
  landingPageDomains.json). Segments have NO dedicated page — `assets/segments`
  404s. developer.adobe.com pages remain JS-rendered / unextractable.
- **Verified against those pages (full details below):** all read endpoints + the
  small write set shipped: email sendSample, form approve/clone/delete, smart
  list clone/delete, smart campaign activate/deactivate/trigger (Request
  Campaign), plus plain reads for campaigns (lead API).
- **sendSample params (verified):** `POST /rest/asset/v1/email/{id}/sendSample.json`
  with `emailAddress` REQUIRED as a QUERY param (not body), optional `leadId`
  (impersonate), optional `textOnly`.
- **NEW executor feature — `body.encoding: "form"`** (record mode): Marketo asset
  write endpoints (clone form/list) REQUIRE `application/x-www-form-urlencoded`,
  not JSON (docs say so explicitly). Executor now serializes record members via
  `_form_value()` (bool→"true/false", dict/list→JSON string, else str) and sends
  through httpx `data=`. Used by forms.clone + smartlists.clone. UI needed NO
  changes (record fields already render; json-type fields parse client-side).
- **NEW executor feature — `body.wrapKey`** (values mode): nests the array one
  level deeper — smartcampaigns.trigger builds `{"input": {"leads":
  [{"id": …}]}}` (key=input, wrapKey=leads, itemKey=id, type=integer,
  maxPerCall=100). Verified by exact-body test.
- **Optional booleans are modeled as `select` with a single option + placeholder**
  (e.g. includeRules: options [true], placeholder "No (default)") — a plain
  boolean select would default to sending "true" for untouched fields. Pattern
  reused for textOnly, isActive, status filters (selects with placeholder
  "All (default)").
- **47 new catalog entries** (107 total; groups: Landing Pages 7, Emails 8,
  Forms 11, Lists & Campaigns 15, Other Assets 6). All write calls that touch
  live assets carry `confirm` dialogs. Permissions from the endpoint reference
  (2026-07-21): activate="Activate Campaign", deactivate="Deactivate Campaign",
  trigger="Read-Write Campaigns", campaigns list/byId="Read-Only Campaigns",
  landingPageDomains="Read-Only Redirect Rules".
- **Judgment call (documented):** smartcampaigns.trigger (Request Campaign) is
  INCLUDED despite not being in the original draft — it's the most-requested
  smart-campaign op, fully documented, and gated by a strong confirm. Schedule
  Campaign (20/day cap, complex body) was deliberately SKIPPED to keep P3
  shippable; LP create/update + LP/email/snippet writes deferred (draft said
  reads-focused for LPs/snippets).
- **Tests: 17/17 pass** (was 14) — new: test_phase3_entries (all 47 present,
  groups, confirms, form encoding, wrapKey, permissions, GET-only reads),
  test_build_form_encoding_body (exact data dict + folder JSON string + bool
  coercion), test_build_wrapkey_body (exact nested body + >100 rejected).
- Server restarted (killed PID on 8787), `/api/health` → 107 calls, UI opened
  in in-app browser for user review.
- **Status: Phase 3 complete (pending live smoke test with real creds). Next:
  Phase 4 — bulk extract/import wizards.**

### 2026-10-28 — Phase 4 BUILT (bulk extract + import wizards)
- All 5 bulk ops doc-verified via web_extract (bulk-extract + bulk-import pages)
  BEFORE authoring. Verified facts (trust these over memory):
  - **Common:** paths start at `/bulk/v1/…` — no `/rest/` prefix. FIFO queue: max 2
    concurrent / 10 total → error **1016** (queue full). Import files <10 MB.
    batchIds valid 7 days; export files retained 10 days; poll status every 5–30s.
    Import status: Queued/Importing/**Complete**/Failed; extract status:
    Created/Queued/Processing/Canceled(Cancelled)/Completed/Failed.
  - **Lead extract:** `POST /bulk/v1/leads/export/create.json` — `fields[]` REQUIRED;
    `filter` exactly ONE of: `createdAt`|`updatedAt` {startAt,endAt} (≤31 days, ISO
    without ms; updatedAt + smart-list filters subscription-gated → 1035), or
    `staticListId`|`staticListName`|`smartListId`|`smartListName`; opt `format`
    CSV|TSV|SSV + `columnHeaderNames` object. → `result[0].exportId`. Enqueue
    `POST …/export/{exportId}/enqueue.json`; status `GET …/status.json`
    (+numberOfRecords, fileSize); file `GET …/file.json` (Range supported);
    cancel `POST …/cancel.json`.
  - **Activity extract:** `POST /bulk/v1/activities/export/create.json` —
    `filter.createdAt` REQUIRED (≤31 days); opt `activityTypeIds[]`,
    `primaryAttributeValueIds[]` (≤50, requires activityTypeIds) XOR
    `primaryAttributeValues[]` (≤50, `<program>.<asset>` notation); opt `fields[]`
    (default 8 cols + actionResult), format, columnHeaderNames. Same endpoint pattern
    under `/bulk/v1/activities/export/`. Perm: Read-Only/Read-Write Activity.
    "Delete Lead" activity NOT supported.
  - **Lead import:** `POST /bulk/v1/leads.json?format=csv|tsv|ssv` multipart `file`;
    opt query `lookupField` (default email; `id`=update-only), `listId`,
    `partitionName` → `result[0].batchId`. Status `GET /bulk/v1/leads/batch/{id}.json`
    (**NO `/status` suffix — classic trap**); failures/warnings
    `GET …/batch/{id}/failures.json|warnings.json`.
  - **Program member import:** `POST /bulk/v1/program/{programId}/members/import.json?
    format=…&programMemberStatus=…` multipart `file`; every row needs email.
    Status/failures/warnings: `GET /bulk/v1/program/members/import/{batchId}/
    status.json|failures.json|warnings.json` (**no programId in these paths**).
  - **Custom object import:** `POST /bulk/v1/customobjects/{apiName}/import.json?
    format=…` multipart `file` → `{batchId, objectApiName}`. Status/failures/warnings:
    `GET /bulk/v1/customobjects/{apiName}/import/{batchId}/…` (404 = no file).
  - **Doc URL patterns:** `…/marketo/rest/bulk-extract/<slug>` and
    `…/marketo/rest/bulk-import/<slug>`. Pages exist but NOT shipped:
    `bulk-extract/bulk-custom-object-extract`, `bulk-extract/bulk-program-member-extract`,
    `bulk-import/bulk-import` (overview) → P4.5 candidates.
- **Shipped:**
  - `catalog/bulk/*.json` — 5 op definitions (extract_leads, extract_activities,
    import_leads, import_program_members, import_custom_objects). Kept in a subfolder
    so the non-recursive catalog glob naturally skips them. Each op: friendly `form`
    (reuses catalog field shapes incl. `lookup`/`select`/`file`/`json` + new
    `dynamicFields` for the lead-extract filter), endpoint templates, `resultId`.
  - `marketo_util/bulk.py` (652 lines) — per-op body builders (explicit > generic
    DSL, documented tradeoff), job lifecycle on the existing `jobs` table
    (profile-scoped, survives restarts), reuses `executor.gate()` +
    `translate_error()` + `auth.request`. States: created/queued/processing/completed/
    failed/canceled/expired (terminal: completed, failed, canceled, expired);
    404 status → "expired" + friendly message; 404 failures/warnings → friendly
    "no file" messages.
  - `app.py` — 9 routes: `GET /api/bulk/ops`, `GET /api/bulk/op/{id}`,
    `POST /api/bulk/op/{id}/start` (JSON for extracts, multipart for imports),
    `GET /api/bulk/jobs`, `GET /api/bulk/jobs/{id}` (refresh), `POST …/cancel`,
    `GET …/file|failures|warnings` (binary downloads via server proxy).
    New lookup source `activityTypes` (GET /rest/v1/activities/types.json).
  - UI: 4-step wizard (Configure → Start → Running → Get results) rendered into
    `#call-detail`, 5s auto-poll while non-terminal, profile-scoped job history
    table, download via direct `<a href>` to the proxy routes. New sidebar groups
    "Bulk Extract" (2 ops) + "Bulk Import" (3 ops) after "Other Assets". Reuses the
    existing field renderers (section name `"bulk"`, IDs `f-bulk-<name>`).
- **Tests: 27/27 pass** (was 17) — 10 new bulk tests: ops load+validate, 404 paths,
  lead/activity extract body exact-JSON (incl. 31-day cap, XOR, 50-item caps),
  import request building (path/query/multipart per op), full extract flow
  (create→enqueue→refresh→download via HTTP route), full import flow
  (create→status→failures download, 404 warnings → friendly), cancel +
  double-cancel, validation errors (missing filter, missing file, >10 MB, missing
  program status). Network faked by monkeypatching `marketo_util.auth.request`
  (bulk.py calls it via module attr, so this works).
- **REGRESSION FOUND & FIXED (test infra — important for future phases):**
  starlette 1.10's TestClient dispatches each request on a FRESH portal thread, so
  `db.get_conn()` re-reads the process-wide `MARKETO_UTIL_DB` env var on every new
  thread. The two "no profile" tests set that env var to a scratch empty DB and
  never restored it → later tests (any fresh thread) connected to the empty DB
  (`test_lookup_endpoint_no_profile` 404 assertion returned 400). Fix: added
  `_isolated_db()` context manager in tests/test_smoke.py (saves/restores the env
  var + drops the main thread's cached connection), made the lookup 404 assertion
  self-sufficient (creates + activates a profile when run in isolation), and made
  `test_bulk_refresh_download_and_route` start its own job when run without the
  start-flow test. RULE: tests must never leave `MARKETO_UTIL_DB` pointing at a
  scratch DB; the app-side design (env-overridable DB path) is correct for prod
  and was not changed.
- Server restarted (killed PID on 8787), `/api/health` → 107 catalog calls (health
  counts catalog calls only, NOT bulk ops — by design), 5 bulk ops + empty job list
  verified over HTTP, UI opened in in-app browser (sidebar groups + wizard render;
  static assets + /api/bulk/ops all 200 in server.log).
- **Status: Phase 4 complete (pending live smoke test with real creds — user has not
  provided them yet). NOT shipped (P4.5 candidates, pages exist + patterns known):
  custom object extract, program member extract.**

### 2026-10-28 — Dark mode BUILT (toggleable, token-based)
- **Scope:** frontend only — `static/styles.css`, `static/index.html`, `static/app.js`. No backend change.
- **Design (user approved plan DARK-MODE-PLAN.md):** theme = `data-theme="light|dark"` on
  `<html>`; light values unchanged; dark = one `:root[data-theme="dark"]` override
  block. 30 new tokens added (surfaces, tints, status lines, secondary text) so every
  previously-hardcoded hex now resolves through a token (verified: 38/38 `var()` refs
  defined; only palette blocks + white button text + the JSON viewer's dark slate keep
  raw hex). `color-scheme: light|dark` set per theme so native selects/scrollbars follow.
- **Toggle:** top-bar icon button (`#btn-theme`, `aria-pressed`, icon = target state).
  Choice persisted in `localStorage["marketo.theme"]`; first visit follows
  `prefers-color-scheme` and keeps tracking the OS until an explicit choice. Inline
  boot script in `<head>` sets the attribute before first paint (no flash).
- **BUG FOUND & FIXED (latent, pre-existing):** `var(--amber-bg)` was used by `.m-post`
  and `.state-canceled` but NEVER defined — POST method chips rendered with no
  background. Now defined in both themes (light `#fdf5e6`, dark
  `rgba(255,180,84,.15)`).
- **Gotcha (future UI work):** app.js top-level `const state` is NOT on `window`
  (strict-mode classic script) — only function declarations are; cross-realm code
  (e.g. iframes, test harnesses) must use the functions, not `window.state`.
- **Verification:** `node --check` clean; pytest 28/28; headless-Edge screenshots of
  home/browse/detail in BOTH themes pixel-checked (dark: topbar `#161c27`, bg
  `#0f131b`, zero white surfaces; light: white topbar/panels as before). Note: this OS
  is already dark — the app's own OS-follow default produced the dark shot; light shots
  came from a seeded `localStorage` profile (temp same-origin seed page, deleted after).
- **Status: shipped.** Docs updated (README, PLAN.md P6.2, this entry).

### 2026-10-28 — P7 PLANNING (Lead Field Library) + Home button
- **Home button SHIPPED (quick fix):** persistent “🏠 Home” button in the top bar
  (`index.html` `#btn-home`, wired to `goHome()` in `app.js` `bindStatic`) — reachable
  from EVERY view; browse view previously had no back-to-home nav at all. Also added
  to mockup 5's top bar. `node --check` clean.
- **P7 LEAD FIELD LIBRARY — plan + mockup approved by user** (mockups/
  mockup-5-lead-field-library.html, registered in mockups/README.md + index.html).
  User ask: easily hit the lead-fields API, STORE the fields, TAG the system-managed
  ones, feed the pickers of calls that support lead fields.
  - **VERIFIED (lead-database/leads page, 2026-10-07):** Browse
    `/rest/v1/leads/schema/fields.json` returns per field: `displayName, name,
    description, dataType, length, isHidden, isHtmlEncodingInEmail, isSensitive,
    isCustom`. `isCustom:false` = standard (system) field; `isCustom:true` =
    user-created. `describe.json` only exposes `rest.readOnly` — NO `isCustom`
    → **Browse is the primary source** (current code already prefers it),
    `describe.json` = fallback (isCustom then unknown/NULL).
  - **Design (approved):** new `lead_fields` table, profile-scoped,
    `UNIQUE(profile_id, api_name)`: api_name, display_name, data_type, is_custom
    (NULL=unknown), read_only, is_hidden, is_sensitive, description, field_len,
    loaded_at. Routes: `GET /api/fields` (stored list + stats), `POST
    /api/fields/refresh` (fetch + upsert + stats — the “one-click load” flow),
    `DELETE /api/fields` (clear). `GET /api/lookup/describe` prefers the stored
    table; if empty, live-fetch, store, return (first-use auto-load). UI: home
    “Lead field library” card (stats + Refresh), picker rows get `custom`/`system`
    badges + `read-only` chip, All/Custom/System filter, custom-first sort; same
    treatment in sync-grid column picker (leads.sync, leads.push). Field objects
    keep the existing `{name,label,dataType,readOnly,…}` shape + new
    `{isCustom (bool|null), isHidden, isSensitive, description, fieldLen}`.
  - **Phases:** P7.1 backend (db + lookups + routes + tests) → P7.2 UI (home card +
    picker + grid + tests) → P7.3 verify (pytest, node --check, restart, live UI) +
    docs (NOTES/PLAN/README).
- **Status: Home button shipped; P7 approved, NOT yet built.** Next: P7.1.
  *(superseded by the P7 BUILT entry below — built + verified the same day.)*

### 2026-10-28 — P7 BUILT (Lead Field Library) — shipped + verified
- **Backend (P7.1):** `db.py` new `lead_fields` table (profile-scoped,
  `UNIQUE(profile_id, api_name)`; cols api_name, display_name, data_type,
  is_custom, read_only, is_hidden, is_sensitive, description, field_len,
  loaded_at) + `store_lead_fields` (upsert, rollback-on-error),
  `list_lead_fields` (custom-first ORDER BY), `lead_field_stats`
  ({count, custom, system, unknown, lastLoaded}), `clear_lead_fields`, `_tri`
  (tri-state 1/0/NULL helper). `lookups.py` `_map_field` now captures
  isCustom/isHidden/isSensitive/description/fieldLen (None-safe for the
  `describe.json` shape which lacks them); new `stored_fields`, `field_stats`,
  `fetch_and_store`, `load_and_store`, `clear`. `app.py` routes: `GET
  /api/fields` (stored list + stats), `POST /api/fields/refresh` (fetch + upsert
  + stats = the one-click load), `DELETE /api/fields` (clear) — all 400 with no
  active profile; `GET /api/lookup/describe` prefers the stored table, else
  fetch + store + return (first-use auto-load). Source = Browse
  `leads/schema/fields.json` (exposes isCustom), `describe.json` fallback
  (isCustom→NULL/unknown).
- **UI (P7.2):** unified BOTH picker variants (lookup param + sync-grid columns)
  into one `renderTaggedPicker(el, fields, opts)` in `app.js` — rows get
  `custom`(green) / `system`(neutral) badges + a `read-only`(amber) chip via
  `fieldBadgesHtml`; All / Custom only / System only segmented filter;
  custom-first sort (`sortFieldOptions`); non-matching rows `.lk-hidden`.
  Home gains a "Lead field library" card (`.task.fld` + `.newtag`) with stats +
  Refresh + Clear (`loadFieldCard`/`refreshFieldList`/`fieldStatsHtml`/`fmtAgo`).
  `styles.css`: `.tagc.*`, `.seg`, `.lk-row .lk-badges`, `.lk-hidden`,
  `.task.fld`, `.newtag`, `.fld-stats`, `.fld-actions` — all theme-token based
  (dark-mode safe).
- **Tests: 38/38 pass** (was 33). New Phase-7 block in tests/test_smoke.py:
  `test_p7_map_field_captures_tags`, `test_p7_field_library_flow` (full route
  flow + "picker must not hit network after first load" + upsert + clear),
  `test_p7_lookup_autostores_on_first_use`,
  `test_p7_describe_fallback_unknown_custom`,
  `test_p7_fields_routes_no_profile` (thread + `_isolated_db` pattern).
- **Verification (all PASS):** `node --check app.js` clean; pytest 38/38.
  DOM functional check (headless Edge + stdlib CDP): 5 sample rows render with
  badges `[custom, custom, system, read-only+system, (none)]`, 3-button
  segmented filter, "Custom only" filter → exactly 2 visible. **Visual pixel
  check (Pillow):** the first crop (`picker-crop.png`) was a solid blank band —
  root cause: `captureBeyondViewport:true` + a viewport-relative `clip`; Chrome
  reads that clip in ABSOLUTE page coords, so it captured a blank band ABOVE the
  picker. The app's own async `openEntry` render was ALSO overwriting the
  picker between separate CDP calls. Fix (`scratch/cdp_picker2.py`): render into
  a standalone `#shotbox` the app never manages, force `data-theme="dark"`,
  render+verify in ONE CDP call, then full-viewport screenshot + Pillow crop.
  New crop (`scratch/picker-crop3.png`): green `#4cc07e` "custom" text = 1218px
  clustered on rows 1–2 right edge; amber `#ffb454` "read-only" = 968px (853 in
  the Lead Score row band, x≈2200–2349). Both badge types confirmed rendered.
- **Status: SHIPPED.** Home button + P7 live on 127.0.0.1:8787. Docs updated
  (this entry, PLAN.md Phase 7, README). REMAINING for P7: **live smoke test
  with real Marketo creds** (confirm Browse returns isCustom as documented +
  end-to-end refresh) — still pending for ALL phases; test profiles use fake
  creds. FUTURE (NOT approved): generalize the field-library pattern to
  custom-object / program-member field libraries.

### 2026-10-28 — P8 PLANNING (Settings + call logging + error-code reference)
User ask: a **Settings** menu that (a) sets the **export/save location** where ALL
calls + their Marketo returns are stored, (b) a **toggle** to log every call +
return, and (c) when a return carries an **error code**, map that code number to
the OFFICIAL description (experienceleague error-codes page) + a doc link. Also
closes the earlier gap that bulk CSVs only land in the browser Downloads folder.
- **VERIFIED (error-codes page, pub. 2026-08-07 — PARTIAL extract):** HTTP-level
  401 (bad client id/secret on identity endpoint), 413, 414, 502, 503;
  response-level 606 (rate limit, >100 calls/20s), 607 (daily quota, resets
  12AM CST), 608 (API temporarily unavailable), 609 (invalid JSON), 610 (URI not
  a REST resource), 611 (system error), 613 (invalid multipart), 614 (invalid
  subscription), 615 (concurrency), 701 (field blank), 702 (no data for search);
  record-level 1001 (type mismatch), 1002 (missing required param), 1003 (invalid
  data), 1004 (lead not found / updateOnly), 1005 (lead exists / createOnly),
  1006 (field not found), 1007 (multiple leads match), 1011 (field not
  supported), 1012 (invalid cookie), 1013 (object not found), 1014 (create
  failed), 1015 (lead not in list), 1016 (too many imports, max 10), 1017
  (object exists), 1020/1021, 1026–1028, 1048–1049, 1076–1079 (merge).
  **NOTE:** extract was PARTIAL (tables truncated) → full transcription is the
  P8.0 verification step. **1003 is overloaded:** officially “Invalid data”, but
  Marketo also returns 1003 when a filter matches too many leads (this project's
  verified behavior) → show the official desc AND Marketo's specific message.
- **Current state:** `executor.ERROR_MESSAGES` is a small hand-picked map (401,
  403, 404, 414, 1003, 606, 1016, 607, 615, 600); `_friendly()` + `_interpret()`
  build `{friendly, code, raw}`. No general settings store (only `profiles`).
  `record_recent` logs metadata only (no body). Bulk files stream to the browser
  (never written to disk).
- **Design (proposed):**
  - `settings` table (db.py): single row; cols `export_dir TEXT` (default
    `<app-data>/exports`), `log_calls INTEGER DEFAULT 0`, `save_bulk_files
    INTEGER DEFAULT 0`. New `settings.py` (get/set, validate_dir, default_dir) —
    kept separate from `config.py` (connections).
  - New `calllog.py`: `maybe_log(profile, call_id, params, result)` — when
    `log_calls` on, append one JSON line to the DAY's log
    `<export_dir>/marketo-calls-YYYY-MM-DD.jsonl` (daily-rotated; one file per
    day, not one ever-growing file) ({ts, profile, call_id, params, status, ok,
    latency_ms, data, error{code, friendly, doc, raw}}); thread lock; a write
    error NEVER breaks the call.
  - `catalog/error_codes.json`: full official code→{desc, category} map + docUrl.
    `executor.py` loads it (cached); `_friendly()` uses it; `_interpret()` adds a
    `doc` link. Keep the “Marketo said: {msg}” suffix for specificity.
  - Bulk save: `save_bulk_files` on → `_file_response`/`download_file` also write
    the CSV to `<export_dir>/marketo-<slug>-<jobId>.csv` (best-effort, never
    breaks the download).
  - Routes: `GET /api/settings`, `PUT /api/settings`, `POST
    /api/settings/test-dir` (writability check). Optional `DELETE
    /api/settings/log` (clear the log).
  - UI: top-bar “⚙️ Settings” → new modal `#settings-modal` (reuses `.modal`
    CSS, same pattern as Manage connections): export-location path input + Test +
    “Use default”; “Log every call + Marketo response” checkbox; “Also save bulk
    export files here” checkbox; error-code doc link. app.js: `openSettings` /
    `loadSettings` / `saveSettings` / `testDir`.
  - Error display: in `showResponse()`, when `error.code` present → show a code
    badge + the official description + a “View in Marketo docs” link.
- **Phases:** P8.0 verify full error-code list (web_extract) → P8.1 settings
  store + routes + modal → P8.2 call logger + `run_call` hook → P8.3 error-code
  reference (data + executor + UI) → P8.4 bulk file save → P8.5 tests + docs.
- **Decisions (confirmed by user 2026-10-28):** (1) `log_calls` default OFF
  (opt-in). (2) Log = ONE file per DAY (daily-rotated)
  `marketo-calls-YYYY-MM-DD.jsonl` — not a single ever-growing file, not
  one-file-per-call. (3) Bulk file save = SEPARATE toggle (`save_bulk_files`),
  independent of `log_calls` (bulk is rare for a single user).
- **Status: CONFIRMED — building (P8.0 → P8.5).**

### 2026-10-28 — P8 BUILT (Settings + call logging + error-code reference)
- **Shipped (all smoke-tested + tested):**
  - `settings` single-row table in `db.py` (`CHECK (id=1)`, cols `export_dir`,
    `log_calls`, `save_bulk_files`) + new `marketo_util/settings.py` module
    (get/set/validate/test-dir, `default_dir()` = `<app-data>/exports`).
    Kept separate from `config.py` (connections) by design.
  - Routes: `GET/PUT /api/settings` (partial updates keep other values;
    relative paths → 400), `POST /api/settings/test-dir` (writability probe),
    `DELETE /api/settings/log` (removes `marketo-calls-*.jsonl`).
  - New `marketo_util/calllog.py`: `maybe_log()` appends one JSON line per call
    to `<export_dir>/marketo-calls-YYYY-MM-DD.jsonl` (daily-rotated per user
    decision; ts/profile/call_id/params/status/ok/latency_ms + data or
    error). Thread-locked; **a write failure NEVER breaks the call** (wrapped).
    `save_bulk_file()` (separate toggle) writes bulk downloads to the export
    dir; `_file_response` in app.py adds header `X-Marketo-Util-Saved-To` when
    saved. Hooked in `run_call` for both success and validation-error paths.
  - New `catalog/error_codes.json`: **48 codes** verified from the official
    page (HTTP-level 401/403/404/413/414/502/503; 600s/700s incl. 714;
    1001–1049 + 1076–1079). `catalog.load_catalog()` skips it (and schema.json)
    so it isn't validated as a call. New `marketo_util/errors.py` (cached
    loader + `describe()`/`info()`/`doc_url()`). `executor.ERROR_MESSAGES`
    (old 10-code hand-picked map) REMOVED — one source of truth.
    `_friendly()` → `errors.describe(code, status, msg)` = official desc +
    docs comment + Marketo's own message; `_interpret()` error object now
    carries `doc` (error-codes page URL) whenever a code is present.
    **1003 overload preserved:** official "Invalid data" + the docs' comment
    ("filter matched too many leads — narrow the filter or use a Bulk
    Extract") + Marketo's specific message.
  - UI: top-bar **⚙️ Settings** button → `#settings-modal` (same modal pattern
    as Manage connections): export-dir input + Test + Use default; "Log every
    call" checkbox; "Also save bulk export files here" checkbox; error-codes
    doc link; "Clear saved call logs". `showResponse()` error box now renders
    a `.err-code` badge + official description + `.err-doc` "View in Marketo
    docs ↗" link. CSS is token-based (dark-mode safe).
- **Tests: 47/47 pass** (was 38; added 9 P8 tests: defaults-off in a truly
  fresh DB via new-thread TestClient; PUT/GET + relative-path rejection +
  test-dir; error-code mapping incl. 1003 overload + unknown fallback;
  `_interpret` doc link; calllog ON writes the daily JSONL with full entry;
  OFF writes nothing; bulk save ON → header + file on disk / OFF → no header;
  unwritable log path never breaks the call; suite-DB left at defaults).
  NOTE: old P0 `test_error_translation_table` asserted the pre-P8 hand-picked
  wording ("100 calls per 20 seconds", "50,000") — updated to the official
  table wording (behavior was intentional, not a regression).
- **Test-infra subtlety discovered (important for future tests):** the
  main thread AND the module-level `TestClient` portal thread each cache their
  SQLite connection (thread-local) against the env var seen at FIRST use —
  both end up pinned to the suite DB (`_TMP/test.db`), even inside
  `_isolated_db()` blocks. Only a brand-new thread + new TestClient sees the
  isolated DB (that's the pattern the no-profile route tests and
  `test_p8_settings_defaults_off` use). Practical rule: tests that mix
  main-thread `config.*` calls with `client` requests must run OUTSIDE
  `_isolated_db` (or in the fresh-thread pattern) to share one DB.
- **Housekeeping done:** live-server settings reset (log_calls back OFF,
  default export dir restored); `scratch/test-exports/` removed.
- **Status: SHIPPED.** Docs updated (this entry, PLAN.md Phase 8, README
  "Settings, export location & call logging"). REMAINING: **live smoke test
  with real Marketo creds** (confirm a real error code maps to the official
  text + a real call lands in the daily log) — still pending for ALL phases;
  test profiles use fake creds. FUTURE (not approved): P4.5 (custom object
  extract + program member extract), P5 (custom activity types), P6 remainder
  (presets/favorites, PyInstaller, permissions guide), generalize the field
  library to other object types.

## Build notes (gotchas we hit)

- Windows Git Bash here has no /tmp — log to a working-dir file instead.
- Thread-pool (FastAPI sync endpoints) + thread-local SQLite conns = the locking
  bug above. Rule: ALWAYS rollback on failed DML; keep WAL+busy_timeout.
- **starlette 1.10 TestClient + thread-local DB (2026-10-28):** each TestClient
  request runs on a fresh portal thread → `db.get_conn()` picks up whatever
  `MARKETO_UTIL_DB` is set to AT THAT MOMENT. Tests that swap the DB must restore
  the env var afterwards (use `_isolated_db()` in tests/test_smoke.py) or every
  later request from a fresh thread connects to the scratch DB. Also note: profile
  auto-activation happens only in the app route — direct `config.create_profile()`
  in tests leaves it INACTIVE (use `config.set_active()` for 400→404-style
  assertions).
- `config.create_profile` must NOT blanket-catch all exceptions as 'duplicate
  name' — it masked a real OperationalError during testing.
- Marketo instance names accept bare ('100-AEK-913'), with .mktorest.com, or full
  URLs — `config.normalize_instance()` handles all three.

## Scaling notes (for the future shared-server deployment)

- SQLite file → can grow to Postgres later; keep all SQL in `db.py` (one seam).
- `config.py` Profile dataclass is the identity seam: per-user profiles later =
  add a `user_id` column + auth middleware in `app.py` (FastAPI dependency).
- Rate-limit gate (`executor.py`): per-instance quotas are shared across users once
  multi-user — the central gate already enforces min-interval/max-concurrency.
- Secrets: move `client_secret` storage from SQLite to OS keyring/vault behind the
  same Profile interface.
- Static files + catalog are read-only content → safe to serve multi-user as-is.
- Job history (bulk) is already DB-backed with `profile_id` → per-user scoping later.
- Windows packaging (PyInstaller) is orthogonal: works for both local & server builds.

## Verified endpoint details (for catalog authoring)

- **Auth:** `POST https://{instance}.mktorest.com/identity/oauth/token`
  body: `grant_type=client_credentials&client_id=…&client_secret=…`
  (also accepts GET per docs; use POST). Token valid ~20 min.
- **Get Lead by ID:** `GET /rest/v1/lead/{id}.json` — opt: `fields` (REST API names,
  comma-separated; `leadFieldIds` is the LEGACY name — do not use).
- **Describe Leads:** `GET /rest/v1/leads/describe.json`; **Describe2:** `describe2.json` (fuller metadata).
- **Get Leads by Filter:** `GET /rest/v1/leads.json?filterType=…&filterValues=…(≤300)
  &fields=…&batchSize=…(≤300)&nextPageToken=…`; filterTypes: `listId, email,
  leadIds, company, …`; >1000 matches → 1003; long URI → 414 (workaround: POST
  with `_method=GET`).
- **Activities:** `GET /rest/v1/activities.json?nextPageToken=…&leadIds=…(≤30)&activityTypeIds=…`;
  `GET /rest/v1/activities/pagingtoken.json?date=…`; `GET /rest/v1/activities/types.json`;
  `GET /rest/v1/activities/leadchanges.json?nextPageToken=…`;
  `GET /rest/v1/activities/deletedleads.json?nextPageToken=…`.
- **Bulk (verified 2026-10-28 for Phase 4 — supersedes the older
  `leadFieldIds`/`leadFileContent` guesses):**
  - **Common:** paths start at `/bulk/v1/…` (NO `/rest/` prefix). FIFO queue: max 2
    concurrent / 10 total → error **1016**. Import files <10 MB. batchIds valid
    7 days; export files retained 10 days; poll status every 5–30s. Import status:
    Queued/Importing/**Complete**/Failed; extract status: Created/Queued/Processing/
    Canceled/Completed/Failed.
  - **Lead extract:** `POST /bulk/v1/leads/export/create.json` — `fields[]`
    REQUIRED; `filter` exactly ONE of: `createdAt`|`updatedAt` {startAt,endAt}
    (≤31 days, ISO without ms; updatedAt + smart-list filters subscription-gated →
    1035) or `staticListId`|`staticListName`|`smartListId`|`smartListName`; opt
    `format` CSV|TSV|SSV, `columnHeaderNames` object. → `result[0].exportId`; then
    `POST …/export/{id}/enqueue.json`, `GET …/status.json` (+numberOfRecords,
    fileSize), `GET …/file.json` (Range supported), `POST …/cancel.json`.
  - **Activity extract:** `POST /bulk/v1/activities/export/create.json` —
    `filter.createdAt` REQUIRED (≤31 days); opt `activityTypeIds[]`;
    `primaryAttributeValueIds[]` (≤50, requires activityTypeIds) XOR
    `primaryAttributeValues[]` (≤50, `<program>.<asset>` notation); opt `fields[]`
    (default 8 cols + actionResult), format, columnHeaderNames. Same pattern under
    `/bulk/v1/activities/export/`. Perm: Read-Only/Read-Write Activity.
    "Delete Lead" activity NOT supported.
  - **Lead import:** `POST /bulk/v1/leads.json?format=csv|tsv|ssv` multipart `file`;
    opt query `lookupField` (default email; `id`=update-only), `listId`,
    `partitionName` → `result[0].batchId`. Status `GET /bulk/v1/leads/batch/{id}.json`
    (**no /status suffix**); `GET …/batch/{id}/failures.json|warnings.json`.
  - **Program member import:** `POST /bulk/v1/program/{programId}/members/import.json?
    format=…&programMemberStatus=…` multipart `file` (every row needs email).
    Status/failures/warnings: `GET /bulk/v1/program/members/import/{batchId}/
    status.json|failures.json|warnings.json` (**no programId in these paths**).
  - **Custom object import:** `POST /bulk/v1/customobjects/{apiName}/import.json?
    format=…` multipart `file` → `{batchId, objectApiName}`;
    `GET /bulk/v1/customobjects/{apiName}/import/{batchId}/status.json|failures.json|
    warnings.json` (404 = no file).
  - **Doc URL patterns:** `…/marketo/rest/bulk-extract/<slug>` and
    `…/marketo/rest/bulk-import/<slug>`; pages exist, NOT shipped (P4.5 candidates):
    `bulk-custom-object-extract`, `bulk-program-member-extract`.
- **Static lists:** `POST /asset/v1/staticLists.json {name}`;
  `GET /rest/v1/lists/{listId}/leads.json` (Phase 2 docs: current API uses
  repeated `id=…` query params, max 300, for Add/Remove/Member-of-List;
  Remove is a real DELETE).
- **Programs (corrected Phase 2):** asset API `GET /rest/asset/v1/programs.json` (+byId/byName);
  `GET /rest/v1/programs/{programId}/members.json?filterType&filterValues`; Set Status (upsert)
  `POST /rest/v1/programs/{programId}/members/status.json {statusName, input:[{leadId}]}`;
  `POST /rest/v1/programs/{programId}/members.json {input:[{leadId,…}]}`;
  `POST /rest/v1/programs/{programId}/members/delete.json {input:[{leadId}]}`;
  Describe `GET /rest/v1/programs/members/describe.json` (PLURAL — old /program/ was wrong);
  `GET /rest/v1/leads/programs/{programId}.json`; `POST /rest/v1/leads/programs/{programId}/status.json` (existing members only);
  bulk member import `POST /bulk/v1/program/{programId}/members/import.json`.
- **Opportunities:** `POST /rest/v1/opportunities.json`; `GET /rest/v1/opportunities.json?opportunityIds=…`;
  roles: `POST|GET /rest/v1/opportunities/roles.json`.
- **Sales persons:** `GET|POST /rest/v1/salespersons.json`.
- **Landing pages (verified 2026-10-28, page 2026-10-07):** `GET /rest/asset/v1/
  landingPages.json` (browse: status Approved|Draft, maxReturn default 20/max 200,
  offset default 0 — standard asset pattern); `GET /rest/asset/v1/landingPage/{id}.json`;
  `…/byName.json?name=`; `…/content.json` (sections; must appear before update);
  `…/variables.json` (guided pages only — template <head> meta tags, String/Color/
  Boolean); preview = `…/fullContent.json?leadId=&segmentation=[{segmentationId,
  segmentId}]`; domains `GET /rest/asset/v1/landingPageDomains.json` (perm
  Read-Only Redirect Rules, per landing-page-redirect-rules page).
- **Emails (verified 2026-10-28, page 2026-08-07):** `GET /rest/asset/v1/emails.json`
  (status Approved|Draft, folder JSON, earliest/latestUpdatedAt, maxReturn def 20/
  max 200, offset def 0); `…/email/{id}.json`; `…/email/byName.json?name=&folder=`;
  `…/email/{id}/content.json?status=`; `…/email/{id}/variables.json` (name/value/
  moduleScope); `GET /rest/asset/v1/email/ccFields.json` (NO id — instance-wide);
  preview `…/email/{id}/fullContent.json?status=draft|approved&type=Text|HTML
  (def HTML)&leadId=` (v1.0 emails ONLY); **sendSample `POST …/email/{id}/
  sendSample.json?emailAddress=…&leadId=&textOnly=` — emailAddress is a QUERY
  param**; approve `…/approveDraft.json` (from/reply/subject must be set first);
  clone `…/clone.json` form-urlencoded {name*, folder*, description?}.
- **Forms (verified 2026-10-28, page 2026-08-07 + endpoint reference):** `GET /
  rest/asset/v1/forms.json` (status approved|approved with draft|draft, maxReturn,
  offset); `…/form/{id}.json` (EXCLUDES field list); `…/form/byName.json?name=`;
  `…/form/{id}/fields.json` (layout order; ids usable for Submit Lead Form);
  `GET /rest/asset/v1/form/fields.json` (available lead fields); `…/form/
  programMemberFields.json` (form + any LP using it must be under a Program, not
  Design Studio); `…/form/{id}/thankYouPage.json?status=approved|draft` (perm
  Read-Only Asset per endpoint ref); `…/form/{id}/usedBy.json` (LPs, smart lists,
  campaigns, reports, email programs); approve `POST …/form/{id}/approveDraft.json`
  (replaces approved version with draft); clone `…/form/{id}/clone.json`
  form-urlencoded {name*, folder*, description?}; delete `…/form/{id}/delete.json`
  (refused if in use by LPs; unapprove first); create `POST /rest/asset/v1/forms.json`
  {name*, folder*} (NOT shipped in P3).
- **Smart lists (verified 2026-10-28, page 2026-10-07):** `GET /rest/asset/v1/
  smartLists.json` (folder, maxReturn, offset, earliest/latestUpdatedAt);
  `…/smartList/{id}.json?includeRules=`; `…/smartList/byName.json?name=` (exact
  match); by program `GET /rest/asset/v1/program/{programId}/smartList.json
  ?includeRules=`; by campaign `GET /rest/asset/v1/smartCampaign/{id}/smartList
  .json?includeRules=`; clone `POST …/smartList/{id}/clone.json` form-urlencoded
  {name* (unique), folder* (program or smart-list folder), description?}; delete
  `…/smartList/{id}/delete.json`. NOTE: UI 'in' operator appears as 'is' in API.
- **Smart campaigns (verified 2026-10-28, page 2026-10-07):** `GET /rest/asset/v1/
  smartCampaigns.json` (folder, maxReturn def 20/max 200, offset def 0,
  earliest/latestUpdatedAt ISO-8601 w/o ms, isActive); `…/smartCampaign/{id}.json`;
  `…/smartCampaign/byName.json?name=`; activate `POST …/smartCampaign/{id}/
  activate.json` (perm "Activate Campaign"; must be deactivated + ≥1 trigger +
  ≥1 flow step + no errors); deactivate `…/deactivate.json` (perm "Deactivate
  Campaign"); **trigger (Request Campaign) `POST /rest/v1/campaigns/{id}/trigger
  .json`** (perm "Read-Write Campaigns") body `{"input": {"leads": [{"id": …}]
  (≤100), "tokens": [{"name":"{{my.x}}","value":"…"}] (≤100, optional)}}` — campaign
  needs 'Campaign is Requested' trigger w/ Web Service API source + active;
  schedule `POST /rest/v1/campaigns/{id}/schedule.json` (20/day cap — NOT shipped).
- **Custom objects (SHIPPED in P2 — verified 2026-08-07 page):** paths are LOWERCASE
  `/rest/v1/customobjects.json` (NOT `customObjects`); full list in catalog
  `customobjects.*` (list/describe/query/sync/delete/types/type_describe/
  add_fields/approve/data_types/linkable) + session log above.
- **Custom activity types:** CRUD under `/rest/v1/activities/external/…` (P5 stretch —
  not started).
- **Bulk (P4 — SHIPPED 2026-10-28):** all verified details in the
  "**Bulk (verified 2026-10-28 for Phase 4 …)**" bullet above (lead extract,
  activity extract, lead import, program member import, custom object import).

## Gotchas checklist (verify per call at implementation time — do NOT trust memory)

- [ ] Each catalog entry's param list must be re-verified with `web_extract` on its doc page.
- [x] Bulk endpoints: no `/rest/` in path — VERIFIED 2026-10-28 (all 5 P4 ops).
      (Download encoding: plain CSV/TSV/SSV expected — confirm on the live smoke test.)
- [ ] Field-selection params (`fields` in lead API) accept REST API names (not
      display names) — the field picker (fed by describe.json) makes this
      non-technical-friendly.
- [ ] Datetime params: ISO-8601 without milliseconds for bulk extract filters.
- [ ] Token: store expiry, refresh at ~15 min, retry once on 401.
- [ ] Windows: config path `%APPDATA%/MarketoUtility/config.json`; no secrets in repo.

## TODO (phase checklist — order: Lead → Assets → Bulk)

- [x] P0: scaffold, config store, auth manager, catalog loader, UI shell, 2 vertical-slice calls (2026-10-28)
- [x] P1: lead read endpoints + activity reads + field picker (describe-driven) (2026-10-28)
- [x] P2: lead writes (sync, push, list add/remove, program status, associate/merge, submitForm, delete) + programs/opps/sales persons/custom objects + select/repeat/values/sync modes + record grid (2026-10-28)
- [x] P3: assets (LPs, emails, forms, smart lists, smart campaigns, campaigns, snippets, segmentations) — 47 entries, form encoding + wrapKey (2026-10-28). Deferred: LP/email/snippet writes, LP templates, files, redirect rules, schedule campaign
- [x] P4: bulk extract wizards (leads, activities) + bulk imports (leads, program members, custom objects) — BUILT + TESTED 2026-10-28 (27/27 tests). NOT shipped: custom object extract + program member extract (P4.5 candidates, doc pages exist)
- [ ] P5: custom activity types (stretch)
- [x] P6.1: recent-calls log (db.record_recent/list_recent + /api/recent) + home “Recently run” strip (2026-10-28, 28/28 tests)
- [ ] P6: presets/favorites, PyInstaller exe, README + permissions guide
- [x] P6.2: toggleable dark mode — token-based themes, `color-scheme` for native controls,
      localStorage-persisted, OS-follow default, FOUC-free boot script (2026-10-28, 28/28 tests)
- [x] P6.3: secrets at rest — client secret → OS credential manager (`keyring`) with DB fallback
      + lazy migration of legacy rows; OAuth token memory-only (re-derivable); 33/33 tests (2026-10-28)
- [x] MacOS support: per-OS data dir (`~/Library/Application Support/`), dual-OS README, platform tests (2026-10-28)
- [x] Home page redesign: task hub (mockup 2) + “Browse all calls” catalog grid (mockup 1 pattern) — 2026-10-28. Old left-sidebar list replaced; detail/form page unchanged. Design record in mockups/.
