/* RESTEasy-Marketo — UI logic (vanilla JS, no build step).
   Everything is rendered from the catalog served at /api/catalog. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function req(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) {
    const msg =
      (data && data.error && data.error.friendly) ||
      (data && (data.detail || data.message)) ||
      `Request failed (HTTP ${r.status})`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

const state = {
  catalog: null,
  activeCall: null,
  profiles: [],
  running: false,
  lastResult: null,
  editingProfileId: null,
  view: "home",
  backTo: "home", // where the ← button on the detail view goes
  browse: { group: "__all", q: "" },
  recent: [],
  bulk: { ops: [], activeOp: null, job: null, pollTimer: null },
};

/* ================= catalog & views ================= */

async function loadCatalog() {
  state.catalog = await req("GET", "/api/catalog");
}

/* ================= views: home (task hub) / browse (catalog) / detail ================= */

function showView(name) {
  state.view = name;
  for (const v of ["home", "browse", "fields", "detail"]) {
    document.getElementById("view-" + v).classList.toggle("hidden", v !== name);
  }
}

function goHome() {
  state.backTo = "home";
  showView("home");
  renderHome();
  document.getElementById("view-home").scrollTo({ top: 0 });
}

function goBrowse(group) {
  state.backTo = "browse";
  if (group) state.browse.group = group;
  showView("browse");
  renderBrowse();
  document.getElementById("view-browse").scrollTo({ top: 0 });
}

/* ---- task definitions (call ids resolved against the live catalog) ---- */
const TASKS = [
  { icon: "\u{1F50D}", title: "Find a lead",
    desc: "Look up one person or a set of leads by email, ID, or any field — and see their data.",
    calls: ["leads.get_by_id", "leads.get_by_filter", "leads.describe2"], groups: ["Leads"] },
  { icon: "\u{1F4E4}", title: "Pull lead data",
    desc: "Export lead data as JSON or CSV — small pulls now, big exports as a background job.",
    calls: ["leads.get_by_filter", "programs.members", "bulk.extract_leads"], groups: ["Leads", "Bulk Extract"] },
  { icon: "\u{1F3AF}", title: "Work with programs",
    desc: "See who's in a program, at what status — and move people in and out.",
    calls: ["programs.list", "programs.members", "leads.change_program_status"], groups: ["Programs"] },
  { icon: "\u{1F4CB}", title: "Lists & campaigns",
    desc: "Add or remove people from lists, run a smart campaign, check its state.",
    calls: ["lists.add", "smartcampaigns.trigger", "smartcampaigns.list"], groups: ["Static Lists", "Lists & Campaigns"] },
  { icon: "\u2709\uFE0F", title: "Emails & landing pages",
    desc: "Inspect the HTML, check variables, and send a real sample to test.",
    calls: ["emails.list", "emails.content", "emails.send_sample"], groups: ["Emails", "Landing Pages"] },
  { icon: "\u{1F5C2}\uFE0F", title: "Custom data",
    desc: "Query or sync your custom objects — the extra record types beyond leads.",
    calls: ["customobjects.query", "customobjects.sync", "customobjects.list"], groups: ["Custom Objects"] },
  { icon: "\u{1F4E6}", title: "Bulk import & export",
    desc: "Move big volumes in and out — thousands of rows via background jobs.",
    calls: ["bulk.import_leads", "bulk.import_program_members", "bulk.extract_activities"], groups: ["Bulk Import", "Bulk Extract"] },
  { icon: "\u{1F9FE}", title: "Forms & opportunities",
    desc: "Form definitions and fields; deals linked to leads.",
    calls: ["forms.list", "forms.available_fields", "opportunities.get"], groups: ["Forms", "Opportunities"] },
];

function allCalls() {
  const out = [];
  for (const g of state.catalog.groups) for (const c of g.calls) out.push(c);
  return out;
}

/* P7b — the field library is a first-class entry in home search (it opens a
   view, not a call — so it needs no catalog/bulk entry behind it). */
const FIELD_LIBRARY_ENTRY = {
  id: "__field_library",
  name: "Lead field library — view all saved fields",
  group: "Field library",
  view: "fields",
};

/* Home search space: the field library, catalog calls, PLUS the bulk wizards
   (P9 — bulk ops were previously invisible to the home search box, so e.g.
   “extract leads” died). */
function searchPool() {
  return [FIELD_LIBRARY_ENTRY].concat(allCalls()).concat(state.bulk.ops || []);
}

function resolveCall(id) {
  if (id === FIELD_LIBRARY_ENTRY.id) return FIELD_LIBRARY_ENTRY;
  return allCalls().find((c) => c.id === id) || state.bulk.ops.find((o) => o.id === id) || null;
}

function openEntry(entry, from) {
  if (entry.view === "fields") { goFields(); return; }
  if (entry.method) selectCall(entry, from);
  else selectBulkOp(entry, from);
}

function badgeFor(entry) {
  if (entry.view === "fields") return { txt: "LIB", cls: "m-get" };
  if (entry.method) return { txt: entry.method, cls: entry.method === "GET" ? "m-get" : "m-post" };
  return entry.kind === "extract" ? { txt: "BULK", cls: "m-get" } : { txt: "BULK", cls: "m-post" };
}

/* ---- home: task cards ---- */
function renderHome() {
  const tasksEl = document.getElementById("tasks");
  tasksEl.innerHTML = "";
  // P7 — Lead field library card (first slot, per approved mockup 5)
  const fld = document.createElement("div");
  fld.className = "task fld";
  fld.dataset.t = "lead field library fields custom system refresh load describe picker";
  fld.innerHTML = `
    <span class="newtag">FIELD LIBRARY</span>
    <div class="task-ic">📇</div>
    <div><h3>Lead field library</h3></div>
    <p>Every lead field in your Marketo, saved on this machine — so calls that take field names (Get Lead, Sync, Bulk Extract…) pick from a stored, tagged list instead of re-asking Marketo.</p>
    <div class="fld-stats" id="fld-stats"><span class="muted">Loading…</span></div>
    <div class="fld-actions">
      <button type="button" class="primary" id="fld-refresh">↻ Refresh field list</button>
      <button type="button" class="ghost" id="fld-view">View all fields →</button>
      <button type="button" class="ghost small" id="fld-clear" hidden>Clear</button>
    </div>`;
  fld.querySelector("#fld-refresh").addEventListener("click", () => refreshFieldList(fld));
  fld.querySelector("#fld-view").addEventListener("click", () => goFields());
  fld.querySelector("#fld-clear").addEventListener("click", async () => {
    if (!confirm("Clear the stored field list? Pickers will re-load it on next use — or click Refresh.")) return;
    try {
      await req("DELETE", "/api/fields");
      loadFieldCard(fld);
    } catch (e) {
      showFieldError(fld, e.message);
    }
  });
  tasksEl.appendChild(fld);
  for (const t of TASKS) {
    const calls = t.calls.map(resolveCall).filter(Boolean);
    if (!calls.length) continue;
    const card = document.createElement("div");
    card.className = "task";
    card.dataset.t = (t.title + " " + t.desc + " " + calls.map((c) => c.name).join(" ")).toLowerCase();
    card.innerHTML = `
      <div class="task-ic">${t.icon}</div>
      <div><h3>${esc(t.title)}</h3></div>
      <p>${esc(t.desc)}</p>
      <div class="links">${calls.slice(0, 4).map((c) => {
        const b = badgeFor(c);
        return `<span class="link" data-id="${esc(c.id)}">
          <span class="m ${b.cls}">${esc(b.txt)}</span>${esc(c.name)}<span class="arr">→</span></span>`;
      }).join("")}</div>
      <div class="more" data-more="${esc((t.groups || [])[0] || "")}">All calls in this area →</div>`;
    for (const link of card.querySelectorAll(".link")) {
      link.addEventListener("click", () => {
        const e = resolveCall(link.dataset.id);
        if (e) openEntry(e, "home");
      });
    }
    card.querySelector(".more").addEventListener("click", () => goBrowse(card.querySelector(".more").dataset.more));
    tasksEl.appendChild(card);
  }
  renderRecentChips();
  filterHomeTasks();
  loadFieldCard(fld);
}

/* ---- P7: lead field library card ---- */
function fmtAgo(ts) {
  if (!ts) return "never";
  const norm = ts.endsWith("Z") || ts.includes("+") ? ts : ts + "Z";
  const then = new Date(norm);
  if (isNaN(then.getTime())) return "recently";
  const s = Math.max(0, (Date.now() - then.getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
}

function fieldStatsHtml(st) {
  return `<span><b>${st.count.toLocaleString()}</b> fields</span> ·
    <span><b class="t-custom">${st.custom.toLocaleString()}</b> custom</span> ·
    <span><b class="t-system">${st.system.toLocaleString()}</b> system</span>${
      st.unknown ? ` · <span><b>${st.unknown.toLocaleString()}</b> untagged</span>` : ""
    } · <span>loaded <b>${esc(fmtAgo(st.lastLoaded))}</b></span>`;
}

function showFieldError(card, msg) {
  card.querySelector("#fld-stats").innerHTML = `<span style="color:var(--red)">${esc(msg)}</span>`;
}

async function loadFieldCard(card) {
  const stats = card.querySelector("#fld-stats");
  try {
    const data = await req("GET", "/api/fields");
    const st = data.stats;
    stats.innerHTML = st.count > 0
      ? fieldStatsHtml(st)
      : `<span class="muted">Not loaded yet — click <b>Refresh field list</b> to pull and store it from Marketo.</span>`;
    card.querySelector("#fld-clear").hidden = st.count === 0;
  } catch (e) {
    stats.innerHTML = `<span class="muted">${esc(e.message)}</span>`;
  }
}

async function refreshFieldList(card) {
  const btn = card.querySelector("#fld-refresh");
  if (btn.disabled) return;
  btn.disabled = true;
  btn.textContent = "⏳ Loading from Marketo…";
  try {
    const data = await req("POST", "/api/fields/refresh");
    card.querySelector("#fld-stats").innerHTML = fieldStatsHtml(data.stats);
    card.querySelector("#fld-clear").hidden = false;
  } catch (e) {
    showFieldError(card, e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ Refresh field list";
  }
}

/* ---- P7b: field library view — browse ALL saved fields (no call needed) ----
   Served entirely by GET /api/fields (the stored library) — zero Marketo
   calls unless the user explicitly Refreshes from Marketo. */

const fldsState = { fields: null, stats: null, tag: "all", q: "", shown: [] };

function goFields() {
  state.backTo = "home";
  showView("fields");
  renderFieldsView();
  document.getElementById("view-fields").scrollTo({ top: 0 });
}

async function renderFieldsView() {
  const statsEl = $("#flds-stats");
  const listEl = $("#flds-list");
  statsEl.innerHTML = '<span class="muted">Loading…</span>';
  listEl.innerHTML = '<div class="empty">Loading field library…</div>';
  try {
    const data = await req("GET", "/api/fields");
    fldsState.fields = data.fields || [];
    fldsState.stats = data.stats;
  } catch (e) {
    statsEl.innerHTML = "";
    fldsErrorState(listEl, e.message);
    return;
  }
  statsEl.innerHTML = fldsState.stats.count > 0 ? fieldStatsHtml(fldsState.stats) : "";
  renderFldsRows();
}

function fldsErrorState(el, msg) {
  const noConn = /no marketo connection/i.test(msg);
  el.innerHTML = `
    <div class="empty">
      <div class="flds-empty-title">${noConn ? "No connection to load fields from" : "Couldn't load the field library"}</div>
      <div>${esc(msg)}</div>
      ${noConn
        ? '<button type="button" class="primary flds-open-conn" style="margin-top:12px">Manage connections</button>'
        : '<button type="button" class="primary flds-retry" style="margin-top:12px">↻ Try again</button>'}
    </div>`;
  const openConn = el.querySelector(".flds-open-conn");
  if (openConn) openConn.addEventListener("click", () => {
    clearProfileForm();
    $("#modal").classList.remove("hidden");
    loadConnStatus();
  });
  const retry = el.querySelector(".flds-retry");
  if (retry) retry.addEventListener("click", renderFieldsView);
}

function renderFldsRows() {
  const listEl = $("#flds-list");
  const countEl = $("#flds-count");
  if (!fldsState.fields) return;

  if (!fldsState.fields.length) {
    fldsState.shown = [];
    countEl.textContent = "0 fields";
    listEl.innerHTML = `
      <div class="empty">
        <div class="flds-empty-title">No fields saved yet for this connection</div>
        <div>Load them from Marketo — one call, stored on this machine, and every picker in the app uses the saved copy afterwards.</div>
        <button type="button" class="primary flds-load" style="margin-top:12px">↻ Load from Marketo</button>
      </div>`;
    const b = listEl.querySelector(".flds-load");
    if (b) b.addEventListener("click", () => refreshFieldsView({ btn: b }));
    return;
  }

  const q = fldsState.q.trim().toLowerCase();
  const rows = [];
  for (const f of sortFieldOptions(fldsState.fields)) {
    const tagOf = f.isCustom === true ? "custom" : f.isCustom === false ? "system" : "untagged";
    if (fldsState.tag !== "all" && tagOf !== fldsState.tag) continue;
    const hay = `${f.label} ${f.name} ${f.dataType || ""} ${f.description || ""}`.toLowerCase();
    if (q && !hay.includes(q)) continue;
    rows.push(f);
  }
  fldsState.shown = rows; // what Copy CSV exports (current filter + search)
  countEl.textContent = `${rows.length.toLocaleString()} of ${fldsState.fields.length.toLocaleString()} fields`;

  if (!rows.length) {
    listEl.innerHTML = `<div class="empty">No fields match ${q ? `<b>“${esc(q)}”</b>` : "this filter"}.<br>
      <span style="font-size:12.5px">Try a different search, or switch the All / Custom / System filter.</span></div>`;
    return;
  }

  listEl.innerHTML = '<div class="flds-card"></div>';
  const card = listEl.querySelector(".flds-card");
  for (const f of rows) {
    const row = document.createElement("div");
    row.className = "flds-row";
    row.innerHTML = `
      <span class="flds-key">Name</span>
      <span class="flds-label">${esc(f.label)}</span>
      <span class="flds-key">API</span>
      <code class="flds-api">${esc(f.name)}</code>
      ${f.dataType ? `<span class="flds-dt">${esc(f.dataType)}</span>` : ""}
      <span class="flds-badges">${fieldBadgesHtml(f)}</span>
      <button type="button" class="ghost small flds-copy" title="Copy the API name to paste into a call">Copy</button>
      ${f.description ? `<div class="flds-desc">${esc(f.description)}</div>` : ""}`;
    row.querySelector(".flds-copy").addEventListener("click", (e) => copyText(f.name, e.currentTarget));
    card.appendChild(row);
  }
}

async function refreshFieldsView(opts = {}) {
  const headerBtn = $("#flds-refresh");
  const busy = opts.btn || headerBtn;
  if (busy.disabled) return;
  busy.disabled = true;
  const old = busy.textContent;
  busy.textContent = "⏳ Loading from Marketo…";
  try {
    await req("POST", "/api/fields/refresh");
    await renderFieldsView();
  } catch (e) {
    if (opts.btn) fldsErrorState($("#flds-list"), e.message); // nothing stored yet — show it in the list
    else $("#flds-stats").innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
  } finally {
    busy.disabled = false;
    busy.textContent = old;
  }
}

/* ---- Copy CSV: export the fields shown (current filter/search) ---- */
function csvCell(v) {
  const s = String(v == null ? "" : v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function fieldsToCsv(fields) {
  const tri = (v) => (v === true ? "yes" : v === false ? "no" : "unknown");
  const lines = ["api_name,display_name,data_type,custom,read_only,hidden,sensitive,description"];
  for (const f of fields) {
    lines.push([
      f.name, f.label, f.dataType || "",
      tri(f.isCustom), tri(f.readOnly), tri(f.isHidden), tri(f.isSensitive),
      f.description || "",
    ].map(csvCell).join(","));
  }
  return lines.join("\r\n");
}

function copyFieldsCsv() {
  const btn = $("#flds-copycsv");
  if (!fldsState.shown.length) {
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "Nothing to copy";
    setTimeout(() => { btn.textContent = old; btn.disabled = false; }, 1400);
    return;
  }
  copyText(fieldsToCsv(fldsState.shown), btn);
}

/* ---- copy field API names to the clipboard ---- */
function copyText(text, btn) {
  const old = btn ? btn.textContent : "";
  const set = (t) => { if (btn) btn.textContent = t; };
  const ok = () => { set("✓ Copied"); setTimeout(() => set(old), 1200); };
  const fail = () => { set("Copy failed"); setTimeout(() => set(old), 1600); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(ok, fail);
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let done = false;
    try { done = document.execCommand("copy"); } catch { done = false; }
    ta.remove();
    done ? ok() : fail();
  }
}

/* ---- home: hero search (live results + task dimming) ---- */
function filterHomeTasks() {
  const q = (document.getElementById("home-search").value || "").trim().toLowerCase();
  for (const card of document.querySelectorAll("#tasks .task")) {
    card.classList.toggle("dim", !!q && !card.dataset.t.includes(q));
  }
}

function onHomeSearch() {
  filterHomeTasks();
  const q = (document.getElementById("home-search").value || "").trim().toLowerCase();
  const box = document.getElementById("home-results");
  if (!q) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  const match = (c) => (c.name + " " + (c.summary || "") + " " + (c.whenToUse || "") + " " + (c.group || "")).toLowerCase().includes(q);
  const pool = searchPool();
  const total = pool.filter(match).length;
  const matches = pool.filter(match).slice(0, 8);
  if (!matches.length) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  box.innerHTML = matches.map((c) => {
    const b = badgeFor(c);
    return `<button class="hr" data-id="${esc(c.id)}">
      <span class="m ${b.cls}">${esc(b.txt)}</span>
      <span class="hr-name">${esc(c.name)}</span>
      <span class="hr-group">${esc(c.group)}</span>
    </button>`;
  }).join("") + `<div class="hr-foot" data-all>View all ${total} match${total === 1 ? "" : "es"} in the catalog →</div>`;
  box.classList.remove("hidden");
  for (const btn of box.querySelectorAll("button.hr")) {
    btn.addEventListener("click", () => {
      const e = resolveCall(btn.dataset.id);
      if (e) { box.classList.add("hidden"); openEntry(e, "home"); }
    });
  }
  box.querySelector(".hr-foot").addEventListener("click", () => {
    state.browse.q = document.getElementById("home-search").value.trim();
    state.browse.group = "__all";
    goBrowse();
  });
}

/* ---- home: recently run ---- */
async function loadRecent() {
  try {
    const r = await req("GET", "/api/recent?limit=8");
    state.recent = r.recent || [];
  } catch {
    state.recent = [];
  }
}

async function refreshRecent() {
  await loadRecent();
  if (state.view === "home") renderRecentChips();
}

function relTime(iso) {
  const t = new Date(String(iso).replace(" ", "T") + "Z");
  if (isNaN(t)) return "";
  const s = (Date.now() - t.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 7 * 86400) return Math.floor(s / 86400) + "d ago";
  return t.toDateString();
}

function renderRecentChips() {
  const sec = document.getElementById("recent-section");
  const chips = document.getElementById("recent-chips");
  const items = state.recent
    .map((r) => {
      const e = resolveCall(r.call_id);
      return e ? { ...r, entry: e } : null;
    })
    .filter(Boolean)
    .slice(0, 6);
  if (!items.length) { sec.classList.add("hidden"); return; }
  sec.classList.remove("hidden");
  chips.innerHTML = "";
  for (const it of items) {
    const b = badgeFor(it.entry);
    const chip = document.createElement("span");
    chip.className = "chip" + (it.ok ? "" : " bad");
    chip.title = it.ok ? "Last run succeeded" : "Last run had an error";
    chip.innerHTML = `<span class="m ${b.cls}">${esc(b.txt)}</span>${esc(it.entry.name)} <span class="t">${esc(relTime(it.created_at))}</span>`;
    chip.addEventListener("click", () => openEntry(it.entry, "home"));
    chips.appendChild(chip);
  }
}

/* ---- browse: group tabs + endpoint cards ---- */
function groupCount(g) {
  const cg = state.catalog.groups.find((x) => x.group === g);
  if (cg) return cg.calls.length;
  return state.bulk.ops.filter((o) => o.group === g).length;
}

function browseSections() {
  const out = [];
  for (const g of state.catalog.groups) out.push({ group: g.group, entries: g.calls });
  for (const g of ["Bulk Extract", "Bulk Import"]) {
    const ops = state.bulk.ops.filter((o) => o.group === g);
    if (ops.length) out.push({ group: g, entries: ops });
  }
  return out;
}

function renderBrowse() {
  const tabsEl = document.getElementById("browse-tabs");
  const total = state.catalog.groups.reduce((n, g) => n + g.calls.length, 0) + state.bulk.ops.length;
  const groupOrder = state.catalog.groups.map((g) => g.group);
  for (const g of ["Bulk Extract", "Bulk Import"]) if (!groupOrder.includes(g)) groupOrder.push(g);
  tabsEl.innerHTML =
    `<button class="tab${state.browse.group === "__all" ? " active" : ""}" data-g="__all">All <span class="count">${total}</span></button>` +
    groupOrder.map((g) =>
      `<button class="tab${state.browse.group === g ? " active" : ""}" data-g="${esc(g)}">${esc(g)} <span class="count">${groupCount(g)}</span></button>`
    ).join("");
  document.getElementById("browse-search").value = state.browse.q;
  renderBrowseGrid();
}

function browseCard(entry) {
  const b = badgeFor(entry);
  const card = document.createElement("div");
  card.className = "epcard";
  const warn = entry.confirm ? `<span class="badge b-warn" title="${esc(entry.confirm)}">⚠ action</span>` : "";
  card.innerHTML = `
    <div class="card-top">
      <span class="m ${b.cls}">${esc(b.txt)}</span>
      ${entry.permission ? `<span class="badge b-perm" title="Marketo permission your API user needs">${esc(entry.permission)}</span>` : ""}
      ${warn}
    </div>
    <h3>${esc(entry.name)}</h3>
    <div><div class="klabel func">What it does</div><p class="kv func">${esc(entry.summary || "")}</p></div>
    ${entry.whenToUse ? `<div><div class="klabel use">Use when</div><p class="kv use"><em>When:</em> ${esc(entry.whenToUse)}</p></div>` : ""}
    <div class="card-foot">
      <span class="path">${esc(entry.path || (entry.create ? entry.create.path : ""))}</span>
      <span class="open">Open call →</span>
    </div>`;
  card.addEventListener("click", () => openEntry(entry, "browse"));
  return card;
}

function renderBrowseGrid() {
  const q = (document.getElementById("browse-search").value || "").trim().toLowerCase();
  const active = state.browse.group;
  const wrapEl = document.getElementById("browse-grid");
  wrapEl.innerHTML = "";
  let anyVisible = false;
  for (const sec of browseSections()) {
    const gOk = active === "__all" || sec.group === active;
    const match = (e) => !q || (e.name + " " + (e.summary || "") + " " + (e.whenToUse || "") + " " + sec.group).toLowerCase().includes(q);
    const cards = sec.entries.filter(match);
    if (!gOk || !cards.length) continue;
    anyVisible = true;
    const secEl = document.createElement("div");
    const noun = q ? "match" : "call";
    secEl.innerHTML = `<div class="section-label">${esc(sec.group)} <span class="n">${cards.length} ${noun}${cards.length === 1 ? "" : "s"}</span></div>`;
    const grid = document.createElement("div");
    grid.className = "grid";
    for (const e of cards) grid.appendChild(browseCard(e));
    secEl.appendChild(grid);
    wrapEl.appendChild(secEl);
  }
  if (!anyVisible) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = "No calls match your search.";
    wrapEl.appendChild(d);
  }
}

function selectCall(call, from) {
  state.activeCall = call;
  state.lastResult = null;
  state.bulk.activeOp = null;
  state.bulk.job = null;
  stopPolling();
  $("#response").innerHTML = "";
  if (from) state.backTo = from;
  renderDetail(call);
  showView("detail");
  document.getElementById("view-detail").scrollTo({ top: 0 });
}

/* ================= detail + form ================= */

function bodyFields(spec) {
  const b = spec.body;
  if (!b) return [];
  if (b.mode === "record") return b.fields || [];
  if (b.mode === "multipart") return b.parts || [];
  if (b.mode === "values") return b.fields || [];
  if (b.mode === "sync") return b.fixed || [];
  return [];
}

function requiredParams(spec) {
  const out = [];
  for (const p of spec.path || []) out.push({ section: "path", p });
  for (const p of spec.query || []) if (p.required) out.push({ section: "query", p });
  for (const f of bodyFields(spec)) if (f.required) out.push({ section: "body", p: f });
  return out;
}

function optionalParams(spec) {
  const out = [];
  for (const p of spec.query || []) if (!p.required) out.push({ section: "query", p });
  for (const f of bodyFields(spec)) if (!f.required) out.push({ section: "body", p: f });
  return out;
}

function fieldInput(section, p) {
  const id = `f-${section}-${p.name}`;
  const ph = p.example ? esc(p.example) : "";
  switch (p.type) {
    case "integer":
      return `<input id="${id}" type="number" step="1" placeholder="${ph}">`;
    case "boolean":
      return `<select id="${id}"><option value="true">Yes</option><option value="false">No</option></select>`;
    case "datetime":
      return `<input id="${id}" type="text" placeholder="${ph || "2026-01-01T00:00:00"}">`;
    case "csv":
      return `<input id="${id}" type="text" placeholder="${ph || "value1, value2, …"}">`;
    case "json":
      return `<textarea id="${id}" rows="4" spellcheck="false" placeholder="${ph}"></textarea>`;
    case "select": {
      const def = p.placeholder ? `<option value="">${esc(p.placeholder)}</option>` : "";
      const opts = (p.options || []).map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
      return `<select id="${id}">${def}${opts}</select>`;
    }
    case "file":
      return `<input id="${id}" type="file">`;
    case "lookup":
      return `<div class="lookup" data-source="${esc(p.source || "describe")}" data-input-id="${id}" data-pending="1"><div class="lk-status muted">Field picker…</div></div>`;
    default:
      return `<input id="${id}" type="text" placeholder="${ph}">`;
  }
}

function fieldHtml(section, p) {
  const req = p.required
    ? ' <b class="req">*</b>'
    : ' <span class="opt">(optional)</span>';
  const hint = p.hint ? `<div class="hint">${esc(p.hint)}</div>` : "";
  return `<div class="field"><label>${esc(p.label)}${req}</label>${fieldInput(section, p)}${hint}</div>`;
}

/* ================= field picker (lookup params) ================= */

const lookupCache = new Map(); // key: source:profileId -> fields[]

function activeProfileId() {
  const p = state.profiles.find((x) => x.is_active);
  return p ? p.id : null;
}

async function hydrateLookups(root) {
  for (const el of $$(".lookup[data-pending]", root)) {
    el.dataset.pending = "";
    const source = el.dataset.source || "describe";
    const key = source + ":" + activeProfileId();
    let fields = lookupCache.get(key);
    if (!fields) {
      el.innerHTML = '<div class="lk-status muted">Loading field list…</div>';
      try {
        const data = await req("GET", `/api/lookup/${source}`);
        fields = data.fields || [];
        lookupCache.set(key, fields);
      } catch (e) {
        el.innerHTML = `<div class="lk-status" style="color:var(--red)">${esc(e.message)} — you can type comma-separated field names below instead.</div>
          <div style="padding:8px"><textarea id="${el.dataset.inputId}" rows="3" placeholder="email,firstName,lastName" style="width:100%;box-sizing:border-box;padding:8px 11px;border:1px solid var(--line);border-radius:8px;font:inherit"></textarea></div>`;
        continue;
      }
    }
    renderLookupPicker(el, fields);
  }
}

/* ---- P7: shared field picker — custom/system badges + All/Custom/System filter ---- */

function fieldBadgesHtml(f) {
  let h = "";
  if (f.readOnly) h += '<span class="tagc ro">read-only</span>';
  if (f.isCustom === true) h += '<span class="tagc custom">custom</span>';
  else if (f.isCustom === false) h += '<span class="tagc system">system</span>';
  return h;
}

const FIELD_SORT_KEY = "marketo.fieldSort";

function savedFieldSort() {
  try {
    const v = localStorage.getItem(FIELD_SORT_KEY);
    return ["name", "custom", "system"].includes(v) ? v : "custom";
  } catch {
    return "custom";
  }
}

function sortFieldOptions(fields, mode) {
  // "name": plain A–Z · "custom": custom fields first · "system": system fields first
  // (untagged fields trail in both group sorts; ties fall back to A–Z by label)
  const alpha = (a, b) => String(a.label).localeCompare(String(b.label));
  if (mode === "name") return [...fields].sort(alpha);
  const rank =
    mode === "system"
      ? (f) => (f.isCustom === false ? 0 : f.isCustom === true ? 1 : 2)
      : (f) => (f.isCustom === true ? 0 : f.isCustom === false ? 1 : 2);
  return [...fields].sort((a, b) => rank(a) - rank(b) || alpha(a, b));
}

function renderTaggedPicker(el, fields, opts = {}) {
  // opts: { disabled:Set, initial:Set, countLabel:"selected"|"columns", onChecked:(values)=>void }
  const disabled = opts.disabled || new Set();
  const initial = opts.initial || new Set();
  let tagFilter = "all";
  el.innerHTML = `
    <div class="lk-bar">
      <input type="search" class="lk-search" placeholder="Filter fields…" autocomplete="off">
      <span class="seg lk-seg" role="group" aria-label="Field type filter">
        <button type="button" class="on" data-f="all">All</button>
        <button type="button" data-f="custom">Custom only</button>
        <button type="button" data-f="system">System only</button>
      </span>
      <select class="lk-sort" title="Sort the field list" aria-label="Sort the field list">
        <option value="name">Name A–Z</option>
        <option value="custom">Custom first</option>
        <option value="system">System first</option>
      </select>
      <button type="button" class="ghost small lk-all">Select all shown</button>
      <button type="button" class="ghost small lk-clear">Clear</button>
      <span class="muted lk-count"></span>
    </div>
    <div class="lk-list"></div>`;
  const list = el.querySelector(".lk-list");
  const rowByName = new Map();
  for (const f of sortFieldOptions(fields, savedFieldSort())) {
    const row = document.createElement("label");
    row.className = "lk-row";
    row.dataset.label = `${f.label} ${f.name} ${f.dataType || ""}`.toLowerCase();
    row.dataset.tag = f.isCustom === true ? "custom" : f.isCustom === false ? "system" : "untagged";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = f.name;
    if (disabled.has(f.name)) cb.disabled = true;
    if (initial.has(f.name)) cb.checked = true;
    row.appendChild(cb);
    const span = document.createElement("span");
    span.className = "lk-name";
    span.textContent = f.label + (disabled.has(f.name) ? " *" : f.dataType ? `  (${f.dataType})` : "");
    row.appendChild(span);
    const badgesHtml = fieldBadgesHtml(f);
    if (badgesHtml) {
      const badges = document.createElement("span");
      badges.className = "lk-badges";
      badges.innerHTML = badgesHtml;
      row.appendChild(badges);
    }
    list.appendChild(row);
    rowByName.set(f.name, row);
  }
  const search = el.querySelector(".lk-search");
  const sortSel = el.querySelector(".lk-sort");
  sortSel.value = savedFieldSort();
  sortSel.addEventListener("change", () => {
    try { localStorage.setItem(FIELD_SORT_KEY, sortSel.value); } catch {}
    for (const f of sortFieldOptions(fields, sortSel.value)) {
      const r = rowByName.get(f.name);
      if (r) list.appendChild(r); // re-appending reorders; checkbox state travels with the row
    }
    update();
  });
  const count = el.querySelector(".lk-count");
  const seg = el.querySelector(".lk-seg");
  const visible = () => Array.from(list.children).filter((r) => !r.classList.contains("lk-hidden"));
  const update = () => {
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    for (const row of list.children) {
      const okTag = tagFilter === "all" || row.dataset.tag === tagFilter;
      const okQ = !q || row.dataset.label.includes(q);
      const match = okTag && okQ;
      row.classList.toggle("lk-hidden", !match);
      if (match) shown++;
    }
    const checked = $$("input[type=checkbox]:checked", el).map((c) => c.value);
    const label = opts.countLabel === "columns" ? "columns" : "selected";
    count.textContent = `${checked.length} ${label} · ${shown}/${fields.length} shown`;
    if (opts.onChecked) opts.onChecked(checked);
  };
  search.addEventListener("input", update);
  for (const b of seg.querySelectorAll("button")) {
    b.addEventListener("click", () => {
      tagFilter = b.dataset.f;
      for (const x of seg.querySelectorAll("button")) x.classList.toggle("on", x === b);
      update();
    });
  }
  el.querySelector(".lk-all").addEventListener("click", () => {
    for (const row of visible()) {
      const cb = row.querySelector("input");
      if (!cb.disabled) cb.checked = true;
    }
    update();
  });
  el.querySelector(".lk-clear").addEventListener("click", () => {
    for (const cb of $$("input[type=checkbox]", el)) if (!cb.disabled) cb.checked = false;
    update();
  });
  update();
}

function renderLookupPicker(el, fields) {
  renderTaggedPicker(el, fields, {});
}

function bodySectionHtml(body) {
  if (!body) return "";
  if (body.mode === "freeform") {
    const ex = body.example ? esc(body.example) : "";
    return `<div class="field">
      <label>Request body <span class="opt">(JSON — advanced)</span></label>
      <textarea id="f-body-json" rows="6" spellcheck="false">${ex}</textarea>
      ${body.hint ? `<div class="hint">${esc(body.hint)}</div>` : ""}
    </div>`;
  }
  if (body.mode === "records") {
    return `<div class="field">
      <label>Records to send <span class="opt">(JSON array — advanced)</span></label>
      <textarea id="f-body-rows" rows="6" spellcheck="false">${body.example ? esc(body.example) : "[]"}</textarea>
      <div class="hint">One JSON object per record. See the Marketo docs link below for the exact expected shape.</div>
    </div>`;
  }
  if (body.mode === "values") {
    const ex = body.example ? esc(body.example) : "value1, value2, …";
    const max = body.maxPerCall ? ` (max ${body.maxPerCall} per call)` : "";
    return `<div class="field">
      <label>Values <b class="req">*</b> <span class="opt">comma-separated${max}</span></label>
      <textarea id="f-body-values" rows="3" spellcheck="false" placeholder="${ex}"></textarea>
      ${body.hint ? `<div class="hint">${esc(body.hint)}</div>` : ""}
    </div>`;
  }
  if (body.mode === "sync") {
    const max = body.maxPerCall ? ` · up to ${body.maxPerCall} per call` : "";
    const picker = body.fieldPicker
      ? `<details class="sync-picker" open>
           <summary>Columns — choose the fields to send</summary>
           <div class="sync-picker-body" data-source="${esc(body.fieldPicker)}"><div class="lk-status muted">Loading field list…</div></div>
         </details>`
      : `<div class="sync-cols">
           <span class="muted small">Columns:</span>
           <span class="sync-addcol"><input type="text" placeholder="column name (API name)" autocomplete="off"><button type="button" class="ghost small">Add</button></span>
         </div>`;
    return `<div class="sync-wrap">
      <div class="section-title">Records to send${max}</div>
      ${body.hint ? `<div class="hint" style="margin-bottom:8px">${esc(body.hint)}</div>` : ""}
      ${picker}
      <div class="sync-table-wrap"><table class="sync-table"></table></div>
      <div style="display:flex;gap:10px;margin-top:8px;align-items:center">
        <button type="button" class="ghost small sync-addrow">+ Add record</button>
        <span class="muted small sync-count"></span>
      </div>
    </div>`;
  }
  return ""; // record & multipart fields are rendered in required/optional sections
}

function renderDetail(call) {
  const d = $("#call-detail");
  const splitEl = document.getElementById("detail-split");
  if (splitEl) splitEl.classList.remove("solo");
  const notes = (call.notes || []).length
    ? `<div class="callout notes"><b>Good to know</b><ul class="notes-list"></ul></div>`
    : "";
  d.innerHTML = `
    <div class="crumbs"><button id="btn-back" class="back">${state.backTo === "browse" ? "← Browse" : "← Home"}</button> <span class="sep">·</span> <span>${esc(call.group)}</span> &rsaquo; <span>${esc(call.name)}</span></div>
    <div class="call-head">
      <div>
        <h1>${esc(call.name)}</h1>
        <div class="badges">
          <span class="badge method-${esc(call.method.toLowerCase())}">${esc(call.method)}</span>
          ${call.permission ? `<span class="badge" title="Marketo permission your API user needs">${esc(call.permission)}</span>` : ""}
          <span class="badge path">${esc(call.path)}</span>
        </div>
      </div>
      ${call.docUrl ? `<a class="doc" target="_blank" rel="noopener" href="${esc(call.docUrl)}">Marketo docs ↗</a>` : ""}
    </div>
    <p class="lede">${esc(call.summary)}</p>
    ${call.whenToUse ? `<div class="callout"><b>When to use:</b> ${esc(call.whenToUse)}</div>` : ""}
    ${notes}
    <div class="card" id="form-card"></div>
  `;
  $("#btn-back").addEventListener("click", () => (state.backTo === "browse" ? goBrowse() : goHome()));
  if (notes) {
    const ul = d.querySelector(".notes-list");
    for (const n of call.notes || []) {
      const li = document.createElement("li");
      li.textContent = n;
      ul.appendChild(li);
    }
  }

  const spec = call.params || { path: [], query: [], body: null };
  const required = requiredParams(spec);
  const optional = optionalParams(spec);
  const card = $("#form-card");
  card.innerHTML = `
    ${required.length
      ? `<div class="section-title">Required fields</div>` + required.map(({ section, p }) => fieldHtml(section, p)).join("")
      : `<div class="section-title">No fields required</div>`}
    ${optional.length
      ? `<details class="optional"><summary>Optional fields (${optional.length})</summary>
         ${optional.map(({ section, p }) => fieldHtml(section, p)).join("")}</details>`
      : ""}
    ${bodySectionHtml(spec.body)}
    <div class="run-row">
      <button id="btn-run" class="primary">▶&ensp;Run call</button>
      <button id="btn-reset" class="ghost">Reset</button>
    </div>
  `;
  $("#btn-run").addEventListener("click", runCall);
  $("#btn-reset").addEventListener("click", () => renderDetail(call));
  state.gridReader = null;
  if (spec.body && spec.body.mode === "sync") {
    const wrap = card.querySelector(".sync-wrap");
    if (wrap) state.gridReader = initSyncGrid(wrap, spec.body);
  }
  hydrateLookups(card);
  // Right pane: friendly empty state until the first run.
  const resp = $("#response");
  if (resp && !resp.children.length) {
    resp.innerHTML = `<div class="card resp-empty">
      <div class="re-ic">📥</div>
      <b>Run the call</b>
      <span>the response will land in this pane</span>
    </div>`;
  }
}

function inputVal(section, name) {
  const lk = $(`[data-input-id="f-${section}-${name}"]`);
  if (lk) {
    const checked = $$("input[type=checkbox]:checked", lk).map((c) => c.value);
    return checked.length ? checked.join(",") : null;
  }
  const el = $(`#f-${section}-${name}`);
  if (!el) return null;
  if (el.type === "file") return el.files && el.files[0] ? el.files[0] : null;
  const v = (el.value || "").trim();
  return v === "" ? null : v;
}

function readParams() {
  const call = state.activeCall;
  const spec = call.params || { path: [], query: [], body: null };
  const out = { path: {}, query: {}, body: {} };
  for (const p of spec.path || []) {
    const v = inputVal("path", p.name);
    if (v !== null) out.path[p.name] = v;
  }
  for (const p of spec.query || []) {
    const v = inputVal("query", p.name);
    if (v !== null) out.query[p.name] = v;
  }
  const body = spec.body;
  if (body) {
    if (body.mode === "freeform") {
      out.body.json = $("#f-body-json") ? $("#f-body-json").value.trim() : "";
    } else if (body.mode === "records") {
      const raw = $("#f-body-rows") ? $("#f-body-rows").value.trim() : "[]";
      try { out.body.rows = JSON.parse(raw); }
      catch (e) { throw new Error("The 'Records to send' box is not valid JSON — check the brackets/commas."); }
    } else if (body.mode === "record") {
      for (const f of body.fields || []) {
        let v = inputVal("body", f.name);
        if (v !== null && f.type === "json") {
          try { v = JSON.parse(v); }
          catch (e) { throw new Error(`The field “${f.label}” must be valid JSON — check the braces and commas.`); }
        }
        if (v !== null) out.body[f.name] = v;
      }
    } else if (body.mode === "values") {
      out.body.values = $("#f-body-values") ? $("#f-body-values").value : "";
    } else if (body.mode === "sync") {
      out.body.fixed = {};
      for (const f of body.fixed || []) {
        const v = inputVal("body", f.name);
        if (v !== null) out.body.fixed[f.name] = v;
      }
      out.body.rows = state.gridReader ? state.gridReader() : [];
    } else if (body.mode === "multipart") {
      for (const f of body.parts || []) {
        const el = $(`#f-body-${f.name}`);
        if (!el) continue;
        if (f.type === "file") {
          if (el.files && el.files[0]) {
            const file = el.files[0];
            out.body[f.name] = { filename: file.name, size: file.size };
            out.body.__files__ = out.body.__files__ || {};
            out.body.__files__[f.name] = file;
          }
        } else {
          const v = (el.value || "").trim();
          if (v) out.body[f.name] = v;
        }
      }
    }
  }
  return { params: out, __files__: out.body && out.body.__files__ };
}

/* ================= sync-mode record grid ================= */

// Builds the interactive "records" table used by sync-mode calls
// (Sync Leads, Sync Opportunities, program member data, …).
// Columns either come from a lookup picker (fieldPicker) or are typed by
// the user. Returns a reader: () => [{fieldName: value}, …].
function initSyncGrid(wrap, spec) {
  const reqFields = new Set(spec.requiredFields || []);
  let cols = [...reqFields];
  const table = wrap.querySelector(".sync-table");
  const countEl = wrap.querySelector(".sync-count");

  function renderTable() {
    table.innerHTML = "";
    const thead = document.createElement("thead");
    const hrow = document.createElement("tr");
    const th0 = document.createElement("th");
    th0.className = "sync-num";
    th0.textContent = "#";
    hrow.appendChild(th0);
    for (const c of cols) {
      const th = document.createElement("th");
      th.textContent = c + (reqFields.has(c) ? " *" : "");
      hrow.appendChild(th);
    }
    const thx = document.createElement("th");
    thx.textContent = "";
    hrow.appendChild(thx);
    thead.appendChild(hrow);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    for (let i = 0; i < 1; i++) tbody.appendChild(makeRow());
    table.appendChild(tbody);
    updateCount();
  }

  function makeRow() {
    const tr = document.createElement("tr");
    const tdNum = document.createElement("td");
    tdNum.className = "sync-num";
    tr.appendChild(tdNum);
    for (const c of cols) {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.type = "text";
      inp.className = "sync-cell";
      inp.dataset.col = c;
      inp.autocomplete = "off";
      td.appendChild(inp);
      tr.appendChild(td);
    }
    const tdX = document.createElement("td");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ghost small";
    b.textContent = "✕";
    b.title = "Remove record";
    b.addEventListener("click", () => {
      tr.remove();
      if (!table.querySelector("tbody tr")) table.querySelector("tbody").appendChild(makeRow());
      updateCount();
    });
    tdX.appendChild(b);
    tr.appendChild(tdX);
    return tr;
  }

  function updateCount() {
    const rows = table.querySelectorAll("tbody tr").length;
    countEl.textContent = `${rows} record${rows === 1 ? "" : "s"} · ${cols.length} column${cols.length === 1 ? "" : "s"}`;
  }

  wrap.querySelector(".sync-addrow").addEventListener("click", () => {
    table.querySelector("tbody").appendChild(makeRow());
    updateCount();
  });

  const addCol = (name) => {
    name = (name || "").trim();
    if (!name) return;
    if (name.includes(",")) { for (const part of name.split(",")) addCol(part); return; }
    if (!cols.includes(name)) { cols.push(name); renderTable(); }
  };

  const colEditor = wrap.querySelector(".sync-addcol");
  if (colEditor) {
    const inp = colEditor.querySelector("input");
    const btn = colEditor.querySelector("button");
    btn.addEventListener("click", () => { addCol(inp.value); inp.value = ""; });
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addCol(inp.value); inp.value = ""; } });
  }

  const pickerBody = wrap.querySelector(".sync-picker-body");
  if (pickerBody) {
    const source = pickerBody.dataset.source || "describe";
    const key = source + ":" + activeProfileId();

    const renderPicker = (fields) => {
      renderTaggedPicker(pickerBody, fields, {
        disabled: reqFields,
        initial: reqFields,
        countLabel: "columns",
        onChecked: (checked) => {
          const next = [...reqFields, ...checked.filter((c) => !reqFields.has(c))];
          if (next.length !== cols.length || next.some((c, i) => cols[i] !== c)) {
            cols = next;
            renderTable();
          }
        },
      });
    };

    const cached = lookupCache.get(key);
    if (cached) renderPicker(cached);
    else {
      req("GET", `/api/lookup/${source}`)
        .then((data) => {
          const fields = data.fields || [];
          lookupCache.set(key, fields);
          renderPicker(fields);
        })
        .catch((e) => {
          pickerBody.innerHTML = `<div class="lk-status" style="color:var(--red)">${esc(e.message)} — type the column names below instead.</div>
            <div class="sync-cols" style="padding:8px 0">
              <span class="muted small">Columns:</span>
              <span class="sync-addcol"><input type="text" placeholder="column name (API name)" autocomplete="off"><button type="button" class="ghost small">Add</button></span>
            </div>`;
          const inp = pickerBody.querySelector(".sync-addcol input");
          const btn = pickerBody.querySelector(".sync-addcol button");
          btn.addEventListener("click", () => { addCol(inp.value); inp.value = ""; });
          inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addCol(inp.value); inp.value = ""; } });
        });
    }
  }

  renderTable();

  return function readGrid() {
    const out = [];
    for (const tr of table.querySelectorAll("tbody tr")) {
      const rec = {};
      for (const inp of tr.querySelectorAll("input.sync-cell")) {
        const v = (inp.value || "").trim();
        if (v) rec[inp.dataset.col] = v;
      }
      if (Object.keys(rec).length) out.push(rec);
    }
    return out;
  };
}

/* ================= running calls ================= */

function setRunning(on) {
  state.running = on;
  const b = $("#btn-run");
  if (b) { b.disabled = on; b.textContent = on ? "Running…" : "▶\u2002Run call"; }
}

async function runCall() {
  const call = state.activeCall;
  if (state.running) return;
  if (call.confirm && !confirm(call.confirm)) return;

  let parsed;
  try { parsed = readParams(); } catch (e) { showResponse({ ok: false, error: { friendly: e.message } }); return; }
  const { params, __files__ } = parsed;

  // Client-side required check for instant feedback.
  const missing = [];
  const spec = call.params || { path: [], query: [], body: null };
  for (const p of spec.path || []) if (p.required && !params.path[p.name]) missing.push(p.label);
  for (const p of spec.query || []) if (p.required && !params.query[p.name]) missing.push(p.label);
  if (spec.body && spec.body.mode === "values")
    for (const f of spec.body.fields || []) if (f.required && !params.body[f.name]) missing.push(f.label);
  if (spec.body && spec.body.mode === "sync")
    for (const f of spec.body.fixed || []) if (f.required && !(params.body.fixed || {})[f.name]) missing.push(f.label);
  if (missing.length) {
    showResponse({ ok: false, error: { friendly: "Missing required field(s): " + missing.join(", ") } });
    return;
  }
  if (spec.body && spec.body.mode === "sync" && !(params.body.rows || []).length) {
    showResponse({ ok: false, error: { friendly: "Add at least one record to the table before running." } });
    return;
  }

  const payload = { params };
  if (__files__) payload.__files__ = undefined; // handled server-side in Phase 4; v0.1 sends names/sizes only

  setRunning(true);
  try {
    const res = await req("POST", `/api/call/${encodeURIComponent(call.id)}`, payload);
    showResponse(res);
  } catch (e) {
    showResponse({ ok: false, error: { friendly: e.message } });
  } finally {
    setRunning(false);
    refreshRecent();
  }
}

/* ================= response ================= */

function findList(data) {
  if (Array.isArray(data)) return data;
  if (data && typeof data === "object" && Array.isArray(data.result)) return data.result;
  return null;
}

function toCsv(rows) {
  const cols = [];
  for (const r of rows) {
    if (r && typeof r === "object") for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
  }
  const cell = (v) => {
    if (v === null || v === undefined) return "";
    if (typeof v === "object") v = JSON.stringify(v);
    v = String(v);
    return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r ? r[c] : "")).join(","))].join("\r\n");
}

function download(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

function showResponse(res) {
  state.lastResult = res;
  const box = $("#response");
  const ok = !!res.ok;
  const status = res.status != null ? ` · HTTP ${res.status}` : "";
  const lat = res.latency_ms != null ? ` · ${res.latency_ms} ms` : "";
  const list = findList(res.data);
  const hasData = res.data !== undefined && res.data !== null;

  box.innerHTML = `
    <div class="card resp ${ok ? "ok" : "err"}">
      <div class="resp-head">
        <span class="resp-status">${ok ? "✓ Success" : "✕ Error"}${esc(status + lat)}</span>
        <span class="resp-actions" style="display:flex;gap:8px;flex-wrap:wrap;">
          ${hasData ? `<button id="dl-json" class="ghost small">Download JSON</button>` : ""}
          ${list ? `<button id="dl-csv" class="ghost small">Download CSV (${list.length} rows)</button>` : ""}
        </span>
      </div>
      ${res.error ? `
        <div class="error-box">
          ${res.error.code ? `<span class="err-code" title="Marketo error code">${esc(res.error.code)}</span>&ensp;` : ""}
          ${esc(res.error.friendly || "Something went wrong.")}
          ${res.error.doc ? `&ensp;<a class="err-doc" href="${esc(res.error.doc)}" target="_blank" rel="noopener">View in Marketo docs ↗</a>` : ""}
        </div>` : ""}
      ${res.data && res.data.moreResult ? `<div class="callout" style="margin-top:10px"><b>More results available.</b> Copy the <code>nextPageToken</code> from the JSON below, then run this call again with it to fetch the next page.</div>` : ""}
      ${res.error && res.error.raw ? `<details class="raw"><summary>Raw response</summary><pre>${esc(res.error.raw)}</pre></details>` : ""}
      ${hasData ? `<pre class="json-view">${esc(JSON.stringify(res.data, null, 2))}</pre>` : ""}
    </div>`;

  if (hasData) $("#dl-json").addEventListener("click", () => download("result.json", JSON.stringify(res.data, null, 2), "application/json"));
  if (list) $("#dl-csv").addEventListener("click", () => download("result.csv", toCsv(list), "text/csv"));
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ================= connections (profiles) ================= */

async function loadProfiles() {
  const r = await req("GET", "/api/config/profiles");
  state.profiles = r.profiles;
  renderProfileSelect();
  renderProfileTable();
}

function renderProfileSelect() {
  const sel = $("#profile-select");
  const active = state.profiles.find((p) => p.is_active);
  sel.innerHTML = "";
  if (!state.profiles.length) {
    sel.innerHTML = `<option>No connections yet — click “Manage connections”</option>`;
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  for (const p of state.profiles) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.name + (p.is_active ? " ✓" : "");
    sel.appendChild(o);
  }
  sel.value = active ? String(active.id) : String(state.profiles[0].id);
}

function renderProfileTable() {
  const t = $("#profile-table");
  t.innerHTML = "";
  if (!state.profiles.length) {
    t.innerHTML = `<div class="muted small">No connections yet — add your first one below.</div>`;
    return;
  }
  for (const p of state.profiles) {
    const row = document.createElement("div");
    row.className = "prow" + (p.is_active ? " active" : "");
    row.innerHTML = `
      <span class="grow"><span class="pname">${esc(p.name)}</span>
      <span class="pinst">${esc(p.instance)}</span>
      ${p.is_active ? '<span class="badge method-get">active</span>' : ""}</span>
      <button class="ghost small" data-act="activate">Activate</button>
      <button class="ghost small" data-act="edit">Edit</button>
      <button class="ghost small" data-act="del">Delete</button>`;
    row.querySelector('[data-act="activate"]').addEventListener("click", async () => {
      await req("POST", `/api/config/active?profile_id=${p.id}`);
      await loadProfiles();
    });
    row.querySelector('[data-act="edit"]').addEventListener("click", () => startEditProfile(p));
    row.querySelector('[data-act="del"]').addEventListener("click", async () => {
      if (!confirm(`Delete connection “${p.name}”?`)) return;
      await req("DELETE", `/api/config/profiles/${p.id}`);
      await loadProfiles();
    });
    t.appendChild(row);
  }
}

function clearProfileForm() {
  state.editingProfileId = null;
  $("#pf-title").textContent = "Add a connection";
  $("#pf-name").value = $("#pf-instance").value = $("#pf-clientid").value = $("#pf-secret").value = "";
  $("#pf-status").textContent = "";
  $("#pf-save").textContent = "Save";
  $("#pf-cancel-edit").classList.add("hidden");
}

function startEditProfile(p) {
  state.editingProfileId = p.id;
  $("#pf-title").textContent = `Edit connection — ${p.name}`;
  $("#pf-name").value = p.name;
  $("#pf-instance").value = p.instance;
  $("#pf-clientid").value = p.client_id;
  $("#pf-secret").value = "";
  $("#pf-status").textContent = "Leave the secret blank to keep the current one.";
  $("#pf-save").textContent = "Save changes";
  $("#pf-cancel-edit").classList.remove("hidden");
}

async function saveProfile() {
  const body = {
    name: $("#pf-name").value.trim(),
    instance: $("#pf-instance").value.trim(),
    client_id: $("#pf-clientid").value.trim(),
    client_secret: $("#pf-secret").value,
  };
  if (!body.name || !body.instance || !body.client_id) {
    $("#pf-status").textContent = "Name, instance, and client ID are all required.";
    return;
  }
  try {
    if (state.editingProfileId) {
      await req("PATCH", `/api/config/profiles/${state.editingProfileId}`, body);
    } else {
      if (!body.client_secret) { $("#pf-status").textContent = "Client secret is required for a new connection."; return; }
      await req("POST", "/api/config/profiles", body);
    }
    clearProfileForm();
    await loadProfiles();
  } catch (e) {
    $("#pf-status").textContent = e.message;
  }
}

async function testConnection() {
  const statusEl = $("#pf-status");
  const active = state.profiles.find((p) => p.is_active);
  statusEl.textContent = "Testing…";
  try {
    const r = await req("POST", `/api/config/test${active ? `?profile_id=${active.id}` : ""}`);
    statusEl.textContent = r.detail || (r.ok ? "OK" : "Failed");
  } catch (e) {
    statusEl.textContent = e.message;
  } finally {
    loadConnStatus(); // test forces a fresh token — refresh the status block
  }
}

/* Connection status (P10): shows the access-token state for the active
   connection — fetched/valid-until/scope from the Marketo Identity response.
   The full token is never sent to the UI (masked only). */
async function loadConnStatus() {
  const box = $("#conn-status");
  if (!box) return;
  const when = (ts) =>
    new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  try {
    const r = await req("GET", "/api/auth/status");
    const t = r.token || {};
    let tokenLine;
    if (t.state === "valid") {
      tokenLine =
        `Access token: fetched at ${when(t.obtainedAt)} · valid until ${when(t.expiresAt)}` +
        (t.scope ? ` · scope: ${esc(t.scope)}` : "") +
        `<br><span class="cs-mask">${esc(t.tokenMasked)}</span>`;
    } else if (t.state === "expired") {
      tokenLine = `Access token expired at ${when(t.expiresAt)} — it will be refreshed automatically on the next call.`;
    } else {
      tokenLine =
        "Access token: not fetched yet. Run any call (or Test connection) and Marketo " +
        "issues one here — tokens are valid for 1 hour.";
    }
    box.innerHTML =
      `<b>${esc(r.profile.name)}</b> <span class="muted">${esc(r.profile.instance)}</span><br>` +
      tokenLine;
    box.classList.remove("hidden");
  } catch (e) {
    box.innerHTML = `<span class="muted">${esc(e.message)}</span>`;
    box.classList.remove("hidden");
  }
}

/* ================= bulk wizards (Phase 4) ================= */

const BULK_TERMINAL = ["completed", "failed", "canceled", "expired"];
const BULK_STATE_LABELS = {
  created: "Created",
  queued: "Queued",
  processing: "Running…",
  completed: "Completed",
  failed: "Failed",
  canceled: "Canceled",
  expired: "Expired",
};

async function loadBulkOps() {
  try {
    const r = await req("GET", "/api/bulk/ops");
    state.bulk.ops = r.ops || [];
  } catch {
    state.bulk.ops = [];
  }
}

async function reqForm(url, formData) {
  const r = await fetch(url, { method: "POST", body: formData });
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) {
    const msg =
      (data && data.error && data.error.friendly) ||
      (data && (data.detail || data.message)) ||
      `Request failed (HTTP ${r.status})`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function selectBulkOp(op, from) {
  stopPolling();
  state.bulk.activeOp = op;
  state.bulk.job = null;
  state.activeCall = null;
  state.lastResult = null;
  $("#response").innerHTML = "";
  if (from) state.backTo = from;
  renderWizard(op);
  showView("detail");
  document.getElementById("view-detail").scrollTo({ top: 0 });
  refreshJobHistory();
}

function renderWizSteps(stage) {
  // stage: 1 = configuring, 2 = started/running, 3 = finished
  const el = $("#wiz-steps");
  if (!el) return;
  const steps = [
    { n: 1, t: "Configure" },
    { n: 2, t: "Start job" },
    { n: 3, t: "Running" },
    { n: 4, t: "Get results" },
  ];
  el.innerHTML = steps
    .map((s) => {
      let cls = "wiz-step";
      if (s.n < stage) cls += " done";
      else if (s.n === stage) cls += " active";
      return `<div class="${cls}"><span class="n">${s.n < stage ? "✓" : s.n}</span>${s.t}</div>`;
    })
    .join("");
}

function renderWizard(op) {
  const d = $("#call-detail");
  const splitEl = document.getElementById("detail-split");
  if (splitEl) splitEl.classList.add("solo");
  const kindCls = op.kind === "extract" ? "method-get" : "method-post";
  d.innerHTML = `
    <div class="crumbs"><button id="btn-back" class="back">${state.backTo === "browse" ? "← Browse" : "← Home"}</button> <span class="sep">·</span> <span>${esc(op.group)}</span> &rsaquo; <span>${esc(op.name)}</span></div>
    <div class="call-head">
      <div>
        <h1>${esc(op.name)}</h1>
        <div class="badges">
          <span class="badge ${kindCls}">${op.kind === "extract" ? "BULK EXTRACT" : "BULK IMPORT"}</span>
          ${op.permission ? `<span class="badge" title="Marketo permission your API user needs">${esc(op.permission)}</span>` : ""}
          <span class="badge path">${esc(op.create.path)}</span>
        </div>
      </div>
      ${op.docUrl ? `<a class="doc" target="_blank" rel="noopener" href="${esc(op.docUrl)}">Marketo docs ↗</a>` : ""}
    </div>
    <p class="lede">${esc(op.summary)}</p>
    ${op.whenToUse ? `<div class="callout"><b>When to use:</b> ${esc(op.whenToUse)}</div>` : ""}
    ${(op.notes || []).length
      ? `<div class="callout notes"><b>Good to know</b><ul class="notes-list">${op.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>`
      : ""}
    <div id="wiz-steps" class="wiz-steps"></div>
    <div class="card" id="wiz-form-card"></div>
    <div id="wiz-job-card"></div>
    <div class="card" id="wiz-history-card">
      <div class="section-title">Job history (this connection)</div>
      <div id="wiz-history" class="jobhist-wrap"><div class="muted pad">Loading…</div></div>
    </div>
  `;
  $("#btn-back").addEventListener("click", () => (state.backTo === "browse" ? goBrowse() : goHome()));
  renderWizSteps(1);
  renderWizForm(op);
}

function renderWizForm(op) {
  const card = $("#wiz-form-card");
  card.innerHTML = `
    <div class="section-title">Step 1 — Set up the job</div>
    <div id="wiz-fields"></div>
    <div class="run-row">
      <button id="wiz-start" class="primary">▶&ensp;Start bulk job</button>
      <button id="wiz-reset" class="ghost">Reset</button>
    </div>
  `;
  const host = $("#wiz-fields");
  for (const p of op.form || []) {
    host.insertAdjacentHTML("beforeend", fieldHtml("bulk", p));
    if (p.dynamicFields) {
      const dyn = document.createElement("div");
      dyn.className = "dyn-fields";
      dyn.id = `dyn-${p.name}`;
      host.appendChild(dyn);
      const sel = $(`#f-bulk-${p.name}`, card);
      if (sel) {
        sel.addEventListener("change", () => renderDynFields(p, dyn));
        if (sel.value) renderDynFields(p, dyn);
      }
    }
  }
  $("#wiz-start").addEventListener("click", () => startBulkJob(op));
  $("#wiz-reset").addEventListener("click", () => {
    state.bulk.job = null;
    renderWizSteps(1);
    $("#wiz-job-card").innerHTML = "";
    renderWizForm(op);
  });
  hydrateLookups(card);
}

function renderDynFields(p, dyn) {
  const sel = $(`#f-bulk-${p.name}`);
  const subs = (p.dynamicFields || {})[sel ? sel.value : ""] || [];
  dyn.innerHTML = subs.map((sf) => fieldHtml("bulk", sf)).join("");
  hydrateLookups(dyn);
}

function readBulkForm(op) {
  const form = {};
  for (const p of op.form || []) {
    const v = inputVal("bulk", p.name);
    if (v !== null) form[p.name] = v;
  }
  for (const p of op.form || []) {
    if (!p.dynamicFields) continue;
    const sel = (form[p.name] || "").trim();
    for (const sf of p.dynamicFields[sel] || []) {
      const v = inputVal("bulk", sf.name);
      if (v !== null) form[sf.name] = v;
    }
  }
  return form;
}

function showBulkError(msg) {
  const box = $("#wiz-job-card");
  if (box) {
    box.innerHTML = `<div class="card resp err">
      <div class="resp-head"><span class="resp-status">✕ Could not start the job</span></div>
      <div class="error-box">${esc(msg)}</div>
    </div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

async function startBulkJob(op) {
  if (state.running) return;
  let form;
  try {
    form = readBulkForm(op);
  } catch (e) {
    showBulkError(e.message);
    return;
  }
  const fileField = (op.form || []).find((p) => p.type === "file");
  const file = fileField ? form.file : null;
  if (op.kind === "import") {
    if (!file) {
      showBulkError("Choose a data file first.");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      showBulkError(`That file is ${(file.size / 1048576).toFixed(1)} MB — the bulk import limit is 10 MB. Split it into smaller files.`);
      return;
    }
    if (!confirm(`This will import “${file.name}” into Marketo (insert or update). Continue?`)) return;
  }
  state.running = true;
  const btn = $("#wiz-start");
  if (btn) { btn.disabled = true; btn.textContent = "Starting…"; }
  try {
    const url = `/api/bulk/op/${encodeURIComponent(op.id)}/start`;
    let res;
    if (op.kind === "import") {
      const fd = new FormData();
      for (const [k, v] of Object.entries(form)) {
        if (k === "file") continue;
        if (v !== null && v !== undefined && v !== "") fd.append(k, v);
      }
      if (file) fd.append("file", file, file.name);
      res = await reqForm(url, fd);
    } else {
      res = await req("POST", url, { form });
    }
    if (!res.ok) throw new Error((res.error || {}).friendly || "Something went wrong.");
    state.bulk.job = res.job;
    renderWizSteps(2);
    renderJobCard(res.job, op);
    refreshJobHistory();
    startPolling();
    refreshRecent();
  } catch (e) {
    showBulkError(e.message);
  } finally {
    state.running = false;
    if (btn) { btn.disabled = false; btn.textContent = "▶\u2002Start bulk job"; }
  }
}

function renderJobCard(job, op) {
  const box = $("#wiz-job-card");
  if (!box) return;
  const terminal = BULK_TERMINAL.includes(job.state);
  const counts = job.counts || {};
  let countsHtml = "";
  if (job.kind === "import") {
    const parts = [];
    if (counts.processed != null) parts.push(`<div class="count-box good"><div class="v">${counts.processed.toLocaleString()}</div><div class="k">processed</div></div>`);
    if (counts.failed != null) parts.push(`<div class="count-box ${counts.failed ? "bad" : ""}"><div class="v">${counts.failed}</div><div class="k">failed</div></div>`);
    if (counts.warnings != null) parts.push(`<div class="count-box ${counts.warnings ? "warn" : ""}"><div class="v">${counts.warnings}</div><div class="k">warnings</div></div>`);
    countsHtml = parts.length ? `<div class="counts">${parts.join("")}</div>` : "";
  } else {
    const parts = [];
    if (counts.records != null) parts.push(`<div class="count-box good"><div class="v">${counts.records.toLocaleString()}</div><div class="k">records</div></div>`);
    if (counts.fileSize != null) parts.push(`<div class="count-box"><div class="v">${(counts.fileSize / 1048576).toFixed(1)} MB</div><div class="k">file size</div></div>`);
    countsHtml = parts.length ? `<div class="counts">${parts.join("")}</div>` : "";
  }
  const base = `/api/bulk/jobs/${job.id}`;
  let actions = "";
  if (!terminal) {
    if (job.canCancel) actions += `<button class="ghost small" data-act="cancel">Cancel job</button>`;
    actions += `<button class="ghost small" data-act="refresh">Refresh now</button>`;
  } else {
    if (job.canDownload) actions += `<a class="primary small" href="${base}/file" download>⬇&ensp;Download file</a>`;
    if (job.canFailures) actions += `<a class="ghost small" href="${base}/failures" download>Failures</a>`;
    if (job.canWarnings) actions += `<a class="ghost small" href="${base}/warnings" download>Warnings</a>`;
    actions += `<button class="ghost small" data-act="newjob">Start another job</button>`;
  }
  box.innerHTML = `
    <div class="card resp job-banner s-${esc(job.state)}">
      <div class="resp-head">
        <span class="resp-status">
          <span class="state-badge state-${esc(job.state)}">${job.state === "processing" ? '<span class="spin">◌</span>' : ""}${esc(BULK_STATE_LABELS[job.state] || job.state)}</span>
        </span>
        <span class="muted small">${esc(job.opName || job.opId)}</span>
      </div>
      <div class="job-meta">
        <span>Job ID <b>${esc(job.marketoJobId)}</b></span>
        ${job.fileName ? `<span>File <b>${esc(job.fileName)}</b></span>` : ""}
        ${job.format ? `<span>Format <b>${esc(job.format)}</b></span>` : ""}
        <span>Started <b>${esc(job.createdAt)}</b></span>
      </div>
      ${job.message ? `<div class="job-msg">${esc(job.message)}</div>` : ""}
      ${countsHtml}
      ${!terminal
        ? `<div class="job-msg muted">Bulk jobs usually take a few minutes. You can keep using the app — this card updates automatically every 5 seconds.</div>`
        : ""}
      <div class="job-actions">${actions}</div>
    </div>`;
  $$("[data-act]", box).forEach((b) => {
    const act = b.dataset.act;
    b.addEventListener("click", async () => {
      if (act === "refresh") {
        b.disabled = true;
        try {
          const res = await req("GET", `/api/bulk/jobs/${job.id}`);
          if (res.ok) {
            state.bulk.job = res.job;
            renderJobCard(res.job, op);
            refreshJobHistory();
          } else throw new Error((res.error || {}).friendly || "Refresh failed");
        } catch (e) {
          alert(e.message);
        } finally {
          b.disabled = false;
        }
      } else if (act === "cancel") {
        if (!confirm("Cancel this job? Marketo will stop working on it.")) return;
        try {
          const res = await req("POST", `/api/bulk/jobs/${job.id}/cancel`);
          if (res.ok) {
            state.bulk.job = res.job;
            renderJobCard(res.job, op);
            refreshJobHistory();
            stopPolling();
          } else throw new Error((res.error || {}).friendly || "Cancel failed");
        } catch (e) {
          alert(e.message);
        }
      } else if (act === "newjob") {
        state.bulk.job = null;
        renderWizSteps(1);
        $("#wiz-job-card").innerHTML = "";
        renderWizForm(op);
      }
    });
  });
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function refreshJobHistory() {
  const box = $("#wiz-history");
  if (!box) return;
  let jobs = [];
  try {
    const r = await req("GET", "/api/bulk/jobs");
    jobs = r.jobs || [];
  } catch (e) {
    box.innerHTML = `<div class="muted pad">${esc(e.message)}</div>`;
    return;
  }
  if (!jobs.length) {
    box.innerHTML = `<div class="muted pad">No bulk jobs yet for this connection.</div>`;
    return;
  }
  const rows = jobs
    .map((j) => {
      const base = `/api/bulk/jobs/${j.id}`;
      let actions;
      if (j.state === "completed") {
        actions = "";
        if (j.canDownload) actions += `<a class="ghost small" href="${base}/file" download>⬇ File</a>`;
        if (j.canFailures) actions += `<a class="ghost small" href="${base}/failures" download>Failures</a>`;
        if (j.canWarnings) actions += `<a class="ghost small" href="${base}/warnings" download>Warnings</a>`;
      } else if (BULK_TERMINAL.includes(j.state)) {
        actions = `<span class="muted small">—</span>`;
      } else {
        actions = `<button class="ghost small" data-hact="refresh" data-hid="${j.id}">Refresh</button>`;
      }
      const counts = j.counts
        ? Object.entries(j.counts)
            .map(([k, v]) => `${k} ${v}`)
            .join(" · ")
        : "";
      return `<tr>
        <td><span class="state-badge state-${esc(j.state)}">${esc(BULK_STATE_LABELS[j.state] || j.state)}</span></td>
        <td>${esc(j.opName)}</td>
        <td class="muted">${esc(j.createdAt)}</td>
        <td class="muted">${esc(counts)}</td>
        <td style="white-space:nowrap">${actions}</td>
      </tr>`;
    })
    .join("");
  box.innerHTML = `
    <table class="jobhist">
      <thead><tr><th>Status</th><th>Job</th><th>Started</th><th>Counts</th><th>Actions</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $$("[data-hact='refresh']", box).forEach((b) =>
    b.addEventListener("click", async () => {
      const hid = b.dataset.hid;
      b.disabled = true;
      try {
        const res = await req("GET", `/api/bulk/jobs/${hid}`);
        if (res.ok) refreshJobHistory();
      } catch (e) {
        alert(e.message);
      } finally {
        b.disabled = false;
      }
    })
  );
}

function startPolling() {
  stopPolling();
  state.bulk.pollTimer = setInterval(async () => {
    const job = state.bulk.job;
    if (!job || BULK_TERMINAL.includes(job.state)) return stopPolling();
    try {
      const res = await req("GET", `/api/bulk/jobs/${job.id}`);
      if (!res.ok) return;
      state.bulk.job = res.job;
      const op = state.bulk.activeOp;
      if (op) renderJobCard(res.job, op);
      refreshJobHistory();
      if (BULK_TERMINAL.includes(res.job.state)) {
        stopPolling();
        renderWizSteps(3);
      }
    } catch {
      /* network blip — retry on the next tick */
    }
  }, 5000);
}

function stopPolling() {
  if (state.bulk.pollTimer) {
    clearInterval(state.bulk.pollTimer);
    state.bulk.pollTimer = null;
  }
}

/* ================= settings (P8) ================= */

function openSettings() {
  $("#settings-modal").classList.remove("hidden");
  loadSettings();
  $("#set-dir").focus();
}

function closeSettings() {
  $("#settings-modal").classList.add("hidden");
}

async function loadSettings() {
  try {
    const s = await req("GET", "/api/settings");
    $("#set-dir").value = s.export_dir || "";
    $("#set-log").checked = !!s.log_calls;
    $("#set-bulk").checked = !!s.save_bulk_files;
    $("#set-status").textContent = "";
    $("#set-dir-status").textContent = "";
    $("#set-clear-status").textContent = "";
  } catch (e) {
    console.error("loadSettings", e);
  }
}

async function saveSettings() {
  const body = {
    export_dir: $("#set-dir").value.trim(),
    log_calls: $("#set-log").checked,
    save_bulk_files: $("#set-bulk").checked,
  };
  const el = $("#set-status");
  el.textContent = "Saving…";
  try {
    const r = await req("PUT", "/api/settings", body);
    el.textContent = r.ok ? "Saved." : (r.error && r.error.friendly) || "Saved.";
  } catch (e) {
    el.textContent = e.message || "Could not save.";
  }
}

async function testDir() {
  const path = $("#set-dir").value.trim();
  const el = $("#set-dir-status");
  el.textContent = "Testing…";
  try {
    const r = await req("POST", "/api/settings/test-dir", { path });
    el.textContent = (r.ok ? "✓ " : "✕ ") + (r.reason || "");
  } catch (e) {
    el.textContent = "Could not test that folder.";
  }
}

async function useDefaultDir() {
  try {
    const s = await req("GET", "/api/settings");
    if (s.default_dir) $("#set-dir").value = s.default_dir;
  } catch (e) { /* ignore */ }
}

async function clearLog() {
  const el = $("#set-clear-status");
  el.textContent = "Clearing…";
  try {
    const r = await req("DELETE", "/api/settings/log");
    el.textContent = r.ok
      ? `Cleared ${r.removed} file${r.removed === 1 ? "" : "s"}.`
      : ((r.error && r.error.friendly) || "Could not clear.");
  } catch (e) {
    el.textContent = "Could not clear logs.";
  }
}

function bindSettings() {
  const m = $("#settings-modal");
  if (!m) return;
  $("#btn-settings").addEventListener("click", openSettings);
  $("#settings-close").addEventListener("click", closeSettings);
  m.addEventListener("click", (e) => { if (e.target === m) closeSettings(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !m.classList.contains("hidden")) closeSettings();
  });
  $("#set-dir-test").addEventListener("click", testDir);
  $("#set-dir-default").addEventListener("click", useDefaultDir);
  $("#set-save").addEventListener("click", saveSettings);
  $("#set-clear-log").addEventListener("click", clearLog);
}

/* ================= theme (light/dark toggle) ================= */

const THEME_KEY = "marketo.theme";

function systemTheme() {
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function savedTheme() {
  try {
    const s = localStorage.getItem(THEME_KEY);
    return s === "light" || s === "dark" ? s : null;
  } catch { return null; }
}

function currentTheme() {
  return savedTheme() || systemTheme();
}

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
  // Keep following the OS setting until the user makes an explicit choice.
  try {
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
      if (!savedTheme()) applyTheme(e.matches ? "dark" : "light");
    });
  } catch {}
}

/* ---- detail view: resizable two-pane split (mockup 7) ----
   Drag the gutter between the form pane and the response pane. The form pane
   can range from 25% to 75% of the window (i.e. the response can grow to 75%).
   Position persists per browser; double-click the gutter to reset.          */
const SPLIT_KEY = "marketo.detailSplit";
const SPLIT_DEFAULT = 42;
const SPLIT_MIN = 25;
const SPLIT_MAX = 75;

function initSplit() {
  const split = document.getElementById("detail-split");
  const gutter = document.getElementById("split-gutter");
  if (!split || !gutter) return;
  const clamp = (v) => Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, v));
  const set = (pct) => split.style.setProperty("--split-left", clamp(pct) + "%");
  try {
    const saved = parseFloat(localStorage.getItem(SPLIT_KEY));
    if (isFinite(saved)) set(saved);
  } catch {}
  let dragging = false;
  gutter.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragging = true;
    gutter.setPointerCapture(e.pointerId);
    split.classList.add("dragging");
    document.body.style.userSelect = "none";
  });
  gutter.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const r = split.getBoundingClientRect();
    if (r.width < 10) return;
    set(((e.clientX - r.left) / r.width) * 100);
  });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    split.classList.remove("dragging");
    document.body.style.userSelect = "";
    try {
      const cur = parseFloat(split.style.getPropertyValue("--split-left"));
      localStorage.setItem(SPLIT_KEY, String(Math.round((isFinite(cur) ? cur : SPLIT_DEFAULT) * 10) / 10));
    } catch {}
  };
  gutter.addEventListener("pointerup", end);
  gutter.addEventListener("pointercancel", end);
  gutter.addEventListener("dblclick", () => {
    split.style.removeProperty("--split-left");
    try { localStorage.removeItem(SPLIT_KEY); } catch {}
  });
  // Keyboard: arrow keys nudge the split (Shift = bigger steps).
  gutter.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const step = e.shiftKey ? 10 : 2;
    const cur = parseFloat(split.style.getPropertyValue("--split-left")) || SPLIT_DEFAULT;
    const next = clamp(cur + (e.key === "ArrowRight" ? step : -step));
    set(next);
    try { localStorage.setItem(SPLIT_KEY, String(next)); } catch {}
  });
}

/* ================= boot ================= */

function bindStatic() {
  initTheme();
  initSplit();
  const homeSearch = $("#home-search");
  homeSearch.addEventListener("input", onHomeSearch);
  homeSearch.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && homeSearch.value.trim()) {
      state.browse.q = homeSearch.value.trim();
      state.browse.group = "__all";
      goBrowse();
    }
  });
  // Popular tags (P9): each links straight to a real call via data-id —
  // resolveCall() covers both catalog calls and bulk wizards. If the id is
  // ever missing from the catalog, fall back to searching by the label text.
  $$(".hintline b[data-id]").forEach((b) =>
    b.addEventListener("click", () => {
      const e = resolveCall(b.dataset.id);
      if (e) { openEntry(e, "home"); return; }
      homeSearch.value = b.textContent.trim();
      onHomeSearch();
      homeSearch.focus();
    })
  );
  document.addEventListener("click", (e) => {
    const box = document.getElementById("home-results");
    if (box && !box.classList.contains("hidden") && !e.target.closest(".searchwrap")) box.classList.add("hidden");
  });
  $("#browse-search").addEventListener("input", (e) => {
    state.browse.q = e.target.value;
    renderBrowseGrid();
  });
  $("#browse-tabs").addEventListener("click", (e) => {
    const t = e.target.closest(".tab");
    if (t) { state.browse.group = t.dataset.g; renderBrowse(); }
  });
  $("#btn-browse").addEventListener("click", () => goBrowse());
  // P7b — field library view (static elements in index.html; bind once).
  $("#flds-back").addEventListener("click", goHome);
  $("#flds-copycsv").addEventListener("click", copyFieldsCsv);
  $("#flds-refresh").addEventListener("click", () => refreshFieldsView());
  $("#flds-search").addEventListener("input", (e) => { fldsState.q = e.target.value; renderFldsRows(); });
  $("#flds-search").addEventListener("keydown", (e) => {
    if (e.key === "Escape" && e.target.value) { e.target.value = ""; fldsState.q = ""; renderFldsRows(); }
  });
  document.querySelector(".flds-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f]");
    if (!b) return;
    fldsState.tag = b.dataset.f;
    $$(".flds-seg button").forEach((x) => x.classList.toggle("on", x === b));
    renderFldsRows();
  });
  // Persistent back-to-main-menu: visible on every view (home/browse/detail/bulk wizard).
  $("#btn-home").addEventListener("click", () => goHome());
  $("#btn-manage").addEventListener("click", () => {
    clearProfileForm();
    $("#modal").classList.remove("hidden");
    $("#pf-name").focus();
    loadConnStatus();
  });
  $$("[data-close]").forEach((b) => b.addEventListener("click", () => $("#modal").classList.add("hidden")));
  $("#modal").addEventListener("click", (e) => { if (e.target === $("#modal")) $("#modal").classList.add("hidden"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").classList.add("hidden"); });
  $("#pf-save").addEventListener("click", saveProfile);
  $("#pf-cancel-edit").addEventListener("click", clearProfileForm);
  $("#pf-test").addEventListener("click", testConnection);
  $("#profile-select").addEventListener("change", async (e) => {
    await req("POST", `/api/config/active?profile_id=${e.target.value}`);
    await loadProfiles();
    loadConnStatus();
  });
  bindSettings();
}

async function init() {
  bindStatic();
  await loadProfiles();
  await loadCatalog();
  await loadBulkOps();
  await loadRecent();
  goHome();
}

init().catch((e) => {
  document.body.innerHTML = `<div style="padding:40px;font-family:sans-serif;color:var(--red)">
    Failed to start: ${esc(e.message)}</div>`;
});
