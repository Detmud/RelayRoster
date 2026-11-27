"use strict";
// Part of the UI, loads after app.js; shared global scope, order defined in index.html
// ---- Device page ------------------------------------------------------------------------
// Own page under #device/<id>[/<tab>] instead of a dialog: linkable, opens in new tabs, reload stays on the device.
// The header (name, actions) is built on load, the tabs on switching without a new request.

const DEV_TABS = ["overview", "settings", "findings", "backups", "changes"];
const devHref = (id, tab) => `#device/${encodeURIComponent(id)}${tab && tab !== "overview" ? `/${tab}` : ""}`;
// data-stop: a click on the link does not also open the row. Ctrl/Cmd/middle click open a new tab
const devLink = (id, text) => `<a href="${devHref(id)}" class="rr-devlink" data-stop>${esc(text)}</a>`;
const goDevice = (id, tab) => { location.hash = devHref(id, tab); };

function devRoute() {
  const m = /^#device\/([^/?]+)(?:\/([a-z]+))?/.exec(location.hash);
  if (!m) return null;
  let id = m[1];
  try { id = decodeURIComponent(id); } catch { /* egal */ }
  return { id, tab: DEV_TABS.includes(m[2]) ? m[2] : "overview" };
}

let devState = { id: null, tab: "overview", body: null, missing: false, hold: 0 };

function setDevMsg(kind, text) {
  const msg = $("#devMsg");
  if (!msg) return;
  msg.className = `small mb-2${kind === "error" ? " text-red" : kind === "ok" ? " text-green" : " text-secondary"}`;
  msg.textContent = text || "";
}

async function showDevice(route) {
  if (route.id !== devState.id) {
    endNameEdit(false);
    cfgReset(route.id);
    devState = { id: route.id, tab: route.tab, body: null, missing: false, hold: 0 };
    setDevMsg("", "");
    $("#pagePre").innerHTML = `<a href="#devices">Devices</a>`;
    $("#pageTitle").textContent = "…";
    $("#pageSub").textContent = route.id;
    $("#devActions").innerHTML = "";
    $("#devPage").innerHTML = `<p class="text-secondary">Loading …</p>`;
    window.scrollTo(0, 0);
    await loadDevicePage(route.id);
    return;
  }
  devState.tab = route.tab;
  // Back to the same device (e.g. from the device list): set the header with name and pencil again, the other
  // page had overwritten it. Not while renaming is in progress
  if (devState.body) {
    if (!nameEdit) renderDeviceHeader();
    renderDeviceTab();
  }
}

async function loadDevicePage(id) {
  let body;
  try {
    ({ body } = await api(`api/devices/${encodeURIComponent(id)}`));
  } catch (e) {
    if (devState.id !== id) return;
    devState.missing = /unknown device/i.test(e.message);
    devState.body = null;
    $("#pageTitle").textContent = devState.missing ? "Unknown device" : "Device";
    document.title = "Device · RelayRoster";
    $("#devPage").innerHTML = devState.missing
      ? `<div class="card"><div class="empty"><p class="empty-title">RelayRoster does not know this device</p>
          <p class="empty-subtitle text-secondary font-monospace">${esc(id)}</p>
          <div class="empty-action"><a class="btn" href="#devices">All devices</a></div></div></div>`
      : `<div class="alert alert-danger">${esc(e.message)}</div>`;
    return;
  }
  if (devState.id !== id) return;   // another device was selected in the meantime
  devState.body = body;
  renderDeviceHeader();
  renderDeviceTab();
}

// After a run, update the open device page too, but never in the middle of editing or writing
function refreshDevicePage() {
  if (currentPage === "device" && devState.id && devState.body && !nameEdit && !devState.hold && !devState.ovOpen && !cfgDirty()) loadDevicePage(devState.id);
}

function deviceSub(d) {
  const parts = [esc(d.model_name || d.app),
    `<a class="font-monospace" href="http://${esc(d.host)}/" target="_blank" rel="noopener noreferrer">${esc(d.host)}</a>`,
    `<span class="font-monospace">${esc(d.ver || "–")}</span>`].filter(Boolean);
  // Operating mode in its own line below the header
  const modes = modeBadges({ profile: d.profile, stacks: (data.devices.find((x) => x.id === d.id) || {}).stacks });
  return `${parts.join(" · ")} · ${statusBadge(d)}${d.auth_en ? "" : ` ${badge("bad", "open", "No device password set")}`}${
    modes ? `<div class="mt-2 d-flex flex-wrap gap-1">${modes}</div>` : ""}`;
}

function deviceActions(d) {
  // Actions only if the device has the method according to Shelly.ListMethods (unknown = offer)
  const fwUpd = d.caps?.update === false ? null : d.updates?.stable;
  const canReboot = d.caps?.reboot !== false;
  const desk = "d-none d-md-inline-flex";
  const web = `http://${esc(d.host)}/`;
  const menuFw = !fwUpd ? ""
    : allowFirmware ? `<button class="dropdown-item d-md-none" type="button" data-fw-open="dev:${esc(d.id)}" data-stop>${ICON.download}Update to ${esc(fwUpd)}</button>`
      : `<a class="dropdown-item d-md-none text-secondary" href="#settings">${ICON.download}Update to ${esc(fwUpd)} (off in Settings)</a>`;
  const menuReboot = !canReboot ? ""
    : allowReboot
    ? `<button class="dropdown-item d-md-none" type="button" data-act="reboot">${ICON.power}Restart device</button>`
    : `<a class="dropdown-item d-md-none text-secondary" href="#settings">${ICON.power}Restart device (off in Settings)</a>`;
  return `<div class="btn-list flex-nowrap">
      <a class="btn ${desk}" href="${web}" target="_blank" rel="noopener noreferrer">${ICON.external}Device web UI</a>
      ${canReboot ? rebootButton(desk) : ""}
      ${fwUpd ? fwBtn(`data-fw-open="dev:${esc(d.id)}"`, `Update to ${fwUpd}`, desk) : ""}
      <div class="dropdown">
        <button class="btn btn-icon" type="button" data-menu-toggle aria-haspopup="menu" aria-expanded="false" aria-label="More device actions">${ICON.dots}</button>
        <div class="dropdown-menu dropdown-menu-end rr-menu" role="menu" data-tblr-popper="static">
          <a class="dropdown-item d-md-none" href="${web}" target="_blank" rel="noopener noreferrer">${ICON.external}Device web UI</a>
          ${menuReboot}${menuFw}
          <div class="dropdown-divider d-md-none"></div>
          <button class="dropdown-item text-red" type="button" data-act="remove"
                  title="Remove from RelayRoster only, e.g. after uninstalling. The device itself is not changed.">Remove from RelayRoster</button>
        </div>
      </div>
    </div>`;
}

function renderDeviceHeader() {
  const b = devState.body, d = b.device;
  document.title = `${d.name || d.id} · RelayRoster`;
  $("#pagePre").innerHTML = `<a href="#devices">Devices</a>`;
  $("#pageTitle").innerHTML = nameField(d, b.planned_name);
  $("#pageSub").innerHTML = deviceSub(d);
  $("#devActions").innerHTML = deviceActions(d);
  wireNameEdit(d);
}

function renderDeviceTab() {
  const b = devState.body, d = b.device, tab = devState.tab;
  const tabs = [["overview", "Overview"], ["settings", "Settings"], ["findings", "Findings", b.findings.length],
    ["backups", "Backups", b.snapshots.length], ["changes", "Changes", (b.audit || []).length]];
  const nav = `<ul class="nav nav-tabs mb-3 rr-scroll-tabs" role="tablist" aria-label="Device sections">${tabs.map(([k, label, n]) =>
    `<li class="nav-item"><a class="nav-link${k === tab ? " active" : ""}" href="${devHref(d.id, k)}" role="tab" aria-selected="${k === tab}"${k === tab ? ` aria-current="page"` : ""}>${label}${n != null ? ` <span class="badge bg-secondary-lt ms-1">${n}</span>` : ""}</a></li>`).join("")}</ul>`;
  const render = { overview: devOverview, settings: devSettings, findings: devFindings, backups: devBackups, changes: devChanges }[tab];
  $("#devPage").innerHTML = nav + render(b);
  $$("[data-diff]", $("#devPage")).forEach((btn) => btn.addEventListener("click", () => toggleDiff(btn.dataset.diff)));
  // Restore: play back the configuration of a backup, preview and job like bulk change
  $$("[data-restore]", $("#devPage")).forEach((btn) => btn.addEventListener("click", () => openApply({
    title: `Restore backup from ${btn.dataset.when}`, request: { mode: "restore", snapshot_id: Number(btn.dataset.restore) }, policy: null })));
  $$("[data-ov-open]", $("#devPage")).forEach((btn) => btn.addEventListener("click", () => openOverride(+btn.dataset.ovOpen)));
  // Bring the active tab into view horizontally only; scrollIntoView pulled the page up
  const tabsEl = $(".rr-scroll-tabs .active", $("#devPage"));
  const strip = tabsEl?.closest(".rr-scroll-tabs");
  if (strip && (tabsEl.offsetLeft < strip.scrollLeft || tabsEl.offsetLeft + tabsEl.offsetWidth > strip.scrollLeft + strip.clientWidth)) {
    strip.scrollLeft = Math.max(0, tabsEl.offsetLeft - 16);
  }
}

const dgItem = (title, content) => `<div class="datagrid-item"><div class="datagrid-title">${title}</div><div class="datagrid-content">${content}</div></div>`;
const cardTable = (head, rows) => `<div class="table-responsive"><table class="table table-vcenter card-table">
    <thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
const infoCard = (title, items) => `<div class="card h-100"><div class="card-header"><h3 class="card-title">${title}</h3></div>
    <div class="card-body"><div class="datagrid">${items.join("")}</div></div></div>`;

function devOverview(b) {
  const d = b.device;
  const row = data.devices.find((x) => x.id === d.id) || {};
  const fwUpd = d.updates?.stable;
  const device = infoCard("Device", [
    dgItem("Type", `${esc(d.type_name || d.model_name || d.app)}<span class="rr-sub">${[d.type_sub, d.model, d.app].filter(Boolean).map(esc).join(" · ")}</span>${stackNote(row)}`),
    // Operating mode of the device itself (2PM: switch or cover); most devices have none
    d.profile ? dgItem("Device mode", `<span title="Shelly calls this the device profile">${esc(d.profile)}</span>`) : "",
    dgItem("Device ID", `<span class="font-monospace">${esc(d.id)}</span>`),
    dgItem("MAC", `<span class="font-monospace">${esc(d.mac || "–")}</span>`),
    dgItem("RPC methods", b.methods ? `${b.methods.length}<span class="rr-sub" title="${esc(b.methods.join("\n"))}">from Shelly.ListMethods</span>` : `<span class="text-secondary">unknown</span>`),
  ]);
  const network = infoCard("Network", [
    dgItem("Address", `<a class="font-monospace" href="http://${esc(d.host)}/" target="_blank" rel="noopener noreferrer">${esc(d.host)}</a>${addressBadge(row)}`),
    dgItem("Wi-Fi", esc(d.status?.wifi?.ssid || "–")),
    dgItem("Signal", rssiBadge(d.status?.wifi?.rssi)),
    b.address_block ? dgItem("Address block", `<span class="font-monospace">${esc(b.address_block.range)}</span>${b.address_block.name ? `<span class="rr-sub">${esc(b.address_block.name)}</span>` : ""}`) : "",
  ]);
  const status = infoCard("Status", [
    dgItem("Status", statusBadge(d)),
    dgItem("Firmware", `<span class="font-monospace">${esc(d.ver)}</span>${fwUpd ? ` ${badge("info", `→ ${fwUpd}`, "Stable update available")}` : ""}`),
    dgItem("Uptime", esc(fmtUptime(d.status?.sys?.uptime))),
    dgItem("Last read", timeHtml(d.last_ok)),
    dgItem("Password", d.auth_en ? badge("ok", "set") : badge("bad", "none")),
    d.last_error ? dgItem("Error", `<span class="text-red">${esc(d.last_error)}</span>`) : "",
  ]);
  const fs = b.findings.slice(0, 5);
  const findings = `<div class="card h-100"><div class="card-header"><h3 class="card-title">Findings</h3>
      <div class="card-actions">${b.findings.length ? badge("warn", b.findings.length) : badge("ok", "none")}</div></div>
    ${fs.length ? cardTable(["Finding", "Expected", "Actual"], fs.map((f) => `<tr>
        ${settingCell(f, f.note)}
        <td>${valHtml(f.expected)}</td><td>${f.kind === "missing" ? `<span class="text-secondary">not present</span>` : valHtml(f.actual)}</td></tr>`).join(""))
      : `<div class="card-body text-secondary">Matches the policy.</div>`}
    ${b.findings.length ? `<div class="card-footer"><a href="${devHref(d.id, "findings")}">${b.findings.length > fs.length ? `All ${b.findings.length} findings` : "Findings and policy rules"}</a></div>` : ""}</div>`;
  return `<div class="row row-cards">
      <div class="col-md-6 col-xl-3">${device}</div>
      <div class="col-md-6 col-xl-3">${network}</div>
      <div class="col-md-6 col-xl-3">${status}</div>
      <div class="col-md-6 col-xl-3">${profilesCard(b)}</div>
      ${(b.covers || []).map((c) => `<div class="col-lg-6">${coverCard(c, d)}</div>`).join("")}
      <div class="col-lg-6">${findings}</div>
      <div class="col-lg-6">${peerCard(b.peer, d)}</div>
    </div>`;
}

// Policy profiles of a device as a card of their own: one row per profile with where it applies from
// (match rule or assigned) and a remove button; adding to another profile sits in the footer
const VIA_TEXT = { match: "by match rule", assigned: "assigned", excluded: "excluded" };
function profilesCard(b) {
  const list = b.profile_assignment || [];
  const on = list.filter((p) => p.via === "match" || p.via === "assigned");
  const rows = on.map((p) => `<div class="list-group-item d-flex align-items-center gap-2">
      <div class="flex-fill"><a href="${profHref(p.name)}">${esc(p.name)}</a><span class="rr-sub">${esc(VIA_TEXT[p.via])}</span></div>
      <button type="button" class="btn btn-sm btn-ghost-secondary" data-unassign="${esc(p.name)}" title="Remove this device from the profile">Remove</button></div>`).join("");
  const off = list.filter((p) => !on.includes(p));
  const sel = off.length ? `<div class="card-footer"><select class="form-select" data-assign aria-label="Add to profile">
      <option value="">Add to profile…</option>${off.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}${p.via === "excluded" ? " (excluded)" : ""}</option>`).join("")}</select></div>` : "";
  return `<div class="card h-100"><div class="card-header"><h3 class="card-title">Policy profiles</h3>
      <div class="card-actions">${on.length ? badge("neutral", on.length) : ""}</div></div>
    ${rows ? `<div class="list-group list-group-flush flex-fill">${rows}</div>`
      : `<div class="card-body text-secondary">No profile applies. Only the baseline of the policy is checked.</div>`}${sel}</div>`;
}

async function assignDevice(name, add) {
  const id = devState.id;
  const { status, body } = await api(`api/profiles/${encodeURIComponent(name)}/devices`, { method: "POST", json: add ? { add: [id] } : { remove: [id] }, keep: "all" });
  if (status >= 400) { setDevMsg("error", (body.errors || [body.detail]).join(" · ")); return; }
  policyLoaded = false;
  prof.loaded = false;
  setDevMsg("ok", add ? `Assigned to profile ${name}. Saved to policy.yaml.` : `Removed from profile ${name}. Saved to policy.yaml.`);
  await loadOverview();
  refreshDevicePage();
}

// Shutter: measured values from the last read next to the limits. Helps to set power_limit per motor
const COVER_ERR = { obstruction: "obstruction", overpower: "power limit", overcurrent: "current limit", overvoltage: "overvoltage",
  undervoltage: "undervoltage", overtemp: "overtemperature", safety_switch: "safety switch" };
function coverCard(c, d) {
  const num = (v, unit, digits = 0) => (typeof v === "number" ? `<span class="rr-val">${esc(v.toFixed(digits))} ${esc(unit)}</span>` : `<span class="text-secondary">–</span>`);
  const L = c.limits || {};
  const cal = c.calibrated === true ? badge("ok", "calibrated") : c.calibrated === false ? badge("warn", "not calibrated", "Run the calibration in the device web UI") : "";
  const state = c.state ? `${esc(c.state)}${c.calibrated && typeof c.position === "number" ? ` · ${esc(c.position)} %` : ""}` : "–";
  const errs = c.errors.length
    ? c.errors.map((e) => badge("bad", COVER_ERR[e] || e, e)).join(" ")
    : `<span class="text-secondary">none</span>`;
  const obst = c.obstruction || {};
  const obstText = obst.enable === true ? `on · ${esc(obst.direction || "?")} · ${esc(obst.action || "?")}`
    : obst.enable === false ? `<span class="text-yellow">off</span>` : `<span class="text-secondary">–</span>`;
  const powerNote = c.moving ? "while moving" : "at last read, not moving";
  return `<div class="card h-100"><div class="card-header"><h3 class="card-title">${esc(c.title)}</h3>
      <div class="card-actions">${cal}</div></div>
    <div class="card-body"><div class="datagrid">
      ${dgItem("State", state)}
      ${dgItem("Errors", errs)}
      ${dgItem("Power", `${num(c.apower, "W", 1)}<span class="rr-sub">${esc(powerNote)}</span>`)}
      ${dgItem("Voltage · current", `${num(c.voltage, "V")} · ${num(c.current, "A", 2)}`)}
      ${typeof c.temperature === "number" ? dgItem("Temperature", num(c.temperature, "°C", 1)) : ""}
      ${dgItem("Power limit", num(L.power_limit, "W"))}
      ${dgItem("Obstruction detection", obstText)}
      ${dgItem("Obstruction threshold", `${num(L.power_thr, "W")}${typeof L.holdoff === "number" ? `<span class="rr-sub">hold-off ${esc(L.holdoff)} s</span>` : ""}`)}
      ${dgItem("Travel time open · close", `${num(L.maxtime_open, "s", 1)} · ${num(L.maxtime_close, "s", 1)}`)}
    </div>
    <div class="text-secondary small mt-3">Power is measured when RelayRoster reads the device. To see the running power, press
      <em>Sync now</em> while the shutter moves. Set <code>${esc(c.id)}.power_limit</code> to about 30 % above it in an override.</div>
    </div>
    <div class="card-footer"><a href="${devHref(d.id, "settings")}">Cover settings</a></div></div>`;
}

// "Differs from its type": paths where this device is an outlier in the peer comparison
function peerCard(p, d) {
  const head = `<div class="card-header"><h3 class="card-title">Differs from its type</h3>
    <div class="card-actions">${p && p.size > 1 ? (p.diffs.length ? badge("warn", p.diffs.length) : badge("ok", "uniform")) : ""}</div></div>`;
  if (!p || p.size < 2) return `<div class="card h-100">${head}<div class="card-body text-secondary">Only device of its type, nothing to compare.</div></div>`;
  const others = (r) => r.others.map((o) => `${esc(o.name || o.id)}: ${esc(fmtVal(o.value))}`).join("\n");
  const rows = [
    ...p.diffs.map((r) => `<tr>
      ${settingCell(r)}
      <td class="rr-outlier">${valHtml(r.value)}</td>
      <td title="${esc(others(r))}">${r.has_majority ? valHtml(r.majority) : `<span class="text-secondary">no majority</span>`}</td></tr>`),
  ].join("");
  const key = `${p.app}|${p.profile || ""}`;
  return `<div class="card h-100">${head}
    ${rows ? cardTable(["Setting", "This device", "Others"], rows) : `<div class="card-body text-secondary">Same as the other ${p.size - 1} device${p.size === 2 ? "" : "s"} of this type.</div>`}
    <div class="card-footer"><a href="#peers" data-peer-goto="${esc(key)}">Peer comparison: ${esc(p.model_name || p.app)}${p.profile ? ` · ${esc(p.profile)}` : ""}</a></div></div>`;
}

// Value of a field per type: switch as on/off, number with unit, options with possible values in the tooltip
function settingVal(f) {
  const v = f.value;
  if (v === null || v === undefined) return `<span class="text-secondary">–</span>`;
  if (f.kind === "bool" && typeof v === "boolean") return v ? badge("ok", "on") : badge("neutral", "off");
  if (f.kind === "number" && typeof v === "number") return `<span class="rr-val">${esc(v)}${f.unit ? ` ${esc(f.unit)}` : ""}</span>`;
  if (f.kind === "enum") {
    const opts = (f.options || []).map((o) => (o === null ? "none" : o)).join(", ");
    return `<span class="rr-val"${opts ? ` title="Options: ${esc(opts)}"` : ""}>${esc(fmtVal(v))}</span>`;
  }
  if (v === "") return `<span class="text-secondary">empty</span>`;
  return valHtml(v);
}

function settingNotes(f) {
  const out = [];
  if (cfgHas(f.path)) out.push(badge("info", `pending: ${fmtVal(cfgEdit.draft[f.path])}`, "Not written yet"));
  if (f.policy && !f.policy.ok) out.push(badge("warn", `policy: ${fmtVal(f.policy.expected)}`, f.policy.source));
  if (f.peer) out.push(f.peer.has_majority ? badge("accent", `type: ${fmtVal(f.peer.majority)}`, "Most devices of this type") : badge("accent", "type: no majority"));
  return out.join(" ");
}

// Mark a deviation directly at the setting instead of as a counter in the header
const fieldWarn = (f) => (f.policy && !f.policy.ok) || f.peer ? ` ${badge("warn", "!", "Differs from the policy or from most devices of this type")}` : "";
const settingRow = (f) => `<tr>
    <td>${esc(f.label)}${fieldWarn(f)}<span class="rr-sub">${esc(f.path)}</span></td>
    <td class="text-end text-nowrap">${settingVal(f)}</td>
    <td class="text-end">${settingNotes(f)}</td></tr>`;

// Row while editing: input instead of value, "Use …" for policy target and type majority
function settingEditRow(f) {
  if (!f.editable) return settingRow(f);
  const err = cfgEdit.errors[f.path];
  const use = [];
  if (f.policy && !f.policy.ok && f.policy.value !== undefined) use.push(cfgUseBtn(f, f.policy.value, `Policy: ${fmtVal(f.policy.expected)} (${f.policy.source})`));
  if (f.peer?.has_majority && JSON.stringify(f.peer.majority) !== JSON.stringify(f.policy?.value)) use.push(cfgUseBtn(f, f.peer.majority, "Most devices of this type"));
  const notes = [f.policy && !f.policy.ok ? badge("warn", `policy: ${fmtVal(f.policy.expected)}`, f.policy.source) : "",
    f.peer ? (f.peer.has_majority ? badge("accent", `type: ${fmtVal(f.peer.majority)}`, "Most devices of this type") : badge("accent", "type: no majority")) : "", ...use].filter(Boolean).join(" ");
  return `<tr class="${cfgHas(f.path) ? "rr-changed" : ""}" data-cfg-row="${esc(f.path)}">
    <td>${esc(f.label)}${fieldWarn(f)}<span class="rr-sub">${esc(f.path)}</span></td>
    <td class="text-end">${cfgInput(f)}${err ? `<div class="invalid-feedback d-block text-start">${esc(err)}</div>` : ""}</td>
    <td class="text-end">${notes}</td></tr>`;
}

function devSettings(b) {
  const s = b.settings || { sections: [], info: [] };
  if (!s.sections.length) return `<div class="card"><div class="empty"><p class="empty-title">No configuration read yet</p></div></div>`;
  const taken = b.snapshot?.taken_at;
  const cards = s.sections.map((sec) => {
    const active = sec.fields.filter((f) => f.active);
    const idle = sec.fields.filter((f) => !f.active);
    const needs = [...new Set(idle.map((f) => f.needs))].join("; ");
    const flag = "";   // status is in the list on the left and in the "Enabled" row, not in the header
    const notes = sec.fields.filter((f) => f.policy && !f.policy.ok || f.peer).length;
    const editable = sec.fields.some((f) => f.editable) && offersSet(sec.id);
    const editing = editable && allowConfig && cfgEdit.open.has(sec.id);
    const row = editing ? settingEditRow : settingRow;
    const edit = !editable ? ""
      : !allowConfig ? `<a class="btn btn-sm rr-off" href="#settings" aria-disabled="true" title="Config changes are off. Turn them on in Settings">${ICON.pencil}Edit</a>`
        : `<button class="btn btn-sm${editing ? " btn-primary" : ""}" type="button" data-cfg-edit="${esc(sec.id)}">${editing ? "Done" : `${ICON.pencil}Edit`}</button>`;
    // Wi-Fi and Ethernet addresses are not edited here (lockout risk) but through the address job, which checks
    // the device at its new address; keeping the current address turns DHCP into a static one
    const mode = sec.fields.find((f) => f.path === (sec.id === "wifi" ? "wifi.sta.ipv4mode" : `${sec.id}.ipv4mode`));
    const addr = sec.id === "eth" || (sec.id === "wifi" && sec.fields.some((f) => f.path.startsWith("wifi.sta.")))
      ? `<button class="btn btn-sm" type="button" data-addr-open="${esc(b.device.id)}" title="${mode?.value === "static" ? "Change the static address" : "Switch from DHCP to a static address"}">Static address…</button>` : "";
    const drafts = sec.fields.filter((f) => cfgHas(f.path)).length;
    return { id: sec.id, title: sec.title, notes, state: sec.enabled, drafts, html: `<div class="card${editing ? " rr-editing" : ""}" data-cfg-card="${esc(sec.id)}">
      <div class="card-header"><div class="rr-minw0"><h3 class="card-title">${esc(sec.title)}</h3>${compAbout(sec.id)}</div><div class="card-actions btn-list flex-nowrap">${compDocLink(sec.id)}${flag}${addr}${edit}</div></div>
      ${active.length ? `<div class="table-responsive"><table class="table table-vcenter card-table rr-settings-table"><tbody>${active.map(row).join("")}</tbody></table></div>` : ""}
      ${idle.length ? `<details class="rr-peer-more border-top"${editing && idle.some((f) => f.editable) ? " open" : ""}><summary class="card-body py-2 text-secondary small">${idle.length} setting${idle.length === 1 ? "" : "s"} without effect (needs ${esc(needs)})</summary>
        <div class="table-responsive"><table class="table table-vcenter card-table rr-settings-table rr-hidden"><tbody>${idle.map(row).join("")}</tbody></table></div></details>` : ""}
    </div>` };
  });
  const info = s.info.length ? `<div class="card"><div class="card-header"><h3 class="card-title">Device info</h3>
      <div class="card-actions text-secondary small">Set by the device or by calibration, cannot be changed here</div></div>
      <div class="table-responsive"><table class="table table-vcenter card-table rr-settings-table rr-hidden"><tbody>${s.info.map((f) => `<tr>
        <td>${esc(f.section)} · ${esc(f.label)}<span class="rr-sub">${esc(f.path)}</span></td>
        <td class="text-end text-nowrap">${settingVal(f)}</td>
        <td class="text-end">${badge("neutral", f.reason === "read_only" ? "read-only" : "managed")}</td></tr>`).join("")}</tbody></table></div></div>` : "";
  const scripts = b.snapshot?.data?.scripts || [];
  const cfg = b.snapshot?.data?.config;
  const extra = `<div class="card card-body">
      ${scripts.length ? `<details class="mt-1"><summary class="text-secondary">Scripts (${scripts.length})</summary>${scripts.map((sc) =>
        `<div class="mt-2 font-monospace small">${esc(sc.name)} ${sc.enable ? badge("ok", "enabled") : badge("neutral", "off")}</div><pre class="rr-json mt-1">${esc(sc.code)}</pre>`).join("")}</details>` : ""}
      <details class="mt-2"><summary class="text-secondary">Raw configuration (Shelly.GetConfig)</summary><pre class="rr-json mt-2">${esc(cfg ? JSON.stringify(cfg, null, 2) : "–")}</pre></details>
    </div>`;
  const intro = allowConfig
    ? `From the backup of ${esc(fmtTime(taken))}. „Edit“ collects changes as a draft; nothing is written before you review and confirm them.`
    : `Read only, from the backup of ${esc(fmtTime(taken))}. To change settings here, turn on <a href="#settings">Allow config changes</a> in Settings.`;
  // components on the left, only the selected one in full width on the right
  const secs = [...cards, ...(info ? [{ id: "__info", title: "Device info", notes: 0, drafts: 0, flag: "", html: info }] : []),
    { id: "__raw", title: "Scripts and raw config", notes: 0, drafts: 0, flag: "", html: extra }];
  const pick = cfgSel.id === b.device.id && secs.some((x) => x.id === cfgSel.sec) ? cfgSel.sec
    : (secs.find((x) => x.notes) || secs[0]).id;
  cfgSel = { id: b.device.id, sec: pick };
  const nav = secs.map((x) => `<a href="#" class="list-group-item list-group-item-action d-flex align-items-center gap-2${x.id === pick ? " active" : ""}" data-cfg-sec="${esc(x.id)}"${x.id === pick ? ' aria-current="true"' : ""}>
      <span class="flex-fill text-truncate">${esc(x.title)}</span>
      ${x.drafts ? badge("info", x.drafts, "Unsaved changes") : ""}${x.notes ? badge("warn", x.notes, "Fields that differ from the policy or the type") : ""}${x.state === true ? badge("ok", "on") : x.state === false ? badge("neutral", "off") : ""}</a>`).join("");
  return `<p class="text-secondary small">${intro} Badges mark values that differ from the policy or from most devices of this type.</p>
    <div id="cfgBar" class="rr-cfg-bar">${cfgBarHtml()}</div>
    <div class="row g-3"><div class="col-md-4 col-lg-3"><div class="list-group rr-cfg-nav">${nav}</div></div>
      <div class="col-md-8 col-lg-9 rr-cfg-main">${secs.find((x) => x.id === pick).html}</div></div>`;
}
let cfgSel = { id: null, sec: null };   // selected component in the Settings tab

// Short description per component and page in the Shelly API docs (Gen2+). Without a docs page only text.
const SHELLY_DOCS = "https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/";
const COMPONENT_INFO = {
  sys: ["Sys", "Device-wide settings: device name, location and time zone, time server (SNTP), debug logging and the local web UI."],
  wifi: ["WiFi", "Wi-Fi client with up to two networks (the second as fallback), the device's own access point and the range extender."],
  eth: ["Eth", "Wired Ethernet on Pro devices: on or off, DHCP or a static address."],
  cloud: ["Cloud", "Connection to Shelly Cloud, used by the Shelly app and for remote access."],
  mqtt: ["Mqtt", "Connection to an MQTT broker: server, login, topic prefix and which status and RPC messages are published."],
  ble: ["BLE", "Bluetooth: Bluetooth itself, RPC over Bluetooth and the observer that receives BTHome sensors."],
  ws: ["Ws", "Outbound WebSocket: the device keeps a connection to a server you set and sends its events there."],
  cover: ["Cover", "Roller shutter output: input mode, motor limits, obstruction detection, swapped or inverted wiring and the state after power-on."],
  switch: ["Switch", "Relay output: state after power-on, how the input controls it, auto on/off timers and power limits."],
  input: ["Input", "Physical input: button, switch or analog, inverted or not, and whether it is detached from the output."],
  light: ["Light", "Dimmable light output: state and brightness after power-on, transitions and night mode."],
  rgb: ["RGB", "RGB light output: color, brightness and state after power-on."],
  rgbw: ["RGBW", "RGBW light output: color, white channel, brightness and state after power-on."],
  cct: ["CCT", "Tunable white light output: color temperature, brightness and state after power-on."],
  pm1: ["PM1", "Power meter for one channel: voltage, current, power and energy reporting."],
  em: ["EM", "Three-phase energy meter: phases, CT type and reverse handling."],
  em1: ["EM1", "Single-phase energy meter: CT type and reverse handling."],
  temperature: ["Temperature", "Temperature sensor: name, unit offset and reporting thresholds."],
  humidity: ["Humidity", "Humidity sensor: name, offset and reporting thresholds."],
  illuminance: ["Illuminance", "Light sensor: name and the thresholds for dark and bright."],
  voltmeter: ["Voltmeter", "Analog voltage input: range, reporting threshold and optional value transformation."],
  devicepower: ["DevicePower", "Battery and external power of battery-powered devices."],
  smoke: ["Smoke", "Smoke alarm sensor."],
  flood: ["Flood", "Flood sensor: alarm mode and reporting."],
  presence: ["Presence", "Presence sensor."],
  modbus: ["Modbus", "Modbus TCP server of the device."],
  matter: ["Matter", "Matter support: when on, the device can be paired with a Matter controller."],
  zigbee: ["Zigbee", "Zigbee support (Gen4): when on, the device can join a Zigbee network."],
  bthome: [null, "BTHome devices and sensors paired over Bluetooth."],
  ui: [null, "Settings of the device's display and local controls."],
  knx: ["https://shelly-api-docs.shelly.cloud/gen2/Integrations/KNX/", "KNX IP: on or off, the device's individual address and the routing multicast address. Switching it on or off needs a restart."],
};
const compInfo = (secId) => COMPONENT_INFO[String(secId).split(":")[0].toLowerCase()];
const compAbout = (secId) => { const i = compInfo(secId); return i ? `<div class="card-subtitle text-secondary small mt-1">${esc(i[1])}</div>` : ""; };
const compDocLink = (secId) => {
  const i = compInfo(secId);
  if (!i?.[0]) return "";
  const url = i[0].startsWith("https://") ? i[0] : `${SHELLY_DOCS}${i[0]}`;
  return `<a class="btn btn-sm btn-ghost-secondary" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="Shelly API documentation">${ICON.external}Docs</a>`;
};

const ruleSource = (s) => s.startsWith("Profile: ") ? `<a href="${profHref(s.slice(9))}">${esc(s)}</a>`
  : s === "Baseline" ? `<a href="#policy/baseline">${esc(s)}</a>` : s === "Override" ? `<a href="#policy/overrides">${esc(s)}</a>` : esc(s);

function devFindings(b) {
  const findings = b.findings.length ? `<div class="card">${cardTable(["Finding", "Expected", "Actual", "Source"], findingRows(b.findings, true))}</div>`
    : `<div class="card"><div class="card-body text-secondary">Matches the policy.</div></div>`;
  const rules = b.rules.length ? `<div class="card">${cardTable(["Pattern", "Expected", "Source", ""], b.rules.map((r, i) =>
    `<tr><td class="font-monospace small">${esc(r.path)}</td><td>${valHtml(r.expected)}</td><td class="text-secondary small">${ruleSource(r.source)}</td>
      <td class="text-end"><button class="btn btn-sm" type="button" data-ov-open="${i}">${r.source === "Override" ? "Change" : "Override"}</button></td></tr>
     <tr class="d-none" id="ov-${i}"><td colspan="4"></td></tr>`).join(""))}
    <div class="card-footer text-secondary small">“Override” adds a rule for this device only to <code>overrides</code> in policy.yaml, e.g. a measured <code>power_limit</code>. A comment is required.</div></div>`
    : `<div class="card"><div class="card-body text-secondary">No rules.</div></div>`;
  const na = (b.not_applicable || []).length ? `<h3 class="mt-4">Rules that do not apply to this model (${b.not_applicable.length})</h3>
    <div class="card">${cardTable(["Pattern", "Expected", "Source"], b.not_applicable.map((n) => `<tr class="text-secondary">
      <td class="font-monospace small">${esc(n.path)}<span class="rr-sub">${esc(n.note)}</span></td><td>${valHtml(n.expected)}</td><td class="small">${ruleSource(n.source)}</td></tr>`).join(""))}
    <div class="card-footer text-secondary small">No device of this model has these settings. They do not count as findings.</div></div>` : "";
  const applyBtns = (b.profile_assignment || []).filter((p) => p.via === "match" || p.via === "assigned")
    .map((p) => `<button class="btn btn-sm" type="button" data-apply-prof="${esc(p.name)}">Apply ${esc(p.name)}…</button>`).join(" ");
  return `${findings}${applyBtns ? `<div class="mt-2 d-flex gap-2 flex-wrap">${applyBtns}</div>` : ""}
    <h3 class="mt-4">Applicable policy rules (${b.rules.length})</h3>${rules}${na}`;
}

// Override from the device page: take over the rule, change the value, comment required
function openOverride(i) {
  const b = devState.body, r = b.rules[i], row = $(`#ov-${i}`);
  if (!row) return;
  if (!row.classList.contains("d-none")) { row.classList.add("d-none"); devState.ovOpen = false; return; }
  const base = { path: r.path, ...ruleFromRaw(r.raw) };
  // Wildcard on this device's component if there is exactly one (cover:* -> cover:0)
  const m = /^([a-z0-9_]+):\*\./.exec(r.path);
  if (m) {
    const comps = Object.keys(b.snapshot?.data?.config || {}).filter((k) => k.startsWith(`${m[1]}:`));
    if (comps.length === 1) base.path = r.path.replace(`${m[1]}:*`, comps[0]);
  }
  row.classList.remove("d-none");
  devState.ovOpen = true;   // no reload after a run while the form is open
  $("td", row).innerHTML = `<form class="row g-2 align-items-end" data-ov-form="${i}">
      <div class="col-md-4"><label class="form-label small">Path</label><input class="form-control form-control-sm font-monospace" name="path" value="${esc(base.path)}" required></div>
      <div class="col-md-2"><label class="form-label small">Operator</label><select class="form-select form-select-sm" name="op">${RULE_OPS.map(([v, l]) => `<option value="${v}"${v === base.op ? " selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="col-md-2"><label class="form-label small">Value</label><input class="form-control form-control-sm font-monospace" name="text" value="${esc(ruleText(base))}"></div>
      <div class="col-md-4"><label class="form-label small required">Comment: why and when</label><input class="form-control form-control-sm" name="comment" placeholder="running power 140 W measured 2026-11-02, +30 %" required></div>
      <div class="col-12 d-flex gap-2 justify-content-end"><span class="me-auto small" data-ov-msg></span>
        <button class="btn btn-sm" type="button" data-ov-cancel>Cancel</button><button class="btn btn-sm btn-primary" type="submit">Save override</button></div>
    </form>`;
  const form = $("form", row);
  $("[data-ov-cancel]", form).addEventListener("click", () => { row.classList.add("d-none"); devState.ovOpen = false; });
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("[data-ov-msg]", form);
    let rule;
    try {
      const f = form.elements;
      rule = { path: f.namedItem("path").value.trim(), op: f.namedItem("op").value, value: parseRule(f.namedItem("text").value, f.namedItem("op").value),
        comment: f.namedItem("comment").value.trim() };
    } catch (err) { msg.className = "me-auto small text-red"; msg.textContent = err.message; return; }
    const { status, body } = await api(`api/devices/${encodeURIComponent(b.device.id)}/override`, { method: "POST", json: { rule }, keep: "all" });
    if (status >= 400) { msg.className = "me-auto small text-red"; msg.textContent = (body.errors || [body.detail]).join(" · "); return; }
    policyLoaded = false;
    prof.loaded = false;
    devState.ovOpen = false;
    await loadDevicePage(b.device.id);
    loadOverview().catch((e) => showError(e.message));
  });
  form.elements.namedItem("comment").focus();
}

function ruleFromRaw(raw) {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const ops = Object.keys(raw);
    if (ops.length && ops.every((k) => ["in", "not", "min", "max"].includes(k))) {
      return ops.length === 1 ? { op: ops[0], value: raw[ops[0]] } : { op: "expr", value: raw };
    }
  }
  return { op: "=", value: raw };
}

function devBackups(b) {
  if (!b.snapshots.length) return `<div class="card"><div class="empty"><p class="empty-title">No snapshot yet</p></div></div>`;
  return `<div class="card">${cardTable(["Version", "Firmware", "Confirmed until", ""], b.snapshots.map((s, i) => `<tr>
      <td>${timeLongHtml(s.taken_at)}${i === 0 ? ` ${badge("ok", "current")}` : ""}</td>
      <td class="font-monospace small">${esc(s.ver || "–")}</td>
      <td class="text-secondary">${timeLongHtml(s.confirmed_at)}</td>
      <td class="text-end text-nowrap">${i < b.snapshots.length - 1 ? `<button class="btn btn-sm" data-diff="${s.id}" type="button">Changes</button> ` : ""}${i > 0 && allowConfig ? `<button class="btn btn-sm" data-restore="${s.id}" data-when="${esc(fmtTime(s.taken_at))}" type="button" title="Write the configuration of this backup back to the device (preview first)">Restore…</button> ` : ""}<a class="btn btn-sm" href="api/snapshots/${s.id}/download">${ICON.download}JSON</a></td>
    </tr><tr class="d-none" id="diff-${s.id}"><td colspan="4"></td></tr>`).join(""))}</div>`;
}

function devChanges(b) {
  const entries = b.audit || [];
  if (!entries.length) return `<div class="card"><div class="empty"><p class="empty-title">No changes yet</p>
    <p class="empty-subtitle text-secondary">Renames, firmware updates, restarts and setting changes from RelayRoster appear here.</p></div></div>`;
  const what = { rename: "Name", firmware: "Firmware", reboot: "Restart", config: "Setting", auth: "Password" };
  return `<div class="card">${cardTable(["When", "What", "Change", "Result"], entries.map((a) => {
    // config: detail = "<path>" or "<path> · <hint>", old and new as JSON
    const [path, ...rest] = a.action === "config" ? (a.detail || "").split(" · ") : [null];
    const detail = a.action === "config" ? rest.join(" · ") : a.detail;
    const val = (v) => { if (a.action !== "config") return esc(v || ""); try { return esc(fmtVal(JSON.parse(v))); } catch { return esc(v || ""); } };
    return `<tr><td class="text-nowrap">${timeLongHtml(a.at)}</td>
      <td>${esc(what[a.action] || a.action)}${path ? `<span class="rr-sub">${esc(path)}</span>` : ""}</td>
      <td>${a.action === "reboot" || a.action === "auth" ? `<span class="text-secondary">–</span>`
        : `${a.old ? val(a.old) : esc(a.action === "rename" ? "(no name)" : "–")}<span class="rr-arrow">→</span>${val(a.new)}`}</td>
      <td>${a.ok ? badge("ok", "ok") : badge("bad", "failed")}${detail ? `<span class="rr-sub">${esc(detail)}</span>` : ""}</td></tr>`;
  }).join(""))}</div>`;
}
