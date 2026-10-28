# Dark Mode — Plan (toggleable)

> **Status: ✅ SHIPPED 2026-10-28** — user approved the recommended defaults (OS-follow
> first visit, localStorage persistence, 2-state icon toggle). See NOTES.md session log.
> Scope: frontend only (`static/` + docs). No backend API changes in the recommended
> design. Convention: BUILT + TESTED before the NOTES.md entry.

## 1. Goal

A one-click **light/dark toggle** in the top bar that:

- re-themes **all** views (home task hub, browse grid, detail form, bulk wizard,
  modal, field pickers, tables, JSON viewer, native controls),
- **persists** the user's choice across reloads,
- defaults to the **OS preference** on first visit,
- never flashes the "wrong" theme on page load (no FOUC),
- keeps WCAG-AA text contrast in both themes.

**Non-goals:** per-profile themes, high-contrast/accessibility themes, print theme,
server-side theme endpoints (unless the user wants the SQLite option in §8).

## 2. Architecture

- Theme = `data-theme="light" | "dark"` attribute on `<html>` (set by a tiny inline
  script in `<head>` **before first paint**, then by `app.js` afterwards).
- All colors flow through CSS custom properties. Light values stay exactly as they are
  today (zero visual change to the current theme); dark = one override block.
- `color-scheme: light | dark` is set per theme so **native widgets** (select
  dropdowns, date pickers, scrollbars, form control focus rings) follow the theme —
  critical because the app uses several native `<select>`s and scrollable panels.
- Persistence: `localStorage["marketo.theme"]` (see decision D2, §8).
- Toggle = one `<button>` in `.top-actions`, wired in `bindStatic()`.

## 3. Design decisions (recommended)

| # | Decision | Recommendation | Rationale |
|---|---|---|---|
| D1 | States | **2 states** (light/dark); first-visit default = `prefers-color-scheme`; explicit choice stored | A 3-state "System" dropdown is more chrome than a non-technical user needs; OS-follow default already covers it |
| D2 | Where the choice lives | **localStorage** (client-side) | Theme is a cosmetic, per-browser preference — not Marketo/app state. Zero backend change, works offline, survives "shared server" future trivially (per-browser is the *correct* scope there). See §8 for the strict-convention alternative |
| D3 | How to re-theme | **Token overrides**, no JS re-render | All UI is class-based CSS (verified: app.js paints no inline colors except one error fallback); a CSS attribute flip is instant and can't desync from JS state |
| D4 | Toggle affordance | Icon button `🌙`/`☀️` in top bar (icon = state you'd switch **to**), `aria-pressed`, `title` tooltip | Matches existing ghost buttons; one glyph is self-explanatory |
| D5 | JSON viewer | Keep its existing dark slate (`#0f172a`) in **both** themes | It already looks right in dark; forcing a light JSON block in light mode is a regression risk with no benefit |

## 4. Palette

### 4.1 Core tokens (existing — light unchanged, dark added)

| Token | Light (today) | Dark (proposed) |
|---|---|---|
| `--bg` | `#f4f5f9` | `#0f131b` |
| `--panel` | `#ffffff` | `#161c27` |
| `--text` | `#171a21` | `#e7ebf3` |
| `--muted` | `#667085` | `#96a1b7` |
| `--line` | `#e4e7ee` | `#2a3242` |
| `--primary` | `#0f62fe` | `#4589ff` |
| `--primary-dark` *(hover token — name kept, meaning = "hover shade")* | `#0043ce` | `#6ba1ff` |
| `--green` | `#0e7a3d` | `#4cc07e` |
| `--green-bg` | `#eefaf2` | `rgba(76,192,126,.15)` |
| `--amber` | `#9a5b00` | `#ffb454` |
| `--red` | `#c2272d` | `#ff8b86` |
| `--red-bg` | `#fdf1f1` | `rgba(255,139,134,.13)` |
| `--shadow` | `0 1px 2px rgba(16,24,40,.06), …` | `0 1px 2px rgba(0,0,0,.4), 0 1px 3px rgba(0,0,0,.5)` |
| (new) `color-scheme` | `light` | `dark` |

Contrast spot-checks (dark): `--muted` on `--panel` ≈ 6.6:1, `--primary` on
`--panel` ≈ 5.1:1, status greens/ambers/reds ≥ 7:1 — all AA+ for body text.

### 4.2 New tokens — audit of every hardcoded color in styles.css

Each hardcoded hex below becomes a token; light value = current value (no visual
change), dark value = new. Grouped by hue so the implementation is mechanical.

**Neutrals / surfaces**

| New token | Light | Dark | Replaces (selector) |
|---|---|---|---|
| `--text-2` | `#3a4356` | `#c9d2e0` | `.hintline b`, `.tab`, `.kv.func`, `.task .link`, `.lede` (`#333c4d`) |
| `--text-3` | `#4a5468` | `#a8b3c9` | `.kv.use em` |
| `--code-bg` | `#eef0f6` | `#232b3d` | `code`, `.tab .count`, `.wiz-step .n` |
| `--panel-2` | `#f8f9fc` | `#1d2432` | `.lk-search`, `table th` (×2), `.count-box`, `.sync-wrap`, `details.optional` (`#fcfcfe`) |
| `--hover` | `#f3f5fa` | `#202839` | `button.ghost:hover`, `.lk-row:hover` (`#f1f3f9`) |
| `--line-soft` | `#c3c9d6` | `#3d475e` | `.hintline .sep`, `.crumbs .sep` |
| `--line-idle` | `#b9c6e8` | `#46568a` | `.job-banner.s-created/.s-queued` |
| `--line-hover` | `#c9d2e4` | `#39445c` | `.tab:hover` |
| `--line-soft2` | `#f0f1f5` | `#232b3d` | `.home-results .hr` border, `table td` borders (×2) |
| `--state-idle-bg` | `#eef1f8` | `#242d40` | `.state-created/.state-queued` |
| `--state-idle-text` | `#55607a` | `#a6b1c8` | `.state-created/.state-queued` |

**Blue tints (primary family)**

| New token | Light | Dark | Replaces (selector) |
|---|---|---|---|
| `--tint-1` | `#f8faff` | `rgba(69,137,255,.08)` | `.hr-foot` |
| `--tint-2` | `#f5f8ff` | `rgba(69,137,255,.12)` | `.hr:hover`, `.chip:hover`, `.prow.active` |
| `--tint-3` | `#eef3fe` | `rgba(69,137,255,.17)` | `.task-ic`, `.callout`, `.state-processing`, `.wiz-step.active`, `.hr-foot:hover` |
| `--tint-line` | `#d6e2fc` | `rgba(69,137,255,.38)` | `.task-ic` border, `.callout` border, `.wiz-step.active` border (`#c9dbfc`) |
| `--primary-line` | `#b9cdf7` | `#35549f` | `.task:hover` border, `.chip:hover` border, `.task:hover .link` border (`#cdd9f2`) |
| `--primary-soft` | `#3f6ad1` | `#79aaff` | `.klabel.func`; **repoint** `.state-processing` color (today `var(--primary-dark)`) |

**Status hues (green / amber / red)**

| New token | Light | Dark | Replaces (selector) |
|---|---|---|---|
| `--green-line` | `#cdeeda` | `rgba(76,192,126,.45)` | `.badge.method-get` border |
| `--amber-bg` | `#fdf5e6` | `rgba(255,180,84,.15)` | `.badge.method-post` bg, `.state-canceled` bg, **and fixes `.m-post` (currently references `var(--amber-bg)` which is never defined → chip renders with no background)** |
| `--amber-line` | `#f0dcb2` | `rgba(255,180,84,.45)` | `.badge.method-post` border |
| `--amber-tint` | `#fffaf0` | `rgba(255,180,84,.08)` | `.callout.notes` bg |
| `--amber-tint-line` | `#f0e2bd` | `rgba(255,180,84,.28)` | `.callout.notes` border |
| `--red-line` | `#f2c6c8` | `rgba(255,139,134,.45)` | `.badge.method-delete` border, `.b-warn` border, `.error-box` border, `.chip.bad` border |
| `--red-text` | `#7f1d21` | `#ffb0ac` | `.error-box` color |

**Pure-`#fff` backgrounds** → replace with existing `var(--panel)`:
`#profile-select`, `#home-search`, `#browse-search`, `.home-results`, `.b-perm`,
`.badge`, `button.ghost`, `.field input/select/textarea`, `.modal-card`, `.lookup`,
`.sync-addcol input`, `.sync-table-wrap`, `.wiz-step`. (UA-default form-control
backgrounds get explicit `background: var(--panel)` where not already set, so dark
mode never shows browser-default white fields.)

**Keep as-is in both themes:** `.json-view, .raw pre` (`#0f172a`/`#d7e1f5`) and
`.modal` overlay `rgba(15,20,35,.45)`.

## 5. Implementation steps

**Step 1 — `marketo_util/static/styles.css`**
1. Add the light values of every new token to `:root` (incl. the missing `--amber-bg`).
2. Add `:root { color-scheme: light; }` and a single
   `:root[data-theme="dark"] { … }` block overriding §4.1 core tokens + §4.2 dark
   values + `--shadow` + `color-scheme: dark`.
3. Mechanical pass: replace every hardcoded hex from the audit tables with its token.
4. Add small `.iconbtn` styling (or reuse `button.ghost` — prefer reusing, zero new CSS).

**Step 2 — `marketo_util/static/index.html`**
1. Inline boot script in `<head>` (before the stylesheet link), so the attribute is
   set before first paint:
   ```html
   <script>
     (function () {
       var t = null;
       try { t = localStorage.getItem("marketo.theme"); } catch (e) {}
       if (t !== "light" && t !== "dark")
         t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
       document.documentElement.dataset.theme = t;
     })();
   </script>
   ```
2. Toggle button in `.top-actions` (after "Manage connections"):
   ```html
   <button id="btn-theme" class="ghost" type="button" aria-pressed="false"
           title="Switch to dark mode"><span class="t-ic">🌙</span></button>
   ```

**Step 3 — `marketo_util/static/app.js`** (new ~35-line "theme" section near the top;
call `initTheme()` as the first line of `bindStatic()`):
```js
/* ================= theme (light/dark toggle) ================= */
const THEME_KEY = "marketo.theme";
function systemTheme() {
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function savedTheme() {
  try { const s = localStorage.getItem(THEME_KEY);
        return s === "light" || s === "dark" ? s : null; } catch { return null; }
}
function currentTheme() { return savedTheme() || systemTheme(); }
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const b = $("#btn-theme");
  if (b) {
    b.setAttribute("aria-pressed", String(t === "dark"));
    b.querySelector(".t-ic").textContent = t === "dark" ? "☀️" : "🌙";
    b.title = t === "dark" ? "Switch to light mode" : "Switch to dark mode";
  }
}
function initTheme() {
  applyTheme(currentTheme());
  $("#btn-theme").addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    try { localStorage.setItem(THEME_KEY, next); } catch {}
    applyTheme(next);
  });
  // keep following the OS until the user makes an explicit choice
  try {
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
      if (!savedTheme()) applyTheme(e.matches ? "dark" : "light");
    });
  } catch {}
}
```
4. Optional polish: the one inline color in `app.js` (error fallback `color:#c2272d`)
   → `color:var(--red)`.

**Step 4 — verify** (see §7) and **Step 5 — docs** (see §9).

## 6. Gotchas / edge cases

- **Native controls**: without `color-scheme`, selects/scrollbars stay white in dark
  mode — this is the #1 "did it break?" complaint source. Covered by §3/Step 1.2.
- **`--primary-dark` is a hover token**, not a "dark theme" token — in dark mode its
  value gets *lighter* (`#6ba1ff`). Don't rename (would churn call sites).
- **`.state-processing`** uses `var(--primary-dark)` for text on `#eef3fe` bg —
  repoint to `--primary-soft` on `--tint-3` (both tokens defined above) or the combo
  inverts badly in dark.
- **`.m-post` latent bug**: `var(--amber-bg)` is used but never defined — fixing it
  in light mode too (chip gains its intended amber tint).
- **Emoji toggle glyphs** render fine in both themes; no SVG needed.
- **No JS re-render on toggle** — the JSON viewer, pickers, and wizard all pick up
  new colors automatically because everything is class-based (verified in app.js).
- **PyInstaller later**: `localStorage` lives in the browser profile, unaffected.

## 7. Verification / acceptance criteria

- [ ] Toggle flips all 3 views + modal + wizard + pickers + tables instantly, no reload.
- [ ] Choice survives reload (localStorage); first visit follows OS; changing OS theme
      re-applies only while no explicit choice exists.
- [ ] No flash of the wrong theme on load (boot script) — check via hard reload.
- [ ] Manual pass in **both** themes: home (hero, search dropdown, task cards, recent
      chips, browse CTA), browse (tabs, cards, empty state), detail (badges, callouts,
      required/optional fields, field picker, sync grid, run row, JSON response, error
      box, raw view), bulk wizard (steps, job banner, state badges, counts, job history
      table), connections modal, native `<select>` dropdown + scrollbars.
- [ ] No white-on-white / dark-on-dark leftovers (grep for `#[0-9a-f]{3,6}` in
      styles.css → only allowed: palette values inside `:root` blocks, `--json-*`,
      modal overlay).
- [ ] `node --check static/app.js` clean; `pytest -q` still 28/28 (no backend touched,
      but suite is cheap insurance).
- [ ] Keyboard: toggle reachable, `aria-pressed` flips, tooltip correct.

## 8. Open questions for the user (defaults = recommendations)

1. **Default theme**: follow the OS on first visit (recommended) vs. always start light?
2. **Persistence**: `localStorage` (recommended) vs. a SQLite `settings` table +
   `GET/PUT /api/settings` (strict "all mutable state in SQLite" convention; costs a
   small backend + route + test, and is per-DB rather than per-browser — arguably the
   *wrong* scope for a cosmetic preference, but fully aligned if the user prefers it).
3. **Toggle shape**: simple 2-state icon button (recommended) vs. a 3-option
   Light/System/Dark control?

## 9. Docs updates (at implementation time, per project convention)

- `README.md` — one line in features/usage: "Appearance: toggle dark mode in the top
  right; your choice is remembered."
- `NOTES.md` — dated session entry (files changed, audit count, bug fix `.m-post`,
  verification results, suite count).
- `PLAN.md` — add + check the polish item:
  `- [x] P6.2: toggleable dark mode (token-based, persisted, FOUC-safe) (date)`.

## 10. Effort estimate

One focused session. ≈ 250–300 lines of CSS edits (mostly mechanical token swaps),
≈ 40 lines of JS, ≈ 10 lines of HTML, plus the manual dual-theme verification pass.
No schema, route, or catalog changes in the recommended design.
