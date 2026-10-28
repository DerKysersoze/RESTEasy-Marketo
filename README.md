# Marketo REST Utility

A local, non-technical-friendly client for the **Marketo Engage REST API**.
Browse a catalog of API calls, fill in a friendly form, and get the data back
as pretty JSON or CSV — no JSON crafting, no token juggling.

> **Reporting is not available via the Marketo REST API.** This tool covers
> leads, activities, assets, programs, opportunities, and bulk data. For
> reports, use the Marketo UI export or the SOAP reporting API.

## Quick start

**Windows**

```bat
:: 1. Install dependencies (Python 3.11+)
pip install -r requirements.txt

:: 2. Run it (opens http://127.0.0.1:8787 in your browser)
python run.py
```

**macOS / Linux**

```sh
# 1. (once) fresh venv — a venv created on another OS won't run here
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Run it (opens http://127.0.0.1:8787 in your browser)
python run.py
```

1. Click **Manage connections** (top-right).
2. Add your **instance** (e.g. `100-AEK-913`), **client ID**, and **client
   secret** — found in Marketo *Admin Center → Integrations → API Keys &
   Access Tokens*.
3. Click **Test connection**, then pick a task on the home page (or search for
   a call — try *Describe Leads*, it needs no fields) and hit **Run call**.
   “Browse all calls” opens the full catalog, grouped by object.
4. **Appearance:** toggle dark mode with the 🌙 button in the top bar — your choice
   is remembered in this browser (first visit follows your OS setting).

Your connection is stored locally, per platform:

| OS | Location |
|---|---|
| Windows | `%APPDATA%\MarketoUtility\marketo_util.db` |
| macOS | `~/Library/Application Support/MarketoUtility/marketo_util.db` |
| Linux | `~/.local/share/marketo-utility/marketo_util.db` |

**Where your credentials actually live:**

- **Client secret** — in the OS credential manager (Windows Credential
  Manager, macOS Keychain, or Linux Secret Service) when one is available;
  otherwise it falls back to the database above. Deleting a connection also
  removes the credential-manager entry.
- **OAuth access token** — in memory only (20-minute token, auto-refreshed);
  never written to disk.
- **Munchkin ID / cookie** — never stored anywhere; it's only ever entered
  as input to the *Associate Lead (cookie)* call.

## Lead field library

So you never have to type lead-field names from memory:

- On the **home page**, open the **Lead field library** card and click
  **Refresh**. The utility calls the *Describe/Browse Leads* schema once, stores
  every field for that connection, and tags each as **custom** (your team's
  field) or **system** (Marketo standard), with a **read-only** chip where it
  applies.
- Any call that accepts lead fields (e.g. *Get Lead by ID → fields to return*,
  *Sync Leads* columns) then shows a **picker**: tick the fields you want, filter
  **All / Custom only / System only**, and custom fields sort to the top. The
  picker loads from the stored library — no extra API call after the first load.
- **Clear** wipes the stored library for that connection; the next picker use
  re-fetches it automatically.

> The “custom vs system” tag comes from the Browse endpoint's `isCustom` flag.
> When only `describe.json` is available (fallback), a field's tag shows as
> unknown rather than guessing.

## Settings, export location & call logging

Open **⚙️ Settings** in the top bar.

- **Where files are saved** — set the folder where this utility writes files
  (default: `…\MarketoUtility\exports` on Windows). **Test** checks the folder
  exists and is writable; **Use default** restores the default. The folder is
  created automatically when you save.
- **Log every call** *(off by default)* — when on, each call you run and
  Marketo's response are appended to one JSON-lines file **per day** in that
  folder: `marketo-calls-YYYY-MM-DD.jsonl`. A log-write failure never breaks
  the call itself. **Clear saved call logs** (same dialog) deletes the
  `marketo-calls-*.jsonl` files.
- **Also save bulk export files here** *(off by default, independent of the
  log)* — when on, downloading a bulk extract/import file also writes a copy
  to that folder, so big CSVs don't only live in your browser's Downloads.

**Error codes, translated.** When a Marketo response carries an error code
(606 rate limit, 1003 invalid data, 607 daily quota, …), the response viewer
shows the code badge, the **official description** from Adobe's error-codes
docs, and a **View in Marketo docs** link. The reference table lives in
`catalog/error_codes.json` (48 codes, verified from the official page).

## How it works

- **`catalog/`** — one JSON file per Marketo call. This drives the entire UI.
  Adding a call = adding a file (see `catalog/schema.json` for the format).
- **`marketo_util/`** — FastAPI backend: auth (auto token refresh), call
  building, rate-limit gate, plain-English error translation.
  Client secrets are kept in the OS credential manager (`secrets.py`),
  never in plaintext on disk when one is available.
- The UI is served from `marketo_util/static/` (vanilla JS, no build step).

## Project docs

- [`PLAN.md`](PLAN.md) — roadmap, architecture, phases, decisions
- [`NOTES.md`](NOTES.md) — verified API facts, gotchas, session log

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MARKETO_UTIL_HOST` | `127.0.0.1` | Bind address |
| `MARKETO_UTIL_PORT` | `8787` | Port |
| `MARKETO_UTIL_DB` | OS app-data dir (see table above) | SQLite location |
| `MARKETO_UTIL_CATALOG` | `./catalog` | Catalog directory |
| `MARKETO_UTIL_NO_BROWSER` | – | Set to `1` to not auto-open the browser |

## Tests

```bat
pytest -q
```
