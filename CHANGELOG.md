# Changelog

User-facing changes, newest first. Dates are the day the release was prepared.

## 0.1.3 — 2026-11-11

### New
- **Saved field sets** — save the fields you tick together (e.g.
  `new_lookupfieldid`, `email`, `firstName`, `lastName`) under a name like
  "Core identity", then apply it in one click from any call's field picker
  (e.g. *Get Leads by Filter Type → Fields to return*). Sets are kept
  **per connection**. The request sent to Marketo is exactly the same as if
  you'd ticked the fields by hand.
  - Manage page (home card or search "field sets"): see each set's fields,
    **rename**, **delete**, **↑/↓ reorder** them.
  - **⤓ Export JSON** / **⤒ Import** (file or pasted JSON) — that's how you
    copy sets between connections.
  - A field in a set that isn't in the saved field library is flagged
    "not in library" (it would be sent to Marketo as-is).
- **Every screen has its own address** — the address bar always shows where
  you are, so you can copy it, paste it, or send it to a colleague:
  - Home → `/#/` · Catalog browser → `/#/browse` (or `/#/browse/Leads`)
  - A call page, e.g. *Get Leads by Filter Type* → `/#/call/leads-get-by-filter`
  - A bulk wizard, e.g. *Extract leads* → `/#/bulk/extract-leads`
  - Lead field library → `/#/fields` · Saved field sets → `/#/fieldsets`
  - Pasting such an address opens that screen directly; the browser's
    **Back / Forward** work as usual. The address names a *screen*, not your
    data.
- **Friendlier addresses (no dots)** — screen addresses use dashes:
  `/#/call/leads-get-by-filter` instead of `/#/call/leads.get_by_filter`.
  Old dotted addresses still open the right screen and the bar quietly
  corrects itself to the new form.
- **Field library reads like a reference** — each field is shown as
  label → value rows: **Display name**, **API name**, and **Type** in
  Marketo's own names (*String, Float, Reference, Lead Function*, …), with
  **Custom / System** and **Read-only** badges and a per-row **Copy** button.
  **⧉ Copy CSV** now includes a friendly `type` column alongside the raw
  `data_type`.

### Developer
- **`python run.py` works with any Python now** — if you start it with a
  Python that's missing the app's packages (e.g. the system Python), it
  automatically re-starts under the app's own `.venv` environment instead of
  dying with `ModuleNotFoundError: No module named 'uvicorn'`; if the
  environment is genuinely broken you get plain-English instructions.

## 0.1.2 — 2026-11-02

### New
- **System-tray icon** — while it's running the app shows a small icon in
  the taskbar's notification area with **Open RESTEasy**, **Copy URL**, and
  **Quit RESTEasy**.
- **Version number** — shown in the app (via the health endpoint) and in
  the Windows exe's file properties (Details tab).
- **Code-signing support (optional)** — when signing certificates are
  configured in the build secrets, the Windows exe is Authenticode-signed
  (verified, with RFC 3161 timestamp) and the macOS app gets a Developer ID
  signature + notarization. Without them the builds ship unsigned and the OS
  warns users.

### Fixed
- **Release pipeline** — the PyInstaller build no longer silently ships an
  exe that dies at startup ("Could not import module"): the spec now pins
  the project root onto the build path and fails loudly if any
  `marketo_util` module is missing from the bundle.
- README: added the community-project disclaimer (not affiliated with or
  endorsed by Adobe); removed internal test documentation.

## 0.1.1 — 2026-10-29

First public release.

- The complete app: catalog browser, call pages with field pickers, bulk
  import/export wizards, home task hub with search + popular tags,
  connections manager, settings (export location + call logging), and
  one-click exports (JSON/CSV) for every call.
- **Desktop builds** — single-file Windows `.exe` and macOS `.app`
  (Apple Silicon); no Python needed to run.
- **Access-token visibility + auto-refresh** — the connections dialog shows
  the token's fetch time, expiry, scope, and a masked token; expired or
  invalid tokens (Marketo 601/602) are refreshed automatically.
- **Lead field library view** — browse every saved field (search,
  custom/system filter, copy API name, copy CSV).
- **Two-pane call page** — form and response side by side.
