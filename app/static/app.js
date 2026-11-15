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
const valHtml = (v) => `<span class="sf-val">${esc(fmtVal(v))}</span>`;

function fmtTime(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" });
}
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
const typeName = (d) => d.model_name || d.app || "?";
// Operating mode of the firmware: profile (cover, switch) and enabled stacks (Matter, Zigbee).
// Own colour, saying nothing about the connection; the tooltip shows the raw value.
const modeBadges = (d) => [d.profile ? badge("mode", d.profile, "Device profile") : "",
  ...(d.stacks || []).map((s) => badge("mode", s.label, s.detail))].filter(Boolean).join(" ");
const typeLabel = (d) => { const m = modeBadges(d); return `${esc(typeName(d))}${m ? ` ${m}` : ""}`; };
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

const num = (n) => Number(n || 0).toLocaleString("en-GB");
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
    if (allowConfig !== !!st.allow_config) {
      allowConfig = !!st.allow_config;
      if (currentPage === "device" && devState.body && devState.tab === "settings" && !cfgDirty()) renderDeviceTab();
    }
    $("#sidebar").classList.toggle("navbar-folded-hover", !!st.sidebar_folded);   // takes effect right after Settings
    show($("#signOutItem"), !!st.auth);
    $("#signOutUser").textContent = st.user || "";
    renderFwJobBar(st.firmware_job);
    renderApplyJobBar(st.apply_job);
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
    const applyRunning = !!st.apply_job?.running;   // reload findings after writing
    if (applyWasRunning && !applyRunning) {
      await loadOverview();
      bulk.loaded = false;
      if (currentPage === "device" && devState.id) refreshDevicePage();
    }
    applyWasRunning = applyRunning;
    stateTimer = setTimeout(pollState, st.running ? 1000 : fwRunning || applyRunning ? 2000 : 30000);
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

function renderDashboard(devs, peerDiffs, updates, down) {
  const counts = { ok: 0, new: 0 };
  devs.forEach((d) => { const k = statusKey(d); if (k in counts) counts[k] += 1; });
  const withFindings = devs.filter((d) => d.findings).length;
  const peerTypes = data.peers.filter((g) => g.diffs.length).length;
  setStat("#cDevices", devs.length, devs.length ? [`${counts.ok} OK`, counts.new ? `${counts.new} new` : ""].filter(Boolean).join(" · ") : "Scan the network to start");
  setStat("#cFindings", data.findings.length, data.findings.length ? `on ${plural(withFindings, "device")}` : "All match the policy", "text-yellow");
  setStat("#cPeers", peerDiffs, peerDiffs ? `in ${plural(peerTypes, "device type")}` : "Types are consistent", "text-orange");
  setStat("#cUpdates", updates, updates ? "Stable available" : "All up to date", "text-blue");
  setStat("#cDown", down, down ? "Error or stale" : "All reachable", "text-red");

  const rows = devs.map((d) => ({ d, items: attention(d) })).filter((r) => r.items.length)
    .sort((a, b) => a.items[0].rank - b.items[0].rank || (b.d.findings || 0) - (a.d.findings || 0)
      || (a.d.name || "").localeCompare(b.d.name || "", "en", { numeric: true }));
  $("#attnTable tbody").innerHTML = rows.slice(0, ATTN_MAX).map(({ d, items }) => `
    <tr class="cursor-pointer" data-id="${esc(d.id)}">
      <td><div class="fw-bold">${devLink(d.id, d.name || "(no name)")}</div><span class="sf-sub">${esc(typeName(d))} · ${esc(d.host)}</span></td>
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
  $("#fleetTable tbody").innerHTML = fleet.map(([t, e]) => {
    const vers = [...e.vers].sort(versionCmp);
    const ver = vers.length > 1 ? badge("warn", `${vers.length} versions`, vers.join(", ")) : `<span class="font-monospace small">${esc(vers[0] || "–")}</span>`;
    return `<tr>
      <td><a href="#devices?${esc(new URLSearchParams({ type: t }).toString())}">${esc(t)}</a></td>
      <td class="text-end">${e.n}</td>
      <td>${ver}</td>
    </tr>`;
  }).join("");
  show($("#fleetTable"), fleet.length > 0);
  show($("#fleetEmpty"), fleet.length === 0);
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
  let c = $(".sf-count", link);
  if (!c) { c = document.createElement("span"); c.className = `badge ${cls} sf-count`; link.appendChild(c); }
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
