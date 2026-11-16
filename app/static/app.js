"use strict";
// Core: helpers, state, rendering, dashboard. The other areas live in static/js/ (see index.html)
// RelayRoster UI on Tabler CSS. No build step, no Tabler JS, no third-party libraries.
// Icons: Tabler Icons (https://tabler.io/icons, MIT, Copyright (c) 2020-2026 Paweł Kuna), see THIRD_PARTY_NOTICES.md

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const show = (el, on) => el.classList.toggle("d-none", !on);

// Badge colours mapped to Tabler classes
// mode: operating mode of the firmware (profile, Matter, Zigbee)
const BADGE = { ok: "bg-green-lt", warn: "bg-yellow-lt", bad: "bg-red-lt", info: "bg-blue-lt", accent: "bg-azure-lt", mode: "bg-purple-lt", neutral: "bg-secondary-lt" };
// Badge text always starts with a capital letter
const capFirst = (t) => { const s = String(t ?? ""); return s.charAt(0).toUpperCase() + s.slice(1); };
const badge = (kind, text, title) => `<span class="badge ${BADGE[kind] || BADGE.neutral}"${title ? ` title="${esc(title)}"` : ""}>${esc(capFirst(text))}</span>`;

const ICON = {
  external: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6h-6a2 2 0 0 0 -2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-6"/><path d="M11 13l9 -9"/><path d="M15 4h5v5"/></svg>',
  download: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2"/><path d="M7 11l5 5l5 -5"/><path d="M12 4l0 12"/></svg>',
  pencil: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4l10.5 -10.5a2.828 2.828 0 1 0 -4 -4l-10.5 10.5v4"/><path d="M13.5 6.5l4 4"/></svg>',
  power: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 6a7.75 7.75 0 1 0 10 0"/><path d="M12 4l0 8"/></svg>',
  dots: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/><path d="M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/><path d="M19 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/></svg>',
};

function fmtVal(v) {
  if (v === undefined) return "–";
  if (v === "__missing__") return "missing";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}
// First cell of a row about a setting: readable name, then the note (what is wrong), then the path in small
// monospace. The path stays visible because the policy is written in paths.
const settingCell = (r, note) => `<td><div class="fw-medium">${esc(r.label || r.path)}</div>${note ? `<div>${esc(note)}</div>` : ""}${r.label && r.label !== r.path ? `<span class="rr-sub">${esc(r.path)}</span>` : ""}</td>`;
const valHtml = (v) => `<span class="rr-val">${esc(fmtVal(v))}</span>`;

// Times: one helper set for the whole UI. Absolute times follow the browser's locale and time zone
// ("16 Nov 2026, 00:15" or "16.11.2026, 00:15"); log-like tables use the long form with seconds.
// The *Html variants add the full ISO 8601 time with offset as tooltip; tables sort by the timestamp, not the text.
const TIME_FMT = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const TIME_FMT_LONG = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });
function fmtTime(iso, long = false) {
  if (!iso) return "–";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return (long ? TIME_FMT_LONG : TIME_FMT).format(d);
}
const fmtTimeLong = (iso) => fmtTime(iso, true);
// ISO 8601 in local time with offset, e.g. 2026-11-16T00:15:03+01:00
function isoLocal(iso) {
  const d = new Date(iso);
  if (!iso || isNaN(d)) return "";
  const p = (n) => String(Math.abs(n)).padStart(2, "0");
  const off = -d.getTimezoneOffset();
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    + `${off < 0 ? "-" : "+"}${p(Math.trunc(off / 60))}:${p(off % 60)}`;
}
const timeTag = (iso, text) => (isoLocal(iso) ? `<time datetime="${esc(isoLocal(iso))}" title="${esc(isoLocal(iso))}">${esc(text)}</time>` : esc(text));
const timeHtml = (iso) => timeTag(iso, fmtTime(iso));
const timeLongHtml = (iso) => timeTag(iso, fmtTimeLong(iso));
const agoHtml = (iso) => timeTag(iso, ago(iso));
function ago(iso) {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const days = Math.round(s / 86400);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}
function fmtUptime(sec) {
  if (sec == null) return "–";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600);
  return d ? `${d} d ${h} h` : `${h} h ${Math.floor((sec % 3600) / 60)} min`;
}
function rssiBadge(rssi) {
  if (rssi == null) return `<span class="text-secondary">–</span>`;
  return badge(rssi >= -65 ? "ok" : rssi >= -75 ? "warn" : "bad", `${rssi} dBm`);
}
// Device type: one line for filters, sorting and group headings ("Shelly 2PM · Gen2 · Pro"); in tables the
// function comes first (typeLabel, "Shelly 2PM") and generation, variant and suffix follow below (typeSub)
const typeName = (d) => d.model_name || d.app || "?";
// Operating mode of the firmware: profile (cover, switch) and enabled stacks (Matter, Zigbee).
// Own colour, saying nothing about the connection; the tooltip shows the raw value.
const modeBadges = (d) => [d.profile ? badge("mode", d.profile, "Device profile") : "",
  ...(d.stacks || []).map((s) => badge("mode", s.label, s.detail))].filter(Boolean).join(" ");
const typeLabel = (d) => { const m = modeBadges(d); return `${esc(d.type_name || typeName(d))}${m ? ` ${m}` : ""}`; };
const typeSub = (d) => `<span class="rr-sub" title="${esc([d.model, d.app].filter(Boolean).join(" · "))}">${esc(d.type_sub || "")}</span>`;
// Type headings: stacks of the devices in a group, count in the tooltip
function groupStackBadges(devs) {
  const rows = (devs || []).map((x) => data.devices.find((r) => r.id === x.id)).filter(Boolean);
  return [["matter", "Matter"], ["zigbee", "Zigbee"]].map(([key, label]) => {
    const n = rows.filter((r) => (r.stacks || []).some((s) => s.stack === key)).length;
    return n ? ` ${badge("mode", label, `${n} of ${rows.length} devices`)}` : "";
  }).join("");
}
// In Zigbee mode the device may no longer answer over IP: explain instead of just "unreachable"
const stackNote = (d) => (d.last_error || d.stale) && (d.stacks || []).some((s) => s.stack === "zigbee")
  ? `<div class="text-secondary small">Zigbee was on in the last snapshot. In Zigbee mode the device may not answer over IP, so RelayRoster shows that last snapshot.</div>` : "";

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (init.method && init.method !== "GET") init.headers["X-Requested-With"] = "relayroster";
  if (init.json !== undefined) {
    init.body = JSON.stringify(init.json);
    init.headers["Content-Type"] = "application/json";
    delete init.json;
  }
  const keep = init.keep;          // keep: return an error response with body instead of throwing
  delete init.keep;
  const res = await fetch(path, init);
  // Session expired or signed out: to the sign-in page, afterwards back to the same view
  if (res.status === 401) { location.replace(`login${location.hash}`); throw new Error("Signed out"); }
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  // keep "all": return every error response, the caller shows body.errors
  if (!res.ok && res.status !== 422 && !(keep && (keep === "all" || res.status >= 500))) throw new Error(body?.detail || body || res.statusText);
  return { status: res.status, body };
}

function showError(msg) {
  const bar = $("#errorBar");
  bar.textContent = msg || "";
  show(bar, !!msg);
}

// ---- Dialogs ---------------------------------------------------------------------------------
// Convention for every dialog: no X in the header; exactly one close button in the footer, called Cancel before
// the start and Close after it, directly left of the primary button. Esc does the same as that button; a click
// on the backdrop does nothing. Confirmations use confirmDialog with a named primary button, never confirm().
// Resolves true when the user confirms. danger: the action deletes something.
function confirmDialog(text, action, danger = false) {
  let dlg = $("#confirmDlg");
  if (!dlg) {
    document.body.insertAdjacentHTML("beforeend", `<dialog id="confirmDlg" class="rr-dialog rr-dialog-confirm"><div class="card">
      <div class="card-body" id="confirmText"></div>
      <div class="card-footer d-flex gap-2 justify-content-end flex-wrap">
        <button class="btn" type="button" value="">Cancel</button><button class="btn" type="button" value="yes" id="confirmOk"></button>
      </div></div></dialog>`);
    dlg = $("#confirmDlg");
    $$("button", dlg).forEach((b) => b.addEventListener("click", () => dlg.close(b.value)));
  }
  $("#confirmText").textContent = text;
  const ok = $("#confirmOk");
  ok.textContent = action;
  ok.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
  dlg.returnValue = "";
  dlg.showModal();
  ok.focus();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "yes"), { once: true }));
}

// ---- Sortable tables -------------------------------------------------------------------------
// Convention: every table is sortable by every column that has a heading. sortTables() runs after each
// change of the page and turns the headings of all tables into sort buttons (same markup as the device list:
// .table-sort, aria-sort). Click order: ascending, descending, back to the order the table was rendered in.
// - Value of a cell: data-sort, else <time datetime>, else the value of an input, else its text without buttons
// - Comparator: th[data-type] (text, num, ip, version, time), else numbers as numbers and text numeric aware
// - Rows that are a single cell with colspan (details) stay below their row; tr.rr-devrow starts a group
// - State: pages keep it in localStorage per table, dialogs in memory. data-rr-sort names the table,
//   data-rr-sort="off" marks tables that sort their data themselves (device list, Needs attention)
const natCmp = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" }).compare;
const SORT_NUM = /^-?\d+(?:\.\d+)?(?:\s*[a-zA-Z%°]+)?$/;
const SORT_CMP = {
  text: natCmp,
  num: (a, b) => parseFloat(a) - parseFloat(b),
  ip: (a, b) => nullsLast(ipKey(a), ipKey(b)) || (ipKey(a) ?? 0) - (ipKey(b) ?? 0) || natCmp(a, b),
  version: (a, b) => versionCmp(a, b),
  time: (a, b) => Number(a) - Number(b),
  auto: (a, b) => (SORT_NUM.test(a) && SORT_NUM.test(b) ? parseFloat(a) - parseFloat(b) : natCmp(a, b)),
};
const sortMem = new Map();
const sortKey = (t) => t.dataset.rrSort || t.id || t.closest("[id]")?.id || "table";
function sortGet(t) {
  const key = sortKey(t);
  if (sortMem.has(key)) return sortMem.get(key);
  let st = null;
  if (t.closest(".rr-tab")) try { st = JSON.parse(localStorage.getItem(`relayroster.sort.${key}`)); } catch { /* egal */ }
  sortMem.set(key, st);
  return st;
}
function sortSet(t, st) {
  const key = sortKey(t);
  sortMem.set(key, st);
  if (t.closest(".rr-tab")) try { st ? localStorage.setItem(`relayroster.sort.${key}`, JSON.stringify(st)) : localStorage.removeItem(`relayroster.sort.${key}`); } catch { /* egal */ }
}
// Mark the active column of a table: arrows on the button, aria-sort on the th
function sortMark(th, dir) {
  const b = $(".table-sort", th);
  if (!b) return;
  b.classList.toggle("asc", dir === "asc");
  b.classList.toggle("desc", dir === "desc");
  th.setAttribute("aria-sort", dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none");
}
function sortVal(td) {
  if (!td) return "";
  if (td.dataset.sort != null) return td.dataset.sort;
  const tm = $("time[datetime]", td);
  if (tm) return String(Date.parse(tm.dateTime));
  const inp = $("input:not([type=checkbox]), select", td);
  if (inp) return inp.value.trim();
  let el = td;
  if ($("button, .btn", td)) { el = td.cloneNode(true); $$("button, .btn", el).forEach((x) => x.remove()); }
  const s = el.textContent.trim();
  return s === "–" ? "" : s;
}
function sortBody(body, st, type) {
  const rows = [...body.rows];
  if (!rows.length) return;
  let next = Math.max(-1, ...rows.map((r) => r._rrIdx ?? -1)) + 1;
  rows.forEach((r) => { if (r._rrIdx == null) r._rrIdx = next++; });
  // Groups (tr.rr-devrow stays on top of its rows), inside them units of a row plus its detail rows
  const groups = [];
  for (const r of rows) {
    const detail = r.cells.length === 1 && r.cells[0].colSpan > 1;
    if (r.classList.contains("rr-devrow") || !groups.length) groups.push({ head: r.classList.contains("rr-devrow") ? r : null, units: [] });
    const g = groups.at(-1);
    if (r === g.head) continue;
    if (detail && g.units.length) g.units.at(-1).push(r);
    else g.units.push([r]);
  }
  const cmp = SORT_CMP[type] || SORT_CMP.auto;
  const dir = st?.dir === "desc" ? -1 : 1;
  const out = [];
  for (const g of groups) {
    if (g.head) out.push(g.head);
    const units = g.units.map((u) => ({ u, v: st ? sortVal(u[0].cells[st.col]) : "" }));
    // Empty values always last, equal values in the rendered order
    units.sort((a, b) => (st ? (a.v === "") - (b.v === "") || (a.v === "" ? 0 : dir * cmp(a.v, b.v)) : 0) || a.u[0]._rrIdx - b.u[0]._rrIdx);
    units.forEach(({ u }) => out.push(...u));
  }
  if (out.some((r, i) => r !== rows[i])) body.append(...out);
}
function sortTables() {
  sortObserver.disconnect();
  for (const t of $$("table.table")) {
    if (t.dataset.rrSort === "off" || !t.tHead?.rows.length) continue;
    const ths = [...t.tHead.rows[0].cells];
    for (const th of ths) {
      if (th.dataset.rrTh) continue;
      th.dataset.rrTh = "1";
      const label = th.textContent.trim();
      if (!label || $("input, .table-sort", th)) continue;
      // A heading with a link (device columns of the peer comparison) keeps the link and gets a button next to it
      if ($("a", th)) th.insertAdjacentHTML("beforeend", ` <button class="table-sort rr-sort-only" type="button" aria-label="Sort by ${esc(label)}"></button>`);
      else th.innerHTML = `<button class="table-sort" type="button">${th.innerHTML}</button>`;
    }
    let st = sortGet(t);
    if (st && !$(".table-sort", ths[st.col] || t)) st = null;
    ths.forEach((th, i) => sortMark(th, st && st.col === i ? st.dir : ""));
    for (const body of t.tBodies) sortBody(body, st, st ? ths[st.col].dataset.type : "");
  }
  sortObserver.observe(document.body, { childList: true, subtree: true });
}
let sortQueued = false;
const sortObserver = new MutationObserver(() => {
  if (sortQueued) return;
  sortQueued = true;
  queueMicrotask(() => { sortQueued = false; sortTables(); });
});
document.addEventListener("DOMContentLoaded", sortTables);
document.addEventListener("click", (ev) => {
  const b = ev.target.closest?.(".table-sort");
  const t = b?.closest("table");
  if (!t || t.dataset.rrSort === "off") return;
  const col = b.closest("th").cellIndex, st = sortGet(t);
  sortSet(t, !st || st.col !== col ? { col, dir: "asc" } : st.dir === "asc" ? { col, dir: "desc" } : null);
  sortTables();
});

// ---- Zustand ---------------------------------------------------------------------------------

let data = { devices: [], findings: [], peers: [], firmware: [], policy_errors: [] };
let driftKind = "";
let wasRunning = false;
let lastFound = -1;
let stateTimer = null;
let allowRename = false;
let allowFirmware = false;
let allowReboot = false;
let allowSetPassword = false;
let allowConfig = false;

const num = (n) => Number(n || 0).toLocaleString();
function fmtDuration(sec) {
  if (sec == null) return "";
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return `${m}:${String(s).padStart(2, "0")} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
function fmtEta(sec) {
  if (sec == null) return "estimating time left …";
  if (sec < 60) return "less than 1 min left";
  return `about ${Math.round(sec / 60)} min left`;
}

function renderRun(st) {
  const card = $("#runCard");
  const prog = $("#progress");
  show(card, st.running);
  show(prog, st.running);
  if (!st.running) return;
  const pct = st.total ? Math.min(100, Math.floor((st.done / st.total) * 100)) : 0;
  const scan = st.phase === "scan";
  $("#runTitle").textContent = scan ? (st.cancelling ? "Cancelling network scan …" : "Network scan running") : "Reading devices";
  $("#runDetail").textContent = scan ? (st.scan_targets || []).join(", ") : "Configuration, status, scripts, schedules, webhooks, KVS";
  $("#runPct").textContent = `${pct} %`;
  $("#runBar").style.width = `${pct}%`;
  $("#runBar").classList.toggle("bg-red", !!st.cancelling);
  $("#runProgress").setAttribute("aria-valuenow", String(pct));
  $("#runCount").textContent = scan
    ? `${num(st.done)} of ${num(st.total)} addresses checked · ${num(st.found)} ${st.found === 1 ? "Shelly" : "Shellies"} found`
    : `${num(st.done)} of ${num(st.total)} devices read`;
  const elapsed = st.elapsed_s != null ? `running for ${fmtDuration(st.elapsed_s)}` : "";
  $("#runEta").textContent = [elapsed, st.done < st.total ? fmtEta(st.eta_s) : ""].filter(Boolean).join(" · ");
  show($("#cancelBtn"), scan);
  $("#cancelBtn").disabled = !!st.cancelling;
  // Header: compact version
  $("#syncText").textContent = scan ? `Network scan ${pct} % · ${num(st.found)} found` : `Reading devices … ${st.done}/${st.total}`;
  $("#progressBar").style.width = `${pct}%`;
}

// No sign-in configured: say so on every page, the app is open to everyone who can reach it
function renderAuthWarning(st) {
  const bar = $("#authWarn");
  bar.innerHTML = st.auth ? "" : `<div class="fw-bold mb-1">No sign-in configured</div>
    <div>Anyone who can reach this address can use RelayRoster, including every write function switched on in
    <a href="#settings">Settings</a>. Fine for a first look in your own network; set up sign-in before you keep it
    running: <a href="https://github.com/Detmud/RelayRoster/blob/main/docs/security.md#setting-up-oidc"
    target="_blank" rel="noopener">Setting up OIDC</a>.</div>`;
  show(bar, !st.auth);
}

function renderScanWarnings(st) {
  const bar = $("#scanWarn");
  const w = st.scan_warnings || [];
  bar.innerHTML = w.length
    ? `<div class="fw-bold mb-1">Check the networks in <a href="#settings">Settings</a></div>${w.map((x) => `<div>${esc(x)}</div>`).join("")}`
    : "";
  show(bar, w.length > 0);
}

async function pollState() {
  clearTimeout(stateTimer);
  try {
    const { body: st } = await api("api/state");
    $("#version").textContent = `v${st.version}`;
    setMode(!!st.allow_config, !!st.allow_firmware_update);   // naming plan depends on Allow config changes
    allowReboot = !!st.allow_reboot;
    allowSetPassword = !!st.allow_set_password;
    if (allowAddress !== !!st.allow_address) { allowAddress = !!st.allow_address; renderDrift(); }
    if (allowConfig !== !!st.allow_config) {
      allowConfig = !!st.allow_config;
      if (currentPage === "device" && devState.body && devState.tab === "settings" && !cfgDirty()) renderDeviceTab();
    }
    $("#sidebar").classList.toggle("navbar-folded-hover", !!st.sidebar_folded);   // takes effect right after Settings
    show($("#signOutItem"), !!st.auth);
    $("#signOutUser").textContent = st.user || "";
    fwParallel = st.firmware_parallel || 1;
    renderFwJobBar(st.firmware_job);
    renderApplyJobBar(st.apply_job);
    renderAddrJobBar(st.address_job);
    const targets = (st.scan_targets || []).join(", ") || "no valid network";
    $("#netList").textContent = targets;
    $("#scanHint").textContent = `${targets} · ${num(st.scan_addresses)} addresses`;
    renderAuthWarning(st);
    renderScanWarnings(st);
    renderRun(st);
    if (!st.running) {
      const s = st.last_summary || {};
      let txt = `Synced ${ago(st.last_finished)}`;
      if (s.cancelled) txt += ` · scan cancelled`;
      if (s.changed) txt += ` · ${s.changed} changed`;
      if (s.errors) txt += ` · ${s.errors} error${s.errors === 1 ? "" : "s"}`;
      $("#syncText").textContent = txt;
      $("#syncText").title = isoLocal(st.last_finished);
    }
    // The menu arrow stays usable (ZIP download also works during a run)
    $("#syncBtn").disabled = $("#scanBtn").disabled = st.running;
    showError(st.last_error ? `Last sync failed: ${st.last_error}` : "");
    // Show new finds in the device list already during the scan
    if (st.running && st.phase === "scan" && st.found !== lastFound) {
      if (lastFound >= 0 && st.found > lastFound) await loadOverview();
      lastFound = st.found;
    }
    if (wasRunning && !st.running) { lastFound = -1; await loadOverview(); }
    wasRunning = st.running;
    const fwRunning = !!st.firmware_job?.running;
    if (fwWasRunning && !fwRunning) await loadOverview();
    fwWasRunning = fwRunning;
    const addrRunning = !!st.address_job?.running;   // devices have new addresses
    if (addrWasRunning && !addrRunning) await loadOverview();
    addrWasRunning = addrRunning;
    const applyRunning = !!st.apply_job?.running;   // reload findings after writing
    if (applyWasRunning && !applyRunning) {
      await loadOverview();
      bulk.loaded = false;
      if (currentPage === "device" && devState.id) refreshDevicePage();
    }
    applyWasRunning = applyRunning;
    stateTimer = setTimeout(pollState, st.running ? 1000 : fwRunning || applyRunning || addrRunning ? 2000 : 30000);
  } catch (e) {
    showError(`Server unreachable: ${e.message}`);
    stateTimer = setTimeout(pollState, 10000);
  }
}

async function refresh(scan) {
  try {
    await api(`api/refresh?scan=${scan ? "true" : "false"}`, { method: "POST" });
    wasRunning = true;
    setTimeout(pollState, 300);
  } catch (e) { showError(e.message); }
}

// On the device page Sync reads only this device
async function syncDevice(id) {
  const btn = $("#syncBtn");
  btn.disabled = true;
  try {
    const { body } = await api(`api/devices/${encodeURIComponent(id)}/refresh`, { method: "POST" });
    if (!body.ok) showError(`Sync failed: ${body.error || "device did not answer"}`);
    await loadOverview();
    if (currentPage === "device" && devState.id === id) await loadDevicePage(id);
  } catch (e) { showError(e.message); }
  btn.disabled = false;
}

async function cancelScan() {
  $("#cancelBtn").disabled = true;
  try {
    await api("api/scan/cancel", { method: "POST" });
    setTimeout(pollState, 200);
  } catch (e) { showError(e.message); $("#cancelBtn").disabled = false; }
}

async function loadOverview() {
  const { body } = await api("api/overview");
  data = body;
  render();
}

// ---- Rendering -------------------------------------------------------------------------------

// ---- Dashboard -------------------------------------------------------------------------

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const isDown = (d) => !!(d.last_error || d.stale);

function setStat(id, n, sub, hotClass) {
  const el = $(id);
  el.textContent = n;
  if (hotClass) el.classList.toggle(hotClass, n > 0);
  $(`${id}Sub`).textContent = sub;
}

// Problems per device, most severe first: unreachable, deviations, firmware update
function attention(d) {
  const items = [];
  if (d.last_error) items.push({ rank: 0, html: badge("bad", "unreachable", d.last_error) });
  else if (d.stale) items.push({ rank: 1, html: badge("warn", "stale", `Last successful read ${fmtTime(d.last_ok)}`) });
  if (d.findings) items.push({ rank: 2, html: badge("warn", plural(d.findings, "finding")) });
  if (d.updates?.stable) items.push({ rank: 3, html: badge("info", `update → ${d.updates.stable}`) });
  return items;
}

const ATTN_MAX = 10;
// Needs attention sorts its data (not the rendered rows), because only the first ATTN_MAX rows are shown.
// Default: most severe issue first. Kept in memory only.
const ATTN_SORT = {
  address: (a, b) => SORT_CMP.ip(a.d.host, b.d.host),
  name: (a, b) => byName(a.d, b.d),
  issue: (a, b) => a.items[0].rank - b.items[0].rank || (b.d.findings || 0) - (a.d.findings || 0),
  status: (a, b) => STATUS_RANK[statusKey(a.d)] - STATUS_RANK[statusKey(b.d)],
};
let attnSort = { key: "issue", dir: "asc" };

function renderDashboard(devs, peerDiffs, updates, down) {
  const counts = { ok: 0, new: 0 };
  devs.forEach((d) => { const k = statusKey(d); if (k in counts) counts[k] += 1; });
  const withFindings = devs.filter((d) => d.findings).length;
  const peerTypes = data.peers.filter((g) => g.diffs.length).length;
  setStat("#cDevices", devs.length, devs.length ? [`${counts.ok} OK`, counts.new ? `${counts.new} new` : ""].filter(Boolean).join(" · ") : "Scan the network to start");
  setStat("#cFindings", data.findings.length, data.findings.length ? `on ${plural(withFindings, "device")}` : "All match the policy", "text-yellow");
  setStat("#cPeers", peerDiffs, peerDiffs ? `in ${plural(peerTypes, "device type")}` : "All consistent", "text-orange");
  setStat("#cUpdates", updates, updates ? "Stable available" : "All up to date", "text-blue");
  setStat("#cDown", down, down ? "Error or stale" : "All reachable", "text-red");

  const rows = devs.map((d) => ({ d, items: attention(d) })).filter((r) => r.items.length)
    .sort((a, b) => (attnSort.dir === "desc" ? -1 : 1) * ATTN_SORT[attnSort.key](a, b) || ATTN_SORT.issue(a, b) || byName(a.d, b.d));
  $$("#attnTable .table-sort").forEach((b) => sortMark(b.closest("th"), b.dataset.sort === attnSort.key ? attnSort.dir : ""));
  $("#attnTable tbody").innerHTML = rows.slice(0, ATTN_MAX).map(({ d, items }) => `
    <tr class="cursor-pointer" data-id="${esc(d.id)}">
      <td class="text-nowrap"><a class="font-monospace small" href="http://${esc(d.host)}/" target="_blank" rel="noopener noreferrer" data-stop>${esc(d.host)}</a></td>
      <td><div class="fw-bold">${devLink(d.id, d.name || "(no name)")}</div><span class="rr-sub">${esc(typeName(d))}</span></td>
      <td><div class="d-flex flex-wrap gap-1">${items.map((i) => i.html).join("")}</div></td>
      <td class="text-end d-none d-sm-table-cell">${statusBadge(d)}</td>
    </tr>`).join("");
  show($("#attnTable"), rows.length > 0);
  show($("#attnEmpty"), devs.length > 0 && rows.length === 0);
  const more = $("#attnMore");
  show(more, rows.length > ATTN_MAX);
  $("a", more).textContent = `All ${plural(rows.length, "device")} with issues`;
  const cnt = $("#attnCount");
  cnt.textContent = rows.length;
  show(cnt, rows.length > 0);

  // Device types with counts and installed versions; a click on the type filters the device list
  const types = new Map();
  devs.forEach((d) => {
    const t = typeName(d);
    if (!types.has(t)) types.set(t, { n: 0, vers: new Set() });
    const e = types.get(t);
    e.n += 1;
    if (d.ver) e.vers.add(d.ver);
  });
  const fleet = [...types.entries()].sort((a, b) => b[1].n - a[1].n || a[0].localeCompare(b[0], "en", { numeric: true }));
  $("#typesTable tbody").innerHTML = fleet.map(([t, e]) => {
    const vers = [...e.vers].sort(versionCmp);
    const ver = vers.length > 1 ? badge("warn", `${vers.length} versions`, vers.join(", ")) : `<span class="font-monospace small">${esc(vers[0] || "–")}</span>`;
    // Firmware sorts by the number of installed versions, then by the newest one
    return `<tr>
      <td><a href="#devices?${esc(new URLSearchParams({ type: t }).toString())}">${esc(t)}</a></td>
      <td class="text-end">${e.n}</td>
      <td data-sort="${vers.length} ${esc(vers.at(-1) || "")}">${ver}</td>
    </tr>`;
  }).join("");
  show($("#typesTable"), fleet.length > 0);
  show($("#typesEmpty"), fleet.length === 0);
}

function render() {
  const devs = data.devices;
  const peerDiffs = data.peers.reduce((n, g) => n + g.diffs.length, 0);
  const updates = devs.filter((d) => d.updates?.stable).length;
  const down = devs.filter(isDown).length;
  renderDashboard(devs, peerDiffs, updates, down);

  const pb = $("#policyBar");
  if (data.policy_errors?.length) {
    pb.innerHTML = `<h4 class="alert-title">The policy is invalid</h4>Findings are not evaluated:<ul class="mb-0">${data.policy_errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
    show(pb, true);
  } else show(pb, false);

  setNavCount("devices", down, "bg-red-lt", "unreachable");
  setNavCount("drift", data.findings.length, "bg-yellow-lt", "findings");
  setNavCount("peers", peerDiffs, "bg-orange-lt", "differences");
  setNavCount("firmware", updates, "bg-blue-lt", "updates available");
  renderDevices();
  renderDrift();
  renderPeers();
  renderFirmware();
  renderBackups();
  refreshDevicePage();
}

// Counter badge on the right of the sidebar menu entry
function setNavCount(page, n, cls, what) {
  const link = $(`#sidebar .nav-link[data-page="${page}"]`);
  let c = $(".rr-count", link);
  if (!c) { c = document.createElement("span"); c.className = `badge ${cls} rr-count`; link.appendChild(c); }
  c.textContent = n || "";
  c.title = n ? `${n} ${what}` : "";
  show(c, !!n);
}

function statusBadge(d) {
  if (d.last_error) return badge("bad", "error", d.last_error);
  if (!d.has_snapshot) return badge("neutral", "new");
  if (d.stale) return badge("warn", "stale", `Last successful read ${fmtTime(d.last_ok)}`);
  return badge("ok", "ok");
}
