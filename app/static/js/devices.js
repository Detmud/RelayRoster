"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Device list: sorting and filtering ------------------------------------------------------
// The state lives in the URL (#devices?sort=address&type=Shelly+Plus+i4) and in localStorage. The URL wins,
// so a shared link opens exactly this view.

const FILTERS = { type: "#fApp", status: "#fStatus", address: "#fAddress", findings: "#fFindings", firmware: "#fFirmware", auth: "#fAuth", stack: "#fStack" };
const VIEW_DEFAULT = { q: "", sort: "name", dir: "asc", type: "", status: "", address: "", findings: "", firmware: "", auth: "", stack: "" };
let view = { ...VIEW_DEFAULT };

const statusKey = (d) => d.last_error ? "error" : !d.has_snapshot ? "new" : d.stale ? "stale" : "ok";
const STATUS_RANK = { error: 0, stale: 1, new: 2, ok: 3 };

function ipKey(host) {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(host || "");
  return m ? m.slice(1).reduce((n, o) => n * 256 + Number(o), 0) : null;
}

// Like compare.version_key in the backend: numeric per part, pre-release (1.7.6-beta1) before the release
function versionCmp(a, b) {
  if (!a || !b) return (a ? 1 : 0) - (b ? 1 : 0);
  const parse = (v) => {
    const [main, pre] = String(v).split("-", 2);
    return { nums: main.split(/[^0-9]+/).filter(Boolean).map(Number), pre: pre ?? null };
  };
  const x = parse(a), y = parse(b);
  for (let i = 0; i < Math.max(x.nums.length, y.nums.length); i++) {
    const d = (x.nums[i] ?? 0) - (y.nums[i] ?? 0);
    if (d) return d;
  }
  if (x.pre === y.pre) return 0;
  if (x.pre === null) return 1;
  if (y.pre === null) return -1;
  return x.pre.localeCompare(y.pre, "en", { numeric: true });
}

const byName = (a, b) => (a.name || a.id).localeCompare(b.name || b.id, "en", { sensitivity: "base", numeric: true });
const nullsLast = (x, y) => (x == null) - (y == null);
const SORTERS = {
  name: byName,
  type: (a, b) => `${typeName(a)} ${a.profile || ""}`.localeCompare(`${typeName(b)} ${b.profile || ""}`, "en", { numeric: true }),
  address: (a, b) => {
    const x = ipKey(a.host), y = ipKey(b.host);
    if (x == null || y == null) return nullsLast(x, y) || String(a.host).localeCompare(String(b.host));
    return x - y;
  },
  firmware: (a, b) => versionCmp(a.ver, b.ver),
  wifi: (a, b) => nullsLast(a.rssi, b.rssi) || (a.rssi ?? 0) - (b.rssi ?? 0),
  findings: (a, b) => (a.findings || 0) - (b.findings || 0),
  status: (a, b) => STATUS_RANK[statusKey(a)] - STATUS_RANK[statusKey(b)],
};

function matches(d) {
  const q = view.q.trim().toLowerCase();
  // MAC also findable without colons (1020ba000201), as it appears in the device ID
  if (q && ![d.name, d.id, d.app, d.model, d.model_name, d.host, d.profile, d.mac, (d.mac || "").replace(/:/g, ""), ...(d.profiles || [])]
    .some((x) => String(x ?? "").toLowerCase().includes(q))) return false;
  if (view.type && typeName(d) !== view.type) return false;
  // "down" like the Unreachable tile on the dashboard: error or stale
  if (view.status === "down" ? !isDown(d) : view.status && statusKey(d) !== view.status) return false;
  if (view.address) {
    const tag = d.address?.tag || "ok";
    if (view.address === "any" ? tag === "ok" : tag !== view.address) return false;
  }
  if (view.findings === "with" && !d.findings) return false;
  if (view.findings === "none" && d.findings) return false;
  if (view.firmware === "update" && !d.updates?.stable) return false;
  if (view.firmware === "current" && d.updates?.stable) return false;
  if (view.auth === "open" && d.auth_en) return false;
  if (view.auth === "set" && !d.auth_en) return false;
  const st = (d.stacks || []).map((s) => s.stack);
  if (view.stack === "wifi" ? st.length : view.stack && !st.includes(view.stack)) return false;
  return true;
}

function sortedDevices(list) {
  const cmp = SORTERS[view.sort] || byName;
  const dir = view.dir === "desc" ? -1 : 1;
  // With equal values always by name, so the order stays stable
  return [...list].sort((a, b) => dir * cmp(a, b) || byName(a, b));
}

const viewIsFiltered = () => Object.keys(FILTERS).some((k) => view[k]) || !!view.q.trim();

function viewToHash() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(view)) if (v && v !== VIEW_DEFAULT[k]) p.set(k, v);
  const qs = p.toString();
  return `#devices${qs ? `?${qs}` : ""}`;
}

function viewFromParams(params) {
  const v = { ...VIEW_DEFAULT };
  for (const k of Object.keys(VIEW_DEFAULT)) if (params.has(k)) v[k] = params.get(k);
  if (!SORTERS[v.sort]) v.sort = "name";
  if (v.dir !== "desc") v.dir = "asc";
  return v;
}

function loadView() {
  const h = location.hash.slice(1);
  if (h.startsWith("devices")) {
    const i = h.indexOf("?");
    return viewFromParams(new URLSearchParams(i >= 0 ? h.slice(i + 1) : ""));
  }
  try {
    const raw = localStorage.getItem("relayroster.devview");
    if (raw) return viewFromParams(new URLSearchParams(raw));
  } catch { /* egal */ }
  return { ...VIEW_DEFAULT };
}

function saveView() {
  const hash = viewToHash();
  if (currentPage === "devices" && location.hash !== hash) {
    history.replaceState(null, "", hash);
  }
  try { localStorage.setItem("relayroster.devview", hash.split("?")[1] || ""); } catch { /* egal */ }
}

function syncViewControls() {
  $("#devFilter").value = view.q;
  for (const [k, sel] of Object.entries(FILTERS)) {
    const el = $(sel);
    // Type from a shared link that does not exist (yet): show it anyway instead of silently ignoring it
    if (view[k] && ![...el.options].some((o) => o.value === view[k])) el.add(new Option(view[k], view[k]));
    el.value = view[k];
    el.classList.toggle("rr-active", !!view[k]);
  }
  $$("#devTable .table-sort").forEach((b) => {
    const on = b.dataset.sort === view.sort;
    b.classList.toggle("asc", on && view.dir === "asc");
    b.classList.toggle("desc", on && view.dir === "desc");
    b.closest("th").setAttribute("aria-sort", on ? (view.dir === "asc" ? "ascending" : "descending") : "none");
  });
}

function fillTypeFilter() {
  const sel = $("#fApp");
  const apps = [...new Set(data.devices.map(typeName))].sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
  const counts = Object.fromEntries(apps.map((a) => [a, data.devices.filter((d) => typeName(d) === a).length]));
  sel.innerHTML = `<option value="">All types</option>${apps.map((a) => `<option value="${esc(a)}">${esc(a)} (${counts[a]})</option>`).join("")}`;
}

function setView(patch) {
  view = { ...view, ...patch };
  saveView();
  renderDevices();
}

function renderDevices() {
  fillTypeFilter();
  syncViewControls();
  const total = data.devices.length;
  const rows = sortedDevices(data.devices.filter(matches));
  const filtered = viewIsFiltered();
  show($("#devEmpty"), total === 0);
  show($("#devNoMatch"), total > 0 && rows.length === 0);
  show($("#devReset"), filtered);
  $("#devCount").textContent = total ? (filtered ? `${rows.length} of ${total} devices` : `${total} device${total === 1 ? "" : "s"}`) : "";
  $("#devTable tbody").innerHTML = rows.map((d) => `
    <tr class="cursor-pointer" data-id="${esc(d.id)}">
      <td><div class="fw-bold">${devLink(d.id, d.name || "(no name)")}</div><span class="rr-sub" title="${esc(d.id)}">${esc(d.mac || d.id)}</span></td>
      <td>${typeLabel(d)}<span class="rr-sub">${esc(d.model || "")} · Gen${esc(d.gen ?? "?")}</span></td>
      <td class="text-nowrap"><a class="font-monospace small" href="http://${esc(d.host)}/" target="_blank" rel="noopener noreferrer" data-stop>${esc(d.host)}</a>${addressBadge(d)}</td>
      <td class="font-monospace small">${esc(d.ver || "–")}${d.updates?.stable ? ` ${badge("info", `→ ${d.updates.stable}`, "Stable update available")}` : ""}</td>
      <td>${rssiBadge(d.rssi)}</td>
      <td>${(d.profiles || []).map((p) => badge("accent", p)).join(" ") || `<span class="text-secondary">–</span>`}</td>
      <td class="text-end">${d.findings ? badge("warn", d.findings) : `<span class="text-secondary">0</span>`}</td>
      <td>${statusBadge(d)}${d.auth_en ? "" : ` ${badge("bad", "open", "No device password set")}`}</td>
    </tr>`).join("");
}

const KIND_LABEL = { mismatch: "setting", missing: "missing", firmware: "firmware", calibration: "calibration", security: "security", network: "address", name: "name" };
const KIND_BADGE = { mismatch: "warn", missing: "warn", firmware: "info", calibration: "warn", security: "bad", network: "accent", name: "info" };
// Short label next to the IP in the device list, the long text is in the tooltip
const ADDR_BADGE = { pool: ["warn", "DHCP"], outside: ["warn", "foreign network"], block: ["warn", "wrong block"], noblock: ["neutral", "no block"] };
function addressBadge(d) {
  if (!d.address) return "";
  const [kind, text] = ADDR_BADGE[d.address.tag] || ["warn", "address"];
  return ` ${badge(kind, text, d.address.note)}`;
}

// Same deviation (path, rule, target) on several devices: "Fix on all"
const fixKey = (f) => `${f.path}|${f.source}|${JSON.stringify(f.target)}`;
function fixGroups() {
  const groups = new Map();
  for (const f of data.findings) {
    if (f.kind !== "mismatch" || !f.fixable) continue;
    if (!groups.has(fixKey(f))) groups.set(fixKey(f), []);
    groups.get(fixKey(f)).push(f.device_id);
  }
  return groups;
}

// Source text is left out when it only repeats the badge (firmware findings: "Firmware")
const sameWord = (a, b) => String(a || "").toLowerCase() === String(b || "").toLowerCase();

function findingRows(fs, withNote, groups = null) {
  return fs.map((f) => {
    const ids = groups && f.kind === "mismatch" && f.fixable ? groups.get(fixKey(f)) || [] : null;
    return `<tr>
    <td class="font-monospace small">${esc(f.path)}${withNote && f.note ? `<span class="rr-sub">${esc(f.note)}</span>` : ""}</td>
    <td>${valHtml(f.expected)}</td>
    <td>${f.kind === "missing" ? `<span class="text-secondary">not present</span>` : valHtml(f.actual)}</td>
    <td>${badge(KIND_BADGE[f.kind], KIND_LABEL[f.kind] || f.kind)}${sameWord(f.source, KIND_LABEL[f.kind] || f.kind) ? "" : ` <span class="text-secondary small">${esc(f.source)}</span>`}</td>
    ${groups ? `<td class="text-end text-nowrap">${ids ? `<button class="btn btn-sm" type="button" data-fix="${esc(fixKey(f))}"
      title="Write ${esc(fmtVal(f.target))} to ${esc(f.path)} on every device with this finding">${ids.length > 1 ? `Fix on all (${ids.length})` : "Fix"}</button>` : ""}</td>` : ""}
  </tr>`;
  }).join("");
}

// Rules whose path exists on no device, e.g. ble.enable since firmware 2.0
function renderStaleBar() {
  const stale = data.stale_rules || [];
  const bar = $("#staleBar");
  show(bar, stale.length > 0);
  bar.innerHTML = stale.length ? `<strong>${plural(stale.length, "rule")} without effect.</strong>
    No device has ${stale.length === 1 ? "this setting" : "these settings"}, often because a newer firmware renamed or dropped it. Remove the rule or fix the path in <a href="#policy">Policy</a>.
    <ul class="mb-0 mt-2">${stale.map((r, i) => `<li><code>${esc(r.path)}</code> <span class="text-secondary small">${esc(r.source)}${r.device_id ? ` · ${esc(r.device_id)}` : ""}</span>
      <button class="btn btn-sm ms-2" type="button" data-stale-del="${i}">Remove rule</button></li>`).join("")}</ul>` : "";
}

async function removeStaleRule(r) {
  if (!confirm(`Remove ${r.path} (${r.source}) from policy.yaml? The previous version is kept as a backup.`)) return;
  const { status, body } = await api("api/policy/rule/remove", { method: "POST", json: { source: r.source, path: r.path, device_id: r.device_id }, keep: "all" });
  if (status >= 400) { showError((body.errors || [body.detail]).join(" · ")); return; }
  policyLoaded = false;
  prof.loaded = false;
  await loadOverview();
}

function renderPlanBar() {
  const bar = $("#planBar");
  const n = data.findings.filter((f) => f.kind === "name").length;
  const unknown = data.names_unknown || [];
  const parts = [];
  if (n) {
    parts.push(`<div class="d-flex align-items-center gap-3 flex-wrap"><div class="flex-fill rr-minw0">
      <strong>${n} device${n === 1 ? "" : "s"}</strong> differ${n === 1 ? "s" : ""} from the naming plan (<code>names:</code> in the policy).
      ${allowRename ? "" : `<span class="text-secondary">Turn on config changes in <a href="#settings">Settings</a> to write the names to the devices.</span>`}</div>
      ${allowRename ? `<button class="btn btn-primary btn-sm" id="planOpen" type="button">Apply naming plan</button>` : ""}</div>`);
  }
  if (unknown.length) {
    parts.push(`<div class="${n ? "mt-2" : ""}">The naming plan lists device IDs RelayRoster does not know: ${unknown.map((u) => `<code>${esc(u)}</code>`).join(", ")}. Check for typos.</div>`);
  }
  bar.innerHTML = parts.join("");
  bar.classList.toggle("alert-warning", !n && unknown.length > 0);
  bar.classList.toggle("alert-info", !!n || !unknown.length);
  show(bar, parts.length > 0);
  $("#planOpen")?.addEventListener("click", openPlan);
}

function renderDrift() {
  renderPlanBar();
  renderStaleBar();
  const groups = fixGroups();
  const list = data.findings.filter((f) => !driftKind || f.kind === driftKind);
  show($("#driftEmpty"), list.length === 0);
  const byDev = new Map();
  for (const f of list) {
    if (!byDev.has(f.device_id)) byDev.set(f.device_id, []);
    byDev.get(f.device_id).push(f);
  }
  $("#driftList").innerHTML = [...byDev.entries()].map(([id, fs]) => {
    const d = data.devices.find((x) => x.id === id) || { id };
    return `<div class="card">
      <div class="card-header">
        <div><h3 class="card-title">${devLink(id, d.name || id)}</h3><div class="text-secondary small">${esc(d.model_name || d.app || "")} · <span class="font-monospace" title="${esc(id)}">${esc(d.mac || id)}</span></div></div>
        <div class="card-actions"><a class="btn btn-sm" href="${devHref(id, "findings")}">Details</a></div>
      </div>
      <div class="table-responsive"><table class="table table-vcenter card-table rr-findings">
        <colgroup><col class="rr-c-path"><col class="rr-c-val"><col class="rr-c-val"><col class="rr-c-src"><col class="rr-c-act"></colgroup>
        <thead><tr><th>Path</th><th>Expected</th><th>Actual</th><th>Source</th><th></th></tr></thead>
        <tbody>${findingRows(fs, true, groups)}</tbody>
      </table></div></div>`;
  }).join("");
}

// Peer comparison: one tab per device type. Only types with deviations; uniform types and
// single devices are in a collapsible summary below
let peerSel = null;
try { peerSel = localStorage.getItem("relayroster.peer"); } catch { /* egal */ }
let peerPeek = null;     // uniform type, opened from the summary (not stored)
const peerKey = (g) => `${g.app}|${g.profile || ""}`;
const peerLabel = (g) => `${g.model_name || g.app}${g.profile ? ` · ${g.profile}` : ""}`;
const byPeerLabel = (a, b) => peerLabel(a).localeCompare(peerLabel(b), "en", { numeric: true });

// Hidden differences: without effect, read-only or managed by the device. Collapsible, so
// nothing disappears silently
function peerHiddenBlock(g, cols) {
  const hs = g.hidden || [];
  if (!hs.length) return "";
  const rows = hs.map((r) => `<tr>
    <td class="font-monospace small">${esc(r.path)}<span class="rr-sub">${esc(r.note)}</span></td>
    ${g.devices.map((d) => `<td>${valHtml(r.values[d.id])}</td>`).join("")}</tr>`).join("");
  return `<details class="rr-peer-more border-top">
      <summary class="card-body py-2 text-secondary small">${hs.length} difference${hs.length === 1 ? "" : "s"} hidden (setting has no effect or cannot be changed)</summary>
      <div class="table-responsive"><table class="table table-vcenter card-table rr-hidden">
      <thead><tr><th>Path</th>${cols}</tr></thead><tbody>${rows}</tbody></table></div>
    </details>`;
}

function peerGroupBody(g) {
  const n = g.diffs.length;
  const info = `<div class="card-body border-bottom py-2 d-flex align-items-center gap-2 flex-wrap">
    <div class="text-secondary small flex-fill rr-minw0">${g.devices.length} devices · firmware ${g.versions.map(esc).join(", ")}</div>
    ${n ? badge("warn", `${n} difference${n === 1 ? "" : "s"}`) : badge("ok", "uniform")}</div>`;
  const cols = g.devices.map((d) => `<th title="${esc(d.id)}">${devLink(d.id, d.name || d.id)}</th>`).join("");
  const extra = peerHiddenBlock(g, cols);
  if (!n) return `${info}<div class="card-body text-secondary">All compared settings are identical.</div>${extra}`;
  const rows = g.diffs.map((r) => `<tr>
    <td class="font-monospace small">${esc(r.path)}<span class="rr-sub">${r.has_majority ? `majority: ${esc(fmtVal(r.majority))}` : "no majority"}</span></td>
    ${g.devices.map((d) => {
      const v = r.values[d.id];
      const cls = [r.outliers.includes(d.id) ? "rr-outlier" : "", v === "__missing__" ? "rr-missing" : ""].join(" ").trim();
      return `<td class="${cls}">${valHtml(v)}</td>`;
    }).join("")}</tr>`).join("");
  return `${info}<div class="table-responsive"><table class="table table-vcenter card-table">
    <thead><tr><th>Path</th>${cols}</tr></thead><tbody>${rows}</tbody></table></div>${extra}`;
}

// Summary below the tabs: uniform types (click shows the comparison) and single devices (click: detail)
function peerSummary(uniform, single, open) {
  if (!uniform.length && !single.length) return "";
  const parts = [];
  if (uniform.length) parts.push(`${uniform.length} type${uniform.length === 1 ? "" : "s"} uniform`);
  if (single.length) parts.push(`${single.length} type${single.length === 1 ? "" : "s"} with a single device`);
  const note = (g) => [
    (g.hidden || []).length ? `${g.hidden.length} hidden` : "",
  ].filter(Boolean).join(" · ");
  const rows = [
    ...uniform.map((g) => `<tr class="cursor-pointer${peerKey(g) === peerPeek ? " table-active" : ""}" data-peer-peek="${esc(peerKey(g))}">
      <td>${esc(g.model_name || g.app)}${g.profile ? ` ${badge("mode", g.profile)}` : ""}${groupStackBadges(g.devices)}</td>
      <td>${g.devices.length} devices</td><td class="text-secondary small">${esc(note(g) || "identical")}</td></tr>`),
    ...single.map((g) => `<tr class="cursor-pointer" data-id="${esc(g.devices[0].id)}">
      <td>${esc(g.model_name || g.app)}${g.profile ? ` ${badge("mode", g.profile)}` : ""}${groupStackBadges(g.devices)}</td>
      <td>${devLink(g.devices[0].id, g.devices[0].name || "(no name)")}</td><td class="text-secondary small">nothing to compare</td></tr>`),
  ].join("");
  return `<details class="rr-peer-more border-top" id="peerSummary"${open ? " open" : ""}>
      <summary class="card-body py-2 text-secondary small">${parts.join(" · ")}</summary>
      <div class="table-responsive"><table class="table table-vcenter card-table table-hover">
      <thead><tr><th>Type</th><th>Devices</th><th>Note</th></tr></thead><tbody>${rows}</tbody></table></div>
    </details>`;
}

function renderPeers() {
  const box = $("#peerList");
  const groups = data.peers;
  if (!groups.length) {
    box.innerHTML = `<div class="card"><div class="empty"><p class="empty-title">No devices read yet</p></div></div>`;
    return;
  }
  // Types with the most deviations first, ties by name
  const multi = groups.filter((g) => g.devices.length > 1);
  const differing = multi.filter((g) => g.diffs.length).sort((a, b) => b.diffs.length - a.diffs.length || byPeerLabel(a, b));
  const uniform = multi.filter((g) => !g.diffs.length).sort(byPeerLabel);
  const single = groups.filter((g) => g.devices.length === 1).sort(byPeerLabel);
  const summaryOpen = $("#peerSummary", box)?.open || false;
  const peek = uniform.find((g) => peerKey(g) === peerPeek);
  if (!peek) peerPeek = null;
  // Stored tab points to a type without deviations by now: fall back to the first one with deviations
  if (!differing.some((g) => peerKey(g) === peerSel)) peerSel = differing[0] ? peerKey(differing[0]) : null;

  const scrollX = $(".rr-peer-tabs", box)?.scrollLeft || 0;
  let head, body;
  if (differing.length) {
    head = `<div class="card-header rr-peer-head"><ul class="nav nav-pills rr-peer-tabs" role="tablist" aria-label="Device types">${differing.map((g) => {
      const on = !peek && peerKey(g) === peerSel;
      const n = g.diffs.length;
      return `<li class="nav-item"><button class="nav-link${on ? " active" : ""}" type="button" role="tab" aria-selected="${on}" data-peer="${esc(peerKey(g))}">${esc(peerLabel(g))}${groupStackBadges(g.devices)} ${badge("warn", n, `${n} difference${n === 1 ? "" : "s"}`)}</button></li>`;
    }).join("")}</ul></div>`;
  } else {
    head = `<div class="empty"><p class="empty-title">All device types are uniform</p>
      <p class="empty-subtitle text-secondary">No setting with an effect differs between devices of the same type.</p></div>`;
  }
  if (peek) {
    body = `<div class="card-header py-2"><h3 class="card-title">${esc(peerLabel(peek))}${groupStackBadges(peek.devices)}</h3>
      <div class="card-actions"><button class="btn btn-sm" type="button" data-peer-close>Close</button></div></div>${peerGroupBody(peek)}`;
  } else {
    body = differing.length ? peerGroupBody(differing.find((g) => peerKey(g) === peerSel)) : "";
  }
  box.innerHTML = `<div class="card">${head}${body}${peerSummary(uniform, single, summaryOpen)}</div>`;
  const tabsEl = $(".rr-peer-tabs", box);
  if (tabsEl) tabsEl.scrollLeft = scrollX;
  $$("[data-peer]", box).forEach((b) => b.addEventListener("click", () => {
    peerSel = b.dataset.peer;
    peerPeek = null;
    try { localStorage.setItem("relayroster.peer", peerSel); } catch { /* egal */ }
    renderPeers();
  }));
  $$("[data-peer-peek]", box).forEach((r) => r.addEventListener("click", () => {
    peerPeek = r.dataset.peerPeek === peerPeek ? null : r.dataset.peerPeek;
    renderPeers();
  }));
  $("[data-peer-close]", box)?.addEventListener("click", () => { peerPeek = null; renderPeers(); });
}

// Short values inline, multi-line ones (script code, objects) as a block
function diffVal(v) {
  const s = fmtVal(v);
  return s.includes("\n") || s.length > 60 ? `<pre class="rr-json">${esc(s)}</pre>` : `<span class="rr-val">${esc(s)}</span>`;
}

const fwOffers = () => data.devices.filter((d) => d.updates?.stable);
const fwDisabledHint = () => `<span class="text-secondary small">Turn on firmware updates in <a href="#settings">Settings</a>.</span>`;
const fwBtn = (attr, label, cls = "btn-sm") => allowFirmware
  ? `<button class="btn ${cls} btn-primary" type="button" ${attr} data-stop>${ICON.download}${esc(label)}</button>`
  : `<button class="btn ${cls}" type="button" disabled title="Firmware updates are off. Turn them on in Settings.">${ICON.download}${esc(label)}</button>`;

function renderFirmware() {
  const offers = fwOffers();
  $("#fwBar").innerHTML = offers.length ? `<div class="card mb-3"><div class="card-body d-flex align-items-center gap-3 flex-wrap">
      <div class="flex-fill rr-minw0"><strong>${offers.length} device${offers.length === 1 ? "" : "s"}</strong> with a stable update.
        ${allowFirmware ? "" : fwDisabledHint()}</div>
      ${fwBtn('data-fw-open="all"', `Update all (${offers.length})`, "")}
    </div></div>` : "";
  if (!data.firmware.length) {
    $("#fwList").innerHTML = `<div class="card"><div class="empty"><p class="empty-title">No devices yet</p></div></div>`;
    return;
  }
  $("#fwList").innerHTML = data.firmware.map((g) => {
    const n = g.devices.filter((d) => d.updates?.stable).length;
    return `<div class="card">
    <div class="card-header">
      <div><h3 class="card-title">${esc(g.model_name || g.app)}${groupStackBadges(g.devices)}</h3><div class="text-secondary small font-monospace">${esc(g.model || "")}</div></div>
      <div class="card-actions btn-list">
        ${g.uniform ? badge("ok", `uniform ${g.newest_installed || ""}`) : badge("warn", "mixed")}
        ${g.stable_available ? badge("info", `stable ${g.stable_available} available`) : ""}
        ${n > 1 ? fwBtn(`data-fw-open="app:${esc(g.app)}"`, `Update ${n}`) : ""}
      </div>
    </div>
    <div class="table-responsive"><table class="table table-vcenter card-table table-hover rr-fw-table">
      <colgroup><col><col class="rr-fw-ver"><col class="rr-fw-ver"><col class="rr-fw-ver"><col class="rr-fw-act"></colgroup>
      <thead><tr><th>Device</th><th>Installed</th><th>Stable available</th><th>Beta available</th><th></th></tr></thead>
      <tbody>${g.devices.map((d) => `<tr class="cursor-pointer" data-id="${esc(d.id)}">
        <td>${devLink(d.id, d.name || "(no name)")}<span class="rr-sub" title="${esc(d.id)}">${esc(d.mac || d.id)}</span></td>
        <td class="font-monospace small">${esc(d.ver || "–")}${d.ver !== g.newest_installed ? ` ${badge("warn", "older")}` : ""}</td>
        <td class="font-monospace small">${esc(d.updates?.stable || "–")}</td>
        <td class="font-monospace small text-secondary">${esc(d.updates?.beta || "–")}</td>
        <td class="text-end">${d.updates?.stable ? fwBtn(`data-fw-open="dev:${esc(d.id)}"`, "Update") : ""}</td></tr>`).join("")}
      </tbody></table></div></div>`;
  }).join("");
}
